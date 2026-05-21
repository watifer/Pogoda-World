"""
prepare_now_layout.py — Moduł dedykowany wyłącznie dla komendy /now.
Generuje taktyczną kartę z 12 najbliższymi godzinami od momentu uruchomienia.
"""

from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# Importujemy sprawdzoną logikę z głównego skryptu (w tym efektywne chmury)
from prepare_layout import _fmt_temp, _feels_like, DNI_PL, _hour_safe, _eff_cld_consensus
from forecast_text import classify_precip, KINDS


def _now_icon(clouds: float, precip: float, temp: float, hour: int, kind: str = None, symbol_code: str = "") -> str:
    """Logika ikon oparta na głównym klasyfikatorze z forecast_text."""
    # NOWOŚĆ: Jeśli Norwegowie podali nam twardy dowód, że jest noc, używamy tego!
    if symbol_code and "_night" in symbol_code.lower():
        is_night = True
    elif symbol_code and "_day" in symbol_code.lower():
        is_night = False
    else:
        # Ratunkowy fallback, gdyby brakowało danych z API
        is_night = hour < 6 or hour >= 20 

    if precip > 0:
        if kind:
            fam = KINDS.get(kind, {}).get("family")
            if fam == "snow": return "wk_snow" if clouds >= 70 else "wk_snow_showers"
            if fam == "mixed": return "wk_sleet"
            if fam == "storm": return "wk_storm"
            if kind == "drizzle": return "wk_drizzle"
            
        # Fallback
        if temp <= 2.0: return "wk_snow" if clouds >= 70 else "wk_snow_showers"
        if precip <= 0.5: return "wk_drizzle"
        return "wk_showers" if clouds < 70 else "wk_rain"
        
    if kind == "fog": 
        return "wk_fog" 
        
    if clouds <= 10: return "wk_clear_night" if is_night else "wk_clear"
    if clouds <= 35: return "wk_moon_one_cloud" if is_night else "wk_sun_one_cloud"
    if clouds < 70: return "wk_partlycloudy_night" if is_night else "wk_partlycloudy"
    if clouds < 85: return "wk_mostly_cloudy"
        
    return "wk_overcast"

def prepare_now_layout_data(payload: dict, now: datetime = None) -> dict:
    tz = ZoneInfo(payload["location"]["tz"])
    
    # 1. PEWNY CZAS LOKALNY
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    else:
        now = now.astimezone(tz)
        
    now_floored = now.replace(minute=0, second=0, microsecond=0)
    weekday = DNI_PL[now.weekday()]
    
    # 2. FILTROWANIE GODZIN (Odrzucamy przeszłość)
    hours = payload.get("hours", [])
    hp = [h for h in hours if h.get("source") == "openmeteo"] or [h for h in hours if h.get("source") == "yrno"]
    
    future_hours = []
    for h in hp:
        try:
            t_str = h["time_local"].replace("Z", "+00:00")
            dt = datetime.fromisoformat(t_str)
            
            if dt.tzinfo is None:
                from datetime import timezone
                dt = dt.replace(tzinfo=timezone.utc)
                
            dt = dt.astimezone(tz)
            
            if dt >= now_floored:
                future_hours.append((dt, h))
        except Exception:
            continue
            
    # Bierzemy 12 najbliższych godzin
    ta_tuples = future_hours[:12]
    
    if not ta_tuples:
        raise ValueError("Brak przyszłych godzin w danych!")
        
    start_dt = ta_tuples[0][0]

    # --- CIŚNIENIE I TREND DLA HERO ---
    def _hour(h_dict):
        try: return datetime.fromisoformat(h_dict["time_local"].replace("Z", "+00:00")).hour
        except: return 0

    current_h = next((h for h in hp if _hour_safe(h.get("time_local", "")) == now.hour and h.get("pressure_hpa") is not None), None)
    pressure_hpa = current_h["pressure_hpa"] if current_h else None
    
    pressure_trend = None
    if pressure_hpa:
        future_time = now + timedelta(hours=12)
        fut_date_str = future_time.strftime("%Y-%m-%d")
        future_h = next((h for h in hp if _hour_safe(h.get("time_local", "")) == future_time.hour and h.get("time_local", "").startswith(fut_date_str)), None)
        if future_h and future_h.get("pressure_hpa") is not None:
            pressure_trend = future_h["pressure_hpa"] - pressure_hpa

    # --- BUDOWA HERO (NOWY INTELIGENTNY SILNIK) ---
    temps = [h.get("temp_c", 0) for dt, h in ta_tuples]
    bmin = min(temps) if temps else 0
    bmax = max(temps) if temps else 0
    
    precips = [float(h.get("precip_mm") or 0) for dt, h in ta_tuples]
    max_precip = max(precips) if precips else 0
    
    # Szukamy max POP (prawdopodobieństwo) w oknie 12h
    max_pop = max((float(h.get("precip_prob_pct", h.get("pop_pct", h.get("pop", 0)))) for dt, h in ta_tuples), default=0)
    pop_val = int(max_pop)
    pop_str = f" ({pop_val}%)" if pop_val > 0 else ""
    
    avg_clouds = sum(_eff_cld_consensus(h) for dt, h in ta_tuples) / len(ta_tuples) if ta_tuples else 0
    
    # POPRAWKA WIATRU DLA HERO: Teraz bierzemy pod uwagę potężne porywy z 12 godzin!
    max_wind_12h = max((float(h.get("gust_kmh") or h.get("wind_kmh") or 0) for dt, h in ta_tuples), default=0)

    # Ujednolicona Złota Skala Wiatru (Hero odzywa się dopiero przy zagrożeniach)
    if max_wind_12h >= 100: hero_wind = "potężna wichura"
    elif max_wind_12h >= 80: hero_wind = "wichura"
    elif max_wind_12h >= 60: hero_wind = "silny wiatr"
    else: hero_wind = ""

    # 1. Określenie tła wizualnego (Chmury) z blokadą fizyczną
    if (max_precip > 0.1 or pop_val >= 40) and avg_clouds < 30:
        avg_clouds = 30  

    # Odpytujemy Norwegów, czy w tej chwili na tych współrzędnych słońce jest pod horyzontem
    current_sym = (ta_tuples[0][1].get("symbol_code") or "").lower()
    if "_night" in current_sym:
        hero_is_night = True
    elif "_day" in current_sym:
        hero_is_night = False
    else:
        # Ratunkowy fallback, gdyby pole symbol_code było puste
        hero_is_night = now.hour >= 20 or now.hour < 6

    if avg_clouds <= 10: 
        base_sky = "Bezchmurnie"
        hero_icon_bg = "wk_clear_night" if hero_is_night else "wk_clear"
    elif avg_clouds <= 35: 
        base_sky = "Pogodnie" if hero_is_night else "Słonecznie"
        hero_icon_bg = "wk_moon_one_cloud" if hero_is_night else "wk_sun_one_cloud"
    elif avg_clouds < 70: 
        base_sky = "Przejaśnienia"
        hero_icon_bg = "wk_partlycloudy_night" if hero_is_night else "wk_partlycloudy"
    elif avg_clouds < 85: 
        base_sky = "Dużo chmur"
        hero_icon_bg = "wk_mostly_cloudy"
    else: 
        base_sky = "Pochmurno"
        hero_icon_bg = "wk_overcast"

    # 2. Łączenie chmur z opadami
    if max_precip > 0:
        has_storm = False; has_snow = False; has_sleet = False; has_real_rain = False; has_drizzle = False
        
        for dt, h in ta_tuples:
            prc = float(h.get("precip_mm") or 0)
            if prc > 0:
                tmp = h.get("temp_c", 0)
                sym = h.get("symbol_code") or ""
                w_code = h.get("weather_code")
                cld = _eff_cld_consensus(h)
                
                kind = classify_precip(prc, tmp, sym, w_code)
                
                # JEDNO ŹRÓDŁO PRAWDY: Pytamy funkcji od ikon, jak sklasyfikowała ten opad dla bloku na dole!
                icon = _now_icon(cld, prc, tmp, dt.hour, kind=kind, symbol_code=sym)
                
                if icon in ["wk_storm", "wk_sun_storm"]: has_storm = True
                elif icon in ["wk_snow", "wk_snow_showers", "wk_snow_showers_night"]: has_snow = True
                elif icon == "wk_sleet": has_sleet = True
                elif icon == "wk_drizzle": has_drizzle = True
                elif icon in ["wk_showers", "wk_showers_night", "wk_rain"]: has_real_rain = True
                else: has_real_rain = True # Fallback bezpieczeństwa

        # Żelazna drabinka "Pożerania"
        if has_storm: precip_desc = "burze"; hero_icon = "wk_storm"
        elif has_snow and (has_real_rain or has_drizzle): precip_desc = "deszcz ze śniegiem"; hero_icon = "wk_sleet"
        elif has_sleet: precip_desc = "deszcz ze śniegiem"; hero_icon = "wk_sleet"
        elif has_snow: precip_desc = "śnieg"; hero_icon = "wk_snow"
        elif has_real_rain: precip_desc = "deszcz"; hero_icon = "wk_showers" if avg_clouds < 70 else "wk_rain"
        elif has_drizzle: precip_desc = "mżawka"; hero_icon = "wk_drizzle"
        else: precip_desc = "opady"; hero_icon = "wk_showers" if avg_clouds < 70 else "wk_rain"

        if avg_clouds < 70 and not precip_desc.startswith("przelotn"):
            if precip_desc == "burze": precip_desc = "przelotne burze"
            elif precip_desc == "mżawka": precip_desc = "przelotna mżawka"
            elif "śnieg" in precip_desc or "deszcz" in precip_desc: precip_desc = f"przelotny {precip_desc}"
            else: precip_desc = f"przelotne {precip_desc}"

        sky_desc = f"{precip_desc.capitalize()}{pop_str}"
    else:
        # Ufamy Norwegom! Zero opadów na radarze = brak straszenia wysokim POP.
        sky_desc = base_sky
        hero_icon = hero_icon_bg
        
    if sky_desc == "Bezchmurnie" and 6 <= now.hour < 20:
        if avg_clouds <= 3.0 and max_wind_12h < 30:
            sky_desc = "Bezchmurnie, pogoda jak kryształ"

    # Bezpieczne klejenie drugiej linii Hero (Wiatr + Ciśnienie)
    hero_line2_parts = []
    if hero_wind: 
        hero_line2_parts.append(hero_wind)
        
    if pressure_hpa:
        arr = "→"
        if pressure_trend is not None:
            if pressure_trend >= 2: arr = "↗"
            elif pressure_trend <= -2: arr = "↘"
        hero_line2_parts.append(f"{round(pressure_hpa)} hPa {arr}")
        
    hero_line2 = " · ".join(hero_line2_parts)
    hero_summary = f"{sky_desc}\n{hero_line2}" if hero_line2 else sky_desc 

    # --- BUDOWA 12 BLOKÓW GODZINOWYCH ---
    today_blocks = []
    for dt, h in ta_tuples:
        hour_str = f"{dt.hour:02d}:00"
        
        cld = _eff_cld_consensus(h)
        temp = h.get("temp_c", 0)
        prc = float(h.get("precip_mm") or 0)
        
        # POPRAWKA WIATRU DLA LINII: Porywy stają się nowym standardem
        wind_avg = float(h.get("wind_kmh") or 0)
        wind_gust = float(h.get("gust_kmh") or h.get("wind_gust_kmh") or 0)
        eff_wind = max(wind_avg, wind_gust)
        
        rh = h.get("rh_pct")
        
        feels = _feels_like(temp, wind_avg, rh) # Feels like zostawiamy na "zwykłym" wietrze!
        if feels is None: feels = temp
        
        kind = None
        if prc > 0:
            kind = classify_precip(prc, temp, h.get("symbol_code"), h.get("weather_code"))
            
        # Przekazujemy symbol_code od Norwegów, by wiedzieć kiedy jest noc
        icon = _now_icon(cld, prc, temp, dt.hour, kind=kind, symbol_code=h.get("symbol_code", ""))   
        
        if prc > 0:
            if icon == "wk_drizzle": base_desc = "Mżawka"
            elif icon == "wk_showers": base_desc = "Przelotny deszcz"
            elif icon == "wk_rain": base_desc = "Deszcz"
            elif icon == "wk_snow_showers": base_desc = "Przelotny śnieg"
            elif icon == "wk_snow": base_desc = "Śnieg"
            elif icon == "wk_sleet": base_desc = "Deszcz ze śniegiem"
            elif icon in ["wk_storm", "wk_sun_storm"]: base_desc = "Burza"
            else:
                base_desc = KINDS[kind]["full"].capitalize() if kind and kind in KINDS else "Opad"
                
            desc = f"{base_desc} ({prc} mm)"
        else:
            if cld <= 10: desc = "Bezchmurnie"
            elif cld <= 35: desc = "Pogodnie"
            elif cld < 70: desc = "Przejaśnienia"
            elif cld < 85: desc = "Dużo chmur"
            else: desc = "Pochmurno"
            
        is_precip_alert = prc >= 5.0
        is_temp_alert = temp >= 30 or temp <= -5
        is_wind_alert = eff_wind >= 60

        extra_spans = []
        if abs(feels - temp) >= 2.0:
            extra_spans.append({"text": f"odcz. {round(feels)}°", "style": "meta"})
            
        if eff_wind >= 40:
            if eff_wind >= 100: wind_desc = "potężna wichura"
            elif eff_wind >= 80: wind_desc = "wichura"
            elif eff_wind >= 60: wind_desc = "silny wiatr"
            else: wind_desc = "wietrznie"
            
            w_style = "alert" if is_wind_alert else "meta"
            if extra_spans:
                extra_spans.append({"text": " • ", "style": "meta"})
            extra_spans.append({"text": f"{wind_desc} ({round(eff_wind)} km/h)", "style": w_style})
            
        extra_lines = []
        if extra_spans:
            extra_lines.append({"type": "custom", "spans": extra_spans})
        
        today_blocks.append({
            "label": hour_str,        
            "hours": hour_str,        
            "icon": icon,
            "temp_range": f"{round(temp)}°", 
            "temp_style": "alert" if is_temp_alert else "default",
            "primary_desc": desc,
            "primary_style": "alert" if is_precip_alert else "default",
            "extra_lines": extra_lines
        })

    # === DATOWANIE ŹRÓDŁA DANYCH ===
    model_time_str = payload.get("model_updated_at_local")
    if model_time_str and len(model_time_str) >= 16:
        data_time = model_time_str[11:16]
        time_suffix = f" (dane z {data_time})"
    else:
        time_suffix = ""

    forecast_source = payload.get("forecast_source", "OpenMeteo + Yr.no")

    return {
        "city":                payload["location"]["name"],
        "weekday":             weekday,
        "date":                now.strftime("%d.%m"),
        "report_type":         f"Radar taktyczny{time_suffix}",
        "main_icon":           hero_icon,
        "temp_range":          _fmt_temp(round(bmin), round(bmax)),
        "summary":             hero_summary,
        "context_line":        None,
        "pressure":            None,  
        "air_quality_text":    None,
        "air_quality_color":   None,
        "section_title":       f"Prognoza godzinowa od {start_dt.hour:02d}:00",
        "today_blocks":        today_blocks,
        "next_days":           [],
        "worth_knowing":       [],
        "forecast_source":     forecast_source 
    }