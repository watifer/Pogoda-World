import os
import json
import requests
import gspread
import main_card
from i18n import t_ui
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from main_card import _parse_users, _send_card_to_user, wirtualne_scalanie, _load_users_from_sheet, DEFAULT_RANO, DEFAULT_WIECZOR, _resolve_tz
from geopy.geocoders import Nominatim
from guest_bot_handler import handle_guest_now
from prepare_now_layout import prepare_now_layout_data
from prepare_layout import prepare_layout_data
from weather_payload import build_payload_for_location
import image_generator

load_dotenv()
#Furtka do testowanie bota bez zaproszenia
MASTER_TOKEN = os.environ.get("MASTER_TOKEN", "DEV_TEST")
import time

# =====================================================================
# PAMIĘĆ RAM DLA STANU OCZEKIWANIA NA MIASTO (State Machine z TTL)
# =====================================================================
PENDING_CITY = {}       # Format: { str(chat_id): expires_timestamp }
PENDING_TTL_SEC = 300   # 5 minut (300 sekund) na wpisanie miasta




# ==============================================================
# WŁASNE FUNKCJE POMOCNICZE (Zamiast importu z main)
# ==============================================================
def _public_codes():
    """
    Pobiera z .env listę aktywnych kodów promocji publicznej.
    Pozwala na łatwą rotację i kilka kampanii jednocześnie.
    """
    s = os.environ.get("PUBLIC_BETA_CODES", "").strip()
    if s:
        return {x.strip() for x in s.split(",") if x.strip()}
    one = os.environ.get("PUBLIC_BETA_CODE", "").strip()
    return {one} if one else set()

def get_user_lang(message):
    """
    Bezpiecznie wyciąga język z danych Telegrama (dla osób spoza bazy).
    """
    user_lang = (message.get("from", {}) or {}).get("language_code", "en")[:2].lower()
    
    if user_lang in ("no", "nb"):
        return "no"
    elif user_lang in ("pl", "en", "de", "fr", "es"):
        return user_lang
    else:
        return "en"  # Fallback dla całej reszty świata (np. Włochy, Japonia)


def get_city_from_coords(lat, lon, lang="pl"):
    try:
        geolocator = Nominatim(user_agent="pogoda_world_bot")  # nazwa: pogoda_world_bot tylko dla geolokalizacji od OpenStreetMap bez zwiazku z Telegramem
        # ZMIANA: Wstrzykujemy język użytkownika (lang) zamiast twardego "pl"
        location = geolocator.reverse(f"{lat}, {lon}", language=lang)
        
        if location and location.raw.get('address'):
            addr = location.raw['address']
            
            nazwa = (addr.get('city') or 
                     addr.get('town') or 
                     addr.get('village') or 
                     addr.get('suburb') or         
                     addr.get('city_district') or  
                     addr.get('state_district') or 
                     addr.get('hamlet') or 
                     addr.get('municipality') or 
                     addr.get('county') or
                     addr.get('state'))            
            
            if nazwa:
                return nazwa
            else:
                return "Lokalizacja w terenie (poza miastem)"
                
    except Exception as e:
        print(f"Błąd geolokalizacji: {e}")
        
    return "Lokalizacja w terenie"
    
def get_coords_from_city(city_name, lang="pl"):
    try:
        geolocator = Nominatim(user_agent="pogoda_world_bot")
        # ZMIANA: Wstrzykujemy język użytkownika przy szukaniu miasta!
        location = geolocator.geocode(city_name, exactly_one=True, language=lang)
        if location:
            return location.latitude, location.longitude, location.address
    except Exception as e:
        print(f"Błąd wyszukiwania miasta po nazwie: {e}")
    return None, None, None

def alert_admin(text):
    admin_id = os.environ.get("TG_CHAT_ID")
    token = os.environ.get("TG_TOKEN")
    if admin_id and token:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                "chat_id": admin_id, "text": f"⚠️ ALERT BOTA LOKALIZACJI:\n{text}"
            }, timeout=10)
        except Exception as e:
            print(f"⚠️ Nie udało się wysłać alertu do admina: {e}")

# ==============================================================
# ⚠️ KONFIGURACJA ZAPROSZEŃ I LINKÓW (Wypełnij to!)
# ==============================================================

# 1. WPISZ NAZWĘ SWOJEGO BOTA (bez znaku @ na początku):
BOT_USERNAME = "Twoja_pogoda_bot" # np. "PogodaWorldBot"


# 2. Link do formularza (Zabezpieczone w .env):
FORM_BASE = os.environ.get("FORM_BASE")
ENTRY_ID = os.environ.get("FORM_ENTRY_ID")

# Zamiast wpisywać na sztywno, pobieramy z pliku środowiskowego:
INVITE_URL = os.environ.get("PUBLIC_INVITE_URL", "https://watifer.github.io/Pogoda-World/invite/")
# ==============================================================

TELEGRAM_TOKEN = os.environ.get("TG_TOKEN")
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def get_google_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly"
    ]
    creds_json = os.environ.get("GOOGLE_CREDS_JSON")
    if creds_json:
        creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=scopes)
    else:
        creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
    return gspread.authorize(creds)

def get_offset(gc):
    try:
        state_sheet = gc.open("Pogoda_Users").worksheet("Bot_State")
        val = state_sheet.acell('B1').value
        return int(val) if val else 0
    except Exception as e:
        print(f"  ⚠️ Błąd odczytu pamięci bota: {e}")
        return 0

def save_offset(gc, offset):
    try:
        state_sheet = gc.open("Pogoda_Users").worksheet("Bot_State")
        state_sheet.update_acell('B1', offset)
    except Exception as e:
        print(f"  ⚠️ Błąd zapisu pamięci bota: {e}")

def send_reply(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id, 
        "text": text, 
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
        
    try:
        # Timeout 10 sekund zabezpiecza bota przed zawieszeniem
        resp = requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=10)
        
        # --- WYKRYWACZ BŁĘDÓW TELEGRAMA (NOWE) ---
        response_data = resp.json()
        if not response_data.get("ok"):
            print(f"⚠️ TELEGRAM ODRZUCIŁ WIADOMOŚĆ: {response_data.get('description')}")
        # -----------------------------------------
        
        # --- SMART AUTO-SPRZĄTACZKA ---
        from db_cleanup import is_bot_blocked, mark_user_as_blocked
        if is_bot_blocked(resp):
            gc = get_google_client() # Używamy Twojej funkcji do pobrania dostępu do Sheets
            mark_user_as_blocked(gc, chat_id)
            
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Błąd sieci podczas wysyłania wiadomości (Timeout/DNS): {e}")
        
        
def send_photo(chat_id, photo_path, caption=None, parse_mode="Markdown"):
    """Bezpośredni wysyłacz kart graficznych PNG dla trybu gościa i nie tylko."""
    try:
        with open(photo_path, "rb") as photo:
            payload = {"chat_id": chat_id}
            if caption:
                payload["caption"] = caption
            if parse_mode:
                payload["parse_mode"] = parse_mode
            requests.post(f"{BASE_URL}/sendPhoto", data=payload, files={"photo": photo}, timeout=15)
    except Exception as e:
        print(f"⚠️ Błąd wysyłania zdjęcia (sendPhoto) do {chat_id}: {e}")
        
        

def main_bot():
    print("🤖 Uruchamiam system nasłuchiwania (Location Bot)...")
    gc = get_google_client()
    offset = get_offset(gc)
    
    try:
        # Timeout w 'params' to Long Polling (dla Telegrama). 
        # Timeout=10 to zabezpieczenie gniazda sieciowego dla Pythona.
        resp = requests.get(f"{BASE_URL}/getUpdates", params={"offset": offset, "timeout": 5}, timeout=10)
        data = resp.json()
    except Exception as e:
        print(f"  ⚠️ Błąd sieci podczas nasłuchiwania Telegrama: {e}")
        return
    
    if not data.get("ok") or not data.get("result"):
        print("  📭 Cisza w eterze. Brak nowych wiadomości.")
        return

    updates = data["result"]
    print(f"  📬 Pobrano {len(updates)} nowych operacji do przetworzenia.")
    
    main_sheet = gc.open("Pogoda_Users").worksheet("Formularz")
    users_records = main_sheet.get_all_records(value_render_option='UNFORMATTED_VALUE')
    
    # --- PANCERNE NAGŁÓWKI DLA NOWYCH REJESTRACJI I PINEZEK ---
    raw_headers = main_sheet.row_values(1)
    headers = [str(h).strip() for h in raw_headers]
    
    clean_users = wirtualne_scalanie(users_records)
    highest_update_id = offset

    for update in updates:
        highest_update_id = update["update_id"] + 1
        
        try:
            message = update.get("message", {})
            chat_id = message.get("chat", {}).get("id")
            
            if not chat_id: 
                continue
            
            # --- DODAJ TO TUTAJ: Błyskawiczne pobranie języka dla Gościa ---
            raw_guest = message.get("from", {}).get("language_code", "en")[:2].lower()
            guest_lang = "no" if raw_guest in ("no", "nb") else raw_guest
            if guest_lang not in ("pl", "en", "de", "fr", "es", "no"):
                guest_lang = "en"
            # ---------------------------------------------------------------

            # ==============================================================
            # 0A. TRYB GOŚCIA I SZYBKIE SKRÓTY (.n, .d, .f)
            # ==============================================================
            is_guest = handle_guest_now(
                message=message,
                bot_username=BOT_USERNAME, 
                get_coords_fn=get_coords_from_city,
                
                # ZMIANA: Przekazujemy c_type, by ustawić is_now i is_future tam, gdzie trzeba (w API)
                build_payload_fn=lambda lat, lon, lang, c_type, city_name: build_payload_for_location(
                    lat=lat,
                    lon=lon,
                    tz_name=_resolve_tz(lat, lon), 
                    location_name=city_name if city_name else "Twoja okolica",
                    lang=lang,
                    is_now=(c_type == "now"),
                    is_future=(c_type == "future")
                ),
                
                # ZMIANA: Usunięto błędy 'is_future' z funkcji przygotowującej układ
                prepare_layout_fn=lambda payload, c_type: (
                    prepare_now_layout_data(payload) if c_type == "now" 
                    else prepare_layout_data(payload)
                ),
                
                render_png_fn=image_generator.generate_weather_card,
                
                send_photo_fn=lambda c_id, path, city_name, f_address: send_photo(
                    c_id, 
                    path, 
                    caption=f"<b>{str(city_name).replace('<', '').replace('>', '')}</b>\n<i>{str(f_address).replace('<', '').replace('>', '')}</i>" if f_address else f"<b>{str(city_name).replace('<', '').replace('>', '')}</b>", 
                    parse_mode="HTML"
                ),
                send_reply_fn=lambda c_id, txt: send_reply(c_id, txt),
                get_city_fn=get_city_from_coords
            )
            
            if is_guest:
                # Wiadomość była @wzmianką w grupie/priv i została obsłużona.
                # Przerywamy obieg pętli dla tej wiadomości – NIE idziemy do autoryzacji arkusza!
                continue
            # ==============================================================

            # ==============================================================
            # 0. ABSOLUTNY PRIORYTET: LOKALIZACJA Z WEB APP (GPS)
            # ==============================================================
            wad = message.get("web_app_data")
            if wad and wad.get("data"):
                
                # --- SZYBKIE POBRANIE JĘZYKA Z BAZY DLA WEBAPP ---
                user_lang = "en"  # Domyślnie angielski (globalny fallback)
                for u in clean_users:
                    if str(u.get("Chat ID", "")).strip() == str(chat_id):
                        lang_z_bazy = str(u.get("Lang", u.get("Język", ""))).strip().lower()
                        if lang_z_bazy in ("pl", "en", "de", "fr", "es", "no", "nb"):
                            user_lang = "no" if lang_z_bazy in ("no", "nb") else lang_z_bazy
                        break
                # -------------------------------------------------
                
                raw_data = wad.get("data", "")
                print(f"  [DEBUG-WEBAPP] Otrzymano czyste dane z WebApp: {raw_data}")
                
                try:
                    data = json.loads(raw_data)
                    if data.get("type") == "set_location":
                        lat = float(data.get("lat"))
                        lon = float(data.get("lon"))
                        
                        print(f"  📍 Odebrano współrzędne GPS od {chat_id}: {lat}, {lon}")
                        
                        # Znajdujemy wszystkie wiersze użytkownika
                        rows_to_update = []
                        for idx, r in enumerate(users_records):
                            if str(r.get("Chat ID", "")).strip() == str(chat_id):
                                rows_to_update.append(idx + 2)
                        
                        if not rows_to_update:
                            try:
                                komorka = main_sheet.find(str(chat_id), in_column=2)
                                rows_to_update.append(komorka.row)
                            except Exception:
                                print("  [DEBUG-WEBAPP] Nie znalazłem usera w bazie!")
                        
                        # Geolokalizacja w języku użytkownika
                        city = get_city_from_coords(lat, lon, user_lang) 
                        if city == "Lokalizacja w terenie" or not city:
                            city = "Twoja okolica"
                            
                        # Aktualizacja Google Sheets
                        if rows_to_update:
                            col_lat = headers.index("Lat") + 1
                            col_lon = headers.index("Lon") + 1
                            col_miasto = headers.index("Miasto") + 1 if "Miasto" in headers else None
                            
                            for r_idx in rows_to_update:
                                main_sheet.update_cell(r_idx, col_lat, lat)
                                main_sheet.update_cell(r_idx, col_lon, lon)
                                if col_miasto:
                                    main_sheet.update_cell(r_idx, col_miasto, city)
                                    
                        # Wysłanie przetłumaczonej wiadomości
                        ukryj_klawiature = {"remove_keyboard": True}
                        sukces_msg = t_ui(user_lang, "loc_updated", city=city)
                        send_reply(chat_id, sukces_msg, reply_markup=ukryj_klawiature)
                        
                    elif data.get("type") == "set_settings":
                        rano = (data.get("rano") or "").strip()
                        wieczor = (data.get("wieczor") or "").strip()
                        print(f"  ⚙️ Odebrano nowe godziny od {chat_id}: Rano={rano}, Popołudnie={wieczor}")
                        
                        # Znajdujemy wiersz użytkownika (analogicznie do GPS)
                        rows_to_update = []
                        for idx, r in enumerate(users_records):
                            if str(r.get("Chat ID", "")).strip() == str(chat_id):
                                rows_to_update.append(idx + 2)
                        
                        if not rows_to_update:
                            try:
                                komorka = main_sheet.find(str(chat_id), in_column=2)
                                rows_to_update.append(komorka.row)
                            except Exception:
                                print("  [DEBUG] Nie znalazłem usera do zapisu godzin!")
                                
                        if rows_to_update:
                            col_rano = headers.index("Raport poranny") + 1 if "Raport poranny" in headers else None
                            col_wieczor = headers.index("Aktualizacja") + 1 if "Aktualizacja" in headers else None
                            
                            for r_idx in rows_to_update:
                                # Zapisujemy TYLKO jeśli użytkownik wybrał jakąś godzinę lub "brak"
                                if col_rano and rano:
                                    # Apostrof chroni przed zmianą na ułamek przez Google Sheets
                                    zapis_rano = f"'{rano}" if rano != "brak" else rano
                                    main_sheet.update_cell(r_idx, col_rano, zapis_rano)
                                    
                                if col_wieczor and wieczor:
                                    zapis_wieczor = f"'{wieczor}" if wieczor != "brak" else wieczor
                                    main_sheet.update_cell(r_idx, col_wieczor, zapis_wieczor)

                        # Zamykamy klawiaturę WebApp i wysyłamy potwierdzenie
                        ukryj_klawiature = {"remove_keyboard": True}
                        
                        # Próba pobrania tłumaczenia (zabezpieczenie, gdyby brakowało klucza)
                        try:
                            msg_to_send = t_ui(user_lang, "settings_saved")
                        except Exception:
                            msg_to_send = "✅ Ustawienia raportów zostały zapisane!"
                            
                        send_reply(chat_id, msg_to_send, reply_markup=ukryj_klawiature)
                        continue    
                        
                        
                        
                except Exception as e:
                    send_reply(chat_id, "⚠️ Błąd zapisu lokalizacji z GPS. Spróbuj za chwilę.")
                    alert_admin(f"❌ Błąd aktualizacji GPS (WebApp) dla {chat_id}: {e}")
                
                # Zawsze przerywamy pętlę dla paczki GPS
                continue

            # ==============================================================
            # STANDARDOWA OBSŁUGA BOTA
            # ==============================================================
            print(f"  [DEBUG] 🔎 Telegram zgłasza wiadomość (tekst/komenda) od Chat ID: {chat_id}")
            
            user_row_index = None
            user_data = None
            for i, u in enumerate(clean_users):
                u_id_z_bazy = str(u.get("Chat ID", "")).strip()
                if u_id_z_bazy == str(chat_id):
                    user_row_index = i + 2
                    user_data = u
                    break 
            # --- BEZPIECZNE POBIERANIE JĘZYKA Z BAZY ---
            user_lang = "en" # Domyślnie angielski
            if user_data:
                raw_l = str(user_data.get("Lang", user_data.get("Język", ""))).strip().lower()
                if raw_l in ("pl", "en", "de", "fr", "es", "no", "nb"):
                    user_lang = "no" if raw_l in ("no", "nb") else raw_l
                    
            
            # ==============================================================
            # BRAMKA WEJŚCIOWA (Tylko Zaproszenia + Kody Beta)
            # ==============================================================
            if not user_row_index:
                text = message.get("text", "").strip()
                wykryty_jezyk = get_user_lang(message)
                
                # Ciche ignorowanie zdarzeń bez tekstu (naklejki, systemowe wiadomości z grup)
                if not text:
                    continue
                
                parts = text.split()
                
                # Miękkie lądowanie dla ludzi, którzy wpisali samo /start (bez kodu)
                if text.startswith("/start") and len(parts) < 2:
                    send_reply(chat_id, t_ui(wykryty_jezyk, "no_access", url=INVITE_URL))
                    continue
                
                # Ciche ignorowanie spamu (ktoś pisze "cześć", "help" bez komendy /start)
                if not (text.startswith("/start") and len(parts) >= 2):
                    continue
                    
                # Pobieramy token wejściowy
                token = parts[1].strip()
                
                # --- ROZSZYFROWANIE TOKENA ZAPROSZENIA (Referral ID) ---
                import base64
                try:
                    padding = 4 - (len(token) % 4)
                    referrer_id = base64.urlsafe_b64decode(token + "=" * padding).decode()
                except Exception:
                    referrer_id = token  # Fallback dla linków z jawnym ID
                
                # --- WALIDACJA UPRAWNIEŃ ---
                is_dev_mode = (token == MASTER_TOKEN)
                is_public_beta = (token in _public_codes())
                is_referral = any(str(u.get("Chat ID", "")).strip() == str(referrer_id) for u in clean_users)
                
                # Jeśli kod nie pasuje do niczego (nie jest adminem, kodem beta ani poleceniem od usera)
                if not (is_dev_mode or is_public_beta or is_referral):
                    send_reply(chat_id, t_ui(wykryty_jezyk, "invalid_link", url=INVITE_URL))
                    continue

                # 1. Weryfikacja limitu miejsc (Podniesiono z 50 do 200)
                # Dev (God Mode) wchodzi zawsze, reszta jest blokowana po osiągnięciu limitu
                if len(clean_users) >= 200 and not is_dev_mode:
                    send_reply(chat_id, t_ui(wykryty_jezyk, "limit_reached", url=INVITE_URL))
                    continue
                    
                # 2. Mamy autoryzację! Przystępujemy do rejestracji:
                chat_title = message.get("chat", {}).get("title")
                first_name = message.get("from", {}).get("first_name", "Nieznany")
                nowa_nazwa = chat_title if chat_title else first_name
                #   Wykrycie języka w Telegramie przy rejestracji
                # Wykrycie języka z Telegrama + bezpieczny fallback do angielskiego
                raw_lang = message.get("from", {}).get("language_code", "en")[:2].lower()
                OBSLUGIWANE_JEZYKI = ("pl", "en", "de", "fr", "es", "no", "nb")

                # Jeśli język użytkownika jest na naszej liście, zostaw go (no/nb konwertujemy na no)
                if raw_lang in OBSLUGIWANE_JEZYKI:
                    wykryty_jezyk = "no" if raw_lang in ("no", "nb") else raw_lang
                else:
                    # Włoch, Czech, Japończyk itp. dostają angielski!
                    wykryty_jezyk = "en"

                print(f"  [DEBUG] 🌟 Nowy klient z ZAPROSZENIA! Dodaję [{nowa_nazwa}] (ID: {chat_id})")
                
                try:
                    from datetime import datetime
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    new_row = [""] * len(headers)
                    def put_val(col_name, val):
                        if col_name in headers:
                            new_row[headers.index(col_name)] = val
                    
                    put_val("Sygnatura czasowa", ts)
                    put_val("Chat ID", str(chat_id))
                    put_val("Imię", nowa_nazwa)
                    put_val("Miasto", "")
                    put_val("Raport poranny", "")
                    put_val("Aktualizacja", "")
                    put_val("Lang", wykryty_jezyk)
                    
                    main_sheet.insert_row(new_row, index=2, value_input_option='USER_ENTERED')
                    
                    user_data = {
                        "Sygnatura czasowa": ts, "Chat ID": str(chat_id), "Imię": nowa_nazwa,
                        "Lat": "", "Lon": "", "Raport poranny": "", "Aktualizacja": ""
                    }
                    clean_users.append(user_data)
                    
                        
                    # Pobieramy tłumaczenie na przycisk i sprawdzamy typ czatu!
                    nazwa_przycisku = t_ui(wykryty_jezyk, "btn_update_gps")
                    tekst_powitania = t_ui(wykryty_jezyk, "welcome_new")
                    
                    if int(chat_id) > 0:
                        # CZAT PRYWATNY -> Wysyłamy WebApp
                        klawiatura_gps = {
                            "keyboard": [
                                [{"text": nazwa_przycisku, "web_app": {"url": f"https://watifer.github.io/Pogoda-World/webapp/?lang={wykryty_jezyk}"}}]
                            ],
                            "resize_keyboard": True
                        }
                        send_reply(chat_id, tekst_powitania, reply_markup=klawiatura_gps)
                    else:
                        # GRUPA (ID ujemne) -> Telegram zabrania WebApp na grupach!
                        # Dopisywanie krótkiej instrukcji tekstowej:
                        powitanie_dla_grupy = tekst_powitania + "\n\n💡 _Aby ustawić miasto dla tej grupy, wpisz po prostu:_ `/miasto Berlin` _(lub inną nazwę)._"
                        send_reply(chat_id, powitanie_dla_grupy)
                        
                    continue # Rejestracja zrobiona, pomijamy resztę pętli dla tej wiadomości
                    
                except Exception as e:
                    print(f"  [DEBUG] ❌ Błąd przy dodawaniu: {e}")
                    continue

            # ==============================================================
            # AKCJE DLA ZAREJESTROWANYCH UŻYTKOWNIKÓW
            # ==============================================================

            # 1. PINEZKA
            if "location" in message:
                if not message.get("reply_to_message"):
                    print(f"  [DEBUG] Ignoruję pinezkę od {chat_id} - to zwykła rozmowa na czacie.")
                    continue
                lat = message["location"]["latitude"]
                lon = message["location"]["longitude"]
                print(f"  📍 Odebrano współrzędne od [{user_data.get('Imię', chat_id)}]: {lat}, {lon}")
                
                try:
                    user_row_index = None
                    for idx, r in enumerate(users_records):
                        if str(r.get("Chat ID", "")).strip() == str(chat_id):
                            if str(r.get("Imię", "")).strip() != "":
                                user_row_index = idx + 2  
                                break
                    
                    if not user_row_index:
                        komorka = main_sheet.find(str(chat_id), in_column=2)
                        user_row_index = komorka.row
                    
                    col_lat = headers.index("Lat") + 1
                    col_lon = headers.index("Lon") + 1
                    
                    main_sheet.update_cell(user_row_index, col_lat, lat)
                    main_sheet.update_cell(user_row_index, col_lon, lon)
                    
                    city = get_city_from_coords(lat, lon, user_lang)
                    if city == "Lokalizacja w terenie" or not city:
                        city = "Twoja okolica"
                        
                    if "Miasto" in headers:
                        try:
                            col_miasto = headers.index("Miasto") + 1
                            main_sheet.update_cell(user_row_index, col_miasto, city)
                        except Exception as e:
                            print(f"  [DEBUG] Nie udało się zapisać miasta do arkusza: {e}")
                            
                    #send_reply(chat_id, f"✅ *Lokalizacja zaktualizowana!*\n\n📍 Rozpoznano: {city}\n🌤️ Od następnego raportu pogoda będzie liczona dla tego miejsca. ")
                    send_reply(chat_id, t_ui(user_lang, "loc_updated", city=city))
                except Exception as e:
                    send_reply(chat_id, "⚠️ Błąd zapisu na serwerze Google. Spróbuj za chwilę.")
                    alert_admin(f"❌ Błąd aktualizacji lokalizacji dla {chat_id}: {e}")

            


            # 2. /zapros (JEDEN UNIWERSALNY LINK + GUZIK DLA GRUP)
            elif message.get("text", "").lower().startswith(("/zapros", "/invite")):
                import base64
                print(f"  💌 Wysłano link zaproszeniowy do {chat_id}")
                
                # Kodowanie surowego chat_id na bezpieczny token URL
                token = base64.urlsafe_b64encode(str(chat_id).encode()).decode().rstrip("=")
                
                # 1. Czyste linki (niezbędne do poprawnego działania przycisków URL!)
                invite_link_priv = f"https://t.me/{BOT_USERNAME}?start={token}"
                invite_link_group = f"https://t.me/{BOT_USERNAME}?startgroup={token}"
                
                # 2. Bezpieczny link do wydrukowania w tekście (Markdown wymaga maskowania _)
                safe_link_priv = invite_link_priv.replace("_", "\\_")
                
                # Wiadomość 1 (Wstęp dla użytkownika)
                send_reply(chat_id, t_ui(user_lang, "invite_intro"))
                
                # Wiadomość 2 (Gotowa, czysta paczka do skopiowania - JEDEN LINK)
                msg_sms = t_ui(user_lang, "invite_sms", link=safe_link_priv)
                send_reply(chat_id, msg_sms)

                # Wiadomość 3 (Opcja dodania do własnej grupy ukryta pod przyciskiem z czystym linkiem)
                klawiatura = {
                    "inline_keyboard": [
                        [{"text": t_ui(user_lang, "invite_group_btn"), "url": invite_link_group}]
                    ]
                }
                send_reply(chat_id, t_ui(user_lang, "invite_group_desc"), reply_markup=klawiatura)

            # 3. /menu
            elif message.get("text", "").startswith("/menu"):
                print(f"  ⚙️ Odebrano żądanie panelu ustawień od [{user_data.get('Imię', chat_id)}]")
                
                # --- NOWY KOD: WYCIĄGANIE GODZIN ---
                godz_rano = str(user_data.get("Raport poranny", "")).strip()
                godz_wieczor = str(user_data.get("Aktualizacja", "")).strip()
                
                # Jeśli komórki w Google Sheets są puste, importujemy domyślne z main_card!
                if not godz_rano: godz_rano = DEFAULT_RANO
                if not godz_wieczor: godz_wieczor = DEFAULT_WIECZOR
                
                # Formatowanie widoku (wykrywanie opcji "Nie chcę")
                disp_rano = t_ui(user_lang, "disp_off") if "nie" in godz_rano.lower() else f"{godz_rano} ⏰"
                disp_wieczor = t_ui(user_lang, "disp_off") if "nie" in godz_wieczor.lower() else f"{godz_wieczor} ⏰"
                
                # Zabezpieczamy współrzędne przed przecinkami z Google Sheets
                bezpieczny_lat = str(user_data.get("Lat", 0)).replace(',', '.')
                bezpieczny_lon = str(user_data.get("Lon", 0)).replace(',', '.')
                city = get_city_from_coords(bezpieczny_lat, bezpieczny_lon, user_lang)
                
                if any(char.isdigit() for char in city):
                    city = "Nieznana miejscowość (wyślij pinezkę ponownie)"
                
                chat_title = message.get("chat", {}).get("title")
                imie_z_arkusza = str(user_data.get("Imię", "")).strip()
                
                wyswietlana_nazwa = chat_title if chat_title else imie_z_arkusza
                if not wyswietlana_nazwa:
                    wyswietlana_nazwa = "Użytkownik"
                
                # Budowanie przycisku WebApp otwierającego nowy panel (tylko dla czatów prywatnych!)
                try:
                    nazwa_przycisku = t_ui(user_lang, "btn_change_hours")
                except Exception:
                    nazwa_przycisku = "⚙️ Zmień ustawienia"

                if int(chat_id) > 0:
                    # CZAT PRYWATNY -> Tworzymy klawiaturę WebApp
                    klawiatura = {
                        "keyboard": [
                            [{"text": nazwa_przycisku, "web_app": {"url": f"https://watifer.github.io/Pogoda-World/webapp/?lang={user_lang}"}}]
                        ],
                        "resize_keyboard": True
                    }
                else:
                    # GRUPA (ID ujemne) -> Telegram zabrania WebApp na grupach!
                    # Ustawiamy None, żeby send_reply niżej nie wysyłało niedozwolonej klawiatury:
                    klawiatura = None

                # --- BUDOWANIE WIADOMOŚCI Z I18N (Teraz jest w dobrym miejscu!) ---
                msg = t_ui(user_lang, "menu_header", name=wyswietlana_nazwa, city=city, disp_rano=disp_rano, disp_wieczor=disp_wieczor)

                send_reply(chat_id, msg, reply_markup=klawiatura)

            # 4. /now
            elif message.get("text", "").startswith("/now") or message.get("text", "").startswith("/teraz"):
                print(f"  ⚡ Odebrano żądanie radaru taktycznego od [{user_data.get('Imię', chat_id)}]")
                try:
                    parsed_list = _parse_users([user_data])
                    if not parsed_list:
                        send_reply(chat_id, t_ui(user_lang, "missing_loc"))
                        continue
                except Exception as e:
                    send_reply(chat_id, "⚠️ Brakuje współrzędnych lub są uszkodzone! Wyślij pinezkę z mapy jeszcze raz.")
                    continue
                    
                send_reply(chat_id, t_ui(user_lang, "scanning"))
                user_parsed = parsed_list[0]
                
                try:
                    _send_card_to_user(user_parsed, is_quiet=False, is_now=True)
                except Exception as e:
                    send_reply(chat_id, t_ui(user_lang, "err_gen"))
            
            # 4.5. /day (karta dzienna)
            elif message.get("text", "").startswith(("/day", "/dzis", "/dzien")):
                print(f"  ☀️ Odebrano żądanie karty dziennej od [{user_data.get('Imię', chat_id)}]")
                try:
                    parsed_list = _parse_users([user_data])
                    if not parsed_list:
                        send_reply(chat_id, t_ui(user_lang, "missing_loc"))
                        continue
                except Exception as e:
                    send_reply(chat_id, "⚠️ Brakuje współrzędnych lub są uszkodzone! Wyślij pinezkę z mapy jeszcze raz.")
                    continue
                    
                user_parsed = parsed_list[0]
                
                # --- OGRANICZENIE CZASOWE DLA KARTY DZIENNEJ (05:00 - 15:59 lokalnego czasu) ---
                user_tz = user_parsed.get("tz", "UTC")
                try:
                    # Importy na wypadek, gdyby nie były na samej górze
                    from datetime import datetime
                    try:
                        from zoneinfo import ZoneInfo
                    except ImportError:
                        from backports.zoneinfo import ZoneInfo
                        
                    local_now = datetime.now(ZoneInfo(user_tz))
                    
                    # Jeśli jest przed 5:00 rano lub po 15:59
                    if local_now.hour < 5 or local_now.hour >= 16:
                        send_reply(chat_id, t_ui(user_lang, "time_limit"))
                        continue
                except Exception as e:
                    print(f"Błąd sprawdzania czasu: {e}")
                # --------------------------------------------------------------------------------
                    
                send_reply(chat_id, t_ui(user_lang, "prep_main"))
                user_parsed = parsed_list[0]
                
                try:
                    # Brak flag is_now=True i is_future=True sprawia, że system wygeneruje standardową kartę dzienną
                    _send_card_to_user(user_parsed, is_quiet=False)
                except Exception as e:
                    send_reply(chat_id, t_ui(user_lang, "err_gen"))
                    import traceback
                    traceback.print_exc()
            
            
            
            # 5. /future
            elif message.get("text", "").startswith(("/future", "/trend", "/14dni")):
                print(f"  🔮 [DEBUG] Otrzymano komendę /future od {chat_id}")
                send_reply(chat_id, t_ui(user_lang, "prep_future"))
                try:
                    raw = _load_users_from_sheet()
                    sklejone = wirtualne_scalanie(raw)
                    users = _parse_users(sklejone)
                    user = next((u for u in users if str(u["chat_id"]) == str(chat_id)), None)
                    
                    if user and user.get("lat") and user.get("lon"):
                        sukces = main_card._send_card_to_user(user, is_quiet=False, is_now=False, is_future=True)
                        if not sukces:
                            send_reply(chat_id, "❌ Wystąpił problem wewnętrzny. Karta nie została wysłana.")
                    else:
                        send_reply(chat_id, "❌ Najpierw musisz ustawić lokalizację (wyślij Pinezkę).")
                except Exception as e:
                    import traceback
                    traceback.print_exc() 

            # 6. /info
            elif message.get("text", "").startswith("/info"):
                print(f"  ℹ️ Wysłano instrukcję obsługi do {chat_id}")
                send_reply(chat_id, t_ui(user_lang, "info_msg"))
            
            # 7. /start (Ktoś klika to po raz kolejny)
            elif message.get("text", "").startswith("/start"):
                print(f"  👋 Wysłano powitanie powrotne do {chat_id}")
                
                send_reply(chat_id, t_ui(user_lang, "welcome_back"))
                
            elif message.get("text", "").lower().startswith(("/porady", "/tips")):
                print(f"  💡 Wysłano porady do {chat_id}")
                
                send_reply(chat_id, t_ui(user_lang, "porady_msg", default_rano=DEFAULT_RANO, default_wieczor=DEFAULT_WIECZOR))
                
                          
                
            # --- PRZYGOTOWANIE ZMIENNYCH I WYGASZANIE STARYCH STANÓW (TTL) ---
            text = (message.get("text") or "").strip()
            text_low = text.lower()
            now_ts = time.time()
            chat_id_str = str(chat_id)

            # 0. Automatyczne wygaszenie stanu, jeśli minęło 5 minut
            exp = PENDING_CITY.get(chat_id_str)
            if exp and now_ts > exp:
                PENDING_CITY.pop(chat_id_str, None)
                print(f"  [TTL] Wygasł stan oczekiwania na miasto dla: {chat_id_str}")

            # =====================================================================
            # 8A. Komenda /miasto BEZ argumentu -> Ustawiamy stan w RAM
            # =====================================================================
            if text_low.startswith(("/miasto", "/city", "/loc")):
                text_parts = text.split(" ", 1)
                
                # Użytkownik wpisał samo "/miasto" (lub kliknął opcję z menu, która to wywołała)
                if len(text_parts) < 2 or not text_parts[1].strip():
                    # AKTYWUJEMY STAN W RAM NA 5 MINUT!
                    PENDING_CITY[chat_id_str] = now_ts + PENDING_TTL_SEC
                    print(f"  [STATE MACHINE] Aktywowano oczekiwanie na miasto dla: {chat_id_str}")
                    
                    # Pobieramy obecne miasto do wyświetlenia
                    bezpieczny_lat = str(user_data.get("Lat", 0)).replace(',', '.')
                    bezpieczny_lon = str(user_data.get("Lon", 0)).replace(',', '.')
                    city = get_city_from_coords(bezpieczny_lat, bezpieczny_lon, user_lang)
                    if any(char.isdigit() for char in city):
                        city = "Nieznana miejscowość"
                    
                    instrukcja = t_ui(user_lang, "city_prompt", city=city)
                    nazwa_przycisku = t_ui(user_lang, "btn_update_gps")
                    
                    # Dolna klawiatura GPS z WebApp (tylko w czatach prywatnych!)
                    if int(chat_id) > 0:
                        klawiatura_gps = {
                            "keyboard": [
                                [{"text": nazwa_przycisku, "web_app": {"url": f"https://watifer.github.io/Pogoda-World/webapp/?lang={user_lang}"}}]
                            ],
                            "resize_keyboard": True
                        }
                        send_reply(chat_id, instrukcja, reply_markup=klawiatura_gps)
                    else:
                        # GRUPA (ID ujemne) -> Wysyłamy instrukcję bez przycisku z dynamicznym tłumaczeniem
                        instrukcja_grupa = instrukcja + t_ui(user_lang, "group_city_tip")
                        send_reply(chat_id, instrukcja_grupa)
                        
                    continue
                
                # Użytkownik wpisał od razu "/miasto Warszawa" -> od razu geokodujemy
                city_query = text_parts[1].strip()

            # =====================================================================
            # 8B. Zwykły tekst (np. "Warszawa"), gdy bot CZEKA NA MIASTO w RAM
            # =====================================================================
            elif PENDING_CITY.get(chat_id_str) and text and not text.startswith("/"):
                city_query = text
                # Zdejmujemy stan OD RAZU, żeby użytkownik nie utknął, gdyby wpisał głupotę!
                PENDING_CITY.pop(chat_id_str, None)
                print(f"  [STATE MACHINE] Wykryto wpisanie samego miasta: {city_query}")

            # =====================================================================
            # 8C. Ignorowanie pozostałych wiadomości
            # =====================================================================
            else:
                print(f"  [DEBUG] Wiadomość od {user_data.get('Imię')} ignorowana.")
                continue

            # =====================================================================
            # WSPÓLNA LOGIKA GEOKODOWANIA (Dla /miasto Warszawa ORAZ samego "Warszawa")
            # =====================================================================
            send_reply(chat_id, t_ui(user_lang, "search_loc"))
            
            lat, lon, full_address = get_coords_from_city(city_query, user_lang)
            
            if lat and lon:
                print(f"  📍 Znaleziono po nazwie: {city_query} -> {lat}, {lon}")
                try:
                    # Znalezienie właściwego wiersza w Arkuszu
                    real_row_index = None
                    for idx, r in enumerate(users_records):
                        if str(r.get("Chat ID", "")).strip() == str(chat_id):
                            if str(r.get("Imię", "")).strip() != "":
                                real_row_index = idx + 2  
                                break
                    if not real_row_index:
                        komorka = main_sheet.find(str(chat_id), in_column=2)
                        real_row_index = komorka.row

                    # Czysta aktualizacja współrzędnych i nazwy w Arkuszu (bez żadnych stanów techniczych!)
                    col_lat = headers.index("Lat") + 1
                    col_lon = headers.index("Lon") + 1
                    main_sheet.update_cell(real_row_index, col_lat, lat)
                    main_sheet.update_cell(real_row_index, col_lon, lon)
                    
                    krotka_nazwa = get_city_from_coords(lat, lon, user_lang)
                    if krotka_nazwa in ("Lokalizacja w terenie", "", None, "Nieznana miejscowość"):
                        krotka_nazwa = city_query.capitalize()
                        
                    if "Miasto" in headers:
                        col_miasto = headers.index("Miasto") + 1
                        main_sheet.update_cell(real_row_index, col_miasto, krotka_nazwa)
                        
                    sukces_msg = t_ui(user_lang, "search_success", city=krotka_nazwa, address=full_address, query=city_query)
                    send_reply(chat_id, sukces_msg)
                    
                except Exception as e:
                    send_reply(chat_id, t_ui(user_lang, "search_err"))
                    alert_admin(f"❌ Błąd aktualizacji miasta: {e}")
            else:
                # Jeśli geokodowanie się nie udało, nie przywracamy stanu. User może kliknąć /miasto z menu jeszcze raz.
                send_reply(chat_id, t_ui(user_lang, "search_fail"))

        except Exception as e:
            print(f"❌ Krytyczny błąd podczas przetwarzania wiadomości od {chat_id}: {e}")
            
    save_offset(gc, highest_update_id)
    print("✅ Pamięć bota zaktualizowana. Koniec pracy.")

import time

if __name__ == "__main__":
    print("🚀 Startuje całodobowy nasłuch...")
    while True:
        try:
            main_bot()
        except Exception as e:
            print(f"⚠️ Krytyczny błąd w głównej pętli: {e}")
        time.sleep(2)