"""test_info_tips_audit.py — /info (info_msg) i /porady (porady_msg) po
rewizji tekstów właściciela produktu + audyt językowy tłumaczeń.

Co pilnujemy:
  1. /info: dokładnie taki tekst PL, jaki podał właściciel produktu; pozycja
     /zapros (/invite) tuż po linii /raport (/report); komend zarządzania
     danymi (/bezGPS, /usunDane, /save_location) w /info NIE ma — są w /dane,
  2. /porady: sześć sekcji z pogrubionym nagłówkiem (⚡ 🌍 📍 🌊 🌙 ⚠️), bez
     starych placeholderów godzin, pinezka tylko „na czacie prywatnym”,
  3. język sekcji == język treści (brak przecieków między językami,
     ES po hiszpańsku, a nie po francusku),
  4. zdrowie legacy Markdown: parzyste `*`, zero backticków.

Uruchomienie: pytest test_info_tips_audit.py -v
"""

import re

import pytest

import i18n

LANGS = ("pl", "en", "de", "es", "fr", "no")

# Komendy zarządzania danymi mieszkają w /dane — w /info ich nie reklamujemy.
MANAGEMENT_CMDS = ("/bezGPS", "/usunDane", "/forget\\_location", "/delete\\_me",
                   "/save_location", "/save\\_location")

INFO_TITLES = {
    "pl": "POMOC I KOMENDY",
    "en": "HELP & COMMANDS",
    "de": "HILFE UND BEFEHLE",
    "es": "AYUDA Y COMANDOS",
    "fr": "AIDE ET COMMANDES",
    "no": "HJELP OG KOMMANDOER",
}

TIPS_TITLES = {
    "pl": "PORADY I PRZYDATNE FUNKCJE",
    "en": "TIPS AND USEFUL FEATURES",
    "de": "TIPPS UND NÜTZLICHE FUNKTIONEN",
    "es": "CONSEJOS Y FUNCIONES ÚTILES",
    "fr": "ASTUCES ET FONCTIONS UTILES",
    "no": "TIPS OG NYTTIGE FUNKSJONER",
}

# Kolejność emoji nagłówków sekcji /porady — identyczna w każdym języku.
TIPS_SECTION_EMOJI = ("⚡", "🌍", "📍", "🌊", "🌙", "⚠️")

# Pinezka z mapy działa tylko w czacie prywatnym (pin_in_city_flow wymaga
# chat_id > 0) — tekst musi to mówić wprost, w każdym języku.
PRIVATE_CHAT_WORDS = {
    "pl": "Na czacie prywatnym",
    "en": "In a private chat",
    "de": "im privaten Chat",
    "es": "En el chat privado",
    "fr": "Dans le chat privé",
    "no": "I en privat chat",
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

PL_INFO_EXACT = (
    "ℹ️ *POMOC I KOMENDY*\n"
    "\n"
    "Prognozy pogody:\n"
    "☀️ /dzien — prognoza na dzisiaj\n"
    "📡 /teraz — prognoza na najbliższe 12 godzin\n"
    "🔮 /trend — trend pogody na 14 dni\n"
    "\n"
    "Ustawienia i dane:\n"
    "🌍 /miasto — zapis i zmiana lokalizacji\n"
    "⚙️ /raport — godziny raportów\n"
    "🤝 /zapros — zaproszenie znajomego lub dodanie bota do grupy\n"
    "📦 /dane — Twoje dane i zarządzanie\n"
    "📜 /priv — polityka prywatności\n"
    "\n"
    "═══════════════════════\n"
    "💡 /porady — triki, skróty i dodatkowe funkcje!\n"
    "═══════════════════════"
)


def _cmd(lang, pl, other):
    return pl if lang == "pl" else other


# ============================================================================
# 1. /info
# ============================================================================

def test_polish_info_is_exactly_as_requested():
    assert i18n.UI_TEXTS["pl"]["info_msg"] == PL_INFO_EXACT


@pytest.mark.parametrize("lang", LANGS)
def test_info_header_and_structure(lang):
    text = i18n.UI_TEXTS[lang]["info_msg"]
    lines = text.split("\n")
    assert INFO_TITLES[lang] in lines[0] and lines[0].startswith("ℹ️ *") and lines[0].endswith("*")
    assert len(lines) == len(PL_INFO_EXACT.split("\n")), \
        f"{lang}: inna liczba linii niż w PL — struktura /info ma być identyczna"
    # Te same emoji / separatory w tych samych liniach co w PL.
    for i, pl_line in enumerate(PL_INFO_EXACT.split("\n")):
        if pl_line and not pl_line[0].isalpha():
            assert lines[i].startswith(pl_line.split(" ")[0]), f"{lang}: linia {i}: {lines[i]!r}"
        elif not pl_line:
            assert lines[i] == "", f"{lang}: linia {i} ma być pusta"


@pytest.mark.parametrize("lang", LANGS)
def test_info_lists_canonical_commands_in_order(lang):
    text = i18n.UI_TEXTS[lang]["info_msg"]
    expected = [
        _cmd(lang, "/dzien", "/day"), _cmd(lang, "/teraz", "/now"), "/trend",
        _cmd(lang, "/miasto", "/city"), _cmd(lang, "/raport", "/report"),
        _cmd(lang, "/zapros", "/invite"), _cmd(lang, "/dane", "/data"),
        _cmd(lang, "/priv", "/privacy"), _cmd(lang, "/porady", "/tips"),
    ]
    found = re.findall(r"/[A-Za-z]+", text)
    assert found == expected, f"{lang}: komendy w /info: {found}"


@pytest.mark.parametrize("lang", LANGS)
def test_info_invite_line_right_after_report_line(lang):
    lines = i18n.UI_TEXTS[lang]["info_msg"].split("\n")
    report = next(i for i, l in enumerate(lines) if _cmd(lang, "/raport", "/report") in l)
    assert lines[report + 1].startswith("🤝 " + _cmd(lang, "/zapros", "/invite") + " — "), \
        f"{lang}: {lines[report + 1]!r}"


@pytest.mark.parametrize("lang", LANGS)
def test_info_does_not_advertise_management_commands(lang):
    text = i18n.UI_TEXTS[lang]["info_msg"]
    for cmd in MANAGEMENT_CMDS:
        assert cmd not in text, f"{lang}: {cmd} nie należy do /info (jest w /dane)"
    assert "save_location" not in text.replace("\\", "")


@pytest.mark.parametrize("lang", LANGS)
def test_info_day_line_says_today_only(lang):
    """D10: w /info „prognoza na dzisiaj” — bez „i noc”."""
    day_line = next(l for l in i18n.UI_TEXTS[lang]["info_msg"].split("\n")
                    if l.startswith("☀️ "))
    for night in (" i noc", " and night", " und Nacht", " y noche", " et la nuit", " og natt"):
        assert night not in day_line, f"{lang}: {day_line!r}"


# ============================================================================
# 2. /porady: sześć sekcji, własny język, pinezka tylko prywatnie
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_tips_header_matches_section_language(lang):
    header = i18n.UI_TEXTS[lang]["porady_msg"].split("\n")[0]
    assert header == f"💡 *{TIPS_TITLES[lang]}*", f"{lang}: nagłówek to {header!r}"


@pytest.mark.parametrize("lang", LANGS)
def test_six_bold_sections_in_fixed_order(lang):
    text = i18n.UI_TEXTS[lang]["porady_msg"]
    headers = [l for l in text.split("\n")[1:] if re.fullmatch(r"\S+\s\*[^*]+\*", l)]
    assert [h.split(" ")[0] for h in headers] == list(TIPS_SECTION_EMOJI), \
        f"{lang}: nagłówki sekcji: {headers}"
    # Stare, numerowane porady i placeholdery godzin zniknęły.
    assert not re.search(r"\*\d+\.\s", text), f"{lang}: została numerowana sekcja"
    assert "{" not in text and "}" not in text, f"{lang}: porady nie mają placeholderów"


@pytest.mark.parametrize("lang", LANGS)
def test_tips_shortcuts_section_uses_own_daily_letter(lang):
    """?d/?12/?14 (de: ?t, fr: ?j) z przykładowym miastem danego języka."""
    text = i18n.UI_TEXTS[lang]["porady_msg"]
    letter = {"de": "t", "fr": "j"}.get(lang, "d")
    city = {"pl": "Warszawa", "en": "Warsaw", "de": "Berlin",
            "es": "Madrid", "fr": "Paris", "no": "Oslo"}[lang]
    for prefix in (f"?{letter}", "?12", "?14"):
        assert f"• {prefix} {city} (" in text, f"{lang}: brak skrótu {prefix} {city}"
        assert f".{prefix[1:]} {city}" in text, f"{lang}: brak wariantu z kropką dla {prefix}"
    assert ".n " not in text and ".f " not in text, "legacy .n/.f nie wracają"


@pytest.mark.parametrize("lang", LANGS)
def test_tips_oneoff_examples_and_trend_command(lang):
    text = i18n.UI_TEXTS[lang]["porady_msg"]
    assert f"• {_cmd(lang, '/dzien', '/day')} " in text
    assert f"• {_cmd(lang, '/teraz', '/now')} Zakopane" in text
    assert "/trend" in text
    assert _cmd(lang, "/miasto", "/city") in text


@pytest.mark.parametrize("lang", LANGS)
def test_tips_pin_section_is_private_chat_only(lang):
    """D14: pinezka z mapy działa po /miasto tylko w czacie prywatnym."""
    text = i18n.UI_TEXTS[lang]["porady_msg"]
    start = text.index("📍 *")
    section = text[start:].split("\n\n", 1)[0]
    body = section.split("\n", 1)[1]
    assert PRIVATE_CHAT_WORDS[lang] in body, f"{lang}: {body!r}"
    assert _cmd(lang, "/miasto", "/city") in body
    assert "📎" in body
    assert "*" not in body, "treść sekcji (w tym słowo „Lokalizacja”) bez pogrubień"
    for group_word in ("grup", "group", "Gruppe", "groupe", "gruppe"):
        assert group_word not in body.lower(), f"{lang}: w sekcji pinezki nie ma instrukcji dla grup"


@pytest.mark.parametrize("lang", LANGS)
def test_tips_alerts_section_is_about_the_forecast_card(lang):
    """D5: alerty = ostrzeżenie w prognozie, nie osobne powiadomienia push."""
    text = i18n.UI_TEXTS[lang]["porady_msg"]
    body = text[text.index("⚠️ *"):].split("\n", 1)[1]
    for push in ("push", "powiadomienie", "notification", "Benachrichtigung",
                 "notificación", "varsling"):
        assert push.lower() not in body.lower(), f"{lang}: {body!r}"


@pytest.mark.parametrize("lang", LANGS)
def test_no_foreign_language_leak(lang):
    text = i18n.UI_TEXTS[lang]["porady_msg"].lower()
    foreign = [w for other, words in OWN_LANG_WORDS.items() if other != lang for w in words]
    hits = [w for w in foreign if re.search(r"\b" + re.escape(w) + r"\b", text)]
    assert hits == [], f"{lang}: obce słowa w poradach: {hits}"


def test_spanish_tips_are_spanish_not_french():
    """Regresja dawnego błędu: ES miał francuską treść."""
    text = i18n.UI_TEXTS["es"]["porady_msg"]
    for french in ("ASTUCES ET FONCTIONS", "Raccourcis", "Épingle", "Littoral",
                   "Vous pouvez", "Alertes automatiques"):
        assert french not in text, f"francuski fragment w ES: {french!r}"
    for spanish in ("CONSEJOS Y FUNCIONES ÚTILES", "Atajos instantáneos",
                    "Pin del mapa cómodo", "Módulo Costa", "Alertas automáticas"):
        assert spanish in text, f"brak hiszpańskiego fragmentu: {spanish!r}"
    assert "?d Madrid" in text and "?12 Madrid" in text and "?14 Madrid" in text


def test_spanish_tips_use_canonical_commands():
    text = i18n.UI_TEXTS["es"]["porady_msg"]
    for cmd in ("/day", "/now", "/city", "/trend"):
        assert cmd in text, f"ES: brak komendy {cmd}"


# ============================================================================
# 3. ZDROWIE FORMATOWANIA (legacy Markdown)
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", ["info_msg", "porady_msg"])
def test_markdown_is_balanced_and_has_no_backticks(lang, key):
    text = i18n.UI_TEXTS[lang][key]
    assert text.count("*") % 2 == 0, f"{lang}:{key}: niedomknięte pogrubienie"
    assert "`" not in text, f"{lang}:{key}: backtick psuje Markdown / render na czerwono"
    # Pogrubienia nie zawierają znaków nowej linii (legacy Markdown tego nie znosi).
    for bold in re.findall(r"\*([^*]+)\*", text):
        assert "\n" not in bold, f"{lang}:{key}: pogrubienie przez kilka linii: {bold!r}"
