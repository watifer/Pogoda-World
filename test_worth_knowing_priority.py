"""Regresje rankingu WK; bez sieci, AI i zależności od modułu nadmorskiego."""

import pytest

from worth_knowing import (
    SOFT_PENALTY,
    VISIBLE_PENALTY,
    _candidates_level1,
    _score,
    _validate_candidate,
    build_worth_knowing,
    enforce_priority_policy,
)


def storm_candidate(priority=0):
    return {
        "priority": priority,
        "category": "storm_risk",
        "kind": "impact",
        "wx": ["storm"],
        "text": "Ryzyko burzy: Modele wskazują dziś na 40% szans na wyładowania.",
    }


def test_storm_priority_zero_is_preserved():
    assert enforce_priority_policy(storm_candidate())["priority"] == 0


@pytest.mark.parametrize("priority", [None, False, "", 0.0])
def test_existing_falsy_priority_fallback_is_preserved(priority):
    assert enforce_priority_policy(storm_candidate(priority))["priority"] == 99


def test_missing_priority_defaults_to_99():
    candidate = storm_candidate()
    del candidate["priority"]
    assert enforce_priority_policy(candidate)["priority"] == 99


@pytest.mark.parametrize("priority, expected", [(1, 1), (-1, -1), ("0", 0), ("12", 12)])
def test_other_priority_values_are_unchanged(priority, expected):
    assert enforce_priority_policy(storm_candidate(priority))["priority"] == expected


def test_storm_wins_in_real_worth_knowing_selection(monkeypatch):
    monkeypatch.setenv("ENABLE_WK_AI_CANDIDATE", "0")
    hours = [
        {
            "time_local": f"2026-07-10T{hour:02d}:00+02:00",
            "thunder_prob": 40,
            "temp_c": 20,
            "dewpoint_c": 18,
            "wind_kmh": 8,
            "gust_kmh": 12,
            "precip_mm": 0,
            "precip_prob_pct": 0,
            "clouds_pct": 30,
            "uv_index": 2,
        }
        for hour in range(10, 17)
    ]
    candidates = _candidates_level1([], 20, 20, 0, False, None, 8, 12, hours, 10)
    storm = next(c for c in candidates if c["category"] == "storm_risk")
    dewpoint = next(c for c in candidates if c["category"] == "dewpoint_alert")
    assert storm["priority"] == 0
    assert _score(enforce_priority_policy(storm), set(), set(), set()) == 0
    assert _score(enforce_priority_policy(dewpoint), set(), set(), set()) == 2

    result = build_worth_knowing(
        payload={"forecast_source": "OpenMeteo + Yr.no", "lang": "pl"},
        blocks=[], alerts=[], temp_min=20, temp_max=20, max_wind=8, gust_kmh=12,
        total_precip_mm=0, is_afternoon_report=False, ta=hours, current_hour=10,
        summary_line="", context_line_text="", built_blocks=[],
    )
    assert result["text"] == "Ryzyko burzy: Modele wskazują dziś na 40% szans na wyładowania."


def test_alert_collision_penalties_and_visible_duplicate_policy():
    storm = enforce_priority_policy(storm_candidate())
    assert _score(storm, {("storm", "impact")}, {"storm"}, set()) == float("inf")
    assert _score(storm, set(), {"storm"}, set()) == SOFT_PENALTY
    assert _score(storm, set(), set(), {"storm"}) == VISIBLE_PENALTY
    assert _score(storm, set(), {"storm"}, {"storm"}) == SOFT_PENALTY + VISIBLE_PENALTY

    # Storm-risk is explicitly exempted from visible-category duplicate rejection.
    assert _validate_candidate(storm, 10, False, {"storm"})
    duplicate = {"text": "silny wiatr", "category": "wind", "wx": ["wind"]}
    assert not _validate_candidate(duplicate, 10, False, {"wind"})
    window = {"text": "okno bez wiatru", "category": "window", "wx": ["wind"]}
    assert _validate_candidate(window, 10, False, {"wind"})


def test_matching_storm_alert_still_suppresses_storm_candidate(monkeypatch):
    monkeypatch.setenv("ENABLE_WK_AI_CANDIDATE", "0")
    hours = [
        {"time_local": f"2026-07-10T{hour:02d}:00+02:00", "thunder_prob": 40,
         "temp_c": 20, "dewpoint_c": 18, "wind_kmh": 8,
         "gust_kmh": 12, "precip_mm": 0, "clouds_pct": 30}
        for hour in range(10, 17)
    ]
    result = build_worth_knowing(
        payload={"forecast_source": "OpenMeteo + Yr.no", "lang": "pl"},
        blocks=[], alerts=["Uwaga burza"], temp_min=20, temp_max=20,
        max_wind=8, gust_kmh=12, total_precip_mm=0, ta=hours, current_hour=10,
    )
    assert "Trudny biomet" in result["text"]
