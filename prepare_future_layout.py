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
    
    # 1. NAJPIERW definiujemy listy (skąd bierzemy dane)
    hours = payload.get("hours", [])
    hy = [h for h in hours if h.get("source") == "yrno"]
    ho = [h for h in hours if h.get("source") == "openmeteo"]

    # 2. DOPIERO TERAZ nasza Magia (Przeszczep procentów z OM do Yr.no)
    om_dict = {h.get("time_local"): h for h in ho if "time_local" in h}
    
    for y_hour in hy:
        time_key = y_hour.get("time_local")
        if time_key and time_key in om_dict:
            o_hour = om_dict[time_key]
            pop = o_hour.get("precip_prob_pct")
            if pop is not None:
                y_hour["precip_prob_pct"] = pop

    future_days = []
    all_temps = []

    for off in range(1, 16):
        tgt = now + timedelta(days=off)
        ts = tgt.strftime("%Y-%m-%d")

        summary = _build_day_summary(hy, ts)
        
        source_marker = ""
        if not summary:
            summary = _build_day_summary(ho, ts)
            source_marker = " *"

        if summary:
            short_day_name = DAYS_SHORT.get(lang, DAYS_SHORT["pl"])[tgt.weekday()]
            future_days.append({
                "name": short_day_name + source_marker,
                "label": f"{short_day_name} {tgt.strftime('%d.%m')}",
                "icon": summary["icon"],
                "temp_min": summary["temp_min"],
                "temp_max": summary["temp_max"],
                "precip_badge": summary["precip_badge"],
                "descriptor": summary["descriptor"]
            })
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