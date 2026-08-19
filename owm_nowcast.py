# owm_nowcast.py
from __future__ import annotations
import os
import time
import json
import requests
from typing import Optional, Dict, Tuple
from datetime import datetime
from i18n import STRINGS

_CACHE: Dict[Tuple[float, float], Tuple[float, dict]] = {}
TTL_SEC = 600  # 10 min cache
USAGE_FILE = "owm_usage.json"
MAX_CALLS_PER_DAY = 950  # Zapas 50 zapytań do darmowego limitu

def _key(lat: float, lon: float) -> tuple[float, float]:
    return (round(float(lat), 3), round(float(lon), 3))

def _check_and_increment_limit() -> bool:
    """Sprawdza i zapisuje dzienne zużycie API w pliku JSON."""
    today = datetime.now().strftime("%Y-%m-%d")
    usage = {"date": today, "count": 0}
    
    if os.path.exists(USAGE_FILE):
        try:
            with open(USAGE_FILE, "r") as f:
                saved = json.load(f)
                if saved.get("date") == today:
                    usage["count"] = saved.get("count", 0)
        except Exception:
            pass

    if usage["count"] >= MAX_CALLS_PER_DAY:
        print(f"[OWM] Uwaga: Przekroczono bezpieczny limit {MAX_CALLS_PER_DAY} zapytań/dzień!")
        return False

    usage["count"] += 1
    try:
        with open(USAGE_FILE, "w") as f:
            json.dump(usage, f)
    except Exception:
        pass

    return True

def get_current_weather(lat: float, lon: float, timeout_sec: int = 8) -> Optional[dict]:
    api_key = os.environ.get("OWM_API_KEY")
    if not api_key:
        return None

    k = _key(lat, lon)
    now = time.time()
    
    # 1. Sprawdzamy Cache
    if k in _CACHE:
        ts, data = _CACHE[k]
        if now - ts < TTL_SEC:
            return data

    # 2. Sprawdzamy limit zapytań
    if not _check_and_increment_limit():
        return None

    # ZAKTUALIZOWANY URL DLA OWM 4.0
    url = "https://api.openweathermap.org/data/4.0/onecall/current"
    params = {
        "lat": lat,
        "lon": lon,
        "appid": api_key,
        "units": "metric",
    }

    try:
        r = requests.get(url, params=params, timeout=timeout_sec)
        if r.status_code != 200:
            return None
        data = r.json()
        # --- TYMCZASOWY DEBUG OWM 4.0 ---
        with open("debug_owm.json", "w", encoding="utf-8") as df:
            json.dump(data, df, indent=2, ensure_ascii=False)
        # --------------------------------
        _CACHE[k] = (now, data)
        return data
    except Exception:
        return None

def nowcast_note(payload_hours: list, now_local: datetime, owm: dict, lang: str = "pl") -> Optional[str]:
    """
    Niezależny arbiter: wykrywa tylko ukryty opad na bazie stanu 'teraz' z OWM 4.0.
    (Korekta zachmurzenia odbywa się całkowicie w tle przez apply_cloud_correction).
    """
    if not owm or not payload_hours:
        return None

    # ZAKTUALIZOWANY PARSER ZGODNIE Z DOKUMENTACJĄ 4.0 (tablica 'data')
    data_array = owm.get("data", [])
    if not data_array or not isinstance(data_array, list):
        return None
        
    current = data_array[0]

    # Ekstrakcja opadów OWM
    rain_1h = current.get("rain", {}).get("1h", 0) if isinstance(current.get("rain"), dict) else 0
    snow_1h = current.get("snow", {}).get("1h", 0) if isinstance(current.get("snow"), dict) else 0
    owm_precip = float(rain_1h) + float(snow_1h)

    today_str = now_local.strftime("%Y-%m-%d")
    hh = now_local.hour
    
    h = next((x for x in payload_hours
              if (x.get("time_local", "").startswith(today_str)
                  and len(x.get("time_local", "")) >= 13
                  and int(x["time_local"][11:13]) == hh)), None)
                  
    if h is None:
        return None

    model_precip = float(h.get("precip_mm") or 0)

    # PRIORYTET: Detekcja ukrytego opadu (OWM widzi wodę, model ma 0.0)
    if model_precip < 0.1 and owm_precip >= 0.2:
        return STRINGS[lang].get("nowcast_precip", "")

    # Jeśli nie ma ukrytego opadu, zachowujemy milczenie w UI
    return None
    
def apply_cloud_correction(ta_tuples: list, owm: dict):
    """
    Agresywnie koryguje chmury na 3 pierwsze bloki karty /now, 
    relaksując dane płynnie z powrotem do uśrednionego modelu.
    """
    if not owm or not ta_tuples:
        return

    data_array = owm.get("data", [])
    if not data_array:
        return

    owm_clouds = data_array[0].get("clouds")
    if owm_clouds is None:
        return
    owm_clouds = float(owm_clouds)

    h0 = ta_tuples[0][1]
    
    # Wyliczamy efektywne chmury modelu dla godziny "0"
    low = h0.get("clouds_low_pct")
    mid = h0.get("clouds_mid_pct")
    if low is not None and mid is not None:
        model_eff = min(100.0, float(low) + float(mid))
    else:
        model_eff = float(h0.get("clouds_pct") or 0)

    # 40% rozjazdu to sygnał do interwencji
    diff = model_eff - owm_clouds
    if abs(diff) >= 40:
        # Korygujemy tylko tyle bloków, ile fizycznie istnieje (max 3)
        steps = min(3, len(ta_tuples))
        for step in range(steps):
            target_h = ta_tuples[step][1]
            
            # Waga powrotu do modelu: 0% -> 33% -> 66%
            correction_factor = step / 3.0
            new_clouds = owm_clouds + (diff * correction_factor)
            new_clouds = max(0.0, min(100.0, new_clouds))

            # Brutalne nadpisanie danych o chmurach (kasujemy też ślad norweski!)
            target_h["clouds_pct"] = new_clouds
            target_h["clouds_low_pct"] = new_clouds
            target_h["clouds_mid_pct"] = 0
            target_h["clouds_high_pct"] = 0
            
            target_h["clouds_pct_yr"] = new_clouds
            target_h["clouds_low_pct_yr"] = new_clouds
            target_h["clouds_mid_pct_yr"] = 0
            target_h["clouds_high_pct_yr"] = 0

            # Korygujemy kody (TYLKO jeśli model przewidywał suchą pogodę)
            current_code = target_h.get("weather_code")
            if current_code in [0, 1, 2, 3] or current_code is None:
                if new_clouds < 15: new_code = 0
                elif new_clouds < 40: new_code = 1
                elif new_clouds < 70: new_code = 2
                else: new_code = 3
                
                target_h["weather_code"] = new_code
                target_h["symbol_code"] = ""  # Wymusza przeliczenie na podstawie weather_code