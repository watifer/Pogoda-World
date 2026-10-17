"""
test_access_gate.py — Testy end-to-end bramki dostępu PR1 (RODO/access migration).

Uruchomienie: pytest test_access_gate.py -v

Testy symulują PEŁNĄ pętlę location_bot.main_bot() z atrapami Telegram API
oraz Google Sheets (Formularz + Users + Bot_State). Zero sieci, zero kluczy,
zero produkcyjnych danych.

Pokrywane scenariusze:
- obcy użytkownik (POPRAWKA #7): odpowiada WYŁĄCZNIE /start — bez kodu dostaje
  pełny no_access, z poprawnym kodem rejestrację, a komendy/pinezki/skróty
  .d/.n/.f/?d/?12 są ignorowane po cichu (zero wiadomości),
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
- kasacja danych (POPRAWKA #7): hard delete Users + delete_rows Formularz +
  czyszczenie RAM, a po skutecznej kasacji DOKŁADNIE dwa komunikaty —
  delete_me_done, a po ok. 1 s osobny, pełny no_access,
- po hard delete czat nie ma danych ani dostępu: wszystkie komendy (w tym
  /dane, /priv, /bezGPS, /usunDane, /delete_me, /privacy, /my_data), skróty,
  pinezki i zwykły tekst są ciszą — działa wyłącznie /start,
- /forget_location (/bezGPS): czyści profil, dostęp zostaje, rozróżnia brak
  zapisanej lokalizacji,
- komendy RODO (/privacy, /my_data, /delete_me) działają dla czatów z dostępem,
  a dla czatów bez dostępu są ciszą (POPRAWKA #7),
- komendy w tekstach UI nie są w backtickach (klikalność na telefonie).
"""

import os
import json

import pytest
import requests

# UWAGA: celowo NIE ustawiamy tu PUBLIC_BETA_CODES. Kody beta ustawia BotHarness
# przez monkeypatch.setenv (odporne na kolejność importów modułów testowych
# oraz na .env użytkownika, który load_dotenv() mógł już wczytać).

import i18n
import location_bot as lb
import users_store

FORM_HEADERS = ["Sygnatura czasowa", "Chat ID", "Imię", "Miasto", "Lat", "Lon",
                "Raport poranny", "Aktualizacja", "Lang"]
USERS_HEADERS = users_store.CANONICAL_HEADERS

# ETAP 1.1: referencja do PRAWDZIWEGO walidatora zapamiętana PRZED patchem
# harnessu. BotHarness podmienia lb.geocode_city_accepted na atrapę, więc testy
# pełnego flow muszą mieć oryginał, żeby naprawdę przejść walidacją kandydatów
# (na atrapie Nominatima, bez ani jednego żądania sieciowego).
REAL_GEOCODE_CITY_ACCEPTED = lb.geocode_city_accepted
REAL_GET_COORDS_FROM_CITY = lb.get_coords_from_city
REAL_GET_LOCATION_DETAILS_FROM_COORDS = lb.get_location_details_from_coords


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

    def get_all_values(self, value_render_option=None):
        return [FORM_HEADERS[:]] + [row[:] for row in self.rows]

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

    def __init__(self, monkeypatch, formularz_rows=None, public_beta_codes=None):
        lb._reset_polling_state()
        self.gc = FakeGC(formularz_rows)
        self.sent = []        # (chat_id, text)
        self.markups = []     # reply_markup każdej wysłanej wiadomości (index = sent)
        self.guest_calls = []  # teksty przekazane do handle_guest_now
        self.guest_kwargs = []  # pełne kwargs handle_guest_now (wiring ETAP 1/1.1)
        self.cards = []       # chat_id z _send_card_to_user (legacy)
        self.oneoffs = []     # (chat_id, lat, lon, city, lang, card_type)

        # Kody beta: jeśli test podał jawnie lub ustawił wcześniej w monkeypatch, zachowaj.
        # Domyślnie ustaw BETAX1 dla powtarzalności środowiska testowego.
        if public_beta_codes is not None:
            monkeypatch.setenv("PUBLIC_BETA_CODES", public_beta_codes)
            monkeypatch.delenv("PUBLIC_BETA_CODE", raising=False)
        elif "PUBLIC_BETA_CODES" not in os.environ and "PUBLIC_BETA_CODE" not in os.environ:
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
            self.guest_kwargs.append(kw)
            if not text or text.startswith("/"):
                return False
            low = text.lower()
            prefix, _card, _query = lb.resolve_shortcut(text)
            return f"@{lb.BOT_USERNAME.lower()}" in low or prefix is not None

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
        # ETAP 1: testy podszywają się pod JEDYNE źródło geokodowania forward,
        # czyli jądrową funkcję statusową. Dzięki temu ścieżki komend i tryb
        # gościa (przez geocode_city_details_status) widzą ten sam wynik.
        monkeypatch.setattr(
            lb, "geocode_city_accepted",
            lambda city, lang="pl": (
                lb.GEOCODE_OK, 52.22972, 21.01223, city, f"{city}, Polska",
            ),
        )
        monkeypatch.setattr(
            lb, "get_city_from_coords",
            lambda lat, lon, lang="pl": "Hel" if float(lat) >= 54 else "Warszawa",
        )
        # POPRAWKA #4: pełny adres z reverse-geocodera (domyślnie sukces).
        monkeypatch.setattr(
            lb, "get_location_details_from_coords",
            lambda lat, lon, lang="pl", query=None, mode=None: (
                ("Hel" if float(lat) >= 54 else "Warszawa"),
                f"{'Hel' if float(lat) >= 54 else 'Warszawa'}, Polska",
                lb.GEO_OK,
            ),
        )
        monkeypatch.setattr(
            lb, "_load_future_users_from_sheet", lambda _gc: self.gc.formularz.get_all_records()
        )
        lb.PENDING_CITY.clear()
        lb.PENDING_SAVE.clear()
        self._updates = updates

    def msg(self, chat_id, text, reply=False, lang="pl", user_id=None, chat_type=None):
        """Wiadomość tekstowa.

        ``user_id`` pozwala oddzielić autora wiadomości od czatu (testy grup:
        chat_id ujemne, ``from.id`` = członek grupy). Domyślnie ``from.id``
        jest równe chat_id — dokładnie tak, jak w czacie prywatnym.
        """
        m = {"update_id": 100000 + len(self.sent) * 7 + len(self._updates),
             "message": {"chat": {"id": chat_id,
                                  "type": chat_type or ("private" if int(chat_id) > 0 else "group")},
                         "from": {"language_code": lang, "first_name": "Ala",
                                  "id": chat_id if user_id is None else user_id},
                         "text": text}}
        if reply:
            m["message"]["reply_to_message"] = {"message_id": 1}
        return m

    def pin(self, chat_id, reply=False, lat=54.5, lon=18.5, lang="pl",
            user_id=None, chat_type=None, reply_to_message=None,
            reply_to_bot=False):
        m = self.msg(chat_id, None, lang=lang, user_id=user_id, chat_type=chat_type)
        m["message"].pop("text")
        m["message"]["location"] = {"latitude": lat, "longitude": lon}
        if reply_to_message is not None:
            m["message"]["reply_to_message"] = reply_to_message
        elif reply_to_bot:
            m["message"]["reply_to_message"] = {
                "message_id": 1,
                "from": {
                    "id": lb._bot_user_id() or 999000,
                    "is_bot": True,
                    "first_name": "Pogoda World",
                    "username": lb.BOT_USERNAME,
                },
                "chat": m["message"]["chat"],
                "text": "Komunikat bota",
            }
        elif reply:
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

    def callback_query(self, chat_id, data="test_data", user_id=None):
        return {
            "update_id": 400000 + len(self.sent) * 7 + len(self._updates),
            "callback_query": {
                "id": "cb123",
                "from": {"id": chat_id if user_id is None else user_id, "first_name": "Ala"},
                "message": {"chat": {"id": chat_id}},
                "data": data,
            },
        }

    def webapp_settings(self, chat_id, morning="", afternoon="", lang="pl"):
        return {
            "update_id": 300000 + len(self.sent) * 7 + len(self._updates),
            "message": {
                "chat": {"id": chat_id, "type": "private"},
                "from": {"language_code": lang, "first_name": "Ala"},
                "web_app_data": {
                    "data": json.dumps({
                        "type": "set_settings",
                        "rano": morning,
                        "wieczor": afternoon,
                    })
                },
            },
        }

    def run(self, update):
        """Jedna paczka update'ów przez main_bot()."""
        # Legacy integration tests mutate fake Sheets directly between polls;
        # opt out of TTL staleness here. Dedicated resilience tests cover TTL.
        lb._invalidate_form_snapshot()
        lb._invalidate_users_snapshot()
        self._updates.append(update)
        lb.main_bot()

    def replies(self, cid):
        return [t for (c, t) in self.sent if c == cid]

    def replies_since(self, cid, idx):
        """Odpowiedzi do chatu cid wysłane PO indeksie idx w self.sent.

        Potrzebne tam, gdzie asercja 'brak komunikatu X' musi dotyczyć
        wyłącznie odpowiedzi po danej akcji — bez wchodzenia w treść
        onboardingowego access_granted (który zawiera okno 05:00–15:59).
        """
        return [t for i, (c, t) in enumerate(self.sent) if i >= idx and c == cid]

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


@pytest.fixture
def fake_sleep(monkeypatch):
    """POPRAWKA #7: przechwytuje pauzę między komunikatami po kasacji.

    Zwraca listę wartości przekazanych do ``time.sleep`` — testy nie czekają
    realnie sekundy, a mimo to mogą sprawdzić, że pauza nastąpiła.
    """
    calls = []
    monkeypatch.setattr(lb.time, "sleep", lambda s: calls.append(s))
    return calls


# ============================================================================
# SCENARIUSZE
# ============================================================================
class TestStrangerIsSilent:
    """POPRAWKA #7: bez dostępu odpowiada WYŁĄCZNIE /start — reszta to cisza."""

    def test_day_now_future_silent(self, bot):
        for cmd in ("/day", "/now", "/future", "/day Warszawa"):
            bot.run(bot.msg(100, cmd))
            assert bot.replies(100) == [], f"obcy dostał odpowiedź na {cmd}"
        assert bot.cards == []
        assert bot.oneoffs == []  # PR2-g: argument miasta nie omija gate accessu

    def test_other_commands_silent(self, bot):
        for cmd in ("/menu", "/miasto", "/zapros", "/info", "/dzien", "/teraz",
                    "/trend", "/raport", "/dane", "/priv", "/bezGPS", "/usunDane"):
            bot.run(bot.msg(100, cmd))
            assert bot.replies(100) == [], f"obcy dostał odpowiedź na {cmd}"

    def test_guest_shortcuts_silent_and_guest_handler_not_called(self, bot):
        for cmd in (".n Hel", "?d Warszawa", "?12 Warszawa", "?14 Warszawa"):
            bot.run(bot.msg(100, cmd))
            assert bot.replies(100) == [], f"obcy dostał odpowiedź na {cmd}"
        assert bot.guest_calls == [], "gate powinien wyciszyć PRZED handle_guest_now"

    def test_mention_silent(self, bot):
        bot.run(bot.msg(100, f"@{lb.BOT_USERNAME} jaka pogoda w Rzymie?"))
        assert bot.replies(100) == []
        assert bot.guest_calls == []

    def test_pins_silent(self, bot):
        bot.run(bot.pin(100, reply=True))
        bot.run(bot.pin(100, reply=False))
        assert bot.replies(100) == [], "pinezka bez dostępu nie generuje wiadomości"

    def test_plain_text_stays_silent(self, bot):
        bot.run(bot.msg(100, "cześć, co potrafisz?"))
        assert bot.replies(100) == []

    def test_start_without_code_gets_no_access(self, bot):
        bot.run(bot.msg(100, "/start"))
        assert bot.has_reply(100, "bot prywatny")
        assert bot.has_reply(100, "/start BETAX1")

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
        assert rec["report_morning_time"] == "" and rec["report_afternoon_time"] == ""
        # RODO: brak wiersza w legacy Formularz
        assert "100" not in bot.gc.formularz.chat_ids()

    def test_report_settings_save_and_menu_read_users_not_formularz(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        legacy_row = [
            "2026-10-07 08:00:00", "100", "Ala", "Hel", "54.6", "18.8",
            "06:00", "16:00", "pl",
        ]
        bot.gc.formularz.rows.append(legacy_row[:])
        form_snapshot = [row[:] for row in bot.gc.formularz.rows]

        bot.run(bot.webapp_settings(100, morning="07:00", afternoon="brak"))

        rec = bot.users.record(100)
        assert rec["report_morning_time"] == "07:00"
        assert rec["report_afternoon_time"] == "brak"
        assert bot.gc.formularz.rows == form_snapshot, "ustawienia nie mogą zapisywać Formularza"
        assert bot.has_reply(100, "Ustawienia raportów")

        bot.run(bot.msg(100, "/menu"))
        panel = bot.replies(100)[-1]
        # PR3 GRUPY: panel godzin to ten sam dialog tekstowy co /raport
        # (bez klawiatury WebApp — także w czacie prywatnym).
        assert "Rano: 07:00" in panel
        assert "06:00" not in panel and "16:00" not in panel
        assert "Czy chcesz zmienić te godziny?" in panel

    def test_onboarding_is_short_and_points_to_city_and_data(self, bot):
        # PR2 UX cleanup: krótki onboarding, bez obietnicy automatycznych raportów
        # (te pojawią się dopiero w PR3) i bez technicznych komend /save_location.
        bot.run(bot.msg(100, "/start BETAX1"))
        assert bot.has_reply(100, "Dostęp aktywny")
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
        assert bot.has_reply(100, "bot prywatny")
        assert bot.has_reply(100, "/start BETAX1")

    def test_referral_from_access_only_user(self, bot):
        import base64
        bot.run(bot.msg(100, "/start BETAX1"))
        token = base64.urlsafe_b64encode(b"100").decode().rstrip("=")
        bot.run(bot.msg(200, f"/start {token}"))
        assert bot.users.record(200) is not None, "referral od usera z samym access-em odrzucony"
        assert bot.has_reply(200, "Dostęp aktywny")


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
            assert bot.has_reply(100, "Wpisz poniżej nazwę miejscowości"), cmd
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
        # POPRAWKA #4: komunikat to pełny opis zapisanej lokalizacji.
        assert bot.has_reply(103, "Zapisana lokalizacja")
        assert bot.has_reply(103, "Warszawa, Polska")
        assert bot.has_reply(103, "Nie to miejsce? Użyj ponownie /miasto i doprecyzuj nazwę")

    def test_city_command_with_argument_saves_profile_immediately(self, bot):
        bot.run(bot.msg(104, "/start BETAX1"))
        bot.run(bot.msg(104, "/miasto Warszawa"))
        assert bot.users.record(104)["profile_status"] == "active"
        assert bot.oneoffs == []
        # Punkt 1: nazwa WPISANA ręcznie zachowuje pełny komunikat z dopiskiem.
        assert bot.has_reply(104, "Nie to miejsce? Użyj ponownie /miasto i doprecyzuj nazwę")

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
        assert bot.has_reply(100, "Dostęp aktywn")  # a wcześniej rejestracja się udała


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

    def test_legacy_webapp_uses_short_safe_label(self, bot):
        bot.run(bot.webapp_location(700, lat=50.0614, lon=19.9366))

        row = next(r for r in bot.gc.formularz.rows if str(r[1]).strip() == "700")
        assert row[FORM_HEADERS.index("Miasto")] == "Warszawa"
        assert bot.has_reply(700, "Rozpoznano: Warszawa")

    def test_blocked_legacy_silent(self, bot):
        # POPRAWKA #7: brak dostępu = cisza (obcy, blocked, revoked, BLOCKED_).
        bot.run(bot.msg(701, "/day"))
        assert bot.replies(701) == []

    def test_users_blocked_wins_over_legacy_row(self, bot, monkeypatch):
        # wiersz legacy CZYSTY (bez BLOCKED_), ale Users mówi blocked -> brak dostępu
        bot.gc.formularz.rows.append(
            ["2026-01-03 08:00:00", "800", "BlockedInUsers", "Poznań",
             "52.4", "16.9", "08:00", "14:00", "pl"])
        bot.gc.users.grid.append(["800"] + [""] * (len(USERS_HEADERS) - 1))
        bot.gc.users.grid[-1][USERS_HEADERS.index("access_status")] = "blocked"

        bot.run(bot.msg(800, "/day"))
        assert bot.replies(800) == [], "Users blocked wygrywa z legacy i milczy"


class TestPrivacyCommands:
    def test_privacy_silent_without_access(self, bot):
        # POPRAWKA #7: czat bez wiersza w rejestrze (obcy / po hard delete) nie ma
        # danych, więc komendy RODO są ciszą — odpowiada wyłącznie /start.
        bot.run(bot.msg(555, "/privacy"))
        bot.run(bot.msg(555, "/my_data"))
        bot.run(bot.msg(555, "/forget_location"))
        bot.run(bot.msg(555, "/delete_me"))
        assert bot.replies(555) == []

    def test_privacy_works_with_access(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/privacy"))
        assert bot.has_reply(100, "PRYWATNOŚĆ I TWOJE DANE")
        privacy = [t for t in bot.replies(100) if "PRYWATNOŚĆ" in t][0]
        assert lb._md_safe(lb.PRIVACY_URL) in privacy
        assert privacy.rstrip().endswith(lb._md_safe(lb.PRIVACY_URL)), \
            "link do pełnej polityki zamyka wiadomość"
        # Numer wersji polityki nie jest już częścią treści — zostaje tylko
        # w kontrakcie wywołania t_ui (version=PRIVACY_VERSION).
        assert lb.PRIVACY_VERSION not in privacy
        assert "{url}" not in privacy and "{version}" not in privacy

    def test_my_data_shows_access(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/my_data"))
        assert bot.has_reply(100, "TWOJE DANE")
        assert bot.has_reply(100, "aktywny")

    def test_my_data_legacy_shows_location(self, bot):
        bot.run(bot.msg(700, "/my_data"))
        assert bot.has_reply(700, "legacy")
        assert bot.has_reply(700, "Gdańsk")

    def test_my_data_stranger_silent(self, bot):
        bot.run(bot.msg(555, "/my_data"))
        assert bot.replies(555) == []

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

    def test_delete_me_hard_delete_everywhere(self, bot, fake_sleep):
        # D2: /delete_me (tak jak /usunDane) najpierw pyta o potwierdzenie,
        # a hard delete wykonuje się dopiero po "Tak, chcę".
        # POPRAWKA #7: po skutecznej kasacji idą DOKŁADNIE dwa komunikaty.
        bot.run(bot.msg(100, "/start BETAX1"))
        lb._set_pending_city("100", lb.CTX_ONEOFF_DAY)
        lb._put_pending_save(100, 52.23, 21.01, "Warszawa", "pl", "city")

        bot.run(bot.msg(100, "/delete_me"))
        assert bot.users.record(100) is not None, "kasacja bez potwierdzenia!"
        assert "100" in lb.PENDING_DELETE

        before = len(bot.sent)
        bot.run(bot.msg(100, "Tak, chcę"))
        after = [t for c, t in bot.sent[before:] if c == 100]
        assert len(after) == 2, f"oczekiwane dwa komunikaty, jest {len(after)}: {after}"
        assert "Usunąłem Twoje dane" in after[0]
        assert "bot prywatny" in after[1] and "/start BETAX1" in after[1]
        assert fake_sleep == [lb.DELETE_NOTICE_DELAY_SEC], "brak pauzy przed drugim komunikatem"
        assert bot.users.record(100) is None, "wiersz Users nieusunięty"
        assert "100" not in lb.PENDING_CITY, "PENDING_CITY nie wyczyszczone"
        assert "100" not in lb.PENDING_SAVE, "PENDING_SAVE nie wyczyszczone"
        assert "100" not in lb.PENDING_DELETE, "PENDING_DELETE nie wyczyszczone"

        # od tego momentu czat milczy (odpowiada tylko /start)
        before = len(bot.sent)
        bot.run(bot.msg(100, "/day"))
        bot.run(bot.msg(100, "/dane"))
        assert len(bot.sent) == before, "po hard delete czat nie może dostawać odpowiedzi"

    def test_delete_me_legacy_rows_removed_including_blocked(self, bot, fake_sleep):
        # Konto z dostępem (czysty wiersz legacy) + własny wiersz BLOCKED_:
        # oba wiersze znikają, a sąsiedzi zostają nietknięci.
        bot.gc.formularz.rows.append(["2026-01-04 08:00:00", "702", "Legacy", "Hel", "54.6", "18.8", "08:00", "14:00", "pl"])
        bot.gc.formularz.rows.append(["2026-01-04 08:05:00", "BLOCKED_702", "Legacy", "Hel", "", "", "08:00", "14:00", "pl"])
        bot.run(bot.msg(702, "/delete_me"))
        bot.run(bot.msg(702, "Tak, chcę"))
        assert "702" not in bot.gc.formularz.chat_ids()
        assert "BLOCKED_702" not in bot.gc.formularz.chat_ids()
        assert "700" in bot.gc.formularz.chat_ids(), "usunięto wiersze obcego użytkownika!"

    def test_delete_me_stranger_silent(self, bot):
        # POPRAWKA #7: obcy nie ma nawet czym potwierdzać — cisza i brak pendingu.
        bot.run(bot.msg(555, "/delete_me"))
        bot.run(bot.msg(555, "Tak, chcę"))
        assert bot.replies(555) == []
        assert "555" not in lb.PENDING_DELETE


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

        assert bot.has_reply(100, "Wpisz poniżej nazwę miejscowości dla prognozy dziennej")
        # Punkt 4: przykład bez backticków (ta sama czcionka co tekst) i lokalny.
        assert bot.has_reply(100, "np. Olsztyn, koło Częstochowa, śląskie lub Olsztyn 42-256")
        assert not bot.has_reply(100, "`")
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
        # (POPRAWKA #4: krótka etykieta i bezpieczny opis lokalizacji)
        assert bot.has_reply(100, "📍 *Użyta lokalizacja:*")
        assert bot.has_reply(100, "Warszawa, Polska")
        assert bot.has_reply(
            100, "Nie to miejsce? Użyj ponownie /miasto i doprecyzuj nazwę"
        )

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
        # POPRAWKA #4: pełny opis lokalizacji zamiast listy komend w stopce.
        assert bot.has_reply(100, "Zapisana lokalizacja")
        assert bot.has_reply(100, "Warszawa, Polska")
        assert not bot.has_reply(100, "/save_location")

    def test_d_city_prompt_shows_current_location_when_profile_active(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/miasto"))
        bot.run(bot.msg(100, "Warszawa"))
        bot.run(bot.msg(100, "/miasto"))

        assert bot.has_reply(100, "Obecna lokalizacja:\nWarszawa")
        assert bot.has_reply(100, "Jeśli zgadzasz się na zapis lokalizacji:")
        assert bot.has_reply(100, "👉 Wpisz nazwę miejscowości lub użyj GPS:")
        assert bot.replies(100)[-1] == lb.t_ui("pl", "city_prompt_active", city="Warszawa")
        # Skrócony prompt: bez bloku "Przykłady:", bez backticków i bez
        # komend prognoz — szczegóły mieszkają w /info i /porady.
        assert not bot.has_reply(100, "Przykłady:")
        assert not bot.has_reply(100, "`Warszawa`")
        assert "/porady" not in bot.replies(100)[-1]

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
    def test_g_usun_dane_requires_confirmation(self, bot, fake_sleep):
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
        before = len(bot.sent)
        bot.run(bot.msg(100, "Tak, chcę"))
        assert bot.users.record(100) is None
        after = [t for c, t in bot.sent[before:] if c == 100]
        assert len(after) == 2
        assert "Usunąłem Twoje dane" in after[0]
        assert "bot prywatny" in after[1] and "/start BETAX1" in after[1]

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

    def test_g_other_text_does_not_consume_delete_pending(self, bot, fake_sleep):
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
        invite_message = bot.replies(100)[-2]
        assert r"https://t.me/Twoja\_pogoda\_bot?start=MTAw" in invite_message

        bot.run(bot.msg(100, "/teraz"))
        assert lb.PENDING_CITY["100"]["ctx"] == lb.CTX_ONEOFF_NOW

        bot.run(bot.msg(100, "/trend"))
        assert lb.PENDING_CITY["100"]["ctx"] == lb.CTX_ONEOFF_FUTURE

    def test_bez_gps_and_usun_dane_are_case_insensitive(self, bot, monkeypatch, fake_sleep):
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

    def test_hidden_delete_commands_work_as_buttons(self, bot, fake_sleep):
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
        before = len(bot.sent)
        bot.run(bot.msg(100, "/dzien Warszawa"))
        assert len(bot.oneoffs) == 1
        # brak komunikatu o oknie czasowym PO wywołaniu /dzien
        assert not any("05:00" in t for t in bot.replies_since(100, before))

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
    def test_report_for_users_only_account_has_unconfigured_slots_without_defaults(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/raport"))

        panel = bot.replies(100)[-1]
        assert "GODZINY RAPORTÓW" in panel
        assert "Rano: —" in panel and "Popołudnie: —" in panel
        assert "08:00" not in panel and "14:00" not in panel
        assert "Obecna lokalizacja" not in panel
        # PR3 GRUPY: panel godzin jest dialogiem tekstowym — bez przycisku
        # WebApp także w czacie prywatnym (godziny zmienia się odpowiedziami).
        assert bot.keyboards_for(100) == []
        assert "Czy chcesz zmienić te godziny?" in panel

    def test_legacy_only_report_settings_are_not_read_from_formularz(self, bot):
        bot.run(bot.msg(700, "/raport"))
        panel = bot.replies(700)[-1]
        assert "GODZINY RAPORTÓW" in panel
        assert "w trakcie przenoszenia" in panel
        assert "08:00" not in panel
        assert bot.keyboards_for(700) == []

    # --- /info (pkt 16) --------------------------------------------------------
    def test_info_contains_tips_and_hidden_commands(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/info"))
        info = bot.replies(100)[-1]
        for needle in ("/dzien", "/teraz", "/trend", "/miasto", "/raport",
                       "/zapros", "/dane", "/porady", "/priv"):
            assert needle in info, needle
        # Komendy zarządzania danymi mieszkają w /dane i /priv, nie w /info.
        assert "/bezGPS" not in info and "/usunDane" not in info
        assert "/zapros — zaproszenie znajomego lub dodanie bota do grupy" in info
        # POPRAWKA #5: techniczny alias /save_location znika z /info
        # (nadal działa w kodzie jako ukryta komenda kompatybilnościowa).
        assert "/save_location" not in info.replace("\\", "")
        assert not BACKTICKED_COMMAND.search(info)

    def test_porady_command_still_works_but_is_not_in_menu(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/porady"))
        assert bot.has_reply(100, "PORADY I PRZYDATNE FUNKCJE")
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
        """Komendy leżą bezpośrednio pod sobą; sekcje rozdzielone \n\n."""
        bot.run(bot.msg(100, "/start BETAX1"))
        welcome = bot.replies(100)[-1]
        assert welcome.startswith("✅ Dostęp aktywny! Witaj w Pogoda World 🌍")

        lines = welcome.split("\n")
        # sekcje rozdzielone DOKŁADNIE jedną pustą linią
        assert lines[1] == "", "po powitaniu pusta linia"
        assert lines[2].startswith("🌤")
        # trzy komendy pod sobą bez pustych linii
        assert lines[3].startswith("/dzien")
        assert lines[4].startswith("/teraz")
        assert lines[5].startswith("/trend")
        assert lines[6] == "", "przed 🔔 pusta linia"
        assert lines[7].startswith("🔔")
        assert "/miasto" in lines[8]
        assert lines[9] == "", "przed 🔒 pusta linia"
        assert lines[10].startswith("🔒")
        assert lines[10].endswith("/dane")
        # komendy zwykłym tekstem, nigdy w backtickach
        assert "`" not in welcome


# ============================================================================
# POPRAWKA #4: pełny opis lokalizacji, teren bez miejscowości, awaria łącz
# ============================================================================
class TestLocationDescription:
    """Karta lokalizacji musi pozwolić rozpoznać miejsce (wiele miejscowości
    dzieli jedną nazwę), a awaria geokodera musi być widoczna dla użytkownika."""

    def test_pin_in_city_flow_shows_full_address(self, bot):
        bot.run(bot.msg(110, "/start BETAX1"))
        bot.run(bot.msg(110, "/miasto"))
        bot.run(bot.pin(110, reply=False))

        assert bot.users.record(110)["profile_status"] == "active"
        # Punkt 1: pinezka nie prosi o doprecyzowanie nazwy — pełny adres TAK,
        # akapit „Nie to miejsce?…” NIE.
        assert bot.has_reply(110, "✅ *Zapisana lokalizacja:*")
        assert bot.has_reply(110, "Hel, Polska")
        assert not bot.has_reply(110, "Nie to miejsce?")
        assert bot.replies(110)[-1] == lb.t_ui(
            "pl", "location_saved_short", display_location=lb._md_safe("Hel, Polska")
        )

    def test_webapp_gps_in_city_flow_shows_full_address(self, bot):
        bot.run(bot.msg(111, "/start BETAX1"))
        bot.run(bot.msg(111, "/miasto"))
        bot.run(bot.webapp_location(111, lat=50.0614, lon=19.9366))

        assert bot.users.record(111)["profile_status"] == "active"
        assert bot.has_reply(111, "Warszawa, Polska")
        # Punkt 1: WebApp/GPS również bez „Nie to miejsce?…”.
        assert not bot.has_reply(111, "Nie to miejsce?")

    def test_field_location_uses_message_from_code(self, bot, monkeypatch):
        """Pustynia/góry: geokoder nie zna miejscowości, ale zna teren."""
        monkeypatch.setattr(
            lb, "get_location_details_from_coords",
            lambda lat, lon, lang, query=None, mode=None: (None, "Sahara, Algieria", lb.GEO_NO_CITY),
        )
        bot.run(bot.msg(112, "/start BETAX1"))
        bot.run(bot.msg(112, "/miasto"))
        bot.run(bot.pin(112, reply=False))

        rec = bot.users.record(112)
        assert rec["profile_status"] == "active", "teren nadal jest zapisywany jako lokalizacja"
        assert rec["location_label"] == lb.FIELD_LOCATION_LABEL
        assert bot.has_reply(112, "Lokalizacja w terenie (poza miastem)")

    def test_geocoder_failure_reports_and_saves_nothing(self, bot, monkeypatch):
        monkeypatch.setattr(
            lb, "get_location_details_from_coords",
            lambda lat, lon, lang, query=None, mode=None: (None, None, lb.GEO_ERROR),
        )
        bot.run(bot.msg(113, "/start BETAX1"))
        bot.run(bot.msg(113, "/miasto"))
        bot.run(bot.pin(113, reply=False))

        rec = bot.users.record(113)
        assert rec["profile_status"] == "none", "przy awarii łącz nie zapisujemy lokalizacji"
        assert rec["lat_round"] == "" and rec["lon_round"] == ""
        assert bot.has_reply(113, "⚠️ Błędy na łączach, spróbuj za chwilę ponownie.")
        assert not bot.has_reply(113, "Zapisana lokalizacja")

    def test_city_search_failure_is_not_reported_as_not_found(self, bot, monkeypatch):
        monkeypatch.setattr(
            lb, "geocode_city_accepted",
            lambda city, lang="pl": (lb.GEOCODE_ERROR, None, None, None, None),
        )
        bot.run(bot.msg(114, "/start BETAX1"))
        bot.run(bot.msg(114, "/miasto"))
        bot.run(bot.msg(114, "Wiązowna"))

        assert bot.has_reply(114, "⚠️ Błędy na łączach, spróbuj za chwilę ponownie.")
        assert not bot.has_reply(114, "Nie mogłem znaleźć")
        assert bot.users.record(114)["profile_status"] == "none"

    def test_city_not_found_keeps_the_old_message(self, bot, monkeypatch):
        monkeypatch.setattr(
            lb, "geocode_city_accepted",
            lambda city, lang="pl": (lb.GEOCODE_NOT_FOUND, None, None, None, None),
        )
        bot.run(bot.msg(115, "/start BETAX1"))
        bot.run(bot.msg(115, "/miasto"))
        bot.run(bot.msg(115, "Xyzzz"))

        assert bot.has_reply(115, "Na mapie nie ma miejsca o takiej nazwie")

    # ------------------------------------------------------------------
    # ETAP 1: krótkie i niepasujące zapytania geokodera
    # ------------------------------------------------------------------
    @pytest.mark.parametrize("text", ["U", "Wa", "Os", "a b"])
    def test_short_query_never_reaches_the_geocoder(self, bot, monkeypatch, text):
        """1-2 znaki bez kontekstu: komunikat, zero pytań do mapy, zero zapisu."""
        calls = []

        def fake(city, lang="pl"):
            calls.append(city)
            return (lb.GEOCODE_OK, 52.22972, 21.01223, city, f"{city}, Polska")

        freeze_local_hour(monkeypatch, 10, 0)
        monkeypatch.setattr(lb, "geocode_city_accepted", fake)

        bot.run(bot.msg(116, "/start BETAX1"))
        bot.run(bot.msg(116, f"/miasto {text}"))
        bot.run(bot.msg(116, "/teraz"))
        bot.run(bot.msg(116, text))

        assert bot.has_reply(116, "Ta nazwa jest za krótka"), text
        assert not bot.has_reply(116, "Szukam lokalizacji"), "nie udajemy, że szukamy"
        assert calls == [], f"geokoder nie mógł być zapytany o {text!r}"
        assert bot.users.record(116)["profile_status"] == "none"
        assert bot.oneoffs == []
        assert "116" not in lb.PENDING_CITY

    def test_short_and_rejected_query_never_generates_a_oneoff_card(
        self, bot, monkeypatch
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(117, "/start BETAX1"))
        for command in ("/dzien", "/teraz", "/trend"):
            bot.run(bot.msg(117, f"{command} Wa"))
        assert bot.oneoffs == []
        assert "117" not in lb.PENDING_SAVE
        assert bot.users.record(117)["profile_status"] == "none"

    @pytest.mark.parametrize("status,expected", [
        (lb.GEOCODE_NO_MATCH, "Na mapie nie ma miejsca o takiej nazwie"),
        (lb.GEOCODE_UNCERTAIN, "kilka różnych miejsc"),
    ])
    def test_rejected_candidates_save_nothing_and_show_own_message(
        self, bot, monkeypatch, status, expected
    ):
        """NO_MATCH i UNCERTAIN zostają osobne: inny tekst, ta sama cisza w danych."""
        freeze_local_hour(monkeypatch, 10, 0)
        monkeypatch.setattr(
            lb, "geocode_city_accepted",
            lambda city, lang="pl": (status, None, None, None, None),
        )
        bot.run(bot.msg(118, "/start BETAX1"))
        bot.run(bot.msg(118, "/miasto"))
        bot.run(bot.msg(118, "Hel"))

        assert bot.has_reply(118, expected)
        assert not bot.has_reply(118, "Nie mogłem znaleźć"), "to nie jest pusta mapa"
        rec = bot.users.record(118)
        assert rec["profile_status"] == "none"
        assert rec["lat_round"] == "" and rec["lon_round"] == ""
        assert "118" not in bot.gc.formularz.chat_ids()
        assert "118" not in lb.PENDING_CITY, "stan oczekiwania nie wraca sam"

        bot.run(bot.msg(118, "/teraz Hel"))
        bot.run(bot.msg(118, "/dzien Hel"))
        assert bot.oneoffs == []
        # Brak wyboru z listy: żaden status nie może doczepić inline keyboarda.
        assert all("inline_keyboard" not in (markup or {}) for markup in bot.markups)

    def test_full_name_still_saves_profile_after_validation(self, bot, monkeypatch):
        """Poprawka nie może cofnąć tego, co działa: pełna nazwa = zapis profilu."""
        calls = []

        def fake(city, lang="pl"):
            calls.append(city)
            return (lb.GEOCODE_OK, 54.6037, 18.7616, "Hel", "Hel, powiat pucki, Polska")

        monkeypatch.setattr(lb, "geocode_city_accepted", fake)
        monkeypatch.setattr(
            lb, "get_location_details_from_coords",
            lambda lat, lon, lang="pl", query=None, mode=None: (
                "Hel", "Hel, powiat pucki, województwo pomorskie, Polska", lb.GEO_OK,
            ),
        )
        bot.run(bot.msg(119, "/start BETAX1"))
        bot.run(bot.msg(119, "/miasto Hel"))

        assert calls == ["Hel"]
        rec = bot.users.record(119)
        assert rec["profile_status"] == "active"
        assert rec["location_label"] == "Hel"
        assert bot.has_reply(119, "Hel, powiat pucki, województwo pomorskie, Polska")


# ============================================================================
# ETAP 1.1: walidowany top result w PEŁNYM flow bota (prawdziwy rdzeń)
# ============================================================================

class _Stage11Nominatim:
    """Atrapa Nominatima dla flow: zero sieci, zapisuje zapytania i kwargs."""

    calls = []
    results = {}

    def __init__(self, user_agent=None):
        pass

    def geocode(self, query, exactly_one=True, language=None, limit=None,
                addressdetails=None, **kwargs):
        _Stage11Nominatim.calls.append({"query": query, **kwargs})
        return _Stage11Nominatim.results.get(query)

    @classmethod
    def reset(cls, results):
        cls.calls = []
        cls.results = {query: list(value) for query, value in results.items()}


def _stage_result(address, name, lat, lon, cls="place", typ="city", namedetails=None):
    """Minimalny obiekt w kształcie geopy Location, z surowym address."""
    raw = {"class": cls, "type": typ, "address": dict(address),
           "display_name": ", ".join(str(v) for v in address.values())}
    if namedetails:
        raw["namedetails"] = dict(namedetails)
    return type("Location", (), {"raw": raw, "latitude": lat, "longitude": lon,
                                 "address": name})()


PARIS_FR = _stage_result(
    {"city": "Paris", "country": "Francja", "country_code": "fr"},
    "Paris", 48.8566, 2.3522, namedetails={"name": "Paris", "name:pl": "Paryż"},
)
NEW_YORK_US = _stage_result(
    {"city": "New York", "state": "New York", "country": "Stany Zjednoczone",
     "country_code": "us"},
    "New York", 40.7128, -74.006,
)
HELMAND_AF = _stage_result(
    {"state": "Helmand", "country": "Afganistan", "country_code": "af"},
    "Helmand", 31.5, 65.0, cls="boundary", typ="administrative",
)
HEL_PL = _stage_result(
    {"city": "Hel", "municipality": "Hel", "county": "pucki", "state": "pomorskie",
     "postcode": "84-150", "country": "Polska", "country_code": "pl"},
    "Hel", 54.6037, 18.7616,
)


@pytest.fixture
def stage11_geocoder(monkeypatch):
    """Prawdziwy ``geocode_city_accepted`` + atrapa Nominatima (bez sieci)."""
    # Klucze = dokładnie to, o co pyta rdzeń geokodowania: jawny kraj jest
    # odcinany jako filtr, więc "Nowy Jork USA" trafia tu jako "Nowy Jork".
    _Stage11Nominatim.reset({"Paryż": [PARIS_FR], "Nowy Jork": [NEW_YORK_US],
                             "Hel": [HELMAND_AF, HEL_PL]})
    monkeypatch.setattr(lb, "Nominatim", _Stage11Nominatim)
    monkeypatch.setattr(lb, "geocode_city_accepted", REAL_GEOCODE_CITY_ACCEPTED)
    lb._GEO_DETAILS_CACHE.clear()
    yield _Stage11Nominatim
    lb._GEO_DETAILS_CACHE.clear()


def _run_real_guest(text, lang="pl", chat_type="private"):
    """Woła PRAWDZIWY handle_guest_now z atrapami wysyłki (bez Telegrama)."""
    import guest_bot_handler as gbh

    sent, photos, payloads = [], [], []
    handled = gbh.handle_guest_now(
        message={"text": text,
                 "chat": {"id": 555 if chat_type == "private" else -100555,
                          "type": chat_type},
                 "from": {"language_code": lang}},
        bot_username="PogodaWorldBot",
        get_coords_fn=REAL_GET_COORDS_FROM_CITY,
        geocode_status_fn=lambda city, l: lb.geocode_city_details_status(city, l),
        shortening_ok_fn=lb.geocode_shortening_is_safe,
        build_payload_fn=lambda lat, lon, lang, card_type, city: (
            payloads.append((lat, lon, card_type, city)) or {"location": {"tz": "UTC"}}
        ),
        prepare_layout_fn=lambda payload, card_type: {"layout": card_type},
        render_png_fn=lambda layout: "/tmp/karta.png",
        send_photo_fn=lambda chat_id, path, city, f_address: photos.append(
            (chat_id, city, f_address)
        ),
        send_reply_fn=lambda chat_id, txt: sent.append(txt),
        get_city_fn=lambda lat, lon, lang: "Paris" if lon > 0 else "Nowy Jork",
    )
    return handled, sent, photos, payloads


class TestStage11ValidatedTopResult:
    """ETAP 1.1: kraj z zapytania + pierwszy zwalidowany kandydat w pełnym flow."""

    def test_teraz_paris_with_country_generates_a_card(self, bot, stage11_geocoder):
        bot.run(bot.msg(120, "/start BETAX1"))
        bot.run(bot.msg(120, "/teraz Paryż Francja"))

        assert stage11_geocoder.calls, "geokoder musi zostać zapytany"
        assert stage11_geocoder.calls[0]["query"] == "Paryż"
        assert stage11_geocoder.calls[0]["country_codes"] == "fr"
        assert len(bot.oneoffs) == 1
        chat_id, lat, lon, city, lang, card_type = bot.oneoffs[0]
        assert (chat_id, lat, lon, lang, card_type) == (120, 48.8566, 2.3522, "pl", "now")
        assert city, "karta musi mieć nazwę miejscowości"
        assert bot.users.record(120)["profile_status"] == "none", "bez zapisu profilu"

    def test_teraz_new_york_usa_generates_a_card(self, bot, stage11_geocoder):
        bot.run(bot.msg(121, "/start BETAX1"))
        bot.run(bot.msg(121, "/teraz Nowy Jork USA"))

        assert stage11_geocoder.calls[0]["query"] == "Nowy Jork"
        assert stage11_geocoder.calls[0]["country_codes"] == "us"
        assert bot.oneoffs[-1][1:3] == (40.7128, -74.006)

    def test_teraz_hel_with_country_skips_helmand(self, bot, stage11_geocoder):
        bot.run(bot.msg(122, "/start BETAX1"))
        bot.run(bot.msg(122, "/teraz Hel Polska"))

        assert stage11_geocoder.calls[0]["query"] == "Hel"
        assert stage11_geocoder.calls[0]["country_codes"] == "pl"
        assert bot.oneoffs[-1][1:3] == (54.6037, 18.7616), "kandydat z Afganistanu odpada"

    def test_miasto_short_query_saves_no_profile(self, bot, stage11_geocoder):
        bot.run(bot.msg(123, "/start BETAX1"))
        bot.run(bot.msg(123, "/miasto Wa"))

        assert stage11_geocoder.calls == [], "za krótkie zapytanie nie pyta mapy"
        assert bot.has_reply(123, "Ta nazwa jest za krótka")
        rec = bot.users.record(123)
        assert rec["profile_status"] == "none"
        assert rec["lat_round"] == "" and rec["lon_round"] == ""

    def test_guest_short_query_makes_no_card_but_valid_city_does(
        self, bot, stage11_geocoder
    ):
        """?12 Wa -> cisza na mapie i zero karty; ?12 Paryż Francja -> karta."""
        bot.run(bot.msg(124, "/start BETAX1"))

        handled, sent, photos, payloads = _run_real_guest("?12 Wa")
        assert handled is True
        assert photos == [] and payloads == []
        assert stage11_geocoder.calls == [], "geokoder nie był pytany o 'Wa'"
        assert i18n.t_ui("pl", "geocode_too_short") in sent[-1]

        _handled, _sent, photos, payloads = _run_real_guest("?12 Paryż Francja")
        assert photos and payloads, "zwalidowany top result daje kartę"
        assert stage11_geocoder.calls[-1]["country_codes"] == "fr"

    def test_guest_call_gets_status_and_shortening_helpers(self, bot):
        """location_bot wstrzykuje adapter statusów i safety skrótu z 1.1."""
        bot.run(bot.msg(125, "/start BETAX1"))
        bot.run(bot.msg(125, "?12 Hel"))

        kwargs = bot.guest_kwargs[-1]
        assert kwargs["get_coords_fn"] is lb.get_coords_from_city
        assert kwargs["shortening_ok_fn"] is lb.geocode_shortening_is_safe
        assert callable(kwargs["geocode_status_fn"])


# ============================================================================
# POPRAWKA #5: /info bez /save_location, /porady w języku użytkownika
# ============================================================================
class TestInfoAndTipsContent:
    def test_info_no_longer_advertises_save_location(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/info"))

        info = bot.replies(100)[-1]
        assert "/save_location" not in info.replace("\\", "")
        assert "/bezGPS" not in info and "/usunDane" not in info, \
            "komendy zarządzania danymi są w /dane i /priv, nie w /info"
        assert "*Komendy rzadziej używane:*" not in info
        assert info == lb.t_ui("pl", "info_msg")

    def test_tips_reply_has_no_french_leak_in_polish(self, bot):
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/porady"))

        tips = bot.replies(100)[-1]
        assert tips.startswith("💡 *PORADY I PRZYDATNE FUNKCJE*")
        assert "TRUCS ET ASTUCES" not in tips
        assert not BACKTICKED_COMMAND.search(tips)
        assert tips.count("`") % 2 == 0 and tips.count("*") % 2 == 0


# ============================================================================
# POPRAWKA #7: po hard delete / bez dostępu odpowiada WYŁĄCZNIE /start
# ============================================================================
# Komendy, skróty i teksty, które dla czatu bez dostępu muszą być CISZĄ.
# Lista celowo pokrywa wszystkie warstwy skrótów (#6) i komendy RODO.
NO_ACCESS_SILENT_INPUTS = (
    "/now", "/dzien", "/teraz", "/trend", "/raport", "/menu", "/miasto",
    "/info", "/zapros", "/dane", "/priv", "/bezGPS", "/usunDane", "/delete_me",
    "/privacy", "/my_data",
    "?d Warszawa", "?12 Warszawa", "?14 Warszawa", "?t Berlin", "?j Paris",
    ".d Warszawa", ".n Hel", ".f", ".p Hel",
    "Warszawa", "jaka pogoda?", "cześć, co potrafisz?",
)


class TestPostDeleteUx:
    """POPRAWKA #7: dwa komunikaty po kasacji, potem cisza poza /start."""

    def test_delete_sends_exactly_two_messages_in_order(self, bot, fake_sleep):
        bot.run(bot.msg(500, "/start BETAX1"))
        bot.run(bot.msg(500, "/miasto Warszawa"))
        bot.run(bot.msg(500, "/usunDane"))
        before = len(bot.sent)
        bot.run(bot.msg(500, "Tak, chcę"))

        msgs = [t for c, t in bot.sent[before:] if c == 500]
        assert len(msgs) == 2, f"oczekiwane dokładnie 2 komunikaty: {msgs}"
        assert "Usunąłem Twoje dane" in msgs[0]
        assert "bot prywatny" in msgs[1] and "/start BETAX1" in msgs[1]
        assert fake_sleep == [lb.DELETE_NOTICE_DELAY_SEC], "brak pauzy 1 s między komunikatami"
        assert bot.users.record(500) is None

    def test_second_message_equals_full_no_access_in_six_languages(self, monkeypatch, fake_sleep):
        monkeypatch.setenv("PUBLIC_BETA_CODES", "BETA2026A")

        for lang in ("pl", "en", "de", "es", "fr", "no"):
            local = BotHarness(monkeypatch)
            local.run(local.msg(600, "/start BETA2026A", lang=lang))
            local.run(local.msg(600, "/usunDane", lang=lang))
            before = len(local.sent)
            local.run(local.msg(600, lb.t_ui(lang, "delete_confirm_yes"), lang=lang))

            msgs = [t for c, t in local.sent[before:] if c == 600]
            assert len(msgs) == 2, f"{lang}: {msgs}"
            assert msgs[0] == lb.t_ui(lang, "delete_me_done"), lang
            assert msgs[1] == lb._invite_message(lang, "no_access"), lang
            assert "/start BETA2026A" in msgs[1], lang

    def test_access_invite_messages_no_link_in_block_messages(self, monkeypatch):
        """D2: invalid_link / limit_reached / registration_closed nie zawierają
        już linku PUBLIC_INVITE_URL — tylko sam no_access niesie kod."""
        monkeypatch.setenv("PUBLIC_BETA_CODES", "BETA2026A")

        for lang in ("pl", "en", "de", "es", "fr", "no"):
            no_access_text = lb._invite_message(lang, "no_access")
            assert "/start BETA2026A" in no_access_text, (lang, no_access_text)
            for key in ("invalid_link", "limit_reached", "registration_closed"):
                text = lb._invite_message(lang, key)
                assert "https://" not in text, (lang, key, text)
                assert "{" not in text and "}" not in text, (lang, key, text)

    def test_no_access_start_sends_dynamic_code_link_in_six_languages(self, monkeypatch):
        monkeypatch.setenv("PUBLIC_BETA_CODES", "BETA2026A")

        for index, lang in enumerate(("pl", "en", "de", "es", "fr", "no")):
            local = BotHarness(monkeypatch)
            chat_id = 700 + index
            local.run(local.msg(chat_id, "/start", lang=lang))
            text = local.replies(chat_id)[-1]

            assert "/start BETA2026A" in text, (lang, text)
            assert "`" not in text, f"{lang}: komenda nie może być otoczona backtickami"

    def test_post_delete_everything_is_silent(self, bot, fake_sleep):
        bot.run(bot.msg(500, "/start BETAX1"))
        bot.run(bot.msg(500, "/usunDane"))
        bot.run(bot.msg(500, "Tak, chcę"))

        before = len(bot.sent)
        for text in NO_ACCESS_SILENT_INPUTS:
            bot.run(bot.msg(500, text))
        bot.run(bot.pin(500, reply=True))
        bot.run(bot.pin(500, reply=False))
        bot.run(bot.callback_query(500, data="test"))
        bot.run(bot.webapp_location(500))
        assert len(bot.sent) == before, (
            "po hard delete czat nie ma danych ani dostępu — odpowiada wyłącznie /start, "
            f"a odpowiedziały: {[t[:40] for c, t in bot.sent[before:] if c == 500]}"
        )

    def test_post_delete_start_paths(self, bot, fake_sleep):
        bot.run(bot.msg(500, "/start BETAX1"))
        bot.run(bot.msg(500, "/usunDane"))
        bot.run(bot.msg(500, "Tak, chcę"))

        # /start bez kodu -> pełny no_access
        before = len(bot.sent)
        bot.run(bot.msg(500, "/start"))
        assert bot.has_reply(500, "bot prywatny")
        assert "/start BETAX1" in bot.replies(500)[-1]

        # /start zły kod -> invalid_link (nadal ścieżka /start)
        bot.run(bot.msg(500, "/start ZLYKOD"))
        assert bot.has_reply(500, "Nieprawidłowy")

        # /start poprawny kod -> aktywacja i powrót do normalnej obsługi
        bot.run(bot.msg(500, "/start BETAX1"))
        assert bot.has_reply(500, "Dostęp aktywny")
        bot.run(bot.msg(500, "/info"))
        assert bot.has_reply(500, "POMOC I KOMENDY")
        assert len([t for c, t in bot.sent[before:] if c == 500]) == 4  # 3 x /start + /info

    def test_no_access_chat_only_start_answers(self, bot):
        before = len(bot.sent)
        for text in NO_ACCESS_SILENT_INPUTS:
            bot.run(bot.msg(999, text))
        bot.run(bot.callback_query(999))
        bot.run(bot.webapp_location(999))
        assert len(bot.sent) == before, "nowy czat bez kodu: cisza na wszystko poza /start"
        bot.run(bot.msg(999, "/start"))
        assert bot.has_reply(999, "bot prywatny")
        assert "/start BETAX1" in bot.replies(999)[-1]

    def test_batch_after_delete_stays_silent_for_rest_of_batch(self, bot, fake_sleep):
        # Z7 (tylko RAM): kolejne update'y z TEJ SAMEJ paczki nie widzą już
        # starego dostępu — czat milczy do końca paczki, bez trzeciego komunikatu.
        bot.gc.formularz.rows.append(
            ["2026-01-07 08:00:00", "710", "Legacy", "Hel", "54.6", "18.8", "08:00", "14:00", "pl"])
        bot._updates.append(bot.msg(710, "/usunDane"))
        bot._updates.append(bot.msg(710, "Tak, chcę"))
        bot._updates.append(bot.msg(710, "/day"))
        bot._updates.append(bot.msg(710, "/dane"))
        before = len(bot.sent)
        lb.main_bot()

        msgs = [t for c, t in bot.sent[before:] if c == 710]
        assert len(msgs) == 3, f"pytanie + 2 komunikaty kasacji, nic więcej: {msgs}"
        assert "Czy na pewno" in msgs[0]
        assert "Usunąłem Twoje dane" in msgs[1]
        assert "bot prywatny" in msgs[2] and "/start BETAX1" in msgs[2]

    def test_delete_without_any_rows_sends_only_no_data(self, bot, fake_sleep, monkeypatch):
        # Ścieżka obronna: rejestr nie potwierdził usunięcia żadnego wiersza
        # (np. zmiana arkusza między odczytem paczki a kasacją) — zostaje
        # jedno no_data, bez pauzy i bez drugiego komunikatu.
        monkeypatch.setattr(lb.users_store, "delete_user_row", lambda *a, **k: False)
        bot.run(bot.msg(100, "/start BETAX1"))
        bot.run(bot.msg(100, "/usunDane"))
        before = len(bot.sent)
        bot.run(bot.msg(100, "Tak, chcę"))

        msgs = [t for c, t in bot.sent[before:] if c == 100]
        assert len(msgs) == 1 and "Nie znaleziono" in msgs[0], msgs
        assert fake_sleep == [], "bez kasacji nie ma drugiego komunikatu ani pauzy"


# ============================================================================
# PINEZKA JAKO ZAPIS LOKALIZACJI W GRUPIE I CZACIE PRYWATNYM (A–F)
# ============================================================================
GROUP_PIN_CHAT = -100777
GROUP_ADMIN_ID = 4242
GROUP_MEMBER_ID = 4343


def _bot_reply_payload(text="Komunikat bota", message_id=10, bot_id=999000,
                       username=None, extra=None):
    data = {
        "message_id": message_id,
        "from": {
            "id": bot_id,
            "is_bot": True,
            "first_name": "Pogoda World",
            "username": lb.BOT_USERNAME if username is None else username,
        },
        "text": text,
    }
    if extra:
        data.update(extra)
    return data


class TestGroupPinSavesProfile:
    """W grupie i w czacie prywatnym pinezka w odpowiedzi na dowolny komunikat
    tego bota zapisuje profil lokalizacji w Users — bez generowania karty /day.
    """

    # --- A. Prywatny czat ----------------------------------------------------
    def test_a_private_pin_reply_to_bot_saves_profile_without_oneoff(
        self, bot, monkeypatch
    ):
        freeze_local_hour(monkeypatch, 22, 0)  # poza oknem 05:00-15:59
        bot.run(bot.msg(100, "/start BETAX1"))
        before = len(bot.sent)
        bot.run(
            bot.pin(
                100,
                lat=54.60372,
                lon=18.76164,
                reply_to_message=_bot_reply_payload("Witaj w Pogoda World"),
            )
        )

        rec = bot.users.record(100)
        assert rec["profile_status"] == "active"
        assert rec["lat_round"] == "54.604" and rec["lon_round"] == "18.762"
        assert rec["location_label"] == "Hel"
        assert rec["location_source"] == "gps"
        assert rec["location_consent_at"]
        assert rec["location_consent_version"] == lb.PRIVACY_VERSION
        assert bot.oneoffs == [] and bot.cards == []
        assert bot.has_reply(100, "✅ *Zapisana lokalizacja:*")
        assert not bot.has_reply(100, "Użyta lokalizacja")
        # Punkt 1: pinezka = brak akapitu „Nie to miejsce?…”.
        assert not bot.has_reply(100, "Nie to miejsce?")
        # brak komunikatu o oknie czasowym PO pinezce (onboarding ma 05:00–15:59)
        assert not any("05:00" in t for t in bot.replies_since(100, before))

    # --- B. Grupa — pinezka jako odpowiedź na komunikat bota -----------------
    def test_b_group_pin_reply_to_bot_saves_group_profile_without_day_card(
        self, bot, monkeypatch
    ):
        # Nawet poza oknem karty dziennej (22:00) pinezka zapisuje profil grupy,
        # nie próbuje generować karty /day i nie wysyła blokady okna czasowego.
        freeze_local_hour(monkeypatch, 22, 0)
        day_oneoff_calls = []
        monkeypatch.setattr(
            lb, "_run_city_oneoff",
            lambda *a, **kw: day_oneoff_calls.append((a, kw)),
        )

        bot.run(bot.msg(GROUP_PIN_CHAT, "/start BETAX1", user_id=GROUP_ADMIN_ID))
        before = len(bot.sent)
        bot.run(
            bot.pin(
                GROUP_PIN_CHAT,
                lat=54.60372,
                lon=18.76164,
                user_id=GROUP_ADMIN_ID,
                reply_to_message=_bot_reply_payload("Dowolny komunikat bota"),
            )
        )

        assert GROUP_PIN_CHAT < 0
        rec = bot.users.record(GROUP_PIN_CHAT)
        assert rec is not None
        assert rec["chat_id"] == str(GROUP_PIN_CHAT)
        assert rec["profile_status"] == "active"
        assert rec["lat_round"] == "54.604"
        assert rec["lon_round"] == "18.762"
        assert rec["location_label"] == "Hel"
        assert rec["location_source"] == "gps"
        assert rec["location_consent_at"]
        assert rec["location_consent_version"] == lb.PRIVACY_VERSION
        assert rec["profile_updated_at"]

        assert bot.has_reply(GROUP_PIN_CHAT, "✅ *Zapisana lokalizacja:*")
        assert bot.has_reply(GROUP_PIN_CHAT, "Hel, Polska")
        assert not bot.has_reply(GROUP_PIN_CHAT, "Użyta lokalizacja")
        # Punkt 1: pinezka w grupie (odpowiedź na czat bota) też bez dopisku.
        assert not bot.has_reply(GROUP_PIN_CHAT, "Nie to miejsce?")
        # brak komunikatu o oknie czasowym PO pinezce (onboarding ma 05:00–15:59)
        assert not any("05:00" in t for t in bot.replies_since(GROUP_PIN_CHAT, before))
        assert bot.oneoffs == [], "nie wolno wywoływać generatora karty one-off"
        assert bot.cards == [], "nie wolno wywoływać generatora karty legacy"
        assert day_oneoff_calls == [], "nie wolno wywoływać _run_city_oneoff"
        assert str(GROUP_PIN_CHAT) not in lb.PENDING_SAVE

    # --- C. Grupa — odpowiedź na różne komunikaty bota -----------------------
    @pytest.mark.parametrize("trigger_cmd,bot_msg_extra", [
        ("/miasto", None),
        ("/info", None),
        ("/menu", None),
        ("/raport", None),
        (None, {"photo": [{"file_id": "card_png"}], "caption": "Hel"}),
    ])
    def test_c_group_pin_reply_to_various_bot_messages_saves_location(
        self, bot, monkeypatch, trigger_cmd, bot_msg_extra
    ):
        monkeypatch.setattr(
            lb, "_telegram_chat_member_status",
            lambda chat_id, user_id: "administrator",
        )
        group_id = -100800 - (hash(str(trigger_cmd) + str(bot_msg_extra)) % 100)
        bot.run(bot.msg(group_id, "/start BETAX1", user_id=GROUP_ADMIN_ID))

        if trigger_cmd:
            bot.run(bot.msg(group_id, trigger_cmd, user_id=GROUP_ADMIN_ID))
            bot_reply_text = bot.replies(group_id)[-1]
        else:
            bot_reply_text = ""

        bot.run(
            bot.pin(
                group_id,
                lat=52.22972,
                lon=21.01223,
                user_id=GROUP_ADMIN_ID,
                reply_to_message=_bot_reply_payload(
                    text=bot_reply_text,
                    extra=bot_msg_extra,
                ),
            )
        )

        rec = bot.users.record(group_id)
        assert rec is not None
        assert rec["chat_id"] == str(group_id)
        assert rec["profile_status"] == "active"
        assert rec["lat_round"] == "52.23"
        assert rec["lon_round"] == "21.012"
        assert rec["location_label"] == "Warszawa"
        assert rec["location_source"] == "gps"
        assert str(group_id) not in lb.PENDING_CITY
        assert bot.oneoffs == [] and bot.cards == []
        assert bot.has_reply(group_id, "✅ *Zapisana lokalizacja:*")
        assert bot.has_reply(group_id, "Warszawa, Polska")

    # --- D. Grupa — location bez odpowiedzi ----------------------------------
    def test_d_group_pin_without_reply_is_ignored_and_saves_nothing(self, bot):
        bot.run(bot.msg(GROUP_PIN_CHAT, "/start BETAX1", user_id=GROUP_ADMIN_ID))
        before = len(bot.sent)

        # Zarówno zwykła pinezka bez odpowiedzi, jak i pinezka bez odpowiedzi po /miasto
        # w grupie nie zapisuje przypadkowo lokalizacji grupy.
        bot.run(bot.pin(GROUP_PIN_CHAT, reply=False, user_id=GROUP_ADMIN_ID))
        assert len(bot.sent) == before
        assert bot.users.record(GROUP_PIN_CHAT)["profile_status"] == "none"

        bot.run(bot.msg(GROUP_PIN_CHAT, "/miasto", user_id=GROUP_ADMIN_ID))
        after_prompt = len(bot.sent)
        bot.run(bot.pin(GROUP_PIN_CHAT, reply=False, user_id=GROUP_ADMIN_ID))
        assert len(bot.sent) == after_prompt
        assert bot.users.record(GROUP_PIN_CHAT)["profile_status"] == "none"
        assert bot.oneoffs == []

    # --- E. Bezpieczeństwo ---------------------------------------------------
    def test_e_reply_to_another_user_is_not_treated_as_bot_reply(self, bot):
        bot.run(bot.msg(GROUP_PIN_CHAT, "/start BETAX1", user_id=GROUP_ADMIN_ID))
        # Nawet przy aktywnym /miasto w grupie odpowiedź pinezką na wiadomość
        # innego człowieka NIE zapisuje profilu grupy.
        bot.run(bot.msg(GROUP_PIN_CHAT, "/miasto", user_id=GROUP_ADMIN_ID))

        other_user_msg = {
            "message_id": 55,
            "from": {
                "id": GROUP_MEMBER_ID,
                "is_bot": False,
                "first_name": "Piotr",
                "username": "piotr_member",
            },
            "text": "Gdzie się spotykamy?",
        }
        bot.run(
            bot.pin(
                GROUP_PIN_CHAT,
                user_id=GROUP_ADMIN_ID,
                reply_to_message=other_user_msg,
            )
        )

        rec = bot.users.record(GROUP_PIN_CHAT)
        assert rec["profile_status"] == "none", (
            "odpowiedź na wiadomość innego użytkownika nie może zapisać profilu grupy"
        )
        assert rec["lat_round"] == "" and rec["lon_round"] == ""

        # Również odpowiedź na obcego bota nie zapisuje profilu:
        other_bot_msg = {
            "message_id": 56,
            "from": {
                "id": 777888,
                "is_bot": True,
                "first_name": "Inny Bot",
                "username": "CompletelyDifferentBot",
            },
            "text": "Inny komunikat",
        }
        bot.run(
            bot.pin(
                GROUP_PIN_CHAT,
                user_id=GROUP_ADMIN_ID,
                reply_to_message=other_bot_msg,
            )
        )
        assert bot.users.record(GROUP_PIN_CHAT)["profile_status"] == "none"

    def test_e_bot_id_from_token_matches_only_this_bot(self, bot, monkeypatch):
        monkeypatch.setattr(lb, "TELEGRAM_TOKEN", "777000111:TEST_SECRET")
        bot.run(bot.msg(GROUP_PIN_CHAT, "/start BETAX1", user_id=GROUP_ADMIN_ID))

        # Odpowiedź na innego bota (bez username, inne from.id) -> brak zapisu profilu.
        foreign_bot = {
            "message_id": 60,
            "from": {"id": 888999000, "is_bot": True, "first_name": "Obcy"},
            "text": "Obcy komunikat",
        }
        bot.run(
            bot.pin(
                GROUP_PIN_CHAT,
                user_id=GROUP_ADMIN_ID,
                reply_to_message=foreign_bot,
            )
        )
        assert bot.users.record(GROUP_PIN_CHAT)["profile_status"] == "none"

        # Odpowiedź na wiadomość o dokładnie tym from.id co nasz bot -> zapis profilu.
        own_bot = {
            "message_id": 61,
            "from": {"id": 777000111, "is_bot": True, "first_name": "Pogoda"},
            "text": "Nasz komunikat",
        }
        bot.run(
            bot.pin(
                GROUP_PIN_CHAT,
                lat=54.60372,
                lon=18.76164,
                user_id=GROUP_ADMIN_ID,
                reply_to_message=own_bot,
            )
        )
        rec = bot.users.record(GROUP_PIN_CHAT)
        assert rec["profile_status"] == "active"
        assert rec["location_label"] == "Hel"

    def test_e_plain_text_and_single_group_write_isolation(self, bot, monkeypatch):
        set_profile_calls = []
        real_set_profile = users_store.set_profile

        def spy_set_profile(ws, chat_id, *args, **kwargs):
            set_profile_calls.append(chat_id)
            return real_set_profile(ws, chat_id, *args, **kwargs)

        monkeypatch.setattr(users_store, "set_profile", spy_set_profile)

        bot.run(bot.msg(GROUP_PIN_CHAT, "/start BETAX1", user_id=GROUP_ADMIN_ID))
        bot.run(bot.msg(GROUP_PIN_CHAT, "/miasto", user_id=GROUP_ADMIN_ID))
        bot.run(
            bot.pin(
                GROUP_PIN_CHAT,
                lat=54.60372,
                lon=18.76164,
                user_id=GROUP_ADMIN_ID,
                reply_to_message=_bot_reply_payload(bot.replies(GROUP_PIN_CHAT)[-1]),
            )
        )

        # Dokładnie jeden zapis i dokładnie pod ujemnym chat_id grupy,
        # nigdy pod prywatnym ID administratora.
        assert set_profile_calls == [GROUP_PIN_CHAT]
        assert bot.users.record(GROUP_ADMIN_ID) is None
        group_rows = [
            r for r in bot.gc.users.grid[1:]
            if r and r[0] == str(GROUP_PIN_CHAT)
        ]
        assert len(group_rows) == 1

        # Zwykła wiadomość tekstowa po zapisie pinezką jest ignorowana (PENDING_CITY zdjęte),
        # a wpisanie miasta po kolejnym /miasto nadal działa jak wcześniej.
        before = len(bot.sent)
        bot.run(bot.msg(GROUP_PIN_CHAT, "zwykła rozmowa na czacie", user_id=GROUP_ADMIN_ID))
        assert len(bot.sent) == before

        bot.run(bot.msg(GROUP_PIN_CHAT, "/miasto", user_id=GROUP_ADMIN_ID))
        bot.run(bot.msg(GROUP_PIN_CHAT, "Warszawa", user_id=GROUP_ADMIN_ID))
        assert bot.users.record(GROUP_PIN_CHAT)["location_label"] == "Warszawa"
        assert set_profile_calls == [GROUP_PIN_CHAT, GROUP_PIN_CHAT]

    # --- F. Język i etykiety -------------------------------------------------
    def test_f_group_pin_uses_group_effective_lang_and_safe_resolver_without_poi_or_postcode(
        self, bot, monkeypatch
    ):
        # Prawdziwy resolver + strukturalny adres z POI, ulicą, numerem domu i kodem pocztowym.
        reverse_calls = []

        class _SafeLoc:
            raw = {
                "address": {
                    "city": "Wiązowna",
                    "municipality": "Wiązowna",
                    "county": "otwocki",
                    "state": "mazowieckie",
                    "postcode": "05-462",
                    "country": "Polen",
                    "country_code": "pl",
                    "road": "Kościelna",
                    "house_number": "41",
                    "suburb": "Osiedle Parkowe",
                    "amenity": "Biblioteka publiczna w Wiązownie",
                }
            }
            address = "Biblioteka publiczna, Kościelna 41, 05-462 Wiązowna"

        class _FakeNom:
            def __init__(self, user_agent=None):
                pass

            def reverse(self, query, language=None):
                reverse_calls.append((query, language))
                return _SafeLoc()

        lb._GEO_DETAILS_CACHE.clear()
        monkeypatch.setattr(lb, "Nominatim", _FakeNom)
        monkeypatch.setattr(
            lb, "get_location_details_from_coords",
            REAL_GET_LOCATION_DETAILS_FROM_COORDS,
        )

        # Grupa zarejestrowana z językiem DE, ale pinezkę wysyła użytkownik z Telegramem PL.
        bot.run(bot.msg(GROUP_PIN_CHAT, "/start BETAX1", lang="de", user_id=GROUP_ADMIN_ID))
        assert bot.users.record(GROUP_PIN_CHAT)["lang"] == "de"

        bot.run(
            bot.pin(
                GROUP_PIN_CHAT,
                lat=52.15,
                lon=21.29,
                lang="pl",
                user_id=GROUP_ADMIN_ID,
                reply_to_message=_bot_reply_payload("Standort ändern"),
            )
        )

        # Resolver wywołany w języku grupy ("de"), a nie prywatnym języku nadawcy ("pl").
        assert reverse_calls == [("52.15, 21.29", "de")]
        rec = bot.users.record(GROUP_PIN_CHAT)
        assert rec["profile_status"] == "active"
        assert rec["lang"] == "de"
        assert rec["location_label"] == "Wiązowna"

        confirmation = bot.replies(GROUP_PIN_CHAT)[-1]
        assert "✅ *Standort gespeichert:*" in confirmation
        assert "Wiązowna" in confirmation
        # Etykieta nie może zawierać surowego POI, ulicy, numeru domu, osiedla ani kodu pocztowego.
        for forbidden in (
            "Biblioteka publiczna",
            "Kościelna",
            "41",
            "Osiedle Parkowe",
            "05-462",
        ):
            assert forbidden not in confirmation
            assert forbidden not in rec["location_label"]


# ============================================================================
# SUBTASK A & B: ROTACJA KODÓW PUBLICZNYCH I MACIERZ AUTORYZACJI P7
# ============================================================================
class TestPublicBetaCodesDynamicRotation:
    """Testy rotacji i obsługi zmiennej środowiskowej PUBLIC_BETA_CODES (Subtask B)."""

    def test_start_without_code_returns_current_public_code(self, bot):
        bot.run(bot.msg(101, "/start"))
        replies = bot.replies(101)
        assert len(replies) == 1
        assert "/start BETAX1" in replies[0]
        assert "bot prywatny" in replies[0].lower()
        # Komendy klikalne => brak backticków
        assert "`/start" not in replies[0]

    def test_code_rotation_changes_no_access_message(self, bot, monkeypatch):
        monkeypatch.setenv("PUBLIC_BETA_CODES", "BETA2026B")
        bot.run(bot.msg(102, "/start"))
        replies = bot.replies(102)
        assert len(replies) == 1
        assert "/start BETA2026B" in replies[0]
        assert "BETAX1" not in replies[0]

    def test_multiple_codes_selects_first_deterministically(self, bot, monkeypatch):
        monkeypatch.setenv("PUBLIC_BETA_CODES", "ALPHA1, BETA2, GAMMA3")
        # /start bez kodu zwraca pierwszy aktywny kod: ALPHA1
        bot.run(bot.msg(103, "/start"))
        replies = bot.replies(103)
        assert "/start ALPHA1" in replies[0]
        assert "BETA2" not in replies[0]

        # Ale drugi kod BETA2 nadal pozwala na autoryzację!
        bot.run(bot.msg(104, "/start BETA2"))
        rec = bot.users.record(104)
        assert rec is not None
        assert rec["access_status"] == "granted"
        assert bot.has_reply(104, "Dostęp aktywny")

    def test_empty_public_beta_codes_safe_no_code_variant(self, bot, monkeypatch):
        monkeypatch.setenv("PUBLIC_BETA_CODES", "")
        monkeypatch.delenv("PUBLIC_BETA_CODE", raising=False)
        bot.run(bot.msg(105, "/start"))
        replies = bot.replies(105)
        assert len(replies) == 1
        # Fallbackowy komunikat bez artefaktu {public_code}
        assert "{public_code}" not in replies[0]
        assert (
            "kod dostępu" in replies[0].lower()
            or "zaproszenie" in replies[0].lower()
            or "dostęp" in replies[0].lower()
        )
        assert bot.users.record(105) is None

    def test_removed_old_code_gives_invalid_link(self, bot, monkeypatch):
        monkeypatch.setenv("PUBLIC_BETA_CODES", "NEWCODE2026")
        # Użycie starego kodu, którego już nie ma na liście
        bot.run(bot.msg(106, "/start BETAX1"))
        assert bot.has_reply(106, "Nieprawidłowy")
        assert bot.users.record(106) is None

    @pytest.mark.parametrize("lang,expected_snippet", [
        ("pl", "Skopiuj i wyślij poniższą komendę na czacie:"),
        ("en", "Copy and send the command below in the chat:"),
        ("de", "Kopiere den folgenden Befehl und sende ihn im Chat:"),
        ("fr", "Copiez la commande ci-dessous et envoyez-la dans le chat :"),
        ("es", "Copia y envía el siguiente comando en el chat:"),
        ("no", "Kopier kommandoen nedenfor og send den i chatten:"),
    ])
    def test_no_access_all_supported_languages(self, bot, monkeypatch, lang, expected_snippet):
        monkeypatch.setenv("PUBLIC_BETA_CODES", "GLOBALBETA")
        cid = 1000 + (abs(hash(lang)) % 1000)
        bot.run(bot.msg(cid, "/start", lang=lang))
        replies = bot.replies(cid)
        assert len(replies) == 1
        assert expected_snippet in replies[0]
        assert "/start GLOBALBETA" in replies[0]
        assert "`/start" not in replies[0]

    @pytest.mark.parametrize("lang,expected_snippet", [
        ("pl", "Jeśli otrzymasz kod lub link z zaproszeniem od znajomego, użyj go, aby odblokować dostęp."),
        ("en", "If you receive a code or invite link from a friend, use it to unlock access."),
        ("de", "Wenn du einen Code oder Einladungslink von einem Freund bekommst, nutze ihn, um den Zugang freizuschalten."),
        ("fr", "Si un ami vous transmet un code ou un lien d'invitation, utilisez-le pour déverrouiller votre accès."),
        ("es", "Si recibes de un amigo un código o enlace de invitación, úsalo para desbloquear el acceso."),
        ("no", "Hvis du får en kode eller invitasjonslenke fra en venn, bruk den for å låse opp tilgangen."),
    ])
    def test_no_access_no_code_all_supported_languages(self, bot, monkeypatch, lang, expected_snippet):
        monkeypatch.setenv("PUBLIC_BETA_CODES", "")
        monkeypatch.delenv("PUBLIC_BETA_CODE", raising=False)
        cid = 2000 + (abs(hash(lang)) % 1000)
        bot.run(bot.msg(cid, "/start", lang=lang))
        replies = bot.replies(cid)
        assert len(replies) == 1
        assert expected_snippet in replies[0]
        assert "{public_code}" not in replies[0]


class TestP7AuthorizationMatrix:
    """Kompleksowa macierz autoryzacji i silent guard P7 (Subtask A)."""

    def test_active_user_start_without_payload_gets_welcome_back_without_data_change(self, bot):
        # 1. Zarejestruj usera z lokalizacją
        bot.run(bot.msg(301, "/start BETAX1"))
        bot.run(bot.msg(301, "/miasto"))
        bot.run(bot.msg(301, "Kraków"))
        rec_before = bot.users.record(301)
        assert rec_before["access_status"] == "granted"
        assert rec_before["location_label"] == "Kraków"
        assert rec_before["profile_status"] == "active"
        rows_count_before = len(bot.gc.users.grid)

        # 2. Wyślij /start bez payloadu
        bot.run(bot.msg(301, "/start"))
        rec_after = bot.users.record(301)
        # Profil, lokalizacja, consents nienaruszone
        assert rec_after == rec_before
        assert len(bot.gc.users.grid) == rows_count_before
        assert bot.has_reply(301, "Witaj")

    def test_active_user_start_with_invalid_payload_keeps_access_and_gets_welcome_back(self, bot):
        bot.run(bot.msg(302, "/start BETAX1"))
        rec_before = bot.users.record(302)

        # Aktywny user odpala /start z losowym payloadem
        bot.run(bot.msg(302, "/start RANDOM_INVALID_CODE"))
        rec_after = bot.users.record(302)
        assert rec_after["access_status"] == "granted"
        assert bot.has_reply(302, "Witaj")
        assert not bot.has_reply(302, "Nieprawidłowy")

    def test_active_user_re_start_with_valid_code_does_not_create_duplicate(self, bot):
        bot.run(bot.msg(303, "/start BETAX1"))
        rows_1 = len([r for r in bot.gc.users.grid if r and r[0] == "303"])
        assert rows_1 == 1

        bot.run(bot.msg(303, "/start BETAX1"))
        rows_2 = len([r for r in bot.gc.users.grid if r and r[0] == "303"])
        assert rows_2 == 1

    def test_stranger_start_with_valid_code_activates_access(self, bot):
        bot.run(bot.msg(304, "/start BETAX1"))
        rec = bot.users.record(304)
        assert rec is not None
        assert rec["access_status"] == "granted"
        assert rec["access_source"] == "public_beta"
        assert rec["profile_status"] == "none"
        assert bot.has_reply(304, "Dostęp aktywny")

    def test_stranger_start_with_bad_code_sends_invalid_link_and_no_record(self, bot):
        bot.run(bot.msg(305, "/start TOTALLY_BOGUS"))
        assert bot.has_reply(305, "Nieprawidłowy")
        assert bot.users.record(305) is None

    def test_stranger_start_at_bot_syntax(self, bot):
        # /start@PogodaWorld_bot bez kodu -> no_access
        bot.run(bot.msg(306, f"/start@{lb.BOT_USERNAME}"))
        assert bot.has_reply(306, "bot prywatny")
        assert bot.users.record(306) is None

        # /start@PogodaWorld_bot z poprawnym kodem -> access_granted
        bot.run(bot.msg(307, f"/start@{lb.BOT_USERNAME} BETAX1"))
        assert bot.has_reply(307, "Dostęp aktywny")
        assert bot.users.record(307) is not None

        # /start@PogodaWorld_bot ze złym kodem -> invalid_link
        bot.run(bot.msg(308, f"/start@{lb.BOT_USERNAME} WRONGCODE"))
        assert bot.has_reply(308, "Nieprawidłowy")
        assert bot.users.record(308) is None

    def test_unauthorized_silent_guard_all_mechanisms(self, bot):
        cid = 400
        # Komendy
        for cmd in (
            "/day", "/now", "/future", "/dzien", "/teraz", "/trend",
            "/miasto", "/raport", "/dane", "/priv", "/bezGPS", "/usunDane",
            "/privacy", "/my_data", "/delete_me", "/menu", "/zapros", "/info",
        ):
            bot.run(bot.msg(cid, cmd))

        # Skróty i zapytania
        for query in (".d", ".n", ".f", "?d", "?12", "?14", ".d Hel", "?d Kraków"):
            bot.run(bot.msg(cid, query))

        # Wzmianki i plain text
        bot.run(bot.msg(cid, f"@{lb.BOT_USERNAME} jaka pogoda?"))
        bot.run(bot.msg(cid, "Hej bota czy jutro pada?"))

        # Pinezka
        bot.run(bot.pin(cid, lat=52.2, lon=21.0))

        # WebApp data
        bot.run(bot.webapp_location(cid, lat=52.2, lon=21.0))
        bot.run(bot.webapp_settings(cid, morning="08:00", afternoon="18:00"))

        # Callback query
        bot.run(bot.callback_query(cid, data="some_callback"))

        # Weryfikacja: całkowita cisza
        assert bot.replies(cid) == []
        assert bot.cards == []
        assert bot.oneoffs == []
        assert bot.users.record(cid) is None

    def test_group_authorization_isolation_from_sender(self, bot, monkeypatch):
        freeze_local_hour(monkeypatch, 10, 0)
        group_id = -100999
        sender_with_access = 501
        sender_without_access = 502

        # 1. sender_with_access ma dostęp prywatny
        bot.run(bot.msg(sender_with_access, "/start BETAX1"))
        assert bot.users.record(sender_with_access)["access_status"] == "granted"

        # 2. Grupa NIE ma dostępu (nie było /start na grupie)
        # Wiadomość z grupy wysłana przez sendera Z DOSTĘPEM musi być zignorowana!
        bot.run(bot.msg(group_id, "/dzien", user_id=sender_with_access, chat_type="group"))
        bot.run(bot.msg(group_id, ".d Warszawa", user_id=sender_with_access, chat_type="group"))
        bot.run(bot.pin(group_id, user_id=sender_with_access, chat_type="group"))
        assert bot.replies(group_id) == []
        assert bot.users.record(group_id) is None

        # 3. Aktywacja grupy przez /start na czacie grupy
        bot.run(bot.msg(group_id, "/start BETAX1", user_id=sender_without_access, chat_type="group"))
        rec_group = bot.users.record(group_id)
        assert rec_group is not None
        assert rec_group["access_status"] == "granted"
        assert bot.has_reply(group_id, "Dostęp aktywny")

        # 4. Teraz sender BEZ prywatnego dostępu może pytać bota na aktywowanej grupie
        bot.run(bot.msg(group_id, "/dzien Warszawa", user_id=sender_without_access, chat_type="group"))
        assert len(bot.oneoffs) == 1
        assert bot.oneoffs[0][0] == group_id

    def test_existing_user_with_revoked_access_and_active_profile_reactivates_access_and_preserves_all_data(
        self, bot, monkeypatch
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        # 1. Zarejestruj i skonfiguruj profil z językiem "de"
        bot.run(bot.msg(310, "/start BETAX1", lang="de"))
        bot.run(bot.msg(310, "/miasto", lang="de"))
        bot.run(bot.msg(310, "Berlin", lang="de"))
        rec_initial = bot.users.record(310)
        assert rec_initial["profile_status"] == "active"
        assert rec_initial["location_label"] == "Berlin"
        assert rec_initial["access_status"] == "granted"

        # 2. Ręcznie symulujemy utratę dostępu (access_status="revoked")
        for r in bot.gc.users.grid[1:]:
            if r and r[0] == "310":
                status_col_idx = [h.lower() for h in bot.gc.users.grid[0]].index("access_status")
                r[status_col_idx] = "revoked"
                break
        lb._invalidate_users_snapshot()

        # Użytkownik nie ma dostępu — komendy są wyciszone
        bot.run(bot.msg(310, "/teraz", lang="de"))
        assert bot.oneoffs == []

        # 3. Użytkownik wysyła poprawny /start BETAX1 (np. z klienta z lang="pl")
        bot.run(bot.msg(310, "/start BETAX1", lang="pl"))
        rec_reloaded = bot.users.record(310)

        # Weryfikacja: access_status zmieniony na granted, reszta zachowana
        assert rec_reloaded["access_status"] == "granted"
        assert rec_reloaded["profile_status"] == "active"  # NIE none!
        assert rec_reloaded["location_label"] == "Berlin"
        assert rec_reloaded["lang"] == "de"  # język profilu zachowany
        assert rec_reloaded["location_consent_version"] == lb.PRIVACY_VERSION
        assert bot.has_reply(310, "Zugang aktiv") or bot.has_reply(310, "Dostęp aktywny")

        # 4. Profil jest aktywny i działa natychmiast bez ponownego podawania miasta
        bot.run(bot.msg(310, "/teraz", lang="de"))
        assert len(bot.oneoffs) == 1
        assert bot.oneoffs[-1][0] == 310
        assert bot.oneoffs[-1][3] == "Berlin"



# ============================================================================
# REJESTRACJA: REGISTRATION_ENABLED / MAX_USERS (centralna bramka przed
# _validate_access_token i przed upsert_access)
# ============================================================================
def _ref_token(cid):
    """Referral payload = base64url(chat_id) bez paddingu (istniejący format)."""
    import base64
    return base64.urlsafe_b64encode(str(cid).encode()).decode().rstrip("=")


def _seed_users_row(bot, cid, **fields):
    """Dopisuje wiersz do atrapy Users (zakładka Users, nie Formularz)."""
    headers = [h.lower() for h in bot.gc.users.grid[0]]
    row = [""] * len(headers)
    defaults = {
        "chat_id": str(cid),
        "created_at": "2026-09-01 10:00:00",
        "last_seen_at": "2026-09-01 10:00:00",
        "access_status": "granted",
        "access_granted_at": "2026-09-01 10:00:00",
        "access_source": "public_beta",
        "privacy_version_seen": lb.PRIVACY_VERSION,
        "profile_status": "none",
        "lang": "pl",
    }
    defaults.update(fields)
    for key, value in defaults.items():
        row[headers.index(key)] = str(value)
    bot.gc.users.grid.append(row)
    lb._invalidate_users_snapshot()
    return row


def _users_row(bot, cid):
    """Surowy wiersz Users (lista) dla chat_id — do porównań 'bez zmian'."""
    for row in bot.gc.users.grid[1:]:
        if users_store.norm_chat_id(row[0]) == users_store.norm_chat_id(cid):
            return list(row)
    return None


def _set_users_fields(bot, cid, **fields):
    """Ręczna edycja wiersza Users — symuluje ścieżkę administracyjną (arkusz)."""
    headers = [h.lower() for h in bot.gc.users.grid[0]]
    for row in bot.gc.users.grid[1:]:
        if users_store.norm_chat_id(row[0]) == users_store.norm_chat_id(cid):
            for key, value in fields.items():
                row[headers.index(key)] = str(value)
            lb._invalidate_users_snapshot()
            return
    raise AssertionError(f"brak wiersza Users dla {cid}")


def _users_row_count(bot):
    return len(bot.gc.users.grid) - 1


@pytest.fixture
def empty_bot(monkeypatch):
    """Harness bez wierszy Formularz — liczniki miejsc zależą tylko od Users."""
    return BotHarness(monkeypatch, formularz_rows=[])


@pytest.fixture
def upsert_spy(monkeypatch):
    """Rejestruje każde wywołanie users_store.upsert_access (nadal je wykonuje)."""
    calls = []
    real = users_store.upsert_access

    def spy(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(users_store, "upsert_access", spy)
    return calls


LIMIT_PL = "limit miejsc wyczerpany"
REG_CLOSED_PL = "rejestracja wstrzymana"
INVALID_PL = "Nieprawidłowy"


class TestRegistrationGate:
    # --- A. Rejestracja publiczna i referral przy zamkniętej rejestracji ----
    def test_public_beta_closed_creates_no_record(self, bot, monkeypatch, upsert_spy):
        monkeypatch.setenv("REGISTRATION_ENABLED", "false")
        bot.run(bot.msg(100, "/start BETAX1"))
        assert bot.has_reply(100, REG_CLOSED_PL)
        assert bot.users.record(100) is None
        assert upsert_spy == [], "upsert_access nie może być wołane przy zamkniętej rejestracji"

    def test_referral_closed_creates_no_record(self, bot, monkeypatch, upsert_spy):
        # Referrer 100 ma aktywny dostęp zarejestrowany PRZED zamknięciem.
        bot.run(bot.msg(100, "/start BETAX1"))
        assert bot.users.record(100)["access_status"] == "granted"

        monkeypatch.setenv("REGISTRATION_ENABLED", "false")
        upsert_spy.clear()
        rows_before = _users_row_count(bot)

        bot.run(bot.msg(200, f"/start {_ref_token(100)}"))

        assert bot.has_reply(200, REG_CLOSED_PL), "referral przy zamkniętej rejestracji musi dostać limit_reached"
        assert bot.users.record(200) is None, "referral nie może tworzyć rekordu"
        assert _users_row_count(bot) == rows_before
        assert upsert_spy == []

    def test_referral_closed_even_if_public_codes_are_irrelevant(self, bot, monkeypatch, upsert_spy):
        # Referral działa niezależnie od listy PUBLIC_BETA_CODES — bramka też.
        bot.run(bot.msg(100, "/start BETAX1"))
        monkeypatch.setenv("PUBLIC_BETA_CODES", "")
        monkeypatch.setenv("REGISTRATION_ENABLED", "false")
        upsert_spy.clear()
        bot.run(bot.msg(201, f"/start {_ref_token(100)}"))
        assert bot.users.record(201) is None
        assert upsert_spy == []

    # --- B. Istniejący aktywny użytkownik nadal działa -----------------------
    def test_active_user_keeps_working_when_registration_closed(self, bot, monkeypatch, upsert_spy):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(310, "/start BETAX1"))
        monkeypatch.setenv("REGISTRATION_ENABLED", "false")
        monkeypatch.setenv("MAX_USERS", "1")  # limit też pełny — aktywny i tak działa
        upsert_spy.clear()
        row_before = _users_row(bot, 310)

        bot.run(bot.msg(310, "/start"))
        bot.run(bot.msg(310, f"/start {_ref_token(310)}"))
        bot.run(bot.msg(310, "/start BETAX1"))
        bot.run(bot.msg(310, "/dzien Warszawa"))

        assert bot.has_reply(310, "Witaj")
        assert not bot.has_reply(310, LIMIT_PL)
        assert len(bot.oneoffs) == 1, "aktywny użytkownik musi nadal dostawać karty"
        assert _users_row(bot, 310) == row_before, "aktywny rekord nie może się zmienić"
        assert upsert_spy == []

    # --- C. Wszystkie ścieżki nowych rejestracji zablokowane ----------------
    @pytest.mark.parametrize("payload_kind", ["public", "referral", "bot_syntax", "bare"])
    def test_all_new_registration_paths_blocked_when_closed(self, bot, monkeypatch, upsert_spy, payload_kind):
        bot.run(bot.msg(100, "/start BETAX1"))
        monkeypatch.setenv("REGISTRATION_ENABLED", "false")
        upsert_spy.clear()
        cid = 500
        text = {
            "public": "/start BETAX1",
            "referral": f"/start {_ref_token(100)}",
            "bot_syntax": f"/start@{lb.BOT_USERNAME} BETAX1",
            "bare": "/start",
        }[payload_kind]
        bot.run(bot.msg(cid, text))
        assert bot.users.record(cid) is None
        assert upsert_spy == []
        assert bot.has_reply(cid, REG_CLOSED_PL)
        assert not bot.has_reply(cid, "/start BETAX1"), "bez kodu też nie wolno pokazać działającego kodu"

    def test_group_registration_closed_creates_no_group_record(self, bot, monkeypatch, upsert_spy):
        monkeypatch.setenv("REGISTRATION_ENABLED", "false")
        group_id = -100777
        bot.run(bot.msg(group_id, "/start BETAX1", user_id=502, chat_type="group"))
        assert bot.users.record(group_id) is None
        assert bot.has_reply(group_id, REG_CLOSED_PL)
        assert upsert_spy == []

    # --- D. Zamknięcie nie nadpisuje istniejącego (nieaktywnego) rekordu -----
    def test_closed_does_not_touch_existing_inactive_record(self, bot, monkeypatch, upsert_spy):
        _seed_users_row(bot, 320, access_status="revoked", location_label="Hel", lat_round="54.6")
        monkeypatch.setenv("REGISTRATION_ENABLED", "false")
        upsert_spy.clear()
        before = _users_row(bot, 320)

        bot.run(bot.msg(320, "/start BETAX1"))

        assert bot.has_reply(320, REG_CLOSED_PL)
        assert _users_row(bot, 320) == before
        assert upsert_spy == []

    # --- E. Limit miejsc liczony z Users ------------------------------------
    def test_seats_counted_from_users_only_limit_reached(self, empty_bot, monkeypatch, upsert_spy):
        monkeypatch.setenv("MAX_USERS", "2")
        _seed_users_row(empty_bot, 601)
        _seed_users_row(empty_bot, 602)
        upsert_spy.clear()
        empty_bot.run(empty_bot.msg(603, "/start BETAX1"))
        assert empty_bot.has_reply(603, LIMIT_PL)
        assert empty_bot.users.record(603) is None
        assert upsert_spy == []

    def test_last_free_seat_is_granted(self, empty_bot, monkeypatch):
        monkeypatch.setenv("MAX_USERS", "2")
        _seed_users_row(empty_bot, 611)
        empty_bot.run(empty_bot.msg(612, "/start BETAX1"))
        assert empty_bot.users.record(612)["access_status"] == "granted"

    def test_inactive_users_rows_do_not_consume_seats(self, empty_bot, monkeypatch):
        monkeypatch.setenv("MAX_USERS", "2")
        _seed_users_row(empty_bot, 621, access_status="blocked", blocked_at="2026-10-01 10:00:00")
        _seed_users_row(empty_bot, 622, access_status="revoked")
        _seed_users_row(empty_bot, 623, access_expires_at="2020-01-01 00:00:00")
        empty_bot.run(empty_bot.msg(624, "/start BETAX1"))
        assert empty_bot.users.record(624)["access_status"] == "granted"

    def test_formularz_rows_do_not_inflate_the_count(self, monkeypatch):
        # 5 wierszy w Formularz, ale każdy ma nieaktywny rekord w Users —
        # licznik z Formularza (len(clean_users)) zablokowałby nowego usera.
        rows = [
            ["2026-01-01 08:00:00", str(801 + i), f"U{i}", "Gdańsk", "54.35", "18.65", "08:00", "14:00", "pl"]
            for i in range(5)
        ]
        bot = BotHarness(monkeypatch, formularz_rows=rows)
        monkeypatch.setenv("MAX_USERS", "2")
        for i in range(5):
            _seed_users_row(bot, 801 + i, access_status="revoked")
        bot.run(bot.msg(810, "/start BETAX1"))
        assert bot.users.record(810)["access_status"] == "granted"

    def test_legacy_only_active_rows_still_occupy_seats(self, monkeypatch):
        # Użytkownik legacy (Formularz, bez wiersza Users) ma dostęp domniemany
        # (fallback _chat_has_access) — zajmuje miejsce.
        rows = [
            ["2026-01-01 08:00:00", "901", "L1", "Gdańsk", "54.35", "18.65", "08:00", "14:00", "pl"],
            ["2026-01-01 08:00:00", "902", "L2", "Gdańsk", "54.35", "18.65", "08:00", "14:00", "pl"],
        ]
        bot = BotHarness(monkeypatch, formularz_rows=rows)
        monkeypatch.setenv("MAX_USERS", "2")
        bot.run(bot.msg(910, "/start BETAX1"))
        assert bot.users.record(910) is None
        assert bot.has_reply(910, LIMIT_PL)

    def test_default_limit_is_200_like_before(self, empty_bot, monkeypatch):
        monkeypatch.delenv("MAX_USERS", raising=False)
        for i in range(199):
            _seed_users_row(empty_bot, 2000 + i)
        empty_bot.run(empty_bot.msg(3000, "/start BETAX1"))
        assert empty_bot.users.record(3000)["access_status"] == "granted"
        _seed_users_row(empty_bot, 3001)
        empty_bot.run(empty_bot.msg(3002, "/start BETAX1"))
        assert empty_bot.has_reply(3002, LIMIT_PL)
        assert empty_bot.users.record(3002) is None

    # --- F. MASTER_TOKEN ----------------------------------------------------
    def test_master_token_bypasses_closed_registration_and_limit(self, empty_bot, monkeypatch):
        monkeypatch.setattr(lb, "MASTER_TOKEN", "MASTERTEST")
        monkeypatch.setenv("REGISTRATION_ENABLED", "false")
        monkeypatch.setenv("MAX_USERS", "1")
        _seed_users_row(empty_bot, 700_1)  # miejsce zajęte
        empty_bot.run(empty_bot.msg(701, "/start MASTERTEST"))
        rec = empty_bot.users.record(701)
        assert rec is not None and rec["access_status"] == "granted"
        assert rec["access_source"] == "admin"

    def test_master_token_does_not_reactivate_blocked_user(self, empty_bot, monkeypatch, upsert_spy):
        monkeypatch.setattr(lb, "MASTER_TOKEN", "MASTERTEST")
        _seed_users_row(empty_bot, 702, access_status="blocked", blocked_at="2026-10-01 10:00:00")
        upsert_spy.clear()
        before = _users_row(empty_bot, 702)
        empty_bot.run(empty_bot.msg(702, "/start MASTERTEST"))
        assert _users_row(empty_bot, 702) == before
        assert upsert_spy == []

    # --- G. Blokada: payload nie reaktywuje konta zablokowanego -------------
    @pytest.mark.parametrize("blocked_fields", [
        {"access_status": "blocked", "blocked_at": "2026-10-01 10:00:00", "blocked_reason": "telegram_blocked"},
        {"access_status": "granted", "blocked_at": "2026-10-01 10:00:00"},
    ])
    def test_blocked_user_cannot_reactivate_with_public_code(self, empty_bot, upsert_spy, blocked_fields):
        _seed_users_row(empty_bot, 400, **blocked_fields)
        upsert_spy.clear()
        before = _users_row(empty_bot, 400)
        empty_bot.run(empty_bot.msg(400, "/start BETAX1"))
        assert _users_row(empty_bot, 400) == before
        assert upsert_spy == []
        assert empty_bot.has_reply(400, INVALID_PL)

    def test_blocked_user_cannot_reactivate_with_referral(self, empty_bot, upsert_spy):
        _seed_users_row(empty_bot, 100)  # aktywny referrer
        _seed_users_row(empty_bot, 410, access_status="blocked", blocked_at="2026-10-01 10:00:00")
        upsert_spy.clear()
        before = _users_row(empty_bot, 410)
        empty_bot.run(empty_bot.msg(410, f"/start {_ref_token(100)}"))
        assert _users_row(empty_bot, 410) == before
        assert upsert_spy == []

    def test_blocked_user_cannot_reactivate_even_when_registration_open(self, empty_bot, upsert_spy):
        _seed_users_row(empty_bot, 420, access_status="blocked", blocked_at="2026-10-01 10:00:00")
        upsert_spy.clear()
        empty_bot.run(empty_bot.msg(420, "/start BETAX1"))
        assert empty_bot.users.record(420)["access_status"] == "blocked"
        assert upsert_spy == []

    @pytest.mark.parametrize("unblocked_status", ["granted", "revoked"])
    def test_admin_unblock_then_valid_code_works_again(self, empty_bot, upsert_spy, unblocked_status):
        _seed_users_row(empty_bot, 430, access_status="blocked", blocked_at="2026-10-01 10:00:00",
                        blocked_reason="telegram_blocked", profile_status="none")
        empty_bot.run(empty_bot.msg(430, "/start BETAX1"))
        assert empty_bot.users.record(430)["access_status"] == "blocked"
        assert upsert_spy == []

        # Ścieżka administracyjna: admin zdejmuje blokadę w arkuszu Users.
        # Wariant "granted": konto aktywne od razu (powitanie powrotne).
        # Wariant "revoked": konto odblokowane, ale nieaktywne — zwykły kod ma
        # ponownie nadać dostęp (ścieżka rejestracji działa).
        _set_users_fields(empty_bot, 430, access_status=unblocked_status, blocked_at="", blocked_reason="")
        empty_bot.run(empty_bot.msg(430, "/start BETAX1"))
        rec = empty_bot.users.record(430)
        assert rec["access_status"] == "granted"
        assert rec["blocked_at"] == ""
        if unblocked_status == "revoked":
            assert empty_bot.has_reply(430, "Dostęp aktywny")
            assert rec["access_source"] == "public_beta"
        else:
            assert empty_bot.has_reply(430, "Witaj")

        # Po odblokowaniu użytkownik działa: /miasto nie dostaje ani limitu, ani odmowy.
        replies_before = len(empty_bot.replies(430))
        empty_bot.run(empty_bot.msg(430, "/miasto"))
        new_replies = empty_bot.replies(430)[replies_before:]
        assert new_replies, "po odblokowaniu bot musi odpowiadać"
        assert not any(LIMIT_PL in t or INVALID_PL in t for t in new_replies)

    # --- H. Silent guard i grupy bez regresji -------------------------------
    def test_silent_guard_unchanged_when_registration_closed(self, bot, monkeypatch, upsert_spy):
        monkeypatch.setenv("REGISTRATION_ENABLED", "false")
        cid = 520
        for cmd in ("/day", "/now", "/dzien", "/dane", "/usunDane", "/privacy", "/menu"):
            bot.run(bot.msg(cid, cmd))
        for query in (".d", ".n", "?d Kraków"):
            bot.run(bot.msg(cid, query))
        bot.run(bot.msg(cid, "Hej bota czy jutro pada?"))
        bot.run(bot.pin(cid, lat=52.2, lon=21.0))
        bot.run(bot.webapp_location(cid, lat=52.2, lon=21.0))
        bot.run(bot.callback_query(cid, data="x"))
        assert bot.replies(cid) == []
        assert bot.cards == [] and bot.oneoffs == []
        assert bot.users.record(cid) is None
        assert upsert_spy == []

    def test_active_group_keeps_working_when_registration_closed(self, bot, monkeypatch, upsert_spy):
        freeze_local_hour(monkeypatch, 10, 0)
        group_id = -100555
        _seed_users_row(bot, group_id, access_source="admin")
        monkeypatch.setenv("REGISTRATION_ENABLED", "false")
        monkeypatch.setenv("MAX_USERS", "1")
        upsert_spy.clear()
        bot.run(bot.msg(group_id, "/dzien Warszawa", user_id=503, chat_type="group"))
        assert len(bot.oneoffs) == 1 and bot.oneoffs[0][0] == group_id
        assert upsert_spy == []

    def test_bare_start_when_closed_shows_registration_closed_not_public_code(self, bot, monkeypatch):
        monkeypatch.setenv("REGISTRATION_ENABLED", "false")
        bot.run(bot.msg(530, "/start"))
        assert bot.has_reply(530, REG_CLOSED_PL)
        assert not bot.has_reply(530, "/start BETAX1")

    # --- I. Konfiguracja -----------------------------------------------------
    @pytest.mark.parametrize("raw,expected", [
        (None, True), ("", True), ("true", True), ("1", True), ("yes", True),
        ("false", False), ("FALSE", False), ("0", False), ("no", False), ("off", False),
        ("garbage", True),
    ])
    def test_registration_enabled_parsing(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("REGISTRATION_ENABLED", raising=False)
        else:
            monkeypatch.setenv("REGISTRATION_ENABLED", raw)
        assert lb._registration_enabled() is expected

    @pytest.mark.parametrize("raw,expected", [
        (None, 200), ("", 200), ("abc", 200), ("-5", 200), ("0", 0), ("1", 1), (" 50 ", 50),
    ])
    def test_max_users_parsing(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("MAX_USERS", raising=False)
        else:
            monkeypatch.setenv("MAX_USERS", raw)
        assert lb._max_users() == expected


# ============================================================================
# POPRAWKI PRZED MERGE: brak domyślnego MASTER_TOKEN, limit_reached z MAX_USERS
# ============================================================================
class TestMasterTokenHardening:
    def test_default_master_token_is_empty_not_dev_test(self):
        import os as _os
        import subprocess
        import sys
        env = {k: v for k, v in _os.environ.items() if k != "MASTER_TOKEN"}
        here = _os.path.dirname(_os.path.abspath(__file__))
        out = subprocess.run(
            [sys.executable, "-c", "import location_bot as lb; print(repr(lb.MASTER_TOKEN))"],
            cwd=here, env=env, capture_output=True, text=True, timeout=180,
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip().splitlines()[-1] == "''"

    def test_unset_master_token_disables_admin_path(self, bot, monkeypatch, upsert_spy):
        monkeypatch.setattr(lb, "MASTER_TOKEN", "")
        assert lb._validate_access_token("DEV_TEST", [], {}) == (False, "")
        assert lb._validate_access_token("", [], {}) == (False, "")
        bot.run(bot.msg(740, "/start DEV_TEST"))
        assert bot.has_reply(740, INVALID_PL)
        assert bot.users.record(740) is None
        assert upsert_spy == []

    def test_whitespace_master_token_counts_as_unset(self, bot, monkeypatch):
        monkeypatch.setattr(lb, "MASTER_TOKEN", "   ")
        assert lb._validate_access_token("   ", [], {}) == (False, "")
        assert lb._validate_access_token("DEV_TEST", [], {}) == (False, "")

    def test_unset_master_does_not_bypass_closed_registration(self, bot, monkeypatch, upsert_spy):
        monkeypatch.setattr(lb, "MASTER_TOKEN", "")
        monkeypatch.setenv("REGISTRATION_ENABLED", "false")
        bot.run(bot.msg(741, "/start DEV_TEST"))
        assert bot.has_reply(741, REG_CLOSED_PL)
        assert bot.users.record(741) is None
        assert upsert_spy == []

    def test_configured_master_still_bypasses_gate_and_limit(self, empty_bot, monkeypatch):
        monkeypatch.setattr(lb, "MASTER_TOKEN", "OwnSecretToken-42")
        monkeypatch.setenv("REGISTRATION_ENABLED", "0")
        monkeypatch.setenv("MAX_USERS", "1")
        _seed_users_row(empty_bot, 742)
        empty_bot.run(empty_bot.msg(743, "/start OwnSecretToken-42"))
        rec = empty_bot.users.record(743)
        assert rec is not None and rec["access_source"] == "admin"

    def test_configured_master_does_not_unblock_blocked_account(self, empty_bot, monkeypatch, upsert_spy):
        monkeypatch.setattr(lb, "MASTER_TOKEN", "OwnSecretToken-42")
        _seed_users_row(empty_bot, 744, access_status="blocked", blocked_at="2026-10-01 10:00:00")
        upsert_spy.clear()
        before = _users_row(empty_bot, 744)
        empty_bot.run(empty_bot.msg(744, "/start OwnSecretToken-42"))
        assert _users_row(empty_bot, 744) == before
        assert upsert_spy == []


class TestLimitReachedMessage:
    """D1: komunikat limit_reached NIE zawiera liczby miejsc ani linku.
    Sama logika MAX_USERS działa bez zmian — zmienił się tylko tekst."""

    @pytest.mark.parametrize("lang", ["pl", "en", "de", "es", "fr", "no"])
    def test_limit_reached_has_no_seat_count(self, monkeypatch, lang):
        monkeypatch.setenv("MAX_USERS", "7")
        text = lb._invite_message(lang, "limit_reached")
        assert not re.search(r"\d+\s*/\s*\d+", text), (lang, text)
        assert "{max_users}" not in text and "{url}" not in text, (lang, text)
        assert "https://" not in text, (lang, text)
        assert "{" not in text and "}" not in text, (lang, text)

    @pytest.mark.parametrize("lang", ["pl", "en", "de", "es", "fr", "no"])
    def test_limit_reached_text_is_independent_of_max_users(self, monkeypatch, lang):
        """Tekst nie zależy od MAX_USERS (brak liczby miejsc)."""
        monkeypatch.setenv("MAX_USERS", "7")
        text_a = lb._invite_message(lang, "limit_reached")
        monkeypatch.setenv("MAX_USERS", "500")
        text_b = lb._invite_message(lang, "limit_reached")
        assert text_a == text_b, (lang, text_a, text_b)

    def test_limit_reached_still_blocks_registration_on_full_slots(self, empty_bot, monkeypatch):
        """MAX_USERS nadal blokuje rejestrację — komunikat to tylko limit_reached."""
        monkeypatch.setenv("MAX_USERS", "1")
        _seed_users_row(empty_bot, 750)
        empty_bot.run(empty_bot.msg(751, "/start BETAX1"))
        assert empty_bot.has_reply(751, LIMIT_PL)
        assert empty_bot.users.record(751) is None, "pełny limit nie tworzy rekordu"
        assert not empty_bot.has_reply(751, INVALID_PL)


class TestClosedRegistrationDecisions:
    def test_closed_invalid_code_gets_registration_closed_not_invalid_link(self, bot, monkeypatch, upsert_spy):
        monkeypatch.setenv("REGISTRATION_ENABLED", "false")
        bot.run(bot.msg(760, "/start TOTALLY_BOGUS"))
        assert bot.has_reply(760, REG_CLOSED_PL)
        assert not bot.has_reply(760, INVALID_PL)
        assert bot.users.record(760) is None
        assert upsert_spy == []

    @pytest.mark.parametrize("raw", ["0", "false", "off", "no"])
    def test_bare_start_closed_returns_registration_closed_without_code(self, bot, monkeypatch, raw):
        monkeypatch.setenv("REGISTRATION_ENABLED", raw)
        bot.run(bot.msg(761, "/start"))
        replies = bot.replies(761)
        assert len(replies) == 1
        assert REG_CLOSED_PL in replies[0]
        assert "/start BETAX1" not in replies[0]
        assert bot.users.record(761) is None

    def test_bare_start_open_returns_no_access_with_code(self, bot, monkeypatch):
        monkeypatch.setenv("REGISTRATION_ENABLED", "1")
        bot.run(bot.msg(762, "/start"))
        replies = bot.replies(762)
        assert len(replies) == 1
        assert "/start BETAX1" in replies[0]
        assert LIMIT_PL not in replies[0]

    def test_legacy_row_also_in_users_is_counted_once(self, monkeypatch):
        rows = [["2026-01-01 08:00:00", "901", "L1", "Gdańsk", "54.35", "18.65", "08:00", "14:00", "pl"]]
        bot = BotHarness(monkeypatch, formularz_rows=rows)
        monkeypatch.setenv("MAX_USERS", "2")
        _seed_users_row(bot, 901)  # ten sam człowiek: Formularz + aktywny wiersz Users
        bot.run(bot.msg(902, "/start BETAX1"))
        assert bot.users.record(902) is not None, "wiersz obecny w Users nie może liczyć się podwójnie"
        assert bot.users.record(902)["access_status"] == "granted"


# ============================================================================
# registration_closed (ręcznie zamknięta rejestracja) vs limit_reached (pełny limit)
# ============================================================================
class TestRegistrationClosedMessage:
    @pytest.mark.parametrize("lang", ["pl", "en", "de", "es", "fr", "no"])
    def test_registration_closed_text_has_no_count_and_no_code(self, monkeypatch, lang):
        monkeypatch.setenv("PUBLIC_BETA_CODES", "BETAX1")
        monkeypatch.setenv("MAX_USERS", "200")
        text = lb._invite_message(lang, "registration_closed")
        assert "https://" not in text, (lang, text)
        assert "BETAX1" not in text, (lang, text)
        assert not re.search(r"\d+\s*/\s*\d+", text), (lang, text)
        assert "{" not in text and "}" not in text, (lang, text)
        assert "50/50" not in text and "200/200" not in text

    @pytest.mark.parametrize("lang", ["pl", "en", "de", "es", "fr", "no"])
    def test_registration_closed_is_dynamic_translation_not_count_dependent(self, monkeypatch, lang):
        monkeypatch.setenv("MAX_USERS", "7")
        text_a = lb._invite_message(lang, "registration_closed")
        monkeypatch.setenv("MAX_USERS", "500")
        text_b = lb._invite_message(lang, "registration_closed")
        assert text_a == text_b, "registration_closed nie może zależeć od MAX_USERS"
        assert text_a == lb.t_ui(lang, "registration_closed", url=lb._md_safe(lb.INVITE_URL))

    def test_registration_closed_translations_are_distinct(self):
        texts = {lang: lb.t_ui(lang, "registration_closed", url="U") for lang in ("pl", "en", "de", "es", "fr", "no")}
        assert len(set(texts.values())) == 6, texts

    def test_block_key_mapping(self):
        assert lb._registration_block_key("closed") == "registration_closed"
        assert lb._registration_block_key("full") == "limit_reached"

    def test_closed_wins_over_full(self, empty_bot, monkeypatch, upsert_spy):
        monkeypatch.setenv("REGISTRATION_ENABLED", "0")
        monkeypatch.setenv("MAX_USERS", "1")
        _seed_users_row(empty_bot, 800)  # miejsca też są pełne
        upsert_spy.clear()
        empty_bot.run(empty_bot.msg(801, "/start BETAX1"))
        assert empty_bot.has_reply(801, REG_CLOSED_PL)
        assert not empty_bot.has_reply(801, LIMIT_PL)
        assert upsert_spy == []

    def test_closed_with_free_seats_shows_registration_closed(self, empty_bot, monkeypatch, upsert_spy):
        monkeypatch.setenv("REGISTRATION_ENABLED", "0")
        monkeypatch.setenv("MAX_USERS", "200")  # miejsca wolne
        upsert_spy.clear()
        empty_bot.run(empty_bot.msg(802, "/start BETAX1"))
        assert empty_bot.has_reply(802, REG_CLOSED_PL)
        assert not empty_bot.has_reply(802, LIMIT_PL)
        assert empty_bot.users.record(802) is None
        assert upsert_spy == []

    def test_full_limit_with_registration_enabled_shows_limit_reached(self, empty_bot, monkeypatch, upsert_spy):
        monkeypatch.setenv("REGISTRATION_ENABLED", "1")
        monkeypatch.setenv("MAX_USERS", "2")
        _seed_users_row(empty_bot, 810)
        _seed_users_row(empty_bot, 811)
        upsert_spy.clear()
        empty_bot.run(empty_bot.msg(812, "/start BETAX1"))
        assert empty_bot.has_reply(812, LIMIT_PL)
        # D1: komunikat limit_reached nie pokazuje liczby miejsc
        assert not any(re.search(r"\d+\s*/\s*\d+", t) for t in empty_bot.replies(812))
        assert not empty_bot.has_reply(812, REG_CLOSED_PL)
        assert empty_bot.users.record(812) is None
        assert upsert_spy == []

    def test_open_with_free_seat_uses_normal_payload_validation(self, empty_bot, monkeypatch):
        monkeypatch.setenv("REGISTRATION_ENABLED", "1")
        monkeypatch.setenv("MAX_USERS", "5")
        empty_bot.run(empty_bot.msg(820, "/start BETAX1"))
        assert empty_bot.users.record(820)["access_status"] == "granted"
        empty_bot.run(empty_bot.msg(821, "/start WRONGCODE"))
        assert empty_bot.has_reply(821, INVALID_PL)
        assert not empty_bot.has_reply(821, REG_CLOSED_PL)

    @pytest.mark.parametrize("payload_kind", ["bare", "public", "referral", "invalid"])
    def test_closed_all_non_master_paths_get_registration_closed(self, empty_bot, monkeypatch, upsert_spy, payload_kind):
        _seed_users_row(empty_bot, 100)  # aktywny referrer
        monkeypatch.setenv("REGISTRATION_ENABLED", "false")
        upsert_spy.clear()
        cid = 830
        text = {
            "bare": "/start",
            "public": "/start BETAX1",
            "referral": f"/start {_ref_token(100)}",
            "invalid": "/start NIEPOPRAWNY",
        }[payload_kind]
        empty_bot.run(empty_bot.msg(cid, text))
        replies = empty_bot.replies(cid)
        assert len(replies) == 1, replies
        assert REG_CLOSED_PL in replies[0]
        assert "/start BETAX1" not in replies[0]
        assert empty_bot.users.record(cid) is None
        assert upsert_spy == []

    def test_closed_blocked_account_keeps_invalid_link_and_record(self, empty_bot, monkeypatch, upsert_spy):
        monkeypatch.setenv("REGISTRATION_ENABLED", "0")
        _seed_users_row(empty_bot, 840, access_status="blocked", blocked_at="2026-10-01 10:00:00")
        upsert_spy.clear()
        before = _users_row(empty_bot, 840)
        empty_bot.run(empty_bot.msg(840, "/start BETAX1"))
        assert _users_row(empty_bot, 840) == before
        assert upsert_spy == []
        assert empty_bot.has_reply(840, INVALID_PL)

    @pytest.mark.parametrize("lang", ["pl", "en", "de", "es", "fr", "no"])
    def test_closed_reply_is_localized_per_user_language(self, empty_bot, monkeypatch, lang):
        monkeypatch.setenv("REGISTRATION_ENABLED", "0")
        cid = 850 + ["pl", "en", "de", "es", "fr", "no"].index(lang)
        empty_bot.run(empty_bot.msg(cid, "/start BETAX1", lang=lang))
        assert empty_bot.replies(cid) == [lb._invite_message(lang, "registration_closed")], lang
