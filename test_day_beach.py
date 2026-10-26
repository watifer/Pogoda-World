"""Offline day-only warm beach policy. Legacy classifier/now remain unchanged."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta
import inspect
from pathlib import Path
import sys
import types
from zoneinfo import ZoneInfo

import pytest
import requests

import coast_detector as cd
import prepare_layout as day
import prepare_now_layout as radar
from test_coast import NY_SIG, _coast_alerts, _hour_row

TZ = ZoneInfo("America/Cancun")
NOW = datetime(2026, 1, 15, 12, 25, tzinfo=TZ)
VERSION = "ne_50m_ocean:50m;radar:v2;r25;step10;minw20;marine75g90;full360"


def payload_at(now=NOW):
    rows = []
    for hh in range(24):
        h = _hour_row(now.date().isoformat(), hh, now.tzinfo, wind=5, gust=8, wind_dir=150)
        h.update(temp_c=24.0, clouds_low_pct=15.0, clouds_mid_pct=20.0,
                 clouds_pct=35.0, precip_prob_pct=0.0)
        rows.append(h)
    return dict(location=dict(name="Synthetic coast", lat=21.16, lon=-86.85,
                              tz=str(now.tzinfo)), hours=rows, lang="en",
                forecast_source="OpenMeteo", airly=None, owm_current=None)


def gate(p, now=NOW):
    hours = p["hours"]
    primary = [h for h in hours if h.get("source") == "openmeteo"] or [
        h for h in hours if h.get("source") == "yrno"]
    return day._day_beach_weather_eligible(primary, hours, now, ZoneInfo(p["location"]["tz"]))


def add_alternate(p):
    alt = deepcopy(p["hours"])
    for h in alt:
        h["source"] = "yrno"
    p["hours"].extend(alt)
    return alt


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for key in ("MARINE_STORM_WIND_KMH", "MARINE_STORM_GUST_KMH",
                "MARINE_STORM_MIN_GUST_WITH_WIND_KMH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ENABLE_GLOBAL_MARINE_STORM", "1")
    monkeypatch.setenv("ENABLE_WK_AI_CANDIDATE", "0")
    def forbidden(*args, **kwargs):
        pytest.fail("No external HTTP is permitted in day beach tests")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(day, "get_current_weather", lambda *a, **k: None)
    monkeypatch.setattr(radar, "get_current_weather", lambda *a, **k: None)


@pytest.fixture
def coast(monkeypatch):
    runtime = types.ModuleType("coast_runtime")
    runtime.GLOBAL_COAST_STORE = object()
    runtime.ensure_coast_index = lambda: None
    monkeypatch.setitem(sys.modules, "coast_runtime", runtime)
    monkeypatch.setattr(cd, "GEO_STACK_AVAILABLE", True)
    calls = []
    def signature(**kwargs):
        calls.append(kwargs)
        return NY_SIG
    monkeypatch.setattr(cd, "get_or_compute_coast_signature_lazy", signature)
    return calls


def test_classifier_default_and_keyword_only_compatibility():
    date = datetime(2026, 7, 10, 12, tzinfo=ZoneInfo("Europe/Warsaw"))
    args = (NY_SIG, 18, 18, 150, "Europe/Warsaw", date)
    assert cd.get_coastal_alert_mode(*args) == cd.MODE_BEACH
    assert cd.get_coastal_alert_mode(*args, day_beach_eligible=None) == cd.MODE_BEACH
    assert inspect.signature(cd.get_coastal_alert_mode).parameters["day_beach_eligible"].kind == inspect.Parameter.KEYWORD_ONLY
    with pytest.raises(TypeError):
        cd.get_coastal_alert_mode(*args, True)


@pytest.mark.parametrize("eligible", [None, False, True])
@pytest.mark.parametrize("zone,month", [("America/Cancun", 1), ("Europe/Warsaw", 7), ("Europe/Warsaw", 12)])
def test_classifier_modes_and_marine_independence(eligible, zone, month):
    date = datetime(2026, month, 15, 12, tzinfo=ZoneInfo(zone))
    kwargs = dict(day_beach_eligible=eligible)
    assert cd.get_coastal_alert_mode(NY_SIG, 60, 95, 150, zone, date, **kwargs) == cd.MODE_MARINE_STORM
    beach = eligible is True or (eligible is None and zone == "Europe/Warsaw" and month == 7)
    assert cd.get_coastal_alert_mode(NY_SIG, 18, 18, 150, zone, date, **kwargs) == (cd.MODE_BEACH if beach else None)
    assert cd.get_coastal_alert_mode(NY_SIG, 18, 18, 150, zone, date) == (
        cd.MODE_BEACH if zone == "Europe/Warsaw" and month == 7 else None)


@pytest.mark.parametrize("eligible", [None, False, True])
def test_marine_flag_preserved(monkeypatch, eligible):
    monkeypatch.setenv("ENABLE_GLOBAL_MARINE_STORM", "0")
    mode = cd.get_coastal_alert_mode(NY_SIG, 60, 95, 150, str(TZ), NOW, day_beach_eligible=eligible)
    assert mode == (cd.MODE_BEACH if eligible is True else None)


@pytest.mark.parametrize("field,value,expected", [
    ("temp_c", 23.999, False), ("temp_c", 24, True), ("temp_c", 24.001, True),
    ("temp_c", None, False), ("temp_c", float("nan"), False),
    ("temp_c", float("inf"), False), ("temp_c", "bad", False),
    ("clouds_low_pct", 14.999, True), ("clouds_low_pct", 15, True),
    ("clouds_low_pct", 15.0001, False), ("clouds_low_pct", -1, False),
    ("clouds_low_pct", 101, False), ("clouds_low_pct", float("nan"), False),
    ("clouds_low_pct", float("inf"), False),
])
def test_current_hour_thresholds(field, value, expected):
    p = payload_at()
    p["hours"][12][field] = value
    assert gate(p) is expected


@pytest.mark.parametrize("variant,expected", [
    ("fallback_total", True), ("fallback_total_cloudy", False),
    ("missing_all", False), ("low_mid_without_total", True),
    ("yr_cloudy", False), ("yr_clear", True), ("yr_missing", False),
    ("high_clouds", True),
])
def test_cloud_inputs_and_existing_consensus(variant, expected):
    p = payload_at()
    h = p["hours"][12]
    if variant.startswith("fallback") or variant == "missing_all":
        h.pop("clouds_low_pct"); h.pop("clouds_mid_pct")
        if variant == "missing_all":
            h.pop("clouds_pct")
        elif variant == "fallback_total_cloudy":
            h["clouds_pct"] = 35.001
    elif variant == "low_mid_without_total":
        h.pop("clouds_pct")
    elif variant.startswith("yr_"):
        h["clouds_pct_yr"] = {"yr_cloudy": 60, "yr_clear": 0, "yr_missing": None}[variant]
    elif variant == "high_clouds":
        h["clouds_high_pct"] = 100
    assert gate(p) is expected


@pytest.mark.parametrize("hh,value,expected", [
    (11, 1, True), (12, .001, False), (15, .1, False), (18, 1, False), (19, 1, True),
    (12, None, False), (12, -1, False), (12, float("nan"), False),
    (12, float("inf"), False), (12, "bad", False), (12, 0, True),
])
def test_precip_exact_zero_and_window(hh, value, expected):
    p = payload_at()
    p["hours"][hh]["precip_mm"] = value
    assert gate(p) is expected


@pytest.mark.parametrize("hh", [12, 15, 18])
def test_missing_required_hour(hh):
    p = payload_at()
    p["hours"].pop(hh)
    assert gate(p) is False


@pytest.mark.parametrize("field", ["temp_c", "precip_mm", "clouds_low_pct"])
def test_missing_fields_do_not_become_zero(field):
    p = payload_at()
    h = p["hours"][12]
    h.pop(field)
    if field == "clouds_low_pct":
        h.pop("clouds_pct")  # no valid total fallback
    assert gate(p) is False


def test_null_effective_precip_does_not_fall_back_to_raw_zero():
    p = payload_at()
    p["hours"][12]["precip_eff_mm"] = None
    assert gate(p) is False


@pytest.mark.parametrize("pop,expected", [(20, True), (30, True), (30.001, False),
                                         (None, False), ("missing", False), (-1, False), (101, False)])
def test_ui_ghost_precip_and_required_pop(pop, expected):
    p = payload_at()
    add_alternate(p)
    wet = p["hours"][12]
    wet["precip_mm"] = .6
    if pop == "missing":
        wet.pop("precip_prob_pct")
    else:
        wet["precip_prob_pct"] = pop
    assert gate(p) is expected


def test_missing_alternate_precip_is_not_dry():
    p = payload_at()
    alt = add_alternate(p)
    alt[12].pop("precip_mm")
    assert gate(p) is False


def test_single_model_and_precip_helper_arguments(monkeypatch):
    p = payload_at()
    original = day.precip_mm_for_ui
    calls = []
    def spy(h, hp_all):
        assert hp_all is p["hours"]
        calls.append(h["time_local"])
        return original(h, hp_all)
    monkeypatch.setattr(day, "precip_mm_for_ui", spy)
    assert gate(p) is True
    assert len(calls) == 7  # 12 through 18 inclusive, no extra source fetch.
    for h in p["hours"]:
        h["source"] = "yrno"
    assert gate(p) is True  # existing fallback source


def test_ambiguous_duplicates_and_wrong_date():
    p = payload_at()
    duplicate = deepcopy(p["hours"][12]); duplicate["temp_c"] = 25
    p["hours"].append(duplicate)
    assert gate(p) is False
    p = payload_at()
    p["hours"][18]["time_local"] = (NOW + timedelta(days=1)).replace(hour=18, minute=0).isoformat()
    assert gate(p) is False


@pytest.mark.parametrize("hh,minute,missing,expected", [(12, 0, 12, False), (12, 59, 12, False),
                                                       (13, 0, 12, True), (18, 59, 12, True),
                                                       (19, 0, 12, False)])
def test_floor_current_hour_and_empty_future(hh, minute, missing, expected):
    now = NOW.replace(hour=hh, minute=minute)
    p = payload_at(now)
    p["hours"].pop(missing)
    assert gate(p, now) is expected


@pytest.mark.parametrize("zone,instant", [
    ("Pacific/Kiritimati", "2026-01-15T22:25:00+00:00"),
    ("Pacific/Honolulu", "2026-01-15T22:25:00+00:00"),
    ("Europe/Warsaw", "2026-03-29T10:25:00+00:00"),
    ("Europe/Warsaw", "2026-10-25T11:25:00+00:00"),
])
def test_point_timezone_date_and_dst(zone, instant):
    utc = datetime.fromisoformat(instant)
    local = utc.astimezone(ZoneInfo(zone))
    assert local.hour == 12
    p = payload_at(local)
    assert gate(p, utc) is True
    p["hours"][12]["temp_c"] = 23
    assert gate(p, utc) is False


def test_gate_is_read_only_without_io_or_cache_changes():
    p = payload_at()
    add_alternate(p)
    before = deepcopy(p)
    assert gate(p) is True
    assert p == before
    assert cd.COAST_SIG_VERSION == VERSION


@pytest.mark.parametrize("hour,wind_hour,expected", [(7, 7, False), (7, 8, True), (8, 8, True),
                                                    (12, 11, False), (12, 18, True), (12, 19, False),
                                                    (18, 18, True), (19, 19, False)])
def test_day_alert_hours(coast, hour, wind_hour, expected):
    now = NOW.replace(hour=hour)
    p = payload_at(now)
    p["hours"][wind_hour].update(wind_kmh=18, gust_kmh=18)
    before = deepcopy(p)
    alerts = _coast_alerts(day.prepare_layout_data(p, now=now))
    assert bool(alerts) is expected
    assert len(coast) == 1
    assert p == before
    if expected:
        assert f"{wind_hour:02d}:00" in alerts[0]


@pytest.mark.parametrize("change", ["cold", "cloudy", "rain", "missing_hour", "missing_temp", "missing_cloud", "missing_precip"])
@pytest.mark.parametrize("storm", [False, True])
def test_invalid_beach_data_only_suppresses_beach(coast, change, storm):
    p = payload_at()
    p["hours"][14].update(wind_kmh=60 if storm else 18, gust_kmh=95 if storm else 18)
    if change == "cold": p["hours"][12]["temp_c"] = 23
    if change == "cloudy": p["hours"][12]["clouds_low_pct"] = 16
    if change == "rain": p["hours"][18]["precip_mm"] = .1
    if change == "missing_hour": p["hours"].pop(18)
    if change == "missing_temp": p["hours"][12].pop("temp_c")
    if change == "missing_cloud":
        for key in ("clouds_pct", "clouds_low_pct", "clouds_mid_pct"):
            p["hours"][12].pop(key)
    if change == "missing_precip": p["hours"][18].pop("precip_mm")
    layout = day.prepare_layout_data(p, now=NOW)
    assert layout["city"] == "Synthetic coast"
    alerts = _coast_alerts(layout)
    assert len(alerts) == int(storm)
    if storm: assert "Marine storm" in alerts[0]


def test_current_conditions_not_whole_block_or_future_conditions(coast):
    p = payload_at()
    for hh in (11, 13, 14, 15, 16, 17, 18):
        p["hours"][hh].update(temp_c=20, clouds_low_pct=60)
    p["hours"][16].update(wind_kmh=18, gust_kmh=18)
    assert "16:00" in _coast_alerts(day.prepare_layout_data(p, now=NOW))[0]


@pytest.mark.parametrize("zone,month,expected", [("Europe/Warsaw", 7, True),
    ("Europe/Warsaw", 1, False), ("America/Cancun", 7, False), ("America/Cancun", 1, False)])
def test_now_keeps_legacy_in_poland_and_abroad(coast, monkeypatch, zone, month, expected):
    now = datetime(2026, month, 15, 12, 25, tzinfo=ZoneInfo(zone))
    p = payload_at(now)
    for h in p["hours"]: h.update(wind_kmh=18, gust_kmh=18)
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None): return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(radar, "datetime", Frozen)
    original = cd.get_coastal_alert_mode
    kwargs_seen = []
    def spy(*args, **kwargs):
        kwargs_seen.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(cd, "get_coastal_alert_mode", spy)
    result = radar.prepare_now_layout_data(p)
    assert bool(result.get("context_line") and "Coast:" in result["context_line"]) is expected
    assert kwargs_seen and all("day_beach_eligible" not in k for k in kwargs_seen)


@pytest.mark.parametrize("warm", [False, True])
def test_geometry_cache_lifecycle_unchanged(monkeypatch, tmp_path, warm):
    runtime = types.ModuleType("coast_runtime")
    path = tmp_path / "coast.json"
    store = cd.JsonCoastSigStore(str(path))
    calls = []
    class Index:
        def compute_signature(self, **kwargs):
            calls.append(kwargs)
            return NY_SIG
    runtime.GLOBAL_COAST_STORE = store
    runtime.ensure_coast_index = lambda: Index()
    monkeypatch.setitem(sys.modules, "coast_runtime", runtime)
    monkeypatch.setattr(cd, "GEO_STACK_AVAILABLE", True)
    if warm:
        cd.get_or_compute_coast_signature_lazy(store, 21.16, -86.85, runtime.ensure_coast_index)
    p = payload_at(); p["hours"][14].update(wind_kmh=18, gust_kmh=18)
    assert _coast_alerts(day.prepare_layout_data(p, now=NOW))
    assert len(calls) == 1
    contents, cache = path.read_bytes(), deepcopy(store._cache)
    p["hours"][12]["temp_c"] = 23
    assert _coast_alerts(day.prepare_layout_data(p, now=NOW)) == []
    assert path.read_bytes() == contents and store._cache == cache
    assert len(calls) == 1
    assert cd.COAST_SIG_VERSION == VERSION
    entry = next(iter(cache.values()))
    assert set(entry) == {"version", "computed_at", "is_coastal", "distance_to_ocean_km", "sea_sectors", "radius_km", "step_deg"}
    assert entry["version"] == VERSION


def test_layout_no_additional_weather_requests_or_payload_mutations(coast, monkeypatch):
    p = payload_at(); p["hours"][14].update(wind_kmh=18, gust_kmh=18)
    calls = []
    def existing_fallback(*args, **kwargs):
        calls.append((args, kwargs))
        return None
    monkeypatch.setattr(day, "get_current_weather", existing_fallback)
    before = deepcopy(p)
    eligible = day._day_beach_weather_eligible
    # For this non-PL winter fixture, False reproduces baseline coast eligibility.
    with monkeypatch.context() as m:
        m.setattr(day, "_day_beach_weather_eligible", lambda *a: False)
        baseline = day.prepare_layout_data(p, now=NOW)
    baseline_calls = calls[:]; calls.clear()
    changed = day.prepare_layout_data(p, now=NOW)
    assert calls == baseline_calls
    assert day._day_beach_weather_eligible is eligible
    assert p == before
    assert _coast_alerts(baseline) == []
    assert len(_coast_alerts(changed)) == 1


def test_only_day_application_call_site_opts_in():
    root = Path(__file__).resolve().parent
    calls = []
    for name in ("prepare_layout.py", "prepare_now_layout.py", "dev_preview.py"):
        tree = ast.parse((root / name).read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "get_coastal_alert_mode":
                calls.append((name, n))
    assert len(calls) == 3
    for name, call in calls:
        assert len(call.args) == 6
        keywords = {k.arg: k.value for k in call.keywords}
        if name == "prepare_layout.py":
            assert set(keywords) == {"day_beach_eligible"}
        else:
            assert not keywords


@pytest.mark.parametrize("distance,wind,gust,expected", [
    (10, 17.999, 24.999, False), (10, 18, 18, True), (10, 17, 25, True),
    (10.001, 25.999, 32.999, False), (10.001, 26, 26, True),
    (10.001, 20, 33, True), (25, 26, 26, True), (25.001, 30, 40, False),
])
def test_global_day_preserves_distance_and_wind_thresholds(distance, wind, gust, expected):
    from dataclasses import replace
    sig = replace(NY_SIG, distance_to_ocean_km=distance)
    mode = cd.get_coastal_alert_mode(sig, wind, gust, 150, str(TZ), NOW, day_beach_eligible=True)
    assert mode == (cd.MODE_BEACH if expected else None)


@pytest.mark.parametrize("kind", ["none", "inland", "no_sectors", "offshore"])
def test_global_day_cannot_bypass_geometry(kind):
    from dataclasses import replace
    sig = NY_SIG
    direction = 150
    if kind == "none": sig = None
    if kind == "inland": sig = replace(sig, is_coastal=False)
    if kind == "no_sectors": sig = replace(sig, sea_sectors=[])
    if kind == "offshore": direction = 300
    assert cd.get_coastal_alert_mode(sig, 18, 18, direction, str(TZ), NOW, day_beach_eligible=True) is None


@pytest.mark.parametrize("month,day_of_month", [(1, 15), (5, 31), (6, 1), (9, 15), (9, 16), (12, 15)])
@pytest.mark.parametrize("zone", ["Europe/Warsaw", "America/Cancun"])
def test_day_is_global_year_round_but_cold_polish_summer_is_not_legacy(coast, month, day_of_month, zone):
    now = datetime(2026, month, day_of_month, 12, 25, tzinfo=ZoneInfo(zone))
    p = payload_at(now)
    p["hours"][14].update(wind_kmh=18, gust_kmh=18)
    assert len(_coast_alerts(day.prepare_layout_data(p, now=now))) == 1
    p["hours"][12]["temp_c"] = 23
    assert _coast_alerts(day.prepare_layout_data(p, now=now)) == []


def test_before_eight_requires_current_hour_dry(coast):
    now = NOW.replace(hour=7)
    p = payload_at(now)
    p["hours"][8].update(wind_kmh=18, gust_kmh=18)
    p["hours"][7]["precip_mm"] = .1
    assert _coast_alerts(day.prepare_layout_data(p, now=now)) == []


def test_marine_outside_beach_window_with_missing_weather(coast):
    p = payload_at()
    p["hours"][20].update(wind_kmh=60, gust_kmh=95)
    p["hours"][12].pop("temp_c")
    alerts = _coast_alerts(day.prepare_layout_data(p, now=NOW))
    assert len(alerts) == 1 and "Marine storm" in alerts[0] and "20:00" in alerts[0]


@pytest.mark.parametrize("eligible", [False, True])
def test_day_alone_passes_explicit_boolean(coast, monkeypatch, eligible):
    p = payload_at()
    if not eligible: p["hours"][12]["temp_c"] = 23
    seen = []
    original = cd.get_coastal_alert_mode
    def spy(*args, **kwargs):
        seen.append(kwargs["day_beach_eligible"])
        return original(*args, **kwargs)
    monkeypatch.setattr(cd, "get_coastal_alert_mode", spy)
    day.prepare_layout_data(p, now=NOW)
    assert seen and all(value is eligible for value in seen)


@pytest.mark.parametrize("flag", ["0", "1"])
@pytest.mark.parametrize("command", ["/day", "/dzien"])
@pytest.mark.parametrize("eligible", [False, True])
def test_real_manual_command_uses_day_policy_without_extra_io(coast, monkeypatch, command, flag, eligible):
    import location_bot as lb
    from test_access_gate import BotHarness, REAL_SEND_ONEOFF_REPORT, USERS_HEADERS
    h = BotHarness(monkeypatch)
    monkeypatch.setenv("PERF_DEBUG", flag)
    cid = 990000001
    record = dict(chat_id=str(cid), access_status="granted", access_source="admin",
                  profile_status="active", lat_round="21.16", lon_round="-86.85",
                  location_label="Synthetic coast", lang="en")
    h.users.grid.append([record.get(key, "") for key in USERS_HEADERS])
    initial = deepcopy(h.users.grid)
    reads = []
    original_read = h.users.get_all_values
    def read():
        reads.append(1)
        return original_read()
    monkeypatch.setattr(h.users, "get_all_values", read)
    p = payload_at()
    p["hours"][14].update(wind_kmh=18, gust_kmh=18)
    if not eligible: p["hours"][12]["temp_c"] = 23
    stages = []
    def build(**kwargs):
        stages.append("payload")
        return p
    def layout(payload):
        stages.append("layout")
        return day.prepare_layout_data(payload, now=NOW)
    layouts = []
    def render(value):
        stages.append("render")
        layouts.append(value)
        return "synthetic-not-written.png"
    def photo(*args, **kwargs): stages.append("photo")
    monkeypatch.setattr(lb, "_send_oneoff_report", REAL_SEND_ONEOFF_REPORT)
    monkeypatch.setattr(lb, "build_payload_for_location", build)
    monkeypatch.setattr(lb, "prepare_layout_data", layout)
    monkeypatch.setattr(lb, "_resolve_tz", lambda *a: str(TZ))
    # Freeze just this pre-existing availability check at the chosen local hour.
    monkeypatch.setattr(lb, "_day_card_window_blocked", lambda *a: False)
    monkeypatch.setattr(lb.image_generator, "generate_weather_card", render)
    monkeypatch.setattr(lb, "send_photo", photo)
    h.run(h.msg(cid, command, lang="en"))
    assert stages == ["payload", "layout", "render", "photo"]
    assert len(_coast_alerts(layouts[0])) == int(eligible)
    assert h.sent == [(cid, lb.t_ui("en", "prep_main"))]
    assert reads == [1] and h.users.grid == initial
    h.assert_users_only()


@pytest.mark.parametrize("eligible", [False, True])
def test_scheduler_day_consumer_same_policy_without_scheduler_changes(coast, monkeypatch, eligible):
    import main_card as main
    p = payload_at()
    add_alternate(p)  # Keep the scheduler's existing two-source quality gate.
    p["hours"][14].update(wind_kmh=18, gust_kmh=18)
    if not eligible: p["hours"][12]["temp_c"] = 23
    stages, layouts = [], []
    def build(**kwargs):
        stages.append("payload")
        return p
    def layout(value):
        stages.append("layout")
        return day.prepare_layout_data(value, now=NOW)
    def render(value):
        stages.append("render"); layouts.append(value)
        return "synthetic-not-written.png"
    def send(*args, **kwargs):
        stages.append("photo")
        return True
    monkeypatch.setattr(main, "build_payload_for_location", build)
    monkeypatch.setattr(main, "prepare_layout_data", layout)
    monkeypatch.setattr(main.image_generator, "generate_weather_card", render)
    monkeypatch.setattr(main, "send_telegram_photo", send)
    user = dict(chat_id="990000001", name="Synthetic coast", lat=21.16, lon=-86.85, tz=str(TZ), lang="en")
    assert main._send_card_to_user(user) is True
    assert stages == ["payload", "layout", "render", "photo"]
    assert len(_coast_alerts(layouts[0])) == int(eligible)


@pytest.mark.parametrize("storm", [False, True])
def test_missing_pop_only_closes_beach_in_real_layout(coast, storm):
    p = payload_at()
    add_alternate(p)
    p["hours"][12]["precip_mm"] = .6
    p["hours"][12].pop("precip_prob_pct")
    p["hours"][14].update(wind_kmh=60 if storm else 18, gust_kmh=95 if storm else 18)
    layout = day.prepare_layout_data(p, now=NOW)
    alerts = _coast_alerts(layout)
    assert len(alerts) == int(storm)
    if storm: assert "Marine storm" in alerts[0]


def test_gate_does_not_touch_source_caches_or_invoke_sources(monkeypatch):
    import weather_payload as wp
    import owm_nowcast as owm
    caches = [(wp, "_OPENMETEO_CACHE"), (wp, "_OWM_CACHE"), (owm, "_CACHE")]
    for module, name in caches:
        monkeypatch.setattr(module, name, {"synthetic_sentinel": {"value": 42}})
    monkeypatch.setattr(wp, "_OPENMETEO_DOWN_UNTIL", 1234)
    def forbidden(*args, **kwargs):
        pytest.fail("The weather gate must not call a weather source")
    for name in ("_fetch_openmeteo", "_fetch_yrno", "_fetch_airly", "_get_owm_cached"):
        monkeypatch.setattr(wp, name, forbidden)
    monkeypatch.setattr(owm, "get_current_weather", forbidden)
    snapshots = [deepcopy(getattr(module, name)) for module, name in caches]
    assert gate(payload_at()) is True
    assert [getattr(module, name) for module, name in caches] == snapshots
    assert wp._OPENMETEO_DOWN_UNTIL == 1234


@pytest.mark.parametrize("lang", ["pl", "en", "de", "fr", "es", "no"])
@pytest.mark.parametrize("hours", [(14,), (14, 16)])
def test_global_day_uses_unchanged_point_range_messages(coast, lang, hours):
    from i18n import t
    p = payload_at()
    p["lang"] = lang
    for hh in hours:
        p["hours"][hh].update(wind_kmh=18, gust_kmh=18)
    layout = day.prepare_layout_data(p, now=NOW)
    if len(hours) == 1:
        expected = t(lang, "coast_beach_day_point", start="14")
    else:
        expected = t(lang, "coast_beach_day_range", start="14", end="16")
    assert expected in layout["alerts"]
