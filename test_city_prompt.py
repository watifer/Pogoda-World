"""test_city_prompt.py — komunikat /miasto (city_prompt / city_prompt_active).

Aktualna, skrócona wersja komunikatu (teksty właściciela produktu):

    📍 ZMIANA LOKALIZACJI

    Obecna lokalizacja:
    brak            ← albo {city} w wariancie city_prompt_active

    Jeśli zgadzasz się na zapis lokalizacji:
    👉 Wpisz nazwę miejscowości lub użyj GPS:

Co pilnujemy:
  1. identyczna, 7-liniowa struktura w obu wariantach i we wszystkich językach,
     różnica wyłącznie w linii 4 („brak” vs {city}),
  2. linia zgody na zapis + linia z 👉 i GPS (klawiatura GPS dochodzi w kodzie),
  3. brak „Przykłady:” / backticków (render na czerwono) i brak starych,
     dłuższych zakończeń,
  4. bezpieczeństwo legacy Markdown (send_reply leci z parse_mode=Markdown),
  5. prompty kart jednorazowych (oneoff_prompt_*) celowo BEZ ZMIAN.

Uruchomienie: pytest test_city_prompt.py -v
"""

import pytest

import i18n

LANGS = ("pl", "en", "de", "es", "fr", "no")

HEADERS = {
    "pl": "📍 ZMIANA LOKALIZACJI",
    "en": "📍 CHANGE LOCATION",
    "de": "📍 STANDORT ÄNDERN",
    "es": "📍 CAMBIAR UBICACIÓN",
    "fr": "📍 CHANGER DE POSITION",
    "no": "📍 ENDRE PLASSERING",
}
CURRENT_LABEL = {
    "pl": "Obecna lokalizacja:",
    "en": "Current location:",
    "de": "Aktueller Standort:",
    "es": "Ubicación actual:",
    "fr": "Position actuelle :",
    "no": "Nåværende plassering:",
}
NONE_WORD = {
    "pl": "brak", "en": "none", "de": "keiner",
    "es": "ninguna", "fr": "aucune", "no": "ingen",
}
CONSENT_WORD = {
    "pl": "zgadzasz się na zapis lokalizacji",
    "en": "agree to save your location",
    "de": "Speichern des Standorts einverstanden",
    "es": "aceptas que guardemos la ubicación",
    "fr": "acceptez l'enregistrement de la position",
    "no": "samtykker til å lagre posisjonen",
}
EXAMPLES_WORD = ("Przykłady", "Examples", "Beispiele", "Ejemplos", "Exemples", "Eksempler")
OLD_ENDINGS = ("bez podawania miasta", "without typing a city", "ohne Stadtangabe",
               "sin indicar la ciudad", "sans indiquer de ville", "uten at du skriver byen")
OLD_DETAILS = ("Szczegóły:", "Details:", "Détails :", "Detalles:", "Detaljer:")


def _texts(lang):
    """(wariant bez lokalizacji, wariant z lokalizacją) — z podstawionym miastem."""
    data = i18n.UI_TEXTS[lang]
    return data["city_prompt"], data["city_prompt_active"].format(city="Testowo")


# ============================================================================
# 1. STRUKTURA: 7 LINII, RÓŻNICA TYLKO W LINII Z LOKALIZACJĄ
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_seven_line_structure(lang):
    for text in _texts(lang):
        lines = text.split("\n")
        assert len(lines) == 7, f"{lang}: oczekiwano 7 linii, jest {len(lines)}: {lines!r}"
        assert lines[0] == HEADERS[lang], "nagłówek komunikatu zmieniony"
        assert lines[1] == "" and lines[4] == "", "puste linie oddzielają trzy akapity"
        assert lines[2] == CURRENT_LABEL[lang]
        assert lines[6].startswith("👉 "), "ostatnia linia to wezwanie do działania z 👉"
        assert lines[6].rstrip().endswith(":"), "po dwukropku następuje odpowiedź użytkownika"
        assert "GPS" in lines[6]


@pytest.mark.parametrize("lang", LANGS)
def test_variants_differ_only_in_current_location_line(lang):
    data = i18n.UI_TEXTS[lang]
    plain = data["city_prompt"].split("\n")
    active = data["city_prompt_active"].split("\n")
    assert plain[3] == NONE_WORD[lang], f"{lang}: wariant bez profilu pokazuje {NONE_WORD[lang]!r}"
    assert active[3] == "{city}", f"{lang}: wariant aktywny ma placeholder {{city}}"
    assert plain[:3] == active[:3] and plain[4:] == active[4:], \
        f"{lang}: oba warianty mają być identyczne poza linią lokalizacji"


@pytest.mark.parametrize("lang", LANGS)
def test_placeholder_only_in_active_variant(lang):
    data = i18n.UI_TEXTS[lang]
    assert "{city}" not in data["city_prompt"]
    assert data["city_prompt_active"].count("{city}") == 1
    assert "{" not in data["city_prompt_active"].replace("{city}", ""), "brak innych placeholderów"
    assert "{" not in data["city_prompt"]


# ============================================================================
# 2. ZGODA NA ZAPIS + LINIA GPS
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_consent_sentence_right_before_call_to_action(lang):
    for text in _texts(lang):
        lines = text.split("\n")
        assert CONSENT_WORD[lang] in lines[5], f"{lang}: linia zgody na zapis: {lines[5]!r}"
        assert lines[5].rstrip().endswith(":")


# ============================================================================
# 3. BRAK „PRZYKŁADY:”, BACKTICKÓW I STARYCH, DŁUŻSZYCH ZAKOŃCZEŃ
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_examples_block_removed(lang):
    for text in _texts(lang):
        for word in EXAMPLES_WORD:
            assert word not in text, f"{lang}: zostało 'Przykłady:' ({word})"
        assert "`" not in text, f"{lang}: backticki (render na czerwono) zostały w treści"


@pytest.mark.parametrize("lang", LANGS)
def test_old_longer_wording_removed(lang):
    for text in _texts(lang):
        for old in OLD_ENDINGS + OLD_DETAILS:
            assert old not in text, f"{lang}: zostało stare sformułowanie: {old!r}"
        # Komendy prognoz i /porady są teraz w /info, nie w /miasto.
        for cmd in ("/dzien", "/day", "/teraz", "/now", "/trend", "/porady", "/tips"):
            assert cmd not in text, f"{lang}: komenda {cmd} nie należy już do /miasto"


# ============================================================================
# 4. BEZPIECZEŃSTWO LEGACY MARKDOWN
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_markdown_parse_mode_safe(lang):
    """send_reply leci z parse_mode=Markdown — brak niedomkniętych znaczników."""
    for text in _texts(lang):
        assert text.count("*") % 2 == 0
        assert text.count("_") % 2 == 0
        assert "[" not in text and "]" not in text


# ============================================================================
# 5. ZAKRES: oneoff_prompt_* świadomie BEZ ZMIAN
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_oneoff_prompts_untouched_for_now(lang):
    data = i18n.UI_TEXTS[lang]
    for key in ("oneoff_prompt_day", "oneoff_prompt_now", "oneoff_prompt_future"):
        assert any(w in data[key] for w in EXAMPLES_WORD), \
            f"{lang}:{key} — ten prompt miał zostać bez zmian"
