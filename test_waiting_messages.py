"""test_waiting_messages.py — komunikaty oczekiwania przed kartą (D13).

Zasada (ustalona z właścicielem produktu):
- zapisana lokalizacja (profil Users, komenda bez argumentu):
      /dzien  -> prep_main   -> karta
      /teraz  -> scanning    -> karta
      /trend  -> prep_future -> karta
  bez żadnego „Szukam lokalizacji” (nic nie jest geokodowane),
- karta jednorazowa (/dzien X, /teraz X, /trend X albo miasto wpisane po
  prompcie): WYŁĄCZNIE search_loc -> geokoder -> karta, bez drugiego
  komunikatu prep_*,
- profil Users po migracji bez lokalizacji: tylko prompt miasta, bez prep_future;
  z lokalizacją: prep_future -> karta.

Bez Telegram API i bez Google Sheets — atrapy z test_access_gate.

Uruchomienie: pytest test_waiting_messages.py -v
"""

import pytest

import i18n
import location_bot as lb
import main_card
from test_access_gate import (
    BLOCKED_LEGACY_ROW, LEGACY_ROW, BotHarness, freeze_local_hour,
)

PRIVATE = 4242
LEGACY_WITH_LOC = 700       # LEGACY_ROW: Gdańsk 54.35/18.65
LEGACY_NO_LOC = 702         # profil Users bez Lat/Lon (dostęp jest, karty nie ma)
LEGACY_NO_LOC_ROW = ["2026-01-01 08:00:00", str(LEGACY_NO_LOC), "NoLocLegacy", "",
                     "", "", "08:00", "14:00", "pl"]
LEGACY_NO_LOC_EN = 703      # jak wyżej, ale profil Users z językiem „en”
LEGACY_NO_LOC_EN_ROW = ["2026-01-01 08:00:00", str(LEGACY_NO_LOC_EN), "NoLocLegacyEn", "",
                        "", "", "08:00", "14:00", "en"]

WAIT_TEXT = {card: lb.t_ui("pl", key) for card, key in lb.SAVED_PROFILE_WAIT_KEYS.items()}
SEARCH_LOC = lb.t_ui("pl", "search_loc")
ALL_WAIT_TEXTS = set(WAIT_TEXT.values())


@pytest.fixture
def bot(monkeypatch):
    return BotHarness(monkeypatch,
                      seed_profiles=[LEGACY_ROW, BLOCKED_LEGACY_ROW,
                                      LEGACY_NO_LOC_ROW, LEGACY_NO_LOC_EN_ROW])


@pytest.fixture
def with_profile(bot):
    bot.run(bot.msg(PRIVATE, "/start BETAX1"))
    bot.run(bot.msg(PRIVATE, "/miasto"))
    bot.run(bot.msg(PRIVATE, "Warszawa"))
    assert bot.users.record(PRIVATE)["profile_status"] == "active"
    return len(bot.sent)   # indeks, od którego liczą się odpowiedzi testu


@pytest.fixture
def geocoder_log(bot, monkeypatch):
    """Rejestruje, ile wiadomości poszło do czatu ZANIM zapytano geokoder."""
    calls = []

    def fake_geocode(city, lang="pl"):
        calls.append({"city": city, "sent_before": list(bot.sent)})
        return (lb.GEOCODE_OK, 41.9028, 12.4964, city, f"{city}, Włochy")

    monkeypatch.setattr(lb, "geocode_city_accepted", fake_geocode)
    return calls


def test_wait_keys_cover_every_card_type_and_exist_in_every_language():
    assert lb.SAVED_PROFILE_WAIT_KEYS == {
        "day": "prep_main", "now": "scanning", "future": "prep_future",
    }
    for lang in ("pl", "en", "de", "es", "fr", "no"):
        for key in ("prep_main", "scanning", "prep_future", "search_loc"):
            text = i18n.UI_TEXTS[lang][key]
            assert text.strip() and "{" not in text and "`" not in text
            assert text.count("*") % 2 == 0
        # Trzy komunikaty oczekiwania są od siebie różne (inna karta = inny tekst).
        assert len({i18n.UI_TEXTS[lang][k] for k in ("prep_main", "scanning", "prep_future")}) == 3


# ============================================================================
# A. ZAPISANA LOKALIZACJA: prep_* -> karta, bez search_loc
# ============================================================================

class TestSavedProfile:

    @pytest.mark.parametrize("command,card", [("/dzien", "day"), ("/day", "day"),
                                              ("/teraz", "now"), ("/now", "now"),
                                              ("/trend", "future"), ("/future", "future")])
    def test_wait_message_then_card(self, bot, monkeypatch, with_profile, command, card):
        freeze_local_hour(monkeypatch, 10, 0)

        bot.run(bot.msg(PRIVATE, command))

        replies = bot.replies_since(PRIVATE, with_profile)
        assert replies == [WAIT_TEXT[card]], replies
        assert SEARCH_LOC not in replies
        assert [o[5] for o in bot.oneoffs] == [card]
        assert bot.oneoffs[0][0] == PRIVATE and bot.oneoffs[0][3] == "Warszawa"

    def test_wait_message_is_sent_before_the_card(self, bot, monkeypatch, with_profile):
        order = []
        monkeypatch.setattr(
            lb, "_send_oneoff_report",
            lambda chat_id, lat, lon, city, lang, card_type, display_location=None:
                (order.append(("card", list(bot.sent))), True)[1],
        )

        bot.run(bot.msg(PRIVATE, "/teraz"))

        assert len(order) == 1
        sent_before_card = [t for c, t in order[0][1][with_profile:] if c == PRIVATE]
        assert sent_before_card == [WAIT_TEXT["now"]]

    def test_day_window_block_has_no_wait_message(self, bot, monkeypatch, with_profile):
        """Poza oknem 05:00–15:59 /dzien nie udaje, że przygotowuje kartę."""
        freeze_local_hour(monkeypatch, 22, 0)

        bot.run(bot.msg(PRIVATE, "/dzien"))

        replies = bot.replies_since(PRIVATE, with_profile)
        assert len(replies) == 1 and "05:00" in replies[0]
        assert WAIT_TEXT["day"] not in replies
        assert bot.oneoffs == []

    def test_generation_error_comes_after_wait_message(self, bot, monkeypatch, with_profile):
        monkeypatch.setattr(lb, "_send_oneoff_report", lambda *a, **k: False)

        bot.run(bot.msg(PRIVATE, "/trend"))

        replies = bot.replies_since(PRIVATE, with_profile)
        assert replies[0] == WAIT_TEXT["future"]
        assert len(replies) == 2 and replies[1] == lb._oneoff_message("pl", "generation_error")

    def test_wait_message_uses_record_language(self, bot, monkeypatch):
        bot.run(bot.msg(PRIVATE, "/start BETAX1", lang="en"))
        bot.run(bot.msg(PRIVATE, "/city", lang="en"))
        bot.run(bot.msg(PRIVATE, "Warsaw", lang="en"))
        before = len(bot.sent)

        bot.run(bot.msg(PRIVATE, "/now", lang="en"))

        assert bot.replies_since(PRIVATE, before) == [lb.t_ui("en", "scanning")]


# ============================================================================
# B. BEZ PROFILU: prompt -> miasto -> search_loc -> geokoder -> karta
# ============================================================================

class TestPromptThenCity:

    @pytest.mark.parametrize("command,card", [("/dzien", "day"), ("/teraz", "now"),
                                              ("/trend", "future")])
    def test_search_loc_before_geocoder_and_no_prep(self, bot, monkeypatch, geocoder_log,
                                                    command, card):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(PRIVATE, "/start BETAX1"))
        bot.run(bot.msg(PRIVATE, command))
        prompt_idx = len(bot.sent)
        assert str(PRIVATE) in lb.PENDING_CITY

        bot.run(bot.msg(PRIVATE, "Rzym"))

        replies = bot.replies_since(PRIVATE, prompt_idx)
        assert replies[0] == SEARCH_LOC
        assert not ALL_WAIT_TEXTS & set(replies), "po geokodowaniu nie ma drugiego prep_*"
        # Geokoder został zapytany dopiero PO wysłaniu „Szukam lokalizacji”.
        assert len(geocoder_log) == 1 and geocoder_log[0]["city"] == "Rzym"
        sent_before = [t for c, t in geocoder_log[0]["sent_before"][prompt_idx:] if c == PRIVATE]
        assert sent_before == [SEARCH_LOC]
        assert [o[5] for o in bot.oneoffs] == [card]
        assert bot.oneoffs[0][3] == "Rzym"
        # Karta jednorazowa niczego nie zapisuje w profilu.
        assert bot.users.record(PRIVATE)["profile_status"] == "none"


# ============================================================================
# C. MIASTO W KOMENDZIE PRZY ZAPISANYM PROFILU: search_loc, profil nietknięty
# ============================================================================

class TestOneOffWithProfile:

    @pytest.mark.parametrize("command,card", [("/dzien Rzym", "day"), ("/teraz Rzym", "now"),
                                              ("/trend Rzym", "future")])
    def test_only_search_loc_then_card(self, bot, monkeypatch, with_profile, geocoder_log,
                                       command, card):
        freeze_local_hour(monkeypatch, 10, 0)
        record_before = bot.users.record(PRIVATE)

        bot.run(bot.msg(PRIVATE, command))

        replies = bot.replies_since(PRIVATE, with_profile)
        assert replies[0] == SEARCH_LOC
        assert not ALL_WAIT_TEXTS & set(replies), "jednorazowa karta: bez prep_*"
        assert not any("Użyta lokalizacja" in r for r in replies), replies
        assert bot.oneoff_descriptions[-1]
        assert [o[5] for o in bot.oneoffs] == [card]
        assert bot.oneoffs[0][3] == "Rzym" and bot.oneoffs[0][1] == 41.9028
        assert bot.users.record(PRIVATE) == record_before, "profil Users bez zmian"

    def test_too_short_query_sends_neither_search_loc_nor_prep(self, bot, with_profile,
                                                               geocoder_log):
        bot.run(bot.msg(PRIVATE, "/teraz Xy"))

        replies = bot.replies_since(PRIVATE, with_profile)
        assert replies == [lb.t_geocode("pl", lb.GEOCODE_TOO_SHORT)]
        assert geocoder_log == [] and bot.oneoffs == []

    def test_not_found_after_search_loc_has_no_card(self, bot, monkeypatch, with_profile):
        monkeypatch.setattr(
            lb, "geocode_city_accepted",
            lambda city, lang="pl": (lb.GEOCODE_NOT_FOUND, None, None, None, None),
        )

        bot.run(bot.msg(PRIVATE, "/teraz Xyzzz"))

        replies = bot.replies_since(PRIVATE, with_profile)
        assert replies == [SEARCH_LOC, lb.t_ui("pl", "search_fail")]
        assert bot.oneoffs == []


# ============================================================================
# D. LEGACY /trend: missing_loc bez prep_future; z lokalizacją prep_future -> karta
# ============================================================================

class TestMigratedProfiles:
    def test_missing_location_opens_prompt(self, bot):
        bot.run(bot.msg(LEGACY_NO_LOC, "/trend"))
        assert bot.replies(LEGACY_NO_LOC) == [lb.t_ui("pl", "oneoff_prompt_future")]
        assert bot.oneoffs == []

    def test_missing_location_prompt_is_translated(self, bot):
        bot.run(bot.msg(LEGACY_NO_LOC_EN, "/trend"))
        assert bot.replies(LEGACY_NO_LOC_EN) == [lb.t_ui("en", "oneoff_prompt_future")]
        assert bot.oneoffs == []

    def test_location_present_gives_prep_future_then_card(self, bot):
        bot.run(bot.msg(LEGACY_WITH_LOC, "/trend"))
        assert bot.replies(LEGACY_WITH_LOC) == [WAIT_TEXT["future"]]
        assert [(o[0], o[5]) for o in bot.oneoffs] == [(LEGACY_WITH_LOC, "future")]

    def test_day_and_now_wait_messages_unchanged(self, bot, monkeypatch):
        freeze_local_hour(monkeypatch, 10, 0)
        bot.run(bot.msg(LEGACY_WITH_LOC, "/day"))
        bot.run(bot.msg(LEGACY_WITH_LOC, "/now"))
        assert bot.replies(LEGACY_WITH_LOC) == [WAIT_TEXT["day"], WAIT_TEXT["now"]]
        assert [o[5] for o in bot.oneoffs] == ["day", "now"]
