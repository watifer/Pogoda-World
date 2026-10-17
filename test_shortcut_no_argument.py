"""test_shortcut_no_argument.py — skróty ?d / ?12 / ?14 BEZ argumentu.

Zasada produktowa (korekta poprzedniego przekierowania na /day, /now, /future):

    Skrót bez argumentu w czacie prywatnym dla autoryzowanego użytkownika
    ZAWSZE otwiera dialog jednorazowego zapytania o miejscowość — niezależnie
    od tego, czy użytkownik ma zapisaną lokalizację.

    ?d  -> CTX_ONEOFF_DAY    -> oneoff_prompt_day
    ?12 -> CTX_ONEOFF_NOW    -> oneoff_prompt_now
    ?14 -> CTX_ONEOFF_FUTURE -> oneoff_prompt_future

Wpisana miejscowość idzie przez gałąź 8B (PENDING_CITY) -> search_loc ->
geokoder -> karta jednorazowa. Stała lokalizacja NIE jest używana jako
lokalizacja karty, NIE jest zmieniana i NIE jest zapisywana na nowo. Ścieżka
/day, /now, /future ani _run_saved_profile_report() nie są w tym przepływie
wołane.

Skróty z argumentem (?d Warszawa) zachowują dotychczasową ścieżkę guest handlera.

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

# Współrzędne i nazwy używane w testach „stała lokalizacja vs. zapytanie”.
SAVED_CITY = "Warszawa"        # stała lokalizacja (profil) — zob. with_profile
QUERY_CITY = "Kraków"          # miejscowość wpisana po skrócie
QUERY_LAT, QUERY_LON = 50.0647, 19.9450

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

# Klucze promptów oneoff per typ karty (lustro ONEOFF_PROMPT_KEYS z location_bot).
SHORTCUT_PROMPT = {
    "day": "oneoff_prompt_day",
    "now": "oneoff_prompt_now",
    "future": "oneoff_prompt_future",
}


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

    # Zapis do arkusza Formularz w ścieżce legacy — rejestrujemy, żeby sprawdzić,
    # że skrót ani odpowiedź z miastem jej nie dotykają.
    form_writes = []
    monkeypatch.setattr(
        lb, "_form_update_cell",
        lambda main_sheet, row, col, value: form_writes.append((row, col, value)),
    )
    harness.form_writes = form_writes
    return harness


@pytest.fixture
def with_profile(bot, monkeypatch):
    """Użytkownik Users z aktywnym profilem: stała lokalizacja = Warszawa."""
    bot.run(bot.msg(PRIVATE, "/start BETAX1"))
    bot.run(bot.msg(PRIVATE, "/miasto"))
    bot.run(bot.msg(PRIVATE, SAVED_CITY))
    assert bot.users.record(PRIVATE)["profile_status"] == "active"
    return len(bot.sent)


@pytest.fixture
def query_geocoder(bot, monkeypatch):
    """Geokoder zwraca Kraków dla wpisanej nazwy i rejestruje wywołania.

    Zapisuje też stan czatu PRZED zapytaniem, żeby sprawdzić kolejność
    search_loc -> geokoder oraz brak prep_* przed wpisaniem miasta.
    """
    calls = []

    def fake_geocode(city, lang="pl"):
        calls.append({"city": city, "sent_before": list(bot.sent), "lang": lang})
        return (lb.GEOCODE_OK, QUERY_LAT, QUERY_LON, city, f"{city}, Polska")

    monkeypatch.setattr(lb, "geocode_city_accepted", fake_geocode)
    # Etykiety z reverse geocodera muszą pokazywać Kraków, nie domyślną Warszawę
    # z atrapy harnessu — inaczej test nie odróżniłby karty od stałej lokalizacji.
    monkeypatch.setattr(
        lb, "get_city_from_coords", lambda lat, lon, lang="pl": QUERY_CITY,
    )
    monkeypatch.setattr(
        lb, "get_location_details_from_coords",
        lambda lat, lon, lang="pl", query=None, mode=None: (
            QUERY_CITY, f"{QUERY_CITY}, Polska", lb.GEO_OK,
        ),
    )
    return calls


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


def _forbid_saved_profile_path(monkeypatch):
    """Dowód, że ścieżka zapisanego profilu NIE jest używana przez skrót."""
    calls = []

    def boom(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("skrót bez argumentu nie może wołać _run_saved_profile_report")

    monkeypatch.setattr(lb, "_run_saved_profile_report", boom)
    return calls


def _forbid_command_paths(monkeypatch):
    """Zabezpiecza, że skrót nie uruchamia zwykłych komend /day, /now, /future."""
    calls = []
    monkeypatch.setattr(
        lb, "_run_city_oneoff",
        lambda *a, **k: calls.append(("city_oneoff", a)) or False,
    )
    return calls


# ============================================================================
# 1. ?d / ?12 / ?14 BEZ ARGUMENTU — UŻYTKOWNIK Z ZAPISANYM PROFILEM
# ============================================================================

class TestSavedProfile:
    """Zapisany profil NIE zmienia faktu, że skrót bez argumentu pyta o miasto."""

    @pytest.mark.parametrize("shortcut,card,ctx,prompt_key,wait_key", SHORTCUT_CASES)
    def test_shortcut_opens_oneoff_prompt_not_card(
        self, bot, monkeypatch, with_profile, shortcut, card, ctx, prompt_key, wait_key
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        _forbid_saved_profile_path(monkeypatch)

        bot.run(bot.msg(PRIVATE, shortcut))

        replies = bot.replies_since(PRIVATE, with_profile)
        assert replies == [lb.t_ui("pl", prompt_key)], replies
        assert SEARCH_LOC not in replies
        assert GUEST_PROMPT not in replies
        assert not WAIT_TEXTS & set(replies), "brak komunikatu oczekiwania ze stałej lokalizacji"
        # Nie może zostać wysłana karta przed wpisaniem miasta.
        assert bot.oneoffs == []
        assert bot.cards == []
        # Stan oczekiwania na miasto jest zapisany z właściwym kontekstem.
        assert lb.PENDING_CITY[str(PRIVATE)]["ctx"] == ctx

    @pytest.mark.parametrize("shortcut,card,ctx,prompt_key,wait_key", SHORTCUT_CASES)
    def test_never_calls_saved_profile_report_or_guest_handler(
        self, bot, monkeypatch, with_profile, shortcut, card, ctx, prompt_key, wait_key
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        saved_calls = _forbid_saved_profile_path(monkeypatch)
        command_calls = _forbid_command_paths(monkeypatch)
        guest_before = len(bot.real_guest_calls)

        bot.run(bot.msg(PRIVATE, shortcut))

        assert saved_calls == [], "skrót bez argumentu nie przechodzi przez _run_saved_profile_report()"
        assert command_calls == [], "skrót nie wywołuje ścieżki /day, /now, /future"
        # Fixture with_profile też przepuszcza zwykły tekst przez guest handler,
        # więc sprawdzamy wyłącznie wywołania dla TEJ wiadomości.
        shortcut_guest_calls = [
            c for c in bot.real_guest_calls[guest_before:]
            if (c["message"].get("text") or "") == shortcut
        ]
        assert shortcut_guest_calls == [], "guest_bot_handler nie jest wołany dla skrótu bez argumentu"

    @pytest.mark.parametrize("shortcut,card,ctx,prompt_key,wait_key", SHORTCUT_CASES)
    def test_profile_is_not_mutated_by_shortcut(
        self, bot, monkeypatch, with_profile, shortcut, card, ctx, prompt_key, wait_key
    ):
        record_before = bot.users.record(PRIVATE)
        form_before = list(bot.form_writes)

        bot.run(bot.msg(PRIVATE, shortcut))

        assert bot.users.record(PRIVATE) == record_before
        assert bot.form_writes == form_before
        assert bot.oneoffs == []

    @pytest.mark.parametrize("alias,card,ctx,prompt_key", ALIAS_CASES)
    def test_existing_aliases_also_open_prompt_with_profile(
        self, bot, monkeypatch, with_profile, alias, card, ctx, prompt_key
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        saved_calls = _forbid_saved_profile_path(monkeypatch)

        bot.run(bot.msg(PRIVATE, alias))

        assert bot.replies_since(PRIVATE, with_profile) == [lb.t_ui("pl", prompt_key)]
        assert lb.PENDING_CITY[str(PRIVATE)]["ctx"] == ctx
        assert saved_calls == []
        assert bot.oneoffs == []

    @pytest.mark.parametrize("shortcut,card,ctx,prompt_key,wait_key", SHORTCUT_CASES)
    def test_city_reply_uses_queried_city_not_saved_location(
        self, bot, monkeypatch, with_profile, query_geocoder,
        shortcut, card, ctx, prompt_key, wait_key,
    ):
        """saved location = Warszawa, query = Kraków, result card = Kraków."""
        freeze_local_hour(monkeypatch, 10, 0)
        record_before = bot.users.record(PRIVATE)
        form_before = list(bot.form_writes)

        bot.run(bot.msg(PRIVATE, shortcut))
        prompt_idx = len(bot.sent)
        bot.run(bot.msg(PRIVATE, QUERY_CITY))

        # Karta dla NOWEGO miasta, jednorazowo.
        assert len(bot.oneoffs) == 1
        chat_id, lat, lon, city, _lang, card_type = bot.oneoffs[0]
        assert chat_id == PRIVATE
        assert card_type == card
        assert (lat, lon) == (QUERY_LAT, QUERY_LON), "karta z zapytanego miasta, nie z profilu"
        assert city == QUERY_CITY

        # Geokoder dostał wpisaną nazwę; search_loc tylko przed nim.
        assert [c["city"] for c in query_geocoder] == [QUERY_CITY]
        sent_before = [t for c, t in query_geocoder[0]["sent_before"][prompt_idx:] if c == PRIVATE]
        assert sent_before == [SEARCH_LOC], "search_loc wyłącznie przed geokoderem"

        replies = bot.replies_since(PRIVATE, prompt_idx)
        assert replies[0] == SEARCH_LOC
        assert not WAIT_TEXTS & set(replies), "brak prep_* przed wpisaniem miasta ani po nim"

        # Stała lokalizacja nadal Warszawa: brak zapisu, brak zmiany profilu.
        assert bot.users.record(PRIVATE) == record_before
        assert bot.users.record(PRIVATE)["location_label"] == record_before["location_label"]
        assert bot.form_writes == form_before
        assert str(PRIVATE) not in lb.PENDING_CITY, "stan skonsumowany po sukcesie"

    def test_saved_location_stays_warszawa_after_two_shortcuts(
        self, bot, monkeypatch, with_profile, query_geocoder
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        record_before = bot.users.record(PRIVATE)

        bot.run(bot.msg(PRIVATE, "?d"))
        bot.run(bot.msg(PRIVATE, QUERY_CITY))
        bot.run(bot.msg(PRIVATE, "?14"))
        bot.run(bot.msg(PRIVATE, "Gdańsk"))

        assert [o[5] for o in bot.oneoffs] == ["day", "future"]
        assert bot.users.record(PRIVATE) == record_before
        assert bot.users.record(PRIVATE)["lat_round"] == record_before["lat_round"]
        assert bot.users.record(PRIVATE)["lon_round"] == record_before["lon_round"]

    def test_expired_pending_with_profile_does_not_fall_back_to_profile(
        self, bot, monkeypatch, with_profile
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(PRIVATE, "?12"))
        lb.PENDING_CITY[str(PRIVATE)]["expires_ts"] = time.time() - 1
        before = len(bot.sent)

        bot.run(bot.msg(PRIVATE, QUERY_CITY))

        assert bot.replies_since(PRIVATE, before) == []
        assert bot.oneoffs == []
        assert str(PRIVATE) not in lb.PENDING_CITY

    def test_day_window_is_checked_after_city_not_before_prompt(
        self, bot, monkeypatch, with_profile, query_geocoder
    ):
        """Poza 05:00–15:59 prompt się pojawia; limit okna działa dopiero po mieście."""
        freeze_local_hour(monkeypatch, 22, 0)

        bot.run(bot.msg(PRIVATE, "?d"))
        prompt_replies = bot.replies_since(PRIVATE, with_profile)
        assert prompt_replies == [lb.t_ui("pl", "oneoff_prompt_day")], prompt_replies
        assert bot.oneoffs == []

        bot.run(bot.msg(PRIVATE, QUERY_CITY))

        replies = bot.replies_since(PRIVATE, with_profile)
        assert any("05:00" in r for r in replies), replies
        assert bot.oneoffs == []
        assert bot.users.record(PRIVATE)["profile_status"] == "active"

    def test_original_update_object_is_not_mutated(self, bot, monkeypatch, with_profile):
        """Skrót nie zmienia wiadomości Telegrama, bo nie jest przekierowywany."""
        freeze_local_hour(monkeypatch, 10, 0)
        update = bot.msg(PRIVATE, "?d")

        bot.run(update)

        assert update["message"]["text"] == "?d"
        assert bot.oneoffs == []
        assert bot.replies_since(PRIVATE, with_profile) == [lb.t_ui("pl", "oneoff_prompt_day")]


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
        assert bot.real_guest_calls == []

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
    def test_alias_opens_prompt_for_same_card_family(
        self, bot, alias, card, ctx, prompt_key
    ):
        bot.run(bot.msg(NO_PROFILE, "/start BETAX1"))
        before = len(bot.sent)

        bot.run(bot.msg(NO_PROFILE, alias))

        assert bot.replies_since(NO_PROFILE, before) == [lb.t_ui("pl", prompt_key)]
        assert lb.PENDING_CITY[str(NO_PROFILE)]["ctx"] == ctx
        assert bot.oneoffs == []

    def test_context_map_covers_only_existing_card_types(self):
        assert set(lb.SHORTCUT_NO_ARG_CONTEXTS) == {"day", "now", "future"}
        assert set(lb.SHORTCUT_NO_ARG_CONTEXTS.values()) == {
            lb.CTX_ONEOFF_DAY, lb.CTX_ONEOFF_NOW, lb.CTX_ONEOFF_FUTURE,
        }

    @pytest.mark.parametrize("text", ["?1", "?11", "?13", "?15", "?g", "?x", "?.", "??d"])
    def test_no_new_alias_is_recognized(self, text):
        assert gbh.resolve_shortcut(text) == (None, None, None)
        assert lb._shortcut_without_argument_ctx(
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
        assert str(PRIVATE) not in lb.PENDING_CITY

    def test_shortcut_reply_to_location_stays_in_guest_handler(
        self, bot, monkeypatch, guest_card
    ):
        """Wyjątek z audytu: skrót jako odpowiedź na pinezkę nie otwiera promptu."""
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

    def test_saved_profile_prompt_then_city_search_then_card(
        self, bot, monkeypatch, with_profile, query_geocoder
    ):
        """Najpierw prompt, potem (po miastu) search_loc, a dopiero na końcu karta."""
        freeze_local_hour(monkeypatch, 10, 0)
        order = []
        monkeypatch.setattr(
            lb, "_send_oneoff_report",
            lambda chat_id, lat, lon, city, lang, card_type, display_location=None:
                (order.append((card_type, [t for c, t in bot.sent if c == PRIVATE][with_profile:])), True)[1],
        )

        bot.run(bot.msg(PRIVATE, "?12"))
        assert order == [], "przed wpisaniem miasta karta nie może powstać"

        bot.run(bot.msg(PRIVATE, QUERY_CITY))

        assert len(order) == 1
        card_type, sent_before_card = order[0]
        assert card_type == "now"
        assert sent_before_card == [lb.t_ui("pl", "oneoff_prompt_now"), SEARCH_LOC]
        assert not WAIT_TEXTS & set(sent_before_card)

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

    @pytest.mark.parametrize("shortcut,card,ctx,prompt_key,wait_key", SHORTCUT_CASES)
    def test_state_is_saved_before_prompt_with_profile(
        self, bot, monkeypatch, with_profile, shortcut, card, ctx, prompt_key, wait_key
    ):
        snapshots = []
        real_send_reply = lb.send_reply

        def spy_send_reply(chat_id, text, reply_markup=None):
            if str(chat_id) == str(PRIVATE):
                snapshots.append((dict(lb.PENDING_CITY.get(str(PRIVATE)) or {}), text))
            return real_send_reply(chat_id, text, reply_markup)

        monkeypatch.setattr(lb, "send_reply", spy_send_reply)
        snapshots.clear()

        bot.run(bot.msg(PRIVATE, shortcut))

        assert snapshots and snapshots[0][1] == lb.t_ui("pl", prompt_key)
        assert snapshots[0][0].get("ctx") == ctx
        assert snapshots[0][0].get("expires_ts", 0) > time.time()

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

    def test_state_is_cleared_after_geocoder_error_with_profile(
        self, bot, monkeypatch, with_profile
    ):
        monkeypatch.setattr(
            lb, "geocode_city_accepted",
            lambda city, lang="pl": (lb.GEOCODE_NOT_FOUND, None, None, None, None),
        )
        record_before = bot.users.record(PRIVATE)
        bot.run(bot.msg(PRIVATE, "?d"))

        bot.run(bot.msg(PRIVATE, "Xyzzz"))

        assert str(PRIVATE) not in lb.PENDING_CITY
        assert bot.oneoffs == []
        assert bot.users.record(PRIVATE) == record_before

    def test_state_is_cleared_after_success_with_profile(
        self, bot, monkeypatch, with_profile, query_geocoder
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(PRIVATE, "?14"))
        assert str(PRIVATE) in lb.PENDING_CITY

        bot.run(bot.msg(PRIVATE, QUERY_CITY))

        assert str(PRIVATE) not in lb.PENDING_CITY
        assert [o[5] for o in bot.oneoffs] == ["future"]

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
    """Skrót bez argumentu kończy dialog godzin, zanim otworzy prompt o miasto.

    Blok skrótów (0A) jest wyżej niż dialog godzin (0.7) i kończy obsługę
    update. Bez jawnego czyszczenia wpis dialogu zostałby otwarty i kolejne
    miasto trafiłoby do dialogu godzin zamiast do gałęzi 8B.
    """

    @pytest.mark.parametrize("shortcut,card,ctx", [
        ("?d", "day", lb.CTX_ONEOFF_DAY),
        ("?12", "now", lb.CTX_ONEOFF_NOW),
        ("?14", "future", lb.CTX_ONEOFF_FUTURE),
    ])
    def test_shortcut_ends_dialog_and_opens_prompt(
        self, bot, monkeypatch, with_profile, shortcut, card, ctx
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        record_before = bot.users.record(PRIVATE)

        bot.run(bot.msg(PRIVATE, "/raport"))
        assert lb.PENDING_REPORT, "dialog godzin musi być aktywny"
        before = len(bot.sent)

        bot.run(bot.msg(PRIVATE, shortcut))

        assert lb.PENDING_REPORT == {}, "skrót kończy dialog godzin"
        assert bot.users.record(PRIVATE) == record_before, "dialog nic nie zapisuje"
        assert bot.replies_since(PRIVATE, before) == [lb.t_ui("pl", SHORTCUT_PROMPT[card])]
        assert bot.oneoffs == []
        assert lb.PENDING_CITY[str(PRIVATE)]["ctx"] == ctx

    def test_city_after_shortcut_goes_to_oneoff_not_to_report_dialog(
        self, bot, monkeypatch, with_profile, query_geocoder
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        record_before = bot.users.record(PRIVATE)

        bot.run(bot.msg(PRIVATE, "/raport"))
        bot.run(bot.msg(PRIVATE, "?12"))
        bot.run(bot.msg(PRIVATE, QUERY_CITY))

        assert [o[5] for o in bot.oneoffs] == ["now"]
        assert bot.oneoffs[0][1:3] == (QUERY_LAT, QUERY_LON)
        assert lb.PENDING_REPORT == {}
        assert bot.users.record(PRIVATE) == record_before

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

    @pytest.mark.parametrize("shortcut", ["?d", "?12", "?14"])
    def test_unauthorized_city_reply_after_shortcut_is_silent(self, bot, shortcut, query_geocoder):
        bot.run(bot.msg(STRANGER, shortcut))
        bot.run(bot.msg(STRANGER, QUERY_CITY))

        assert bot.replies(STRANGER) == []
        assert bot.oneoffs == []
        assert bot.users.record(STRANGER) is None


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
        # Grupa nie otwiera promptu — skrót nadal trafia do guest handlera,
        # który w grupie zachowuje ciszę.
        assert bot.real_guest_calls[-1]["message"]["text"] == shortcut

    @pytest.mark.parametrize("shortcut", ["?d", "?12", "?14"])
    def test_shortcut_without_argument_stays_silent_with_group_profile(
        self, bot, monkeypatch, shortcut
    ):
        monkeypatch.setattr(
            lb, "_telegram_chat_member_status",
            lambda chat_id, user_id: "administrator",
        )
        bot.run(bot.msg(GROUP, "/start BETAX1", user_id=GROUP_ADMIN))
        bot.run(bot.msg(GROUP, "/miasto", user_id=GROUP_ADMIN))
        bot.run(bot.msg(GROUP, SAVED_CITY, user_id=GROUP_ADMIN))
        assert bot.users.record(GROUP)["profile_status"] == "active"
        before = len(bot.sent)
        record_before = bot.users.record(GROUP)

        bot.run(bot.msg(GROUP, shortcut, user_id=GROUP_ADMIN, chat_type="group"))

        assert bot.replies_since(GROUP, before) == []
        assert bot.oneoffs == []
        assert str(GROUP) not in lb.PENDING_CITY
        assert bot.users.record(GROUP) == record_before

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
# 9. LEGACY-ONLY — SKRÓT BEZ ARGUMENTU TEŻ OTWIERA PROMPT, BEZ ZAPISU
# ============================================================================

class TestLegacyOnly:
    """Użytkownik tylko w Formularz (bez Users) też dostaje prompt o miasto.

    Karta jednorazowa z odpowiedzi nic nie zapisuje — zwłaszcza nie zmienia
    lokalizacji w Formularz, nawet gdy użytkownik ma już zapisane współrzędne.
    """

    @pytest.mark.parametrize("shortcut,ctx,prompt_key", [
        ("?d", lb.CTX_ONEOFF_DAY, "oneoff_prompt_day"),
        ("?12", lb.CTX_ONEOFF_NOW, "oneoff_prompt_now"),
        ("?14", lb.CTX_ONEOFF_FUTURE, "oneoff_prompt_future"),
    ])
    def test_legacy_shortcut_opens_prompt_without_card(
        self, bot, monkeypatch, shortcut, ctx, prompt_key
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        before = len(bot.sent)

        bot.run(bot.msg(LEGACY_WITH_LOC, shortcut))

        assert bot.replies_since(LEGACY_WITH_LOC, before) == [lb.t_ui("pl", prompt_key)]
        assert bot.cards == []
        assert bot.oneoffs == []
        assert lb.PENDING_CITY[str(LEGACY_WITH_LOC)]["ctx"] == ctx
        assert bot.users.record(LEGACY_WITH_LOC) is None

    def test_legacy_without_location_gets_prompt_not_missing_loc(self, bot, monkeypatch):
        freeze_local_hour(monkeypatch, 10, 0)
        before = len(bot.sent)

        bot.run(bot.msg(LEGACY_NO_LOC, "?12"))

        assert bot.replies_since(LEGACY_NO_LOC, before) == [lb.t_ui("pl", "oneoff_prompt_now")]
        assert bot.oneoffs == []
        assert bot.users.record(LEGACY_NO_LOC) is None

    def test_legacy_city_reply_after_shortcut_is_oneoff_and_does_not_write_formularz(
        self, bot, monkeypatch, query_geocoder
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(LEGACY_WITH_LOC, "?12"))
        form_before = list(bot.form_writes)

        bot.run(bot.msg(LEGACY_WITH_LOC, QUERY_CITY))

        assert [o[5] for o in bot.oneoffs] == ["now"]
        assert bot.oneoffs[0][0] == LEGACY_WITH_LOC
        assert bot.oneoffs[0][1:3] == (QUERY_LAT, QUERY_LON)
        assert bot.form_writes == form_before, "Formularz nie może zmienić lokalizacji"
        assert bot.users.record(LEGACY_WITH_LOC) is None
        assert str(LEGACY_WITH_LOC) not in lb.PENDING_CITY

    def test_legacy_shortcut_with_argument_keeps_guest_path(
        self, bot, monkeypatch, guest_card
    ):
        freeze_local_hour(monkeypatch, 10, 0)
        before = len(bot.sent)

        bot.run(bot.msg(LEGACY_WITH_LOC, "?d Warszawa"))

        assert guest_card.layouts == ["day"]
        assert bot.replies_since(LEGACY_WITH_LOC, before) == []
        assert bot.oneoffs == []
        assert str(LEGACY_WITH_LOC) not in lb.PENDING_CITY


# ============================================================================
# 10. KONTRAKT POMOCNIKA KONTEKSTU SKRÓTU
# ============================================================================

class TestShortcutContextHelper:

    def _ctx(self, text, chat_type="private", reply=None):
        message = {"text": text, "chat": {"id": 1, "type": chat_type}}
        if reply is not None:
            message["reply_to_message"] = reply
        return lb._shortcut_without_argument_ctx(message)

    @pytest.mark.parametrize("text,ctx", [
        ("?d", lb.CTX_ONEOFF_DAY), (".d", lb.CTX_ONEOFF_DAY),
        ("?t", lb.CTX_ONEOFF_DAY), ("?j", lb.CTX_ONEOFF_DAY),
        ("?12", lb.CTX_ONEOFF_NOW), (".12", lb.CTX_ONEOFF_NOW),
        ("?n", lb.CTX_ONEOFF_NOW), ("?p", lb.CTX_ONEOFF_NOW),
        ("?14", lb.CTX_ONEOFF_FUTURE), (".14", lb.CTX_ONEOFF_FUTURE),
        ("?f", lb.CTX_ONEOFF_FUTURE),
    ])
    def test_private_shortcut_without_argument_maps_to_oneoff_context(self, text, ctx):
        assert self._ctx(text) == ctx

    @pytest.mark.parametrize("text", [
        "?d Warszawa", "?12 Warszawa", "?14 05-462 Wiązowna", "?dWarszawa",
        "@Bot ?d", "Cześć", "/dzien", "", "   ",
    ])
    def test_other_messages_have_no_context(self, text):
        assert self._ctx(text) is None

    @pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
    def test_groups_have_no_context(self, chat_type):
        assert self._ctx("?d", chat_type=chat_type) is None

    def test_reply_to_location_has_no_context(self):
        assert self._ctx("?d", reply={"location": {"latitude": 1, "longitude": 2}}) is None

    def test_helper_does_not_mutate_message(self):
        original = {"text": "?d", "chat": {"id": 1, "type": "private"}}
        snapshot = dict(original)

        assert lb._shortcut_without_argument_ctx(original) == lb.CTX_ONEOFF_DAY
        assert original == snapshot
        assert original["text"] == "?d"
