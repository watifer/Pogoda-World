from datetime import timedelta

from i18n import translate_weather_text
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


def _payload_for_target_midpoint(target_midpoint, *, lat=0.0, lon=0.0, low=0, mid=0, high=0, uv=0.0, precip=0.0, pop=0, code=0, include_yr=True):
    # Kandydat bada noc od daty generated_at 22:00 do następnego dnia 06:00,
    # a fazę liczy dla środka okna, czyli 02:00.
    generated_date = (target_midpoint - timedelta(days=1)).date()
    generated_at = target_midpoint.replace(year=generated_date.year, month=generated_date.month, day=generated_date.day, hour=12, minute=0)
    night_start = generated_at.replace(hour=22, minute=0)

    hours = []
    for off in range(0, 9):  # 22..06; 06 ma zostać wykluczona przez selektor
        dt = night_start + timedelta(hours=off)
        hours.append(_make_hour(dt, "openmeteo", low=low, mid=mid, high=high, precip=precip, pop=pop, uv=uv, code=code))
        if include_yr:
            hours.append(_make_hour(dt, "yrno", low=low, mid=mid, high=high, precip=precip, pop=pop, uv=uv, code=code))

    return {
        "generated_at_local": _iso(generated_at),
        "forecast_source": "OpenMeteo + Yr.no",
        "location": {"name": "Test", "lat": lat, "lon": lon, "tz": "UTC"},
        "hours": hours,
    }


def test_moon_phase_bucket_keeps_three_day_variants():
    assert _moon_phase_info(_REF_FULL_MOON_UTC)["bucket"] == "full_today"

    before_3 = _moon_phase_info(_REF_FULL_MOON_UTC - timedelta(days=3.3))
    assert before_3 is not None
    assert before_3["bucket"] == "before_3"
    assert before_3["illumination"] >= 0.85

    after_3 = _moon_phase_info(_REF_FULL_MOON_UTC + timedelta(days=3.3))
    assert after_3 is not None
    assert after_3["bucket"] == "after_3"

    assert _moon_phase_info(_REF_FULL_MOON_UTC - timedelta(days=3.6)) is None


def test_night_selector_uses_primary_hours_and_excludes_06():
    payload = _payload_for_target_midpoint(_REF_FULL_MOON_UTC.replace(hour=2, minute=0), high=90)
    night, core, _, _ = _select_primary_night_hours(payload)

    assert len(night) == 8
    assert all(h["source"] == "openmeteo" for h in night)
    assert [h["time_local"][11:13] for h in night] == ["22", "23", "00", "01", "02", "03", "04", "05"]
    assert [h["time_local"][11:13] for h in core] == ["23", "00", "01", "02", "03", "04"]


def test_weather_gate_accepts_high_clouds_but_rejects_low_mid_concrete():
    payload = _payload_for_target_midpoint(_REF_FULL_MOON_UTC.replace(hour=2, minute=0), high=90)
    night, _, _, _ = _select_primary_night_hours(payload)
    assert _night_weather_allows_moon_tip(night, payload["hours"]) is True

    payload_bad = _payload_for_target_midpoint(_REF_FULL_MOON_UTC.replace(hour=2, minute=0), low=80, mid=0, high=0)
    night_bad, _, _, _ = _select_primary_night_hours(payload_bad)
    assert _night_weather_allows_moon_tip(night_bad, payload_bad["hours"]) is False


def test_weather_gate_rejects_precip_pop_and_fog():
    target = _REF_FULL_MOON_UTC.replace(hour=2, minute=0)

    payload_rain = _payload_for_target_midpoint(target, precip=0.2, pop=80)
    night, _, _, _ = _select_primary_night_hours(payload_rain)
    assert _night_weather_allows_moon_tip(night, payload_rain["hours"]) is False

    payload_pop = _payload_for_target_midpoint(target, precip=0.0, pop=60)
    night, _, _, _ = _select_primary_night_hours(payload_pop)
    assert _night_weather_allows_moon_tip(night, payload_pop["hours"]) is False

    payload_fog = _payload_for_target_midpoint(target, code=45)
    night, _, _, _ = _select_primary_night_hours(payload_fog)
    assert _night_weather_allows_moon_tip(night, payload_fog["hours"]) is False


def test_true_night_uses_uv_and_sun_altitude():
    target = _REF_FULL_MOON_UTC.replace(hour=2, minute=0)
    payload = _payload_for_target_midpoint(target, lat=0.0, lon=0.0, uv=0.0)
    _, core, _, _ = _select_primary_night_hours(payload)
    assert _true_night_allows_moon_tip(core, 0.0, 0.0) is True

    payload_uv = _payload_for_target_midpoint(target, lat=0.0, lon=0.0, uv=0.5)
    _, core_uv, _, _ = _select_primary_night_hours(payload_uv)
    assert _true_night_allows_moon_tip(core_uv, 0.0, 0.0) is False

    # Biała noc / dzień polarny: samo UV=0 nie wystarcza, wysokość Słońca blokuje tip.
    polar_target = target.replace(year=2026, month=6, day=21)
    payload_polar = _payload_for_target_midpoint(polar_target, lat=69.6, lon=18.9, uv=0.0)
    _, core_polar, _, _ = _select_primary_night_hours(payload_polar)
    assert _true_night_allows_moon_tip(core_polar, 69.6, 18.9) is False


def test_build_moon_candidate_and_alert_gating():
    target = _REF_FULL_MOON_UTC.replace(hour=2, minute=0)
    payload = _payload_for_target_midpoint(target, lat=0.0, lon=0.0, high=90)

    c = _build_moon_night_candidate(payload, alerts=[])
    assert c is not None
    assert c["category"] == "moon_night"
    assert c["priority"] == 14
    assert c["text"] == MOON_TIP_TEXTS["full_today"]

    # Alerty z sekcji „Uważaj" nie blokują już dopisku księżycowego w „Warto wiedzieć".
    assert _build_moon_night_candidate(payload, alerts=["Burze"]) is not None

    one_model = dict(payload)
    one_model["forecast_source"] = "OpenMeteo"
    assert _build_moon_night_candidate(one_model, alerts=[]) is None


def test_build_worth_knowing_can_return_moon_tip_when_no_stronger_candidate():
    target = _REF_FULL_MOON_UTC.replace(hour=2, minute=0)
    payload = _payload_for_target_midpoint(target, lat=0.0, lon=0.0, high=90)

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
    assert wk["text"] == MOON_TIP_TEXTS["full_today"]

    wk_alert = build_worth_knowing(
        payload=payload,
        blocks=[],
        alerts=["Silny wiatr"],
        temp_min=8,
        temp_max=12,
        max_wind=5,
        gust_kmh=8,
        total_precip_mm=0,
        built_blocks=[],
        ta=[],
        current_hour=8,
    )
    assert wk_alert is not None
    assert wk_alert["text"] == MOON_TIP_TEXTS["full_today"]


def test_moon_tip_is_appended_after_existing_worth_knowing_text():
    target = _REF_FULL_MOON_UTC.replace(hour=2, minute=0)
    payload = _payload_for_target_midpoint(target, lat=0.0, lon=0.0, high=90)
    base_dt = target - timedelta(days=1)
    ta = [
        {**_make_hour(base_dt.replace(hour=h, minute=0), "openmeteo"), "temp_c": temp, "wind_kmh": 4, "gust_kmh": 6}
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
    assert wk["text"].endswith(MOON_TIP_TEXTS["full_today"])
    assert wk["text"].split("\n\n", 1)[0] != MOON_TIP_TEXTS["full_today"]


def test_moon_tip_i18n_uses_real_translate_weather_text_path():
    s = MOON_TIP_TEXTS["before_2"]
    expected = {
        "en": "Clear night expected — Full moon in 2 days. The sky should be noticeably brighter.",
        "fr": "Nuit dégagée prévue — Pleine lune dans 2 jours. Le ciel devrait être nettement plus lumineux.",
        "de": "Eine klare Nacht ist zu erwarten — Vollmond in 2 Tagen. Der Himmel sollte deutlich heller sein.",
        "es": "Se espera una noche despejada — Luna llena en 2 días. El cielo debería verse claramente más iluminado.",
        "no": "Det ventes en klar natt — Fullmåne om 2 dager. Himmelen bør bli merkbart lysere.",
    }
    for lang, translated in expected.items():
        assert translate_weather_text(s, lang) == translated
        assert "pełnia" not in translated.lower()
        assert "pogodna noc" not in translated.lower()
