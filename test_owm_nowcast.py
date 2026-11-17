"""Testy charakteryzujące aktywną korektę OWM (nowcast) — ETAP 2.

Zamrażają OBECNE zachowanie produkcyjne modułu `owm_nowcast` oraz jego wpływ
na layouty `/now`, `/day` i `/future`, zgodnie z rejestrem etapu 1
(docs/audit/2026-11-weekly/rejestr_etapu1.md §3.4) i raportem końcowym (§3).

Pokrycie (wcześniej brak — problem P5 dokumentu decyzji):
  * extract_owm_current / extract_owm_dt_seconds / owm_is_fresh,
  * classify_owm_cloud_correction: brak snapshotu, brak chmur, świeży/nieświeży
    snapshot, timestamp s/ms, próg różnicy 24/25/26 pkt proc., taka sama / inna
    etykieta nieba, bramka high-only, reguła UV (85% -> 65%), przypadek nocny;
  * nowcast_note: progi 0,1/0,2 mm, brak godziny modelowej, nieświeży snapshot;
  * layouty: aktywna korekta zmienia Hero i pierwszy blok /now, zmienia wyłącznie
    linię „Teraz: …" na /day, nie wpływa na /future.

Testy są offline: OWM podawany jako fixture `owm_current`, pobieranie sieciowe
mockowane. Żadne zachowanie produkcyjne nie jest tu zmieniane ani „poprawiane" —
asercje opisują stan obecny, łącznie z zachowaniami dyskusyjnymi (oznaczonymi).
"""

import copy
import inspect
from datetime import datetime, timedelta

import pytest

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo

import owm_nowcast
from owm_nowcast import (
    OWM_FRESH_MAX_SEC,
    classify_owm_cloud_correction,
    extract_owm_current,
    extract_owm_dt_seconds,
    find_model_hour_for_now,
    nowcast_note,
    owm_is_fresh,
)
import prepare_layout
import prepare_now_layout
import prepare_future_layout
from prepare_layout import prepare_layout_data
from prepare_now_layout import prepare_now_layout_data
from prepare_future_layout import prepare_future_layout_data

TZ = ZoneInfo("Europe/Warsaw")
# Czwartek 2026-01-15 12:00 czasu warszawskiego.
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=TZ)
NOW_TS = NOW.timestamp()


# ─────────────────────────── fixtures ───────────────────────────

def _h_model(**over):
    """Rekord godziny modelu dla bieżącej godziny.

    Domyślnie: chmury niskie+średnie = 35 (powyżej progu high-only lowmid<=20),
    UV 0 (wtedy klasyfikator bierze UV z OWM), bez opadu.
    """
    h = {
        "time_local": NOW.isoformat(timespec="minutes"),
        "source": "openmeteo",
        "temp_c": 8.0,
        "precip_mm": 0.0,
        "clouds_pct": 35.0,
        "clouds_low_pct": 25.0,
        "clouds_mid_pct": 10.0,
        "clouds_high_pct": 0.0,
        "uv_index": 0.0,
        "wind_kmh": 12.0,
        "gust_kmh": 20.0,
    }
    h.update(over)
    return h


def _owm(dt=NOW_TS, clouds=95.0, uvi=None, rain=None, snow=None):
    """Świeży snapshot OWM (wariant płaski, jak w payloadzie `owm_current`)."""
    d = {"dt": dt, "clouds": clouds}
    if uvi is not None:
        d["uvi"] = uvi
    if rain is not None:
        d["rain"] = {"1h": rain}
    if snow is not None:
        d["snow"] = {"1h": snow}
    return d


CLASSIFY_KEYS = {
    "fresh", "reason", "model_clouds", "live_clouds", "effective_live_clouds",
    "label_model", "label_live", "icon_live", "should_override_hour0",
    "should_show_snapshot", "high_only_gate", "note_key",
}


# ─────────────────── extract_owm_current / extract_owm_dt_seconds ───────────────────

class TestExtractOwmCurrent:
    def test_none_and_empty(self):
        assert extract_owm_current(None) is None
        assert extract_owm_current({}) is None
        assert extract_owm_current([]) is None

    def test_plain_dict_returned_as_is(self):
        d = {"dt": 1, "clouds": 5}
        assert extract_owm_current(d) is d

    def test_data_list_variant_returns_first_element(self):
        # OWM 4.0 potrafi zwrócić {"data": [ {...} ]}.
        rec = {"dt": 1, "clouds": 5}
        assert extract_owm_current({"data": [rec, {"dt": 2}]}) is rec

    def test_empty_data_list_falls_back_to_wrapper(self):
        # Charakterystyka stanu obecnego: pusta lista "data" NIE daje None —
        # zwracany jest sam słownik-opakowanie (bez pól dt/clouds), a brak pól
        # wykrywają dopiero konsumenci.
        wrapper = {"data": []}
        assert extract_owm_current(wrapper) is wrapper


class TestExtractOwmDtSeconds:
    def test_seconds_timestamp(self):
        assert extract_owm_dt_seconds(_owm()) == NOW_TS

    def test_milliseconds_timestamp_is_divided_by_1000(self):
        assert extract_owm_dt_seconds(_owm(dt=NOW_TS * 1000)) == pytest.approx(NOW_TS)

    def test_missing_dt(self):
        assert extract_owm_dt_seconds({"clouds": 50}) is None

    def test_non_numeric_dt(self):
        assert extract_owm_dt_seconds({"dt": "wczoraj"}) is None

    def test_dt_from_data_list_variant(self):
        assert extract_owm_dt_seconds({"data": [{"dt": NOW_TS}]}) == NOW_TS

    def test_empty_input(self):
        assert extract_owm_dt_seconds(None) is None
        assert extract_owm_dt_seconds({}) is None


# ─────────────────── owm_is_fresh ───────────────────

class TestOwmIsFresh:
    def test_default_max_age_is_90_minutes(self):
        # Zamrożony próg świeżości (D-TERAZ-8): 5400 s.
        assert OWM_FRESH_MAX_SEC == 5400

    def test_now_is_fresh(self):
        assert owm_is_fresh(_owm(), NOW) is True

    def test_exactly_max_age_is_still_fresh(self):
        assert owm_is_fresh(_owm(dt=NOW_TS - 5400), NOW) is True

    def test_older_than_max_age_is_stale(self):
        assert owm_is_fresh(_owm(dt=NOW_TS - 5401), NOW) is False

    def test_future_timestamp_within_window_counts_as_fresh(self):
        # Charakterystyka: porównanie jest symetryczne (abs), więc snapshot
        # "z przyszłości" w obrębie okna też przechodzi bramkę.
        assert owm_is_fresh(_owm(dt=NOW_TS + 5400), NOW) is True
        assert owm_is_fresh(_owm(dt=NOW_TS + 5401), NOW) is False

    def test_missing_dt_is_never_fresh(self):
        assert owm_is_fresh({"clouds": 50}, NOW) is False
        assert owm_is_fresh(None, NOW) is False

    def test_milliseconds_timestamp_supported(self):
        assert owm_is_fresh(_owm(dt=NOW_TS * 1000), NOW) is True

    def test_now_as_float_timestamp(self):
        assert owm_is_fresh(_owm(), NOW_TS) is True

    def test_custom_max_age(self):
        assert owm_is_fresh(_owm(dt=NOW_TS - 100), NOW, max_age_sec=60) is False
        assert owm_is_fresh(_owm(dt=NOW_TS - 100), NOW, max_age_sec=120) is True


# ─────────────────── find_model_hour_for_now ───────────────────

class TestFindModelHourForNow:
    def test_exact_aware_match_returns_record(self):
        h = _h_model()
        assert find_model_hour_for_now([h], NOW) is h

    def test_minutes_seconds_are_floored(self):
        h = _h_model()
        now = NOW.replace(minute=45, second=30)
        assert find_model_hour_for_now([h], now) is h

    def test_naive_time_local_matches_aware_now(self):
        h = _h_model(time_local="2026-01-15T12:00")
        assert find_model_hour_for_now([h], NOW) is h

    def test_other_timezone_is_converted(self):
        # 11:00 UTC == 12:00 w Warszawie.
        h = _h_model(time_local="2026-01-15T11:00+00:00")
        assert find_model_hour_for_now([h], NOW) is h

    def test_no_match_returns_none_without_fallback(self):
        # Dokumentowane „Bez fallbacku": funkcja NIE zwraca pierwszego rekordu.
        h_prev = _h_model(time_local=(NOW - timedelta(hours=1)).isoformat(timespec="minutes"))
        h_next = _h_model(time_local=(NOW + timedelta(hours=1)).isoformat(timespec="minutes"))
        assert find_model_hour_for_now([h_prev, h_next], NOW) is None

    def test_empty_or_missing_input(self):
        assert find_model_hour_for_now([], NOW) is None
        assert find_model_hour_for_now(None, NOW) is None
        assert find_model_hour_for_now([_h_model()], None) is None

    def test_unparseable_records_are_skipped(self):
        good = _h_model()
        bad = _h_model(time_local="nie-czas")
        no_time = {"source": "openmeteo"}
        assert find_model_hour_for_now([bad, no_time, good], NOW) is good


# ─────────────────── classify_owm_cloud_correction ───────────────────

class TestClassifyGates:
    def test_no_snapshot(self):
        r = classify_owm_cloud_correction(_h_model(), None, NOW, is_night=False)
        assert r["fresh"] is False
        assert r["reason"] == "no_owm"
        assert r["should_override_hour0"] is False
        assert r["should_show_snapshot"] is False
        assert set(r) == CLASSIFY_KEYS

    def test_no_model_hour(self):
        r = classify_owm_cloud_correction(None, _owm(), NOW, is_night=False)
        assert r["reason"] == "no_owm"
        assert r["should_override_hour0"] is False

    def test_no_clouds_in_snapshot(self):
        r = classify_owm_cloud_correction(_h_model(), {"dt": NOW_TS}, NOW, is_night=False)
        assert r["reason"] == "no_clouds"
        # Charakterystyka: "fresh" pozostaje False mimo świeżego dt — flaga
        # oznacza tu „świeże I sklasyfikowalne", nie samą świeżość.
        assert r["fresh"] is False
        assert r["should_override_hour0"] is False

    def test_stale_snapshot(self):
        r = classify_owm_cloud_correction(
            _h_model(), _owm(dt=NOW_TS - OWM_FRESH_MAX_SEC - 1), NOW, is_night=False)
        assert r["reason"] == "stale_owm"
        assert r["fresh"] is False
        assert r["should_override_hour0"] is False

    def test_fresh_snapshot_full_shape(self):
        r = classify_owm_cloud_correction(_h_model(), _owm(), NOW, is_night=False)
        assert set(r) == CLASSIFY_KEYS
        assert r["fresh"] is True
        assert r["reason"] == "ok"
        assert r["model_clouds"] == 35.0
        assert r["live_clouds"] == 95.0
        # should_show_snapshot jest zawsze równe should_override_hour0
        # (to samo `should_act`) — zamrożone sprzężenie obu ścieżek.
        assert r["should_show_snapshot"] == r["should_override_hour0"]


class TestClassifyDiffThreshold:
    """Próg różnicy efektywnych chmur: 25 pkt proc. (zamrożony, D-TERAZ-8)."""

    @pytest.mark.parametrize("live,expected", [(34.0, False), (35.0, True), (36.0, True)])
    def test_24_25_26_points(self, live, expected):
        # Model 10% („Bezchmurnie"), live 34/35/36% („Słonecznie"/„Przejaśnienia").
        h = _h_model(clouds_pct=10.0, clouds_low_pct=10.0, clouds_mid_pct=0.0)
        # lowmid=10 <= 20, ale live<70, więc bramka high-only tu nie działa.
        r = classify_owm_cloud_correction(h, _owm(clouds=live), NOW, is_night=False)
        assert abs(r["effective_live_clouds"] - r["model_clouds"]) == pytest.approx(live - 10.0)
        assert r["should_override_hour0"] is expected
        assert r["label_model"] == "Bezchmurnie"

    def test_same_label_blocks_correction_even_with_big_diff(self):
        # Model 40% i live 66% to ta sama etykieta „Przejaśnienia" — różnica
        # 26 pkt proc. NIE wystarczy bez zmiany etykiety nieba.
        h = _h_model(clouds_pct=40.0, clouds_low_pct=20.0, clouds_mid_pct=20.0)
        r = classify_owm_cloud_correction(h, _owm(clouds=66.0), NOW, is_night=False)
        assert r["label_model"] == r["label_live"] == "Przejaśnienia"
        assert r["should_override_hour0"] is False

    def test_different_label_required_and_reported(self):
        h = _h_model()  # 35% -> „Słonecznie"
        r = classify_owm_cloud_correction(h, _owm(clouds=95.0, uvi=0.5), NOW, is_night=False)
        assert r["label_model"] == "Słonecznie"
        assert r["label_live"] == "Pochmurno"
        assert r["icon_live"] == "wk_overcast"
        assert r["should_override_hour0"] is True
        assert r["note_key"] == "nowcast_more_clouds"

    def test_fewer_clouds_direction(self):
        h = _h_model(clouds_pct=95.0, clouds_low_pct=80.0, clouds_mid_pct=15.0)
        r = classify_owm_cloud_correction(h, _owm(clouds=5.0), NOW, is_night=False)
        assert r["label_model"] == "Pochmurno"
        assert r["label_live"] == "Bezchmurnie"
        assert r["should_override_hour0"] is True
        assert r["note_key"] == "nowcast_less_clouds"

    def test_yr_clouds_raise_model_effective_clouds(self):
        # _eff_clouds_for_owm: przy obecnych polach *_yr model = max(OM, Yr).
        h = _h_model(clouds_low_pct=5.0, clouds_mid_pct=5.0,  # OM: 10%
                     clouds_pct_yr=90.0, clouds_low_pct_yr=50.0, clouds_mid_pct_yr=30.0)
        r = classify_owm_cloud_correction(h, _owm(clouds=95.0, uvi=0.5), NOW, is_night=False)
        assert r["model_clouds"] == 80.0  # max(10, 50+30) — nie 10 i nie 90


class TestHighOnlyGate:
    """Bramka high-only: model widzi wyłącznie wysokie chmury (cirrusy) — korekta
    jest blokowana, bo OWM „clouds" liczy wszystkie warstwy (stan obecny)."""

    def _high_only_model(self):
        # lowmid = 0 <= 20, prc 0 <= 0.05, model_cld = 30 <= 40.
        return _h_model(clouds_pct=30.0, clouds_low_pct=0.0, clouds_mid_pct=0.0,
                        clouds_high_pct=30.0)

    def test_gate_blocks_correction_by_day(self):
        r = classify_owm_cloud_correction(self._high_only_model(), _owm(clouds=80.0), NOW, is_night=False)
        assert r["high_only_gate"] is True
        assert r["should_override_hour0"] is False
        assert r["should_show_snapshot"] is False
        # Efektywne chmury „live" są ściągane do modelowych.
        assert r["effective_live_clouds"] == r["model_clouds"]
        assert r["label_live"] == r["label_model"]

    def test_gate_is_off_at_night(self):
        # Nocą bramka nie działa: ta sama konfiguracja daje aktywną korektę.
        r = classify_owm_cloud_correction(self._high_only_model(), _owm(clouds=80.0), NOW, is_night=True)
        assert r["high_only_gate"] is False
        assert r["should_override_hour0"] is True

    def test_gate_requires_strongly_clear_model(self):
        # model_cld = 50 > 40 -> brak bramki, korekta przechodzi.
        h = _h_model(clouds_pct=80.0, clouds_low_pct=30.0, clouds_mid_pct=20.0,
                     clouds_high_pct=30.0)
        r = classify_owm_cloud_correction(h, _owm(clouds=80.0), NOW, is_night=False)
        assert r["high_only_gate"] is False

    def test_gate_requires_owm_claims_cloudy(self):
        # live < 70 -> OWM nie „zarzuca" zachmurzenia, bramka nie powstaje.
        r = classify_owm_cloud_correction(self._high_only_model(), _owm(clouds=60.0), NOW, is_night=False)
        assert r["high_only_gate"] is False


class TestUvCapRule:
    """Reguła UV: przy live >= 85% i UV > 1,2 za dnia efektywne chmury live
    są obniżane do 65% (założenie: cienka, jasna zasłona). Stan obecny."""

    def _model_30(self):
        # 30% -> „Słonecznie"; lowmid 30 > 20, więc bramka high-only nie działa.
        return _h_model(clouds_pct=30.0, clouds_low_pct=25.0, clouds_mid_pct=5.0)

    def test_uv_below_threshold_keeps_live_clouds(self):
        r = classify_owm_cloud_correction(self._model_30(), _owm(clouds=95.0, uvi=1.2), NOW, is_night=False)
        assert r["effective_live_clouds"] == 95.0
        assert r["label_live"] == "Pochmurno"
        assert r["should_override_hour0"] is True

    def test_uv_above_threshold_caps_live_clouds_to_65(self):
        r = classify_owm_cloud_correction(self._model_30(), _owm(clouds=95.0, uvi=1.3), NOW, is_night=False)
        assert r["live_clouds"] == 95.0
        assert r["effective_live_clouds"] == 65.0
        assert r["label_live"] == "Przejaśnienia"
        # Korekta nadal aktywna (różnica 35 pkt, inna etykieta), ale łagodniejsza.
        assert r["should_override_hour0"] is True

    def test_cap_can_flip_decision_when_labels_equalize(self):
        # Model 50% („Przejaśnienia"): bez capa live 95% dałoby „Pochmurno"
        # (korekta), z capem 65% etykieta = modelowa -> korekta znika.
        h = _h_model(clouds_pct=50.0, clouds_low_pct=25.0, clouds_mid_pct=25.0)
        no_cap = classify_owm_cloud_correction(h, _owm(clouds=95.0, uvi=1.0), NOW, is_night=False)
        capped = classify_owm_cloud_correction(h, _owm(clouds=95.0, uvi=1.3), NOW, is_night=False)
        assert no_cap["should_override_hour0"] is True
        assert capped["effective_live_clouds"] == 65.0
        assert capped["label_live"] == capped["label_model"] == "Przejaśnienia"
        assert capped["should_override_hour0"] is False

    def test_uv_from_model_wins_over_owm(self):
        # uv = uv_index modelu, gdy > 0; dopiero potem uvi z OWM.
        h = self._model_30()
        h["uv_index"] = 3.0
        r = classify_owm_cloud_correction(h, _owm(clouds=95.0, uvi=0.1), NOW, is_night=False)
        assert r["effective_live_clouds"] == 65.0

    def test_live_below_85_is_never_capped(self):
        r = classify_owm_cloud_correction(self._model_30(), _owm(clouds=84.9, uvi=5.0), NOW, is_night=False)
        assert r["effective_live_clouds"] == 84.9

    def test_live_exactly_85_is_capped(self):
        r = classify_owm_cloud_correction(self._model_30(), _owm(clouds=85.0, uvi=5.0), NOW, is_night=False)
        assert r["effective_live_clouds"] == 65.0

    def test_night_disables_cap(self):
        r = classify_owm_cloud_correction(self._model_30(), _owm(clouds=95.0, uvi=5.0), NOW, is_night=True)
        assert r["effective_live_clouds"] == 95.0


class TestNightCase:
    def test_night_labels_and_icons(self):
        h = _h_model(clouds_pct=10.0, clouds_low_pct=10.0, clouds_mid_pct=0.0)
        r = classify_owm_cloud_correction(h, _owm(clouds=95.0, uvi=0.0), NOW, is_night=True)
        assert r["label_model"] == "Bezchmurnie"
        assert r["label_live"] == "Pochmurno"
        assert r["should_override_hour0"] is True
        # Nocna ikona nieba bezchmurnego pojawia się przy niskich chmurach:
        r_low = classify_owm_cloud_correction(
            _h_model(clouds_pct=95.0, clouds_low_pct=80.0, clouds_mid_pct=15.0),
            _owm(clouds=5.0), NOW, is_night=True)
        assert r_low["icon_live"] == "wk_clear_night"


# ─────────────────── nowcast_note ───────────────────

class TestNowcastNote:
    """Notka o ukrytym opadzie: model < 0,1 mm i OWM >= 0,2 mm (zamrożone progi)."""

    def test_hidden_precip_is_reported(self):
        note = nowcast_note([_h_model(precip_mm=0.0)], NOW, _owm(rain=0.2), lang="pl")
        assert note == "Lokalnie możliwe słabe opady poza prognozą."

    def test_localized_note(self):
        note = nowcast_note([_h_model(precip_mm=0.0)], NOW, _owm(rain=0.2), lang="en")
        assert note == "Locally possible light rain outside the forecast."

    def test_model_below_and_above_0_1mm(self):
        assert nowcast_note([_h_model(precip_mm=0.09)], NOW, _owm(rain=0.5), lang="pl") is not None
        assert nowcast_note([_h_model(precip_mm=0.1)], NOW, _owm(rain=0.5), lang="pl") is None

    def test_owm_below_and_above_0_2mm(self):
        assert nowcast_note([_h_model()], NOW, _owm(rain=0.19), lang="pl") is None
        assert nowcast_note([_h_model()], NOW, _owm(rain=0.2), lang="pl") is not None

    def test_snow_counts_as_precip(self):
        assert nowcast_note([_h_model()], NOW, _owm(snow=0.3), lang="pl") is not None

    def test_rain_and_snow_sum(self):
        note = nowcast_note([_h_model()], NOW, _owm(rain=0.1, snow=0.1), lang="pl")
        assert note is not None

    def test_stale_snapshot_silences_note(self):
        stale = _owm(dt=NOW_TS - OWM_FRESH_MAX_SEC - 1, rain=5.0)
        assert nowcast_note([_h_model()], NOW, stale, lang="pl") is None

    def test_missing_model_hour_gives_no_note(self):
        other_hour = _h_model(time_local=(NOW + timedelta(hours=3)).isoformat(timespec="minutes"))
        assert nowcast_note([other_hour], NOW, _owm(rain=5.0), lang="pl") is None

    def test_empty_inputs(self):
        assert nowcast_note([], NOW, _owm(rain=5.0), lang="pl") is None
        assert nowcast_note([_h_model()], NOW, None, lang="pl") is None
        assert nowcast_note(None, NOW, _owm(rain=5.0), lang="pl") is None

    def test_non_dict_rain_is_treated_as_zero(self):
        # Charakterystyka: "rain": None / "rain": 0.5 (nie-dict) -> brak notki.
        assert nowcast_note([_h_model()], NOW, {"dt": NOW_TS, "rain": None}, lang="pl") is None
        assert nowcast_note([_h_model()], NOW, {"dt": NOW_TS, "rain": 0.5}, lang="pl") is None


# ─────────────────── fixtures layoutowe ───────────────────

@pytest.fixture
def no_coast(monkeypatch):
    """Wyłącza ścieżkę coast w layoutach (bez zapisów do cache, bez stosu geo)."""
    import coast_detector
    monkeypatch.setattr(coast_detector, "get_or_compute_coast_signature_lazy",
                        lambda store, lat, lon, idx_factory: None)


def _layout_hour(dt, **over):
    """Godzina w kształcie payloadu; chmury 35% (nisko+średnio — bez high-only)."""
    h = {
        "time_local": dt.isoformat(timespec="minutes"),
        "source": "openmeteo",
        "temp_c": 8.0,
        "precip_mm": 0.0,
        "precip_prob_pct": 5,
        "clouds_pct": 35.0,
        "clouds_low_pct": 25.0,
        "clouds_mid_pct": 10.0,
        "clouds_high_pct": 0.0,
        "wind_kmh": 12.0,
        "gust_kmh": 20.0,
        "wind_dir_deg": 270,
        "rh_pct": 70,
        "dewpoint_c": 3.0,
        "pressure_hpa": 1015,
        "symbol_code": "clearsky_day",
        "weather_code": 1,
    }
    h.update(over)
    return h


def _layout_hours(n_hours=48):
    start = NOW.replace(hour=0)
    return [_layout_hour(start + timedelta(hours=i)) for i in range(n_hours)]


def _layout_payload(hours, **extra):
    p = {
        "location": {"name": "Warszawa", "lat": 52.23, "lon": 21.01, "tz": "Europe/Warsaw"},
        "lang": "pl",
        "hours": hours,
        "forecast_source": "OpenMeteo + Yr.no",
        "model_updated_at_local": "2026-01-15T03:00+01:00",
        "alerts": [],
        "daily_diag": {},
    }
    p.update(extra)
    return p


# Świeży snapshot: OWM widzi 95% chmur tam, gdzie model daje 35%.
OWM_LIVE = {"dt": int(NOW_TS), "clouds": 95, "uvi": 0.5}


# ─────────────────── /now: aktywna korekta ───────────────────

class TestNowLayoutCorrection:
    """Ablacja jak w audycie (raport końcowy §3): layout z `owm_current`
    vs layout bez — przy aktywnej korekcie zmieniają się Hero i pierwszy blok."""

    def _cards(self, monkeypatch, hours, owm_current):
        monkeypatch.setattr(prepare_now_layout, "get_current_weather",
                            lambda *a, **k: None)  # offline; brak sieci
        payload = _layout_payload(hours, owm_current=owm_current)
        card = prepare_now_layout_data(payload, now=NOW + timedelta(minutes=5))
        return card, payload

    def test_active_correction_changes_hero_and_first_block(self, monkeypatch, no_coast):
        without, _ = self._cards(monkeypatch, copy.deepcopy(_layout_hours()), None)
        with_, payload = self._cards(monkeypatch, copy.deepcopy(_layout_hours()), dict(OWM_LIVE))

        # Hero: ikona i opis zmieniają się pod wpływem korekty.
        assert without["main_icon"] == "wk_sun_one_cloud"
        assert with_["main_icon"] == "wk_overcast"
        assert without["summary"].split("\n")[0] == "Obecnie słonecznie"
        assert with_["summary"].split("\n")[0].startswith("Obecnie pochmurno")

        # Pierwszy blok godzinowy (bieżąca godzina) zmienia ikonę i opis.
        assert without["today_blocks"][0]["icon"] == "wk_sun_one_cloud"
        assert without["today_blocks"][0]["primary_desc"] == "Słonecznie"
        assert with_["today_blocks"][0]["icon"] == "wk_overcast"
        assert with_["today_blocks"][0]["primary_desc"] == "Pochmurno"

        # Pozostałe bloki pozostają nietknięte.
        assert without["today_blocks"][1:] == with_["today_blocks"][1:]

        # Korekta żyje w pamięci: `_cld_override` ląduje na rekordzie bieżącej
        # godziny przekazanego payloadu (mutacja w pamięci — stan obecny).
        h0 = next(h for h in payload["hours"]
                  if h["time_local"].startswith("2026-01-15T12"))
        assert h0["_cld_override"] == 95.0
        assert h0["_radar_changed_label"] is True
        assert "_cld_override" not in payload["hours"][0] or \
            payload["hours"][0]["time_local"].startswith("2026-01-15T12")

    def test_no_correction_without_snapshot(self, monkeypatch, no_coast):
        card, payload = self._cards(monkeypatch, copy.deepcopy(_layout_hours()), None)
        assert card["main_icon"] == "wk_sun_one_cloud"
        assert all("_cld_override" not in h for h in payload["hours"])

    def test_inactive_correction_changes_nothing(self, monkeypatch, no_coast):
        # Świeży snapshot, ale różnica 20 pkt proc. < 25 -> korekta nieaktywna.
        weak = {"dt": int(NOW_TS), "clouds": 55, "uvi": 0.5}
        without, _ = self._cards(monkeypatch, copy.deepcopy(_layout_hours()), None)
        with_, payload = self._cards(monkeypatch, copy.deepcopy(_layout_hours()), weak)
        assert with_ == without
        assert all("_cld_override" not in h for h in payload["hours"])

    def test_stale_snapshot_changes_nothing(self, monkeypatch, no_coast):
        stale = {"dt": int(NOW_TS) - OWM_FRESH_MAX_SEC - 1, "clouds": 95}
        without, _ = self._cards(monkeypatch, copy.deepcopy(_layout_hours()), None)
        with_, _ = self._cards(monkeypatch, copy.deepcopy(_layout_hours()), stale)
        assert with_ == without


# ─────────────────── /day: linia snapshotu „Teraz: …" ───────────────────

class TestDayLayoutSnapshotLine:
    """Na /day OWM nie nadpisuje Hero (audyt: „Hero /day świadomie bez zmian"),
    a jedynie dokłada linię snapshotu; w fixture'ach audytowych bloki były
    stabilne (mediana bloku nie przechodzi progu przy korekcie jednej godziny)."""

    def _cards(self, monkeypatch, owm_current):
        monkeypatch.setattr(prepare_layout, "get_current_weather",
                            lambda *a, **k: None)  # offline; bramka kosztowa nie strzela
        payload = _layout_payload(copy.deepcopy(_layout_hours()), owm_current=owm_current)
        card = prepare_layout_data(payload, now=NOW + timedelta(minutes=5))
        return card, payload

    def test_active_correction_adds_snapshot_line_only(self, monkeypatch, no_coast):
        without, _ = self._cards(monkeypatch, None)
        with_, _ = self._cards(monkeypatch, dict(OWM_LIVE))

        # Linia snapshotu „Teraz: …" pojawia się tylko z aktywną korektą.
        assert without["current_snapshot_line"] is None
        assert with_["current_snapshot_line"] == "Teraz: pochmurno"

        # Hero /day pozostaje bez zmian (zachowanie udokumentowane w audycie).
        assert with_["main_icon"] == without["main_icon"]
        assert with_["summary"] == without["summary"]

        # Bloki godzinowe: w tej charakterystyce mediana bloku z bieżącą godziną
        # nie przechodzi progu, więc bloki są identyczne — jak w ablacji audytu
        # (10/10 payloadów: zmienia się wyłącznie linia snapshotu).
        assert with_["today_blocks"] == without["today_blocks"]

        # Pozostałe sekcje karty bez zmian.
        assert with_["next_days"] == without["next_days"]
        assert with_["alerts"] == without["alerts"]
        assert with_["worth_knowing"] == without["worth_knowing"]
        assert with_["context_line"] == without["context_line"]

    def test_stale_snapshot_adds_no_line(self, monkeypatch, no_coast):
        stale = {"dt": int(NOW_TS) - OWM_FRESH_MAX_SEC - 1, "clouds": 95}
        card, _ = self._cards(monkeypatch, stale)
        assert card["current_snapshot_line"] is None

    def test_snapshot_below_threshold_adds_no_line(self, monkeypatch, no_coast):
        weak = {"dt": int(NOW_TS), "clouds": 55, "uvi": 0.5}  # różnica 20 pkt < 25
        card, _ = self._cards(monkeypatch, weak)
        assert card["current_snapshot_line"] is None


# ─────────────────── /future: OWM nie wpływa ───────────────────

class TestFutureLayoutOwmFree:
    def _future_payload(self, **extra):
        hours = []
        for day_off in range(1, 15):
            day = NOW + timedelta(days=day_off)
            for i in range(24):
                hours.append(_layout_hour(day.replace(hour=0) + timedelta(hours=i)))
        return _layout_payload(hours, **extra)

    def test_future_layout_ignores_owm_current(self, no_coast):
        without = prepare_future_layout_data(self._future_payload(owm_current=None), now=NOW)
        rich_owm = {"dt": int(NOW_TS), "clouds": 99, "uvi": 8.0,
                    "rain": {"1h": 5.0}, "snow": {"1h": 1.0}}
        with_ = prepare_future_layout_data(self._future_payload(owm_current=rich_owm), now=NOW)
        assert with_ == without

    def test_future_module_does_not_reference_owm(self):
        # Strukturalnie: kod /future nie importuje nowcastu OWM.
        src = inspect.getsource(prepare_future_layout)
        assert "owm" not in src.lower()
        assert "nowcast" not in src.lower()
