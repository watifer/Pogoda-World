"""Offline contract for the temporary PERF_DEBUG instrumentation.

All profiles, weather, messages and credentials below are synthetic canaries.
pytest.ini disables external sockets; tests fake the existing transports.
"""
import json
import re
import socket
import time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import requests

import guest_bot_handler as guest
import location_bot as lb
import main_card as mc
import owm_nowcast as owm
import perf_debug as perf
import users_store
import weather_payload as wp
from test_access_gate import BotHarness, REAL_GET_OFFSET, REAL_SEND_ONEOFF_REPORT, freeze_local_hour

CANARY = "private-canary-message-city-secret-token"
CID = 12345678901234  # synthetic, never a real target


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setenv("PERF_DEBUG", "0")
    with perf.bind((("0" * 32, 0),)):
        # bind's finally restores the parent's contexts even if a test raises.
        token = perf._requests.set(())
        try:
            yield
        finally:
            perf._requests.reset(token)


def records(capsys):
    return [json.loads(line[len("PERF_DEBUG "):]) for line in capsys.readouterr().out.splitlines()
            if line.startswith("PERF_DEBUG ")]


def run(function, *args, **kwargs):
    return perf.entry("card")(function)(*args, **kwargs)


def ends(rows, operation):
    return [r for r in rows if r["operation"] == operation and r["stage"].endswith("_after")]


@pytest.mark.parametrize("flag", [None, "0", "false", "true", "2"])
def test_disabled_no_clock_uuid_or_perf_output(monkeypatch, capsys, flag):
    if flag is None:
        monkeypatch.delenv("PERF_DEBUG", raising=False)
    else:
        monkeypatch.setenv("PERF_DEBUG", flag)
    def forbidden():
        pytest.fail("Disabled instrumentation must not read a clock or allocate IDs")
    monkeypatch.setattr(perf.time, "monotonic_ns", forbidden)
    monkeypatch.setattr(perf.uuid, "uuid4", forbidden)
    calls = []
    value = object()
    def operation(*args, **kwargs):
        calls.append((args, kwargs))
        return value
    assert run(lambda: perf.call("payload", operation, CANARY, key=CANARY)) is value
    assert calls == [((CANARY,), {"key": CANARY})]
    assert perf.received([{"message": CANARY}]) is None
    assert records(capsys) == []


@pytest.mark.parametrize("flag", ["0", "1"])
def test_order_results_and_arguments_unchanged(monkeypatch, capsys, flag):
    monkeypatch.setenv("PERF_DEBUG", flag)
    seen = []
    def step(name):
        def fn(*args, **kwargs):
            seen.append((name, args, kwargs))
            return name
        return fn
    def pipeline():
        for name in ("waiting_message", "geocode.forward", "payload", "layout", "render", "send_photo"):
            assert perf.call(name, step(name), CANARY, key=CANARY) == name
    run(pipeline)
    names = [row[0] for row in seen]
    assert names == ["waiting_message", "geocode.forward", "payload", "layout", "render", "send_photo"]
    assert all(args == (CANARY,) and kwargs == {"key": CANARY} for _, args, kwargs in seen)
    rows = records(capsys)
    assert bool(rows) == (flag == "1")
    if rows:
        assert [r["operation"] for r in rows if r["stage"].endswith("_before")] == ["card"] + names
        assert len({r["request_id"] for r in rows}) == 1


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError, KeyboardInterrupt])
def test_error_closes_all_spans_and_propagates_same_exception(monkeypatch, capsys, error_type):
    monkeypatch.setenv("PERF_DEBUG", "1")
    error = error_type(CANARY)
    def fail():
        raise error
    with pytest.raises(error_type) as caught:
        run(lambda: perf.call("payload", fail))
    assert caught.value is error
    rows = records(capsys)
    assert ends(rows, "payload")[0]["outcome"] == "error"
    assert ends(rows, "card")[0]["outcome"] == "error"
    assert CANARY not in json.dumps(rows)
    assert perf._requests.get() == () and perf._spans.get() == ()


def test_duration_uses_monotonic_ns_only(monkeypatch, capsys):
    monkeypatch.setenv("PERF_DEBUG", "1")
    clock = [1_000_000_000]
    monkeypatch.setattr(perf.time, "monotonic_ns", lambda: clock[0])
    def forbidden():
        pytest.fail("Instrument timer must not use wall-clock/perf_counter")
    monkeypatch.setattr(perf.time, "time", forbidden)
    monkeypatch.setattr(perf.time, "perf_counter", forbidden)
    run(lambda: perf.call("layout", lambda: clock.__setitem__(0, clock[0] + 17_250_000)))
    assert ends(records(capsys), "layout")[0]["elapsed_ms"] == 17.25


def test_logger_failure_does_not_change_results(monkeypatch):
    monkeypatch.setenv("PERF_DEBUG", "1")
    def broken(*args, **kwargs):
        raise OSError(CANARY)
    monkeypatch.setattr("builtins.print", broken)
    assert run(lambda: perf.call("layout", lambda: 42)) == 42
    assert perf._requests.get() == () and perf._spans.get() == ()


def test_ids_are_random_not_user_derived_and_schema_is_safe(monkeypatch, capsys):
    monkeypatch.setenv("PERF_DEBUG", "1")
    forbidden = [CANARY, str(CID), "52.123456", "21.654321", "https://secret.example/", "geocode_error_details"]
    for _ in range(2):
        run(lambda: perf.call("payload", lambda *a: a, *forbidden))
    rows = records(capsys)
    ids = {r["request_id"] for r in rows}
    assert len(ids) == 2
    assert all(re.fullmatch(r"[0-9a-f]{32}", rid) for rid in ids)
    for row in rows:
        assert set(row) == {"request_id", "operation", "stage", "elapsed_ms", "outcome", "cache"}
        assert row["operation"] in perf._OPERATIONS
        assert row["outcome"] in perf._OUTCOMES
        assert row["cache"] in perf._CACHES
    assert all(value not in json.dumps(rows) for value in forbidden)
    # Unapproved labels must not be serialised either.
    run(lambda: perf.call(CANARY, lambda: None))
    assert CANARY not in json.dumps(records(capsys))


def test_external_sockets_are_blocked_even_with_debug(monkeypatch):
    from pytest_socket import SocketBlockedError
    monkeypatch.setenv("PERF_DEBUG", "1")
    with pytest.warns(UserWarning, match="socket.socket"), pytest.raises(SocketBlockedError):
        run(lambda: perf.call("weather.openmeteo.http", socket.socket))


@pytest.fixture
def bot(monkeypatch):
    harness = BotHarness(monkeypatch)
    freeze_local_hour(monkeypatch, 10)
    assert users_store.upsert_access(harness.users, CID, "pl", "admin", "2026-01-01 10:00:00", "v1")
    assert users_store.set_profile(harness.users, CID, 52.23, 21.012, CANARY, "city", "pl", "v1", "2026-01-01 10:00:00")
    return harness


def test_users_shared_read_before_two_independent_dispatchers(bot, monkeypatch, capsys):
    monkeypatch.setenv("PERF_DEBUG", "1")
    calls = []
    original = bot.users.get_all_values
    def values():
        calls.append("users")
        return original()
    monkeypatch.setattr(bot.users, "get_all_values", values)
    bot._updates.extend([bot.msg(CID, "/teraz"), bot.msg(CID, "/teraz")])
    lb.main_bot()
    assert calls == ["users"]
    assert len(bot.oneoffs) == 2
    rows = records(capsys)
    receipt = [r for r in rows if r["stage"] == "update_received"]
    assert len(receipt) == 2 and len({r["request_id"] for r in receipt}) == 2
    assert len({r["poll_request_id"] for r in rows}) == 1
    for received in receipt:
        own = [r for r in rows if r["request_id"] == received["request_id"]]
        stages = [r["stage"] for r in own]
        assert stages.index("update_received") < stages.index("snapshot_users_before")
        assert stages.index("snapshot_users_after") < stages.index("dispatcher_started")
        snap = ends(own, "snapshot_users")[0]
        assert snap["snapshot_users_cache"] == "miss"
        assert snap["snapshot_users_outcome"] == "ok"
        assert ends(own, "dispatcher")[0]["outcome"] == "ok"
    assert str(CID) not in json.dumps(rows) and CANARY not in json.dumps(rows)
    bot.assert_users_only()


def test_users_cache_hit_and_miss_observe_real_branch(bot, monkeypatch, capsys):
    monkeypatch.setenv("PERF_DEBUG", "1")
    lb._reset_polling_state()
    clock = [100.0]
    monkeypatch.setattr(lb.time, "monotonic", lambda: clock[0])
    run(lambda: lb._load_users_snapshot(bot.gc))
    run(lambda: lb._load_users_snapshot(bot.gc))
    clock[0] += lb.SHEETS_CACHE_TTL_SECONDS + 1
    run(lambda: lb._load_users_snapshot(bot.gc))
    assert [r["cache"] for r in ends(records(capsys), "snapshot_users")] == ["miss", "hit", "miss"]


def test_users_swallowed_error_closes_marker_and_preserves_silent_dispatch(bot, monkeypatch, capsys):
    monkeypatch.setenv("PERF_DEBUG", "1")
    def fail():
        raise RuntimeError(CANARY)
    monkeypatch.setattr(bot.users, "get_all_values", fail)
    bot.run(bot.msg(CID, "/teraz"))
    assert bot.oneoffs == [] and bot.sent == []
    rows = records(capsys)
    assert ends(rows, "snapshot_users")[0]["snapshot_users_outcome"] == "error"
    assert ends(rows, "dispatcher")[0]["outcome"] == "skipped"
    assert CANARY not in json.dumps(rows)


@pytest.mark.parametrize("local", [True, False])
def test_bot_state_bootstrap_or_local_skip(bot, monkeypatch, capsys, tmp_path, local):
    monkeypatch.setenv("PERF_DEBUG", "1")
    path = tmp_path / "offset.json"
    if local:
        path.write_text('{"offset": 4}')
    monkeypatch.setenv("BOT_OFFSET_FILE", str(path))
    monkeypatch.setattr(lb, "get_offset", REAL_GET_OFFSET)
    lb._reset_polling_state()
    assert run(lambda: lb._runtime_offset(bot.gc)) == (4 if local else 0)
    run(lambda: lb._runtime_offset(bot.gc))
    rows = ends(records(capsys), "snapshot_bot_state")
    assert [(r["cache"], r["outcome"]) for r in rows] == (
        [("hit", "skipped"), ("hit", "skipped")] if local else
        [("miss", "ok"), ("hit", "skipped")])
    assert bot.gc.requested_tabs == ([] if local else ["Bot_State"])


def test_bot_state_error_closes_marker(bot, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("PERF_DEBUG", "1")
    monkeypatch.setenv("BOT_OFFSET_FILE", str(tmp_path / "absent.json"))
    monkeypatch.setattr(lb, "get_offset", REAL_GET_OFFSET)
    def fail(gc):
        raise RuntimeError(CANARY)
    monkeypatch.setattr(lb, "_read_offset_from_sheets", fail)
    lb._reset_polling_state()
    with pytest.raises(RuntimeError):
        run(lambda: lb._runtime_offset(bot.gc))
    row = ends(records(capsys), "snapshot_bot_state")[0]
    assert (row["cache"], row["outcome"]) == ("miss", "error")


@pytest.mark.parametrize("command,card", [("/dzien", "day"), ("/teraz", "now"), ("/trend", "future")])
@pytest.mark.parametrize("flag", ["0", "1"])
def test_real_command_pipeline_order_and_outputs(bot, monkeypatch, capsys, command, card, flag):
    monkeypatch.setenv("PERF_DEBUG", flag)
    monkeypatch.setattr(lb, "_send_oneoff_report", REAL_SEND_ONEOFF_REPORT)
    seen = []
    payload, layout = {"synthetic": True}, {"render": True}
    def fetch(**kwargs):
        seen.append("payload")
        assert kwargs["location_name"] == CANARY
        return payload
    def prepare(value):
        seen.append("layout")
        assert value is payload
        return layout
    def render(value):
        seen.append("render")
        assert value is layout
        return "test.png"
    def photo(*args, **kwargs):
        seen.append("send_photo")
    monkeypatch.setattr(lb, "build_payload_for_location", fetch)
    monkeypatch.setattr(lb, {"day": "prepare_layout_data", "now": "prepare_now_layout_data", "future": "prepare_future_layout_data"}[card], prepare)
    monkeypatch.setattr(lb.image_generator, "generate_weather_card", render)
    monkeypatch.setattr(lb, "send_photo", photo)
    bot.run(bot.msg(CID, command))
    assert seen == ["payload", "layout", "render", "send_photo"]
    assert bot.replies(CID) == [lb.t_ui("pl", lb.SAVED_PROFILE_WAIT_KEYS[card])]
    rows = records(capsys)
    if flag == "0":
        assert rows == []
    else:
        stages = [r["stage"] for r in rows]
        ordered = ["waiting_message_before", "waiting_message_after", "payload_before", "payload_after", "layout_before", "layout_after", "render_before", "render_after", "send_photo_before", "send_photo_after"]
        assert [s for s in stages if s in ordered] == ordered
        assert len({r["request_id"] for r in rows if r["operation"] in ("waiting_message", "payload", "layout", "render", "send_photo")}) == 1


@pytest.mark.parametrize("flag", ["0", "1"])
def test_silent_gatekeeper_unchanged(bot, monkeypatch, capsys, flag):
    monkeypatch.setenv("PERF_DEBUG", flag)
    bot.run(bot.msg(-1005555555, "/teraz " + CANARY))
    assert bot.sent == [] and bot.oneoffs == []
    rows = records(capsys)
    if flag == "1":
        assert ends(rows, "dispatcher")[0]["outcome"] == "skipped"
        assert not ends(rows, "payload")


@pytest.mark.parametrize("source", ["openmeteo", "yrno", "airly", "owm"])
def test_source_and_http_boundaries_distinct_and_safe(monkeypatch, capsys, source):
    monkeypatch.setenv("PERF_DEBUG", "1")
    response = SimpleNamespace(status_code=200)
    def source_call():
        perf.call("weather." + source + ".http", lambda *a, **k: response, CANARY, headers={"apikey": CANARY})
        return {"sensitive": CANARY}
    run(lambda: perf.call("weather." + source, source_call))
    rows = records(capsys)
    assert len(ends(rows, "weather." + source)) == 1
    assert len(ends(rows, "weather." + source + ".http")) == 1
    assert CANARY not in json.dumps(rows)


@pytest.mark.parametrize("key", [None, ""])
def test_airly_without_key_enters_source_but_never_http(monkeypatch, capsys, key):
    monkeypatch.setenv("PERF_DEBUG", "1")
    if key is None:
        monkeypatch.delenv("AIRLY_API_KEY", raising=False)
    else:
        monkeypatch.setenv("AIRLY_API_KEY", key)
    def forbidden(*args, **kwargs):
        pytest.fail("Airly must skip HTTP with a missing/empty key")
    monkeypatch.setattr(wp.requests, "get", forbidden)
    assert run(lambda: perf.call("weather.airly", wp._fetch_airly, 52.23, 21.012)) is None
    rows = records(capsys)
    assert ends(rows, "weather.airly")[0]["outcome"] == "skipped"
    assert not ends(rows, "weather.airly.http")


def test_airly_real_http_error_and_swallowed_error_are_closed(monkeypatch, capsys):
    monkeypatch.setenv("PERF_DEBUG", "1")
    monkeypatch.setenv("AIRLY_API_KEY", CANARY)
    def fail(*args, **kwargs):
        raise requests.Timeout(CANARY)
    monkeypatch.setattr(wp.requests, "get", fail)
    assert run(lambda: perf.call("weather.airly", wp._fetch_airly, 52.23, 21.012)) is None
    rows = records(capsys)
    assert ends(rows, "weather.airly.http")[0]["outcome"] == "error"
    assert ends(rows, "weather.airly")[0]["outcome"] == "error"
    assert CANARY not in json.dumps(rows)


@pytest.mark.parametrize("cached,breaker", [(True, False), (False, True)])
def test_openmeteo_cache_and_breaker_do_not_issue_http(monkeypatch, capsys, cached, breaker):
    monkeypatch.setenv("PERF_DEBUG", "1")
    tz = ZoneInfo("Europe/Warsaw")
    key = (52.23, 21.012, str(tz))
    data = [{"time_local": CANARY}]
    monkeypatch.setattr(wp, "_OPENMETEO_CACHE", {key: (time.time(), data)} if cached else {})
    monkeypatch.setattr(wp, "_OPENMETEO_DOWN_UNTIL", time.time() + 1000 if breaker else 0)
    def forbidden():
        pytest.fail("No HTTP session should be created on cache hit/breaker")
    monkeypatch.setattr(wp, "_get_retry_session", forbidden)
    result = run(lambda: perf.call("weather.openmeteo", wp._fetch_openmeteo, 52.23, 21.012, tz))
    assert result is data if cached else result == []
    rows = records(capsys)
    row = ends(rows, "weather.openmeteo")[0]
    assert (row["cache"], row["outcome"]) == (("hit", "ok") if cached else ("miss", "skipped"))
    assert not ends(rows, "weather.openmeteo.http")


def test_openmeteo_swallowed_timeout_closes_source_and_http(monkeypatch, capsys):
    monkeypatch.setenv("PERF_DEBUG", "1")
    monkeypatch.setattr(wp, "_OPENMETEO_CACHE", {})
    monkeypatch.setattr(wp, "_OPENMETEO_DOWN_UNTIL", 0)
    def fail(*args, **kwargs):
        raise requests.Timeout(CANARY)
    monkeypatch.setattr(wp, "_get_retry_session", lambda: SimpleNamespace(get=fail))
    assert run(lambda: perf.call("weather.openmeteo", wp._fetch_openmeteo, 52.23, 21.012, ZoneInfo("UTC"))) == []
    rows = records(capsys)
    assert ends(rows, "weather.openmeteo.http")[0]["outcome"] == "error"
    assert ends(rows, "weather.openmeteo")[0]["outcome"] == "error"


@pytest.mark.parametrize("stage", ["payload", "layout", "render", "send_photo"])
def test_pipeline_failure_stops_at_same_point_and_closes_marker(bot, monkeypatch, capsys, stage):
    monkeypatch.setenv("PERF_DEBUG", "1")
    monkeypatch.setattr(lb, "_send_oneoff_report", REAL_SEND_ONEOFF_REPORT)
    seen = []
    def step(name, value):
        def fn(*args, **kwargs):
            seen.append(name)
            if name == stage:
                raise RuntimeError(CANARY)
            return value
        return fn
    monkeypatch.setattr(lb, "build_payload_for_location", step("payload", {}))
    monkeypatch.setattr(lb, "prepare_now_layout_data", step("layout", {}))
    monkeypatch.setattr(lb.image_generator, "generate_weather_card", step("render", "test.png"))
    monkeypatch.setattr(lb, "send_photo", step("send_photo", None))
    bot.run(bot.msg(CID, "/teraz"))
    order = ["payload", "layout", "render", "send_photo"]
    assert seen == order[:order.index(stage) + 1]
    rows = records(capsys)
    assert ends(rows, stage)[0]["outcome"] == "error"
    assert ends(rows, "card")[0]["outcome"] == "error"
    assert CANARY not in json.dumps(rows)


def test_smoke_has_independent_context_and_keeps_existing_selection(monkeypatch, capsys):
    monkeypatch.setenv("PERF_DEBUG", "1")
    monkeypatch.setattr(mc, "_load_scheduler_users_from_sheet", lambda: [])
    mc.run_smoke_test(str(CID))
    rows = records(capsys)
    assert ends(rows, "smoke")[0]["outcome"] == "skipped"
    assert not any(r["stage"] in ("update_received", "dispatcher_started") for r in rows)
    assert str(CID) not in json.dumps(rows)


@pytest.mark.parametrize("enabled_owm", [False, True])
def test_payload_equivalence_and_source_order_with_debug_off_and_on(monkeypatch, capsys, enabled_owm):
    import copy
    from datetime import datetime
    from test_integration import _make_hour
    now = datetime(2026, 10, 17, 10, tzinfo=ZoneInfo("Europe/Warsaw"))
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(wp, "datetime", Frozen)
    monkeypatch.setattr(wp, "ENABLE_OWM_NOWCAST", enabled_owm)
    monkeypatch.setenv("AIRLY_API_KEY", "")
    hour = _make_hour("2026-10-17", 11)
    outputs = []
    for flag in ("0", "1"):
        monkeypatch.setenv("PERF_DEBUG", flag)
        seen = []
        def om(*args):
            seen.append("openmeteo")
            return [copy.deepcopy(hour)]
        def yr(*args):
            seen.append("yrno")
            return [], None
        def live(*args, **kwargs):
            seen.append("owm")
            return {"sensitive": CANARY}
        monkeypatch.setattr(wp, "_fetch_openmeteo", om)
        monkeypatch.setattr(wp, "_fetch_yrno", yr)
        monkeypatch.setattr(wp, "_get_owm_cached", live)
        outputs.append(run(lambda: perf.call("payload", wp.build_payload_for_location, 52.23, 21.012, "Europe/Warsaw", CANARY)))
        assert seen == ["openmeteo", "yrno"] + (["owm"] if enabled_owm else [])
        rows = records(capsys)
        if flag == "0":
            assert rows == []
        else:
            assert [r["operation"] for r in rows if r["stage"] == "weather_source_before"] == ["weather.openmeteo", "weather.yrno", "weather.airly", "weather.owm"]
            assert ends(rows, "weather.airly")[0]["outcome"] == "skipped"
            assert ends(rows, "weather.owm")[0]["outcome"] == ("ok" if enabled_owm else "skipped")
            assert ends(rows, "weather.yrno")[0]["outcome"] == "unavailable"
            assert CANARY not in json.dumps(rows)
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize("source", ["openmeteo", "yrno"])
def test_real_forecast_fetch_preserves_request_arguments_and_retry_config(monkeypatch, capsys, source):
    monkeypatch.setenv("PERF_DEBUG", "1")
    monkeypatch.setattr(wp, "_OPENMETEO_CACHE", {})
    monkeypatch.setattr(wp, "_OPENMETEO_DOWN_UNTIL", 0)
    seen = []
    def get(url, **kwargs):
        seen.append(kwargs)
        body = ({"hourly": {"time": []}} if source == "openmeteo" else
                {"properties": {"timeseries": [], "meta": {"updated_at": "2026-10-17T10:00:00Z"}}})
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: body)
    monkeypatch.setattr(wp.requests.Session, "get", lambda self, url, **kwargs: get(url, **kwargs))
    result = run(lambda: perf.call("weather." + source, getattr(wp, "_fetch_" + source), 52.23, 21.012, ZoneInfo("UTC")))
    assert result == [] if source == "openmeteo" else result[0] == []
    assert len(seen) == 1
    assert seen[0]["timeout"] == (8 if source == "openmeteo" else 20)
    adapter = wp._get_retry_session().get_adapter("https://")
    assert adapter.max_retries.total == 2  # unchanged retry policy
    rows = records(capsys)
    assert ends(rows, "weather." + source + ".http")[0]["outcome"] == "ok"


@pytest.mark.parametrize("mode", ["disabled", "outer_hit", "inner_hit", "limit", "http", "error"])
def test_real_owm_cache_skips_and_http_preserve_existing_behavior(monkeypatch, capsys, mode):
    monkeypatch.setenv("PERF_DEBUG", "1")
    monkeypatch.setenv("OWM_API_KEY", "" if mode == "disabled" else CANARY)
    monkeypatch.setattr(wp, "_OWM_CACHE", {})
    monkeypatch.setattr(owm, "_CACHE", {})
    now = time.time()
    value = {"sensitive": CANARY}
    if mode == "outer_hit":
        wp._OWM_CACHE[(52.23, 21.01)] = (now, value)
    if mode == "inner_hit":
        owm._CACHE[owm._key(52.23, 21.012)] = (now, value)
    checks, calls = [], []
    def limit():
        checks.append(True)
        return mode != "limit"
    def get(url, **kwargs):
        calls.append(kwargs)
        if mode == "error":
            raise requests.Timeout(CANARY)
        return SimpleNamespace(status_code=200, json=lambda: value)
    monkeypatch.setattr(owm, "_check_and_increment_limit", limit)
    monkeypatch.setattr(owm.requests, "get", get)
    result = run(lambda: perf.call("weather.owm", wp._get_owm_cached, 52.23, 21.012))
    assert result == (None if mode in ("disabled", "limit", "error") else value)
    assert len(calls) == (1 if mode in ("http", "error") else 0)
    assert len(checks) == (1 if mode in ("limit", "http", "error") else 0)
    if calls:
        assert calls[0]["timeout"] == 8 and calls[0]["params"]["appid"] == CANARY
    rows = records(capsys)
    row = ends(rows, "weather.owm")[0]
    assert row["outcome"] == ("skipped" if mode in ("disabled", "limit") else "error" if mode == "error" else "ok")
    assert row["cache"] == ("hit" if mode in ("outer_hit", "inner_hit") else "miss")
    assert CANARY not in json.dumps(rows)


@pytest.mark.parametrize("reverse", [False, True])
def test_real_geocode_boundaries_cache_and_order(monkeypatch, capsys, reverse):
    from test_geocode_validation import poland_place
    monkeypatch.setenv("PERF_DEBUG", "1")
    monkeypatch.setattr(lb, "_GEO_DETAILS_CACHE", {})
    seen = []
    result = poland_place("Hel")
    def forward(*args, **kwargs):
        seen.append("forward")
        return [result]
    def backward(*args, **kwargs):
        seen.append("reverse")
        return result
    monkeypatch.setattr(lb, "Nominatim", lambda **kwargs: SimpleNamespace(geocode=forward, reverse=backward))
    if reverse:
        first = run(lambda: lb.get_location_details_from_coords(52.23, 21.012))
        second = run(lambda: lb.get_location_details_from_coords(52.23, 21.012))
        assert first == second and first[-1] == lb.GEO_OK
        assert seen == ["reverse"]
        assert [r["cache"] for r in ends(records(capsys), "geocode.reverse")] == ["miss", "hit"]
    else:
        output = run(lambda: lb.geocode_city_accepted("Hel"))
        assert output[0] == lb.GEOCODE_OK and seen == ["forward"]
        assert ends(records(capsys), "geocode.forward")[0]["outcome"] == "ok"


def test_users_429_closes_snapshot_before_backoff_without_dispatch(bot, monkeypatch, capsys):
    from test_sheets_resilience import FakeGoogle429
    monkeypatch.setenv("PERF_DEBUG", "1")
    def fail():
        raise FakeGoogle429()
    sleeps = []
    monkeypatch.setattr(bot.users, "get_all_values", fail)
    monkeypatch.setattr(lb.time, "sleep", sleeps.append)
    bot.run(bot.msg(CID, "/teraz"))
    assert sleeps == [30]
    rows = records(capsys)
    assert ends(rows, "snapshot_users")[0]["outcome"] == "error"
    assert ends(rows, "poll")[0]["outcome"] == "error"
    assert not any(r["stage"] == "dispatcher_started" for r in rows)


def test_observing_result_cannot_change_return_value(monkeypatch, capsys):
    monkeypatch.setenv("PERF_DEBUG", "1")
    class Opaque:
        def __eq__(self, other):
            raise RuntimeError(CANARY)
    value = Opaque()
    assert run(lambda: perf.call("weather.airly", lambda: value)) is value
    assert CANARY not in json.dumps(records(capsys))


@pytest.mark.parametrize("card", ["day", "now", "future"])
def test_smoke_actual_card_pipeline_marks_all_stages(bot, monkeypatch, capsys, card):
    monkeypatch.setenv("PERF_DEBUG", "1")
    assert users_store.set_report_settings(bot.users, CID, "08:00", "14:00")
    monkeypatch.setattr(mc, "_load_scheduler_users_from_sheet", lambda: users_store._read_rows(bot.users))
    monkeypatch.setattr(mc, "_resolve_tz", lambda *a: "UTC")
    seen = []
    def fetch(**kwargs):
        seen.append("payload")
        return {"hours": [{"source": "openmeteo"}, {"source": "yrno"}]}
    def layout(payload):
        seen.append("layout")
        return {}
    monkeypatch.setattr(mc, "build_payload_for_location", fetch)
    monkeypatch.setattr(mc, {"day": "prepare_layout_data", "now": "prepare_now_layout_data", "future": "prepare_future_layout_data"}[card], layout)
    monkeypatch.setattr(mc, "run_sanity_check", lambda *a: [])
    monkeypatch.setattr(mc.image_generator, "generate_weather_card", lambda *a: seen.append("render") or "test.png")
    monkeypatch.setattr(mc, "send_telegram_photo", lambda *a, **k: seen.append("send_photo") or True)
    mc.run_smoke_test(str(CID), is_now=card == "now", is_future=card == "future")
    assert seen == ["payload", "layout", "render", "send_photo"]
    rows = records(capsys)
    assert len({r["request_id"] for r in rows}) == 1
    for operation in ("snapshot_users", "payload", "layout", "render", "send_photo", "smoke"):
        assert len(ends(rows, operation)) == 1
        assert ends(rows, operation)[0]["outcome"] == "ok"
    assert not any(r["stage"] in ("update_received", "dispatcher_started") for r in rows)
    assert CANARY not in json.dumps(rows) and str(CID) not in json.dumps(rows)


def test_guest_pipeline_cache_and_stages_are_not_double_counted(bot, monkeypatch, capsys):
    monkeypatch.setenv("PERF_DEBUG", "1")
    monkeypatch.setattr(lb, "handle_guest_now", guest.handle_guest_now)
    monkeypatch.setattr(guest, "_GEO_CACHE", {})
    monkeypatch.setattr(lb, "build_payload_for_location", lambda **kwargs: {"synthetic": True})
    monkeypatch.setattr(lb, "prepare_now_layout_data", lambda payload: {})
    monkeypatch.setattr(lb.image_generator, "generate_weather_card", lambda *a: "test.png")
    monkeypatch.setattr(lb, "send_photo", lambda *a, **k: None)
    for _ in range(2):
        bot.run(bot.msg(CID, "?12 Warszawa"))
    rows = records(capsys)
    assert [r["cache"] for r in ends(rows, "geocode.guest")] == ["miss", "hit"]
    for operation in ("waiting_message", "payload", "layout", "render", "send_photo"):
        assert len(ends(rows, operation)) == 2
    for received in (r for r in rows if r["stage"] == "update_received"):
        stages = [r["stage"] for r in rows if r["request_id"] == received["request_id"]]
        assert stages.index("waiting_message_after") < stages.index("geocode_before")
        assert stages.index("geocode_after") < stages.index("payload_before")


@pytest.mark.parametrize("kind", ["waiting", "photo"])
def test_swallowed_telegram_errors_close_stage_without_changing_contract(bot, monkeypatch, capsys, tmp_path, kind):
    monkeypatch.setenv("PERF_DEBUG", "1")
    def fail(*a, **k):
        raise requests.Timeout(CANARY)
    monkeypatch.setattr(lb.requests, "post", fail)
    if kind == "waiting":
        result = run(lambda: perf.call("waiting_message", lb.send_reply, CID, CANARY))
        operation = "waiting_message"
    else:
        path = tmp_path / "photo.png"
        path.write_bytes(b"synthetic")
        result = run(lambda: perf.call("send_photo", lb.send_photo, CID, str(path), caption=CANARY))
        operation = "send_photo"
    assert result is None  # preserved legacy send function contract
    rows = records(capsys)
    assert ends(rows, operation)[0]["outcome"] == "error"
    assert ends(rows, operation + ".http")[0]["outcome"] == "error"
    assert CANARY not in json.dumps(rows)
