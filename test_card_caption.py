"""test_card_caption.py — POPRAWKA #1: karty bez podpisu (caption) z lokalizacją.

Problem zgłoszony z produkcji: w Telegramie pod grafiką karty (/day, /now,
/future) lądował dopisek z nazwą miejscowości — identyczną z tytułem samej
karty (np. „Wiązowna”). Źródłem był caption wiadomości sendPhoto, a nie grafika
PNG (stopka obrazka rysuje wyłącznie źródło danych i „Pogoda World”).

Zakres poprawki: wyłącznie ścieżka Users, czyli `_send_oneoff_report`
(profil zapisany w arkuszu Users oraz karta jednorazowa dla miasta podanego
w komendzie, np. `/day Manaus`). Tryb gościa (.d/.n/.f, wzmianka @bot) nadal
wysyła kartę z adresem z geokodera — tego pilnuje
`test_send_photo_keeps_caption_for_guest_path`.

Uruchomienie: pytest test_card_caption.py -v
"""

import ast

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

    def fake_post(url, data=None, files=None, timeout=None, **kwargs):
        posts.append({"url": url, "data": dict(data or {})})
        return None

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


@pytest.mark.parametrize("card_type", ["day", "now", "future"])
def test_oneoff_card_for_given_city_has_no_caption(tg_posts, card_type):
    """Karta jednorazowa dla miasta z komendy (/day Manaus) też bez podpisu."""
    assert lb._send_oneoff_report(CHAT_ID, 52.15, 21.29, CITY, "pl", card_type) is True

    photos = _photo_posts(tg_posts)
    assert len(photos) == 1
    assert "caption" not in photos[0]["data"]


def test_long_display_location_never_becomes_card_location_name(monkeypatch):
    """Długi opis jest tylko tekstem pod kartą; payload/layout dostają short_label."""
    display_location = (
        "Wiązowna, gmina Wiązowna, powiat otwocki, województwo mazowieckie, "
        "05-462, Polska"
    )
    payload_calls = []
    layout_calls = []
    replies = []

    def build_payload(**kwargs):
        payload_calls.append(kwargs)
        return {"location_name": kwargs["location_name"], "hourly": []}

    def prepare_layout(payload):
        layout_calls.append(payload)
        return payload

    monkeypatch.setattr(lb, "build_payload_for_location", build_payload)
    monkeypatch.setattr(lb, "_resolve_tz", lambda lat, lon: "Europe/Warsaw")
    monkeypatch.setattr(lb, "prepare_layout_data", prepare_layout)
    monkeypatch.setattr(lb.image_generator, "generate_weather_card", lambda layout: "card.png")
    monkeypatch.setattr(lb, "send_photo", lambda *args, **kwargs: None)
    monkeypatch.setattr(lb, "send_reply", lambda chat_id, text, **kwargs: replies.append(text))
    lb.PENDING_SAVE.clear()

    assert lb._run_oneoff_report(
        CHAT_ID, 52.123456, 21.987654, CITY, "pl", "city", "day",
        display_location=display_location,
    ) is True

    assert payload_calls[0]["location_name"] == CITY
    assert payload_calls[0]["lat"] == 52.123456
    assert payload_calls[0]["lon"] == 21.987654
    assert layout_calls[0]["location_name"] == CITY
    assert display_location not in str(payload_calls[0])
    assert display_location not in str(layout_calls[0])
    assert display_location in replies[0]
    lb.PENDING_SAVE.clear()


def test_used_location_note_still_follows_city_card(monkeypatch):
    """Usuwamy TYLKO caption; informacja o użytej lokalizacji zostaje (pkt. UX)."""
    sent = []
    monkeypatch.setattr(lb, "_send_oneoff_report", lambda *a, **kw: True)
    monkeypatch.setattr(lb, "send_reply", lambda chat_id, text, **kw: sent.append(text))
    lb.PENDING_SAVE.clear()

    ok = lb._run_oneoff_report(
        CHAT_ID, 52.15, 21.29, CITY, "pl", "city", "day",
        display_location=f"{CITY}, Polska",
    )

    assert ok is True
    assert len(sent) == 1
    assert CITY in sent[0]
    assert f"{CITY}, Polska" in sent[0]


# ============================================================================
# 2. WARSTWA WYSYŁKI: send_photo
# ============================================================================

def test_send_photo_keeps_caption_for_guest_path(tg_posts, fake_png):
    """Tryb gościa bez zmian: `card_caption` domyślnie True, caption przechodzi."""
    caption = f"<b>{CITY}</b>\n<i>{CITY}, Polska</i>"

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


# ============================================================================
# 3. GUARD ŹRÓDŁA: nikt nie doklei tu z powrotem captionu
# ============================================================================

def test_oneoff_report_never_passes_caption_argument():
    """Statyczny guard na przyszłe edycje `_send_oneoff_report`."""
    with open(lb.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    fn = next(node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and node.name == "_send_oneoff_report")

    caption_uses = [
        kw.arg
        for call in ast.walk(fn)
        if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "send_photo"
        for kw in call.keywords
        if kw.arg == "caption"
    ]
    assert caption_uses == [], "karta ze ścieżki Users nie może dostać captionu"
