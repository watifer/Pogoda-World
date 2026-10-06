import os
import re
import time
import logging

# ETAP 1: walidacja zapytania geokodera mieszka w location_bot, ale jej
# SŁOWNIK STATUSÓW jest w i18n — dzięki temu tryb gościa widzi te same wartości
# bez importu location_bot (byłby cykliczny). import i18n nie ciągnie żadnych
# zależności sieciowych, więc tryb gościa nadal działa samodzielnie.
from i18n import t_geocode, GEOCODE_OK, GEOCODE_NO_MATCH, GEOCODE_NOT_FOUND

logger = logging.getLogger(__name__)

# ============================================================================
# SKRÓTY (POPRAWKA #6) — trzy warstwy, rozpoznawanie po PIERWSZYM TOKENIE
# ============================================================================
# Warstwy (kolejność rozpoznawania ma znaczenie):
#   1. GLOBALNE          ?12 -> 12 godzin (now), ?14 -> trend 14 dni (future),
#   2. DZIENNY LOKALNY   ?d (pl/en/es/no), ?t (de), ?j (fr) -> karta dzienna,
#   3. LEGACY (ukryte)   ?n -> 12h, ?f -> trend, ?p -> 12h, ?d -> dzienna.
#
# Kropka jest równoważna pytajnikowi w każdej warstwie (.14, .t, .n ...).
# Rozpoznajemy PIERWSZY TOKEN: "?14 Warszawa" to skrót "?14" + argument
# "Warszawa" (a nie "?1" + "4 Warszawa"). Zlepiona forma "?dWarszawa" też
# działa — to zachowanie historyczne, wymagane dla zgodności wstecznej.

LOCAL_DAILY_SHORTCUT = {
    "pl": "d",   # dzień
    "en": "d",   # day
    "de": "t",   # Tag
    "es": "d",   # día
    "fr": "j",   # jour
    "no": "d",   # dag
}

GLOBAL_SHORTCUTS = {
    "12": "now",
    "14": "future",
}

# Dzienne skróty wszystkich języków — użytkownik z innym językiem nie trafi
# w ciszę, np. Polak piszący "?t Berlin" też dostanie kartę dzienną.
DAILY_SHORTCUT_CODES = ("d", "t", "j")

LEGACY_SHORTCUTS = {
    "n": "now",
    "f": "future",
    "p": "now",   # kompatybilność ze starymi klawiaturami
}

SHORTCUT_LEADS = ("?", ".")


def _shortcut_layers():
    """Warstwy skrótów w kolejności rozpoznawania: globalne, dzienne, legacy."""
    return (
        tuple(GLOBAL_SHORTCUTS.items()),
        tuple((code, "day") for code in DAILY_SHORTCUT_CODES),
        tuple(LEGACY_SHORTCUTS.items()),
    )


def iter_shortcut_keys():
    """Wszystkie rozpoznawane klucze skrótów (?14, .d, ?n ...) — do bramki dostępu."""
    for layer in _shortcut_layers():
        for code, _card_type in layer:
            for lead in SHORTCUT_LEADS:
                yield f"{lead}{code}"


def resolve_shortcut(text):
    """Zwraca (prefix, card_type, query) albo (None, None, None).

    Rozpoznanie idzie po PIERWSZYM TOKENIE: token musi być równy kluczowi,
    a cała reszta wiadomości po nim jest argumentem (nazwą miejscowości).
    Dopiero jako fallback sprawdzamy formę zlepioną ("?dWarszawa"), bo taka
    działała u użytkowników od początku.
    """
    raw = (text or "").strip()
    if not raw or raw.startswith("/"):
        return None, None, None

    low = raw.lower()
    first_token = re.split(r"\s+", low, maxsplit=1)[0]

    for layer in _shortcut_layers():
        for code, card_type in layer:
            for lead in SHORTCUT_LEADS:
                key = f"{lead}{code}"
                if first_token == key:
                    return key, card_type, raw[len(key):].strip()

    for layer in _shortcut_layers():
        for code, card_type in layer:
            for lead in SHORTCUT_LEADS:
                key = f"{lead}{code}"
                if low.startswith(key) and len(low) > len(key):
                    return key, card_type, raw[len(key):].strip()

    return None, None, None


# ============================================================================
# HELPERY TEKSTOWE I GEOKODUJĄCE
# ============================================================================
# --- PAMIĘĆ PODRĘCZNA (CACHE) DLA GEOMETRII I NAZW ---
_GEO_CACHE = {}      # (lang, query_lower) -> (expiry_time, (lat, lon, full_address))
_GEO_TTL = 3600      # 1 godzina

_CITY_CACHE = {}     # (lat_rounded, lon_rounded, lang) -> (expiry_time, city_name)
_CITY_TTL = 86400    # 24 godziny

def _ttl_get(cache_dict, key):
    """Pobiera z cache jeśli nie wygasł TTL."""
    if key in cache_dict:
        exp, val = cache_dict[key]
        if time.time() < exp:
            return val
        else:
            del cache_dict[key]
    return None

def _ttl_set(cache_dict, key, val, ttl_seconds):
    """Zapisuje w cache z czasem wygaśnięcia."""
    cache_dict[key] = (time.time() + ttl_seconds, val)

def _extract_query_from_mention(text: str, bot_username: str) -> str:
    if not text:
        return ""

    mention = f"@{bot_username.lower()}"
    low = text.lower()
    idx = low.find(mention)
    if idx == -1:
        return ""

    after = text[idx + len(mention):]
    after = re.sub(r"^[\s:–—,]+", "", after).strip()
    after = re.split(r"[!\n\r\(\)\[\]\{\}]", after, maxsplit=1)[0].strip()

    # Stopwordy komentarza (PL + kilka podstawowych)
    stop = {
        "tak","będzie","bedzie","dziś","dzis","dzisiaj","jutro",
        "today","tomorrow","now","heute","morgen","hoy","mañana","aujourd'hui"
    }

    toks = after.split()
    kept = []
    for tok in toks:
        t = tok.lower().strip(".,;:!?")
        if t in stop:
            break
        kept.append(tok)
        if len(kept) >= 4:
            break

    return " ".join(kept).strip()


def _clean_location_query(q: str) -> str:
    """Sanitizer z inteligentnym przecinkiem (dopuszcza kody i nazwy krajów)."""
    q = (q or "").strip()
    
    for sep in (" — ", " – ", " - "):
        if sep in q:
            q = q.split(sep, 1)[0].strip()
            
    if "," in q:
        parts = q.split(",", 1)
        miasto = parts[0].strip()
        reszta = parts[1].strip()
        ilosc_slow = len(reszta.split())
        
        if 0 < ilosc_slow <= 2:
            q = f"{miasto}, {reszta}"
        else:
            q = miasto
            
    # --- NOWOŚĆ: ucinamy komentarze po spójnikach (bez NLP) ---
    stopwords = {
        "ale", "lecz", "jednak", "i", "a", "oraz", "bo", "że", "ze", "ponieważ", "więc", # PL
        "but", "and", "because", "since", "so",                                          # EN
        "aber", "und", "oder", "weil", "dass",                                           # DE
        "pero", "y", "porque", "que",                                                    # ES
        "mais", "et", "car", "parce", "que",                                             # FR
        "men", "og", "eller", "fordi", "at"                                              # NO
    }
    toks = q.split()
    for i, tok in enumerate(toks):
        if tok.lower().strip(".,;:!?") in stopwords and i >= 1:
            q = " ".join(toks[:i]).strip()
            break
            
    return q


# Kod pocztowy — dokładnie ten sam wzór co w location_bot._GEOCODE_POSTCODE_RE.
# Powtórzony, żeby tryb gościa nie musiał pytać location_botu o zdanie przy
# każdej skróconej próbie.
_GEOCODE_POSTCODE_RE = re.compile(r"(?<!\d)(?:\d{2}-\d{3}|\d{4,10})(?!\d)")


def _shortening_keeps_context(query: str) -> bool:
    """Czy skrócenie zapytania do pierwszych dwóch tokenów nie gubi kontekstu.

    "Hel, PL" nie wolno skrócić do "Hel" — zniknąłby jawny kraj, czyli jedyny
    dowód, który rozstrzygał niepewność. To samo dotyczy kodu pocztowego.
    """
    raw = (query or "").strip()
    if "," in raw:
        return False
    if _GEOCODE_POSTCODE_RE.search(raw):
        return bool(_GEOCODE_POSTCODE_RE.search(" ".join(raw.split()[:2])))
    return True


def _geocode_best_effort(query: str, get_coords_fn, lang: str, geocode_status_fn=None):
    """Geokoduje zapytanie trybu gościa i zwraca ``(lat, lon, full, used, status)``.

    ETAP 1: jeśli dostarczymy ``geocode_status_fn`` (statusowy adapter z
    location_bot), kandydat jest akceptowany tylko po walidacji nazwy, a status
    decyduje o cache'u i o komunikacie. Bez niego zostaje stara ścieżka
    "bierz cokolwiek" — wyłącznie dla kompatybilności (w bocie adapter jest
    podawany zawsze, więc realnie idziemy przez statusy).
    """
    q = (query or "").strip()
    if not q:
        return (None, None, None, None, GEOCODE_NOT_FOUND)

    candidates = [q] # Zawsze zaczynamy od pełnego, wyczyszczonego zdania
    toks = q.split()
    
    # Deska ratunku: jeśli ktoś wpisał np. "Nowy Jork super", sprawdzamy "Nowy Jork"
    # — ale tylko gdy skrócenie nie wyrzuca jawnego kraju ani kodu pocztowego.
    if len(toks) > 2 and _shortening_keeps_context(q):
        candidates.append(" ".join(toks[:2]))

    seen = set()
    uniq = []
    for c in candidates:
        key = c.lower()
        if key not in seen:
            seen.add(key)
            uniq.append(c)

    # KLUCZOWY LIMIT: Zawsze robimy maksymalnie 2 zapytania!
    status = GEOCODE_NOT_FOUND
    for c in uniq[:2]:
        if geocode_status_fn:
            lat, lon, full, geo_status = geocode_status_fn(c, lang)
            status = geo_status or GEOCODE_NOT_FOUND
            if lat and lon and status == GEOCODE_OK:
                return (lat, lon, full, c, status)
            # Druga próba ma sens wyłącznie, gdy mapa odpowiedziała, ale nazwa
            # nie pasowała albo nie znalazła nic. TOO_SHORT (nie pytamy mapy),
            # UNCERTAIN (skrót nic nie rozstrzyga) i ERROR (sieć leży) zostają.
            if status not in (GEOCODE_NO_MATCH, GEOCODE_NOT_FOUND):
                break
            continue

        lat, lon, full = get_coords_fn(c, lang)
        if lat and lon:
            return (lat, lon, full, c, GEOCODE_OK)

    return (None, None, None, None, status)

# ============================================================================
# GŁÓWNY HANDLER TRYBU GOŚCIA
# ============================================================================

def handle_guest_now(
    message: dict, 
    bot_username: str,
    get_coords_fn,
    build_payload_fn,
    prepare_layout_fn,
    render_png_fn,
    send_photo_fn,
    send_reply_fn,
    get_city_fn=None,
    geocode_status_fn=None,
) -> bool:
    """Tryb gościa: skróty (?d/.n/?12/?14 ...) i wzmianki @bot.

    ``geocode_status_fn`` (opcjonalny, ale podawany przez location_bot) zwraca
    ``(lat, lon, display_location, status)`` z ETAP 1 — tę samą walidację co
    /dzien, /teraz, /trend, /miasto i prompty. Karta powstaje wyłącznie dla
    GEOCODE_OK; w grupie błąd oznacza ciszę, ale karty nie ma nigdy.
    """
    
    text = (message.get("text") or "").strip()
    
    # Ignorujemy tradycyjne komendy menu (aby nie dublować pracy)
    if text.startswith("/"):
        return False
        
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    chat_type = chat.get("type", "")
    
    # Od teraz tryb skrótów działa wszędzie (brak hard-blocka dla "private")
    is_private = (chat_type == "private")
    
    text_lower = text.lower()
    mention = f"@{bot_username.lower()}"
    is_mention = mention in text_lower
    
    # 1. IDENTYFIKACJA SKRÓTU I TYPU KARTY (POPRAWKA #6: trzy warstwy)
    prefix, shortcut_type, query = resolve_shortcut(text)
    is_shortcut = prefix is not None
    card_type = shortcut_type or "now"  # Domyślnie dla zapytań przez @
    query = query or ""

    if not (is_mention or is_shortcut):
        return False
        
    # --- BEZPIECZNE POBRANIE JĘZYKA ---
    raw_l = (message.get("from", {}) or {}).get("language_code", "en")[:2].lower()
    user_lang = "no" if raw_l in ("no", "nb") else raw_l
    if user_lang not in ("pl", "en", "de", "fr", "es", "no"):
        user_lang = "en"
        
    reply = message.get("reply_to_message") or {}
    loc = reply.get("location")
    
    used_query = None  
    full_address = None  # <--- Zmienna na pełny, oficjalny adres państwowy
    
    try:
        from i18n import t_ui
        fallback_city = t_ui(user_lang, "default_city")
        err_msg = t_ui(user_lang, "search_err")
    except Exception:
        fallback_city = "Twoja okolica"
        err_msg = "Chwilowy problem z wyszukiwaniem lokalizacji."
        
    oficjalna_nazwa = fallback_city
    
    if loc and "latitude" in loc and "longitude" in loc:
        lat = float(loc["latitude"])
        lon = float(loc["longitude"])
        
        if get_city_fn:
            try:
                ckey = (round(lat, 3), round(lon, 3), user_lang)
                city_name = _ttl_get(_CITY_CACHE, ckey)
                if not city_name:
                    city_name = get_city_fn(lat, lon, user_lang)
                    if city_name:
                        _ttl_set(_CITY_CACHE, ckey, city_name, _CITY_TTL)
                
                if city_name and "Lokalizacja" not in city_name and "Location" not in city_name and not any(ch.isdigit() for ch in city_name):
                    oficjalna_nazwa = city_name
                    full_address = city_name
            except Exception:
                pass
                
    else:
        # Płynne odcinanie nazwy miasta, bez dotykania funkcji czyszczącej
        if is_shortcut:
            pass # (Zmienna 'query' odcięta już na samej górze!)
        else:
            query = _extract_query_from_mention(text, bot_username)
            
        query = _clean_location_query(query)
        
        if not query:
            if is_private:
                # POPRAWKA #6: skrótów nie promujemy poza /porady, więc prompt
                # o miejscowość jest po prostu prośbą (tekst z i18n).
                try:
                    from i18n import t_ui
                    prompt_city = t_ui(user_lang, "guest_need_city")
                except Exception:
                    prompt_city = "📍 Podaj nazwę miejscowości lub kod pocztowy."
                send_reply_fn(chat_id, prompt_city)
            return True
            
        try:
            qkey = (user_lang, query.lower())
            cached_geo = _ttl_get(_GEO_CACHE, qkey)

            if cached_geo:
                # W cache trafia wyłącznie GEOCODE_OK, więc trafienie to zawsze
                # wynik zaakceptowany przez walidację nazwy (ETAP 1).
                lat, lon, full_address = cached_geo
                used_query = query
                geo_status = GEOCODE_OK
            else:
                lat, lon, full_address, used_query, geo_status = _geocode_best_effort(
                    query, get_coords_fn, user_lang, geocode_status_fn
                )
                # ETAP 1: cache gościa TYLKO dla wyników zwalidowanych.
                # TOO_SHORT, NO_MATCH, UNCERTAIN, NOT_FOUND i ERROR nie wolno
                # utrwalać — inaczej jedna literówka lub chwila awarii mapy
                # powtarzałaby się przez godzinę.
                if lat and lon and geo_status == GEOCODE_OK:
                    _ttl_set(_GEO_CACHE, qkey, (lat, lon, full_address), _GEO_TTL)

            if not lat or not lon:
                if is_private:
                    # Wspólny słownik statusów z komendami: ten sam komunikat dla
                    # "?12 Wa" i dla "/teraz Wa". W grupie zachowujemy ciszę.
                    status_msg = t_geocode(user_lang, geo_status)
                    if status_msg:  # GEOCODE_OK nie ma tekstu — nie wysyłamy pustki
                        send_reply_fn(chat_id, status_msg)
                return True

            city_name = None
            if get_city_fn:
                try:
                    ckey = (round(lat, 3), round(lon, 3), user_lang)
                    city_name = _ttl_get(_CITY_CACHE, ckey)
                    if not city_name:
                        city_name = get_city_fn(lat, lon, user_lang)
                        if city_name:
                            _ttl_set(_CITY_CACHE, ckey, city_name, _CITY_TTL)
                except Exception:
                    pass
                    
            if (not city_name) or ("Lokalizacja" in city_name) or ("Location" in city_name) or any(ch.isdigit() for ch in city_name):
                city_name = (used_query or query).strip() if (used_query or query) else fallback_city
                
            oficjalna_nazwa = city_name
                
        except Exception as e:
            print(f"❌ [GuestMode] Błąd w bloku geokodowania: {e}")
            if is_private:
                send_reply_fn(chat_id, err_msg)
            return True

    # ===============================
    # OSTATNI KROK: BUDOWA I WYSYŁKA
    # ===============================
    try:
        # 1. Pobieramy pakiet danych (tu znajduje się już odpowiednia strefa czasowa dla danego miasta)
        payload = build_payload_fn(lat, lon, user_lang, card_type, oficjalna_nazwa)
        if not payload:
            return True
            
        # --- NOWOŚĆ: BLOKADA CZASOWA DLA KARTY DZIENNEJ (.d) ---
        if card_type == "day":
            try:
                from datetime import datetime
                try:
                    from zoneinfo import ZoneInfo
                except ImportError:
                    from backports.zoneinfo import ZoneInfo
                    
                # Pobieramy strefę czasową dla dokładnie tego wyszukanego miasta
                tz_str = payload.get("location", {}).get("tz", "UTC")
                local_now = datetime.now(ZoneInfo(tz_str))
                
                # Blokada od 16:00 do 04:59 czasu lokalnego
                if local_now.hour < 5 or local_now.hour >= 16:
                    from i18n import t_ui
                    send_reply_fn(chat_id, t_ui(user_lang, "time_limit"))
                    return True # Przerywamy działanie, nie rysujemy karty
            except Exception as e:
                print(f"❌ [GuestMode] Błąd weryfikacji czasu lokalnego: {e}")
        # -------------------------------------------------------
            
        # 2. Renderujemy odpowiedni układ karty na bazie wybranego skrótu
        lay = prepare_layout_fn(payload, card_type)
        img_path = render_png_fn(lay)
        
        if img_path:
            # 3. Wysłanie gotowej karty z wstrzyknięciem pełnego adresu z geokodera!
            final_address = full_address if full_address else oficjalna_nazwa
            send_photo_fn(chat_id, img_path, oficjalna_nazwa, final_address)
            
    except Exception as e:
        print(f"❌ [GuestMode] KRYTYCZNY BŁĄD generowania karty: {e}")
        import traceback
        traceback.print_exc()
        if is_private:
            send_reply_fn(chat_id, err_msg)
            
    return True