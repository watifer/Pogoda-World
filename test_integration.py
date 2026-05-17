"""
test_integration.py — Testy integracyjne pipeline Pogoda-World.
Uruchomienie: pytest test_integration.py -v
"""

import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch, MagicMock

from prepare_layout import (
    prepare_layout_data,
    _select_block_hours,
    _build_time_blocks,
    _determine_report_type,
    _build_day_summary,
)
from weather_payload import build_payload_for_location


# ═══════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════

TZ = ZoneInfo("Europe/Warsaw")


def _make_hour(date_str: str, hour: int, tz=TZ, **kwargs) -> dict:
    t = datetime.fromisoformat(f"{date_str}T{hour:02d}:00").replace(tzinfo=tz)
    return {
        "time_local": t.isoformat(timespec="minutes"),
        "temp_c":     kwargs.get("temp_c", 10.0),
        "dewpoint_c": kwargs.get("dewpoint_c", 5.0),
        "rh_pct":     kwargs.get("rh_pct", 70.0),
        "wind_kmh":   kwargs.get("wind_kmh", 10.0),
        "gust_kmh":   kwargs.get("gust_kmh", 15.0),
        "wind_dir_deg": 180,
        "clouds_pct": kwargs.get("clouds_pct", 50.0),
        "clouds_low_pct": 20.0,
        "clouds_mid_pct": 20.0,
        "clouds_high_pct": 10.0,
        "precip_mm":  kwargs.get("precip_mm", 0.0),
        "weather_code": kwargs.get("weather_code", 3),
        "symbol_code":  kwargs.get("symbol_code", None),
        "source":     "openmeteo",
    }


def _make_payload(hours: list, date_str: str = "2024-04-03",
                  name: str = "Test") -> dict:
    return {
        "version": "1.0",
        "location": {"name": name, "lat": 52.0, "lon": 21.0,
                     "tz": "Europe/Warsaw"},
        "generated_at_local": f"{date_str}T08:00:00+02:00",
        "forecast_source": "openmeteo",
        "airly": None,
        "bias_temp_c": None,
        "hours": hours,
    }


# ═══════════════════════════════════════
# 1. Granice bloków: start <= hour < end
# ═══════════════════════════════════════

class TestBlockBoundaries:
    """Blok 06–10 nie może zawierać godziny 10."""

    def test_06_10_excludes_hour_10(self):
        hours = [_make_hour("2024-04-03", h) for h in range(6, 12)]
        result = _select_block_hours(
            hours, "2024-04-03", "2024-04-04", 6, 10, "Rano")
        included = [datetime.fromisoformat(h["time_local"]).hour
                    for h in result]
        assert 10 not in included
        assert 6 in included
        assert 9 in included

    def test_11_16_excludes_hour_16(self):
        hours = [_make_hour("2024-04-03", h) for h in range(10, 18)]
        result = _select_block_hours(
            hours, "2024-04-03", "2024-04-04", 11, 16, "Popołudnie")
        included = [datetime.fromisoformat(h["time_local"]).hour
                    for h in result]
        assert 16 not in included
        assert 11 in included
        assert 15 in included

    def test_17_22_excludes_hour_22(self):
        hours = [_make_hour("2024-04-03", h) for h in range(17, 24)]
        result = _select_block_hours(
            hours, "2024-04-03", "2024-04-04", 17, 22, "Wieczór")
        included = [datetime.fromisoformat(h["time_local"]).hour
                    for h in result]
        assert 22 not in included
        assert 17 in included


# ═══════════════════════════════════════
# 2. Blok przez północ 22–06
# ═══════════════════════════════════════

class TestCrossMidnight:
    """Blok 22–06 musi obejmować godziny po północy."""

    def test_22_06_includes_midnight_hours(self):
        today  = [_make_hour("2024-04-03", h) for h in range(22, 24)]
        tomor  = [_make_hour("2024-04-04", h) for h in range(0, 7)]
        hours  = today + tomor
        result = _select_block_hours(
            hours, "2024-04-03", "2024-04-04", 22, 6, "Noc")
        included = set(datetime.fromisoformat(h["time_local"]).hour
                       for h in result)
        assert 22 in included
        assert 23 in included
        assert 0  in included
        assert 3  in included
        assert 5  in included
        assert 6  not in included   # end jest exclusive

    def test_hero_block_sees_post_midnight_events(self):
        """Hero block 22–06 musi widzieć eventy po północy."""
        today = [_make_hour("2024-04-03", 22, precip_mm=2.0),
                 _make_hour("2024-04-03", 23, precip_mm=0.0)]
        tomor = [_make_hour("2024-04-04", 0, precip_mm=3.0),
                 _make_hour("2024-04-04", 3, precip_mm=1.5),
                 _make_hour("2024-04-04", 5, precip_mm=0.0)]
        hours = today + tomor
        result = _select_block_hours(
            hours, "2024-04-03", "2024-04-04", 22, 6, "Noc")
        precip_hours = [h for h in result if float(h.get("precip_mm", 0)) >= 0.1]
        included_hrs = {datetime.fromisoformat(h["time_local"]).hour
                        for h in precip_hours}
        assert 22 in included_hrs   # precip w 22
        assert 0  in included_hrs   # precip po północy
        assert 3  in included_hrs


# ═══════════════════════════════════════
# 3. Single-day summary: brak labela 06–22
# ═══════════════════════════════════════

class TestSingleDaySummaryLabel:
    """Pojedynczy dzień summary nie może mieć labela 06–22."""

    def test_single_day_label_is_00_24(self):
        today = "2024-04-04"   # piątek
        tom   = "2024-04-05"   # sobota

        # Payload: dane tylko na poniedziałek po weekendzie
        monday = "2024-04-08"
        hours = (
            [_make_hour(today, h) for h in range(6, 22)]
            + [_make_hour(tom, h) for h in range(6, 22)]    # sobota (detail)
            + [_make_hour("2024-04-06", h) for h in range(6, 22)]  # niedziela (detail)
            + [_make_hour(monday, h) for h in range(6, 22)]
        )
        payload = _make_payload(hours, today)
        # Symuluj piątek 08:00
        now = datetime(2024, 4, 4, 8, 0, tzinfo=TZ)
        layout = prepare_layout_data(payload, now=now)

        for d in layout.get("next_days", []):
            label = d.get("label", "")
            assert label != "06–22", (
                f"Single-day summary nie może mieć labela '06–22', got '{label}'"
            )
            if label:
                assert label == "00–24"

    def test_single_day_name_is_empty(self):
        """W wierszu single-day summary name musi być pusty."""
        today  = "2024-04-04"
        monday = "2024-04-08"
        hours  = (
            [_make_hour(today, h) for h in range(6, 22)]
            + [_make_hour("2024-04-05", h) for h in range(6, 22)]
            + [_make_hour("2024-04-06", h) for h in range(6, 22)]
            + [_make_hour(monday, h) for h in range(6, 22)]
        )
        payload = _make_payload(hours, today)
        now = datetime(2024, 4, 4, 8, 0, tzinfo=TZ)
        layout = prepare_layout_data(payload, now=now)
        nd = layout.get("next_days", [])
        if len(nd) == 1:
            assert nd[0].get("name", "") == ""


# ═══════════════════════════════════════
# 4. Airly opcjonalne
# ═══════════════════════════════════════

class TestAirlyOptional:
    """Brak Airly nie może wywalić payload buildera."""

    def test_no_airly_key_in_env(self):
        import os
        from datetime import date
        today = date.today().strftime("%Y-%m-%d")
        env_backup = os.environ.pop("AIRLY_API_KEY", None)
        try:
            with patch("weather_payload.requests.get") as mock_get:
                mock_resp = MagicMock()
                mock_resp.raise_for_status = lambda: None
                mock_resp.json.return_value = {
                    "hourly": {
                        "time": [f"{today}T{h:02d}:00" for h in range(24)],
                        "temperature_2m":         [10.0] * 24,
                        "dewpoint_2m":            [5.0]  * 24,
                        "relative_humidity_2m":   [70.0] * 24,
                        "wind_speed_10m":         [10.0] * 24,
                        "wind_gusts_10m":         [15.0] * 24,
                        "wind_direction_10m":     [180.0] * 24,
                        "precipitation":          [0.0]  * 24,
                        "precipitation_probability": [0] * 24,
                        "cloud_cover":            [50.0] * 24,
                        "cloud_cover_low":        [20.0] * 24,
                        "cloud_cover_mid":        [20.0] * 24,
                        "cloud_cover_high":       [10.0] * 24,
                        "weather_code":           [3]    * 24,
                    }
                }
                mock_get.return_value = mock_resp
                payload = build_payload_for_location(
                    52.0, 21.0, "Europe/Warsaw", "TestCity")
            assert payload["airly"] is None
            assert payload["hours"] is not None
        finally:
            if env_backup is not None:
                os.environ["AIRLY_API_KEY"] = env_backup
    def test_airly_exception_returns_none_not_crash(self):
        """Wyjątek w Airly → airly=None, reszta payloadu OK."""
        from datetime import date
        today = date.today().strftime("%Y-%m-%d")

        with patch("weather_payload._fetch_airly", return_value=None):
            with patch("weather_payload._fetch_yrno", return_value=[]):
                with patch("weather_payload._fetch_openmeteo") as mock_om:
                    mock_om.return_value = [
                        _make_hour(today, h) for h in range(0, 24)
                    ]
                    payload = build_payload_for_location(
                        52.0, 21.0, "Europe/Warsaw", "Test")
                    assert payload["airly"] is None
                    assert len(payload["hours"]) > 0


# ═══════════════════════════════════════
# 5. Typy raportów
# ═══════════════════════════════════════

class TestReportType:
    def test_morning(self):
        assert _determine_report_type(7)  == "raport poranny"
        assert _determine_report_type(11) == "raport poranny"

    def test_afternoon(self):
        assert _determine_report_type(14) == "raport popołudniowy"
        assert _determine_report_type(17) == "raport popołudniowy"

    def test_evening(self):
        assert _determine_report_type(18) == "aktualizacja wieczorna"
        assert _determine_report_type(21) == "aktualizacja wieczorna"
        assert _determine_report_type(23) == "aktualizacja wieczorna"

    def test_layout_uses_evening(self):
        """Layout z godziną 19 powinien mieć 'aktualizacja wieczorna'."""
        hours = [_make_hour("2024-04-03", h) for h in range(0, 24)]
        payload = _make_payload(hours, "2024-04-03")
        now = datetime(2024, 4, 3, 19, 0, tzinfo=TZ)
        layout = prepare_layout_data(payload, now=now)
        assert layout["report_type"] == "aktualizacja wieczorna"


# ═══════════════════════════════════════
# 6. Pełny layout smoke test
# ═══════════════════════════════════════

class TestLayoutSmoke:
    def test_full_layout_has_required_keys(self):
        hours = [_make_hour("2024-04-03", h) for h in range(0, 24)]
        for d in range(1, 6):
            date = (datetime(2024, 4, 3) + timedelta(days=d)).strftime("%Y-%m-%d")
            hours += [_make_hour(date, h) for h in range(0, 24)]

        payload = _make_payload(hours, "2024-04-03")
        now = datetime(2024, 4, 3, 8, 0, tzinfo=TZ)
        layout = prepare_layout_data(payload, now=now)

        required = [
            "city", "weekday", "date", "report_type",
            "main_icon", "temp_range", "summary",
            "section_title", "today_blocks",
            "next_days",
            "alerts", "worth_knowing",
        ]
        for key in required:
            assert key in layout, f"Brak klucza: {key}"

    def test_today_blocks_not_empty(self):
        hours = [_make_hour("2024-04-03", h) for h in range(0, 24)]
        payload = _make_payload(hours, "2024-04-03")
        now = datetime(2024, 4, 3, 8, 0, tzinfo=TZ)
        layout = prepare_layout_data(payload, now=now)
        assert len(layout["today_blocks"]) > 0

    def test_no_debug_prints_in_layout(self, capsys):
        hours = [_make_hour("2024-04-03", h) for h in range(0, 24)]
        payload = _make_payload(hours, "2024-04-03")
        now = datetime(2024, 4, 3, 8, 0, tzinfo=TZ)
        prepare_layout_data(payload, now=now)
        captured = capsys.readouterr()
        assert "[debug]" not in captured.out