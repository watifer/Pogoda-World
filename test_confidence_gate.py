"""Testy charakteryzujące bramkę niepewności (confidence gate) — ETAP 2.

Zamrażają OBECNE zachowanie `confidence_gate.compute_trust_report`
(rejestr etapu 1 §3.3, raport końcowy §3): dopasowanie par OM/Yr po pełnym
`time_local`, okno dzienne ``max(6, current_hour)..22``, próg ≥3 par,
twardy konflikt opadów ≥2 / wiatru ≥2 / chmur (n≥4 i ≥max(2, n//2)),
``soften_hero_language = is_volatile``, ``hide_block_details = precip_hard``
oraz zależność notki `trust_clouds` od progów max_p < 2,0 mm i max_w < 50 km/h.

Ważne rozróżnienie (rejestr §3.3): `TrustReport.is_volatile` (dopasowane godziny,
konflikty punktowe) jest NIEZALEŻNE od `daily_diag[date]["is_volatile"]`
(ekstrema legacy, bez dopasowania godzin) — oba są tu charakteryzowane razem.

Wszystko offline, na syntetycznych rekordach godzinowych.
"""

import pytest

from confidence_gate import TrustReport, compute_trust_report

DAY = "2026-01-15"


def _om(hour, day=DAY, **kw):
    h = {
        "time_local": f"{day}T{hour:02d}:00+01:00",
        "source": "openmeteo",
        "temp_c": 10.0,
        "precip_mm": 0.0,
        "precip_prob_pct": 0,
        "wind_kmh": 10.0,
        "gust_kmh": 15.0,
        "clouds_pct": 30.0,
        "clouds_low_pct": 10.0,
        "clouds_mid_pct": 10.0,
    }
    h.update(kw)
    return h


def _yr(hour, day=DAY, **kw):
    return _om(hour, day, source="yrno", **kw)


class TestTrustReportDefaults:
    def test_dataclass_defaults(self):
        r = TrustReport()
        assert r.is_volatile is False
        assert r.soften_hero_language is False
        assert r.hide_block_details is False
        assert r.note == ""

    def test_empty_inputs_give_neutral_report(self):
        r = compute_trust_report([], [], DAY, current_hour=9)
        assert r == TrustReport()

    def test_fewer_than_3_pairs_is_neutral(self):
        r = compute_trust_report([_om(10), _om(11)], [_yr(10), _yr(11)], DAY, current_hour=9)
        assert r == TrustReport()

    def test_exactly_3_agreeing_pairs_is_neutral(self):
        r = compute_trust_report([_om(h) for h in (10, 11, 12)],
                                 [_yr(h) for h in (10, 11, 12)], DAY, current_hour=9)
        assert r == TrustReport()

    def test_matching_requires_same_time_local(self):
        # Rozsunięte o godzinę -> zero par -> raport neutralny.
        ho = [_om(h, precip_mm=5.0, precip_prob_pct=90) for h in (10, 11, 12)]
        hy = [_yr(h) for h in (11, 12, 13)]
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r.is_volatile is False


class TestPrecipConflict:
    """Twardy konflikt opadów: >= 2 par, w których jedno >= 2,0 mm, drugie 0,0,
    a strona mokra ma pop >= 40%. Priorytet nad innymi notkami i hide=True."""

    def test_two_conflicts_trigger_volatility(self):
        ho = [_om(10, precip_mm=3.0, precip_prob_pct=80),
              _om(11, precip_mm=2.5, precip_prob_pct=60), _om(12)]
        hy = [_yr(10), _yr(11), _yr(12)]
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r.is_volatile is True
        assert r.soften_hero_language is True
        assert r.hide_block_details is True
        assert r.note == "Modele są rozbieżne co do opadów w ciągu dnia."

    def test_mirrored_conflict_also_counts(self):
        # Yr mokro, OM sucho (pop po stronie Yr >= 40).
        ho = [_om(10), _om(11), _om(12)]
        hy = [_yr(10, precip_mm=4.0, precip_prob_pct=90),
              _yr(11, precip_mm=3.0, precip_prob_pct=50), _yr(12)]
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r.is_volatile is True
        assert r.hide_block_details is True

    def test_single_conflict_is_not_hard(self):
        ho = [_om(10, precip_mm=3.0, precip_prob_pct=80), _om(11), _om(12)]
        hy = [_yr(10), _yr(11), _yr(12)]
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r.is_volatile is False
        assert r.note == ""

    def test_wet_side_below_pop_40_does_not_count(self):
        ho = [_om(10), _om(11), _om(12)]
        hy = [_yr(10, precip_mm=4.0, precip_prob_pct=39),
              _yr(11, precip_mm=3.0, precip_prob_pct=50), _yr(12)]
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r.is_volatile is False

    def test_localized_note(self):
        ho = [_om(10, precip_mm=3.0, precip_prob_pct=80),
              _om(11, precip_mm=2.5, precip_prob_pct=60), _om(12)]
        hy = [_yr(10), _yr(11), _yr(12)]
        r = compute_trust_report(ho, hy, DAY, current_hour=9, lang="en")
        assert r.note == "Forecast models disagree on daytime precipitation."


class TestWindConflict:
    """Rozjazd wiatru: max(wind, gust) różni się o >= 30 km/h przy max >= 55 km/h;
    twardy konflikt od >= 2 par. Nie ukrywa szczegółów bloków."""

    def test_two_conflicts_trigger_volatility(self):
        ho = [_om(10, wind_kmh=60.0), _om(11, gust_kmh=70.0), _om(12)]
        hy = [_yr(10, wind_kmh=20.0), _yr(11, gust_kmh=25.0), _yr(12)]
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r.is_volatile is True
        assert r.soften_hero_language is True
        assert r.hide_block_details is False
        assert r.note == "Modele są rozbieżne co do siły wiatru."

    def test_gusts_are_compared_via_max_wind_gust(self):
        ho = [_om(10, wind_kmh=10.0, gust_kmh=80.0), _om(11, wind_kmh=70.0), _om(12)]
        hy = [_yr(10, wind_kmh=20.0), _yr(11, wind_kmh=20.0), _yr(12)]
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r.is_volatile is True

    def test_single_conflict_is_not_hard(self):
        ho = [_om(10, wind_kmh=60.0), _om(11), _om(12)]
        hy = [_yr(10, wind_kmh=20.0), _yr(11), _yr(12)]
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r.is_volatile is False

    def test_big_diff_below_55_kmh_does_not_count(self):
        ho = [_om(10, wind_kmh=45.0), _om(11, wind_kmh=45.0), _om(12)]
        hy = [_yr(10, wind_kmh=10.0), _yr(11, wind_kmh=10.0), _yr(12)]
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r.is_volatile is False


class TestCloudConflict:
    """Rozjazd chmur: efektywne niskie+średnie (fallback: clouds_*_yr) różnią się
    o >= 50 pkt; twardy konflikt gdy par >= 4 i dużych rozjazdów >= max(2, n//2)."""

    def _pairs(self, hours=(10, 11, 12, 13), om_low=5.0, om_mid=5.0,
               yr_low=60.0, yr_mid=30.0):
        ho = [_om(h, clouds_low_pct=om_low, clouds_mid_pct=om_mid) for h in hours]
        hy = [_yr(h, clouds_low_pct=yr_low, clouds_mid_pct=yr_mid) for h in hours]
        return ho, hy

    def test_four_of_four_big_conflicts(self):
        ho, hy = self._pairs()
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r.is_volatile is True
        assert r.soften_hero_language is True
        assert r.hide_block_details is False
        assert r.note == "Modele są rozbieżne co do zachmurzenia. Skorygowano kartę /now"

    def test_three_pairs_cannot_be_hard_even_if_all_conflict(self):
        # cloud_n = 3 < 4 -> reguła n>=4 nie pozwala na twardy konflikt.
        ho, hy = self._pairs(hours=(10, 11, 12))
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r.is_volatile is False

    def test_one_big_of_four_is_not_enough(self):
        # n=4 wymaga >= max(2, 4//2)=2 dużych rozjazdów.
        ho = [_om(h, clouds_low_pct=5.0, clouds_mid_pct=5.0) for h in (10, 11, 12, 13)]
        hy = [_yr(10, clouds_low_pct=60.0, clouds_mid_pct=30.0),
              _yr(11), _yr(12), _yr(13)]
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r.is_volatile is False

    def test_yr_total_clouds_fallback_is_used(self):
        # Charakterystyka fallbacków _get_eff_cloud: gdy rekord nie ma pól
        # clouds_low/mid (ani ich wariantów _yr), brane jest clouds_pct total.
        ho = [_om(h, clouds_low_pct=5.0, clouds_mid_pct=5.0) for h in (10, 11, 12, 13)]
        hy = []
        for h in (10, 11, 12, 13):
            y = _yr(h)
            del y["clouds_low_pct"], y["clouds_mid_pct"]
            y["clouds_pct"] = 100.0
            hy.append(y)
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r.is_volatile is True

    def test_missing_all_cloud_fields_skips_pair(self):
        # Bez żadnych pól chmur po stronie Yr para nie liczy się do rozjazdu.
        ho = [_om(h, clouds_low_pct=5.0, clouds_mid_pct=5.0) for h in (10, 11, 12, 13)]
        hy = []
        for h in (10, 11, 12, 13):
            y = _yr(h)
            del y["clouds_low_pct"], y["clouds_mid_pct"], y["clouds_pct"]
            hy.append(y)
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r.is_volatile is False

    def test_cloud_note_suppressed_by_heavy_precip(self):
        # Notka trust_clouds tylko gdy max_p < 2,0 mm.
        ho, hy = self._pairs()
        ho[0]["precip_mm"] = 3.0
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r.is_volatile is True
        assert r.note == ""

    def test_cloud_note_suppressed_by_strong_wind(self):
        # ...i gdy max_w >= 50 km/h.
        ho, hy = self._pairs()
        ho[0]["wind_kmh"] = 55.0
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r.is_volatile is True
        assert r.note == ""


class TestDaylightWindow:
    """Pary są filtrowane do okna [max(6, current_hour), 22) danego dnia."""

    def _conflict(self, hours):
        ho = [_om(h, precip_mm=3.0, precip_prob_pct=80) for h in hours]
        hy = [_yr(h) for h in hours]
        return ho, hy

    def test_hours_before_current_are_excluded(self):
        ho, hy = self._conflict((6, 7, 8))
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r.is_volatile is False

    def test_morning_floor_is_6(self):
        # O 5:00 okno zaczyna się od 6:00 (max(6, current_hour)).
        ho, hy = self._conflict((6, 7, 8))
        r = compute_trust_report(ho, hy, DAY, current_hour=5)
        assert r.is_volatile is True

    def test_hours_from_22_on_are_excluded(self):
        ho, hy = self._conflict((22, 23))
        r = compute_trust_report(ho, hy, DAY, current_hour=20)
        assert r.is_volatile is False

    def test_other_dates_are_excluded(self):
        ho, hy = self._conflict((10, 11, 12))
        r = compute_trust_report(ho, hy, "2026-01-16", current_hour=9)
        assert r.is_volatile is False


class TestVsLegacyDailyDiag:
    """TrustReport.is_volatile (pary godzinowe) vs legacy daily_diag.is_volatile
    (ekstrema): to niezależne sygnały (rejestr §3.3)."""

    def test_hourly_conflict_without_extreme_spread(self):
        # Modele zgodne w ekstremach (min/max identyczne), ale rozjazd punktowy:
        # legacy is_volatile=False, TrustReport volatile.
        ho = [_om(10, temp_c=5.0, precip_mm=3.0, precip_prob_pct=80),
              _om(11, temp_c=20.0, precip_mm=2.5, precip_prob_pct=60),
              _om(12, temp_c=10.0)]
        hy = [_yr(10, temp_c=20.0), _yr(11, temp_c=5.0), _yr(12, temp_c=10.0)]
        # ekstrema dzienne: OM min 5 / max 20 == Yr min 5 / max 20 -> spread 0
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r.is_volatile is True

    def test_extreme_spread_without_hourly_conflict(self):
        # Duży spread ekstremów (legacy is_volatile=True), ale pary godzinowe
        # zgodne co do opadów/wiatru/chmur -> TrustReport neutralny.
        ho = [_om(10, temp_c=30.0), _om(11, temp_c=30.0), _om(12, temp_c=30.0)]
        hy = [_yr(10, temp_c=10.0), _yr(11, temp_c=10.0), _yr(12, temp_c=10.0)]
        r = compute_trust_report(ho, hy, DAY, current_hour=9)
        assert r == TrustReport()
