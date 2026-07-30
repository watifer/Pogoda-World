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
    # NIE obsługujemy guest-mode dla komend typu /now@bot
    # bo to ma iść normalną ścieżką bota w grupie.
    if text.startswith("/"):
        return False
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    chat_type = chat.get("type", "")
    is_private = (chat_type == "private")
    
    # --- ODCINAMY WSZYSTKO, CO NIE JEST WZMIANKĄ ---
    mention = f"@{bot_username.lower()}"
    if mention not in text.lower():
        return False
        
    # --- BEZPIECZNE POBRANIE JĘZYKA ---
    raw_l = (message.get("from", {}) or {}).get("language_code", "en")[:2].lower()
    user_lang = "no" if raw_l in ("no", "nb") else raw_l
    if user_lang not in ("pl", "en", "de", "fr", "es", "no"):
        user_lang = "en"
    # ----------------------------------
    
    reply = message.get("reply_to_message") or {}
    loc = reply.get("location")
    
    # --- BEZPIECZNE POBRANIE TŁUMACZEŃ ---
    try:
        from i18n import t_ui
        fallback_city = t_ui(user_lang, "default_city")  # np. "Twoja okolica" / "Your location"
        err_msg = t_ui(user_lang, "search_err")          # np. "Błąd wyszukiwania" / "Search error"
    except Exception:
        fallback_city = "Twoja okolica"
        err_msg = "Chwilowy problem z wyszukiwaniem lokalizacji. Spróbuj za chwilę."
        
    oficjalna_nazwa = fallback_city
    
    if loc and "latitude" in loc and "longitude" in loc:
        # --- 1. ŚCIEŻKA DLA PINEZKI ---
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
                
                # Dodaliśmy "Location", bo Nominatim po angielsku może zwrócić "Location in terrain"
                if city_name and "Lokalizacja" not in city_name and "Location" not in city_name and not any(ch.isdigit() for ch in city_name):
                    oficjalna_nazwa = city_name
            except Exception:
                pass
                
    else:
        # --- 2. ŚCIEŻKA DLA WYSZUKIWANIA TEKSTOWEGO ---
        query = _extract_query_from_mention(text, bot_username)
        query = _clean_location_query(query)
        
        if not query:
            if is_private:
                send_reply_fn(chat_id, "Podaj miasto lub kod pocztowy po @nazwie_bota.")
            return True
            
        try:
            # Sprawdzamy cache geokodowania
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
                
            # Reverse geocoding dla ładnej nazwy
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
                    
            # Przypisanie oficjalnej nazwy z obsługą błędów i cyfr
            if (not city_name) or ("Lokalizacja" in city_name) or ("Location" in city_name) or any(ch.isdigit() for ch in city_name):
                city_name = (used_query or query).strip() if (used_query or query) else fallback_city
                
            oficjalna_nazwa = city_name
                
        except Exception as e:
            logger.error(f"[GuestMode] Błąd geokodowania dla '{query}': {e}")
            if is_private:
                send_reply_fn(chat_id, err_msg)
            return True

    # --- GENEROWANIE KARTY ---
    try:
        # ZMIANA: Przekazujemy oficjalna_nazwa z powrotem do funkcji
        payload = build_payload_fn(lat, lon, user_lang, True, oficjalna_nazwa)
        
        layout = prepare_layout_fn(payload)
        img_path = render_png_fn(layout)
        
        if img_path:
            send_photo_fn(chat_id, img_path)
            try:
                os.remove(img_path)
            except Exception as e:
                logger.error(f"[GuestMode] Błąd usuwania pliku {img_path}: {e}")
        else:
            if is_private:
                send_reply_fn(chat_id, "Błąd podczas generowania grafiki pogodowej.")
                
    except Exception as e:
        logger.error(f"[GuestMode] Wyjątek podczas generowania/wysyłki karty: {e}")
        if is_private:
            send_reply_fn(chat_id, "Nie udało się pobrać danych pogodowych. Spróbuj ponownie.")
            
    return True

    # --- GENEROWANIE KARTY ---
    try:
        # 3. POBIERANIE POGODY
        payload = build_payload_fn(lat, lon, user_lang, True, oficjalna_nazwa)
        t3 = time.time()
        print(f"[DEBUG-TIME] 3. Pobieranie API Pogody zajęło: {t3 - t2:.2f} s", flush=True)
        
        # 4. RYSOWANIE KARTY PNG
        layout = prepare_layout_fn(payload)
        img_path = render_png_fn(layout)
        t4 = time.time()
        print(f"[DEBUG-TIME] 4. Generowanie PNG zajęło: {t4 - t3:.2f} s", flush=True)
        
        # 5. WYSYŁKA DO TELEGRAMA
        if img_path:
            send_photo_fn(chat_id, img_path)
            t5 = time.time()
            print(f"[DEBUG-TIME] 5. Wysłanie zdjęcia do TG zajęło: {t5 - t4:.2f} s", flush=True)
            print(f"[DEBUG-TIME] 🚀 CAŁKOWITY CZAS: {t5 - t0:.2f} s", flush=True)
            
            try:
                os.remove(img_path)
            except Exception as e:
                pass
        else:
            if is_private:
                send_reply_fn(chat_id, "Błąd podczas generowania grafiki pogodowej.")
                
    except Exception as e:
        logger.error(f"[GuestMode] Wyjątek: {e}")
            
    return True