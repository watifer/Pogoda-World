"""
prepare_now_layout.py — Moduł dedykowany wyłącznie dla komendy /now.
Generuje taktyczną kartę z 12 najbliższymi godzinami od momentu uruchomienia.
"""
from owm_nowcast import get_current_weather, nowcast_note, classify_owm_cloud_correction, find_model_hour_for_now
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# Importujemy sprawdzoną logikę z głównego skryptu (w tym efektywne chmury)
from prepare_layout import (_fmt_temp, _feels_like, DNI_PL, _hour_safe, _eff_cld_consensus,
                            _drizzle_hint, precip_mm_for_ui, _eff_wind_kmh)
from i18n import t, DAYS_FULL
from i18n import translate_weather_text
from forecast_text import classify_precip, KINDS, is_freezing_precip
from forecast_text import sky_from_clouds
from ui_softening import strip_mm_pct_parens, soften_possible_prefix

# Neutralne tokeny na przyimki czasowe w Hero. Wstawiamy je przed tłumaczeniem
# całego opisu i podmieniamy na t(lang, "from"/"until") już po nim.
PREP_TOKENS = {"from": "\u2e24FROM\u2e25", "until": "\u2e24UNTIL\u2e25"}


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
    forecast_source = payload.get("forecast_source", "OpenMeteo + Yr.no")

    # /now zawsze chce świeży radar satelitarny — ale najpierw konsumujemy snapshot,
    # który weather_payload mógł już dołączyć (payload["owm_current"]).
    # Dopiero jego brak uzasadnia blokujący HTTP w ścieżce użytkownika.
    owm = payload.get("owm_current")
    if not owm:
        try:
            owm = get_current_weather(payload["location"]["lat"], payload["location"]["lon"], timeout_sec=3)
        except Exception:
            owm = None

    if owm:
        # Tworzymy tylko notatkę ratunkową, zjawiska pogodowe nadpisze lokalny override.
        # nowcast_note() ma własny fresh-gate (owm_is_fresh) — stare dane zwrócą None.
        owm_note = nowcast_note(payload_hours=payload.get("hours", []), now_local=now, owm=owm, lang=lang)

    # --- CIŚNIENIE I TREND DLA HERO ---
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

    # POPRAWKA WIATRU DLA HERO: Tutaj skanujemy całe 12h, żeby ostrzec przed nadciągającą wichurą
    max_wind_12h = max((_eff_wind_kmh(h) for dt, h in ta_tuples), default=0)

    # Ujednolicona Złota Skala Wiatru (Hero odzywa się dopiero przy zagrożeniach)
    if max_wind_12h >= 100: hero_wind = "potężna wichura"
    elif max_wind_12h >= 80: hero_wind = "wichura"
    elif max_wind_12h >= 60: hero_wind = "silny wiatr"
    else: hero_wind = ""

    # 1. HORYZONT HERO: Odcinamy daleką przyszłość.
    # Bierzemy tylko 4 najbliższe godziny, żeby deszcz o 03:00 nie psuł słońca o 16:00!
    hero_ta_tuples = ta_tuples[:4]

    # avg_clouds liczymy DOPIERO po korekcie OWM (niżej, po ustaleniu _cld_override).

    # Odpytujemy Norwegów, czy w tej chwili na tych współrzędnych słońce jest pod horyzontem
    current_sym = (ta_tuples[0][1].get("symbol_code") or "").lower()
    if "_night" in current_sym:
        hero_is_night = True
    elif "_day" in current_sym:
        hero_is_night = False
    else:
        hero_is_night = now.hour >= 20 or now.hour < 6

    

    # ==================================================================
    # BAZA CHMUR I TWARDA KOREKTA (LOCAL OVERRIDE DLA GODZINY 0)
    # ==================================================================
    h0 = hero_ta_tuples[0][1] if hero_ta_tuples else {}
    cld_model = _eff_cld_consensus(h0) if h0 else 0
    label_model, icon_model = sky_from_clouds(cld_model, hero_is_night)
    
    cld_now = cld_model
    radar_changed_label = False
    
    h0_is_current_hour = h0 and find_model_hour_for_now([h for _, h in ta_tuples[:1]], now) is h0
    if owm and h0 and h0_is_current_hour:
        owm_cloud = classify_owm_cloud_correction(h0, owm, now, is_night=hero_is_night)
        if owm_cloud.get("should_override_hour0"):
            cld_now = float(owm_cloud["effective_live_clouds"])
            h0["_cld_override"] = cld_now
            radar_changed_label = bool(owm_cloud.get("label_live") != owm_cloud.get("label_model"))
                
    # Zapisz flagę do h0, żeby użyć jej później w Hero
    if h0:
        h0["_radar_changed_label"] = radar_changed_label

    # Wygenerowanie bazy z uwzględnieniem ewentualnej korekty
    base_sky, hero_icon_bg = sky_from_clouds(cld_now, hero_is_night)

    # Średnie zachmurzenie okna Hero — PO korekcie satelitarnej.
    # `_cld_override` jest wpisywany do h0 dopiero powyżej, więc licząc avg_clouds
    # wcześniej dostawaliśmy wartość modelową: radar mógł zdjąć chmury z godziny 0
    # (base_sky = "Słonecznie"), a avg_clouds >= 70 nadal degradowało "przelotny
    # deszcz" do "deszcz" i ikonę wk_showers do wk_rain.
    avg_clouds = (sum(h.get("_cld_override", _eff_cld_consensus(h)) for _, h in hero_ta_tuples)
                  / len(hero_ta_tuples)) if hero_ta_tuples else 0



    # ==================================================================
    # 2. Łączenie chmur z opadami (TYLKO okno 4 godzin Hero)
    # ==================================================================
    def get_prc(h_dict):
        return precip_mm_for_ui(h_dict, hours)

    def get_pop(h_dict):
        v = h_dict.get("precip_prob_pct", h_dict.get("pop_pct", h_dict.get("pop")))
        return float(v) if v is not None else 0.0

    hero = hero_ta_tuples
    prc_vals = [get_prc(h) for _, h in hero]
    pop_vals = [get_pop(h) for _, h in hero]
    max_precip_4h = max(prc_vals) if prc_vals else 0.0

    # Przywrócenie zmiennych dla dolnej części skryptu (Softening i Age-Gating)
    max_precip = max_precip_4h
    pop_val = int(max(pop_vals) if pop_vals else 0)
    
    # Stany opadowe w 4h i “zmienność”
    prc_states = [p > 0.05 for p in prc_vals]  # True = pada
    prc_transitions = sum(1 for i in range(1, len(prc_states)) if prc_states[i] != prc_states[i-1])
    is_precip_now = prc_states[0] if prc_states else False
    is_volatile_precip = prc_transitions > 1
    
    change_hour = None
    change_type = None  # "until" / "from"
    final_pop = 0.0     
    from_desc = None

    # --------------------------------------------------------------
    # A) W OKNIE 4H SĄ OPADY
    # --------------------------------------------------------------
    if max_precip_4h > 0.0:
        has_storm = has_snow = has_sleet = has_real_rain = has_drizzle = False
        
        for (dt, h) in hero:
            prc = get_prc(h)
            if prc <= 0:
                continue
            tmp = h.get("temp_c", 0)
            sym = h.get("symbol_code_eff", h.get("symbol_code")) or ""
            w_code = h.get("weather_code_eff", h.get("weather_code"))
            cld = h.get("_cld_override", _eff_cld_consensus(h))
            
            kind = classify_precip(prc, tmp, symbol_code=sym, weather_code=w_code)
            icon = _now_icon(cld, prc, tmp, dt.hour, kind=kind, symbol_code=sym)
            
            if icon in ["wk_storm", "wk_sun_storm"]: has_storm = True
            elif icon in ["wk_snow", "wk_snow_showers", "wk_snow_showers_night"]: has_snow = True
            elif icon == "wk_sleet": has_sleet = True
            elif icon == "wk_drizzle": has_drizzle = True
            elif icon in ["wk_showers", "wk_showers_night", "wk_rain"]: has_real_rain = True

        if has_storm: precip_plain = "burze"; hero_icon_rain = "wk_storm"
        elif has_snow and (has_real_rain or has_drizzle): precip_plain = "deszcz ze śniegiem"; hero_icon_rain = "wk_sleet"
        elif has_sleet: precip_plain = "deszcz ze śniegiem"; hero_icon_rain = "wk_sleet"
        elif has_snow: precip_plain = "śnieg"; hero_icon_rain = "wk_snow"
        elif has_real_rain: precip_plain = "deszcz"; hero_icon_rain = "wk_showers" if avg_clouds < 70 else "wk_rain"
        elif has_drizzle: precip_plain = "mżawka"; hero_icon_rain = "wk_drizzle"
        else: precip_plain = "opady"; hero_icon_rain = "wk_showers" if avg_clouds < 70 else "wk_rain"

        precip_desc = precip_plain
        if avg_clouds < 70:
            if precip_desc == "burze": precip_desc = "przelotne burze"
            elif precip_desc == "mżawka": precip_desc = "przelotna mżawka"
            elif "śnieg" in precip_desc or "deszcz" in precip_desc: precip_desc = f"przelotny {precip_desc}"
            else: precip_desc = f"przelotne {precip_desc}"

        if is_precip_now:
            sky_desc = precip_desc.capitalize()
            hero_icon = hero_icon_rain
            final_pop = pop_vals[0] 

            if (not is_volatile_precip) and len(hero) > 1:
                for i in range(1, len(hero)):
                    if not prc_states[i]:
                        change_hour = hero[i][0].hour
                        change_type = "until"
                        break
            else:
                sky_desc += " (przelotnie)"
        else:
            sky_desc = base_sky
            hero_icon = hero_icon_bg
            
            if is_volatile_precip:
                sky_desc += f" · przelotnie {precip_plain}"
                final_pop = max(pop_vals)
            else:
                for i in range(1, len(hero)):
                    if prc_states[i]:
                        change_hour = hero[i][0].hour
                        change_type = "from"
                        final_pop = pop_vals[i] 
                        break

    # --------------------------------------------------------------
    # B) BRAK OPADÓW W OKNIE 4H -> przełamania zachmurzenia
    # --------------------------------------------------------------
    else:
        sky_desc = base_sky
        hero_icon = hero_icon_bg
        
        # Skaner musi brać pod uwagę nadpisane chmury z godziny 0!
        cld_states = [h.get("_cld_override", _eff_cld_consensus(h)) for _, h in hero]
        good = [c < 70 for c in cld_states]
        bad  = [c >= 70 for c in cld_states]
        
        cld_trans = sum(1 for i in range(1, len(hero)) if good[i] != good[i-1] or bad[i] != bad[i-1])

        if cld_trans <= 1 and len(hero) > 1:
            if good[0] and not bad[0]:
                for i in range(1, len(hero)):
                    if bad[i]:
                        change_hour = hero[i][0].hour
                        change_type = "until"
                        break
            elif bad[0]:
                for i in range(1, len(hero)):
                    if good[i] and not bad[i]:
                        if all((good[j] and not bad[j]) for j in range(i, len(hero))):
                            change_hour = hero[i][0].hour
                            change_type = "from"
                            target_sky, _ = sky_from_clouds(cld_states[i], hero_is_night)
                            from_desc = target_sky.lower()
                        break

    if sky_desc == "Bezchmurnie" and 6 <= now.hour < 20:
        if avg_clouds <= 3.0 and max(_eff_wind_kmh(h) for _, h in hero) < 30:
            sky_desc = "Bezchmurnie, pogoda jak kryształ"

    # ==================================================================
    # SKLEJANIE FINALNEGO OPISU Z DODATKIEM "DO/OD"
    # ==================================================================
    if change_hour is not None and change_type is not None:
        is_sunny_target = ("słonecz" in sky_desc.lower()) or (from_desc and "słonecz" in from_desc)
        
        if is_sunny_target and (change_hour >= 20 or change_hour <= 4):
            pass 
        else:
            # Placeholder zamiast gotowego słowa: sky_desc jest tłumaczony w całości
            # dokładnie raz (niżej), więc fragment już przetłumaczony przeszedłby przez
            # tłumacza po raz drugi (ES: "a" -> "y"). Z kolei polskie "do"/"od" zostałyby
            # zdegradowane przez REPLACEMENTS ("do" -> "to"/"a" zamiast "until"/"hasta").
            # Token jest neutralny dla tłumacza i podmieniamy go na t(lang, ...) na końcu.
            prep_word = PREP_TOKENS[change_type]
            
            if change_type == "from":
                if max_precip_4h > 0.0:
                    sky_desc += f" · {precip_plain} {prep_word} {change_hour:02d}:00"
                elif from_desc:
                    sky_desc += f" · {from_desc} {prep_word} {change_hour:02d}:00"
                else:
                    sky_desc += f" {prep_word} {change_hour:02d}:00"
            else:
                sky_desc += f" {prep_word} {change_hour:02d}:00"

    pop_val_int = int(round(final_pop))
    if max_precip_4h > 0.0 and pop_val_int > 0:
        sky_desc += f" ({pop_val_int}%)"

    # ==================================================================
    # 3. LATE WARNING (Zagrożenia poza oknem 4h, ale w tabeli 12h)
    # ==================================================================
    # Linia "Później: ..." powstaje od razu w języku docelowym (t(lang, ...)),
    # więc NIE wolno jej wkleić do sky_desc przed tłumaczeniem — doklejamy ją
    # dopiero za ostatnim przebiegiem tłumacza.
    later_line = None
    if max_precip_4h < 1.0:
        for dt_late, h_late in ta_tuples[4:12]:
            prc_late = get_prc(h_late)
            pop_late = get_pop(h_late)
            
            if prc_late >= 1.0 or pop_late >= 70:
                tmp_late = h_late.get("temp_c", 0)
                sym_late = h_late.get("symbol_code_eff", h_late.get("symbol_code")) or ""
                w_code_late = h_late.get("weather_code_eff", h_late.get("weather_code"))
                
                kind_late = classify_precip(prc_late, tmp_late, symbol_code=sym_late, weather_code=w_code_late)
                
                # Zamiast twardego polskiego tekstu, przypisujemy klucze systemowe
                if kind_late in ["storm"]: 
                    late_key = "storms"
                elif kind_late in ["snow", "heavy_snow", "light_snow"] or (tmp_late <= 2.0 and kind_late not in ["sleet"]): 
                    late_key = "snow"
                elif kind_late in ["sleet"]: 
                    late_key = "sleet"
                elif kind_late == "drizzle" or prc_late <= 0.5: 
                    late_key = "drizzle"
                else: 
                    late_key = "rain"
                
                # Tłumaczymy typ opadu, słowo "od" ("from") oraz "Później" w locie.
                # Nazwa opadu z wielkiej litery: po dwukropku wymaga tego niemiecki
                # (rzeczowniki), a wcześniej robił to za nas tłumacz (reguła dwukropka).
                late_name = t(lang, late_key)
                late_name = late_name[:1].upper() + late_name[1:]
                prep_from = t(lang, "from")
                later_str = t(lang, "later")
                
                # Gotowa, w 100% przetłumaczona linijka
                later_line = f"{later_str}: {late_name} {prep_from} {dt_late.hour:02d}:00"
                break

    # ==================================================================
    # ZAKOTWICZENIE CZASOWE DLA HERO W /NOW ("OBECNIE")
    # ==================================================================
    if sky_desc:
        # 1. JEDYNE tłumaczenie Hero. sky_desc jest tu w całości po polsku,
        # więc EXACT_MAPS ma szansę trafić w pełne zdanie, a REPLACEMENTS
        # nie dostaje tekstu, który już raz przez nie przeszedł.
        if lang != "pl":
            sky_desc = translate_weather_text(sky_desc, lang)

        # Tokeny przyimków -> właściwe słowa z i18n (po tłumaczeniu, więc bez ryzyka
        # drugiego przebiegu i bez degradacji "until" do "to").
        for tok_key, tok in PREP_TOKENS.items():
            if tok in sky_desc:
                sky_desc = sky_desc.replace(tok, t(lang, tok_key))

        sky_desc_low = sky_desc[:1].lower() + sky_desc[1:]
        
        # 2. Słowo wprowadzające bierzemy ze słownika UI, nie z tłumacza tekstów
        # pogodowych (REPLACEMENTS["fr"] mapował "obecnie" -> "Currently").
        obecnie_str = t(lang, "currently")
        
        # 3. Sklejamy z TWARDĄ spacją w kodzie (strip() usuwa ewentualne spacje ze słownika)
        sky_desc = f"{obecnie_str.strip()} {sky_desc_low}"
            
        # 4. Podniesienie pierwszej litery całego zdania
        sky_desc = sky_desc[:1].upper() + sky_desc[1:]

    # Linia "Później: ..." jest już w języku docelowym — dopinamy po tłumaczeniu.
    if later_line:
        sky_desc = f"{sky_desc}\n{later_line}" if sky_desc else later_line

    # Bezpieczne klejenie drugiej linii Hero (Wiatr + Ciśnienie)
    hero_line2_parts = []
    if hero_wind: 
        # hero_wind powstaje po polsku ("silny wiatr"/"wichura") i nigdy nie
        # przechodził przez tłumacza po zmianie na hero_prelocalized.
        hero_line2_parts.append(translate_weather_text(hero_wind, lang) if lang != "pl" else hero_wind)
        
    if pressure_hpa:
        arr = "→"
        if pressure_trend is not None:
            if pressure_trend >= 2: arr = "↗"
            elif pressure_trend <= -2: arr = "↘"
        hero_line2_parts.append(f"{round(pressure_hpa)} hPa {arr}")
        
    hero_line2 = " · ".join(hero_line2_parts)
    hero_summary = f"{sky_desc}\n{hero_line2}" if hero_line2 else sky_desc
    # Hero jest już w 100% w języku docelowym — ostatnia mila go NIE dotyka.
    hero_summary_prelocalized = True

    # --- BUDOWA 12 BLOKÓW GODZINOWYCH ---
    today_blocks = []
    for dt, h in ta_tuples:
        hour_str = f"{dt.hour:02d}:00"
        
        # Pobranie chmur z uwzględnieniem ewentualnego nadpisania
        cld = h.get("_cld_override", _eff_cld_consensus(h))
        temp = h.get("temp_c", 0)
        # Jedno mm na cały blok: ikona, tekst, styl alertowy i softening.
        prc = precip_mm_for_ui(h, hours)
        
        wind_avg = float(h.get("wind_kmh") or 0)
        eff_wind = _eff_wind_kmh(h)
        
        rh = h.get("rh_pct")
        
        feels = _feels_like(temp, wind_avg, rh)
        if feels is None: feels = temp
        
        kind = None
        if prc > 0:
            kind = classify_precip(prc, temp, symbol_code=h.get("symbol_code"),
                                   weather_code=h.get("weather_code"))
            
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
                base_desc = t("pl", KINDS[kind]["full_key"]).capitalize() if kind and kind in KINDS else "Opad"
                
            desc = f"{base_desc} ({round(prc, 1)} mm)"
        else:
            sym_code = h.get("symbol_code", "") or ""
            if "_night" in sym_code.lower():
                is_night_hr = True
            elif "_day" in sym_code.lower():
                is_night_hr = False
            else:
                is_night_hr = dt.hour >= 20 or dt.hour < 6

            desc, _ = sky_from_clouds(cld, is_night_hr)
            
        is_precip_alert = prc >= 5.0
        is_temp_alert = temp >= 30 or temp <= -5
        is_wind_alert = eff_wind >= 60

        extra_spans = []
        if abs(feels - temp) >= 2.0:
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
    # Radar wiatru od morza na żywo (/now)
    # ------------------------------------------------------------------
    # Dwa niezależne tryby:
    #  - marine_storm: globalnie, cały rok, tylko naprawdę groźny wiatr od wody
    #  - beach: lifestyle, tylko PL i tylko sezon 01.06–15.09
    # Teksty budujemy od razu przez t(lang, ...) — bez surowych PL stringów.
    # ==================================================================
    coastal_note = None
    try:
        from coast_detector import GEO_STACK_AVAILABLE, warn_coast_disabled
        from coast_runtime import GLOBAL_COAST_STORE, ensure_coast_index
        if not GEO_STACK_AVAILABLE:
            # Ostrzegamy raz na proces i pomijamy blok. Wcześniej lądowało to
            # w except jako mylące "Błąd modułu nadmorskiego" przy każdym renderze.
            warn_coast_disabled("/now")
        elif GLOBAL_COAST_STORE and ensure_coast_index:
            from coast_detector import (
                get_or_compute_coast_signature_lazy,
                get_coastal_alert_mode,
                MODE_MARINE_STORM,
                MODE_BEACH,
            )

            loc = payload.get("location", {}) or {}
            loc_lat = loc.get("lat")
            loc_lon = loc.get("lon")
            tz_str = loc.get("tz", "UTC")

            if loc_lat is not None and loc_lon is not None:
                sig = get_or_compute_coast_signature_lazy(
                    store=GLOBAL_COAST_STORE, lat=loc_lat, lon=loc_lon, idx_factory=ensure_coast_index
                )

                first_beach = None
                first_storm = None
                storm_max_eff = 0.0

                for idx_h, (dt_local, h) in enumerate(ta_tuples):
                    try:
                        wdir = float(h.get("wind_dir_deg") or 0)
                        wspd = float(h.get("wind_kmh") or 0)
                        gust = float(h.get("gust_kmh") or 0)
                        eff_wind = _eff_wind_kmh(h)
                        hh = dt_local.hour

                        mode = get_coastal_alert_mode(sig, wspd, gust, wdir, tz_str, dt_local)

                        if mode == MODE_MARINE_STORM:
                            if first_storm is None:
                                first_storm = (idx_h, hh, wspd, eff_wind)
                            storm_max_eff = max(storm_max_eff, eff_wind)
                        elif mode == MODE_BEACH and (8 <= hh <= 18) and first_beach is None:
                            first_beach = (idx_h, hh, wspd, eff_wind)
                    except Exception:
                        continue

                if first_storm:
                    # Sztorm ma pierwszeństwo przed lifestyle'owym alertem plażowym.
                    idx_h, hh, wind_spd, eff_wind = first_storm
                    gust_txt = round(max(storm_max_eff, eff_wind))
                    if idx_h == 0:
                        coastal_note = t(lang, "coast_marine_storm_now", gust=gust_txt)
                    elif idx_h <= 2:
                        coastal_note = t(lang, "coast_marine_storm_soon", gust=gust_txt)
                    else:
                        coastal_note = t(lang, "coast_marine_storm_from", hh=f"{hh:02d}", gust=gust_txt)

                elif first_beach:
                    idx_h, hh, wind_spd, eff_wind = first_beach
                    if idx_h == 0:
                        if eff_wind > wind_spd:
                            coastal_note = t(lang, "coast_beach_now_gust",
                                             wind=round(wind_spd), gust=round(eff_wind))
                        else:
                            coastal_note = t(lang, "coast_beach_now", wind=round(wind_spd))
                    elif idx_h <= 2:
                        coastal_note = t(lang, "coast_beach_soon", gust=round(eff_wind))
                    else:
                        coastal_note = t(lang, "coast_beach_from", hh=f"{hh:02d}", gust=round(eff_wind))

    except Exception as e:
        print(f"[SYSTEM] Błąd modułu nadmorskiego w /now: {e}")
    # ==================================================================

    # ==================================================================
    # RADAR GOŁOLEDZI (/now) - ze stałymi i ciągłością czasu
    # ==================================================================
    def _freezing_risk_now(h_dict):
        # Detekcja żyje w forecast_text.is_freezing_precip() — wspólnie z /day.
        # Poprzedni warunek lokalny był nieosiągalny: classify_precip() nie zwraca
        # kindów "freezing_*", a family=="rain" wymaga temp > 2.0 °C.
        return is_freezing_precip(
            precip_mm_for_ui(h_dict, hours),
            h_dict.get("temp_c"),
            symbol_code=h_dict.get("symbol_code_eff", h_dict.get("symbol_code")),
            weather_code=h_dict.get("weather_code_eff", h_dict.get("weather_code")),
            rh_pct=h_dict.get("rh_pct"),
            dewpoint_c=h_dict.get("dewpoint_c"),
        )

    risk_dts_now = []
    for dt_val, h_dict in ta_tuples:
        if _freezing_risk_now(h_dict):
            risk_dts_now.append(dt_val)

    freezing_note = None
    if risk_dts_now:
        def fmt_rng(a, b):
            end = b + timedelta(hours=1)
            end_h = end.hour
            # Zmiana z 00 na 24, gdy to równo północ następnego dnia
            if end_h == 0 and end.date() != a.date():
                end_h = 24
            # Produktowy dopisek: jeśli początek ryzyka to jutro, poinformuj o tym
            prefix = f"{t(lang, 'tomorrow').lower()} " if a.date() > now.date() else ""
            return f"{prefix}{a.hour:02d}–{end_h:02d}"

        def group_hourly_datetimes(dts):
            dts = sorted(list(set(dts))) # set zabezpiecza w razie dubli
            rngs, st, pv = [], dts[0], dts[0]
            for dt in dts[1:]:
                # Używamy bezpiecznego przedziału dla ciągłości 1 godziny
                diff = (dt - pv).total_seconds()
                if 3500 <= diff <= 3700: pv = dt
                else: rngs.append((st, pv)); st = dt; pv = dt
            rngs.append((st, pv))
            return rngs
            
        rng = group_hourly_datetimes(risk_dts_now)
        when = ", ".join(fmt_rng(a, b) for a, b in rng[:2])
        # Treść z i18n: dynamiczne "({when})" rozbijało EXACT_MAPS, więc
        # translate_weather_text() zostawiał ten alert po polsku we WSZYSTKICH językach.
        freezing_note = t(lang, "freezing_alert", when=when)

    # 3. Kaskada priorytetów (Gołoledź najwyżej!)
    context_line = freezing_note or now_context_line or coastal_note or owm_note or hint
    # Notki zbudowane przez t(lang, ...) są już w języku docelowym,
    # więc nie wolno ich przepuścić przez translate_weather_text.
    context_line_prelocalized = bool(
        context_line and context_line in (freezing_note, coastal_note)
    )
    
    
    # ══════════════════════════════════════════════════════════
    # OSTATNIA MILA: TŁUMACZENIE DLA KOMENDY /now (TYLKO RAZ!)
    # ══════════════════════════════════════════════════════════
    if lang != "pl":
        if hero_summary and not hero_summary_prelocalized:
            hero_summary = translate_weather_text(hero_summary, lang)
        if context_line and not context_line_prelocalized:
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