"""Photo captions: one-off descriptions, bare saved profiles and guest HTML."""

import pytest
from PIL import Image

import location_bot as lb

CHAT_ID = 424242
CITY = "Wiązowna"


# ============================================================================
# ATRAPY: sieć + pipeline danych (zero ruchu na zewnątrz)
# ============================================================================

@pytest.fixture
def fake_png(tmp_path):
    """Minimalny poprawny PNG — treść grafiki nie gra tu roli, liczy się wysyłka."""
    path = tmp_path / "raport_telegram.png"
    Image.new("RGB", (16, 16), (41, 57, 104)).save(path)
    return str(path)


@pytest.fixture
def tg_posts(monkeypatch, fake_png):
    """Przechwytuje POST-y do Telegram API i wycina cały pipeline pogodowy."""
    posts = []

    class _OkResp:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "result": {}}

    def fake_post(url, data=None, files=None, timeout=None, json=None, **kwargs):
        # sendMessage (komunikat „Przygotowuję…” przed kartą) leci jako json=,
        # sendPhoto jako data= — zbieramy oba, żeby policzyć wysyłki.
        posts.append({"url": url, "data": dict(data or json or {})})
        return _OkResp()

    monkeypatch.setattr(lb.requests, "post", fake_post)
    monkeypatch.setattr(lb, "build_payload_for_location",
                        lambda **kwargs: {"location": {}, "hourly": []})
    monkeypatch.setattr(lb, "_resolve_tz", lambda lat, lon: "Europe/Warsaw")
    monkeypatch.setattr(lb, "prepare_layout_data", lambda payload: {"card": "day"})
    monkeypatch.setattr(lb, "prepare_now_layout_data", lambda payload: {"card": "now"})
    monkeypatch.setattr(lb, "prepare_future_layout_data", lambda payload: {"card": "future"})
    monkeypatch.setattr(lb.image_generator, "generate_weather_card", lambda layout: fake_png)
    return posts


def _photo_posts(posts):
    return [p for p in posts if p["url"].endswith("/sendPhoto")]


# ============================================================================
# 1. ŚCIEŻKA USERS: /day, /now, /future -> sama karta
# ============================================================================

@pytest.mark.parametrize("card_type", ["day", "now", "future"])
def test_users_profile_card_has_no_caption(tg_posts, card_type):
    """Profil zapisany w arkuszu Users: karta leci jako SAM obrazek."""
    profile = {"lat": 52.15, "lon": 21.29, "city": CITY}

    assert lb._run_saved_profile_report(CHAT_ID, profile, "pl", card_type) is True

    photos = _photo_posts(tg_posts)
    assert len(photos) == 1, "karta powinna polecieć dokładnie raz"
    data = photos[0]["data"]
    assert "caption" not in data, f"karta {card_type} nie może mieć podpisu z lokalizacją"
    assert "parse_mode" not in data, "bez captionu parse_mode jest zbędne w payloadzie"
    # Przed kartą leci wyłącznie komunikat oczekiwania (prep_main / scanning /
    # prep_future) — nic po karcie i żadnego „Szukam lokalizacji”.
    messages = [p for p in tg_posts if p["url"].endswith("/sendMessage")]
    assert len(messages) == 1 and tg_posts.index(messages[0]) < tg_posts.index(photos[0])
    assert messages[0]["data"]["text"] == lb.t_ui("pl", lb.SAVED_PROFILE_WAIT_KEYS[card_type])


@pytest.mark.parametrize("card_type", ["day", "now", "future"])
def test_oneoff_without_description_has_no_city_fallback(tg_posts, card_type):
    """Without display_location even a known city must not become a caption."""
    assert lb._send_oneoff_report(CHAT_ID, 52.15, 21.29, CITY, "pl", card_type) is True

    photos = _photo_posts(tg_posts)
    assert len(photos) == 1
    assert "caption" not in photos[0]["data"]


@pytest.mark.parametrize("card_type", ["day", "now", "future"])
def test_long_display_location_never_becomes_card_location_name(monkeypatch, card_type):
    """Długi opis jest tylko tekstem pod kartą; payload/layout dostają short_label."""
    display_location = (
        "Wiązowna, gmina Wiązowna, powiat otwocki, województwo mazowieckie, "
        "05-462, Polska"
    )
    payload_calls = []
    layout_calls = []
    replies = []
    photos = []

    def build_payload(**kwargs):
        payload_calls.append(kwargs)
        return {"location_name": kwargs["location_name"], "hourly": []}

    def prepare_layout(payload):
        layout_calls.append(payload)
        return payload

    monkeypatch.setattr(lb, "build_payload_for_location", build_payload)
    monkeypatch.setattr(lb, "_resolve_tz", lambda lat, lon: "Europe/Warsaw")
    monkeypatch.setattr(lb, "prepare_layout_data", prepare_layout)
    monkeypatch.setattr(lb, "prepare_now_layout_data", prepare_layout)
    monkeypatch.setattr(lb, "prepare_future_layout_data", prepare_layout)
    monkeypatch.setattr(lb.image_generator, "generate_weather_card", lambda layout: "card.png")
    monkeypatch.setattr(lb, "send_photo", lambda *args, **kwargs: photos.append(kwargs))
    monkeypatch.setattr(lb, "send_reply", lambda chat_id, text, **kwargs: replies.append(text))
    lb.PENDING_SAVE.clear()

    assert lb._run_oneoff_report(
        CHAT_ID, 52.123456, 21.987654, CITY, "pl", "city", card_type,
        display_location=display_location,
    ) is True

    assert payload_calls[0]["location_name"] == CITY
    assert payload_calls[0]["lat"] == 52.123456
    assert payload_calls[0]["lon"] == 21.987654
    assert layout_calls[0]["location_name"] == CITY
    assert display_location not in str(payload_calls[0])
    assert display_location not in str(layout_calls[0])
    assert replies == []
    assert photos == [{"caption": f"_{display_location}_"}]
    lb.PENDING_SAVE.clear()


def test_used_location_note_is_not_sent_after_city_card(monkeypatch):
    """No extra sendMessage after the photo."""
    sent = []
    monkeypatch.setattr(lb, "_send_oneoff_report", lambda *a, **kw: True)
    monkeypatch.setattr(lb, "send_reply", lambda chat_id, text, **kw: sent.append(text))
    lb.PENDING_SAVE.clear()

    ok = lb._run_oneoff_report(
        CHAT_ID, 52.15, 21.29, CITY, "pl", "city", "day",
        display_location=f"{CITY}, Polska",
    )

    assert ok is True
    assert sent == []


# ============================================================================
# 2. WARSTWA WYSYŁKI: send_photo
# ============================================================================

def test_send_photo_keeps_caption_for_guest_path(tg_posts, fake_png):
    """The transport preserves the supplied HTML description."""
    caption = f"<i>{CITY}, Polska</i>"

    lb.send_photo(CHAT_ID, fake_png, caption=caption, parse_mode="HTML")

    data = tg_posts[-1]["data"]
    assert data["caption"] == caption
    assert data["parse_mode"] == "HTML"
    assert str(data["chat_id"]) == str(CHAT_ID)


def test_card_caption_false_blocks_caption(tg_posts, fake_png):
    """`card_caption=False` wygrywa nawet z jawnie podanym captionem (bezpiecznik)."""
    lb.send_photo(CHAT_ID, fake_png, caption=f"<b>{CITY}</b>", parse_mode="HTML",
                  card_caption=False)

    data = tg_posts[-1]["data"]
    assert "caption" not in data
    assert "parse_mode" not in data


def test_send_photo_without_caption_sends_bare_card(tg_posts, fake_png):
    """Karta bez captionu: w payloadzie zostaje wyłącznie chat_id."""
    lb.send_photo(CHAT_ID, fake_png)

    data = tg_posts[-1]["data"]
    assert set(data.keys()) == {"chat_id"}


FULL_LOCATION = "Warszawa, województwo mazowieckie, Polska"
REAL_SEND_ONEOFF = lb._send_oneoff_report


@pytest.mark.parametrize("card_type", ["day", "now", "future"])
def test_oneoff_description_is_only_in_photo_caption(tg_posts, card_type):
    assert lb._run_oneoff_report(
        CHAT_ID, 52.15, 21.29, "Warszawa", "pl", "city", card_type,
        display_location=FULL_LOCATION,
    )
    assert len(tg_posts) == 1
    photo = _photo_posts(tg_posts)[0]["data"]
    assert photo["caption"] == f"_{FULL_LOCATION}_"
    assert photo["parse_mode"] == "Markdown"
    assert "Użyta lokalizacja" not in photo["caption"]
    assert "*Warszawa*" not in photo["caption"]


@pytest.mark.parametrize("description", [None, "", " \n "])
def test_missing_oneoff_description_has_no_caption_or_extra_message(tg_posts, description):
    assert lb._run_oneoff_report(
        CHAT_ID, 52.15, 21.29, CITY, "pl", "city", "now",
        display_location=description,
    )
    assert len(tg_posts) == 1
    assert _photo_posts(tg_posts)[0]["data"] == {"chat_id": CHAT_ID}


def _read_legacy_italic_caption(caption):
    """Validate the emitted subset: escapes outside entities, italic text inside.

    This deliberately rejects escaped punctuation INSIDE an italic entity,
    empty entities, links, bold/code markup and unclosed delimiters.
    """
    plain = []
    i = 0
    while i < len(caption):
        if caption[i] == "\\":
            assert i + 1 < len(caption) and caption[i + 1] in "\\_*`["
            plain.append(caption[i + 1])
            i += 2
        else:
            assert caption[i] == "_"
            end = caption.index("_", i + 1)
            text = caption[i + 1:end]
            assert text and not any(c in text for c in "\\_*`[")
            plain.append(text)
            i = end + 1
    return "".join(plain)


@pytest.mark.parametrize("description", [
    FULL_LOCATION, "Dąbrowa_Górnicza *test* [link](adres)",
    r"Miasto\_**[x]`kod`", "_*`[\\", "_Warszawa_", "a__b", "a\\_b",
    "<b>Miasto & okolice</b>",
])
def test_markdown_caption_has_balanced_entities_and_literal_dynamic_text(tg_posts, description):
    assert lb._send_oneoff_report(
        CHAT_ID, 52.15, 21.29, CITY, "pl", "now", display_location=description,
    )
    caption = _photo_posts(tg_posts)[0]["data"]["caption"]
    assert _read_legacy_italic_caption(caption) == description
    assert "\n\n" not in caption


@pytest.mark.parametrize("description", [None, "", " \n "])
def test_guest_missing_description_produces_no_empty_html(description):
    assert lb._guest_location_caption(description) == ""


def test_guest_html_is_escaped_without_duplicate_title(tg_posts, fake_png):
    from html.parser import HTMLParser

    class CaptionParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags, self.text = [], []

        def handle_starttag(self, tag, attrs):
            self.tags.append(("start", tag, attrs))

        def handle_endtag(self, tag):
            self.tags.append(("end", tag))

        def handle_data(self, data):
            self.text.append(data)

    description = 'Warszawa_<x> & "okolice" * [link]'
    lb.send_photo(CHAT_ID, fake_png, caption=lb._guest_location_caption(description), parse_mode="HTML")
    photo = _photo_posts(tg_posts)[0]["data"]
    parser = CaptionParser()
    parser.feed(photo["caption"])
    assert parser.tags == [("start", "i", []), ("end", "i")]
    assert "".join(parser.text) == description
    assert photo["parse_mode"] == "HTML"


@pytest.mark.parametrize("flow,card_type", [
    ("command", "day"), ("command", "now"), ("command", "future"),
    ("prompt", "day"), ("prompt", "now"), ("prompt", "future"),
    ("shortcut", "day"), ("shortcut", "now"), ("shortcut", "future"),
    ("legacy_shortcut", "day"), ("legacy_shortcut", "now"), ("legacy_shortcut", "future"),
    ("other_city", "now"), ("pin", "day"), ("webapp", "day"),
])
def test_all_oneoff_call_sites_send_description_on_same_photo(monkeypatch, tg_posts, flow, card_type):
    """Run the real routing, report builder AND Telegram photo serialization."""
    import copy
    from test_access_gate import BotHarness, LEGACY_ROW, freeze_local_hour

    bot = BotHarness(monkeypatch, formularz_rows=[LEGACY_ROW])
    freeze_local_hour(monkeypatch, 10, 0)
    cid = 700 if flow == "legacy_shortcut" else CHAT_ID
    if cid != 700:
        bot.run(bot.msg(cid, "/start BETAX1"))
    if flow == "other_city":
        bot.run(bot.msg(cid, "/miasto Kraków"))
    profile_before = copy.deepcopy(bot.users.record(cid))
    legacy_before = copy.deepcopy(bot.gc.formularz.rows)
    monkeypatch.setattr(lb, "_send_oneoff_report", REAL_SEND_ONEOFF)
    monkeypatch.setattr(lb, "get_location_details_from_coords", lambda *a, **kw: (
        "Warszawa", FULL_LOCATION, lb.GEO_OK,
    ))
    # BotHarness replaced requests.post; intercept the actual sendPhoto payload.
    def post(url, data=None, json=None, **kw):
        tg_posts.append({"url": url, "data": dict(data or json or {})})
        return type("Response", (), {"status_code": 200, "json": lambda self: {"ok": True}})()

    monkeypatch.setattr(lb.requests, "post", post)
    command = {"day": "/dzien", "now": "/teraz", "future": "/trend"}[card_type]
    shortcut = {"day": "?d", "now": "?12", "future": "?14"}[card_type]
    tg_posts.clear()
    if flow in ("command", "other_city"):
        bot.run(bot.msg(cid, command + " Warszawa"))
    elif flow == "prompt":
        bot.run(bot.msg(cid, command))
        bot.run(bot.msg(cid, "Warszawa"))
    elif flow in ("shortcut", "legacy_shortcut"):
        bot.run(bot.msg(cid, shortcut))
        bot.run(bot.msg(cid, "Warszawa"))
    elif flow == "pin":
        bot.run(bot.pin(cid, reply=True))
    else:
        bot.run(bot.webapp_location(cid))
    photos = _photo_posts(tg_posts)
    assert len(photos) == 1, tg_posts
    assert photos[0]["data"]["caption"] == f"_{FULL_LOCATION}_"
    assert photos[0]["data"]["parse_mode"] == "Markdown"
    messages = [p["data"]["text"] for p in tg_posts if p["url"].endswith("/sendMessage")]
    assert not any("Użyta lokalizacja" in m or FULL_LOCATION in m for m in messages)
    assert bot.users.record(cid) == profile_before
    assert bot.gc.formularz.rows == legacy_before


@pytest.mark.parametrize("description", [FULL_LOCATION, None, ""])
def test_guest_adapter_uses_only_description(monkeypatch, tg_posts, description):
    from test_access_gate import BotHarness

    bot = BotHarness(monkeypatch)
    bot.run(bot.msg(CHAT_ID, "/start BETAX1"))
    bot.run(bot.msg(CHAT_ID, "?12 Warszawa"))
    adapter = bot.guest_kwargs[-1]["send_photo_fn"]
    photos = []
    monkeypatch.setattr(lb, "send_photo", lambda *a, **kw: photos.append(kw))
    adapter(CHAT_ID, "card.png", "Warszawa", description)
    assert photos == [{"caption": lb._guest_location_caption(description), "parse_mode": "HTML"}]
    assert "<b>" not in photos[0]["caption"]


@pytest.mark.parametrize("text,card_type,pin", [
    ("?d Warszawa", "day", False), ("?12 Warszawa", "now", False),
    ("?14 Warszawa", "future", False), ("@bot Warszawa", "now", False),
    ("@bot", "now", True),
])
@pytest.mark.parametrize("description", [FULL_LOCATION, None, "A_B *C* <D> & E"])
def test_real_guest_keeps_hero_and_only_captions_full_description(
    monkeypatch, tg_posts, text, card_type, pin, description,
):
    import copy
    import guest_bot_handler as guest
    from test_access_gate import BotHarness, freeze_local_hour

    bot = BotHarness(monkeypatch)
    bot.run(bot.msg(CHAT_ID, "/start BETAX1"))
    profile_before = copy.deepcopy(bot.users.record(CHAT_ID))
    freeze_local_hour(monkeypatch, 10, 0)
    # Clear only the guest caches for test isolation; no live geocoder calls.
    monkeypatch.setattr(guest, "_GEO_CACHE", {})
    monkeypatch.setattr(guest, "_CITY_CACHE", {})
    monkeypatch.setattr(lb, "handle_guest_now", guest.handle_guest_now)
    monkeypatch.setattr(lb, "BOT_USERNAME", "bot")
    monkeypatch.setattr(lb, "_resolve_location_labels", lambda *a, **kw: (
        "Warszawa", description, lb.GEO_OK,
    ))
    payloads, rendered = [], []

    def build(**kwargs):
        payloads.append(kwargs)
        return {"location": {"tz": "Europe/Warsaw", "name": kwargs["location_name"]}}

    monkeypatch.setattr(lb, "build_payload_for_location", build)
    for kind, name in [("day", "prepare_layout_data"), ("now", "prepare_now_layout_data"),
                       ("future", "prepare_future_layout_data")]:
        monkeypatch.setattr(lb, name, lambda payload, kind=kind: rendered.append(
            (kind, payload["location"]["name"])
        ) or {})
    # Capture the photo after BotHarness installed its own Telegram fake.
    def post(url, data=None, json=None, **kw):
        tg_posts.append({"url": url, "data": dict(data or json or {})})
        return type("Response", (), {"status_code": 200, "json": lambda self: {"ok": True}})()

    monkeypatch.setattr(lb.requests, "post", post)
    tg_posts.clear()
    update = bot.msg(CHAT_ID, text)
    if pin:
        update["message"]["reply_to_message"] = {"location": {"latitude": 52.15, "longitude": 21.29}}
    bot.run(update)
    # Skrót z argumentem ma teraz DOKŁADNIE JEDEN komunikat search_loc PRZED
    # kartą. Wzmianka @bot i pinezka pozostają bez tego komunikatu.
    is_shortcut_with_arg = text.split()[0] in {"?d", "?12", "?14"}
    photos_only = _photo_posts(tg_posts)
    messages = [p for p in tg_posts if p["url"].endswith("/sendMessage")]
    if is_shortcut_with_arg:
        assert len(tg_posts) == 2, tg_posts
        assert [m["data"]["text"] for m in messages] == [lb.t_ui("pl", "search_loc")]
        assert tg_posts.index(messages[0]) < tg_posts.index(photos_only[0])
    else:
        assert len(tg_posts) == 1, tg_posts
        assert messages == []
    assert len(photos_only) == 1
    photo = photos_only[0]["data"]
    if description:
        assert photo["caption"] == lb._guest_location_caption(description)
        assert photo["parse_mode"] == "HTML"
    else:
        assert photo == {"chat_id": CHAT_ID}
    assert rendered == [(card_type, "Warszawa")]
    assert payloads[0]["location_name"] == "Warszawa"
    assert bot.users.record(CHAT_ID) == profile_before
