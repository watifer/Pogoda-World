import copy
import re
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback
    from backports.zoneinfo import ZoneInfo

from PIL import Image

from i18n import STRINGS, t, translate_weather_text
from image_generator import WIDTH, generate_weather_card
from prepare_future_layout import prepare_future_layout_data
from prepare_layout import prepare_layout_data
from worth_knowing import (
    MAX_WK_LEN,
    MOON_TIP_TEXTS,
    _REF_FULL_MOON_UTC,
    _build_moon_night_candidate,
    _moon_phase_info,
    _night_weather_allows_moon_tip,
    _select_primary_night_hours,
    _true_night_allows_moon_tip,
    build_worth_knowing,
    get_moon_phase_for_local_date,
)

ALL_LANGS = ("pl", "en", "fr", "de", "es", "no")
NON_PL_LANGS = ("en", "fr", "de", "es", "no")
PL_DIACRITICS = re.compile(r"[ąćęłńśźż]", re.IGNORECASE)
VS16 = "\ufe0f"


def _iso(dt):
    return dt.isoformat(timespec="minutes")


def _make_hour(dt, source="openmeteo", low=0, mid=0, high=0, precip=0.0, pop=0, uv=0.0, code=0):
    h = {
        "time_local": _iso(dt),
        "temp_c": 10.0,
        "dewpoint_c": 5.0,
        "rh_pct": 70,
        "wind_kmh": 8,
        "gust_kmh": 12,
        "clouds_pct": min(100, low + mid + high),
        "clouds_low_pct": low,
        "clouds_mid_pct": mid,
        "clouds_high_pct": high,
        "pressure_hpa": 1020,
        "uv_index": uv,
        "precip_mm": precip,
        "precip_eff_mm": precip,
        "precip_prob_pct": pop,
        "weather_code": code,
        "weather_code_eff": code,
        "symbol_code": None,
        "symbol_code_eff": None,
        "source": source,
    }
    if source == "openmeteo":
        h.update({
            "clouds_pct_yr": min(100, low + mid + high),
            "clouds_low_pct_yr": low,
            "clouds_mid_pct_yr": mid,
            "clouds_high_pct_yr": high,
        })
    return h


def _payload_for_report_dt(
    report_dt,
    *,
    tz_name="UTC",
    lat=0.0,
    lon=0.0,
    low=0,
    mid=0,
    high=0,
    uv=0.0,
    precip=0.0,
    pop=0,
    code=0,
    include_yr=True,
    include_hours=True,
    lang="pl",
):
    night_start = report_dt.replace(hour=22, minute=0, second=0, microsecond=0)
    hours = []
    if include_hours:
        for off in range(0, 9):  # 22..06; 06 ma zostać wykluczona przez selektor
            dt = night_start + timedelta(hours=off)
            hours.append(_make_hour(dt, "openmeteo", low=low, mid=mid, high=high, precip=precip, pop=pop, uv=uv, code=code))
            if include_yr:
                hours.append(_make_hour(dt, "yrno", low=low, mid=mid, high=high, precip=precip, pop=pop, uv=uv, code=code))

    return {
        "lang": lang,
        "generated_at_local": _iso(report_dt),
        "forecast_source": "OpenMeteo + Yr.no" if include_yr else "OpenMeteo",
        "location": {"name": "Test", "lat": lat, "lon": lon, "tz": tz_name},
        "hours": hours,
    }


def _full_day_payload(base_dt, *, tz_name="Europe/Warsaw", lat=52.23, lon=21.01, lang="pl", low=0, days_ahead=5):
    """Buduje kompletny wielodniowy payload godzinowy dla prepare_layout_data / prepare_future_layout_data."""
    tz = ZoneInfo(tz_name)
    start_dt = base_dt.astimezone(tz) if base_dt.tzinfo else base_dt.replace(tzinfo=tz)
    day0 = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    hours = []
    for h_off in range(0, 24 * (days_ahead + 1)):
        dt = day0 + timedelta(hours=h_off)
        uv = 0.0 if (dt.hour >= 20 or dt.hour < 6) else 2.0
        hours.append(_make_hour(dt, "openmeteo", low=low, uv=uv))
        hours.append(_make_hour(dt, "yrno", low=low, uv=uv))
    return {
        "lang": lang,
        "generated_at_local": start_dt.isoformat(timespec="seconds"),
        "model_updated_at_local": start_dt.isoformat(timespec="seconds"),
        "forecast_source": "OpenMeteo + Yr.no",
        "location": {"name": "Warszawa", "lat": lat, "lon": lon, "tz": tz_name},
        "hours": hours,
        "daily_diag": {},
    }


def test_moon_phase_bucket_uses_local_date_difference_and_strict_full_today():
    # Pełnia: 2000-01-21 04:40 UTC
    full_info = _moon_phase_info(_REF_FULL_MOON_UTC)
    assert full_info is not None
    assert full_info["bucket"] == "full_today"
    assert full_info["time_local_hm"] == "04:40"

    # Raport z poprzedniego dnia o 23:50 (zaledwie ~4h50m przed pełnią, abs_delta < 0.5):
    # inna lokalna data kalendarzowa => MUSI być before_1, nigdy full_today!
    prev_day_late = datetime(2000, 1, 20, 23, 50, tzinfo=timezone.utc)
    b1 = _moon_phase_info(prev_day_late)
    assert b1 is not None
    assert b1["bucket"] == "before_1"
    assert b1["time_local_hm"] == "04:40"

    before_3 = _moon_phase_info(datetime(2000, 1, 18, 8, 0, tzinfo=timezone.utc))
    assert before_3 is not None
    assert before_3["bucket"] == "before_3"

    after_3 = _moon_phase_info(datetime(2000, 1, 24, 20, 0, tzinfo=timezone.utc))
    assert after_3 is not None
    assert after_3["bucket"] == "after_3"

    assert _moon_phase_info(datetime(2000, 1, 17, 23, 0, tzinfo=timezone.utc)) is None
    assert _moon_phase_info(datetime(2000, 1, 25, 1, 0, tzinfo=timezone.utc)) is None


def test_new_moon_phase_bucket_supports_today_and_before_1_only():
    # Nów: 2026-10-10 15:50 UTC = 17:50 Europe/Warsaw
    tz_waw = ZoneInfo("Europe/Warsaw")
    new_today = _moon_phase_info(datetime(2026, 10, 10, 9, 0, tzinfo=tz_waw), tz=tz_waw, phase="new")
    assert new_today is not None
    assert new_today["bucket"] == "new_today"
    assert new_today["day_diff"] == 0
    assert new_today["time_local_hm"] == "17:50"

    new_before_1 = _moon_phase_info(datetime(2026, 10, 9, 23, 30, tzinfo=tz_waw), tz=tz_waw, phase="new")
    assert new_before_1 is not None
    assert new_before_1["bucket"] == "new_before_1"
    assert new_before_1["day_diff"] == 1
    assert new_before_1["time_local_hm"] == "17:50"

    # Dwa dni przed nowiem oraz dzień po nowiu -> brak bucketa
    assert _moon_phase_info(datetime(2026, 10, 8, 12, 0, tzinfo=tz_waw), tz=tz_waw, phase="new") is None
    assert _moon_phase_info(datetime(2026, 10, 11, 0, 10, tzinfo=tz_waw), tz=tz_waw, phase="new") is None


def test_local_timezone_conversion_assigns_night_phase_to_correct_local_day():
    # Pełnia 2026-10-26 wypada o 04:12 UTC, czyli:
    # - w Warszawie (Europe/Warsaw, UTC+1) to 2026-10-26 05:12 -> dzień 26.10 (25.10 = before_1)
    # - w Honolulu (Pacific/Honolulu, UTC-10) to 2026-10-25 18:12 -> dzień 25.10 (24.10 = before_1)!
    tz_waw = ZoneInfo("Europe/Warsaw")
    waw_info = _moon_phase_info(datetime(2026, 10, 26, 8, 0, tzinfo=tz_waw), tz=tz_waw, phase="full")
    assert waw_info is not None
    assert waw_info["bucket"] == "full_today"
    assert waw_info["time_local_hm"] == "05:12"

    waw_prev = _moon_phase_info(datetime(2026, 10, 25, 8, 0, tzinfo=tz_waw), tz=tz_waw, phase="full")
    assert waw_prev is not None
    assert waw_prev["bucket"] == "before_1"
    assert waw_prev["time_local_hm"] == "05:12"

    tz_hnl = ZoneInfo("Pacific/Honolulu")
    hnl_24 = _moon_phase_info(datetime(2026, 10, 24, 9, 0, tzinfo=tz_hnl), tz=tz_hnl, phase="full")
    assert hnl_24 is not None
    assert hnl_24["bucket"] == "before_1"
    assert hnl_24["time_local_hm"] == "18:12"

    hnl_25 = _moon_phase_info(datetime(2026, 10, 25, 9, 0, tzinfo=tz_hnl), tz=tz_hnl, phase="full")
    assert hnl_25 is not None
    assert hnl_25["bucket"] == "full_today"
    assert hnl_25["time_local_hm"] == "18:12"

    hnl_26 = _moon_phase_info(datetime(2026, 10, 26, 9, 0, tzinfo=tz_hnl), tz=tz_hnl, phase="full")
    assert hnl_26 is not None
    assert hnl_26["bucket"] == "after_1"

    # Nów 2026-10-10 15:50 UTC:
    # - w Warszawie (UTC+2) to 2026-10-10 17:50 -> 10.10 to new_today
    # - na Kiritimati (UTC+14) to 2026-10-11 05:50 -> 10.10 to new_before_1!
    tz_kiritimati = ZoneInfo("Pacific/Kiritimati")
    kir_10 = _moon_phase_info(datetime(2026, 10, 10, 9, 0, tzinfo=tz_kiritimati), tz=tz_kiritimati, phase="new")
    assert kir_10 is not None
    assert kir_10["bucket"] == "new_before_1"
    assert kir_10["time_local_hm"] == "05:50"


def test_dst_transition_between_day_before_and_phase_day():
    tz_waw = ZoneInfo("Europe/Warsaw")

    # 1. Wiosenna zmiana czasu CET (+01) -> CEST (+02) w niedzielę 2021-03-28:
    # Pełnia 2021-03-28 18:48 UTC = 20:48 CEST (nie 19:48 CET).
    sat_before_spring_full = datetime(2021, 3, 27, 12, 0, tzinfo=tz_waw)
    assert sat_before_spring_full.utcoffset() == timedelta(hours=1)
    p_spring_full = _payload_for_report_dt(
        sat_before_spring_full, tz_name="Europe/Warsaw", lat=52.23, lon=21.01, low=95
    )
    c_spring_full = _build_moon_night_candidate(p_spring_full, alerts=[])
    assert c_spring_full is not None
    assert c_spring_full["text"] == "Jutro pełnia Księżyca o godz. 20:48."

    # 2. Wiosenna zmiana czasu CET (+01) -> CEST (+02) w niedzielę 2028-03-26:
    # Nów 2028-03-26 04:31 UTC = 06:31 CEST (nie 05:31 CET).
    sat_before_spring_new = datetime(2028, 3, 25, 12, 0, tzinfo=tz_waw)
    assert sat_before_spring_new.utcoffset() == timedelta(hours=1)
    p_spring_new = _payload_for_report_dt(
        sat_before_spring_new, tz_name="Europe/Warsaw", lat=52.23, lon=21.01, low=95
    )
    c_spring_new = _build_moon_night_candidate(p_spring_new, alerts=[])
    assert c_spring_new is not None
    assert c_spring_new["text"] == "Jutro nów Księżyca o godz. 06:31."

    # 3. Jesienna zmiana czasu CEST (+02) -> CET (+01) w niedzielę 2016-10-30:
    # Nów 2016-10-30 17:38 UTC = 18:38 CET (nie 19:38 CEST).
    sat_before_autumn_new = datetime(2016, 10, 29, 12, 0, tzinfo=tz_waw)
    assert sat_before_autumn_new.utcoffset() == timedelta(hours=2)
    p_autumn_new = _payload_for_report_dt(
        sat_before_autumn_new, tz_name="Europe/Warsaw", lat=52.23, lon=21.01, low=95
    )
    c_autumn_new = _build_moon_night_candidate(p_autumn_new, alerts=[])
    assert c_autumn_new is not None
    assert c_autumn_new["text"] == "Jutro nów Księżyca o godz. 18:38."


def test_night_selector_uses_primary_hours_and_excludes_06():
    payload = _payload_for_report_dt(_REF_FULL_MOON_UTC.replace(hour=12, minute=0), high=90)
    night, core, _, _ = _select_primary_night_hours(payload)

    assert len(night) == 8
    assert all(h["source"] == "openmeteo" for h in night)
    assert [h["time_local"][11:13] for h in night] == ["22", "23", "00", "01", "02", "03", "04", "05"]
    assert [h["time_local"][11:13] for h in core] == ["23", "00", "01", "02", "03", "04"]


def test_weather_gate_accepts_high_clouds_but_rejects_low_mid_concrete():
    payload = _payload_for_report_dt(_REF_FULL_MOON_UTC.replace(hour=12, minute=0), high=90)
    night, _, _, _ = _select_primary_night_hours(payload)
    assert _night_weather_allows_moon_tip(night, payload["hours"]) is True

    payload_bad = _payload_for_report_dt(_REF_FULL_MOON_UTC.replace(hour=12, minute=0), low=80, mid=0, high=0)
    night_bad, _, _, _ = _select_primary_night_hours(payload_bad)
    assert _night_weather_allows_moon_tip(night_bad, payload_bad["hours"]) is False


def test_weather_gate_rejects_precip_pop_and_fog():
    report_dt = _REF_FULL_MOON_UTC.replace(hour=12, minute=0)

    payload_rain = _payload_for_report_dt(report_dt, precip=0.2, pop=80)
    night, _, _, _ = _select_primary_night_hours(payload_rain)
    assert _night_weather_allows_moon_tip(night, payload_rain["hours"]) is False

    payload_pop = _payload_for_report_dt(report_dt, precip=0.0, pop=60)
    night, _, _, _ = _select_primary_night_hours(payload_pop)
    assert _night_weather_allows_moon_tip(night, payload_pop["hours"]) is False

    payload_fog = _payload_for_report_dt(report_dt, code=45)
    night, _, _, _ = _select_primary_night_hours(payload_fog)
    assert _night_weather_allows_moon_tip(night, payload_fog["hours"]) is False


def test_true_night_uses_uv_and_sun_altitude():
    report_dt = _REF_FULL_MOON_UTC.replace(hour=12, minute=0)
    payload = _payload_for_report_dt(report_dt, lat=0.0, lon=0.0, uv=0.0)
    _, core, _, _ = _select_primary_night_hours(payload)
    assert _true_night_allows_moon_tip(core, 0.0, 0.0) is True

    payload_uv = _payload_for_report_dt(report_dt, lat=0.0, lon=0.0, uv=0.5)
    _, core_uv, _, _ = _select_primary_night_hours(payload_uv)
    assert _true_night_allows_moon_tip(core_uv, 0.0, 0.0) is False

    # Biała noc / dzień polarny: samo UV=0 nie wystarcza, wysokość Słońca blokuje tip.
    polar_report = report_dt.replace(year=2026, month=6, day=20)
    payload_polar = _payload_for_report_dt(polar_report, lat=69.6, lon=18.9, uv=0.0)
    _, core_polar, _, _ = _select_primary_night_hours(payload_polar)
    assert _true_night_allows_moon_tip(core_polar, 69.6, 18.9) is False


def test_full_moon_day_clear_vs_cloudy_and_single_model():
    report_dt = _REF_FULL_MOON_UTC.replace(hour=12, minute=0)  # 2000-01-21

    # 1. Pogodna noc + 2 modele -> pełny tekst z godziną i jaśniejszym niebem
    payload_clear = _payload_for_report_dt(report_dt, lat=0.0, lon=0.0, high=90)
    c_clear = _build_moon_night_candidate(payload_clear, alerts=[])
    assert c_clear is not None
    assert c_clear["text"] == "Dziś pełnia Księżyca o godz. 04:40. Niebo może być tej nocy wyraźnie jaśniejsze."
    assert "kulminacja" not in c_clear["text"].lower()

    # 2. Pochmurna noc w dzień pełni -> neutralny komunikat z godziną (bez zdania o jaśniejszym niebie)
    payload_cloudy = _payload_for_report_dt(report_dt, lat=0.0, lon=0.0, low=95)
    c_cloudy = _build_moon_night_candidate(payload_cloudy, alerts=[])
    assert c_cloudy is not None
    assert c_cloudy["text"] == "Dziś pełnia Księżyca o godz. 04:40."

    # 3. Jeden model lub puste hours w dzień pełni -> nadal pokazuje neutralny komunikat z godziną
    payload_one_model = _payload_for_report_dt(report_dt, lat=0.0, lon=0.0, high=0, include_yr=False)
    c_one_model = _build_moon_night_candidate(payload_one_model, alerts=[])
    assert c_one_model is not None
    assert c_one_model["text"] == "Dziś pełnia Księżyca o godz. 04:40."

    payload_no_hours = _payload_for_report_dt(report_dt, lat=0.0, lon=0.0, include_hours=False, include_yr=False)
    c_no_hours = _build_moon_night_candidate(payload_no_hours, alerts=[])
    assert c_no_hours is not None
    assert c_no_hours["text"] == "Dziś pełnia Księżyca o godz. 04:40."


def test_full_moon_tomorrow_clear_cloudy_precip_fog_polar_single_model_and_no_hours():
    # Pełnia referencyjna: 2000-01-21 04:40 UTC -> dzień wcześniej: 2000-01-20
    report_before_1 = datetime(2000, 1, 20, 12, 0, tzinfo=timezone.utc)

    # 1. Pogodna noc + 2 modele -> moon_full_tomorrow_clear
    payload_clear = _payload_for_report_dt(report_before_1, lat=0.0, lon=0.0, high=90)
    c_clear = _build_moon_night_candidate(payload_clear, alerts=[])
    assert c_clear is not None
    assert c_clear["text"] == "Jutro pełnia Księżyca o godz. 04:40. Już tej nocy niebo może być wyraźnie jaśniejsze."

    # 2. Zachmurzenie -> moon_full_tomorrow
    payload_cloudy = _payload_for_report_dt(report_before_1, lat=0.0, lon=0.0, low=95)
    c_cloudy = _build_moon_night_candidate(payload_cloudy, alerts=[])
    assert c_cloudy is not None
    assert c_cloudy["text"] == "Jutro pełnia Księżyca o godz. 04:40."

    # 3. Opad / mgła -> moon_full_tomorrow
    payload_rain = _payload_for_report_dt(report_before_1, lat=0.0, lon=0.0, precip=0.4, pop=80)
    c_rain = _build_moon_night_candidate(payload_rain, alerts=[])
    assert c_rain is not None
    assert c_rain["text"] == "Jutro pełnia Księżyca o godz. 04:40."

    payload_fog = _payload_for_report_dt(report_before_1, lat=0.0, lon=0.0, code=45)
    c_fog = _build_moon_night_candidate(payload_fog, alerts=[])
    assert c_fog is not None
    assert c_fog["text"] == "Jutro pełnia Księżyca o godz. 04:40."

    # 4. Biała noc / dzień polarny (Tromsø, przed pełnią 2026-06-30 01:57 Europe/Oslo) -> moon_full_tomorrow
    tz_oslo = ZoneInfo("Europe/Oslo")
    polar_before_full = datetime(2026, 6, 29, 12, 0, tzinfo=tz_oslo)
    payload_polar = _payload_for_report_dt(
        polar_before_full, tz_name="Europe/Oslo", lat=69.6, lon=18.9, low=0, uv=0.0
    )
    c_polar = _build_moon_night_candidate(payload_polar, alerts=[])
    assert c_polar is not None
    assert c_polar["text"] == "Jutro pełnia Księżyca o godz. 01:57."

    # 5. Jeden model -> moon_full_tomorrow
    payload_one_model = _payload_for_report_dt(report_before_1, lat=0.0, lon=0.0, low=0, include_yr=False)
    c_one_model = _build_moon_night_candidate(payload_one_model, alerts=[])
    assert c_one_model is not None
    assert c_one_model["text"] == "Jutro pełnia Księżyca o godz. 04:40."

    # 6. Puste godziny -> moon_full_tomorrow (również przez build_worth_knowing)
    payload_no_hours = _payload_for_report_dt(
        report_before_1, lat=0.0, lon=0.0, include_hours=False, include_yr=False
    )
    c_no_hours = _build_moon_night_candidate(payload_no_hours, alerts=[])
    assert c_no_hours is not None
    assert c_no_hours["text"] == "Jutro pełnia Księżyca o godz. 04:40."

    wk_1m = build_worth_knowing(
        payload=payload_no_hours,
        blocks=[],
        alerts=[],
        temp_min=8,
        temp_max=12,
        max_wind=5,
        gust_kmh=8,
        total_precip_mm=0,
        built_blocks=[],
        ta=[],
        current_hour=8,
    )
    assert wk_1m is not None
    assert wk_1m["text"] == "Jutro pełnia Księżyca o godz. 04:40."


def test_new_moon_today_and_tomorrow_regardless_of_clouds_models_or_hours():
    # Nów: 2026-10-10 15:50 UTC = 17:50 Europe/Warsaw
    tz_waw = ZoneInfo("Europe/Warsaw")
    report_new = datetime(2026, 10, 10, 9, 0, tzinfo=tz_waw)
    report_prev = report_new - timedelta(days=1)

    # 1. new_today: pochmurno + 1 model + puste godziny -> dotychczasowy komunikat bez regresji
    payload_new = _payload_for_report_dt(
        report_new, tz_name="Europe/Warsaw", lat=52.23, lon=21.01, low=100, include_yr=False, include_hours=False
    )
    c_new = _build_moon_night_candidate(payload_new, alerts=[])
    assert c_new is not None
    assert c_new["text"] == "Dziś nów Księżyca (17:50) — noc będzie wyjątkowo ciemna."
    assert "kulminacja" not in c_new["text"].lower()

    # 2. new_before_1: pogodnie, pochmurno, 1 model oraz puste godziny -> zawsze moon_new_tomorrow z godziną
    payload_prev_clear = _payload_for_report_dt(
        report_prev, tz_name="Europe/Warsaw", lat=52.23, lon=21.01, low=0
    )
    payload_prev_cloudy = _payload_for_report_dt(
        report_prev, tz_name="Europe/Warsaw", lat=52.23, lon=21.01, low=100
    )
    payload_prev_one_model = _payload_for_report_dt(
        report_prev, tz_name="Europe/Warsaw", lat=52.23, lon=21.01, low=0, include_yr=False
    )
    payload_prev_no_hours = _payload_for_report_dt(
        report_prev, tz_name="Europe/Warsaw", lat=52.23, lon=21.01, low=100, include_yr=False, include_hours=False
    )

    for p in (payload_prev_clear, payload_prev_cloudy, payload_prev_one_model, payload_prev_no_hours):
        cand = _build_moon_night_candidate(p, alerts=[])
        assert cand is not None
        assert cand["text"] == "Jutro nów Księżyca o godz. 17:50."

    wk_prev_1m = build_worth_knowing(
        payload=payload_prev_no_hours,
        blocks=[],
        alerts=[],
        temp_min=8,
        temp_max=12,
        max_wind=5,
        gust_kmh=8,
        total_precip_mm=0,
        built_blocks=[],
        ta=[],
        current_hour=8,
    )
    assert wk_prev_1m is not None
    assert wk_prev_1m["text"] == "Jutro nów Księżyca o godz. 17:50."

    # 3. Nów dwa dni wcześniej oraz dzień później -> brak komunikatu
    payload_prev_2 = _payload_for_report_dt(
        report_new - timedelta(days=2), tz_name="Europe/Warsaw", lat=52.23, lon=21.01, low=0
    )
    payload_next_1 = _payload_for_report_dt(
        report_new + timedelta(days=1), tz_name="Europe/Warsaw", lat=52.23, lon=21.01, low=0
    )
    assert _build_moon_night_candidate(payload_prev_2, alerts=[]) is None
    assert _build_moon_night_candidate(payload_next_1, alerts=[]) is None


def test_before_2_and_after_days_still_require_clear_sky_and_two_models():
    # Pełnia za 2 dni (2000-01-19) oraz dzień po pełni (2000-01-22)
    report_before_2 = datetime(2000, 1, 19, 12, 0, tzinfo=timezone.utc)
    report_after_1 = datetime(2000, 1, 22, 12, 0, tzinfo=timezone.utc)

    for report_dt, bucket in ((report_before_2, "before_2"), (report_after_1, "after_1")):
        payload_clear = _payload_for_report_dt(report_dt, lat=0.0, lon=0.0, low=0)
        c_clear = _build_moon_night_candidate(payload_clear, alerts=[])
        assert c_clear is not None
        assert c_clear["text"] == MOON_TIP_TEXTS[bucket]

        # Pochmurno -> None
        payload_cloudy = _payload_for_report_dt(report_dt, lat=0.0, lon=0.0, low=85)
        assert _build_moon_night_candidate(payload_cloudy, alerts=[]) is None

        # 1 model -> None
        payload_one_model = _payload_for_report_dt(report_dt, lat=0.0, lon=0.0, low=0, include_yr=False)
        assert _build_moon_night_candidate(payload_one_model, alerts=[]) is None


def test_hard_gates_block_moon_candidate(monkeypatch):
    report_dt = _REF_FULL_MOON_UTC.replace(hour=12, minute=0)
    payload = _payload_for_report_dt(report_dt, lat=0.0, lon=0.0)

    # 1. ENABLE_WK_MOON_TIP=0 blokuje wszystko
    monkeypatch.setenv("ENABLE_WK_MOON_TIP", "0")
    assert _build_moon_night_candidate(payload, alerts=[]) is None
    monkeypatch.setenv("ENABLE_WK_MOON_TIP", "1")

    # 2. Brak timezone lub błędny timezone blokuje wszystko
    bad_tz = dict(payload, location={"name": "Test", "lat": 0.0, "lon": 0.0, "tz": ""})
    assert _build_moon_night_candidate(bad_tz, alerts=[]) is None
    invalid_tz = dict(payload, location={"name": "Test", "lat": 0.0, "lon": 0.0, "tz": "Mars/Olympus"})
    assert _build_moon_night_candidate(invalid_tz, alerts=[]) is None

    # 3. Brak daty (brak generated_at_local i puste hours) blokuje wszystko
    no_date = dict(payload, generated_at_local=None, hours=[])
    assert _build_moon_night_candidate(no_date, alerts=[]) is None


def test_build_worth_knowing_can_return_moon_tip_when_no_stronger_candidate_and_with_single_model():
    report_dt = _REF_FULL_MOON_UTC.replace(hour=12, minute=0)
    payload = _payload_for_report_dt(report_dt, lat=0.0, lon=0.0, high=90)
    expected_clear = t("pl", "moon_full_today_clear", time="04:40")

    wk = build_worth_knowing(
        payload=payload,
        blocks=[],
        alerts=[],
        temp_min=8,
        temp_max=12,
        max_wind=5,
        gust_kmh=8,
        total_precip_mm=0,
        built_blocks=[],
        ta=[],
        current_hour=8,
    )
    assert wk is not None
    assert wk["text"] == expected_clear

    # Przy 1 modelu w dzień pełni build_worth_knowing zwraca neutralny komunikat astronomiczny
    payload_1m = _payload_for_report_dt(report_dt, lat=0.0, lon=0.0, include_yr=False, include_hours=False)
    wk_1m = build_worth_knowing(
        payload=payload_1m,
        blocks=[],
        alerts=[],
        temp_min=8,
        temp_max=12,
        max_wind=5,
        gust_kmh=8,
        total_precip_mm=0,
        built_blocks=[],
        ta=[],
        current_hour=8,
    )
    assert wk_1m is not None
    assert wk_1m["text"] == t("pl", "moon_full_today", time="04:40")


def test_moon_tip_is_appended_after_existing_worth_knowing_text_and_max_wk_len_unchanged():
    assert MAX_WK_LEN == 140

    report_dt = datetime(2000, 1, 20, 12, 0, tzinfo=timezone.utc)  # dzień przed pełnią
    payload = _payload_for_report_dt(report_dt, lat=0.0, lon=0.0, high=90)
    expected_clear = t("pl", "moon_full_tomorrow_clear", time="04:40")
    ta = [
        {**_make_hour(report_dt.replace(hour=h, minute=0), "openmeteo"), "temp_c": temp, "wind_kmh": 4, "gust_kmh": 6}
        for h, temp in [(8, 19), (9, 20), (10, 21), (11, 22)]
    ]

    wk = build_worth_knowing(
        payload=payload,
        blocks=[],
        alerts=["Upał — test alertu w sekcji Uważaj"],
        temp_min=18,
        temp_max=22,
        max_wind=5,
        gust_kmh=8,
        total_precip_mm=0,
        built_blocks=[],
        ta=ta,
        current_hour=8,
    )

    assert wk is not None
    assert "\n\n" in wk["text"]
    lead, tail = wk["text"].split("\n\n", 1)
    assert lead != expected_clear
    assert len(lead) <= MAX_WK_LEN + 5
    assert tail == expected_clear


def test_moon_tip_i18n_uses_dedicated_keys_and_real_translate_weather_text_path():
    # 1. Stare koszyki before/after
    s_before = MOON_TIP_TEXTS["before_2"]
    expected_before = {
        "en": "Clear night expected — Full moon in 2 days. The sky should be noticeably brighter.",
        "fr": "Nuit dégagée prévue — Pleine lune dans 2 jours. Le ciel devrait être nettement plus lumineux.",
        "de": "Eine klare Nacht ist zu erwarten — Vollmond in 2 Tagen. Der Himmel sollte deutlich heller sein.",
        "es": "Se espera una noche despejada — Luna llena en 2 días. El cielo debería verse claramente más iluminado.",
        "no": "Det ventes en klar natt — Fullmåne om 2 dager. Himmelen bør bli merkbart lysere.",
    }
    for lang, translated in expected_before.items():
        assert translate_weather_text(s_before, lang) == translated

    # 2. Wszystkie dedykowane klucze z placeholderem {time} w STRINGS (today + tomorrow)
    time_keys = (
        "moon_full_today_clear",
        "moon_full_today",
        "moon_new_today",
        "moon_full_tomorrow_clear",
        "moon_full_tomorrow",
        "moon_new_tomorrow",
    )
    for key in time_keys:
        for lang in ALL_LANGS:
            assert key in STRINGS[lang]
            tpl = STRINGS[lang][key]
            assert "{time}" in tpl
            assert " · " not in tpl
            assert "·" not in tpl
            assert VS16 not in tpl

        pl_text = t("pl", key, time="18:42")
        assert translate_weather_text(pl_text, "pl") == pl_text

        for lang in NON_PL_LANGS:
            expected_lang_text = t(lang, key, time="18:42")
            assert translate_weather_text(pl_text, lang) == expected_lang_text
            assert "18:42" in expected_lang_text
            assert not PL_DIACRITICS.search(expected_lang_text)
            assert "pełnia" not in expected_lang_text.lower()
            assert "nów" not in expected_lang_text.lower()
            assert "jutro" not in expected_lang_text.lower()


def test_moon_tip_i18n_survives_appended_worth_knowing_paragraph():
    for key in (
        "moon_new_today",
        "moon_full_tomorrow_clear",
        "moon_full_tomorrow",
        "moon_new_tomorrow",
    ):
        pl_moon = t("pl", key, time="18:42")
        combined = f"najcieplejszy moment dnia to okolice 15:00 (22°C)\n\n{pl_moon}"
        for lang in NON_PL_LANGS:
            translated = translate_weather_text(combined, lang)
            assert "\n\n" in translated
            assert translated.endswith(t(lang, key, time="18:42"))
            assert not PL_DIACRITICS.search(translated)


def test_trend_keeps_icons_only_on_exact_full_and_new_moon_days():
    tz_waw = ZoneInfo("Europe/Warsaw")

    # Helper dla /trend zwraca fazę wyłącznie w dokładnym lokalnym dniu pełni/nowiu
    assert get_moon_phase_for_local_date(date(2026, 10, 9), tz_waw) is None
    assert get_moon_phase_for_local_date(date(2026, 10, 10), tz_waw)["phase"] == "new"
    assert get_moon_phase_for_local_date(date(2026, 10, 25), tz_waw) is None
    assert get_moon_phase_for_local_date(date(2026, 10, 26), tz_waw)["phase"] == "full"

    # Pełne 14 dni /trend od 2026-10-08 obejmuje zarówno przeddzień i dzień nowiu (09-10.10),
    # jak i inne dni; od 2026-10-20 obejmuje przeddzień i dzień pełni (25-26.10).
    trend_new = prepare_future_layout_data(
        _full_day_payload(datetime(2026, 10, 8, 10, 0, tzinfo=tz_waw), days_ahead=14),
        now=datetime(2026, 10, 8, 10, 0, tzinfo=tz_waw),
    )
    flagged_new = [(d["moon_date_short"], d["moon_phase"]) for d in trend_new["next_days"] if d.get("moon_phase")]
    assert flagged_new == [("10.10", "new")]

    trend_full = prepare_future_layout_data(
        _full_day_payload(datetime(2026, 10, 20, 10, 0, tzinfo=tz_waw), days_ahead=14),
        now=datetime(2026, 10, 20, 10, 0, tzinfo=tz_waw),
    )
    flagged_full = [(d["moon_date_short"], d["moon_phase"]) for d in trend_full["next_days"] if d.get("moon_phase")]
    assert flagged_full == [("26.10", "full")]


def test_day_layout_and_image_render_preserve_multiparagraph_tomorrow_moon_tip():
    tz_waw = ZoneInfo("Europe/Warsaw")
    # 2026-10-25 -> przeddzień pełni 2026-10-26 05:12 w Warszawie
    now = datetime(2026, 10, 25, 9, 0, tzinfo=tz_waw)

    for lang in ("pl", "en", "de"):
        payload = _full_day_payload(now, lang=lang, low=0, days_ahead=5)
        layout = prepare_layout_data(payload, now=now)
        assert layout["worth_knowing"] is not None
        wk_text = layout["worth_knowing"]["text"]
        expected_tail = t(lang, "moon_full_tomorrow_clear", time="05:12")
        assert wk_text.endswith(expected_tail)
        if lang != "pl":
            assert not PL_DIACRITICS.search(wk_text)

    payload_pl = _full_day_payload(now, lang="pl", low=0, days_ahead=5)
    layout_multi = prepare_layout_data(payload_pl, now=now)
    # Wymuszamy 2 akapity, jeśli pogoda syntetyczna dała tylko tip księżycowy
    moon_tail = t("pl", "moon_full_tomorrow_clear", time="05:12")
    layout_single = copy.deepcopy(layout_multi)
    layout_single["worth_knowing"] = {"title": "Dziś warto wiedzieć", "text": "Spokojny, pogodny dzień bez opadów."}
    layout_two_para = copy.deepcopy(layout_multi)
    layout_two_para["worth_knowing"] = {
        "title": "Dziś warto wiedzieć",
        "text": f"Spokojny, pogodny dzień bez opadów.\n\n{moon_tail}",
    }

    before_snapshot = copy.deepcopy(layout_two_para)
    img_single = Image.open(generate_weather_card(layout_single))
    img_two_para = Image.open(generate_weather_card(layout_two_para))

    assert layout_two_para == before_snapshot
    assert img_single.size[0] == WIDTH
    assert img_two_para.size[0] == WIDTH
    assert img_two_para.size[1] > img_single.size[1]
