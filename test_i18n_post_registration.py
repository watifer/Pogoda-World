"""test_i18n_post_registration.py — spójność 11 kluczy UI po rewizji tekstów
„po rejestracji” (teksty właściciela produktu + tłumaczenia × 6 języków).

Klucze objęte rewizją:
    info_msg, porady_msg, city_prompt, city_prompt_active, location_saved,
    search_success, geocode_no_match, search_fail, report_dialog,
    report_bad_answer, privacy_msg
(+ prep_main / scanning / prep_future / search_loc używane w D13 — bez zmian
treści, ale pilnujemy, że istnieją i są czyste).

Co sprawdzamy:
  1. parytet placeholderów: każdy język ma dokładnie ten sam zestaw {pól} co PL,
  2. legacy Markdown: parzyste „*”, brak backticków, pogrubienia w jednej linii,
     brak surowych nawiasów klamrowych poza placeholderami,
  3. pary bliźniacze (osobne klucze, identyczna treść):
     location_saved == search_success, geocode_no_match == search_fail,
  4. przykłady doprecyzowania nazwy (D6/D12): „(np. A, kontekst, region lub
     A KOD)” w języku i kraju danego języka; DE/ES/FR/NO bez polskich nazw
     i bez „koło”; kod pocztowy z przykładu akceptuje _GEOCODE_POSTCODE_RE,
  5. komendy: PL aliasy, pozostałe języki kanoniczne; żadnych nieużywanych
     kluczy (info_main_help / tips_help_msg nie istnieją).

Uruchomienie: pytest test_i18n_post_registration.py -v
"""

import re

import pytest

import i18n
import location_bot as lb

LANGS = ("pl", "en", "de", "es", "fr", "no")
OTHER = ("en", "de", "es", "fr", "no")

REVISED_KEYS = (
    "info_msg", "porady_msg", "city_prompt", "city_prompt_active",
    "location_saved", "search_success", "geocode_no_match", "search_fail",
    "report_dialog", "report_bad_answer", "privacy_msg",
)
WAIT_KEYS = ("prep_main", "scanning", "prep_future", "search_loc")

EXPECTED_PLACEHOLDERS = {
    "info_msg": set(), "porady_msg": set(), "city_prompt": set(),
    "city_prompt_active": {"city"},
    "location_saved": {"display_location"}, "search_success": {"display_location"},
    "geocode_no_match": set(), "search_fail": set(),
    "report_dialog": {"morning", "afternoon", "yes", "no"},
    "report_bad_answer": {"yes", "no"},
    "privacy_msg": {"url"},
}

# „np.” / „lub” w każdym języku — nawias z przykładami doprecyzowania.
EXAMPLE_MARKERS = {
    "pl": ("np. ", " lub "),
    "en": ("e.g. ", " or "),
    "de": ("z. B. ", " oder "),
    "es": ("p. ej. ", " o "),
    "fr": ("par ex. ", " ou "),
    "no": ("f.eks. ", " eller "),
}
# Przykłady muszą pochodzić z kraju danego języka (ten sam zestaw co w i18n).
EXPECTED_EXAMPLES = {
    "pl": ("Olsztyn, koło Częstochowa, śląskie lub Olsztyn 42-256",
           "Ameryka, koło Olsztynek, Olsztyn lub Ameryka 11-015"),
    "en": ("Springfield, near Decatur, Illinois or Springfield 62701",
           "Portland, near Lewiston, Maine or Portland 04101"),
    "de": ("Neustadt, Weinstraße, Rheinland-Pfalz oder Neustadt 67433",
           "Frankenberg, Eder, Hessen oder Frankenberg 35066"),
    "es": ("Alcúdia, Mallorca, Islas Baleares o Alcúdia 07400",
           "Mora, Toledo, Castilla-La Mancha o Mora 45400"),
    "fr": ("Valence, Drôme, Auvergne-Rhône-Alpes ou Valence 26000",
           "Vernon, Eure, Normandie ou Vernon 27200"),
    "no": ("Våler, Solør, Innlandet eller Våler 2436",
           "Herøy, Helgeland, Nordland eller Herøy 8850"),
}
POLISH_LEAK = ("koło", "Olsztyn", "Częstochow", "Ameryka", "śląskie", "Warszaw", "Kraków")

PL_COMMANDS = ("/dzien", "/teraz", "/trend", "/miasto", "/raport", "/zapros",
               "/dane", "/priv", "/porady", "/bezGPS", "/usunDane")
CANON_COMMANDS = ("/day", "/now", "/trend", "/city", "/report", "/invite",
                  "/data", "/privacy", "/tips", "/forget\\_location", "/delete\\_me")


def _placeholders(text):
    return set(re.findall(r"\{(\w+)\}", text))


# ============================================================================
# 1. PARYTET PLACEHOLDERÓW I ISTNIENIE KLUCZY
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", REVISED_KEYS + WAIT_KEYS)
def test_key_exists_and_is_non_empty(lang, key):
    text = i18n.UI_TEXTS[lang][key]
    assert isinstance(text, str) and text.strip()
    assert text == text.strip("\n"), f"{lang}:{key}: zbędne puste linie na brzegach"


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", REVISED_KEYS)
def test_placeholders_match_polish_and_expectation(lang, key):
    assert _placeholders(i18n.UI_TEXTS[lang][key]) == EXPECTED_PLACEHOLDERS[key], \
        f"{lang}:{key}"
    assert _placeholders(i18n.UI_TEXTS[lang][key]) == _placeholders(i18n.UI_TEXTS["pl"][key])


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", REVISED_KEYS + WAIT_KEYS)
def test_t_ui_renders_without_error(lang, key):
    kwargs = {"city": "X", "display_location": "X, Y", "morning": "08:00",
              "afternoon": "14:00", "yes": "y", "no": "n", "url": "https://x/"}
    out = i18n.t_ui(lang, key, **kwargs)
    assert "{" not in out and "}" not in out, f"{lang}:{key}: surowe nawiasy klamrowe"


def test_no_unused_or_duplicate_help_keys():
    for lang in LANGS:
        for stale in ("info_main_help", "tips_help_msg", "location_saved_short",
                      "search_success_short"):
            assert stale not in i18n.UI_TEXTS[lang], f"{lang}: nieużywany klucz {stale}"
    src = open(lb.__file__, encoding="utf-8").read()
    for key in REVISED_KEYS + WAIT_KEYS:
        assert f'"{key}"' in src or key in ("search_fail", "geocode_no_match"), \
            f"{key} nie jest używany w location_bot.py"
    # search_fail / geocode_no_match idą przez t_geocode (GEOCODE_UI_KEYS).
    assert i18n.GEOCODE_UI_KEYS[i18n.GEOCODE_NOT_FOUND] == "search_fail"
    assert i18n.GEOCODE_UI_KEYS[i18n.GEOCODE_NO_MATCH] == "geocode_no_match"


# ============================================================================
# 2. LEGACY MARKDOWN
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", REVISED_KEYS + WAIT_KEYS)
def test_markdown_is_safe(lang, key):
    text = i18n.UI_TEXTS[lang][key]
    assert text.count("*") % 2 == 0, f"{lang}:{key}: niedomknięte pogrubienie"
    assert "`" not in text, f"{lang}:{key}: backtick"
    assert "[" not in text and "]" not in text, f"{lang}:{key}: nawiasy kwadratowe (link Markdown)"
    for bold in re.findall(r"\*([^*]*)\*", text):
        assert bold.strip() and "\n" not in bold, f"{lang}:{key}: pogrubienie {bold!r}"
    # Podkreślenia tylko w escapowanych komendach (/forget\_location, /delete\_me)
    # i w nazwach placeholderów (podstawiane wartości escapuje _md_safe).
    stripped = re.sub(r"\{\w+\}", "", text).replace("\\_", "")
    assert "_" not in stripped, f"{lang}:{key}: nieescapowane „_”"


@pytest.mark.parametrize("lang", LANGS)
def test_headers_are_bold_single_line(lang):
    for key in ("info_msg", "porady_msg", "privacy_msg", "report_dialog"):
        first = i18n.UI_TEXTS[lang][key].split("\n")[0]
        assert re.fullmatch(r"\S+ \*[^*\n]+\*", first), f"{lang}:{key}: {first!r}"
    for key in ("location_saved", "search_success"):
        first = i18n.UI_TEXTS[lang][key].split("\n")[0]
        assert first.startswith("✅ *") and first.endswith("*"), f"{lang}:{key}: {first!r}"


# ============================================================================
# 3. PARY BLIŹNIACZE
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_twin_keys_have_identical_text(lang):
    data = i18n.UI_TEXTS[lang]
    assert data["location_saved"] == data["search_success"], f"{lang}: D11"
    assert data["geocode_no_match"] == data["search_fail"], f"{lang}: jeden tekst „nie ma takiego miejsca”"
    assert data["location_saved"] != data["used_location"], \
        f"{lang}: „Zapisana” i „Użyta” lokalizacja to różne komunikaty"


@pytest.mark.parametrize("lang", LANGS)
def test_saved_location_message_structure(lang):
    text = i18n.UI_TEXTS[lang]["location_saved"]
    lines = text.split("\n")
    assert lines[1] == "{display_location}" and lines[2] == ""
    assert len(lines) == 4
    assert (("/miasto" in lines[3]) if lang == "pl" else ("/city" in lines[3]))


@pytest.mark.parametrize("lang", LANGS)
def test_no_match_message_structure(lang):
    text = i18n.UI_TEXTS[lang]["geocode_no_match"]
    lines = text.split("\n")
    assert lines[0].startswith("❌ ") and lines[1] == "" and len(lines) == 3
    assert (("/miasto" in lines[2]) if lang == "pl" else ("/city" in lines[2]))


# ============================================================================
# 4. PRZYKŁADY DOPRECYZOWANIA NAZWY (D6/D12)
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_examples_are_exactly_the_agreed_ones(lang):
    saved, no_match = EXPECTED_EXAMPLES[lang]
    eg = EXAMPLE_MARKERS[lang][0]
    assert f"({eg}{saved})." in i18n.UI_TEXTS[lang]["location_saved"]
    assert f"({eg}{no_match})." in i18n.UI_TEXTS[lang]["geocode_no_match"]
    assert saved != no_match, "dwa różne przykłady: po zapisie i po braku dopasowania"


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", ("location_saved", "geocode_no_match"))
def test_example_shape_region_then_postcode(lang, key):
    """„(np. A, kontekst, region lub A KOD)” — nazwa, dwa doprecyzowania, kod."""
    text = i18n.UI_TEXTS[lang][key]
    eg, or_word = EXAMPLE_MARKERS[lang]
    match = re.search(r"\(" + re.escape(eg) + r"(.+?)\)\.?$", text, flags=re.MULTILINE)
    assert match, f"{lang}:{key}: brak nawiasu z przykładami"
    inside = match.group(1)
    assert inside.count(or_word) == 1, f"{lang}:{key}: {inside!r}"
    left, right = inside.split(or_word)
    parts = [p.strip() for p in left.split(",")]
    assert len(parts) == 3, f"{lang}:{key}: {parts}"
    name = parts[0]
    assert right.startswith(name + " "), f"{lang}:{key}: wariant z kodem używa tej samej nazwy"
    postcode = right[len(name) + 1:].strip()
    assert lb._GEOCODE_POSTCODE_RE.fullmatch(postcode), \
        f"{lang}:{key}: kod {postcode!r} nie przejdzie przez _GEOCODE_POSTCODE_RE"
    if lang == "pl":
        assert parts[1].startswith("koło ")
    elif lang == "en":
        assert parts[1].startswith("near ")
    else:
        assert not parts[1].startswith(("near ", "koło ")), f"{lang}: {parts[1]!r}"


@pytest.mark.parametrize("lang", OTHER)
@pytest.mark.parametrize("key", ("location_saved", "search_success", "geocode_no_match", "search_fail"))
def test_translations_have_no_polish_places_or_kolo(lang, key):
    text = i18n.UI_TEXTS[lang][key]
    for leak in POLISH_LEAK:
        assert leak not in text, f"{lang}:{key}: polski fragment {leak!r}"


# ============================================================================
# 5. KOMENDY: PL ALIASY vs KANONICZNE
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", REVISED_KEYS)
def test_commands_follow_language_convention(lang, key):
    text = i18n.UI_TEXTS[lang][key]
    found = set(re.findall(r"/[A-Za-z\\_]+", text))
    allowed = set(PL_COMMANDS if lang == "pl" else CANON_COMMANDS)
    assert found <= allowed, f"{lang}:{key}: nieoczekiwane komendy {found - allowed}"
    if lang != "pl":
        for pl_only in ("/dzien", "/teraz", "/miasto", "/raport", "/zapros", "/dane",
                        "/priv ", "/porady", "/bezGPS", "/usunDane"):
            assert pl_only not in text, f"{lang}:{key}: polski alias {pl_only!r}"


@pytest.mark.parametrize("lang", LANGS)
def test_report_dialog_and_bad_answer_texts(lang):
    dialog = i18n.UI_TEXTS[lang]["report_dialog"]
    assert "🌅" in dialog and "🌇" in dialog, f"{lang}: Rano/Popołudnie z emoji"
    assert dialog.index("🌅") < dialog.index("🌇")
    assert dialog.rstrip().endswith((".", "?")) and "{yes}" in dialog and "{no}" in dialog
    bad = i18n.UI_TEXTS[lang]["report_bad_answer"]
    assert bad.startswith("⚠️ ")
    assert bad.index("{yes}") < bad.index("{no}"), f"{lang}: najpierw „tak” (zmiana), potem „nie” (zachowanie)"


def test_polish_report_texts_are_exactly_as_requested():
    assert i18n.UI_TEXTS["pl"]["report_dialog"] == (
        "⚙️ *GODZINY RAPORTÓW*\n\n"
        "Twoje godziny raportów:\n"
        "🌅 Rano: {morning}\n"
        "🌇 Popołudnie: {afternoon}\n\n"
        "Czy chcesz zmienić te godziny? Wpisz „{yes}” lub „{no}”."
    )
    assert i18n.UI_TEXTS["pl"]["report_bad_answer"] == (
        "⚠️ Nie zrozumiałem odpowiedzi. Wpisz „{yes}”, aby zmienić godziny, "
        "albo „{no}”, aby zachować obecne."
    )
    assert i18n.UI_TEXTS["pl"]["geocode_no_match"] == (
        "❌ Na mapie nie ma miejsca o takiej nazwie.\n\n"
        "Użyj ponownie /miasto i doprecyzuj nazwę (np. Ameryka, koło Olsztynek, Olsztyn lub Ameryka 11-015)."
    )
