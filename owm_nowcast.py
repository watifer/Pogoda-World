# owm_nowcast.py
from __future__ import annotations
import os
import time
import json
import requests
from typing import Optional, Dict, Tuple
from datetime import datetime

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

def nowcast_note(payload_hours: list, now_local: datetime, owm: dict) -> Optional[str]:
    """
    Niezależny arbiter: koryguje chmury i ukryte opady na bazie stanu 'teraz' z OWM 4.0.
    """
    if not owm or not payload_hours:
        return None

    # ZAKTUALIZOWANY PARSER ZGODNIE Z DOKUMENTACJĄ 4.0 (tablica 'data')
    data_array = owm.get("data", [])
    if not data_array or not isinstance(data_array, list):
        return None
        
    current = data_array[0]
    
    owm_clouds = current.get("clouds")
    if owm_clouds is None:
        return None
    owm_clouds = float(owm_clouds)

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

    # 1. PRIORYTET: Detekcja ukrytego opadu (OWM widzi wodę, model ma 0.0)
    if model_precip < 0.1 and owm_precip >= 0.2:
        return "Lokalnie możliwe słabe opady poza prognozą."

    # 2. DRUGI PLAN: Detekcja błędu w zachmurzeniu
    def eff(x: dict) -> float:
        low = x.get("clouds_low_pct")
        mid = x.get("clouds_mid_pct")
        if low is not None and mid is not None:
            return min(100.0, float(low) + float(mid))
        return float(x.get("clouds_pct") or 0)

    om_eff = eff(h)
    yr_eff = None
    if h.get("clouds_low_pct_yr") is not None and h.get("clouds_mid_pct_yr") is not None:
        yr_eff = min(100.0, float(h.get("clouds_low_pct_yr")) + float(h.get("clouds_mid_pct_yr")))
    elif h.get("clouds_pct_yr") is not None:
        yr_eff = float(h.get("clouds_pct_yr"))

    model_eff = max(om_eff, yr_eff) if yr_eff is not None else om_eff

    # Uruchomienie notatki chmurowej tylko przy różnicy > 40%
    if abs(owm_clouds - model_eff) < 40:
        return None

    if owm_clouds >= 70 and model_eff <= 30:
        return "Teraz więcej chmur niż w prognozie."
    if owm_clouds <= 30 and model_eff >= 70:
        return "Teraz mniej chmur niż w prognozie."
    return "Teraz zachmurzenie może odbiegać od prognozy."