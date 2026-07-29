import os
import re
import time
import logging

logger = logging.getLogger(__name__)

# ============================================================================
# HELPERY TEKSTOWE I GEOKODUJĄCE
# ============================================================================

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
    stopwords = {"ale", "lecz", "jednak", "but", "aber", "pero", "mais"}
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

    toks = q.split()
    candidates = []
    for n in (min(3,len(toks)), 2, 1):
        if len(toks) >= n:
            candidates.append(" ".join(toks[:n]))

    seen = set()
    uniq = []
    for c in candidates:
        key = c.lower()
        if key in seen: 
            continue
        seen.add(key)
        uniq.append(c)

    for c in uniq:
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
    used_query = None  # <--- Dodajemy pustą zmienną
    
    if loc and "latitude" in loc and "longitude" in loc:
        lat = float(loc["latitude"])
        lon = float(loc["longitude"])
    else:
        query = _extract_query_from_mention(text, bot_username)
        query = _clean_location_query(query)
        
        if not query:
            if is_private:
                send_reply_fn(chat_id, "Podaj miasto lub kod pocztowy po @nazwie_bota.")
            return True
            
        try:
            # POBIERAMY DANE (zmienna full_address to pełny adres z bazy OSM!)
            lat, lon, full_address, used_query = _geocode_best_effort(query, get_coords_fn, user_lang)
            
            if not lat or not lon:
                if is_private:
                    send_reply_fn(chat_id, f"🧐 Nie znalazłem miejsca: *{query}*. Wpisz samo miasto lub kod.")
                return True
                
            # --- TARCZA PRZED BZDURAMI I LITERÓWKAMI ---
            # Bierzemy z oficjalnego adresu wszystko do pierwszego przecinka
            if full_address:
                oficjalna_nazwa = full_address.split(",")[0].strip()
            else:
                oficjalna_nazwa = used_query.title() if used_query else "Twoja okolica"
                
        except Exception as e:
            logger.error(f"[GuestMode] Błąd geokodowania dla '{query}': {e}")
            if is_private:
                send_reply_fn(chat_id, "Chwilowy problem z wyszukiwaniem lokalizacji. Spróbuj za chwilę.")
            return True

    # --- GENEROWANIE KARTY ---
    try:
        # ZMIANA: Przekazujemy oficjalna_nazwa zamiast used_query!
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