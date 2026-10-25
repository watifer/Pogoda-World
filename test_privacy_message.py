"""Checks the privacy-message content for every supported UI language."""

import re

import pytest

import i18n
import location_bot as lb

LANGS = ("pl", "en", "de", "es", "fr", "no")

SECTIONS = {
    "pl": (
        "📦 *Co przechowujemy?*",
        "🌐 *Komu przekazujemy dane?*",
        "🛠 *Zarządzanie:*",
    ),
    "en": (
        "📦 *What do we store?*",
        "🌐 *Who receives data?*",
        "🛠 *Management:*",
    ),
    "de": (
        "📦 *Was speichern wir?*",
        "🌐 *Wer erhält Daten?*",
        "🛠 *Verwaltung:*",
    ),
    "es": (
        "📦 *¿Qué guardamos?*",
        "🌐 *¿Quién recibe los datos?*",
        "🛠 *Gestión:*",
    ),
    "fr": (
        "📦 *Que conservons-nous ?*",
        "🌐 *Qui reçoit les données ?*",
        "🛠 *Gestion :*",
    ),
    "no": (
        "📦 *Hva lagrer vi?*",
        "🌐 *Hvem mottar data?*",
        "🛠 *Administrasjon:*",
    ),
}

EXPECTED_COMMANDS = {
    "pl": {"/miasto", "/dane", "/bezGPS", "/usunDane"},
    "en": {"/city", "/data", "/forget_location", "/delete_me"},
    "de": {"/city", "/data", "/forget_location", "/delete_me"},
    "es": {"/city", "/data", "/forget_location", "/delete_me"},
    "fr": {"/city", "/data", "/forget_location", "/delete_me"},
    "no": {"/city", "/data", "/forget_location", "/delete_me"},
}

POLICY_LINK_LABELS = {
    "pl": "Pełna polityka prywatności",
    "en": "Full privacy policy",
    "de": "Vollständige Datenschutzerklärung",
    "es": "Política de privacidad completa",
    "fr": "Politique de confidentialité complète",
    "no": "Fullstendig personvernerklæring",
}

CITY_COMMANDS = {"/city", "/miasto", "/loc"}


@pytest.mark.parametrize("lang", LANGS)
def test_privacy_message_has_no_policy_link_or_url(lang):
    text = i18n.UI_TEXTS[lang]["privacy_msg"]
    rendered = i18n.t_ui(lang, "privacy_msg")

    assert "{url}" not in text
    assert not re.search(r"https?://", text, flags=re.IGNORECASE)
    assert POLICY_LINK_LABELS[lang] not in text
    assert "{url}" not in rendered
    assert not re.search(r"https?://", rendered, flags=re.IGNORECASE)


@pytest.mark.parametrize("lang", LANGS)
def test_privacy_message_contains_localized_sections(lang):
    text = i18n.UI_TEXTS[lang]["privacy_msg"]
    for section in SECTIONS[lang]:
        assert section in text, f"{lang}: missing section {section!r}"


@pytest.mark.parametrize("lang", LANGS)
def test_privacy_bullets_have_no_tabs_and_exactly_one_space(lang):
    text = i18n.UI_TEXTS[lang]["privacy_msg"]
    assert "\t" not in text

    bullets = [line for line in text.splitlines() if "•" in line]
    assert len(bullets) == 7, f"{lang}: unexpected bullets {bullets}"
    for line in bullets:
        assert line.startswith("• "), line
        assert len(line) > 2 and line[2] != " ", line
        assert line.count("•") == 1, line


@pytest.mark.parametrize("lang", LANGS)
def test_privacy_commands_match_supported_backend_commands(lang):
    text = i18n.UI_TEXTS[lang]["privacy_msg"].replace(r"\_", "_")
    commands = set(re.findall(r"/[A-Za-z_]+", text))
    assert commands == EXPECTED_COMMANDS[lang], f"{lang}: {commands}"

    for command in commands:
        normalized = command.lower()
        canonical = lb.COMMAND_ALIASES.get(normalized, normalized)
        supported = (
            normalized in lb.COMMAND_ALIASES
            or canonical in lb.PRIVACY_COMMANDS
            or normalized in CITY_COMMANDS
        )
        assert supported, f"{lang}: unsupported command {command}"
