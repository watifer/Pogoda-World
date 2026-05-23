# confidence_gate.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict

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

def compute_trust_report(
    ho: List[Dict],
    hy: List[Dict],
    today_str: str,
    current_hour: int,
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
    for hh, a, b in paired:
        p_om = _f(a.get("precip_mm"), 0.0)
        p_yr = _f(b.get("precip_mm"), 0.0)

        pop_om = _f(a.get("precip_prob_pct"), _f(a.get("pop_pct"), _f(a.get("pop"), 0.0)))
        pop_yr = _f(b.get("precip_prob_pct"), _f(b.get("pop_pct"), _f(b.get("pop"), 0.0)))

        if p_om >= 2.0 and p_yr <= 0.0 and pop_om >= 40:
            precip_conflicts += 1
        elif p_yr >= 2.0 and p_om <= 0.0 and pop_yr >= 40:
            precip_conflicts += 1

    precip_hard = precip_conflicts >= 2

    cloud_big = 0
    cloud_n = 0
    for hh, a, b in paired:
        c_om = _f(a.get("clouds_pct"), 0.0)
        c_yr = _f(b.get("clouds_pct"), _f(b.get("clouds_pct_yr"), None))
        if c_yr is None: continue
        
        cloud_n += 1
        if abs(c_om - c_yr) >= 50:
            cloud_big += 1

    clouds_soft = (cloud_n >= 4 and cloud_big >= max(2, cloud_n // 2))

    is_volatile = bool(precip_hard or clouds_soft)
    soften = is_volatile
    hide = bool(precip_hard)

    note = ""
    if precip_hard: note = "Modele są rozbieżne co do opadów w ciągu dnia."
    elif clouds_soft: note = "Modele są rozbieżne co do zachmurzenia."

    return TrustReport(
        is_volatile=is_volatile,
        soften_hero_language=soften,
        hide_block_details=hide,
        note=note
    )