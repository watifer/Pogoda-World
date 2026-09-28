"""
test_coast.py — moduł Wybrzeże / coast.

Sprawdza rozdzielenie trybów:
  * marine_storm — globalnie, cały rok, tylko blisko wody, tylko onshore,
    tylko wysokie progi (gust >= 90 albo wind >= 75 i gust >= 80),
  * beach — lifestyle, tylko Polska i tylko sezon 01.06–15.09.

Uruchomienie: pytest test_coast.py -v
"""

import sys
import types
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import coast_detector as cd
from coast_detector import (
    MODE_BEACH,
    MODE_MARINE_STORM,
    CoastSignature,
    get_coastal_alert_mode,
)
from i18n import STRINGS, t

# ═══════════════════════════════════════
# FIXTURES / HELPERY
# ═══════════════════════════════════════

TZ_PL = ZoneInfo("Europe/Warsaw")
TZ_NY = ZoneInfo("America/New_York")

LANGS = ("pl", "en", "fr", "de", "es", "no")

COAST_KEYS = (
    "coast_marine_storm_day",
    "coast_marine_storm_now",
    "coast_marine_storm_soon",
    "coast_marine_storm_from",
    "coast_beach_day_point",
    "coast_beach_day_range",
    "coast_beach_now",
    "coast_beach_now_gust",
    "coast_beach_soon",
    "coast_beach_from",
)

COAST_KEY_ARGS = {
    "coast_marine_storm_day": dict(hh="07", wind=78, gust=95),
    "coast_marine_storm_now": dict(gust=95),
    "coast_marine_storm_soon": dict(gust=95),
    "coast_marine_storm_from": dict(hh="07", gust=95),
    "coast_beach_day_point": dict(start="12"),
    "coast_beach_day_range": dict(start="12", end="17"),
    "coast_beach_now": dict(wind=22),
    "coast_beach_now_gust": dict(wind=22, gust=31),
    "coast_beach_soon": dict(gust=31),
    "coast_beach_from": dict(hh="15", gust=31),
}


def _sig(distance_km=5.0, sectors=None, radius_km=25.0, is_coastal=True):
    """Sztuczna sygnatura wybrzeża — bez shapely/pyproj."""
    if sectors is None:
        sectors = [(90.0, 200.0)]  # morze od E przez S
    return CoastSignature(
        is_coastal=is_coastal,
        distance_to_ocean_km=distance_km,
        sea_sectors=[tuple(x) for x in sectors],
        radius_km=radius_km,
        step_deg=10,
    )


# New York: ocean od wschodu/południa (onshore ~150°, offshore ~300°)
NY_SIG = _sig(distance_km=4.0, sectors=[(90.0, 200.0)])
NY_ONSHORE_DIR = 150.0
NY_OFFSHORE_DIR = 300.0

# Polska plaża (Bałtyk): morze od północy (onshore ~20°)
PL_SIG = _sig(distance_km=1.0, sectors=[(330.0, 60.0)])
PL_ONSHORE_DIR = 20.0

JULY = datetime(2025, 7, 15, 13, 0, tzinfo=TZ_PL)
OCTOBER = datetime(2025, 10, 15, 13, 0, tzinfo=TZ_PL)
JANUARY_NY = datetime(2025, 1, 20, 13, 0, tzinfo=TZ_NY)


@pytest.fixture(autouse=True)
def _clean_marine_env(monkeypatch):
    """Każdy test startuje z domyślnymi progami (bez zaszłości z ENV)."""
    for key in (
        "ENABLE_GLOBAL_MARINE_STORM",
        "MARINE_STORM_WIND_KMH",
        "MARINE_STORM_GUST_KMH",
        "MARINE_STORM_MIN_GUST_WITH_WIND_KMH",
    ):
        monkeypatch.delenv(key, raising=False)


# ═══════════════════════════════════════
# 1. SCENARIUSZE PRODUKTOWE (get_coastal_alert_mode)
# ═══════════════════════════════════════

class TestScenarios:

    def test_new_york_onshore_50_no_alert(self):
        """New York, onshore 50 km/h — to normalny wietrzny dzień, zero spamu."""
        mode = get_coastal_alert_mode(
            NY_SIG, 50.0, 50.0, NY_ONSHORE_DIR, "America/New_York", JANUARY_NY
        )
        assert mode is None

    def test_new_york_onshore_gust_95_marine_storm(self):
        """New York, onshore, poryw 95 km/h — realny sztorm od wody."""
        mode = get_coastal_alert_mode(
            NY_SIG, 60.0, 95.0, NY_ONSHORE_DIR, "America/New_York", JANUARY_NY
        )
        assert mode == MODE_MARINE_STORM

    def test_new_york_onshore_gust_95_message_is_english(self):
        """Komunikat marine_storm dla usera EN nie może być (pół)polski."""
        msg = t("en", "coast_marine_storm_day", hh="07", wind=60, gust=95)
        assert "Marine storm" in msg
        assert " — " in msg  # separator tytuł/opis dla renderera alertów
        for pl_word in ("Sztorm", "wiatr", "Wybrzeże", "porywy", "morza"):
            assert pl_word not in msg

    def test_new_york_offshore_gust_95_no_alert(self):
        """Wiatr od lądu (offshore) — nawet 95 km/h nie jest alertem nadmorskim."""
        mode = get_coastal_alert_mode(
            NY_SIG, 60.0, 95.0, NY_OFFSHORE_DIR, "America/New_York", JANUARY_NY
        )
        assert mode is None

    def test_poland_beach_july_onshore_25_beach(self):
        """Polska plaża, lipiec, onshore 25 km/h — lifestyle beach alert zostaje."""
        mode = get_coastal_alert_mode(
            PL_SIG, 25.0, 30.0, PL_ONSHORE_DIR, "Europe/Warsaw", JULY
        )
        assert mode == MODE_BEACH

    def test_poland_beach_october_onshore_25_no_alert(self):
        """Poza sezonem (październik) lifestyle beach alert nie istnieje."""
        mode = get_coastal_alert_mode(
            PL_SIG, 25.0, 30.0, PL_ONSHORE_DIR, "Europe/Warsaw", OCTOBER
        )
        assert mode is None

    def test_poland_beach_july_gust_95_is_marine_storm_not_beach(self):
        """Sztorm wygrywa z plażowaniem nawet w środku sezonu."""
        mode = get_coastal_alert_mode(
            PL_SIG, 60.0, 95.0, PL_ONSHORE_DIR, "Europe/Warsaw", JULY
        )
        assert mode == MODE_MARINE_STORM

    def test_poland_coast_october_gust_95_is_marine_storm(self):
        """marine_storm działa cały rok, także poza sezonem plażowym."""
        mode = get_coastal_alert_mode(
            PL_SIG, 60.0, 95.0, PL_ONSHORE_DIR, "Europe/Warsaw", OCTOBER
        )
        assert mode == MODE_MARINE_STORM


# ═══════════════════════════════════════
# 2. PROGI I ENV
# ═══════════════════════════════════════

class TestThresholds:

    @pytest.mark.parametrize("wind,gust,expected", [
        (10.0, 89.9, None),                 # tuż pod progiem porywu
        (10.0, 90.0, MODE_MARINE_STORM),    # gust >= 90
        (75.0, 80.0, MODE_MARINE_STORM),    # wind >= 75 i gust >= 80
        (75.0, 79.9, None),                 # wiatr ok, poryw za słaby
        (74.9, 85.0, None),                 # poryw ok, wiatr za słaby
        (95.0, 0.0, MODE_MARINE_STORM),     # gust brakujący -> fallback na wind
    ])
    def test_marine_storm_thresholds(self, wind, gust, expected):
        mode = get_coastal_alert_mode(
            NY_SIG, wind, gust, NY_ONSHORE_DIR, "America/New_York", JANUARY_NY
        )
        assert mode == expected

    def test_env_can_disable_global_marine_storm(self, monkeypatch):
        monkeypatch.setenv("ENABLE_GLOBAL_MARINE_STORM", "0")
        mode = get_coastal_alert_mode(
            NY_SIG, 60.0, 120.0, NY_ONSHORE_DIR, "America/New_York", JANUARY_NY
        )
        assert mode is None

    def test_env_can_lower_thresholds(self, monkeypatch):
        monkeypatch.setenv("MARINE_STORM_GUST_KMH", "60")
        mode = get_coastal_alert_mode(
            NY_SIG, 40.0, 65.0, NY_ONSHORE_DIR, "America/New_York", JANUARY_NY
        )
        assert mode == MODE_MARINE_STORM

    def test_env_garbage_falls_back_to_defaults(self, monkeypatch):
        monkeypatch.setenv("MARINE_STORM_GUST_KMH", "abc")
        assert cd.marine_storm_thresholds() == (75.0, 90.0, 80.0)

    def test_disabled_marine_storm_does_not_kill_beach(self, monkeypatch):
        monkeypatch.setenv("ENABLE_GLOBAL_MARINE_STORM", "0")
        mode = get_coastal_alert_mode(
            PL_SIG, 25.0, 30.0, PL_ONSHORE_DIR, "Europe/Warsaw", JULY
        )
        assert mode == MODE_BEACH


# ═══════════════════════════════════════
# 3. GUARDY (dystans, onshore, is_coastal)
# ═══════════════════════════════════════

class TestGuards:

    def test_distance_guard_blocks_far_locations(self):
        """40 km od wody przy promieniu skanu 25 km — to już nie jest wybrzeże."""
        far = _sig(distance_km=40.0, radius_km=25.0)
        mode = get_coastal_alert_mode(
            far, 60.0, 120.0, NY_ONSHORE_DIR, "America/New_York", JANUARY_NY
        )
        assert mode is None

    def test_distance_guard_uses_signature_radius(self):
        """Gdy sygnatura liczona była szerzej, guard respektuje jej promień."""
        wide = _sig(distance_km=40.0, radius_km=50.0)
        mode = get_coastal_alert_mode(
            wide, 60.0, 120.0, NY_ONSHORE_DIR, "America/New_York", JANUARY_NY
        )
        assert mode == MODE_MARINE_STORM

    def test_missing_distance_is_treated_as_far(self):
        unknown = _sig(distance_km=None)
        mode = get_coastal_alert_mode(
            unknown, 60.0, 120.0, NY_ONSHORE_DIR, "America/New_York", JANUARY_NY
        )
        assert mode is None

    def test_not_coastal_returns_none(self):
        inland = _sig(distance_km=2.0, is_coastal=False)
        assert get_coastal_alert_mode(
            inland, 60.0, 120.0, NY_ONSHORE_DIR, "America/New_York", JANUARY_NY
        ) is None

    def test_no_sea_sectors_returns_none(self):
        no_sectors = _sig(distance_km=2.0, sectors=[])
        assert get_coastal_alert_mode(
            no_sectors, 60.0, 120.0, NY_ONSHORE_DIR, "America/New_York", JANUARY_NY
        ) is None

    def test_none_signature_returns_none(self):
        assert get_coastal_alert_mode(
            None, 60.0, 120.0, 150.0, "America/New_York", JANUARY_NY
        ) is None

    def test_beach_needs_polish_timezone(self):
        """Ten sam wiatr w lipcu poza PL nie generuje beach alertu."""
        mode = get_coastal_alert_mode(
            NY_SIG, 25.0, 30.0, NY_ONSHORE_DIR, "America/New_York",
            datetime(2025, 7, 15, 13, 0, tzinfo=TZ_NY),
        )
        assert mode is None

    @pytest.mark.parametrize("day,expected", [
        (datetime(2025, 5, 31, 12, 0), None),
        (datetime(2025, 6, 1, 12, 0), MODE_BEACH),
        (datetime(2025, 9, 15, 12, 0), MODE_BEACH),
        (datetime(2025, 9, 16, 12, 0), None),
    ])
    def test_beach_season_boundaries(self, day, expected):
        mode = get_coastal_alert_mode(
            PL_SIG, 25.0, 30.0, PL_ONSHORE_DIR, "Europe/Warsaw",
            day.replace(tzinfo=TZ_PL),
        )
        assert mode == expected


# ═══════════════════════════════════════
# 4. WERSJONOWANIE SYGNATURY
# ═══════════════════════════════════════

class TestSignatureVersion:

    def test_single_definition_of_version(self):
        src = open("coast_detector.py", encoding="utf-8").read()
        assert src.count("COAST_SIG_VERSION = ") == 1

    def test_version_matches_runtime_dataset(self):
        """Wersja cache musi opisywać dataset faktycznie ładowany w runtime."""
        runtime_src = open("coast_runtime.py", encoding="utf-8").read()
        assert "ne_50m_ocean" in runtime_src
        assert cd.COAST_SIG_VERSION.startswith("ne_50m_ocean:50m")
        assert "v2" in cd.COAST_SIG_VERSION
        assert "marine75g90" in cd.COAST_SIG_VERSION


# ═══════════════════════════════════════
# 5. i18n
# ═══════════════════════════════════════

class TestI18n:

    @pytest.mark.parametrize("lang", LANGS)
    def test_all_coast_keys_exist(self, lang):
        missing = [k for k in COAST_KEYS if k not in STRINGS[lang]]
        assert not missing, f"{lang}: brak kluczy {missing}"

    @pytest.mark.parametrize("lang", LANGS)
    def test_keys_render_without_placeholders(self, lang):
        for key in COAST_KEYS:
            msg = t(lang, key, **COAST_KEY_ARGS[key])
            assert msg and msg != key
            assert "{" not in msg and "}" not in msg

    @pytest.mark.parametrize("lang", [l for l in LANGS if l != "pl"])
    def test_no_polish_leftovers_in_marine_storm(self, lang):
        """Żadnych pół-polskich alertów sztormowych."""
        pl_markers = ("Sztorm od morza", "wiatr od wody", "Wybrzeże", "porywy do", "plaży")
        for key in COAST_KEYS:
            msg = t(lang, key, **COAST_KEY_ARGS[key])
            for marker in pl_markers:
                assert marker not in msg, f"{lang}/{key}: polski tekst '{marker}'"

    @pytest.mark.parametrize("lang", LANGS)
    def test_day_alert_has_title_separator(self, lang):
        """Renderer karty dzieli alert po ' — ' na tytuł i opis."""
        for key in ("coast_marine_storm_day", "coast_beach_day_point", "coast_beach_day_range"):
            assert " — " in t(lang, key, **COAST_KEY_ARGS[key])


# ═══════════════════════════════════════
# 6. INTEGRACJA /day — okno czasowe i tryby
# ═══════════════════════════════════════

def _hour_row(date_str, hour, tz, wind=5.0, gust=8.0, wind_dir=150):
    dt = datetime.fromisoformat(f"{date_str}T{hour:02d}:00").replace(tzinfo=tz)
    return {
        "time_local": dt.isoformat(timespec="minutes"),
        "temp_c": 12.0,
        "dewpoint_c": 6.0,
        "rh_pct": 70.0,
        "wind_kmh": wind,
        "gust_kmh": gust,
        "wind_dir_deg": wind_dir,
        "clouds_pct": 50.0,
        "clouds_low_pct": 20.0,
        "clouds_mid_pct": 20.0,
        "clouds_high_pct": 10.0,
        "precip_mm": 0.0,
        "weather_code": 3,
        "symbol_code": None,
        "source": "openmeteo",
    }


def _ny_payload(date_str, storm_hours=(), lang="en"):
    hours = []
    for h in range(0, 24):
        if h in storm_hours:
            hours.append(_hour_row(date_str, h, TZ_NY, wind=60.0, gust=95.0, wind_dir=150))
        else:
            hours.append(_hour_row(date_str, h, TZ_NY, wind=15.0, gust=20.0, wind_dir=150))
    return {
        "version": "1.0",
        "lang": lang,
        "location": {"name": "New York", "lat": 40.71, "lon": -74.0, "tz": "America/New_York"},
        "generated_at_local": f"{date_str}T00:00:00-05:00",
        "forecast_source": "OpenMeteo + Yr.no",
        "airly": None,
        "bias_temp_c": None,
        "hours": hours,
    }


@pytest.fixture
def coast_stub(monkeypatch):
    """Podstawia runtime wybrzeża (bez shapely) i zwraca ustaloną sygnaturę."""
    fake_runtime = types.ModuleType("coast_runtime")
    fake_runtime.GLOBAL_COAST_STORE = object()
    fake_runtime.ensure_coast_index = lambda: None
    monkeypatch.setitem(sys.modules, "coast_runtime", fake_runtime)
    monkeypatch.setattr(
        cd, "get_or_compute_coast_signature_lazy",
        lambda store, lat, lon, idx_factory: NY_SIG,
    )
    import prepare_layout
    monkeypatch.setattr(prepare_layout, "get_current_weather", lambda *a, **k: None)
    return NY_SIG


def _coast_alerts(layout):
    markers = ("Marine storm", "Sztorm od morza", "Coast —", "Wybrzeże")
    return [a for a in layout.get("alerts", []) if any(m in a for m in markers)]


class TestDayLayout:

    def test_afternoon_does_not_catch_morning_episode(self, coast_stub):
        """Raport o 15:00 nie może odgrzewać sztormu z godzin 5–7."""
        from prepare_layout import prepare_layout_data

        payload = _ny_payload("2025-01-20", storm_hours=(5, 6, 7))
        now = datetime(2025, 1, 20, 15, 0, tzinfo=TZ_NY)
        layout = prepare_layout_data(payload, now=now)

        assert _coast_alerts(layout) == []

    def test_morning_report_catches_morning_episode(self, coast_stub):
        """Ten sam payload o 04:00 — alert musi się pojawić."""
        from prepare_layout import prepare_layout_data

        payload = _ny_payload("2025-01-20", storm_hours=(5, 6, 7))
        now = datetime(2025, 1, 20, 4, 0, tzinfo=TZ_NY)
        layout = prepare_layout_data(payload, now=now)

        found = _coast_alerts(layout)
        assert len(found) == 1
        alert = found[0]
        assert "Marine storm" in alert          # EN, nie PL
        assert "05:00" in alert                 # pierwsza godzina epizodu
        assert "95" in alert                    # maksymalny poryw
        assert "Wybrzeże" not in alert

    def test_future_episode_is_caught_in_the_afternoon(self, coast_stub):
        """Sztorm o 20:00 widziany z 15:00 — alert jest, z godziną 20:00."""
        from prepare_layout import prepare_layout_data

        payload = _ny_payload("2025-01-20", storm_hours=(20, 21))
        now = datetime(2025, 1, 20, 15, 0, tzinfo=TZ_NY)
        layout = prepare_layout_data(payload, now=now)

        found = _coast_alerts(layout)
        assert len(found) == 1
        assert "20:00" in found[0]

    def test_moderate_wind_produces_no_alert(self, coast_stub):
        """New York onshore 50 km/h — brak jakiegokolwiek alertu nadmorskiego."""
        from prepare_layout import prepare_layout_data

        payload = _ny_payload("2025-01-20")
        for h in payload["hours"]:
            h["wind_kmh"] = 50.0
            h["gust_kmh"] = 50.0
        now = datetime(2025, 1, 20, 9, 0, tzinfo=TZ_NY)
        layout = prepare_layout_data(payload, now=now)

        assert _coast_alerts(layout) == []

    def test_offshore_storm_produces_no_alert(self, coast_stub):
        """Poryw 95 km/h, ale wiatr od lądu — cisza."""
        from prepare_layout import prepare_layout_data

        payload = _ny_payload("2025-01-20", storm_hours=(9, 10, 11))
        for h in payload["hours"]:
            h["wind_dir_deg"] = NY_OFFSHORE_DIR
        now = datetime(2025, 1, 20, 8, 0, tzinfo=TZ_NY)
        layout = prepare_layout_data(payload, now=now)

        assert _coast_alerts(layout) == []


# ═══════════════════════════════════════
# 7. INTEGRACJA /now
# ═══════════════════════════════════════

class TestNowLayout:

    def test_marine_storm_now_is_english_context_line(self, coast_stub, monkeypatch):
        import owm_nowcast
        monkeypatch.setattr(owm_nowcast, "get_current_weather", lambda *a, **k: None)
        from prepare_now_layout import prepare_now_layout_data

        payload = _ny_payload("2025-01-20", storm_hours=tuple(range(8, 14)))
        now = datetime(2025, 1, 20, 8, 0, tzinfo=TZ_NY)
        layout = prepare_now_layout_data(payload, now=now)

        line = layout.get("context_line") or ""
        assert "Marine storm" in line
        assert "95" in line
        assert "Wybrzeże" not in line and "Sztorm" not in line

    def test_marine_storm_later_today_uses_from_hour(self, coast_stub, monkeypatch):
        import owm_nowcast
        monkeypatch.setattr(owm_nowcast, "get_current_weather", lambda *a, **k: None)
        from prepare_now_layout import prepare_now_layout_data

        payload = _ny_payload("2025-01-20", storm_hours=(14, 15))
        now = datetime(2025, 1, 20, 8, 0, tzinfo=TZ_NY)
        layout = prepare_now_layout_data(payload, now=now)

        line = layout.get("context_line") or ""
        assert "Marine storm" in line
        assert "14:00" in line

    def test_no_alert_for_moderate_onshore_wind(self, coast_stub, monkeypatch):
        import owm_nowcast
        monkeypatch.setattr(owm_nowcast, "get_current_weather", lambda *a, **k: None)
        from prepare_now_layout import prepare_now_layout_data

        payload = _ny_payload("2025-01-20")
        for h in payload["hours"]:
            h["wind_kmh"] = 50.0
            h["gust_kmh"] = 50.0
        now = datetime(2025, 1, 20, 8, 0, tzinfo=TZ_NY)
        layout = prepare_now_layout_data(payload, now=now)

        line = layout.get("context_line") or ""
        assert "Marine storm" not in line
        assert "Wybrzeże" not in line


# ═══════════════════════════════════════════════════════════════════
# STOS GEO: fallback importu zostaje, ale awaria nie może być cicha
# ═══════════════════════════════════════════════════════════════════

class TestGeoStackVisibility:
    def test_status_reports_available_stack(self):
        import coast_detector
        status = coast_detector.coast_stack_status()
        assert set(status) == {"available", "error", "packages", "message"}
        assert status["packages"] == ["pyshp", "shapely", "pyproj"]
        if coast_detector.GEO_STACK_AVAILABLE:
            assert status["error"] is None and status["message"] is None

    def test_status_reports_missing_stack(self, monkeypatch):
        import coast_detector
        monkeypatch.setattr(coast_detector, "GEO_STACK_AVAILABLE", False)
        monkeypatch.setattr(coast_detector, "GEO_STACK_ERROR", ImportError("No module named 'shapely'"))
        status = coast_detector.coast_stack_status()
        assert status["available"] is False
        assert "shapely" in status["error"]
        assert "WYŁĄCZONY" in status["message"]
        for pkg in ("pyshp", "shapely", "pyproj"):
            assert pkg in status["message"]

    def test_warning_is_emitted_once_per_process(self, monkeypatch, capsys):
        import coast_detector
        monkeypatch.setattr(coast_detector, "GEO_STACK_AVAILABLE", False)
        monkeypatch.setattr(coast_detector, "GEO_STACK_ERROR", ImportError("brak pyproj"))
        monkeypatch.setattr(coast_detector, "_COAST_WARNED", False)

        assert coast_detector.warn_coast_disabled("/now") is True
        first = capsys.readouterr().err
        assert "WYŁĄCZONY" in first and "/now" in first
        assert "pip install -r requirements.txt" in first

        # drugie wywołanie milczy — inaczej zalałoby logi przy każdym renderze
        assert coast_detector.warn_coast_disabled("/day") is False
        assert capsys.readouterr().err == ""

    def test_no_warning_when_stack_is_available(self, monkeypatch, capsys):
        import coast_detector
        monkeypatch.setattr(coast_detector, "GEO_STACK_AVAILABLE", True)
        monkeypatch.setattr(coast_detector, "_COAST_WARNED", False)
        assert coast_detector.warn_coast_disabled("/now") is False
        assert capsys.readouterr().err == ""

    def test_requirements_pin_the_geo_stack(self):
        from pathlib import Path
        req = Path(__file__).with_name("requirements.txt").read_text(encoding="utf-8").lower()
        for pkg in ("pyshp", "shapely", "pyproj"):
            assert pkg in req, f"{pkg} musi być w requirements.txt — bez niego coast milczy"
