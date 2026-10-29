"""Regression tests for prospective WK rules in the active /day report."""
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

import pytest

import worth_knowing as wk


TZ = ZoneInfo("Europe/Warsaw")
BASE = datetime(2026, 7, 15, tzinfo=TZ)


def _hour(hour, day_offset=0, **overrides):
    dt = (BASE + timedelta(days=day_offset)).replace(hour=hour)
    item = {
        "time_local": dt.isoformat(timespec="minutes"),
        "source": "openmeteo",
        "temp_c": 4.0,
        "dewpoint_c": 0.0,
        "uv_index": 0.0,
        "thunder_prob": 0.0,
        "clouds_pct": 20.0,
        "clouds_low_pct": 20.0,
        "clouds_mid_pct": 0.0,
        "clouds_high_pct": 0.0,
        "precip_mm": 0.0,
        "precip_prob_pct": 0,
        "wind_kmh": 10.0,
        "gust_kmh": 12.0,
        "pressure_hpa": 1015.0,
        "weather_code": 1,
        "symbol_code": "fair_day",
    }
    item.update(overrides)
    return item


def _level1(hours, current_hour, temp_min=4, temp_max=4, total_precip=0,
            max_wind=10, gust=12, afternoon=None):
    if afternoon is None:
        afternoon = current_hour >= 12
    return wk._candidates_level1(
        [], temp_min, temp_max, total_precip, afternoon, None,
        max_wind, gust, hours, current_hour,
    )


def _categories(candidates):
    return {candidate["category"] for candidate in candidates}


def _build(hours, current_hour, total_precip=0):
    return wk.build_worth_knowing(
        payload={"forecast_source": "OpenMeteo + Yr.no", "lang": "pl"},
        blocks=[], alerts=[], temp_min=4, temp_max=4,
        max_wind=10, gust_kmh=10, total_precip_mm=total_precip,
        is_afternoon_report=current_hour >= 12,
        ta=hours, current_hour=current_hour,
        summary_line="", context_line_text="", built_blocks=[],
    )


def _disable_optional_candidates(monkeypatch):
    monkeypatch.setenv("ENABLE_WK_MOON_TIP", "0")
    monkeypatch.setenv("ENABLE_WK_AI_CANDIDATE", "0")


def test_storm_risk_ignores_past_hour_at_1000():
    hours = [
        _hour(9, thunder_prob=65),
        _hour(10, thunder_prob=0),
        _hour(11, thunder_prob=0),
    ]
    assert "storm_risk" not in _categories(_level1(hours, 10))


@pytest.mark.parametrize("event_hour", [10, 11])
def test_storm_risk_still_uses_current_or_future_hour(event_hour):
    hours = [_hour(hour, thunder_prob=65 if hour == event_hour else 0)
             for hour in (9, 10, 11)]
    candidates = _level1(hours, 10)
    storm = next(c for c in candidates if c["category"] == "storm_risk")
    assert storm["priority"] == 0
    assert "65% szans" in storm["text"]


def test_report_1559_uses_hour_15_and_excludes_storm_at_1400():
    report_time = BASE.replace(hour=15, minute=59)
    current_hour = report_time.hour

    past_only = [_hour(14, thunder_prob=65), _hour(15), _hour(16)]
    assert "storm_risk" not in _categories(_level1(past_only, current_hour))

    current = [_hour(14), _hour(15, thunder_prob=65), _hour(16)]
    assert "storm_risk" in _categories(_level1(current, current_hour))


def test_dewpoint_ignores_past_hour_at_1500():
    hours = [
        _hour(9, dewpoint_c=22),
        _hour(15, dewpoint_c=12),
        _hour(16, dewpoint_c=12),
    ]
    assert "dewpoint_alert" not in _categories(_level1(hours, 15))


@pytest.mark.parametrize("event_hour", [15, 16])
def test_dewpoint_still_uses_current_or_future_hour(event_hour):
    hours = [_hour(hour, dewpoint_c=22 if hour == event_hour else 12)
             for hour in (9, 15, 16)]
    candidates = _level1(hours, 15)
    dewpoint = next(c for c in candidates if c["category"] == "dewpoint_alert")
    assert dewpoint["priority"] == 2
    assert "Tropikalna duchota" in dewpoint["text"]


def test_uv_ignores_past_peak_at_1500():
    hours = []
    for hour in range(9, 17):
        past_peak = hour == 10
        hours.append(_hour(
            hour,
            uv_index=8 if past_peak else 1,
            clouds_pct=0 if past_peak else 20,
            clouds_low_pct=0 if past_peak else 20,
        ))
    assert "uv_alert" not in _categories(_level1(hours, 15))


@pytest.mark.parametrize("event_hour", [15, 16])
def test_uv_still_uses_current_or_future_daylight_hour(event_hour):
    hours = []
    for hour in range(9, 17):
        event = hour == event_hour
        hours.append(_hour(
            hour,
            uv_index=8 if event else 1,
            clouds_pct=0 if event else 20,
            clouds_low_pct=0 if event else 20,
        ))
    candidates = _level1(hours, 15)
    uv = next(c for c in candidates if c["category"] == "uv_alert")
    assert uv["priority"] == 12
    assert "UV 8" in uv["text"]


def test_prospective_rules_do_not_use_next_day_even_when_hour_is_later():
    hours = [_hour(hour) for hour in (9, 10, 11, 12)]
    hours.append(_hour(
        11, day_offset=1, thunder_prob=65, dewpoint_c=22, uv_index=8,
        clouds_pct=0, clouds_low_pct=0,
    ))

    categories = _categories(_level1(hours, 10))
    assert "storm_risk" not in categories
    assert "dewpoint_alert" not in categories
    assert "uv_alert" not in categories


def test_daily_aggregate_rules_keep_their_unfiltered_inputs():
    hours = [
        _hour(8, temp_c=-12, precip_mm=6, wind_kmh=35, gust_kmh=35),
        _hour(9, temp_c=-12, wind_kmh=35, gust_kmh=35),
        _hour(10, temp_c=2),
        _hour(11, temp_c=2),
    ]
    candidates = _level1(
        hours, 10, temp_min=-12, temp_max=2, total_precip=6,
        max_wind=35, gust=35, afternoon=False,
    )
    candidates += wk._candidates_level2([], -12, 35, 35, 6)
    categories = _categories(candidates)

    assert "temp_change" in categories
    assert "wind_cold" in categories
    assert "precip_sum" in categories
    amplitude = next(c for c in candidates if c["category"] == "temp_change")
    precip = next(c for c in candidates if c["category"] == "precip_sum")
    assert "14°" in amplitude["text"]
    assert "6 mm śniegu" in precip["text"]


def test_final_day_selection_drops_past_storm_but_keeps_daily_precip_total(monkeypatch):
    _disable_optional_candidates(monkeypatch)
    hours = [_hour(9, thunder_prob=65), _hour(10), _hour(11)]

    result = _build(hours, 10, total_precip=6)

    assert result is not None
    assert "Ryzyko burzy" not in result["text"]
    assert result["text"] == "dziś może spaść łącznie ok. 6 mm deszczu"


def test_trust_gating_still_uses_past_hours_and_can_suppress_uv(monkeypatch):
    _disable_optional_candidates(monkeypatch)
    monkeypatch.setenv("WK_TRUST_GATING", "1")
    hours = [_hour(9, precip_prob_pct=80)]
    for hour in (10, 11, 12):
        uv_peak = hour == 11
        hours.append(_hour(
            hour,
            uv_index=8 if uv_peak else 1,
            clouds_pct=0 if uv_peak else 20,
            clouds_low_pct=0 if uv_peak else 20,
        ))

    # The future UV candidate exists, but the unchanged all-day POP trust gate
    # still suppresses this lifestyle winner because of the 09:00 record.
    assert "uv_alert" in _categories(_level1(hours, 10))
    assert _build(hours, 10) is None
