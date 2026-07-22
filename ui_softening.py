from __future__ import annotations
import re

_MM_PCT_PARENS_RE = re.compile(r"\s*\([^)]*(mm|%)[^)]*\)", re.IGNORECASE)

def strip_mm_pct_parens(text: str) -> str:
    if not text:
        return text
    return _MM_PCT_PARENS_RE.sub("", text).strip()

def soften_possible_prefix(text: str, lang: str = "pl") -> str:
    """
    Dodaje odpowiedni prefix (np. Możliwy/Possible/Möglicher) bez gubienia zjawiska.
    Obsługuje 6 języków.
    """
    if not text:
        return text
        
    pd2 = text.strip()
    low = pd2.lower()
    prefix = ""

    # --- LOGIKA DLA POLSKIEGO ---
    if lang == "pl":
        has_wx = any(w in low for w in ["deszcz", "mżawk", "ulew", "opad", "burz", "śnieg", "grad"])
        already_soft = any(w in low for w in ["możliw", "ryzyko", "szansa", "prawdopodobn", "niepewn", "lokalnie", "miejscami"])
        if not has_wx or already_soft: return pd2
        
        if any(w in low for w in ["opad", "opady", "burz", "ulew"]): prefix = "Możliwe"
        elif "mżawka" in low: prefix = "Możliwa"
        else: prefix = "Możliwy"

    # --- LOGIKA DLA ANGIELSKIEGO ---
    elif lang == "en":
        has_wx = any(w in low for w in ["rain", "drizzle", "shower", "storm", "snow", "hail", "precip"])
        already_soft = any(w in low for w in ["possibl", "risk", "chance", "probabl", "local", "slight"])
        if not has_wx or already_soft: return pd2
        
        prefix = "Possible"

    # --- LOGIKA DLA NIEMIECKIEGO ---
    elif lang == "de":
        has_wx = any(w in low for w in ["regen", "niesel", "schauer", "gewitter", "schnee", "hagel"])
        already_soft = any(w in low for w in ["möglich", "risiko", "chance", "wahrscheinlich", "örtlich", "lokal"])
        if not has_wx or already_soft: return pd2
        
        if any(w in low for w in ["regen", "schnee", "hagel", "niesel"]): prefix = "Möglicher"
        else: prefix = "Mögliche"

    # --- LOGIKA DLA FRANCUSKIEGO ---
    elif lang == "fr":
        has_wx = any(w in low for w in ["pluie", "bruine", "averse", "orage", "neige", "grêle"])
        already_soft = any(w in low for w in ["possible", "risque", "chance", "probabl", "localement"])
        if not has_wx or already_soft: return pd2
        
        prefix = "Risque de" # Brzmi naturalniej we francuskim niż "Possible pluie"

    # --- LOGIKA DLA HISZPAŃSKIEGO ---
    elif lang == "es":
        has_wx = any(w in low for w in ["lluvia", "llovizna", "chubasco", "tormenta", "nieve", "granizo"])
        already_soft = any(w in low for w in ["posibl", "riesgo", "probabil", "local"])
        if not has_wx or already_soft: return pd2
        
        if any(w in low for w in ["lluvia", "llovizna", "nieve", "tormenta"]): prefix = "Posible"
        else: prefix = "Posibles"

    # --- LOGIKA DLA NORWESKIEGO ---
    elif lang in ("no", "nb"):
        has_wx = any(w in low for w in ["regn", "yr", "byger", "storm", "snø", "hagl", "torden"])
        already_soft = any(w in low for w in ["mulig", "risiko", "sjanse", "sannsynlig", "lokalt"])
        if not has_wx or already_soft: return pd2
        
        if "byger" in low: prefix = "Mulige"
        else: prefix = "Mulig"

    # --- FALLBACK (Brak języka na liście) ---
    else:
        return pd2

    # --- DOKLEJANIE PREFIXU DO ZDANIA ---
    if len(pd2) >= 2:
        return f"{prefix} {pd2[0].lower()}{pd2[1:]}"
    else:
        return f"{prefix} {pd2.lower()}"