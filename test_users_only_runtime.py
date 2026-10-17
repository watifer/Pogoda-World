"""Users-only runtime contract, including denied legacy access and entry cost.

Only synthetic profiles/weather are used. No Sheets, Telegram, weather or
geocoder sockets are allowed by pytest.ini. The archived decoy records attempts
BEFORE raising so broad production exception handlers cannot hide a regression.
"""
from datetime import datetime
from pathlib import Path
import ast
import copy
import time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import db_cleanup
import dev_preview
import guest_bot_handler
import image_generator
import location_bot as lb
import main_card
import users_store
from test_access_gate import (BotHarness, freeze_local_hour, REAL_SEND_ONEOFF_REPORT,
                              REAL_GET_OFFSET)

PRIVATE = 42420
GROUP = -10042420
AUTHOR = 98989


@pytest.fixture
def bot(monkeypatch):
    harness = BotHarness(monkeypatch)
    freeze_local_hour(monkeypatch, 10)
    monkeypatch.setattr(lb.time, "sleep", lambda seconds: None)
    for cid in (PRIVATE, GROUP):
        assert users_store.upsert_access(harness.users, cid, "pl", "legacy",
                                         "2026-01-01 10:00:00", "v1")
        assert users_store.set_profile(harness.users, cid, 52.23, 21.012,
                                       "Test City", "city", "pl", "v1",
                                       "2026-01-01 10:00:00")
    # A tempting but inaccessible archive must never influence any path.
    harness.gc.formularz.rows = [["2020-01-01", str(PRIVATE), "Old name",
                                  "Wrong City", "1", "2", "01:00", "02:00", "de"]]
    return harness


@pytest.mark.parametrize("cid", [PRIVATE, GROUP])
@pytest.mark.parametrize("command,card", [("/dzien", "day"), ("/teraz", "now"),
                                         ("/trend", "future")])
def test_saved_cards_use_users_and_group_chat_not_author(bot, cid, command, card):
    bot.run(bot.msg(cid, command, user_id=AUTHOR))
    assert [(o[0], o[1], o[2], o[3], o[5]) for o in bot.oneoffs] == [
        (cid, 52.23, 21.012, "Test City", card)]
    assert bot.users.record(AUTHOR) is None
    bot.assert_users_only()


@pytest.mark.parametrize("cid", [PRIVATE, GROUP])
@pytest.mark.parametrize("command,card", [("/dzien Warszawa", "day"),
                                         ("/teraz Warszawa", "now"),
                                         ("/trend Warszawa", "future")])
def test_oneoff_uses_users_access_and_never_writes_archive(bot, cid, command, card):
    before = bot.users.record(cid)
    bot.run(bot.msg(cid, command, user_id=AUTHOR))
    assert bot.oneoffs[0][5] == card
    assert bot.users.record(cid) == before
    bot.assert_users_only()


@pytest.mark.parametrize("shortcut,card", [("?d", "day"), ("?12", "now"), ("?14", "future")])
def test_shortcut_prompt_and_city_without_formularz(bot, shortcut, card):
    before = bot.users.record(PRIVATE)
    bot.run(bot.msg(PRIVATE, shortcut))
    bot.run(bot.msg(PRIVATE, "Warszawa"))
    assert bot.oneoffs[0][5] == card
    assert bot.users.record(PRIVATE) == before
    bot.assert_users_only()


@pytest.mark.parametrize("cid", [PRIVATE, GROUP])
@pytest.mark.parametrize("shortcut", ["?d Warszawa", "?12 Warszawa", "?14 Warszawa"])
def test_guest_shortcuts_do_not_open_formularz(bot, monkeypatch, cid, shortcut):
    photos = []
    monkeypatch.setattr(lb, "handle_guest_now", guest_bot_handler.handle_guest_now)
    monkeypatch.setattr(guest_bot_handler, "_GEO_CACHE", {})
    monkeypatch.setattr(lb, "build_payload_for_location", lambda **kw: {
        "location": {"tz": "Europe/Warsaw"}})
    for name in ("prepare_layout_data", "prepare_now_layout_data", "prepare_future_layout_data"):
        monkeypatch.setattr(lb, name, lambda payload: {})
    monkeypatch.setattr(image_generator, "generate_weather_card", lambda layout: "fake.png")
    monkeypatch.setattr(lb, "send_photo", lambda *args, **kwargs: photos.append(args))
    bot.run(bot.msg(cid, shortcut, user_id=AUTHOR))
    assert len(photos) == 1 and photos[0][0] == cid
    bot.assert_users_only()


@pytest.mark.parametrize("cid", [PRIVATE, GROUP])
def test_city_data_forget_and_delete_use_only_users(bot, cid):
    bot.run(bot.msg(cid, "/miasto Warszawa", user_id=AUTHOR))
    assert bot.users.record(cid)["location_source"] == "city"
    bot.run(bot.msg(cid, "/dane", user_id=AUTHOR))
    assert "Wrong City" not in bot.replies(cid)[-1]
    bot.run(bot.msg(cid, "/bezGPS", user_id=AUTHOR))
    record = bot.users.record(cid)
    assert record["access_status"] == "granted" and record["profile_status"] == "none"
    assert all(record[field] == "" for field in users_store.PROFILE_COLS)
    bot.run(bot.msg(cid, "/usunDane", user_id=AUTHOR))
    bot.run(bot.msg(cid, "Tak, chcę", user_id=AUTHOR))
    assert bot.users.record(cid) is None
    before = list(bot.sent)
    for command in ("/teraz", "/dane", "/bezGPS", "?12 Warszawa"):
        bot.run(bot.msg(cid, command, user_id=AUTHOR))
    assert bot.sent == before
    bot.assert_users_only()


@pytest.mark.parametrize("cid", [PRIVATE, GROUP])
def test_pin_profile_and_webapp_use_only_users(bot, cid):
    bot.run(bot.pin(cid, reply_to_bot=True, user_id=AUTHOR))
    assert bot.users.record(cid)["location_source"] == "gps"
    bot.run(bot.msg(cid, "/miasto", user_id=AUTHOR))
    bot.run(bot.webapp_location(cid))
    assert bot.users.record(cid)["location_source"] == "webapp"
    assert bot.oneoffs == []
    bot.assert_users_only()


@pytest.mark.parametrize("cid", [PRIVATE, GROUP])
def test_report_dialog_saves_users_only(bot, monkeypatch, cid):
    monkeypatch.setattr(lb, "_telegram_chat_member_status", lambda *args: "administrator")
    bot.run(bot.msg(cid, "/raport", user_id=AUTHOR))
    for text in ("tak", "07:30", "14:30"):
        bot.run(bot.msg(cid, text, user_id=AUTHOR))
    record = bot.users.record(cid)
    assert (record["report_morning_time"], record["report_afternoon_time"]) == ("07:30", "14:30")
    bot.assert_users_only()


@pytest.mark.parametrize("cid", [PRIVATE, GROUP])
def test_send_message_block_cleanup_uses_users_only(bot, monkeypatch, cid):
    response = SimpleNamespace(status_code=403, json=lambda: {
        "ok": False, "description": "bot was blocked by the user"})
    monkeypatch.setattr(lb.requests, "post", lambda *args, **kwargs: response)
    lb.send_reply(cid, "test")
    record = bot.users.record(cid)
    assert record["access_status"] == "blocked" and record["blocked_at"]
    assert record["blocked_reason"] == "telegram_blocked"
    assert record["profile_status"] == "none"
    bot.assert_users_only()


@pytest.mark.parametrize("cid", [PRIVATE, GROUP])
def test_send_photo_block_cleanup_never_opens_archive(bot, monkeypatch, tmp_path, cid):
    import gspread
    from google.oauth2.service_account import Credentials
    monkeypatch.setattr(Credentials, "from_service_account_file", lambda *a, **k: object())
    monkeypatch.setattr(gspread, "authorize", lambda creds: bot.gc)
    monkeypatch.setattr(main_card.requests, "post", lambda *a, **k: SimpleNamespace(
        status_code=403, json=lambda: {"ok": False, "error_code": 403,
                                       "description": "bot was blocked by the user"}))
    image = tmp_path / "photo.png"
    image.write_bytes(b"synthetic-upload")
    assert main_card.send_telegram_photo(str(cid), str(image)) is False
    assert bot.users.record(cid)["access_status"] == "blocked"
    bot.assert_users_only()


@pytest.mark.parametrize("cid", [12345, -10012345])
def test_archive_only_chat_never_gains_access_seat_or_referral(monkeypatch, cid):
    bot = BotHarness(monkeypatch)
    bot.gc.formularz.rows.append(["2020-01-01", str(cid), "Archived", "City",
                                  "52", "21", "08:00", "14:00", "pl"])
    bot.run(bot.msg(cid, "/teraz"))
    assert bot.sent == [] and bot.oneoffs == []
    assert lb._active_seats_count({}) == 0
    assert lb._validate_access_token(str(cid), {}) == (False, "")
    bot.assert_users_only()


def test_users_unavailable_does_not_fall_back(bot, monkeypatch):
    monkeypatch.setattr(bot.users, "get_all_values", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    bot.run(bot.msg(PRIVATE, "/teraz"))
    assert bot.sent == [] and bot.oneoffs == []
    bot.assert_users_only()


def test_bootstrap_keeps_bot_state_without_formularz(bot, monkeypatch, tmp_path):
    path = tmp_path / "offset.json"
    monkeypatch.setenv("BOT_OFFSET_FILE", str(path))
    assert REAL_GET_OFFSET(bot.gc) == 0
    assert path.exists()
    assert bot.gc.requested_tabs == ["Bot_State"]
    bot.assert_users_only()


@pytest.mark.parametrize("card", ["day", "now", "future"])
def test_users_profile_real_layout_and_renderer(bot, monkeypatch, tmp_path, card):
    now = datetime(2026, 9, 30, 10, tzinfo=ZoneInfo("Europe/Warsaw"))
    payload = dev_preview.build_payload(dev_preview.SCENARIOS["clear"], "pl", now, 24 * 16)
    payload["location"].update(name="Test City", lat=52.23, lon=21.012)
    monkeypatch.setattr(lb, "build_payload_for_location", lambda **kw: copy.deepcopy(payload))
    from PIL import Image
    photos = []
    def send(cid, path, **kw):
        with Image.open(path) as image:
            assert image.width == 1080 and image.height > 0
        photos.append((cid, path))
    monkeypatch.setattr(image_generator, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(lb, "send_photo", send)
    # Real pipeline and renderer; only fetch and Telegram are faked.
    for name in ("prepare_layout_data", "prepare_now_layout_data", "prepare_future_layout_data"):
        real_layout = getattr(lb, name)
        monkeypatch.setattr(lb, name, lambda payload, fn=real_layout: fn(payload, now=now))
    monkeypatch.setattr(lb, "_send_oneoff_report", REAL_SEND_ONEOFF_REPORT)
    bot.run(bot.msg(PRIVATE, {"day": "/dzien", "now": "/teraz", "future": "/trend"}[card]))
    assert len(photos) == 1
    bot.assert_users_only()


def test_no_legacy_reader_writer_or_snapshot_survives():
    forbidden = {"_load_form_snapshot", "_load_future_users_from_sheet", "_form_update_cell",
                 "_clear_legacy_location", "_delete_legacy_rows", "_is_legacy_only_user",
                 "_legacy_user_row", "_FORM_SNAPSHOT", "_load_users_from_sheet",
                 "_parse_users", "wirtualne_scalanie", "virtual_merge"}
    for module in (lb, main_card, db_cleanup):
        assert not forbidden.intersection(vars(module))
        for node in ast.walk(ast.parse(Path(module.__file__).read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in ("Formularz", "Form_Responses4", "Arkusz1", "Sheet1")


@pytest.mark.parametrize("command", ["/dzien", "/teraz", "/trend"])
@pytest.mark.parametrize("cache_hit", [False, True])
def test_entry_cost_dual_read_vs_users_only(bot, monkeypatch, command, cache_hit):
    """Deterministic network-budget benchmark, NOT production milliseconds.

    The test-only baseline reconstructs the removed pre-dispatch Form read,
    leaving current Users loading and routing identical in both runs. Each
    values read advances a virtual monotonic clock by exactly 20 ms; no sleep
    or real HTTP. Cold metadata costs and historical merge CPU are excluded.
    Cache-hit runs prime both snapshots, then measure the second update.
    """
    real_loader = lb._load_users_snapshot
    results = {}
    for variant in ("dual-read", "users-only"):
        lb._reset_polling_state()
        bot.gc.requested_tabs.clear()
        clock = [1_000_000_000]
        reads = []
        dispatch = []
        card_start = []
        form_loaded = [False]
        def users_values():
            reads.append("Users")
            clock[0] += 20_000_000
            return [row[:] for row in bot.users.grid]
        class BaselineForm:
            def get_all_values(self):
                reads.append("Formularz")
                clock[0] += 20_000_000
                return [["Chat ID", "Lat", "Lon"], [str(PRIVATE), "52.23", "21.012"]]
        baseline_form = BaselineForm()
        class BaselineSpreadsheet:
            def worksheet(self, name):
                if name == "Formularz":
                    return baseline_form
                return bot.gc.worksheet(name)
        baseline_gc = SimpleNamespace(open=lambda title: BaselineSpreadsheet())
        def loader(gc):
            if variant == "dual-read" and not form_loaded[0]:
                # Reconstruct the former Form open/read before real Users load.
                values = gc.open("Pogoda_Users").worksheet("Formularz").get_all_values()
                records = [dict(zip(values[0], row)) for row in values[1:]]
                assert records[0]["Chat ID"] == str(PRIVATE)
                form_loaded[0] = True
            return real_loader(gc)
        real_extract = lb._extract_update_chat_id
        def extract(update):
            dispatch.append(clock[0])
            return real_extract(update)
        def card(*args, **kwargs):
            card_start.append(clock[0])
            return True
        with monkeypatch.context() as patch:
            patch.setattr(lb, "get_google_client",
                          lambda: baseline_gc if variant == "dual-read" else bot.gc)
            patch.setattr(lb.time, "monotonic", lambda: clock[0] / 1e9)
            patch.setattr(lb.time, "monotonic_ns", lambda: clock[0])
            patch.setattr(bot.users, "get_all_values", users_values)
            patch.setattr(lb, "_load_users_snapshot", loader)
            patch.setattr(lb, "_extract_update_chat_id", extract)
            patch.setattr(lb, "_send_oneoff_report", card)
            # Waiting-message RTT is not part of the comparison.
            patch.setattr(lb, "send_reply", lambda *a, **k: None)
            if cache_hit:
                bot._updates.append(bot.msg(PRIVATE, command))
                lb.main_bot()
                reads.clear()
                dispatch.clear()
                card_start.clear()
            before = time.monotonic_ns()
            bot._updates.append(bot.msg(PRIVATE, command))
            lb.main_bot()  # not BotHarness.run(), which intentionally invalidates TTL
            results[variant] = {
                "sheets_values_reads": list(reads),
                "before_dispatch_ms": (dispatch[0] - before) / 1e6,
                "update_to_card_ms": (card_start[0] - before) / 1e6,
            }
        bot.assert_users_only()
    expected_dual = [] if cache_hit else ["Formularz", "Users"]
    expected_users = [] if cache_hit else ["Users"]
    assert results["dual-read"]["sheets_values_reads"] == expected_dual
    assert results["users-only"]["sheets_values_reads"] == expected_users
    saving = 0 if cache_hit else 20
    for metric in ("before_dispatch_ms", "update_to_card_ms"):
        assert results["dual-read"][metric] - results["users-only"][metric] == saving
    print(f"SYNTHETIC entry benchmark command={command} cache_hit={cache_hit}: {results}")


def test_scheduler_uses_users_for_private_and_group_with_forbidden_archive(bot, monkeypatch):
    monkeypatch.setattr(main_card, "SHEET_ID", "")
    monkeypatch.setattr(main_card, "SHEET_TAB_ENV", "Formularz")
    monkeypatch.setattr(bot.users, "get_values", lambda range_name: bot.users.get_all_values(), raising=False)
    for cid in (PRIVATE, GROUP):
        assert users_store.set_report_settings(bot.users, cid, "08:00", "14:00")
    rows = main_card._load_scheduler_users_from_sheet(client=bot.gc)
    selected = main_card._parse_scheduler_users(rows)
    assert {row["chat_id"] for row in selected} == {str(PRIVATE), str(GROUP)}
    assert all(row["godzina_rano"] == "08:00" and row["godzina_wieczor"] == "14:00"
               for row in selected)
    assert bot.gc.requested_tabs == ["Users"]
    bot.assert_users_only()


@pytest.mark.parametrize("attempt", ["open", "api"])
def test_forbidden_probe_detects_even_swallowed_exceptions(bot, attempt):
    # Guard the guard: raising inside a broad production try/except is not enough.
    try:
        if attempt == "open":
            bot.gc.open("Pogoda_Users").worksheet("Formularz")
        else:
            bot.gc.formularz.get_all_values()
    except Exception:
        pass
    with pytest.raises(AssertionError):
        bot.assert_users_only()
