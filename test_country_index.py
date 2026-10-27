"""test_country_index.py — dane i resolver indeksu krajów (bez sieci).

Ten plik sprawdza WYŁĄCZNIE warstwę danych i `country_index`, całkowicie osobno
od testów geokodowania (`test_geocode_country_forms.py`). Żaden test nie wymaga
prawdziwej odpowiedzi Nominatima ani połączenia z siecią.

Co jest pilnowane:
  1. plik danych istnieje, jest deployowalny (nie ignorowany przez git) i ma
     poprawny schemat oraz sumę kontrolną,
  2. zbiór krajów = 249 ISO 3166-1, każdy z 6 językami interfejsu,
  3. KAŻDA nazwa kanoniczna wskazuje własny kraj, a aliasy wymagane przez
     dotychczasowe zachowanie działają bez zmian,
  4. dopasowanie jest pełno-frazowe: bez prefiksów, bez fuzzy, bez alpha-3,
  5. kolizje nie są rozstrzygane po cichu (`kongo` -> cd+cg),
  6. `context_ambiguous` to zamknięta lista dwóch nazw z udowodnioną regresją,
  7. uszkodzony lub brakujący plik => jasny błąd (fail-closed), nigdy indeks
     częściowy,
  8. runtime nie importuje Babel/pycountry i jest zgodny z Pythonem 3.8.

Uruchomienie: pytest test_country_index.py -v
"""

import ast
import hashlib
import json
import os
import re
import subprocess

import pytest

import country_index as ci

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(BASE_DIR, "data", "countries", "country_index.json")
NOTICE_PATH = os.path.join(BASE_DIR, "data", "countries", "NOTICE.md")
OVERRIDES_PATH = os.path.join(BASE_DIR, "tools", "country_index_overrides.json")
GENERATOR_PATH = os.path.join(BASE_DIR, "tools", "build_country_index.py")

# 249 krajów i terytoriów ISO 3166-1. Liczba jest częścią kontraktu danych:
# zmiana w pycountry musi być świadomą decyzją, nie cichym dryfem.
EXPECTED_COUNTRIES = 249


@pytest.fixture(scope="module")
def payload():
    with open(INDEX_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def index(payload):
    return ci.validate_payload(payload)


@pytest.fixture()
def fresh_index(monkeypatch):
    """Czysty singleton z prawdziwym plikiem (testy podmieniają ścieżkę)."""
    ci.reset()
    yield
    ci.reset()


# ============================================================================
# 1. PLIK DANYCH: OBECNOŚĆ, DEPLOYOWALNOŚĆ, ROZMIAR
# ============================================================================

def test_index_file_exists_and_is_not_empty():
    assert os.path.isfile(INDEX_PATH), (
        "brak %s — bez indeksu krajów geokodowanie tekstowe jest wyłączone "
        "(fail-closed). Uruchom: python tools/build_country_index.py" % INDEX_PATH
    )
    assert os.path.getsize(INDEX_PATH) > 10_000


def test_index_file_size_stays_reasonable():
    """Indeks ma być małym, wersjonowanym plikiem, nie binarną bazą."""
    assert os.path.getsize(INDEX_PATH) < 200_000


def test_index_file_is_not_ignored_by_git():
    """Plik musi trafić do artefaktu uruchomieniowego (Oracle: całe repo)."""
    if not os.path.isdir(os.path.join(BASE_DIR, ".git")):
        pytest.skip("brak repozytorium git (archiwum/wdrożenie)")
    result = subprocess.run(
        ["git", "check-ignore", "-q", INDEX_PATH],
        cwd=BASE_DIR, capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        "data/countries/country_index.json jest ignorowany przez git — nie trafi "
        "na produkcję i wszystkie tekstowe wyszukiwania zwrócą GEOCODE_ERROR"
    )


def test_data_directory_files_are_known_to_git_or_tracked():
    """Plik jest śledzony albo przynajmniej widoczny dla git jako nowy."""
    if not os.path.isdir(os.path.join(BASE_DIR, ".git")):
        pytest.skip("brak repozytorium git (archiwum/wdrożenie)")
    relative = os.path.relpath(INDEX_PATH, BASE_DIR).replace(os.sep, "/")
    tracked = subprocess.run(
        ["git", "ls-files", "--", relative], cwd=BASE_DIR, capture_output=True, text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", relative],
        cwd=BASE_DIR, capture_output=True, text=True,
    ).stdout.strip()
    assert tracked or status, "indeks krajów nie jest widoczny dla git — nie da się go wdrożyć"


def test_notice_file_documents_sources_and_licenses():
    assert os.path.isfile(NOTICE_PATH)
    with open(NOTICE_PATH, "r", encoding="utf-8") as handle:
        notice = handle.read()
    for needle in ("CLDR", "pycountry", "Babel", "Licencje", "build_country_index.py"):
        assert needle in notice, "NOTICE.md nie wspomina %r" % (needle,)


# ============================================================================
# 2. SCHEMAT I KOMPLETNOŚĆ (deklarowany zbiór krajów)
# ============================================================================

def test_schema_and_sources_are_declared(payload):
    meta = payload["meta"]
    assert meta["schema_version"] == ci.SCHEMA_VERSION
    assert meta["languages"] == list(ci.LANGUAGES)
    assert meta["source"]["cldr"].startswith("CLDR ")
    assert meta["source"]["iso3166"].startswith("pycountry ")
    assert meta["source"]["overrides"].startswith("tools/country_index_overrides.json@")


def test_country_count_is_the_declared_iso_set(index):
    assert index.country_count == EXPECTED_COUNTRIES
    assert len({country["alpha_2"] for country in index.countries}) == EXPECTED_COUNTRIES


def test_every_country_has_all_interface_languages(index):
    for country in index.countries:
        assert set(country["names"]) == set(ci.LANGUAGES), country["alpha_2"]
        for language in ci.LANGUAGES:
            assert country["names"][language].strip(), (country["alpha_2"], language)


def test_iso_codes_have_the_right_shape(index):
    for country in index.countries:
        assert re.match(r"^[a-z]{2}$", country["alpha_2"]), country["alpha_2"]
        assert re.match(r"^[a-z]{3}$", country["alpha_3"]), country["alpha_3"]
    assert len({country["alpha_3"] for country in index.countries}) == EXPECTED_COUNTRIES


def test_spot_check_of_known_codes(index):
    by_code = {country["alpha_2"]: country for country in index.countries}
    assert by_code["jp"]["alpha_3"] == "jpn"
    assert by_code["jp"]["names"]["pl"] == "Japonia"
    assert by_code["jp"]["names"]["en"] == "Japan"
    assert by_code["pl"]["names"]["pl"] == "Polska"
    assert by_code["gb"]["names"]["pl"] == "Wielka Brytania"
    assert by_code["us"]["names"]["pl"] == "Stany Zjednoczone"
    assert by_code["cd"]["names"]["pl"].startswith("Demokratyczna")
    assert by_code["cg"]["names"]["pl"] == "Kongo"


def test_lookup_keys_are_canonical_and_codes_are_sorted_sets(payload, index):
    known = {country["alpha_2"] for country in index.countries}
    for key, codes in payload["lookup"].items():
        assert ci.normalize_country_name(key) == key, key
        assert codes == sorted(set(codes)), key
        assert codes, key
        assert set(codes) <= known, (key, codes)


def test_payload_checksum_matches_canonical_content(payload):
    assert payload["meta"]["payload_sha256"] == ci.canonical_payload_sha256(payload)


def test_payload_round_trip_is_stable(payload, index):
    """Walidacja nie zmienia danych: drugi przebieg daje identyczny wynik."""
    again = ci.validate_payload(json.loads(json.dumps(payload)))
    assert [c["names"] for c in again.countries] == [c["names"] for c in index.countries]
    assert again.max_name_tokens == index.max_name_tokens


# ============================================================================
# 3. KAŻDA NAZWA KANONICZNA W KAŻDYM JĘZYKU
# ============================================================================

def test_every_canonical_name_resolves_to_its_own_country(index):
    checked = 0
    for country in index.countries:
        for language, name in country["names"].items():
            match = index.classify(name)
            assert country["alpha_2"] in match.codes, (language, name, match)
            checked += 1
    assert checked == EXPECTED_COUNTRIES * len(ci.LANGUAGES)


def test_every_canonical_name_also_works_through_case_and_whitespace(index):
    assert index.classify("japonia").code == "jp"
    assert index.classify("  JAPONIA  ").code == "jp"
    assert index.classify("Japón").code == "jp"
    assert index.classify("JAPÓN").code == "jp"


def test_former_manual_alias_keys_keep_their_meaning(fresh_index):
    """Wszystkie 30 kluczy usuniętego słownika musi nadal działać tak samo."""
    former = {
        "pl": "pl", "polska": "pl", "poland": "pl",
        "fr": "fr", "francja": "fr", "france": "fr",
        "us": "us", "usa": "us", "stany zjednoczone": "us", "united states": "us",
        "united states of america": "us", "ameryka": "us",
        "de": "de", "niemcy": "de", "germany": "de", "deutschland": "de",
        "uk": "gb", "gb": "gb", "wielka brytania": "gb", "united kingdom": "gb",
        "great britain": "gb",
        "no": "no", "norwegia": "no", "norway": "no", "norge": "no",
        "es": "es", "hiszpania": "es", "spain": "es", "espana": "es", "españa": "es",
    }
    assert len(former) == 30
    for name, expected in former.items():
        assert ci.classify(name).code == expected, name


@pytest.mark.parametrize(
    "name,code",
    [
        ("democratic republic of the congo", "cd"),
        ("democratic republic of congo", "cd"),
        ("trinidad and tobago", "tt"),
    ],
)
def test_curated_aliases_are_present_in_the_data(index, name, code):
    """Aliasy wymagane przez produkt, których CLDR nie zawiera w tej formie."""
    assert index.classify(name).kind == ci.UNIQUE, name
    assert index.classify(name).code == code, name


def test_drc_alias_does_not_touch_the_short_congo_name(index):
    """Pełna nazwa DR Konga dopisana OBOK krótkiego 'congo' — bez nadpisania."""
    assert index.classify("congo").code == "cg"
    assert index.classify("kongo").kind == ci.AMBIGUOUS
    assert index.classify("democratic republic of the congo").code == "cd"
    assert index.classify("democratic republic of congo").code == "cd"


def test_trinidad_alias_does_not_replace_the_cldr_ampersand_form(index):
    assert index.classify("trinidad and tobago").code == "tt"
    assert index.classify("trinidad & tobago").code == "tt"
    assert index.classify("trynidad i tobago").code == "tt"


def test_aliases_are_full_phrases_only(index):
    """Alias nie tworzy dopasowania prefiksowego ani 'prawie' nazwy."""
    for name in ("democratic republic", "democratic republic of", "trinidad",
                 "trinidad and", "republic of the congo"):
        assert index.classify(name).kind == ci.NONE, name


def test_names_beyond_four_tokens_are_supported(index):
    """Stary limit 4 tokenów ucinał 24 nazwy CLDR (maks. 9 tokenów)."""
    assert index.max_name_tokens >= 9
    assert index.max_name_tokens > 4
    for name, code in (
        ("Saint Vincent et les Grenadines", "vc"),
        ("Georgia Południowa i Sandwich Południowy", "gs"),
        ("Géorgie du Sud-et-les Îles Sandwich du Sud", "gs"),
        ("Islas Georgia del Sur y Sandwich del Sur", "gs"),
    ):
        assert index.classify(name).code == code, name
        assert len(ci.normalize_country_name(name).split()) > 4


def test_prefix_matching_is_impossible(index):
    """Niger != Nigeria, Guinea != Guinea-Bissau, Congo != DR Congo."""
    assert index.classify("niger").codes == ("ne",)
    assert index.classify("nigeria").codes == ("ng",)
    assert index.classify("guinea").codes == ("gn",)
    assert index.classify("guinea bissau").codes == ("gw",)
    assert index.classify("nige").kind == ci.NONE
    assert index.classify("guine").kind == ci.NONE
    assert index.classify("jap").kind == ci.NONE


def test_alpha3_codes_are_stored_but_not_matched(index):
    """Decyzja D5: alpha-3 są w danych, ale nie służą do dopasowania."""
    stored = {country["alpha_3"] for country in index.countries}
    assert {"jpn", "pol", "deu", "fra"} <= stored
    for code in ("jpn", "pol", "deu", "fra", "nor", "esp"):
        assert index.classify(code).kind == ci.NONE, code


def test_subnational_alias_names_are_not_invented(index):
    """Decyzja D6: bez aliasów subnarodowych."""
    for name in ("england", "anglia", "scotland", "szkocja", "wales", "walia"):
        assert index.classify(name).kind == ci.NONE, name


# ============================================================================
# 4. KOLIZJE
# ============================================================================

def test_kongo_collision_is_kept_as_a_set(index):
    match = index.classify("Kongo")
    assert match.kind == ci.AMBIGUOUS
    assert match.codes == ("cd", "cg")
    assert match.code is None, "kolizja nie może być rozstrzygana 'pierwszym kodem'"


def test_congo_short_name_is_data_driven_and_documented(index):
    """CLDR nazywa CG krótko 'Congo'; CD to 'Congo - Kinshasa'.

    To fakt danych, nie arbitralny wybór: 'congo' to oficjalna krótka nazwa
    Republiki Konga. Faza 1 NIE zmienia tego kodu — pełna nazwa DR Konga jest
    dopisana jako osobny alias (patrz test_curated_aliases_are_present_in_the_data),
    więc użytkownik ma poprawne wejście dla obu państw.
    """
    assert index.classify("congo").codes == ("cg",)
    assert index.classify("democratic republic of the congo").codes == ("cd",)
    assert index.classify("kongo kinshasa").codes == ("cd",)


def test_korea_and_macedonia_are_not_guessed(index):
    assert index.classify("korea").kind == ci.NONE
    assert index.classify("macedonia").kind == ci.NONE
    assert index.classify("korea poludniowa").codes == ("kr",)
    assert index.classify("korea polnocna").codes == ("kp",)
    assert index.classify("macedonia polnocna").codes == ("mk",)


def test_context_ambiguous_list_is_closed_and_justified(payload):
    """D3: override tylko przy udowodnionej regresji — lista ma dokładnie 2 wpisy."""
    assert payload["context_ambiguous"] == {
        "georgia": {
            "codes": ["ge"],
            "reason": payload["context_ambiguous"]["georgia"]["reason"],
        },
        "wlochy": {
            "codes": ["it"],
            "reason": payload["context_ambiguous"]["wlochy"]["reason"],
        },
    }
    for entry in payload["context_ambiguous"].values():
        assert "admin_context" in entry["reason"]


@pytest.mark.parametrize(
    "name", ["georgia", "wlochy", "Georgia", "Włochy", "WLOCHY"]
)
def test_context_ambiguous_names_never_become_a_country_filter(index, name):
    match = index.classify(name)
    assert match.kind == ci.CONTEXT_AMBIGUOUS
    assert match.code is None


def test_names_rejected_from_context_ambiguous_stay_global(index):
    """jordan/chad/niger/guinea: brak dowodu regresji => zwykłe rozpoznanie kraju."""
    for name, code in (("jordan", "jo"), ("chad", "td"), ("niger", "ne"), ("guinea", "gn")):
        assert index.classify(name).kind == ci.UNIQUE, name
        assert index.classify(name).code == code, name


def test_wlochy_reason_does_not_hide_the_country_spelling(index):
    """'gruzja' (kraj) działa dalej — override dotyczy tylko nazwy 'georgia'."""
    assert index.classify("gruzja").code == "ge"
    assert index.classify("wlochy").kind == ci.CONTEXT_AMBIGUOUS
    assert index.classify("italy").code == "it"


# ============================================================================
# 5. SUFIKS ZAPYTANIA (resolve_suffix)
# ============================================================================

@pytest.mark.parametrize(
    "raw,size,kind,code",
    [
        ("Tokio, Japonia", 1, ci.UNIQUE, "jp"),
        ("Tokio Japonia", 1, ci.UNIQUE, "jp"),
        ("Tokio,Japan", 1, ci.UNIQUE, "jp"),
        ("Hel PL", 1, ci.UNIQUE, "pl"),
        ("Nowy Jork United States of America", 4, ci.UNIQUE, "us"),
        ("Atlanta, Georgia", 1, ci.CONTEXT_AMBIGUOUS, None),
        ("X, Kongo", 1, ci.AMBIGUOUS, None),
        ("Polska", 0, ci.NONE, None),
        ("Japonia", 0, ci.NONE, None),
        ("Tokio", 0, ci.NONE, None),
        ("", 0, ci.NONE, None),
        ("Hel", 0, ci.NONE, None),
    ],
)
def test_resolve_suffix_contract(index, raw, size, kind, code):
    got_size, match = index.resolve_suffix(raw)
    assert got_size == size, raw
    assert match.kind == kind, raw
    assert match.code == code, raw


def test_resolve_suffix_never_takes_the_whole_query(index):
    """Samo 'Polska'/'Japonia' to pytanie o miejscowość, nie o kraj."""
    for query in ("Polska", "Japonia", "Kongo", "Niemcy", "Stany Zjednoczone"):
        assert index.resolve_suffix(query)[0] == 0, query


# ============================================================================
# 6. NORMALIZACJA — KONTRAKT KRAJÓW == KONTRAKT MIAST
# ============================================================================

NORMALIZATION_CORPUS = (
    "Polska", "POLSKA", "polska", "Włochy", "WŁOCHY", "Wlochy",
    "Japón", "España", "Österreich", "Åland", "Æ", "Œ",
    "Saint Vincent et les Grenadines", "Géorgie du Sud-et-les Îles Sandwich du Sud",
    "Côte d’Ivoire", "Côte d'Ivoire", "Cote d Ivoire",
    "Timor-Leste", "Guinea-Bissau", "Guinea Bissau", "Guinea,Bissau",
    "Bosnia and Herzegovina", "Bosnia & Herzegovina",
    "  Polska  ", "Polska,", ",Polska", "Pol  ska", "Pol\t ska",
    "Unknown Region", "Islas menores alejadas de EE. UU.",
    "R.A.S. chinoise de Hong Kong", "Südgeorgien und die Südlichen Sandwichinseln",
    "東京", "Япония", "日本", "Türkiye", "Türkei", "Curaçao", "CURAÇAO",
    "0", "123", "-", "—", "", "   ", "São Tomé and Príncipe", "Réunion",
)


def test_country_normalization_matches_place_normalization():
    """Kontrakt kraju to TEN SAM algorytm co dla miast — bez zmian globalnych."""
    import location_bot as lb

    for text in NORMALIZATION_CORPUS:
        assert ci.normalize_country_name(text) == lb._normalize_location_text(text), text


def test_country_normalization_declared_limits_hold():
    """Brak odmiany, transliteracji i alfabetów niełacińskich — jawnie."""
    assert ci.normalize_country_name("Japonii") != ci.classify("Japonia").codes[0]
    assert ci.classify("Japonii").kind == ci.NONE
    assert ci.classify("Niemczech").kind == ci.NONE
    assert ci.classify("Tokyo").kind == ci.NONE          # brak transliteracji
    assert ci.classify("日本").kind == ci.NONE            # brak CJK
    assert ci.classify("Япония").kind == ci.NONE          # brak cyrylicy
    assert ci.classify("Japoniaa").kind == ci.NONE        # brak literówek/fuzzy


# ============================================================================
# 7. FAIL-CLOSED: BRAK I USZKODZENIE PLIKU
# ============================================================================

def _write(tmp_path, payload, name="country_index.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_missing_file_means_index_unavailable(tmp_path, fresh_index, monkeypatch, capsys):
    monkeypatch.setattr(ci, "INDEX_PATH", str(tmp_path / "nie-ma.json"))
    assert ci.get_index() is None
    assert ci.index_available() is False
    assert "brak pliku" in ci.index_error()
    assert ci.classify("Japonia").kind == ci.NONE
    assert ci.resolve_suffix("Tokio, Japonia") == (0, ci.NO_MATCH)
    assert ci.max_name_tokens() == 0
    log = capsys.readouterr().out
    assert "[COUNTRY_INDEX]" in log and "fail-closed" in log


def test_broken_json_means_index_unavailable(tmp_path, fresh_index, monkeypatch):
    path = tmp_path / "country_index.json"
    path.write_text("{ to nie jest json", encoding="utf-8")
    monkeypatch.setattr(ci, "INDEX_PATH", str(path))
    assert ci.get_index() is None
    assert "uszkodzony JSON" in ci.index_error()


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda p: p["meta"].__setitem__("schema_version", 99), "schema_version"),
        (lambda p: p["meta"].__setitem__("languages", ["pl"]), "język"),
        (lambda p: p["meta"].__setitem__("country_count", 1), "country_count"),
        (lambda p: p["meta"].__setitem__("max_name_tokens", 0), "max_name_tokens"),
        (lambda p: p["meta"].__setitem__("max_name_tokens", 99), "max_name_tokens"),
        (lambda p: p["meta"].__setitem__("payload_sha256", "0" * 64), "suma kontrolna"),
        (lambda p: p["countries"].pop(), "country_count"),
        (lambda p: p["countries"][0]["names"].pop("de"), "brak nazwy"),
        (lambda p: p["countries"][0].__setitem__("alpha_3", "TOOLONG"), "alpha_3"),
        (lambda p: (p["countries"].append(dict(p["countries"][0])),
                    p["meta"].__setitem__("country_count", len(p["countries"]))), "duplikat alpha_2"),
        (lambda p: p["lookup"].__setitem__("  zle  ", ["pl"]), "znormalizowany"),
        (lambda p: p["lookup"].__setitem__("zle", ["zz"]), "nie istnieje"),
        (lambda p: p["lookup"].__setitem__("pusta", []), "pusta lista"),
        (lambda p: p["context_ambiguous"].__setitem__("wlochy", {"codes": ["zz"]}), "nie istnieje"),
        (lambda p: p["context_ambiguous"].__setitem__("nie ma", {"codes": ["pl"]}), "nie występuje"),
        (lambda p: p.pop("lookup"), "brak sekcji"),
    ],
)
def test_corrupted_payload_is_rejected_loudly(payload, mutate, expected):
    broken = json.loads(json.dumps(payload))
    mutate(broken)
    with pytest.raises(ci.CountryIndexError) as excinfo:
        ci.validate_payload(broken)
    assert expected in str(excinfo.value)


def test_partial_index_is_worse_than_none(payload):
    """Nazwa kraju bez wpisu w 'lookup' nie może przejść walidacji.

    Suma kontrolna jest przeliczana celowo: chcemy sprawdzić WŁAŚCIWY niezmiennik
    (spójność nazw z mapą), a nie tylko to, że plik różni się od oryginału.
    """
    broken = json.loads(json.dumps(payload))
    broken["lookup"].pop(ci.normalize_country_name("Japonia"))
    broken["meta"]["payload_sha256"] = ci.canonical_payload_sha256(broken)
    with pytest.raises(ci.CountryIndexError) as excinfo:
        ci.validate_payload(broken)
    assert "Japonia" in str(excinfo.value) and "lookup" in str(excinfo.value)


def test_singleton_is_loaded_once_and_can_be_reset(fresh_index, monkeypatch, capsys):
    first = ci.get_index()
    assert first is not None
    monkeypatch.setattr(ci, "INDEX_PATH", "/nie/tam/country_index.json")
    assert ci.get_index() is first, "drugie wywołanie nie czytuje pliku ponownie"
    ci.reset()
    assert ci.get_index() is None
    monkeypatch.undo()
    ci.reset()
    assert ci.get_index() is not None


def test_index_path_points_into_the_repository():
    assert ci.index_path() == INDEX_PATH


# ============================================================================
# 8. ZALEŻNOŚCI, PYTHON 3.8, GENERATOR
# ============================================================================

FORBIDDEN_RUNTIME_IMPORTS = {"babel", "pycountry", "geopy", "requests", "gspread"}


def _imported_modules(path):
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_runtime_module_has_no_third_party_imports():
    imported = _imported_modules(os.path.join(BASE_DIR, "country_index.py"))
    assert not (imported & FORBIDDEN_RUNTIME_IMPORTS), imported
    assert "json" in imported and "unicodedata" in imported


def test_requirements_do_not_gain_generator_dependencies():
    with open(os.path.join(BASE_DIR, "requirements.txt"), "r", encoding="utf-8") as handle:
        runtime = handle.read().lower()
    for package in ("babel", "pycountry"):
        assert package not in runtime, "%s nie może być zależnością runtime" % (package,)
    with open(os.path.join(BASE_DIR, "requirements-tools.txt"), "r", encoding="utf-8") as handle:
        tools = handle.read()
    assert "babel==" in tools and "pycountry==" in tools


def test_new_runtime_code_is_python38_compatible():
    """Produkcja ma 3.8.10: bez removeprefix/removesuffix, match i PEP 585/604."""
    path = os.path.join(BASE_DIR, "country_index.py")
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()
    for forbidden in (".removeprefix(", ".removesuffix("):
        assert forbidden not in source, forbidden
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Match) if hasattr(ast, "Match") else False:
            pytest.fail("match/case wymaga Pythona 3.10")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in list(node.args.args) + list(node.args.kwonlyargs):
                annotation = arg.annotation
                if isinstance(annotation, ast.Subscript):
                    base = annotation.value
                    name = getattr(base, "id", None)
                    assert name not in {"list", "dict", "tuple", "set"}, (
                        "PEP 585 w adnotacji: %s" % (name,)
                    )


def test_generator_is_not_imported_at_runtime():
    """Generator jest narzędziem offline — runtime nie może go importować."""
    for path in ("location_bot.py", "country_index.py", "guest_bot_handler.py"):
        imported = _imported_modules(os.path.join(BASE_DIR, path))
        assert "build_country_index" not in imported, path
        assert "tools" not in imported, path


def test_overrides_file_is_valid_and_documents_rejections():
    with open(OVERRIDES_PATH, "r", encoding="utf-8") as handle:
        overrides = json.load(handle)
    assert overrides["version"] >= 1
    for key in overrides["aliases"]:
        assert ci.normalize_country_name(key) == key, key
    for key in overrides["context_ambiguous"]:
        assert ci.normalize_country_name(key) == key, key
    description = " ".join(overrides["description"])
    assert "jordan" in description and "niger" in description, (
        "override'y muszą dokumentować, że jordan/chad/niger/guinea odrzucono"
    )


def test_generator_reproduces_the_committed_file(tmp_path):
    """Deterministyczna regeneracja (pominięta bez narzędzi offline)."""
    pytest.importorskip("babel")
    pytest.importorskip("pycountry")
    import sys

    sys.path.insert(0, os.path.join(BASE_DIR, "tools"))
    try:
        import build_country_index as generator
    finally:
        sys.path.pop(0)

    payload, warnings = generator.build_payload()
    assert not warnings or all("UWAGA" not in w for w in warnings)
    blob = generator.serialize(payload)
    with open(INDEX_PATH, "r", encoding="utf-8") as handle:
        committed = handle.read()
    assert blob == committed, (
        "indeks w repo nie odpowiada generatorowi — uruchom "
        "python tools/build_country_index.py"
    )
    assert generator.main(["--check"]) == 0
