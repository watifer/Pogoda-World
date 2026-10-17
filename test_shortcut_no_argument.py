"""test_shortcut_no_argument.py — skróty ?d / ?12 / ?14 BEZ argumentu.

Zgłoszony błąd: `?d` (oraz `?12` i `?14`) wysyłał prompt o miasto, ale
odpowiedź „Warszawa” nie była przechwytywana — `guest_bot_handler` nie
ustawiał `PENDING_CITY`, więc gałąź 8B nie miała czego obsłużyć, a użytkownik
z zapisanym profilem i tak dostawał prompt zamiast karty.

Naprawa (location_bot, blok 0A, PO globalnym guardzie P7): skrót bez argumentu
w czacie prywatnym jest przekierowywany na istniejącą komendę:
    ?d  -> /day     ?12 -> /now     ?14 -> /future
Dzięki temu działa JEDNA, sprawdzona ścieżka:
    - zapisany profil Users -> prep_* -> karta, bez search_loc i bez pending,
    - brak profilu -> PENDING_CITY + oneoff_prompt_* -> miasto -> search_loc
      -> geokoder -> karta jednorazowa,
    - legacy-only -> dotychczasowe /day, /now, /future,
    - grupa -> bez zmian (cisza dla skrótu bez argumentu).

UWAGA (dlaczego ten plik nie używa atrapy guest handlera): `BotHarness`
podmienia `location_bot.handle_guest_now` na atrapę, która zwraca True bez
wysyłania promptu i bez ustawiania stanu — właśnie dlatego błąd nie był
widoczny w testach. Tutaj atrapa jest NATYCHMIAST zastępowana cienkim spy,
który woła PRAWDZIWY `guest_bot_handler.handle_guest_now` z tymi samymi
argumentami, jakie dostaje produkcja.

Uruchomienie: pytest test_shortcut_no_argument.py -v
"""

import time

import pytest

import guest_bot_handler as gbh
import i18n
import location_bot as lb
from test_access_gate import (
    LEGACY_ROW, BotHarness, freeze_local_hour,
)

PRIVATE = 4300
NO_PROFILE = 4301
STRANGER = 4302
GROUP = -100430
GROUP_ADMIN = 930
LEGACY_WITH_LOC = 700           # LEGACY_ROW: Gdańsk 54.35/18.65

LEGACY_NO_LOC = 704
LEGACY_NO_LOC_ROW = ["2026-01-01 08:00:00", str(LEGACY_NO_LOC), "NoLocLegacy", "",
                     "", "", "08:00", "14:00", "pl"]

# (skrót, typ karty, kontekst pending, klucz promptu, klucz komunikatu oczekiwania)
SHORTCUT_CASES = [
    pytest.param("?d", "day", lb.CTX_ONEOFF_DAY, "oneoff_prompt_day", "prep_main", id="day"),
    pytest.param("?12", "now", lb.CTX_ONEOFF_NOW, "oneoff_prompt_now", "scanning", id="now"),
    pytest.param("?14", "future", lb.CTX_ONEOFF_FUTURE, "oneoff_prompt_future", "prep_future", id="future"),
]

# Istniejące aliasy parsera o tym samym typie karty — bez dodawania nowych.
ALIAS_CASES = [
    pytest.param(alias, card, ctx, prompt_key, id=alias)
    for card, ctx, prompt_key, aliases in (
        ("day", lb.CTX_ONEOFF_DAY, "oneoff_prompt_day", (".d", "?t", "?j", ".t", ".j")),
        ("now", lb.CTX_ONEOFF_NOW, "oneoff_prompt_now", (".12", "?n", ".n", "?p", ".p")),
        ("future", lb.CTX_ONEOFF_FUTURE, "oneoff_prompt_future", (".14", "?f", ".f")),
    )
    for alias in aliases
]

WAIT_TEXTS = {lb.t_ui("pl", key) for key in lb.SAVED_PROFILE_WAIT_KEYS.values()}
SEARCH_LOC = lb.t_ui("pl", "search_loc")
GUEST_PROMPT = lb.t_ui("pl", "guest_need_city")


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def bot(monkeypatch):
    """Harness z PRAWDZIWYM guest_bot_handler pod cienkim spy."""
    harness = BotHarness(monkeypatch,
                         formularz_rows=[LEGACY_ROW, LEGACY_NO_LOC_ROW])
    real_calls = []

    def real_guest_handler(**kwargs):
        real_calls.append(kwargs)
        return gbh.handle_guest_now(**kwargs)

    monkeypatch.setattr(lb, "handle_guest_now", real_guest_handler)
    harness.real_guest_calls = real_calls
    return harness


@pytest.fixture
def with_profile(bot):
    bot.run(bot.msg(PRIVATE, "/start BETAX1"))
    bot.run(bot.msg(PRIVATE, "/miasto"))
    bot.run(bot.msg(PRIVATE, "Warszawa"))
    assert bot.users.record(PRIVATE)["profile_status"] == "active"
    return len(bot.sent)


@pytest.fixture
def geocoder_log(bot, monkeypatch):
    """Rejestruje miasta wysłane do geokodera i stan czatu PRZED zapytaniem."""
    calls = []

    def fake_geocode(city, lang="pl"):
        calls.append({"city": city, "sent_before": list(bot.sent), "lang": lang})
        return (lb.GEOCODE_OK, 41.9028, 12.4964, city, f"{city}, Włochy")

    monkeypatch.setattr(lb, "geocode_city_accepted", fake_geocode)
    return calls


class _GuestCardRecorder:
    """Atrapy generowania karty trybu gościa (bez sieci i bez renderu)."""

    def __init__(self):
        self.layouts = []
        self.photos = []


@pytest.fixture
def guest_card(monkeypatch):
    rec = _GuestCardRecorder()

    monkeypatch.setattr(
        lb, "build_payload_for_location",
        lambda **kw: {"location": {"tz": "UTC"}, "request": kw},
    )
    monkeypatch.setattr(lb, "prepare_layout_data", lambda payload: rec.layouts.append("day") or {})
    monkeypatch.setattr(lb, "prepare_now_layout_data", lambda payload: rec.layouts.append("now") or {})
    monkeypatch.setattr(lb, "prepare_future_layout_data", lambda payload: rec.layouts.append("future") or {})
    monkeypatch.setattr(lb.image_generator, "generate_weather_card", lambda layout: "/tmp/karta.png")
    monkeypatch.setattr(
        lb, "send_photo",
        lambda chat_id, path, caption=None, parse_mode=None, card_caption=True:
            rec.photos.append((chat_id, caption)),
    )
    return rec


# ============================================================================
# 1. ?d / ?12 / ?14 BEZ ARGUMENTU — UŻYTKOWNIK Z ZAPISANYM PROFILEM
# ============================================================================

class TestSavedProfile:

    @pytest.mark.parametrize("shortcut,card,ctx,prompt_key,wait_key", SHORTCUT_CASES)
    def test_card_with_wait_message_without_search_loc(
        self, bot, monkeypatch, with_profile, shortcut, card, ctx, prompt_key, wait_key
    ):
        freeze_local_hour(monkeypatch, 10, 0)

        bot.run(bot.msg(PRIVATE, shortcut))

        replies = bot.replies_since(PRIVATE, with_profile)
        assert replies == [lb.t_ui("pl", wait_key)], replies
        assert SEARCH_LOC not in replies
        assert GUEST_PROMPT not in replies
        assert [o[5] for o in bot.oneoffs] == [card]
        assert lb.SHORTCUT_NO_ARG_COMMANDS[card] in ("/day", "/now", "/future")
        # Zapisany profil: żaden stan oczekiwania nie powstaje.
        assert str(PRIVATE) not in lb.PENDING_CITY

    @pytest.mark.parametrize("shortcut,card,ctx,prompt_key,wait_key", SHORTCUT_CASES)
    def test_profile_is_not_mutated(
        self, bot, monkeypatch, with_profile, shortcut, card, ctx, prompt_key, wait_key
    ):
        record_before = bot.users.record(PRIVATE)

        bot.run(bot.msg(PRIVATE, shortcut))

        assert bot.users.record(PRIVATE) == record_before
        assert bot.oneoffs[-1][0] == PRIVATE

    def test_day_outside_window_keeps_05_00_15_59_limit(
        self, bot, monkeypatch, with_profile
    ):
        """?d korzysta z tej samej bramki godziny co /dzien (bez zmian)."""
        freeze_local_hour(monkeypatch, 22, 0)

        bot.run(bot.msg(PRIVATE, "?d"))

        replies = bot.replies_since(PRIVATE, with_profile)
        assert len(replies) == 1 and "05:00" in replies[0], replies
        assert lb.t_ui("pl", "prep_main") not in replies
        assert bot.oneoffs == []

    def test_original_update_object_is_not_mutated(self, bot, monkeypatch, with_profile):
        """Przekierowanie używa kopii — update z Telegrama zostaje nietknięty."""
        freeze_local_hour(monkeypatch, 10, 0)
        update = bot.msg(PRIVATE, "?d")

        bot.run(update)

        assert update["message"]["text"] == "?d"
        assert [o[5] for o in bot.oneoffs] == ["day"]


# ============================================================================
# 2. ?d / ?12 / ?14 BEZ ARGUMENTU — UŻYTKOWNIK BEZ PROFILU
# ============================================================================

class TestNoProfile:

    @pytest.mark.parametrize("shortcut,card,ctx,prompt_key,wait_key", SHORTCUT_CASES)
    def test_oneoff_prompt_and_pending_context(
        self, bot, shortcut, card, ctx, prompt_key, wait_key
    ):
        bot.run(bot.msg(NO_PROFILE, "/start BETAX1"))
        before = len(bot.sent)

        bot.run(bot.msg(NO_PROFILE, shortcut))

        replies = bot.replies_since(NO_PROFILE, before)
        assert replies == [lb.t_ui("pl", prompt_key)], replies
        assert GUEST_PROMPT not in replies
        assert lb.PENDING_CITY[str(NO_PROFILE)]["ctx"] == ctx

    @pytest.mark.parametrize("shortcut,card,ctx,prompt_key,wait_key", SHORTCUT_CASES)
    def test_city_reply_geocodes_and_generates_oneoff(
        self, bot, monkeypatch, geocoder_log, shortcut, card, ctx, prompt_key, wait_key
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(NO_PROFILE, "/start BETAX1"))
        bot.run(bot.msg(NO_PROFILE, shortcut))
        prompt_idx = len(bot.sent)

        bot.run(bot.msg(NO_PROFILE, "Warszawa"))

        replies = bot.replies_since(NO_PROFILE, prompt_idx)
        assert replies[0] == SEARCH_LOC
        assert not WAIT_TEXTS & set(replies), "po geokodowaniu nie ma drugiego prep_*"
        assert len(geocoder_log) == 1 and geocoder_log[0]["city"] == "Warszawa"
        sent_before = [t for c, t in geocoder_log[0]["sent_before"][prompt_idx:]
                       if c == NO_PROFILE]
        assert sent_before == [SEARCH_LOC], "search_loc wyłącznie przed geokoderem"
        assert [o[5] for o in bot.oneoffs] == [card]
        assert bot.oneoffs[0][0] == NO_PROFILE and bot.oneoffs[0][3] == "Warszawa"
        assert str(NO_PROFILE) not in lb.PENDING_CITY, "stan skonsumowany po sukcesie"

    @pytest.mark.parametrize("shortcut,card,ctx,prompt_key,wait_key", SHORTCUT_CASES)
    def test_location_is_not_saved_as_profile(
        self, bot, monkeypatch, geocoder_log, shortcut, card, ctx, prompt_key, wait_key
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(NO_PROFILE, "/start BETAX1"))
        bot.run(bot.msg(NO_PROFILE, shortcut))
        bot.run(bot.msg(NO_PROFILE, "Warszawa"))

        rec = bot.users.record(NO_PROFILE)
        assert rec["profile_status"] == "none"
        assert rec["lat_round"] == "" and rec["lon_round"] == ""
        assert rec["location_label"] == ""
        assert rec["location_consent_at"] == ""
        assert str(NO_PROFILE) not in bot.gc.formularz.chat_ids()
        assert str(NO_PROFILE) not in lb.PENDING_CITY

    def test_day_outside_05_00_15_59_window_sends_time_limit(
        self, bot, monkeypatch, geocoder_log
    ):
        """Zakres /dzien 05:00–15:59 obowiązuje też skrót ?d (ta sama ścieżka)."""
        freeze_local_hour(monkeypatch, 22, 0)
        bot.run(bot.msg(NO_PROFILE, "/start BETAX1"))
        bot.run(bot.msg(NO_PROFILE, "?d"))
        prompt_idx = len(bot.sent)

        bot.run(bot.msg(NO_PROFILE, "Warszawa"))

        replies = bot.replies_since(NO_PROFILE, prompt_idx)
        assert replies[0] == SEARCH_LOC
        assert any("05:00" in r for r in replies), replies
        assert bot.oneoffs == []


# ============================================================================
# 3. ISTNIEJĄCE ALIASY PARSERA — BEZ NOWYCH ALIASÓW
# ============================================================================

class TestExistingAliases:

    @pytest.mark.parametrize("alias,card,ctx,prompt_key", ALIAS_CASES)
    def test_alias_redirects_to_same_command_family(
        self, bot, alias, card, ctx, prompt_key
    ):
        bot.run(bot.msg(NO_PROFILE, "/start BETAX1"))
        before = len(bot.sent)

        bot.run(bot.msg(NO_PROFILE, alias))

        assert bot.replies_since(NO_PROFILE, before) == [lb.t_ui("pl", prompt_key)]
        assert lb.PENDING_CITY[str(NO_PROFILE)]["ctx"] == ctx

    def test_redirect_map_covers_only_existing_card_types(self):
        assert set(lb.SHORTCUT_NO_ARG_COMMANDS) == {"day", "now", "future"}
        assert set(lb.SHORTCUT_NO_ARG_COMMANDS.values()) == {"/day", "/now", "/future"}

    @pytest.mark.parametrize("text", ["?1", "?11", "?13", "?15", "?g", "?x", "?.", "??d"])
    def test_no_new_alias_is_recognized(self, text):
        assert gbh.resolve_shortcut(text) == (None, None, None)
        assert lb._redirect_shortcut_without_argument(
            {"text": text, "chat": {"id": PRIVATE, "type": "private"}}
        ) is None


# ============================================================================
# 4. SKRÓTY Z ARGUMENTEM — ZACHOWANIE BEZ ZMIAN (GUEST HANDLER)
# ============================================================================

class TestShortcutWithArgument:

    @pytest.mark.parametrize("text,card", [
        ("?d Warszawa", "day"),
        ("?12 Warszawa", "now"),
        ("?14 Kraków", "future"),
        (".d Kraków", "day"),
        (".14 Kraków", "future"),
    ])
    def test_stays_one_off_in_guest_handler(
        self, bot, monkeypatch, guest_card, text, card
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(PRIVATE, "/start BETAX1"))
        before = len(bot.sent)

        bot.run(bot.msg(PRIVATE, text))

        assert guest_card.layouts == [card]
        assert guest_card.photos and guest_card.photos[0][0] == PRIVATE
        # Żadnego prep_*, search_loc ani promptu; żadnego pending i żadnej
        # karty jednorazowej ścieżki Users.
        assert bot.replies_since(PRIVATE, before) == []
        assert bot.oneoffs == []
        assert str(PRIVATE) not in lb.PENDING_CITY
        assert bot.users.record(PRIVATE)["profile_status"] == "none"

    def test_with_saved_profile_does_not_touch_users(
        self, bot, monkeypatch, guest_card, with_profile
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        record_before = bot.users.record(PRIVATE)

        bot.run(bot.msg(PRIVATE, "?14 Kraków"))

        assert guest_card.layouts == ["future"]
        assert bot.users.record(PRIVATE) == record_before
        assert bot.replies_since(PRIVATE, with_profile) == []
        assert bot.oneoffs == []

    def test_shortcut_reply_to_location_stays_in_guest_handler(
        self, bot, monkeypatch, guest_card
    ):
        """Wyjątek z audytu: skrót jako odpowiedź na pinezkę nie jest przekierowany."""
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(PRIVATE, "/start BETAX1"))
        update = bot.msg(PRIVATE, "?d")
        update["message"]["reply_to_message"] = {
            "message_id": 5,
            "location": {"latitude": 54.5, "longitude": 18.5},
        }

        bot.run(update)

        assert bot.real_guest_calls[-1]["message"]["text"] == "?d"
        assert guest_card.layouts == ["day"]
        assert bot.oneoffs == []
        assert str(PRIVATE) not in lb.PENDING_CITY


# ============================================================================
# 5. KOLEJNOŚĆ KOMUNIKATÓW
# ============================================================================

class TestMessageOrder:

    def test_saved_profile_wait_message_comes_before_card(
        self, bot, monkeypatch, with_profile
    ):
        order = []
        monkeypatch.setattr(
            lb, "_send_oneoff_report",
            lambda chat_id, lat, lon, city, lang, card_type:
                (order.append(list(bot.sent)), True)[1],
        )

        bot.run(bot.msg(PRIVATE, "?12"))

        assert len(order) == 1
        sent_before_card = [t for c, t in order[0][with_profile:] if c == PRIVATE]
        assert sent_before_card == [lb.t_ui("pl", "scanning")]

    def test_no_profile_prompt_then_search_loc_then_card(
        self, bot, monkeypatch, geocoder_log
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(NO_PROFILE, "/start BETAX1"))
        bot.run(bot.msg(NO_PROFILE, "?d"))
        prompt_idx = len(bot.sent)
        geocoder_saw = []

        def spy_geocode(city, lang="pl"):
            geocoder_saw.append([t for c, t in bot.sent[prompt_idx:] if c == NO_PROFILE])
            return (lb.GEOCODE_OK, 52.22972, 21.01223, city, f"{city}, Polska")

        monkeypatch.setattr(lb, "geocode_city_accepted", spy_geocode)

        bot.run(bot.msg(NO_PROFILE, "Warszawa"))

        assert geocoder_saw == [[SEARCH_LOC]], "przed geokoderem tylko search_loc"
        replies = bot.replies_since(NO_PROFILE, prompt_idx)
        assert not WAIT_TEXTS & set(replies)
        assert [o[5] for o in bot.oneoffs] == ["day"]


# ============================================================================
# 6. PENDING: ZAPIS, TTL, CZYSZCZENIE, ANULOWANIE
# ============================================================================

class TestPendingCity:

    @pytest.mark.parametrize("shortcut,card,ctx,prompt_key,wait_key", SHORTCUT_CASES)
    def test_state_is_saved_before_prompt(
        self, bot, monkeypatch, shortcut, card, ctx, prompt_key, wait_key
    ):
        snapshots = []
        real_send_reply = lb.send_reply

        def spy_send_reply(chat_id, text, reply_markup=None):
            if str(chat_id) == str(NO_PROFILE):
                entry = dict(lb.PENDING_CITY.get(str(NO_PROFILE)) or {})
                snapshots.append((entry, text))
            return real_send_reply(chat_id, text, reply_markup)

        monkeypatch.setattr(lb, "send_reply", spy_send_reply)
        bot.run(bot.msg(NO_PROFILE, "/start BETAX1"))
        snapshots.clear()

        bot.run(bot.msg(NO_PROFILE, shortcut))

        assert snapshots, "prompt musi zostać wysłany"
        entry, text = snapshots[0]
        assert text == lb.t_ui("pl", prompt_key)
        assert entry.get("ctx") == ctx, "stan zapisany PRZED promptem"
        assert entry.get("expires_ts", 0) > time.time()

    def test_ttl_matches_existing_pending_city(self, bot):
        before = time.time()
        bot.run(bot.msg(NO_PROFILE, "/start BETAX1"))
        bot.run(bot.msg(NO_PROFILE, "?12"))

        entry = lb.PENDING_CITY[str(NO_PROFILE)]
        assert lb.PENDING_TTL_SEC == 300
        assert before + lb.PENDING_TTL_SEC <= entry["expires_ts"] <= time.time() + lb.PENDING_TTL_SEC

    def test_expired_state_is_removed_and_city_reply_is_ignored(self, bot):
        bot.run(bot.msg(NO_PROFILE, "/start BETAX1"))
        bot.run(bot.msg(NO_PROFILE, "?12"))
        lb.PENDING_CITY[str(NO_PROFILE)]["expires_ts"] = time.time() - 1
        before = len(bot.sent)

        bot.run(bot.msg(NO_PROFILE, "Warszawa"))

        assert bot.replies_since(NO_PROFILE, before) == []
        assert bot.oneoffs == []
        assert str(NO_PROFILE) not in lb.PENDING_CITY

    def test_state_is_cleared_after_geocoder_error(self, bot, monkeypatch):
        monkeypatch.setattr(
            lb, "geocode_city_accepted",
            lambda city, lang="pl": (lb.GEOCODE_NOT_FOUND, None, None, None, None),
        )
        bot.run(bot.msg(NO_PROFILE, "/start BETAX1"))
        bot.run(bot.msg(NO_PROFILE, "?12"))
        before = len(bot.sent)

        bot.run(bot.msg(NO_PROFILE, "Xyzzz"))

        replies = bot.replies_since(NO_PROFILE, before)
        assert replies == [SEARCH_LOC, lb.t_ui("pl", "search_fail")]
        assert bot.oneoffs == []
        assert str(NO_PROFILE) not in lb.PENDING_CITY

    def test_new_shortcut_overrides_pending_context(self, bot, monkeypatch, geocoder_log):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(NO_PROFILE, "/start BETAX1"))
        bot.run(bot.msg(NO_PROFILE, "?12"))
        bot.run(bot.msg(NO_PROFILE, "?14"))

        assert lb.PENDING_CITY[str(NO_PROFILE)]["ctx"] == lb.CTX_ONEOFF_FUTURE

        bot.run(bot.msg(NO_PROFILE, "Warszawa"))

        assert [o[5] for o in bot.oneoffs] == ["future"]
        assert str(NO_PROFILE) not in lb.PENDING_CITY

    def test_forget_location_clears_pending(self, bot):
        bot.run(bot.msg(NO_PROFILE, "/start BETAX1"))
        bot.run(bot.msg(NO_PROFILE, "?d"))
        assert str(NO_PROFILE) in lb.PENDING_CITY

        bot.run(bot.msg(NO_PROFILE, "/bezGPS"))

        assert str(NO_PROFILE) not in lb.PENDING_CITY


# ============================================================================
# 6B. AKTYWNY DIALOG /raport — SKRÓT KOŃCZY DIALOG GODZIN
# ============================================================================

class TestShortcutDuringReportDialog:
    """Zaakceptowana reguła produktowa.

    W czacie prywatnym skrót bez argumentu jest przekierowany na komendę, więc
    w trakcie dialogu /raport działa jak KAŻDA inna komenda: kończy dialog bez
    zapisu godzin (``_handle_report_settings_dialog`` czyści stan, gdy tekst
    zaczyna się od „/”) i realizuje swoje zadanie. Wcześniej skrót ginął
    w wadliwym prompcie gościa, a dialog godzin zostawał otwarty.

    Dialog godzin jest prywatny (autor = czat), więc stanu nie ruszają skróty
    z argumentem — te nadal należą do guest handlera.
    """

    @pytest.mark.parametrize("shortcut,card,wait_key", [
        ("?d", "day", "prep_main"),
        ("?12", "now", "scanning"),
        ("?14", "future", "prep_future"),
    ])
    def test_shortcut_ends_dialog_and_runs_its_command(
        self, bot, monkeypatch, with_profile, shortcut, card, wait_key
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        record_before = bot.users.record(PRIVATE)

        bot.run(bot.msg(PRIVATE, "/raport"))
        assert lb.PENDING_REPORT, "dialog godzin musi być aktywny"
        before = len(bot.sent)

        bot.run(bot.msg(PRIVATE, shortcut))

        assert lb.PENDING_REPORT == {}, "skrót kończy dialog godzin"
        assert bot.users.record(PRIVATE) == record_before, "dialog nic nie zapisuje"
        assert bot.replies_since(PRIVATE, before) == [lb.t_ui("pl", wait_key)]
        assert [o[5] for o in bot.oneoffs] == [card]
        assert str(PRIVATE) not in lb.PENDING_CITY

    def test_shortcut_with_argument_does_not_touch_report_dialog(
        self, bot, monkeypatch, guest_card, with_profile
    ):
        freeze_local_hour(monkeypatch, 10, 0)

        bot.run(bot.msg(PRIVATE, "/raport"))
        bot.run(bot.msg(PRIVATE, "?12 Kraków"))

        assert lb.PENDING_REPORT, "skrót z argumentem zostaje w trybie gościa"
        assert guest_card.layouts == ["now"]
        assert bot.oneoffs == []


# ============================================================================
# 7. P7 — NIEUPRAWNIONY UŻYTKOWNIK: CISZA I BRAK SKUTKÓW
# ============================================================================

class TestP7SilentGuard:

    @pytest.mark.parametrize("shortcut", ["?d", "?12", "?14"])
    def test_unauthorized_shortcut_is_silent(self, bot, shortcut):
        bot.run(bot.msg(STRANGER, shortcut))

        assert bot.replies(STRANGER) == []
        assert bot.oneoffs == []
        assert bot.real_guest_calls == [], "guard musi wyciszyć PRZED guest handlerem"
        assert lb.PENDING_CITY == {}
        assert lb.PENDING_SAVE == {}
        assert bot.users.record(STRANGER) is None
        assert str(STRANGER) not in bot.gc.formularz.chat_ids()

    @pytest.mark.parametrize("shortcut", ["?d", "?12", "?14"])
    def test_no_prompt_of_any_kind_reaches_stranger(self, bot, shortcut):
        bot.run(bot.msg(STRANGER, shortcut))

        sent = [t for c, t in bot.sent if c == STRANGER]
        assert sent == []
        for key in ("guest_need_city", "oneoff_prompt_day", "oneoff_prompt_now",
                    "oneoff_prompt_future", "search_loc"):
            assert lb.t_ui("pl", key) not in sent


# ============================================================================
# 8. GRUPY — POLITYKA BEZ ZMIAN
# ============================================================================

class TestGroups:

    @pytest.mark.parametrize("shortcut", ["?d", "?12", "?14"])
    def test_shortcut_without_argument_stays_silent(self, bot, shortcut):
        bot.run(bot.msg(GROUP, "/start BETAX1", user_id=GROUP_ADMIN))
        before = len(bot.sent)

        bot.run(bot.msg(GROUP, shortcut, user_id=GROUP_ADMIN, chat_type="group"))

        assert bot.replies_since(GROUP, before) == []
        assert bot.oneoffs == []
        assert str(GROUP) not in lb.PENDING_CITY
        # Grupa nie jest przekierowywana — skrót nadal trafia do guest handlera,
        # który w grupie zachowuje ciszę.
        assert bot.real_guest_calls[-1]["message"]["text"] == shortcut

    def test_group_profile_is_read_from_group_chat_id(self, bot, monkeypatch):
        monkeypatch.setattr(
            lb, "_telegram_chat_member_status",
            lambda chat_id, user_id: "administrator",
        )
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(GROUP, "/start BETAX1", user_id=GROUP_ADMIN))
        bot.run(bot.msg(GROUP, "/miasto", user_id=GROUP_ADMIN))
        bot.run(bot.msg(GROUP, "Warszawa", user_id=GROUP_ADMIN))

        rec = bot.users.record(GROUP)
        assert rec["profile_status"] == "active"
        assert bot.users.record(GROUP_ADMIN) is None

        before = len(bot.sent)
        bot.run(bot.msg(GROUP, "/dzien", user_id=GROUP_ADMIN, chat_type="group"))

        assert bot.replies_since(GROUP, before) == [lb.t_ui("pl", "prep_main")]
        assert bot.oneoffs[-1][0] == GROUP
        assert str(GROUP) not in lb.PENDING_CITY

    def test_group_shortcut_with_argument_still_cards(self, bot, monkeypatch, guest_card):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(GROUP, "/start BETAX1", user_id=GROUP_ADMIN))
        before = len(bot.sent)

        bot.run(bot.msg(GROUP, "?12 Warszawa", user_id=GROUP_ADMIN, chat_type="group"))

        assert guest_card.layouts == ["now"]
        assert guest_card.photos and guest_card.photos[0][0] == GROUP
        assert bot.replies_since(GROUP, before) == []
        assert bot.oneoffs == []


# ============================================================================
# 9. LEGACY-ONLY — DOTYCHCZASOWE /day, /now, /future
# ============================================================================

class TestLegacyOnly:

    def test_legacy_day_shortcut_uses_legacy_command_path(self, bot, monkeypatch):
        freeze_local_hour(monkeypatch, 10, 0)
        before = len(bot.sent)

        bot.run(bot.msg(LEGACY_WITH_LOC, "?d"))

        assert bot.replies_since(LEGACY_WITH_LOC, before) == [lb.t_ui("pl", "prep_main")]
        assert bot.cards == [str(LEGACY_WITH_LOC)]
        assert bot.oneoffs == []
        assert str(LEGACY_WITH_LOC) not in lb.PENDING_CITY
        # Legacy nie jest migrowany do Users.
        assert bot.users.record(LEGACY_WITH_LOC) is None

    def test_legacy_without_location_gets_missing_loc(self, bot):
        before = len(bot.sent)

        bot.run(bot.msg(LEGACY_NO_LOC, "?12"))

        assert bot.replies_since(LEGACY_NO_LOC, before) == [lb.t_ui("pl", "missing_loc")]
        assert bot.oneoffs == []
        assert str(LEGACY_NO_LOC) not in lb.PENDING_CITY
        assert bot.users.record(LEGACY_NO_LOC) is None


# ============================================================================
# 10. KONTRAKT POMOCNIKA PRZEKIEROWANIA
# ============================================================================

class TestRedirectHelper:

    def _redirect(self, text, chat_type="private", reply=None):
        message = {"text": text, "chat": {"id": 1, "type": chat_type}}
        if reply is not None:
            message["reply_to_message"] = reply
        return lb._redirect_shortcut_without_argument(message)

    @pytest.mark.parametrize("text,command", [
        ("?d", "/day"), (".d", "/day"), ("?t", "/day"), ("?j", "/day"),
        ("?12", "/now"), (".12", "/now"), ("?n", "/now"), ("?p", "/now"),
        ("?14", "/future"), (".14", "/future"), ("?f", "/future"),
    ])
    def test_private_shortcut_without_argument_maps_to_command(self, text, command):
        redirected = self._redirect(text)
        assert redirected is not None
        assert redirected["text"] == command

    @pytest.mark.parametrize("text", [
        "?d Warszawa", "?12 Warszawa", "?14 05-462 Wiązowna", "?dWarszawa",
        "@Bot ?d", "Cześć", "/dzien", "", "   ",
    ])
    def test_other_messages_are_not_redirected(self, text):
        assert self._redirect(text) is None

    @pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
    def test_groups_are_not_redirected(self, chat_type):
        assert self._redirect("?d", chat_type=chat_type) is None

    def test_reply_to_location_is_not_redirected(self):
        assert self._redirect("?d", reply={"location": {"latitude": 1, "longitude": 2}}) is None

    def test_helper_returns_a_copy(self):
        original = {"text": "?d", "chat": {"id": 1, "type": "private"}}
        redirected = lb._redirect_shortcut_without_argument(original)

        assert redirected is not original
        assert original["text"] == "?d"
        assert redirected["text"] == "/day"
