"""Testy niezależności warstw (sygnały poboczne) — ETAP 2.

Zamrażają OBECNE granice wpływu warstw pobocznych (rejestr etapu 1 §3.4-3.7):

  * OWM nowcast: wpływa na /now i linię snapshotu /day; NIE wpływa na /future
    (szczegóły korekty: test_owm_nowcast.py);
  * coast: zmienia alerty/kontekst /day i /now, ale NIE wartości pogodowe;
    NIE wpływa na /future (prepare_future_layout nie zna modułu coast);
  * globalny cache coast: kluczowanie po współrzędnych — wpis jednej
    lokalizacji nie jest danymi innej lokalizacji;
  * faza Księżyca: niezależna od daily_diag i volatility;
  * tag diagnostyczny vs ikona Księżyca: planer traktuje je oddzielnie
    (tag wygrywa pozycję — charakterystyka w test_dead_paths.py).

Wszystko offline: fetchery i geo-indeks coast mockowane/stubowane.
"""

import copy
import inspect
from datetime import datetime, timedelta

import pytest

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo

import coast_detector
import prepare_future_layout
import prepare_layout
import prepare_now_layout
from coast_detector import (
    COAST_SIG_VERSION,
    CoastSignature,
    JsonCoastSigStore,
    coast_cache_key,
    get_or_compute_coast_signature_lazy,
)
from prepare_future_layout import prepare_future_layout_data
from prepare_layout import prepare_layout_data
from prepare_now_layout import prepare_now_layout_data

TZ_NY = ZoneInfo("America/New_York")
NOW_NY = datetime(2025, 1, 20, 8, 0, tzinfo=TZ_NY)

# Sygnatura nadmorska w kształcie store (jak w test_coast.py).
NY_SIG = CoastSignature(is_coastal=True, distance_to_ocean_km=4.0,
                        sea_sectors=[(90.0, 200.0)], radius_km=25.0, step_deg=10)


@pytest.fixture
def freeze_owm(monkeypatch):
    """Layouty /day i /now nigdy nie dobijają OWM z sieci w tych testach."""
    monkeypatch.setattr(prepare_layout, "get_current_weather", lambda *a, **k: None)
    monkeypatch.setattr(prepare_now_layout, "get_current_weather", lambda *a, **k: None)


def _ny_hour(date_str, hour, wind=15.0, gust=20.0, wdir=150):
    """Rekord godziny dla Nowego Jorku (jak _hour_row w test_coast.py)."""
    dt = datetime.fromisoformat(f"{date_str}T{hour:02d}:00").replace(tzinfo=TZ_NY)
    return {
        "time_local": dt.isoformat(timespec="minutes"),
        "temp_c": 12.0, "dewpoint_c": 6.0, "rh_pct": 70.0,
        "wind_kmh": wind, "gust_kmh": gust, "wind_dir_deg": wdir,
        "clouds_pct": 50.0, "clouds_low_pct": 20.0, "clouds_mid_pct": 20.0,
        "clouds_high_pct": 10.0, "precip_mm": 0.0, "precip_prob_pct": 0,
        "weather_code": 3, "symbol_code": None, "pressure_hpa": 1010,
        "source": "openmeteo",
    }


def _ny_payload():
    hours = []
    for h in range(24):
        storm = h in (9, 10, 11)  # sztorm od lądu w stronę morza (onshore)
        hours.append(_ny_hour("2025-01-20", h,
                              wind=60.0 if storm else 15.0,
                              gust=95.0 if storm else 20.0))
    return {
        "version": "1.0", "lang": "en",
        "location": {"name": "New York", "lat": 40.71, "lon": -74.0,
                     "tz": "America/New_York"},
        "generated_at_local": "2025-01-20T00:00:00-05:00",
        "forecast_source": "OpenMeteo + Yr.no",
        "airly": None, "bias_temp_c": None,
        "hours": hours, "alerts": [], "daily_diag": {},
    }


# ─────────────────── 1. OWM nie wpływa na /future ───────────────────

TZ_PL = ZoneInfo("Europe/Warsaw")
NOW_PL = datetime(2026, 1, 15, 12, 0, tzinfo=TZ_PL)


def _pl_future_payload(**extra):
    hours = []
    for day_off in range(1, 15):
        day = NOW_PL + timedelta(days=day_off)
        for i in range(24):
            dt = day.replace(hour=0) + timedelta(hours=i)
            hours.append({
                "time_local": dt.isoformat(timespec="minutes"), "source": "openmeteo",
                "temp_c": 10.0, "precip_mm": 0.0, "precip_prob_pct": 0,
                "clouds_pct": 20.0, "clouds_low_pct": 10.0, "clouds_mid_pct": 10.0,
                "clouds_high_pct": 0.0, "wind_kmh": 10.0, "gust_kmh": 15.0,
                "wind_dir_deg": 270, "rh_pct": 60, "dewpoint_c": 2.0,
                "pressure_hpa": 1015, "symbol_code": "clearsky_day", "weather_code": 1,
            })
    p = {
        "location": {"name": "Wiązowna", "lat": 52.17, "lon": 21.32, "tz": "Europe/Warsaw"},
        "lang": "pl", "hours": hours, "forecast_source": "OpenMeteo + Yr.no",
        "model_updated_at_local": NOW_PL.isoformat(timespec="minutes"),
        "alerts": [], "daily_diag": {},
    }
    p.update(extra)
    return p


class TestOwmDoesNotReachFuture:
    def test_future_layout_is_byte_identical_with_owm_current(self):
        # Nawet „bogaty" snapshot OWM (chmury, UV, opad) nie zmienia /future.
        plain = prepare_future_layout_data(_pl_future_payload(), now=NOW_PL)
        with_owm = prepare_future_layout_data(_pl_future_payload(
            owm_current={"dt": int(NOW_PL.timestamp()), "clouds": 99,
                         "uvi": 8.0, "rain": {"1h": 5.0}}), now=NOW_PL)
        assert plain == with_owm

    def test_future_module_has_no_owm_dependency(self):
        src = inspect.getsource(prepare_future_layout)
        assert "owm" not in src.lower()
        assert "nowcast" not in src.lower()


# ─────────────────── 2. coast zmienia /day i /now, nie zmienia wartości ───────────────────

class TestCoastLayerScope:
    def _day_and_now(self, monkeypatch, sig):
        monkeypatch.setattr(coast_detector, "get_or_compute_coast_signature_lazy",
                            lambda store, lat, lon, idx_factory: sig)
        day = prepare_layout_data(copy.deepcopy(_ny_payload()), now=NOW_NY)
        now = prepare_now_layout_data(copy.deepcopy(_ny_payload()), now=NOW_NY)
        return day, now

    def test_coast_adds_alerts_and_context(self, monkeypatch, freeze_owm):
        day_off, now_off = self._day_and_now(monkeypatch, None)
        day_on, now_on = self._day_and_now(monkeypatch, NY_SIG)

        assert day_off["alerts"] == []
        assert any("beach" in a.lower() or "morze" in a.lower() or "storm" in a.lower()
                   for a in day_on["alerts"])
        assert now_off["context_line"] is None
        assert now_on["context_line"] is not None

    def test_coast_does_not_change_weather_values(self, monkeypatch, freeze_owm):
        day_off, now_off = self._day_and_now(monkeypatch, None)
        day_on, now_on = self._day_and_now(monkeypatch, NY_SIG)

        # Bloki godzinowe, hero i temperatury pozostają identyczne.
        assert day_off["today_blocks"] == day_on["today_blocks"]
        assert day_off["main_icon"] == day_on["main_icon"]
        assert day_off["summary"] == day_on["summary"]
        assert day_off["temp_range"] == day_on["temp_range"]
        assert day_off["next_days"] == day_on["next_days"]

        assert now_off["today_blocks"] == now_on["today_blocks"]
        assert now_off["main_icon"] == now_on["main_icon"]
        assert now_off["summary"] == now_on["summary"]


# ─────────────────── 3. coast nie wpływa na /future ───────────────────

class TestCoastDoesNotReachFuture:
    def test_future_module_has_no_coast_dependency(self):
        src = inspect.getsource(prepare_future_layout)
        assert "coast" not in src.lower()

    def test_future_layout_identical_with_and_without_coast_signature(self, monkeypatch):
        monkeypatch.setattr(coast_detector, "get_or_compute_coast_signature_lazy",
                            lambda store, lat, lon, idx_factory: NY_SIG)
        with_coast = prepare_future_layout_data(_pl_future_payload(), now=NOW_PL)
        monkeypatch.setattr(coast_detector, "get_or_compute_coast_signature_lazy",
                            lambda store, lat, lon, idx_factory: None)
        without_coast = prepare_future_layout_data(_pl_future_payload(), now=NOW_PL)
        assert with_coast == without_coast


# ─────────────────── 4. globalny cache coast ≠ dane lokalizacji ───────────────────

class TestCoastCacheKeying:
    def test_key_format_is_location_scoped(self):
        assert coast_cache_key(40.71, -74.0) == "coast:40.710:-74.000"
        assert coast_cache_key(52.23, 21.01) == "coast:52.230:21.010"

    def test_signature_of_one_location_is_not_served_to_another(self, tmp_path):
        store = JsonCoastSigStore(str(tmp_path / "coast.json"))
        store.set("coast:40.710:-74.000", {
            "version": COAST_SIG_VERSION, "is_coastal": True,
            "distance_to_ocean_km": 4.0, "sea_sectors": [[90, 200]],
            "radius_km": 25.0, "step_deg": 10,
        })
        # Wpis Nowego Jorku nie odpowiada na pytanie o Warszawę.
        assert store.get("coast:52.230:21.010") is None

    def test_lazy_compute_fills_only_the_missing_key(self, tmp_path):
        store = JsonCoastSigStore(str(tmp_path / "coast.json"))
        store.set("coast:40.710:-74.000", {
            "version": COAST_SIG_VERSION, "is_coastal": True,
            "distance_to_ocean_km": 4.0, "sea_sectors": [[90, 200]],
            "radius_km": 25.0, "step_deg": 10,
        })
        calls = []

        class _Idx:
            def compute_signature(self, **kw):
                return CoastSignature(is_coastal=False, distance_to_ocean_km=None,
                                      sea_sectors=[], radius_km=25.0, step_deg=10)

        def idx_factory():
            calls.append(1)
            return _Idx()

        sig_ny = get_or_compute_coast_signature_lazy(store, 40.71, -74.0, idx_factory)
        assert sig_ny.is_coastal is True
        assert calls == []  # cache hit — geo-indeks nie jest budowany

        sig_waw = get_or_compute_coast_signature_lazy(store, 52.23, 21.01, idx_factory)
        assert sig_waw.is_coastal is False
        assert len(calls) == 1
        # Zapis pod kluczem Warszawy, wpis Nowego Jorku nienaruszony.
        assert store.get("coast:52.230:21.010")["is_coastal"] is False
        assert store.get("coast:40.710:-74.000")["is_coastal"] is True


# ─────────────────── 5. faza Księżyca niezależna od daily_diag / volatility ───────────────────

NOW_MOON = datetime(2026, 11, 20, 10, 0, tzinfo=TZ_PL)  # pełnia 24.11 w oknie +1..+14


def _moon_payload(daily_diag):
    hours = []
    for day_off in range(1, 15):
        day = NOW_MOON + timedelta(days=day_off)
        for i in range(24):
            dt = day.replace(hour=0) + timedelta(hours=i)
            hours.append({
                "time_local": dt.isoformat(timespec="minutes"), "source": "openmeteo",
                "temp_c": 10.0, "precip_mm": 0.0, "precip_prob_pct": 0,
                "clouds_pct": 20.0, "clouds_low_pct": 10.0, "clouds_mid_pct": 10.0,
                "clouds_high_pct": 0.0, "wind_kmh": 10.0, "gust_kmh": 15.0,
                "wind_dir_deg": 270, "rh_pct": 60, "dewpoint_c": 2.0,
                "pressure_hpa": 1015, "symbol_code": "clearsky_day", "weather_code": 1,
            })
    return {
        "location": {"name": "Wiązowna", "lat": 52.17, "lon": 21.32, "tz": "Europe/Warsaw"},
        "lang": "pl", "hours": hours, "forecast_source": "OpenMeteo + Yr.no",
        "model_updated_at_local": NOW_MOON.isoformat(timespec="minutes"),
        "alerts": [], "daily_diag": daily_diag,
    }


class TestMoonIndependentOfDiagnostics:
    def test_full_moon_day_in_layout(self):
        lay = prepare_future_layout_data(_moon_payload({}), now=NOW_MOON)
        d24 = next(d for d in lay["next_days"] if d["label"].endswith("24.11"))
        assert d24["moon_phase"] == "full"
        assert d24["moon_icon"] == "wk_full_moon"

    def test_moon_identical_with_and_without_volatile_diag(self):
        plain = prepare_future_layout_data(_moon_payload({}), now=NOW_MOON)
        volatile_diag = {
            "2026-11-24": {"max_om": 25.0, "max_yr": 10.0, "min_om": 5.0, "min_yr": 1.0,
                           "spread_max": 15.0, "spread_min": 4.0, "spread": 15.0,
                           "is_volatile": True, "n_om": 24, "n_yr": 24},
        }
        volatile = prepare_future_layout_data(_moon_payload(volatile_diag), now=NOW_MOON)
        moon = lambda lay: [(d["label"], d.get("moon_phase"), d.get("moon_icon"))
                            for d in lay["next_days"]]
        assert moon(plain) == moon(volatile)

    def test_moon_day_entry_carries_no_diag_tag_even_under_volatility(self):
        # Nawet gdy dzień pełni spełnia bramki volatility, day_entry nie ma tagu
        # (martwa ścieżka — zob. test_dead_paths.py).
        volatile_diag = {
            "2026-11-24": {"max_om": 25.0, "max_yr": 10.0, "min_om": 5.0, "min_yr": 1.0,
                           "spread_max": 15.0, "spread_min": 4.0, "spread": 15.0,
                           "is_volatile": True, "n_om": 24, "n_yr": 24},
        }
        lay = prepare_future_layout_data(_moon_payload(volatile_diag), now=NOW_MOON)
        d24 = next(d for d in lay["next_days"] if d["label"].endswith("24.11"))
        assert "diag_tag" not in d24
        assert d24["moon_phase"] == "full"
