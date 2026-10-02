import os
import json
import re
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
from prepare_future_layout import prepare_future_layout_data
from weather_payload import build_payload_for_location
import image_generator
import users_store

load_dotenv()
#Furtka do testowanie bota bez zaproszenia
MASTER_TOKEN = os.environ.get("MASTER_TOKEN", "DEV_TEST")
import time




# =====================================================================
# PAMIĘĆ RAM DLA STANU OCZEKIWANIA NA MIASTO (State Machine z TTL)
# =====================================================================
# PR2 UX cleanup: PENDING_CITY przechowuje KONTEKST oczekiwania, bo wpisanie
# samego "Warszawa" znaczy co innego po /dzien (karta jednorazowa, bez zapisu)
# niż po /miasto (świadomy zapis profilu w Users).
# Format: { str(chat_id): {"ctx": str, "expires_ts": float} }
PENDING_CITY = {}
PENDING_TTL_SEC = 300   # 5 minut (300 sekund) na wpisanie miasta

# Konteksty oczekiwania na nazwę miejscowości
CTX_SAVE_PROFILE = "save_profile"     # po /miasto -> zapis profilu, BEZ karty
CTX_ONEOFF_DAY = "oneoff_day"         # po /dzien  -> jednorazowa karta dzienna
CTX_ONEOFF_NOW = "oneoff_now"         # po /teraz  -> jednorazowa karta 12 h
CTX_ONEOFF_FUTURE = "oneoff_future"   # po /trend  -> jednorazowa karta 14 dni

ONEOFF_PROMPT_KEYS = {
    CTX_ONEOFF_DAY: "oneoff_prompt_day",
    CTX_ONEOFF_NOW: "oneoff_prompt_now",
    CTX_ONEOFF_FUTURE: "oneoff_prompt_future",
}
ONEOFF_CARD_TYPES = {
    CTX_ONEOFF_DAY: "day",
    CTX_ONEOFF_NOW: "now",
    CTX_ONEOFF_FUTURE: "future",
}

# PR2: lokalizacja wykorzystana do jednorazowego raportu NIE jest profilem.
# Przechowujemy ją wyłącznie w RAM przez 10 minut jako zaplecze UKRYTEGO aliasu
# technicznego /save_location (w UX zapis lokalizacji jest tylko przez /miasto).
PENDING_SAVE = {}       # { str(chat_id): {lat, lon, city, lang, source, expires_ts} }
PENDING_SAVE_TTL_SEC = 600

# Potwierdzenie usunięcia danych (/usunDane oraz /delete_me — obie komendy
# najpierw pytają). Bez callbacków: odpowiedź przychodzi przyciskiem klawiatury
# reply ("Tak, chcę" / "Nie, nie chcę") albo ukrytą komendą techniczną.
PENDING_DELETE = {}     # { str(chat_id): expires_ts }
PENDING_DELETE_TTL_SEC = 300

# =====================================================================
# PR2 UX cleanup — ALIASY KOMEND (PL skróty + formy techniczne)
# =====================================================================
# Użytkownik widzi w menu i w komunikatach krótkie, czytelne komendy
# (/dzien, /miasto, /dane, /bezGPS, /usunDane). Parser mapuje je na komendy
# kanoniczne, więc cała istniejąca logika handlerów zostaje bez zmian.
# Klucze ZAWSZE lowercase — dopasowanie jest case-insensitive, czyli /bezGPS,
# /bezgps, /BEZGPS oraz /usunDane, /usundane, /USUNDANE działają tak samo.
COMMAND_ALIASES = {
    "/dzien": "/day",
    "/dzis": "/day",
    "/teraz": "/now",
    "/trend": "/future",
    "/14dni": "/future",
    "/raport": "/menu",
    "/report": "/menu",
    "/miasto": "/city",
    "/loc": "/city",
    "/zapros": "/invite",
    "/dane": "/my_data",
    "/data": "/my_data",
    "/priv": "/privacy",
    "/bezgps": "/forget_location",
    "/usundane": "/delete_confirm",
    # ukryte komendy techniczne potwierdzenia usunięcia danych (niepokazywane)
    "/potwierdzusun": "/delete_yes",
    "/anulujusun": "/delete_no",
}




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


def _norm_lang(raw):
    """Normalizuje kod języka z bazy (Lang/Język/lang) albo None."""
    raw = str(raw or "").strip().lower()
    if raw in ("no", "nb"):
        return "no"
    if raw in ("pl", "en", "de", "fr", "es"):
        return raw
    return None


def _is_guest_trigger(text, bot_username):
    """
    Wykrywa wiadomości, które obsłużyłby tryb gościa (wzmianka @bot lub skrót
    .d/.n/.f/.p). Utrzymujemy to tutaj, żeby guest_bot_handler pozostał bez zmian —
    gate accessu (PR1) robimy PRZED jego wywołaniem.
    """
    if not text or text.startswith("/"):
        return False
    low = text.lower()
    if f"@{bot_username.lower()}" in low:
        return True
    return low.startswith(GUEST_SHORTCUT_PREFIXES)


def _chat_has_access(users_map, clean_users, chat_id) -> bool:
    """
    Bramka dostępu (PR1):
    - Jeśli istnieje wiersz w Users — rozstrzyga jego access_status
      (granted = dostęp; blocked/revoked = brak dostępu, nawet przy wierszu legacy).
    - Jeśli NIE ma wiersza w Users (użytkownik legacy sprzed migracji) — dostęp
      implikuje wiersz w Formularz (BLOCKED_ jest już odfiltrowany przez
      wirtualne_scalanie, więc zablokowani legacy dostępu nie mają).
    """
    u = (users_map or {}).get(users_store.norm_chat_id(chat_id))
    if u:
        return str(u.get("access_status", "")).strip().lower() == users_store.ACCESS_GRANTED
    return any(str(x.get("Chat ID", "")).strip() == str(chat_id) for x in clean_users)


# ==============================================================
# PR2 — RAPORTY JEDNORAZOWE I ŚWIADOMY ZAPIS PROFILU
# ==============================================================
def _is_legacy_only_user(users_map, clean_users, chat_id) -> bool:
    """True tylko dla starego użytkownika z Formularz, bez rekordu w Users.

    To rozróżnienie jest ważne: PR2 nie migruje legacy ani nie zmienia jego
    istniejącego przepływu. Nowy przepływ one-off/PENDING_SAVE dotyczy rekordów
    zarządzanych przez Users.
    """
    cid = users_store.norm_chat_id(chat_id)
    has_users_record = cid in (users_map or {})
    has_legacy_record = any(str(x.get("Chat ID", "")).strip() == str(chat_id) for x in clean_users)
    return has_legacy_record and not has_users_record


def _split_command(text, commands):
    """Zwraca argument komendy albo None, gdy tekst nie jest jedną z komend.

    Obsługuje też telegramowy wariant ``/day@NazwaBota Warszawa`` i nie myli
    ``/daybreak`` z ``/day``.
    """
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return None
    head, _, tail = raw.partition(" ")
    command = head.split("@", 1)[0].lower()
    if command not in commands:
        return None
    return tail.strip()


def _command_head(text):
    """Kanoniczna (lowercase) nazwa komendy z tekstu, bez @NazwaBota, albo ""."""
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return ""
    parts = re.split(r"\s+", raw, maxsplit=1)
    return parts[0].split("@", 1)[0].lower()


def _match_command(text, commands):
    """Zwraca dopasowaną komendę (albo None) — DOKŁADNE dopasowanie nagłówka.

    Odporniejsze niż ``startswith``: ``/my_database`` nie łapie się na
    ``/my_data``, a ``/dzień`` nie udaje ``/dzien``.
    """
    head = _command_head(text)
    return head if head and head in commands else None


def _normalize_command_text(text):
    """Zamienia alias komendy na formę kanoniczną, zachowując argument 1:1.

    Case-insensitive po nazwie komendy (wymóg dla /bezGPS i /usunDane), obsługuje
    wariant ``/dzien@NazwaBota Warszawa`` i nie myli ``/dziennik`` z ``/dzien``.
    Zwraca tekst bez zmian, gdy to nie komenda albo gdy nie ma aliasu — dzięki
    temu nadpisanie ``message["text"]`` wynikiem tej funkcji jest bezpieczne dla
    całej istniejącej logiki handlerów.
    """
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return text
    parts = re.split(r"\s+", raw, maxsplit=1)
    head = parts[0]
    canonical = COMMAND_ALIASES.get(head.split("@", 1)[0].lower())
    if not canonical:
        return text
    if len(parts) < 2 or not parts[1].strip():
        return canonical
    return f"{canonical} {parts[1].strip()}"


def _set_pending_city(chat_id, ctx, now_ts=None):
    """Ustawia kontekst oczekiwania na nazwę miejscowości (TTL 5 minut)."""
    now_ts = time.time() if now_ts is None else float(now_ts)
    entry = {"ctx": str(ctx), "expires_ts": now_ts + PENDING_TTL_SEC}
    PENDING_CITY[str(chat_id)] = entry
    return entry


def _take_pending_city_ctx(chat_id, now_ts=None, consume=True):
    """Zwraca kontekst oczekiwania na miasto albo None (brak/po TTL).

    ``consume=False`` tylko podgląda stan (pinezka GPS i WebApp sprawdzają, czy
    jesteśmy w flow /miasto, zanim zdecydują, czy konsumować wpis).
    """
    key = str(chat_id)
    entry = PENDING_CITY.get(key)
    if not entry:
        return None
    now_ts = time.time() if now_ts is None else float(now_ts)
    if now_ts >= float(entry.get("expires_ts", 0) or 0):
        PENDING_CITY.pop(key, None)
        return None
    if consume:
        PENDING_CITY.pop(key, None)
    return str(entry.get("ctx") or "")


def _prune_expired_pending(now_ts=None):
    """Sprząta wygasłe stany RAM (miasto, pending zapisu, potwierdzenie kasacji)."""
    now_ts = time.time() if now_ts is None else float(now_ts)
    _prune_expired_pending_saves(now_ts)
    for key, entry in list(PENDING_CITY.items()):
        if now_ts >= float((entry or {}).get("expires_ts", 0) or 0):
            PENDING_CITY.pop(key, None)
    for key, exp in list(PENDING_DELETE.items()):
        if now_ts >= float(exp or 0):
            PENDING_DELETE.pop(key, None)


def _ask_oneoff_city(chat_id, lang, ctx):
    """Pyta o SAMĄ nazwę miejscowości i zapamiętuje kontekst jednorazowej karty.

    Zamiast suchego "profil nieaktywny" użytkownik z dostępem, ale bez zapisanej
    lokalizacji dostaje pytanie — wpisane miasto wygeneruje kartę jednorazowo i
    NICZEGO nie zapisze.
    """
    _set_pending_city(chat_id, ctx)
    send_reply(chat_id, t_ui(lang, ONEOFF_PROMPT_KEYS[ctx]))


def _set_pending_delete(chat_id, now_ts=None):
    """Ustawia oczekiwanie na potwierdzenie usunięcia wszystkich danych."""
    now_ts = time.time() if now_ts is None else float(now_ts)
    PENDING_DELETE[str(chat_id)] = now_ts + PENDING_DELETE_TTL_SEC


def _pending_delete_active(chat_id, now_ts=None) -> bool:
    """True, gdy czekamy na Tak/Nie (i TTL jeszcze nie minął)."""
    exp = PENDING_DELETE.get(str(chat_id))
    if not exp:
        return False
    now_ts = time.time() if now_ts is None else float(now_ts)
    if now_ts >= float(exp):
        PENDING_DELETE.pop(str(chat_id), None)
        return False
    return True


def _delete_confirm_labels(lang):
    """Etykiety przycisków reply keyboard (Tak / Nie) dla danego języka."""
    return t_ui(lang, "delete_confirm_yes"), t_ui(lang, "delete_confirm_no")


def _delete_confirm_markup(lang):
    """Klawiatura potwierdzenia — zwykłe przyciski, BEZ callbacków."""
    yes, no = _delete_confirm_labels(lang)
    return {
        "keyboard": [[{"text": yes}, {"text": no}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def _norm_answer(text):
    """Normalizacja odpowiedzi z przycisku (białe znaki + wielkość liter)."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _put_pending_save(chat_id, lat, lon, city, lang, source, now_ts=None):
    """Umieszcza dokładną lokalizację wyłącznie w efemerycznym RAM (10 min)."""
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None
    if source not in users_store.LOCATION_SOURCES:
        return None
    now_ts = time.time() if now_ts is None else float(now_ts)
    pending = {
        "lat": lat,
        "lon": lon,
        "city": str(city or "Twoja okolica").strip() or "Twoja okolica",
        "lang": _norm_lang(lang) or "en",
        "source": source,
        "expires_ts": now_ts + PENDING_SAVE_TTL_SEC,
    }
    PENDING_SAVE[str(chat_id)] = pending
    return pending


def _prune_expired_pending_saves(now_ts=None):
    """Usuwa wygasłe wpisy, żeby efemeryczny RAM nie rósł bez końca."""
    now_ts = time.time() if now_ts is None else float(now_ts)
    expired = [
        cid for cid, pending in PENDING_SAVE.items()
        if now_ts >= float(pending.get("expires_ts", 0) or 0)
    ]
    for cid in expired:
        PENDING_SAVE.pop(cid, None)
    return len(expired)


def _get_pending_save(chat_id, now_ts=None, consume=False):
    """Pobiera bieżący pending lub usuwa go, jeśli upłynął TTL.

    ``consume=True`` usuwa prawidłowy pending (używane przez /oneoff); /save_location
    najpierw odczytuje, zapisuje w Users, a dopiero po sukcesie usuwa wpis.
    """
    key = str(chat_id)
    pending = PENDING_SAVE.get(key)
    if not pending:
        return None
    now_ts = time.time() if now_ts is None else float(now_ts)
    if now_ts >= float(pending.get("expires_ts", 0) or 0):
        PENDING_SAVE.pop(key, None)
        return None
    if consume:
        PENDING_SAVE.pop(key, None)
    return pending


def _saved_users_profile(users_map, chat_id):
    """Zwraca aktywny profil Users do manualnego raportu bez argumentu."""
    user = (users_map or {}).get(users_store.norm_chat_id(chat_id)) or {}
    if str(user.get("profile_status", "")).strip().lower() != "active":
        return None
    try:
        return {
            "lat": float(str(user.get("lat_round", "")).replace(",", ".")),
            "lon": float(str(user.get("lon_round", "")).replace(",", ".")),
            "city": str(user.get("location_label", "")).strip() or "Twoja okolica",
        }
    except (TypeError, ValueError):
        return None


def _oneoff_message(lang, key, **kwargs):
    """Teksty techniczne UKRYTYCH aliasów (/save_location, /oneoff).

    PR2 UX cleanup: te komendy nie istnieją już w menu ani w komunikatach po
    karcie, więc zostały tu wyłącznie komunikaty kompatybilności wstecznej.
    Komendy (gdy się pojawiają) piszemy zwykłym tekstem — nigdy w backtickach,
    żeby były klikalne na telefonie.
    """
    en = (lang or "").lower() != "pl"
    messages = {
        "discarded": (
            "OK, nic nie zapisuję." if not en
            else "OK, I am not saving anything."
        ),
        "no_pending": (
            "ℹ️ Nie ma lokalizacji do zapisania. Swoje miejsce zapiszesz komendą /miasto."
            if not en else
            "ℹ️ There is no location to save. Use /city to save your place."
        ),
        "generation_error": (
            "⚠️ Nie udało się wygenerować raportu jednorazowego. Spróbuj ponownie za chwilę."
            if not en else
            "⚠️ The one-off report could not be generated. Please try again shortly."
        ),
    }
    return messages[key].format(**kwargs)


def _send_oneoff_report(chat_id, lat, lon, city, lang, card_type) -> bool:
    """Generuje kartę bez odczytu lub zapisu Formularz/Users."""
    try:
        payload = build_payload_for_location(
            lat=float(lat),
            lon=float(lon),
            tz_name=_resolve_tz(float(lat), float(lon)),
            location_name=city,
            lang=lang,
        )
        layout = (
            prepare_now_layout_data(payload) if card_type == "now"
            else prepare_future_layout_data(payload) if card_type == "future"
            else prepare_layout_data(payload)
        )
        image_path = image_generator.generate_weather_card(layout)
        if not image_path:
            return False
        safe_city = str(city).replace("<", "").replace(">", "")
        send_photo(chat_id, image_path, caption=f"<b>{safe_city}</b>", parse_mode="HTML")
        return True
    except Exception as e:
        print(f"  ❌ [oneoff] Błąd generowania dla {chat_id}: {e}")
        import traceback
        traceback.print_exc()
        return False


def _used_location_message(lang, city, address=None):
    """Stopka po karcie jednorazowej: TYLKO użyta lokalizacja z geokodera.

    PR2 UX cleanup: po raporcie jednorazowym nie pytamy o zapis i nie pokazujemy
    /save_location ani /oneoff. Użytkownik dostaje wyłącznie informację, czy
    geokoder trafił w zamierzone miejsce.
    """
    address = str(address or "").strip()
    if address:
        return t_ui(lang, "used_location", city=city, address=address)
    return t_ui(lang, "used_location_short", city=city)


def _day_window_blocked_for_tz(chat_id, lang, tz_name) -> bool:
    """Stare ograniczenie czasowe karty dziennej: 05:00-15:59 czasu lokalnego.

    Przy błędzie strefy czasowej NIE blokujemy karty — dokładnie tak, jak robiła
    to dotychczas ścieżka legacy (datetime importowany w czasie wywołania, żeby
    testy mogły zamrozić godzinę).
    """
    try:
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo

        local_now = datetime.now(ZoneInfo(tz_name))
        if local_now.hour < 5 or local_now.hour >= 16:
            send_reply(chat_id, t_ui(lang, "time_limit"))
            return True
    except Exception as e:
        print(f"Błąd sprawdzania czasu: {e}")
    return False


def _day_card_window_blocked(chat_id, lang, lat, lon) -> bool:
    """Wariant okna czasowego dla kont Users (strefa liczona ze współrzędnych).

    Obowiązuje zarówno zapisany profil, jak i raport jednorazowy (/dzien oraz
    /dzien Warszawa) — pkt 5 zakresu PR2 UX cleanup.
    """
    try:
        tz_name = _resolve_tz(float(lat), float(lon))
    except Exception as e:
        print(f"Błąd wyznaczania strefy czasowej: {e}")
        return False
    return _day_window_blocked_for_tz(chat_id, lang, tz_name)


def _run_oneoff_report(chat_id, lat, lon, city, lang, source, card_type, address=None):
    """Raport jednorazowy: karta + informacja o użytej lokalizacji.

    Nic nie zapisuje w Users ani w Formularz. Dokładne współrzędne zostają tylko
    w efemerycznym RAM (PENDING_SAVE) jako zaplecze ukrytego aliasu technicznego.
    """
    pending = _put_pending_save(chat_id, lat, lon, city, lang, source)
    if not pending:
        send_reply(chat_id, _oneoff_message(lang, "generation_error"))
        return False
    if not _send_oneoff_report(chat_id, lat, lon, pending["city"], pending["lang"], card_type):
        PENDING_SAVE.pop(str(chat_id), None)
        send_reply(chat_id, _oneoff_message(lang, "generation_error"))
        return False
    send_reply(chat_id, _used_location_message(pending["lang"], pending["city"], address))
    return True


def _run_saved_profile_report(chat_id, profile, lang, card_type):
    """Raport dla wcześniej zapisanego profilu — bez tworzenia nowego pending."""
    if not _send_oneoff_report(chat_id, profile["lat"], profile["lon"], profile["city"], lang, card_type):
        send_reply(chat_id, _oneoff_message(lang, "generation_error"))
        return False
    return True


def _run_city_oneoff(chat_id, city_query, lang, card_type):
    """Geokoduje nazwę miasta i generuje raport bez żadnego trwałego zapisu."""
    send_reply(chat_id, t_ui(lang, "search_loc"))
    lat, lon, full_address = get_coords_from_city(city_query, lang)
    if lat is None or lon is None:
        send_reply(chat_id, t_ui(lang, "search_fail"))
        return False
    if card_type == "day" and _day_card_window_blocked(chat_id, lang, lat, lon):
        return False
    city = get_city_from_coords(lat, lon, lang)
    if city in ("Lokalizacja w terenie", "", None, "Nieznana miejscowość"):
        city = str(city_query).strip()
    return _run_oneoff_report(chat_id, lat, lon, city, lang, "city", card_type, address=full_address)


def _current_location_label(chat_id, lang, users_map, clean_users):
    """Obecna lokalizacja do wyświetlenia w /miasto (albo None, gdy brak).

    Kolejność: profil Users -> legacy Formularz (etykieta, a dopiero potem
    reverse geokodowanie współrzędnych). Bez lokalizacji = None, dzięki czemu
    /miasto pokazuje "brak" zamiast technicznego "Nieznana miejscowość".
    """
    profile = _saved_users_profile(users_map, chat_id)
    if profile and str(profile.get("city", "")).strip():
        return str(profile["city"]).strip()

    for x in clean_users or []:
        if str(x.get("Chat ID", "")).strip() != str(chat_id):
            continue
        label = str(x.get("Miasto", "")).strip()
        if label:
            return label
        lat = str(x.get("Lat", "")).strip().replace(",", ".")
        lon = str(x.get("Lon", "")).strip().replace(",", ".")
        if lat and lon:
            try:
                city = get_city_from_coords(lat, lon, lang)
            except Exception as e:
                print(f"  ⚠️ [city] Reverse geokodowanie nieudane: {e}")
                return None
            if city and not any(ch.isdigit() for ch in str(city)) and not str(city).startswith("Lokalizacja"):
                return str(city)
        return None
    return None


def _save_profile_from_location(chat_id, users_ws, users_map, lat, lon, city, lang, source, address=None):
    """Świadomy zapis profilu w Users (flow /miasto) — BEZ generowania karty.

    Współrzędne są zaokrąglane przez users_store.set_profile do 3 miejsc po
    przecinku; dokładny punkt GPS nigdy nie trafia do bazy.
    """
    saved_at = users_store.now_iso()
    saved = users_store.set_profile(
        users_ws, chat_id, lat, lon, city, source, lang, PRIVACY_VERSION, saved_at
    )
    if not saved:
        send_reply(chat_id, _oneoff_message(lang, "generation_error"))
        return False

    # Po świadomym zapisie żaden stan oczekiwania nie ma już sensu.
    PENDING_CITY.pop(str(chat_id), None)
    PENDING_SAVE.pop(str(chat_id), None)

    user_entry = (users_map or {}).setdefault(users_store.norm_chat_id(chat_id), {})
    user_entry.update({
        "profile_status": "active",
        "lat_round": str(round(float(lat), 3)),
        "lon_round": str(round(float(lon), 3)),
        "location_label": city,
        "location_source": source,
        "lang": _norm_lang(lang) or "en",
        "location_consent_at": saved_at,
        "location_consent_version": PRIVACY_VERSION,
        "profile_updated_at": saved_at,
    })

    address = str(address or "").strip()
    if address:
        msg = t_ui(lang, "location_saved", city=city, address=address)
    else:
        msg = t_ui(lang, "location_saved_short", city=city)
    send_reply(chat_id, msg, reply_markup={"remove_keyboard": True})
    return True


# ==============================================================
# KOMENDY PRYWATNOŚCI (RODO) — PR1
# Dostępne dla KAŻDEGO (także bez access i bez wiersza w bazie).
# ==============================================================
def _resolve_privacy_lang(message, chat_id, users_map, clean_users):
    """Język dla komend prywatności: Users -> Formularz (legacy) -> Telegram."""
    lang = users_store.get_lang(users_map, chat_id)
    if lang:
        return lang
    for u in clean_users:
        if str(u.get("Chat ID", "")).strip() == str(chat_id):
            lang = _norm_lang(u.get("Lang", u.get("Język", "")))
            if lang:
                return lang
            break
    return get_user_lang(message)


def _handle_privacy(chat_id, cmd, lang, users_ws, main_sheet, users_map, clean_users):
    """Router komend prywatności.

    /privacy (/priv), /my_data (/dane), /forget_location (/bezGPS) oraz
    /delete_me i /delete_confirm (/usunDane) — obie komendy kasacji najpierw
    pytają o potwierdzenie (PR2 UX cleanup, decyzja D2).
    """
    if cmd == "/privacy":
        send_reply(chat_id, t_ui(lang, "privacy_msg", url=PRIVACY_URL, version=PRIVACY_VERSION))

    elif cmd == "/my_data":
        _handle_my_data(chat_id, lang, users_map, clean_users)

    elif cmd == "/forget_location":
        _handle_forget_location(chat_id, lang, users_ws, main_sheet, users_map, clean_users)

    elif cmd in ("/delete_me", "/delete_confirm"):
        _ask_delete_confirmation(chat_id, lang)


def _has_saved_location(chat_id, users_map, clean_users) -> bool:
    """True, gdy użytkownik ma JAKĄKOLWIEK zapisaną lokalizację (Users lub legacy)."""
    u = (users_map or {}).get(users_store.norm_chat_id(chat_id)) or {}
    if str(u.get("profile_status", "")).strip().lower() == "active":
        return True
    if str(u.get("lat_round", "")).strip() or str(u.get("location_label", "")).strip():
        return True
    for x in clean_users or []:
        if str(x.get("Chat ID", "")).strip() != str(chat_id):
            continue
        if (str(x.get("Lat", "")).strip() or str(x.get("Lon", "")).strip()
                or str(x.get("Miasto", "")).strip()):
            return True
    return False


def _has_any_record(chat_id, users_map, clean_users) -> bool:
    """True, gdy czat ma JAKIKOLWIEK rekord (wiersz Users albo legacy Formularz)."""
    if users_store.norm_chat_id(chat_id) in (users_map or {}):
        return True
    return any(str(x.get("Chat ID", "")).strip() == str(chat_id) for x in clean_users or [])


def _my_data_timestamp(lat, lon):
    """"Stan na" w /dane.

    Z zapisaną lokalizacją pokazujemy czas lokalny tej lokalizacji
    (YYYY-MM-DD HH:MM), bez lokalizacji wyłącznie datę — nie zdradzamy wtedy
    żadnej strefy czasowej użytkownika.
    """
    from datetime import datetime, timezone
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    lat = str(lat or "").strip().replace(",", ".")
    lon = str(lon or "").strip().replace(",", ".")
    if lat and lon:
        try:
            tz_name = _resolve_tz(float(lat), float(lon))
            return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M")
        except Exception as e:
            print(f"  ⚠️ [my_data] Czas lokalny niedostępny: {e}")
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _handle_my_data(chat_id, lang, users_map, clean_users):
    """/dane (/my_data) — uproszczony panel danych.

    Bez nicka i bez technicznych szczegółów źródła: ID Telegrama, dostęp,
    zapisana lokalizacja (albo "brak") oraz komendy zarządzające w osobnych
    liniach, żeby były klikalne na telefonie.
    """
    u = (users_map or {}).get(users_store.norm_chat_id(chat_id))
    legacy = next((x for x in clean_users if str(x.get("Chat ID", "")).strip() == str(chat_id)), None)

    if not u and not legacy:
        send_reply(chat_id, t_ui(lang, "no_data"))
        return

    # --- Dostęp ---
    if u:
        status = str(u.get("access_status", "")).strip().lower()
        if status == users_store.ACCESS_GRANTED:
            access_txt = t_ui(lang, "my_access_granted")
        else:
            access_txt = t_ui(lang, "my_access_denied", status=status or "—")
    else:
        access_txt = t_ui(lang, "my_access_legacy")

    # --- Zapisana lokalizacja (Users, a do czasu migracji także legacy Formularz) ---
    label = lat = lon = ""
    if u:
        label = str(u.get("location_label", "")).strip()
        lat = str(u.get("lat_round", "")).strip().replace(",", ".")
        lon = str(u.get("lon_round", "")).strip().replace(",", ".")
    if not (lat and lon) and legacy:
        label = label or str(legacy.get("Miasto", "")).strip()
        lat = str(legacy.get("Lat", "")).strip().replace(",", ".")
        lon = str(legacy.get("Lon", "")).strip().replace(",", ".")

    has_location = bool(lat and lon)
    if has_location:
        location_txt = label or t_ui(lang, "default_city")
        coords_txt = f"{lat}, {lon}"
    else:
        location_txt = t_ui(lang, "my_lbl_location_none")
        coords_txt = t_ui(lang, "my_lbl_coords_none")

    send_reply(chat_id, t_ui(
        lang, "my_data_msg",
        now=_my_data_timestamp(lat, lon),
        chat_id=str(chat_id),
        access=access_txt,
        location=location_txt,
        coords=coords_txt,
    ))


def _handle_forget_location(chat_id, lang, users_ws, main_sheet, users_map=None, clean_users=None):
    """/bezGPS (/forget_location) — czyści TYLKO lokalizację. Access zostaje.

    Komunikat zależy od stanu SPRZED czyszczenia: nie wolno napisać "usunąłem",
    gdy nie było czego usuwać (wymóg UX PR2 cleanup). Samo czyszczenie jest
    idempotentne i wykonuje się zawsze — także po to, żeby nie zostawić
    półpustych pól profilu.
    """
    had_location = _has_saved_location(chat_id, users_map, clean_users)
    has_record = _has_any_record(chat_id, users_map, clean_users)

    # Nie pozwalamy, aby lokalizacja właśnie zapomniana wciąż czekała w RAM.
    PENDING_SAVE.pop(str(chat_id), None)
    PENDING_CITY.pop(str(chat_id), None)
    cleared_users = users_store.clear_profile(users_ws, chat_id, users_store.now_iso())
    cleared_legacy = _clear_legacy_location(main_sheet, chat_id)
    if not had_location and (cleared_users or cleared_legacy):
        print(f"  🧹 [forget_location] {chat_id}: czyszczenie prewencyjne (brak widocznej lokalizacji)")

    if had_location:
        send_reply(chat_id, t_ui(lang, "forget_location_done"))
    elif has_record:
        send_reply(chat_id, t_ui(lang, "forget_location_none"))
    else:
        send_reply(chat_id, t_ui(lang, "no_data"))


def _ask_delete_confirmation(chat_id, lang):
    """/usunDane (i /delete_me) — krok 1: pytanie + przyciski Tak/Nie.

    Niczego jeszcze nie usuwamy. Świadomie bez callbacków (inline keyboard),
    żeby flow działał też na starych klientach i w czatach grupowych.
    """
    _set_pending_delete(chat_id)
    send_reply(chat_id, t_ui(lang, "delete_confirm"), reply_markup=_delete_confirm_markup(lang))


def _handle_delete_answer(chat_id, cmd, text, lang, users_ws, main_sheet) -> bool:
    """Krok 2: odpowiedź na pytanie o usunięcie danych.

    Zwraca True, gdy wiadomość została skonsumowana (przycisk "Tak, chcę" /
    "Nie, nie chcę" albo ukryta komenda /potwierdzusun, /anulujusun). Inny tekst
    nie konsumuje pendingu — użytkownik może odpowiedzieć później (w TTL).
    """
    if cmd in ("/delete_yes", "/delete_no"):
        if not _pending_delete_active(chat_id):
            # Ukryta komenda techniczna bez oczekującego pytania: NIE kasujemy
            # danych bez świeżego potwierdzenia — pytamy ponownie.
            _ask_delete_confirmation(chat_id, lang)
            return True
        confirmed = (cmd == "/delete_yes")
    else:
        if not _pending_delete_active(chat_id):
            return False
        yes, no = _delete_confirm_labels(lang)
        answer = _norm_answer(text)
        if answer == _norm_answer(yes):
            confirmed = True
        elif answer == _norm_answer(no):
            confirmed = False
        else:
            return False

    PENDING_DELETE.pop(str(chat_id), None)
    if confirmed:
        _handle_delete_me(chat_id, lang, users_ws, main_sheet)
    else:
        send_reply(chat_id, t_ui(lang, "delete_cancelled"), reply_markup={"remove_keyboard": True})
    return True


def _handle_delete_me(chat_id, lang, users_ws, main_sheet):
    """HARD DELETE: wiersz w Users + wszystkie wiersze legacy + stany RAM.

    Wywoływane WYŁĄCZNIE po potwierdzeniu ("Tak, chcę" / /potwierdzusun).
    Po usunięciu nie podajemy linku zaproszenia — tylko informację, że powrót
    wymaga ponownego użycia zaproszenia.
    """
    users_deleted = users_store.delete_user_row(users_ws, chat_id)
    legacy_deleted = _delete_legacy_rows(main_sheet, chat_id)

    # Czyszczenie stanów RAM — pending nigdy nie może przetrwać kasacji danych.
    PENDING_CITY.pop(str(chat_id), None)
    PENDING_SAVE.pop(str(chat_id), None)
    PENDING_DELETE.pop(str(chat_id), None)

    remove_keyboard = {"remove_keyboard": True}
    if users_deleted or legacy_deleted:
        send_reply(chat_id, t_ui(lang, "delete_me_done"), reply_markup=remove_keyboard)
    else:
        send_reply(chat_id, t_ui(lang, "no_data"), reply_markup=remove_keyboard)


def _clear_legacy_location(main_sheet, chat_id):
    """
    Czyści kolumny Lat/Lon/Miasto we wszystkich wierszach legacy (Formularz)
    dla danego chat_id — jeden batch update_cells. Zwraca True, gdy coś wyczyszczono.
    """
    try:
        headers = [str(h).strip() for h in main_sheet.row_values(1)]
        col_lat = headers.index("Lat") + 1 if "Lat" in headers else None
        col_lon = headers.index("Lon") + 1 if "Lon" in headers else None
        col_miasto = headers.index("Miasto") + 1 if "Miasto" in headers else None

        col_values = main_sheet.col_values(2)
        cells = []
        for i, val in enumerate(col_values):
            if str(val).strip() == str(chat_id):
                row = i + 1
                if col_lat:
                    cells.append(gspread.Cell(row=row, col=col_lat, value=""))
                if col_lon:
                    cells.append(gspread.Cell(row=row, col=col_lon, value=""))
                if col_miasto:
                    cells.append(gspread.Cell(row=row, col=col_miasto, value=""))

        if cells:
            main_sheet.update_cells(cells)
            rows = len({c.row for c in cells})
            print(f"  🧹 [forget_location] Wyczyszczono lokalizację w {rows} wierszach legacy (Formularz) dla {chat_id}.")
            return True
        return False
    except Exception as e:
        print(f"  ❌ [forget_location] Błąd czyszczenia legacy: {e}")
        alert_admin(f"❌ /forget_location ({chat_id}): błąd czyszczenia Formularz: {e}")
        return False


def _delete_legacy_rows(main_sheet, chat_id):
    """
    Usuwa WSZYSTKIE wiersze użytkownika w Formularz (również z prefixem BLOCKED_)
    przez delete_rows — numery wierszy zbieramy i kasujemy OD KOŃCA, żeby
    indeksy nie rozjechały się w trakcie usuwania. Zwraca liczbę usuniętych wierszy.
    """
    try:
        col_values = main_sheet.col_values(2)
        cid = str(chat_id)
        targets = [
            i + 1 for i, val in enumerate(col_values)
            if str(val).strip() in (cid, f"BLOCKED_{cid}")
        ]
        for row in sorted(targets, reverse=True):
            main_sheet.delete_rows(row)
        if targets:
            print(f"  🗑 [delete_me] Usunięto {len(targets)} wierszy legacy (Formularz) dla {chat_id}.")
        return len(targets)
    except Exception as e:
        print(f"  ❌ [delete_me] Błąd usuwania wierszy legacy: {e}")
        alert_admin(f"❌ /delete_me ({chat_id}): błąd usuwania wierszy Formularz: {e}")
        return 0



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

# ==============================================================
# PR1 (RODO/access migration): polityka prywatności i komendy prywatności
# ==============================================================
PRIVACY_VERSION = "2026-09-v1"
PRIVACY_URL = os.environ.get("PRIVACY_URL", "https://watifer.github.io/Pogoda-World/privacy/")

# Komendy prywatności działające ZAWSZE (także bez access — wymóg RODO)
# Kanoniczne formy (aliasy PL są mapowane wcześniej przez _normalize_command_text):
#   /priv -> /privacy, /dane -> /my_data, /bezGPS -> /forget_location,
#   /usunDane -> /delete_confirm
PRIVACY_COMMANDS = ("/privacy", "/my_data", "/delete_me", "/forget_location", "/delete_confirm")

# Odpowiedzi na pytanie o usunięcie danych — ukryte komendy techniczne
# (przyciski reply keyboard są obsługiwane po tekście, nie po komendzie).
DELETE_ANSWER_COMMANDS = ("/delete_yes", "/delete_no")

# Prefiksy skrótów trybu gościa (muszą być spójne z guest_bot_handler.handle_guest_now)
GUEST_SHORTCUT_PREFIXES = (".n", "?n", ".d", "?d", ".f", "?f", ".p", "?p")
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
        from db_cleanup import is_bot_blocked, mark_user_as_blocked, classify_block_reason
        if is_bot_blocked(resp):
            gc = get_google_client() # Używamy Twojej funkcji do pobrania dostępu do Sheets
            try:
                powod = classify_block_reason(response_data.get("description", ""))
            except Exception:
                powod = "unknown"
            mark_user_as_blocked(gc, chat_id, reason=powod)
            
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

    # --- PR1 (RODO/access): rejestr dostępu z zakładki Users ---
    # Jeden odczyt na całą paczkę update'ów. Przy awarii Users bot degraduje się
    # do trybu legacy (access = obecność wiersza w Formularz).
    users_ws = users_store.get_ws(gc)
    users_map = users_store.load_users_map(users_ws)

    highest_update_id = offset

    for update in updates:
        highest_update_id = update["update_id"] + 1
        
        try:
            message = update.get("message", {})
            chat_id = message.get("chat", {}).get("id")
            
            if not chat_id: 
                continue

            # TTL jest egzekwowany również wtedy, gdy właściciel pending nie
            # wyśle ponownie żadnej komendy (wpisy nadal pozostają tylko w RAM).
            _prune_expired_pending()

            # --- PR2 UX cleanup: ALIASY KOMEND (case-insensitive) ---
            # Nadpisujemy tekst wiadomości formą kanoniczną, żeby cała istniejąca
            # logika handlerów (startswith("/day") itd.) działała bez zmian, a
            # użytkownik mógł klikać krótkie komendy PL z menu:
            # /dzien /teraz /trend /raport /miasto /zapros /dane /priv /bezGPS /usunDane
            raw_command_text = message.get("text")
            if isinstance(raw_command_text, str):
                stripped_text = raw_command_text.strip()
                normalized_text = _normalize_command_text(stripped_text)
                if normalized_text != stripped_text:
                    message["text"] = normalized_text
            # --------------------------------------------------------

            # --- DODAJ TO TUTAJ: Błyskawiczne pobranie języka dla Gościa ---
            raw_guest = message.get("from", {}).get("language_code", "en")[:2].lower()
            guest_lang = "no" if raw_guest in ("no", "nb") else raw_guest
            if guest_lang not in ("pl", "en", "de", "fr", "es", "no"):
                guest_lang = "en"
            # ---------------------------------------------------------------

            # ==============================================================
            # 0A. TRYB GOŚCIA I SZYBKIE SKRÓTY (.n, .d, .f) — GATE PO ACCESS (PR1)
            # ==============================================================
            # Skróty .d/.n/.f i wzmianki @bot generują karty pogodowe, więc od PR1
            # wymagają dostępu (Users granted lub legacy Formularz). Bez access:
            # odmowa + link zaproszenia. guest_bot_handler pozostaje bez zmian.
            if _is_guest_trigger((message.get("text") or "").strip(), BOT_USERNAME) and not _chat_has_access(users_map, clean_users, chat_id):
                send_reply(chat_id, t_ui(guest_lang, "no_access", url=INVITE_URL))
                continue

            is_guest = handle_guest_now(
                message=message,
                bot_username=BOT_USERNAME, 
                get_coords_fn=get_coords_from_city,
                
                build_payload_fn=lambda lat, lon, lang, c_type, city_name: build_payload_for_location(
                    lat=lat,
                    lon=lon,
                    tz_name=_resolve_tz(lat, lon), 
                    location_name=city_name if city_name else "Twoja okolica",
                    lang=lang
                ),
                
                # ZMIANA: Pełne sterowanie ruchem dla 3 typów kart!
                prepare_layout_fn=lambda payload, c_type: (
                    prepare_now_layout_data(payload) if c_type == "now" 
                    else prepare_future_layout_data(payload) if c_type == "future"
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
                if user_lang == "en":
                    # PR1: język może być zapisany w zakładce Users (nowa rejestracja)
                    lang_z_users = users_store.get_lang(users_map, chat_id)
                    if lang_z_users:
                        user_lang = lang_z_users
                # -------------------------------------------------
                
                raw_data = wad.get("data", "")
                print(f"  [DEBUG-WEBAPP] Otrzymano czyste dane z WebApp: {raw_data}")
                
                try:
                    data = json.loads(raw_data)
                    if data.get("type") == "set_location":
                        lat = float(data.get("lat"))
                        lon = float(data.get("lon"))
                        print(f"  📍 Odebrano współrzędne GPS od {chat_id}: {lat}, {lon}")

                        # WebApp także podlega gate accessu. Bez dostępu nie generuje
                        # raportu i nie pozostawia lokalizacji nawet w RAM.
                        if not _chat_has_access(users_map, clean_users, chat_id):
                            send_reply(chat_id, t_ui(user_lang, "no_access", url=INVITE_URL))
                            continue

                        city = get_city_from_coords(lat, lon, user_lang)
                        if city == "Lokalizacja w terenie" or not city:
                            city = "Twoja okolica"

                        # PR2 UX cleanup: konta Users dostają raport jednorazowy,
                        # ALE GPS wysłany w trakcie flow /miasto (ctx=save_profile)
                        # jest świadomym zapisem profilu — bez karty. Nie dotykamy
                        # Formularz. Stary użytkownik bez wiersza Users zostaje na
                        # poprzednim, legacy przepływie — bez migracji i bez zmiany
                        # schedulera.
                        if not _is_legacy_only_user(users_map, clean_users, chat_id):
                            if _take_pending_city_ctx(chat_id, consume=False) == CTX_SAVE_PROFILE:
                                PENDING_CITY.pop(str(chat_id), None)
                                _save_profile_from_location(
                                    chat_id, users_ws, users_map, lat, lon, city,
                                    user_lang, "webapp"
                                )
                            else:
                                _run_oneoff_report(
                                    chat_id, lat, lon, city, user_lang, "webapp", "day"
                                )
                            continue

                        # --- niezmieniony legacy zapis do Formularz ---
                        rows_to_update = []
                        for idx, r in enumerate(users_records):
                            if str(r.get("Chat ID", "")).strip() == str(chat_id):
                                rows_to_update.append(idx + 2)
                        if not rows_to_update:
                            try:
                                rows_to_update.append(main_sheet.find(str(chat_id), in_column=2).row)
                            except Exception:
                                print("  [DEBUG-WEBAPP] Nie znalazłem usera legacy w bazie!")

                        if rows_to_update:
                            col_lat = headers.index("Lat") + 1
                            col_lon = headers.index("Lon") + 1
                            col_miasto = headers.index("Miasto") + 1 if "Miasto" in headers else None
                            for r_idx in rows_to_update:
                                main_sheet.update_cell(r_idx, col_lat, lat)
                                main_sheet.update_cell(r_idx, col_lon, lon)
                                if col_miasto:
                                    main_sheet.update_cell(r_idx, col_miasto, city)

                        ukryj_klawiature = {"remove_keyboard": True}
                        send_reply(chat_id, t_ui(user_lang, "loc_updated", city=city), reply_markup=ukryj_klawiature)

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

                        # PR1: brak wiersza w Formularz = brak profilu (zapis dopiero od PR2)
                        if not rows_to_update:
                            if _chat_has_access(users_map, clean_users, chat_id):
                                send_reply(chat_id, t_ui(user_lang, "no_profile_yet"))
                            else:
                                send_reply(chat_id, t_ui(user_lang, "no_access", url=INVITE_URL))
                            continue

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
            # 0.5 KOMENDY PRYWATNOŚCI (RODO) — PR1 + aliasy PL (PR2 UX cleanup)
            # /privacy (/priv), /my_data (/dane), /forget_location (/bezGPS),
            # /delete_me oraz /delete_confirm (/usunDane) — kasacja zawsze pyta.
            # Działają ZAWSZE: także bez access i bez wiersza w bazie (wymóg RODO).
            # ==============================================================
            text_priv = (message.get("text") or "").strip()
            priv_cmd = _match_command(text_priv, PRIVACY_COMMANDS)
            if priv_cmd:
                priv_lang = _resolve_privacy_lang(message, chat_id, users_map, clean_users)
                print(f"  🛡 Komenda prywatności {priv_cmd} od {chat_id}")
                _handle_privacy(chat_id, priv_cmd, priv_lang, users_ws, main_sheet, users_map, clean_users)
                # Odświeżamy RAM po operacjach zapisu (kasacja / czyszczenie profilu)
                if priv_cmd in ("/delete_me", "/forget_location"):
                    fresh_map = users_store.load_users_map(users_ws)
                    if fresh_map:
                        users_map = fresh_map
                continue

            # ==============================================================
            # 0.6 ODPOWIEDŹ NA PYTANIE O USUNIĘCIE DANYCH (bez callbacków)
            # Przyciski reply keyboard ("Tak, chcę" / "Nie, nie chcę") albo
            # ukryte komendy techniczne /potwierdzusun, /anulujusun.
            # Obsługujemy PRZED bramką access — po kasacji użytkownik nie ma już
            # dostępu, a prawo do usunięcia danych nie może zależeć od accessu.
            # ==============================================================
            delete_cmd = _match_command(text_priv, DELETE_ANSWER_COMMANDS)
            if delete_cmd or _pending_delete_active(chat_id):
                priv_lang = _resolve_privacy_lang(message, chat_id, users_map, clean_users)
                consumed = _handle_delete_answer(
                    chat_id, delete_cmd or "", text_priv, priv_lang, users_ws, main_sheet
                )
                if consumed:
                    print(f"  🗑 Odpowiedź na potwierdzenie usunięcia danych od {chat_id}")
                    fresh_map = users_store.load_users_map(users_ws)
                    if fresh_map:
                        users_map = fresh_map
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
            else:
                # PR1: język może być w zakładce Users (nowa rejestracja), inaczej Telegram
                lang_z_users = users_store.get_lang(users_map, chat_id)
                user_lang = lang_z_users if lang_z_users else get_user_lang(message)


            # ==============================================================
            # BRAMKA WEJŚCIOWA (PR1: dostęp z Users + legacy Formularz)
            # ==============================================================
            # access = Users(access_status=granted) LUB wiersz w legacy Formularz.
            # Bez access dozwolone są tylko: /start <token> (rejestracja), komendy
            # prywatności (obsłużone wyżej) — reszta komend: odmowa + link zaproszenia.
            chat_has_access = _chat_has_access(users_map, clean_users, chat_id)
            has_legacy_row = user_row_index is not None

            if not chat_has_access:
                text = message.get("text", "").strip()
                wykryty_jezyk = get_user_lang(message)

                # PR1: pinezka wysłana jako odpowiedź botowi to świadoma interakcja —
                # dostaje odmowę z linkiem zaproszenia (zwykłe pinezki ignorujemy).
                if "location" in message and message.get("reply_to_message"):
                    send_reply(chat_id, t_ui(wykryty_jezyk, "no_access", url=INVITE_URL))
                    continue

                # Ciche ignorowanie zdarzeń bez tekstu (naklejki, systemowe wiadomości z grup)
                if not text:
                    continue

                parts = text.split()

                # PR1: każda komenda bez access -> odmowa + link zaproszenia.
                # Zwykły tekst ("cześć", spam na grupach) nadal ignorujemy po cichu.
                if not text.startswith("/start"):
                    if text.startswith("/"):
                        send_reply(chat_id, t_ui(wykryty_jezyk, "no_access", url=INVITE_URL))
                    continue

                # Miękkie lądowanie dla ludzi, którzy wpisali samo /start (bez kodu)
                if len(parts) < 2:
                    send_reply(chat_id, t_ui(wykryty_jezyk, "no_access", url=INVITE_URL))
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
                # PR1: polecającym może być też użytkownik z samym access-em w Users
                # (rejestracja od PR1 nie tworzy już wiersza w Formularz).
                is_referral = (
                    any(str(u.get("Chat ID", "")).strip() == str(referrer_id) for u in clean_users)
                    or users_store.has_access_in_map(users_map, referrer_id)
                )

                # Jeśli kod nie pasuje do niczego (nie jest adminem, kodem beta ani poleceniem od usera)
                if not (is_dev_mode or is_public_beta or is_referral):
                    send_reply(chat_id, t_ui(wykryty_jezyk, "invalid_link", url=INVITE_URL))
                    continue

                # 1. Weryfikacja limitu miejsc (Podniesiono z 50 do 200)
                # Dev (God Mode) wchodzi zawsze, reszta jest blokowana po osiągnięciu limitu
                # (liczymy profile legacy — tylko one generują obciążenie schedulera)
                if len(clean_users) >= 200 and not is_dev_mode:
                    send_reply(chat_id, t_ui(wykryty_jezyk, "limit_reached", url=INVITE_URL))
                    continue

                # 2. Mamy autoryzację! PR1: zapisujemy WYŁĄCZNIE minimalny access
                #    w zakładce Users — bez lat/lon, bez profilu i BEZ wiersza
                #    w Formularz (scheduler nadal czyta Formularz, ale nowi
                #    użytkownicy nie dostają profili aż do PR2).
                access_source = "admin" if is_dev_mode else ("public_beta" if is_public_beta else "referral")

                # Wykrycie języka z Telegrama + bezpieczny fallback do angielskiego
                raw_lang = message.get("from", {}).get("language_code", "en")[:2].lower()
                OBSLUGIWANE_JEZYKI = ("pl", "en", "de", "fr", "es", "no", "nb")
                if raw_lang in OBSLUGIWANE_JEZYKI:
                    wykryty_jezyk = "no" if raw_lang in ("no", "nb") else raw_lang
                else:
                    wykryty_jezyk = "en"

                print(f"  [DEBUG] 🌟 Nowy klient z ZAPROSZENIA! Zapisuję access w Users (ID: {chat_id}, źródło: {access_source})")

                try:
                    zapisano = users_store.upsert_access(
                        users_ws, chat_id, wykryty_jezyk, access_source,
                        users_store.now_iso(), PRIVACY_VERSION
                    )
                    if not zapisano:
                        raise RuntimeError("upsert_access nie zapisał wiersza (brak zakładki Users?)")

                    # Odświeżamy mapę w RAM, żeby dalsze wiadomości z tej samej
                    # paczki update'ów widziały nowy dostęp bez ponownego odczytu API.
                    users_map[users_store.norm_chat_id(chat_id)] = {
                        "chat_id": str(chat_id),
                        "access_status": users_store.ACCESS_GRANTED,
                        "access_source": access_source,
                        "access_granted_at": users_store.now_iso(),
                        "privacy_version_seen": PRIVACY_VERSION,
                        "lang": wykryty_jezyk,
                        "profile_status": "none",
                    }

                    # Onboarding PR1: privacy-first, bez klawiatury GPS
                    # (zapis lokalizacji dopiero od PR2 przez /save_location).
                    send_reply(chat_id, t_ui(wykryty_jezyk, "welcome_access"))

                    continue  # Rejestracja zrobiona, pomijamy resztę pętli dla tej wiadomości

                except Exception as e:
                    print(f"  [DEBUG] ❌ Błąd przy zapisie access: {e}")
                    alert_admin(f"❌ Błąd zapisu access (Users) dla {chat_id}: {e}")
                    send_reply(chat_id, t_ui(wykryty_jezyk, "reg_err"))
                    continue

            # ==============================================================
            # AKCJE DLA ZAREJESTROWANYCH UŻYTKOWNIKÓW
            # ==============================================================

            # PR1: użytkownik z access-em, ale bez wiersza w Formularz (rejestracja
            # od PR1 zapisuje tylko access). Syntetyzujemy minimalny user_data,
            # żeby dalsze handlery mogły bezpiecznie działać. Zapis lokalizacji
            # dla takich użytkowników odblokuje dopiero PR2 (/save_location).
            if not has_legacy_row:
                user_data = {
                    "Sygnatura czasowa": "",
                    "Chat ID": str(chat_id),
                    "Imię": (message.get("chat", {}).get("title")
                             or message.get("from", {}).get("first_name") or ""),
                    "Miasto": "", "Lat": "", "Lon": "",
                    "Raport poranny": "", "Aktualizacja": "",
                    "Lang": user_lang,
                }

            # PR2 UX cleanup: /save_location zostaje WYŁĄCZNIE jako ukryty alias
            # techniczny (kompatybilność wsteczna ze starymi klawiaturami oraz
            # wzmianka w /info). Widocznym flow zapisu lokalizacji jest /miasto.
            # /oneoff też jest ukryty: porzuca pending i daje krótką, neutralną
            # odpowiedź — bot nie może milczeć, jeśli ktoś kliknie stary przycisk.
            command_text = (message.get("text") or "").strip()
            if _split_command(command_text, {"/save_location"}) is not None:
                pending = _get_pending_save(chat_id)
                if not pending:
                    send_reply(chat_id, _oneoff_message(user_lang, "no_pending"))
                    continue
                # Przy błędzie Sheets pending zostaje — użytkownik może ponowić
                # zapis bez konieczności ponownego wysyłania GPS.
                _save_profile_from_location(
                    chat_id, users_ws, users_map,
                    pending["lat"], pending["lon"], pending["city"],
                    pending["lang"], pending["source"],
                )
                continue

            if _split_command(command_text, {"/oneoff"}) is not None:
                _get_pending_save(chat_id, consume=True)
                send_reply(
                    chat_id,
                    _oneoff_message(user_lang, "discarded"),
                    reply_markup={"remove_keyboard": True},
                )
                continue

            # 1. PINEZKA
            if "location" in message:
                # D4: w czacie prywatnym, w trakcie flow /miasto (ctx=save_profile),
                # przyjmujemy pinezkę także bez "Odpowiedz" — bot sam o nią prosił.
                pin_ctx = _take_pending_city_ctx(chat_id, consume=False)
                pin_in_city_flow = (pin_ctx == CTX_SAVE_PROFILE and int(chat_id) > 0)
                if not message.get("reply_to_message") and not pin_in_city_flow:
                    print(f"  [DEBUG] Ignoruję pinezkę od {chat_id} - to zwykła rozmowa na czacie.")
                    continue
                lat = message["location"]["latitude"]
                lon = message["location"]["longitude"]
                print(f"  📍 Odebrano współrzędne od [{user_data.get('Imię', chat_id)}]: {lat}, {lon}")

                # PR2 UX cleanup: konto Users nie zapisuje pinezki automatycznie —
                # dostaje raport jednorazowy, CHYBA że pinezka przyszła w flow
                # /miasto (wtedy jest świadomym zapisem profilu, bez karty).
                # Legacy-only zachowuje historyczne działanie Formularz bez migracji.
                if not _is_legacy_only_user(users_map, clean_users, chat_id):
                    city = get_city_from_coords(lat, lon, user_lang)
                    if city == "Lokalizacja w terenie" or not city:
                        city = "Twoja okolica"
                    if pin_in_city_flow:
                        PENDING_CITY.pop(str(chat_id), None)
                        _save_profile_from_location(
                            chat_id, users_ws, users_map, lat, lon, city, user_lang, "gps"
                        )
                    else:
                        _run_oneoff_report(chat_id, lat, lon, city, user_lang, "gps", "day")
                    continue

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

            # 3. /menu (PL alias: /raport, EN alias: /report)
            elif message.get("text", "").startswith("/menu"):
                print(f"  ⚙️ Odebrano żądanie panelu ustawień od [{user_data.get('Imię', chat_id)}]")

                # PR2 UX cleanup: /raport dotyczy WYŁĄCZNIE godzin raportów, a zmiana
                # lokalizacji jest tylko pod /miasto — dlatego nie liczymy tu miasta
                # (odpada jedno zapytanie do geokodera). Konto bez wiersza w Formularz
                # nie ma jeszcze automatycznych raportów: scheduler czyta Formularz
                # i zostanie przełączony na Users dopiero w PR3. Nie obiecujemy więcej.
                if not has_legacy_row:
                    send_reply(chat_id, t_ui(user_lang, "menu_reports_migration"))
                    continue

                # --- WYCIĄGANIE GODZIN ---
                godz_rano = str(user_data.get("Raport poranny", "")).strip()
                godz_wieczor = str(user_data.get("Aktualizacja", "")).strip()
                
                # Jeśli komórki w Google Sheets są puste, importujemy domyślne z main_card!
                if not godz_rano: godz_rano = DEFAULT_RANO
                if not godz_wieczor: godz_wieczor = DEFAULT_WIECZOR
                
                # Formatowanie widoku (wykrywanie opcji "Nie chcę")
                disp_rano = t_ui(user_lang, "disp_off") if "nie" in godz_rano.lower() else f"{godz_rano} ⏰"
                disp_wieczor = t_ui(user_lang, "disp_off") if "nie" in godz_wieczor.lower() else f"{godz_wieczor} ⏰"
                
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

                # --- BUDOWANIE WIADOMOŚCI Z I18N (bez sekcji lokalizacji) ---
                msg = t_ui(user_lang, "menu_header", name=wyswietlana_nazwa, disp_rano=disp_rano, disp_wieczor=disp_wieczor)

                send_reply(chat_id, msg, reply_markup=klawiatura)

            # 4. /now
            elif message.get("text", "").startswith("/now") or message.get("text", "").startswith("/teraz"):
                print(f"  ⚡ Odebrano żądanie radaru taktycznego od [{user_data.get('Imię', chat_id)}]")

                # PR2: rekord Users bez legacy może podać miasto do jednorazowej
                # karty albo użyć wcześniej świadomie zapisanego profilu. Żaden z
                # tych wariantów nie zapisuje Formularz.
                if not _is_legacy_only_user(users_map, clean_users, chat_id):
                    city_query = _split_command(message.get("text", ""), {"/now", "/teraz"})
                    if city_query:
                        _run_city_oneoff(chat_id, city_query, user_lang, "now")
                    else:
                        profile = _saved_users_profile(users_map, chat_id)
                        if profile:
                            _run_saved_profile_report(chat_id, profile, user_lang, "now")
                        else:
                            # PR2 UX cleanup: zamiast "profil nieaktywny" pytamy o
                            # miejscowość; wpisane miasto da kartę jednorazową.
                            _ask_oneoff_city(chat_id, user_lang, CTX_ONEOFF_NOW)
                    continue

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

                if not _is_legacy_only_user(users_map, clean_users, chat_id):
                    city_query = _split_command(message.get("text", ""), {"/day", "/dzis", "/dzien"})
                    if city_query:
                        # /dzien Warszawa — okno 05:00-15:59 sprawdza _run_city_oneoff
                        _run_city_oneoff(chat_id, city_query, user_lang, "day")
                    else:
                        profile = _saved_users_profile(users_map, chat_id)
                        if profile:
                            # Stare ograniczenie czasowe karty dziennej obowiązuje
                            # również zapisany profil (pkt 5 zakresu PR2 UX cleanup).
                            if _day_card_window_blocked(chat_id, user_lang, profile["lat"], profile["lon"]):
                                continue
                            _run_saved_profile_report(chat_id, profile, user_lang, "day")
                        else:
                            _ask_oneoff_city(chat_id, user_lang, CTX_ONEOFF_DAY)
                    continue

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
                # Zachowanie bez zmian (ścieżka legacy), tylko współdzielone z
                # nowymi ścieżkami kont Users przez _day_window_blocked_for_tz.
                user_tz = user_parsed.get("tz", "UTC")
                if _day_window_blocked_for_tz(chat_id, user_lang, user_tz):
                    continue
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

                if not _is_legacy_only_user(users_map, clean_users, chat_id):
                    city_query = _split_command(message.get("text", ""), {"/future", "/trend", "/14dni"})
                    if city_query:
                        _run_city_oneoff(chat_id, city_query, user_lang, "future")
                    else:
                        profile = _saved_users_profile(users_map, chat_id)
                        if profile:
                            _run_saved_profile_report(chat_id, profile, user_lang, "future")
                        else:
                            _ask_oneoff_city(chat_id, user_lang, CTX_ONEOFF_FUTURE)
                    continue

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
                
                          
                
            # --- PRZYGOTOWANIE ZMIENNYCH (TTL stanów RAM sprząta _prune_expired_pending) ---
            text = (message.get("text") or "").strip()
            text_low = text.lower()
            chat_id_str = str(chat_id)
            # Kontekst wpisywanej miejscowości: /miasto => świadomy zapis profilu,
            # /dzien|/teraz|/trend => karta jednorazowa. Ustawiany w 8A/8B, czytany
            # we wspólnej logice geokodowania poniżej.
            pending_ctx = None

            # =====================================================================
            # 8A. Komenda /miasto -> GŁÓWNY, ŚWIADOMY FLOW ZAPISU LOKALIZACJI
            # =====================================================================
            if text_low.startswith(("/miasto", "/city", "/loc")):
                text_parts = text.split(" ", 1)
                pending_ctx = CTX_SAVE_PROFILE

                # Użytkownik wpisał samo "/miasto" (lub kliknął opcję z menu Telegrama)
                if len(text_parts) < 2 or not text_parts[1].strip():
                    # AKTYWUJEMY STAN W RAM NA 5 MINUT (kontekst: zapis profilu)
                    _set_pending_city(chat_id_str, CTX_SAVE_PROFILE)
                    print(f"  [STATE MACHINE] Aktywowano oczekiwanie na miasto (save_profile) dla: {chat_id_str}")

                    # Obecna lokalizacja: profil Users -> legacy Formularz -> "brak"
                    current_city = _current_location_label(chat_id, user_lang, users_map, clean_users)
                    prompt_key = "city_prompt_active" if current_city else "city_prompt"
                    instrukcja = t_ui(user_lang, prompt_key, city=current_city or "")
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
                # i zapisujemy profil (BEZ generowania karty).
                _take_pending_city_ctx(chat_id_str, consume=True)
                city_query = text_parts[1].strip()

            # =====================================================================
            # 8B. Zwykły tekst (np. "Warszawa"), gdy bot CZEKA NA MIASTO w RAM
            # =====================================================================
            elif PENDING_CITY.get(chat_id_str) and text and not text.startswith("/"):
                city_query = text
                # Zdejmujemy stan OD RAZU, żeby użytkownik nie utknął, gdyby wpisał głupotę!
                # Kontekst (oneoff_day / oneoff_now / oneoff_future / save_profile)
                # decyduje poniżej, czy to karta jednorazowa, czy zapis profilu.
                pending_ctx = _take_pending_city_ctx(chat_id_str, consume=True)
                print(f"  [STATE MACHINE] Wykryto wpisanie samego miasta: {city_query} (kontekst: {pending_ctx})")

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
            
            if lat is not None and lon is not None:
                print(f"  📍 Znaleziono po nazwie: {city_query} -> {lat}, {lon}")

                # PR2 UX cleanup: KONTEKST decyduje, co robimy z wpisaną miejscowością.
                #   oneoff_*      -> karta jednorazowa + stopka "użyta lokalizacja"
                #   save_profile  -> świadomy zapis profilu w Users, BEZ karty
                # Legacy-only (brak wiersza w Users) zachowuje dotychczasowy zapis
                # do Formularz — bez migracji i bez zmiany schedulera.
                if not _is_legacy_only_user(users_map, clean_users, chat_id):
                    krotka_nazwa = get_city_from_coords(lat, lon, user_lang)
                    if krotka_nazwa in ("Lokalizacja w terenie", "", None, "Nieznana miejscowość"):
                        krotka_nazwa = city_query.capitalize()

                    oneoff_card_type = ONEOFF_CARD_TYPES.get(pending_ctx or "")
                    if oneoff_card_type:
                        if oneoff_card_type == "day" and _day_card_window_blocked(chat_id, user_lang, lat, lon):
                            continue
                        _run_oneoff_report(
                            chat_id, lat, lon, krotka_nazwa, user_lang, "city",
                            oneoff_card_type, address=full_address,
                        )
                        continue

                    _save_profile_from_location(
                        chat_id, users_ws, users_map, lat, lon, krotka_nazwa,
                        user_lang, "city", address=full_address,
                    )
                    continue

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