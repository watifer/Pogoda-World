# confidence_gate.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict
from i18n import STRINGS

@dataclass
class TrustReport:
    is_volatile: bool = False
    soften_hero_language: bool = False
    hide_block_details: bool = False
    note: str = ""

def _f(x, default=0.0) -> float:
    try:
        if x is None: 
            return default
        if isinstance(x, str) and not x.strip():
            return default
        return float(x)
    except Exception:
        return default
        
def _get_eff_cloud(h_dict: dict, suffix: str = "") -> Optional[float]:
    """Zwraca efektywne zachmurzenie (bez cirrusów) dla podanego sufiksu kluczy"""
    low = h_dict.get(f"clouds_low_pct{suffix}")
    mid = h_dict.get(f"clouds_mid_pct{suffix}")
    tot = h_dict.get(f"clouds_pct{suffix}")
    
    if low is not None and mid is not None:
        return min(100.0, float(low) + float(mid))
    if tot is not None:
        return float(tot)
    return None        


def compute_trust_report(
    ho: List[Dict],
    hy: List[Dict],
    today_str: str,
    current_hour: int,
    lang: str = "pl"  # <--- ZMIANA 1: Dodajemy obsługę języka
) -> TrustReport:
    """Minimalny detektor rozjazdu modeli, tylko dla UI."""
    om = {h.get("time_local"): h for h in ho if h.get("time_local")}
    yr = {h.get("time_local"): h for h in hy if h.get("time_local")}

    start = max(6, int(current_hour or 0))
    end = 22

    paired = []
    for t, h_om in om.items():
        if not (t and t.startswith(today_str)): continue
        try: hh = int(t[11:13])
        except Exception: continue
        if hh < start or hh >= end: continue
        
        h_yr = yr.get(t)
        if not h_yr: continue
        paired.append((hh, h_om, h_yr))

    if len(paired) < 3:
        return TrustReport()

    precip_conflicts = 0
    wind_conflicts = 0
    cloud_big = 0
    cloud_n = 0
    
    for hh, a, b in paired:
        # --- 1. ROZBIEŻNOŚĆ OPADÓW ---
        p_om = _f(a.get("precip_mm"), 0.0)
        p_yr = _f(b.get("precip_mm"), 0.0)
        pop_om = _f(a.get("precip_prob_pct"), _f(a.get("pop_pct"), _f(a.get("pop"), 0.0)))
        pop_yr = _f(b.get("precip_prob_pct"), _f(b.get("pop_pct"), _f(b.get("pop"), 0.0)))

        if p_om >= 2.0 and p_yr <= 0.0 and pop_om >= 40:
            precip_conflicts += 1
        elif p_yr >= 2.0 and p_om <= 0.0 and pop_yr >= 40:
            precip_conflicts += 1

        # --- 2. ROZBIEŻNOŚĆ WIATRU (NOWOŚĆ) ---
        # Bierzemy najwyższą wartość z wiatru lub porywów
        w_om = max(_f(a.get("gust_kmh")), _f(a.get("wind_kmh")))
        w_yr = max(_f(b.get("gust_kmh")), _f(b.get("wind_kmh")))
        
        # Konflikt: różnica > 30 km/h, przy czym jeden z modeli widzi co najmniej silny wiatr (>= 55 km/h)
        if abs(w_om - w_yr) >= 30 and max(w_om, w_yr) >= 55:
            wind_conflicts += 1

        # --- 3. ROZBIEŻNOŚĆ ZACHMURZENIA ---
        c_om = _get_eff_cloud(a)
        c_yr = _get_eff_cloud(b)
        if c_yr is None:
            c_yr = _get_eff_cloud(b, "_yr") # Fallback na klucze Yr.no
            
        if c_om is not None and c_yr is not None:
            cloud_n += 1
            if abs(c_om - c_yr) >= 50:
                cloud_big += 1

    # Podsumowanie twardych rozjazdów
    precip_hard = precip_conflicts >= 2
    wind_hard = wind_conflicts >= 2
    clouds_soft = (cloud_n >= 4 and cloud_big >= max(2, cloud_n // 2))

    is_volatile = bool(precip_hard or wind_hard or clouds_soft)
    soften = is_volatile
    hide = bool(precip_hard)

    # --- INTELIGENTNY DOBÓR NOTATKI ---
    note = ""
    if precip_hard: 
        note = STRINGS[lang].get("trust_precip", "")
    elif wind_hard:
        note = STRINGS[lang].get("trust_wind", "")
    elif clouds_soft:
        max_p = max([max(_f(a.get("precip_mm")), _f(b.get("precip_mm"))) for hh, a, b in paired] + [0.0])
        max_w = max([max(_f(a.get("gust_kmh")), _f(a.get("wind_kmh")), _f(b.get("gust_kmh")), _f(b.get("wind_kmh"))) for hh, a, b in paired] + [0.0])
        
        if max_p < 2.0 and max_w < 50.0:
            note = STRINGS[lang].get("trust_clouds", "")

    return TrustReport(
        is_volatile=is_volatile,
        soften_hero_language=soften,
        hide_block_details=hide,
        note=note
    )