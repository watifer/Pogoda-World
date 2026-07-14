"""
worth_knowing.py — Moduł „Dziś warto wiedzieć".
Regułowy, deterministyczny, bez AI.
Maksymalnie 1 komunikat na raport.
"""

from __future__ import annotations
from typing import Optional, List, Dict, Tuple, Set
from datetime import datetime, timedelta
import re
import os
import json


_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
_HOUR_RE = re.compile(r"\b([01]?\d|2[0-3]):00\b")





def _num_tokens(s: str) -> set[str]:
    # Najpierw bezlitośnie wymazujemy z tekstu zegary (np. 21:00, 10:00), 
    # żeby walidator liczb w ogóle ich nie widział.
    import re
    s_clean = re.sub(r'\b\d{1,2}:\d{2}\b', '', s or "")
    
    # Dopiero na czystym tekście szukamy zwykłych liczb (np. temperatur czy mm opadu)
    return set(m.group(0).replace(",", ".") for m in _NUM_RE.finditer(s_clean))

def _hour_tokens(s: str) -> set[int]:
    return set(int(m.group(0).split(":")[0]) for m in _HOUR_RE.finditer(s or ""))

def _f(x, default=0.0) -> float:
    try:
        if x is None: 
            return default
        if isinstance(x, str) and not x.strip():
            return default
        return float(x)
    except Exception:
        return default

def _eff_cld(h: dict) -> float:
    """Zwraca efektywne zachmurzenie (bez wysokich chmur - cirrusów)."""
    low_c = h.get("clouds_low_pct")
    mid_c = h.get("clouds_mid_pct")
    if low_c is not None and mid_c is not None:
        return min(100.0, float(low_c) + float(mid_c))
    return float(h.get("clouds_pct") or 0)
    
def _eff_cld_alt(low_c, mid_c, total_c) -> float:
    if low_c is not None and mid_c is not None:
        return min(100.0, float(low_c) + float(mid_c))
    return float(total_c or 0)

def _eff_cld_consensus(h: dict) -> float:
    base = _eff_cld(h)
    alt = _eff_cld_alt(h.get("clouds_low_pct_yr"), h.get("clouds_mid_pct_yr"), h.get("clouds_pct_yr"))
    return max(base, alt)
    
    
def _uncertain_sky(ta: list) -> bool:
    """Sprawdza czy modele prognozują zupełnie co innego (rozjazd chmur >= 40%)."""
    if not ta:
        return False
    
    diffs = []
    for h in ta:
        # Zgodnie z audytorem: wyliczamy chmury dla OM i Yr osobno
        om_cld = _eff_cld(h) 
        yr_cld = _eff_cld_alt(h.get("clouds_low_pct_yr"), h.get("clouds_mid_pct_yr"), h.get("clouds_pct_yr"))
        
        # Jeśli Yr.no nie ma danych (żadnych pól z chmurami), pomijamy
        if (h.get("clouds_pct_yr") is None 
            and h.get("clouds_low_pct_yr") is None 
            and h.get("clouds_mid_pct_yr") is None):
            continue
            
        diffs.append(abs(om_cld - yr_cld))
    
    if not diffs:
        return False
        
    # Jeśli w co najmniej 1/3 godzin różnica >= 40% -> to jest "niepewne niebo"
    big_diffs = sum(1 for d in diffs if d >= 40)
    return big_diffs >= max(2, len(diffs) // 3)
    



def _build_wk_facts(ta: list, blocks: list, current_hour: int, is_afternoon_report: bool,
                    temp_min, temp_max, total_precip_mm: float, max_wind: float, gust_kmh) -> dict:
    future_ta = []
    if ta:
        for h in ta:
            t = h.get("time_local", "")
            if len(t) >= 13:
                try:
                    hh = int(t[11:13])
                    if hh >= current_hour:
                        future_ta.append(h)
                except Exception:
                    pass

    facts = {
        "current_hour": int(current_hour),
        "is_afternoon_report": bool(is_afternoon_report),
        "temp_min_today": None if temp_min is None else round(_f(temp_min)),
        "temp_max_today": None if temp_max is None else round(_f(temp_max)),
        "total_precip_mm_today": round(_f(total_precip_mm), 1),
        "max_wind_kmh_today": round(_f(max_wind)),
        "gust_kmh_today": round(_f(gust_kmh)) if gust_kmh is not None else None,
    }

    if future_ta:
        gust_peak_h = max(future_ta, key=lambda x: max(_f(x.get("gust_kmh")), _f(x.get("wind_kmh"))))
        gval = max(_f(gust_peak_h.get("gust_kmh")), _f(gust_peak_h.get("wind_kmh")))
        if gval > 0:
            facts["gust_peak_kmh"] = round(gval)
            facts["gust_hour"] = int(gust_peak_h["time_local"][11:13])

        precip_peak_h = max(future_ta, key=lambda x: _f(x.get("precip_mm")))
        pval = _f(precip_peak_h.get("precip_mm"))
        if pval > 0:
            facts["precip_peak_mm_h"] = round(pval, 1)
            facts["precip_hour"] = int(precip_peak_h["time_local"][11:13])

        try:
            pop_peak = max(int(_f(h.get("precip_prob_pct"), 0)) for h in future_ta)
        except Exception:
            pop_peak = 0
        if pop_peak > 0:
            facts["pop_peak_pct"] = pop_peak

        temps = [(int(h["time_local"][11:13]), _f(h.get("temp_c"), None)) for h in future_ta if h.get("temp_c") is not None]
        if temps:
            cold_hour, cold_temp = min(temps, key=lambda x: x[1])
            warm_hour, warm_temp = max(temps, key=lambda x: x[1])
            facts["cold_hour"] = int(cold_hour)
            facts["cold_temp_c"] = round(cold_temp)
            facts["warm_hour"] = int(warm_hour)
            facts["warm_temp_c"] = round(warm_temp)

        max_drop, max_rise, drop_hour, rise_hour = 0.0, 0.0, None, None
        for i in range(len(future_ta) - 3):
            t1 = future_ta[i].get("temp_c")
            t2 = future_ta[i + 3].get("temp_c")
            if t1 is None or t2 is None: continue
            diff = _f(t2) - _f(t1)
            try:
                hh = int(future_ta[i]["time_local"][11:13])
                if diff <= -5 and diff < max_drop:
                    max_drop, drop_hour = diff, hh
                if diff >= 5 and diff > max_rise:
                    max_rise, rise_hour = diff, hh
            except Exception: pass

        if drop_hour is not None:
            facts["temp_drop_3h_c"] = abs(round(max_drop))
            facts["drop_hour"] = int(drop_hour)
        if rise_hour is not None:
            facts["temp_rise_3h_c"] = round(max_rise)
            facts["rise_hour"] = int(rise_hour)

        # KOMFORTOWE OKNO
        best_comfort = None
        if len(future_ta) >= 3:
            for i in range(len(future_ta) - 2):
                window = future_ta[i:i+3]
                w_precip = max((_f(h.get("precip_mm")) for h in window), default=0)
                w_wind = max((_f(h.get("wind_kmh")) for h in window), default=0)
                w_temps = [ _f(h.get("temp_c")) for h in window if h.get("temp_c") is not None ]
                if w_precip == 0 and w_wind < 15 and w_temps:
                    avg_t = sum(w_temps) / len(w_temps)
                    if 16 <= avg_t <= 24:
                        try:
                            start_hr = int(window[0]["time_local"][11:13])
                            best_comfort = (start_hr, round(avg_t))
                            break
                        except Exception:
                            pass
        if best_comfort:
            facts["comfort_hour"] = int(best_comfort[0])
            facts["comfort_avg_temp_c"] = int(best_comfort[1])

        # SŁONECZNE OKNO
        daylight = []
        for h in future_ta:
            try:
                hh = int(h["time_local"][11:13])
                cld = _eff_cld_consensus(h)
                if 8 <= hh <= 16:
                    daylight.append((hh, cld))
            except Exception:
                pass
        if len(daylight) >= 4:
            avg_day_clouds = sum(c for _, c in daylight) / len(daylight)
            sun_h, sun_c = min(daylight, key=lambda x: x[1])
            if avg_day_clouds >= 40 and sun_c < 45:
                facts["sunniest_hour"] = int(sun_h)
                facts["sunniest_clouds_pct"] = int(round(sun_c))
                facts["avg_day_clouds_pct"] = int(round(avg_day_clouds))

    if blocks:
        w = _dry_window(blocks, is_afternoon_report)
        if w:
            ws, we, _, variant = w
            facts["window_start"], facts["window_end"], facts["window_variant"] = int(ws), int(we), variant
            # DODANA FLAGA DLA AI:
            facts["window_crosses_midnight"] = bool(int(we) <= int(ws))

    return facts

MAX_WK_LEN = 140
TOP_K = 5        

FAMILY_MAP = {
    "drizzle": "rain", "light_rain": "rain", "rain": "rain",
    "heavy_rain": "rain", "downpour": "rain",
    "light_snow": "snow", "snow": "snow", "heavy_snow": "snow",
    "sleet": "snow",
    "storm": "storm",
    "fog": "fog",
}

# ═══════════════════════════════════════
# HELPERY
# ═══════════════════════════════════════

def _ev(ev, attr, default=None):
    if isinstance(ev, dict):
        return ev.get(attr, default)
    return getattr(ev, attr, default)

def _has_family(blocks, family, indices=None):
    for i, b in enumerate(blocks):
        if indices and i not in indices:
            continue
        for ev in b.get("events", []):
            if FAMILY_MAP.get(_ev(ev, "kind", "")) == family:
                return True
    return False

def _storm_start(blocks):
    for b in blocks:
        for ev in b.get("events", []):
            if FAMILY_MAP.get(_ev(ev, "kind", "")) == "storm":
                return _ev(ev, "start", 0)
    return None

def _dry_window(blocks, is_afternoon=False):
    if not blocks:
        return None
    anchor = blocks[0].get("start", 0)

    def _norm(h):
        if h < anchor:
            return h + 24
        return h

    wet = set()
    storm_start_norm = None
    report_start = None
    report_end = None

    for b in blocks:
        bs = _norm(b.get("start", 0))
        be = _norm(b.get("end", 0))
        if be <= bs:
            be += 24
        if report_start is None or bs < report_start:
            report_start = bs
        if report_end is None or be > report_end:
            report_end = be
        for ev in b.get("events", []):
            kind = _ev(ev, "kind", "")
            family = FAMILY_MAP.get(kind)
            es = _norm(_ev(ev, "start", 0))
            ee = _norm(_ev(ev, "end", 0))
            if ee <= es:
                ee += 24
            if family in ("rain", "snow", "storm"):
                for h in range(es, ee):
                    wet.add(h)
            if family == "storm" and storm_start_norm is None:
                storm_start_norm = es

    if report_start is None:
        return None

    windows = []
    cur_s, cur_l = None, 0
    for h in range(report_start, report_end):
        if h not in wet:
            if cur_s is None:
                cur_s = h
            cur_l += 1
        else:
            if cur_l >= 2 and cur_s is not None:
                windows.append((cur_s, cur_s + cur_l))
            cur_s, cur_l = None, 0
    if cur_l >= 2 and cur_s is not None:
        windows.append((cur_s, cur_s + cur_l))

    if not windows:
        return None

    best = None
    for ws, we in windows:
        ws_real = ws % 24
        we_real = we % 24
        length = we - ws
        score = 100

        score -= min(length * 3, 20)
        if 8 <= ws_real <= 16:
            score -= 25
        elif 6 <= ws_real <= 18:
            score -= 15
        elif ws_real >= 20 or ws_real <= 5:
            score += 10

        if storm_start_norm and we <= storm_start_norm:
            score -= 12
        if ws_real >= 19 and length <= 3:
            score += 15

        if storm_start_norm and we <= storm_start_norm and ws_real < storm_start_norm % 24:
            variant = "before_storm"
        elif ws_real >= 17:
            variant = "evening_calm"
        else:
            variant = "midday"

        if best is None or score < best[2]:
            best = (ws_real, we_real, score, variant)

    return best


# ═══════════════════════════════════════
# ALERT + VISIBILITY PARSER
# ═══════════════════════════════════════

def parse_alert_signal(alert_text: str) -> Tuple[Optional[str], Optional[str]]:
    t = alert_text.lower()
    cat = None
    if "burz" in t:
        cat = "storm"
    elif "wiatr" in t or "poryw" in t:
        cat = "wind"
    elif "przymroz" in t or "oblodz" in t or "ślisk" in t or "mróz" in t:
        cat = "frost"
    elif "mgł" in t or "widoczno" in t:
        cat = "fog"
    elif "śnieg" in t or "śnież" in t:
        cat = "snow"
    elif "powietrz" in t or "caqi" in t:
        cat = "air"
    if cat is None:
        return (None, None)

    import re
    kind = "generic"
    if re.search(r'po \d|od \d|do \d|\d{1,2}:\d{2}', t):
        kind = "timing"
    elif any(w in t for w in ("silny", "porywy", "km/h")):
        kind = "strength"
    elif any(w in t for w in ("uwaga", "ślisk", "widoczność", "utrudnion")):
        kind = "impact"
    elif any(w in t for w in ("średni", "zła jakość")):
        kind = "quality"
    return (cat, kind)

def _extract_visible_categories(
    blocks: List[Dict],
    summary_line: str = "",
    context_line_text: str = "",
) -> Set[str]:
    visible = set()
    combined = (summary_line + " " + context_line_text).lower()

    if "śnieg" in combined or "śnieżn" in combined:
        visible.add("snow")
    if "deszcz" in combined:
        visible.add("rain")
    if "burz" in combined:
        visible.add("storm")
    if "mgł" in combined or "mglist" in combined:
        visible.add("fog")
    if "wiatr" in combined or "wietrz" in combined or "poryw" in combined:
        visible.add("wind")
    if "mróz" in combined or "przymroz" in combined:
        visible.add("frost")

    for b in blocks:
        desc = b.get("primary_desc", "").lower()
        extras = " ".join(e.get("text", "") for e in b.get("extra_lines", [])).lower()
        block_text = desc + " " + extras
        if "burz" in block_text:
            visible.add("storm")
        if "śnieg" in block_text:
            visible.add("snow")
        if "deszcz" in block_text or "mżawka" in block_text:
            visible.add("rain")
        if "mgła" in block_text:
            visible.add("fog")

    return visible


# ═══════════════════════════════════════
# KANDYDACI — POZIOM 1 (preferowani)
# ═══════════════════════════════════════

def _candidates_level1(blocks, temp_min, temp_max, total_precip_mm,
                       is_afternoon_report, tomorrow_morning_events, max_wind, gust_kmh, ta, current_hour):
    candidates = []

    # === NOWOCZESNE DANE: UV, BURZE i DUCHOTA (Najwyższy priorytet) ===
    if ta:
        try:
            month = int(ta[0]["time_local"][5:7])
        except Exception:
            month = 5
            
        max_uv = max((_f(h.get("uv_index")) for h in ta), default=0)
        max_thunder = max((_f(h.get("thunder_prob")) for h in ta), default=0)
        max_dewpoint = max((_f(h.get("dewpoint_c")) for h in ta), default=-99)
        
        # 1. ALERT BURZOWY
        if max_thunder >= 25.0:
            candidates.append({
                "priority": 0,
                "text": f"Ryzyko burzy: Modele wskazują dziś na {round(max_thunder)}% szans na wyładowania.",
                "category": "storm_risk",
                "kind": "impact",
                "wx": ["storm"]
            })
            
        # 2. INTELIGENTNY ALERT UV (Zakotwiczony, z kontrastem i niższym priorytetem)
        day_clouds = []
        sun_windows = []
        uv_peaks = []
        
        for h in ta:
            try:
                hh = int(h["time_local"][11:13])
                if 10 <= hh <= 16:
                    cld = _eff_cld_consensus(h)
                    day_clouds.append(cld)
                    sun_windows.append((hh, cld))
                    uv_peaks.append((hh, _f(h.get("uv_index"))))
            except Exception:
                pass
                
        avg_day_clouds = sum(day_clouds) / len(day_clouds) if day_clouds else 100
        
        best_sun_h, best_sun_c = min(sun_windows, key=lambda x: x[1], default=(12, 100))
        
        # UV bierzemy DOKŁADNIE z tej godziny, w której przewidujemy przejaśnienie
        uv_at_best = next((uvv for hh, uvv in uv_peaks if hh == best_sun_h), 0)
        uv = round(uv_at_best)

        # [POPRAWKA 2]: Obliczamy opad i grube chmury w środku dnia (10-16)
        daytime_precip = 0
        for h in ta:
            try:
                if 10 <= int(h["time_local"][11:13]) <= 16:
                    daytime_precip += _f(h.get("precip_mm"))
            except Exception: pass

        # Wymagamy UV >= 5, ORAZ braku silnego deszczu w środku dnia (mniej niż 2.0 mm)
        if uv >= 5.0 and daytime_precip < 2.0:
            uv_text = ""
            
            is_cloudy_day = avg_day_clouds >= 65
            has_sun_window = (best_sun_c <= 70) and ((avg_day_clouds - best_sun_c) >= 15)
            
            if not is_cloudy_day:
                if uv >= 8:
                    uv_text = f"Ekstremalne promieniowanie (UV {uv}). Unikaj słońca w południe, nałóż mocny filtr!"
                elif uv >= 6:
                    uv_text = f"Wysokie promieniowanie słoneczne (UV {uv}). Pamiętaj o kremie z filtrem i ochronie głowy."
                elif month in [5, 6, 7, 8]:
                    uv_text = f"Słońce dziś mocno operuje (UV {uv}). Przy dłuższym pobycie na zewnątrz użyj kremu."
            
            elif is_cloudy_day and has_sun_window:
                if uv >= 7:
                    uv_text = f"Zdradliwe słońce — przy przejaśnieniach ok. {best_sun_h:02d}:00 promieniowanie UV sięgnie aż {uv}."
                elif month in [5, 6, 7, 8]:
                    uv_text = f"Jeśli ok. {best_sun_h:02d}:00 wyjdzie słońce, promieniowanie UV będzie podwyższone ({uv})."

            if uv_text:
                # Priorytet 12-14 oznacza, że UV nie zagłuszy już informacji o ulewie czy wichurze!
                uv_prio = 12 if uv >= 7 else 14
                candidates.append({
                    "priority": uv_prio,
                    "text": uv_text,
                    "category": "uv_alert",
                    "kind": "impact",
                    "wx": []  # Puste, aby nie kolidowało z walidatorem unikalności
                })
                
        # 3. WSKAŹNIK DUCHOTY (Punkt rosy)
        if month in [5, 6, 7, 8, 9]:
            if max_dewpoint >= 20.0:
                candidates.append({
                    "priority": 2,
                    "text": f"Tropikalna duchota: Bardzo wysoka wilgotność sprawi, że powietrze będzie wyjątkowo ciężkie.",
                    "category": "dewpoint_alert",
                    "kind": "impact",
                    "wx": ["temp_change"]
                })
            elif max_dewpoint >= 17.0:
                candidates.append({
                    "priority": 2,
                    "text": f"Trudny biomet: Przez wysoką wilgotność odczujemy zaduch, a powietrze stanie się ciężkie i lepkie.",
                    "category": "dewpoint_alert",
                    "kind": "impact",
                    "wx": ["temp_change"]
                })
    # ==========================================================

    future_ta = []
    if ta:
        future_ta = [h for h in ta if len(h.get("time_local", "")) >= 13 and int(h["time_local"][11:13]) >= current_hour]

    cold_hr, cold_temp, warm_hr, warm_temp = None, None, None, None
    if future_ta:
        valid_ta = [h for h in future_ta if h.get("temp_c") is not None]
        if valid_ta:
            c_h = min(valid_ta, key=lambda x: x["temp_c"])
            w_h = max(valid_ta, key=lambda x: x["temp_c"])
            try:
                cold_hr = int(c_h["time_local"][11:13])
                cold_temp = c_h["temp_c"]
                warm_hr = int(w_h["time_local"][11:13])
                warm_temp = w_h["temp_c"]
            except:
                pass

    has_precip = _has_family(blocks, "rain") or _has_family(blocks, "snow")
    eff_gust = gust_kmh or max_wind

    if future_ta:
        max_hr_precip = max(future_ta, key=lambda x: float(x.get("precip_mm") or 0))
        p_val = float(max_hr_precip.get("precip_mm") or 0)
        
        max_hr_gust = max(future_ta, key=lambda x: float(x.get("gust_kmh") or x.get("wind_kmh") or 0))
        g_val = float(max_hr_gust.get("gust_kmh") or max_hr_gust.get("wind_kmh") or 0)

        if g_val >= 65:
            g_hr = int(max_hr_gust["time_local"][11:13])
            candidates.append({
                "priority": 1,
                "text": f"najmocniejsze porywy wiatru uderzą około {g_hr:02d}:00",
                "category": "peak_impact",
                "kind": "wind_peak",
                "wx": ["wind"]
            })
        elif p_val >= 3.0:
            p_hr = int(max_hr_precip["time_local"][11:13])
            t_op = max_hr_precip.get("temp_c", 10)
            typ = "śniegu" if t_op <= 2 else "opadów"
            candidates.append({
                "priority": 1,
                "text": f"największe natężenie {typ} zapowiada się około {p_hr:02d}:00",
                "category": "peak_impact",
                "kind": "precip_peak",
                "wx": ["rain", "snow"]
            })

        if len(future_ta) >= 2:
            max_drop, max_rise = 0, 0
            drop_hr, rise_hr = None, None
            drop_dur, rise_dur = 3, 3
            
            for i in range(len(future_ta)):
                t1 = future_ta[i].get("temp_c")
                if t1 is None: continue
                
                # Skanujemy okna o szerokości 1, 2 i 3 godzin
                for duration in range(1, 4):
                    if i + duration >= len(future_ta): break
                    t2 = future_ta[i + duration].get("temp_c")
                    if t2 is None: continue
                    
                    diff = t2 - t1
                    
                    if diff <= -5 and diff < max_drop:
                        max_drop = diff
                        drop_hr = int(future_ta[i]["time_local"][11:13])
                        drop_dur = duration
                    elif diff >= 5 and diff > max_rise:
                        max_rise = diff
                        rise_hr = int(future_ta[i]["time_local"][11:13])
                        rise_dur = duration

            dur_str = {1: "1 godziny", 2: "2 godzin", 3: "3 godzin"}

            if max_drop <= -5:
                candidates.append({
                    "priority": 2,
                    "text": f"Nagłe ochłodzenie! Po {drop_hr:02d}:00 odczuwalnie spadnie temperatura w ciągu {dur_str.get(drop_dur, '3 godzin')}.",
                    "category": "gradient",
                    "kind": "fast_drop",
                    "wx": ["temp_change"]
                })
            elif max_rise >= 5:
                candidates.append({
                    "priority": 2,
                    "text": f"Nagłe ocieplenie! Po {rise_hr:02d}:00 temperatura odczuwalnie wzrośnie w ciągu {dur_str.get(rise_dur, '3 godzin')}.",
                    "category": "gradient",
                    "kind": "fast_rise",
                    "wx": ["temp_change"]
                })

        if not has_precip and max_wind < 20 and temp_max is not None and 15 <= temp_max <= 25:
            best_comfort_hr = None
            for i in range(len(future_ta) - 2):
                window_slice = future_ta[i:i+3]
                w_precip = max((float(h.get("precip_mm") or 0) for h in window_slice), default=0)
                w_wind = max((float(h.get("wind_kmh") or 0) for h in window_slice), default=0)
                w_temps = [h.get("temp_c") for h in window_slice if h.get("temp_c") is not None]
                if w_precip == 0 and w_wind < 15 and w_temps:
                    avg_t = sum(w_temps) / len(w_temps)
                    if 16 <= avg_t <= 24:
                        best_comfort_hr = int(window_slice[0]["time_local"][11:13])
                        break

            if best_comfort_hr is not None:
                candidates.append({
                    "priority": 3,
                    "text": f"najlepsze, komfortowe warunki na wyjście będą po {best_comfort_hr:02d}:00",
                    "category": "comfort_window",
                    "kind": "opportunity",
                    "wx": ["window"]
                })

    if has_precip and eff_gust >= 45:
        candidates.append({
            "priority": 2,
            "text": "Wietrznie i mokro. Parasol się nie sprawdzi, załóż dobrą kurtkę!",
            "category": "wind_rain",
            "kind": "impact",
            "wx": ["rain", "wind"]
        })

    if temp_max is not None and temp_max <= 2 and max_wind >= 30:
        candidates.append({
            "priority": 3,
            "text": "Wiatr mocno potęguje chłód. Koniecznie ubierz się na cebulkę!",
            "category": "wind_cold",
            "kind": "impact",
            "wx": ["wind", "frost"]
        })

    if future_ta:
        pressures_12h = [h.get("pressure_hpa") for h in future_ta[:12] if h.get("pressure_hpa") is not None]
        if pressures_12h:
            p_now = pressures_12h[0]
            p_min = min(pressures_12h)
            if (p_now - p_min) >= 7:
                candidates.append({
                    "priority": 4,
                    "text": f"Spadek ciśnienia o ok. {round(p_now - p_min)} hPa — meteopaci mogą czuć się gorzej.",
                    "category": "pressure_drop",
                    "kind": "biomet",
                    "wx": []
                })

    if has_precip:
        window = _dry_window(blocks, is_afternoon_report)
        if window:
            ws, we, w_score, variant = window
            is_freezing = temp_min is not None and temp_min <= -3
            has_snow_visible = _has_family(blocks, "snow")
            
            woda = "śniegu" if has_snow_visible else "deszczu"
            
            if is_freezing and has_snow_visible:
                w_score += 15
                if variant == "before_storm":
                    text = f"do {we:02d}:00 bez {woda}, ale nadal zimno"
                elif variant == "evening_calm":
                    text = f"po {ws:02d}:00 bez {woda}, ale nadal zimno"
                else:
                    text = f"po {ws:02d}:00 bez {woda}, ale nadal mróz"
            else:
                if variant == "before_storm":
                    if we >= 18:
                        text = f"po {we:02d}:00 lepiej pozostać w domu"
                    else:
                        text = f"najlepiej wrócić przed {we:02d}:00"
                elif variant == "evening_calm":
                    text = f"po {ws:02d}:00 warunki na zewnątrz będą stabilniejsze"
                else:
                    first_start = blocks[0].get("start", 0) % 24 if blocks else -1
                    if ws == first_start:
                        text = f"do {we:02d}:00 utrzyma się okno bez {woda}"
                    else:
                        text = f"główne okno bez {woda} między {ws:02d}:00 a {we:02d}:00"

            if w_score < 60: prio = 5
            elif w_score < 80: prio = 6
            else: prio = 8

            candidates.append({
                "priority": prio,
                "text": text,
                "category": "window",
                "kind": "opportunity",
                "wx": ["rain", "snow"]
            })

    if not is_afternoon_report and temp_min is not None and temp_max is not None:
        amplitude = temp_max - temp_min
        if amplitude >= 10:
            candidates.append({
                "priority": 9,
                "text": f"rano nawet o {round(amplitude)}° chłodniej niż po południu",
                "category": "temp_change",
                "kind": "contrast",
                "wx": ["temp_change"]
            })

    if cold_temp is not None and cold_temp <= -3 and cold_hr is not None:
        candidates.append({
            "priority": 12, 
            "text": f"najzimniejszy moment to okolice {cold_hr:02d}:00 ({round(cold_temp)}°C)",
            "category": "extrema_time",
            "kind": "cold_peak",
            "wx": ["frost"] 
        })

    storm_hour = _storm_start(blocks)
    if storm_hour is not None:
        candidates.append({
            "priority": 10,
            "text": f"po {storm_hour:02d}:00 pogoda szybko się pogorszy",
            "category": "storm_start",
            "kind": "timing",
            "wx": ["storm"]
        })

    if len(blocks) >= 3:
        last = blocks[-1]
        last_events = last.get("events", [])
        last_start = last.get("start", 0)
        last_end = last.get("end", 0)
        last_hours = last_end - last_start
        if last_hours <= 0: last_hours += 24
        mid_events = blocks[1].get("events", []) if len(blocks) > 1 else []
        is_evening = 16 <= last_start <= 23
        if (is_evening and len(mid_events) > len(last_events) and len(last_events) == 0 and last_hours >= 3):
            candidates.append({
                "priority": 11,
                "text": "wieczór spokojniejszy niż reszta dnia",
                "category": "contrast",
                "kind": "evening_better",
                "wx": ["rain", "storm"]
            })

    if is_afternoon_report and tomorrow_morning_events:
        has_tom_snow = any(FAMILY_MAP.get(_ev(e, "kind", "")) == "snow" for e in tomorrow_morning_events)
        if has_tom_snow:
            candidates.append({
                "priority": 13,
                "text": "jutro rano możliwe opady śniegu",
                "category": "tomorrow",
                "kind": "forecast",
                "wx": ["snow"]
            })

    # G. Pozytywne ciekawostki na spokojne dni
    if not has_precip and max_wind < 40 and future_ta:
        daylight = []
        for h in future_ta:
            try:
                hh = int(h.get("time_local", "")[11:13])
                if 8 <= hh <= 16:
                    daylight.append((hh, _eff_cld_consensus(h)))
            except Exception: pass
            
        if len(daylight) >= 4:
            avg_day_clouds = sum(c for _, c in daylight) / len(daylight)
            if avg_day_clouds >= 40:
                sun_h, sun_c = min(daylight, key=lambda x: x[1])
                if sun_c < 45:
                    s_hr = sun_h
                    if 8 <= s_hr <= 16:
                        if s_hr == current_hour:
                            text = "najwięcej słońca będzie w najbliższych godzinach"
                        else:
                            text = f"najwięcej słońca zapowiada się w okolicach {s_hr:02d}:00"
                    else:
                        text = f"najmniej chmur około {s_hr:02d}:00"
                        
                    candidates.append({
                        "priority": 14,
                        "text": text,
                        "category": "sun",
                        "kind": "opportunity",
                        "wx": []
                    })
                    
    if warm_temp is not None and warm_hr is not None:
        if 8 <= warm_hr <= 18 and warm_temp >= 5:
            candidates.append({
                "priority": 15,
                "text": f"najcieplejszy moment dnia to okolice {warm_hr:02d}:00 ({round(warm_temp)}°C)",
                "category": "temperature",
                "kind": "warm_peak",
                "wx": []
            })

    return candidates
        
# ═══════════════════════════════════════
# KANDYDACI — POZIOM 2 (warunkowi)
# ═══════════════════════════════════════

def _candidates_level2(blocks, temp_min, max_wind, gust_kmh, total_precip_mm):
    candidates = []

    storm_hour = _storm_start(blocks)
    if storm_hour is not None:
        candidates.append({
            "priority": 20,
            "text": f"po {storm_hour:02d}:00 pogoda szybko się pogorszy",
            "category": "storm",
            "kind": "impact",
            "wx": "storm"
        })

    if temp_min is not None and -4 <= temp_min <= 2:
        candidates.append({
            "priority": 21,
            "text": "możliwy przymrozek — uważaj na śliskie nawierzchnie",
            "category": "frost",
            "kind": "impact",
            "wx": "frost"
        })

    if _has_family(blocks, "fog", indices=[0]):
        candidates.append({
            "priority": 22,
            "text": "rano gorsza widoczność przez mgłę",
            "category": "fog",
            "kind": "impact",
            "wx": "fog"
        })

    eff_gust = gust_kmh or max_wind
    if eff_gust > 55:
        candidates.append({
            "priority": 23,
            "text": f"możliwe silniejsze porywy wiatru do {round(eff_gust)} km/h",
            "category": "wind",
            "kind": "strength",
            "wx": "wind"
        })

    if total_precip_mm >= 5:
        woda = "śniegu" if temp_min is not None and temp_min <= 2 else "deszczu"
        candidates.append({
            "priority": 24,
            "text": f"dziś może spaść łącznie ok. {round(total_precip_mm)} mm {woda}",
            "category": "precip_sum",
            "kind": "amount",
            "wx": "rain"
        })

    return candidates


# ═══════════════════════════════════════
# SCORING + DEDUPE
# ═══════════════════════════════════════

def _validate_candidate(
    c: Dict,
    current_hour: int,
    is_afternoon_report: bool,
    visible_categories: Set[str],
) -> bool:
    text = (c.get("text") or "").lower()

    if not text.strip():
        return False

    if len(text) > MAX_WK_LEN * 2:
        return False

    if is_afternoon_report and "rano" in text:
        return False

    m = re.search(r'(po|przed|do)\s+(\d{1,2}):00', text)
    if m:
        hh = int(m.group(2))
        if hh < current_hour and hh != 0: 
            return False

    wx = c.get("wx")
    wx_tags = []
    if isinstance(wx, str):
        wx_tags = [wx]
    elif wx:
        wx_tags = list(wx)
        
    if wx_tags and any(tag in visible_categories for tag in wx_tags):
        # Dopuszczamy duplikaty tagów TYLKO dla wartościowych i konkretnych kategorii,
        # czyli okien pogodowych, zmian temperatur, dokładnego czasu apogeum zjawiska (peak_impact)
        # oraz informacji o przyszłości (jutro / kolejne dni).
        allowed_dupe_categories = (
            "window", "temp_change", "peak_impact", "tomorrow",
            "future_wind", "future_pressure", "future_warming", "future_cooling",
            "storm_risk", "uv_alert", "dewpoint_alert"
        )
        if c.get("category") not in allowed_dupe_categories:
            return False

    if "słońc" in text and current_hour >= 17:
        return False

    return True


SOFT_PENALTY = 10
VISIBLE_PENALTY = 8

def _score(candidate, alert_signals, alert_categories, visible_categories):
    wx = candidate.get("wx")
    kind = candidate.get("kind")

    wx_tags = []
    if isinstance(wx, str):
        wx_tags = [wx]
    elif isinstance(wx, list):
        wx_tags = wx

    if not wx_tags:
        return candidate["priority"]

    for tag in wx_tags:
        if (tag, kind) in alert_signals:
            return float("inf")

    score = candidate["priority"]
    
    if any(tag in alert_categories for tag in wx_tags):
        score += SOFT_PENALTY
        
    if any(tag in visible_categories for tag in wx_tags):
        score += VISIBLE_PENALTY

    return score

def _candidates_future(ta: list) -> list:
    candidates = []
    if not ta:
        return candidates

    try:
        first_dt = datetime.fromisoformat(ta[0]["time_local"].replace("Z", "+00:00"))
    except:
        return candidates

    future_start = first_dt + timedelta(days=2)
    future_end = first_dt + timedelta(days=5)

    future_data = [
        h for h in ta 
        if h.get("time_local") and future_start <= datetime.fromisoformat(h["time_local"].replace("Z", "+00:00")) <= future_end
    ]

    if not future_data:
        return candidates

    dni_tygodnia = ["w poniedziałek", "we wtorek", "w środę", "w czwartek", "w piątek", "w sobotę", "w niedzielę"]

    max_gust = max((h.get("gust_kmh") or 0 for h in future_data), default=0)
    if max_gust >= 60:
        gust_hour = next(h for h in future_data if (h.get("gust_kmh") or 0) == max_gust)
        dt_gust = datetime.fromisoformat(gust_hour["time_local"].replace("Z", "+00:00"))
        dzien_str = dni_tygodnia[dt_gust.weekday()]
        
        candidates.append({
            "wx": "wind",
            "category": "future_wind",
            "kind": "strength",
            "priority": 15,
            "text": f"Wstępne prognozy wskazują silny wiatr. Największe porywy (ok. {round(max_gust)} km/h) {dzien_str}."
        })

    pressures = [h.get("pressure_hpa") for h in future_data if h.get("pressure_hpa") is not None]
    if pressures:
        min_p = min(pressures)
        max_p = max(pressures)
        if (max_p - min_p) >= 12 and min_p < 1000:
            min_p_hour = next(h for h in future_data if h.get("pressure_hpa") == min_p)
            dt_press = datetime.fromisoformat(min_p_hour["time_local"].replace("Z", "+00:00"))
            dzien_str_p = dni_tygodnia[dt_press.weekday()]
            
            candidates.append({
                "wx": "storm",
                "category": "future_pressure",
                "kind": "impact",
                "priority": 20,
                "text": f"Sygnał nadejścia niżu. {dzien_str_p.capitalize()} ciśnienie może spaść do ok. {round(min_p)} hPa."
            })

    try:
        current_max_temp = max(h.get("temp_c", -99) for h in ta[:48])
        future_max_temp = max(h.get("temp_c", -99) for h in future_data)
        
        if future_max_temp > -90 and current_max_temp > -90:
            if future_max_temp - current_max_temp >= 8:
                warm_hour = next(h for h in future_data if h.get("temp_c") == future_max_temp)
                dt_warm = datetime.fromisoformat(warm_hour["time_local"].replace("Z", "+00:00"))
                dzien_str_w = dni_tygodnia[dt_warm.weekday()]
                candidates.append({
                    "wx": "air",
                    "category": "future_warming",
                    "kind": "contrast",
                    "priority": 25,
                    "text": f"Szykuje się spore ocieplenie. Termometry mogą pokazać nawet {round(future_max_temp)}°C ({dzien_str_w})."
                })
            elif current_max_temp - future_max_temp >= 8:
                cold_hour = min((h for h in future_data), key=lambda x: x.get("temp_c", 99))
                dt_cold = datetime.fromisoformat(cold_hour["time_local"].replace("Z", "+00:00"))
                dzien_str_c = dni_tygodnia[dt_cold.weekday()]
                candidates.append({
                    "wx": "air",
                    "category": "future_cooling",
                    "kind": "contrast",
                    "priority": 25,
                    "text": f"Przed nami ochłodzenie. {dzien_str_c.capitalize()} temperatura w dzień może spaść do {round(cold_hour.get('temp_c', 0))}°C."
                })
    except Exception:
        pass

    return candidates


HAZARD_WX = {"rain", "snow", "storm", "wind", "frost", "fog"}

# Te kategorie są “ważne” nawet jeśli wx jest puste (biomet/duchota/ciśnienie itp.)
PRIORITY_EXEMPT_CATEGORIES = {
    "storm_risk",
    "peak_impact",
    "wind_rain",
    "wind_cold",
    "pressure_drop",
    "dewpoint_alert",
    "gradient",
    "window",
    "storm_start",
    "tomorrow",
    "future_wind",
    "future_pressure",
    "future_warming",
    "future_cooling",
    "precip_sum",
}

# Kategorie stricte lifestyle/miękkie — nie chcemy żeby miały priorytet < 14
LIFESTYLE_CATEGORIES = {
    "sun",
    "temperature",
    "comfort_window",
    "uv_alert",
    "extrema_time",
    "contrast",
}

def _wx_tags(c: dict) -> list[str]:
    wx = c.get("wx")
    if isinstance(wx, str):
        return [wx]
    if isinstance(wx, list):
        return [str(x) for x in wx]
    return []

def enforce_priority_policy(c: dict) -> dict:
    pr = int(c.get("priority", 99) or 99)
    cat = str(c.get("category") or "")
    wx_tags = set(_wx_tags(c))

    # 1) lifestyle zawsze >= 14
    if cat in LIFESTYLE_CATEGORIES:
        pr = max(pr, 14)
    else:
        # 2) jeśli brak hazard wx i nie jest “ważnym wyjątkiem” => też >= 14
        has_hazard = any(t in HAZARD_WX for t in wx_tags)
        if (not has_hazard) and (cat not in PRIORITY_EXEMPT_CATEGORIES):
            pr = max(pr, 14)

    c["priority"] = pr
    return c


# ═══════════════════════════════════════
# GŁÓWNA FUNKCJA
# ═══════════════════════════════════════

def build_worth_knowing(
    payload: dict,  # <--- To jest kluczowe!
    blocks: List[Dict],
    alerts: List[str] = None,
    temp_min: float = None,
    temp_max: float = None,
    max_wind: float = 0,
    gust_kmh: float = None,
    total_precip_mm: float = 0,
    is_afternoon_report: bool = False,
    tomorrow_morning_events: List[Dict] = None,
    summary_line: str = "",
    context_line_text: str = "",
    built_blocks: List[Dict] = None,
    ta: List[Dict] = None,
    current_hour: int = 0,
) -> Optional[Dict]:
    if alerts is None: alerts = []
    if built_blocks is None: built_blocks = []

    alert_signals: Set[Tuple] = set()
    alert_categories: Set[str] = set()
    for a in alerts:
        cat, kind = parse_alert_signal(a)
        if cat:
            alert_signals.add((cat, kind))
            alert_categories.add(cat)

    visible_categories = _extract_visible_categories(built_blocks, summary_line, context_line_text or "")
    
    # --- NOWY TRUST GATING ---
    trust_block = False
    
    # [POPRAWKA 1]: Odcinamy lifestylowe pierdoły, jeśli padł jeden model
    forecast_source = payload.get("forecast_source", "")
    has_two_models = " + " in forecast_source
    if not has_two_models:
        trust_block = True
    
    # Jeśli karta główna już ostrzega o niepewności, WK milknie (spójność systemu)
    ctx_low = (context_line_text or "").lower()
    if "modele są rozbieżne" in ctx_low or "wczesny raport" in ctx_low:
        trust_block = True
        
    if os.environ.get("WK_TRUST_GATING", "1") == "1" and not alerts and not trust_block:
        # 1) niepewne niebo (rozjazd modeli)
        if _uncertain_sky(ta or []):
            trust_block = True

        # 2) niepewne opady (wysoki POP, ale brak mm)
        if ta:
            pop_peak = max((_f(h.get("precip_prob_pct"), 0) for h in ta), default=0)
            precip_peak = max((_f(h.get("precip_mm"), 0) for h in ta), default=0)
            if pop_peak >= 60 and precip_peak < 0.2:
                trust_block = True
    # --------------------------
    # [POPRAWKA 1b]: Awaryjny zwrot, gdy mamy tylko 1 model
    if not has_two_models:
        return {
            "title": "Dziś warto wiedzieć",
            "text": "Brak weryfikacji z drugiego modelu. Prognoza może ulec zmianie. Wygeneruj ponownie za kilka minut.",
        }

    candidates = _candidates_level1(
        blocks, temp_min, temp_max, total_precip_mm,
        is_afternoon_report, tomorrow_morning_events, max_wind, gust_kmh, ta, current_hour
    )
    candidates += _candidates_level2(blocks, temp_min, max_wind, gust_kmh, total_precip_mm)
    
    if ta:
        candidates += _candidates_future(ta)
        
    candidates = [enforce_priority_policy(c) for c in candidates]

    if not candidates:
        return None

    scored = []
    for c in candidates:
        s = _score(c, alert_signals, alert_categories, visible_categories)
        if s < float("inf"):
            scored.append((s, c))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0])
    
    # --- TOP-K + WALIDATOR ---
    winner = None
    winner_score = None
    for s, c in scored[:TOP_K]:
        if _validate_candidate(c, current_hour, is_afternoon_report, visible_categories):
            winner = c
            winner_score = s
            break

    # Bezpieczny fallback
    if winner is None:
        return None
        
    # --- Trust gating jako filtr stylu (nie zabijamy twardych wskazówek) ---
    if trust_block:
        # 1) lifestyle zawsze milczy w dni niepewne
        if winner.get("category") in LIFESTYLE_CATEGORIES:
            return None

        # 2) jeśli brak hazard wx i nie jest wyjątkiem -> milczymy
        wx = winner.get("wx")
        if isinstance(wx, str):
            wx_tags = {wx}
        elif isinstance(wx, list):
            wx_tags = set(wx)
        else:
            wx_tags = set()

        has_hazard = any(t in HAZARD_WX for t in wx_tags)

        if (not has_hazard) and (winner.get("category") not in PRIORITY_EXEMPT_CATEGORIES):
            return None

    
    # ==============================================================
    # WSPARCIE AI (KREATYWNY KANDYDAT NA NUDNE DNI)
    # ==============================================================
    
    if os.environ.get("AI_DEBUG", "0") == "1":
        print(f"[WK] trust_block={trust_block}, alerts={len(alerts) if alerts else 0}, winner_cat={winner.get('category')} prio={winner.get('priority')}")
    
    # AI uruchamiamy TYLKO gdy dzień jest “pewny”
    #if os.environ.get("ENABLE_WK_AI_CANDIDATE", "0") == "1":
    if (not trust_block) and os.environ.get("ENABLE_WK_AI_CANDIDATE", "0") == "1":
        BORING_PRIORITY_THRESHOLD = 12
        BORING_CATEGORIES = {"sun", "temperature", "generic"}

        if (winner.get("priority", 999) >= BORING_PRIORITY_THRESHOLD) or (winner.get("category") in BORING_CATEGORIES):
            try:
                from ai_client import wk_candidate_from_facts
                
                facts = _build_wk_facts(
                    ta=ta or [], blocks=blocks or [], current_hour=current_hour,
                    is_afternoon_report=is_afternoon_report, temp_min=temp_min,
                    temp_max=temp_max, total_precip_mm=total_precip_mm,
                    max_wind=max_wind, gust_kmh=gust_kmh
                )
                
                # GATING 1: Blokada przy alertach
                if alerts:
                    raise ValueError("Aktywne alerty - odcinam AI")

                # GATING 2: Blokada przy dynamicznej pogodzie
                precip_peak = float(facts.get("precip_peak_mm_h") or 0)
                pop_peak = int(facts.get("pop_peak_pct") or 0)
                gust_peak = float(facts.get("gust_peak_kmh") or 0)
                drop3 = float(facts.get("temp_drop_3h_c") or 0)
                rise3 = float(facts.get("temp_rise_3h_c") or 0)

                if not (precip_peak < 0.1 and pop_peak < 40 and gust_peak < 45 and drop3 < 5 and rise3 < 5):
                    raise ValueError("Fakty wskazują na dynamiczną pogodę - odcinam AI")

                # GATING 3: WYBÓR TRYBU I WYMAGANYCH KOTWIC (Actionability)
                mode = None
                required_hours: set[int] = set()

                if isinstance(facts.get("comfort_hour"), int):
                    mode = "comfort"
                    required_hours = {facts["comfort_hour"]}
                elif isinstance(facts.get("sunniest_hour"), int):
                    mode = "sun"
                    required_hours = {facts["sunniest_hour"]}
                elif isinstance(facts.get("window_start"), int) and isinstance(facts.get("window_end"), int):
                    # ZMIANA: Obsługa okna przez północ - obcinamy koniec!
                    if facts.get("window_crosses_midnight"):
                        mode = "window_start_only"
                        required_hours = {facts["window_start"]}
                    else:
                        mode = "window"
                        required_hours = {facts["window_start"], facts["window_end"]}

                if mode is None:
                    raise ValueError("Brak konkretu (kotwicy). Odcinam AI, żeby nie produkowało pustych ogólników.")
                
                if os.environ.get("AI_DEBUG") == "1":
                    print(f"[AI] Dzień spokojny. Znalazłem kotwicę '{mode}'. Wołam LLM...")
                    
                ai_obj = wk_candidate_from_facts(facts, MAX_WK_LEN, mode=mode)

                if ai_obj:
                    ai_text = (ai_obj.get("text") or "").strip()
                    ai_wx = ai_obj.get("wx") or []
                    ai_kind = (ai_obj.get("kind") or "generic").strip()

                    if not ai_wx:
                        low = ai_text.lower()
                        if "wiatr" in low or "poryw" in low: ai_wx = ["wind"]
                        elif "deszcz" in low or "mżawk" in low: ai_wx = ["rain"]
                        elif "śnieg" in low: ai_wx = ["snow"]
                        elif "burz" in low: ai_wx = ["storm"]
                        elif "mgł" in low: ai_wx = ["fog"]

                    allowed_tags = {"wind", "rain", "snow", "storm", "fog", "temp_change", "window"}
                    ai_wx = [t for t in ai_wx if t in allowed_tags]

                    # TARCZA SŁOWNA
                    def _contains_any(text: str, words: list[str]) -> bool:
                        t = (text or "").lower()
                        return any(w in t for w in words)
                        
                    if precip_peak < 0.1 and pop_peak < 40:
                        if _contains_any(ai_text, ["deszcz", "mżawk", "ulew", "śnieg", "burz", "grad", "nawałn"]):
                            raise ValueError("AI zmyśliło opady/burzę bez pokrycia w liczbach")
                            
                    if gust_peak < 45:
                        if _contains_any(ai_text, ["wichur", "poryw", "silny wiatr"]):
                            raise ValueError("AI zmyśliło wichurę bez pokrycia w liczbach")

                    if "słoń" in ai_text.lower() and "sunniest_hour" not in facts:
                        raise ValueError("AI zmyśliło słońce bez pokrycia w faktach")

                    # TWARDA WALIDACJA SŁÓW ZAKAZANYCH
                    if "jutro" in ai_text.lower() or "jutra" in ai_text.lower():
                        raise ValueError("AI użyło słowa 'jutro' (zakazane w lifestylowym AI WK)")

                    # WALIDACJA TWARDA (LICZBY I GODZINY ZGODNE Z KOTWICĄ)
                    allowed_nums = _num_tokens(json.dumps(facts, ensure_ascii=False))
                    ai_hours = _hour_tokens(ai_text)
                    godziny_ok = True

                    # AI może użyć TYLKO tych godzin, których wymaga tryb!
                    if mode in ("comfort", "sun", "window_start_only"):  # <--- Dodany nowy tryb
                        if not ai_hours.issuperset(required_hours):
                            if os.environ.get("AI_DEBUG") == "1": print(f"[AI] Brak wymaganej godziny: {required_hours}")
                            godziny_ok = False
                        if not ai_hours.issubset(required_hours):
                            if os.environ.get("AI_DEBUG") == "1": print(f"[AI] Dodano wymyśloną godzinę! Wymagano tylko: {required_hours}")
                            godziny_ok = False
                    elif mode == "window":
                        if not required_hours.issubset(ai_hours):
                            if os.environ.get("AI_DEBUG") == "1": print(f"[AI] Brak wymaganego zakresu: {required_hours}")
                            godziny_ok = False
                        if not ai_hours.issubset(required_hours):
                            if os.environ.get("AI_DEBUG") == "1": print(f"[AI] Dodano wymyśloną godzinę obok okna: {required_hours}")
                            godziny_ok = False

                    if godziny_ok and _num_tokens(ai_text).issubset(allowed_nums):
                        ai_candidate = {
                            "priority": max(6, int(winner.get("priority", 12)) - 1), 
                            "text": ai_text,
                            "category": "ai",
                            "kind": ai_kind,
                            "wx": ai_wx,
                        }

                        if _validate_candidate(ai_candidate, current_hour, is_afternoon_report, visible_categories):
                            ai_score = _score(ai_candidate, alert_signals, alert_categories, visible_categories)
                            if winner_score is None or ai_score < winner_score:
                                if os.environ.get("AI_DEBUG") == "1":
                                    print(f"[AI] SUKCES! Wniosek AI wstawiony na kartę: {ai_text}")
                                winner = ai_candidate
                                winner_score = ai_score
                            else:
                                if os.environ.get("AI_DEBUG") == "1":
                                    print("[AI] AI napisało dobrze, ale przegrało z ważniejszym faktem.")
                    else:
                        if os.environ.get("AI_DEBUG") == "1":
                            print(f"[AI] ODRZUCONO! Nieudana walidacja liczb/godzin. AI: {ai_text}")
            except Exception as e:
                if os.environ.get("AI_DEBUG") == "1":
                    print(f"[AI] {e}")
    # ==============================================================

    final_text = winner["text"]
    
    
        
    if winner.get("category") == "ai":
        final_text = f"[AI] {final_text}"

    if len(final_text) > MAX_WK_LEN + 5:
        truncated = final_text[:MAX_WK_LEN].rsplit(' ', 1)[0]
        final_text = truncated + "…"

    

    return {
        "title": "Dziś warto wiedzieć",
        "text": final_text,
    }