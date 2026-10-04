"""test_info_tips_audit.py — POPRAWKA #5: /info bez /save_location + audyt językowy.

Zgłoszenia właściciela produktu:
  1. W /info (info_msg) znika pozycja /save_location — zostają wyłącznie dwie
     rzadziej używane komendy: usunięcie samej lokalizacji i usunięcie konta,
  2. porady_msg w sekcji "es" był po FRANCUSKU (ktoś przetłumaczył tylko ostatni
     akapit) — sekcje 1-9 dostały hiszpańskie tłumaczenie,
  3. przy okazji audytu znalazłem literówkę w FR: "mx`ise à jour" — backtick
     w środku zdania rozwalał formatowanie Markdown całej wiadomości.

Ten plik pilnuje, żeby żaden z tych trzech błędów nie wrócił, i robi lekki
audyt spójności językowej wszystkich porady_msg.

Uruchomienie: pytest test_info_tips_audit.py -v
"""

import re

import pytest

import i18n

LANGS = ("pl", "en", "de", "es", "fr", "no")

# Komendy "rzadziej używane" — dwie pozycje, które MUSZĄ zostać
MANAGEMENT_CMDS = {
    "pl": ("/bezGPS", "/usunDane"),
    "en": ("/forget\\_location", "/delete\\_me"),
    "de": ("/forget\\_location", "/delete\\_me"),
    "es": ("/forget\\_location", "/delete\\_me"),
    "fr": ("/forget\\_location", "/delete\\_me"),
    "no": ("/forget\\_location", "/delete\\_me"),
}

TIPS_TITLES = {
    "pl": "PORADY I TRIKI",
    "en": "TIPS AND TRICKS",
    "de": "TIPPS UND TRICKS",
    "es": "CONSEJOS Y TRUCOS",
    "fr": "TRUCS ET ASTUCES",
    "no": "TIPS OG TRIKS",
}

# Charakterystyczne słowa KAŻDEGO języka — wykrywanie "przecieków" polega na
# sprawdzeniu, czy w sekcji danego języka nie pojawiają się słowa z pozostałych.
OWN_LANG_WORDS = {
    "pl": ("wpisz", "wybierz", "użyj", "twoj"),
    "en": ("you can", "your", "please", "settings"),
    "de": ("dein", "nutze", "wähle", "verwende"),
    "es": ("puedes", "tus", "ajustes", "elige"),
    "fr": ("vous", "votre", "sélectionnez", "exploitez"),
    "no": ("bruk", "dine", "velg"),
}


# ============================================================================
# 1. /info: koniec z /save_location
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_info_does_not_advertise_save_location(lang):
    text = i18n.UI_TEXTS[lang]["info_msg"]
    assert "save_location" not in text.replace("\\", ""), \
        f"{lang}: /save_location wrócił do /info"


@pytest.mark.parametrize("lang", LANGS)
def test_info_keeps_the_two_management_commands(lang):
    text = i18n.UI_TEXTS[lang]["info_msg"]
    for cmd in MANAGEMENT_CMDS[lang]:
        assert cmd in text, f"{lang}: zniknęła komenda {cmd}"


def test_polish_info_tail_is_exactly_as_requested():
    text = i18n.UI_TEXTS["pl"]["info_msg"]
    tail = "*Komendy rzadziej używane:*\n" + text.split("*Komendy rzadziej używane:*\n", 1)[1]
    assert tail.split("\n\n")[0] == (
        "*Komendy rzadziej używane:*\n"
        "/bezGPS — usuwa zapisaną lokalizację, dostęp zostaje\n"
        "/usunDane — usuwa wszystkie dane i dostęp do bota (pyta o potwierdzenie)"
    )


# ============================================================================
# 2. porady_msg: język sekcji == język treści
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_tips_header_matches_section_language(lang):
    header = i18n.UI_TEXTS[lang]["porady_msg"].split("\n")[0]
    assert TIPS_TITLES[lang] in header, f"{lang}: nagłówek to {header!r}"


@pytest.mark.parametrize("lang", LANGS)
def test_ten_sections_with_own_language(lang):
    text = i18n.UI_TEXTS[lang]["porady_msg"]
    numbers = re.findall(r"\*(\d+)\.\s", text)
    assert numbers == [str(i) for i in range(1, 11)], \
        f"{lang}: sekcje porad to {numbers}"
    assert "{default_rano}" in text and "{default_wieczor}" in text


@pytest.mark.parametrize("lang", LANGS)
def test_no_foreign_language_leak(lang):
    text = i18n.UI_TEXTS[lang]["porady_msg"].lower()
    foreign = [w for other, words in OWN_LANG_WORDS.items() if other != lang for w in words]
    hits = [w for w in foreign if re.search(r"\b" + re.escape(w) + r"\b", text)]
    assert hits == [], f"{lang}: obce słowa w poradach: {hits}"


def test_spanish_tips_are_spanish_not_french():
    """Regresja zgłoszonego błędu: ES miał francuską treść sekcji 1-9."""
    text = i18n.UI_TEXTS["es"]["porady_msg"]
    for french in ("TRUCS ET ASTUCES", "Exploitez", "Matinées calmes",
                   "Rapport supplémentaire", "Vous avez déjà reçu"):
        assert french not in text, f"francuski fragment w ES: {french!r}"
    for spanish in ("CONSEJOS Y TRUCOS", "Aprovecha", "Mañanas tranquilas",
                    "Informe adicional", "Privacidad y seguridad"):
        assert spanish in text, f"brak hiszpańskiego fragmentu: {spanish!r}"
    # sekcja 10: POPRAWKA #6 — promujemy ?d / ?12 / ?14 (bez legacy .n/.f/.d)
    assert "Atajos instantáneos" in text and "?d Madrid" in text
    assert "?12 Madrid" in text and "?14 Madrid" in text


def test_spanish_tips_use_canonical_commands():
    text = i18n.UI_TEXTS["es"]["porady_msg"]
    for cmd in ("/day", "/now", "/report", "/city", "/invite"):
        assert cmd in text, f"ES: brak komendy {cmd}"


# ============================================================================
# 3. ZDROWIE FORMATOWANIA (Markdown) — nie tylko w ES
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_tips_markdown_is_balanced(lang):
    text = i18n.UI_TEXTS[lang]["porady_msg"]
    assert text.count("*") % 2 == 0, f"{lang}: niedomknięte pogrubienie w poradach"
    assert text.count("`") % 2 == 0, f"{lang}: niedomknięty code-span w poradach"


def test_french_typo_is_fixed():
    """FR: 'mx`ise à jour' psuło Markdown — ma być 'mise à jour'."""
    text = i18n.UI_TEXTS["fr"]["porady_msg"]
    assert "mx`ise" not in text
    assert "une mise à jour" in text
