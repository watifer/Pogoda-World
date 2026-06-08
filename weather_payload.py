"""
weather_payload.py — Produkcyjny builder payloadu pogodowego
Funkcja: build_payload_for_location(lat, lon, tz_name, location_name, days_ahead)
Airly jest opcjonalne i best-effort.
"""

from __future__ import annotations

import os
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3  # <--- TEGO BRAKOWAŁO
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# --- TARCZA NA ORACLE CLOUD (Wymuszenie IPv4) ---
# Rozwiązuje 90% problemów z ReadTimeout na darmowych instancjach
urllib3.util.connection.HAS_IPV6 = False
# ------------------------------------------------


# ═══════════════════════════════════════
# HELPERY
# ═══════════════════════════════════════

def _get_retry_session() -> requests.Session:
    """Zwraca sesję requests z wbudowanym mechanizmem ponawiania (Exponential Backoff)."""
    session = requests.Session()
    retry = Retry(
        total=4,            # Maksymalnie 4 próby ponowienia
        read=4,             # W tym na błędy ReadTimeout
        connect=4,          # W tym na błędy połączenia
        backoff_factor=1.5, # Odczekaj: 1.5s, 3s, 6s... przed kolejną próbą
        status_forcelist=[429, 500, 502, 503, 504], # Błędy API, przy których walczymy dalej
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session



def _kmh_from_ms(ms):
    return None if ms is None else round(ms * 3.6, 1)


def _to_local(dt_utc: datetime, tz: ZoneInfo) -> datetime:
    return dt_utc.astimezone(tz)


# ═══════════════════════════════════════
# YR.NO
# ═══════════════════════════════════════

def _fetch_yrno(lat: float, lon: float, tz: ZoneInfo) -> tuple[list, str]:
    ua = os.environ.get("YRNO_USER_AGENT", "PogodaWorld/1.0")
    # ZMIANA NA TRYB COMPLETE: Pobieramy pełne warstwy chmur i porywy wiatru!
    url = (f"https://api.met.no/weatherapi/locationforecast/2.0/complete"
           f"?lat={lat}&lon={lon}")
    session = _get_retry_session()
    r = session.get(url, headers={"User-Agent": ua}, timeout=20)
    r.raise_for_status()
    
    data = r.json()
    items = data["properties"]["timeseries"]
    
    # WYCIĄGAMY CZAS AKTUALIZACJI I KONWERTUJEMY NA CZAS LOKALNY MIEJSCOWOŚCI
    updated_at_utc = data["properties"]["meta"]["updated_at"]
    dt_utc = datetime.fromisoformat(updated_at_utc.replace("Z", "+00:00"))
    updated_at_loc = _to_local(dt_utc, tz).isoformat(timespec="minutes")

    hours = []
    for item in items:
        t_utc = datetime.fromisoformat(item["time"].replace("Z", "+00:00"))
        t_loc = _to_local(t_utc, tz)
        details = item["data"]["instant"]["details"]
        p1 = (item["data"]
              .get("next_1_hours", {})
              .get("details", {})
              .get("precipitation_amount"))
        symbol = (item["data"]
                  .get("next_1_hours", {})
                  .get("summary", {})
                  .get("symbol_code"))
        # NOWOŚĆ: Wyciągamy procentowe ryzyko burzy
        thunder = (item["data"]
                   .get("next_1_hours", {})
                   .get("details", {})
                   .get("probability_of_thunder"))
                  
        hours.append({
            "time_local":      t_loc.isoformat(timespec="minutes"),
            "temp_c":          details.get("air_temperature"),
            "dewpoint_c":      details.get("dew_point_temperature"),
            "rh_pct":          details.get("relative_humidity"),
            "wind_kmh":        _kmh_from_ms(details.get("wind_speed")),
            "gust_kmh":        _kmh_from_ms(details.get("wind_speed_of_gust")),  # BONUS: Teraz mamy norweskie porywy!
            "wind_dir_deg":    details.get("wind_from_direction"),
            "clouds_pct":      details.get("cloud_area_fraction"),
            "clouds_low_pct":  details.get("cloud_area_fraction_low"),           # Rozbite warstwy dla _eff_cld!
            "clouds_mid_pct":  details.get("cloud_area_fraction_medium"),        # Rozbite warstwy dla _eff_cld!
            "clouds_high_pct": details.get("cloud_area_fraction_high"),          # Rozbite warstwy dla _eff_cld!
            "pressure_hpa":    details.get("air_pressure_at_sea_level"),         # W 'complete' ta zmienna ma taką nazwę
            "uv_index":        details.get("ultraviolet_index_clear_sky"),
            "thunder_prob":    thunder,
            "precip_mm":       p1,
            "weather_code":    None,
            "symbol_code":     symbol,
            "source":          "yrno",
        })
    return hours, updated_at_loc


# ═══════════════════════════════════════
# OPEN-METEO
# ═══════════════════════════════════════

def _fetch_openmeteo(lat: float, lon: float, tz: ZoneInfo) -> list:
    tz_str = str(tz).replace("/", "%2F")
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&past_days=1"  # <--- To zostawiamy, żeby dbało o ciągłość historii!
        "&hourly=temperature_2m,dewpoint_2m,relative_humidity_2m,"
        "wind_speed_10m,wind_gusts_10m,wind_direction_10m,"
        "precipitation,precipitation_probability,"
        "cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,"
        "weather_code,pressure_msl,uv_index"    
        f"&timezone={tz_str}"
        "&forecast_days=15"  # <--- NOWOŚĆ: Twarde żądanie 14 dni ( bo 1 dzień jest bieżący )
    )
    session = _get_retry_session()
    r = session.get(url, timeout=20)
    r.raise_for_status()
    h = r.json()["hourly"]
    times = h["time"]
    prob = h.get("precipitation_probability", [None] * len(times))

    hours = []
    for i, t_str in enumerate(times):
        t_loc = datetime.fromisoformat(t_str).replace(tzinfo=tz)
        hours.append({
            "time_local":      t_loc.isoformat(timespec="minutes"),
            "temp_c":          h["temperature_2m"][i],
            "dewpoint_c":      h["dewpoint_2m"][i],
            "rh_pct":          h["relative_humidity_2m"][i],
            "wind_kmh":        h["wind_speed_10m"][i],
            "gust_kmh":        h["wind_gusts_10m"][i],
            "wind_dir_deg":    h["wind_direction_10m"][i],
            "clouds_pct":      h["cloud_cover"][i],
            "clouds_low_pct":  h["cloud_cover_low"][i],
            "clouds_mid_pct":  h["cloud_cover_mid"][i],
            "clouds_high_pct": h["cloud_cover_high"][i],
            "pressure_hpa":    h["pressure_msl"][i] if "pressure_msl" in h else None,
            "uv_index":        h["uv_index"][i] if "uv_index" in h else None,  # <--- NOWA LINIJKA: Zapisujemy UV od OM
            "precip_mm":       h["precipitation"][i],
            "precip_prob_pct": prob[i] if prob[i] is not None else 0,
            "weather_code":    h["weather_code"][i],
            "symbol_code":     None,
            "source":          "openmeteo",
        })
    return hours


# ═══════════════════════════════════════
# FILTR GODZIN
# ═══════════════════════════════════════

def _select_hours(hours: list, tz: ZoneInfo, days_ahead: int = 5) -> list:
    start = datetime.now(tz).replace(
        hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days_ahead + 1)
    result = []
    for h in hours:
        t = datetime.fromisoformat(h["time_local"])
        if not t.tzinfo:
            t = t.replace(tzinfo=tz)
        if start <= t <= end:
            result.append(h)
    return result


# ═══════════════════════════════════════
# AIRLY — opcjonalne, best-effort
# ═══════════════════════════════════════

def _airly_get(url: str, api_key: str, retries: int = 3):
    headers = {"Accept": "application/json", "apikey": api_key}
    last_exc = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code in (429, 502, 503, 504):
                time.sleep(1.5 * (2 ** i))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last_exc = e
            time.sleep(1.5 * (2 ** i))
    raise last_exc


def _fetch_airly(lat: float, lon: float, max_km: float = 3.0) -> dict | None:
    api_key = os.environ.get("AIRLY_API_KEY")
    if not api_key:
        return None
    try:
        url_inst = (
            "https://airapi.airly.eu/v2/installations/nearest"
            f"?lat={lat}&lng={lon}&maxDistanceKM={max_km}&maxResults=5"
        )
        inst_list = _airly_get(url_inst, api_key)
        if not inst_list:
            return None

        inst = inst_list[0]
        inst_id = inst.get("id")
        time.sleep(0.3)

        url_m = (
            "https://airapi.airly.eu/v2/measurements/installation"
            f"?installationId={inst_id}&indexType=AIRLY_CAQI"
        )
        m = _airly_get(url_m, api_key)
        current = m.get("current") or {}

        def _vals(lst):
            return {v.get("name"): v.get("value") for v in (lst or [])}

        vals = _vals(current.get("values"))
        idxs = _vals(current.get("indexes"))

        return {
            "installation_id": inst_id,
            "address":         inst.get("address"),
            "location":        inst.get("location"),
            "from":            current.get("fromDateTime"),
            "till":            current.get("tillDateTime"),
            "temp_c":          vals.get("TEMPERATURE"),
            "rh_pct":          vals.get("HUMIDITY"),
            "pressure_hpa":    vals.get("PRESSURE"),
            "pm25":            vals.get("PM25"),
            "caqi":            idxs.get("AIRLY_CAQI"),
            "source":          "airly",
        }
    except Exception:
        return None
        

# ═══════════════════════════════════════
# INTELIGENTNE ALERTY (Wewnętrzny Silnik)
# ═══════════════════════════════════════

def _generate_alerts(forecast_hours: list, now: datetime) -> list:
    """Skanuje najbliższe 36 godzin (od 'teraz') w poszukiwaniu niebezpiecznych zjawisk."""
    alerts = []
    if not forecast_hours:
        return alerts
        
    # Odrzucamy godziny z przeszłości
    future_hours = []
    now_floored = now.replace(minute=0, second=0, microsecond=0)
    for h in forecast_hours:
        dt = datetime.fromisoformat(h["time_local"])
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=now.tzinfo)
        if dt >= now_floored:
            future_hours.append(h)
            
    horizon = future_hours[:36]
    if not horizon:
        return alerts

    # Helper do szukania ekstremów i ich godzin
    def get_max_event(key1, key2=None):
        max_val = 0
        max_h = None
        for h in horizon:
            v1 = float(h.get(key1) or 0)
            v2 = float(h.get(key2) or 0) if key2 else 0
            val = max(v1, v2)
            if val > max_val:
                max_val = val
                max_h = h
        return max_val, max_h

    # Helper do formatowania czasu
    # Helper do formatowania czasu (Teraz z wykrywaniem dni!)
    def fmt_time(h_dict):
        if not h_dict: return ""
        dt = datetime.fromisoformat(h_dict["time_local"])
        
        # Liczymy różnicę w dniach kalendarzowych
        days_diff = (dt.date() - now.date()).days
        if days_diff == 0: day_prefix = "dziś"
        elif days_diff == 1: day_prefix = "jutro"
        else: day_prefix = "pojutrze"
            
        return f" ({day_prefix} ok. {dt.strftime('%H:00')})"

    # 1. WICHURY
    max_wind, wind_h = get_max_event("gust_kmh", "wind_kmh")
    if max_wind >= 90:
        alerts.append(f"Bardzo silny wiatr — Prognozowane są niszczące porywy do {round(max_wind)} km/h{fmt_time(wind_h)}.")
    elif max_wind >= 75:
        alerts.append(f"Silny wiatr — Prognozowane są porywy sięgające {round(max_wind)} km/h{fmt_time(wind_h)}.")
    elif max_wind >= 60:
        alerts.append(f"Silny wiatr — Spodziewane porywy do {round(max_wind)} km/h{fmt_time(wind_h)}.")
        
    # 2. UPAŁY
    max_temp, temp_h = get_max_event("temp_c")
    if max_temp >= 35:
        alerts.append(f"Ekstremalny upał — Temperatura maksymalna wzrośnie do {round(max_temp)}°C{fmt_time(temp_h)}.")
    elif max_temp >= 30:
        alerts.append(f"Upał — Spodziewany jest wzrost temperatury do {round(max_temp)}°C{fmt_time(temp_h)}.")
        
    # 3. MRÓZ (Szukamy minimum, więc osobna logika)
    min_temp = 999
    min_h = None
    for h in horizon:
        t = float(h.get("temp_c") or 0)
        if t < min_temp:
            min_temp = t
            min_h = h
            
    if min_temp <= -20:
        alerts.append(f"Ekstremalny mróz — Temperatura spadnie nawet do {round(min_temp)}°C{fmt_time(min_h)}.")
    elif min_temp <= -10:
        alerts.append(f"Silny mróz — Prognozowane są spadki temperatury do {round(min_temp)}°C{fmt_time(min_h)}.")
    elif min_temp <= -5:
        alerts.append(f"Mróz — Temperatura minimalna wyniesie około {round(min_temp)}°C{fmt_time(min_h)}.")
        
    # 4. INTENSYWNE OPADY
    max_precip, precip_h = get_max_event("precip_mm")
    if max_precip >= 10.0:
        alerts.append(f"Nawałnica — Spodziewane są gwałtowne opady o natężeniu {round(max_precip, 1)} mm/h{fmt_time(precip_h)}.")
    elif max_precip >= 5.0:
        alerts.append(f"Ulewny deszcz — Możliwe intensywne opady do {round(max_precip, 1)} mm/h{fmt_time(precip_h)}.")
        
    return alerts   

# ═══════════════════════════════════════
# GŁÓWNA FUNKCJA (Wstrzyknięcie optymizmu V2)
# ═══════════════════════════════════════

def build_payload_for_location(
    lat: float,
    lon: float,
    tz_name: str,
    location_name: str = None,
    days_ahead: int = 14,
) -> dict:
    tz = ZoneInfo(tz_name)
    name = location_name or "Twoja okolica"

    hours_all = []
    forecast_source = None

    # 1. Pobieramy Niemców (Open-Meteo)
    om_hours = []
    try:
        om_hours = _fetch_openmeteo(lat, lon, tz)
        forecast_source = "OpenMeteo"
    except Exception as e:
        print(f"[weather_payload] Open-Meteo niedostępne: {type(e).__name__}: {e}")

    # 2. Pobieramy Norwegów (Yr.no)
    yr_hours = []
    yr_updated_at = None  # Dodana zmienna
    try:
        yr_hours, yr_updated_at = _fetch_yrno(lat, lon, tz)  # Rozpakowujemy krotkę
        if forecast_source is None:
            forecast_source = "yr.no"
    except Exception as e:
        print(f"[weather_payload] Yr.no niedostępne: {type(e).__name__}: {e}")

    if not om_hours and not yr_hours:
        raise RuntimeError(f"Brak danych prognozy dla ({lat}, {lon}) z Yr.no i Open-Meteo.")

    # 3. WSTRZYKNIĘCIE NORWESKIEGO OPTYMIZMU (Złota Fuzja)
    if om_hours and yr_hours:
        # --- AKTUALIZACJA ŹRÓDŁA ---
        forecast_source = "OpenMeteo + Yr.no"

        yr_dict = {h["time_local"]: h for h in yr_hours}
        for om in om_hours:
            t = om["time_local"]
            yr = yr_dict.get(t)
            if yr:
                # 1. Kopiujemy TWARDE, PRAWDZIWE DANE od Norwegów
                if yr.get("temp_c") is not None: om["temp_c"] = yr["temp_c"]
                if yr.get("precip_mm") is not None: om["precip_mm"] = yr["precip_mm"]
                
                # ZACHOWUJEMY chmury OM, ale dopisujemy chmury Yr jako alternatywę do konsensusu:
                if yr.get("clouds_pct") is not None:      om["clouds_pct_yr"] = yr["clouds_pct"]
                if yr.get("clouds_low_pct") is not None:  om["clouds_low_pct_yr"] = yr["clouds_low_pct"]
                if yr.get("clouds_mid_pct") is not None:  om["clouds_mid_pct_yr"] = yr["clouds_mid_pct"]
                if yr.get("clouds_high_pct") is not None: om["clouds_high_pct_yr"] = yr["clouds_high_pct"]
                # Burze i indeks UV
                if yr.get("uv_index") is not None: om["uv_index"] = yr["uv_index"]
                if yr.get("thunder_prob") is not None: om["thunder_prob"] = yr["thunder_prob"]
                
                # 2. Opcjonalna asysta kodów zjawisk dla mechanizmu klasyfikacji opadów
                sym = yr.get("symbol_code", "")
                if sym:
                    s = sym.lower()
                    om["symbol_code"] = sym # Zapisujemy też oryginalny symbol Yr.no!
                    
                    if "clearsky" in s: om["weather_code"] = 0
                    elif "fair" in s: om["weather_code"] = 1
                    elif "partlycloudy" in s: om["weather_code"] = 2
                    elif s == "cloudy": om["weather_code"] = 3
                    elif "fog" in s: om["weather_code"] = 45
                    elif "thunder" in s: om["weather_code"] = 95
                    elif "heavyrainshowers" in s: om["weather_code"] = 82
                    elif "lightrainshowers" in s: om["weather_code"] = 80
                    elif "rainshowers" in s: om["weather_code"] = 81
                    elif "heavyrain" in s: om["weather_code"] = 65
                    elif "lightrain" in s: om["weather_code"] = 61
                    elif "rain" in s: om["weather_code"] = 63
                    elif "heavysnow" in s: om["weather_code"] = 75
                    elif "lightsnow" in s: om["weather_code"] = 71
                    elif "snow" in s: om["weather_code"] = 73

    # Dodajemy obie listy DOKŁADNIE tak, jak było oryginalnie
    if om_hours: hours_all.extend(om_hours)
    if yr_hours: hours_all.extend(yr_hours)

    forecast_hours = _select_hours(hours_all, tz, days_ahead)

    airly = _fetch_airly(lat, lon)

    model_temp = forecast_hours[0]["temp_c"] if forecast_hours else None
    airly_temp = airly["temp_c"] if airly else None
    bias_temp = None
    if model_temp is not None and airly_temp is not None:
        bias_temp = round(float(airly_temp) - float(model_temp), 2)

    now_dt = datetime.now(tz)
    active_alerts = _generate_alerts(forecast_hours, now_dt)
    
    
    
   

    return {
        "version": "1.0",
        "location": {
            "name": name,
            "lat":  lat,
            "lon":  lon,
            "tz":   tz_name,
        },
        "generated_at_local": now_dt.isoformat(timespec="seconds"),
        "model_updated_at_local": yr_updated_at,  # <--- NOWOŚĆ: Przekazujemy do frontend'u!
        "forecast_source":    forecast_source,
        "model_agreement":    None,
        "airly":              airly,
        "bias_temp_c":        bias_temp,
        "hours":              forecast_hours,
        "alerts":             active_alerts,
    }