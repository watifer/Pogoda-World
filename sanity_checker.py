"""
sanity_checker.py — Moduł wyłapujący fizyczne i logiczne absurdy w przygotowanej karcie pogodowej.
Działa na podstawie surowych danych (payload), a nie tekstów UI.
"""
import re
from typing import List

def run_sanity_check(layout: dict, payload: dict) -> List[str]:
    anomalies = []
    hours = payload.get("hours", [])
    
    if not hours:
        return anomalies

    main_icon = layout.get("main_icon", "")
    
    # Wyciąganie skrajnych temperatur z najbliższych 24h z surowego payloadu
    temps = [h.get("temp_c") for h in hours[:24] if h.get("temp_c") is not None]
    if not temps:
        return anomalies
        
    payload_tmax = max(temps)
    payload_tmin = min(temps)

    # R1: Deszcz przy silnym mrozie (Syndrom Alaski)
    if "rain" in main_icon or "showers" in main_icon:
        if payload_tmax <= -5:
            anomalies.append(f"[R1] ANOMALIA: Hero icon to deszcz ({main_icon}), ale max temp w payload to {payload_tmax}°C.")

    # R2: Śnieg w upale
    if "snow" in main_icon or "sleet" in main_icon:
        if payload_tmin >= 8:
            anomalies.append(f"[R2] ANOMALIA: Hero icon to śnieg ({main_icon}), ale min temp w payload to {payload_tmin}°C.")

    # R3: Błąd opadów w konkretnych blokach godzinowych
    today_blocks = layout.get("today_blocks", [])
    for b in today_blocks:
        b_icon = b.get("icon", "")
        if "rain" in b_icon:
            # Szukamy temperatur zdefiniowanych w tym bloku za pomocą regex
            temps_in_block = [int(t) for t in re.findall(r"-?\d+", b.get("temp_range", ""))]
            if temps_in_block and max(temps_in_block) <= -5:
                anomalies.append(f"[R3] ANOMALIA: Blok '{b.get('label')}' pokazuje deszcz ({b_icon}) przy temp. max {max(temps_in_block)}°C.")

    # R4: Brak model_agreement przy wątpliwej prognozie (Ostrzeżenie)
    context_line = layout.get("context_line", "") or ""
    sources = set(h.get("source") for h in hours if h.get("source"))
    if "rozbież" in context_line.lower() and len(sources) < 2:
        anomalies.append("[R4] OSTRZEŻENIE: context_line sugeruje rozbieżność modeli, ale payload zawiera tylko jedno źródło danych.")

    return anomalies