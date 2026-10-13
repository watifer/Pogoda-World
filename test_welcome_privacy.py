"""test_welcome_privacy.py — ekran onboardingu (po aktywacji zaproszenia).

Kontekst: klucz `welcome_access` jest testowym proxy ekranu powitalnego.
Produkcja wysyła `access_granted` (patrz location_bot._main_bot_iteration),
a od wdrożenia D3 oba klucze mają identyczne wartości w każdym języku,
więc ten plik pilnuje treści, którą użytkownik naprawdę widzi.

Struktura zatwierdzona w Etapie 2 (jedna pusta linia między sekcjami,
komendy pod sobą bez pustych linii):

    ✅ Dostęp aktywny! Witaj w Pogoda World 🌍
                                          <- pusta linia (\n\n)
    🌤 Sprawdź pogodę:
    /dzien — prognoza na dziś (05:00–15:59)
    /teraz — najbliższe 12h
    /trend — kolejne 14 dni
                                          <- pusta linia (\n\n)
    🔔 Dla wygody lub codziennych raportów:
    Zapisz stałą lokalizację komendą /miasto
                                          <- pusta linia (\n\n)
    🔒 Prywatność i Twoje dane: /dane

Ustalenia, które nadal obowiązują:
  1. powitanie z emoji ✅ i nazwą świata 🌍,
  2. sekcja pogodowa z trzema komendami pod sobą,
  3. sekcja 🔔 z podpowiedzią zapisania lokalizacji,
  4. sekcja 🔒 z komendą prywatności/danych na końcu,
  5. komendy ZAWSZE zwykłym tekstem (bez backticków), żeby były klikalne,
  6. `welcome_access` == `access_granted` w każdym języku,
  7. brak niedomkniętych znaczników Markdown (send_reply leci
     z parse_mode=Markdown).

Uruchomienie: pytest test_welcome_privacy.py -v
"""

import re

import pytest

import i18n

LANGS = ("pl", "en", "de", "es", "fr", "no")
# Komendy zarejestrowane w menu Telegrama dla danego języka.
WEATHER_CMDS = {"pl": ("/dzien", "/teraz", "/trend"), "default": ("/day", "/now", "/trend")}
CITY_CMD = {"pl": "/miasto", "default": "/city"}
DATA_CMD = {"pl": "/dane", "default": "/data"}


def _onboarding(lang):
    return i18n.UI_TEXTS[lang]["welcome_access"]


def _lines(lang):
    return _onboarding(lang).split("\n")


# ============================================================================
# 0. KLUCZOWE: welcome_access == access_granted (D3, zadanie pkt 7)
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_welcome_access_is_identical_to_access_granted(lang):
    assert i18n.UI_TEXTS[lang]["welcome_access"] == i18n.UI_TEXTS[lang]["access_granted"]


# ============================================================================
# 1. POWITANIE
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_greeting_has_checkmark_and_world(lang):
    first = _lines(lang)[0]
    assert first.startswith("✅ "), f"{lang}: brak emoji ✅ na początku"
    assert "Pogoda World 🌍" in first, f"{lang}: brak nazwy świata w powitaniu"


# ============================================================================
# 2. SEKCJE ROZDZIELONE DOKŁADNIE JEDNĄ PUSTĄ LINIĄ (formatowanie Etapu 2)
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_sections_separated_by_exactly_one_blank_line(lang):
    text = _onboarding(lang)
    assert "\n\n\n" not in text, f"{lang}: więcej niż jedna pusta linia"
    assert "🌍\n\n🌤" in text, f"{lang}: brak pustej linii po powitaniu"
    assert "\n\n🔔" in text, f"{lang}: brak pustej linii przed sekcją 🔔"
    assert "\n\n🔒" in text, f"{lang}: brak pustej linii przed sekcją 🔒"


@pytest.mark.parametrize("lang", LANGS)
def test_label_is_followed_by_single_newline_then_command(lang):
    lines = _lines(lang)
    weather_label = next(i for i, l in enumerate(lines) if l.startswith("🌤"))
    bell_label = next(i for i, l in enumerate(lines) if l.startswith("🔔"))
    assert lines[weather_label + 1].startswith("/"), f"{lang}: etykiata nie bezpośrednio nad komendą"
    assert not lines[bell_label + 1] == "", f"{lang}: pusta linia pod etykietą 🔔"


# ============================================================================
# 3. KOMENDY POD SOBĄ, BEZ PUSTYCH LINII MIĘDZY NIMI
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_weather_commands_are_stacked_without_blank_lines(lang):
    expected = WEATHER_CMDS.get(lang, WEATHER_CMDS["default"])
    lines = _lines(lang)
    weather_label = next(i for i, l in enumerate(lines) if l.startswith("🌤"))
    block = lines[weather_label + 1:weather_label + 4]
    for cmd, line in zip(expected, block):
        assert line.startswith(cmd), f"{lang}: oczekiwano {cmd}, jest {line!r}"
    # żadna pusta linia pomiędzy komendami w jednej sekcji
    assert not re.search(r"/[a-z]+[^\n]*\n\n/[a-z]+", _onboarding(lang)), \
        f"{lang}: pusta linia pomiędzy komendami"


@pytest.mark.parametrize("lang", LANGS)
def test_sections_are_not_glued_together(lang):
    """Niedopuszczalne np. '/trend — kolejne 14 dni🔔'."""
    text = _onboarding(lang)
    assert not re.search(r"/[a-z]+[^\n]{0,6}?[🔔🔒🌤⚙️✅]", text), \
        f"{lang}: sekcje sklejone bez pustej linii"


# ============================================================================
# 4. SEKCJA LOKALIZACJI I SEKCJA PRYWATNOŚCI
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_bell_section_mentions_saving_location(lang):
    lines = _lines(lang)
    idx = next(i for i, l in enumerate(lines) if l.startswith("🔔"))
    body = lines[idx + 1]
    assert body, f"{lang}: pusta treść pod etykietą 🔔"
    assert CITY_CMD.get(lang, CITY_CMD["default"]) in body, \
        f"{lang}: brak komendy zapisania lokalizacji w sekcji 🔔"


@pytest.mark.parametrize("lang", LANGS)
def test_footer_is_privacy_and_data_section(lang):
    lines = [l for l in _lines(lang) if l.strip()]
    assert lines[-1].startswith("🔒"), f"{lang}: stopka to nie sekcja prywatności"
    assert lines[-1].endswith(DATA_CMD.get(lang, DATA_CMD["default"])), \
        f"{lang}: sekcja prywatności nie kończy się komendą danych"


# ============================================================================
# 5. TREŚĆ W JĘZYKU UŻYTKOWNIKA, NIE KOPIA PL
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_onboarding_is_language_specific_not_shared_pl_text(lang):
    if lang == "pl":
        pytest.skip("polski jest językiem źródłowym")
    text = _onboarding(lang)
    assert "Twoje ustawienia" not in text, f"{lang}: polska zapowiedź w obcym języku"
    assert "Sprawdź pogodę" not in text, f"{lang}: polska etykieta pogodowa"
    assert "Dla wygody" not in text, f"{lang}: polska zapowiedź sekcji 🔔"


# ============================================================================
# 6. KLIKALNOŚĆ: komendy w tekście, nie w backtickach (wartość z audytu)
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_commands_are_plain_text_not_backticked(lang):
    text = _onboarding(lang)
    assert "`" not in text, f"{lang}: backtick unieważnia klikalność komend"
    assert not re.search(r"`/", text), f"{lang}: komenda w backtickach jest nieklikalna"
    assert "\\/" not in text, f"{lang}: escapowany slash"
    assert not re.search(r"</?[a-zA-Z][^>]*>", text), f"{lang}: HTML w komendach"


@pytest.mark.parametrize("lang", LANGS)
def test_every_command_is_clickable(lang):
    """Telegram auto-linkuje /komendę na początku linii lub po spacji."""
    text = _onboarding(lang)
    found = []
    for m in re.finditer(r"(?<!\S)/[a-zA-Z]+", text):
        start, end = m.start(), m.end()
        nxt = text[end] if end < len(text) else "\n"
        assert nxt.isspace() or not nxt.isalpha(), \
            f"{lang}: /{m.group(0)[1:]} nie jest wykonalne jako encja bot_command"
        found.append(m.group(0))
    assert found, f"{lang}: brak komend w onboardingu"


@pytest.mark.parametrize("lang", LANGS)
def test_command_count_is_symmetric_across_languages(lang):
    """Ta sama liczba komend PL <-> non-PL (ta sama funkcjonalność)."""
    count = len(re.findall(r"(?<!\S)/[a-zA-Z]+", _onboarding(lang)))
    assert count == len(re.findall(r"(?<!\S)/[a-zA-Z]+", _onboarding("pl"))), \
        f"{lang}: inna liczba komend niż w polskim"


# ============================================================================
# 7. MARKDOWN I str.format()
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_markdown_markers_are_balanced(lang):
    """send_reply leci z parse_mode=Markdown — żadnych niedomkniętych znaczników."""
    text = _onboarding(lang)
    assert text.count("**") % 2 == 0, f"{lang}: niedomknięte pogrubienie"
    assert text.count("_") % 2 == 0, f"{lang}: niedomknięta kursywa"


@pytest.mark.parametrize("lang", LANGS)
def test_no_stray_braces_outside_placeholders(lang):
    """Renderowanie idzie przez str.format() — klamry tylko jako placeholdery."""
    text = _onboarding(lang)
    for brace in re.findall(r"\{[^}]*\}", text):
        assert brace == "{public_code}", f"{lang}: nieuzgodniony placeholder {brace}"
    stripped = text.replace("{public_code}", "X")
    assert "{" not in stripped and "}" not in stripped, f"{lang}: klamry poza placeholderem"
    # realne renderowanie nie może rzucić wyjątku
    assert i18n.t_ui(lang, "welcome_access")