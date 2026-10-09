"""
users_store.py — Rejestr dostępu i profilu użytkownika (zakładka `Users` w Pogoda_Users).

 Architektura migracji RODO/access (PR1):
- NOWA zakładka `Users` = jedno miejsce na access_* (granted/revoked/blocked)
  oraz profile_* (lokalizacja; w PR1 tylko czyszczona — zapis profilu pojawił się
  w PR2 razem z PENDING_SAVE, a od PR2-UX-cleanup świadomym flow jest `/miasto`;
  `/save_location` działa dalej, ale wyłącznie jako ukryty alias techniczny).
- Scheduler raportów korzysta z `Users`; ustawienia raportowe są przechowywane
  jako `report_morning_time` i `report_afternoon_time`.
- `Formularz` pozostaje legacy dla pozostałych ścieżek bota; PR3 nie wykonuje
  backfillu ani zmian danych w tej zakładce.
- Użytkownicy legacy (wiersz w Formularz, brak wiersza w Users) mają domniemany
  dostęp w ścieżkach legacy — o fallback dba location_bot (`_chat_has_access`).

Konwencje techniczne:
- Wszystkie aktualizacje są wykonywane BATCHOWO (jeden `update_cells` na operację),
  nigdy `update_cell` w pętli (ochrona limitów Google API).
- chat_id zapisujemy jako TEKST (RAW), timestamps jako "YYYY-MM-DD HH:MM:SS" (UTC).
- Nagłówki dopasowujemy case-insensitively z obróbką białych znaków.
"""

import gspread
from datetime import datetime, timezone

# =====================================================================
# KONFIGURACJA
# =====================================================================
SHEET_TITLE = "Pogoda_Users"
USERS_TAB = "Users"

# Statusy dostępu
ACCESS_GRANTED = "granted"
ACCESS_REVOKED = "revoked"
ACCESS_BLOCKED = "blocked"

# Dozwolone wartości blocked_reason (zgodnie z planem migracji)
BLOCK_REASONS = (
    "telegram_blocked",
    "chat_not_found",
    "user_deactivated",
    "kicked_from_group",
    "unknown",
)

# Źródła dostępu
ACCESS_SOURCES = ("public_beta", "referral", "admin", "legacy")

# Kanoniczna kolejność kolumn zakładki Users (wg planu; fallback przy append)
CANONICAL_HEADERS = [
    "chat_id", "created_at", "last_seen_at",
    "access_status", "access_granted_at", "access_source", "privacy_version_seen",
    "access_expires_at",
    "blocked_at", "blocked_reason",
    "profile_status", "lat_round", "lon_round", "location_label", "location_source",
    "lang", "location_consent_at", "location_consent_version", "profile_updated_at",
    "report_morning_time", "report_afternoon_time",
]

REPORT_TIME_COLS = ("report_morning_time", "report_afternoon_time")

# Kolumny profilu (lokalizacja) — czyszczone przez clear_profile() / set_blocked()
PROFILE_COLS = (
    "lat_round", "lon_round", "location_label", "location_source",
    "location_consent_at", "location_consent_version",
)

SUPPORTED_LANGS = ("pl", "en", "de", "fr", "es", "no")
LOCATION_SOURCES = ("gps", "city", "webapp")


def _is_rate_limit_error(exc: Exception) -> bool:
    """Rozpoznaje 429 Google API, które nie może być zamienione na brak rekordu."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if status == 429:
        return True

    text = str(exc).lower()
    return "429" in text or "quota exceeded" in text or "resource has been exhausted" in text


def _reraise_rate_limit(exc: Exception) -> None:
    """Zachowuje dotychczasowe fallbacki, ale udostępnia 429 warstwie pollera."""
    if _is_rate_limit_error(exc):
        raise exc


def now_iso() -> str:
    """Aktualny czas UTC w formacie czytelnym w Google Sheets (spójny z resztą bazy)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def norm_chat_id(val) -> str:
    """Normalizuje chat_id do porównań: str/strip/usunięcie sufiksu '.0' z liczb Sheets."""
    s = str(val if val is not None else "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


# =====================================================================
# DOSTĘP DO ZAKŁADKI
# =====================================================================
def get_ws(gc, title: str = SHEET_TITLE, tab: str = USERS_TAB):
    """
    Zwraca worksheet zakładki Users. W razie problemu (brak zakładki/uprawnień)
    zwraca None — bot wtedy działa w trybie legacy (access = wiersz w Formularz).
    """
    try:
        return gc.open(title).worksheet(tab)
    except Exception as e:
        _reraise_rate_limit(e)
        print(f"  ⚠️ [users_store] Nie mogę otworzyć zakładki {title}/{tab}: {e}")
        return None


def _read_rows(ws):
    """
    Jedno zapytanie API: nagłówki + wszystkie niepuste wiersze.
    Zwraca listę dictów {kolumna: wartość} z ukrytym kluczem '_row' (1-based).
    """
    values = ws.get_all_values()
    if not values:
        return []

    headers = [str(h).strip().lower() for h in values[0]]
    rows = []
    for i, raw in enumerate(values[1:]):
        row = list(raw) + [""] * max(0, len(headers) - len(raw))
        if not any(str(c).strip() for c in row):
            continue  # całkowicie pusty wiersz
        d = {headers[j]: row[j] for j in range(len(headers))}
        d["_row"] = i + 2  # wiersz 1 to nagłówki
        rows.append(d)
    return rows


# =====================================================================
# ODCZYT
# =====================================================================
def find_row_by_chat_id(ws, chat_id):
    """Zwraca numer wiersza (1-based) z danym chat_id albo None. 1 zapytanie API."""
    if ws is None:
        return None
    try:
        col_values = ws.col_values(1)
        needle = norm_chat_id(chat_id)
        for i, val in enumerate(col_values):
            if norm_chat_id(val) == needle:
                return i + 1  # col_values[0] = wiersz nagłówków
    except Exception as e:
        _reraise_rate_limit(e)
        print(f"  ⚠️ [users_store] find_row_by_chat_id({chat_id}): {e}")
    return None


def get_user(ws, chat_id):
    """Zwraca rekord użytkownika (dict z '_row') albo None. 1 zapytanie API."""
    if ws is None:
        return None
    try:
        needle = norm_chat_id(chat_id)
        for d in _read_rows(ws):
            if norm_chat_id(d.get("chat_id")) == needle:
                return d
    except Exception as e:
        _reraise_rate_limit(e)
        print(f"  ⚠️ [users_store] get_user({chat_id}): {e}")
    return None


def load_users_map(ws) -> dict:
    """
    Mapa {chat_id(znormalizowany): rekord} — do taniej bramki access w RAM
    (jedno zapytanie na cykl nasłuchiwania, zamiast per wiadomość).
    W razie błędu zwraca {} — bot degraduje się do trybu legacy.
    """
    if ws is None:
        return {}
    try:
        out = {}
        for d in _read_rows(ws):
            cid = norm_chat_id(d.get("chat_id"))
            if cid:
                out[cid] = d
        return out
    except Exception as e:
        _reraise_rate_limit(e)
        print(f"  ⚠️ [users_store] load_users_map: {e}")
        return {}


def has_access(ws, chat_id) -> bool:
    """True tylko gdy istnieje wiersz w Users z access_status=granted."""
    u = get_user(ws, chat_id)
    return bool(u) and str(u.get("access_status", "")).strip().lower() == ACCESS_GRANTED


def has_access_in_map(users_map: dict, chat_id) -> bool:
    """Wariant has_access działający na mapie z RAM (bez zapytań API)."""
    u = (users_map or {}).get(norm_chat_id(chat_id))
    return bool(u) and str(u.get("access_status", "")).strip().lower() == ACCESS_GRANTED


def get_lang(users_map: dict, chat_id):
    """Język użytkownika zapisany w Users (albo None, gdy brak/nieobsługiwany)."""
    u = (users_map or {}).get(norm_chat_id(chat_id))
    if not u:
        return None
    raw = str(u.get("lang", "")).strip().lower()
    if raw in ("no", "nb"):
        return "no"
    if raw in SUPPORTED_LANGS:
        return raw
    return None


def get_report_settings(users_map: dict, chat_id):
    """Zwraca jawne ustawienia raportów z Users; brak rekordu daje None."""
    user = (users_map or {}).get(norm_chat_id(chat_id))
    if not user:
        return None
    return {
        col: str(user.get(col, "") or "").strip()
        for col in REPORT_TIME_COLS
    }


# =====================================================================
# ZAPIS (BATCHOWO)
# =====================================================================
def _header_columns(ws) -> dict:
    """Mapa {nazwa_kolumny(lowercase): numer_kolumny(1-based)} z wiersza nagłówków."""
    raw_headers = ws.row_values(1)
    return {str(h).strip().lower(): i + 1 for i, h in enumerate(raw_headers)}


def _update_cells_batch(ws, row: int, updates: dict) -> bool:
    """Aktualizuje wiele pól jednego wiersza JEDNYM zapytaniem update_cells."""
    if not updates:
        return False
    header_map = _header_columns(ws)
    cells = []
    for col_name, val in updates.items():
        col = header_map.get(col_name)
        if col:
            cells.append(gspread.Cell(row=row, col=col, value=val))
    if not cells:
        print(f"  ⚠️ [users_store] Brak znanych kolumn do update ({list(updates.keys())})")
        return False
    ws.update_cells(cells)
    return True


def upsert_access(ws, chat_id, lang, source, now_iso_str, privacy_ver) -> bool:
    """
    Zapisuje/odświeża MINIMALNY dostęp użytkownika (access_*), bez lat/lon
    i bez profilu pogodowego. Nowy wiersz: append; istniejący: batch update.
    Zwraca True przy sukcesie.
    """
    if ws is None:
        return False
    try:
        existing = get_user(ws, chat_id)
        if lang not in SUPPORTED_LANGS:
            lang = "en"

        # --- NOWY UŻYTKOWNIK: append pełnego wiersza wg nagłówków arkusza ---
        if existing is None:
            header_map = _header_columns(ws)
            width = max(len(header_map), len(CANONICAL_HEADERS))
            new_row = [""] * width

            def put(col, val):
                idx = header_map.get(col)
                if idx is not None:
                    new_row[idx - 1] = val

            put("chat_id", str(chat_id))
            put("created_at", now_iso_str)
            put("last_seen_at", now_iso_str)
            put("access_status", ACCESS_GRANTED)
            put("access_granted_at", now_iso_str)
            put("access_source", source if source in ACCESS_SOURCES else "legacy")
            put("privacy_version_seen", privacy_ver)
            put("profile_status", "none")
            put("lang", lang)
            ws.append_row(new_row)
            return True

        # --- ISTNIEJĄCY WIERSZ: batch update pól access ---
        updates = {
            "last_seen_at": now_iso_str,
            "access_status": ACCESS_GRANTED,
            "access_granted_at": now_iso_str,
            "access_source": source if source in ACCESS_SOURCES else str(existing.get("access_source") or "legacy"),
            "privacy_version_seen": privacy_ver,
            "lang": lang,
        }
        if not str(existing.get("created_at", "")).strip():
            updates["created_at"] = now_iso_str
        if not str(existing.get("profile_status", "")).strip():
            updates["profile_status"] = "none"
        return _update_cells_batch(ws, existing["_row"], updates)

    except Exception as e:
        _reraise_rate_limit(e)
        print(f"  ❌ [users_store] upsert_access({chat_id}): {e}")
        return False


def set_profile(
    ws,
    chat_id,
    lat,
    lon,
    location_label,
    location_source,
    lang,
    consent_version,
    now_iso_str,
) -> bool:
    """Zapisuje profil lokalizacji wyłącznie po wyraźnej zgodzie użytkownika.

    Funkcja nie tworzy rekordu access i nie zapisuje niczego do legacy ``Formularz``.
    Wywołujący musi wcześniej przejść bramkę access oraz potwierdzić lokalizację
    świadomym flow ``/miasto`` (PL alias ``/city``; ``/save_location`` pozostał
    wyłącznie ukrytym aliasem technicznym).  Współrzędne są zaokrąglane do
    TRZECH miejsc po przecinku przed trwałym zapisem; dokładniejsze wartości
    żyją tylko w RAM w ``PENDING_SAVE`` location_bota.
    """
    if ws is None:
        return False
    try:
        existing = get_user(ws, chat_id)
        if existing is None:
            return False

        try:
            lat_round = str(round(float(lat), 3))
            lon_round = str(round(float(lon), 3))
        except (TypeError, ValueError):
            return False

        clean_lang = str(lang or "").strip().lower()
        if clean_lang in ("nb", "no"):
            clean_lang = "no"
        if clean_lang not in SUPPORTED_LANGS:
            clean_lang = "en"

        source = str(location_source or "").strip().lower()
        if source not in LOCATION_SOURCES:
            return False

        updates = {
            "profile_status": "active",
            "lat_round": lat_round,
            "lon_round": lon_round,
            "location_label": str(location_label or "").strip(),
            "location_source": source,
            "lang": clean_lang,
            "location_consent_at": now_iso_str,
            "location_consent_version": str(consent_version or "").strip(),
            "profile_updated_at": now_iso_str,
            "last_seen_at": now_iso_str,
        }
        return _update_cells_batch(ws, existing["_row"], updates)
    except Exception as e:
        _reraise_rate_limit(e)
        print(f"  ❌ [users_store] set_profile({chat_id}): {e}")
        return False


def set_report_settings(ws, chat_id, morning_time="", afternoon_time="") -> bool:
    """Zapisuje wybrane godziny w Users; puste wejście oznacza "bez zmian".

    Dozwolone wartości to HH:MM (24-godzinne) oraz jawny wyłącznik ``brak``.
    Nie wstawiamy domyślnych godzin. Jeśli kolumny raportowe nie istnieją w
    arkuszu, zapis kończy się bez częściowej aktualizacji.
    """
    if ws is None:
        return False
    try:
        existing = get_user(ws, chat_id)
        if existing is None:
            return False

        updates = {}
        for column, value in zip(REPORT_TIME_COLS, (morning_time, afternoon_time)):
            raw = str(value or "").strip()
            if not raw:
                continue  # WebApp: pusta opcja to "bez zmian"
            if raw.lower() == "brak":
                updates[column] = "brak"
                continue
            if (len(raw) != 5 or raw[2] != ":" or
                    not raw[:2].isdigit() or not raw[3:].isdigit()):
                return False
            hour, minute = int(raw[:2]), int(raw[3:])
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return False
            updates[column] = raw

        if not updates:
            return True

        headers = _header_columns(ws)
        if any(column not in headers for column in updates):
            print("  ⚠️ [users_store] Brak kolumn raportowych w Users; pomijam zapis.")
            return False
        return _update_cells_batch(ws, existing["_row"], updates)
    except Exception as e:
        _reraise_rate_limit(e)
        print(f"  ❌ [users_store] set_report_settings({chat_id}): {e}")
        return False


def set_blocked(ws, chat_id, reason, now_iso_str, clear_profile=True) -> bool:
    """
    Oznacza użytkownika jako blocked: access_status=blocked + blocked_at/blocked_reason,
    opcjonalnie czyści pola profilu (lokalizację). Zwraca False, gdy wiersza brak
    (użytkownik legacy) — wtedy wywołujący używa fallbacku BLOCKED_ w Formularz.
    """
    if ws is None:
        return False
    try:
        existing = get_user(ws, chat_id)
        if existing is None:
            return False

        updates = {
            "access_status": ACCESS_BLOCKED,
            "blocked_at": now_iso_str,
            "blocked_reason": reason if reason in BLOCK_REASONS else "unknown",
        }
        if clear_profile:
            updates.update({col: "" for col in PROFILE_COLS})
            updates["profile_status"] = "none"
        return _update_cells_batch(ws, existing["_row"], updates)

    except Exception as e:
        _reraise_rate_limit(e)
        print(f"  ❌ [users_store] set_blocked({chat_id}): {e}")
        return False


def clear_profile(ws, chat_id, now_iso_str) -> bool:
    """
    Czyści TYLKO pola profilu (współrzędne, etykietę, zgodę). Access zostaje bez zmian.
    Zwraca False, gdy użytkownika nie ma w Users.
    """
    if ws is None:
        return False
    try:
        existing = get_user(ws, chat_id)
        if existing is None:
            return False

        updates = {col: "" for col in PROFILE_COLS}
        updates["profile_status"] = "none"
        updates["profile_updated_at"] = now_iso_str
        return _update_cells_batch(ws, existing["_row"], updates)

    except Exception as e:
        _reraise_rate_limit(e)
        print(f"  ❌ [users_store] clear_profile({chat_id}): {e}")
        return False


def delete_user_row(ws, chat_id) -> bool:
    """
    HARD DELETE wiersza użytkownika z Users (access + profil + historia).
    Zwraca True, gdy wiersz istniał i został usunięty.
    """
    if ws is None:
        return False
    try:
        row = find_row_by_chat_id(ws, chat_id)
        if row is None:
            return False
        ws.delete_rows(row)
        return True
    except Exception as e:
        _reraise_rate_limit(e)
        print(f"  ❌ [users_store] delete_user_row({chat_id}): {e}")
        return False
