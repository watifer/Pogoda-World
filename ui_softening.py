# ui_softening.py
from __future__ import annotations
import re

_MM_PCT_PARENS_RE = re.compile(r"\s*\([^)]*(mm|%)[^)]*\)", re.IGNORECASE)

def strip_mm_pct_parens(text: str) -> str:
    if not text:
        return text
    return _MM_PCT_PARENS_RE.sub("", text).strip()

def soften_possible_prefix(text: str) -> str:
    """
    Dodaje 'Możliwy/Możliwa/Możliwe' bez gubienia zjawiska.
    Zakłada, że tekst jest krótkim opisem (primary_desc).
    """
    if not text:
        return text
    pd2 = text.strip()
    low = pd2.lower()
    has_wx = any(w in low for w in ["deszcz", "mżawk", "ulew", "opad", "burz", "śnieg", "grad"])
    already_soft = any(w in low for w in ["możliw", "ryzyko", "szansa", "prawdopodobn", "niepewn", "lokalnie", "miejscami"])
    
    if not has_wx or already_soft:
        return pd2

    # mikro-gramatyka (minimalna, praktyczna)
    if any(w in low for w in ["opad", "opady", "burz", "ulew"]):
        prefix = "Możliwe"
    elif "mżawka" in low:
        prefix = "Możliwa"
    elif any(w in low for w in ["deszcz", "śnieg", "grad"]):
        prefix = "Możliwy"
    else:
        prefix = "Możliwe"
        
    # nie robimy lower() całego zdania, tylko pierwszą literę
    if len(pd2) >= 2:
        pd2 = f"{prefix} {pd2[0].lower()}{pd2[1:]}"
    else:
        pd2 = f"{prefix} {pd2.lower()}"
        
    return pd2