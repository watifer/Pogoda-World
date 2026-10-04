"""
test_users_store.py — Testy jednostkowe rejestru dostępu (zakładka Users) dla PR1.

Uruchomienie: pytest test_users_store.py -v

Testy działają w 100% na atrapie arkusza (FakeWorksheet) — żadnych sieci,
żadnych produkcyjnych danych, żadnych kluczy API.
"""

import pytest

import users_store
from users_store import (
    norm_chat_id, now_iso, get_ws, find_row_by_chat_id, get_user,
    load_users_map, has_access, has_access_in_map, get_lang,
    upsert_access, set_profile, set_blocked, clear_profile, delete_user_row,
)

# Dokładnie te nagłówki, które powstały w pkt 0 planu (zakładka Users)
USERS_HEADERS = [
    "chat_id", "created_at", "last_seen_at",
    "access_status", "access_granted_at", "access_source", "privacy_version_seen",
    "access_expires_at",
    "blocked_at", "blocked_reason",
    "profile_status", "lat_round", "lon_round", "location_label", "location_source",
    "lang", "location_consent_at", "location_consent_version", "profile_updated_at",
]


# ============================================================================
# ATRAPA GSPREAD (implementuje tylko API używane przez users_store)
# ============================================================================
class FakeWorksheet:
    """Imituje gspread.worksheet dla operacji wykonywanych przez users_store."""

    def __init__(self, headers=None, rows=None):
        self.grid = [list(headers or USERS_HEADERS)] + [list(r) for r in (rows or [])]
        self.calls = {"append_row": 0, "update_cells": 0, "delete_rows": 0}

    # --- odczyt ---
    def get_all_values(self):
        return [row[:] for row in self.grid]

    def row_values(self, index):
        return self.grid[index - 1][:]

    def col_values(self, col):
        return [row[col - 1] if col - 1 < len(row) else "" for row in self.grid]

    # --- zapis ---
    def append_row(self, values, **kwargs):
        self.calls["append_row"] += 1
        self.grid.append([str(v) for v in values])
        return {}

    def update_cells(self, cells, **kwargs):
        self.calls["update_cells"] += 1
        headers = [str(h).strip().lower() for h in self.grid[0]]
        for cell in cells:
            if cell.col - 1 >= len(headers):
                continue
            while len(self.grid) < cell.row:
                self.grid.append([""] * len(headers))
            row = self.grid[cell.row - 1]
            while len(row) < len(headers):
                row.append("")
            row[cell.col - 1] = cell.value
        return {}

    def delete_rows(self, start_index, end_index=None):
        self.calls["delete_rows"] += 1
        assert end_index is None, "users_store usuwa pojedyncze wiersze"
        del self.grid[start_index - 1]
        return {}

    # --- pomocnicze (testy) ---
    def rows_by_chat_id(self):
        out = {}
        for row in self.grid[1:]:
            if row and str(row[0]).strip():
                out[norm_chat_id(row[0])] = row
        return out

    def record(self, chat_id):
        headers = [str(h).strip().lower() for h in self.grid[0]]
        row = self.rows_by_chat_id().get(norm_chat_id(chat_id))
        return dict(zip(headers, row)) if row else None


class FakeGC:
    """gc.open(...).worksheet(...) zwracające FakeWorksheet albo rzucające wyjątek."""

    def __init__(self, ws=None, fail=False):
        self.ws = ws
        self.fail = fail

    def open(self, title):
        if self.fail:
            raise RuntimeError("brak dostępu do arkusza")
        return self

    def worksheet(self, tab):
        if self.fail or self.ws is None:
            raise RuntimeError(f"zakładka {tab} nie istnieje")
        return self.ws


# ============================================================================
# norm_chat_id / now_iso
# ============================================================================
class TestNormChatId:
    def test_plain(self):
        assert norm_chat_id(12345) == "12345"
        assert norm_chat_id(" 12345 ") == "12345"
        assert norm_chat_id(-100200300) == "-100200300"

    def test_sheets_float_suffix(self):
        assert norm_chat_id("12345.0") == "12345"
        assert norm_chat_id("-100200300.0") == "-100200300"

    def test_none_and_empty(self):
        assert norm_chat_id(None) == ""
        assert norm_chat_id("") == ""


def test_now_iso_format():
    ts = now_iso()
    assert len(ts) == 19 and ts[4] == "-" and ts[10] == " "  # "YYYY-MM-DD HH:MM:SS"


# ============================================================================
# get_ws — degradacja przy braku zakładki
# ============================================================================
def test_get_ws_returns_none_on_failure():
    assert get_ws(FakeGC(fail=True)) is None
    assert get_ws(FakeGC()) is None  # brak zakładki Users


def test_get_ws_ok():
    ws = FakeWorksheet()
    assert get_ws(FakeGC(ws=ws)) is ws


def test_functions_safe_with_none_ws():
    # Wszystkie operacje na ws=None muszą bezpiecznie zwracać fałsz/None
    assert find_row_by_chat_id(None, 1) is None
    assert get_user(None, 1) is None
    assert load_users_map(None) == {}
    assert has_access(None, 1) is False
    assert upsert_access(None, 1, "pl", "referral", now_iso(), "v1") is False
    assert set_profile(None, 1, 1, 2, "X", "city", "pl", "v1", now_iso()) is False
    assert set_blocked(None, 1, "unknown", now_iso()) is False
    assert clear_profile(None, 1, now_iso()) is False
    assert delete_user_row(None, 1) is False


# ============================================================================
# upsert_access — minimalny access bez profilu
# ============================================================================
class TestUpsertAccess:
    def test_new_registration_appends_row_without_location(self):
        ws = FakeWorksheet()
        ok = upsert_access(ws, 111, "pl", "referral", "2026-09-30 10:00:00", "2026-09-v1")
        assert ok is True
        assert ws.calls["append_row"] == 1

        rec = ws.record(111)
        assert rec["chat_id"] == "111"
        assert rec["access_status"] == "granted"
        assert rec["access_granted_at"] == "2026-09-30 10:00:00"
        assert rec["access_source"] == "referral"
        assert rec["privacy_version_seen"] == "2026-09-v1"
        assert rec["profile_status"] == "none"
        assert rec["lang"] == "pl"
        assert rec["created_at"] == "2026-09-30 10:00:00"
        # KLUCZOWE (RODO): żadnej lokalizacji przy /start
        assert rec["lat_round"] == "" and rec["lon_round"] == ""
        assert rec["location_label"] == "" and rec["location_source"] == ""
        assert rec["location_consent_at"] == "" and rec["location_consent_version"] == ""

    def test_second_upsert_updates_not_duplicates(self):
        ws = FakeWorksheet()
        upsert_access(ws, 111, "pl", "referral", "2026-09-30 10:00:00", "v1")
        upsert_access(ws, 111, "en", "admin", "2026-09-30 12:00:00", "v2")

        assert ws.calls["append_row"] == 1          # tylko jeden wiersz!
        assert ws.calls["update_cells"] == 1        # batch update, nie update_cell w pętli
        rows = ws.rows_by_chat_id()
        assert len(rows) == 1

        rec = ws.record(111)
        assert rec["access_status"] == "granted"
        assert rec["access_source"] == "admin"
        assert rec["privacy_version_seen"] == "v2"
        assert rec["lang"] == "en"
        assert rec["created_at"] == "2026-09-30 10:00:00"  # created_at nietknięte
        assert rec["last_seen_at"] == "2026-09-30 12:00:00"

    def test_access_sources_enum(self):
        ws = FakeWorksheet()
        upsert_access(ws, 5, "en", "bogus_source", "2026-09-30 10:00:00", "v1")
        rec = ws.record(5)
        assert rec["access_source"] == "legacy"  # nieznane źródło -> legacy

    def test_unsupported_lang_falls_back_to_en(self):
        ws = FakeWorksheet()
        upsert_access(ws, 6, "it", "referral", "2026-09-30 10:00:00", "v1")
        assert ws.record(6)["lang"] == "en"


# ============================================================================
# set_profile — wyłącznie po świadomym flow /miasto (ukryty alias: /save_location)
# ============================================================================
def test_set_profile_writes_active_profile_and_consent_in_one_batch():
    ws = FakeWorksheet()
    upsert_access(ws, 123, "pl", "referral", "2026-09-30 10:00:00", "v1")

    ok = set_profile(
        ws, 123, 52.22972, 21.01223, "Warszawa", "city", "pl",
        "2026-09-v1", "2026-09-30 10:05:00",
    )

    assert ok is True
    assert ws.calls["update_cells"] == 1  # profil aktualizowany batchowo
    rec = ws.record(123)
    assert rec["access_status"] == "granted"
    assert rec["profile_status"] == "active"
    # PR2 UX cleanup: współrzędne zapisujemy do 3 miejsc po przecinku
    assert rec["lat_round"] == "52.23"      # 52.22972 -> 52.23
    assert rec["lon_round"] == "21.012"     # 21.01223 -> 21.012
    assert rec["location_label"] == "Warszawa"
    assert rec["location_source"] == "city"
    assert rec["lang"] == "pl"
    assert rec["location_consent_at"] == "2026-09-30 10:05:00"
    assert rec["location_consent_version"] == "2026-09-v1"
    assert rec["profile_updated_at"] == "2026-09-30 10:05:00"


def test_set_profile_never_creates_access_row_or_accepts_bad_source():
    ws = FakeWorksheet()
    assert set_profile(ws, 999, 1, 2, "X", "city", "pl", "v1", "2026-09-30 10:00:00") is False
    assert ws.record(999) is None

    upsert_access(ws, 999, "pl", "referral", "2026-09-30 10:00:00", "v1")
    assert set_profile(ws, 999, 1, 2, "X", "unknown", "pl", "v1", "2026-09-30 10:01:00") is False
    assert ws.record(999)["profile_status"] == "none"


# ============================================================================
# has_access / load_users_map
# ============================================================================
class TestHasAccess:
    def _ws_with_status(self, status):
        ws = FakeWorksheet()
        row = [""] * len(USERS_HEADERS)
        row[USERS_HEADERS.index("chat_id")] = "777"
        row[USERS_HEADERS.index("access_status")] = status
        ws.grid.append(row)
        return ws

    def test_granted(self):
        ws = self._ws_with_status("granted")
        assert has_access(ws, 777) is True
        assert has_access_in_map(load_users_map(ws), 777) is True

    def test_blocked(self):
        ws = self._ws_with_status("blocked")
        assert has_access(ws, 777) is False

    def test_revoked(self):
        ws = self._ws_with_status("revoked")
        assert has_access(ws, 777) is False

    def test_missing_user(self):
        ws = self._ws_with_status("granted")
        assert has_access(ws, 999) is False

    def test_float_chat_id_normalization(self):
        ws = self._ws_with_status("granted")
        # Google Sheets mógł zapisać chat_id jako liczbę
        ws.grid[1][USERS_HEADERS.index("chat_id")] = "777.0"
        assert has_access(ws, 777) is True
        assert load_users_map(ws).get("777") is not None


# ============================================================================
# set_blocked — Users first, czyszczenie profilu
# ============================================================================
class TestSetBlocked:
    def _prepared(self):
        ws = FakeWorksheet()
        upsert_access(ws, 888, "pl", "referral", "2026-09-30 10:00:00", "v1")
        # symulujemy profil dopisany ręcznie (jak zrobi to PR2)
        headers = [h.lower() for h in USERS_HEADERS]
        rec_row = ws.grid[1]
        rec_row[headers.index("profile_status")] = "active"
        rec_row[headers.index("lat_round")] = "54.5"
        rec_row[headers.index("lon_round")] = "18.5"
        rec_row[headers.index("location_label")] = "Hel"
        rec_row[headers.index("location_source")] = "gps"
        rec_row[headers.index("location_consent_at")] = "2026-09-30 11:00:00"
        return ws

    def test_blocked_marks_and_clears_profile(self):
        ws = self._prepared()
        ok = set_blocked(ws, 888, "telegram_blocked", "2026-09-30 12:00:00", clear_profile=True)
        assert ok is True
        assert ws.calls["update_cells"] == 1  # jeden batch

        rec = ws.record(888)
        assert rec["access_status"] == "blocked"
        assert rec["blocked_at"] == "2026-09-30 12:00:00"
        assert rec["blocked_reason"] == "telegram_blocked"
        assert rec["profile_status"] == "none"
        assert rec["lat_round"] == "" and rec["lon_round"] == ""
        assert rec["location_label"] == "" and rec["location_source"] == ""
        assert rec["location_consent_at"] == ""
        # lang zostaje (język interfejsu, nie dane lokalizacyjne)
        assert rec["lang"] == "pl"
        assert has_access(ws, 888) is False

    def test_blocked_keeps_profile_when_clear_profile_false(self):
        ws = self._prepared()
        set_blocked(ws, 888, "unknown", "2026-09-30 12:00:00", clear_profile=False)
        rec = ws.record(888)
        assert rec["access_status"] == "blocked"
        assert rec["lat_round"] == "54.5"  # profil nietknięty

    def test_unknown_reason_mapped_to_unknown(self):
        ws = self._prepared()
        set_blocked(ws, 888, "jakis-blad", "2026-09-30 12:00:00")
        assert ws.record(888)["blocked_reason"] == "unknown"

    def test_legacy_user_without_row_returns_false(self):
        ws = FakeWorksheet()
        assert set_blocked(ws, 999, "unknown", "2026-09-30 12:00:00") is False


# ============================================================================
# clear_profile — access zostaje
# ============================================================================
def test_clear_profile_keeps_access():
    ws = FakeWorksheet()
    upsert_access(ws, 555, "en", "public_beta", "2026-09-30 10:00:00", "v1")
    headers = [h.lower() for h in USERS_HEADERS]
    ws.grid[1][headers.index("profile_status")] = "active"
    ws.grid[1][headers.index("lat_round")] = "52.23"
    ws.grid[1][headers.index("lon_round")] = "21.01"
    ws.grid[1][headers.index("location_label")] = "Warszawa"

    ok = clear_profile(ws, 555, "2026-09-30 13:00:00")
    assert ok is True

    rec = ws.record(555)
    assert rec["access_status"] == "granted"      # access bez zmian!
    assert rec["access_granted_at"] == "2026-09-30 10:00:00"
    assert rec["profile_status"] == "none"
    assert rec["lat_round"] == "" and rec["lon_round"] == ""
    assert rec["location_label"] == "" and rec["location_source"] == ""
    assert rec["profile_updated_at"] == "2026-09-30 13:00:00"
    assert has_access(ws, 555) is True

    # powtórne czyszczenie nieistniejącego użytkownika
    assert clear_profile(ws, 555, "2026-09-30 13:05:00") is True  # wiersz istnieje
    assert clear_profile(FakeWorksheet(), 555, "2026-09-30 13:05:00") is False


# ============================================================================
# delete_user_row — hard delete
# ============================================================================
def test_delete_user_row():
    ws = FakeWorksheet()
    upsert_access(ws, 333, "pl", "referral", "2026-09-30 10:00:00", "v1")
    upsert_access(ws, 444, "en", "referral", "2026-09-30 10:00:00", "v1")

    assert delete_user_row(ws, 333) is True
    assert ws.calls["delete_rows"] == 1
    assert ws.record(333) is None
    assert ws.record(444) is not None   # sąsiad nietknięty
    assert delete_user_row(ws, 333) is False  # już nie ma


# ============================================================================
# find_row_by_chat_id / get_user / get_lang
# ============================================================================
def test_find_row_by_chat_id():
    ws = FakeWorksheet()
    upsert_access(ws, 111, "pl", "referral", "2026-09-30 10:00:00", "v1")
    upsert_access(ws, 222, "en", "referral", "2026-09-30 10:00:00", "v1")
    assert find_row_by_chat_id(ws, 111) == 2
    assert find_row_by_chat_id(ws, 222) == 3
    assert find_row_by_chat_id(ws, 999) is None


def test_get_user_returns_record_with_row():
    ws = FakeWorksheet()
    upsert_access(ws, 111, "no", "referral", "2026-09-30 10:00:00", "v1")
    u = get_user(ws, 111)
    assert u is not None
    assert u["_row"] == 2
    assert u["access_status"] == "granted"
    assert get_user(ws, 999) is None


def test_get_lang():
    ws = FakeWorksheet()
    upsert_access(ws, 111, "no", "referral", "2026-09-30 10:00:00", "v1")
    m = load_users_map(ws)
    assert get_lang(m, 111) == "no"
    assert get_lang(m, 999) is None
    assert get_lang({}, 111) is None


def test_header_case_and_whitespace_tolerated():
    # Nagłówki z differently-cased / ze spacjami muszą działać
    headers = [h.upper() for h in USERS_HEADERS]
    ws = FakeWorksheet(headers=headers)
    ok = upsert_access(ws, 111, "pl", "referral", "2026-09-30 10:00:00", "v1")
    assert ok is True
    rec = ws.record(111)
    assert rec["access_status"] == "granted"
    assert rec["profile_status"] == "none"


# ============================================================================
# db_cleanup.classify_block_reason
# ============================================================================
def test_classify_block_reason():
    from db_cleanup import classify_block_reason, mark_user_as_blocked
    assert classify_block_reason("Forbidden: bot was blocked by the user") == "telegram_blocked"
    assert classify_block_reason("Bad Request: kicked from the group") == "kicked_from_group"
    assert classify_block_reason("Forbidden: user is deactivated") == "user_deactivated"
    assert classify_block_reason("Bad Request: chat not found") == "chat_not_found"
    assert classify_block_reason("Something totally different") == "unknown"
    assert classify_block_reason("") == "unknown"
    assert classify_block_reason(None) == "unknown"


# ============================================================================
# location_bot — czyste helpery bramki (bez sieci i bez Telegrama)
# ============================================================================
import location_bot as lb  # smoke: import przechodzi bez sieci i bez kluczy


class TestLocationBotHelpers:
    def test_is_guest_trigger(self):
        f = lb._is_guest_trigger
        assert f(".n Hel", "MyBot") is True
        assert f("?d Paryż", "MyBot") is True
        assert f(".f", "MyBot") is True
        assert f(".p Miasto", "MyBot") is True
        assert f("Hej @mybot, jaka pogoda?", "MyBot") is True
        assert f("/day", "MyBot") is False          # komendy idą inną ścieżką
        assert f("/start TOKEN", "MyBot") is False
        assert f("Cześć, co tam?", "MyBot") is False
        assert f("", "MyBot") is False

    def test_chat_has_access_users_wins(self):
        f = lb._chat_has_access
        # granted w Users -> dostęp
        assert f({"111": {"access_status": "granted"}}, [], 111) is True
        # blocked w Users -> brak dostępu NAWET przy wierszu legacy
        assert f({"111": {"access_status": "blocked"}},
                 [{"Chat ID": "111"}], 111) is False
        # revoked w Users -> brak dostępu
        assert f({"111": {"access_status": "revoked"}}, [], 111) is False
        # brak w Users, jest w legacy -> dostęp (implikowany)
        assert f({}, [{"Chat ID": "222", "Lat": "1"}], 222) is True
        # nigdzie -> brak
        assert f({}, [{"Chat ID": "222"}], 333) is False

    def test_norm_lang(self):
        f = lb._norm_lang
        assert f("pl") == "pl"
        assert f("NB") == "no"
        assert f("  no ") == "no"
        assert f("it") is None
        assert f("") is None
        assert f(None) is None

    def test_pending_save_ttl_expires_and_does_not_return_location(self):
        lb.PENDING_SAVE.clear()
        pending = lb._put_pending_save(123456, 52.23, 21.01, "Warszawa", "pl", "city", now_ts=1000)
        assert pending["expires_ts"] == 1000 + lb.PENDING_SAVE_TTL_SEC
        assert lb._get_pending_save(123456, now_ts=1001)["city"] == "Warszawa"
        assert lb._get_pending_save(123456, now_ts=1000 + lb.PENDING_SAVE_TTL_SEC) is None
        assert "123456" not in lb.PENDING_SAVE

    def test_delete_me_clears_pending_city_and_save_ram(self, monkeypatch):
        # /delete_me czyści wszystkie stany RAM (także PENDING_DELETE z flow
        # potwierdzenia); send_reply mockujemy, żeby test nie próbował realnie
        # wołać Telegrama, a pauzę między komunikatami (#7) skracamy do zera.
        sent = []
        sleeps = []
        monkeypatch.setattr(lb, "send_reply", lambda cid, txt, **kw: sent.append((cid, txt)))
        monkeypatch.setattr(lb.time, "sleep", lambda s: sleeps.append(s))
        lb._set_pending_city("123456", lb.CTX_ONEOFF_DAY)
        lb._set_pending_delete("123456")
        lb._put_pending_save(123456, 52.23, 21.01, "Warszawa", "pl", "city")
        lb._handle_delete_me(123456, "pl", None, _LegacySheetStub())
        assert "123456" not in lb.PENDING_CITY
        assert "123456" not in lb.PENDING_SAVE
        assert "123456" not in lb.PENDING_DELETE
        # POPRAWKA #7: dokładnie dwa komunikaty — delete_me_done (bez linku),
        # po pauzie osobny, pełny no_access (z linkiem zaproszenia).
        assert len(sent) == 2, sent
        assert "usunąłem twoje dane" in sent[0][1].lower()
        assert "http" not in sent[0][1]
        assert "brak dostępu" in sent[1][1].lower() and "http" in sent[1][1]
        assert sleeps == [lb.DELETE_NOTICE_DELAY_SEC]


class _LegacySheetStub:
    """Minimalna atrapa Formularz dla _delete_legacy_rows (col_values/delete_rows)."""

    def __init__(self):
        self.grid = [
            ["Sygnatura czasowa", "Chat ID", "Imię", "Miasto", "Lat", "Lon"],
            ["2026-01-01", "123456", "Ala", "Warszawa", "52", "21"],
            ["2026-01-02", "123456", "Ala", "Warszawa", "52", "21"],
            ["2026-01-03", "BLOCKED_123456", "Ala", "", "", ""],
            ["2026-01-04", "999", "Inny", "Hel", "54", "18"],
        ]

    def col_values(self, col):
        return [row[col - 1] if col - 1 < len(row) else "" for row in self.grid]

    def delete_rows(self, idx):
        del self.grid[idx - 1]


def test_delete_me_legacy_rows_deleted_from_the_end():
    stub = _LegacySheetStub()
    deleted = lb._delete_legacy_rows(stub, 123456)
    assert deleted == 3  # 2 zwykłe + 1 z BLOCKED_
    # Zostaje tylko obcy użytkownik
    assert [r[1] for r in stub.grid[1:]] == ["999"]
    # Kolumny pozostałych wierszy nietknięte
    assert stub.grid[1][2] == "Inny"
