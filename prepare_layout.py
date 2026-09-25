"""
prepare_layout.py — Produkcyjny builder layoutu karty pogodowej
Funkcja: prepare_layout_data(payload, now=None)
"""

import os
import re
import math
import statistics
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from forecast_text import WxEvent, BlockForecast, build_block_copy, classify_precip, sky_from_clouds, SKY_RANK, KINDS
from worth_knowing import build_worth_knowing
from confidence_gate import compute_trust_report
from ui_softening import strip_mm_pct_parens, soften_possible_prefix
from i18n import t, DAYS_FULL, DAYS_SHORT, translate_weather_text
from owm_nowcast import get_current_weather, nowcast_note

# ═══════════════════════════════════════
# STAŁE
# ═══════════════════════════════════════

DNI_PL    = ["Poniedziałek", "Wtorek", "Środa", "Czwartek",
             "Piątek", "Sobota", "Niedziela"]
DNI_SHORT = ["Pn", "Wt", "Śr", "Czw", "Pt", "Sob", "Nd"]

WK_ICON_FALLBACK = {
    "wk_clear":         "sun",
    "wk_clear_one_cloude": "sun",
    "wk_mostly_sunny":  "sun",
    "wk_mostly_cloudy": "cloud",
    "wk_overcast":      "cloud",
    "wk_drizzle":       "rain",
    "wk_light_rain":    "rain",
    "wk_showers":       "rain",
    "wk_heavy_rain":    "rain",
    "wk_light_snow":    "snow",
    "wk_snow":          "snow",
    "wk_storm":         "storm",
    "wk_fog":           "cloud",
    "wk_wind":          "wind",
}


# ═══════════════════════════════════════
# HELPERY POGODOWE
# ═══════════════════════════════════════

def _hour_in_block(hh: int, start: int, end: int) -> bool:
    """Sprawdza czy godzina hh zawiera się w bloku [start, end), z obsługą przejścia przez północ."""
    if start < end:
        return start <= hh < end
    return (hh >= start) or (hh < end)

def _is_night_from_symbol_or_time(h: dict) -> bool:
    sym = (h.get("symbol_code") or h.get("symbol_code_eff") or "").lower()
    if "_night" in sym:
        return True
    if "_day" in sym:
        return False
    try:
        dt = datetime.fromisoformat(h["time_local"].replace("Z", "+00:00"))
        return dt.hour < 6 or dt.hour >= 20
    except Exception:
        return False


def _drizzle_hint(ta: list, hp_all: list, start_hour: int) -> Optional[str]:
    """
    Miękka podpowiedź: możliwe pojedyncze krople mimo 0.0 mm w modelu.
    Umiarkowane progi + warunek 2 kolejnych godzin, żeby nie spamować.
    """
    if not ta or not hp_all:
        return None

    # patrzymy lekko wstecz (2h), żeby user o 13:40 mógł dostać hint,
    # jeśli "siąpiło już od 12"
    lookback_start = max(5, int(start_hour or 6) - 2)

    hours = []
    for h in ta:
        hh = _hour_safe(h.get("time_local", ""))
        if hh is None:
            continue

        # tylko dzień
        if hh < lookback_start or hh > 18:
            continue

        # modele nie widzą twardego opadu
        mm = _precip_consensus(h, hp_all)
        if mm >= 0.1:
            continue

        # beton chmur (efektywne)
        cld = _eff_cld_consensus(h)
        if cld < 98:
            continue

        # umiarkowana wilgotność + niezbyt duża różnica t-dp
        rh = float(h.get("rh_pct") or 0)
        if rh < 70:
            continue

        t_raw = h.get("temp_c")
        dp_raw = h.get("dewpoint_c")
        if t_raw is None or dp_raw is None:
            continue
        t_val = float(t_raw); dp_val = float(dp_raw)
        if (t_val - dp_val) > 6.5:
            continue

        # spokojny wiatr (żeby nie łapać byle pochmurnego dnia z wiatrem)
        wind = max(
            float(h.get("wind_kmh") or 0),
            float(h.get("gust_kmh") or h.get("wind_gust_kmh") or 0),
        )
        if wind > 15:
            continue

        hours.append(hh)

    if not hours:
        return None

    # min. 2 kolejne godziny
    hs = sorted(set(hours))
    run = 1
    best = 1
    for i in range(1, len(hs)):
        if hs[i] == hs[i - 1] + 1:
            run += 1
            best = max(best, run)
        else:
            run = 1
        
    main_rain_hours = []
    for h in ta:
        hh = _hour_safe(h.get("time_local", ""))
        if hh is None:
            continue
        if hh < (start_hour or 6):
            continue
        if _precip_consensus(h, hp_all) >= 0.2:
            main_rain_hours.append(hh)

    has_main_rain_later = bool(main_rain_hours)
    if best >= 2:
        return "Przed zapowiadanym deszczem może siąpić." if has_main_rain_later else "Możliwe lekkie siąpienie."
    return None


def _precip_consensus(h: dict, hp_all: list) -> float:
    """Inteligentny konsensus: nie bierze Max, tylko sprawdza prawdopodobieństwo (POP)."""
    p_base = float(h.get("precip_eff_mm", h.get("precip_mm")) or 0)
    pop_base = float(h.get("precip_prob_pct", 0))
    
    t_loc = h.get("time_local")
    if not t_loc or not hp_all: return p_base
        
    # Szukamy tego samego czasu w drugim modelu
    alt_source = "yrno" if h.get("source") == "openmeteo" else "openmeteo"
    h_alt = next((y for y in hp_all if y.get("time_local") == t_loc and y.get("source") == alt_source), None)
    
    if not h_alt: return p_base
    
    p_alt = float(h_alt.get("precip_eff_mm", h_alt.get("precip_mm")) or 0)
    pop_alt = float(h_alt.get("precip_prob_pct", 0))
    
    # LOGIKA KONSENSUSU:
    # 1. Jeśli oba widzą deszcz -> średnia (realistyczne).
    if p_base > 0 and p_alt > 0: return (p_base + p_alt) / 2
    # 2. Jeśli jeden widzi deszcz, a drugi nie -> weryfikujemy POP (jeśli POP < 30%, to "duch").
    if p_base > 0 and p_alt == 0: return p_base if pop_base > 30 else 0
    if p_alt > 0 and p_base == 0: return p_alt if pop_alt > 30 else 0
        
    return 0.0


def _eff_cld(h: dict) -> float:
    """Kalkulator efektywnego zachmurzenia, ignorujący wysokie chmury (cirrusy)."""
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
    base = _eff_cld(h)  # Chmury z Open-Meteo
    alt = _eff_cld_alt(h.get("clouds_low_pct_yr"), h.get("clouds_mid_pct_yr"), h.get("clouds_pct_yr")) # Chmury z Yr.no
    return max(base, alt) # Bierzemy bardziej pesymistyczną wartość


def _feels_like(temp_c, wind_kmh, rh_pct=None) -> Optional[float]:
    if temp_c is None:
        return None
    wind = float(wind_kmh or 0)
    if temp_c <= 10.0 and wind >= 4.8:
        v = wind ** 0.16
        return round(13.12 + 0.6215 * temp_c - 11.37 * v + 0.3965 * temp_c * v, 1)
    if temp_c >= 20.0 and rh_pct is not None:
        e = (rh_pct / 100.0) * 6.105 * math.exp((17.27 * temp_c) / (237.7 + temp_c))
        hx = temp_c + (5.0 / 9.0) * (e - 10.0)
        return round(max(temp_c, hx), 1)
    return round(temp_c, 1)


def _choose_icon(clouds: float, precip: float,
                 temp: float = 10, wind: float = 0, events: list = None, hour_hint: int = 12) -> str:
    
    is_night = hour_hint < 6 or hour_hint >= 20

    # 1. Zgodność z tekstem (absolutny priorytet)
    if events:
        kinds = [ev.kind for ev in events]
        if any("storm" in k for k in kinds): return "wk_storm"
        if any("sleet" in k for k in kinds): return "wk_sleet"
        if any("snow" in k for k in kinds): 
            if float(clouds or 0) < 70:
                return "wk_snow_showers_night" if is_night else "wk_snow_showers"
            return "wk_snow"
        
        rain_kinds = {"light_rain", "rain", "heavy_rain", "downpour"}
        if any(k in rain_kinds for k in kinds):
            if float(clouds or 0) < 70:
                return "wk_showers_night" if is_night else "wk_showers"
            return "wk_rain"
        if "drizzle" in kinds: return "wk_drizzle"
        if "fog" in kinds: return "wk_fog"

    # 2. Fallback surowych wartości
    precip = float(precip or 0)
    clouds = float(clouds or 0)
    wind   = float(wind or 0)
    temp   = float(temp) if temp is not None else 10

    if precip > 0.1:
        if temp <= 2: 
            if clouds < 70:
                return "wk_snow_showers_night" if is_night else "wk_snow_showers"
            return "wk_snow"
        if precip <= 0.5: return "wk_drizzle"
        
        if clouds < 70:
            return "wk_showers_night" if is_night else "wk_showers"
        return "wk_rain"
        
    if wind > 60:
        return "wk_wind"
        
    # 3. Synchronizacja z ujednoliconym systemem w forecast_text
    return sky_from_clouds(clouds, is_night)[1]


def _fmt_temp(t_min: int, t_max: int) -> str:
    return f"{t_min}°/{t_max}°"


def _determine_report_type(hour: int) -> str:
    if hour < 12:
        return "raport poranny"
    if hour < 18:
        return "raport popołudniowy"
    return "aktualizacja wieczorna"


def _hour_safe(t_loc: str) -> Optional[int]:
    try:
        return datetime.fromisoformat(t_loc.replace("Z", "+00:00")).hour
    except Exception:
        return None

def _hour(h: dict) -> int:
    return datetime.fromisoformat(h["time_local"].replace("Z", "+00:00")).hour


def _format_single_hours(text: str) -> str:
    if not text: return text
    
    # Bezpieczny regex dla 'od/po/ok.' (Doda zera do "silniej po 19" -> "silniej po 19:00")
    t_val = re.sub(
        r'(?<!\w)(od|po|ok\.)\s+([0-1]?[0-9]|2[0-4])(?!\d)(?!\s*km/h)(?!\s*°)(?!\s*mm)(?!\s*%)', 
        r'\1 \2:00', 
        text, 
        flags=re.IGNORECASE
    )
    
    # Bezpieczny regex dla 'do' (Ignoruje słowa o wietrze, by nie zrobić "wiatr do 32:00")
    t_val = re.sub(
        r'(?<!\w)(?<!wiatr )(?<!wichura )(?<!porywy )(?<!ok\. )(do)\s+([0-1]?[0-9]|2[0-4])(?!\d)(?!\s*km/h)(?!\s*°)(?!\s*mm)(?!\s*%)', 
        r'\1 \2:00', 
        t_val, 
        flags=re.IGNORECASE
    )
    return t_val

def _ensure_kmh(text: str) -> str:
    if not text: return text
    # Wymusza dopisanie km/h do siły wiatru, jeśli inny system o nim zapomniał!
    return re.sub(
        r'(wiatr|wichura|porywy)(.*?\bdo\s+\d+)(?!\d)(?!\s*km/h)', 
        r'\1\2 km/h', 
        text, 
        flags=re.IGNORECASE
    )


# ═══════════════════════════════════════
# BUDOWA BLOKÓW — GRANICE: start <= hour < end
# ═══════════════════════════════════════

def _select_block_hours(hp: list, date_str: str, next_date_str: str,
                        start: int, end: int, label: str) -> list:
    """
    Wybiera godziny dla bloku z ujednoliconą konwencją start <= hour < end.
    """
    if label == "Jutro rano":
        return [h for h in hp
                if h.get("time_local", "").startswith(next_date_str)
                and start <= _hour(h) < end]

    if start < end:
        return [h for h in hp
                if h.get("time_local", "").startswith(date_str)
                and start <= _hour(h) < end]

    # Blok przez północ (np. 22–06)
    return [
        h for h in hp
        if (h.get("time_local", "").startswith(date_str)
            and _hour(h) >= start)
        or (h.get("time_local", "").startswith(next_date_str)
            and _hour(h) < end)
    ]


DRIZZLE_MAX_MM = 0.5

from typing import Optional

def _coerce_drizzle(kind: Optional[str], mm: float) -> Optional[str]:
    if not kind or mm is None:
        return kind
    try:
        mm = float(mm)
    except Exception:
        return kind
    
    fam = KINDS.get(kind, {}).get("family")
    if fam == "rain" and 0 < mm <= DRIZZLE_MAX_MM:
        return "drizzle"
    return kind

def _build_wx_events(block_hours: list, hp_all: list = None) -> list:
    events = []
    for h in block_hours:
        mm = _precip_consensus(h, hp_all)
        temp = h.get("temp_c", 10)
        hr = _hour(h)
        kind = classify_precip(
            mm, temp,
            symbol_code=h.get("symbol_code_eff", h.get("symbol_code")),
            weather_code=h.get("weather_code_eff", h.get("weather_code"))
        )
        
        # ZGODNOŚĆ Z /NOW: wymuszamy mżawkę dla drobnych opadów
        kind = _coerce_drizzle(kind, mm)
        
        if kind:
            events.append(WxEvent(kind, hr, hr + 1))
    return events


def _build_time_blocks(hp: list, date_str: str, next_date_str: str, block_defs: list, hp_all: list = None, lang: str = "pl", cld_overrides: Optional[dict] = None) -> list:
    blocks = []
    for bd in block_defs:
        label = bd["label"]
        s, e  = bd["start"], bd["end"]

        bh = _select_block_hours(hp, date_str, next_date_str, s, e, label)
        if not bh:
            continue
        temps = [h["temp_c"] for h in bh if h.get("temp_c") is not None]
        if not temps:
            continue

        t_min = round(min(temps))
        t_max = round(max(temps))
        
        # --- 1. INTELIGENTNE CHMURY (Pełna synchronizacja ikony i tekstu) ---
        cld_eff_list = []
        
        for h in bh:
            tloc = h.get("time_local", "")
            # Zaszczepienie OWM jeśli dostępne dla tej godziny, w przeciwnym razie model
            if cld_overrides and tloc in cld_overrides:
                cld_eff = float(cld_overrides[tloc])
            else:
                cld_eff = _eff_cld_consensus(h)
                
            cld_eff_list.append(cld_eff)
            
        if cld_eff_list:
            median_cld = float(statistics.median(cld_eff_list))
            mid_h = bh[len(bh) // 2]
            is_block_night = _is_night_from_symbol_or_time(mid_h)
            dominant_sky_pl, icon_name = sky_from_clouds(median_cld, is_block_night)
        else:
            dominant_sky_pl = "Pochmurno"
            median_cld = 100.0

        

        # --- OPADY: spójnie z _build_day_summary (prawdziwy konsensus z hp_all) ---
        p_vals = [_precip_consensus(h, hp_all or hp) for h in bh]
        tot_p = sum(p_vals) if p_vals else 0.0
        max_p = max(p_vals + [0.0])
        max_w = max([float(h.get("wind_gust_kmh") or h.get("gust_kmh") or 0) for h in bh] + [0])
        
        evs = _build_wx_events(bh, hp_all=hp_all)
        
        icon  = _choose_icon(median_cld, max_p, (t_min + t_max) / 2, wind=max_w, events=evs, hour_hint=s)

        fv = [_feels_like(h.get("temp_c"), h.get("wind_kmh"), h.get("rh_pct"))
              for h in bh]
        fv = [f for f in fv if f is not None]
        f_min = round(min(fv)) if fv else None
        f_max = round(max(fv)) if fv else None

        # Wyświetla przetłumaczoną etykietę (Noc/Night) tylko dla bloku 22-06, reszta to godziny
        display_label = label if (s == 22 and e == 6) else f"{s:02d}–{e:02d}"
        
        # Płaski tekst bazowy po polsku dla Ostatniej Mili
        sky = dominant_sky_pl if max_p < 0.1 else None

        # --- 3. TWORZENIE BLOKU Z WIATREM ---
        bf = BlockForecast(
            label=display_label, hours_range=f"{s:02d}–{e:02d}",
            start=s, end=e,
            temp_min=t_min, temp_max=t_max,
            feels_min=f_min, feels_max=f_max,
            sky_label=sky,
            max_wind=max_w,
            events=evs,
        )
        copy = build_block_copy(bf, lang=lang, inline_max_chars=48, meta_max_chars=32)

        # NOWOŚĆ: Twarde wymuszenie wiatru w bloku (zgodność z /now)
        max_eff_w = max([max(float(h.get("wind_kmh") or 0), float(h.get("gust_kmh") or h.get("wind_gust_kmh") or 0)) for h in bh], default=0)
        
        if max_eff_w >= 40:
            if max_eff_w >= 100: wind_label = "potężna wichura"
            elif max_eff_w >= 80: wind_label = "wichura"
            elif max_eff_w >= 60: wind_label = "silny wiatr"
            else: wind_label = "wietrznie"
            desc_lower = copy["primary_desc"].lower()
            
            extra_texts = []
            for ex in copy.get("extra_lines", []):
                if isinstance(ex, dict): extra_texts.append(ex.get("text", ""))
                else: extra_texts.append(str(ex))
            extras_lower = " ".join(extra_texts).lower()
            
            combined_text = desc_lower + " " + extras_lower
            
            if "wiatr" not in combined_text and "wichur" not in combined_text:
                copy["primary_desc"] += f" · {wind_label} do {round(max_eff_w)} km/h"

        # --- APLIKUJEMY FORMATOWANIE POJEDYNCZYCH GODZIN I JEDNOSTEK ---
        copy["primary_desc"] = _format_single_hours(_ensure_kmh(copy["primary_desc"]))
        if "extra_lines" in copy:
            for i, ex in enumerate(copy["extra_lines"]):
                if isinstance(ex, dict):
                    if "text" in ex:
                        ex["text"] = _format_single_hours(_ensure_kmh(ex["text"]))
                    if "spans" in ex:
                        for span in ex["spans"]:
                            if "text" in span:
                                span["text"] = _format_single_hours(_ensure_kmh(span["text"]))
                elif isinstance(ex, str):
                    copy["extra_lines"][i] = _format_single_hours(_ensure_kmh(ex))

        # --- INTELIGENTNE ŁAMANIE LINII (Zabezpieczenie przed ucinaniem) ---
        if len(copy["primary_desc"]) > 32 and " · " in copy["primary_desc"]:
            parts = copy["primary_desc"].split(" · ", 1)
            copy["primary_desc"] = parts[0]
            
            if "extra_lines" not in copy:
                copy["extra_lines"] = []
            copy["extra_lines"].insert(0, {"text": parts[1]})

        blocks.append({
            "label":        display_label,
            "hours":        f"{s:02d}–{e:02d}",
            "icon":         icon,
            "temp_range":   _fmt_temp(t_min, t_max),
            "primary_desc": copy["primary_desc"],
            "extra_lines":  copy["extra_lines"],
        })
    return blocks


# ═══════════════════════════════════════
# KRÓTKIE OPISY DNI (sekcja next_days)
# ═══════════════════════════════════════

def _build_day_summary(hp: list, date_str: str, is_night_mode: bool = False) -> Optional[dict]:
    dh = [h for h in hp if h.get("time_local", "").startswith(date_str)]
    if not dh: return None

    temps = [h["temp_c"] for h in dh if h.get("temp_c") is not None]
    if not temps: return None

    d_min = round(min(temps))
    d_max = round(max(temps))

    # --- 1. Skanowanie Aktywnego Dnia (06:00 - 22:00) ---
    day_temps = []
    has_rain_m = False; has_rain_a = False
    has_snow_m = False; has_snow_a = False
    has_storm_m = False; has_storm_a = False
    has_fog = False
    has_real_rain = False
    has_drizzle = False

    cld_eff_list = []
    sky_labels_pl = []
    max_wind = 0

    rain_hours = []
    snow_hours = []
    storm_hours = []
    
    max_pop = 0 

    for h in dh:
        t_loc = h.get("time_local", "")
        if len(t_loc) < 16: continue
        hour = _hour_safe(t_loc)

        if hour is None:
            continue
        
        pop = float(h.get("precip_prob_pct", h.get("pop_pct", h.get("pop", 0))))
        if pop > max_pop: 
            max_pop = pop

        if 6 <= hour <= 20 and h.get("temp_c") is not None:
            day_temps.append(h["temp_c"])

        if 6 <= hour < 22:
            is_morning = hour < 14
            max_wind = max(max_wind, float(h.get("wind_gust_kmh") or h.get("gust_kmh") or 0))

            cld_eff = _eff_cld_consensus(h)
            cld_eff_list.append(cld_eff)
            label_pl, _ = sky_from_clouds(cld_eff, is_night=False)
            sky_labels_pl.append(label_pl)

            precip = _precip_consensus(h, hp)
            code = str(h.get("symbol_code_eff", h.get("symbol_code")) or "").lower()
            w_code = h.get("weather_code_eff", h.get("weather_code"))
            temp_opadu = h.get("temp_c") if h.get("temp_c") is not None else 10

            if "fog" in code or (w_code in [41,42,43,44,45,46,47,48,49]):
                has_fog = True

            # === PANCERNY BEZPIECZNIK OPADÓW ===
            precip = _precip_consensus(h, hp) 
            
            is_snow = False
            is_rain = False
            is_storm = False
            
            if precip > 0:
                kind = classify_precip(precip, temp_opadu, symbol_code=code, weather_code=w_code)
                kind = _coerce_drizzle(kind, precip)
                
                fam = KINDS.get(kind, {}).get("family")
                if fam == "rain":
                    is_rain = True
                    if kind == "drizzle": 
                        has_drizzle = True
                    else: 
                        has_real_rain = True
                elif fam == "snow":
                    is_snow = True
                elif fam == "mixed":
                    is_snow = True
                    is_rain = True
                elif fam == "storm":
                    is_storm = True

                if is_snow:
                    snow_hours.append(hour)
                    if is_morning: has_snow_m = True
                    else: has_snow_a = True
                
                if is_rain:
                    rain_hours.append(hour)
                    if is_morning: has_rain_m = True
                    else: has_rain_a = True
                
                if is_storm:
                    storm_hours.append(hour)
                    if is_morning: has_storm_m = True
                    else: has_storm_a = True

    if sky_labels_pl:
        c = Counter(sky_labels_pl)
        top_count = c.most_common(1)[0][1]
        cands = [lab for lab, cnt in c.items() if cnt == top_count]
        dominant_sky_pl = max(cands, key=lambda x: SKY_RANK.get(x, 99))
        median_cld = float(statistics.median(cld_eff_list))
    else:
        dominant_sky_pl = "Pochmurno"
        median_cld = 100.0

    # Reguła pożerania
    rain_word = "deszcz" if has_real_rain else ("mżawka" if has_drizzle else "deszcz")

    # --- 2. Odznaka Opadów ---
    badge = None
    def group_hours(hours_list):
        if not hours_list: return []
        hours_list = sorted(set(hours_list))
        ranges, st, pv = [], hours_list[0], hours_list[0]
        for hr in hours_list[1:]:
            if hr == pv + 1: pv = hr
            else: ranges.append((st, pv + 1)); st = hr; pv = hr
        ranges.append((st, pv + 1))
        return ranges

    if storm_hours:
        rng = group_hours(storm_hours)
        badge = "burze " + ", ".join(f"{a:02d}–{b:02d}" for a, b in rng) if len(rng) <= 2 else "przelotne burze"
    elif snow_hours and rain_hours:
        badge = "śnieg z deszczem" 
    elif snow_hours:
        rng = group_hours(snow_hours)
        if len(rng) == 1 and rng[0][0] <= 9 and rng[0][1] >= 20:
            badge = "śnieżnie"
        else:
            badge = "śnieg " + ", ".join(f"{a:02d}–{b:02d}" for a, b in rng) if len(rng) <= 2 else "przelotny śnieg"
    elif rain_hours:
        rng = group_hours(rain_hours)
        if len(rng) == 1 and rng[0][0] <= 9 and rng[0][1] >= 20:
            badge = "deszczowo" if rain_word == "deszcz" else "ciągła mżawka"
        else:
            fallback = "przelotna mżawka" if rain_word == "mżawka" else "przelotny deszcz"
            badge = f"{rain_word} " + ", ".join(f"{a:02d}–{b:02d}" for a, b in rng) if len(rng) <= 2 else fallback

    # --- 3. Drabinka Priorytetów ---
    has_rain = has_rain_m or has_rain_a
    has_snow = has_snow_m or has_snow_a
    has_storm = has_storm_m or has_storm_a

    has_sun = SKY_RANK.get(dominant_sky_pl, 4) <= 2
    has_heavy_clouds = SKY_RANK.get(dominant_sky_pl, 4) >= 3

    max_dzien = round(max(day_temps)) if day_temps else d_max
    temp_anomaly = (d_max - max_dzien >= 4)

    descriptor = ""
    icon = "wk_clear"

    # --- Anti-drizzle domination (ikona dnia) ---
    drizzle_minor = False
    if has_rain and has_drizzle and not has_real_rain:
        drizzle_hours = sorted(set(rain_hours))
        total_mm = 0.0
        max_mm = 0.0
        for h in dh:
            hh = _hour_safe(h.get("time_local", ""))
            if hh in drizzle_hours:
                mm = float(h.get("precip_eff_mm", h.get("precip_mm")) or 0.0)
                total_mm += mm
                if mm > max_mm: max_mm = mm
                
        # "krótko i słabo" => mżawka nie zmienia ikony całego dnia
        if len(drizzle_hours) < 2 and total_mm < 1.0 and max_mm < 0.7:
            drizzle_minor = True

    if temp_anomaly:
        descriptor = f"W dzień tylko {max_dzien}°C"
        icon = "wk_overcast" if has_heavy_clouds else "wk_partlycloudy"
    elif max_wind >= 60:
        descriptor = ""
        icon = "wk_wind"
        badge = f"wiatr do {round(max_wind)} km/h"
    elif has_snow and has_rain:
        descriptor = "Śnieg, potem deszcz" if rain_hours and snow_hours and max(rain_hours) > min(snow_hours) else "Deszcz ze śniegiem"
        icon = "wk_sleet"
    elif has_storm:
        if has_sun:
            descriptor = "Rano mgły, po poł. burze" if has_fog else ("Słonecznie, po poł. burze" if has_storm_a and not has_storm_m else "Przelotne burze")
            icon = "wk_sun_storm"
        else:
            descriptor = "Rano mgły, po poł. burze" if has_fog and not has_storm_m else "Burze"
            icon = "wk_storm"
            
    elif has_snow:
        if has_fog:
            if has_sun:
                descriptor = "Rano mgły, w dzień przelotny śnieg"
            elif has_snow_a and not has_snow_m:
                descriptor = "Rano mgły, po poł. śnieg"
            else:
                descriptor = "Mglisto i śnieżnie"
            icon = "wk_snow_showers" if has_sun else ("wk_snow" if has_snow_m and has_snow_a else "wk_light_snow")
        else:
            if has_sun:
                descriptor = "Słońce i przelotny śnieg"
                icon = "wk_snow_showers"
            else:
                descriptor = "Śnieg" if has_snow_m and has_snow_a else ("Rano śnieg" if has_snow_m else "Po południu śnieg")
                icon = "wk_snow" if has_snow_m and has_snow_a else "wk_light_snow"
                
    elif has_rain:
        is_drizzle = has_drizzle and not has_real_rain 
        
        if has_fog:
            if has_sun:
                descriptor = "Rano mgły, potem przel. mżawka" if is_drizzle else "Rano mgły, potem przel. deszcz"
            elif has_rain_a and not has_rain_m:
                descriptor = "Rano mgły, po poł. mżawka" if is_drizzle else "Rano mgły, po poł. deszcz"
            else:
                descriptor = "Mgły i mżawka" if is_drizzle else "Mgły i deszcz"
                
            icon = "wk_drizzle" if is_drizzle else ("wk_rain" if has_rain_m and has_rain_a else "wk_showers")
        else:
            if has_sun:
                descriptor = "Słońce i przelotna mżawka" if is_drizzle else "Słońce i przelotny deszcz"
                icon = "wk_drizzle" if is_drizzle else "wk_showers"
            else:
                if is_drizzle:
                    descriptor = "Mżawka" if has_rain_m and has_rain_a else ("Rano mżawka" if has_rain_m else "Po poł. mżawka")
                    icon = "wk_drizzle"
                else:
                    descriptor = "Deszcz" if has_rain_m and has_rain_a else ("Rano deszcz" if has_rain_m else "Po południu deszcz")
                    icon = "wk_rain" if has_rain_m and has_rain_a else "wk_showers"
                    
        # --- Zastosowanie override dla "minor drizzle" ---
        if is_drizzle and drizzle_minor:
            # Zostawiamy descriptor z informacją o mżawce, ale ikonę bierzemy z dominującego nieba
            _, icon = sky_from_clouds(median_cld, is_night_mode)
            
    elif has_fog and not has_heavy_clouds:
        if SKY_RANK.get(dominant_sky_pl, 0) <= 1: # Bezchmurnie, Słonecznie, Pogodnie
            descriptor = "Rano mgły, w dzień słońce"
        elif SKY_RANK.get(dominant_sky_pl, 0) == 2: # Przejaśnienia
            descriptor = "Rano mgły, przejaśnienia"
        else:
            descriptor = "Rano mgły, dużo chmur"
        icon = "wk_fog"
    else:
        
        # ŻELAZNA DRABINKA CHMUR (Zsynchronizowana)
        # Bierzemy opis i ikonę bezpośrednio z jednego matematycznego źródła
        label_med, icon_med = sky_from_clouds(median_cld, is_night_mode)
        descriptor = label_med
        icon = icon_med

    if badge and len(descriptor) > 15 and ("Rano" in descriptor or "Po południu" in descriptor):
        descriptor = descriptor.replace("Rano ", "").replace("Po południu ", "").capitalize()

    pop_val = int(max_pop)
    pop_str = f" ({pop_val}%)" if pop_val > 0 else ""

    pop_for_badge = pop_str
    if badge:
        lowb = badge.lower()
        is_precip_badge = any(w in lowb for w in ["deszcz", "mżawk", "śnieg", "burz", "opad"])
        if not is_precip_badge:
            pop_for_badge = ""   

    # --- BLOKADA FIZYCZNA
    is_daytime_precip_strictly = (has_rain_m or has_rain_a or has_snow_m or has_snow_a or has_storm_m or has_storm_a)
    if is_daytime_precip_strictly and (badge or pop_val >= 40) and SKY_RANK.get(dominant_sky_pl, 0) < 2:
        descriptor = "Przejaśnienia"

    # --- IKONA JEST SZEFEM & NOCNE OPADY ---
    is_daytime_precip = has_rain or has_snow or has_storm

    if badge:
        if "wiatr" not in badge:
            badge = badge.replace("przelotne ", "przel. ").replace("przelotny ", "przel. ")
            if is_daytime_precip:
                badge = f"{badge[0].upper()}{badge[1:]}{pop_for_badge}"
            else:
                if "mżawka" in badge: badge = "nocna mżawka"
                elif "śnieg z deszczem" in badge: badge = "nocny deszcz ze śniegiem"
                elif "burz" in badge: badge = "nocne burze"
                elif "śnieg" in badge: badge = "nocny śnieg"
                else: badge = "nocny deszcz"
                
                badge = f"{_smart_cap(badge)}{pop_for_badge}"
        else:
            badge += pop_for_badge 
    else:
        if not descriptor:
            descriptor = dominant_sky_pl

    # --- APLIKUJEMY FORMATOWANIE POJEDYNCZYCH GODZIN I JEDNOSTEK ---
    if badge: badge = _format_single_hours(_ensure_kmh(badge))
    if descriptor: descriptor = _format_single_hours(_ensure_kmh(descriptor))

    # --- SEMANTYKA ZAGROŻEŃ (Wielopoziomowa, niezależna od języka) ---
    if has_storm or max_wind >= 80:
        severity = "alert"
    elif max_wind >= 60:
        severity = "caution"
    # Tutaj w przyszłości można dodać: elif has_freezing_rain: severity = "alert" itp.
    else:
        severity = "normal"

    return {
        "icon": icon, "temp_min": d_min, "temp_max": d_max,
        "precip_badge": badge, "descriptor": descriptor,
        "severity": severity
    }

# ═══════════════════════════════════════
# WEEKEND TEASER
# ═══════════════════════════════════════

def _build_weekend_day_teaser(hp: list, day_short: str, payload: dict = None) -> Optional[dict]:
    if not hp: return None
    
    date_str = hp[0].get("time_local", "")[:10]
    if len(date_str) < 10: return None
    
    date_short_formatted = f"{date_str[8:10]}.{date_str[5:7]}"
    
    summary = _build_day_summary(hp, date_str)
    if not summary:
        return None

    desc = summary.get("precip_badge") or summary.get("descriptor") or "Brak danych"
    desc = desc[0].upper() + desc[1:] if desc else ""
    
    return {
        "label": day_short,
        "date_short": date_short_formatted,
        "icon": summary["icon"],
        "temp_min": summary["temp_min"],
        "temp_max": summary["temp_max"],
        "desc": desc,
        "severity": summary.get("severity", "normal") # Przekazanie znacznika semantycznego na frontend!
    }


# ═══════════════════════════════════════
# BLOKI CZASU — definicje
# ═══════════════════════════════════════

def _get_time_blocks(hour: int) -> Tuple[str, list]:
    if hour < 12:
        return "Prognoza na dziś", [
            {"label": "Rano",       "start": 6,  "end": 10},
            {"label": "Popołudnie", "start": 11,   "end": 16},
            {"label": "Wieczór",    "start": 17,   "end": 22},
        ]
    if hour < 18:
        blocks = []
        if hour <= 14:
            blocks.append({"label": "Popołudnie", "start": hour, "end": 16})  
            blocks.append({"label": "Wieczór",    "start": 17,   "end": 22}) 
        else:
            blocks.append({"label": "Późne popoł.", "start": hour, "end": 18}) 
            blocks.append({"label": "Wieczór",      "start": 19,   "end": 22}) 
            
        blocks.append({"label": "Noc", "start": 22, "end": 6})
        return "Reszta dnia", blocks
        
    blocks = []
    if hour < 22:
        blocks.append({"label": "Wieczór", "start": hour, "end": 22})
        
    blocks.append({"label": "Noc",        "start": 22,   "end": 6})
    blocks.append({"label": "Jutro rano", "start": 6,    "end": 10})
    
    return "Najbliższe godziny", blocks


# ═══════════════════════════════════════
# GŁÓWNA FUNKCJA (Z KIEROWNIKIEM RUCHU)
# ═══════════════════════════════════════

def _smart_cap(val: str) -> str:
    if not val or not isinstance(val, str):
        return val
    for i, char in enumerate(val):
        if char.isalpha():
            return val[:i] + char.upper() + val[i+1:]
    return val


def prepare_layout_data(payload, now=None): 
    if os.environ.get("DEBUG_PAYLOAD_JSON") == "1":
        import json
        with open("debug_pogoda.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            
    tz  = ZoneInfo(payload["location"]["tz"])
    now = now or datetime.now(tz)
    
    raw_lang = str(payload.get("lang", "pl")).strip().lower()
    lang = raw_lang[:2] 

    today_str    = now.strftime("%Y-%m-%d")
    tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    hours = payload.get("hours", [])
    ho = [h for h in hours if h.get("source") == "openmeteo"]
    hy = [h for h in hours if h.get("source") == "yrno"]
    hp = ho if ho else hy
    
    trust_report = compute_trust_report(
        ho=ho,
        hy=hy,
        today_str=today_str,
        current_hour=now.hour,
        lang=lang
    )

    # === DATOWANIE ŹRÓDŁA DANYCH I AGE-GATING ===
    is_morning_report = (now.hour < 12)
    is_night_run = False
    model_time_str = payload.get("model_updated_at_local")
    
    if model_time_str and len(model_time_str) >= 16:
        data_time = model_time_str[11:16]
        time_suffix = f" ({t(lang, 'data_from')} {data_time})"
        
        try:
            model_dt_loc = datetime.fromisoformat(model_time_str.replace("Z", "+00:00")).astimezone(tz)
            is_night_run = (model_dt_loc.hour < 6)
        except Exception:
            pass
    else:
        time_suffix = ""

    # === NAZWA RAPORTU ===
    if payload.get("is_now"):
        base_title = t(lang, "tactical_radar")
    elif payload.get("is_future"):
        base_title = t(lang, "long_range")
    else:
        rt_pl = _determine_report_type(now.hour)
        base_title = t(lang, {
            "raport poranny": "report_morning",
            "raport popołudniowy": "report_afternoon",
            "aktualizacja wieczorna": "report_evening"
        }.get(rt_pl, rt_pl))

    report_type = f"{base_title}{time_suffix}"
    
    weekday      = DAYS_FULL.get(lang, DAYS_FULL["pl"])[now.weekday()]
    date_display = now.strftime("%d.%m")

    ta = [h for h in hp if h.get("time_local", "").startswith(today_str)]
    tc = [_eff_cld_consensus(h) for h in ta]
    tp = sum(_precip_consensus(h, hours) for h in ta)
    ac = sum(tc) / len(tc) if tc else 0

    max_wind = max((float(h.get("wind_kmh") or 0) for h in ta), default=0)
    max_gust = max((float(h.get("gust_kmh") or 0) for h in ta), default=0) or None

    alerts = list(payload.get("alerts", []))
    ar = payload.get("airly")
    if ar and ar.get("caqi") is not None:
        caqi = ar["caqi"]
        if caqi > 75:
            alerts.append("Zła jakość powietrza — normy zanieczyszczeń są przekroczone")

    # === DETEKTOR GOŁOLEDZI I MARZNĄCYCH OPADÓW (24h) ===
    FREEZING_MM_MIN = 0.05
    FREEZING_TEMP_C = 0.8
    FREEZING_RH_MIN = 88
    FREEZING_DP_MAX = 1.0

    def _freezing_risk(h_dict):
        t_val = float(h_dict.get("temp_c") if h_dict.get("temp_c") is not None else 99)
        rh = float(h_dict.get("rh_pct") or 0)
        dp = h_dict.get("dewpoint_c")
        dp = float(dp) if dp is not None else None
        mm = float(h_dict.get("precip_eff_mm", h_dict.get("precip_mm")) or 0.0)
        
        if mm <= FREEZING_MM_MIN: return False
        kind_val = classify_precip(
            mm, t_val,
            symbol_code=h_dict.get("symbol_code_eff", h_dict.get("symbol_code")),
            weather_code=h_dict.get("weather_code_eff", h_dict.get("weather_code"))
        )
        if kind_val in {"freezing_drizzle", "freezing_rain"}: return True
        fam = KINDS.get(kind_val, {}).get("family")
        if fam == "rain" and t_val <= FREEZING_TEMP_C:
            if (dp is not None and dp <= FREEZING_DP_MAX) or (rh >= FREEZING_RH_MIN): return True
        return False

    all_hours = payload.get("hours", [])
    scan_limit = now + timedelta(hours=24)
    risk_dts = []

    for h in all_hours:
        try:
            dt_val = datetime.fromisoformat(h["time_local"].replace("Z", "+00:00")).astimezone(tz)
        except Exception:
            continue
        if now <= dt_val <= scan_limit:
            if _freezing_risk(h):
                risk_dts.append(dt_val)

    if risk_dts:
        def fmt_rng(a, b):
            end = b + timedelta(hours=1)
            end_h = end.hour
            if end_h == 0 and end.date() != a.date():
                end_h = 24
            prefix = "jutro " if a.date() > now.date() else ""
            return f"{prefix}{a.hour:02d}–{end_h:02d}"

        def group_hourly_datetimes(dts):
            dts = sorted(list(set(dts)))
            rngs, st, pv = [], dts[0], dts[0]
            for dt in dts[1:]:
                diff = (dt - pv).total_seconds()
                if 3500 <= diff <= 3700: pv = dt
                else: rngs.append((st, pv)); st = dt; pv = dt
            rngs.append((st, pv))
            return rngs
            
        rng = group_hourly_datetimes(risk_dts)
        when = ", ".join(fmt_rng(a, b) for a, b in rng[:2])
        alert_msg = t(lang, "freezing_alert") if t(lang, "freezing_alert") != "freezing_alert" else "⚠️ Marznące opady: ryzyko gołoledzi"
        alerts.insert(0, f"{alert_msg} ({when}).")

    section_title, block_defs = _get_time_blocks(now.hour)
    section_title = t(lang, {
        "Prognoza na dziś": "section_today",
        "Reszta dnia": "section_rest_day",
        "Najbliższe godziny": "section_next_hours"
    }.get(section_title, section_title))

    for bd in block_defs:
        lbl = bd.get("label", "")
        bd["label"] = t(lang, {
            "Rano": "blk_morning",
            "Popołudnie": "blk_afternoon",
            "Późne popoł.": "blk_afternoon", 
            "Wieczór": "blk_evening",
            "Noc": "blk_night",
            "Jutro rano": "blk_tomorrow_morning",
        }.get(lbl, lbl))

    # --- WYLICZANIE BMIN / BMAX PRZED WYWOŁANIEM OWM ---
    all_block_temps = []
    for bd in block_defs:
        _bh = _select_block_hours(hp, today_str, tomorrow_str, bd["start"], bd["end"], bd["label"])
        all_block_temps.extend([h["temp_c"] for h in _bh if h.get("temp_c") is not None])
        
    bmin = round(min(all_block_temps)) if all_block_temps else 0
    bmax = round(max(all_block_temps)) if all_block_temps else 0

    # ── KIEROWNIK RUCHU (Tryb Weekendowy) ──
    dow = now.weekday()
    summary_offsets = []
    wdd_offsets = []
    future_order = []
    show_teaser = False

    if dow == 3: # Czwartek
        summary_offsets = [1]       
        wdd_offsets = [2, 3]        
        future_order = ["summary", "detail", "detail"]
    elif dow == 4: # Piątek
        summary_offsets = [3]       
        wdd_offsets = [1, 2]        
        future_order = ["detail", "detail", "summary"]
    elif dow == 5: # Sobota
        summary_offsets = [2, 3]    
        wdd_offsets = [1]           
        future_order = ["detail", "summary"]
    elif dow == 6: # Niedziela
        summary_offsets = [1, 2, 3] 
        show_teaser = True
    elif dow == 0: # Poniedziałek
        summary_offsets = [1, 2, 3] 
        show_teaser = True
    elif dow == 1: # Wtorek
        summary_offsets = [1, 2, 3] 
        show_teaser = True
    elif dow == 2: # Środa
        summary_offsets = [1, 2]    
        show_teaser = True

    from daily_source import pick_hours_for_daily_summary
    daily_diag_dict = payload.get("daily_diag", {})

    future_sections = []
    for off in summary_offsets:
        tgt = now + timedelta(days=off)
        ts  = tgt.strftime("%Y-%m-%d")
        
        pick = pick_hours_for_daily_summary(hours, daily_diag_dict, ts)
        base = _build_day_summary(pick.hp, ts, is_night_mode=False)
            
        haz = _build_day_summary(ho, ts, is_night_mode=False) if ho else None
        
        extra_note = None
        if base and haz:
            haz_icon = haz.get("icon") or ""
            if haz_icon in ("wk_wind", "wk_storm", "wk_snow", "wk_sleet"):
                extra_note = haz.get("precip_badge") or haz.get("descriptor")
                
        if base:
            if extra_note:
                base_badge = base.get("precip_badge") or ""
                base_desc = base.get("descriptor") or ""
                
                if extra_note not in base_badge and extra_note not in base_desc:
                    if base_badge:
                        base["precip_badge"] = f"{base_badge} · {extra_note}"
                    elif base_desc:
                        base["descriptor"] = f"{base_desc} · {extra_note}"
                    else:
                        base["descriptor"] = extra_note

            future_sections.append({
                "type": "summary", "date": tgt,
                "name":      DAYS_SHORT.get(lang, DAYS_SHORT["pl"])[tgt.weekday()],
                "name_full": f"{DAYS_FULL.get(lang, DAYS_FULL['pl'])[tgt.weekday()]}, {tgt.strftime('%d.%m')}",
                **base,
            })
            
    future_sections.sort(key=lambda x: x["date"])
    nd  = []
    for fs in future_sections:
        d = {k: v for k, v in fs.items() if k not in ("type", "date")}
        d["_date"] = fs["date"]
        nd.append(d)

    FULL_DAY_BLOCKS = [
        {"label": t(lang, "blk_morning"),   "start": 6,  "end": 10},
        {"label": t(lang, "blk_afternoon"), "start": 11, "end": 16},
        {"label": t(lang, "blk_evening"),   "start": 17, "end": 22},
        {"label": t(lang, "blk_night"),     "start": 22, "end": 6}
    ]
    wdd = []
    for off in wdd_offsets:
        tgt = now + timedelta(days=off)
        ts = tgt.strftime("%Y-%m-%d")
        ts_next = (tgt + timedelta(days=1)).strftime("%Y-%m-%d")
        blocks = _build_time_blocks(hp, ts, ts_next, FULL_DAY_BLOCKS, hp_all=hours)
        if blocks:
            wdd.append({
                "name": f"{DAYS_FULL.get(lang, DAYS_FULL['pl'])[tgt.weekday()]}, {tgt.strftime('%d.%m')}",
                "blocks": blocks
            })

    tomorrow_date = (now + timedelta(days=1)).date()
    if len(nd) == 1:
        the_date = nd[0]["_date"]
        the_date = the_date.date() if hasattr(the_date, "date") else the_date
        if the_date == tomorrow_date:
            next_days_title = t(lang, "tomorrow")
        else:
            next_days_title = nd[0].get("name_full", nd[0].get("name", ""))
        nd[0]["label"] = "00–24"
        nd[0]["name"]  = ""
    else:
        next_days_title = t(lang, "next_days")

    for d in nd:
        d.pop("_date", None); d.pop("name_full", None)

    hero_start_hour = 6 if now.hour < 12 else now.hour
    
    hp_hero = [h for h in hp if h.get("time_local", "")[:10] > today_str or (h.get("time_local", "")[:10] == today_str and _hour(h) >= hero_start_hour)]
    
    hero_is_night = False
    if hp_hero:
        current_sym = (hp_hero[0].get("symbol_code") or "").lower()
        if "_night" in current_sym:
            hero_is_night = True
        elif "_day" in current_sym:
            hero_is_night = False
        else:
            hero_is_night = now.hour >= 20 or now.hour < 6
    else:
        hero_is_night = now.hour >= 20 or now.hour < 6

    day_hero = _build_day_summary(hp_hero, today_str, is_night_mode=hero_is_night)
    if not day_hero:
        day_hero = _build_day_summary(hp_hero, tomorrow_str, is_night_mode=False)
        
    if day_hero:
        hero_icon = day_hero["icon"]
        b_badge = day_hero.get("precip_badge")
        b_desc = day_hero.get("descriptor")
        if b_badge:
            base_desc = b_badge[0].upper() + b_badge[1:]
        else:
            base_desc = b_desc[0].upper() + b_desc[1:] if b_desc else ""
    else:
        if ac < 50:
            hero_icon = "wk_moon_one_cloud" if hero_is_night else "wk_sun_one_cloud"
            base_desc = "Pogodnie" if hero_is_night else "Słonecznie"
        else:
            hero_icon = "wk_overcast"
            base_desc = "Pochmurno"
        
    if base_desc.lower() == "bezchmurnie" and 6 <= now.hour < 20:
        if ac <= 10.0 and tp == 0 and max_wind < 30:
            base_desc = "Bezchmurnie, pogoda jak kryształ"

    current_h = next(
        (h for h in hp 
         if h.get("time_local", "").startswith(today_str) 
         and _hour(h) == now.hour 
         and h.get("pressure_hpa") is not None), 
        None
    )
    pressure_hpa = current_h["pressure_hpa"] if current_h else None
    
    pressure_trend = None
    if pressure_hpa:
        future_time = now + timedelta(hours=12)
        fut_date_str = future_time.strftime("%Y-%m-%d")
        future_h = next((h for h in hp if _hour(h) == future_time.hour and h.get("time_local", "").startswith(fut_date_str)), None)
        if future_h and future_h.get("pressure_hpa") is not None:
            pressure_trend = future_h["pressure_hpa"] - pressure_hpa

    line1 = base_desc
    line2_parts = []
    
    if trust_report.soften_hero_language:
        low = (line1 or "").lower()
        if any(w in low for w in ["wiatr", "wichur", "poryw"]):
            pass
        elif any(w in low for w in ["deszcz", "mżawk", "ulew", "burz", "śnieg", "opad"]):
            line1 = "Niestabilna aura, możliwe opady"
        elif any(w in low for w in ["słonecz", "bezchmurn", "pogodnie"]):
            line1 = "Niepewna prognoza zachmurzenia"
        else:
            line1 = "Niepewna prognoza"
    
    future_ta_hero = [h for h in ta if int(h.get("time_local", "T00:")[11:13]) >= hero_start_hour]
    
    eff_winds_hero = [max(float(h.get("wind_kmh") or 0), float(h.get("gust_kmh") or h.get("wind_gust_kmh") or 0)) for h in future_ta_hero]
    max_eff_wind = max(eff_winds_hero, default=0)
    
    if "wiatr" not in line1.lower() and "wichur" not in line1.lower():
        if max_eff_wind >= 100: line2_parts.append("potężna wichura")
        elif max_eff_wind >= 80: line2_parts.append("wichura")
        elif max_eff_wind >= 60: line2_parts.append("silny wiatr")
        
    if pressure_hpa:
        arrow = ""
        if pressure_trend is not None:
            if pressure_trend > 2: arrow = " ↗"
            elif pressure_trend < -2: arrow = " ↘"
        line2_parts.append(f"{round(pressure_hpa)} hPa{arrow}")

    line2 = " · ".join(line2_parts)
    hero_summary_line = f"{line1}\n{line2}" if line2_parts else line1

    hero_synoptic = None
    if pressure_trend is not None:
        if pressure_trend <= -6: hero_synoptic = "Gwałtowny spadek ciśnienia"
        elif pressure_trend <= -3: hero_synoptic = "Spadek ciśnienia"
        elif pressure_trend >= 6: hero_synoptic = "Gwałtowny wzrost ciśnienia"
        elif pressure_trend >= 3: hero_synoptic = "Wzrost ciśnienia"

    hero_blocks = []
    for bd in block_defs:
        s, e = bd["start"], bd["end"]
        bh = _select_block_hours(hp, today_str, tomorrow_str, s, e, bd["label"])
        evs = _build_wx_events(bh, hp_all=hours)
        hero_blocks.append({
            "start": s, "end": e,
            "events": [{"kind": ev.kind, "start": ev.start, "end": ev.end} for ev in evs],
        })

    day_temps = [h["temp_c"] for h in ta if 6 <= _hour(h) <= 20 and h.get("temp_c") is not None]
    max_dzien = round(max(day_temps)) if day_temps else bmax
    
    anomaly_text = None
    if (bmax - max_dzien) >= 3:
        anomaly_text = f"! Dziś maks. temp. {bmax}°C w nocy. W dzień najwyżej {max_dzien}°C"

    agreement = payload.get("model_agreement") or {}
    agreement_note = agreement.get("note")
    
    if trust_report.is_volatile and not agreement_note:
        agreement_note = trust_report.note
    
    if anomaly_text:
        final_context_line = anomaly_text
        if agreement_note: alerts.append(agreement_note)
    elif agreement_note:
        final_context_line = agreement_note
    else:
        final_context_line = hero_synoptic  

    is_dynamic = (tp >= 1.0) or (max_wind >= 45) or ((max_gust or 0) >= 60)
    show_age_note = is_morning_report and is_night_run and (trust_report.is_volatile or is_dynamic)

    if show_age_note:
        final_context_line = "Nocne dane — odśwież prognozę z menu później"

    forecast_source = payload.get("forecast_source", "")
    if " + " not in forecast_source:
        alerts.insert(0, "Awaria źródeł — Brak weryfikacji prognozy z drugiego modelu. Możliwe błędy w dzisiejszej prognozie. Wywołaj raport za chwilę ( z menu - opcja /day ).")

    alerts = list(dict.fromkeys(alerts))

    # ==================================================================
    # Wiatr od morza (Tryb Sztorm vs Plaża)
    # ==================================================================
    try:
        from coast_runtime import GLOBAL_COAST_STORE, ensure_coast_index
        if GLOBAL_COAST_STORE and ensure_coast_index:
            from coast_detector import get_or_compute_coast_signature_lazy, get_coastal_alert_mode
            
            loc_lat = payload.get("location", {}).get("lat")
            loc_lon = payload.get("location", {}).get("lon")
            tz_str = payload.get("location", {}).get("tz", "UTC")
            
            if loc_lat is not None and loc_lon is not None:
                sig = get_or_compute_coast_signature_lazy(
                    store=GLOBAL_COAST_STORE, lat=loc_lat, lon=loc_lon, idx_factory=ensure_coast_index
                )
                
                beach_hours = []
                has_storm = False
                
                for h in ta:
                    try:
                        dt_local = datetime.fromisoformat(h.get("time_local", "").replace("Z", "+00:00"))
                        hh = dt_local.hour
                        
                        wdir = float(h.get("wind_dir_deg", 0))
                        wspd = float(h.get("wind_kmh", 0))
                        wgst = float(h.get("gust_kmh", h.get("wind_gust_kmh", 0)))
                        
                        mode = get_coastal_alert_mode(sig, wspd, wgst, wdir, tz_str, dt_local)
                        
                        if mode == "storm":
                            has_storm = True
                        elif mode == "beach" and 8 <= hh <= 18:
                            beach_hours.append(hh)
                                
                    except Exception:
                        pass
                
                if has_storm:
                    alerts.append("Wybrzeże — Sztormowy wiatr od wody! Trudne warunki na morzu.")
                elif beach_hours:
                    start_h = min(beach_hours)
                    end_h = max(beach_hours)
                    time_str = f"ok. {start_h:02d}:00" if start_h == end_h else f"głównie {start_h:02d}:00–{end_h:02d}:00"
                    alerts.append(f"Wybrzeże — Nad wodą możliwy wiatr od morza ({time_str}). Sprawdź /now (radar taktyczny).")

    except Exception as e:
        print(f"[SYSTEM] Błąd modułu nadmorskiego (prepare_layout): {e}")

    if os.environ.get("ENABLE_VOLATILITY_UI", "1") == "1":
        daily_diag = payload.get("daily_diag", {})
        target_sat = (now + timedelta(days=(5 - now.weekday()) % 7)).strftime("%Y-%m-%d")
        target_sun = (now + timedelta(days=(6 - now.weekday()) % 7)).strftime("%Y-%m-%d")
        MAX_VOLATILITY_DAY_HORIZON = int(os.environ.get("MAX_VOLATILITY_DAY_HORIZON", "3"))
        
        for date_str in (target_sat, target_sun):
            diag = daily_diag.get(date_str)
            if not diag:
                continue
                
            try:
                dt_evt = datetime.strptime(date_str, "%Y-%m-%d").date()
                days_ahead = (dt_evt - now.date()).days
                if days_ahead < 0 or days_ahead > MAX_VOLATILITY_DAY_HORIZON:
                    continue
            except ValueError:
                continue
                
            if not (diag.get("is_volatile") and diag.get("n_om", 0) >= 6 and diag.get("n_yr", 0) >= 3):
                continue
                
            max_diff = float(diag.get("spread_max", diag.get("spread", 0)) or 0.0)
            min_diff = float(diag.get("spread_min", 0) or 0.0)
            
            if max(max_diff, min_diff) < 2.0:
                continue
            
            pick = pick_hours_for_daily_summary(all_hours, daily_diag, date_str)
            base_summary = _build_day_summary(pick.hp, date_str, is_night_mode=False)
            
            if not base_summary:
                continue
                
            if max_diff >= min_diff:
                pora = t(lang, "diff_day")
                base_val = int(base_summary["temp_max"])
                alt_temp = diag.get("max_yr") if pick.source == "openmeteo" else diag.get("max_om")
            else:
                pora = t(lang, "diff_night")
                base_val = int(base_summary["temp_min"])
                alt_temp = diag.get("min_yr") if pick.source == "openmeteo" else diag.get("min_om")
                
            if alt_temp is None:
                continue
                
            alt_val = int(round(float(alt_temp)))
            
            if abs(alt_val - base_val) < 2:
                continue
                
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                event_key = "alert_diag_event_sat" if dt.weekday() == 5 else "alert_diag_event_sun"
                desc_template = t(lang, "alert_diag_desc")
                desc_text = desc_template.format(temp=alt_val, pora=pora)
                sender_text = f"⚠️ {t(lang, 'alert_diag_sender')}"
                final_alert = f"{sender_text} — {t(lang, event_key)}. {desc_text}"
                if final_alert not in alerts:
                    alerts.append(final_alert)
            except ValueError:
                continue
                
            if diag.get("is_volatile") and diag.get("n_om", 0) >= 6 and diag.get("n_yr", 0) >= 3:
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    if dt.weekday() in [5, 6]:
                        max_diff = diag.get("spread_max", diag.get("spread", 0))
                        min_diff = diag.get("spread_min", 0)
                        if max_diff >= min_diff:
                            alt_temp = diag.get("max_om")
                            pora = t(lang, "diff_day")
                        else:
                            alt_temp = diag.get("min_om")
                            pora = t(lang, "diff_night")
                        alt_val = int(round(alt_temp)) if alt_temp is not None else "?"
                        event_key = "alert_diag_event_sat" if dt.weekday() == 5 else "alert_diag_event_sun"
                        desc_template = t(lang, "alert_diag_desc")
                        desc_text = desc_template.format(temp=alt_val, pora=pora)
                        sender_text = f"⚠️ {t(lang, 'alert_diag_sender')}"
                        final_alert = f"{sender_text} — {t(lang, event_key)}. {desc_text}"
                        if final_alert not in alerts:
                            alerts.append(final_alert)
                except ValueError:
                    continue

    weekend_teaser = None
    if show_teaser:
        days_to_sat = 5 - dow
        if days_to_sat <= 0: days_to_sat += 7
        sat     = now + timedelta(days=days_to_sat)
        sun     = sat + timedelta(days=1)
        
        sat_str = sat.strftime("%Y-%m-%d")
        sun_str = sun.strftime("%Y-%m-%d")
        
        pick_sat = pick_hours_for_daily_summary(hours, daily_diag_dict, sat_str)
        pick_sun = pick_hours_for_daily_summary(hours, daily_diag_dict, sun_str)

        lang_days = DAYS_SHORT.get(lang, DAYS_SHORT["en"])
        
        sat_t   = _build_weekend_day_teaser(pick_sat.hp, lang_days[5], payload=payload)  
        sun_t   = _build_weekend_day_teaser(pick_sun.hp, lang_days[6], payload=payload) 
        
        if sat_t and sun_t:
            weekend_teaser = {"sat": sat_t, "sun": sun_t, "title": t(lang, "next_weekend")}
            
    hint = _drizzle_hint(ta=ta, hp_all=hours, start_hour=hero_start_hour)

    if hint:
        low = (final_context_line or "").lower()
        has_pressure_synoptic = any(x in low for x in ["hpa", "ciśnien", "cisnien", "spadek", "wzrost"])
        has_other_meta = any(x in low for x in ["nocne dane", "modele są rozbieżne", "odśwież"])
        pressure_only = has_pressure_synoptic and not has_other_meta
        if (not final_context_line) or pressure_only:
            final_context_line = (final_context_line + " · " + hint) if final_context_line else hint
            
    # --- INTELIGENTNY GATING OWM (Leniwa Weryfikacja 2.0) ---
    should_call_owm = False
    forecast_source = payload.get("forecast_source", "OpenMeteo + Yr.no")
    if payload.get("is_now"):
        should_call_owm = True
    elif " + " not in forecast_source:
        should_call_owm = True
    elif final_context_line and any(x in final_context_line.lower() for x in ["rozbieżne", "niepewn", "wczesny", "nocne", "divergent", "uncertain", "early", "night"]):
        should_call_owm = True
    elif hours:
        today_str = now.strftime("%Y-%m-%d")
        current_h = next((h for h in hours if h.get("time_local", "").startswith(today_str) and len(h.get("time_local", "")) >= 13 and int(h["time_local"][11:13]) == now.hour), None)
        
        if current_h:
            rh = float(current_h.get("rh_pct") or 0)
            mm_now = float(current_h.get("precip_eff_mm", current_h.get("precip_mm")) or 0)
            cld = max(float(current_h.get("clouds_low_pct") or 0) + float(current_h.get("clouds_mid_pct") or 0), float(current_h.get("clouds_pct_yr") or 0))
            
            if mm_now < 0.1 and rh >= 85 and cld >= 85:
                should_call_owm = True

    owm = payload.get("owm_current")
    if not owm and should_call_owm:
        try:
            owm = get_current_weather(payload["location"]["lat"], payload["location"]["lon"], timeout_sec=3)
        except Exception:
            pass

    owm_note = None
    if owm:
        owm_note = nowcast_note(payload_hours=payload.get("hours", []), now_local=now, owm=owm, lang=lang)

    if owm_note:
        low = (final_context_line or "").lower()
        pressure_only = any(x in low for x in ["hpa", "ciśnien", "cisnien", "spadek", "wzrost", "pressure", "drop", "rise"]) and not any(
            x in low for x in ["nocne dane", "rozbieżne", "odśwież", "night data", "divergent", "refresh"]
        )
        if (not final_context_line) or pressure_only:
            final_context_line = (final_context_line + " · " + owm_note) if final_context_line else owm_note
            
    # ══════════════════════════════════════════════════════════
    # TWARDA KOREKTA HERO (SATELITA ZABIJA KŁAMSTWA MODELI NA TERAZ)
    # ══════════════════════════════════════════════════════════
    if owm:
        current_data = owm.get("data", [{}])[0] if "data" in owm else owm
        if current_data:
            real_clouds = float(current_data.get("clouds", 0))
            real_uvi = float(current_data.get("uvi", 0.0))
            
            h0 = next((h for h in ta if _hour_safe(h.get("time_local", "")) == now.hour), None) or (ta[0] if ta else {})
            
            model_cld = _eff_cld_consensus(h0) if h0 else 0
            label_model, _ = sky_from_clouds(model_cld, hero_is_night)
            
            low = float(h0.get("clouds_low_pct") or 0.0)
            mid = float(h0.get("clouds_mid_pct") or 0.0)
            lowmid = low + mid
            prc = float(h0.get("precip_eff_mm", h0.get("precip_mm")) or 0.0)
            
            uv_model = float(h0.get("uv_index") or 0.0)
            uv_live  = float(real_uvi or 0.0)
            uv = uv_model if uv_model > 0 else uv_live
            
            models_strong_clear = (model_cld <= 40.0)
            looks_like_high_only = (lowmid <= 20.0) and (prc <= 0.05)
            owm_claims_cloudy = (real_clouds >= 70.0) and ((real_clouds - model_cld) >= 30.0)
            
            high_only_gate = (not hero_is_night) and looks_like_high_only and models_strong_clear
            
            if real_clouds >= 85 and uv > 1.2 and not hero_is_night:
                real_clouds = 65.0
            
            if abs(real_clouds - model_cld) >= 25:
                if high_only_gate and owm_claims_cloudy:
                    print(f"[OWM-DAY] high-only gate: model={model_cld:.1f} lowmid={lowmid:.1f} owm={real_clouds:.1f} uv={uv:.2f}")
                    real_clouds = min(real_clouds, 65.0)
                    
                if abs(real_clouds - model_cld) >= 25:
                    label_live, icon_live = sky_from_clouds(real_clouds, hero_is_night)
                    
                    if label_live != label_model:
                        has_precip = bool(day_hero and day_hero.get("precip_badge"))
                        has_wind = ((max_gust or 0) >= 60) or (max_wind >= 45)
                        
                        if not has_precip and not has_wind:
                            hero_icon = icon_live
                            
                        nowy_napis = f"Obecnie {label_live.lower()}"
                        nowy_napis = nowy_napis[0].upper() + nowy_napis[1:]
                        
                        if "\n" in hero_summary_line:
                            parts = hero_summary_line.split("\n", 1)
                            hero_summary_line = f"{nowy_napis}\n{parts[1]}"
                        else:
                            hero_summary_line = nowy_napis

    # --- AWARYJNY SENSOR MŻAWKI ---
    if not final_context_line:
        hint = _drizzle_hint(ta=ta, hp_all=hours, start_hour=hero_start_hour)
        if hint:
            final_context_line = hint

    # ══════════════════════════════════════════════════════════
    # HYBRYDOWY START BLOKÓW (ZASZCZEPIENIE OWM NA BIEŻĄCĄ GODZINĘ)
    # ══════════════════════════════════════════════════════════
    cld_overrides = {}
    bd_now = next((bd for bd in block_defs if _hour_in_block(now.hour, bd["start"], bd["end"])), None)
    
    if bd_now and owm:
        current_data = owm.get("data", [{}])[0] if "data" in owm else owm
        
        owm_dt = current_data.get("dt")
        if owm_dt:
            owm_dt = float(owm_dt)
            if owm_dt > 1e12:
                owm_dt /= 1000.0
        is_fresh = abs(now.timestamp() - owm_dt) < 5400 if owm_dt else True
        
        real_clouds = current_data.get("clouds")
        
        if real_clouds is not None and is_fresh:
            real_clouds = float(real_clouds)
            
            now_floored = now.replace(minute=0, second=0, microsecond=0)
            h_target = None
            
            for h in hp:
                tloc_str = h.get("time_local", "")
                if tloc_str:
                    try:
                        dt_h = datetime.fromisoformat(tloc_str.replace("Z", "+00:00"))
                        if tz:
                            dt_h = dt_h.astimezone(tz)
                        
                        if dt_h.replace(minute=0, second=0, microsecond=0) == now_floored:
                            h_target = h
                            break
                    except Exception:
                        pass
            
            if h_target:
                model_cld = _eff_cld_consensus(h_target)
                
                low = float(h_target.get("clouds_low_pct") or h_target.get("clouds_low_pct_yr") or 0.0)
                mid = float(h_target.get("clouds_mid_pct") or h_target.get("clouds_mid_pct_yr") or 0.0)
                prc = float(h_target.get("precip_eff_mm", h_target.get("precip_mm")) or 0.0)
                
                models_strong_clear = (model_cld <= 40.0)
                looks_like_high_only = ((low + mid) <= 20.0) and (prc <= 0.05)
                owm_claims_cloudy = (real_clouds >= 70.0) and ((real_clouds - model_cld) >= 30.0)
                
                high_only_gate = (not hero_is_night) and looks_like_high_only and models_strong_clear
                
                if abs(real_clouds - model_cld) >= 25 and not (high_only_gate and owm_claims_cloudy):
                    cld_overrides[h_target["time_local"]] = real_clouds

    today_blocks = _build_time_blocks(
        hp, today_str, tomorrow_str, block_defs, 
        hp_all=hours, lang=lang, cld_overrides=cld_overrides
    )

    if trust_report.hide_block_details and today_blocks:
        for b in today_blocks:
            pd = (b.get("primary_desc") or "").strip()
            if not pd:
                continue
            
            pd2 = strip_mm_pct_parens(pd)
            obecny_jezyk = payload.get("lang", "en")
            pd2 = soften_possible_prefix(pd2, lang=obecny_jezyk)
            
            b["primary_desc"] = pd2
            
            extras = b.get("extra_lines") or []
            for ex in extras:
                if isinstance(ex, dict):
                    if "text" in ex and ex["text"]:
                        ex["text"] = re.sub(r"\s*\([^)]*(mm|%)[^)]*\)", "", ex["text"], flags=re.IGNORECASE).strip()
                    if "spans" in ex and isinstance(ex["spans"], list):
                        for sp in ex["spans"]:
                            if isinstance(sp, dict) and sp.get("text"):
                                sp["text"] = re.sub(r"\s*\([^)]*(mm|%)[^)]*\)", "", sp["text"], flags=re.IGNORECASE).strip()

    wk = build_worth_knowing(
        payload=payload, blocks=hero_blocks, alerts=alerts, temp_min=bmin, temp_max=bmax,
        max_wind=max_wind, gust_kmh=max_gust, total_precip_mm=tp,
        is_afternoon_report=(now.hour >= 12),
        summary_line=hero_summary_line, 
        context_line_text=final_context_line or "",
        built_blocks=today_blocks, ta=ta, current_hour=now.hour 
    )
    if isinstance(wk, dict) and "title" in wk:
        wk["title"] = t(lang, "good_to_know")
    hero_text = hero_summary_line.replace("\n", " ").lower()
    wk_text = wk.get("text", "").lower() if isinstance(wk, dict) else (str(wk).lower() if wk else "")

    if wk_text and hero_text:
        if any(w in wk_text for w in ["wiatr", "poryw", "wichur"]):
            if any(w in hero_text for w in ["wiatr", "wietrznie", "wichur", "poryw"]):
                wk = None
        elif ("deszcz" in wk_text or "ulew" in wk_text) and ("deszcz" in hero_text or "ulew" in hero_text):
            wk = None
            
    for d in nd:
        if d.get("precip_badge") and len(d["precip_badge"]) > 0:
            d["precip_badge"] = d["precip_badge"][0].lower() + d["precip_badge"][1:]
        if d.get("descriptor") and len(d["descriptor"]) > 0:
            d["descriptor"] = d["descriptor"][0].lower() + d["descriptor"][1:]
            
    # ══════════════════════════════════════════════════════════
    # OSTATNIA MILA: TŁUMACZENIE I PANCERNE FORMATOWANIE LAYOUTU
    # ══════════════════════════════════════════════════════════

    if lang != "pl":
        if hero_summary_line:
            hero_summary_line = translate_weather_text(hero_summary_line, lang)
        if final_context_line:
            final_context_line = translate_weather_text(final_context_line, lang)
        
        alerts = [translate_weather_text(a, lang) for a in alerts if a]
        
        if wk and isinstance(wk, dict) and wk.get("text"):
            wk["text"] = translate_weather_text(wk["text"], lang)

        for d in nd or []:
            if d.get("precip_badge"): 
                d["precip_badge"] = _smart_cap(translate_weather_text(d["precip_badge"], lang))
            if d.get("descriptor"): 
                d["descriptor"] = _smart_cap(translate_weather_text(d["descriptor"], lang))

        all_blocks = list(today_blocks or [])
        for day in wdd or []:
            all_blocks.extend(day.get("blocks", []) or [])

        for b in all_blocks:
            if b.get("primary_desc"):
                b["primary_desc"] = _smart_cap(translate_weather_text(b["primary_desc"], lang))
            
            extra = b.get("extra_lines", []) or []
            for i, ex in enumerate(extra):
                if isinstance(ex, str):
                    extra[i] = _smart_cap(translate_weather_text(ex, lang))
                elif isinstance(ex, dict):
                    if ex.get("text"):
                        ex["text"] = _smart_cap(translate_weather_text(ex["text"], lang))
                    if isinstance(ex.get("spans"), list):
                        for sp in ex["spans"]:
                            if isinstance(sp, dict) and sp.get("text"):
                                sp["text"] = _smart_cap(translate_weather_text(sp["text"], lang))

    if weekend_teaser:
        for k in ("sat", "sun"):
            d = weekend_teaser.get(k)
            if isinstance(d, dict) and d.get("desc"):
                if lang != "pl":
                    d["desc"] = _smart_cap(translate_weather_text(d["desc"], lang))
                else:
                    d["desc"] = d["desc"].lower()
    
    return {
        "city":                payload["location"]["name"],
        "weekday":             weekday,
        "date":                date_display,
        "report_type":         report_type,
        "main_icon":           hero_icon,
        "temp_range":          _fmt_temp(bmin, bmax),
        "summary":             hero_summary_line,
        "context_line":        final_context_line,
        "worth_knowing":       wk,
        "pressure":            None,
        "air_quality_text":    None,
        "air_quality_color":   None,
        "section_title":       section_title,
        "today_blocks":        today_blocks,
        "weekend_detail_days": wdd,
        "future_order":        future_order,
        "next_days":           nd,
        "next_days_title":     next_days_title,
        "alerts":              [a for a in alerts if a],
        "alert_title":         t(lang, "watch_out"),
        "weekend_teaser":      weekend_teaser, 
        "forecast_source":     payload.get("forecast_source", "Yr.no"),
        "source_label":        t(lang, "source_label")
    }