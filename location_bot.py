import os
import unicodedata
import json
import math
import re
import tempfile
import requests
import gspread
from gspread.utils import numericise_all
import main_card
from i18n import (
    t_ui,
    t_geocode,
    report_word,
    report_words,
    REPORT_WORDS,
    GEOCODE_OK,
    GEOCODE_TOO_SHORT,
    GEOCODE_NO_MATCH,
    GEOCODE_UNCERTAIN,
    GEOCODE_NOT_FOUND,
    GEOCODE_ERROR,
)
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from main_card import _parse_users, _send_card_to_user, wirtualne_scalanie, DEFAULT_RANO, DEFAULT_WIECZOR, _resolve_tz
from geopy.geocoders import Nominatim
from guest_bot_handler import handle_guest_now, resolve_shortcut, iter_shortcut_keys
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
# PR3 GRUPY — KONWERSACYJNA ZMIANA GODZIN RAPORTÓW (/raport, /report)
# =====================================================================
# W grupach Telegram nie pozwala na reply-keyboard z web_app, a prywatny
# WebApp został odcięty od zapisu godzin — dlatego panel godzin jest teraz
# zwykłym dialogiem tekstowym: /raport pokazuje aktualne godziny, pyta o ich
# zachowanie, a potem zbiera dwie odpowiedzi („brak” wyłącza slot).
# Stan żyje WYŁĄCZNIE w RAM (jak PENDING_CITY): wygasa po TTL, a po restarcie
# procesu znika. Nic nie trafia do arkusza ani do nowej bazy.
# Format: { str(chat_id): {"user_id": str, "stage": str, "morning": str|None,
#                          "afternoon": str|None, "expires_ts": float} }
PENDING_REPORT = {}
PENDING_REPORT_TTL_SEC = 600   # 10 minut na przejście całego dialogu

# Etapy dialogu
STAGE_REPORT_CONFIRM = "confirm"      # czy zachować obecne godziny?
STAGE_REPORT_MORNING = "morning"      # godzina raportu porannego
STAGE_REPORT_AFTERNOON = "afternoon"  # godzina raportu popołudniowego

# /menu to nazwa kanoniczna panelu; /raport (PL) i /report (EN) to aliasy
# widoczne w menu Telegrama. Wszystkie trzy uruchamiają ten sam dialog.
REPORT_COMMAND_HEADS = {"/menu", "/raport", "/report"}

# Dozwolone okna godzin (włącznie z krańcami). ranges są jedynym źródłem
# prawdy — komunikaty pytań i walidacja czytają te same wartości.
REPORT_MORNING_WINDOW = ("05:00", "10:00")
REPORT_AFTERNOON_WINDOW = ("13:00", "16:00")
REPORT_TIME_EXAMPLES = {
    STAGE_REPORT_MORNING: "08:26",
    STAGE_REPORT_AFTERNOON: "14:00",
}

# Marker wyłączonego slotu — dokładnie ta wartość, którą czyta scheduler
# (main_card._parse_scheduler_report_time) i zapisuje users_store.
REPORT_OFF_MARKER = "brak"

# Statusy członka czatu uprawniające do zmiany ustawień grupy.
REPORT_ADMIN_STATUSES = ("creator", "administrator")

# POPRAWKA #7: po skutecznym hard delete idą DOKŁADNIE dwa komunikaty —
# delete_me_done, a po tej pauzie osobny, pełny no_access (dostęp został
# odebrany razem z danymi). Wartość w sekundach trzymamy w stałej, żeby
# testy mogły podmienić czas i nie czekać realnie.
DELETE_NOTICE_DELAY_SEC = 1

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
    user_lang = str((message.get("from", {}) or {}).get("language_code") or "en")[:2].lower()
    
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


def _legacy_user_row(clean_users, chat_id):
    """Wiersz legacy z zakładki Formularz dla chat_id (albo None)."""
    for u in clean_users or ():
        if str(u.get("Chat ID", "")).strip() == str(chat_id):
            return u
    return None


def _effective_lang(message, chat_id, users_map=None, legacy_row=None):
    """JEDNO źródło języka tekstów i etykiet (komendy, prompty, guest, WebApp).

    Priorytet: 1) Users.lang tego chat_id (także grupy — własny rekord grupy,
    nigdy język prywatnego użytkownika); 2) legacy wiersz Formularz (tylko gdy
    brak rekordu Users); 3) Telegram ``message.from.language_code``; 4) en.

    Users.lang wygrywa nawet wtedy, gdy różni się od języka Telegrama. Język
    interfejsu NIE służy do wnioskowania kraju — to wyłącznie wybór tekstów.
    """
    lang = users_store.get_lang(users_map, chat_id)
    if lang:
        return lang
    if legacy_row:
        lang = _norm_lang(legacy_row.get("Lang", legacy_row.get("Język", "")))
        if lang:
            return lang
    return get_user_lang(message)


def _is_guest_trigger(text, bot_username):
    """Wykrywa wiadomości, które obsłużyłby tryb gościa (wzmianka @bot lub skrót).

    POPRAWKA #6: rozpoznawanie skrótów idzie przez trzywarstwowy parser
    (`guest_bot_handler.resolve_shortcut`) — dzięki temu bramka dostępu (PR1)
    obejmuje też nowe ?12 / ?14 / ?t / ?j, a nie tylko stare .d/.n/.f/.p.
    """
    if not text or text.startswith("/"):
        return False
    low = text.lower()
    if f"@{bot_username.lower()}" in low:
        return True
    prefix, _card_type, _query = resolve_shortcut(text)
    return prefix is not None


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
    """Sprząta wygasłe stany RAM (miasto, pending zapisu, kasacja, godziny raportów)."""
    now_ts = time.time() if now_ts is None else float(now_ts)
    _prune_expired_pending_saves(now_ts)
    for key, entry in list(PENDING_CITY.items()):
        if now_ts >= float((entry or {}).get("expires_ts", 0) or 0):
            PENDING_CITY.pop(key, None)
    for key, exp in list(PENDING_DELETE.items()):
        if now_ts >= float(exp or 0):
            PENDING_DELETE.pop(key, None)
    for key, entry in list(PENDING_REPORT.items()):
        if now_ts >= float((entry or {}).get("expires_ts", 0) or 0):
            PENDING_REPORT.pop(key, None)


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


# Anulowanie dialogu godzin raportów (/raport anuluj). Słowo rozpoznajemy we
# wszystkich obsługiwanych językach — autor zmiany w grupie nie musi pisać
# w języku czatu, a polecenie i tak niczego nie zapisuje.
REPORT_CANCEL_ANSWERS = frozenset(
    _norm_answer(report_word(code, "cancel")) for code in REPORT_WORDS
) | {"cancel", "anuluj"}


def _delete_answer_is_confirmation(chat_id, delete_cmd, text, lang) -> bool:
    """True, gdy odpowiedź na pytanie o kasację to potwierdzenie ("Tak, chcę").

    Lustrzana, minimalna kopia rozstrzygnięcia z ``_handle_delete_answer``.
    POPRAWKA #7: main_bot potrzebuje tej informacji PRZED wywołaniem handlera,
    żeby po hard delete odfiltrować czat z RAM na resztę paczki (Z7), a przy
    "Nie, nie chcę" nie filtrować niczego. Wymaga aktywnego pendingu.
    """
    if not _pending_delete_active(chat_id):
        return False
    if delete_cmd:
        return delete_cmd == "/delete_yes"
    yes_label, _no_label = _delete_confirm_labels(lang)
    return _norm_answer(text) == _norm_answer(yes_label)


# =====================================================================
# PR3 GRUPY — DIALOG GODZIN RAPORTÓW (/raport, /report)
# =====================================================================
# Prosty pending state (confirm -> morning -> afternoon), dokładnie w tym
# samym stylu co PENDING_CITY: słownik w RAM, TTL, brak ogólnego frameworka
# stanów. Zapis do Users następuje DOPIERO po ostatniej odpowiedzi — nigdy
# w środku dialogu i nigdy na odpowiedź innej osoby niż jego autor.
def _set_pending_report(chat_id, user_id, stage, morning=None, afternoon=None,
                        now_ts=None):
    """Zakłada (albo nadpisuje) dialog godzin raportów dla czatu."""
    now_ts = time.time() if now_ts is None else float(now_ts)
    entry = {
        "user_id": "" if user_id is None else str(user_id),
        "stage": str(stage),
        "morning": morning,
        "afternoon": afternoon,
        "expires_ts": now_ts + PENDING_REPORT_TTL_SEC,
    }
    PENDING_REPORT[str(chat_id)] = entry
    return entry


def _get_pending_report(chat_id, now_ts=None):
    """Zwraca aktywny dialog albo None (brak wpisu / po TTL)."""
    key = str(chat_id)
    entry = PENDING_REPORT.get(key)
    if not entry:
        return None
    now_ts = time.time() if now_ts is None else float(now_ts)
    if now_ts >= float(entry.get("expires_ts", 0) or 0):
        PENDING_REPORT.pop(key, None)
        return None
    return entry


def _clear_pending_report(chat_id):
    """Kończy dialog bez dotykania danych w Users."""
    PENDING_REPORT.pop(str(chat_id), None)


_REPORT_TIME_RE = re.compile(r"^(\d{1,2})\s*:\s*(\d{2})$")


def _parse_report_time(raw, window):
    """Zwraca HH:MM dla poprawnej godziny z okna ``window`` albo None.

    Akceptuje zarówno ``08:26``, jak i ``8:26`` (normalizacja do ``08:26``).
    Okno jest domknięte: 05:00 i 10:00 przechodzą dla poranka, 04:59 i 10:01
    już nie. Wartość wyłączająca („brak”) NIE jest tutaj rozpoznawana — pustej
    odpowiedzi też nie traktujemy jako wyłączenia.
    """
    text = str(raw or "").strip()
    match = _REPORT_TIME_RE.match(text)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not 0 <= minute <= 59:
        return None
    lo_h, lo_m = (int(part) for part in window[0].split(":"))
    hi_h, hi_m = (int(part) for part in window[1].split(":"))
    if (hour, minute) < (lo_h, lo_m) or (hour, minute) > (hi_h, hi_m):
        return None
    return f"{hour:02d}:{minute:02d}"


def _report_slot_display(value, lang):
    """Godzina do pokazania: wartość, zlokalizowane „brak” albo „—” (puste)."""
    raw = str(value or "").strip()
    if raw.lower() == REPORT_OFF_MARKER:
        return report_word(lang, "off")
    if not raw:
        return "—"
    return raw


def _report_saved_message(lang, morning, afternoon):
    """Podsumowanie po zapisaniu obu slotów (albo komunikat o wyłączeniu obu)."""
    if morning == REPORT_OFF_MARKER and afternoon == REPORT_OFF_MARKER:
        return t_ui(lang, "report_both_off")

    off_label = t_ui(lang, "report_slot_off")
    lines = [
        t_ui(lang, "report_line_morning",
             value=morning if morning != REPORT_OFF_MARKER else off_label),
        t_ui(lang, "report_line_afternoon",
             value=afternoon if afternoon != REPORT_OFF_MARKER else off_label),
    ]
    next_lines = []
    if morning != REPORT_OFF_MARKER:
        next_lines.append(t_ui(lang, "report_next_morning", time=morning))
    if afternoon != REPORT_OFF_MARKER:
        next_lines.append(t_ui(lang, "report_next_afternoon", time=afternoon))
    return t_ui(lang, "report_saved", lines="\n".join(lines),
                next="\n".join(next_lines))


def _telegram_chat_member_status(chat_id, user_id):
    """Status członka czatu z Telegram API; None, gdy sprawdzenie się nie udało.

    Używane wyłącznie przez dialog /raport w grupach (pytanie o administratora).
    Błąd sieci, odpowiedź ``ok: false`` albo brak pola status = None, czyli
    „nie wiemy” — wywołujący traktuje to jako brak zgody (fail-closed).
    """
    try:
        resp = requests.get(
            f"{BASE_URL}/getChatMember",
            params={"chat_id": chat_id, "user_id": user_id},
            timeout=10,
        )
        data = resp.json() or {}
    except Exception as e:
        print(f"  ⚠️ [raport] getChatMember({chat_id}, {user_id}) nie powiodło się: {e}")
        return None
    if not data.get("ok"):
        print(f"  ⚠️ [raport] getChatMember({chat_id}, {user_id}) odrzucone: {data.get('description')}")
        return None
    return str((data.get("result") or {}).get("status") or "").strip().lower()


def _report_change_allowed(chat_id, user_id):
    """Czy ten użytkownik może zmieniać godziny raportów tego czatu?

    Zwraca True/False, a None, gdy NIE udało się sprawdzić uprawnień — None
    nigdy nie oznacza zgody (fail-closed). W czacie prywatnym użytkownik
    zmienia wyłącznie własne ustawienia, więc kontrola nie jest potrzebna;
    w grupie wymagamy statusu administrator albo creator.
    """
    try:
        is_private = int(chat_id) > 0
    except (TypeError, ValueError):
        return None
    if is_private:
        return True
    if user_id is None:
        return None
    status = _telegram_chat_member_status(chat_id, user_id)
    if status is None:
        return None
    return status in REPORT_ADMIN_STATUSES


def _start_report_dialog(chat_id, user_id, lang, users_map):
    """Pierwszy ekran /raport: aktualne godziny + pytanie o ich zachowanie.

    Zwraca True (komenda została obsłużona). Dialog nie startuje — i nic nie
    zapisuje — gdy użytkownik nie ma uprawnień, gdy nie udało się ich sprawdzić
    albo gdy czat nie ma jeszcze rekordu w Users (nie tworzymy go tutaj).
    """
    allowed = _report_change_allowed(chat_id, user_id)
    if allowed is None:
        send_reply(chat_id, t_ui(lang, "report_perm_error"))
        return True
    if not allowed:
        send_reply(chat_id, t_ui(lang, "report_no_perm"))
        return True

    settings = users_store.get_report_settings(users_map, chat_id)
    if settings is None:
        # Brak aktywnego rekordu Users: grupa wymaga wcześniejszej konfiguracji.
        send_reply(chat_id, t_ui(lang, "menu_reports_migration"))
        return True

    _set_pending_report(chat_id, user_id, STAGE_REPORT_CONFIRM)
    send_reply(chat_id, t_ui(
        lang, "report_dialog",
        morning=_report_slot_display(settings["report_morning_time"], lang),
        afternoon=_report_slot_display(settings["report_afternoon_time"], lang),
        yes=report_word(lang, "yes"),
        no=report_word(lang, "no"),
    ))
    return True


def _save_report_settings(chat_id, lang, users_ws, users_map, pending):
    """Zapis obu slotów naraz + podsumowanie. Wywoływane na końcu dialogu."""
    morning = pending.get("morning") or REPORT_OFF_MARKER
    afternoon = pending.get("afternoon") or REPORT_OFF_MARKER

    saved = _sheets_call(
        "sheets.users.set_report_settings",
        users_store.set_report_settings,
        users_ws, chat_id, morning, afternoon,
    )
    _clear_pending_report(chat_id)
    if not saved:
        send_reply(chat_id, t_ui(lang, "report_save_err"))
        return

    # Odświeżamy mapę bieżącej paczki bez ponownego odczytu arkusza
    # (ta sama semantyka co w ścieżce WebApp).
    record = users_map.get(users_store.norm_chat_id(chat_id))
    if record is not None:
        record["report_morning_time"] = morning
        record["report_afternoon_time"] = afternoon
        _cache_users_map(users_map)

    send_reply(chat_id, _report_saved_message(lang, morning, afternoon))


def _handle_report_slot_answer(chat_id, lang, users_ws, users_map, pending,
                               stage, answer):
    """Obsługuje odpowiedź na pytanie o godzinę (poranną albo popołudniową)."""
    window = (REPORT_MORNING_WINDOW if stage == STAGE_REPORT_MORNING
              else REPORT_AFTERNOON_WINDOW)
    ask_key = ("report_ask_morning" if stage == STAGE_REPORT_MORNING
               else "report_ask_afternoon")
    slot = ("morning" if stage == STAGE_REPORT_MORNING else "afternoon")

    if _norm_answer(answer) in report_words(lang, "off"):
        pending[slot] = REPORT_OFF_MARKER
    else:
        parsed = _parse_report_time(answer, window)
        if parsed is None:
            # Błędna wartość: nie przechodzimy do następnego etapu, niczego nie
            # zapisujemy i POWTARZAMY właściwe pytanie razem z przykładem.
            send_reply(chat_id, "\n\n".join((
                t_ui(lang, "report_bad_time",
                     lo=window[0], hi=window[1],
                     example=REPORT_TIME_EXAMPLES.get(stage, window[0]),
                     off=report_word(lang, "off")),
                t_ui(lang, ask_key,
                     lo=window[0], hi=window[1], off=report_word(lang, "off")),
            )))
            return True
        pending[slot] = parsed

    if stage == STAGE_REPORT_MORNING:
        pending["stage"] = STAGE_REPORT_AFTERNOON
        _set_pending_report(chat_id, pending.get("user_id"),
                            STAGE_REPORT_AFTERNOON,
                            morning=pending.get("morning"),
                            afternoon=pending.get("afternoon"))
        send_reply(chat_id, t_ui(
            lang, "report_ask_afternoon",
            lo=REPORT_AFTERNOON_WINDOW[0], hi=REPORT_AFTERNOON_WINDOW[1],
            off=report_word(lang, "off"),
        ))
        return True

    _save_report_settings(chat_id, lang, users_ws, users_map, pending)
    return True


def _handle_report_settings_dialog(message, chat_id, lang, users_map, users_ws) -> bool:
    """Dialog godzin raportów (/raport, /report, /menu). True = wiadomość zużyta.

    Kolejność ma znaczenie: najpierw anulowanie, potem start nowego dialogu,
    a na końcu odpowiedzi właściciela aktywnego dialogu. Odpowiedź innej osoby
    nigdy nie zmienia stanu i nigdy nie zapisuje danych.
    """
    text = (message.get("text") or "").strip()
    head = _command_head(text)
    user_id = (message.get("from") or {}).get("id")

    # 1. /raport [anuluj] — koniec dialogu bez dotykania Users.
    if head in REPORT_COMMAND_HEADS:
        argument = (_split_command(text, REPORT_COMMAND_HEADS) or "").strip()
        if _norm_answer(argument) in REPORT_CANCEL_ANSWERS:
            _clear_pending_report(chat_id)
            send_reply(chat_id, t_ui(lang, "report_cancelled"))
            return True
        # Ponowne /raport w trakcie dialogu: stary stan znika, startujemy
        # od aktualnych wartości zapisanych w Users.
        return _start_report_dialog(chat_id, user_id, lang, users_map)

    pending = _get_pending_report(chat_id)
    if not pending or not text:
        return False

    # Cudza wiadomość nie przejmuje dialogu (i nie kasuje go nikomu).
    if str(pending.get("user_id") or "") != str("" if user_id is None else user_id):
        return False
    if text.startswith("/"):
        # Inna komenda właściciela kończy dialog — bez zostawiania
        # niewidocznego stanu, który przechwyci kolejny wpisany tekst.
        _clear_pending_report(chat_id)
        return False

    answer = _norm_answer(text)
    if answer in REPORT_CANCEL_ANSWERS:
        _clear_pending_report(chat_id)
        send_reply(chat_id, t_ui(lang, "report_cancelled"))
        return True

    stage = pending.get("stage")

    if stage == STAGE_REPORT_CONFIRM:
        if answer in report_words(lang, "yes"):
            # „tak”: godziny zostają dokładnie takie, jakie są — zapisujemy
            # wyłącznie nowe ustawienia, i to dopiero na końcu dialogu.
            settings = users_store.get_report_settings(users_map, chat_id) or {}
            _clear_pending_report(chat_id)
            send_reply(chat_id, t_ui(
                lang, "report_kept",
                morning=_report_slot_display(settings.get("report_morning_time"), lang),
                afternoon=_report_slot_display(settings.get("report_afternoon_time"), lang),
            ))
            return True
        if answer in report_words(lang, "no"):
            pending["stage"] = STAGE_REPORT_MORNING
            _set_pending_report(chat_id, pending.get("user_id"),
                                STAGE_REPORT_MORNING)
            send_reply(chat_id, t_ui(
                lang, "report_ask_morning",
                lo=REPORT_MORNING_WINDOW[0], hi=REPORT_MORNING_WINDOW[1],
                off=report_word(lang, "off"),
            ))
            return True
        send_reply(chat_id, t_ui(
            lang, "report_bad_answer",
            yes=report_word(lang, "yes"), no=report_word(lang, "no"),
        ))
        return True

    if stage in (STAGE_REPORT_MORNING, STAGE_REPORT_AFTERNOON):
        return _handle_report_slot_answer(
            chat_id, lang, users_ws, users_map, pending, stage, answer,
        )

    # Nieznany etap (np. po zmianie kodów w RAM): nie zgadujemy, kończymy stan.
    _clear_pending_report(chat_id)
    return False


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
    """Generuje kartę bez odczytu lub zapisu Formularz/Users.

    POPRAWKA #1 (captions): karta leci jako SAM obrazek — bez podpisu (caption)
    z nazwą lokalizacji. Dotąd pod zdjęciem pojawiał się dopisek identyczny z
    tytułem karty (np. „Wiązowna”), bo nazwa miejscowości jest już wtopiona
    w grafikę. Dotyczy to ścieżki Users (profil zapisany w arkuszu oraz karta
    jednorazowa dla podanego miasta: /day, /now, /future). Tryb gościa
    (.d/.n/.f, wzmianka @bot) ma własny send_photo_fn i pozostaje bez zmian.
    """
    try:
        payload = _timed_call(
            "weather.build_payload",
            build_payload_for_location,
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
        image_path = _timed_call(
            "card.render", image_generator.generate_weather_card, layout
        )
        if not image_path:
            return False
        # POPRAWKA #1: zero captionu — sama karta (nazwa lokalizacji jest
        # tytułem grafiki, więc podpis pod zdjęciem tylko ją dublował).
        send_photo(chat_id, image_path, card_caption=False)
        return True
    except Exception as e:
        print(f"  ❌ [oneoff] Błąd generowania dla {chat_id}: {e}")
        import traceback
        traceback.print_exc()
        return False


def _used_location_message(lang, short_label, display_location=None):
    """Stopka one-off: krótka nazwa pozostaje osobna od display_location."""
    safe_display = _md_safe(display_location) or _md_safe(short_label)
    return t_ui(lang, "used_location", display_location=safe_display)



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


def _run_oneoff_report(
    chat_id, lat, lon, short_label, lang, source, card_type, display_location=None
):
    """One-off report: forecast coordinates and short card name stay separate
    from the full, safe label shown in the confirmation message.

    Nothing is persisted to Users or Formularz. The original coordinates are
    kept in RAM only for the existing hidden technical alias.
    """
    pending = _put_pending_save(chat_id, lat, lon, short_label, lang, source)
    if not pending:
        send_reply(chat_id, _oneoff_message(lang, "generation_error"))
        return False
    if not _send_oneoff_report(
        chat_id, lat, lon, pending["city"], pending["lang"], card_type
    ):
        PENDING_SAVE.pop(str(chat_id), None)
        send_reply(chat_id, _oneoff_message(lang, "generation_error"))
        return False
    send_reply(
        chat_id,
        _used_location_message(pending["lang"], pending["city"], display_location),
    )
    return True


def _run_saved_profile_report(chat_id, profile, lang, card_type):
    """Raport dla wcześniej zapisanego profilu — bez tworzenia nowego pending."""
    if not _send_oneoff_report(chat_id, profile["lat"], profile["lon"], profile["city"], lang, card_type):
        send_reply(chat_id, _oneoff_message(lang, "generation_error"))
        return False
    return True


def _run_city_oneoff(chat_id, city_query, lang, card_type):
    """Geokoduje nazwę miasta i generuje raport bez żadnego trwałego zapisu.

    ETAP 1: raport powstaje wyłącznie dla GEOCODE_OK. TOO_SHORT odpada zanim
    zdążymy cokolwiek wyszukać, a NO_MATCH / UNCERTAIN / NOT_FOUND / ERROR
    dostają własny, wspólny komunikat statusowy — bez karty i bez pending.
    """
    if _geocode_query_is_too_short(city_query):
        # 1-2 znaki bez kontekstu: nie pytamy mapy i nie udajemy, że szukamy.
        send_reply(chat_id, t_geocode(lang, GEOCODE_TOO_SHORT))
        return False
    send_reply(chat_id, t_ui(lang, "search_loc"))
    (
        geocode_status, lat, lon,
        fallback_short_label, fallback_display_location,
    ) = geocode_city_accepted(city_query, lang)
    if geocode_status != GEOCODE_OK:
        # Awaria serwera map (geo_conn_err), pusta odpowiedź (search_fail) albo
        # odrzuceni kandydaci — żaden wariant nie generuje karty.
        send_reply(chat_id, t_geocode(lang, geocode_status))
        return False
    if lat is None or lon is None:
        send_reply(chat_id, t_ui(lang, "search_fail"))
        return False
    if card_type == "day" and _day_card_window_blocked(chat_id, lang, lat, lon):
        return False
    short_label, display_location, geo_status = _resolve_location_labels(
        lat, lon, lang,
        fallback_short_label=fallback_short_label,
        fallback_display_location=fallback_display_location,
        query=city_query,
    )
    if geo_status == GEO_ERROR:
        send_reply(chat_id, t_ui(lang, "geo_conn_err"))
        return False
    return _run_oneoff_report(
        chat_id, lat, lon, short_label, lang, "city", card_type,
        display_location=display_location,
    )


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


def _save_profile_from_location(
    chat_id, users_ws, users_map, lat, lon, short_label, lang, source,
    display_location=None,
):
    """Save the short label and original coordinates through the existing Users
    writer (which rounds stored coordinates to three decimal places).
    """
    saved_at = users_store.now_iso()
    saved = _sheets_call(
        "sheets.users.set_profile",
        users_store.set_profile,
        users_ws, chat_id, lat, lon, short_label, source, lang, PRIVACY_VERSION, saved_at,
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
        "location_label": short_label,
        "location_source": source,
        "lang": _norm_lang(lang) or "en",
        "location_consent_at": saved_at,
        "location_consent_version": PRIVACY_VERSION,
        "profile_updated_at": saved_at,
    })
    _cache_users_map(users_map)

    # Tylko oczyszczony display_location trafia do komunikatu; profil i karta
    # nadal używają osobnego short_label.
    safe_display = _md_safe(display_location) or _md_safe(short_label)
    send_reply(
        chat_id,
        t_ui(lang, "location_saved", display_location=safe_display),
        reply_markup={"remove_keyboard": True},
    )
    return True


# ==============================================================
# KOMENDY PRYWATNOŚCI (RODO) — PR1
# Dostępne dla KAŻDEGO (także bez access i bez wiersza w bazie).
# ==============================================================
def _resolve_privacy_lang(message, chat_id, users_map, clean_users):
    """Język dla komend prywatności: Users -> Formularz (legacy) -> Telegram."""
    return _effective_lang(
        message, chat_id, users_map,
        legacy_row=_legacy_user_row(clean_users, chat_id),
    )


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
    cleared_at = users_store.now_iso()
    cleared_users = _sheets_call(
        "sheets.users.clear_profile",
        users_store.clear_profile,
        users_ws, chat_id, cleared_at,
    )
    if cleared_users:
        user_entry = (users_map or {}).get(users_store.norm_chat_id(chat_id))
        if user_entry is not None:
            for field in users_store.PROFILE_COLS:
                user_entry[field] = ""
            user_entry["profile_status"] = "none"
            user_entry["profile_updated_at"] = cleared_at
        _cache_users_map(users_map or {})

    cleared_legacy = _clear_legacy_location(main_sheet, chat_id)
    _invalidate_form_snapshot()
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
    POPRAWKA #7: po skutecznej kasacji idą DOKŁADNIE dwa komunikaty — zaraz po
    kasacji ``delete_me_done`` (bez linku zaproszenia), a po ok. 1 sekundzie
    osobny, pełny ``no_access``: dostęp został odebrany razem z danymi, więc od
    tego momentu odpowiada wyłącznie /start. Gdy nie było czego kasować,
    zostaje jedno ``no_data`` (bez drugiego komunikatu).
    """
    users_deleted = _sheets_call(
        "sheets.users.delete_user",
        users_store.delete_user_row,
        users_ws, chat_id,
    )
    legacy_deleted = _delete_legacy_rows(main_sheet, chat_id)
    if users_deleted:
        _invalidate_users_snapshot()

    # Czyszczenie stanów RAM — pending nigdy nie może przetrwać kasacji danych.
    PENDING_CITY.pop(str(chat_id), None)
    PENDING_SAVE.pop(str(chat_id), None)
    PENDING_DELETE.pop(str(chat_id), None)

    remove_keyboard = {"remove_keyboard": True}
    if users_deleted or legacy_deleted:
        send_reply(chat_id, t_ui(lang, "delete_me_done"), reply_markup=remove_keyboard)
        # POPRAWKA #7: drugi, osobny komunikat po krótkiej pauzie — pokazuje, że
        # razem z danymi zniknął dostęp i że powrót wymaga ponownego zaproszenia.
        time.sleep(DELETE_NOTICE_DELAY_SEC)
        send_reply(chat_id, t_ui(lang, "no_access", url=INVITE_URL))
    else:
        send_reply(chat_id, t_ui(lang, "no_data"), reply_markup=remove_keyboard)


def _clear_legacy_location(main_sheet, chat_id):
    """
    Czyści kolumny Lat/Lon/Miasto we wszystkich wierszach legacy (Formularz)
    dla danego chat_id — jeden batch update_cells. Zwraca True, gdy coś wyczyszczono.
    """
    try:
        headers = [
            str(h).strip()
            for h in _sheets_call(
                "sheets.formularz.read_headers", main_sheet.row_values, 1
            )
        ]
        col_lat = headers.index("Lat") + 1 if "Lat" in headers else None
        col_lon = headers.index("Lon") + 1 if "Lon" in headers else None
        col_miasto = headers.index("Miasto") + 1 if "Miasto" in headers else None

        col_values = _sheets_call(
            "sheets.formularz.read_chat_ids", main_sheet.col_values, 2
        )
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
            _sheets_call("sheets.formularz.write_cells", main_sheet.update_cells, cells)
            _invalidate_form_snapshot()
            rows = len({c.row for c in cells})
            print(f"  🧹 [forget_location] Wyczyszczono lokalizację w {rows} wierszach legacy (Formularz) dla {chat_id}.")
            return True
        return False
    except Exception as e:
        _raise_if_sheets_429(e)
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
        col_values = _sheets_call(
            "sheets.formularz.read_chat_ids", main_sheet.col_values, 2
        )
        cid = str(chat_id)
        targets = [
            i + 1 for i, val in enumerate(col_values)
            if str(val).strip() in (cid, f"BLOCKED_{cid}")
        ]
        for row in sorted(targets, reverse=True):
            _sheets_call("sheets.formularz.delete_row", main_sheet.delete_rows, row)
            _invalidate_form_snapshot()
        if targets:
            print(f"  🗑 [delete_me] Usunięto {len(targets)} wierszy legacy (Formularz) dla {chat_id}.")
        return len(targets)
    except Exception as e:
        _raise_if_sheets_429(e)
        print(f"  ❌ [delete_me] Błąd usuwania wierszy legacy: {e}")
        alert_admin(f"❌ /delete_me ({chat_id}): błąd usuwania wierszy Formularz: {e}")
        return 0



# ============================================================================
# GEOKODER Z JAWNYM STATUSEM (POPRAWKA #4)
# ============================================================================
# Odpowiedź geokodera to nie tylko dane, ale i STATUS — dopiero on pozwala
# odróżnić trzy sytuacje, które dla użytkownika znaczą co innego:
#   GEO_OK      -> geokoder znalazł miejscowość, wieś lub miasto,
#   GEO_NO_CITY -> odpowiedział, ale to teren bez miejscowości (pustynia, góry
#                  itp.) -> komunikat "Lokalizacja w terenie (poza miastem)",
#   GEO_ERROR   -> NIE odpowiedział (timeout sieci/limit) -> "Błędy na łączach,
#                  spróbuj za chwilę ponownie." i NIC nie zapisujemy.
GEO_OK = "ok"
GEO_NO_CITY = "no_city"
GEO_ERROR = "error"

# Etykiety zapisywane do bazy, gdy geokoder nie wskaże miejscowości
# (dokładnie te same napisy, które kod zwracał dotychczas).
FIELD_LOCATION_LABEL = "Lokalizacja w terenie (poza miastem)"
FIELD_LOCATION_LEGACY = "Lokalizacja w terenie"

_GEO_DETAILS_CACHE = {}   # (lat, lon, lang, query, mode) -> (expiry_ts, (short_label, display_location, status))
_GEO_DETAILS_TTL = 86400  # 24 h — adres miejscowości nie zmienia się co chwilę


def _md_safe(text):
    """Escape dynamic text for Telegram's legacy Markdown parse mode.

    Call only for dynamic values inserted outside an existing Markdown entity.
    Telegram then renders the escaped punctuation literally instead of
    interpreting geocoder data as formatting or a link.
    """
    value = str(text or "").strip()
    escaped = []
    for char in value:
        if char in "\\_*`[":
            escaped.append("\\")
        escaped.append(char)
    return "".join(escaped)


_LOCALITY_ADDRESS_FIELDS = ("city", "town", "village", "hamlet", "locality")
_ADMIN_ADDRESS_FIELDS = ("municipality", "county", "state")
_PUBLIC_ADDRESS_FIELDS = _ADMIN_ADDRESS_FIELDS + ("postcode", "country")
_STREET_MARKER_RE = re.compile(
    r"(?<!\w)(?:ul(?:ica)?|al(?:eja)?|street|road|avenue|ave\.?|"
    r"rue|calle|carrer|straße|strasse|weg|platz|place|gate|gata|"
    r"vei|veien|veg|straat|plac)(?!\w)",
    re.IGNORECASE,
)


def _nominatim_address_components(raw_address):
    """Return only Nominatim's structured ``address`` map, never display_name."""
    if not isinstance(raw_address, dict):
        return {}
    nested = raw_address.get("address")
    return nested if isinstance(nested, dict) else raw_address


def _clean_location_component(value):
    if value is None or isinstance(value, (dict, list, tuple)):
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\r", " ").replace("\n", " ")).strip()


def _normalize_location_text(value, discard_numbers=False):
    """Loose comparison form for matching a query to structured place names."""
    text = str(value or "").casefold().replace("ł", "l").replace("ø", "o")
    text = text.replace("æ", "ae").replace("œ", "oe")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    tokens = text.split()
    if discard_numbers:
        tokens = [token for token in tokens if not any(ch.isdigit() for ch in token)]
    return " ".join(tokens)


def _contains_location_phrase(text, phrase):
    if not text or not phrase:
        return False
    return f" {phrase} " in f" {text} "


# ============================================================================
# POSTCODE GATING (HOTFIX) — widoczny tylko, gdy user wpisał TEN SAM postcode
# ============================================================================
# Postcode z reverse/forward adresu trafia do publicznej etykiety wyłącznie,
# gdy query użytkownika wprost go zawiera. Bez tego np. "London" dostawałby
# "WC2N 5DU" z reverse, mimo że nikt go nie wpisał. Dopasowanie jest
# międzynarodowe (bez regexu ograniczonego do polskich kodów numerycznych) i
# działa na RÓWNOŚCI skompaktowanego ciągu, nigdy na "substring in query" —
# inaczej krótki postcode pasowałby jako fragment dłuższej liczby.
def _compact_alnum(text):
    """Znormalizuj (casefold, bez diakrytyków) i zostaw wyłącznie [a-z0-9].

    "WC2N 5DU" -> "wc2n5du", "05-462" -> "05462" — dzięki temu porównanie
    nie zależy od spacji ani myślników w żadnym z dwóch tekstów.
    """
    normalized = _normalize_location_text(text)
    return re.sub(r"[^a-z0-9]", "", normalized)


def _query_mentions_candidate_postcode(address, query):
    """True tylko, gdy postcode kandydata pojawia się w query jako 1-3 sąsiednie
    tokeny, po kompakcji — RÓWNOŚĆ, nigdy podciąg większej liczby/tokenu.
    """
    postcode = _clean_location_component((address or {}).get("postcode"))
    if not postcode:
        return False

    pc = _compact_alnum(postcode)
    if not pc:
        return False

    toks = _normalize_location_text(query or "", discard_numbers=False).split()
    toks = [token for token in toks if token]
    if not toks:
        return False

    for n in (1, 2, 3):
        for index in range(0, len(toks) - n + 1):
            ngram = _compact_alnum(" ".join(toks[index:index + n]))
            if ngram == pc:
                return True
    return False


def _select_short_location_label(address, query=None, lang="pl"):
    """Select one locality/admin value for cards, payloads and stored labels."""
    locality_values = [
        _clean_location_component(address.get(field))
        for field in _LOCALITY_ADDRESS_FIELDS
    ]
    locality_values = [value for value in locality_values if value]
    normalized_query = _normalize_location_text(query, discard_numbers=True)

    if normalized_query:
        # Prefer an exact query match to avoid selecting a longer settlement name.
        for value in locality_values:
            if _normalize_location_text(value) == normalized_query:
                return value
        # If the user supplied a postcode or street along with the town, choose
        # the most specific locality that appears as a complete phrase.
        matches = [
            value for value in locality_values
            if _contains_location_phrase(
                normalized_query, _normalize_location_text(value)
            )
        ]
        if matches:
            return max(matches, key=lambda value: len(_normalize_location_text(value)))

    if locality_values:
        return locality_values[0]

    # No settlement was returned: use one safe administrative value, not query
    # text or a raw Nominatim address. Prefix Polish admin units to keep the
    # standalone card/profile label meaningful without repeating it in display.
    polish = _norm_lang(lang) == "pl" and _is_polish_address(address)
    for field in _ADMIN_ADDRESS_FIELDS:
        value = _clean_location_component(address.get(field))
        if value:
            return _format_admin_component(field, value, polish=polish)
    for field in ("postcode", "country"):
        value = _clean_location_component(address.get(field))
        if value:
            return value
    return ""


def _public_road_component(address):
    """Return the road field without any duplicated house-number suffix."""
    road = _clean_location_component(address.get("road"))
    house_number = _clean_location_component(address.get("house_number"))
    if road and house_number:
        road = re.sub(
            rf"(?<!\w){re.escape(house_number)}(?!\w)", " ", road, flags=re.IGNORECASE
        )
        road = re.sub(r"\s+", " ", road).strip(" ,")
    return road


def _query_explicitly_names_road(query, address):
    """Only expose ``road`` when the user explicitly asked for street-level data."""
    query_text = str(query or "").strip()
    road = _public_road_component(address)
    normalized_query = _normalize_location_text(query_text)
    normalized_road = _normalize_location_text(road)
    if not query_text or not normalized_road or not _contains_location_phrase(
        normalized_query, normalized_road
    ):
        return False

    house_number = _normalize_location_text(address.get("house_number"))
    if house_number and _contains_location_phrase(normalized_query, house_number):
        return True
    if _STREET_MARKER_RE.search(query_text):
        return True

    # A separately comma-delimited street component is also explicit, while a
    # A multiword place name alone is not treated as a street address.
    parts = query_text.split(",")
    return len(parts) > 1 and any(
        _contains_location_phrase(_normalize_location_text(part), normalized_road)
        for part in parts
    )


def _location_mode_for_query(query, address):
    return "address" if _query_explicitly_names_road(query, address) else "city"


def _is_polish_address(address):
    country_code = _clean_location_component(address.get("country_code")).casefold()
    country_name = _normalize_location_text(address.get("country"))
    return country_code == "pl" or country_name in {"polska", "poland"}


def _format_admin_component(field, value, polish=False):
    cleaned = _clean_location_component(value)
    if not cleaned or not polish:
        return cleaned

    prefixes = {
        "municipality": ("gmina", "miasto"),
        "county": ("powiat",),
        "state": ("wojewodztwo", "woj"),
    }
    allowed_prefixes = prefixes.get(field, ())
    normalized = _normalize_location_text(cleaned)
    if any(
        normalized == prefix or normalized.startswith(prefix + " ")
        for prefix in allowed_prefixes
    ):
        return cleaned

    prefix = {
        "municipality": "gmina",
        "county": "powiat",
        "state": "województwo",
    }.get(field)
    return f"{prefix} {cleaned}" if prefix else cleaned


def _format_public_location_parts(raw_address, query=None, mode="city", lang="pl"):
    """Return ``(short_label, display_location)`` from an allowlist of fields."""
    address = _nominatim_address_components(raw_address)
    if not address:
        return "", ""

    short_label = _select_short_location_label(address, query=query, lang=lang)
    if not short_label:
        return "", ""

    components = []
    seen = set()
    polish = _norm_lang(lang) == "pl" and _is_polish_address(address)

    def add(value):
        cleaned = _clean_location_component(value)
        key = _normalize_location_text(cleaned)
        if cleaned and key and key not in seen:
            seen.add(key)
            components.append(cleaned)

    # The short locality/admin name is always first and remains separate from
    # the long label passed to UI messages.
    add(short_label)
    if mode == "address" and _query_explicitly_names_road(query, address):
        add(_public_road_component(address))
    # HOTFIX: postcode gating — public tylko, gdy query wprost wymienia TEN SAM
    # kod. ``query`` bywa ``None`` (pinezka/GPS) — wtedy postcode nigdy nie jest
    # jawnie wpisany, więc nie wolno go pokazać tylko dlatego, że istnieje w
    # adresie reverse/forward.
    include_postcode = _query_mentions_candidate_postcode(address, query)
    for field in _PUBLIC_ADDRESS_FIELDS:
        if field == "postcode" and not include_postcode:
            continue
        value = _format_admin_component(field, address.get(field), polish=polish)
        add(value)

    return short_label, ", ".join(components)


def format_public_location_label(raw_address, query=None, mode="city", lang="pl"):
    """Format a user-facing label from structured Nominatim address fields.

    ``raw_address`` is ``location.raw["address"]`` (or the containing raw
    mapping), never ``location.address``. City/reverse mode excludes roads,
    house numbers, POIs, buildings, suburbs and neighbourhoods. Address mode
    may include only a road explicitly present in the query, still without a
    house number or POI.
    """
    address = _nominatim_address_components(raw_address)
    effective_mode = mode
    if effective_mode == "auto":
        effective_mode = _location_mode_for_query(query, address)
    if effective_mode not in ("city", "address"):
        effective_mode = "city"
    if effective_mode == "address" and not _query_explicitly_names_road(query, address):
        effective_mode = "city"
    _short_label, display_location = _format_public_location_parts(
        address, query=query, mode=effective_mode, lang=lang
    )
    return display_location


# HOTFIX (pinezka/GPS) — krok 2 reverse: coarse zoom, best-effort.
# Wynik POI (kawiarnia, sklep, budynek) w metropolii często niesie jako "city"
# nazwę dzielnicy/borough. Zamiast mapować borough->city, pytamy DODATKOWO
# o zgrubny wynik (zoom=10) i używamy go WYŁĄCZNIE, gdy zawiera prawdziwe pole
# miejscowości i nie jest POI. W każdym innym przypadku zostaje wynik kroku 1.
_REVERSE_COARSE_ZOOM = 10
_REVERSE_POI_CATEGORIES = frozenset({
    "amenity", "shop", "tourism", "leisure", "office", "building", "man_made",
})
_REVERSE_POI_ADDRESS_FIELDS = (
    "amenity", "shop", "tourism", "building", "house_number", "road",
)


def _reverse_is_poi_category(raw):
    """Kategoria/klasa OSM z najwyższego poziomu payloadu wskazuje POI."""
    if not isinstance(raw, dict):
        return False
    for key in ("category", "class"):
        value = _clean_location_component(raw.get(key)).casefold()
        if value in _REVERSE_POI_CATEGORIES:
            return True
    return False


def _reverse_has_poi_address_fields(address):
    return any(_clean_location_component(address.get(f)) for f in _REVERSE_POI_ADDRESS_FIELDS)


def _reverse_has_settlement(address):
    return any(_clean_location_component(address.get(f)) for f in _LOCALITY_ADDRESS_FIELDS)


def _reverse_needs_coarse(raw, address):
    """Kiedy uruchomić krok 2: wynik POI albo adres POI bez miejscowości."""
    if _reverse_is_poi_category(raw):
        return True
    return _reverse_has_poi_address_fields(address) and not _reverse_has_settlement(address)


def _coarse_reverse_settlement(geolocator, lat, lon, lang):
    """Krok 2 (best-effort): ``(short, display)`` albo None — nigdy wyjątek.

    Brak ``zoom`` w geopy (TypeError), timeout, wyjątek, pusty wynik, wynik
    nadal POI albo bez pola miejscowości => None, a wywołujący zostaje przy
    wyniku kroku 1. Etykieta idzie przez ten sam formatter i allowlistę pól
    co dotąd, w trybie city (bez road/house_number), bez postcode (query=None).
    """
    try:
        location = _timed_call(
            "nominatim.reverse.coarse",
            geolocator.reverse,
            f"{lat}, {lon}", language=lang, zoom=_REVERSE_COARSE_ZOOM,
        )
    except Exception:
        return None
    if not location:
        return None
    raw = getattr(location, "raw", None)
    if not isinstance(raw, dict):
        return None
    address = _nominatim_address_components(raw)
    if not address or _reverse_is_poi_category(raw) or _reverse_has_poi_address_fields(address):
        return None
    if not _reverse_has_settlement(address):
        return None
    short_label, display_location = _format_public_location_parts(
        address, query=None, mode="city", lang=lang
    )
    if not short_label or not display_location:
        return None
    return short_label, display_location


def get_location_details_from_coords(lat, lon, lang="pl", query=None, mode=None):
    """Reverse geocode to ``(short_label, display_location, status)``.

    Only structured Nominatim address fields are cached/returned. The formatted
    ``location.address`` string is intentionally never read or exposed.

    Krok 1: reverse jak dotąd. Krok 2 (tylko pinezka/GPS, tj. bez query, gdy
    krok 1 wskazuje POI): coarse reverse zoom=10 — używany tylko, gdy daje
    pole miejscowości. Błąd kroku 2 nigdy nie psuje wyniku kroku 1.
    """
    try:
        lat_value, lon_value = float(lat), float(lon)
        query_key = _normalize_location_text(query)
        key = (round(lat_value, 5), round(lon_value, 5),
               (lang or "pl")[:2].lower(), query_key, mode or "auto")
    except (TypeError, ValueError):
        return None, None, GEO_ERROR

    cached = _GEO_DETAILS_CACHE.get(key)
    if cached and time.time() < cached[0]:
        return cached[1]

    try:
        geolocator = Nominatim(user_agent="pogoda_world_bot")
        location = _timed_call(
            "nominatim.reverse", geolocator.reverse, f"{lat}, {lon}", language=lang
        )
        if not location:
            return None, None, GEO_ERROR

        raw_location = getattr(location, "raw", {}) or {}
        address = _nominatim_address_components(raw_location)
        effective_mode = mode or _location_mode_for_query(query, address)
        short_label, display_location = _format_public_location_parts(
            address, query=query, mode=effective_mode, lang=lang
        )
        if short_label and display_location:
            result = (short_label, display_location, GEO_OK)
        else:
            # Do not fall back to the formatted Nominatim address or the query.
            result = (None, None, GEO_NO_CITY)

        # HOTFIX krok 2: tylko pinezka/GPS (bez query), tylko dla POI/adresu
        # bez miejscowości. Sukces = pole miejscowości z coarse reverse; w
        # przeciwnym razie zostaje wynik kroku 1 (także GEO_NO_CITY).
        if not query_key and _reverse_needs_coarse(raw_location, address):
            try:
                coarse = _coarse_reverse_settlement(geolocator, lat, lon, lang)
            except Exception:
                coarse = None
            if coarse:
                result = (coarse[0], coarse[1], GEO_OK)

        _GEO_DETAILS_CACHE[key] = (time.time() + _GEO_DETAILS_TTL, result)
        return result

    except Exception as e:
        print(f"Błąd geolokalizacji: {e}")
        return None, None, GEO_ERROR


def get_city_from_coords(lat, lon, lang="pl"):
    """Compatibility adapter: short place name only (guest/cards/legacy)."""
    short_label, _display_location, status = get_location_details_from_coords(lat, lon, lang)
    if status == GEO_OK and short_label:
        return short_label
    if status == GEO_NO_CITY:
        return FIELD_LOCATION_LABEL
    return FIELD_LOCATION_LEGACY


# ============================================================================
# WALIDACJA ZAPYTANIA DO GEOKODERA — ETAP 1
# ============================================================================
# Nominatim potrafi dla krótkiej lub niepełnej nazwy zwrócić miejsce, które z
# zapytaniem nie ma nic wspólnego: "U" -> Chubut (Argentyna), "Wa" -> Little
# Sandy Desert (Australia), "Hel" -> Helmand (Afganistan). Z takiego wyniku nie
# generujemy karty i niczego nie zapisujemy. Stąd trzy proste zasady:
#
#   1. 1-2 znaki bez kontekstu = w ogóle nie pytamy mapy (GEOCODE_TOO_SHORT),
#   2. pytamy o kilku kandydatów naraz (exactly_one=False, limit=7,
#      addressdetails=True, language=lang) i akceptujemy wyłącznie takiego,
#      którego nazwa miejscowości równa się zapytaniu albo zawiera je jako
#      pełny token/frazę — prefiks dłuższego słowa nie wystarcza ("Hel" to nie
#      "Helmand", "Wa" to nie "Western Australia"),
#   3. zero zaakceptowanych = GEOCODE_NO_MATCH, a spośród zwalidowanych wygrywa
#      PIERWSZY w kolejności geokodera (validated top result). To nie jest
#      powrót do "pierwszego wyniku z całej kuli ziemskiej" — kolejność znaczy
#      coś dopiero PO filtrach klasy, kraju, nazwy i kontekstu.
#
# ETAP 1.1 dokłada do tego samego mechanizmu:
#   * jawny kraj z zapytania ("Hel PL", "Paryż, Francja") — parsowany z samego
#     zapytania i podawany geokoderowi jako country_codes, a kandydat musi
#     zgodzić się krajem w raw["address"]["country_code"] (twardy filtr),
#   * minimalna mapa egzonimów + namedetails ("Paryż" -> Paris), bez bazy miast,
#   * GEOCODE_UNCERTAIN zostaje jako rzadki bezpiecznik dla dowodu wyłącznie ze
#     surowego display_name, gdy geokoder zwrócił kilka rozróżnialnych miejsc.
#
# Statusy i ich komunikaty są jednoznaczne i definiowane raz (i18n), więc
# /dzien, /teraz, /trend, /miasto, prompty, skróty i wzmianka @bot czytają ten
# sam wynik. `_geocode_forward_candidates` pozostaje JEDYNYM miejscem w
# projekcie, które woła forward geokoder.
GEOCODE_CANDIDATE_LIMIT = 7
GEOCODE_MIN_QUERY_CHARS = 3

# Kraj czytamy WYŁĄCZNIE z zapytania użytkownika — język UI nigdy nie jest
# kontekstem kraju. Klucze są już znormalizowane (casefold + bez diakrytyków),
# a wartości to kody Nominatima: "UK" to "gb", bo takiego kodu używa geokoder.
_GEOCODE_COUNTRY_MAX_TOKENS = 4
_GEOCODE_COUNTRY_ALIASES = {
    "pl": "pl", "polska": "pl", "poland": "pl",
    "fr": "fr", "francja": "fr", "france": "fr",
    "us": "us", "usa": "us", "stany zjednoczone": "us", "united states": "us",
    "united states of america": "us", "ameryka": "us",
    "de": "de", "niemcy": "de", "germany": "de", "deutschland": "de",
    "uk": "gb", "gb": "gb", "wielka brytania": "gb", "united kingdom": "gb",
    "great britain": "gb",
    "no": "no", "norwegia": "no", "norway": "no", "norge": "no",
    "es": "es", "hiszpania": "es", "spain": "es", "espana": "es", "españa": "es",
}
# Minimalna mapa egzonimów (Paryż -> Paris, Nowy Jork -> New York). To WYŁĄCZNIE
# dodatkowy wariant porównania — reguła dopasowania nazwy (pełna nazwa, pełny
# token/fraza, bez prefiksów) zostaje bez zmian i bez bazy miast.
_GEOCODE_CITY_ALIASES = {
    "paryz": "paris",
    "nowy jork": "new york",
    # HOTFIX: London/Londyn — wyłącznie kontrolowany wariant pełnej nazwy
    # (patrz _geocode_query_variants), bez fuzzy/substring/prefix matchingu.
    "london": "londyn",
    "londyn": "london",
}
# Egzonimy z namedetails: tylko te klucze i dowolne name:<kod>.
_GEOCODE_NAMEDETAILS_KEYS = ("name", "int_name", "official_name", "alt_name")
# Siła dowodu dopasowania (patrz _geocode_candidate_evidence).
_GEOCODE_EVIDENCE_PLACE = "place"          # pola miejscowości
_GEOCODE_EVIDENCE_LOCALIZED = "localized"  # egzonim z namedetails przy polach miejscowości
_GEOCODE_EVIDENCE_DISPLAY = "display"      # tylko name/display_name — dowód słaby

# Pola miejscowości = dowód, że geokoder wskazał TO miejsce, o które pytano.
_GEOCODE_PLACE_FIELDS = ("city", "town", "village", "hamlet", "locality")
# Pola administracyjne = kontekst rozstrzygający (kraj, region, kod pocztowy).
# NIGDY samodzielny dowód miejscowości dla krótkiego zapytania.
_GEOCODE_CONTEXT_FIELDS = ("municipality", "county", "state", "country", "postcode")
# Kontekst, który nie dowodzi nazwy, ale NIE jest też sprzecznością: adres,
# dzielnica, osiedle. Dzięki nim "Wiązowna, Kościelna 41" nadal geoduje się po
# ulicy, a "Wiązowna, Gdynia" nadal jest odrzucane.
_GEOCODE_EXTRA_CONTEXT_FIELDS = (
    "road", "house_number", "suburb", "neighbourhood", "quarter", "city_district",
    "city_block", "hamlet", "square", "place_square",
)
# Kod pocztowy: polski XX-XXX oraz każdy ciąg 4-10 cyfr (inne kraje).
_GEOCODE_POSTCODE_RE = re.compile(r"(?<!\d)(?:\d{2}-\d{3}|\d{4,10})(?!\d)")

# ============================================================================
# ETAP 1.2 — LUDZKIE DOPRECYZOWANIA LOKALIZACJI
# ============================================================================
# Zapytanie dzielimy na trzy części: ``core`` (nazwa miejscowości),
# ``admin_context`` (powiat/województwo/miejscowość nadrzędna) i ``country``
# (jawny kraj, już obsługiwany w etapie 1.1).
#
# Kontekst administracyjny jest DODATKOWYM filtrem, nigdy zamiennikiem walidacji
# core: kandydat musi najpierw udowodnić nazwę miejscowości (pełny token/fraza,
# bez prefiksów — „hel" to nadal nie „helmand"), a dopiero potem zgadzać się
# kontekstem. Reguła jest WARUNKOWA:
#
#   * parser wykrył admin_context  -> kandydat MUSI przejść nowy, jawny filtr
#     administracyjny. Stary matcher kontekstu z etapu 1.1 NIE jest obejściem:
#     nie ratuje kandydata, który nie pasuje do county/state/city/town tylko
#     dlatego, że kontekst przewinął się w display_name,
#   * brak admin_context           -> walidacja etapu 1.1 bez zmian.
#
# Filery („powiat", „koło", „województwo"...) są usuwane PRZED wysłaniem
# zapytania do Nominatima, bo pogarszają ranking.
_GEOCODE_ADMIN_FIELDS = (
    "county", "state", "region", "municipality", "province", "state_district",
    "district", "city", "town", "city_district", "suburb",
)
# Markery kontekstu — osobno frazy wielowyrazowe i pojedyncze tokeny. Wartości
# są znormalizowane (casefold, bez diakrytyków), więc „woj." i „województwo"
# i „koło" trafiają do tego samego koszyka.
_GEOCODE_ADMIN_MARKER_PHRASES = ("w poblizu",)
_GEOCODE_ADMIN_MARKER_TOKENS = frozenset({
    "powiat", "wojewodztwo", "woj", "gmina", "kolo", "okolice", "near", "around",
    "poblizu",
})
# Kontrolowany mechanizm rdzeni: NIE obcinamy końcówek („otwocki" nigdy nie
# staje się „otwo") — porównujemy wspólny PREFIKS dwóch tokenów. Krótkie
# tokeny i krótkie wspólne rdzenie są odrzucane, więc „wa" nie pasuje do niczego.
_GEOCODE_ADMIN_STEM_MIN_TOKEN = 3
_GEOCODE_ADMIN_STEM_MIN_COMMON = 4


def _geocode_name_matches(query_norm, name_norm):
    """Pełna nazwa albo pełny token/fraza — bez dopasowań prefiksem.

    Porównanie idzie po znormalizowanych wartościach (casefold, diakrytyki
    usunięte, interpunkcja do spacji, wiele spacji do jednej), więc granica
    tokenu jest twarda: "hel" pasuje do "Hel", ale nie do "Helmand", a "wa"
    nie pasuje do "Western Australia".
    """
    if not query_norm or not name_norm:
        return False
    if query_norm == name_norm:
        return True
    return f" {query_norm} " in f" {name_norm} "


def _geocode_query_variants(query_name):
    """Warianty porównania nazwy: zapytanie + minimalna mapa egzonimów.

    Alias (np. ``paryz`` -> ``paris``) jest TYLKO dodatkowym wariantem tego
    samego porównania — nie luzuje reguły dopasowania, więc ``paryz`` nadal nie
    pasuje do ``Paryżanka``, a ``hel`` do ``Helmand``.
    """
    variants = []
    normalized = _normalize_location_text(query_name)
    if normalized:
        variants.append(normalized)
    alias = _GEOCODE_CITY_ALIASES.get(normalized)
    if alias and alias not in variants:
        variants.append(alias)
    return variants


def _geocode_name_matches_any(variants, name_norm):
    """Czy którykolwiek wariant zapytania pasuje do nazwy kandydata."""
    if not name_norm:
        return False
    return any(_geocode_name_matches(variant, name_norm) for variant in variants)


def _geocode_query_context(query):
    """Rozkłada zapytanie na ``(nazwa, konteksty, kod pocztowy)``.

    Kontekstem jest TYLKO to, co użytkownik napisał sam: kraj albo region po
    przecinku oraz kod pocztowy. ``language=pl`` kontekstem kraju nie jest.
    """
    raw = str(query or "").strip()
    head, comma, tail = raw.partition(",")

    postcode = ""
    match = _GEOCODE_POSTCODE_RE.search(head)
    if match:
        postcode = _normalize_location_text(match.group(0))
        head = f"{head[:match.start()]} {head[match.end():]}"

    context = []
    if comma:
        for part in tail.split(","):
            normalized = _normalize_location_text(part)
            if normalized:
                context.append(normalized)

    return _normalize_location_text(head), context, postcode


def _geocode_query_country(query):
    """Wydobywa jawny kraj z zapytania: ``(rdzeń, kod kraju)`` albo ``(query, None)``.

    Kraj pochodzi WYŁĄCZNIE z zapytania (nigdy z języka UI). Normalizujemy jak
    walidator nazw i próbujemy sufiksu od najdłuższego (4 tokeny -> 3 -> 2 -> 1).
    Po trafieniu odcinamy sufiks od rdzenia, zjadamy końcowe przecinki/spacje
    i zwracamy ``(core_query, kod)``.

    Całego zapytania NIGDY nie odcinamy: "Polska" to pytanie o miejscowość, nie
    o kraj, więc sam kraj bez rdzenia nie jest kontekstem (parser zwraca wtedy
    oryginał bez kodu).
    """
    raw = str(query or "").strip()
    if not raw:
        return raw, None
    size, code = _geocode_country_suffix(raw)
    if not size or not code:
        return raw, None
    core = " ".join(raw.replace(",", " ").split()[:-size]).rstrip(" ,").strip()
    if not core:
        return raw, None
    return core, code


def _geocode_country_suffix(raw):
    """``(rozmiar sufiksu w tokenach, kod kraju)`` albo ``(0, None)``.

    Wspólne źródło prawdy dla parserów etapu 1.1 i 1.2 — wycięte z
    ``_geocode_query_country`` bez zmiany semantyki (najdłuższy pasujący sufiks,
    maksymalnie ``_GEOCODE_COUNTRY_MAX_TOKENS`` tokenów).
    """
    # Przecinek jest separatorem, nie częścią nazwy: "Hel, PL" i "Hel,PL" to ten
    # sam kraj na końcu zapytania.
    tokens = raw.replace(",", " ").split()
    max_tokens = min(_GEOCODE_COUNTRY_MAX_TOKENS, len(tokens) - 1)
    for size in range(max_tokens, 0, -1):
        suffix = " ".join(tokens[-size:])
        key = _normalize_location_text(suffix)
        if not key:
            continue
        code = _GEOCODE_COUNTRY_ALIASES.get(key)
        if code:
            return size, code
    return 0, None


def _geocode_strip_country_suffix(raw, size):
    """Usuwa ``size`` ostatnich tokenów, ZACHOWUJĄC strukturę przecinkową reszty.

    Etap 1.1 skleja resztę spacjami (``"Wiązowna, otwock, Polska"`` zmieniłoby
    się w ``"Wiązowna otwock"`` i podział na core/kontekst przestałby istnieć).
    Tu odcinamy tokeny od końca łańcucha i zjadamy powstałe separatory, więc
    zostaje ``"Wiązowna, otwock"``.
    """
    remaining = raw
    for _ in range(size):
        stripped = remaining.rstrip()
        match = re.search(r"\S+\s*$", stripped)
        if not match:
            return ""
        remaining = stripped[:match.start()].rstrip()
        if remaining.endswith(","):
            remaining = remaining[:-1].rstrip()
    return remaining.rstrip(" ,")


def _geocode_split_country(query):
    """``(reszta zapytania z zachowanymi przecinkami, kod kraju)``.

    To samo rozpoznanie sufiksu co ``_geocode_query_country``, ale rdzeń wraca
    w postaci nadającej się do dalszego podziału na core/admin_context.
    """
    raw = str(query or "").strip()
    if not raw:
        return raw, None
    size, code = _geocode_country_suffix(raw)
    if not size or not code:
        return raw, None
    core = _geocode_strip_country_suffix(raw, size)
    if not core:
        return raw, None
    return core, code


def _geocode_clean_admin_text(text):
    """Czyści kontekst z fillerów, zachowując oryginalną pisownię reszty.

    „powiat otwocki" -> „otwocki", „województwo śląskie" -> „śląskie",
    „koło Częstochowy" -> „Częstochowy". Frazy wielowyrazowe usuwamy pierwsze
    (całym zakresem tokenów), żeby „w pobliżu" nie zostawiło wiszącego „w".

    Porównanie idzie po znormalizowanych tokenach, ale USUWAMY oryginalne
    słowa — dzięki temu „Częstochowy" trafia do Nominatima z właściwą
    pisownią, a nie w postaci pozbawionej diakrytyków.
    """
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    tokens = cleaned.split()
    normalized = [_normalize_location_text(token) for token in tokens]

    drop = set()
    for phrase in _GEOCODE_ADMIN_MARKER_PHRASES:
        span = len(phrase.split())
        for start in range(0, len(normalized) - span + 1):
            if " ".join(normalized[start:start + span]) == phrase:
                drop.update(range(start, start + span))
    for index, token in enumerate(normalized):
        if token in _GEOCODE_ADMIN_MARKER_TOKENS:
            drop.add(index)

    return " ".join(
        token for index, token in enumerate(tokens) if index not in drop
    ).strip(" ,")


def _geocode_query_parse(query):
    """Rozkłada zapytanie na ``(core, admin_context, kod kraju)``.

    Kolejność: najpierw kraj (także po wielu segmentach — „Wiązowna, otwock,
    Polska"), potem podział reszty. Z przecinkiem pierwszy segment to core, a
    pozostałe to kontekst. Bez przecinka kontekst wykrywamy po markerze
    („Olsztyn województwo śląskie", „Olsztyn koło Częstochowy") — marker musi
    mieć token przed sobą i po sobie, żeby nie rozciąć nazwy własnej
    („Kolo", „Wielkie Kolo" zostają w całości).
    """
    rest, country_code = _geocode_split_country(str(query or "").strip())
    rest = rest.strip()
    if not rest:
        return "", [], country_code

    if "," in rest:
        segments = [segment.strip() for segment in rest.split(",")]
        core = segments[0]
        admin_context = []
        for segment in segments[1:]:
            cleaned = _geocode_clean_admin_text(segment)
            if cleaned:
                admin_context.append(cleaned)
        return core, admin_context, country_code

    tokens = rest.split()
    marker_index = None
    for index in range(1, len(tokens) - 1):
        window = _normalize_location_text(" ".join(tokens[index:index + 2]))
        if window in _GEOCODE_ADMIN_MARKER_PHRASES:
            marker_index = index
            break
    if marker_index is None:
        for index in range(1, len(tokens) - 1):
            if _normalize_location_text(tokens[index]) in _GEOCODE_ADMIN_MARKER_TOKENS:
                marker_index = index
                break
    if marker_index is None:
        return rest, [], country_code

    core = " ".join(tokens[:marker_index]).strip()
    cleaned = _geocode_clean_admin_text(" ".join(tokens[marker_index:]))
    return core, ([cleaned] if cleaned else []), country_code


def _geocode_geocoder_query(core, admin_context):
    """Zapytanie do Nominatima: ``core`` albo ``core, oczyszczony kontekst``.

    Bez wykrytego kontekstu zwraca dokładnie to, co wysyłał etap 1.1 —
    kontrakt sieciowy dla starych zapytań nie zmienia się ani o znak.
    """
    core = str(core or "").strip()
    parts = [
        str(part).strip() for part in (admin_context or ())
        if str(part).strip()
    ]
    if not core:
        return ", ".join(parts)
    if not parts:
        return core
    return f"{core}, {', '.join(parts)}"


def _geocode_query_is_too_short(query):
    """Czy zapytanie jest za krótkie, żeby w ogóle pytać mapę.

    1-2 znaki bez kontekstu (np. "U", "Wa", "Os") to zawsze loteria. Ten sam
    skrót z jawnym krajem albo kodem pocztowym ("Hel, PL", "Wiązowna 05-462")
    ma sens — wtedy pytamy, ale kandydat i tak musi przejść walidację nazwy.
    """
    raw = str(query or "").strip()
    if not raw:
        return True
    name, context, postcode = _geocode_query_context(raw)
    if context or postcode:
        return False
    if not name:
        return True
    # Liczymy znaki alfanumeryczne, nie długość napisu: "a b" to dwie litery,
    # czyli dokładnie to samo ryzyko co "U" czy "Wa".
    return len(name.replace(" ", "")) < GEOCODE_MIN_QUERY_CHARS


def _geocode_candidate_is_safe(raw):
    """Klasa kandydata, która w ogóle może być bazą dla lokalizacji pogodowej.

    Bezpieczne są WYŁĄCZNIE:
      * ``class == "place"`` — miejscowości,
      * ``class == "boundary"`` (oraz zachowana z etapu 1 legacy nazwa
        ``class == "administrative"``, traktowana identycznie) z POTWIERDZONYM
        ``type``/``addresstype == "administrative"``.

    Sam ``boundary`` bez potwierdzonego typu administracyjnego NIE przechodzi:
    brak ``type``/``addresstype`` to brak dowodu, że to granica administracyjna —
    a nie np. obszar chroniony, park narodowy albo granica morska. Etap 1 miał
    w kodzie cichą tolerancję ``typ in ("", "administrative")``; nie korzystał
    z niej żaden test, docstring mówił o "boundary + type=administrative", więc
    ETAP 1.1 wymaga jawnego potwierdzenia typu.

    Filtr odcina też natural=desert (Little Sandy Desert dla "Wa"),
    railway=station i całe POI (amenity/tourism/shop), które dawałoby albo
    bzdurny wynik, albo fałszywą niepewność przez nazwę wspólną z miastem.
    """
    cls = _clean_location_component(raw.get("class")).casefold()
    typ = _clean_location_component(raw.get("type") or raw.get("addresstype")).casefold()
    if cls == "place":
        return True
    if cls in ("boundary", "administrative"):
        return typ == "administrative"
    return False


def _geocode_namedetails_values(raw):
    """Nazwy/egzonimy z ``namedetails`` — wyłącznie dozwolone klucze.

    Bierzemy ``name``, ``int_name``, ``official_name``, ``alt_name`` oraz
    dowolne ``name:<kod>`` (np. ``name:pl`` = "Paryż" dla Paryża). Namedetails
    jest best-effort: gdy geopy go nie przyjmie, mapa go nie zwróci i walidacja
    działa dalej na pozostałych dowodach.
    """
    details = raw.get("namedetails")
    values = []
    if isinstance(details, dict):
        for key, value in details.items():
            key_text = _clean_location_component(key).casefold()
            if key_text in _GEOCODE_NAMEDETAILS_KEYS or key_text.startswith("name:"):
                cleaned = _clean_location_component(value)
                if cleaned:
                    values.append(cleaned)
    elif isinstance(details, str) and details.strip():
        values.append(details.strip())
    return values


def _geocode_raw_name_values(raw):
    """Surowe nazwy kandydata (raw["name"], namedetails, nagłówek display_name).

    Używane WYŁĄCZNIE jako fallback, gdy geokoder nie podał żadnych pól
    miejscowości — nigdy nie nadpisują sprzecznych danych z tych pól.
    """
    values = []
    name = _clean_location_component(raw.get("name"))
    if name:
        values.append(name)
    values.extend(_geocode_namedetails_values(raw))
    display = _clean_location_component(raw.get("display_name"))
    if display:
        values.append(display.split(",")[0].strip())
    return values


def _geocode_place_values(address):
    """Znormalizowane nazwy z pól miejscowości (dowód dopasowania)."""
    return [
        normalized
        for normalized in (
            _normalize_location_text(address.get(field))
            for field in _GEOCODE_PLACE_FIELDS
        )
        if normalized
    ]


def _geocode_context_values(raw, address):
    """Wszystko, co kandydat mówi o swoim kontekście (administracja i adres)."""
    values = [
        _normalize_location_text(address.get(field))
        for field in _GEOCODE_CONTEXT_FIELDS + _GEOCODE_EXTRA_CONTEXT_FIELDS
    ]
    values.append(_clean_location_component(address.get("country_code")).casefold())
    display = _clean_location_component(raw.get("display_name"))
    if display:
        values.extend(_normalize_location_text(part) for part in display.split(","))
    return [value for value in values if value]


def _geocode_piece_explained(piece, values):
    """Czy element kontekstu z zapytania da się wyjaśnić danymi kandydata.

    Wystarczy pełna fraza ALBO każdy osobny token — inaczej "Kościelna 41"
    byłby sprzecznością z adresem, który exactly tak się nazywa, tylko w dwóch
    polach (road + house_number).
    """
    if any(_geocode_name_matches(piece, value) for value in values):
        return True
    tokens = set(piece.split())
    corpus = set()
    for value in values:
        corpus.update(value.split())
    return bool(tokens) and tokens <= corpus


# ============================================================================
# ETAP 1.2 — DOPASOWANIE KONTEKSTU ADMINISTRACYJNEGO
# ============================================================================
# Kontekst czytamy z ``raw["address"]``. ``city``/``town``/``state_district``
# są TU wyłącznie polami kontekstu, ale NIE znikają z ``_GEOCODE_PLACE_FIELDS``
# (dowód nazwy miejscowości w etapie 1.1): wyjęcie ich stamtąd osłabiłoby
# walidację core, czego etap 1.2 nie robi.

def _geocode_admin_values(address):
    """Znormalizowane wartości pól administracyjnych kandydata."""
    return [
        normalized
        for normalized in (
            _normalize_location_text(address.get(field))
            for field in _GEOCODE_ADMIN_FIELDS
        )
        if normalized
    ]


def _geocode_admin_tokens(values):
    """Zbiór tokenów kontekstu administracyjnego."""
    tokens = set()
    for value in values:
        tokens.update(value.split())
    return tokens


def _geocode_common_prefix_len(first, second):
    """Długość wspólnego PREFIKSU — nie obcinamy końcówek, tylko porównujemy."""
    common = 0
    for left, right in zip(first, second):
        if left != right:
            break
        common += 1
    return common


def _geocode_admin_stem_matches(token, admin_tokens):
    """Kontrolowane dopasowanie wariantów administracyjnych, bez fuzzy matchingu.

    Wspólny rdzeń musi mieć co najmniej ``_GEOCODE_ADMIN_STEM_MIN_COMMON``
    znaków, a oba tokeny co najmniej ``_GEOCODE_ADMIN_STEM_MIN_TOKEN``. Dzięki
    temu przechodzą odmiany typu ``otwock``/``otwocki`` czy
    ``czestochowa``/``czestochowski``/``czestochowy``, a krótkie wartości
    (``wa``) i przypadkowe zbieżności (``warszawa``/``wiazowna``) odpadają.
    Porównujemy PREFIKS, więc nie da się sztucznie skrocic „otwocki" do „otwo".
    """
    if len(token) < _GEOCODE_ADMIN_STEM_MIN_TOKEN:
        return False
    for candidate in admin_tokens:
        if len(candidate) < _GEOCODE_ADMIN_STEM_MIN_TOKEN:
            continue
        if _geocode_common_prefix_len(token, candidate) >= _GEOCODE_ADMIN_STEM_MIN_COMMON:
            return True
    return False


def _geocode_admin_piece_matches(piece, admin_values):
    """Czy element kontekstu jest potwierdzony polami administracyjnymi.

    Najpierw pełna fraza („otwocki" w „powiat otwocki"), potem każdy token
    osobno — dokładnie albo kontrolowanym rdzeniem. Wielowyrazowy kontekst
    musi potwierdzić się W CAŁOŚCI: „Osiedle Batory" nie przejdzie przez samo
    „osiedle". Brak danych administracyjnych to brak potwierdzenia, nie
    przepustka.

    Element kontekstu jest normalizowany tą samą funkcją co wartości kandydata
    (casefold, diakrytyki, interpunkcja) — inaczej „Osiedle Parkowe" nigdy nie
    zrównałoby się z polem ``suburb``.
    """
    piece = _normalize_location_text(piece)
    if not piece:
        return True
    if not admin_values:
        return False
    if any(_geocode_name_matches(piece, value) for value in admin_values):
        return True
    tokens = piece.split()
    if not tokens:
        return True
    admin_tokens = _geocode_admin_tokens(admin_values)
    return all(
        token in admin_tokens or _geocode_admin_stem_matches(token, admin_tokens)
        for token in tokens
    )


def _geocode_context_conflicts(contexts, postcode, raw, address, admin_context=None):
    """True, jeśli jawny kontekst z zapytania przeczy danym kandydata.

    Brak danych nie jest sprzecznością (geokoder dla miejscowości nie zawsze
    wypełnia postcode), ale inna gmina, inne województwo albo inny kraj
    dyskwalifikuje kandydata — i to jest mechanizm rozstrzygania "Hel, PL".
    """
    # ETAP 1.2: reguła jest WARUNKOWA, nie alternatywą.
    #   * admin_context wykryty -> decyduje WYLACZNIE nowy filtr
    #     administracyjny. Stary matcher kontekstu (czyta też display_name)
    #     NIE ratuje kandydata, bo inaczej kontekst stalby sie miekką sugestią.
    #   * admin_context pusty   -> walidacja etapu 1.1, bez zmian.
    admin_context = [piece for piece in (admin_context or ()) if piece]

    if admin_context:
        admin_values = _geocode_admin_values(address)
        for piece in admin_context:
            if not _geocode_admin_piece_matches(piece, admin_values):
                return True
    elif contexts:
        values = _geocode_context_values(raw, address)
        if values:
            for piece in contexts:
                if not _geocode_piece_explained(piece, values):
                    return True
    if postcode:
        candidate_postcode = _normalize_location_text(address.get("postcode"))
        if candidate_postcode and candidate_postcode != postcode:
            return True
    return False


# Ta sama miejscowość wraca z Nominatima zwykle dwa razy: raz jako punkt
# (class=place), raz jako relacja granicy (class=boundary), a ich współrzędne
# różnią się o kilkaset metrów do kilku kilometrów. Bez tego liczyłbym "jedno
# miasto" jako dwa różne i każde duże miasto byłoby GEOCODE_UNCERTAIN. 10 km to
# widełki, które łączą punkt z jego granicą, a nadal rozróżniają dwie osady o
# tej samej nazwie leżące dalej od siebie.
_GEOCODE_SAME_PLACE_KM = 10.0


def _geocode_candidate_identity(raw, address, matched_name):
    """Klucz grupujący kandydatów: nazwa miejscowości + kraj."""
    country = (
        _clean_location_component(address.get("country_code")).casefold()
        or _normalize_location_text(address.get("country"))
    )
    return (matched_name, country)


def _geocode_same_place(first, second):
    """True, gdy dwa zaakceptowane wyniki leżą tak blisko siebie, że to ta sama miejscowość."""
    return (
        _geocode_distance_km(
            getattr(first, "latitude", None), getattr(first, "longitude", None),
            getattr(second, "latitude", None), getattr(second, "longitude", None),
        )
        or float("inf")
    ) <= _GEOCODE_SAME_PLACE_KM


def _geocode_distance_km(lat_a, lon_a, lat_b, lon_b):
    """Przybliżona odległość w kilometrach — dość dokładna, by porównać dwa wyniki.

    Celowo bez dodatkowych zależności: potrzebujemy odpowiedzi "czy to ten sam
    ośrodek", a nie precyzyjnego pomiaru geodezyjnego.
    """
    try:
        lat_a, lon_a, lat_b, lon_b = (
            float(lat_a), float(lon_a), float(lat_b), float(lon_b)
        )
    except (TypeError, ValueError):
        return None
    lat_span = (lat_b - lat_a) * 111.32
    lon_span = (lon_b - lon_a) * 111.32 * math.cos(math.radians((lat_a + lat_b) / 2))
    return (lat_span ** 2 + lon_span ** 2) ** 0.5


def _geocode_candidate_evidence(query_name, address, raw, postcode):
    """``(dopasowana nazwa, siła dowodu)`` albo ``("", None)``.

    Trzy siły dowodu (od najmocniejszej):
      * ``place``     — nazwa z PÓL MIEJSCOWOŚCI (tożsamość kandydata),
      * ``localized`` — egzonim z ``namedetails`` przy istniejących polach
                        miejscowości (np. ``name:pl`` = "Paryż" dla "Paris");
                        nie nadpisuje danych z pól miejscowości, więc zwracana
                        nazwa to nadal pole miejscowości,
      * ``display``   — tylko surowa nazwa / nagłówek ``display_name``, gdy
                        geokoder nie podał ŻADNEGO pola miejscowości. Dowód
                        słaby — stąd bezpiecznik ``GEOCODE_UNCERTAIN``.
    """
    if not query_name:
        # Sam kod pocztowy jest własnym dowodem (jak w etapie 1).
        return (postcode, _GEOCODE_EVIDENCE_PLACE) if postcode else ("", None)

    variants = _geocode_query_variants(query_name)
    place_values = _geocode_place_values(address)
    matched_place = next(
        (value for value in place_values if _geocode_name_matches_any(variants, value)), ""
    )
    if matched_place:
        return matched_place, _GEOCODE_EVIDENCE_PLACE

    localized = next(
        (
            normalized
            for normalized in (
                _normalize_location_text(value) for value in _geocode_namedetails_values(raw)
            )
            if _geocode_name_matches_any(variants, normalized)
        ),
        "",
    )
    if localized:
        # Tożsamością zostaje pole miejscowości ("Paris"), nie egzonim ("Paryż").
        return (place_values[0] if place_values else localized), _GEOCODE_EVIDENCE_LOCALIZED

    if place_values:
        # Pola miejscowości istnieją, ale nie potwierdzają zapytania — surowa
        # nazwa nie może nadpisać sprzecznych danych strukturalnych.
        return "", None

    for value in _geocode_raw_name_values(raw):
        normalized = _normalize_location_text(value)
        if _geocode_name_matches_any(variants, normalized):
            return normalized, _GEOCODE_EVIDENCE_DISPLAY
    return "", None


def _geocode_matched_place_name(query_name, address, raw, postcode):
    """Zachowany kontrakt etapu 1: sama dopasowana nazwa, bez siły dowodu."""
    return _geocode_candidate_evidence(query_name, address, raw, postcode)[0]


def _geocode_select_accepted(query, candidates, country_code=None, admin_context=None):
    """Validated top result: ``(status, kandydat)`` dla jednego zapytania.

    Kolejność Nominatima ma znaczenie DOPIERO po wszystkich filtrach: bierzemy
    pierwszego kandydata, który przeszedł filtr klasy, twardy filtr kraju
    (gdy zapytanie zawierało jawny kraj), dowód dopasowania nazwy i brak
    sprzecznego kontekstu. Zero zwalidowanych = ``GEOCODE_NO_MATCH``.

    ``GEOCODE_UNCERTAIN`` zostaje wyłącznie jako rzadki bezpiecznik: gdy dowód
    pochodzi TYLKO z surowej nazwy / nagłówka ``display_name`` (bez pól
    miejscowości i bez dopasowanych ``namedetails``) i geokoder zwrócił więcej
    niż jedno rozróżnialne miejsce. To nie jest normalna odpowiedź dla
    popularnych nazw — to sygnał, że dane są za słabe, by ufać top resultowi.

    ETAP 1.2 — ``admin_context`` (powiat/województwo/miejscowość nadrzędna) jest
    dodatkowym filtrem, aplikowanym PRZED walidacją nazwy miejscowości:
    najpierw odsiewamy kandydatów niepasujących kontekstem, potem dopiero
    sprawdzamy klasę, kraj i dowód nazwy. Kandydat, który nie pasuje kontekstem,
    odpada bez względu na to, jak dobrze pasuje nazwą — kontekst nie jest
    miękką sugestią i nie da się go obejść starym matcherem kontekstu.
    """
    query_name, contexts, postcode = _geocode_query_context(query)
    admin_context = [piece for piece in (admin_context or ()) if piece]
    accepted = []
    seen = []  # (tożsamość, kandydat) — by nie liczyć dwa razy tej samej miejscowości

    for location in candidates:
        raw = getattr(location, "raw", None)
        raw = raw if isinstance(raw, dict) else {}
        address = _nominatim_address_components(raw)

        # ETAP 1.2: jawny kontekst odsiewa NAJPIERW — zanim klasa, kraj i
        # dowód nazwy. Z admin_context rządzi wyłącznie nowy filtr
        # administracyjny; bez niego zostaje stara reguła etapu 1.1.
        if _geocode_context_conflicts(contexts, postcode, raw, address, admin_context):
            continue

        if not _geocode_candidate_is_safe(raw):
            continue

        # Twardy filtr: jawny kraj z zapytania musi się zgadzać z krajem
        # kandydata. Brak country_code przy jawnym kraju = odrzucenie.
        if country_code:
            candidate_country = _clean_location_component(
                address.get("country_code")
            ).casefold()
            if candidate_country != country_code:
                continue

        # Walidacja core pozostaje głównym warunkiem: kontekst nie może
        # sprawić, że nazwa dłuższego obiektu zacznie pasować.
        matched, strength = _geocode_candidate_evidence(query_name, address, raw, postcode)
        if not matched:
            continue

        identity = _geocode_candidate_identity(raw, address, matched)
        if any(
            known_identity == identity
            and _geocode_same_place(known_location, location)
            for known_identity, known_location in seen
        ):
            continue
        seen.append((identity, location))
        accepted.append((location, strength))

    if not accepted:
        return GEOCODE_NO_MATCH, None

    # HOTFIX (admin vs city): kandydat, którego JEDYNYM dowodem jest display
    # (np. "Greater London" z nazwy, bez pól miejscowości), nie wygrywa, gdy w
    # tych samych wynikach jest kandydat z dowodem place/localized (np. London
    # z address.city). Poza tym kolejność Nominatima bez zmian.
    strong = [
        location for location, strength in accepted
        if strength != _GEOCODE_EVIDENCE_DISPLAY
    ]
    if strong:
        return GEOCODE_OK, strong[0]

    # Same słabe dowody (tylko display): dotychczasowy bezpiecznik UNCERTAIN.
    if len(accepted) > 1:
        return GEOCODE_UNCERTAIN, None
    return GEOCODE_OK, accepted[0][0]


def _geocode_forward_candidates(query, lang="pl", country_code=None):
    """Pobiera kandydatów z geokodera: ``(status, lista)``.

    ``status`` jest różny od None tylko wtedy, gdy nie ma czego walidować.
    Obsługujemy każdy kształt odpowiedzi geopy: None, pustą listę, pojedynczy
    obiekt oraz listę obiektów; wyjątek transportowy to natychmiast
    ``GEOCODE_ERROR`` — bez ponawiania i bez udawania pustej listy.

    Parametry idą drabinką wariantów, bo różne wersje geopy znają różne nazwy:
    ``country_codes`` (2.5+) albo starsze ``countrycodes``, a ``namedetails``
    jest wyłącznie best-effort. ``TypeError`` geopy rzuca PRZED ciałem metody
    (nieznany kwargs), więc to nie jest kolejne żądanie sieciowe; przy
    wykrytym kraju wolimy zachować jego filtr niż namedetails.
    """
    attempts = []
    if country_code:
        attempts.extend([
            {"country_codes": country_code, "namedetails": True},
            {"countrycodes": country_code, "namedetails": True},
            {"country_codes": country_code},
            {"countrycodes": country_code},
        ])
    attempts.extend([{"namedetails": True}, {}])

    try:
        geolocator = Nominatim(user_agent="pogoda_world_bot")
    except Exception as e:
        print(f"Błąd wyszukiwania miasta po nazwie: {e}")
        return GEOCODE_ERROR, []

    results = None
    for extra in attempts:
        try:
            results = _timed_call(
                "nominatim.forward",
                geolocator.geocode,
                query,
                exactly_one=False,
                limit=GEOCODE_CANDIDATE_LIMIT,
                addressdetails=True,
                language=lang,
                **extra,
            )
            break
        except TypeError:
            # Geopy nie zna tego kwargs — następny wariant drabinki.
            continue
        except Exception as e:
            # Timeout, reset, HTTP 429: NIE ponawiamy i NIE udajemy pustej mapy.
            print(f"Błąd wyszukiwania miasta po nazwie: {e}")
            return GEOCODE_ERROR, []
    else:
        # Żaden wariant nie przeszedł walidacji kwargs geopy — to awaria
        # wywołania, nie brak wyników.
        return GEOCODE_ERROR, []

    if not results:
        return GEOCODE_NOT_FOUND, []
    if not isinstance(results, (list, tuple)):
        results = [results]
    candidates = [result for result in results if result is not None]
    if not candidates:
        return GEOCODE_NOT_FOUND, []
    return None, candidates


def geocode_city_accepted(city_name, lang="pl"):
    """Walidowane geokodowanie forward: ``(status, lat, lon, short, display)``.

    Jedyne źródło prawdy dla wszystkich ścieżek user-facing. Etykiety są budowane
    wyłącznie ze strukturalnych pól adresu AKCEPTOWANEGO kandydata (ten sam
    formatter co dotąd); surowy adres z ``location.address`` ani samo zapytanie
    nigdy nie wracają jako fallback.
    """
    query = str(city_name or "").strip()
    if _geocode_query_is_too_short(query):
        return GEOCODE_TOO_SHORT, None, None, None, None

    # ETAP 1.1: jawny kraj ("Hel PL", "Paryż, Francja") jest wyłącznie z
    # zapytania — nigdy z języka UI. Rdzeń pytamy bez sufiksu kraju, a kraj
    # podajemy geokoderowi i twardo filtrujemy po country_code kandydata.
    #
    # ETAP 1.2: ten sam parser oddaje też admin_context ("Wiązowna, otwock",
    # "Olsztyn koło Częstochowy"). Bez kontekstu zapytanie do geokodera jest
    # identyczne jak w etapie 1.1; z kontekstem dokładamy oczyszczony z
    # fillerów ("powiat", "koło") sufiks po przecinku.
    core_query, admin_context, country_code = _geocode_query_parse(query)
    if not core_query:
        return GEOCODE_TOO_SHORT, None, None, None, None

    forward_query = _geocode_geocoder_query(core_query, admin_context)
    base_status, candidates = _geocode_forward_candidates(
        forward_query, lang, country_code=country_code
    )
    if base_status == GEOCODE_NOT_FOUND and admin_context and forward_query != core_query:
        # Fallback core-only, gdy zapytanie kontekstowe wróciło pustką.
        # Kandydaci z fallbacku NIE dostają dyspensy: filtr admin_context
        # i twardy filtr kraju nadal obowiązują przy selekcji.
        base_status, candidates = _geocode_forward_candidates(
            core_query, lang, country_code=country_code
        )
    if base_status is not None:
        return base_status, None, None, None, None

    status, location = _geocode_select_accepted(
        core_query, candidates, country_code, admin_context
    )
    if status != GEOCODE_OK:
        return status, None, None, None, None

    raw = getattr(location, "raw", None)
    raw = raw if isinstance(raw, dict) else {}
    address = _nominatim_address_components(raw)
    mode = _location_mode_for_query(query, address)
    short_label, display_location = _format_public_location_parts(
        address, query=query, mode=mode, lang=lang
    )
    if not short_label or not display_location:
        short_label = FIELD_LOCATION_LABEL
        display_location = t_ui(lang, "location_field")

    try:
        lat, lon = float(location.latitude), float(location.longitude)
    except (TypeError, ValueError):
        # Kandydat przeszedł walidację nazwy, ale nie ma współrzędnych — nie ma
        # z czego zbudować pogody, więc traktujemy to jak brak wyniku.
        return GEOCODE_NOT_FOUND, None, None, None, None

    return GEOCODE_OK, lat, lon, short_label, display_location


def geocode_city_details_status(city_name, lang="pl"):
    """Statusowy adapter ``(lat, lon, display_location, status)``.

    Tego API używają WSZYSTKIE nowe ścieżki: /dzien, /teraz, /trend z
    argumentem, prompt PENDING_CITY, /miasto oraz tryb gościa (skróty i
    wzmianka @bot).
    """
    status, lat, lon, _short_label, display_location = geocode_city_accepted(
        city_name, lang
    )
    return lat, lon, display_location, status


def geocode_city_labels_status(city_name, lang="pl"):
    """Guest adapter (HOTFIX): ``(lat, lon, fallback_short, fallback_display, status)``.

    Sam status + obie bezpieczne etykiety (krótka i pełna) zaakceptowanego
    forward geokodowania — guest dostaje dokładnie te same dane, które /miasto
    przekazuje do ``_resolve_location_labels`` jako ``fallback_short_label`` /
    ``fallback_display_location``. Dzięki temu reverse i forward budują
    etykiety jednym, wspólnym mechanizmem, zamiast mieszać tytuł z reverse i
    opis z forward.
    """
    status, lat, lon, short_label, display_location = geocode_city_accepted(
        city_name, lang
    )
    return lat, lon, short_label, display_location, status


def geocode_city_details(city_name, lang="pl"):
    """Compatibility adapter: ``(lat, lon, safe_display_location, ok)``.

    Zostaje wyłącznie dla kompatybilności wstecznej. ``ok`` różni tylko awarię
    sieci, więc NIE wolno na nim opierać nowych decyzji — TOO_SHORT, NO_MATCH,
    UNCERTAIN i NOT_FOUND mają ``ok=True`` i puste współrzędne.
    """
    status, lat, lon, _short_label, display_location = geocode_city_accepted(
        city_name, lang
    )
    return lat, lon, display_location, status != GEOCODE_ERROR


def _geocode_city_public_details(city_name, lang="pl"):
    """Compatibility adapter: ``(lat, lon, short_label, display_location, ok)``.

    Both labels are built from allowlisted structured address fields of the
    accepted candidate. The formatted ``location.address`` string and the
    original query are never returned as a fallback. ``ok`` is False only for a
    transport failure — exactly the old contract of this adapter.
    """
    status, lat, lon, short_label, display_location = geocode_city_accepted(
        city_name, lang
    )
    return lat, lon, short_label, display_location, status != GEOCODE_ERROR


def get_coords_from_city(city_name, lang="pl"):
    """Guest/shortcut adapter: coordinates plus a safe public label, never raw address.

    Sygnatura pozostaje ``(lat, lon, display_location)`` — ścieżki, które muszą
    rozróżnić statusy, używają geocode_city_details_status.
    """
    _status, lat, lon, _short_label, display_location = geocode_city_accepted(
        city_name, lang
    )
    return lat, lon, display_location


def geocode_shortening_is_safe(query):
    """Czy skrócenie zapytania do pierwszych dwóch tokenów nie gubi kontekstu.

    Skrót ratunkowy gościa ("Nowy Jork super" -> "Nowy Jork") nie może zgubić
    jawnego kraju ani kodu pocztowego — inaczej "Hel, PL proszę" zamieniłoby
    się w "Hel" bez kraju i walidacja straciłaby jedyny dowód rozstrzygający.

    Helper siedzi w ``location_bot`` (tu mieszka wiedza o kraju i kodach
    pocztowych) i jest WSTRZYKIWANY do ``handle_guest_now``; sam guest handler
    nie musi znać parsera kraju.
    """
    raw = str(query or "").strip()
    if not raw:
        return False
    if "," in raw:
        return False

    tokens = raw.split()
    if len(tokens) <= 2:
        return True
    short = " ".join(tokens[:2])

    # ETAP 1.2: skrót nie może zgubić ani zmienić admin_context. Inaczej
    # "Olsztyn koło Częstochowy" stałoby się "Olsztyn koło" (kontekst znika,
    # filler zostaje) i geokoder odpowiedziałby zupełnie innym Olsztynem.
    _core, admin_context, _code = _geocode_query_parse(raw)
    if admin_context:
        _short_core, short_admin, _short_code = _geocode_query_parse(short)
        if short_admin != admin_context:
            return False

    _core, country_code = _geocode_query_country(raw)
    if country_code:
        _short_core, short_country = _geocode_query_country(short)
        if short_country != country_code:
            return False

    match = _GEOCODE_POSTCODE_RE.search(raw)
    if match and not _GEOCODE_POSTCODE_RE.search(short):
        return False
    return True


def _reverse_label_matches_query(short_label, query):
    """Czy reverse short_label pasuje do rdzenia query — tym samym matcherem
    (warianty/egzonimy + pełny token/fraza, bez prefiksów), którego używa
    walidacja forward. Brak query albo brak rdzenia = nic do porównania, więc
    traktujemy to jako "nie ma rozbieżności" (nie blokuje reverse).
    """
    if not query:
        return True
    core_name, _admin_context, _country_code = _geocode_query_parse(query)
    if not core_name:
        return True
    variants = _geocode_query_variants(core_name)
    if not variants:
        return True
    return _geocode_name_matches_any(variants, _normalize_location_text(short_label))


def _resolve_location_labels(
    lat, lon, lang, fallback_short_label=None, fallback_display_location=None,
    query=None, mode=None,
):
    """Resolve the separate short label and display-only location description.

    Reverse geocoding is preferred. Safe short/display labels from structured
    forward-geocoder fields are used only if reverse geocoding fails, has no
    usable components, OR (HOTFIX) technically succeeds but names a different
    place than the one the user typed (e.g. reverse returns an administrative
    area like "City of Westminster" for a query of "London") while a validated
    forward fallback for the SAME query exists. The user query itself is never
    used as a display fallback.
    """
    if query:
        short_label, display_location, status = get_location_details_from_coords(
            lat, lon, lang, query=query, mode=mode
        )
    else:
        short_label, display_location, status = get_location_details_from_coords(
            lat, lon, lang
        )
    fallback_short = _clean_location_component(fallback_short_label)
    fallback_display = _clean_location_component(fallback_display_location)
    has_forward_fallback = bool(fallback_short and fallback_display)

    if status == GEO_ERROR:
        if has_forward_fallback:
            return fallback_short, fallback_display, GEO_OK
        return None, None, GEO_ERROR

    if status == GEO_NO_CITY:
        if has_forward_fallback:
            return fallback_short, fallback_display, GEO_NO_CITY
        return FIELD_LOCATION_LABEL, t_ui(lang, "location_field"), GEO_NO_CITY

    # status == GEO_OK: reverse odpowiedział poprawnie technicznie, ale może
    # wskazywać inne miejsce niż to, o które pytał użytkownik (patrz docstring
    # powyżej). Zwalidowany forward fallback dla TEGO SAMEGO zapytania wygrywa
    # wyłącznie wtedy, gdy reverse short_label nie pasuje do rdzenia query.
    if has_forward_fallback and query and not _reverse_label_matches_query(
        short_label, query
    ):
        return fallback_short, fallback_display, GEO_OK

    return short_label, (display_location or short_label), GEO_OK

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

# Prefiksy skrótów trybu gościa (POPRAWKA #6).
# Zestaw jest teraz WYPROWADZANY z parsera guest_bot_handler, więc bramka
# dostępu i handler nie mogą się rozjechać. Zostaje jako publiczna stała dla
# zgodności wstecznej (stare testy/harnessy) — logika używa resolve_shortcut().
GUEST_SHORTCUT_PREFIXES = tuple(iter_shortcut_keys())
# ==============================================================

TELEGRAM_TOKEN = os.environ.get("TG_TOKEN")
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def _env_number(name, default, cast):
    try:
        return cast(os.environ.get(name, default))
    except (TypeError, ValueError):
        return cast(default)


PERF_SLOW_MS = max(0.0, _env_number("BOT_PERF_SLOW_MS", 500, float))
PERF_EVERY_N = max(0, _env_number("BOT_PERF_EVERY_N", 20, int))
SHEETS_CACHE_TTL_SECONDS = max(1.0, _env_number("BOT_SHEETS_CACHE_TTL_SECONDS", 30, float))
SHEETS_BACKOFF_SECONDS = (30, 60, 120)
_PERF_COUNTS = {}
_SHEETS_SUCCESSFUL_CALLS = 0
_SHEETS_BACKOFF_ATTEMPTS = 0
_GOOGLE_CLIENT = None
_POLL_RUNTIME = {"client": None, "offset": None}
_FORM_SNAPSHOT = {
    "client": None, "loaded_at": 0.0, "worksheet": None,
    "users_records": [], "headers": [], "clean_users": [],
}
_USERS_SNAPSHOT = {
    "client": None, "loaded_at": 0.0, "worksheet": None, "users_map": {},
}


class SheetsRateLimitError(RuntimeError):
    """Google Sheets/API 429 — przerwij bieżącą paczkę i odrocz polling."""

    def __init__(self, operation, original):
        self.operation = operation
        self.original = original
        super().__init__(f"{operation}: {original}")


def _is_sheets_429(exc):
    if isinstance(exc, SheetsRateLimitError):
        return True
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "code", None)
    if status == 429:
        return True
    # gspread/google-api-client versions differ in their response/status attributes.
    text = str(exc).lower()
    return "429" in text or "quota exceeded" in text or "resource has been exhausted" in text


def _log_perf(stage, started_at, slow=True):
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    sample = _PERF_COUNTS.get(stage, 0) + 1
    _PERF_COUNTS[stage] = sample
    if (slow and elapsed_ms >= PERF_SLOW_MS) or (PERF_EVERY_N and sample % PERF_EVERY_N == 0):
        # Intentionally only stage + elapsed time: no user IDs, queries, or coordinates.
        print(f"⏱ PERF stage={stage} duration_ms={elapsed_ms:.1f} sample={sample}")


def _timed_call(stage, function, *args, _perf_slow=True, **kwargs):
    started_at = time.perf_counter()
    try:
        return function(*args, **kwargs)
    finally:
        _log_perf(stage, started_at, slow=_perf_slow)


def _sheets_call(stage, function, *args, **kwargs):
    """Measure one Sheets operation and turn any HTTP 429 into a poll-level signal."""
    global _SHEETS_SUCCESSFUL_CALLS
    started_at = time.perf_counter()
    try:
        result = function(*args, **kwargs)
        _SHEETS_SUCCESSFUL_CALLS += 1
        return result
    except SheetsRateLimitError:
        raise
    except Exception as exc:
        if _is_sheets_429(exc):
            raise SheetsRateLimitError(stage, exc) from exc
        raise
    finally:
        _log_perf(stage, started_at)


def _raise_if_sheets_429(exc):
    """Do not let a broad legacy handler swallow a quota signal."""
    if isinstance(exc, SheetsRateLimitError):
        raise exc
    if _is_sheets_429(exc):
        raise SheetsRateLimitError("sheets.operation", exc) from exc


def _offset_file_path():
    configured = os.environ.get("BOT_OFFSET_FILE")
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    state_home = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return os.path.join(state_home, "pogoda-world", "location_bot_offset.json")


def _read_local_offset():
    try:
        with open(_offset_file_path(), "r", encoding="utf-8") as offset_file:
            payload = json.load(offset_file)
        value = payload.get("offset") if isinstance(payload, dict) else payload
        if isinstance(value, bool):
            raise ValueError("offset must be an integer")
        return int(value)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"  ⚠️ Nie mogę odczytać lokalnego offsetu; spróbuję Bot_State: {exc}")
        return None


def _write_local_offset(offset):
    path = _offset_file_path()
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".location-bot-offset-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as offset_file:
            json.dump({"offset": int(offset)}, offset_file)
            offset_file.flush()
            os.fsync(offset_file.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        except OSError:
            pass


def _read_offset_from_sheets(gc):
    state_sheet = gc.open("Pogoda_Users").worksheet("Bot_State")
    value = state_sheet.acell("B1").value
    return int(value) if value else 0


def get_google_client():
    """Create one reusable gspread client; no API request is made just by caching it."""
    global _GOOGLE_CLIENT
    if _GOOGLE_CLIENT is not None:
        return _GOOGLE_CLIENT
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly"
    ]
    creds_json = os.environ.get("GOOGLE_CREDS_JSON")
    if creds_json:
        creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=scopes)
    else:
        creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
    _GOOGLE_CLIENT = gspread.authorize(creds)
    return _GOOGLE_CLIENT


def _reset_runtime_for_client(gc):
    global _POLL_RUNTIME, _FORM_SNAPSHOT, _USERS_SNAPSHOT
    if _POLL_RUNTIME["client"] is gc:
        return
    _POLL_RUNTIME = {"client": gc, "offset": None}
    _FORM_SNAPSHOT = {
        "client": gc, "loaded_at": 0.0, "worksheet": None,
        "users_records": [], "headers": [], "clean_users": [],
    }
    _USERS_SNAPSHOT = {
        "client": gc, "loaded_at": 0.0, "worksheet": None, "users_map": {},
    }


def _reset_polling_state(clear_client=False):
    """Internal reset hook for isolated tests and controlled process reinitialization."""
    global _POLL_RUNTIME, _FORM_SNAPSHOT, _USERS_SNAPSHOT
    global _SHEETS_BACKOFF_ATTEMPTS, _SHEETS_SUCCESSFUL_CALLS, _GOOGLE_CLIENT
    _POLL_RUNTIME = {"client": None, "offset": None}
    _FORM_SNAPSHOT = {
        "client": None, "loaded_at": 0.0, "worksheet": None,
        "users_records": [], "headers": [], "clean_users": [],
    }
    _USERS_SNAPSHOT = {
        "client": None, "loaded_at": 0.0, "worksheet": None, "users_map": {},
    }
    _SHEETS_BACKOFF_ATTEMPTS = 0
    _SHEETS_SUCCESSFUL_CALLS = 0
    if clear_client:
        _GOOGLE_CLIENT = None


def get_offset(gc):
    """Load persisted offset locally; bootstrap from Bot_State only once if absent."""
    local_offset = _timed_call("offset.local_read", _read_local_offset)
    if local_offset is not None:
        return local_offset

    if gc is None:
        raise RuntimeError("Google client is required to bootstrap Bot_State offset")
    offset = _sheets_call("sheets.offset.bootstrap_read", _read_offset_from_sheets, gc)
    try:
        _timed_call("offset.local_write", _write_local_offset, offset)
    except OSError as exc:
        # Continue with the known in-memory offset; never replace a failed read by zero.
        print(f"  ⚠️ Nie udało się utrwalić lokalnego offsetu: {exc}")
    return offset


def save_offset(gc, offset):
    """Persist the Telegram offset atomically in local state (Bot_State is migration-only)."""
    try:
        _timed_call("offset.local_write", _write_local_offset, offset)
        return True
    except Exception as exc:
        print(f"  ⚠️ Błąd lokalnego zapisu offsetu: {exc}")
        return False


def _runtime_offset(gc):
    _reset_runtime_for_client(gc)
    if _POLL_RUNTIME["offset"] is None:
        # A 429 propagates; caller must not poll Telegram with offset=0.
        _POLL_RUNTIME["offset"] = _timed_call("offset.load", get_offset, gc)
    return _POLL_RUNTIME["offset"]


def _set_runtime_offset(gc, offset):
    _reset_runtime_for_client(gc)
    _POLL_RUNTIME["offset"] = int(offset)


def _snapshot_fresh(snapshot, gc):
    return (
        snapshot.get("client") is gc
        and snapshot.get("loaded_at", 0.0) > 0
        and time.monotonic() - snapshot["loaded_at"] < SHEETS_CACHE_TTL_SECONDS
    )


def _load_form_snapshot(gc):
    global _FORM_SNAPSHOT
    _reset_runtime_for_client(gc)
    if _snapshot_fresh(_FORM_SNAPSHOT, gc):
        return _FORM_SNAPSHOT

    worksheet = _FORM_SNAPSHOT.get("worksheet")
    if worksheet is None:
        worksheet = _sheets_call(
            "sheets.formularz.open",
            lambda: gc.open("Pogoda_Users").worksheet("Formularz"),
        )
    _FORM_SNAPSHOT["client"] = gc
    _FORM_SNAPSHOT["worksheet"] = worksheet
    values = _sheets_call(
        "sheets.formularz.read",
        worksheet.get_all_values,
        value_render_option="UNFORMATTED_VALUE",
    )
    if not values:
        raw_headers = []
        users_records = []
    else:
        raw_headers = list(values[0])
        if len(raw_headers) != len(set(raw_headers)):
            # Keep the former get_all_records duplicate-header safeguard.
            raise ValueError("Duplicate headers in Formularz")
        users_records = [
            dict(zip(raw_headers, numericise_all(row))) for row in values[1:]
        ]
    headers = [str(header).strip() for header in raw_headers]
    clean_users = wirtualne_scalanie(users_records)
    _FORM_SNAPSHOT = {
        "client": gc,
        "loaded_at": time.monotonic(),
        "worksheet": worksheet,
        "users_records": users_records,
        "headers": headers,
        "clean_users": clean_users,
    }
    return _FORM_SNAPSHOT


def _load_users_snapshot(gc):
    global _USERS_SNAPSHOT
    _reset_runtime_for_client(gc)
    if _snapshot_fresh(_USERS_SNAPSHOT, gc):
        return _USERS_SNAPSHOT

    users_ws = _USERS_SNAPSHOT.get("worksheet")
    if users_ws is None:
        users_ws = _sheets_call("sheets.users.open", users_store.get_ws, gc)
        # Retain the handle even if the values read is rate-limited; retrying after
        # backoff should not reopen the spreadsheet unnecessarily.
        _USERS_SNAPSHOT["client"] = gc
        _USERS_SNAPSHOT["worksheet"] = users_ws
    users_map = _sheets_call("sheets.users.read", users_store.load_users_map, users_ws)
    _USERS_SNAPSHOT = {
        "client": gc,
        "loaded_at": time.monotonic(),
        "worksheet": users_ws,
        "users_map": users_map,
    }
    return _USERS_SNAPSHOT


def _load_future_users_from_sheet(gc):
    """Strict, timed version of main_card's legacy loader for /future.

    Preserve its configured spreadsheet/tab fallback order, but let 429 escape
    immediately instead of silently trying another Sheets read in this poll.
    """
    sheet = None
    sheet_id = getattr(main_card, "SHEET_ID", "")
    sheet_name = getattr(main_card, "SHEET_NAME", "Pogoda_Users")
    tab_from_env = getattr(main_card, "SHEET_TAB_ENV", "")

    if sheet_id:
        try:
            sheet = _sheets_call(
                "sheets.legacy_future.open_by_key", gc.open_by_key, sheet_id
            )
        except Exception as exc:
            _raise_if_sheets_429(exc)
            print(f"[Google Sheets] Fallback: Nie udało się otworzyć po ID ({exc})")

    if not sheet:
        sheet = _sheets_call("sheets.legacy_future.open", gc.open, sheet_name)

    tab_candidates = ([tab_from_env] if tab_from_env else []) + [
        "Formularz", "Form_Responses4", "Arkusz1", "Sheet1"
    ]
    worksheet = None
    errors = []
    for tab in tab_candidates:
        try:
            worksheet = _sheets_call(
                "sheets.legacy_future.open_tab", sheet.worksheet, tab
            )
            break
        except gspread.exceptions.WorksheetNotFound:
            errors.append(tab)

    if worksheet is None:
        raise RuntimeError(f"Krytyczny błąd: Brak zakładek. Odrzucone: {', '.join(errors)}")

    values = _sheets_call(
        "sheets.legacy_future.read", worksheet.get_values, "A1:Z"
    )
    if not values:
        return []

    headers = values[0]
    return [
        dict(zip(headers, list(row) + [""] * max(0, len(headers) - len(row))))
        for row in values[1:]
    ]


def _cache_users_map(users_map):
    if _USERS_SNAPSHOT.get("client") is _POLL_RUNTIME.get("client"):
        _USERS_SNAPSHOT["users_map"] = users_map
        _USERS_SNAPSHOT["loaded_at"] = time.monotonic()


def _invalidate_form_snapshot():
    _FORM_SNAPSHOT["loaded_at"] = 0.0


def _invalidate_users_snapshot():
    _USERS_SNAPSHOT["loaded_at"] = 0.0


def _form_update_cell(main_sheet, row, col, value):
    result = _sheets_call("sheets.formularz.write_cell", main_sheet.update_cell, row, col, value)
    _invalidate_form_snapshot()
    return result

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
            try:
                _sheets_call(
                    "sheets.blocked_user_cleanup",
                    mark_user_as_blocked,
                    gc,
                    chat_id,
                    reason=powod,
                )
            finally:
                _invalidate_users_snapshot()
                _invalidate_form_snapshot()
            
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Błąd sieci podczas wysyłania wiadomości (Timeout/DNS): {e}")
        
        
def send_photo(chat_id, photo_path, caption=None, parse_mode="Markdown", card_caption=True):
    """Bezpośredni wysyłacz kart graficznych PNG dla trybu gościa i nie tylko.

    POPRAWKA #1: ``card_caption=False`` wysyła SAMĄ kartę — bez podpisu
    (caption) pod zdjęciem. Ścieżka Users (/day, /now, /future) korzysta z tego
    trybu w ``_send_oneoff_report``; tryb gościa wysyła kartę z adresem, więc
    dla niego parametr pozostaje domyślnie włączony.
    """
    try:
        with open(photo_path, "rb") as photo:
            payload = {"chat_id": chat_id}
            if card_caption and caption:
                payload["caption"] = caption
                if parse_mode:
                    payload["parse_mode"] = parse_mode
            requests.post(f"{BASE_URL}/sendPhoto", data=payload, files={"photo": photo}, timeout=15)
    except Exception as e:
        print(f"⚠️ Błąd wysyłania zdjęcia (sendPhoto) do {chat_id}: {e}")
        
        

def _main_bot_iteration(gc, offset):
    print("🤖 Uruchamiam system nasłuchiwania (Location Bot)...")

    try:
        # Timeout w 'params' to Long Polling (dla Telegrama).
        # Timeout=10 to zabezpieczenie gniazda sieciowego dla Pythona.
        resp = _timed_call(
            "telegram.getUpdates",
            requests.get,
            f"{BASE_URL}/getUpdates",
            params={"offset": offset, "timeout": 5},
            timeout=10,
            _perf_slow=False,  # Long-poll duration is expected; sample every N calls.
        )
        data = resp.json()
    except Exception as e:
        print(f"  ⚠️ Błąd sieci podczas nasłuchiwania Telegrama: {e}")
        return
    
    if not data.get("ok") or not data.get("result"):
        print("  📭 Cisza w eterze. Brak nowych wiadomości.")
        return

    updates = data["result"]
    print(f"  📬 Pobrano {len(updates)} nowych operacji do przetworzenia.")
    
    # Read each tab as one snapshot per TTL window. If either read gets a 429,
    # the exception aborts this iteration before further Sheets calls.
    form_snapshot = _load_form_snapshot(gc)
    users_snapshot = _load_users_snapshot(gc)
    main_sheet = form_snapshot["worksheet"]
    users_records = form_snapshot["users_records"]
    headers = form_snapshot["headers"]
    clean_users = form_snapshot["clean_users"]
    users_ws = users_snapshot["worksheet"]
    users_map = users_snapshot["users_map"]

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

            # POPRAWKA #7: dawny błyskawiczny język gościa (guest_lang) obsługiwał
            # odmowę no_access dla skrótów/wzmianek bez dostępu. Odmowa zniknęła
            # (bez dostępu jest cisza), więc zmienna nie jest już potrzebna.

            # ==============================================================
            # POPRAWKA #7 — BRAMKA DOSTĘPU LICZONA RAZ NA WIADOMOŚĆ
            # ==============================================================
            # Bez dostępu odpowiada WYŁĄCZNIE /start (rejestracja kodem albo
            # informacja no_access). Wszystko inne — komendy RODO, komendy
            # zwykłe, skróty i wzmianki, pinezki, WebApp i zwykły tekst — jest
            # ignorowane po cichu. Dzięki temu po hard delete czat bez wiersza
            # w rejestrze nie dostaje ani no_data, ani linku zaproszenia.
            chat_has_access = _chat_has_access(users_map, clean_users, chat_id)
            raw_text = (message.get("text") or "").strip()
            if not chat_has_access and not raw_text.startswith("/start"):
                continue

            # ==============================================================
            # 0A. TRYB GOŚCIA I SZYBKIE SKRÓTY (.n, .d, .f) — GATE PO ACCESS (PR1)
            # ==============================================================
            # Skróty .d/.n/.f i wzmianki @bot generują karty pogodowe, więc od PR1
            # wymagają dostępu. Brak dostępu został już obsłużony wyżej (cisza),
            # więc tutaj docierają wyłącznie czaty z dostępem.
            # ETAP 1: guest_bot_handler nadal sam nic nie wie o geokoderze —
            # dostaje tylko statusowy adapter z tego pliku.
            # HOTFIX (effective language): jeden język dla guesta — Users.lang
            # tego chat_id (grupa = własny rekord grupy), potem Telegram, en.
            # Przekazywany PRZED handle_guest_now jako override.
            guest_lang = _effective_lang(
                message, chat_id, users_map,
                legacy_row=_legacy_user_row(clean_users, chat_id),
            )
            is_guest = handle_guest_now(
                message=message,
                bot_username=BOT_USERNAME,
                effective_lang=guest_lang,
                get_coords_fn=get_coords_from_city,
                # ETAP 1: skróty i wzmianka @bot dostają TEN SAM zestaw statusów
                # co /dzien, /teraz, /trend, /miasto i prompty. Karta trybu gościa
                # powstaje wyłącznie dla GEOCODE_OK, a do cache trafia tylko OK.
                # HOTFIX: adapter zwraca też fallback_short/fallback_display
                # (zaakceptowany forward), żeby tytuł i opis karty gościa nie
                # mieszały źródeł — tak samo jak /miasto i jednorazowe raporty.
                geocode_status_fn=lambda city, lang: geocode_city_labels_status(city, lang),
                # HOTFIX: spójny resolver etykiet (reverse preferowany, ale nie
                # autorytatywny, gdy rozmija się z query) — DOKŁADNIE ten sam,
                # którego używa /miasto. Callback injection: guest_bot_handler
                # nie importuje location_bot.
                resolve_labels_fn=lambda lat, lon, lang, q, fs, fd: _resolve_location_labels(
                    lat, lon, lang,
                    fallback_short_label=fs,
                    fallback_display_location=fd,
                    query=q,
                ),
                # ETAP 1.1: skrót ratunkowy (pierwsze 2 tokeny) nie może zgubić
                # jawnego kraju ani kodu pocztowego — decyduje helper z tego pliku.
                shortening_ok_fn=geocode_shortening_is_safe,
                
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
                
                render_png_fn=lambda layout: _timed_call(
                    "card.render", image_generator.generate_weather_card, layout
                ),
                
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
                # Wspólny effective_lang: Users.lang > legacy Formularz > Telegram > en.
                user_lang = _effective_lang(
                    message, chat_id, users_map,
                    legacy_row=_legacy_user_row(clean_users, chat_id),
                )
                # -------------------------------------------------
                
                raw_data = wad.get("data", "")
                print(f"  [DEBUG-WEBAPP] Otrzymano czyste dane z WebApp: {raw_data}")
                
                try:
                    data = json.loads(raw_data)
                    if data.get("type") == "set_location":
                        lat = float(data.get("lat"))
                        lon = float(data.get("lon"))
                        print(f"  📍 Odebrano współrzędne GPS od {chat_id}: {lat}, {lon}")

                        # POPRAWKA #7: brak dostępu = cisza. Ten warunek jest
                        # już nieosiągalny (bramka na wejściu pętli wycisza
                        # WebApp bez dostępu) — zostaje jako pas bezpieczeństwa.
                        if not _chat_has_access(users_map, clean_users, chat_id):
                            continue

                        # POPRAWKA #4: pełny adres (albo teren/awaria łącz).
                        short_label, display_location, geo_status = _resolve_location_labels(
                            lat, lon, user_lang
                        )
                        if geo_status == GEO_ERROR:
                            send_reply(chat_id, t_ui(user_lang, "geo_conn_err"))
                            continue

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
                                    chat_id, users_ws, users_map, lat, lon, short_label,
                                    user_lang, "webapp", display_location=display_location,
                                )
                            else:
                                _run_oneoff_report(
                                    chat_id, lat, lon, short_label, user_lang, "webapp", "day",
                                    display_location=display_location,
                                )
                            continue

                        # --- niezmieniony legacy zapis do Formularz ---
                        rows_to_update = []
                        for idx, r in enumerate(users_records):
                            if str(r.get("Chat ID", "")).strip() == str(chat_id):
                                rows_to_update.append(idx + 2)
                        if not rows_to_update:
                            try:
                                cell = _sheets_call(
                                    "sheets.formularz.find",
                                    main_sheet.find,
                                    str(chat_id),
                                    in_column=2,
                                )
                                rows_to_update.append(cell.row)
                            except Exception as e:
                                _raise_if_sheets_429(e)
                                print("  [DEBUG-WEBAPP] Nie znalazłem usera legacy w bazie!")

                        if rows_to_update:
                            col_lat = headers.index("Lat") + 1
                            col_lon = headers.index("Lon") + 1
                            col_miasto = headers.index("Miasto") + 1 if "Miasto" in headers else None
                            for r_idx in rows_to_update:
                                _form_update_cell(main_sheet, r_idx, col_lat, lat)
                                _form_update_cell(main_sheet, r_idx, col_lon, lon)
                                if col_miasto:
                                    _form_update_cell(main_sheet, r_idx, col_miasto, short_label)
                            _invalidate_form_snapshot()

                        ukryj_klawiature = {"remove_keyboard": True}
                        send_reply(
                            chat_id, t_ui(user_lang, "loc_updated", city=short_label),
                            reply_markup=ukryj_klawiature,
                        )

                    elif data.get("type") == "set_settings":
                        # PR3 GRUPY: bot NIE oferuje już WebApp do zmiany godzin
                        # (przycisk z panelu /raport został usunięty — w grupach
                        # Telegram i tak zabrania reply-keyboard web_app).
                        # Ścieżka zostaje wyłącznie jako kompatybilność wsteczna
                        # dla starej strony webapp/index.html: zapis idzie przez
                        # ten sam helper users_store co dialog /raport.
                        rano = (data.get("rano") or "").strip()
                        wieczor = (data.get("wieczor") or "").strip()
                        print(f"  ⚙️ Odebrano nowe godziny od {chat_id}: Rano={rano}, Popołudnie={wieczor}")

                        # Puste pola WebApp oznaczają "bez zmian"; jawne godziny lub
                        # "brak" zapisujemy wyłącznie w Users. Formularz pozostaje legacy.
                        settings_saved = _sheets_call(
                            "sheets.users.set_report_settings",
                            users_store.set_report_settings,
                            users_ws, chat_id, rano, wieczor,
                        )
                        if not settings_saved:
                            print(f"  ⚠️ [report settings] Nie zapisano ustawień w Users dla {chat_id}.")
                            if _chat_has_access(users_map, clean_users, chat_id):
                                send_reply(chat_id, t_ui(user_lang, "no_profile_yet"))
                            continue

                        # Aktualizujemy mapę bieżącej paczki bez ponownego odczytu arkusza.
                        user_settings = users_map.get(users_store.norm_chat_id(chat_id))
                        if user_settings is not None:
                            if rano:
                                user_settings["report_morning_time"] = (
                                    "brak" if rano.lower() == "brak" else rano
                                )
                            if wieczor:
                                user_settings["report_afternoon_time"] = (
                                    "brak" if wieczor.lower() == "brak" else wieczor
                                )
                        _cache_users_map(users_map)

                        # Zamykamy klawiaturę WebApp i wysyłamy dotychczasowe potwierdzenie.
                        ukryj_klawiature = {"remove_keyboard": True}
                        try:
                            msg_to_send = t_ui(user_lang, "settings_saved")
                        except Exception:
                            msg_to_send = "✅ Ustawienia raportów zostały zapisane!"
                        send_reply(chat_id, msg_to_send, reply_markup=ukryj_klawiature)
                        continue
                        
                        
                        
                except SheetsRateLimitError:
                    raise
                except Exception as e:
                    send_reply(chat_id, "⚠️ Błąd zapisu lokalizacji z GPS. Spróbuj za chwilę.")
                    alert_admin(f"❌ Błąd aktualizacji GPS (WebApp) dla {chat_id}: {e}")
                
                # Zawsze przerywamy pętlę dla paczki GPS
                continue

            # ==============================================================
            # 0.5 KOMENDY PRYWATNOŚCI (RODO) — PR1 + aliasy PL (PR2 UX cleanup)
            # /privacy (/priv), /my_data (/dane), /forget_location (/bezGPS),
            # /delete_me oraz /delete_confirm (/usunDane) — kasacja zawsze pyta.
            # POPRAWKA #7: działają wyłącznie dla czatów z dostępem. Bez dostępu
            # (obcy, blocked/revoked, po hard delete) komendy RODO są ciszą —
            # czat bez wiersza w rejestrze nie ma czego wglądać ani usuwać.
            # ==============================================================
            text_priv = (message.get("text") or "").strip()
            priv_cmd = _match_command(text_priv, PRIVACY_COMMANDS)
            if chat_has_access and priv_cmd:
                priv_lang = _resolve_privacy_lang(message, chat_id, users_map, clean_users)
                print(f"  🛡 Komenda prywatności {priv_cmd} od {chat_id}")
                _handle_privacy(chat_id, priv_cmd, priv_lang, users_ws, main_sheet, users_map, clean_users)
                # Mutacje aktualizują lub unieważniają snapshot lokalnie; samo
                # /delete_me jedynie pyta o potwierdzenie i nie wymaga odczytu.
                continue

            # ==============================================================
            # 0.6 ODPOWIEDŹ NA PYTANIE O USUNIĘCIE DANYCH (bez callbacków)
            # Przyciski reply keyboard ("Tak, chcę" / "Nie, nie chcę") albo
            # ukryte komendy techniczne /potwierdzusun, /anulujusun.
            # POPRAWKA #7: pytanie o kasację zadaje wyłącznie czat z dostępem
            # (blok 0.5), więc tutaj też wystarczy sam dostęp — po hard delete
            # pending jest wyczyszczony, a czat nie ma już czym potwierdzać.
            # ==============================================================
            delete_cmd = _match_command(text_priv, DELETE_ANSWER_COMMANDS)
            if chat_has_access and (delete_cmd or _pending_delete_active(chat_id)):
                priv_lang = _resolve_privacy_lang(message, chat_id, users_map, clean_users)
                delete_confirmed = _delete_answer_is_confirmation(
                    chat_id, delete_cmd or "", text_priv, priv_lang
                )
                consumed = _handle_delete_answer(
                    chat_id, delete_cmd or "", text_priv, priv_lang, users_ws, main_sheet
                )
                if consumed:
                    print(f"  🗑 Odpowiedź na potwierdzenie usunięcia danych od {chat_id}")
                    # POPRAWKA #7 (Z7 — wyłącznie RAM): po hard delete usuwamy
                    # czat także z bieżącej mapy Users i z listy legacy w pamięci,
                    # żeby kolejne update'y z TEJ SAMEJ paczki nie widziały
                    # starego dostępu. Nie dotykamy arkusza ani migracji.
                    if delete_confirmed:
                        users_map.pop(users_store.norm_chat_id(chat_id), None)
                        clean_users = [
                            u for u in clean_users
                            if str(u.get("Chat ID", "")).strip() != str(chat_id)
                        ]
                        _cache_users_map(users_map)
                        _FORM_SNAPSHOT["clean_users"] = clean_users
                        _invalidate_form_snapshot()
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
            # --- JĘZYK (effective_lang): Users.lang > legacy Formularz > Telegram > en ---
            user_lang = _effective_lang(
                message, chat_id, users_map, legacy_row=user_data,
            )


            # ==============================================================
            # BRAMKA WEJŚCIOWA (PR1: dostęp z Users + legacy Formularz)
            # ==============================================================
            # access = Users(access_status=granted) LUB wiersz w legacy Formularz.
            # POPRAWKA #7: bramka na wejściu pętli wyciszyła już wszystko, co nie
            # jest /start, więc tutaj zostaje wyłącznie obsługa /start dla czatu
            # bez dostępu: bez kodu -> no_access, zły kod -> invalid_link,
            # limit miejsc -> limit_reached, poprawny kod -> rejestracja.
            has_legacy_row = user_row_index is not None

            if not chat_has_access:
                text = message.get("text", "").strip()
                wykryty_jezyk = get_user_lang(message)
                parts = text.split()

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
                    zapisano = _sheets_call(
                        "sheets.users.upsert_access",
                        users_store.upsert_access,
                        users_ws, chat_id, wykryty_jezyk, access_source,
                        users_store.now_iso(), PRIVACY_VERSION,
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
                    _cache_users_map(users_map)

                    # Onboarding PR1: privacy-first, bez klawiatury GPS
                    # (zapis lokalizacji dopiero od PR2 przez /save_location).
                    send_reply(chat_id, t_ui(wykryty_jezyk, "welcome_access"))

                    continue  # Rejestracja zrobiona, pomijamy resztę pętli dla tej wiadomości

                except SheetsRateLimitError:
                    raise
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

            # ==============================================================
            # 0.7 KONWERSACYJNA ZMIANA GODZIN RAPORTÓW (/raport, /report)
            # ==============================================================
            # Ten sam dialog w grupie i w czacie prywatnym, bez WebApp i bez
            # inline keyboard: /raport pokazuje aktualne godziny, pyta o ich
            # zachowanie, a potem zbiera dwie odpowiedzi. W grupie zmianę może
            # przeprowadzić wyłącznie administrator/creator. Stan żyje w RAM
            # (jak PENDING_CITY) i wygasa po PENDING_REPORT_TTL_SEC.
            if _handle_report_settings_dialog(
                message=message,
                chat_id=chat_id,
                lang=user_lang,
                users_map=users_map,
                users_ws=users_ws,
            ):
                continue

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
                    # POPRAWKA #4: pełny adres (albo teren/awaria łącz).
                    short_label, display_location, geo_status = _resolve_location_labels(
                        lat, lon, user_lang
                    )
                    if geo_status == GEO_ERROR:
                        send_reply(chat_id, t_ui(user_lang, "geo_conn_err"))
                        continue
                    if pin_in_city_flow:
                        PENDING_CITY.pop(str(chat_id), None)
                        _save_profile_from_location(
                            chat_id, users_ws, users_map, lat, lon, short_label, user_lang, "gps",
                            display_location=display_location,
                        )
                    else:
                        _run_oneoff_report(
                            chat_id, lat, lon, short_label, user_lang, "gps", "day",
                            display_location=display_location,
                        )
                    continue

                try:
                    user_row_index = None
                    for idx, r in enumerate(users_records):
                        if str(r.get("Chat ID", "")).strip() == str(chat_id):
                            if str(r.get("Imię", "")).strip() != "":
                                user_row_index = idx + 2  
                                break
                    
                    if not user_row_index:
                        komorka = _sheets_call(
                            "sheets.formularz.find",
                            main_sheet.find,
                            str(chat_id),
                            in_column=2,
                        )
                        user_row_index = komorka.row
                    
                    col_lat = headers.index("Lat") + 1
                    col_lon = headers.index("Lon") + 1
                    
                    _form_update_cell(main_sheet, user_row_index, col_lat, lat)
                    _form_update_cell(main_sheet, user_row_index, col_lon, lon)
                    
                    city = get_city_from_coords(lat, lon, user_lang)
                    if city == "Lokalizacja w terenie" or not city:
                        city = "Twoja okolica"
                        
                    if "Miasto" in headers:
                        try:
                            col_miasto = headers.index("Miasto") + 1
                            _form_update_cell(main_sheet, user_row_index, col_miasto, city)
                        except Exception as e:
                            _raise_if_sheets_429(e)
                            print(f"  [DEBUG] Nie udało się zapisać miasta do arkusza: {e}")
                            
                    _invalidate_form_snapshot()
                    #send_reply(chat_id, f"✅ *Lokalizacja zaktualizowana!*\n\n📍 Rozpoznano: {city}\n🌤️ Od następnego raportu pogoda będzie liczona dla tego miejsca. ")
                    send_reply(chat_id, t_ui(user_lang, "loc_updated", city=city))
                except SheetsRateLimitError:
                    raise
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
            # PR3 GRUPY: panel godzin jest już wyłącznie dialogiem tekstowym
            # (blok 0.7 wyżej) — ten sam ekran w grupie i w czacie prywatnym.
            # Stary panel z przyciskiem WebApp został usunięty: Telegram nie
            # pozwala na reply-keyboard web_app w grupach, a prywatny zapis
            # godzin przez WebApp nie jest już oferowany. /miasto (GPS)
            # korzysta z WebApp nadal — bez żadnych zmian.

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
                except SheetsRateLimitError:
                    raise
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
                except SheetsRateLimitError:
                    raise
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
                    raw = _load_future_users_from_sheet(gc)
                    sklejone = wirtualne_scalanie(raw)
                    users = _parse_users(sklejone)
                    user = next((u for u in users if str(u["chat_id"]) == str(chat_id)), None)
                    
                    if user and user.get("lat") and user.get("lon"):
                        sukces = main_card._send_card_to_user(user, is_quiet=False, is_now=False, is_future=True)
                        if not sukces:
                            send_reply(chat_id, "❌ Wystąpił problem wewnętrzny. Karta nie została wysłana.")
                    else:
                        send_reply(chat_id, "❌ Najpierw musisz ustawić lokalizację (wyślij Pinezkę).")
                except SheetsRateLimitError:
                    raise
                except Exception as e:
                    _raise_if_sheets_429(e)
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
            # ETAP 1: /miasto X oraz sam tekst wpisany po prompcie (/miasto, /dzien,
            # /teraz, /trend) przechodzą przez TĘ SAMĄ walidację co reszta ścieżek.
            if _geocode_query_is_too_short(city_query):
                # 1-2 znaki bez kontekstu (kraj po przecinku albo kod pocztowy)
                # to nie jest zapytanie do mapy. Kontekstu NIE przywracamy: stan
                # oczekiwania zostaje zdjęty tak jak przy każdej innej głupocie.
                send_reply(chat_id, t_geocode(user_lang, GEOCODE_TOO_SHORT))
                continue

            send_reply(chat_id, t_ui(user_lang, "search_loc"))

            (
                geocode_status, lat, lon,
                fallback_short_label, fallback_display_location,
            ) = geocode_city_accepted(city_query, user_lang)

            if geocode_status != GEOCODE_OK:
                # POPRAWKA #4: awaria serwera map != brak wyników. ETAP 1 dokłada
                # NO_MATCH (nazwa nie pasuje) i UNCERTAIN (kilka miejsc o tej samej
                # nazwie). Żaden status poza GEOCODE_OK niczego nie zapisuje.
                send_reply(chat_id, t_geocode(user_lang, geocode_status))
                continue

            if lat is not None and lon is not None:
                print(f"  📍 Znaleziono po nazwie: {city_query} -> {lat}, {lon}")

                # PR2 UX cleanup: KONTEKST decyduje, co robimy z wpisaną miejscowością.
                #   oneoff_*      -> karta jednorazowa + stopka "użyta lokalizacja"
                #   save_profile  -> świadomy zapis profilu w Users, BEZ karty
                # Legacy-only (brak wiersza w Users) zachowuje dotychczasowy zapis
                # do Formularz — bez migracji i bez zmiany schedulera.
                if not _is_legacy_only_user(users_map, clean_users, chat_id):
                    # POPRAWKA #4: etykieta + opis lokalizacji z jednego miejsca.
                    # Ratujemy się danymi z forward-geocode, więc chwilowa awaria
                    # reverse nie gubi wpisanej przez użytkownika nazwy.
                    short_label, display_location, geo_status = _resolve_location_labels(
                        lat, lon, user_lang,
                        fallback_short_label=fallback_short_label,
                        fallback_display_location=fallback_display_location,
                        query=city_query,
                    )
                    if geo_status == GEO_ERROR:
                        send_reply(chat_id, t_ui(user_lang, "geo_conn_err"))
                        continue

                    oneoff_card_type = ONEOFF_CARD_TYPES.get(pending_ctx or "")
                    if oneoff_card_type:
                        if oneoff_card_type == "day" and _day_card_window_blocked(chat_id, user_lang, lat, lon):
                            continue
                        _run_oneoff_report(
                            chat_id, lat, lon, short_label, user_lang, "city",
                            oneoff_card_type, display_location=display_location,
                        )
                        continue

                    _save_profile_from_location(
                        chat_id, users_ws, users_map, lat, lon, short_label,
                        user_lang, "city", display_location=display_location,
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
                        komorka = _sheets_call(
                            "sheets.formularz.find",
                            main_sheet.find,
                            str(chat_id),
                            in_column=2,
                        )
                        real_row_index = komorka.row

                    # Czysta aktualizacja współrzędnych i nazwy w Arkuszu (bez żadnych stanów techniczych!)
                    col_lat = headers.index("Lat") + 1
                    col_lon = headers.index("Lon") + 1
                    _form_update_cell(main_sheet, real_row_index, col_lat, lat)
                    _form_update_cell(main_sheet, real_row_index, col_lon, lon)
                    
                    krotka_nazwa = get_city_from_coords(lat, lon, user_lang)
                    if krotka_nazwa in (FIELD_LOCATION_LEGACY, "", None, "Nieznana miejscowość"):
                        krotka_nazwa = fallback_short_label
                    krotka_nazwa = krotka_nazwa or FIELD_LOCATION_LABEL

                    if "Miasto" in headers:
                        col_miasto = headers.index("Miasto") + 1
                        _form_update_cell(main_sheet, real_row_index, col_miasto, krotka_nazwa)
                        
                    _invalidate_form_snapshot()
                    safe_display = _md_safe(fallback_display_location) or _md_safe(krotka_nazwa)
                    sukces_msg = t_ui(
                        user_lang, "search_success", display_location=safe_display
                    )
                    send_reply(chat_id, sukces_msg)
                    
                except SheetsRateLimitError:
                    raise
                except Exception as e:
                    send_reply(chat_id, t_ui(user_lang, "search_err"))
                    alert_admin(f"❌ Błąd aktualizacji miasta: {e}")
            else:
                # Jeśli geokodowanie się nie udało, nie przywracamy stanu. User może kliknąć /miasto z menu jeszcze raz.
                send_reply(chat_id, t_ui(user_lang, "search_fail"))

        except SheetsRateLimitError:
            # Confirm already completed updates, but leave the failed update pending.
            # save_offset is local/atomic, so this does not issue another Sheets call.
            failed_update_offset = int(update["update_id"])
            if failed_update_offset > offset:
                save_offset(gc, failed_update_offset)
                _set_runtime_offset(gc, failed_update_offset)
            raise
        except Exception as e:
            print(f"❌ Krytyczny błąd podczas przetwarzania wiadomości od {chat_id}: {e}")

    if highest_update_id > offset:
        save_offset(gc, highest_update_id)
        _set_runtime_offset(gc, highest_update_id)
    print("✅ Pamięć bota zaktualizowana. Koniec pracy.")


def _handle_sheets_429(exc):
    global _SHEETS_BACKOFF_ATTEMPTS
    index = min(_SHEETS_BACKOFF_ATTEMPTS, len(SHEETS_BACKOFF_SECONDS) - 1)
    delay = SHEETS_BACKOFF_SECONDS[index]
    _SHEETS_BACKOFF_ATTEMPTS += 1
    print(
        f"⚠️ Google Sheets 429 (stage={exc.operation}); wstrzymuję polling na {delay}s. "
        "Offset zachowany, dalsze odczyty w tej iteracji pominięte."
    )
    time.sleep(delay)


def main_bot():
    """Run one poll; turn Sheets 429 into a controlled delay, never an offset=0 poll."""
    global _SHEETS_BACKOFF_ATTEMPTS
    calls_before = _SHEETS_SUCCESSFUL_CALLS
    try:
        gc = get_google_client()
        offset = _runtime_offset(gc)
        _main_bot_iteration(gc, offset)
    except SheetsRateLimitError as exc:
        _handle_sheets_429(exc)
        return True
    else:
        # Reset only after a successful Google Sheets operation, not just an idle Telegram poll.
        if _SHEETS_SUCCESSFUL_CALLS > calls_before:
            _SHEETS_BACKOFF_ATTEMPTS = 0
        return False


if __name__ == "__main__":
    print("🚀 Startuje całodobowy nasłuch...")
    while True:
        try:
            backed_off = main_bot()
        except Exception as e:
            print(f"⚠️ Krytyczny błąd w głównej pętli: {e}")
            backed_off = False
        if not backed_off:
            time.sleep(2)
