"""test_shortcuts.py — POPRAWKA #6: trzywarstwowy system skrótów.

Docelowa mapa (promowana w /porady, sekcja 10):
  dzienny: ?d (pl/en/es/no), ?t (de), ?j (fr)
  globalne: ?12 (12 godzin) i ?14 (trend 14 dni)
  legacy (ukryte, ale nadal działające): ?d/?n/?f/?p oraz kropkowe warianty

Kluczowe wymagania:
  1. rozpoznawanie po PIERWSZYM TOKENIE — "?14 Warszawa" to skrót "?14"
     z argumentem "Warszawa", a nie "?1" + "4 Warszawa",
  2. kolejność warstw: globalne -> dzienny lokalny -> legacy,
  3. kropka równoważna pytajnikowi,
  4. zlepiona forma "?dWarszawa" działa (zgodność wsteczna),
  5. bramka dostępu (PR1) obejmuje nowe skróty — obcy user dostaje zaproszenie
     także po "?14 Berlin".

Uruchomienie: pytest test_shortcuts.py -v
"""

import re

import pytest

import guest_bot_handler as gbh
import i18n
import location_bot as lb

LANGS = ("pl", "en", "de", "es", "fr", "no")
DAILY_EXPECTED = {"pl": "?d", "en": "?d", "de": "?t", "es": "?d", "fr": "?j", "no": "?d"}


# ============================================================================
# 1. PARSER: WARSTWY I PIERWSZY TOKEN
# ============================================================================

@pytest.mark.parametrize("text,card,query", [
    ("?12 Warszawa", "now", "Warszawa"),
    ("?14 Warszawa", "future", "Warszawa"),
    ("?14 Berlin", "future", "Berlin"),
    ("?12", "now", ""),
    ("?14", "future", ""),
])
def test_global_shortcuts(text, card, query):
    assert gbh.resolve_shortcut(text) == (text.split()[0], card, query)


@pytest.mark.parametrize("lang,shortcut", sorted(DAILY_EXPECTED.items()))
def test_local_daily_shortcut_per_language(lang, shortcut):
    city = {"pl": "Warszawa", "en": "Warsaw", "de": "Berlin",
            "es": "Madrid", "fr": "Paris", "no": "Oslo"}[lang]
    assert gbh.resolve_shortcut(f"{shortcut} {city}") == (shortcut, "day", city)


@pytest.mark.parametrize("text,card", [
    ("?d Paryż", "day"), (".d Paryż", "day"),
    ("?t Berlin", "day"), (".t Berlin", "day"),
    ("?j Paris", "day"), (".j Paris", "day"),
    ("?n Hel", "now"), (".n Hel", "now"),
    ("?f Tokio", "future"), (".f Tokio", "future"),
    ("?p Miasto", "now"), (".p Miasto", "now"),
])
def test_all_layers_and_dot_variants(text, card):
    prefix, found_card, query = gbh.resolve_shortcut(text)
    assert found_card == card
    assert prefix == text.split()[0]
    assert query == text.split()[1]


def test_daily_shortcuts_of_other_languages_do_not_end_in_silence():
    """Polak piszący ?t albo ?j też ma dostać kartę dzienną (nie ciszę)."""
    for text in ("?t Berlin", "?j Paris"):
        assert gbh.resolve_shortcut(text)[1] == "day"


def test_glued_form_still_works_backwards_compatible():
    assert gbh.resolve_shortcut("?dWarszawa") == ("?d", "day", "Warszawa")
    assert gbh.resolve_shortcut(".nHel") == (".n", "now", "Hel")
    assert gbh.resolve_shortcut("?14Berlin") == ("?14", "future", "Berlin")


def test_first_token_wins_for_argument_with_digits():
    """Regresja: ?14 Warszawa nie może się rozłożyć na '?1' i '4 Warszawa'."""
    prefix, card, query = gbh.resolve_shortcut("?14 05-462 Wiązowna")
    assert (prefix, card) == ("?14", "future")
    assert query == "05-462 Wiązowna"


@pytest.mark.parametrize("text", [
    "", "   ", "/day", "/start TOKEN", "Cześć, jaka pogoda?",
    "@MyBot pogoda", "?15 Warszawa", "?1", "??14", "pogoda ?12",
])
def test_non_shortcuts_are_not_recognized(text):
    assert gbh.resolve_shortcut(text) == (None, None, None)


def test_keys_cover_all_layers():
    keys = set(gbh.iter_shortcut_keys())
    for code in ("12", "14", "d", "t", "j", "n", "f", "p"):
        assert f"?{code}" in keys and f".{code}" in keys


# ============================================================================
# 2. BRAMKA DOSTĘPU: NOWE SKRÓTY TEŻ SĄ GATOWANE
# ============================================================================

@pytest.mark.parametrize("text", [
    "?12 Berlin", "?14 Berlin", "?t Berlin", "?j Paris", "?d Paryż",
    ".n Hel", ".f Tokio", ".p Miasto", ".12 Berlin", ".14 Berlin",
])
def test_gate_recognizes_every_shortcut_layer(text):
    assert lb._is_guest_trigger(text, "MyBot") is True


@pytest.mark.parametrize("text", ["/day", "/start X", "Cześć", "", "?15 X"])
def test_gate_ignores_non_shortcuts(text):
    assert lb._is_guest_trigger(text, "MyBot") is False


def test_gate_constant_is_derived_from_parser():
    """Stała publiczna zostaje (kompatybilność), ale musi pochodzić z parsera."""
    assert set(lb.GUEST_SHORTCUT_PREFIXES) == set(gbh.iter_shortcut_keys())
    for key in ("?12", "?14", "?t", "?j"):
        assert key in lb.GUEST_SHORTCUT_PREFIXES


# ============================================================================
# 3. /porady, SEKCJA 10: PROMUJEMY TYLKO NOWE SKRÓTY
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_tips_promote_local_daily_and_globals(lang):
    tips = i18n.UI_TEXTS[lang]["porady_msg"]
    section = "*10." + tips.split("*10.", 1)[1]
    daily = DAILY_EXPECTED[lang]
    assert re.search(rf"^{re.escape(daily)} \S+ — ", section, flags=re.MULTILINE), \
        f"{lang}: brak dziennego skrótu {daily} w sekcji 10"
    assert re.search(r"^\?12 \S+ — ", section, flags=re.MULTILINE), f"{lang}: brak ?12"
    assert re.search(r"^\?14 \S+ — ", section, flags=re.MULTILINE), f"{lang}: brak ?14"


@pytest.mark.parametrize("lang", LANGS)
def test_tips_do_not_advertise_legacy(lang):
    """Legacy działa, ale nie może być promowany (ani kropka, ani ?n/?f)."""
    tips = i18n.UI_TEXTS[lang]["porady_msg"]
    section = "*10." + tips.split("*10.", 1)[1]
    for legacy in (".n ", ".f ", ".p ", "?n ", "?f ", "?p "):
        assert legacy not in section, f"{lang}: legacy {legacy!r} promowany w /porady"
    for note in ("Zamiast kropki", "Statt des Punktes", "in place of a dot",
                 "en lugar de un punto", "au lieu d'un point",
                 "i stedet for et punktum"):
        assert note not in section, f"{lang}: stara nota o kropce została ({note!r})"


@pytest.mark.parametrize("lang", LANGS)
def test_tips_mention_dot_as_easier_alternative(lang):
    """Notka odwrotna: promujemy "?", a kropka jest wygodną alternatywą."""
    tips = i18n.UI_TEXTS[lang]["porady_msg"]
    section = "*10." + tips.split("*10.", 1)[1]
    last_line = [l for l in section.split("\n") if l.strip()][-1]

    assert last_line.startswith("*(") and last_line.endswith(")*"), \
        f"{lang}: notka o kropce powinna być ostatnią, kursywą"
    assert "?" in last_line and "." in last_line, \
        f"{lang}: notka musi wspominać oba znaki"
    # przykład w nocie używa formy kropkowej lokalnego skrótu dziennego
    daily = DAILY_EXPECTED[lang].replace("?", ".")
    assert daily in last_line, f"{lang}: brak przykładu {daily} w nocie"
    assert "`" not in last_line, f"{lang}: backticki w nocie renderują się jako kod"


@pytest.mark.parametrize("lang", LANGS)
def test_tips_section_has_no_backticks_and_balanced_markdown(lang):
    tips = i18n.UI_TEXTS[lang]["porady_msg"]
    assert "`" not in tips, f"{lang}: backticki renderują skróty jako kod"
    assert tips.count("*") % 2 == 0


# ============================================================================
# 4. PROMPT O MIASTO NIE PROMUJE SKRÓTÓW
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_guest_need_city_key_exists_without_shortcuts(lang):
    text = i18n.UI_TEXTS[lang]["guest_need_city"]
    assert len(text) > 15
    # Skrótu NIE promujemy poza /porady: żaden token nie może zaczynać się
    # od "?" ani "." (kropka kończąca zdanie jest w porządku).
    assert not re.search(r"(^|\s)[?.](?=\S)", text), \
        f"{lang}: prompt nie może pokazywać skrótów: {text!r}"


def test_guest_handler_uses_translated_prompt():
    src = open(gbh.__file__, encoding="utf-8").read()
    assert "guest_need_city" in src, "prompt o miasto nadal jest hardkodowany"
    assert "np. .d Paryż" not in src, "stary polski prompt promujący legacy został"
