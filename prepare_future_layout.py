"""
prepare_future_layout.py — Moduł dedykowany dla komendy /future
Generuje 14-dniową, hybrydową prognozę pogody (Yr.no + Open-Meteo).
"""

from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from prepare_layout import _build_day_summary, _fmt_temp
from worth_knowing import get_moon_phase_for_local_date
from ui_softening import strip_mm_pct_parens
from i18n import t, DAYS_SHORT, DAYS_FULL
from i18n import translate_weather_text

def prepare_future_layout_data(payload, now=None):
    tz = ZoneInfo(payload["location"]["tz"])
    now = now or datetime.now(tz)

    # Wyciągamy język (z twardym fallbackiem na polski)
    #lang = payload.get("lang", "pl")
    # Zabezpieczamy i normalizujemy zmienną lang
    raw_lang = str(payload.get("lang", "pl")).strip().lower()
    lang = raw_lang[:2]
    
    from daily_source import pick_hours_for_daily_summary

    # 1. NAJPIERW definiujemy listy (bezpieczna kopia, żeby nie mutować payloadu!)
    raw_hours = payload.get("hours", [])
    safe_hours = [h.copy() for h in raw_hours]
    
    hy = [h for h in safe_hours if h.get("source") == "yrno"]
    ho = [h for h in safe_hours if h.get("source") == "openmeteo"]

    # 2. DOPIERO TERAZ nasza Magia (Przeszczep procentów z OM do Yr.no na kopii)
    om_dict = {h.get("time_local"): h for h in ho if "time_local" in h}
    for y_hour in hy:
        time_key = y_hour.get("time_local")
        if time_key and time_key in om_dict:
            pop = om_dict[time_key].get("precip_prob_pct")
            if pop is not None:
                y_hour["precip_prob_pct"] = pop

    future_days = []
    all_temps = []
    daily_diag_dict = payload.get("daily_diag", {})

    for off in range(1, 15):
        tgt = now + timedelta(days=off)
        ts = tgt.strftime("%Y-%m-%d")

        # 3. Zcentralizowany wybór źródła
        pick = pick_hours_for_daily_summary(safe_hours, daily_diag_dict, ts)
        summary = _build_day_summary(pick.hp, ts)
        source_marker = pick.marker
        if summary:
            import os
            ENABLE_VOLATILITY_UI = os.getenv("ENABLE_VOLATILITY_UI", "1") == "1"
            diag = payload.get("daily_diag", {}).get(ts, {})
            
            if ENABLE_VOLATILITY_UI and diag.get("is_volatile"):
                if diag.get("n_om", 0) >= 6 and diag.get("n_yr", 0) >= 3:
                    
                    # Sprawdzamy czy dzień jest spokojny
                    icon_name = summary.get("icon", "")
                    has_bad_weather = bool(summary.get("precip_badge")) or any(x in icon_name for x in ["rain", "storm", "snow", "sleet", "showers", "wind"])
                    
                    if not has_bad_weather:
                        max_diff = float(diag.get("spread_max", diag.get("spread", 0)) or 0.0)
                        min_diff = float(diag.get("spread_min", 0) or 0.0)
                        
                        if max_diff >= min_diff:
                            alt_temp = diag.get("max_yr") if pick.source == "openmeteo" else diag.get("max_om")
                            pora_full = t(lang, "diff_day")
                            base_val = summary.get("temp_max")
                        else:
                            alt_temp = diag.get("min_yr") if pick.source == "openmeteo" else diag.get("min_om")
                            pora_full = t(lang, "diff_night")
                            base_val = summary.get("temp_min")
                            
                        if alt_temp is not None and base_val is not None:
                            alt_val = int(round(float(alt_temp)))
                            
                            # TWARDY WARUNEK: Rozbieżność musi wynosić min. 2 stopnie
                            if abs(alt_val - int(base_val)) >= 2:
                                
                                # --- POBIERANIE Z FALLBACKAMI ---
                                warn_word = t(lang, "possible_alert")
                                if warn_word == "possible_alert":
                                    warn_word = "Possible" if lang == "en" else "Możliwe"
                                    
                                if max_diff >= min_diff:
                                    pora_short = t(lang, "diff_day_short")
                                    if pora_short == "diff_day_short":
                                        pora_short = "day" if lang == "en" else "dzień"
                                else:
                                    pora_short = t(lang, "diff_night_short")
                                    if pora_short == "diff_night_short":
                                        pora_short = "night" if lang == "en" else "noc"

                                # TWORZYMY ZUPEŁNIE NOWE POLE SEMANTYCZNE (krótki, zwarty tekst)
                                summary["diag_tag"] = f"⚠️ {warn_word}: {alt_val}° {pora_short}"
                                summary["diag_severity"] = "caution"

            short_day_name = DAYS_SHORT.get(lang, DAYS_SHORT["pl"])[tgt.weekday()]
            date_short = tgt.strftime("%d.%m")
            day_entry = {
                "name": short_day_name + source_marker,
                "label": f"{short_day_name} {date_short}",
                "icon": summary["icon"],
                "temp_min": summary["temp_min"],
                "temp_max": summary["temp_max"],
                "precip_badge": summary["precip_badge"],
                "descriptor": summary["descriptor"],
                "severity": summary.get("severity", "normal")
            }
            moon_info = get_moon_phase_for_local_date(tgt.date(), tz)
            if moon_info:
                phase = moon_info["phase"]
                label_key = "trend_moon_full_date" if phase == "full" else "trend_moon_new_date"
                day_entry["moon_phase"] = phase
                day_entry["moon_icon"] = moon_info["icon"]
                day_entry["moon_date_short"] = date_short
                day_entry["moon_hero_label"] = t(lang, label_key, date=date_short)

            future_days.append(day_entry)
            all_temps.extend([summary["temp_min"], summary["temp_max"]])

    overall_min = min(all_temps) if all_temps else 0
    overall_max = max(all_temps) if all_temps else 0
    
    # --- UI ujednolicenie: zdejmujemy %/mm w nawiasach z opisów w trendzie 14 dni ---
    for d in future_days:
        if d.get("precip_badge"):
            d["precip_badge"] = strip_mm_pct_parens(d["precip_badge"])
        if d.get("descriptor"):
            d["descriptor"] = strip_mm_pct_parens(d["descriptor"])
    
    # === BEZPIECZNY HERO OPARTY NA TWARDYCH DANYCH ===
    icons_used = [d.get("icon", "") for d in future_days]
    
    storm_count = sum(1 for i in icons_used if i and "storm" in i)
    snow_count = sum(1 for i in icons_used if i and ("snow" in i or "sleet" in i))
    rain_count = sum(1 for i in icons_used if i and ("rain" in i or "showers" in i or "drizzle" in i))
    wind_count = sum(1 for i in icons_used if i and "wind" in i)
    sun_count = sum(1 for i in icons_used if i and ("clear" in i or "sun_one_cloud" in i))
    partly_count = sum(1 for i in icons_used if i and ("partlycloudy" in i or "mostly_cloudy" in i))
    
    # Nowe, życiowe proporcje dla 14 dni z przetłumaczonym nagłówkiem Hero!
    if storm_count >= 2:
        hero_icon = "wk_storm"
        hero_summary = t(lang, "hero_storm_trend")
    elif wind_count >= 3:
        hero_icon = "wk_wind"
        hero_summary = t(lang, "hero_wind_trend")
    elif snow_count >= 4:
        hero_icon = "wk_snow"
        hero_summary = t(lang, "hero_snow_trend")
    elif rain_count >= 7:
        hero_icon = "wk_rain"
        hero_summary = t(lang, "hero_rain_trend")
    elif sun_count >= 7:
        hero_icon = "wk_clear"
        hero_summary = t(lang, "hero_sun_trend")
    elif rain_count >= 4:
        hero_icon = "wk_showers"
        hero_summary = t(lang, "hero_showers_trend")
    elif (sun_count + partly_count) >= 8:
        hero_icon = "wk_partlycloudy"
        hero_summary = t(lang, "hero_partly_trend")
    else:
        hero_icon = "wk_overcast"
        hero_summary = t(lang, "hero_overcast_trend")

    # === DATOWANIE ŹRÓDŁA DANYCH ===
    model_time_str = payload.get("model_updated_at_local")
    if model_time_str and len(model_time_str) >= 16:
        data_time = model_time_str[11:16]
        time_suffix = f"{t(lang, 'data_from')} {data_time}"  # Wykorzystuje "data from" / "dane z"
    else:
        time_suffix = ""
    
    # === DYNAMICZNA LICZBA DNI ===
    actual_days = len(future_days)
    if actual_days == 0:
        actual_days = 14
        
    # Pobieramy Twoje gotowe nagłówki ze słownika
    dynamic_weekday = t(lang, "outlook_14d")
    dynamic_title = t(lang, "next_14d_trend")
    
    # Jeśli API zwróci inną liczbę dni niż 14, po prostu podmieniamy cyfrę w gotowym tłumaczeniu!
    if actual_days != 14:
        dynamic_weekday = dynamic_weekday.replace("14", str(actual_days))
        dynamic_title = dynamic_title.replace("14", str(actual_days))
            
    # ══════════════════════════════════════════════════════════
    # OSTATNIA MILA: TŁUMACZENIE DLA KOMENDY /future
    # ══════════════════════════════════════════════════════════
    if lang != "pl":
        if hero_summary:
            hero_summary = translate_weather_text(hero_summary, lang)
        
        # --- PANCERNY HELPER DO WIELKICH LITER (Odporny na spacje, "·" i "•") ---
        def _smart_cap(val: str) -> str:
            if not val or not isinstance(val, str):
                return val
            for i, char in enumerate(val):
                if char.isalpha():
                    return val[:i] + char.upper() + val[i+1:]
            return val

        for d in future_days or []:
            if d.get("precip_badge"): 
                d["precip_badge"] = _smart_cap(translate_weather_text(d["precip_badge"], lang))
            if d.get("descriptor"): 
                d["descriptor"] = _smart_cap(translate_weather_text(d["descriptor"], lang))
    # ══════════════════════════════════════════════════════════
        
    return {
        "city": payload["location"]["name"],
        "weekday": dynamic_weekday,  
        "date": "",                       
        "report_type": time_suffix,             
        "main_icon": hero_icon,           
        "temp_range": _fmt_temp(overall_min, overall_max),
        "summary": hero_summary,              
        "context_line": "",               
        
        "today_blocks": [], 
        "alerts": [],
        "worth_knowing": None,
        "weekend_teaser": None,
        
        "section_title": "",  
        "next_days_title": dynamic_title,
        "next_days": future_days,
        "source_label": t(lang, "source_label")
    }