# owm_nowcast.py
from __future__ import annotations
import os
import time
import json
import requests
from typing import Optional, Dict, Tuple
from datetime import datetime
from i18n import STRINGS
from forecast_text import sky_from_clouds

_CACHE: Dict[Tuple[float, float], Tuple[float, dict]] = {}
TTL_SEC = 600  # 10 min cache
USAGE_FILE = "owm_usage.json"
MAX_CALLS_PER_DAY = 950  # Zapas 50 zapytań do darmowego limitu
OWM_FRESH_MAX_SEC = int(os.environ.get("OWM_FRESH_MAX_SEC", "5400"))  # 90 min domyślnie

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

def extract_owm_current(owm: dict) -> Optional[dict]:
    """Zwraca bieżący rekord OWM niezależnie od wariantu odpowiedzi API."""
    if not owm:
        return None
    if isinstance(owm.get("data"), list) and owm.get("data"):
        return owm["data"][0]
    return owm if isinstance(owm, dict) else None


def extract_owm_dt_seconds(owm: dict) -> Optional[float]:
    """Wyciąga timestamp OWM w sekundach; obsługuje też milisekundy."""
    current = extract_owm_current(owm)
    if not current:
        return None
    dt = current.get("dt")
    if dt is None:
        return None
    try:
        dt = float(dt)
        if dt > 1e12:
            dt /= 1000.0
        return dt
    except Exception:
        return None


def owm_is_fresh(owm: dict, now_ts, max_age_sec: int = None) -> bool:
    """Jedno źródło prawdy: OWM może zasilać UI tylko gdy ma świeży timestamp."""
    dt = extract_owm_dt_seconds(owm)
    if dt is None:
        return False
    if hasattr(now_ts, "timestamp"):
        now_ts = now_ts.timestamp()
    max_age = OWM_FRESH_MAX_SEC if max_age_sec is None else int(max_age_sec)
    try:
        return abs(float(now_ts) - dt) <= max_age
    except Exception:
        return False


def _eff_clouds_for_owm(h: dict) -> float:
    def _eff(low_key, mid_key, total_key):
        low = h.get(low_key)
        mid = h.get(mid_key)
        if low is not None and mid is not None:
            return min(100.0, float(low or 0) + float(mid or 0))
        return float(h.get(total_key) or 0)

    base = _eff("clouds_low_pct", "clouds_mid_pct", "clouds_pct")
    has_yr = any(h.get(k) is not None for k in ("clouds_low_pct_yr", "clouds_mid_pct_yr", "clouds_pct_yr"))
    if not has_yr:
        return base
    alt = _eff("clouds_low_pct_yr", "clouds_mid_pct_yr", "clouds_pct_yr")
    return max(base, alt)


def find_model_hour_for_now(hours: list, now_local: datetime) -> Optional[dict]:
    """Znajduje rekord modelu odpowiadający dokładnie lokalnej godzinie 'teraz'. Bez fallbacku."""
    if not hours or not now_local:
        return None
    tz = now_local.tzinfo
    target = now_local.replace(minute=0, second=0, microsecond=0)
    target_naive = target.replace(tzinfo=None)
    for h in hours:
        tloc = h.get("time_local")
        if not tloc:
            continue
        try:
            dt_h = datetime.fromisoformat(str(tloc).replace("Z", "+00:00"))
            if tz and dt_h.tzinfo:
                dt_h = dt_h.astimezone(tz)
            elif tz and dt_h.tzinfo is None:
                dt_h = dt_h.replace(tzinfo=tz)
            dt_cmp = dt_h.replace(minute=0, second=0, microsecond=0)
            # time_local jest już czasem lokalnym; gdy jedna ze stron jest naive,
            # porównujemy lokalną datę+godzinę bez bezpiecznego fallbacku do ta[0].
            if dt_cmp == target or dt_cmp.replace(tzinfo=None) == target_naive:
                return h
        except Exception:
            continue
    return None


def classify_owm_cloud_correction(h_model: dict, owm: dict, now_local: datetime, is_night: bool, max_age_sec: int = None) -> dict:
    """Wspólna klasyfikacja chmur OWM dla /now i /day.

    Helper liczy fakty tylko raz: świeżość, high-only gate, etykiety model/live i decyzję,
    czy wolno nadpisać bieżącą godzinę albo pokazać snapshot „Teraz: ...".
    """
    result = {
        "fresh": False,
        "reason": "no_owm",
        "model_clouds": None,
        "live_clouds": None,
        "effective_live_clouds": None,
        "label_model": None,
        "label_live": None,
        "icon_live": None,
        "should_override_hour0": False,
        "should_show_snapshot": False,
        "high_only_gate": False,
        "note_key": None,
    }
    if not h_model or not owm:
        return result
    if not owm_is_fresh(owm, now_local, max_age_sec=max_age_sec):
        result["reason"] = "stale_owm"
        return result

    current = extract_owm_current(owm)
    if not current or current.get("clouds") is None:
        result["reason"] = "no_clouds"
        return result

    model_cld = _eff_clouds_for_owm(h_model)
    live_cld = float(current.get("clouds") or 0.0)
    real_uvi = float(current.get("uvi") or 0.0)

    low = float(h_model.get("clouds_low_pct") or 0.0)
    mid = float(h_model.get("clouds_mid_pct") or 0.0)
    lowmid = low + mid
    prc = float(h_model.get("precip_eff_mm", h_model.get("precip_mm")) or 0.0)
    uv_model = float(h_model.get("uv_index") or 0.0)
    uv = uv_model if uv_model > 0 else real_uvi

    models_strong_clear = model_cld <= 40.0
    looks_like_high_only = lowmid <= 20.0 and prc <= 0.05
    owm_claims_cloudy = live_cld >= 70.0 and (live_cld - model_cld) >= 30.0
    high_only_gate = (not is_night) and looks_like_high_only and models_strong_clear and owm_claims_cloudy

    effective_live = live_cld
    if live_cld >= 85.0 and uv > 1.2 and not is_night:
        effective_live = 65.0

    label_model, _ = sky_from_clouds(model_cld, is_night)
    if high_only_gate:
        effective_live = model_cld
    label_live, icon_live = sky_from_clouds(effective_live, is_night)

    diff = abs(effective_live - model_cld)
    should_act = (not high_only_gate) and diff >= 25.0 and label_live != label_model

    result.update({
        "fresh": True,
        "reason": "ok",
        "model_clouds": model_cld,
        "live_clouds": live_cld,
        "effective_live_clouds": effective_live,
        "label_model": label_model,
        "label_live": label_live,
        "icon_live": icon_live,
        "should_override_hour0": should_act,
        "should_show_snapshot": should_act,
        "high_only_gate": high_only_gate,
        "note_key": "nowcast_more_clouds" if effective_live > model_cld else "nowcast_less_clouds",
    })
    return result


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
        # --- DEBUG OWM 4.0 (tylko na żądanie) ---
        # Odpowiedź zawiera współrzędne użytkownika, więc w produkcji nie zapisujemy
        # jej na dysk. Włącz przez OWM_DEBUG=1.
        if os.environ.get("OWM_DEBUG") == "1":
            try:
                with open("debug_owm.json", "w", encoding="utf-8") as df:
                    json.dump(data, df, indent=2, ensure_ascii=False)
            except Exception:
                pass
        # ----------------------------------------
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

    if not owm_is_fresh(owm, now_local):
        return None

    current = extract_owm_current(owm)
    if not current:
        return None

    # Ekstrakcja opadów OWM
    rain_1h = current.get("rain", {}).get("1h", 0) if isinstance(current.get("rain"), dict) else 0
    snow_1h = current.get("snow", {}).get("1h", 0) if isinstance(current.get("snow"), dict) else 0
    owm_precip = float(rain_1h) + float(snow_1h)

    h = find_model_hour_for_now(payload_hours, now_local)
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