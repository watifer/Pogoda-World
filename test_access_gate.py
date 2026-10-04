"""
test_access_gate.py — Testy end-to-end bramki dostępu PR1 (RODO/access migration).

Uruchomienie: pytest test_access_gate.py -v

Testy symulują PEŁNĄ pętlę location_bot.main_bot() z atrapami Telegram API
oraz Google Sheets (Formularz + Users + Bot_State). Zero sieci, zero kluczy,
zero produkcyjnych danych.

Pokrywane scenariusze:
- obcy użytkownik: komendy/pinezka/skróty .d/.n/.f -> odmowa + link zaproszenia,
- /start <token>: access TYLKO w Users (bez lat/lon, bez wiersza w Formularz),
- użytkownik z access-em bez profilu: /day Warszawa, /now Hel, /future Berlin
  generują one-off + PENDING_SAVE, bez automatycznego zapisu,
- PR2 UX cleanup: /dzien, /teraz, /trend bez profilu pytają o SAMĄ miejscowość,
  wpisane miasto generuje kartę jednorazową i NIC nie zapisuje, a po karcie nie
  pokazujemy już /save_location ani /oneoff (tylko użytą lokalizację),
- /miasto to jedyny widoczny flow zapisu lokalizacji (zapis bez karty),
- aliasy PL są case-insensitive (/bezGPS, /BEZGPS, /usunDane, /USUNDANE),
- /usunDane i /delete_me wymagają potwierdzenia przyciskiem (bez callbacków),
- użytkownik legacy (Formularz): pełna funkcjonalność bez zmian,
- Users blocked wygrywa z legacy; BLOCKED_ legacy traci dostęp,
- kasacja danych: hard delete Users + delete_rows Formularz + czyszczenie RAM,
- /forget_location (/bezGPS): czyści profil, dostęp zostaje, rozróżnia brak
  zapisanej lokalizacji,
- /privacy i /my_data działają bez accessu,
- komendy w tekstach UI nie są w backtickach (klikalność na telefonie).
"""

import os

import pytest
import requests

# UWAGA: celowo NIE ustawiamy tu PUBLIC_BETA_CODES. Kody beta ustawia BotHarness
# przez monkeypatch.setenv (odporne na kolejność importów modułów testowych
# oraz na .env użytkownika, który load_dotenv() mógł już wczytać).

import location_bot as lb
import users_store

FORM_HEADERS = ["Sygnatura czasowa", "Chat ID", "Imię", "Miasto", "Lat", "Lon",
                "Raport poranny", "Aktualizacja", "Lang"]
USERS_HEADERS = users_store.CANONICAL_HEADERS


def freeze_local_hour(monkeypatch, hour, minute=0):
    """
    Deterministycznie zamraża datetime.now() na ustaloną godzinę.

    Konieczne dla testów /day: karta dzienna ma w produkcji (zachowanie
    ISTNIEJĄCE NA STAGING, sprzed PR1) okno dostępności 05:00–15:59 czasu
    lokalnego użytkownika. Bez zamrożenia test /day jest nie-deterministyczny
    — failuje, gdy suite odpalany jest poza oknem. Handler /day importuje
    datetime na czas wywołania (`from datetime import datetime`), więc
    podmiana atrybutu modułu `datetime` jest w nim widoczna.
    """
    import datetime as _dt
    real = _dt.datetime

    class _FrozenDateTime(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 9, 30, hour, minute, tzinfo=tz)

    monkeypatch.setattr(_dt, "datetime", _FrozenDateTime)


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
        self.markups = []     # reply_markup każdej wysłanej wiadomości (index = sent)
        self.guest_calls = []  # teksty przekazane do handle_guest_now
        self.cards = []       # chat_id z _send_card_to_user (legacy)
        self.oneoffs = []     # (chat_id, lat, lon, city, lang, card_type)

        # Kody beta ustawiane PER-TEST przez monkeypatch (auto-undo po teście):
        # 1) działa niezależnie od kolejności importu modułów testowych,
        # 2) nadpisuje PUBLIC_BETA_CODES z .env wczytanego przez load_dotenv(),
        # 3) _public_codes() czyta env na czas WYWOŁANIA, nie importu.
        monkeypatch.setenv("PUBLIC_BETA_CODES", "BETAX1")
        monkeypatch.delenv("PUBLIC_BETA_CODE", raising=False)

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
                self.markups.append(json.get("reply_markup"))
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
        monkeypatch.setattr(
            lb, "_send_oneoff_report",
            lambda chat_id, lat, lon, city, lang, card_type:
                (self.oneoffs.append((chat_id, lat, lon, city, lang, card_type)), True)[1],
        )
        monkeypatch.setattr(
            lb, "get_coords_from_city",
            lambda city, lang="pl": (52.22972, 21.01223, f"{city}, Polska"),
        )
        monkeypatch.setattr(
            lb, "get_city_from_coords",
            lambda lat, lon, lang="pl": "Hel" if float(lat) >= 54 else "Warszawa",
        )
        monkeypatch.setattr(lb, "_load_users_from_sheet", self.gc.formularz.get_all_records)
        lb.PENDING_CITY.clear()
        lb.PENDING_SAVE.clear()
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

    def webapp_location(self, chat_id, lat=50.0614, lon=19.9366, lang="pl"):
        return {
            "update_id": 200000 + len(self.sent) * 7 + len(self._updates),
            "message": {
                "chat": {"id": chat_id, "type": "private"},
                "from": {"language_code": lang, "first_name": "Ala"},
                "web_app_data": {
                    "data": '{"type":"set_location","lat":%s,"lon":%s}' % (lat, lon)
                },
            },
        }

    def run(self, update):
        """Jedna paczka update'ów przez main_bot()."""
        self._updates.append(update)
        lb.main_bot()

    def replies(self, cid):
        return [t for (c, t) in self.sent if c == cid]

    def markups_for(self, cid):
        """reply_markup wiadomości wysłanych do danego czatu (None = brak)."""
        return [m for (c, _t), m in zip(self.sent, self.markups) if c == cid]

    def keyboards_for(self, cid):
        """Tylko klawiatury (reply/inline) — bez remove_keyboard."""
        return [m for m in self.markups_for(cid) if m and "keyboard" in m]

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
        for cmd in ("/day", "/now", "/future", "/day Warszawa"):
            bot.run(bot.msg(100, cmd))
            assert bot.has_reply(100, "Brak dostępu"), f"brak odmowy dla {cmd}"
            assert bot.has_reply(100, "https://"), "odmowa bez linku zaproszenia"
        assert bot.cards == []
        assert bot.oneoffs == []  # PR2-g: argument miasta nie omija gate accessu

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

    def test_onboarding_is_short_and_points_to_city_and_data(self, bot):
        # PR2 UX cleanup: krótki onboarding, bez obietnicy automatycznych raportów
        # (te pojawią się dopiero w PR3) i bez technicznych komend /save_location.
        bot.run(bot.msg(100, "/start BETAX1"))
        assert bot.has_reply(100, "Dostęp aktywowany")
        assert bot.has_reply(100, "/dzien")
        assert bot.has_reply(100, "/teraz")
        assert bot.has_reply(100, "/trend")
        assert bot.has_reply(100, "/miasto")
        assert bot.has_reply(100, "/dane")
        assert not bot.has_reply(100, "/save_location")
        assert not bot.has_reply(100, "/oneoff")
        assert not bot.has_reply(100, "automatyczne raporty")
        assert not bot.has_reply(100, "`")  # komendy klikalne => bez backticków

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
    def test_day_city_generates_oneoff_without_saving_profile(self, bot, monkeypatch):
        # PR2-a: Users(access=granted, profile=none) może dostać kartę dla
        # miasta, lecz arkusz Users pozostaje bez profilu. Zapis lokalizacji jest
        # świadomą akcją wyłącznie przez /miasto.
        freeze_local_hour(monkeypatch, 10, 0)  # okno karty dziennej 05:00-15:59
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/day Warszawa"))

        assert bot.oneoffs == [(100, 52.22972, 21.01223, "Warszawa", "pl", "day")]
        rec = bot.users.record(100)
        assert rec["profile_status"] == "none"
        assert rec["lat_round"] == "" and rec["lon_round"] == ""
        assert "100" not in bot.gc.formularz.chat_ids(), "one-off nie może tworzyć legacy profilu"
        assert lb.PENDING_SAVE["100"]["source"] == "city"
        # PR2 UX cleanup: po karcie pokazujemy TYLKO użytą lokalizację
        assert bot.has_reply(100, "Użyta lokalizacja")
        assert bot.has_reply(100, "Warszawa, Polska")
        assert not bot.has_reply(100, "/save_location")
        assert not bot.has_reply(100, "/oneoff")

    def test_now_and_future_city_generate_the_requested_oneoffs(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/now Hel"))
        bot.run(bot.msg(100, "/future Berlin"))
        assert [report[-1] for report in bot.oneoffs] == ["now", "future"]
        assert lb.PENDING_SAVE["100"]["source"] == "city"  # ostatni, jeszcze RAM-only
        assert bot.users.record(100)["profile_status"] == "none"

    def test_no_city_and_no_saved_profile_asks_for_location_not_access(self, bot):
        # PR2 UX cleanup: bez profilu nie ma suchego "profil nieaktywny" — bot pyta
        # o SAMĄ nazwę miejscowości i zapamiętuje kontekst jednorazowej karty.
        bot.run(bot.msg(100, "/start BETAX1"))
        for cmd, ctx in (("/day", lb.CTX_ONEOFF_DAY),
                         ("/now", lb.CTX_ONEOFF_NOW),
                         ("/future", lb.CTX_ONEOFF_FUTURE)):
            bot.run(bot.msg(100, cmd))
            assert bot.has_reply(100, "Wpisz poniżej samą nazwę miejscowości"), cmd
            assert not bot.has_reply(100, "Brak dostępu"), cmd
            assert not bot.has_reply(100, "Profil nie jest"), cmd
            assert lb.PENDING_CITY["100"]["ctx"] == ctx, cmd
        assert bot.oneoffs == []

    def test_save_location_persists_profile_and_consent(self, bot, monkeypatch):
        # PR2-b: ukryty alias techniczny /save_location nadal przenosi pending RAM
        # do Users (kompatybilność wsteczna), choć nie pokazujemy go już w UX.
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/day Warszawa"))
        bot.run(bot.msg(100, "/save_location"))

        rec = bot.users.record(100)
        assert rec["profile_status"] == "active"
        # współrzędne do 3 miejsc po przecinku
        assert rec["lat_round"] == "52.23" and rec["lon_round"] == "21.012"
        assert rec["location_label"] == "Warszawa"
        assert rec["location_source"] == "city"
        assert rec["lang"] == "pl"
        assert rec["location_consent_at"]
        assert rec["location_consent_version"] == lb.PRIVACY_VERSION
        assert rec["profile_updated_at"]
        assert "100" not in lb.PENDING_SAVE

    def test_oneoff_discards_pending_without_saving_profile(self, bot):
        # PR2-c: /oneoff jest ukryty (nie ma go w menu ani w komunikatach), ale
        # kliknięcie starej klawiatury nie może zostać bez odpowiedzi (D1).
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/now Hel"))
        bot.run(bot.msg(100, "/oneoff"))

        rec = bot.users.record(100)
        assert rec["profile_status"] == "none"
        assert rec["lat_round"] == "" and rec["location_consent_at"] == ""
        assert "100" not in lb.PENDING_SAVE
        assert bot.has_reply(100, "OK, nic nie zapisuję")

    def test_hidden_oneoff_and_save_location_are_not_advertised(self, bot, monkeypatch):
        # Po żadnej karcie jednorazowej nie pokazujemy /save_location ani /oneoff,
        # nie wysyłamy też klawiatury z tymi przyciskami.
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(100, "/start BETAX1"))
        for cmd in ("/day Warszawa", "/now Hel", "/future Berlin"):
            bot.run(bot.msg(100, cmd))
        bot.run(bot.pin(100, reply=True))

        assert len(bot.oneoffs) == 4
        for _cid, text in bot.sent:
            assert "/save_location" not in text
            assert "/oneoff" not in text
        assert bot.keyboards_for(100) == [], "klawiatura z /save_location i /oneoff wróciła do UX"

    def test_save_without_or_after_expired_pending_has_clear_message(self, bot):
        # PR2-d: zarówno brak wpisu, jak i TTL nie mogą nic zapisać.
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/save_location"))
        assert bot.has_reply(100, "Nie ma lokalizacji do zapisania")

        lb._put_pending_save(100, 52.23, 21.01, "Warszawa", "pl", "city", now_ts=0)
        bot.run(bot.msg(100, "/save_location"))
        assert bot.has_reply(100, "Nie ma lokalizacji do zapisania")
        assert "100" not in lb.PENDING_SAVE
        assert bot.users.record(100)["profile_status"] == "none"

    def test_gps_and_webapp_outside_city_flow_make_pending_not_profile(self, bot):
        # PR2-e (po UX cleanup): pinezka i WebApp POZA flow /miasto nadal dają
        # tylko raport jednorazowy + efemeryczny PENDING_SAVE — bez zapisu do
        # Formularz i bez zapisu do Users.
        for chat_id, source, update in (
            (101, "gps", lambda: bot.pin(101, reply=True)),
            (102, "webapp", lambda: bot.webapp_location(102)),
        ):
            bot.run(bot.msg(chat_id, "/start BETAX1"))
            bot.run(update())
            rec = bot.users.record(chat_id)
            assert rec["profile_status"] == "none", source
            assert rec["lat_round"] == "" and rec["location_consent_at"] == "", source
            assert lb.PENDING_SAVE[str(chat_id)]["source"] == source
            assert str(chat_id) not in bot.gc.formularz.chat_ids(), source
            assert bot.has_reply(chat_id, "Użyta lokalizacja"), source

    def test_city_command_saves_profile_and_generates_no_card(self, bot):
        # PR2 UX cleanup (pkt 7 + D): /miasto jest jedynym widocznym flow zapisu.
        bot.run(bot.msg(103, "/start BETAX1"))
        bot.run(bot.msg(103, "/miasto"))
        assert bot.has_reply(103, "ZMIANA LOKALIZACJI")
        assert bot.has_reply(103, "Obecna lokalizacja:\nbrak")
        assert lb.PENDING_CITY["103"]["ctx"] == lb.CTX_SAVE_PROFILE

        bot.run(bot.msg(103, "Warszawa"))

        rec = bot.users.record(103)
        assert rec["profile_status"] == "active"
        assert rec["lat_round"] == "52.23" and rec["lon_round"] == "21.012"
        assert rec["location_label"] == "Warszawa"
        assert rec["location_source"] == "city"
        assert rec["location_consent_at"] and rec["profile_updated_at"]
        assert bot.oneoffs == [], "flow /miasto nie może generować karty"
        assert bot.cards == []
        assert "103" not in bot.gc.formularz.chat_ids(), "zapis Users nie dotyka Formularz"
        assert "103" not in lb.PENDING_CITY, "stan oczekiwania musi być skonsumowany"
        assert bot.has_reply(103, "Lokalizacja zapisana")
        assert bot.has_reply(103, "/dzien")

    def test_city_command_with_argument_saves_profile_immediately(self, bot):
        bot.run(bot.msg(104, "/start BETAX1"))
        bot.run(bot.msg(104, "/miasto Warszawa"))
        assert bot.users.record(104)["profile_status"] == "active"
        assert bot.oneoffs == []

    def test_gps_in_city_flow_saves_profile_without_card(self, bot):
        # D4: GPS wysłany w trakcie flow /miasto zapisuje profil (bez karty),
        # także pinezka bez "Odpowiedz" w czacie prywatnym.
        bot.run(bot.msg(105, "/start BETAX1"))
        bot.run(bot.msg(105, "/miasto"))
        bot.run(bot.pin(105, reply=False))

        rec = bot.users.record(105)
        assert rec["profile_status"] == "active"
        assert rec["location_source"] == "gps"
        assert rec["lat_round"] == "54.5" and rec["lon_round"] == "18.5"
        assert bot.oneoffs == [], "GPS w flow /miasto nie generuje karty"
        assert "105" not in lb.PENDING_CITY

    def test_webapp_gps_in_city_flow_saves_profile_without_card(self, bot):
        bot.run(bot.msg(106, "/start BETAX1"))
        bot.run(bot.msg(106, "/miasto"))
        bot.run(bot.webapp_location(106, lat=50.0614, lon=19.9366))

        rec = bot.users.record(106)
        assert rec["profile_status"] == "active"
        assert rec["location_source"] == "webapp"
        assert rec["lat_round"] == "50.061" and rec["lon_round"] == "19.937"
        assert bot.oneoffs == []

    def test_guest_shortcut_works_with_access(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, ".n Hel"))
        # handle_guest_now jest wołane dla każdej wiadomości (dla komend zwraca False),
        # więc na liście jest też "/start BETAX1" — ale ".n Hel" MUSI do niego trafić
        assert bot.guest_calls[-1] == ".n Hel", "skrót .n nie doszedł do trybu gościa"
        assert bot.has_reply(100, "Dostęp aktywowan")  # a wcześniej rejestracja się udała


class TestLegacyUserUnchanged:
    def test_legacy_day_generates_card(self, bot, monkeypatch):
        # Zamrożenie godziny na 10:00 (wewnątrz okna 05:00–15:59) — inaczej
        # test zależałby od pory odpalenia suite'u (zachowanie pre-existing).
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(700, "/day"))
        assert not bot.has_reply(700, "Brak dostępu")
        assert bot.cards == ["700"]

    def test_legacy_day_time_limit_is_preexisting(self, bot, monkeypatch):
        # Dokumentuje ISTNIEJĄCE (pre-PR1, obecne też na staging) okno /day:
        # poza 05:00–15:59 legacy user z dostępem dostaje time_limit zamiast
        # karty — to nie jest regresja PR1, ścieżka legacy jest nietknięta.
        freeze_local_hour(monkeypatch, 22, 0)
        bot.run(bot.msg(700, "/day"))
        assert not bot.has_reply(700, "Brak dostępu")  # dostęp nadal jest
        assert bot.cards == []                          # ale karta wstrzymana
        assert bot.has_reply(700, "05:00")              # komunikat okna czasowego

    def test_legacy_city_keeps_existing_formularz_flow(self, bot):
        # PR2-f: brak Users oznacza pełną kompatybilność legacy, bez PENDING_SAVE.
        bot.run(bot.msg(700, "/miasto Warszawa"))
        row = next(r for r in bot.gc.formularz.rows if str(r[1]).strip() == "700")
        assert row[FORM_HEADERS.index("Lat")] == 52.22972
        assert row[FORM_HEADERS.index("Lon")] == 21.01223
        assert row[FORM_HEADERS.index("Miasto")] == "Warszawa"
        assert "700" not in lb.PENDING_SAVE
        assert bot.oneoffs == []

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
        assert bot.has_reply(700, "Usunąłem zapisaną lokalizację")
        assert bot.has_reply(700, "dostęp pozostaje aktywny")

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
        # D2: /delete_me (tak jak /usunDane) najpierw pyta o potwierdzenie,
        # a hard delete wykonuje się dopiero po "Tak, chcę".
        bot.run(bot.msg(100, "/start BETAX1"))
        lb._set_pending_city("100", lb.CTX_ONEOFF_DAY)
        lb._put_pending_save(100, 52.23, 21.01, "Warszawa", "pl", "city")

        bot.run(bot.msg(100, "/delete_me"))
        assert bot.users.record(100) is not None, "kasacja bez potwierdzenia!"
        assert "100" in lb.PENDING_DELETE

        bot.run(bot.msg(100, "Tak, chcę"))
        assert bot.users.record(100) is None, "wiersz Users nieusunięty"
        assert "100" not in lb.PENDING_CITY, "PENDING_CITY nie wyczyszczone"
        assert "100" not in lb.PENDING_SAVE, "PENDING_SAVE nie wyczyszczone"
        assert "100" not in lb.PENDING_DELETE, "PENDING_DELETE nie wyczyszczone"
        assert bot.has_reply(100, "Usunąłem Twoje dane")
        assert not bot.has_reply(100, "https://"), "po kasacji nie podajemy linku zaproszenia"

        # dostęp znika w kolejnej paczce
        bot.run(bot.msg(100, "/day"))
        assert bot.has_reply(100, "Brak dostępu")

    def test_delete_me_legacy_rows_removed_including_blocked(self, bot):
        bot.gc.formularz.rows.append(["2026-01-04 08:00:00", "701", "BlockedLegacy", "Hel", "54.6", "18.8", "", "", "pl"])
        bot.run(bot.msg(701, "/delete_me"))
        bot.run(bot.msg(701, "Tak, chcę"))
        assert "701" not in bot.gc.formularz.chat_ids()
        assert "BLOCKED_701" not in bot.gc.formularz.chat_ids()
        assert "700" in bot.gc.formularz.chat_ids(), "usunięto wiersze obcego użytkownika!"

    def test_delete_me_stranger_gets_no_data(self, bot):
        bot.run(bot.msg(555, "/delete_me"))
        bot.run(bot.msg(555, "Tak, chcę"))
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


# ============================================================================
# PR2 UX CLEANUP — punkt 8 zakresu (testy a-h) + aliasy PL
# ============================================================================
import re

BACKTICKED_COMMAND = re.compile(r"`/")


class TestPr2UxCleanup:
    """Jednorazowy raport o nic nie pyta; zapis lokalizacji jest tylko przez /miasto."""

    # --- a) /dzien bez profilu pyta o miasto -------------------------------
    def test_a_dzien_without_profile_asks_for_city_only(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/dzien"))

        assert bot.has_reply(100, "Wpisz poniżej samą nazwę miejscowości dla prognozy dziennej")
        assert bot.has_reply(100, "`Warszawa`")            # backticki TYLKO dla przykładów
        assert not bot.has_reply(100, "profil nieaktywny")
        assert not bot.has_reply(100, "Profil nie jest")
        assert not bot.has_reply(100, "Brak dostępu")
        assert lb.PENDING_CITY["100"]["ctx"] == lb.CTX_ONEOFF_DAY
        assert bot.oneoffs == []

    def test_a_teraz_and_trend_ask_with_their_own_context(self, bot):
        for cmd, ctx, needle in (
            ("/teraz", lb.CTX_ONEOFF_NOW, "najbliższe godziny"),
            ("/trend", lb.CTX_ONEOFF_FUTURE, "trendu 14 dni"),
        ):
            bot.run(bot.msg(100, "/start BETAX1"))
            bot.run(bot.msg(100, cmd))
            assert bot.has_reply(100, needle), cmd
            assert lb.PENDING_CITY["100"]["ctx"] == ctx, cmd

    # --- b) miasto po /dzien -> karta jednorazowa, bez zapisu profilu -------
    def test_b_city_after_dzien_generates_oneoff_and_saves_nothing(self, bot, monkeypatch):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/dzien"))
        bot.run(bot.msg(100, "Warszawa"))

        assert bot.oneoffs == [(100, 52.22972, 21.01223, "Warszawa", "pl", "day")]
        rec = bot.users.record(100)
        assert rec["profile_status"] == "none", "jednorazowa karta nie może zapisywać profilu"
        assert rec["lat_round"] == "" and rec["lon_round"] == ""
        assert rec["location_consent_at"] == ""
        assert "100" not in bot.gc.formularz.chat_ids()
        assert "100" not in lb.PENDING_CITY, "kontekst musi być skonsumowany"

    def test_b_context_routes_to_the_right_card_type(self, bot, monkeypatch):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(100, "/start BETAX1"))
        for cmd, expected in (("/dzien", "day"), ("/teraz", "now"), ("/trend", "future")):
            bot.run(bot.msg(100, cmd))
            bot.run(bot.msg(100, "Warszawa"))
        assert [report[-1] for report in bot.oneoffs] == ["day", "now", "future"]
        assert bot.users.record(100)["profile_status"] == "none"

    # --- c) po karcie nie ma /save_location ani /oneoff ---------------------
    def test_c_no_save_location_nor_oneoff_after_card(self, bot, monkeypatch):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/dzien"))
        bot.run(bot.msg(100, "Warszawa"))
        bot.run(bot.msg(100, "/teraz Hel"))

        for _cid, text in bot.sent:
            assert "/save_location" not in text
            assert "/oneoff" not in text
        assert bot.keyboards_for(100) == []
        # zamiast tego pokazujemy tylko użytą lokalizację z geokodera
        assert bot.has_reply(100, "📍 Użyta lokalizacja:\nWarszawa")
        assert bot.has_reply(100, "🌍 Warszawa, Polska")
        assert bot.has_reply(100, "wpisz nazwę dokładniej")

    # --- d) /miasto + miasto zapisuje profil i nie generuje karty -----------
    def test_d_city_flow_saves_profile_without_card(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/miasto"))
        bot.run(bot.msg(100, "Warszawa"))

        rec = bot.users.record(100)
        assert rec["profile_status"] == "active"
        assert rec["lat_round"] == "52.23" and rec["lon_round"] == "21.012"  # 3 miejsca
        assert rec["location_label"] == "Warszawa"
        assert rec["location_consent_version"] == lb.PRIVACY_VERSION
        assert bot.oneoffs == [] and bot.cards == []
        assert bot.has_reply(100, "Lokalizacja zapisana")
        for cmd in ("/dzien", "/teraz", "/trend"):
            assert bot.has_reply(100, cmd)
        assert not bot.has_reply(100, "/save_location")

    def test_d_city_prompt_shows_current_location_when_profile_active(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/miasto"))
        bot.run(bot.msg(100, "Warszawa"))
        bot.run(bot.msg(100, "/miasto"))

        assert bot.has_reply(100, "Obecna lokalizacja:\nWarszawa")
        assert bot.has_reply(100, "Wpisz nową miejscowość")
        assert bot.has_reply(100, "współrzędne są zaokrąglane")

        # POPRAWKA #3: pinezka z mapy + "Szczegóły:" z klikalnym /porady
        # w jednej linii, bez bloku "Przykłady:" z renderowanymi miastami.
        assert bot.has_reply(100, "wyślij pinezkę z mapy lub lokalizację GPS. Szczegóły:")
        assert bot.has_reply(100, "\n/porady\n")
        assert bot.has_reply(100, "poranne lub popołudniowe raporty pogodowe")
        assert not bot.has_reply(100, "Przykłady:")
        assert not bot.has_reply(100, "`Warszawa`")

    def test_d_saved_profile_makes_dzien_work_without_city(self, bot, monkeypatch):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/miasto"))
        bot.run(bot.msg(100, "Warszawa"))
        bot.run(bot.msg(100, "/dzien"))

        assert bot.oneoffs[-1][:5] == (100, 52.23, 21.012, "Warszawa", "pl")
        assert "100" not in lb.PENDING_CITY, "z profilem nie pytamy ponownie o miasto"

    # --- e) /bezGPS bez lokalizacji -----------------------------------------
    def test_e_bez_gps_without_location_reports_missing_location(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/bezGPS"))

        assert bot.has_reply(100, "Nie masz zapisanej lokalizacji")
        assert bot.has_reply(100, "dostęp pozostaje aktywny")
        assert not bot.has_reply(100, "Usunąłem zapisaną lokalizację")
        assert bot.users.record(100)["access_status"] == "granted"

    # --- f) /bezGPS z lokalizacją czyści profil ------------------------------
    def test_f_bez_gps_clears_profile_and_keeps_access(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/miasto"))
        bot.run(bot.msg(100, "Warszawa"))
        bot.run(bot.msg(100, "/bezGPS"))

        rec = bot.users.record(100)
        assert rec["profile_status"] == "none"
        assert rec["lat_round"] == "" and rec["lon_round"] == ""
        assert rec["location_label"] == "" and rec["location_consent_at"] == ""
        assert rec["access_status"] == "granted", "/bezGPS nie może odbierać dostępu"
        assert bot.has_reply(100, "Usunąłem zapisaną lokalizację")
        assert bot.has_reply(100, "/dzien Warszawa")

    # --- g) /usunDane wymaga potwierdzenia ----------------------------------
    def test_g_usun_dane_requires_confirmation(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/usunDane"))

        # krok 1: pytanie + przyciski, dane NIENARUSZONE
        assert bot.users.record(100) is not None
        assert bot.has_reply(100, "Czy na pewno chcesz usunąć wszystkie swoje dane")
        assert "100" in lb.PENDING_DELETE
        keyboards = bot.keyboards_for(100)
        assert keyboards, "brak klawiatury potwierdzenia"
        labels = [btn["text"] for row in keyboards[-1]["keyboard"] for btn in row]
        assert labels == ["Tak, chcę", "Nie, nie chcę"]
        assert "callback" not in str(keyboards[-1]).lower()

        # krok 2A: potwierdzenie wykonuje hard delete
        bot.run(bot.msg(100, "Tak, chcę"))
        assert bot.users.record(100) is None
        assert bot.has_reply(100, "Usunąłem Twoje dane")
        assert not bot.has_reply(100, "https://"), "po kasacji nie podajemy linku zaproszenia"

    def test_g_usun_dane_cancellation_keeps_data(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/miasto"))
        bot.run(bot.msg(100, "Warszawa"))
        bot.run(bot.msg(100, "/usunDane"))
        bot.run(bot.msg(100, "Nie, nie chcę"))

        assert bot.has_reply(100, "OK, nic nie usuwam")
        rec = bot.users.record(100)
        assert rec["access_status"] == "granted"
        assert rec["profile_status"] == "active"
        assert "100" not in lb.PENDING_DELETE
        # dostęp dalej działa
        bot.run(bot.msg(100, "/teraz"))
        assert not bot.has_reply(100, "Brak dostępu")

    def test_g_other_text_does_not_consume_delete_pending(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/usunDane"))
        bot.run(bot.msg(100, "chwila, zastanawiam się"))
        assert "100" in lb.PENDING_DELETE, "inny tekst nie może konsumować potwierdzenia"
        assert bot.users.record(100) is not None
        bot.run(bot.msg(100, "Tak, chcę"))
        assert bot.users.record(100) is None

    # --- h) komendy w tekstach nie są w backtickach -------------------------
    def test_h_no_backticked_commands_in_ui_texts(self):
        import i18n
        offenders = []
        for lang, bundle in i18n.UI_TEXTS.items():
            for key, text in bundle.items():
                if BACKTICKED_COMMAND.search(text or ""):
                    offenders.append(f"{lang}:{key}")
        assert offenders == [], f"komendy w backtickach (nieklikalne): {offenders}"

    def test_h_no_backticked_commands_in_sent_messages(self, bot, monkeypatch):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(100, "/start BETAX1"))
        for cmd in ("/info", "/porady", "/priv", "/dane", "/raport", "/dzien", "/miasto"):
            bot.run(bot.msg(100, cmd))
        bot.run(bot.msg(100, "Warszawa"))
        bot.run(bot.msg(100, "/dzien"))
        bot.run(bot.msg(100, "/bezGPS"))
        bot.run(bot.msg(100, "/usunDane"))
        bot.run(bot.msg(100, "Nie, nie chcę"))

        offenders = [t[:60] for t in bot.replies(100) if BACKTICKED_COMMAND.search(t)]
        assert offenders == [], f"komendy w backtickach: {offenders}"

    # --- aliasy PL (case-insensitive) ---------------------------------------
    def test_aliases_map_to_canonical_handlers(self, bot, monkeypatch):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(100, "/start BETAX1"))

        bot.run(bot.msg(100, "/priv"))
        assert bot.has_reply(100, "PRYWATNOŚĆ")

        bot.run(bot.msg(100, "/dane"))
        assert bot.has_reply(100, "TWOJE DANE")

        bot.run(bot.msg(100, "/raport"))
        assert bot.has_reply(100, "GODZINY RAPORTÓW")
        assert not bot.has_reply(100, "Brak dostępu")

        bot.run(bot.msg(100, "/zapros"))
        assert bot.has_reply(100, "zaproszenie jest gotowe")

        bot.run(bot.msg(100, "/teraz"))
        assert lb.PENDING_CITY["100"]["ctx"] == lb.CTX_ONEOFF_NOW

        bot.run(bot.msg(100, "/trend"))
        assert lb.PENDING_CITY["100"]["ctx"] == lb.CTX_ONEOFF_FUTURE

    def test_bez_gps_and_usun_dane_are_case_insensitive(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        for cmd in ("/bezGPS", "/bezgps", "/BEZGPS", "/BezGps"):
            bot.run(bot.msg(100, cmd))
        hits = sum(1 for t in bot.replies(100) if "Nie masz zapisanej lokalizacji" in t)
        assert hits == 4, f"/bezGPS musi działać case-insensitive (trafienia: {hits})"
        assert not bot.has_reply(100, "Brak dostępu")

        for cmd in ("/usunDane", "/usundane", "/USUNDANE"):
            bot.run(bot.msg(101, "/start BETAX1"))
            bot.run(bot.msg(101, cmd))
            assert bot.has_reply(101, "Czy na pewno chcesz usunąć"), cmd
            assert "101" in lb.PENDING_DELETE

    def test_hidden_delete_commands_work_as_buttons(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/usunDane"))
        bot.run(bot.msg(100, "/potwierdzusun"))
        assert bot.users.record(100) is None

    def test_hidden_confirm_without_pending_ask_does_not_delete(self, bot):
        # /potwierdzusun wpisane "z powietrza" nie może kasować danych bez pytania
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/potwierdzusun"))
        assert bot.users.record(100) is not None
        assert "100" in lb.PENDING_DELETE
        assert bot.has_reply(100, "Czy na pewno chcesz usunąć")
        bot.run(bot.msg(100, "Nie, nie chcę"))
        assert bot.users.record(100) is not None

    # --- limit czasowy karty dziennej (pkt 5) --------------------------------
    def test_day_time_limit_applies_to_profile_and_oneoff(self, bot, monkeypatch):
        freeze_local_hour(monkeypatch, 22, 0)  # poza oknem 05:00-15:59
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/miasto"))
        bot.run(bot.msg(100, "Warszawa"))

        bot.run(bot.msg(100, "/dzien"))
        assert bot.has_reply(100, "05:00")
        assert bot.oneoffs == [], "poza oknem karta dzienna nie może powstać"

        bot.run(bot.msg(100, "/dzien Warszawa"))
        assert bot.oneoffs == []
        assert bot.users.record(100)["profile_status"] == "active"

    def test_day_inside_window_generates_card(self, bot, monkeypatch):
        freeze_local_hour(monkeypatch, 9, 30)
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/dzien Warszawa"))
        assert len(bot.oneoffs) == 1
        assert not bot.has_reply(100, "05:00")

    def test_now_and_trend_have_no_time_limit(self, bot, monkeypatch):
        freeze_local_hour(monkeypatch, 23, 0)
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/teraz Warszawa"))
        bot.run(bot.msg(100, "/trend Warszawa"))
        assert [r[-1] for r in bot.oneoffs] == ["now", "future"]

    # --- /dane (pkt 9) --------------------------------------------------------
    def test_my_data_without_location_shows_date_only(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/dane"))

        panel = bot.replies(100)[-1]
        assert "TWOJE DANE" in panel
        assert "ID Telegrama: 100" in panel
        assert "Dostęp: ✅ aktywny" in panel
        assert "Zapisana lokalizacja: brak" in panel
        assert "Współrzędne: brak" in panel
        assert "/priv" in panel and "/bezGPS" in panel and "/usunDane" in panel
        assert "Ala" not in panel, "bez nicka w panelu danych"
        stan_na = [l for l in panel.split("\n") if l.startswith("Stan na:")][0]
        assert re.fullmatch(r"Stan na: \d{4}-\d{2}-\d{2}", stan_na), stan_na

    def test_my_data_with_location_shows_local_time(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/miasto"))
        bot.run(bot.msg(100, "Warszawa"))
        bot.run(bot.msg(100, "/dane"))

        panel = bot.replies(100)[-1]
        assert "Zapisana lokalizacja: Warszawa" in panel
        assert "Współrzędne: 52.23, 21.012" in panel
        stan_na = [l for l in panel.split("\n") if l.startswith("Stan na:")][0]
        assert re.fullmatch(r"Stan na: \d{4}-\d{2}-\d{2} \d{2}:\d{2}", stan_na), stan_na

    # --- /raport (pkt 13) -----------------------------------------------------
    def test_report_for_users_only_account_does_not_promise_reports(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/raport"))

        assert bot.has_reply(100, "GODZINY RAPORTÓW")
        assert bot.has_reply(100, "w trakcie przenoszenia")
        assert bot.has_reply(100, "/miasto")
        # /menu bez sekcji lokalizacji i bez obietnicy automatycznych raportów
        assert not bot.has_reply(100, "Obecna lokalizacja")
        assert not bot.has_reply(100, "będą wysyłane")
        assert bot.keyboards_for(100) == [], "konto bez Formularz nie dostaje panelu godzin"

    def test_report_for_legacy_user_shows_hours_without_location(self, bot):
        bot.run(bot.msg(700, "/raport"))
        panel = bot.replies(700)[-1]
        assert "GODZINY RAPORTÓW" in panel
        assert "Rano: 08:00" in panel
        assert "Obecna lokalizacja" not in panel
        assert "/miasto" in panel

    # --- /info (pkt 16) --------------------------------------------------------
    def test_info_contains_tips_and_hidden_commands(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/info"))
        info = bot.replies(100)[-1]
        for needle in ("/dzien", "/teraz", "/trend", "/miasto", "/raport",
                       "/zapros", "/dane", "/porady", "/priv",
                       "/bezGPS", "/usunDane", "/save\\_location"):
            assert needle in info, needle
        assert not BACKTICKED_COMMAND.search(info)

    def test_porady_command_still_works_but_is_not_in_menu(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/porady"))
        assert bot.has_reply(100, "PORADY I TRIKI")
        assert not bot.has_reply(100, "/miasto Rzym`")
        # treść porad nie obiecuje już karty z /miasto ani "promienia 3 km"
        assert not bot.has_reply(100, "3 km")
        assert not bot.has_reply(100, "wygeneruje świeży raport")

    # --- /oneoff jako ukryty no-op z odpowiedzią (D1) --------------------------
    def test_oneoff_answers_neutrally_and_shows_no_commands(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/now Hel"))
        bot.run(bot.msg(100, "/oneoff"))
        reply = bot.replies(100)[-1]
        assert reply == "OK, nic nie zapisuję."
        assert "/save_location" not in reply and "/oneoff" not in reply
        assert bot.users.record(100)["profile_status"] == "none"

    # --- onboarding (pkt 1) ----------------------------------------------------
    def test_welcome_access_lists_commands_on_separate_lines(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        welcome = bot.replies(100)[-1]
        assert welcome.startswith("✅ Dostęp aktywowany!")
        for line in ("/dzien — prognoza dzienna", "/teraz — prognoza na 12 godzin",
                     "/trend — prognoza na 14 dni", "/miasto", "/priv", "/dane"):
            assert any(l.strip().startswith(line) for l in welcome.split("\n")), line

        # POPRAWKA #2: akapit "Najpierw Twoja prywatność..." po powitaniu,
        # a na dole sama "Administracja Twoimi danymi".
        lines = [l.strip() for l in welcome.split("\n")]
        assert lines[lines.index("Witaj w Pogoda World 🌍") + 2].startswith(
            "Najpierw Twoja prywatność, przeczytaj:")
        assert lines[lines.index("Najpierw Twoja prywatność, przeczytaj:") + 1] == "/priv"
        assert lines[-2] == "Administracja Twoimi danymi:"
        assert lines[-1] == "/dane"
        assert "Prywatność i administracja" not in welcome
