"""test_shortcut_search_loc.py — search_loc dla skrótów Z ARGUMENTEM.

Zasada produktowa:

    Rozpoznany skrót z niepustym argumentem miejscowości wysyła DOKŁADNIE
    JEDEN istniejący komunikat ``search_loc`` — PRZED wyszukiwaniem lokalizacji
    (cache albo geokoder) i przed generowaniem karty. Ten sam komunikat, tę samą
    kolejność i ten sam język mają już /teraz Warszawa i miasto wpisane po
    prompcie.

Granice (świadomie NIEobjęte zmianą):

- skrót BEZ argumentu nadal tylko otwiera prompt o miejscowość,
- pinezka z odpowiedzi nie wysyła search_loc (nie ma tekstowego wyszukiwania),
- sama wzmianka @bot bez skrótu zachowuje się jak dotąd,
- TOO_SHORT dostaje dotychczasowy komunikat walidacji, bez pozorowania
  wyszukiwania (czyli bez search_loc),
- brak dostępu to nadal cisza.

Uruchomienie: pytest test_shortcut_search_loc.py -v
"""

import pytest

import guest_bot_handler as gbh
import i18n
import location_bot as lb
from test_access_gate import (
    LEGACY_ROW, BotHarness, freeze_local_hour,
)

PRIVATE = 4400
NO_PROFILE = 4401
STRANGER = 4402
GROUP = -100440
GROUP_ADMIN = 940
LEGACY_WITH_LOC = 700           # LEGACY_ROW: Gdańsk 54.35/18.65, lang=pl

LEGACY_NO_LOC = 704
LEGACY_NO_LOC_ROW = ["2026-01-01 08:00:00", str(LEGACY_NO_LOC), "NoLocLegacy", "",
                     "", "", "08:00", "14:00", "pl"]

SAVED_CITY = "Warszawa"          # stała lokalizacja (profil)
QUERY_SHORT = "Warszawa"         # miejscowość wpisana po skrócie
QUERY_FULL = "Warszawa, Polska"
QUERY_LAT, QUERY_LON = 52.2297, 21.0122

ALL_SHORTCUT_KEYS = tuple(gbh.iter_shortcut_keys())

# Formy zlepione (historyczne, rozpoznawane przez resolve_shortcut).
GLUED_CASES = ("?dWarszawa", ".dWarszawa", "?12Warszawa", ".12Warszawa",
               "?14Warszawa", ".14Warszawa", "?nWarszawa", ".fWarszawa")



def _search_loc(lang="pl"):
    return lb.t_ui(lang, "search_loc")


# ============================================================================
# FIXTURES: prawdziwy routing + prawdziwy guest handler, sieć wycięta
# ============================================================================

class Geocoder:
    """Atrapa geokodera forward: rejestruje miasta, język i stan czatu."""

    def __init__(self, bot):
        self.bot = bot
        self.calls = []
        self.status = lb.GEOCODE_OK
        self.lat = QUERY_LAT
        self.lon = QUERY_LON
        self.short = QUERY_SHORT
        self.display = QUERY_FULL
        # Miasto -> inny status (dla dwóch prób geokodowania).
        self.status_by_city = {}

    def __call__(self, city, lang="pl"):
        self.calls.append({
            "city": city,
            "lang": lang,
            "sent_before": list(self.bot.sent),
        })
        status = self.status_by_city.get(city, self.status)
        if status != lb.GEOCODE_OK:
            return status, None, None, None, None
        return (lb.GEOCODE_OK, self.lat, self.lon,
                self.short, self.display)


class CardRecorder:
    def __init__(self):
        self.layouts = []
        self.photos = []
        self.payloads = []


@pytest.fixture
def bot(monkeypatch):
    """Harness z PRAWDZIWYM guest_bot_handler pod cienkim spy."""
    harness = BotHarness(monkeypatch,
                         seed_profiles=[LEGACY_ROW, LEGACY_NO_LOC_ROW])
    real_calls = []

    def real_guest_handler(**kwargs):
        real_calls.append(kwargs)
        return gbh.handle_guest_now(**kwargs)

    monkeypatch.setattr(lb, "handle_guest_now", real_guest_handler)
    harness.real_guest_calls = real_calls

    harness.form_writes = harness.gc.formularz.operations

    # Cache gościa jest globalny — każdy test zaczyna od czystego stanu.
    monkeypatch.setattr(gbh, "_GEO_CACHE", {})
    monkeypatch.setattr(gbh, "_CITY_CACHE", {})
    return harness


@pytest.fixture
def order(monkeypatch):
    """Wspólna lista zdarzeń: komunikat -> geokoder -> resolver -> karta."""
    events = []
    real_send_reply = lb.send_reply

    def spy_send_reply(chat_id, text, reply_markup=None):
        events.append(("message", text))
        return real_send_reply(chat_id, text, reply_markup)

    monkeypatch.setattr(lb, "send_reply", spy_send_reply)
    return events


@pytest.fixture
def geocoder(bot, monkeypatch, order):
    fake = Geocoder(bot)

    def spy(city, lang="pl"):
        order.append(("geocoder", city))
        return fake(city, lang)

    monkeypatch.setattr(lb, "geocode_city_accepted", spy)
    return fake


@pytest.fixture
def guest_card(monkeypatch, order):
    rec = CardRecorder()

    def build_payload(**kwargs):
        order.append(("generate", kwargs))
        rec.payloads.append(kwargs)
        return {"location": {"tz": "UTC"}, "request": kwargs}

    monkeypatch.setattr(lb, "build_payload_for_location", build_payload)
    monkeypatch.setattr(
        lb, "prepare_layout_data",
        lambda payload: rec.layouts.append("day") or {},
    )
    monkeypatch.setattr(
        lb, "prepare_now_layout_data",
        lambda payload: rec.layouts.append("now") or {},
    )
    monkeypatch.setattr(
        lb, "prepare_future_layout_data",
        lambda payload: rec.layouts.append("future") or {},
    )
    monkeypatch.setattr(
        lb.image_generator, "generate_weather_card", lambda layout: "/tmp/karta.png",
    )

    def fake_send_photo(chat_id, path, caption=None, parse_mode=None, card_caption=True):
        order.append(("photo", chat_id))
        rec.photos.append((chat_id, caption))

    monkeypatch.setattr(lb, "send_photo", fake_send_photo)
    return rec


@pytest.fixture
def resolver(bot, monkeypatch, geocoder, order):
    """Resolver etykiet — ta sama ścieżka co /miasto, bez reverse-geokodera."""
    def fake_resolve(lat, lon, lang, fallback_short_label=None,
                     fallback_display_location=None, query=None):
        order.append(("resolver", lang))
        return (fallback_short_label or geocoder.short,
                fallback_display_location or geocoder.display,
                lb.GEO_OK)

    monkeypatch.setattr(lb, "_resolve_location_labels", fake_resolve)
    return fake_resolve


def _messages_since(bot, chat_id, index):
    return [text for cid, text in bot.sent[index:] if cid == chat_id]


# ============================================================================
# 1. DOKŁADNIE JEDEN search_loc — WSZYSTKIE ALIASY PARSERA
# ============================================================================

@pytest.mark.parametrize("shortcut", ALL_SHORTCUT_KEYS)
def test_every_recognized_alias_sends_exactly_one_search_loc(
    bot, monkeypatch, guest_card, geocoder, resolver, shortcut
):
    freeze_local_hour(monkeypatch, 10, 0)
    bot.run(bot.msg(PRIVATE, "/start BETAX1"))
    before = len(bot.sent)

    bot.run(bot.msg(PRIVATE, f"{shortcut} {QUERY_SHORT}"))

    assert _messages_since(bot, PRIVATE, before) == [_search_loc("pl")], shortcut
    assert gbh.resolve_shortcut(f"{shortcut} {QUERY_SHORT}")[0] == shortcut
    assert guest_card.photos and guest_card.photos[0][0] == PRIVATE
    # Komunikat na pewno przed geokoderem.
    sent_before_geocode = [t for c, t in geocoder.calls[0]["sent_before"]
                           if c == PRIVATE]
    assert sent_before_geocode[-1] == _search_loc("pl")


@pytest.mark.parametrize("text", GLUED_CASES)
def test_glued_historical_aliases_send_exactly_one_search_loc(
    bot, monkeypatch, guest_card, geocoder, resolver, text
):
    freeze_local_hour(monkeypatch, 10, 0)
    bot.run(bot.msg(PRIVATE, "/start BETAX1"))
    before = len(bot.sent)

    bot.run(bot.msg(PRIVATE, text))

    assert _messages_since(bot, PRIVATE, before) == [_search_loc("pl")], text
    assert guest_card.photos, text
    assert geocoder.calls and geocoder.calls[0]["city"] == QUERY_SHORT


@pytest.mark.parametrize("card_type,command", [
    ("day", "/dzien Warszawa"),
    ("now", "/teraz Warszawa"),
    ("future", "/trend Warszawa"),
])
def test_message_text_and_count_match_the_command_path(
    bot, monkeypatch, guest_card, geocoder, resolver, order, card_type, command
):
    """Ten sam search_loc i ta sama kolejność co komendy z miastem."""
    freeze_local_hour(monkeypatch, 10, 0)
    bot.run(bot.msg(PRIVATE, "/start BETAX1"))
    shortcut = {"day": "?d", "now": "?12", "future": "?14"}[card_type]

    bot.run(bot.msg(PRIVATE, command))
    command_messages = [t for t in _messages_since(bot, PRIVATE, 0)
                        if t == _search_loc("pl")]

    before = len(bot.sent)
    order.clear()
    bot.run(bot.msg(PRIVATE, f"{shortcut} {QUERY_SHORT}"))

    assert _messages_since(bot, PRIVATE, before) == command_messages == [_search_loc("pl")]
    assert [kind for kind, _ in order] == ["message", "geocoder", "resolver",
                                           "generate", "photo"]


# ============================================================================
# 2. KOLEJNOŚĆ: KOMUNIKAT -> GEOKODER/RESOLVER -> GENEROWANIE -> ZDJĘCIE
# ============================================================================

def test_order_message_then_geocoder_then_resolver_then_card(
    bot, monkeypatch, guest_card, geocoder, resolver, order
):
    freeze_local_hour(monkeypatch, 10, 0)
    bot.run(bot.msg(PRIVATE, "/start BETAX1"))
    order.clear()

    bot.run(bot.msg(PRIVATE, "?12 Warszawa"))

    assert [kind for kind, _ in order] == ["message", "geocoder", "resolver",
                                           "generate", "photo"]
    assert order[0] == ("message", _search_loc("pl"))
    assert order[1][0] == "geocoder"
    assert guest_card.photos and guest_card.photos[0][0] == PRIVATE


# ============================================================================
# 3. CACHE: DOKŁADNIE JEDEN KOMUNIKAT NA ŻĄDANIE, TAKŻE PRZY TRAFIENIU
# ============================================================================

def test_cache_hit_keeps_one_search_loc_and_reuses_geocode(
    bot, monkeypatch, guest_card, geocoder, resolver
):
    freeze_local_hour(monkeypatch, 10, 0)
    bot.run(bot.msg(PRIVATE, "/start BETAX1"))

    before_first = len(bot.sent)
    bot.run(bot.msg(PRIVATE, "?12 Warszawa"))
    first = _messages_since(bot, PRIVATE, before_first)

    before_second = len(bot.sent)
    bot.run(bot.msg(PRIVATE, "?12 Warszawa"))
    second = _messages_since(bot, PRIVATE, before_second)

    assert first == [_search_loc("pl")]
    assert second == [_search_loc("pl")], "trafienie w cache też ma dokładnie jeden komunikat"
    assert len(geocoder.calls) == 1, "drugie żądanie nie pyta geokodera"
    assert len(guest_card.photos) == 2, "karta powstaje z cache w obu żądaniach"
    assert list(gbh._GEO_CACHE) == [("pl", "warszawa")]


# ============================================================================
# 4. DWIE PRÓBY GEOKODOWANIA: KOMUNIKAT NIE MOŻE SIĘ PODWOIĆ
# ============================================================================

def test_two_geocode_attempts_never_duplicate_search_loc(
    bot, monkeypatch, guest_card, geocoder, resolver
):
    freeze_local_hour(monkeypatch, 10, 0)
    bot.run(bot.msg(PRIVATE, "/start BETAX1"))
    geocoder.status_by_city = {
        "Warszawa Centrum Extra": lb.GEOCODE_NO_MATCH,
        "Warszawa Centrum": lb.GEOCODE_OK,
    }
    before = len(bot.sent)

    bot.run(bot.msg(PRIVATE, "?12 Warszawa Centrum Extra"))

    assert [c["city"] for c in geocoder.calls] == [
        "Warszawa Centrum Extra", "Warszawa Centrum",
    ]
    assert _messages_since(bot, PRIVATE, before) == [_search_loc("pl")]
    assert guest_card.photos, "skrócona, zaakceptowana nazwa daje kartę"


# ============================================================================
# 5. JĘZYK: effective_lang MA PIERWSZEŃSTWO NAD JĘZYKIEM TELEGRAMA
# ============================================================================

def test_effective_lang_beats_telegram_language(bot, monkeypatch, guest_card,
                                                geocoder, resolver):
    """Users.lang=pl, Telegram de -> komunikat i karta po polsku."""
    freeze_local_hour(monkeypatch, 10, 0)
    bot.run(bot.msg(PRIVATE, "/start BETAX1", lang="pl"))
    assert bot.users.record(PRIVATE)["lang"] == "pl"
    before = len(bot.sent)

    bot.run(bot.msg(PRIVATE, "?12 Warszawa", lang="de"))

    assert _messages_since(bot, PRIVATE, before) == [_search_loc("pl")]
    assert _search_loc("de") not in _messages_since(bot, PRIVATE, before)
    assert geocoder.calls[0]["lang"] == "pl"


def test_migrated_users_language_drives_the_message(
    bot, monkeypatch, guest_card, geocoder, resolver
):
    """Users.lang (pl) wygrywa z językiem Telegrama (de)."""
    freeze_local_hour(monkeypatch, 10, 0)
    before = len(bot.sent)

    bot.run(bot.msg(LEGACY_WITH_LOC, "?12 Warszawa", lang="de"))

    assert bot.users.record(LEGACY_WITH_LOC)["profile_status"] == "active"
    assert _messages_since(bot, LEGACY_WITH_LOC, before) == [_search_loc("pl")]
    assert bot.form_writes == []
    assert guest_card.photos


@pytest.mark.parametrize("lang", ["pl", "en", "de", "fr", "es", "no"])
def test_direct_handler_uses_telegram_language_without_override(lang):
    """Brak effective_lang: zostaje dotychczasowy fallback z Telegrama."""
    sent, photos = [], []

    handled = gbh.handle_guest_now(
        message={"text": "?12 Hel", "chat": {"id": 555, "type": "private"},
                 "from": {"language_code": lang}},
        bot_username="PogodaWorldBot",
        get_coords_fn=lambda city, l="pl": (None, None, None),
        build_payload_fn=lambda **kw: {"location": {"tz": "UTC"}},
        prepare_layout_fn=lambda payload, card_type: {},
        render_png_fn=lambda layout: "/tmp/karta.png",
        send_photo_fn=lambda chat_id, path, city, f_address: photos.append(city),
        send_reply_fn=lambda chat_id, text: sent.append(text),
        geocode_status_fn=None,
        too_short_fn=lb._geocode_query_is_too_short,
    )

    assert handled is True
    assert sent == [
        _search_loc(lang),
        i18n.t_geocode(lang, lb.GEOCODE_NOT_FOUND),
    ], "komunikat w języku Telegrama, potem dotychczasowy status"
    assert photos == [], "brak współrzędnych = brak karty, ale komunikat został"


# ============================================================================
# 6. PROFIL / BEZ PROFILU / USERS / LEGACY / GRUPA Z DOSTĘPEM
# ============================================================================

@pytest.fixture
def with_profile(bot, monkeypatch):
    bot.run(bot.msg(PRIVATE, "/start BETAX1"))
    bot.run(bot.msg(PRIVATE, "/miasto"))
    bot.run(bot.msg(PRIVATE, SAVED_CITY))
    assert bot.users.record(PRIVATE)["profile_status"] == "active"
    return len(bot.sent)


def test_with_profile_message_is_sent_and_profile_is_untouched(
    bot, monkeypatch, guest_card, geocoder, resolver, with_profile
):
    freeze_local_hour(monkeypatch, 10, 0)
    record_before = bot.users.record(PRIVATE)
    form_before = list(bot.form_writes)

    bot.run(bot.msg(PRIVATE, "?14 Kraków"))

    assert _messages_since(bot, PRIVATE, with_profile) == [_search_loc("pl")]
    assert guest_card.layouts == ["future"]
    assert guest_card.payloads[0]["lat"] == QUERY_LAT
    assert guest_card.payloads[0]["lon"] == QUERY_LON
    assert bot.users.record(PRIVATE) == record_before
    assert bot.form_writes == form_before
    assert bot.oneoffs == []


def test_without_profile_message_is_sent_and_nothing_is_saved(
    bot, monkeypatch, guest_card, geocoder, resolver
):
    freeze_local_hour(monkeypatch, 10, 0)
    bot.run(bot.msg(NO_PROFILE, "/start BETAX1"))
    before = len(bot.sent)

    bot.run(bot.msg(NO_PROFILE, "?d Warszawa"))

    record = bot.users.record(NO_PROFILE)
    assert _messages_since(bot, NO_PROFILE, before) == [_search_loc("pl")]
    assert guest_card.layouts == ["day"]
    assert record["profile_status"] == "none"
    assert record["lat_round"] == "" and record["lon_round"] == ""
    assert record["location_label"] == ""
    assert bot.form_writes == []
    assert bot.oneoffs == []


def test_migrated_user_gets_message_and_no_formularz_write(
    bot, monkeypatch, guest_card, geocoder, resolver
):
    freeze_local_hour(monkeypatch, 10, 0)
    before = len(bot.sent)

    bot.run(bot.msg(LEGACY_WITH_LOC, "?12 Gdańsk"))

    assert _messages_since(bot, LEGACY_WITH_LOC, before) == [_search_loc("pl")]
    assert guest_card.photos and guest_card.photos[0][0] == LEGACY_WITH_LOC
    assert bot.users.record(LEGACY_WITH_LOC)["profile_status"] == "active"
    assert bot.form_writes == []


def test_authorized_group_receives_exactly_one_message(
    bot, monkeypatch, guest_card, geocoder, resolver
):
    freeze_local_hour(monkeypatch, 10, 0)
    bot.run(bot.msg(GROUP, "/start BETAX1", user_id=GROUP_ADMIN))
    before = len(bot.sent)

    bot.run(bot.msg(GROUP, "?12 Warszawa", user_id=GROUP_ADMIN, chat_type="group"))

    assert _messages_since(bot, GROUP, before) == [_search_loc("pl")]
    assert guest_card.photos and guest_card.photos[0][0] == GROUP


# ============================================================================
# 7. BRAK DOSTĘPU: CISZA (TAKŻE BEZ search_loc)
# ============================================================================

@pytest.mark.parametrize("text", ["?d Warszawa", "?12 Warszawa", "?14 Warszawa",
                                  ".n Warszawa"])
def test_no_access_is_silent(bot, text):
    bot.run(bot.msg(STRANGER, text))

    assert bot.replies(STRANGER) == []
    assert bot.real_guest_calls == [], "guard P7 wycisza PRZED guest handlerem"
    assert _search_loc("pl") not in [t for _c, t in bot.sent]


# ============================================================================
# 8. SKRÓT BEZ ARGUMENTU: TYLKO DOTYCHCZASOWY PROMPT
# ============================================================================

@pytest.mark.parametrize("shortcut,prompt_key", [
    ("?d", "oneoff_prompt_day"),
    ("?12", "oneoff_prompt_now"),
    ("?14", "oneoff_prompt_future"),
])
def test_shortcut_without_argument_only_opens_prompt(
    bot, monkeypatch, shortcut, prompt_key
):
    freeze_local_hour(monkeypatch, 10, 0)
    bot.run(bot.msg(NO_PROFILE, "/start BETAX1"))
    before = len(bot.sent)

    bot.run(bot.msg(NO_PROFILE, shortcut))

    assert _messages_since(bot, NO_PROFILE, before) == [
        lb.t_ui("pl", prompt_key)
    ], "bez argumentu nie ma search_loc przed wpisaniem miasta"
    assert str(NO_PROFILE) in lb.PENDING_CITY


# ============================================================================
# 9. PINEZKA I SAMA WZMIANKA @bot — BEZ search_loc
# ============================================================================

def test_pin_reply_does_not_send_search_loc(bot, monkeypatch, guest_card, resolver):
    freeze_local_hour(monkeypatch, 10, 0)
    bot.run(bot.msg(PRIVATE, "/start BETAX1"))
    update = bot.msg(PRIVATE, "?d")
    update["message"]["reply_to_message"] = {
        "message_id": 5,
        "location": {"latitude": 54.5, "longitude": 18.5},
    }
    before = len(bot.sent)

    bot.run(update)

    assert _messages_since(bot, PRIVATE, before) == []
    assert guest_card.layouts == ["day"]
    assert guest_card.payloads[0]["lat"] == 54.5


def test_plain_mention_without_shortcut_does_not_send_search_loc(
    bot, monkeypatch, guest_card, geocoder, resolver
):
    freeze_local_hour(monkeypatch, 10, 0)
    bot.run(bot.msg(PRIVATE, "/start BETAX1"))
    mention = f"@{lb.BOT_USERNAME}"
    before = len(bot.sent)

    bot.run(bot.msg(PRIVATE, f"{mention} Warszawa"))

    assert _messages_since(bot, PRIVATE, before) == []
    assert guest_card.photos, "wzmianka nadal generuje kartę"


# ============================================================================
# 10. STATUSY GEOKODERA
# ============================================================================

def test_too_short_gets_validation_message_without_search_loc(
    bot, monkeypatch, guest_card, geocoder, resolver
):
    freeze_local_hour(monkeypatch, 10, 0)
    bot.run(bot.msg(PRIVATE, "/start BETAX1"))
    geocoder.status = lb.GEOCODE_TOO_SHORT
    before = len(bot.sent)

    bot.run(bot.msg(PRIVATE, "?12 Wa"))

    replies = _messages_since(bot, PRIVATE, before)
    assert replies == [lb.t_geocode("pl", lb.GEOCODE_TOO_SHORT)]
    assert _search_loc("pl") not in replies, "TOO_SHORT nie pozoruje wyszukiwania"
    # Adapter walidacji widzi zapytanie (jak dotąd), ale bez drugiej próby.
    assert [c["city"] for c in geocoder.calls] == ["Wa"]
    assert guest_card.photos == [] and guest_card.layouts == []


@pytest.mark.parametrize("status", [
    lb.GEOCODE_NOT_FOUND, lb.GEOCODE_NO_MATCH, lb.GEOCODE_UNCERTAIN,
    lb.GEOCODE_ERROR,
])
def test_every_geocoder_failure_has_one_message_then_status(
    bot, monkeypatch, guest_card, geocoder, resolver, status
):
    freeze_local_hour(monkeypatch, 10, 0)
    bot.run(bot.msg(PRIVATE, "/start BETAX1"))
    geocoder.status = status
    before = len(bot.sent)

    bot.run(bot.msg(PRIVATE, "?12 Warszawa"))

    replies = _messages_since(bot, PRIVATE, before)
    assert replies == [_search_loc("pl"), lb.t_geocode("pl", status)]
    assert len(replies) == 2
    assert geocoder.calls and geocoder.calls[0]["city"] == "Warszawa"
    assert guest_card.photos == [] and guest_card.layouts == []
    assert gbh._GEO_CACHE == {}, "status błędu nie trafia do cache"


def test_group_failure_keeps_only_search_loc(bot, monkeypatch, guest_card,
                                             geocoder, resolver):
    freeze_local_hour(monkeypatch, 10, 0)
    bot.run(bot.msg(GROUP, "/start BETAX1", user_id=GROUP_ADMIN))
    geocoder.status = lb.GEOCODE_NOT_FOUND
    before = len(bot.sent)

    bot.run(bot.msg(GROUP, "?12 Warszawa", user_id=GROUP_ADMIN, chat_type="group"))

    assert _messages_since(bot, GROUP, before) == [_search_loc("pl")]
    assert guest_card.photos == []


# ============================================================================
# 11. CAPTION I HERO BEZ ZMIAN — OPIS TYLKO POD KARTĄ
# ============================================================================

def test_caption_is_still_only_the_description(bot, monkeypatch, guest_card,
                                               geocoder, resolver):
    freeze_local_hour(monkeypatch, 10, 0)
    bot.run(bot.msg(PRIVATE, "/start BETAX1"))
    geocoder.display = "Warszawa, województwo mazowieckie, Polska"

    bot.run(bot.msg(PRIVATE, "?12 Warszawa"))

    assert guest_card.photos[0][1] == lb._guest_location_caption(geocoder.display)
    assert guest_card.payloads[0]["location_name"] == QUERY_SHORT
    all_texts = [t for _c, t in bot.sent]
    assert not any("Użyta lokalizacja" in t for t in all_texts)
    assert geocoder.display not in all_texts, "opis wyłącznie w captionie zdjęcia"
