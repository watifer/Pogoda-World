"""Testy charakteryzujące legacy diagnostykę dziennych ekstremów — ETAP 2.

Zamrażają OBECNE zachowanie `weather_payload.build_daily_diagnostics`
(rejestr etapu 1 §3.3, raport końcowy §3):

  * liczy min/max temperatury per dzień i per model (OM / Yr) z pól kanonicznych
    (`temp_c` po fuzji), BEZ dopasowywania wspólnych timestampów;
  * spread = różnica ekstremów modeli; ``spread_max >= 5.0`` LUB
    ``spread_min >= 4.0`` -> ``is_volatile`` (VOLATILITY_SPREAD_C = 5.0);
  * przy braku drugiego modelu spread = 0.0 (nie None) i is_volatile = False;
  * kształt rekordu: dokładny zestaw kluczy i typów;
  * opcjonalny zapis audytu do audit_spread.jsonl (ENABLE_AUDIT_LOG).

To jest ścieżka legacy — działa dalej, zanim dopasowana diagnostyka
(`daily_diagnostics_matched.py`) zacznie być konsumowana (etap 3+).
Zachowanie produkcyjne pozostaje nietknięte.
"""

import json
import math

import pytest

import weather_payload as wp

DAY = "2026-01-15"


def _om(hour, temp, day=DAY):
    return {"time_local": f"{day}T{hour:02d}:00+01:00", "source": "openmeteo", "temp_c": temp}


def _yr(hour, temp, day=DAY):
    return {"time_local": f"{day}T{hour:02d}:00+01:00", "source": "yrno", "temp_c": temp}


DIAG_KEYS = {"max_om", "max_yr", "min_om", "min_yr", "spread_max", "spread_min",
             "spread", "is_volatile", "n_om", "n_yr"}
AUDIT_KEYS = {"date", "fetched_at_local", "location", "max_om", "max_yr", "min_om",
              "min_yr", "n_om", "n_yr", "spread_max", "spread_min"}


class TestExtremes:
    def test_both_models_extremes(self):
        hours = ([_om(0, 2.0), _om(6, 6.0), _om(12, 14.0), _om(18, 9.0)]
                 + [_yr(0, 3.0), _yr(6, 8.0), _yr(12, 19.0), _yr(18, 7.0)])
        d = wp.build_daily_diagnostics(hours, "Test")[DAY]
        assert d["max_om"] == 14.0 and d["max_yr"] == 19.0
        assert d["min_om"] == 2.0 and d["min_yr"] == 3.0
        assert d["spread_max"] == 5.0 and d["spread_min"] == 1.0
        assert d["spread"] == 5.0  # spread = spread_max (większy z dwojga)
        assert d["n_om"] == 4 and d["n_yr"] == 4

    def test_only_openmeteo(self):
        d = wp.build_daily_diagnostics([_om(0, 2.0), _om(12, 14.0)], "Test")[DAY]
        assert d["max_om"] == 14.0 and d["min_om"] == 2.0
        assert d["max_yr"] is None and d["min_yr"] is None
        # Legacy: brak porównania -> spread dokładnie 0.0 (nie None).
        assert d["spread_max"] == 0.0 and d["spread_min"] == 0.0 and d["spread"] == 0.0
        assert d["is_volatile"] is False
        assert d["n_om"] == 2 and d["n_yr"] == 0

    def test_only_yrno(self):
        d = wp.build_daily_diagnostics([_yr(0, 3.0), _yr(12, 19.0)], "Test")[DAY]
        assert d["max_om"] is None and d["min_om"] is None
        assert d["max_yr"] == 19.0 and d["min_yr"] == 3.0
        assert d["spread"] == 0.0 and d["is_volatile"] is False
        assert d["n_om"] == 0 and d["n_yr"] == 2

    def test_spread_from_extremes_without_common_timestamps(self):
        # Charakterystyka legacy: spread liczy się z dziennych ekstremów nawet
        # wtedy, gdy modele nie mają ŻADNEJ wspólnej godziny (rozłączne okna).
        hours = [_om(h, 5.0) for h in range(0, 6)] + [_yr(h, 20.0) for h in range(12, 18)]
        d = wp.build_daily_diagnostics(hours, "Test")[DAY]
        assert d["spread_max"] == 15.0
        assert d["spread_min"] == 15.0
        assert d["is_volatile"] is True

    def test_unknown_source_yields_empty_entry(self):
        # Charakterystyka: dzień z rekordem nieznane źródła powstaje w diag
        # z pustymi ekstremami i licznikami 0.
        hours = [{"time_local": f"{DAY}T12:00+01:00", "source": "airly", "temp_c": 5.0}]
        d = wp.build_daily_diagnostics(hours, "Test")[DAY]
        assert d["n_om"] == 0 and d["n_yr"] == 0
        assert d["max_om"] is None and d["max_yr"] is None

    def test_none_temperature_is_skipped(self):
        # Rekordy z temp_c=None (lub bez pola) są pomijane; dzień bez żadnej
        # temperatury nie dostaje wpisu w diagnostyce.
        hours = [_om(12, None),
                 {"time_local": f"{DAY}T14:00+01:00", "source": "openmeteo"}]
        assert wp.build_daily_diagnostics(hours, "Test") == {}

    def test_non_numeric_temperature_raises(self):
        # Charakterystyka stanu obecnego: temp_c będące nie-parsowalnym stringiem
        # powoduje ValueError (funkcja nie filtruje nietekstowych wartości).
        with pytest.raises(ValueError):
            wp.build_daily_diagnostics([_om(13, "ciepło")], "Test")

    def test_unparseable_or_missing_time_is_skipped(self):
        hours = [{"time_local": "nie-czas", "source": "openmeteo", "temp_c": 5.0},
                 {"source": "openmeteo", "temp_c": 6.0}]
        assert wp.build_daily_diagnostics(hours, "Test") == {}

    def test_multiple_days_each_get_an_entry(self):
        hours = [_om(12, 10.0, day=DAY), _om(12, 11.0, day="2026-01-16")]
        diag = wp.build_daily_diagnostics(hours, "Test")
        assert sorted(diag) == ["2026-01-15", "2026-01-16"]


class TestVolatilityRule:
    def test_spread_max_at_5_0_triggers(self):
        hours = [_om(12, 10.0), _om(0, 8.0), _yr(12, 15.0), _yr(0, 9.0)]
        d = wp.build_daily_diagnostics(hours, "Test")[DAY]
        assert d["spread_max"] == 5.0
        assert d["is_volatile"] is True

    def test_spread_max_just_below_threshold_does_not_trigger(self):
        hours = [_om(12, 10.0), _om(0, 8.0), _yr(12, 14.9), _yr(0, 9.0)]
        d = wp.build_daily_diagnostics(hours, "Test")[DAY]
        assert d["spread_max"] == pytest.approx(4.9)
        assert d["is_volatile"] is False

    def test_spread_min_at_4_0_triggers(self):
        # spread_max = 4.0 < 5.0, ale spread_min = 4.0 -> volatile.
        hours = [_om(12, 10.0), _om(0, 8.0), _yr(12, 14.0), _yr(0, 12.0)]
        d = wp.build_daily_diagnostics(hours, "Test")[DAY]
        assert d["spread_max"] == 4.0 and d["spread_min"] == 4.0
        assert d["is_volatile"] is True

    def test_spread_min_just_below_threshold_does_not_trigger(self):
        hours = [_om(12, 10.0), _om(0, 8.0), _yr(12, 14.0), _yr(0, 11.9)]
        d = wp.build_daily_diagnostics(hours, "Test")[DAY]
        assert d["spread_min"] == pytest.approx(3.9)
        assert d["is_volatile"] is False

    def test_volatility_spread_constant_is_5(self):
        assert wp.VOLATILITY_SPREAD_C == 5.0


class TestShape:
    def test_exact_key_set_and_types(self):
        hours = [_om(0, 2.0), _om(12, 14.0), _yr(0, 3.0), _yr(12, 19.0)]
        d = wp.build_daily_diagnostics(hours, "Test")[DAY]
        assert set(d) == DIAG_KEYS
        for k in ("max_om", "max_yr", "min_om", "min_yr",
                  "spread_max", "spread_min", "spread"):
            assert isinstance(d[k], float)
        assert isinstance(d["is_volatile"], bool)
        assert isinstance(d["n_om"], int) and isinstance(d["n_yr"], int)


class TestAuditLog:
    def test_disabled_flag_writes_nothing(self, monkeypatch, tmp_path):
        audit = tmp_path / "audit_spread.jsonl"
        monkeypatch.setattr(wp, "AUDIT_PATH", str(audit))
        monkeypatch.setattr(wp, "ENABLE_AUDIT_LOG", False)
        wp.build_daily_diagnostics([_om(12, 10.0), _yr(12, 15.0)], "TestLoc",
                                   fetched_at_local="2026-01-15T12:00:00+01:00")
        assert not audit.exists()

    def test_enabled_flag_appends_jsonl_records(self, monkeypatch, tmp_path):
        audit = tmp_path / "audit_spread.jsonl"
        monkeypatch.setattr(wp, "AUDIT_PATH", str(audit))
        monkeypatch.setattr(wp, "ENABLE_AUDIT_LOG", True)
        wp.build_daily_diagnostics([_om(12, 10.0), _yr(12, 15.0)], "TestLoc",
                                   fetched_at_local="2026-01-15T12:00:00+01:00")
        wp.build_daily_diagnostics([_om(12, 1.0, day="2026-01-16")], "TestLoc",
                                   fetched_at_local="2026-01-16T12:00:00+01:00")
        lines = audit.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2  # dopisywanie, nie nadpisywanie
        rec = json.loads(lines[0])
        assert set(rec) == AUDIT_KEYS
        # Wpis audytu NIE zawiera flagi is_volatile (kształt zamrożony).
        assert rec == {
            "date": DAY, "location": "TestLoc",
            "fetched_at_local": "2026-01-15T12:00:00+01:00",
            "max_om": 10.0, "max_yr": 15.0, "min_om": 10.0, "min_yr": 15.0,
            "spread_max": 5.0, "spread_min": 5.0, "n_om": 1, "n_yr": 1,
        }
