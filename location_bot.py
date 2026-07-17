import os
import json
import requests
import gspread
import main_card
from i18n import t_ui
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from main_card import _parse_users, _send_card_to_user, wirtualne_scalanie, _load_users_from_sheet, DEFAULT_RANO, DEFAULT_WIECZOR
from geopy.geocoders import Nominatim
load_dotenv()
#Furtka do testowanie bota bez zaproszenia
MASTER_TOKEN = os.environ.get("MASTER_TOKEN", "DEV_TEST")


# ==============================================================
# WŁASNE FUNKCJE POMOCNICZE (Zamiast importu z main)
# ==============================================================
def get_city_from_coords(lat, lon):
    try:
        geolocator = Nominatim(user_agent="pogoda_world_bot")
        location = geolocator.reverse(f"{lat}, {lon}", language="pl")
        
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
        
        # --- SMART AUTO-SPRZĄTACZKA ---
        from db_cleanup import is_bot_blocked, mark_user_as_blocked
        if is_bot_blocked(resp):
            gc = get_google_client() # Używamy Twojej funkcji do pobrania dostępu do Sheets
            mark_user_as_blocked(gc, chat_id)
            
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Błąd sieci podczas wysyłania wiadomości (Timeout/DNS): {e}")

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
                
            print(f"  [DEBUG] 🔎 Telegram zgłasza wiadomość od Chat ID: {chat_id}")
            
            user_row_index = None
            user_data = None
            for i, u in enumerate(clean_users):
                u_id_z_bazy = str(u.get("Chat ID", "")).strip()
                if u_id_z_bazy == str(chat_id):
                    user_row_index = i + 2
                    user_data = u
                    break 
            # --- BEZPIECZNE POBIERANIE JĘZYKA Z BAZY ---
            user_lang = "pl" # Domyślnie polski
            if user_data:
                raw_l = str(user_data.get("Lang", user_data.get("Język", ""))).strip().lower()
                if raw_l in ("pl", "en"):
                    user_lang = raw_l
                    
            
            # ==============================================================
            # BRAMKA WEJŚCIOWA (Tylko Zaproszenia)
            # ==============================================================
            if not user_row_index:
                text = message.get("text", "").strip()
                
                # --- NOWE: Ciche ignorowanie zdarzeń bez tekstu ---
                # Jeśli to powiadomienie o dodaniu do grupy, zdjęcie lub naklejka - ignoruj.
                if not text:
                    continue
                
                parts = text.split()
                
                # Odrzucamy wszystkie przypadkowe wiadomości od obcych ludzi bez linku
                if not (text.startswith("/start") and len(parts) >= 2):
                    send_reply(chat_id, t_ui("pl", "no_access"))
                    continue
                    
                # ROZSZYFROWANIE TOKENA
                token = parts[1]
                import base64
                try:
                    padding = 4 - (len(token) % 4)
                    referrer_id = base64.urlsafe_b64decode(token + "=" * padding).decode()
                except Exception:
                    referrer_id = token  # Fallback, gdyby ktoś użył starego linku z jawnym ID
                
                # --- FURTKA DEWELOPERSKA (GOD MODE) ---
                is_dev_mode = (token == MASTER_TOKEN)

                # 1. Weryfikacja limitu miejsc (50 osób) - Dev może wejść nawet gdy brakuje miejsc
                if len(clean_users) >= 50 and not is_dev_mode:
                    send_reply(chat_id, t_ui("pl", "limit_reached"))
                    continue
                    
                # 2. Weryfikacja zaproszenia
                if is_dev_mode:
                    referrer_exists = True  # Omijamy sprawdzanie bazy!
                else:
                    referrer_exists = any(str(u.get("Chat ID", "")).strip() == str(referrer_id) for u in clean_users)
                    
                if not referrer_exists:
                    send_reply(chat_id, t_ui("pl", "invalid_link"))
                    continue
                    
                # 3. Mamy autoryzację! Przystępujemy do rejestracji:
                chat_title = message.get("chat", {}).get("title")
                first_name = message.get("from", {}).get("first_name", "Nieznany")
                nowa_nazwa = chat_title if chat_title else first_name
                #   Wykrycie języka w Telegramie przy rejestracji
                raw_lang = message.get("from", {}).get("language_code", "pl")[:2].lower()
                wykryty_jezyk = "en" if raw_lang == "en" else "pl"

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
                    
                        
                    send_reply(chat_id, t_ui(wykryty_jezyk, "welcome_new"))
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
                    
                    city = get_city_from_coords(lat, lon)
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
            elif message.get("text", "").startswith("/zapros"):
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
                city = get_city_from_coords(bezpieczny_lat, bezpieczny_lon)
                
                if any(char.isdigit() for char in city):
                    city = "Nieznana miejscowość (wyślij pinezkę ponownie)"
                
                chat_title = message.get("chat", {}).get("title")
                imie_z_arkusza = str(user_data.get("Imię", "")).strip()
                
                wyswietlana_nazwa = chat_title if chat_title else imie_z_arkusza
                if not wyswietlana_nazwa:
                    wyswietlana_nazwa = "Użytkownik"
                
                # Bezpieczne budowanie linku (tylko jeśli dane są w .env)
                klawiatura = None
                if FORM_BASE and ENTRY_ID:
                    link = f"{FORM_BASE}?usp=pp_url&{ENTRY_ID}={chat_id}"
                    klawiatura = {
                        "inline_keyboard": [
                            [{"text": t_ui(user_lang, "btn_change_hours"), "url": link}]
                        ]
                    }

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
                
            elif message.get("text", "").startswith("/porady"):
                print(f"  💡 Wysłano porady do {chat_id}")
                
                send_reply(chat_id, t_ui(user_lang, "porady_msg", default_rano=DEFAULT_RANO, default_wieczor=DEFAULT_WIECZOR))
                
            else:
                print(f"  [DEBUG] Wiadomość od {user_data.get('Imię')} ignorowana.")

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