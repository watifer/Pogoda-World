"""
prepare_now_layout.py — Moduł dedykowany wyłącznie dla komendy /now.
Generuje taktyczną kartę z 12 najbliższymi godzinami od momentu uruchomienia.
"""
from owm_nowcast import get_current_weather, nowcast_note
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# Importujemy sprawdzoną logikę z głównego skryptu (w tym efektywne chmury)
from prepare_layout import _fmt_temp, _feels_like, DNI_PL, _hour_safe, _eff_cld_consensus, _drizzle_hint, _precip_consensus
from i18n import t, DAYS_FULL
from i18n import translate_weather_text
from forecast_text import classify_precip, KINDS
from ui_softening import strip_mm_pct_parens, soften_possible_prefix
from prepare_layout import _fmt_temp, _feels_like, DNI_PL, _hour_safe, _eff_cld_consensus, _drizzle_hint

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
    
    # --- PANCERNA NORMALIZACJA JĘZYKA (Defensive Programming) ---
    raw_lang = str(payload.get("lang", "pl")).strip().lower()
    lang = raw_lang[:2]  # Zabezpiecza przed frazami typu "DE ", "en-US" itp.
    
    # Przetłumaczony dzień tygodnia
    weekday = DAYS_FULL.get(lang, DAYS_FULL["pl"])[now.weekday()]
    
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

    # ══════════════════════════════════════════════════════════
    # NOWOŚĆ: INTELIGENTNA KOREKTA SATELITARNA (OWM) NA SAMYM STARCIE!
    # ══════════════════════════════════════════════════════════
    owm_note = None
    should_call_owm = False
    forecast_source = payload.get("forecast_source", "OpenMeteo + Yr.no")

    # Bramka logiki: czy uderzać do OWM?
    if " + " not in forecast_source: 
        should_call_owm = True
    elif hours:
        # Szybki skan pierwszej godziny na wypadek ukrytego opadu lub wysokiego zachmurzenia
        first_h = ta_tuples[0][1]
        rh = float(first_h.get("rh_pct") or 0)
        mm_now = float(first_h.get("precip_eff_mm", first_h.get("precip_mm")) or 0)
        cld = max(float(first_h.get("clouds_low_pct") or 0) + float(first_h.get("clouds_mid_pct") or 0), float(first_h.get("clouds_pct_yr") or 0))
        if mm_now < 0.1 and rh >= 85 and cld >= 85:
            should_call_owm = True
        # Zawsze sprawdzamy chmury do fuzji dla /now!
        should_call_owm = True  # Celowo wymuszamy call dla taktycznego radaru

    if should_call_owm:
        from owm_nowcast import get_current_weather, nowcast_note, apply_cloud_correction
        
        # Pobieramy PRAWDZIWE dane z satelity na żywo:
        owm = get_current_weather(payload["location"]["lat"], payload["location"]["lon"], timeout_sec=8)
        
        if owm:
            # 1. NAJPIERW notatka (zanim skasujemy stare dane z modelu!)
            owm_note = nowcast_note(payload_hours=payload.get("hours", []), now_local=now, owm=owm, lang=lang)
            
            # 2. DOPIERO POTEM ratunkowe nadpisanie ikon i chmur w pamięci RAM na 3 godziny
            apply_cloud_correction(ta_tuples, owm)

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
    
    precips = [float(h.get("precip_eff_mm", h.get("precip_mm")) or 0) for dt, h in ta_tuples]
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
            prc = float(h.get("precip_eff_mm", h.get("precip_mm")) or 0)
            if prc > 0:
                tmp = h.get("temp_c", 0)
                sym = h.get("symbol_code_eff", h.get("symbol_code")) or ""
                w_code = h.get("weather_code_eff", h.get("weather_code"))
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
        prc = float(h.get("precip_eff_mm", h.get("precip_mm")) or 0)
        
        # POPRAWKA WIATRU DLA LINII: Porywy stają się nowym standardem
        wind_avg = float(h.get("wind_kmh") or 0)
        wind_gust = float(h.get("gust_kmh") or h.get("wind_gust_kmh") or 0)
        eff_wind = max(wind_avg, wind_gust)
        
        rh = h.get("rh_pct")
        
        feels = _feels_like(temp, wind_avg, rh) # Feels like zostawiamy na "zwykłym" wietrze!
        if feels is None: feels = temp
        
        kind = None
        # Pobieramy pełną listę "hours" z payloadu do rzetelnego konsensusu
        hours_all = payload.get("hours", [])
        
        # Wyliczamy opad z prawdziwego konsensusu obu modeli
        prc_consensus = _precip_consensus(h, hours_all) if hours_all else prc
        
        if prc_consensus > 0:
            kind = classify_precip(prc_consensus, temp, h.get("symbol_code"), h.get("weather_code"))
            
        # Przekazujemy symbol_code od Norwegów, by wiedzieć kiedy jest noc
        icon = _now_icon(cld, prc_consensus, temp, dt.hour, kind=kind, symbol_code=h.get("symbol_code", ""))   
        
        if prc > 0:
            if icon == "wk_drizzle": base_desc = "Mżawka"
            elif icon == "wk_showers": base_desc = "Przelotny deszcz"
            elif icon == "wk_rain": base_desc = "Deszcz"
            elif icon == "wk_snow_showers": base_desc = "Przelotny śnieg"
            elif icon == "wk_snow": base_desc = "Śnieg"
            elif icon == "wk_sleet": base_desc = "Deszcz ze śniegiem"
            elif icon in ["wk_storm", "wk_sun_storm"]: base_desc = "Burza"
            else:
                # Zabezpieczamy tłumaczenie - bierzemy polski string, żeby Ostatnia Mila mogła go przerobić
                base_desc = t("pl", KINDS[kind]["full_key"]).capitalize() if kind and kind in KINDS else "Opad"
                
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
            # Pobieramy prefiks prosto ze słownika (odcz. dla PL, feels dla EN)
            feels_prefix = t(lang, "feels_like_prefix")
            extra_spans.append({"text": f"{feels_prefix}{round(feels)}°", "style": "meta"})
            
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
        
    # --- UI softening dla /now: spójne z prepare_layout ---
    soft_now = (pop_val >= 60 and max_precip < 0.2)
    if soft_now:
        # 1) hero: jeśli było o opadach, zmiękcz
        parts = (hero_summary or "").split("\n", 1)
        if parts:
            parts[0] = soften_possible_prefix(strip_mm_pct_parens(parts[0]), lang=lang)
            hero_summary = "\n".join(parts)
            
        # 2) godziny: zdejmij mm i dodaj prefiks zależny od znormalizowanego języka
        for b in today_blocks:
            pd_desc = b.get("primary_desc", "")
            pd2 = strip_mm_pct_parens(pd_desc)
            pd2 = soften_possible_prefix(pd2, lang=lang)
            b["primary_desc"] = pd2
    

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

    # Dynamiczny dzień dla okna 12h w /now
    is_dynamic_now = (max_precip >= 1.0) or (max_wind_12h >= 45) or (pop_val >= 60)
    
    # Ostrzeżenie aktywuje się tylko w dynamiczne poranki oparte na nocnym runie
    show_age_note = is_morning_report and is_night_run and is_dynamic_now
    
    now_context_line = "Nocne dane — możliwa korekta prognozy rano." if show_age_note else None

    forecast_source = payload.get("forecast_source", "OpenMeteo + Yr.no")
    
    ta_now = [h for dt, h in ta_tuples]
    
    # 1. Sprawdzamy sensor mżawki z głównego payloadu (zawsze warto mieć w zanadrzu)
    hint = _drizzle_hint(ta=ta_now, hp_all=hours, start_hour=start_dt.hour)

    # ==================================================================
    # NOWY KOD: Radar wiatru od morza na żywo (/now)
    # ==================================================================
    coastal_note = None
    try:
        from main_card import GLOBAL_COAST_STORE, ensure_coast_index
        if GLOBAL_COAST_STORE and ensure_coast_index:
            from coast_detector import get_or_compute_coast_signature, is_onshore, CoastSignature
            loc_lat = payload.get("location", {}).get("lat")
            loc_lon = payload.get("location", {}).get("lon")
            if loc_lat is not None and loc_lon is not None:
                
                # 1. Pytamy lekki plik Cache (łączymy współrzędne w klucz)
                cache_key = f"{round(loc_lat, 2)}_{round(loc_lon, 2)}"
                sig = GLOBAL_COAST_STORE.get(cache_key)
                
                if isinstance(sig, dict):
                    sig = CoastSignature(**sig)
                
                # 2. Brak w Cache -> ładujemy mapę
                if not sig:
                    idx = ensure_coast_index()
                    sig = get_or_compute_coast_signature(idx, GLOBAL_COAST_STORE, loc_lat, loc_lon)
                    
                if sig and getattr(sig, "is_coastal", False) and getattr(sig, "sea_sectors", None):
                    dist = getattr(sig, "distance_to_ocean_km", 999.0) or 999.0
                    add_thresh = 8.0 if dist > 10.0 else 0.0

                    # Skanujemy najbliższe 3 godziny
                    for idx_h, h in enumerate(ta_now[:3]):
                        wind_dir = float(h.get("wind_dir_deg", 0))
                        wind_spd = float(h.get("wind_kmh", 0))
                        gust = float(h.get("gust_kmh", h.get("wind_gust_kmh", 0)))
                        eff_wind = max(wind_spd, gust)
                        
                        if is_onshore(wind_dir, sig.sea_sectors):
                            if wind_spd >= (18.0 + add_thresh) or eff_wind >= (25.0 + add_thresh):
                                g_txt = f", porywy do {round(eff_wind)} km/h" if eff_wind > wind_spd else ""
                                
                                if idx_h == 0:
                                    coastal_note = f"🌬️ Wybrzeże: wiatr od wody {round(wind_spd)} km/h{g_txt} — na otwartym brzegu mocniej."
                                else:
                                    coastal_note = f"🌬️ Wybrzeże: w ciągu 1-2h wiatr od wody (do {round(eff_wind)} km/h) — na plaży mocniej."
                                break
    except Exception as e:
        print(f"[SYSTEM] Błąd modułu nadmorskiego w /now: {e}")
    # ==================================================================

    # 3. Kaskada priorytetów (Wiatr taktyczny przed informacją o satelicie!)
    context_line = now_context_line or coastal_note or owm_note or hint
    
    
    # ══════════════════════════════════════════════════════════
    # OSTATNIA MILA: TŁUMACZENIE DLA KOMENDY /now (TYLKO RAZ!)
    # ══════════════════════════════════════════════════════════
    if lang != "pl":
        if hero_summary:
            hero_summary = translate_weather_text(hero_summary, lang)
        if context_line:
            context_line = translate_weather_text(context_line, lang)
        
        # --- PANCERNY HELPER DO WIELKICH LITER (Odporny na spacje, "·" i "•") ---
        def _smart_cap(val: str) -> str:
            if not val or not isinstance(val, str):
                return val
            for i, char in enumerate(val):
                if char.isalpha():
                    return val[:i] + char.upper() + val[i+1:]
            return val

        for block in today_blocks or []:
            if block.get("primary_desc"): 
                block["primary_desc"] = _smart_cap(translate_weather_text(block["primary_desc"], lang))
                
            # Bezpieczna mutacja extra_lines (obsługa dict oraz str, tak jak w prepare_layout)
            extra = block.get("extra_lines", []) or []
            for i, el in enumerate(extra):
                if isinstance(el, str):
                    extra[i] = _smart_cap(translate_weather_text(el, lang))
                elif isinstance(el, dict):
                    # 1. Tłumaczymy i podnosimy główny tekst (jeśli istnieje)
                    if el.get("text"):
                        el["text"] = _smart_cap(translate_weather_text(el["text"], lang))
                    # 2. NIEZALEŻNIE tłumaczymy i podnosimy spany
                    if isinstance(el.get("spans"), list):
                        for sp in el["spans"]:
                            if isinstance(sp, dict) and sp.get("text"): 
                                sp["text"] = _smart_cap(translate_weather_text(sp["text"], lang))
    # ══════════════════════════════════════════════════════════
    
    return {
        "city":                payload["location"]["name"],
        "weekday":             weekday,
        "date":                now.strftime("%d.%m"),
        "report_type":         f"{t(lang, 'tactical_radar')}{time_suffix}",
        "main_icon":           hero_icon,
        "temp_range":          _fmt_temp(round(bmin), round(bmax)),
        "summary":             hero_summary,
        "context_line":        context_line,  # <--- Skompilowany context_line
        "pressure":            None,  
        "air_quality_text":    None,
        "air_quality_color":   None,
        "section_title":       t(lang, "section_hourly_from", h=start_dt.hour),
        "today_blocks":        today_blocks,
        "next_days":           [],
        "worth_knowing":       [],
        "forecast_source":     forecast_source,
        "source_label":        t(lang, "source_label")
    }