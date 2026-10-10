from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from i18n import STRINGS, t, translate_weather_text
from worth_knowing import (
    MOON_TIP_TEXTS,
    _REF_FULL_MOON_UTC,
    _build_moon_night_candidate,
    _moon_phase_info,
    _night_weather_allows_moon_tip,
    _select_primary_night_hours,
    _true_night_allows_moon_tip,
    build_worth_knowing,
)


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
        "generated_at_local": _iso(report_dt),
        "forecast_source": "OpenMeteo + Yr.no" if include_yr else "OpenMeteo",
        "location": {"name": "Test", "lat": lat, "lon": lon, "tz": tz_name},
        "hours": hours,
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

    before_3 = _moon_phase_info(datetime(2000, 1, 18, 8, 0, tzinfo=timezone.utc))
    assert before_3 is not None
    assert before_3["bucket"] == "before_3"

    after_3 = _moon_phase_info(datetime(2000, 1, 24, 20, 0, tzinfo=timezone.utc))
    assert after_3 is not None
    assert after_3["bucket"] == "after_3"

    assert _moon_phase_info(datetime(2000, 1, 17, 23, 0, tzinfo=timezone.utc)) is None
    assert _moon_phase_info(datetime(2000, 1, 25, 1, 0, tzinfo=timezone.utc)) is None


def test_local_timezone_conversion_assigns_night_phase_to_correct_local_day():
    # Pełnia 2026-10-26 wypada o 04:12 UTC, czyli:
    # - w Warszawie (Europe/Warsaw, UTC+1) to 2026-10-26 05:12 -> dzień 26.10
    # - w Nowym Jorku (America/New_York, UTC-4) to 2026-10-26 00:12 -> dzień 26.10
    # - w Honolulu (Pacific/Honolulu, UTC-10) to 2026-10-25 18:12 -> dzień 25.10!
    tz_waw = ZoneInfo("Europe/Warsaw")
    waw_info = _moon_phase_info(datetime(2026, 10, 26, 8, 0, tzinfo=tz_waw), tz=tz_waw, phase="full")
    assert waw_info is not None
    assert waw_info["bucket"] == "full_today"
    assert waw_info["time_local_hm"] == "05:12"

    tz_hnl = ZoneInfo("Pacific/Honolulu")
    hnl_25 = _moon_phase_info(datetime(2026, 10, 25, 9, 0, tzinfo=tz_hnl), tz=tz_hnl, phase="full")
    assert hnl_25 is not None
    assert hnl_25["bucket"] == "full_today"
    assert hnl_25["time_local_hm"] == "18:12"

    hnl_26 = _moon_phase_info(datetime(2026, 10, 26, 9, 0, tzinfo=tz_hnl), tz=tz_hnl, phase="full")
    assert hnl_26 is not None
    assert hnl_26["bucket"] == "after_1"


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


def test_new_moon_day_shows_only_on_exact_day_regardless_of_clouds_or_models():
    # Nów: 2026-10-10 15:50 UTC = 17:50 Europe/Warsaw
    tz_waw = ZoneInfo("Europe/Warsaw")
    report_new = datetime(2026, 10, 10, 9, 0, tzinfo=tz_waw)

    # Pochmurno + 1 model + puste godziny -> nów i tak się pojawia z godziną lokalną
    payload_new = _payload_for_report_dt(
        report_new, tz_name="Europe/Warsaw", lat=52.23, lon=21.01, low=100, include_yr=False, include_hours=False
    )
    c_new = _build_moon_night_candidate(payload_new, alerts=[])
    assert c_new is not None
    assert c_new["text"] == "Dziś nów Księżyca (17:50) — noc będzie wyjątkowo ciemna."
    assert "kulminacja" not in c_new["text"].lower()

    # Dzień przed nowiem i dzień po nowiu -> brak komunikatu
    payload_prev = _payload_for_report_dt(
        report_new - timedelta(days=1), tz_name="Europe/Warsaw", lat=52.23, lon=21.01, low=0
    )
    payload_next = _payload_for_report_dt(
        report_new + timedelta(days=1), tz_name="Europe/Warsaw", lat=52.23, lon=21.01, low=0
    )
    assert _build_moon_night_candidate(payload_prev, alerts=[]) is None
    assert _build_moon_night_candidate(payload_next, alerts=[]) is None


def test_before_and_after_days_still_require_clear_sky_and_two_models():
    # Dzień przed pełnią (2000-01-20)
    report_before_1 = datetime(2000, 1, 20, 12, 0, tzinfo=timezone.utc)

    payload_clear = _payload_for_report_dt(report_before_1, lat=0.0, lon=0.0, low=0)
    c_clear = _build_moon_night_candidate(payload_clear, alerts=[])
    assert c_clear is not None
    assert c_clear["text"] == MOON_TIP_TEXTS["before_1"]

    # Pochmurno 1 dzień przed pełnią -> None
    payload_cloudy = _payload_for_report_dt(report_before_1, lat=0.0, lon=0.0, low=85)
    assert _build_moon_night_candidate(payload_cloudy, alerts=[]) is None

    # 1 model 1 dzień przed pełnią -> None
    payload_one_model = _payload_for_report_dt(report_before_1, lat=0.0, lon=0.0, low=0, include_yr=False)
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


def test_moon_tip_is_appended_after_existing_worth_knowing_text():
    report_dt = _REF_FULL_MOON_UTC.replace(hour=12, minute=0)
    payload = _payload_for_report_dt(report_dt, lat=0.0, lon=0.0, high=90)
    expected_clear = t("pl", "moon_full_today_clear", time="04:40")
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
    assert wk["text"].endswith(expected_clear)
    assert wk["text"].split("\n\n", 1)[0] != expected_clear


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

    # 2. Nowe dedykowane klucze z placeholderem {time} w STRINGS
    for key in ("moon_full_today_clear", "moon_full_today", "moon_new_today"):
        pl_text = t("pl", key, time="18:42")
        for lang in ("en", "fr", "de", "es", "no"):
            assert key in STRINGS[lang]
            assert "{time}" in STRINGS[lang][key]
            expected_lang_text = t(lang, key, time="18:42")
            assert translate_weather_text(pl_text, lang) == expected_lang_text
            assert "18:42" in expected_lang_text
            assert "pełnia" not in expected_lang_text.lower()
            assert "nów" not in expected_lang_text.lower()


def test_moon_tip_i18n_survives_appended_worth_knowing_paragraph():
    pl_new = t("pl", "moon_new_today", time="18:42")
    combined = f"Silny wiatr może być odczuwalny.\n\n{pl_new}"
    translated = translate_weather_text(combined, "en")

    assert "\n\n" in translated
    assert translated.endswith(t("en", "moon_new_today", time="18:42"))
    assert "nów" not in translated.lower()
