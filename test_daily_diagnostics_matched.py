"""Offline regressions for the parallel raw matched diagnostics (ETAP 1).

The new structure is observational. These tests pin down:

* exact full-timestamp matching (including DST and differing local dates);
* None vs 0.0 handling;
* coverage statuses without introducing a production threshold;
* that legacy ``daily_diag`` and every production consumer stay untouched.

No network calls are made; hourly rows are built locally in payload shape.
"""

import copy
import inspect
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

import pytest

import confidence_gate
import daily_source
import prepare_future_layout as future_layout
import prepare_layout
import weather_payload as weather
import worth_knowing
from daily_diagnostics_matched import (
    STATUS_MATCHED,
    STATUS_NO_COMMON_HOURS,
    STATUS_NO_MATCHED_TEMPERATURE,
    STATUS_NO_OM_RECORDS,
    STATUS_NO_YR_RECORDS,
    STATUS_PARTIAL_COVERAGE,
    build_daily_diagnostics_matched,
)


TZ = ZoneInfo("Europe/Warsaw")


# ─────────────────────────────────────────────────────────────
# Helpers — payload-shaped rows, no HTTP
# ─────────────────────────────────────────────────────────────

def _iso(dt):
    return dt.isoformat(timespec="minutes")


def _om_hour(dt, temp_c, **overrides):
    """Open-Meteo row with provenance/raw fields, exactly like the fetcher."""
    hour = {
        "time_local": _iso(dt),
        "temp_c": temp_c,
        "dewpoint_c": 4.0,
        "rh_pct": 55.0,
        "wind_kmh": 21.0,
        "gust_kmh": 33.0,
        "wind_dir_deg": 100.0,
        "clouds_pct": 61.0,
        "clouds_low_pct": 31.0,
        "clouds_mid_pct": 21.0,
        "clouds_high_pct": 11.0,
        "pressure_hpa": 1001.0,
        "uv_index": 3.5,
        "precip_mm": 2.5,
        "precip_prob_pct": 70,
        "weather_code": 63,
        "symbol_code": None,
        "source": "openmeteo",
    }
    hour.update(overrides)
    weather._add_om_raw_and_provenance(hour, hour.get("precip_prob_pct"))
    return hour


def _yr_hour(dt, temp_c, **overrides):
    """Yr.no row (raw, never fused) with its provenance fields."""
    hour = {
        "time_local": _iso(dt),
        "temp_c": temp_c,
        "dewpoint_c": 9.0,
        "rh_pct": 80.0,
        "wind_kmh": 18.0,
        "gust_kmh": 36.0,
        "wind_dir_deg": 250.0,
        "clouds_pct": 90.0,
        "clouds_low_pct": 70.0,
        "clouds_mid_pct": 60.0,
        "clouds_high_pct": 50.0,
        "pressure_hpa": 990.0,
        "uv_index": 8.5,
        "thunder_prob": 44.0,
        "precip_mm": 0.2,
        "weather_code": None,
        "symbol_code": "lightrain_day",
        "source": "yrno",
    }
    hour.update(overrides)
    weather._add_yr_raw_and_provenance(hour)
    return hour


def _configure(monkeypatch, om_hours, yr_hours):
    monkeypatch.setattr(weather, "_fetch_openmeteo", lambda *a, **k: om_hours)
    monkeypatch.setattr(weather, "_fetch_yrno", lambda *a, **k: (yr_hours, "2026-10-30T10:00+01:00"))
    monkeypatch.setattr(weather, "_fetch_airly", lambda *a, **k: None)
    monkeypatch.setattr(weather, "ENABLE_OWM_NOWCAST", False)
    monkeypatch.setattr(weather, "ENABLE_AUDIT_LOG", False)


def _build_payload():
    return weather.build_payload_for_location(
        52.23, 21.01, "Europe/Warsaw", "Offline", days_ahead=1,
    )


def _day(diag, date_str):
    return diag["days"][date_str]


# ─────────────────────────────────────────────────────────────
# 1. Same timestamp, OM 11 °C vs Yr 20 °C
# ─────────────────────────────────────────────────────────────

def test_same_timestamp_reports_nine_degree_spread_and_keeps_legacy_diag(monkeypatch):
    local = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    _configure(monkeypatch, [_om_hour(local, 11.0)], [_yr_hour(local, 20.0)])

    payload = _build_payload()
    date_str = local.date().isoformat()

    matched = payload["daily_diag_matched"]
    day = _day(matched, date_str)
    assert day["n_om_records"] == 1
    assert day["n_yr_records"] == 1  # only the separate raw Yr row
    assert day["n_common_timestamps"] == 1
    assert day["matched_temperature_count"] == 1
    assert day["max_abs_spread_c"] == 9.0
    assert day["mean_abs_spread_c"] == 9.0
    assert day["coverage_status"] == STATUS_MATCHED
    assert matched["totals"]["matched_temperature_count"] == 1
    assert matched["observation_only"] is True

    # Legacy diagnostics are computed from canonical values, exactly as before.
    legacy = payload["daily_diag"][date_str]
    assert set(legacy) == {
        "max_om", "max_yr", "min_om", "min_yr", "spread_max", "spread_min",
        "spread", "is_volatile", "n_om", "n_yr",
    }
    assert legacy["max_om"] == 20.0
    assert legacy["max_yr"] == 20.0
    assert legacy["spread_max"] == 0.0
    assert legacy["spread_min"] == 0.0
    assert legacy["spread"] == 0.0
    assert legacy["is_volatile"] is False


# ─────────────────────────────────────────────────────────────
# 2. Several common hours
# ─────────────────────────────────────────────────────────────

def test_max_and_mean_abs_spread_over_several_common_hours():
    base = datetime(2026, 5, 10, 0, 0, tzinfo=TZ)
    om_temps = {0: 10.0, 1: 12.0, 2: 9.0, 3: 15.0}
    yr_temps = {0: 11.0, 1: 15.0, 2: 8.0, 3: 15.5}

    hours = []
    for hour, temp in om_temps.items():
        hours.append(_om_hour(base + timedelta(hours=hour), temp))
    for hour, temp in yr_temps.items():
        hours.append(_yr_hour(base + timedelta(hours=hour), temp))

    diag = build_daily_diagnostics_matched(hours)
    day = _day(diag, "2026-05-10")

    # |10-11|, |12-15|, |9-8|, |15-15.5| -> max 3.0, mean 5.5/4 = 1.375 -> 1.4
    assert day["matched_temperature_count"] == 4
    assert day["max_abs_spread_c"] == 3.0
    assert day["mean_abs_spread_c"] == 1.4
    assert day["n_common_timestamps"] == 4
    assert day["coverage_status"] == STATUS_MATCHED
    assert day["max_abs_spread_at_local"] == _iso(base + timedelta(hours=1))


# ─────────────────────────────────────────────────────────────
# 3. Different coverage: only the shared window may drive the diff
# ─────────────────────────────────────────────────────────────

def test_partial_coverage_ignores_extremes_outside_common_window():
    base = datetime(2026, 5, 10, 0, 0, tzinfo=TZ)
    hours = []
    for hour in range(24):
        # OM minimum lives outside of the shared window.
        om_temp = -5.0 if hour == 0 else 5.0
        hours.append(_om_hour(base + timedelta(hours=hour), om_temp))
    for hour in range(12, 24):
        hours.append(_yr_hour(base + timedelta(hours=hour), 5.0))

    diag = build_daily_diagnostics_matched(hours)
    day = _day(diag, "2026-05-10")

    assert day["n_om_records"] == 24
    assert day["n_yr_records"] == 12
    assert day["n_om_timestamps"] == 24
    assert day["n_yr_timestamps"] == 12
    assert day["n_common_timestamps"] == 12
    assert day["matched_temperature_count"] == 12
    assert day["coverage_status"] == STATUS_PARTIAL_COVERAGE
    # The -5 °C OM minimum is outside the common range and cannot leak in.
    assert day["max_abs_spread_c"] == 0.0
    assert day["mean_abs_spread_c"] == 0.0


# ─────────────────────────────────────────────────────────────
# 4. No common hours must never look like agreement
# ─────────────────────────────────────────────────────────────

def test_no_common_hours_has_explicit_status_and_no_fake_zero_spread():
    base = datetime(2026, 5, 10, 0, 0, tzinfo=TZ)
    hours = [_om_hour(base + timedelta(hours=h), 10.0) for h in range(0, 6)]
    hours += [_yr_hour(base + timedelta(hours=h), 20.0) for h in range(12, 18)]

    diag = build_daily_diagnostics_matched(hours)
    day = _day(diag, "2026-05-10")

    assert day["coverage_status"] == STATUS_NO_COMMON_HOURS
    assert day["n_common_timestamps"] == 0
    assert day["matched_temperature_count"] == 0
    assert day["max_abs_spread_c"] is None
    assert day["mean_abs_spread_c"] is None
    assert day["max_abs_spread_at_local"] is None
    assert diag["coverage_status_counts"][STATUS_NO_COMMON_HOURS] == 1
    assert diag["coverage_status_counts"][STATUS_MATCHED] == 0


# ─────────────────────────────────────────────────────────────
# 5. None is not zero; a real zero stays a value
# ─────────────────────────────────────────────────────────────

def test_none_and_zero_are_distinguished():
    base = datetime(2026, 1, 10, 0, 0, tzinfo=TZ)
    hours = [
        _om_hour(base, 0.0),
        _yr_hour(base, 0.0),
        _om_hour(base + timedelta(hours=1), None),
        _yr_hour(base + timedelta(hours=1), 0.0),
    ]

    diag = build_daily_diagnostics_matched(hours)
    day = _day(diag, "2026-01-10")

    assert day["n_common_timestamps"] == 2
    assert day["matched_temperature_count"] == 1  # only the real 0.0 pair
    assert day["max_abs_spread_c"] == 0.0
    assert day["mean_abs_spread_c"] == 0.0
    # A missing OM temperature means partial coverage, not agreement.
    assert day["coverage_status"] == STATUS_PARTIAL_COVERAGE


def test_missing_temperature_with_common_timestamp_is_not_a_zero_pair():
    base = datetime(2026, 1, 10, 0, 0, tzinfo=TZ)
    hours = [
        _om_hour(base, None),
        _yr_hour(base, 0.0),
    ]

    diag = build_daily_diagnostics_matched(hours)
    day = _day(diag, "2026-01-10")

    assert day["n_common_timestamps"] == 1
    assert day["matched_temperature_count"] == 0
    assert day["coverage_status"] == STATUS_NO_MATCHED_TEMPERATURE
    assert day["max_abs_spread_c"] is None
    assert day["mean_abs_spread_c"] is None


# ─────────────────────────────────────────────────────────────
# 6. Different local dates are never mixed
# ─────────────────────────────────────────────────────────────

def test_hours_from_different_local_dates_stay_in_their_own_buckets():
    day_one = datetime(2026, 5, 10, 23, 0, tzinfo=TZ)
    day_two = datetime(2026, 5, 11, 0, 0, tzinfo=TZ)
    hours = [
        _om_hour(day_one, 10.0),
        _yr_hour(day_two, 20.0),
    ]

    diag = build_daily_diagnostics_matched(hours)

    first = _day(diag, "2026-05-10")
    second = _day(diag, "2026-05-11")
    assert first["coverage_status"] == STATUS_NO_YR_RECORDS
    assert first["n_common_timestamps"] == 0
    assert first["max_abs_spread_c"] is None
    assert second["coverage_status"] == STATUS_NO_OM_RECORDS
    assert second["n_common_timestamps"] == 0
    assert second["max_abs_spread_c"] is None
    assert diag["totals"]["n_common_timestamps"] == 0


# ─────────────────────────────────────────────────────────────
# 7. DST: same wall clock, two different offsets, two different hours
# ─────────────────────────────────────────────────────────────

NY = ZoneInfo("America/New_York")


def _utc_instants_for_local_day(date_str, tz, n_hours):
    """UTC instants covering one *local* day, which may have 23 or 25 hours."""
    start_local = datetime.fromisoformat(date_str + "T00:00").replace(tzinfo=tz, fold=0)
    start_utc = start_local.astimezone(timezone.utc)
    return [start_utc + timedelta(hours=i) for i in range(n_hours)]


def test_dst_fold_hours_are_distinct_timestamps_and_never_overwrite():
    # America/New_York 2026-11-01: 01:00-04:00 then 01:00-05:00, same wall clock.
    first = datetime(2026, 11, 1, 1, 0, tzinfo=NY, fold=0)
    second = datetime(2026, 11, 1, 1, 0, tzinfo=NY, fold=1)
    assert first.utcoffset() != second.utcoffset()
    assert _iso(first) == "2026-11-01T01:00-04:00"
    assert _iso(second) == "2026-11-01T01:00-05:00"

    hours = [
        _om_hour(first, 10.0),
        _yr_hour(first, 11.0),
        _om_hour(second, 12.0),
        _yr_hour(second, 13.0),
    ]

    diag = build_daily_diagnostics_matched(hours)
    day = _day(diag, "2026-11-01")

    assert day["n_om_timestamps"] == 2
    assert day["n_yr_timestamps"] == 2
    assert day["n_common_timestamps"] == 2
    assert day["matched_temperature_count"] == 2
    assert day["max_abs_spread_c"] == 1.0
    assert day["mean_abs_spread_c"] == 1.0
    # Neither hour overwrites the other.
    assert day["n_conflicting_om_values"] == 0
    assert day["n_conflicting_yr_values"] == 0
    assert diag["timestamp_key_mode"] == "local_timestamp_with_offset"


@pytest.mark.parametrize("date_str, n_hours", [("2026-03-29", 23), ("2026-10-25", 25)])
def test_dst_days_are_not_rejected_for_having_23_or_25_local_records(date_str, n_hours):
    instants = _utc_instants_for_local_day(date_str, TZ, n_hours)
    local_stamps = [i.astimezone(TZ) for i in instants]
    assert len({_iso(s) for s in local_stamps}) == n_hours

    hours = []
    for stamp in local_stamps:
        hours.append(_om_hour(stamp, 5.0))
        hours.append(_yr_hour(stamp, 6.0))

    diag = build_daily_diagnostics_matched(hours)
    day = _day(diag, date_str)

    assert day["n_om_records"] == n_hours
    assert day["n_yr_records"] == n_hours
    assert day["n_common_timestamps"] == n_hours
    assert day["matched_temperature_count"] == n_hours
    assert day["max_abs_spread_c"] == 1.0
    assert day["coverage_status"] == STATUS_MATCHED


# ─────────────────────────────────────────────────────────────
# 8. Production consumers unchanged
# ─────────────────────────────────────────────────────────────

CONSUMERS = (
    daily_source,
    confidence_gate,
    prepare_layout,
    future_layout,
    worth_knowing,
)


@pytest.mark.parametrize("module", CONSUMERS, ids=lambda m: m.__name__)
def test_consumers_do_not_reference_matched_diagnostics(module):
    source = inspect.getsource(module)
    assert "daily_diag_matched" not in source
    assert "build_daily_diagnostics_matched" not in source
    assert "daily_diagnostics_matched" not in source


def _fixed_payload(extra_key=True):
    """Payload fixture with two models over three days, fixed clock."""
    base = datetime(2026, 7, 10, 6, 0, tzinfo=TZ)
    hours = []
    for hour in range(0, 48):
        stamp = base + timedelta(hours=hour)
        hours.append(_om_hour(stamp, 12.0 + (hour % 5)))
        if hour % 2 == 0:
            hours.append(_yr_hour(stamp, 13.0 + (hour % 4)))

    payload = {
        "version": "1.0",
        "location": {"name": "Offline", "lat": 52.23, "lon": 21.01, "tz": "Europe/Warsaw"},
        "generated_at_local": _iso(base),
        "model_updated_at_local": _iso(base),
        "forecast_source": "OpenMeteo + Yr.no",
        "model_agreement": None,
        "airly": None,
        "bias_temp_c": None,
        "hours": hours,
        "alerts": [],
        "daily_diag": weather.build_daily_diagnostics(hours, "Offline", fetched_at_local=None),
        "owm_current": None,
        "lang": "pl",
    }
    if extra_key:
        payload["daily_diag_matched"] = build_daily_diagnostics_matched(hours)
    return payload, base


def test_layout_and_worth_knowing_are_insensitive_to_the_new_key(monkeypatch):
    monkeypatch.setenv("ENABLE_WK_AI_CANDIDATE", "0")
    with_key, base = _fixed_payload(extra_key=True)
    without_key, _ = _fixed_payload(extra_key=False)

    assert with_key["daily_diag"] == without_key["daily_diag"]
    assert "daily_diag_matched" not in without_key

    layout_with = future_layout.prepare_future_layout_data(copy.deepcopy(with_key), now=base)
    layout_without = future_layout.prepare_future_layout_data(copy.deepcopy(without_key), now=base)
    assert layout_with == layout_without

    hours = with_key["hours"]
    wk_with = worth_knowing.build_worth_knowing(
        copy.deepcopy(with_key), blocks=[], ta=hours,
        temp_min=10.0, temp_max=17.0, current_hour=9,
    )
    wk_without = worth_knowing.build_worth_knowing(
        copy.deepcopy(without_key), blocks=[], ta=hours,
        temp_min=10.0, temp_max=17.0, current_hour=9,
    )
    assert wk_with == wk_without


def test_confidence_gate_result_is_identical_for_both_payload_variants():
    with_key, base = _fixed_payload(extra_key=True)
    without_key, _ = _fixed_payload(extra_key=False)

    def trust(payload):
        ho = [h for h in payload["hours"] if h.get("source") == "openmeteo"]
        hy = [h for h in payload["hours"] if h.get("source") == "yrno"]
        return confidence_gate.compute_trust_report(
            ho, hy, base.date().isoformat(), current_hour=9, lang="pl",
        )

    assert trust(with_key) == trust(without_key)


def test_payload_gains_only_the_new_key(monkeypatch):
    local = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    _configure(monkeypatch, [_om_hour(local, 11.0)], [_yr_hour(local, 20.0)])

    payload = _build_payload()

    assert set(payload) == {
        "version", "location", "generated_at_local", "model_updated_at_local",
        "forecast_source", "model_agreement", "airly", "bias_temp_c", "hours",
        "alerts", "daily_diag", "owm_current", "lang", "daily_diag_matched",
    }
    assert payload["model_agreement"] is None
    assert payload["alerts"] == []


def test_old_daily_diag_matches_the_legacy_builder_output():
    base = datetime(2026, 7, 10, 0, 0, tzinfo=TZ)
    hours = [
        _om_hour(base, 10.0),
        _yr_hour(base, 14.0),
        _om_hour(base + timedelta(hours=1), 12.0),
        _yr_hour(base + timedelta(hours=1), 15.0),
    ]
    expected = weather.build_daily_diagnostics(hours, "Offline", fetched_at_local=None)

    day = expected["2026-07-10"]
    assert day == {
        "max_om": 12.0,
        "max_yr": 15.0,
        "min_om": 10.0,
        "min_yr": 14.0,
        "spread_max": 3.0,
        "spread_min": 4.0,
        "spread": 3.0,
        # Legacy rule: spread_min >= 4.0 marks the day volatile. Unchanged.
        "is_volatile": True,
        "n_om": 2,
        "n_yr": 2,
    }


# ─────────────────────────────────────────────────────────────
# 9. Payload without the new raw fields must not raise
# ─────────────────────────────────────────────────────────────

def test_payload_without_raw_fields_does_not_raise_and_invents_no_values():
    base = datetime(2026, 5, 10, 12, 0, tzinfo=TZ)
    legacy_hours = [
        {"time_local": _iso(base), "temp_c": 20.0, "source": "openmeteo"},
        {"time_local": _iso(base), "temp_c": 20.0, "source": "yrno"},
    ]

    diag = build_daily_diagnostics_matched(legacy_hours)
    day = _day(diag, "2026-05-10")

    assert day["n_common_timestamps"] == 1
    assert day["matched_temperature_count"] == 0
    assert day["coverage_status"] == STATUS_NO_MATCHED_TEMPERATURE
    assert day["max_abs_spread_c"] is None
    assert day["mean_abs_spread_c"] is None
    assert day["n_conflicting_om_values"] == 0


def test_empty_and_garbage_inputs_are_safe():
    assert build_daily_diagnostics_matched(None)["days"] == {}
    assert build_daily_diagnostics_matched([])["days"] == {}
    diag = build_daily_diagnostics_matched([
        {},
        {"time_local": None, "source": "openmeteo", "temp_c": 1.0},
        {"time_local": "not-a-time", "source": "openmeteo", "temp_c": 1.0},
        {"time_local": "2026-05-10T10:00", "source": "yrno", "temp_c": "abc"},
        {"time_local": "2026-05-10T11:00+02:00"},
    ])
    assert diag["totals"]["n_hours_without_timestamp"] == 3
    assert diag["totals"]["n_hours_unclassified"] == 1
    day = _day(diag, "2026-05-10")
    assert day["coverage_status"] == STATUS_NO_OM_RECORDS
    assert day["max_abs_spread_c"] is None


# ─────────────────────────────────────────────────────────────
# 10. Older audit payload: raw fields are used, canonical values are not
# ─────────────────────────────────────────────────────────────

def _audited_payload_hours():
    """Shape captured from a previous audit run (post-fusion production rows)."""
    base = datetime(2026, 10, 28, 12, 0, tzinfo=TZ)
    fused = {
        "time_local": _iso(base),
        "temp_c": 20.0,
        "temp_c_source": "yrno",
        "temp_c_om": 11.0,
        "temp_c_yr": 20.0,
        "precip_mm": 0.2,
        "precip_mm_om": 2.5,
        "precip_mm_yr": 0.2,
        "uv_index": 8.5,
        "uv_index_om": 3.5,
        "uv_index_clear_sky_yr": 8.5,
        "uv_index_semantics": "clear_sky",
        "weather_code": 61,
        "weather_code_om": 63,
        "source": "openmeteo",
    }
    raw_yr = {
        "time_local": _iso(base),
        "temp_c": 20.0,
        "temp_c_source": "yrno",
        "uv_index": 8.5,
        "uv_index_clear_sky_yr": 8.5,
        "uv_index_semantics": "clear_sky",
        "precip_mm": 0.2,
        "source": "yrno",
    }
    return base, [fused, raw_yr]


def test_audited_payload_uses_raw_fields_not_canonical_fused_values():
    base, hours = _audited_payload_hours()
    diag = build_daily_diagnostics_matched(hours)
    day = _day(diag, base.date().isoformat())

    assert day["matched_temperature_count"] == 1
    assert day["max_abs_spread_c"] == 9.0  # |11.0 - 20.0|, not |20.0 - 20.0|
    assert day["mean_abs_spread_c"] == 9.0
    assert day["coverage_status"] == STATUS_MATCHED
    # UV is intentionally out of scope for this stage.
    assert "max_abs_uv_spread" not in day
    assert "max_abs_precip_spread" not in day


def test_fused_only_payload_reports_matched_from_raw_pairs():
    """A stored payload may contain only fused OM rows (no separate Yr rows)."""
    base = datetime(2026, 10, 28, 12, 0, tzinfo=TZ)
    fused = {
        "time_local": _iso(base),
        "temp_c": 20.0,
        "temp_c_source": "yrno",
        "temp_c_om": 11.0,
        "temp_c_yr": 20.0,
        "source": "openmeteo",
    }

    diag = build_daily_diagnostics_matched([fused])
    day = _day(diag, base.date().isoformat())

    assert day["n_om_records"] == 1
    assert day["n_yr_records"] == 0  # no separate Yr source rows
    assert day["n_yr_timestamps"] == 1  # but the paired raw Yr value exists
    assert day["matched_temperature_count"] == 1
    assert day["max_abs_spread_c"] == 9.0
    assert day["coverage_status"] == STATUS_MATCHED


def test_fused_row_without_raw_om_field_never_reconstructs_om_from_canonical():
    base = datetime(2026, 10, 28, 12, 0, tzinfo=TZ)
    fused = {
        "time_local": _iso(base),
        "temp_c": 20.0,
        "temp_c_source": "yrno",
        "temp_c_yr": 20.0,
        "source": "openmeteo",
    }
    raw_yr = {"time_local": _iso(base), "temp_c": 20.0, "source": "yrno"}

    diag = build_daily_diagnostics_matched([fused, raw_yr])
    day = _day(diag, base.date().isoformat())

    assert day["n_om_records"] == 1
    assert day["n_common_timestamps"] == 1
    assert day["matched_temperature_count"] == 0
    assert day["max_abs_spread_c"] is None
    assert day["coverage_status"] == STATUS_NO_MATCHED_TEMPERATURE


# ─────────────────────────────────────────────────────────────
# Extra guards: read-only, no audit side effects
# ─────────────────────────────────────────────────────────────

def test_diagnostics_do_not_mutate_input_and_write_no_audit_log(tmp_path, monkeypatch):
    audit_path = tmp_path / "audit_spread.jsonl"
    monkeypatch.setattr(weather, "AUDIT_PATH", audit_path)

    base = datetime(2026, 5, 10, 0, 0, tzinfo=TZ)
    hours = [_om_hour(base, 11.0), _yr_hour(base, 20.0)]
    before = copy.deepcopy(hours)

    build_daily_diagnostics_matched(hours)

    assert hours == before
    assert not audit_path.exists()
