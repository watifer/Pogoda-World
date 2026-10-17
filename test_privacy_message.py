"""test_privacy_message.py — /priv (/privacy): treść i bezpieczny URL.

Decyzje właściciela produktu (D1–D3):
- D1: jedna kategoria identyfikacyjna: „identyfikator czatu, język i status
  dostępu” — bez „tylko” i bez dodatkowych kategorii danych,
- D2: żadnych zastrzeżeń o legacy / Formularz w treści,
- D3: wywołanie zachowuje kontrakt t_ui(..., url=_md_safe(PRIVACY_URL),
  version=PRIVACY_VERSION); tekst używa {url}, a numer wersji NIE jest już
  częścią treści. URL przechodzi przez _md_safe, bo send_reply wysyła
  legacy Markdown — adres z „_” (np. nadpisany przez ENV) nie może rozsypać
  formatowania całej wiadomości.

Uruchomienie: pytest test_privacy_message.py -v
"""

import pytest

import i18n
import location_bot as lb
from test_access_gate import BLOCKED_LEGACY_ROW, LEGACY_ROW, BotHarness

LANGS = ("pl", "en", "de", "es", "fr", "no")
PRIVATE = 9100

PL_PRIVACY_EXACT = (
    "📜 *PRYWATNOŚĆ I TWOJE DANE*\n"
    "\n"
    "Zasady przetwarzania:\n"
    "• Zapisujemy identyfikator czatu, język i status dostępu.\n"
    "• Przybliżoną lokalizację przechowujemy wyłącznie po Twojej wyraźnej zgodzie w /miasto.\n"
    "• Nie zbieramy imion, nazwisk, adresów e-mail ani dokładnych współrzędnych GPS.\n"
    "• Nigdy nie przekazujemy ani nie sprzedajemy Twoich danych.\n"
    "\n"
    "Zarządzanie:\n"
    "/dane — Twoje zapisane dane\n"
    "/bezGPS — usunięcie lokalizacji (dostęp zostaje)\n"
    "/usunDane — pełne usunięcie konta\n"
    "\n"
    "Pełna polityka prywatności:\n"
    "{url}"
)

LEGACY_WORDS = ("Formularz", "legacy", "starsz", "older", "ältere", "antigu", "ancien", "eldre")


@pytest.fixture
def bot(monkeypatch):
    return BotHarness(monkeypatch, seed_profiles=[LEGACY_ROW, BLOCKED_LEGACY_ROW])


def _privacy_reply(bot, chat_id, lang="pl"):
    bot.run(bot.msg(chat_id, "/start BETAX1", lang=lang))
    before = len(bot.sent)
    bot.run(bot.msg(chat_id, "/privacy", lang=lang))
    replies = bot.replies_since(chat_id, before)
    assert len(replies) == 1, replies
    return replies[0]


# ============================================================================
# 1. TREŚĆ (i18n)
# ============================================================================

def test_polish_privacy_text_is_exactly_as_requested():
    assert i18n.UI_TEXTS["pl"]["privacy_msg"] == PL_PRIVACY_EXACT


@pytest.mark.parametrize("lang", LANGS)
def test_privacy_template_uses_url_only_and_is_markdown_safe(lang):
    text = i18n.UI_TEXTS[lang]["privacy_msg"]
    assert text.count("{url}") == 1 and text.rstrip().endswith("{url}"), \
        f"{lang}: link do pełnej polityki zamyka wiadomość"
    assert "{version}" not in text, "numer wersji nie jest częścią treści (D3)"
    assert "{" not in text.replace("{url}", ""), f"{lang}: tylko placeholder {{url}}"
    assert text.count("*") % 2 == 0 and "`" not in text
    # Komendy zarządzania: PL aliasy, pozostałe języki kanoniczne z escapowanym „_”.
    if lang == "pl":
        for cmd in ("/dane", "/bezGPS", "/usunDane", "/miasto"):
            assert cmd in text
    else:
        for cmd in ("/data", "/forget\\_location", "/delete\\_me", "/city"):
            assert cmd in text, f"{lang}: brak {cmd}"
        assert "/forget_location" not in text and "/delete_me" not in text, \
            f"{lang}: podkreślenia w komendach muszą być escapowane (legacy Markdown)"
    # Jedyne pogrubienie to nagłówek.
    assert text.split("\n")[0].startswith("📜 *") and text.split("\n")[0].endswith("*")
    assert text.count("*") == 2


@pytest.mark.parametrize("lang", LANGS)
def test_privacy_has_no_legacy_caveats(lang):
    text = i18n.UI_TEXTS[lang]["privacy_msg"].lower()
    for word in LEGACY_WORDS:
        assert word.lower() not in text, f"{lang}: zastrzeżenie o legacy ({word!r}) — D2"


@pytest.mark.parametrize("lang", LANGS)
def test_privacy_has_four_rule_bullets_and_three_management_lines(lang):
    text = i18n.UI_TEXTS[lang]["privacy_msg"]
    bullets = [l for l in text.split("\n") if l.startswith("• ")]
    assert len(bullets) == 4, f"{lang}: {bullets}"
    commands = [l for l in text.split("\n") if l.startswith("/")]
    assert len(commands) == 3, f"{lang}: {commands}"


def test_version_kwarg_is_tolerated_and_url_required():
    """t_ui = str.format: nadmiarowe version jest ignorowane, brak url to błąd."""
    out = i18n.t_ui("pl", "privacy_msg", url="https://x.example/", version="2026-09-v1")
    assert out.endswith("https://x.example/") and "2026-09-v1" not in out
    with pytest.raises(KeyError):
        i18n.t_ui("pl", "privacy_msg", version="2026-09-v1")


# ============================================================================
# 2. WYWOŁANIE: url=_md_safe(PRIVACY_URL), version=PRIVACY_VERSION
# ============================================================================

def test_handler_passes_md_safe_url_and_version_kwarg(bot, monkeypatch):
    seen = {}
    real_t_ui = lb.t_ui

    def spy(lang, key, **kwargs):
        if key == "privacy_msg":
            seen.update(lang=lang, kwargs=dict(kwargs))
        return real_t_ui(lang, key, **kwargs)

    monkeypatch.setattr(lb, "t_ui", spy)

    reply = _privacy_reply(bot, PRIVATE)

    assert seen["lang"] == "pl"
    assert seen["kwargs"] == {"url": lb._md_safe(lb.PRIVACY_URL),
                              "version": lb.PRIVACY_VERSION}
    assert reply == lb.t_ui("pl", "privacy_msg", url=lb._md_safe(lb.PRIVACY_URL))


def test_default_url_is_unchanged_by_md_safe(bot):
    reply = _privacy_reply(bot, PRIVATE)
    assert lb.PRIVACY_URL == "https://watifer.github.io/Pogoda-World/privacy/"
    assert lb._md_safe(lb.PRIVACY_URL) == lb.PRIVACY_URL
    assert reply.rstrip().endswith(lb.PRIVACY_URL)
    assert lb.PRIVACY_VERSION not in reply


def test_url_with_underscores_is_escaped_for_legacy_markdown(bot, monkeypatch):
    monkeypatch.setattr(lb, "PRIVACY_URL", "https://example.org/pogoda_world/privacy_v2/")

    reply = _privacy_reply(bot, PRIVATE)

    assert reply.rstrip().endswith("https://example.org/pogoda\\_world/privacy\\_v2/")
    assert "/pogoda_world/" not in reply, "nieescapowane „_” rozsypałoby Markdown"


@pytest.mark.parametrize("lang", ("en", "de"))
def test_privacy_reply_follows_record_language(bot, lang):
    reply = _privacy_reply(bot, PRIVATE, lang=lang)
    assert reply == lb.t_ui(lang, "privacy_msg", url=lb._md_safe(lb.PRIVACY_URL))


@pytest.mark.parametrize("alias", ("/priv", "/privacy"))
def test_privacy_aliases_give_the_same_message(bot, alias):
    bot.run(bot.msg(PRIVATE, "/start BETAX1"))
    before = len(bot.sent)
    bot.run(bot.msg(PRIVATE, alias))
    assert bot.replies_since(PRIVATE, before) == [
        lb.t_ui("pl", "privacy_msg", url=lb._md_safe(lb.PRIVACY_URL))
    ]
