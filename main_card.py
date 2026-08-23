"""
main_card.py — Produkcyjny entrypoint wysyłki kart pogodowych.
Wysyłka przez Telegram Bot API (requests, bez python-telegram-bot).
"""

from __future__ import annotations


import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*Python 3.8.*")  # Ostateczny tłumik na cryptography

import os
import requests
import json
import time
import random
from datetime import datetime, time as dt_time, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

try:
    from timezonefinder import TimezoneFinder
    _TF = TimezoneFinder()
except ImportError:
    _TF = None

from dotenv import load_dotenv
load_dotenv()

import image_generator
from weather_payload import build_payload_for_location
from prepare_layout  import prepare_layout_data
from prepare_now_layout import prepare_now_layout_data
from prepare_future_layout import prepare_future_layout_data
from sanity_checker import run_sanity_check


# --- INICJALIZACJA SYSTEMU NADMORSKIEGO ---
try:
    from coast_detector import JsonCoastSigStore
    GLOBAL_COAST_STORE = JsonCoastSigStore("coast_cache.json")
    GLOBAL_COAST_INDEX = None  # Na starcie w RAM-ie nie ma żadnej mapy!

    def ensure_coast_index():
        global GLOBAL_COAST_INDEX
        if GLOBAL_COAST_INDEX is None:
            # Ładujemy ciężką mapę do RAM tylko, gdy jest to absolutnie konieczne (Cache Miss)
            from coast_detector import CoastIndex
            GLOBAL_COAST_INDEX = CoastIndex("data/natural_earth/ne_110m_ocean/ne_110m_ocean.shp")
        return GLOBAL_COAST_INDEX
except Exception as e:
    GLOBAL_COAST_STORE = None
    ensure_coast_index = None
    print(f"[SYSTEM] Błąd inicjalizacji mapy wybrzeża: {e}")



def wirtualne_scalanie(raw_records: list) -> list:
    """Kompresuje rozbite wiersze z Google Sheets w jeden perfekcyjny rekord na użytkownika, sortując je po dacie."""
    
    # --- OSTATECZNA TARCZA: MYJNIA NAGŁÓWKÓW ---
    # Automatycznie czyścimy WSZYSTKIE kolumny ze spacji (z przodu i z tyłu)
    cleaned_records = []
    for row in raw_records:
        clean_row = {str(k).strip(): v for k, v in row.items()}
        cleaned_records.append(clean_row)

    # 1. Funkcja pomocnicza do sortowania dat. Puste komórki traktujemy jako najstarsze (rok 1970).
    def get_timestamp(row):
        ts = str(row.get("Sygnatura czasowa", "")).strip()
        return ts if ts else "1970-01-01 00:00:00"
        
    # 2. Sortujemy rekordy (używamy już tych wyczyszczonych!)
    sorted_records = sorted(cleaned_records, key=get_timestamp)
    
    merged = {}
    for row in sorted_records:
        # Dzięki "myjni" mamy pewność, że klucz "Chat ID" zawsze zadziała
        cid = str(row.get("Chat ID", "")).strip()
        
        # Zabezpieczenie przed ułamkami .0 od Google Sheets
        if cid.endswith(".0"): 
            cid = cid[:-2]
            
        if not cid: continue
        
        # --- SMART AUTO-SPRZĄTACZKA: Ignoruj zablokowanych użytkowników! ---
        if cid.startswith("BLOCKED"): 
            continue
        # -------------------------------------------------------------------
        
        if cid not in merged:
            merged[cid] = {
                "Chat ID": cid, "Imię": "", "Lat": "", "Lon": "", 
                "Raport poranny": "", "Aktualizacja": "", "Sygnatura czasowa": "",
                "Lang": ""  # <--- NOWE
            }
        
        # 3. Nadpisywanie (teraz klucze zawsze będą idealnie pasować do nagłówków)
        if str(row.get("Imię", "")).strip(): merged[cid]["Imię"] = str(row.get("Imię")).strip()
        if str(row.get("Miasto", "")).strip(): merged[cid]["Miasto"] = str(row.get("Miasto")).strip()
        if str(row.get("Lat", "")).strip(): merged[cid]["Lat"] = str(row.get("Lat")).strip()
        if str(row.get("Lon", "")).strip(): merged[cid]["Lon"] = str(row.get("Lon")).strip()
        if str(row.get("Raport poranny", "")).strip(): merged[cid]["Raport poranny"] = str(row.get("Raport poranny")).strip()
        if str(row.get("Aktualizacja", "")).strip(): merged[cid]["Aktualizacja"] = str(row.get("Aktualizacja")).strip()
        if str(row.get("Sygnatura czasowa", "")).strip(): merged[cid]["Sygnatura czasowa"] = str(row.get("Sygnatura czasowa")).strip()
        
        # --- NOWE: Pobieranie języka (akceptuje kolumnę "Lang" lub "Język") ---
        lang_val = str(row.get("Lang", row.get("Język", ""))).strip()
        if lang_val: merged[cid]["Lang"] = lang_val
        
    return list(merged.values())



# ═══════════════════════════════════════
# KONFIGURACJA
# ═══════════════════════════════════════

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TG_TOKEN", "")
# Zmienne środowiskowe Google Sheets
SHEET_ID      = os.environ.get("GOOGLE_SHEET_ID") or ""
SHEET_NAME    = os.environ.get("GOOGLE_SHEET_NAME") or "Pogoda_Users"
SHEET_TAB_ENV = os.environ.get("GOOGLE_SHEET_TAB") or ""

# Okna wysyłki (lokalna godzina usera)
WINDOW_MORNING_START, WINDOW_MORNING_END     = dt_time(6, 0), dt_time(9, 0)
WINDOW_AFTERNOON_START, WINDOW_AFTERNOON_END = dt_time(14, 0), dt_time(15, 0)

# --- DODANE: DOMYŚLNE GODZINY RAPORTÓW ---
DEFAULT_RANO = "08:00"
DEFAULT_WIECZOR = "14:00"

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sent_today.json")


# ═══════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════

def send_telegram_photo(chat_id: str, image_path: str,
                        caption: str = "", disable_notification: bool = False) -> bool:
    """Wysyła zdjęcie przez Telegram Bot API z inteligentną obsługą blokad."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(image_path, "rb") as f:
            r = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption, "disable_notification": disable_notification},
                files={"photo": f},
                timeout=30,
            )
        
        wynik = r.json()
        
        if r.status_code == 200 and wynik.get("ok"):
            return True
            
        error_code = wynik.get("error_code")
        error_msg = wynik.get("description", "").lower()
        print(f"[Telegram] Odpowiedź API {error_code}: {error_msg}")
        
        # --- PRECYZYJNY SCANER BLOKAD ---
        # Usuwamy tylko gdy mamy pewność, że bot nie ma już tam wstępu
        trigger_words = [
            "blocked by the user", 
            "kicked from",               # <--- Zmienione! Wyłapie "group" oraz "supergroup chat"
            "user is deactivated", 
            "chat not found",
            "group chat was deactivated" # Opcjonalnie warto to dodać, gdy cała grupa zostanie usunięta
        ]
        
        if error_code in [400, 403] and any(word in error_msg for word in trigger_words):
            print(f"⚠️ [AUTO-CLEANUP] Wykryto trwałą blokadę dla {chat_id}. Rozpoczynam procedurę Soft-Delete...")
            _soft_delete_user(chat_id)
            
        return False

    except Exception as e:
        print(f"[Telegram] Wyjątek krytyczny wysyłki: {e}")
        return False


# ═══════════════════════════════════════
# UŻYTKOWNICY — Google Sheets
# ═══════════════════════════════════════

def _load_users_from_sheet() -> list[dict]:
    """
    Wczytuje użytkowników z Google Sheets przez API.
    Wymagane kolumny: Chat ID, Lat, Lon
    Opcjonalne: TZ/Timezone, Miasto/City/Display Name, Aktywny, Imię, Format
    """
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    creds_env = os.environ.get("GOOGLE_CREDS_JSON")
    if creds_env and creds_env.startswith("{"):
        creds_dict = json.loads(creds_env)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    else:
        creds_path = creds_env or "credentials.json"
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    client = gspread.authorize(creds)
    
    sheet = None
    if SHEET_ID:
        try:
            sheet = client.open_by_key(SHEET_ID)
        except Exception as e:
            print(f"[Google Sheets] Fallback: Nie udało się otworzyć po ID ({e})")

    if not sheet:
        sheet = client.open(SHEET_NAME)

    tab_candidates = []
    if SHEET_TAB_ENV:
        tab_candidates.append(SHEET_TAB_ENV)
    
    # Kaskada "spadochronów" dla nazw zakładek (najbardziej prawdopodobne na górze)
    tab_candidates.extend([
        "Formularz",
        "Form_Responses4", 
        "Arkusz1",
        "Sheet1"
    ])

    worksheet = None
    errors = []
    for tab in tab_candidates:
        try:
            worksheet = sheet.worksheet(tab)
            break
        except gspread.exceptions.WorksheetNotFound:
            errors.append(tab)

    if not worksheet:
        raise RuntimeError(f"Krytyczny błąd: Brak zakładek. Odrzucone: {', '.join(errors)}")
        
    # --- P0: Odporne ładowanie Sheets na stałym zakresie (Eliminacja błędu duplicate headers) ---
    values = worksheet.get_values("A1:Z")
    if not values:
        return []
        
    headers = values[0]
    out = []
    
    for row in values[1:]:
        # Wyrównujemy krótsze wiersze pustymi stringami do długości nagłówków
        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))
        out.append(dict(zip(headers, row[:len(headers)])))
        
    return out

def _soft_delete_user(chat_id: str):
    """
    Inteligentny Grabarz: Oznacza użytkownika jako BLOCKED w Google Sheets.
    Operacja hurtowa (1 zapytanie do API), bezpieczna dla limitów Google.
    """
    import gspread
    from google.oauth2.service_account import Credentials

    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds_env = os.environ.get("GOOGLE_CREDS_JSON")
        if creds_env and creds_env.startswith("{"):
            creds = Credentials.from_service_account_info(json.loads(creds_env), scopes=scopes)
        else:
            creds = Credentials.from_service_account_file(creds_env or "credentials.json", scopes=scopes)
        
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID) if SHEET_ID else client.open(SHEET_NAME)
        worksheet = sheet.worksheet("Formularz")
        
        # 1. Pobieramy całą kolumnę Chat ID jednym strzałem
        col_values = worksheet.col_values(2) 
        
        # 2. Szukamy wszystkich wystąpień tego ID
        cells_to_update = []
        for i, val in enumerate(col_values):
            if str(val).strip() == str(chat_id):
                # row index w gspread jest od 1, więc i+1
                cells_to_update.append(gspread.Cell(row=i+1, col=2, value=f"BLOCKED_{chat_id}"))
        
        # 3. Jeśli znaleziono, aktualizujemy hurtowo (Batch Update)
        if cells_to_update:
            worksheet.update_cells(cells_to_update)
            print(f"✅ [AUTO-CLEANUP] Oznaczono {len(cells_to_update)} rekordów jako BLOCKED dla ID: {chat_id}. Miejsce zwolnione.")
        else:
            print(f"❓ [AUTO-CLEANUP] Nie znaleziono ID {chat_id} w arkuszu do oznaczenia.")

    except Exception as e:
        print(f"❌ [AUTO-CLEANUP] Błąd podczas czyszczenia bazy dla {chat_id}: {e}")



def _resolve_tz(lat: float, lon: float, raw_tz: str = "") -> str:
    """Zwraca tz_name: z arkusza → TimezoneFinder → fallback UTC."""
    if raw_tz and raw_tz.strip():
        return raw_tz.strip()
    if _TF:
        found = _TF.timezone_at(lat=lat, lng=lon)
        if found:
            return found
    return "UTC"


def _parse_users(raw_rows: list[dict]) -> list[dict]:
    """
    Normalizuje rekordy z arkusza. 
    Inteligentne scalanie: zbiera dane użytkownika z wielu wierszy,
    nadpisując stare wartości tylko wtedy, gdy nowe nie są puste.
    """
    users_dict = {}
    
    for row in raw_rows:
        chat_id = str(row.get("Chat ID", "")).strip()
        if not chat_id:
            continue

        try:
            lat_raw = str(row.get("Lat", "")).replace(",", ".").strip()
            lon_raw = str(row.get("Lon", "")).replace(",", ".").strip()
            current_lat = float(lat_raw) if lat_raw else None
            current_lon = float(lon_raw) if lon_raw else None
        except (ValueError, TypeError):
            current_lat, current_lon = None, None

        current_rano = str(row.get("Raport poranny", "")).strip()
        current_wieczor = str(row.get("Aktualizacja", "")).strip()
        current_imie = str(row.get("Imię", "")).strip()
        current_miasto = str(row.get("Miasto", "")).strip()  
        
        # --- BEZPIECZNE POBIERANIE JĘZYKA Z FALLBACKAMI ---
        current_lang = str(row.get("Lang", row.get("Język", row.get("Language", "")))).strip().lower()

        if chat_id not in users_dict:
            users_dict[chat_id] = {
                "chat_id": chat_id,
                "lat": None,
                "lon": None,
                "imie": "",
                "miasto": "",  
                "godzina_rano": DEFAULT_RANO,      
                "godzina_wieczor": DEFAULT_WIECZOR, 
                "lang": "en",  # Domyślny globalny fallback
            }

        if current_lat is not None: users_dict[chat_id]["lat"] = current_lat
        if current_lon is not None: users_dict[chat_id]["lon"] = current_lon
        if current_imie: users_dict[chat_id]["imie"] = current_imie
        if current_miasto: users_dict[chat_id]["miasto"] = current_miasto  
        if current_rano: users_dict[chat_id]["godzina_rano"] = current_rano
        if current_wieczor: users_dict[chat_id]["godzina_wieczor"] = current_wieczor
        
        # --- WHITELISTA (Ochrona przed błędnymi wpisami) ---
        if current_lang in ("pl", "en", "de", "fr", "es", "no", "nb"): 
            users_dict[chat_id]["lang"] = "no" if current_lang in ("no", "nb") else current_lang

    final_users = []
    for u in users_dict.values():
        if u["lat"] is not None and u["lon"] is not None:
            u["tz"] = _resolve_tz(u["lat"], u["lon"])
            
            # --- TARCZA DOMENOWA (Hotfix) ---
            # Jeśli nie ma wpisanego miasta w Arkuszu, wstawiamy Twoja okolica.
            # Absolutnie NIE używamy u["imie"]!
            u["name"] = u["miasto"] if u["miasto"] else "Twoja okolica"
            
            final_users.append(u)

    return final_users


# ═══════════════════════════════════════
# OKNO WYSYŁKI
# ═══════════════════════════════════════

def _in_send_window(tz_name: str, godz_rano_str: str, godz_wiecz_str: str, chat_id: str | int) -> tuple[bool, str, str, bool]:
    """
    Rozproszone okno wysyłki z zabezpieczeniem przed Thundering Herd i retry-gatingiem.
    Użytkownik dostaje offset (1-10 min) i próbuje wysłać przez kolejne 15 minut.
    """
    # 1. Obliczamy unikalny offset dla użytkownika (od 1 do 10 minut)
    try:
        user_offset = (abs(int(chat_id)) % 10) + 1
    except Exception:
        user_offset = 1  # Bezpieczny fallback

    # Maksymalny czas na próby (w minutach) od momentu staggered startu
    RETRY_WINDOW = 15  

    try:
        local_now = datetime.now(ZoneInfo(tz_name))
    except Exception as e:
        print(f"[main_card] ⚠️ TZ fallback dla '{tz_name}': {e}. Używam UTC.")
        local_now = datetime.now(timezone.utc)

    local_date_str = local_now.strftime("%Y-%m-%d")
    is_quiet = 0 <= local_now.hour < 7

    def _parse_hm(s: str):
        try:
            s = (s or "").strip()
            hh = int(s.split(":")[0])
            mm = int(s.split(":")[1]) if ":" in s else 0
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return hh, mm
        except Exception:
            pass
        return None, None

    def _match_with_grace(hh, mm) -> bool:
        if hh is None:
            return False
        # Tworzymy idealny cel (np. dzisiaj o 08:30)
        target = local_now.replace(hour=hh, minute=mm or 0, second=0, microsecond=0)
        # Obliczamy, ile minut minęło od tego idealnego celu
        delta_min = (local_now - target).total_seconds() / 60.0
        
        # --- ROZPROSZONE OKNO WYSYŁKI ---
        # Start: dopiero po upływie 'user_offset' minut od planowanej godziny
        # Koniec: 15 minut później
        return user_offset <= delta_min <= (user_offset + RETRY_WINDOW)

    h_r, m_r = _parse_hm(godz_rano_str)
    h_w, m_w = _parse_hm(godz_wiecz_str)

    if _match_with_grace(h_r, m_r):
        return True, "RANO", local_date_str, is_quiet
    if _match_with_grace(h_w, m_w):
        return True, "POPOLUDNIE", local_date_str, is_quiet

    return False, "NONE", local_date_str, False


# ═══════════════════════════════════════
# TRWAŁY CACHE (JSON)
# ═══════════════════════════════════════

def _load_cache() -> dict:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _prune_cache(cache: dict, keep_days: int = 2) -> dict:
    """Czyści wpisy starsze niż keep_days, utrzymując lekkość pliku JSON."""
    try:
        cutoff = (datetime.now(timezone.utc).date() - timedelta(days=keep_days)).isoformat()
    except Exception:
        return cache or {}
    out = {}
    for k, v in (cache or {}).items():
        if isinstance(v, str) and v >= cutoff:
            out[k] = v
    return out

def _save_cache(cache_data: dict):
    try:
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=4)
        os.replace(tmp, CACHE_FILE)  # Atomowy zapis chroni plik przed przerwami w zasilaniu
    except Exception as e:
        print(f"[main_card] Błąd zapisu cache: {e}")


# ═══════════════════════════════════════
# GŁÓWNA FUNKCJA WYSYŁKI
# ═══════════════════════════════════════

def _is_429(e: Exception) -> bool:
    """Helper do pancernego rozpoznawania błędów Quota/Rate Limit od Google API."""
    s = str(e).lower()
    if "429" in s or "resource has been exhausted" in s or "quota" in s:
        return True
        
    # próba wyciągnięcia status_code z obiektu (gspread czasem ma response jako obiekt)
    resp = getattr(e, "response", None)
    if resp is not None:
        code = getattr(resp, "status_code", None)
        if code == 429:
            return True
            
    return False

def run_send_cycle():
    """
    Jeden cykl wysyłki — wywołuj co minutę ze schedulera.
    Wysyła kartę do każdego aktywnego użytkownika, jeśli:
    - jest w oknie wysyłki
    - karta nie była jeszcze wysłana w tym oknie
    """
    
    # --- P1: Backoff/Retry w razie rzucenia 429 Quota Exceeded/Rate Limit ---
    MAX_RETRIES = 3
    users = []
    
    for attempt in range(MAX_RETRIES):
        try:  # <--- TO MUSI BYĆ WCIĘTE (Tab)
            raw = _load_users_from_sheet()
            sklejone = wirtualne_scalanie(raw)
            users = _parse_users(sklejone)
            break  # Udało się, przerywamy pętlę prób
        except Exception as e: # <--- TO TEŻ WCIĘTE W RÓWNEJ LINII Z TRY
            # Sprawdzamy czy to błąd typu Rate Limit (429)
            is_rate_limit = _is_429(e)
            
            # Sprawdzamy czy to tymczasowy błąd serwera Google (500, 502, 503, 504)
            err_str = str(e)
            is_temporary_server_error = any(code in err_str for code in ["500", "502", "503", "504"])

            if (is_rate_limit or is_temporary_server_error) and attempt < MAX_RETRIES - 1:
                if is_rate_limit:
                    sleep_time = 30 * (attempt + 1) + random.uniform(1, 5)
                    print(f"⚠️ [API 429] Rate Limit Google Sheets. Ponawiam {attempt+1}/{MAX_RETRIES} za {sleep_time:.1f}s...")
                else:
                    sleep_time = 3 * (attempt + 1) + random.uniform(1, 3)  
                    print(f"⚠️ [API {err_str[:15]}] Chwilowy błąd Google Sheets. Ponawiam {attempt+1}/{MAX_RETRIES} za {sleep_time:.1f}s...")
                
                time.sleep(sleep_time)
            else:
                print(f"[main_card] Krytyczny błąd ładowania użytkowników: {e}")
                import sys
                sys.exit(1)

    cache = _load_cache()
    # Puszczamy auto-sprzątaczkę
    cache_pruned = _prune_cache(cache, keep_days=2)
    if cache_pruned != cache:
        cache = cache_pruned
        _save_cache(cache)

    for user in users:
        chat_id = user["chat_id"]
        # Przekazujemy preferencje do okna wysyłki
        in_window, window_name, local_date_str, is_quiet = _in_send_window(
            user["tz"], user.get("godzina_rano", ""), user.get("godzina_wieczor", ""), chat_id
        )

        if not in_window:
            continue

        # --- NOWY, PRECYZYJNY KLUCZ PAMIĘCI (POZWALA NA WIELOKROTNE ALARMY PO ZMIANIE GODZINY) ---
        godzina_z_arkusza = user.get("godzina_rano", "") if window_name == "RANO" else user.get("godzina_wieczor", "")
        cache_key = f"{chat_id}_{window_name}_{godzina_z_arkusza}"
        
        if cache.get(cache_key) == local_date_str:
            continue

        try:
            # Przekazujemy info o wyciszeniu do funkcji wysyłającej
            success = _send_card_to_user(user, is_quiet)
            if success:
                cache[cache_key] = local_date_str
                _save_cache(cache)
                
            # --- DODANY "ODDECH" DLA OPEN-METEO ---
            # Po wygenerowaniu karty dla usera (nawet jeśli wysyłka się nie powiodła, ale odpytaliśmy API)
            # czekamy 2.5 sekundy, żeby nie zasypać darmowego API żądaniami z crona.
            time.sleep(2.0 + random.random() * 1.0) 
            
        except Exception as e:
            print(f"[main_card] Błąd dla {chat_id}: {e}")
            time.sleep(2.0 + random.random() * 1.0) # Odczekajmy nawet w przypadku błędu


def _send_card_to_user(user: dict, is_quiet: bool = False, is_now: bool = False, is_future: bool = False) -> bool:

    chat_id = user["chat_id"]
    print(f"[main_card] Wysyłam do {chat_id} ({user['name']})...")

    # 1. Payload
    payload = build_payload_for_location(
        lat=user["lat"],
        lon=user["lon"],
        tz_name=user["tz"],
        location_name=user["name"],
        lang=user.get("lang", "en"),  # Fallback na angielski
    )

    # ══════════════════════════════════════════════════════════
    # TWARDA BLOKADA JAKOŚCI (Gwarancja 2 modeli dla głównego raportu)
    # ══════════════════════════════════════════════════════════
    # Sprawdzamy blokadę tylko dla standardowego raportu /day (żeby nie blokować /now i /future)
    if not is_now and not is_future:
        hours = payload.get("hours", [])
        has_om = any(h.get("source") == "openmeteo" for h in hours)
        has_yr = any(h.get("source") == "yrno" for h in hours)
        
        if not (has_om and has_yr):
            print(f"[main_card] 🛑 Odrzucam: Brak dwóch źródeł danych dla {chat_id}. Odkładam na kolejną minutę.")
            return False  # Zwracamy False! Bot ucieka z funkcji.
    # ══════════════════════════════════════════════════════════
    
    # ==================================================================
    # FILTR OPADÓW WIDMOWYCH (VIRGA / PHANTOM RAIN) - WERSJA PRO
    # Zabezpiecza UX przed mżawkami (<= 0.2 mm), uwzględniając chmury niskie i RH
    # ==================================================================
    if "hours" in payload:
        for h in payload["hours"]:
            prc = float(h.get("precip_mm") or 0.0)
            pop = float(h.get("precip_prob_pct", h.get("pop_pct", h.get("pop", 0.0))) or 0.0)
            
            rh_raw = h.get("rh_pct")
            rh = float(rh_raw) if rh_raw is not None else None
            
            # Całkowite chmury (do ikon)
            cld_om = float(h.get("clouds_pct") or 0.0)
            cld_yr = float(h.get("clouds_pct_yr") or cld_om)
            cld_max = max(cld_om, cld_yr)
            
            # Niskie chmury (do detekcji mżawki)
            low_om = float(h.get("clouds_low_pct") or 0.0)
            low_yr = float(h.get("clouds_low_pct_yr") or low_om)
            low_max = max(low_om, low_yr)
            
            expected_mm = prc * (pop / 100.0)
            
            # Domyślne wartości (przechodzą bez zmian)
            h["precip_eff_mm"] = prc
            h["weather_code_eff"] = h.get("weather_code")
            h["symbol_code_eff"] = h.get("symbol_code")
            h["precip_class_eff"] = "rain" if prc > 0 else "none"
            
            # Warunek na RH (jeśli brak danych, wyłączamy sprawdzanie, żeby nie psuć)
            rh_too_dry = False if rh is None else (rh < 75)
            
            # FILTR: Opady <= 0.2 mm. Wyrzucamy, jeśli wystąpi CHOĆ JEDEN z objawów fałszywego opadu:
            if 0 < prc <= 0.2 and (expected_mm < 0.05 or low_max < 50 or rh_too_dry):
                h["precip_eff_mm"] = 0.0
                h["precip_class_eff"] = "trace" # Śladowy opad (nie wyzwala alarmów)
                
                code = h.get("weather_code")
                # Jeśli kod wskazuje na opad, podmieniamy go na rzeczywiste zachmurzenie
                if code in [51, 53, 55, 61, 63, 65, 80, 81, 82]:
                    if cld_max < 10:
                        h["weather_code_eff"] = 0
                    elif cld_max < 30:
                        h["weather_code_eff"] = 1
                    elif cld_max < 70:
                        h["weather_code_eff"] = 2
                    else:
                        h["weather_code_eff"] = 3
                        
                    # Naprawa symbol_code
                    sym = h.get("symbol_code") or ""
                    suffix = ""
                    if "_day" in sym: suffix = "_day"
                    elif "_night" in sym: suffix = "_night"
                    
                    if cld_max < 10: h["symbol_code_eff"] = f"clearsky{suffix}"
                    elif cld_max < 30: h["symbol_code_eff"] = f"fair{suffix}"
                    elif cld_max < 70: h["symbol_code_eff"] = f"partlycloudy{suffix}"
                    else: h["symbol_code_eff"] = "cloudy"
    # ==================================================================

    # 2. Layout (ROZWIDLENIE ARCHITEKTONICZNE)
    if is_now:
        layout = prepare_now_layout_data(payload)
    elif is_future:
        layout = prepare_future_layout_data(payload)
    else:
        layout = prepare_layout_data(payload)
        
    # === KONTROLA ZDROWEGO ROZSĄDKU (Sanity Check) ===   
    anomalies = run_sanity_check(layout, payload)
    if anomalies:
        print(f"[SANITY CHECK] ⚠️ Wykryto anomalie dla {chat_id}:")
        for a in anomalies:
            print(f"  -> {a}")
        # Poniżej możesz odkomentować linię, która wyśle Ci wiadomość na Telegram
        # requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data={"chat_id": "TWOJE_ID_ADMINA", "text": "⚠️ Anomalie:\n" + "\n".join(anomalies)})
    
    # 3. Render
    img_path = image_generator.generate_weather_card(layout)
    if not img_path:
        print(f"[main_card] Błąd renderu dla {chat_id}")
        return False

    # 4. Wyślij
    ok = send_telegram_photo(chat_id, img_path, disable_notification=is_quiet)
    if ok:
        print(f"[main_card] ✅ Wysłano do {chat_id}")
        return True
    else:
        print(f"[main_card] ❌ Błąd wysyłki do {chat_id}")
        return False

def run_smoke_test(target_chat_id: str, is_now: bool = False, is_future: bool = False):

    """Admin-only path: wysyła kartę do podanego chat_id ignorując okna czasowe i cache."""
    print(f"[SMOKE TEST] Uruchamiam test dla chat_id: {target_chat_id}...")
    try:
        raw = _load_users_from_sheet()
        sklejone = wirtualne_scalanie(raw)    
        users = _parse_users(sklejone)
    except Exception as e:
        print(f"[SMOKE TEST] Błąd ładowania bazy: {e}")
        return

    user = next((u for u in users if str(u["chat_id"]) == target_chat_id), None)
    if not user:
        print(f"[SMOKE TEST] Nie znaleziono użytkownika {target_chat_id} w arkuszu.")
        return

    print(f"[SMOKE TEST] Znalazłem użytkownika: {user['name']}. Omijam cache i scheduler.")
    success = _send_card_to_user(user, is_now=is_now, is_future=is_future)
    if success:
        print("[SMOKE TEST] Zakończony pełnym sukcesem.")
    else:
        print("[SMOKE TEST] Zakończony błędem na etapie wysyłki/renderu.")

# ═══════════════════════════════════════
# ENTRYPOINT
# ═══════════════════════════════════════

if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        try:
            # Szukamy ID użytkownika zaraz po słowie --smoke
            idx = sys.argv.index("--smoke")
            target_id = sys.argv[idx + 1]
            
            # Wyłapujemy dodatkowe flagi
            test_now = "--now" in sys.argv
            test_future = "--future" in sys.argv
            
            run_smoke_test(target_id, is_now=test_now, is_future=test_future)
        except IndexError:
            print("❌ Użycie: python main_card.py --smoke <TWOJE_ID> [--now] [--future]")
    else:
        run_send_cycle()