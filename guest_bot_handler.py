import os
import re
import time
import logging


logger = logging.getLogger(__name__)

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


def _geocode_best_effort(query: str, get_coords_fn, lang: str):
    q = (query or "").strip()
    if not q:
        return (None, None, None, None)

    candidates = [q] # Zawsze zaczynamy od pełnego, wyczyszczonego zdania
    toks = q.split()
    
    # Deska ratunku: jeśli ktoś wpisał np. "Nowy Jork super", sprawdzamy "Nowy Jork"
    if len(toks) > 2:
        candidates.append(" ".join(toks[:2]))

    seen = set()
    uniq = []
    for c in candidates:
        key = c.lower()
        if key not in seen:
            seen.add(key)
            uniq.append(c)

    # KLUCZOWY LIMIT: Zawsze robimy maksymalnie 2 zapytania!
    for c in uniq[:2]:
        lat, lon, full = get_coords_fn(c, lang)
        if lat and lon:
            return (lat, lon, full, c)

    return (None, None, None, None)

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
    get_city_fn=None    
) -> bool:
    
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
    
    # 1. IDENTYFIKACJA SKRÓTU I TYPU KARTY
    is_shortcut = False
    card_type = "now"  # Domyślnie dla zapytań przez @
    query = ""
    
    # Sprawdzamy prefiksy z kropką i pytajnikiem
    if text_lower.startswith((".n ", "?n ", ".n", "?n")):
        is_shortcut = True
        card_type = "now"
        query = text[2:].strip()
    elif text_lower.startswith((".d ", "?d ", ".d", "?d")):
        is_shortcut = True
        card_type = "day"
        query = text[2:].strip()
    elif text_lower.startswith((".f ", "?f ", ".f", "?f")):
        is_shortcut = True
        card_type = "future"
        query = text[2:].strip()
    elif text_lower.startswith((".p ", "?p ", ".p", "?p")): # Wsteczna kompatybilność
        is_shortcut = True
        card_type = "now"
        query = text[2:].strip()

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
                send_reply_fn(chat_id, "Podaj miasto lub kod pocztowy, np. .d Paryż")
            return True
            
        try:
            qkey = (user_lang, query.lower())
            cached_geo = _ttl_get(_GEO_CACHE, qkey)
            
            if cached_geo:
                lat, lon, full_address = cached_geo
                used_query = query
            else:
                lat, lon, full_address, used_query = _geocode_best_effort(query, get_coords_fn, user_lang)
                if lat and lon:
                    _ttl_set(_GEO_CACHE, qkey, (lat, lon, full_address), _GEO_TTL)
                    
            if not lat or not lon:
                if is_private:
                    send_reply_fn(chat_id, f"🧐 Nie znalazłem miejsca: *{query}*. Wpisz samo miasto lub kod.")
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
        payload = build_payload_fn(lat, lon, user_lang, card_type == "now", oficjalna_nazwa)
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