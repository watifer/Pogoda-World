"""
test_access_gate.py — Testy end-to-end bramki dostępu PR1 (RODO/access migration).

Uruchomienie: pytest test_access_gate.py -v

Testy symulują PEŁNĄ pętlę location_bot.main_bot() z atrapami Telegram API
oraz Google Sheets (Formularz + Users + Bot_State). Zero sieci, zero kluczy,
zero produkcyjnych danych.

Pokrywane scenariusze:
- obcy użytkownik: komendy/pinezka/skróty .d/.n/.f -> odmowa + link zaproszenia,
- /start <token>: access TYLKO w Users (bez lat/lon, bez wiersza w Formularz),
- użytkownik z samym access-em: /day //now //future -> "profil nieaktywny",
- użytkownik legacy (Formularz): pełna funkcjonalność bez zmian,
- Users blocked wygrywa z legacy; BLOCKED_ legacy traci dostęp,
- /delete_me: hard delete Users + delete_rows Formularz + czyszczenie RAM,
- /forget_location: czyści profil legacy, dostęp zostaje,
- /privacy i /my_data działają bez accessu.
"""

import os

import pytest
import requests

os.environ.setdefault("PUBLIC_BETA_CODES", "BETAX1")

import location_bot as lb
import users_store

FORM_HEADERS = ["Sygnatura czasowa", "Chat ID", "Imię", "Miasto", "Lat", "Lon",
                "Raport poranny", "Aktualizacja", "Lang"]
USERS_HEADERS = users_store.CANONICAL_HEADERS


# ============================================================================
# ATRAPY TELEGRAM + SHEETS
# ============================================================================
class FakeFormularz:
    """Imituje zakładkę Formularz (legacy) dla potrzeb main_bot i privacy handlers."""

    def __init__(self, rows=None):
        self.rows = [list(r) for r in (rows or [])]

    def get_all_records(self, value_render_option=None):
        return [dict(zip(FORM_HEADERS, r)) for r in self.rows]

    def row_values(self, idx):
        if idx == 1:
            return FORM_HEADERS[:]
        return self.rows[idx - 2][:] if idx - 2 < len(self.rows) else [""] * len(FORM_HEADERS)

    def col_values(self, col):
        return [FORM_HEADERS[col - 1] if col - 1 < len(FORM_HEADERS) else ""] + \
               [r[col - 1] if col - 1 < len(r) else "" for r in self.rows]

    def find(self, value, in_column=None):
        class _C:
            pass
        for i, r in enumerate(self.rows):
            if in_column and str(r[in_column - 1]).strip() == str(value):
                c = _C()
                c.row = i + 2
                return c
        raise LookupError("CellNotFound")

    def update_cell(self, row, col, value):
        while len(self.rows) < row - 1:
            self.rows.append([""] * len(FORM_HEADERS))
        while len(self.rows[row - 2]) < col:
            self.rows[row - 2].append("")
        self.rows[row - 2][col - 1] = value

    def update_cells(self, cells):
        for c in cells:
            self.update_cell(c.row, c.col, c.value)

    def delete_rows(self, idx):
        del self.rows[idx - 2]

    def insert_row(self, values, index=2, value_input_option=None):
        self.rows.insert(index - 2, list(values))

    # pomocnicze dla asercji
    def chat_ids(self):
        return [str(r[1]).strip() for r in self.rows]


class FakeUsersTab:
    """Imituje zakładkę Users — tylko API używane przez users_store."""

    def __init__(self):
        self.grid = [list(USERS_HEADERS)]

    def get_all_values(self):
        return [r[:] for r in self.grid]

    def row_values(self, idx):
        return self.grid[idx - 1][:] if idx - 1 < len(self.grid) else [""] * len(USERS_HEADERS)

    def col_values(self, col):
        return [r[col - 1] if col - 1 < len(r) else "" for r in self.grid]

    def append_row(self, values, **kwargs):
        self.grid.append([str(v) for v in values])

    def update_cells(self, cells, **kwargs):
        for cell in cells:
            while len(self.grid) < cell.row:
                self.grid.append([""] * len(USERS_HEADERS))
            while len(self.grid[cell.row - 1]) < cell.col:
                self.grid[cell.row - 1].append("")
            self.grid[cell.row - 1][cell.col - 1] = cell.value

    def delete_rows(self, idx):
        del self.grid[idx - 1]

    def record(self, chat_id):
        headers = [h.lower() for h in self.grid[0]]
        for row in self.grid[1:]:
            if users_store.norm_chat_id(row[0]) == users_store.norm_chat_id(chat_id):
                return dict(zip(headers, row))
        return None


class FakeBotState:
    def acell(self, _a):
        class _V:
            value = "0"
        return _V()

    def update_acell(self, _a, _v):
        pass


class FakeGC:
    def __init__(self, formularz_rows=None):
        self.formularz = FakeFormularz(formularz_rows)
        self.users = FakeUsersTab()
        self.state = FakeBotState()

    def open(self, title):
        return self

    def worksheet(self, tab):
        return {"Formularz": self.formularz, "Users": self.users, "Bot_State": self.state}[tab]


class _Resp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def json(self):
        return self._p


class BotHarness:
    """Patched location_bot z atrapami; rejestruje wysłane wiadomości i karty."""

    def __init__(self, monkeypatch, formularz_rows=None):
        self.gc = FakeGC(formularz_rows)
        self.sent = []        # (chat_id, text)
        self.guest_calls = []  # teksty przekazane do handle_guest_now
        self.cards = []       # chat_id z _send_card_to_user

        updates = []

        def fake_get(url, **kw):
            if "getUpdates" in url:
                out = updates[:]
                updates.clear()
                return _Resp({"ok": True, "result": out})
            return _Resp({"ok": True})

        def fake_post(url, json=None, data=None, files=None, timeout=None, **kw):
            if "sendMessage" in url and json:
                self.sent.append((json["chat_id"], json["text"]))
            return _Resp({"ok": True, "result": {}})

        def fake_guest_now(**kw):
            text = (kw.get("message", {}).get("text") or "").strip()
            self.guest_calls.append(text)
            if not text or text.startswith("/"):
                return False
            low = text.lower()
            return (f"@{lb.BOT_USERNAME.lower()}" in low
                    or low.startswith(lb.GUEST_SHORTCUT_PREFIXES))

        monkeypatch.setattr(requests, "get", fake_get)
        monkeypatch.setattr(requests, "post", fake_post)
        monkeypatch.setattr(lb, "get_google_client", lambda: self.gc)
        monkeypatch.setattr(lb, "get_offset", lambda gc: 0)
        monkeypatch.setattr(lb, "save_offset", lambda gc, off: None)
        monkeypatch.setattr(lb, "handle_guest_now", fake_guest_now)
        monkeypatch.setattr(lb, "_send_card_to_user",
                            lambda user, **kw: (self.cards.append(user["chat_id"]), True)[1])
        monkeypatch.setattr(lb, "_load_users_from_sheet", self.gc.formularz.get_all_records)
        lb.PENDING_CITY.clear()
        self._updates = updates

    def msg(self, chat_id, text, reply=False, lang="pl"):
        m = {"update_id": 100000 + len(self.sent) * 7 + len(self._updates),
             "message": {"chat": {"id": chat_id, "type": "private"},
                         "from": {"language_code": lang, "first_name": "Ala"},
                         "text": text}}
        if reply:
            m["message"]["reply_to_message"] = {"message_id": 1}
        return m

    def pin(self, chat_id, reply=False):
        m = self.msg(chat_id, None)
        m["message"].pop("text")
        m["message"]["location"] = {"latitude": 54.5, "longitude": 18.5}
        if reply:
            m["message"]["reply_to_message"] = {"message_id": 1}
        return m

    def run(self, update):
        """Jedna paczka update'ów przez main_bot()."""
        self._updates.append(update)
        lb.main_bot()

    def replies(self, cid):
        return [t for (c, t) in self.sent if c == cid]

    @property
    def users(self):
        return self.gc.users

    def has_reply(self, cid, needle):
        return any(needle in t for t in self.replies(cid))


LEGACY_ROW = ["2026-01-01 08:00:00", "700", "LegacyUser", "Gdańsk",
              "54.35", "18.65", "08:00", "14:00", "pl"]
BLOCKED_LEGACY_ROW = ["2026-01-02 08:00:00", "BLOCKED_701", "BlockedLegacy", "Hel",
                      "54.6", "18.8", "08:00", "14:00", "pl"]


@pytest.fixture
def bot(monkeypatch):
    return BotHarness(monkeypatch, formularz_rows=[LEGACY_ROW, BLOCKED_LEGACY_ROW])


# ============================================================================
# SCENARIUSZE
# ============================================================================
class TestStrangerIsRefused:
    def test_day_now_future_refused_with_invite(self, bot):
        for cmd in ("/day", "/now", "/future"):
            bot.run(bot.msg(100, cmd))
            assert bot.has_reply(100, "Brak dostępu"), f"brak odmowy dla {cmd}"
            assert bot.has_reply(100, "https://"), "odmowa bez linku zaproszenia"
        assert bot.cards == []

    def test_other_commands_refused(self, bot):
        for cmd in ("/menu", "/miasto", "/zapros", "/info"):
            bot.run(bot.msg(100, cmd))
            assert bot.has_reply(100, "Brak dostępu"), f"brak odmowy dla {cmd}"

    def test_guest_shortcut_refused_and_guest_handler_not_called(self, bot):
        bot.run(bot.msg(100, ".n Hel"))
        assert bot.has_reply(100, "Brak dostępu")
        assert bot.guest_calls == [], "gate powinien odmówić PRZED handle_guest_now"

    def test_mention_refused(self, bot):
        bot.run(bot.msg(100, f"@{lb.BOT_USERNAME} jaka pogoda w Rzymie?"))
        assert bot.has_reply(100, "Brak dostępu")
        assert bot.guest_calls == []

    def test_pin_as_reply_refused_but_plain_pin_silent(self, bot):
        bot.run(bot.pin(100, reply=True))
        assert bot.has_reply(100, "Brak dostępu")
        before = len(bot.sent)
        bot.run(bot.pin(100, reply=False))
        assert len(bot.sent) == before, "zwykła pinezka (bez reply) ma być ignorowana po cichu"

    def test_plain_text_stays_silent(self, bot):
        bot.run(bot.msg(100, "cześć, co potrafisz?"))
        assert bot.replies(100) == []

    def test_invalid_start_token(self, bot):
        bot.run(bot.msg(999, "/start ZLYKOD"))
        assert bot.has_reply(999, "Nieprawidłowy")
        assert bot.users.record(999) is None


class TestStartRegistersAccessOnly:
    def test_start_public_beta_code(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))

        rec = bot.users.record(100)
        assert rec is not None, "brak wiersza w Users"
        assert rec["access_status"] == "granted"
        assert rec["access_source"] == "public_beta"
        assert rec["profile_status"] == "none"
        assert rec["privacy_version_seen"] == lb.PRIVACY_VERSION
        assert rec["lang"] == "pl"
        # RODO: przy /start NIE zapisujemy żadnej lokalizacji
        assert rec["lat_round"] == "" and rec["lon_round"] == ""
        assert rec["location_label"] == "" and rec["location_source"] == ""
        assert rec["location_consent_at"] == "" and rec["location_consent_version"] == ""
        # RODO: brak wiersza w legacy Formularz
        assert "100" not in bot.gc.formularz.chat_ids()

    def test_onboarding_is_privacy_first(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        assert bot.has_reply(100, "Dostęp aktywowany")
        assert bot.has_reply(100, "/privacy")

    def test_start_without_code_hint(self, bot):
        bot.run(bot.msg(100, "/start"))
        assert bot.has_reply(100, "Brak dostępu")

    def test_referral_from_access_only_user(self, bot):
        import base64
        bot.run(bot.msg(100, "/start BETAX1"))
        token = base64.urlsafe_b64encode(b"100").decode().rstrip("=")
        bot.run(bot.msg(200, f"/start {token}"))
        assert bot.users.record(200) is not None, "referral od usera z samym access-em odrzucony"
        assert bot.has_reply(200, "Dostęp aktywowany")


class TestAccessWithoutProfile:
    def test_day_now_future_ask_for_profile_not_access(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        for cmd in ("/day", "/now", "/future"):
            bot.run(bot.msg(100, cmd))
            assert bot.has_reply(100, "Profil nie jest jeszcze aktywny"), cmd
            assert not bot.has_reply(100, "Brak dostępu"), cmd
        assert bot.cards == []

    def test_city_and_pin_ask_for_profile(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/miasto Warszawa"))
        assert bot.has_reply(100, "Profil nie jest jeszcze aktywny")
        assert "100" not in bot.gc.formularz.chat_ids(), "BŁĄD RODO: /miasto zapisało profil przy braku zgody"

    def test_guest_shortcut_works_with_access(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, ".n Hel"))
        # handle_guest_now jest wołane dla każdej wiadomości (dla komend zwraca False),
        # więc na liście jest też "/start BETAX1" — ale ".n Hel" MUSI do niego trafić
        assert bot.guest_calls[-1] == ".n Hel", "skrót .n nie doszedł do trybu gościa"
        assert bot.has_reply(100, "Dostęp aktywowan")  # a wcześniej rejestracja się udała


class TestLegacyUserUnchanged:
    def test_legacy_day_generates_card(self, bot):
        bot.run(bot.msg(700, "/day"))
        assert not bot.has_reply(700, "Brak dostępu")
        assert bot.cards == ["700"]

    def test_blocked_legacy_refused(self, bot):
        bot.run(bot.msg(701, "/day"))
        assert bot.has_reply(701, "Brak dostępu")

    def test_users_blocked_wins_over_legacy_row(self, bot, monkeypatch):
        # wiersz legacy CZYSTY (bez BLOCKED_), ale Users mówi blocked -> brak dostępu
        bot.gc.formularz.rows.append(
            ["2026-01-03 08:00:00", "800", "BlockedInUsers", "Poznań",
             "52.4", "16.9", "08:00", "14:00", "pl"])
        bot.gc.users.grid.append(["800"] + [""] * (len(USERS_HEADERS) - 1))
        bot.gc.users.grid[-1][USERS_HEADERS.index("access_status")] = "blocked"

        bot.run(bot.msg(800, "/day"))
        assert bot.has_reply(800, "Brak dostępu")


class TestPrivacyCommands:
    def test_privacy_works_without_access(self, bot):
        bot.run(bot.msg(555, "/privacy"))
        assert bot.has_reply(555, "PRYWATNOŚĆ")
        assert bot.has_reply(555, "https://")
        assert lb.PRIVACY_VERSION.replace("-", "-") in bot.replies(555)[0]

    def test_my_data_shows_access(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/my_data"))
        assert bot.has_reply(100, "TWOJE DANE")
        assert bot.has_reply(100, "aktywny")

    def test_my_data_legacy_shows_location(self, bot):
        bot.run(bot.msg(700, "/my_data"))
        assert bot.has_reply(700, "legacy")
        assert bot.has_reply(700, "Gdańsk")

    def test_my_data_stranger_no_data(self, bot):
        bot.run(bot.msg(555, "/my_data"))
        assert bot.has_reply(555, "Nie znaleziono")

    def test_forget_location_legacy_clears_profile_keeps_access(self, bot):
        bot.run(bot.msg(700, "/forget_location"))
        assert bot.has_reply(700, "Lokalizacja usunięta")

        row = next(r for r in bot.gc.formularz.rows if str(r[1]).strip() == "700")
        assert row[FORM_HEADERS.index("Lat")] == ""
        assert row[FORM_HEADERS.index("Lon")] == ""
        assert row[FORM_HEADERS.index("Miasto")] == ""
        # dostęp (wiersz) zostaje
        assert "700" in bot.gc.formularz.chat_ids()
        bot.run(bot.msg(700, "/day"))
        assert not bot.has_reply(700, "Brak dostępu")

    def test_forget_location_users_row(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        # ręcznie dopisujemy profil (jak zrobi to PR2)
        bot.gc.users.grid[1][USERS_HEADERS.index("profile_status")] = "active"
        bot.gc.users.grid[1][USERS_HEADERS.index("lat_round")] = "52.23"
        bot.gc.users.grid[1][USERS_HEADERS.index("lon_round")] = "21.01"
        bot.gc.users.grid[1][USERS_HEADERS.index("location_label")] = "Warszawa"

        bot.run(bot.msg(100, "/forget_location"))
        rec = bot.users.record(100)
        assert rec["lat_round"] == "" and rec["lon_round"] == ""
        assert rec["location_label"] == ""
        assert rec["profile_status"] == "none"
        assert rec["access_status"] == "granted", "forget_location nie może odbierać accessu"

    def test_delete_me_hard_delete_everywhere(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        lb.PENDING_CITY["100"] = 9999999999.0

        bot.run(bot.msg(100, "/delete_me"))
        assert bot.users.record(100) is None, "wiersz Users nieusunięty"
        assert "100" not in lb.PENDING_CITY, "PENDING_CITY nie wyczyszczone"

        # dostęp znika w kolejnej paczce
        bot.run(bot.msg(100, "/day"))
        assert bot.has_reply(100, "Brak dostępu")

    def test_delete_me_legacy_rows_removed_including_blocked(self, bot):
        bot.gc.formularz.rows.append(["2026-01-04 08:00:00", "701", "BlockedLegacy", "Hel", "54.6", "18.8", "", "", "pl"])
        bot.run(bot.msg(701, "/delete_me"))
        assert "701" not in bot.gc.formularz.chat_ids()
        assert "BLOCKED_701" not in bot.gc.formularz.chat_ids()
        assert "700" in bot.gc.formularz.chat_ids(), "usunięto wiersze obcego użytkownika!"

    def test_delete_me_stranger_gets_no_data(self, bot):
        bot.run(bot.msg(555, "/delete_me"))
        assert bot.has_reply(555, "Nie znaleziono")


class TestBlockedMarking:
    def test_mark_user_as_blocked_users_first_then_legacy(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.gc.users.grid[1][USERS_HEADERS.index("profile_status")] = "active"
        bot.gc.users.grid[1][USERS_HEADERS.index("lat_round")] = "52.23"

        from db_cleanup import mark_user_as_blocked
        mark_user_as_blocked(bot.gc, 100, reason="telegram_blocked")

        rec = bot.users.record(100)
        assert rec["access_status"] == "blocked"
        assert rec["blocked_reason"] == "telegram_blocked"
        assert rec["blocked_at"], "brak blocked_at"
        assert rec["lat_round"] == "", "profil nie został wyczyszczony"

        # fallback legacy (u usera 100 nie ma wiersza w Formularz — nic do zrobienia)
        # dla użytkownika legacy 700: prefix BLOCKED_
        mark_user_as_blocked(bot.gc, 700, reason="kicked_from_group")
        assert "BLOCKED_700" in bot.gc.formularz.chat_ids()
        # i w Users (jeśli był wiersz) — 700 nie miał wiersza w Users, więc tylko Formularz
