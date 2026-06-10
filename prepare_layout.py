"""
prepare_layout.py — Produkcyjny builder layoutu karty pogodowej
Funkcja: prepare_layout_data(payload, now=None)
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from forecast_text import WxEvent, BlockForecast, build_block_copy, classify_precip
from worth_knowing import build_worth_knowing
from confidence_gate import compute_trust_report
from ui_softening import strip_mm_pct_parens, soften_possible_prefix


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


def _drizzle_hint(ta: list, hp_all: list, start_hour: int) -> str | None:
    """
    Miękka podpowiedź: możliwe pojedyncze krople mimo 0.0 mm w modelu.
    Umiarkowane progi + warunek 2 kolejnych godzin, żeby nie spamować.
    """
    import os
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
        t = float(t_raw); dp = float(dp_raw)
        if (t - dp) > 6.5:
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
    p_base = float(h.get("precip_mm") or 0)
    pop_base = float(h.get("precip_prob_pct", 0))
    
    t_loc = h.get("time_local")
    if not t_loc or not hp_all: return p_base
        
    # Szukamy tego samego czasu w drugim modelu
    alt_source = "yrno" if h.get("source") == "openmeteo" else "openmeteo"
    h_alt = next((y for y in hp_all if y.get("time_local") == t_loc and y.get("source") == alt_source), None)
    
    if not h_alt: return p_base
    
    p_alt = float(h_alt.get("precip_mm") or 0)
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

# --- NOWE FUNKCJE KONSENSUSU ---
def _eff_cld_alt(low_c, mid_c, total_c) -> float:
    if low_c is not None and mid_c is not None:
        return min(100.0, float(low_c) + float(mid_c))
    return float(total_c or 0)

def _eff_cld_consensus(h: dict) -> float:
    base = _eff_cld(h)  # Chmury z Open-Meteo
    alt = _eff_cld_alt(h.get("clouds_low_pct_yr"), h.get("clouds_mid_pct_yr"), h.get("clouds_pct_yr")) # Chmury z Yr.no
    return max(base, alt) # Bierzemy bardziej pesymistyczną wartość
# -------------------------------

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
    
    # Detekcja nocy (od 20:00 do 6:00 rano)
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
        
    # 3. Synchronizacja z nową ikoną "Słonecznie" (Żelazna drabinka)
    if clouds <= 10: return "wk_clear_night" if is_night else "wk_clear"
    elif clouds <= 35: return "wk_moon_one_cloud" if is_night else "wk_sun_one_cloud"
    elif clouds < 70: return "wk_partlycloudy_night" if is_night else "wk_partlycloudy"
    elif clouds < 85: return "wk_mostly_cloudy"
    else: return "wk_overcast"


def _sky_human(clouds: float, hour_hint: Optional[int] = None) -> str:
    c = float(clouds or 0)
    is_night = hour_hint is not None and (hour_hint >= 18 or hour_hint <= 5)

    if c <= 10: return "bezchmurnie"
    if c <= 35: return "pogodnie" if is_night else "słonecznie"
    if c < 70: return "przejaśnienia"
    if c < 85: return "dużo chmur"
    return "pochmurno"

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

import re

def _format_single_hours(text: str) -> str:
    if not text: return text
    
    # Bezpieczny regex dla 'od/po/ok.' (Doda zera do "silniej po 19" -> "silniej po 19:00")
    t = re.sub(
        r'(?<!\w)(od|po|ok\.)\s+([0-1]?[0-9]|2[0-4])(?!\d)(?!\s*km/h)(?!\s*°)(?!\s*mm)(?!\s*%)', 
        r'\1 \2:00', 
        text, 
        flags=re.IGNORECASE
    )
    
    # Bezpieczny regex dla 'do' (Ignoruje słowa o wietrze, by nie zrobić "wiatr do 32:00")
    t = re.sub(
        r'(?<!\w)(?<!wiatr )(?<!wichura )(?<!porywy )(?<!ok\. )(do)\s+([0-1]?[0-9]|2[0-4])(?!\d)(?!\s*km/h)(?!\s*°)(?!\s*mm)(?!\s*%)', 
        r'\1 \2:00', 
        t, 
        flags=re.IGNORECASE
    )
    return t

def _ensure_kmh(text: str) -> str:
    if not text: return text
    # Wymusza dopisanie km/h do siły wiatru, jeśli inny system o nim zapomniał!
    # (?!\d) to żelazna blokada: "nie waż się ciąć liczby, jeśli zaraz po niej jest kolejna cyfra!"
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


def _build_wx_events(block_hours: list, hp_all: list = None) -> list:
    events = []
    for h in block_hours:
        mm = _precip_consensus(h, hp_all) #### if hp_all else float(h.get("precip_mm") or 0)
        temp = h.get("temp_c", 10)
        hr = _hour(h)
        kind = classify_precip(
            mm, temp,
            symbol_code=h.get("symbol_code"),
            weather_code=h.get("weather_code")
        )
        if kind:
            events.append(WxEvent(kind, hr, hr + 1))
    return events


def _build_time_blocks(hp: list, date_str: str, next_date_str: str, block_defs: list, hp_all: list = None) -> list:
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
        
        # --- 1. INTELIGENTNE CHMURY ---
        eff_clouds = [_eff_cld_consensus(h) for h in bh]
                
        #avg_c = sum(eff_clouds) / len(eff_clouds) if eff_clouds else 0
        #tot_p = sum(_precip_consensus(h, hp) for h in bh) # <--- KONSENSUS
        #max_p = max([_precip_consensus(h, hp) for h in bh] + [0]) # <--- KONSENSUS
        #max_w = max([float(h.get("wind_gust_kmh") or h.get("gust_kmh") or 0) for h in bh] + [0])
        
        #evs = _build_wx_events(bh, hp) # <--- PRZEKAZANIE HP DO EVENTÓW
        
        avg_c = sum(eff_clouds) / len(eff_clouds) if eff_clouds else 0
        
        # --- OPADY: spójnie z _build_day_summary (prawdziwy konsensus z hp_all) ---
        p_vals = [_precip_consensus(h, hp_all or hp) for h in bh]
        tot_p = sum(p_vals) if p_vals else 0.0
        max_p = max(p_vals + [0.0])
        
        max_w = max([float(h.get("wind_gust_kmh") or h.get("gust_kmh") or 0) for h in bh] + [0])
        
        evs = _build_wx_events(bh, hp_all=hp_all)
        
        
        icon  = _choose_icon(avg_c, max_p, (t_min + t_max) / 2, wind=max_w, events=evs, hour_hint=s)

        fv = [_feels_like(h.get("temp_c"), h.get("wind_kmh"), h.get("rh_pct"))
              for h in bh]
        fv = [f for f in fv if f is not None]
        f_min = round(min(fv)) if fv else None
        f_max = round(max(fv)) if fv else None

        display_label = "Noc" if label == "Noc" else f"{s:02d}–{e:02d}"
        sky = _sky_human(avg_c, hour_hint=s) if max_p < 0.1 else None

        # --- 3. TWORZENIE BLOKU Z WIATREM ---
        bf = BlockForecast(
            label=display_label, hours_range=f"{s:02d}–{e:02d}",
            start=s, end=e,
            temp_min=t_min, temp_max=t_max,
            feels_min=f_min, feels_max=f_max,
            sky_label=sky,
            max_wind=max_w,  # <--- WSTRZYKUJEMY WIATR DO FORECAST_TEXT
            events=evs,
        )
        copy = build_block_copy(bf, inline_max_chars=48, meta_max_chars=32)

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
                    if "spans" in ex:  # <--- NOWE: Zaglądamy w głąb stylizowanych tekstów alertowych
                        for span in ex["spans"]:
                            if "text" in span:
                                span["text"] = _format_single_hours(_ensure_kmh(span["text"]))
                elif isinstance(ex, str):
                    copy["extra_lines"][i] = _format_single_hours(_ensure_kmh(ex))

        # --- INTELIGENTNE ŁAMANIE LINII (Zabezpieczenie przed ucinaniem) ---
        # Jeśli pierwsza linia przekracza 32 znaki i zawiera kropeczkę " · "
        if len(copy["primary_desc"]) > 32 and " · " in copy["primary_desc"]:
            parts = copy["primary_desc"].split(" · ", 1)
            copy["primary_desc"] = parts[0]
            
            # Wrzucamy drugą część tekstu na samą górę nowej linii
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

def _day_descriptor(hours_day: list) -> Optional[str]:
    if not hours_day:
        return None
    morning = [h for h in hours_day if 6 <= _hour(h) < 10]
    has_fog = any(
        classify_precip(0, h.get("temp_c", 10),
                        symbol_code=h.get("symbol_code"),
                        weather_code=h.get("weather_code")) == "fog"
        for h in morning
    )
    if has_fog:
        return "mgła rano"
    max_gust = max(
        (float(h.get("gust_kmh") or h.get("wind_kmh") or 0) for h in hours_day),
        default=0)
    if max_gust >= 60:
        return "wietrznie"
    avg_c = sum(_eff_cld_consensus(h) for h in hours_day) / len(hours_day) if hours_day else 0
    if avg_c <= 10: return "słonecznie"
    if avg_c <= 35: return "pogodnie"
    if avg_c < 70: return "przejaśnienia"
    if avg_c < 85: return "dużo chmur"
    return "pochmurno"


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

    eff_clouds = []
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

            eff_clouds.append(_eff_cld_consensus(h))

            precip = _precip_consensus(h, hp)
            code = str(h.get("symbol_code") or "").lower()
            w_code = h.get("weather_code")
            temp_opadu = h.get("temp_c") if h.get("temp_c") is not None else 10

            if "fog" in code or (w_code in [41,42,43,44,45,46,47,48,49]):
                has_fog = True

            # === PANCERNY BEZPIECZNIK OPADÓW (Zsynchronizowany z blokami) ===
            precip = _precip_consensus(h, hp) 
            
            # Resetujemy flagi dla każdej godziny
            is_snow = False
            is_rain = False
            is_storm = False
            
            if precip > 0:
                # Jedyna funkcja decyzyjna (z forecast_text.py)
                kind = classify_precip(precip, temp_opadu, symbol_code=code, weather_code=w_code)
                
                # Przypisujemy zdarzenia na podstawie wyniku classify_precip
                if kind in ["snow", "light_snow", "heavy_snow", "snow_showers"]:
                    is_snow = True
                elif kind == "sleet":
                    is_snow = True
                    is_rain = True
                elif kind in ["storm", "heavy_storm"]:
                    is_storm = True
                elif kind:
                    is_rain = True
                    if kind == "drizzle":
                        has_drizzle = True
                    else:
                        has_real_rain = True

                # Zapisujemy godziny wystąpienia
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

    # Reguła pożerania: Jeśli jest jakikolwiek deszcz, nazywamy to deszczem. Mżawka wygrywa tylko, gdy cały opad to mżawka.
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

    avg_eff_c = sum(eff_clouds) / len(eff_clouds) if eff_clouds else 0
    has_sun = avg_eff_c < 55
    has_heavy_clouds = avg_eff_c >= 75

    max_dzien = round(max(day_temps)) if day_temps else d_max
    temp_anomaly = (d_max - max_dzien >= 4)

    descriptor = ""
    icon = "wk_clear"

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
        is_drizzle = has_drizzle  # has_drizzle obliczyłeś w Pancernym Bezpieczniku!
        
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
                    
    elif has_fog and not has_heavy_clouds:
        if avg_eff_c <= 35:
            descriptor = "Rano mgły, w dzień słońce"
        elif avg_eff_c < 70:
            descriptor = "Rano mgły, przejaśnienia"
        else:
            descriptor = "Rano mgły, dużo chmur"
        icon = "wk_fog"
    else:
        # ŻELAZNA DRABINKA CHMUR (Zsynchronizowana)
        if avg_eff_c <= 10:
            descriptor = "Bezchmurnie"
            icon = "wk_clear_night" if is_night_mode else "wk_clear"
        elif avg_eff_c <= 35:
            descriptor = "Pogodnie" if is_night_mode else "Słonecznie"
            icon = "wk_moon_one_cloud" if is_night_mode else "wk_sun_one_cloud"
        elif avg_eff_c < 70:
            descriptor = "Przejaśnienia"
            icon = "wk_partlycloudy_night" if is_night_mode else "wk_partlycloudy"
        elif avg_eff_c < 85:
            descriptor = "Dużo chmur"
            icon = "wk_mostly_cloudy"
        else:
            descriptor = "Pochmurno"
            icon = "wk_overcast"

    if badge and len(descriptor) > 15 and ("Rano" in descriptor or "Po południu" in descriptor):
        descriptor = descriptor.replace("Rano ", "").replace("Po południu ", "").capitalize()

    # <--- ROZBUDOWANY KONTEKST
    pop_val = int(max_pop)
    pop_str = f" ({pop_val}%)" if pop_val > 0 else ""

    # NOWOŚĆ: Dopinamy POP tylko do opadów!
    pop_for_badge = pop_str
    if badge:
        lowb = badge.lower()
        is_precip_badge = any(w in lowb for w in ["deszcz", "mżawk", "śnieg", "burz", "opad"])
        if not is_precip_badge:
            pop_for_badge = ""   # wyciszamy % przy wietrze, mgle, chmurach itd.

    # --- BLOKADA FIZYCZNA
    if (badge or pop_val >= 40) and avg_eff_c < 45:
        avg_eff_c = 45  # Sztucznie podbijamy minimum do "Przejaśnienia"

    # 1. Określenie tła wizualnego
    if avg_eff_c <= 10: base_sky = "Bezchmurnie"
    elif avg_eff_c <= 35: base_sky = "Pogodnie" if is_night_mode else "Słonecznie"
    elif avg_eff_c < 70: base_sky = "Przejaśnienia"
    elif avg_eff_c < 85: base_sky = "Dużo chmur"
    else: base_sky = "Pochmurno"

    # --- IKONA JEST SZEFEM & NOCNE OPADY ---
    is_daytime_precip = has_rain or has_snow or has_storm

    if badge:
        if "wiatr" not in badge:
            badge = badge.replace("przelotne ", "przel. ").replace("przelotny ", "przel. ")
            if is_daytime_precip:
                badge = f"{badge[0].upper()}{badge[1:]}{pop_for_badge}" # <--- ZMIANA
            else:
                if "mżawka" in badge: badge = "nocna mżawka"
                elif "śnieg z deszczem" in badge: badge = "nocny deszcz ze śniegiem"
                elif "burz" in badge: badge = "nocne burze"
                elif "śnieg" in badge: badge = "nocny śnieg"
                else: badge = "nocny deszcz"
                
                badge = f"{base_sky} · {badge}{pop_for_badge}" # <--- ZMIANA
        else:
            badge += pop_for_badge # <--- ZMIANA
    else:
        # Twarda reguła Norwegów: 0.0 mm na radarze = 0 gadania o deszczu w Hero.
        if not descriptor:
            descriptor = base_sky

    # --- APLIKUJEMY FORMATOWANIE POJEDYNCZYCH GODZIN I JEDNOSTEK ---
    if badge: badge = _format_single_hours(_ensure_kmh(badge))
    if descriptor: descriptor = _format_single_hours(_ensure_kmh(descriptor))

    return {
        "icon": icon, "temp_min": d_min, "temp_max": d_max,
        "precip_badge": badge, "descriptor": descriptor,
    }

# ═══════════════════════════════════════
# WEEKEND TEASER
# ═══════════════════════════════════════

def _build_weekend_day_teaser(hp: list, day_short: str) -> Optional[dict]:
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
        "desc": desc
    }


# ═══════════════════════════════════════
# BLOKI CZASU — definicje
# ═══════════════════════════════════════

def _get_time_blocks(hour: int) -> tuple[str, list]:
    if hour < 12:
        return "Prognoza na dziś", [
            {"label": "Rano",       "start": 6,    "end": 10},
            {"label": "Popołudnie", "start": 11,   "end": 16},
            {"label": "Wieczór",    "start": 17,   "end": 22},
        ]
    if hour < 18:
        # Inteligentne, nienachodzące na siebie bloki dla raportów popołudniowych
        blocks = []
        if hour <= 14:
            blocks.append({"label": "Popołudnie", "start": hour, "end": 16})  
            blocks.append({"label": "Wieczór",    "start": 17,   "end": 22}) 
        else:
            blocks.append({"label": "Późne popoł.", "start": hour, "end": 18}) 
            blocks.append({"label": "Wieczór",      "start": 19,   "end": 22}) 
            
        # Noc pozostaje żelazną kotwicą
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

def prepare_layout_data(payload, now=None): 
    import os
    if os.environ.get("DEBUG_PAYLOAD_JSON") == "1":
        import json
        with open("debug_pogoda.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            
    tz  = ZoneInfo(payload["location"]["tz"])
    now = now or datetime.now(tz)

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
        current_hour=now.hour
    )

    # === DATOWANIE ŹRÓDŁA DANYCH I AGE-GATING ===
    is_morning_report = (now.hour < 12)
    is_night_run = False
    model_time_str = payload.get("model_updated_at_local")
    
    if model_time_str and len(model_time_str) >= 16:
        data_time = model_time_str[11:16]
        time_suffix = f" (dane z {data_time})"
        try:
            model_dt_loc = datetime.fromisoformat(model_time_str.replace("Z", "+00:00")).astimezone(tz)
            is_night_run = (model_dt_loc.hour < 6)
        except Exception:
            pass
    else:
        time_suffix = ""

    # === NAZWA RAPORTU ===
    if payload.get("is_now"):
        base_title = "Radar taktyczny"
    elif payload.get("is_future"):
        base_title = "Prognoza długoterminowa"
    else:
        base_title = _determine_report_type(now.hour)

    report_type = f"{base_title}{time_suffix}"
    
    weekday      = DNI_PL[now.weekday()]
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

    section_title, block_defs = _get_time_blocks(now.hour)
    today_blocks = _build_time_blocks(hp, today_str, tomorrow_str, block_defs, hp_all=hours)
    
    if trust_report.hide_block_details and today_blocks:
        for b in today_blocks:
            pd = (b.get("primary_desc") or "").strip()
            if not pd:
                continue
            
            # Używamy naszego nowego modułu!
            pd2 = strip_mm_pct_parens(pd)
            pd2 = soften_possible_prefix(pd2)
            
            b["primary_desc"] = pd2
            
            # (opcjonalnie, ale bezpiecznie) zdejmij mm/% z extra_lines, bez prefiksów
            extras = b.get("extra_lines") or []
            for ex in extras:
                if isinstance(ex, dict):
                    if "text" in ex and ex["text"]:
                        ex["text"] = re.sub(r"\s*\([^)]*(mm|%)[^)]*\)", "", ex["text"], flags=re.IGNORECASE).strip()
                    if "spans" in ex and isinstance(ex["spans"], list):
                        for sp in ex["spans"]:
                            if isinstance(sp, dict) and sp.get("text"):
                                sp["text"] = re.sub(r"\s*\([^)]*(mm|%)[^)]*\)", "", sp["text"], flags=re.IGNORECASE).strip()    
                
    
    
    all_temps = []  
    for b in today_blocks:
        for part in b.get("temp_range", "").replace("°", "").split("/"):
            try: all_temps.append(int(part))
            except ValueError: pass
    bmin = min(all_temps) if all_temps else 0
    bmax = max(all_temps) if all_temps else 0

    # ── KIEROWNIK RUCHU (Tryb Weekendowy) ──
    dow = now.weekday()
    summary_offsets = []
    wdd_offsets = []
    future_order = []
    show_teaser = False

    if dow == 3: # Czwartek
        summary_offsets = [1]       # Pt (1 linijka)
        wdd_offsets = [2, 3]        # Sob, Nd (Pełne bloki)
        future_order = ["summary", "detail", "detail"]
    elif dow == 4: # Piątek
        summary_offsets = [3]       # Pn (1 linijka)
        wdd_offsets = [1, 2]        # Sob, Nd (Pełne bloki)
        future_order = ["detail", "detail", "summary"]
    elif dow == 5: # Sobota
        summary_offsets = [2, 3]    # Pn, Wt (1 linijka)
        wdd_offsets = [1]           # Nd (Pełny blok)
        future_order = ["detail", "summary"]
    elif dow == 6: # Niedziela
        summary_offsets = [1, 2, 3] # Pn, Wt, Śr
        show_teaser = True
    elif dow == 0: # Poniedziałek
        summary_offsets = [1, 2, 3] # Wt, Śr, Czw
        show_teaser = True
    elif dow == 1: # Wtorek
        summary_offsets = [1, 2, 3] # Śr, Czw, Pt
        show_teaser = True
    elif dow == 2: # Środa
        summary_offsets = [1, 2]    # Czw, Pt
        show_teaser = True

    # 1. Płaskie dni (summary)
    future_sections = []
    for off in summary_offsets:
        tgt = now + timedelta(days=off)
        ts  = tgt.strftime("%Y-%m-%d")
        
        # 1) Baza z Yr.no (żeby ikony i główny ton zgadzały się z /future)
        base = _build_day_summary(hy, ts, is_night_mode=False) if hy else None
        if not base:
            base = _build_day_summary(hp, ts, is_night_mode=False)
            
        # 2) Poszukiwanie zagrożeń (hazardów) z Open-Meteo
        haz = _build_day_summary(ho, ts, is_night_mode=False) if ho else None
        
        extra_note = None
        if base and haz:
            haz_icon = haz.get("icon") or ""
            # Jeśli OM wygenerował ikonę ostrzegawczą...
            if haz_icon in ("wk_wind", "wk_storm", "wk_snow", "wk_sleet"):
                # Pobieramy konkretny opis tego zjawiska
                extra_note = haz.get("precip_badge") or haz.get("descriptor")
                
        if base:
            # 3) Inteligentne doklejanie alertu do bazy
            if extra_note:
                # Zabezpieczenie przed dublowaniem (jeśli oba modele wyłapały to samo)
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
                "name":      DNI_SHORT[tgt.weekday()],
                "name_full": f"{DNI_PL[tgt.weekday()]}, {tgt.strftime('%d.%m')}",
                **base,
            })
            
    future_sections.sort(key=lambda x: x["date"])
    nd  = []
    for fs in future_sections:
        d = {k: v for k, v in fs.items() if k not in ("type", "date")}
        d["_date"] = fs["date"]
        nd.append(d)

    # 2. Pełne bloki weekendowe (wdd)
    FULL_DAY_BLOCKS = [
        {"label": "Rano",       "start": 6,  "end": 10},
        {"label": "Popołudnie", "start": 11, "end": 16},
        {"label": "Wieczór",    "start": 17, "end": 22},
        {"label": "Noc",        "start": 22, "end": 6}
    ]
    wdd = []
    for off in wdd_offsets:
        tgt = now + timedelta(days=off)
        ts = tgt.strftime("%Y-%m-%d")
        ts_next = (tgt + timedelta(days=1)).strftime("%Y-%m-%d")
        blocks = _build_time_blocks(hp, ts, ts_next, FULL_DAY_BLOCKS, hp_all=hours)
        if blocks:
            wdd.append({
                "name": f"{DNI_PL[tgt.weekday()]}, {tgt.strftime('%d.%m')}",
                "blocks": blocks
            })

    # Tytuł + label dla sekcji summary
    tomorrow_date = (now + timedelta(days=1)).date()
    if len(nd) == 1:
        the_date = nd[0]["_date"]
        the_date = the_date.date() if hasattr(the_date, "date") else the_date
        if the_date == tomorrow_date:
            next_days_title = "Jutro"
        else:
            next_days_title = nd[0].get("name_full", nd[0].get("name", ""))
        nd[0]["label"] = "00–24"
        nd[0]["name"]  = ""
    else:
        next_days_title = "Najbliższe dni"

    for d in nd:
        d.pop("_date", None); d.pop("name_full", None)

    # ── Hero summary
    # Synchronizacja Hero z widocznymi blokami (Raport poranny widzi od 6:00)
    hero_start_hour = 6 if now.hour < 12 else now.hour
    
    hp_hero = [h for h in hp if h.get("time_local", "")[:10] > today_str or (h.get("time_local", "")[:10] == today_str and _hour(h) >= hero_start_hour)]
    
    # --- INTELIGENTNY DETEKTOR NOCY (Astronomiczny) ---
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
        # Przyszłe dni (np. jutro) w Hero zawsze podsumowujemy dziennymi ikonami
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
        
    # --- EASTER EGG: POGODA JAK KRYSZTAŁ ---
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
        # Zabezpieczenie: jeśli główny opis ostrzega o wietrze, NIE zamazujemy tego "niepewnością"!
        if any(w in low for w in ["wiatr", "wichur", "poryw"]):
            pass
        elif any(w in low for w in ["deszcz", "mżawk", "ulew", "burz", "śnieg", "opad"]):
            line1 = "Niestabilna aura, możliwe opady"
        elif any(w in low for w in ["słonecz", "bezchmurn", "pogodnie"]):
            line1 = "Niepewna prognoza zachmurzenia"
        else:
            line1 = "Niepewna prognoza"
    
    # Synchronizacja wiatru na głównym ekranie z widocznymi blokami
    future_ta_hero = [h for h in ta if int(h.get("time_local", "T00:")[11:13]) >= hero_start_hour]
    
    # Złota reguła: efektywny wiatr z pozostałej części dnia
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

    # --- AGE GATING: Ostateczne nadpisanie (najwyższy priorytet na wypadek starych danych) ---
    is_dynamic = (tp >= 1.0) or (max_wind >= 45) or ((max_gust or 0) >= 60)
    show_age_note = is_morning_report and is_night_run and (trust_report.is_volatile or is_dynamic)

    if show_age_note:
        final_context_line = "Nocne dane — odśwież prognozę z menu później"

    alerts = list(dict.fromkeys(alerts))

    wk = build_worth_knowing(
        blocks=hero_blocks, alerts=alerts, temp_min=bmin, temp_max=bmax,
        max_wind=max_wind, gust_kmh=max_gust, total_precip_mm=tp,
        is_afternoon_report=(now.hour >= 12),
        summary_line=hero_summary_line, 
        context_line_text=final_context_line or "",
        built_blocks=today_blocks, ta=ta, current_hour=now.hour 
    )

    hero_text = hero_summary_line.replace("\n", " ").lower()
    wk_text = wk.get("text", "").lower() if isinstance(wk, dict) else (str(wk).lower() if wk else "")

    if wk_text and hero_text:
        if any(w in wk_text for w in ["wiatr", "poryw", "wichur"]):
            if any(w in hero_text for w in ["wiatr", "wietrznie", "wichur", "poryw"]):
                wk = None
        elif ("deszcz" in wk_text or "ulew" in wk_text) and ("deszcz" in hero_text or "ulew" in hero_text):
            wk = None

    # ── Weekend teaser ──
    weekend_teaser = None
    if show_teaser:
        days_to_sat = 5 - dow
        if days_to_sat <= 0: days_to_sat += 7
        sat     = now + timedelta(days=days_to_sat)
        sun     = sat + timedelta(days=1)
        sat_h   = [h for h in hp if h.get("time_local", "").startswith(sat.strftime("%Y-%m-%d"))]
        sun_h   = [h for h in hp if h.get("time_local", "").startswith(sun.strftime("%Y-%m-%d"))]
        sat_t   = _build_weekend_day_teaser(sat_h, "Sob")
        sun_t   = _build_weekend_day_teaser(sun_h, "Ndz")
        if sat_t and sun_t:
            weekend_teaser = {"sat": sat_t, "sun": sun_t, "title": "Przyszły weekend"}
    hint = _drizzle_hint(ta=ta, hp_all=hours, start_hour=hero_start_hour)

    if hint:
        low = (final_context_line or "").lower()

        # Nadpisujemy tylko gdy context_line jest puste albo to tylko "hPa"/strzałki (meta),
        # ale NIE nadpisujemy age-gatingu ani notek o rozbieżności modeli.
        #is_pressure_only = ("hpa" in low) and ("modele" not in low) and ("nocne dane" not in low)
        #if (not final_context_line) or is_pressure_only:
        #    final_context_line = hint
        
        has_pressure_synoptic = any(x in low for x in [
            "hpa",
            "ciśnien", "cisnien",          # na wypadek braku polskich znaków
            "spadek", "wzrost"             # Twoje synoptyki to zwykle spadek/wzrost ciśnienia
        ])

        has_other_meta = any(x in low for x in [
            "nocne dane",
            "modele są rozbieżne",
            "odśwież"                      # age-gating / inne meta
        ])

        pressure_only = has_pressure_synoptic and not has_other_meta
        if (not final_context_line) or pressure_only:
            final_context_line = (final_context_line + " · " + hint) if final_context_line else hint
            
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
        "alerts":              alerts,          
        "weekend_teaser":      weekend_teaser,
        "forecast_source":     payload.get("forecast_source", "Yr.no")
    }