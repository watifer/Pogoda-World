import pytest
from worth_knowing import _validate_candidate

def test_v2_rejects_rano_in_afternoon():
    c = {"text": "jutro rano opady", "wx": "rain", "category": "forecast"}
    assert _validate_candidate(c, current_hour=15, is_afternoon_report=True, visible_categories=set()) is False

def test_v4_rejects_visible_wx_unless_window():
    visible = {"wind"}
    c_impact = {"text": "silny wiatr", "wx": "wind", "category": "impact"}
    c_window = {"text": "okno bez wiatru", "wx": "wind", "category": "window"}

    assert _validate_candidate(c_impact, current_hour=10, is_afternoon_report=False, visible_categories=visible) is False
    assert _validate_candidate(c_window, current_hour=10, is_afternoon_report=False, visible_categories=visible) is True

def test_v3_rejects_past_hours():
    c = {"text": "najlepiej wyjść przed 14:00", "wx": "rain"}
    assert _validate_candidate(c, current_hour=16, is_afternoon_report=False, visible_categories=set()) is False
    assert _validate_candidate(c, current_hour=10, is_afternoon_report=False, visible_categories=set()) is True

def test_top_k_logic_simulation():
    scored = [
        (10, {"text": "rano deszcz", "wx": "rain", "category": "impact"}), # Odpadnie
        (20, {"text": "wyjdź przed 12:00", "wx": "rain", "category": "window"}), # Odpadnie
        (30, {"text": "idealne warunki", "wx": None, "category": "opportunity"}) # Przejdzie
    ]
    
    current_hour = 15
    is_afternoon = True
    visible = set()
    
    winner = None
    for _, c in scored[:5]:
        if _validate_candidate(c, current_hour, is_afternoon, visible):
            winner = c
            break
            
    assert winner is not None
    assert winner["text"] == "idealne warunki"

def test_v5_rejects_sun_at_night():
    """V5 odrzuca kandydata mówiącego o słońcu w raporcie popołudniowym/wieczornym."""
    c = {"text": "najwięcej słońca będzie o 18:00", "wx": None, "category": "sun"}
    # Dla godziny 17 i wyżej powinno odrzucić
    assert _validate_candidate(c, current_hour=17, is_afternoon_report=True, visible_categories=set()) is False
    # Dla godziny 14 powinno przepuścić
    assert _validate_candidate(c, current_hour=14, is_afternoon_report=True, visible_categories=set()) is True