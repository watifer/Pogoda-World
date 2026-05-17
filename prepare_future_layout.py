"""
prepare_future_layout.py — Moduł dedykowany dla komendy /future
Generuje 14-dniową, hybrydową prognozę pogody (Yr.no + Open-Meteo).
"""

from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from prepare_layout import _build_day_summary, DNI_PL, DNI_SHORT, _fmt_temp

def prepare_future_layout_data(payload, now=None):
    tz = ZoneInfo(payload["location"]["tz"])
    now = now or datetime.now(tz)

    # 1. NAJPIERW definiujemy listy (skąd bierzemy dane)
    hours = payload.get("hours", [])
    hy = [h for h in hours if h.get("source") == "yrno"]
    ho = [h for h in hours if h.get("source") == "openmeteo"]

    # 2. DOPIERO TERAZ nasza Magia (Przeszczep procentów z OM do Yr.no)
    # OPTYMALIZACJA O(1): Tworzymy słownik godzin z Open-Meteo dla natychmiastowego dostępu
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
            future_days.append({
                "name": DNI_SHORT[tgt.weekday()] + source_marker,
                "label": f"{DNI_SHORT[tgt.weekday()]} {tgt.strftime('%d.%m')}",
                "icon": summary["icon"],
                "temp_min": summary["temp_min"],
                "temp_max": summary["temp_max"],
                "precip_badge": summary["precip_badge"],
                "descriptor": summary["descriptor"]
            })
            all_temps.extend([summary["temp_min"], summary["temp_max"]])

    overall_min = min(all_temps) if all_temps else 0
    overall_max = max(all_temps) if all_temps else 0
    
    # === BEZPIECZNY HERO OPARTY NA TWARDYCH DANYCH ===
    icons_used = [d.get("icon", "") for d in future_days]
    
    storm_count = sum(1 for i in icons_used if i and "storm" in i)
    snow_count = sum(1 for i in icons_used if i and ("snow" in i or "sleet" in i))
    rain_count = sum(1 for i in icons_used if i and ("rain" in i or "showers" in i or "drizzle" in i))
    
    # DODANE: Licznik porywistego wiatru!
    wind_count = sum(1 for i in icons_used if i and "wind" in i)
    
    # AKTUALIZACJA: Zliczanie nowych ikon słonecznych!
    sun_count = sum(1 for i in icons_used if i and ("clear" in i or "sun_one_cloud" in i))
    
    # AKTUALIZACJA: Dodajemy "mostly_cloudy" do dni z chmurami
    partly_count = sum(1 for i in icons_used if i and ("partlycloudy" in i or "mostly_cloudy" in i))
    
    # Nowe, życiowe proporcje dla 14 dni z dodanym wiatrem!
    if storm_count >= 2: # Burze są groźne, więc 2 dni wystarczą do ostrzeżenia
        hero_icon = "wk_storm"
        hero_summary = "Uwaga na burze w nadchodzących dniach"
    elif wind_count >= 3: # DODANE: 3 dni wichury psują każdy tydzień
        hero_icon = "wk_wind"
        hero_summary = "Uwaga: Niezwykle wietrzne i niebezpieczne dni"
    elif snow_count >= 4:
        hero_icon = "wk_snow"
        hero_summary = "Kierunek na chłodne, śnieżne dni"
    elif rain_count >= 7: # Musi padać przez POŁOWĘ dni, by nazwać trend deszczowym
        hero_icon = "wk_rain"
        hero_summary = "Przewaga deszczowej, mokrej pogody"
    elif sun_count >= 7:
        hero_icon = "wk_clear"
        hero_summary = "Przewaga słonecznej, wyżowej pogody"
    elif rain_count >= 4:
        hero_icon = "wk_showers"
        hero_summary = "Zmienna pogoda z okresowymi opadami"
    elif (sun_count + partly_count) >= 8:
        hero_icon = "wk_partlycloudy"
        hero_summary = "Większość dni pogodnych i przejściowych"
    else:
        hero_icon = "wk_overcast"
        hero_summary = "Przewaga pochmurnej aury"

    # === DATOWANIE ŹRÓDŁA DANYCH ===
    model_time_str = payload.get("model_updated_at_local")
    if model_time_str and len(model_time_str) >= 16:
        data_time = model_time_str[11:16]
        time_suffix = f"dane z {data_time}"  # Bez nawiasu, dla lepszego wyglądu
    else:
        time_suffix = ""
        
    return {
        "city": payload["location"]["name"],
        "weekday": "Prognoza 14-dniowa",  
        "date": "",                       
        "report_type": time_suffix,  # Wstawi się jako np: Prognoza 14-dniowa · dane z 13:17             
        "main_icon": hero_icon,           
        "temp_range": _fmt_temp(overall_min, overall_max),
        "summary": hero_summary,              
        "context_line": "",               # <--- Brak technicznych tekstów
        
        "today_blocks": [], 
        "alerts": [],
        "worth_knowing": None,
        "weekend_teaser": None,
        
        "section_title": "",  
        "next_days_title": "Trend na kolejne 14 dni",
        "next_days": future_days
    }