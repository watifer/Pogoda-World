"""Testy charakteryzujące wybór źródła danych dla karty „Następne dni" — ETAP 2.

Zamrażają OBECNE zachowanie `daily_source.pick_hours_for_daily_summary`
(rejestr etapu 1 §3.2, dokument decyzji §2):

  * priorytet Yr.no włącza się przy ``n_yr >= MIN_YR_SAMPLES_FOR_DAILY_SUMMARY``
    (18; audyt: „Nie zmieniać progu 18") ORAZ ``len(hy_day) >= 6``;
  * próg opiera się o licznik z legacy diagnostyki ``daily_diag[date]["n_yr"]``
    oraz o faktyczną liczbę rekordów Yr tego dnia;
  * fallback OpenMeteo (marker " *"), fallback „mixed" (marker " *");
  * marker dopina się do ``day_entry["name"]`` w prepare_future_layout i nie
    trafia do pola ``label`` czytanego przez renderer (szczegóły: test_dead_paths.py);
  * przypadek Hel: ~15 rekordów Yr na dzień +2 -> zawsze OpenMeteo.

Zachowanie produkcyjne pozostaje nietknięte — w szczególności próg 18.
"""

import pytest

from daily_source import (
    MIN_YR_SAMPLES_FOR_DAILY_SUMMARY,
    DailyPick,
    pick_hours_for_daily_summary,
)

DAY = "2026-01-16"


def _h(hour, source, day=DAY, **over):
    h = {
        "time_local": f"{day}T{hour:02d}:00+01:00",
        "source": source,
        "temp_c": 5.0,
        "precip_mm": 0.0,
        "clouds_pct": 20.0,
        "wind_kmh": 10.0,
        "symbol_code": "clearsky_day" if source == "yrno" else None,
        "weather_code": 1 if source == "openmeteo" else None,
    }
    h.update(over)
    return h


def _om_day(day=DAY, n=24):
    return [_h(i, "openmeteo", day) for i in range(n)]


def _yr_day(day=DAY, n=24):
    return [_h(i, "yrno", day) for i in range(n)]


class TestThresholdConstants:
    def test_min_yr_samples_is_18(self):
        # Zamrożony próg pełnego dnia Yr (przy 24 rekordach pokrycie godzinowe
        # Yr kończy się ok. 09:00 dnia D+9).
        assert MIN_YR_SAMPLES_FOR_DAILY_SUMMARY == 18


class TestReturnShape:
    def test_daily_pick_fields(self):
        pick = pick_hours_for_daily_summary(_om_day() + _yr_day(),
                                            {DAY: {"n_yr": 18}}, DAY)
        assert isinstance(pick, DailyPick)
        assert pick.source == "yrno"
        assert pick.marker == ""
        assert len(pick.hp) == 24
        assert all(h["source"] == "yrno" for h in pick.hp)

    def test_days_other_than_target_are_excluded(self):
        hours = _om_day() + _yr_day() + _om_day("2026-01-17") + _yr_day("2026-01-17")
        pick = pick_hours_for_daily_summary(hours, {DAY: {"n_yr": 18}}, DAY)
        assert all(h["time_local"].startswith(DAY) for h in pick.hp)


class TestYrPriorityBoundaries:
    @pytest.mark.parametrize("n_yr,expected_source", [(17, "openmeteo"), (18, "yrno")])
    def test_n_yr_boundary_17_18(self, n_yr, expected_source):
        hours = _om_day() + _yr_day()
        pick = pick_hours_for_daily_summary(hours, {DAY: {"n_yr": n_yr}}, DAY)
        assert pick.source == expected_source

    @pytest.mark.parametrize("n_records,expected_source", [(5, "openmeteo"), (6, "yrno")])
    def test_yr_record_count_boundary_5_6(self, n_records, expected_source):
        # licznik diag mówi „pełno", ale faktycznych rekordów Yr jest 5/6.
        hours = _om_day() + _yr_day(n=n_records)
        pick = pick_hours_for_daily_summary(hours, {DAY: {"n_yr": 18}}, DAY)
        assert pick.source == expected_source
        if expected_source == "yrno":
            assert len(pick.hp) == n_records

    def test_both_conditions_required(self):
        # n_yr=18, ale 5 rekordów -> OM; n_yr=17 przy 24 rekordach -> OM.
        assert pick_hours_for_daily_summary(
            _om_day() + _yr_day(n=5), {DAY: {"n_yr": 18}}, DAY).source == "openmeteo"
        assert pick_hours_for_daily_summary(
            _om_day() + _yr_day(), {DAY: {"n_yr": 17}}, DAY).source == "openmeteo"


class TestOpenMeteoFallback:
    def test_fallback_marker_and_source(self):
        pick = pick_hours_for_daily_summary(_om_day(), {}, DAY)
        assert pick.source == "openmeteo"
        assert pick.marker == " *"
        assert len(pick.hp) == 24
        assert all(h["source"] == "openmeteo" for h in pick.hp)

    def test_missing_daily_diag_entry_falls_back_to_openmeteo(self):
        # Brak klucza dnia w daily_diag -> n_yr=0 -> OM, nawet przy 24 rekordach Yr.
        pick = pick_hours_for_daily_summary(_om_day() + _yr_day(), {}, DAY)
        assert pick.source == "openmeteo"
        assert pick.marker == " *"

    def test_none_daily_diag_raises(self):
        # Charakterystyka stanu obecnego: funkcja NIE toleruje daily_diag=None
        # (AttributeError na .get). Konsumenci zawsze przekazują dict.
        with pytest.raises(AttributeError):
            pick_hours_for_daily_summary(_om_day() + _yr_day(), None, DAY)

    def test_other_days_diag_entry_does_not_count(self):
        pick = pick_hours_for_daily_summary(
            _om_day() + _yr_day(), {"2026-01-17": {"n_yr": 24}}, DAY)
        assert pick.source == "openmeteo"


class TestMixedFallback:
    def test_only_short_yr_records_is_mixed(self):
        # Charakterystyka: gdy nie ma rekordów OM danego dnia, a Yr jest poniżej
        # progu, „mixed" zawiera wyłącznie rekordy Yr (nie „mieszankę" modeli).
        pick = pick_hours_for_daily_summary(_yr_day(n=4), {DAY: {"n_yr": 4}}, DAY)
        assert pick.source == "mixed"
        assert pick.marker == " *"
        assert len(pick.hp) == 4
        assert all(h["source"] == "yrno" for h in pick.hp)

    def test_no_records_at_all_is_empty_mixed(self):
        pick = pick_hours_for_daily_summary([], {}, DAY)
        assert pick.source == "mixed"
        assert pick.marker == " *"
        assert pick.hp == []


class TestHelCase:
    def test_hel_day_plus_two_with_15_yr_records_uses_openmeteo(self):
        # Audyt (raport końcowy §3): dla Helu dzień +2 ma 15 rekordów Yr,
        # czyli zawsze poniżej progu 18 -> wyświetlane są dane OpenMeteo.
        hours = _om_day() + _yr_day(n=15)
        pick = pick_hours_for_daily_summary(hours, {DAY: {"n_yr": 15}}, DAY)
        assert pick.source == "openmeteo"
        assert pick.marker == " *"
        assert all(h["source"] == "openmeteo" for h in pick.hp)


class TestNoMatchedInfluence:
    """Wybór źródła dnia nie konsumuje `daily_diag_matched` (rejestr §3.3)."""

    def test_matched_key_in_daily_diag_is_ignored(self):
        hours = _om_day() + _yr_day()
        plain = {DAY: {"n_yr": 18}}
        with_matched = {DAY: {"n_yr": 18},
                        "daily_diag_matched": {DAY: {"coverage_status": "matched"}}}
        assert pick_hours_for_daily_summary(hours, plain, DAY) == \
            pick_hours_for_daily_summary(hours, with_matched, DAY)

    def test_matched_key_does_not_flip_fallback(self):
        hours = _om_day() + _yr_day()
        matched = {DAY: {"n_yr": 3},
                   "daily_diag_matched": {DAY: {"coverage_status": "matched", "n_matched": 24}}}
        pick = pick_hours_for_daily_summary(hours, matched, DAY)
        assert pick.source == "openmeteo"  # liczy się wyłącznie legacy n_yr
