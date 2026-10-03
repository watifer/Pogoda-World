"""test_welcome_privacy.py — POPRAWKA #2: ekran onboardingu (po aktywacji zaproszenia).

Problem zgłoszony z produkcji: komunikat `welcome_access` (wysyłany po udanej
rejestracji przez link zaproszenia) nie informował nowego użytkownika o
prywatności i nie dał mu na wejściu klikalnej komendy prywatności.

Ustalenia:
  1. po „Witaj w Pogoda World 🌍” wchodzi nowy akapit:
     „Najpierw Twoja prywatność, przeczytaj:” + komenda prywatności,
  2. na samym dole zostaje TYLKO administracja danymi (bez części o prywatności),
  3. reszta treści (prognozy, /miasto, akapity) bez zmian,
  4. komendy piszemy zwykłym tekstem (bez backticków), żeby były klikalne —
     tak samo, jak /bezGPS i /usunDane w panelu /dane.

Uruchomienie: pytest test_welcome_privacy.py -v
"""

import re

import pytest

import i18n

LANGS = ("pl", "en", "de", "es", "fr", "no")
PRIVACY_CMD = {"pl": "/priv"}
DATA_CMD = {"pl": "/dane"}


def _privacy(lang):
    return PRIVACY_CMD.get(lang, "/privacy")


def _data(lang):
    return DATA_CMD.get(lang, "/data")


def _lines(lang):
    return i18n.UI_TEXTS[lang]["welcome_access"].split("\n")


# ============================================================================
# 1. AKAPIT PRYWATNOŚCI: dokładnie między powitaniem a sekcją "Możesz..."
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_privacy_paragraph_sits_right_after_welcome(lang):
    lines = _lines(lang)
    greeting_idx = next(i for i, l in enumerate(lines)
                        if l.strip().startswith(("Witaj ", "Welcome ", "Willkommen ",
                                                 "Bienvenido ", "Bienvenue ", "Velkommen ")))

    # powitanie -> pusta linia -> zapowiedź akapitu -> komenda -> pusta linia
    assert lines[greeting_idx + 1].strip() == "", "akapity oddzielamy pustą linią"
    intro = lines[greeting_idx + 2].strip()
    assert intro, "brakuje zapowiedzi akapitu prywatności"
    assert lines[greeting_idx + 3].strip() == _privacy(lang)
    assert lines[greeting_idx + 4].strip() == "", "akapity oddzielamy pustą linią"


@pytest.mark.parametrize("lang", LANGS)
def test_privacy_paragraph_precedes_forecast_section(lang):
    lines = [l.strip() for l in _lines(lang)]
    priv_idx = lines.index(_privacy(lang))
    # pierwsza komenda prognozy po akapicie prywatności
    forecast_idx = next(i for i, l in enumerate(lines)
                        if l.startswith(("/day", "/dzien")))
    assert priv_idx < forecast_idx, "akapit prywatności ma być PRZED sekcją o prognozach"


@pytest.mark.parametrize("lang", LANGS)
def test_privacy_intro_is_language_specific_not_shared_pl_text(lang):
    """Każdy język ma własne brzmienie zapowiedzi (nie kopię polskiego)."""
    lines = [l.strip() for l in _lines(lang)]
    idx = lines.index(_privacy(lang))
    intro = lines[idx - 1]
    assert len(intro) > 10, f"{lang}: zapowiedź wygląda na pustą/za krótką"
    if lang != "pl":
        assert "Twoja prywatność" not in intro, f"{lang}: polska zapowiedź w obcym języku"
        assert not re.search(r"[ąćęłńśźż]", intro, re.IGNORECASE), \
            f"{lang}: polskie diakrytyki w zapowiedzi"


# ============================================================================
# 2. STOPKA: na dole tylko administracja danymi
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_footer_is_data_administration_only(lang):
    lines = [l.strip() for l in _lines(lang) if l.strip()]
    assert lines[-1] == _data(lang)
    footer_label = lines[-2]
    assert "administracja" in footer_label.lower() or \
           any(w in footer_label.lower() for w in ("managing", "verwaltung", "gestión",
                                                   "gestion", "administrasjon")), \
        f"{lang}: nieoczekiwana stopka: {footer_label!r}"


@pytest.mark.parametrize("lang", LANGS)
def test_privacy_word_removed_from_footer(lang):
    """Słowo o prywatności NIE może zostać w dolnej stopce (tam jest już akapit)."""
    lines = [l.strip() for l in _lines(lang) if l.strip()]
    footer_block = "\n".join(lines[-2:]).lower()
    privacy_words = ("prywatn", "privacy", "privatsphäre", "privacidad",
                     "confidentialité", "personvern")
    assert not any(w in footer_block for w in privacy_words), \
        f"{lang}: prywatność została w stopce: {lines[-2:]}"


# ============================================================================
# 3. RESZTA KOMUNIKATU BEZ ZMIAN (regresja onboardingu)
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_rest_of_message_unchanged(lang):
    lines = [l.strip() for l in _lines(lang)]
    # PL używa krótkich aliasów (mapowanych w location_bot.COMMAND_ALIASES)
    expected_cmds = ("/dzien", "/teraz", "/trend") if lang == "pl" \
        else ("/day", "/now", "/trend")
    for expected in expected_cmds:
        assert any(l.startswith(expected) for l in lines), \
            f"{lang}: zniknęła komenda {expected}"
    assert ("/miasto" if lang == "pl" else "/city") in lines
    assert lines.index("/miasto" if lang == "pl" else "/city") < len(lines) - 2


# ============================================================================
# 4. KLIKALNOŚĆ: komendy w tekście, nie w backtickach
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_commands_are_plain_text_not_backticked(lang):
    text = i18n.UI_TEXTS[lang]["welcome_access"]
    assert not re.search(r"`/", text), f"{lang}: komenda w backtickach jest nieklikalna"
    # komendy stoją w osobnych liniach — tak jak pozostałe w tym komunikacie
    assert f"\n{_privacy(lang)}\n" in text
    assert text.endswith(f"\n{_data(lang)}"), "stopka administracji ma być ostatnią linią"


@pytest.mark.parametrize("lang", LANGS)
def test_new_lines_do_not_break_markdown_parse_mode(lang):
    """send_reply leci z parse_mode=Markdown — żadnych niedomkniętych znaczników."""
    text = i18n.UI_TEXTS[lang]["welcome_access"]
    assert text.count("**") % 2 == 0, f"{lang}: niedomknięte pogrubienie"
    assert text.count("_") % 2 == 0, f"{lang}: niedomknięta kursywa"
