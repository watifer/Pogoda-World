"""test_city_prompt.py — POPRAWKA #3: komunikat /miasto (city_prompt / city_prompt_active).

Zmiany zgłoszone przez właściciela produktu:
  1. Linia o wprowadzaniu lokalizacji wspomina też o pinezce z mapy:
     „Wpisz nazwę miejscowości, wyślij pinezkę z mapy lub lokalizację GPS.
     Szczegóły:” + komenda tipsów w następnej linii (jedna linia opisu!),
  2. Koniec zdania o zapisie mówi o lokalizacji ORAZ o porannych/popołudniowych
     raportach (wcześniej tylko „bez podawania miasta”),
  3. Usunięte „Przykłady:” z czerwonymi miastami (backticki = render na czerwono),
  4. Komenda tipsów (/porady w PL, /tips w pozostałych) pisana zwykłym tekstem,
     więc jest klikalna — dokładnie tak, jak w /info.

Zakres: city_prompt + city_prompt_active × 6 języków. Promptów kart
jednorazowych (oneoff_prompt_*) celowo NIE ruszamy — pilnuje tego ostatni test.

Uruchomienie: pytest test_city_prompt.py -v
"""

import pytest

import i18n

LANGS = ("pl", "en", "de", "es", "fr", "no")

TIPS_CMD = {"pl": "/porady"}
HEADERS = {"pl": "📍 ZMIANA LOKALIZACJI"}
EXAMPLES_WORD = ("Przykłady", "Examples", "Beispiele", "Ejemplos", "Exemples", "Eksempler")
DETAILS_WORD = ("Szczegóły:", "Details:", "Détails :", "Detalles:", "Detaljer:")
REPORTS_WORD = {
    "pl": "poranne lub popołudniowe raporty pogodowe",
    "en": "morning or afternoon weather reports",
    "de": "morgendliche oder nachmittägliche Wetterberichte",
    "es": "informes meteorológicos por la mañana o por la tarde",
    "fr": "bulletins météo le matin ou l'après-midi",
    "no": "morgen- eller ettermiddagsrapporter",
}
ROUNDING_WORD = {
    "pl": "współrzędne są zaokrąglane",
    "en": "coordinates are rounded",
    "de": "Koordinaten werden gerundet",
    "es": "coordenadas se redondean",
    "fr": "coordonnées sont arrondies",
    "no": "koordinatene avrundes",
}
OLD_ENDINGS = ("bez podawania miasta", "without typing a city", "ohne Stadtangabe",
               "sin indicar la ciudad", "sans indiquer de ville", "uten at du skriver byen")


def _tips(lang):
    return TIPS_CMD.get(lang, "/tips")


def _texts(lang):
    """(wariant bez lokalizacji, wariant z lokalizacją) — z podstawionym miastem."""
    data = i18n.UI_TEXTS[lang]
    return data["city_prompt"], data["city_prompt_active"].format(city="Testowo")


# ============================================================================
# 1. PINEZKA Z MAPY + AKAPIT "SZCZEGÓŁY:" Z KOMENDĄ TIPSÓW
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_description_mentions_map_pin_and_gps_on_one_line(lang):
    for text in _texts(lang):
        paragraph = next(l for l in text.split("\n")
                         if "GPS" in l and any(w in l for w in DETAILS_WORD))
        assert "pin" in paragraph.lower() or "nål" in paragraph.lower() \
            or "épingle" in paragraph.lower() or "pinezk" in paragraph.lower(), paragraph


@pytest.mark.parametrize("lang", LANGS)
def test_tips_command_on_its_own_line_right_after_details(lang):
    for text in _texts(lang):
        lines = text.split("\n")
        idx = lines.index(_tips(lang))
        assert lines[idx - 1].strip().endswith(":"), "komenda tipsów ma być w osobnej linii"
        assert any(w in lines[idx - 1] for w in DETAILS_WORD), lines[idx - 1]


# ============================================================================
# 2. ZDANIE O ZAPISIE: LOKALIZACJA + RAPORTY
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_saving_sentence_mentions_reports(lang):
    for text in _texts(lang):
        assert REPORTS_WORD[lang] in text, f"{lang}: brak wzmianki o porannych/popołudniowych raportach"


@pytest.mark.parametrize("lang", LANGS)
def test_old_city_only_ending_removed(lang):
    for text in _texts(lang):
        for old in OLD_ENDINGS:
            assert old not in text, f"{lang}: zostało stare zakończenie: {old!r}"


# ============================================================================
# 3. USUNIĘTE "PRZYKŁADY:" I RENDER CZERWONYCH MIAST
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_examples_block_removed(lang):
    for text in _texts(lang):
        for word in EXAMPLES_WORD:
            assert word not in text, f"{lang}: zostało 'Przykłady:' ({word})"
        assert "`" not in text, f"{lang}: backticki (render na czerwono) zostały w treści"


# ============================================================================
# 4. RESZTA KOMUNIKATU BEZ ZMIAN
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_structure_and_safety_texts_intact(lang):
    for text in _texts(lang):
        lines = text.split("\n")
        assert lines[0] == HEADERS.get(lang, lines[0]), "nagłówek komunikatu zmieniony"
        assert lines[1] == ""
        # komendy prognoz: PL aliasy, pozostałe języki kanoniczne
        for cmd in (("/dzien", "/teraz", "/trend") if lang == "pl" else ("/day", "/now", "/trend")):
            assert f"\n{cmd}\n" in text, f"{lang}: zniknęła komenda {cmd}"
        assert ROUNDING_WORD[lang] in text, f"{lang}: zniknęła informacja o zaokrąglaniu"
        assert "GPS" in text


@pytest.mark.parametrize("lang", LANGS)
def test_markdown_parse_mode_safe(lang):
    """send_reply leci z parse_mode=Markdown — brak niedomkniętych znaczników."""
    for text in _texts(lang):
        assert text.count("**") % 2 == 0
        assert text.count("_") % 2 == 0


# ============================================================================
# 5. ZAKRES POPRAWKI: oneoff_prompt_* świadomie BEZ ZMIAN
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_oneoff_prompts_untouched_for_now(lang):
    data = i18n.UI_TEXTS[lang]
    for key in ("oneoff_prompt_day", "oneoff_prompt_now", "oneoff_prompt_future"):
        assert any(w in data[key] for w in EXAMPLES_WORD), \
            f"{lang}:{key} — ten prompt miał zostać bez zmian w tej poprawce"
