import os
import re
import time
import logging

logger = logging.getLogger(__name__)

# ============================================================================
# HELPERY TEKSTOWE I GEOKODUJĄCE
# ============================================================================

def _extract_query_from_mention(text: str, bot_username: str) -> str:
    """Odciąga tekst po wzmiance i ucina na twardych separatorach zdania."""
    if not text:
        return ""
    
    mention = f"@{bot_username.lower()}"
    low = text.lower()
    idx = low.find(mention)
    
    if idx == -1:
        return ""
        
    after = text[idx + len(mention):]
    # Usuń separatory zaraz po wzmiance
    after = re.sub(r"^[\s:–—,]+", "", after).strip()
    
    # Ucinamy na typowych separatorach kończących myśl
    after = re.split(r"[!\n\r\(\)\[\]\{\}]", after, maxsplit=1)[0].strip()
    return after


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
            
    return q


def _geocode_best_effort(query: str, get_coords_fn, lang: str):
    """
    Próbuje znaleźć lokalizację bez NLP:
    - najpierw pełny tekst po wzmiance
    - potem wersje skrócone zachowujące wielowyrazowe nazwy
    Zwraca: (lat, lon, full, used_query)
    """
    q = (query or "").strip()
    if not q:
        return (None, None, None, None)
        
    tokens = q.split()
    candidates = [q]
    
    # 1) Usuń wiodące 'w/we/in' (np. "@bot w Warszawie")
    if tokens and tokens[0].lower() in ("w", "we", "in"):
        candidates.append(" ".join(tokens[1:]))
        
    # 2) Fallback: ostatnie N tokenów (dla "Rio de Janeiro jutro")
    for n in (4, 3, 2, 1):
        if len(tokens) >= n:
            candidates.append(" ".join(tokens[-n:]))
            
    # Deduplikacja (case-insensitive) z zachowaniem kolejności
    seen = set()
    uniq = []
    for c in candidates:
        c = " ".join((c or "").split()).strip()
        if not c:
            continue
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
        
    # Max 4 próby, żeby nie spamować geokodera (Nominatim)
    for c in uniq[:4]:
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
    send_reply_fn
) -> bool:
    
    text = (message.get("text") or "").strip()
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    chat_type = chat.get("type", "")
    is_private = (chat_type == "private")
    
    # --- ODCINAMY WSZYSTKO, CO NIE JEST WZMIANKĄ ---
    mention = f"@{bot_username.lower()}"
    if mention not in text.lower():
        return False
        
    user_lang = (message.get("from", {}) or {}).get("language_code", "en")[:2].lower()
    
    reply = message.get("reply_to_message") or {}
    loc = reply.get("location")
    
    # POBRANIE WSPÓŁRZĘDNYCH (Z pinezki lub z tekstu)
    if loc and "latitude" in loc and "longitude" in loc:
        lat = float(loc["latitude"])
        lon = float(loc["longitude"])
    else:
        # Krok 1 & 2: Odcięcie szumu i wstępne czyszczenie
        query = _extract_query_from_mention(text, bot_username)
        query = _clean_location_query(query)
        
        if not query:
            if is_private:
                send_reply_fn(chat_id, "Podaj miasto lub kod pocztowy po @nazwie_bota.")
            return True  # W grupie zachowujemy ciszę
            
        # Krok 3: Inteligentne geokodowanie (Fallback)
        try:
            lat, lon, _full, used_query = _geocode_best_effort(query, get_coords_fn, user_lang)
            
            if not lat or not lon:
                if is_private:
                    send_reply_fn(chat_id, f"🧐 Nie znalazłem miejsca: *{query}*. Wpisz samo miasto lub kod.")
                return True  # W grupie zachowujemy ciszę
                
        except Exception as e:
            logger.error(f"[GuestMode] Błąd geokodowania dla '{query}': {e}")
            if is_private:
                send_reply_fn(chat_id, "Chwilowy problem z wyszukiwaniem lokalizacji. Spróbuj za chwilę.")
            return True  # W grupie zachowujemy ciszę

    # --- GENEROWANIE KARTY (Jeśli mamy koordynaty) ---
    try:
        payload = build_payload_fn(lat, lon, user_lang, is_now=True)
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
            send_reply_fn(chat_id, "Nie udało się pobrać aktualnych danych pogodowych. Spróbuj ponownie.")
            
    return True