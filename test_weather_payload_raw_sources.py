"""Offline regression tests for source-level weather data preservation."""
import copy
import json
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

import weather_payload as weather
from prepare_future_layout import prepare_future_layout_data


TZ = ZoneInfo("Europe/Warsaw")


def _iso(dt):
    return dt.isoformat(timespec="minutes")


def _yr_time(local):
    return _iso(local.astimezone(timezone.utc)).replace("+00:00", "Z")


def _om_response(local):
    return {
        "hourly": {
            "time": [_iso(local.replace(tzinfo=None))],
            "temperature_2m": [11.0],
            "dewpoint_2m": [4.0],
            "relative_humidity_2m": [55.0],
            "wind_speed_10m": [21.0],
            "wind_gusts_10m": [33.0],
            "wind_direction_10m": [100.0],
            "precipitation": [2.5],
            "precipitation_probability": [70],
            "cloud_cover": [61.0],
            "cloud_cover_low": [31.0],
            "cloud_cover_mid": [21.0],
            "cloud_cover_high": [11.0],
            "weather_code": [63],
            "pressure_msl": [1001.0],
            "uv_index": [3.5],
        }
    }


def _yr_response(local):
    yr_time = _yr_time(local)
    return {
        "properties": {
            "meta": {"updated_at": yr_time},
            "timeseries": [{
                "time": yr_time,
                "data": {
                    "instant": {"details": {
                        "air_temperature": 20.0,
                        "dew_point_temperature": 9.0,
                        "relative_humidity": 80.0,
                        "wind_speed": 5.0,
                        "wind_speed_of_gust": 10.0,
                        "wind_from_direction": 250.0,
                        "cloud_area_fraction": 90.0,
                        "cloud_area_fraction_low": 70.0,
                        "cloud_area_fraction_medium": 60.0,
                        "cloud_area_fraction_high": 50.0,
                        "air_pressure_at_sea_level": 990.0,
                        "ultraviolet_index_clear_sky": 8.5,
                    }},
                    "next_1_hours": {
                        "details": {
                            "precipitation_amount": 0.2,
                            "probability_of_thunder": 44.0,
                        },
                        "summary": {"symbol_code": "lightrain_day"},
                    },
                },
            }],
        }
    }


def _empty_om_response():
    return {"hourly": {"time": []}}


def _empty_yr_response(local):
    return {
        "properties": {
            "meta": {"updated_at": _yr_time(local)},
            "timeseries": [],
        }
    }


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _FakeWeatherSession:
    def __init__(self, openmeteo_payload, yrno_payload):
        self.openmeteo_payload = openmeteo_payload
        self.yrno_payload = yrno_payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "api.open-meteo.com" in url:
            return _FakeResponse(self.openmeteo_payload)
        if "api.met.no" in url:
            return _FakeResponse(self.yrno_payload)
        raise AssertionError("Unexpected URL in offline test: " + url)


def _configure(monkeypatch, session):
    monkeypatch.setattr(weather, "_get_retry_session", lambda: session)
    monkeypatch.setattr(weather, "_fetch_airly", lambda *args, **kwargs: None)
    monkeypatch.setattr(weather, "ENABLE_OWM_NOWCAST", False)
    monkeypatch.setattr(weather, "ENABLE_AUDIT_LOG", False)
    monkeypatch.setattr(weather, "_OPENMETEO_CACHE", {})
    monkeypatch.setattr(weather, "_OPENMETEO_DOWN_UNTIL", 0.0)


def _build():
    return weather.build_payload_for_location(
        52.23, 21.01, "Europe/Warsaw", "Offline", days_ahead=1,
    )


def _one(payload, source):
    return next(h for h in payload["hours"] if h["source"] == source)


def _pair(payload, local):
    rows = [h for h in payload["hours"] if h["time_local"] == _iso(local)]
    return _one({"hours": rows}, "openmeteo"), _one({"hours": rows}, "yrno")


def test_fusion_preserves_raw_values_canonical_behavior_and_provenance(monkeypatch):
    local = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    session = _FakeWeatherSession(_om_response(local), _yr_response(local))
    _configure(monkeypatch, session)

    payload = _build()
    om, yr = _pair(payload, local)

    # Existing canonical/presentation values remain exactly as before fusion.
    assert om["temp_c"] == 20.0
    assert om["precip_mm"] == 0.2
    assert om["uv_index"] == 8.5
    assert om["wind_kmh"] == 21.0
    assert om["gust_kmh"] == 33.0
    assert om["dewpoint_c"] == 4.0
    assert om["clouds_pct"] == 61.0
    assert om["weather_code"] == 61
    assert om["symbol_code"] == "lightrain_day"
    assert om["thunder_prob"] == 44.0

    # Open-Meteo was captured before cache/fusion, including its original WMO code.
    assert {
        "temp_c_om": om["temp_c_om"],
        "precip_mm_om": om["precip_mm_om"],
        "precip_prob_pct_om": om["precip_prob_pct_om"],
        "uv_index_om": om["uv_index_om"],
        "wind_kmh_om": om["wind_kmh_om"],
        "gust_kmh_om": om["gust_kmh_om"],
        "wind_dir_deg_om": om["wind_dir_deg_om"],
        "dewpoint_c_om": om["dewpoint_c_om"],
        "rh_pct_om": om["rh_pct_om"],
        "pressure_hpa_om": om["pressure_hpa_om"],
        "clouds_pct_om": om["clouds_pct_om"],
        "clouds_low_pct_om": om["clouds_low_pct_om"],
        "clouds_mid_pct_om": om["clouds_mid_pct_om"],
        "clouds_high_pct_om": om["clouds_high_pct_om"],
        "weather_code_om": om["weather_code_om"],
    } == {
        "temp_c_om": 11.0,
        "precip_mm_om": 2.5,
        "precip_prob_pct_om": 70,
        "uv_index_om": 3.5,
        "wind_kmh_om": 21.0,
        "gust_kmh_om": 33.0,
        "wind_dir_deg_om": 100.0,
        "dewpoint_c_om": 4.0,
        "rh_pct_om": 55.0,
        "pressure_hpa_om": 1001.0,
        "clouds_pct_om": 61.0,
        "clouds_low_pct_om": 31.0,
        "clouds_mid_pct_om": 21.0,
        "clouds_high_pct_om": 11.0,
        "weather_code_om": 63,
    }

    # The paired Yr values are available on the fused OM row as raw fields.
    assert om["temp_c_yr"] == 20.0
    assert om["precip_mm_yr"] == 0.2
    assert om["uv_index_clear_sky_yr"] == 8.5
    assert om["wind_kmh_yr"] == 18.0
    assert om["gust_kmh_yr"] == 36.0
    assert om["wind_dir_deg_yr"] == 250.0
    assert om["dewpoint_c_yr"] == 9.0
    assert om["rh_pct_yr"] == 80.0
    assert om["pressure_hpa_yr"] == 990.0
    assert om["thunder_prob_yr"] == 44.0
    assert om["symbol_code_yr"] == "lightrain_day"
    assert om["clouds_pct_yr"] == 90.0
    assert om["clouds_low_pct_yr"] == 70.0
    assert om["clouds_mid_pct_yr"] == 60.0
    assert om["clouds_high_pct_yr"] == 50.0

    # Provenance is explicit even though the fused row retains source=openmeteo.
    assert om["source"] == "openmeteo"
    for field in ("temp_c", "precip_mm", "uv_index", "thunder_prob", "symbol_code"):
        assert om[field + "_source"] == "yrno"
    assert om["weather_code_source"] == "yrno_symbol_code"
    for field in (
        "dewpoint_c", "rh_pct", "wind_kmh", "gust_kmh", "wind_dir_deg",
        "clouds_pct", "clouds_low_pct", "clouds_mid_pct", "clouds_high_pct",
        "pressure_hpa", "precip_prob_pct",
    ):
        assert om[field + "_source"] == "openmeteo"

    assert om["uv_index_semantics"] == "clear_sky"
    assert "uv_index_yr" not in om
    assert "precip_prob_pct_yr" not in om

    # The separate Yr row is still source-raw; only clear-sky UV needs an alias.
    assert yr["temp_c"] == 20.0
    assert yr["precip_mm"] == 0.2
    assert yr["uv_index"] == 8.5
    assert yr["uv_index_clear_sky_yr"] == 8.5
    assert yr["uv_index_semantics"] == "clear_sky"
    assert "uv_index_yr" not in yr
    for field in (
        "temp_c", "dewpoint_c", "rh_pct", "wind_kmh", "gust_kmh",
        "wind_dir_deg", "clouds_pct", "clouds_low_pct", "clouds_mid_pct",
        "clouds_high_pct", "pressure_hpa", "uv_index", "thunder_prob",
        "precip_mm", "weather_code", "symbol_code",
    ):
        assert yr[field + "_source"] == "yrno"

    # The builder adds no effective fields and does not switch diagnostics to raw data.
    for row in (om, yr):
        for field in ("precip_eff_mm", "precip_class_eff", "weather_code_eff", "symbol_code_eff"):
            assert field not in row
    diag = payload["daily_diag"][local.date().isoformat()]
    assert diag["spread_max"] == 0.0
    assert diag["spread_min"] == 0.0


def test_missing_values_and_real_zero_remain_distinct(monkeypatch):
    local = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    om_response = _om_response(local)
    hourly = om_response["hourly"]
    hourly["temperature_2m"] = [0.0]
    hourly["precipitation"] = [0.0]
    hourly["uv_index"] = [0.0]
    hourly["dewpoint_2m"] = [None]
    hourly["precipitation_probability"] = [None]

    yr_response = _yr_response(local)
    item = yr_response["properties"]["timeseries"][0]
    details = item["data"]["instant"]["details"]
    details["air_temperature"] = None
    details["wind_speed"] = 0.0
    details["ultraviolet_index_clear_sky"] = None
    for field in (
        "cloud_area_fraction", "cloud_area_fraction_low",
        "cloud_area_fraction_medium", "cloud_area_fraction_high",
    ):
        details[field] = None
    item["data"]["next_1_hours"]["details"]["precipitation_amount"] = None
    item["data"]["next_1_hours"]["details"]["probability_of_thunder"] = None
    item["data"]["next_1_hours"]["summary"]["symbol_code"] = None

    _configure(monkeypatch, _FakeWeatherSession(om_response, yr_response))
    om, yr = _pair(_build(), local)

    assert om["temp_c_om"] == 0.0
    assert om["precip_mm_om"] == 0.0
    assert om["uv_index_om"] == 0.0
    assert om["dewpoint_c_om"] is None
    assert om["precip_prob_pct_om"] is None
    assert om["precip_prob_pct"] == 0  # Legacy canonical fallback is unchanged.

    assert om["temp_c_yr"] is None
    assert om["precip_mm_yr"] is None
    assert om["uv_index_clear_sky_yr"] is None
    assert om["thunder_prob_yr"] is None
    assert om["wind_kmh_yr"] == 0.0
    assert om["temp_c"] == 0.0
    assert om["precip_mm"] == 0.0
    assert om["uv_index"] == 0.0
    assert om["temp_c_source"] == "openmeteo"
    assert om["precip_mm_source"] == "openmeteo"
    assert om["uv_index_source"] == "openmeteo"
    assert om["uv_index_semantics"] == "standard"

    # Existing key-presence behavior is retained for coastal and other consumers.
    for field in ("clouds_pct_yr", "clouds_low_pct_yr", "clouds_mid_pct_yr", "clouds_high_pct_yr"):
        assert field not in om
        assert field not in yr

    assert "uv_index_yr" not in om
    assert "thunder_prob" not in om
    assert "thunder_prob_source" not in om
    assert json.loads(json.dumps(om))["precip_prob_pct_om"] is None


def test_openmeteo_only_fallback_has_no_fabricated_yr_fields(monkeypatch):
    local = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    session = _FakeWeatherSession(_om_response(local), _empty_yr_response(local))
    _configure(monkeypatch, session)

    payload = _build()
    om = _one(payload, "openmeteo")

    assert payload["forecast_source"] == "OpenMeteo"
    assert len(payload["hours"]) == 1
    assert om["temp_c"] == 11.0
    assert om["precip_mm"] == 2.5
    assert om["uv_index"] == 3.5
    assert om["temp_c_om"] == 11.0
    assert om["precip_mm_om"] == 2.5
    assert om["uv_index_om"] == 3.5
    assert om["temp_c_source"] == "openmeteo"
    assert om["precip_mm_source"] == "openmeteo"
    assert om["uv_index_source"] == "openmeteo"
    assert om["uv_index_semantics"] == "standard"
    for field in (
        "temp_c_yr", "precip_mm_yr", "uv_index_clear_sky_yr", "wind_kmh_yr",
        "clouds_pct_yr", "clouds_low_pct_yr", "clouds_mid_pct_yr", "clouds_high_pct_yr",
        "uv_index_yr",
    ):
        assert field not in om


def test_yr_only_fallback_preserves_values_and_clear_sky_semantics(monkeypatch):
    local = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    session = _FakeWeatherSession(_empty_om_response(), _yr_response(local))
    _configure(monkeypatch, session)

    payload = _build()
    yr = _one(payload, "yrno")

    assert payload["forecast_source"] == "yr.no"
    assert len(payload["hours"]) == 1
    assert yr["temp_c"] == 20.0
    assert yr["precip_mm"] == 0.2
    assert yr["uv_index"] == 8.5
    assert yr["uv_index_clear_sky_yr"] == 8.5
    assert yr["uv_index_source"] == "yrno"
    assert yr["uv_index_semantics"] == "clear_sky"
    assert yr["temp_c_source"] == "yrno"
    assert yr["precip_mm_source"] == "yrno"
    assert "temp_c_om" not in yr
    assert "precip_mm_om" not in yr
    assert "uv_index_om" not in yr
    assert "uv_index_yr" not in yr
    assert "precip_prob_pct" not in yr
    assert "precip_prob_pct_source" not in yr


def test_both_sources_without_exact_time_match_do_not_fuse_or_invent_raw_pair(monkeypatch):
    local = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    session = _FakeWeatherSession(_om_response(local), _yr_response(local + timedelta(hours=1)))
    _configure(monkeypatch, session)

    payload = _build()
    om = _one(payload, "openmeteo")
    yr = _one(payload, "yrno")

    assert payload["forecast_source"] == "OpenMeteo + Yr.no"
    assert len(payload["hours"]) == 2
    assert om["time_local"] != yr["time_local"]
    assert om["temp_c"] == 11.0
    assert om["precip_mm"] == 2.5
    assert om["uv_index"] == 3.5
    assert om["temp_c_om"] == 11.0
    assert om["temp_c_source"] == "openmeteo"
    assert om["uv_index_semantics"] == "standard"
    assert "temp_c_yr" not in om
    assert "precip_mm_yr" not in om
    assert "uv_index_clear_sky_yr" not in om
    assert "clouds_pct_yr" not in om


def test_cached_openmeteo_raw_values_survive_repeated_fusion(monkeypatch):
    local = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    session = _FakeWeatherSession(_om_response(local), _yr_response(local))
    _configure(monkeypatch, session)

    first = _build()
    second = _build()
    first_om, _ = _pair(first, local)
    second_om, _ = _pair(second, local)

    assert len(session.calls) == 3  # OM is cached; Yr is fetched for each build.
    assert sum("api.open-meteo.com" in url for url, _ in session.calls) == 1
    for row in (first_om, second_om):
        assert row["temp_c_om"] == 11.0
        assert row["precip_mm_om"] == 2.5
        assert row["uv_index_om"] == 3.5
        assert row["weather_code_om"] == 63
        assert row["temp_c"] == 20.0
        assert row["precip_mm"] == 0.2
        assert row["uv_index"] == 8.5
        assert row["temp_c_source"] == "yrno"
        assert row["uv_index_semantics"] == "clear_sky"


def test_legacy_or_post_fusion_rows_are_not_backfilled_from_canonical_values(monkeypatch):
    local = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    legacy = {
        "time_local": _iso(local),
        "temp_c": 20.0,
        "precip_mm": 0.2,
        "uv_index": 8.5,
        "wind_kmh": 10.0,
        "gust_kmh": 12.0,
        "source": "openmeteo",
    }
    session = _FakeWeatherSession(_empty_om_response(), _yr_response(local))
    _configure(monkeypatch, session)
    monkeypatch.setattr(weather, "_fetch_openmeteo", lambda *args: [legacy])

    om, _ = _pair(_build(), local)

    # These canonical values look exactly like a completed Yr fusion. They are
    # not evidence of the original OM values and must not become raw OM fields.
    assert om["temp_c"] == 20.0
    assert om["precip_mm"] == 0.2
    assert om["uv_index"] == 8.5
    assert "temp_c_om" not in om
    assert "precip_mm_om" not in om
    assert "uv_index_om" not in om
    assert "weather_code_om" not in om
    assert "wind_kmh_source" not in om

    # Only values actually supplied by the current Yr response are preserved.
    assert om["temp_c_yr"] == 20.0
    assert om["precip_mm_yr"] == 0.2
    assert om["uv_index_clear_sky_yr"] == 8.5
    assert om["temp_c_source"] == "yrno"
    assert om["uv_index_semantics"] == "clear_sky"


def test_future_layout_does_not_mutate_original_or_raw_records(monkeypatch):
    local = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    session = _FakeWeatherSession(_om_response(local), _yr_response(local))
    _configure(monkeypatch, session)
    payload = _build()
    before = copy.deepcopy(payload)

    prepare_future_layout_data(payload, now=local)

    assert payload == before
    yr = _one(payload, "yrno")
    assert "precip_prob_pct" not in yr
    assert "precip_prob_pct_source" not in yr
    om = _one(payload, "openmeteo")
    assert om["temp_c_om"] == 11.0
    assert om["temp_c_yr"] == 20.0
    assert om["precip_prob_pct_om"] == 70
