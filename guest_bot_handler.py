import os
import re
import time
import logging
from typing import Optional, Tuple, Dict, Any

# Zmienne globalne (Cache in-memory bez zewnętrznych zależności)
_GUEST_RL: Dict[int, float] = {}              # chat_id -> timestamp następnego dozwolonego wywołania
_GUEST_RL_SEC: int = 60                       # Limit: 1 wywołanie na 60 sekund na czat
_GUEST_DEDUPE: Dict[Tuple[int, int], Tuple[float, bool]] = {}  # (chat_id, msg_id) -> (expires_ts, True)
_GUEST_DEDUPE_SEC: int = 600                  # Okres deduplikacji na wypadek retries ze strony Telegram API
_GUEST_FORECAST_CACHE: Dict[Tuple[float, float], Tuple[float, Dict[Any, Any]]] = {} # (lat3, lon3) -> (expires_ts, payload)
_GUEST_FORECAST_TTL: int = 300                # Cache danych pogodowych na 5 minut

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HELPERY DO ZARZĄDZANIA PAMĘCIĄ PODRĘCZNĄ (TTL CACHE)
# ---------------------------------------------------------------------------
def _ttl_get(cache_dict: dict, key: any) -> Optional[any]:
    """Pobiera element z pamięci podręcznej i automatycznie usuwa przeterminowane wpisy."""
    now = time.time()
    item = cache_dict.get(key)
    if not item:
        return None
    exp, val = item
    if now >= exp:
        cache_dict.pop(key, None)
        return None
    return val

def _ttl_set(cache_dict: dict, key: any, val: any, ttl: int) -> None:
    """Zapisuje element do pamięci podręcznej z określoną żywotnością (TTL)."""
    cache_dict[key] = (time.time() + ttl, val)

def _guest_rate_limited(chat_id: int) -> bool:
    """Sprawdza i aktualizuje limit wywołań (Rate-Limit) dla danego czatu."""
    now = time.time()
    nxt = _GUEST_RL.get(chat_id, 0)
    if now < nxt:
        return True
    _GUEST_RL[chat_id] = now + _GUEST_RL_SEC
    return False

def _guest_deduped(chat_id: int, message_id: int) -> bool:
    """Zapobiega ponownemu przetwarzaniu tej samej wiadomości w przypadku ponownych wysyłek ze strony API."""
    k = (chat_id, message_id)
    if _ttl_get(_GUEST_DEDUPE, k) is not None:
        return True
    _ttl_set(_GUEST_DEDUPE, k, True, _GUEST_DEDUPE_SEC)
    return False

def _extract_query_from_mention(text: str, bot_username: str) -> str:
    """
    Bezpiecznie usuwa @wzmiankę z treści wiadomości wraz z wiodącymi znakami
    interpunkcyjnymi (np. ':', '-', '—') za pomocą jednego przejścia RegEx.
    """
    if not text:
        return ""
    pattern = re.compile(rf"@{re.escape(bot_username)}\b[\s:–—]*", re.IGNORECASE)
    return pattern.sub("", text).strip()

# ---------------------------------------------------------------------------
# GŁÓWNY HANDLER TRYBU GOŚCIA (/now)
# ---------------------------------------------------------------------------
def handle_guest_now(
    message: dict, 
    bot_username: str,
    # Haki (Hooks) - wstrzykiwane zależności z Twojego głównego systemu:
    get_coords_fn,
    build_payload_fn,
    prepare_layout_fn,
    render_png_fn,
    send_photo_fn,
    send_reply_fn
) -> bool:
    """
    Jednorazowy handler w trybie gościa. Zwraca True, jeżeli wiadomość została obsłużona 
    jako interakcja gościnna (lub świadomie zignorowana ze względu na limity/błędy).
    Zwraca False, jeżeli wiadomość nie była wywołaniem @mention bota.
    """
    if not message:
        return False

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return False

    msg_id = message.get("message_id")
    if msg_id is not None and _guest_deduped(chat_id, msg_id):
        return True  # Wiadomość już przetworzona w ostatnich 10 minutach

    text = (message.get("text") or "").strip()
    
    # 1. Weryfikacja, czy wiadomość zawiera wzmiankę @bota
    if f"@{bot_username.lower()}" not in text.lower():
        return False

    # 2. Ochrona przed spamem (Rate-Limit per czat)
    if _guest_rate_limited(chat_id):
        logger.warning(f"[GuestMode] Rate-limited wywołanie w czacie: {chat_id}")
        return True

    # 3. Odporna normalizacja kodu językowego
    raw_lang = str(((message.get("from") or {}).get("language_code") or "en")).split("-")[0].strip().lower()
    user_lang = raw_lang[:2]
    if user_lang == "no":
        user_lang = "nb"

    # 4. Ustalenie współrzędnych geograficznych (priorytet ma odpowiedź z lokalizacją)
    reply = message.get("reply_to_message") or {}
    loc = reply.get("location")
    
    if loc and "latitude" in loc and "longitude" in loc:
        lat = float(loc["latitude"])
        lon = float(loc["longitude"])
    else:
        query = _extract_query_from_mention(text, bot_username)
        if not query:
            send_reply_fn(chat_id, "Podaj miasto lub kod pocztowy po @nazwie_bota.")
            return True

        try:
            lat, lon, _full = get_coords_fn(query, user_lang)
            if not lat or not lon:
                send_reply_fn(chat_id, f"Nie znaleziono lokalizacji '{query}'. Podaj inne miasto lub kod pocztowy.")
                return True
        except Exception as e:
            logger.error(f"[GuestMode] Błąd geokodowania dla '{query}': {e}")
            send_reply_fn(chat_id, "Wystąpił chwilowy problem ze znalezieniem lokalizacji. Spróbuj ponownie za chwilę.")
            return True

    # 5. Pobranie danych pogodowych z uwzględnieniem pamięci podręcznej
    key = (round(lat, 3), round(lon, 3))
    payload = _ttl_get(_GUEST_FORECAST_CACHE, key)
    
    if payload is None:
        try:
            # POBIERANIE BEZSTAWE: Brak odczytów z Google Sheets i zapisów w bazie
            payload = build_payload_fn(lat, lon, lang=user_lang, is_now=True)
            _ttl_set(_GUEST_FORECAST_CACHE, key, payload, _GUEST_FORECAST_TTL)
        except Exception as e:
            logger.error(f"[GuestMode] Błąd generowania payloadu dla coords {key}: {e}")
            send_reply_fn(chat_id, "Nie udało się pobrać aktualnych danych pogodowych. Spróbuj ponownie.")
            return True

    # 6. Generowanie widoku, renderowanie karty PNG i wysyłka
    png_path = None
    try:
        layout = prepare_layout_fn(payload)
        png_path = render_png_fn(layout)
        send_photo_fn(chat_id, png_path)
    except Exception as e:
        logger.error(f"[GuestMode] Błąd renderowania/wysyłki karty: {e}")
        send_reply_fn(chat_id, "Wystąpił błąd przy generowaniu karty pogodowej.")
    finally:
        # PROGRAMOWANIE DEFENSYWNE: Bezpieczne czyszczenie dysku na serwerze
        if png_path and os.path.exists(png_path):
            try:
                os.remove(png_path)
            except OSError as os_err:
                logger.error(f"[GuestMode] Nie udało się usunąć pliku {png_path}: {os_err}")

    return True