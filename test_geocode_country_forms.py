"""test_geocode_country_forms.py — kraj w zapytaniu: parser, kandydaci, regresje.

Zakres (faza 1): nowy silnik rozpoznawania kraju (`country_index`) wewnątrz
ISTNIEJĄCEJ gramatyki zapytań. Gramatyka, statusy, komunikaty, etykiety, GPS,
pinezka i zapis profilu pozostają bez zmian.

Co jest pilnowane:
  1. formy zapytania wymagane przez zlecenie działają (`Tokio, Japonia`,
     `Tokio, Japan`, `Tokio, JP`, `Tokio Japonia`, `Warszawa, Polska`, `Hel PL`,
     `Hel,PL`, formy z kontekstem administracyjnym),
  2. naprawa odcinania sufiksu: `Hel,PL`, `Nowy Jork,USA`, `Wiązowna,otwock,Polska`,
  3. kolizje kraj–kontekst: `Atlanta, Georgia`, `Warszawa, Włochy` (wariant A),
  4. brak dopasowań prefiksowych (`Niger`/`Nigeria`, `Guinea`/`Guinea-Bissau`),
  5. twardy filtr `country_code` i jeden request na zapytanie,
  6. fail-closed przy niedostępnym indeksie (+ brak requestu, + TOO_SHORT pierwszy),
  7. GPS/pinezka (`_resolve_location_labels` bez query) działają także wtedy, gdy
     indeks jest niedostępny,
  8. sprzeczne wskazania kraju: tylko KOMPATYBILNOŚĆ WSTECZNA, nie reguła,
  9. kontrakty publiczne bez zmian (`geocode_city_accepted` = 5 krotek).

Zero sieci: geokoder jest atrapą z `test_geocode_validation`
(`FakeNominatim`), a `pytest.ini` ma `--disable-socket`.

Uruchomienie: pytest test_geocode_country_forms.py -v
"""

import json
import os

import pytest

import country_index as ci
import guest_bot_handler as gbh
import location_bot as lb
from test_geocode_validation import (
    FakeNominatim, FakeResult, guest_handler_run, poland_place,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ============================================================================
# ATRAPY I FIKSURY
# ============================================================================

def tokyo_result(name="Tokio"):
    return FakeResult(
        {"city": name, "state": "Tokio", "country": "Japonia", "country_code": "jp"},
        lat=35.6895, lon=139.6917, display="%s, Japonia" % (name,),
    )


def atlanta_result():
    return FakeResult(
        {"city": "Atlanta", "state": "Georgia", "country": "United States",
         "country_code": "us"},
        lat=33.7490, lon=-84.3880, display="Atlanta, Georgia, United States",
    )


def warszawa_wlochy_result():
    return FakeResult(
        {"city": "Warszawa", "city_district": "Włochy", "state": "mazowieckie",
         "country": "Polska", "country_code": "pl"},
        lat=52.2000, lon=20.9000, display="Włochy, Warszawa, Polska",
    )


def kongo_result(code):
    city = "Brazzaville" if code == "cg" else "Kinshasa"
    return FakeResult(
        {"city": city, "country": "Kongo", "country_code": code},
        lat=-4.26, lon=15.24, display="%s, Kongo" % (city,),
    )


@pytest.fixture(autouse=True)
def clean_geocoder(monkeypatch):
    """Każdy test dostaje czystą atrapę geokodera i świeży singleton indeksu."""
    FakeNominatim.calls = []
    FakeNominatim.responses = []
    monkeypatch.setattr(lb, "Nominatim", FakeNominatim)
    # Cache trybu gościa i reverse są procesowe — bez czyszczenia testy
    # „dziedziczą” trafienie cache po poprzednim zapytaniu.
    gbh._GEO_CACHE.clear()
    gbh._CITY_CACHE.clear()
    lb._GEO_DETAILS_CACHE.clear()
    ci.reset()
    yield
    FakeNominatim.calls = []
    FakeNominatim.responses = []
    gbh._GEO_CACHE.clear()
    gbh._CITY_CACHE.clear()
    lb._GEO_DETAILS_CACHE.clear()
    ci.reset()


def geo(query, lang="pl"):
    return lb.geocode_city_accepted(query, lang)


def forward_calls():
    return FakeNominatim.forward_calls()


# ============================================================================
# 1. PARSER: FORMY WYMAGANE PRZEZ ZLECENIE
# ============================================================================

@pytest.mark.parametrize(
    "query,core,admin,code",
    [
        # kraj jako końcowy segment (przecinek)
        ("Tokio, Japonia", "Tokio", [], "jp"),
        ("Tokio, Japan", "Tokio", [], "jp"),
        ("Tokio, JP", "Tokio", [], "jp"),
        ("Tokio,Japonia", "Tokio", [], "jp"),
        ("Tokio,  Japonia  ", "Tokio", [], "jp"),
        # kraj jako końcowy sufiks bez przecinka
        ("Tokio Japonia", "Tokio", [], "jp"),
        ("Warszawa, Polska", "Warszawa", [], "pl"),
        ("Hel PL", "Hel", [], "pl"),
        ("Hel,PL", "Hel", [], "pl"),
        ("Hel, PL", "Hel", [], "pl"),
        ("Hel ,  Polska  ", "Hel", [], "pl"),
        # dodatkowy kontekst administracyjny z krajem
        ("Wiązowna, otwock, Polska", "Wiązowna", ["otwock"], "pl"),
        ("Wiązowna,otwock,Polska", "Wiązowna", ["otwock"], "pl"),
        ("Wiązowna, powiat otwocki, Polska", "Wiązowna", ["otwocki"], "pl"),
        ("Olsztyn, Częstochowa, Polska", "Olsztyn", ["Częstochowa"], "pl"),
        # formy bez kraju (bez zmian)
        ("Wiązowna, otwock", "Wiązowna", ["otwock"], None),
        ("Olsztyn koło Częstochowy", "Olsztyn", ["Częstochowy"], None),
        ("Olsztyn województwo śląskie", "Olsztyn", ["śląskie"], None),
        ("Wiązowna 05-462", "Wiązowna 05-462", [], None),
        ("Kolo", "Kolo", [], None),
        ("Wielkie Kolo", "Wielkie Kolo", [], None),
    ],
)
def test_query_parse_keeps_the_grammar(query, core, admin, code):
    assert lb._geocode_query_parse(query) == (core, admin, code)


@pytest.mark.parametrize(
    "query,core,code",
    [
        ("Tokio, Japonia", "Tokio", "jp"),
        ("Tokio, Japan", "Tokio", "jp"),
        ("Tokio, JP", "Tokio", "jp"),
        ("Tokio Japonia", "Tokio", "jp"),
        ("Hel PL", "Hel", "pl"),
        ("Hel,PL", "Hel", "pl"),
        ("Nowy Jork,USA", "Nowy Jork", "us"),
        ("Nowy Jork United States of America", "Nowy Jork", "us"),
        ("Londyn UK", "Londyn", "gb"),
        ("Madrid, España", "Madrid", "es"),
        ("X, Saint Vincent et les Grenadines", "X", "vc"),
    ],
)
def test_country_parser_uses_the_index(query, core, code):
    assert lb._geocode_query_country(query) == (core, code)


def test_split_country_keeps_commas_for_later_parsing():
    assert lb._geocode_split_country("Wiązowna, otwock, Polska") == ("Wiązowna, otwock", "pl")
    assert lb._geocode_split_country("Wiązowna,otwock,Polska") == ("Wiązowna,otwock", "pl")
    assert lb._geocode_split_country("Hel,PL") == ("Hel", "pl")
    assert lb._geocode_split_country("Nowy Jork,USA") == ("Nowy Jork", "us")


def test_country_alone_is_never_cut_out_of_the_query():
    """Samo "Polska"/"Japonia" to pytanie o miejscowość, nie o kraj."""
    for query in ("Polska", "Japonia", "Niemcy", "Stany Zjednoczone", "Kongo", "Korea"):
        assert lb._geocode_query_parse(query) == (query, [], None)
        assert lb._geocode_query_country(query) == (query, None)


# ============================================================================
# 2. NAPRAWA ODCINANIA SUFIksu (D8)
# ============================================================================

@pytest.mark.parametrize(
    "raw,size,expected",
    [
        ("Hel,PL", 1, "Hel"),
        ("Hel, PL", 1, "Hel"),
        ("Hel ,  Polska  ", 1, "Hel"),
        ("Nowy Jork,USA", 1, "Nowy Jork"),
        ("Wiązowna,otwock,Polska", 1, "Wiązowna,otwock"),
        ("Wiązowna, otwock, Polska", 1, "Wiązowna, otwock"),
        ("Tokio, JP,", 1, "Tokio"),
        ("Tokio, JP.", 1, "Tokio"),
        ("Nowy Jork United States of America", 4, "Nowy Jork"),
    ],
)
def test_strip_country_suffix_keeps_the_rest_of_the_query(raw, size, expected):
    assert lb._geocode_strip_country_suffix(raw, size) == expected


def test_comma_is_a_token_boundary_consistently():
    """Sufiks i odcięcie muszą zgadzać się co do granicy tokenu.

    Regresja: poprzednia wersja liczyła sufiks po tokenach (przecinek = separator),
    ale odcinała ostatni ``\\S+``, więc ``"Hel,PL"`` gubiło cały napis, a
    ``"Nowy Jork,USA"`` gubiło token ``"Jork"``.
    """
    for raw in ("Hel,PL", "Hel, PL", "Nowy Jork,USA", "Wiązowna,otwock,Polska"):
        size, code = lb._geocode_country_suffix(raw)
        assert size == 1 and code, raw
        rest = lb._geocode_strip_country_suffix(raw, size)
        assert rest not in ("", None), raw
        assert "Polska" not in rest and "USA" not in rest and "PL" not in rest


def test_long_country_names_are_no_longer_cut_off_by_a_four_token_limit():
    """Limit tokenów jest wyprowadzany z danych (9), nie zaszyty na 4."""
    assert ci.max_name_tokens() == 9
    size, code = lb._geocode_country_suffix("Port-of-Spain, Trynidad i Tobago")
    assert (size, code) == (3, "tt")
    assert lb._geocode_query_country("Saint Vincent et les Grenadines") == (
        "Saint Vincent et les Grenadines", None
    )
    assert lb._geocode_query_country("Kingstown, Saint Vincent et les Grenadines") == (
        "Kingstown", "vc"
    )


# ============================================================================
# 3. KOLIZJE: KRAJ KONTRA KONTEKST ADMINISTRACYJNY (wariant A)
# ============================================================================

def test_conflict_names_never_become_a_country_filter():
    assert lb._geocode_query_country("Atlanta, Georgia") == ("Atlanta, Georgia", None)
    assert lb._geocode_query_parse("Atlanta, Georgia") == ("Atlanta", ["Georgia"], None)
    assert lb._geocode_query_parse("Warszawa, Włochy") == ("Warszawa", ["Włochy"], None)
    assert lb._geocode_query_parse("Warszawa, WLOCHY") == ("Warszawa", ["WLOCHY"], None)


def test_atlanta_georgia_still_resolves_through_admin_context():
    """Wariant A: dotychczasowa forma zapytania działa — stan, nie kraj."""
    FakeNominatim.responses.append([atlanta_result()])
    status, lat, lon, short_label, display = geo("Atlanta, Georgia")
    assert status == lb.GEOCODE_OK
    assert (lat, lon) == (33.7490, -84.3880)
    assert short_label == "Atlanta"
    call = forward_calls()[-1]
    assert call["query"] == "Atlanta, Georgia"
    assert "country_codes" not in call["extras"], "Georgia nie może być filtrem kraju"
    assert "Georgia" in display and "United States" in display


def test_warszawa_wlochy_still_resolves_through_admin_context():
    FakeNominatim.responses.append([warszawa_wlochy_result()])
    status, _lat, _lon, short_label, display = geo("Warszawa, Włochy")
    assert status == lb.GEOCODE_OK
    assert short_label == "Warszawa"
    assert "mazowieckie" in display
    assert "country_codes" not in forward_calls()[-1]["extras"]


def test_georgia_compromise_is_documented_and_unchanged():
    """Wariant A: `georgia` zostaje w `context_ambiguous` (faza 2 ją rozważy).

    a) `Atlanta, Georgia` działa jako kontekst administracyjny (stan USA),
    b) `Tbilisi, Georgia` NIE dostaje filtra kraju `ge` — to świadomy koszt,
       odłożony do fazy 2 (wariant B / evidence). Użytkownik gruziński ma
       `Tbilisi, Gruzja` → `ge`, co jest pokryte osobnym testem.
    """
    assert ci.classify("Georgia").kind == ci.CONTEXT_AMBIGUOUS
    assert lb._geocode_query_country("Tbilisi, Georgia") == ("Tbilisi, Georgia", None)
    assert lb._geocode_query_parse("Tbilisi, Georgia") == ("Tbilisi", ["Georgia"], None)

    # Kandydat z Gruzji przy zapisie "Georgia" nie przechodzi przez kontekst
    # administracyjny => NO_MATCH (żadnej karty), a nie karta dla złego miejsca.
    tbilisi = FakeResult(
        {"city": "Tbilisi", "country": "Georgia", "country_code": "ge"},
        lat=41.6938, lon=44.8015, display="Tbilisi, Georgia",
    )
    FakeNominatim.responses.append([tbilisi])
    assert geo("Tbilisi, Georgia") == (lb.GEOCODE_NO_MATCH, None, None, None, None)
    assert "country_codes" not in forward_calls()[-1]["extras"]


def test_country_spelling_of_the_same_state_still_works():
    """Override dotyczy nazwy 'Georgia', nie kraju Gruzja ('gruzja' -> ge)."""
    gruzja = FakeResult(
        {"city": "Tbilisi", "country": "Gruzja", "country_code": "ge"},
        lat=41.6938, lon=44.8015, display="Tbilisi, Gruzja",
    )
    FakeNominatim.responses.append([gruzja])
    tokio = FakeResult(
        {"city": "Tokio", "country": "Japonia", "country_code": "jp"},
        lat=41.7, lon=44.8, display="Tokio, Japonia",
    )
    FakeNominatim.responses.append([tokio])
    status, _lat, _lon, short_label, _display = geo("Tbilisi, Gruzja")
    assert status == lb.GEOCODE_OK and short_label == "Tbilisi"
    assert forward_calls()[-1]["extras"]["country_codes"] == "ge"


def test_ambiguous_country_name_is_not_guessed_and_keeps_context_role():
    """'Kongo' jest wieloznaczne (cd/cg): żadnego kodu, dotychczasowa rola."""
    assert lb._geocode_query_country("X, Kongo") == ("X, Kongo", None)
    assert lb._geocode_query_parse("X, Kongo") == ("X", ["Kongo"], None)
    FakeNominatim.responses.append([kongo_result("cg")])
    status, _lat, _lon, _short, _display = geo("X, Kongo")
    assert status == lb.GEOCODE_NO_MATCH
    assert "country_codes" not in forward_calls()[-1]["extras"]


@pytest.mark.parametrize(
    "query,code",
    [
        ("X, Niger", "ne"),
        ("X, Nigeria", "ng"),
        ("X, Guinea", "gn"),
        ("X, Guinea-Bissau", "gw"),
        ("X, Chad", "td"),
        ("X, Jordan", "jo"),
    ],
)
def test_names_tested_for_full_match_are_real_countries(query, code):
    """jordan/chad/niger/guinea NIE są override'ami — działają jako kraj."""
    assert lb._geocode_query_country(query) == ("X", code)


def test_prefixes_do_not_match_across_countries():
    assert lb._geocode_query_country("X, Niger") != lb._geocode_query_country("X, Nigeria")
    assert lb._geocode_query_country("X, Guinea") != lb._geocode_query_country("X, Guinea-Bissau")
    assert lb._geocode_query_country("X, Nige") == ("X, Nige", None)
    assert lb._geocode_query_country("X, Guine") == ("X, Guine", None)
    assert lb._geocode_query_country("X, Korea") == ("X, Korea", None)
    assert lb._geocode_query_country("X, Macedonia") == ("X, Macedonia", None)


# ============================================================================
# 4. INTEGRACJA: DOPASOWANIE KANDYDATA I TWARDY FILTR KRAJU
# ============================================================================

@pytest.mark.parametrize(
    "query", ["Tokio, Japonia", "Tokio, Japan", "Tokio, JP", "Tokio Japonia", "Tokio,Japonia"]
)
def test_tokyo_forms_resolve_to_japan_with_one_request(query):
    FakeNominatim.responses.append([tokyo_result()])
    status, lat, lon, short_label, display = geo(query)
    assert status == lb.GEOCODE_OK, query
    assert (lat, lon) == (35.6895, 139.6917)
    assert short_label == "Tokio"
    assert display == "Tokio, Japonia"
    calls = forward_calls()
    assert len(calls) == 1, "jedno zapytanie na wszystkich kandydatów"
    assert calls[0]["query"] == "Tokio", "kraj jest filtrem, nie częścią nazwy"
    assert calls[0]["extras"].get("country_codes") == "jp"


@pytest.mark.parametrize(
    "query", ["Tokio, Japonia", "Tokio, Japan", "Tokio, JP", "Tokio Japonia"]
)
def test_candidate_with_other_country_code_is_rejected(query):
    FakeNominatim.responses.append([
        FakeResult({"city": "Tokio", "country": "Peru", "country_code": "pe"},
                   lat=-8.0, lon=-75.0, display="Tokio, Peru"),
        tokyo_result(),
    ])
    status, lat, _lon, short_label, _display = geo(query)
    assert status == lb.GEOCODE_OK
    assert lat == 35.6895 and short_label == "Tokio"


def test_candidate_without_country_code_is_rejected_when_country_is_known():
    FakeNominatim.responses.append([
        FakeResult({"city": "Tokio", "country": "Japonia"}, lat=35.6895, lon=139.6917,
                   display="Tokio, Japonia"),
    ])
    assert geo("Tokio, Japonia") == (lb.GEOCODE_NO_MATCH, None, None, None, None)


def test_country_mismatch_is_no_match_not_a_guess():
    FakeNominatim.responses.append([tokyo_result(), tokyo_result()])
    assert geo("Tokio, Korea Południowa")[0] in (lb.GEOCODE_NO_MATCH,)


def test_context_query_keeps_admin_context_in_the_geocoder_query():
    FakeNominatim.responses.append([
        FakeResult({"city": "Wiązowna", "county": "otwocki", "country": "Polska",
                    "country_code": "pl"}, lat=52.17, lon=21.29,
                   display="Wiązowna, powiat otwocki, Polska"),
    ])
    status, _lat, _lon, short_label, _display = geo("Wiązowna,otwock,Polska")
    assert status == lb.GEOCODE_OK and short_label == "Wiązowna"
    call = forward_calls()[-1]
    assert call["query"] == "Wiązowna, otwock"
    assert call["extras"].get("country_codes") == "pl"


def test_no_extra_requests_for_any_country_form():
    """Brak nowego fallbacku sieciowego: dokładnie jedno zapytanie na formę."""
    for query, response in [
        ("Tokio, Japonia", [tokyo_result()]),
        ("Hel,PL", [poland_place("Hel", municipality="Hel", postcode="84-150")]),
        ("Wiązowna,otwock,Polska", [poland_place("Wiązowna")]),
        ("Atlanta, Georgia", [atlanta_result()]),
    ]:
        FakeNominatim.calls = []
        FakeNominatim.responses.append(response)
        geo(query)
        assert len(forward_calls()) == 1, query


# ============================================================================
# 5. FAIL-CLOSED (D7)
# ============================================================================

@pytest.fixture()
def broken_index(monkeypatch, tmp_path):
    """Indeks niedostępny: zła ścieżka + zresetowany singleton."""
    monkeypatch.setattr(ci, "INDEX_PATH", str(tmp_path / "brak.json"))
    ci.reset()
    yield tmp_path
    ci.reset()


@pytest.mark.parametrize(
    "query", ["Tokio, Japonia", "Tokio", "Warszawa, Polska", "Hel PL", "Wiązowna, otwock"]
)
def test_missing_index_fails_closed_without_any_request(broken_index, query):
    FakeNominatim.responses.append([tokyo_result()])
    assert geo(query) == (lb.GEOCODE_ERROR, None, None, None, None)
    assert forward_calls() == [], "nie ma wyszukiwania bez filtra kraju"


def test_too_short_wins_over_index_availability(broken_index):
    """TOO_SHORT jest pierwszy — nie dotyka mapy i nie potrzebuje indeksu."""
    assert geo("U") == (lb.GEOCODE_TOO_SHORT, None, None, None, None)
    assert forward_calls() == []


def test_corrupted_index_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "country_index.json"
    path.write_text("{ uszkodzony", encoding="utf-8")
    monkeypatch.setattr(ci, "INDEX_PATH", str(path))
    ci.reset()
    FakeNominatim.responses.append([tokyo_result()])
    assert geo("Tokio, Japonia")[0] == lb.GEOCODE_ERROR
    assert forward_calls() == []


def test_index_unavailable_is_reported_loudly(broken_index, capsys):
    geo("Tokio")
    log = capsys.readouterr().out
    assert "[COUNTRY_INDEX]" in log and "fail-closed" in log


def test_gps_and_pin_do_not_depend_on_the_country_index(broken_index, monkeypatch):
    """Pinezka/GPS nie mają tekstu — indeks krajów nie może ich blokować."""
    monkeypatch.setattr(
        lb, "get_location_details_from_coords",
        lambda lat, lon, lang, query=None, mode=None: (
            "Tokio", "Tokio, Japonia", lb.GEO_OK,
        ),
    )
    assert lb._resolve_location_labels(35.68, 139.69, "pl") == (
        "Tokio", "Tokio, Japonia", lb.GEO_OK,
    )
    # Ta sama ścieżka z query (tekstowa) nadal działa lokalnie — fail-closed
    # dotyczy wyłącznie geokodowania forward.
    assert lb.get_city_from_coords(35.68, 139.69, "pl") == "Tokio"


def test_available_index_keeps_the_old_positive_path():
    FakeNominatim.responses.append([tokyo_result()])
    assert geo("Tokio, Japonia")[0] == lb.GEOCODE_OK


# ============================================================================
# 6. SPRZECZNE WSKAZANIA KRAJU — TYLKO KOMPATYBILNOŚĆ WSTECZNA
# ============================================================================

def test_conflicting_countries_keep_legacy_behaviour_and_are_documented():
    """OGRANICZENIE, nie reguła: parser bierze ostatni segment kraju.

    To zachowanie istniejącej gramatyki, utrzymane w fazie 1 wyłącznie po to, by
    nie zmieniać kontraktu parsera i nie dodawać requestów. Poprawne
    rozstrzyganie sprzeczności jest ODŁOŻONE — ten test ma udokumentować stan,
    nie go zatwierdzić jako docelowy.
    """
    core, admin, code = lb._geocode_query_parse("Tokio, Japonia, Polska")
    assert (core, admin, code) == ("Tokio", ["Japonia"], "pl")
    assert "sprzeczno" in lb._geocode_query_parse.__doc__.lower(), (
        "docstring parsera musi nazywać to ograniczenie wprost"
    )


def test_conflicting_countries_do_not_add_requests_or_change_status_shape():
    FakeNominatim.responses.append([poland_place("Tokio", country_code="pl",
                                                 country="Polska")])
    result = geo("Tokio, Japonia, Polska")
    assert len(result) == 5
    assert len(forward_calls()) == 1


# ============================================================================
# 7. KONTRAKTY PUBLICZNE BEZ ZMIAN
# ============================================================================

def test_public_geocode_contract_is_unchanged():
    FakeNominatim.responses.append([tokyo_result()])
    result = geo("Tokio, Japonia")
    assert isinstance(result, tuple) and len(result) == 5
    status, lat, lon, short_label, display = result
    assert (status, short_label, display) == (lb.GEOCODE_OK, "Tokio", "Tokio, Japonia")
    assert (lat, lon) == (35.6895, 139.6917)

    FakeNominatim.responses.append([tokyo_result()])
    assert len(lb.geocode_city_labels_status("Tokio, Japan")) == 5
    FakeNominatim.responses.append([tokyo_result()])
    assert len(lb.geocode_city_details_status("Tokio, JP")) == 4
    FakeNominatim.responses.append([tokyo_result()])
    assert len(lb.get_coords_from_city("Tokio")) == 3


def test_select_accepted_contract_is_unchanged():
    status, location = lb._geocode_select_accepted("Tokio", [tokyo_result()], "jp", [])
    assert status == lb.GEOCODE_OK and location is not None


def test_raw_forward_address_is_never_used_as_a_label():
    """Istniejąca pułapka: `location.address` nie może wejść do etykiety."""
    FakeNominatim.responses.append([tokyo_result()])
    _status, _lat, _lon, short_label, display = geo("Tokio, Japonia")
    assert "RAW" not in (display or "") and "RAW" not in (short_label or "")


# ============================================================================
# 8. SKRÓT RATUNKOWY GOŚCIA (jedno źródło prawdy)
# ============================================================================

@pytest.mark.parametrize(
    "query,expected",
    [
        ("Tokio, Japonia proszę", False),   # przecinek: skrót zabroniony
        ("Tokio Japonia proszę", True),     # kraj przetrwa pierwsze 2 tokeny
        ("Tokio Japonia super extra", True),
        ("Hel super PL", False),            # skrót zgubiłby kraj
        ("Hel PL proszę", True),            # "Hel PL" zachowuje kraj
        ("Atlanta Georgia teraz", True),    # kontekst administracyjny, nie kraj
        ("Nowy Jork super", True),
    ],
)
def test_shortening_follows_the_country_index(query, expected):
    assert lb.geocode_shortening_is_safe(query) is expected


# ============================================================================
# 9. DANE: BRAK NOWYCH ZALEŻNOŚCI I BRAK SIECI W TYM PLIKU
# ============================================================================

def test_index_module_is_the_only_country_source_of_truth():
    """location_bot nie ma już własnego słownika krajów."""
    assert not hasattr(lb, "_GEOCODE_COUNTRY_ALIASES")
    overrides = json.load(open(os.path.join(BASE_DIR, "tools", "country_index_overrides.json"),
                               encoding="utf-8"))
    assert set(overrides["context_ambiguous"]) == {"georgia", "wlochy"}

# ============================================================================
# 10. TRYB GOŚCIA (skróty i wzmianka @bot) — to samo źródło prawdy o kraju
# ============================================================================

def _forward_labels_resolver(lat, lon, lang, query, fallback_short, fallback_display):
    """Produkcyjne okablowanie: etykiety z zaakceptowanego forward."""
    return fallback_short, fallback_display, lb.GEO_OK


@pytest.mark.parametrize(
    "text,expected_query",
    [
        ("?12 Tokio, Japonia", "Tokio"),
        ("?n Tokio, JP", "Tokio"),
        (".n Tokio, Japan", "Tokio"),
        ("@PogodaWorldBot Tokio, Japonia", "Tokio"),
    ],
)
def test_guest_shortcuts_and_mention_use_the_country_filter(text, expected_query):
    FakeNominatim.responses.append([tokyo_result()])
    handled, _sent, photos, _payloads = guest_handler_run(
        text, lb.geocode_city_labels_status, city_name=None,
        resolve_labels_fn=_forward_labels_resolver,
    )
    assert handled is True
    assert photos, "zwalidowany kraj musi dać kartę"
    calls = forward_calls()
    assert len(calls) == 1, "skrót nie może dublować requestów"
    assert calls[0]["query"] == expected_query
    assert calls[0]["extras"].get("country_codes") == "jp"


def test_guest_without_country_is_unchanged():
    FakeNominatim.responses.append([tokyo_result()])
    handled, _sent, photos, _payloads = guest_handler_run(
        "?12 Tokio", lb.geocode_city_labels_status, city_name=None,
        resolve_labels_fn=_forward_labels_resolver,
    )
    assert handled is True and photos
    assert "country_codes" not in forward_calls()[-1]["extras"]


def test_guest_wrong_country_still_produces_no_card():
    """Kandydat z innym krajem odpada także w trybie gościa (brak karty)."""
    FakeNominatim.responses.append([
        FakeResult({"city": "Hel", "country": "Norway", "country_code": "no"},
                   lat=59.0, lon=10.9, display="Hel, Norway"),
    ])
    handled, _sent, photos, _payloads = guest_handler_run(
        "?12 Hel, PL", lb.geocode_city_labels_status, city_name=None,
        resolve_labels_fn=_forward_labels_resolver,
    )
    assert handled is True
    assert photos == []


def test_guest_shortening_never_drops_the_country():
    """"Hel super PL" nie może zostać skrócone do "Hel" bez kraju."""
    assert lb.geocode_shortening_is_safe("Hel super PL") is False
    assert lb.geocode_shortening_is_safe("Hel PL proszę") is True
    # Pierwsza próba ("Hel PL proszę") nie ma kraju na końcu, więc geokoder
    # odpowiada pustką; skrót ratunkowy "Hel PL" zachowuje kraj i trafia.
    FakeNominatim.responses.append([])
    FakeNominatim.responses.append(
        [poland_place("Hel", municipality="Hel", postcode="84-150")]
    )
    handled, _sent, photos, _payloads = guest_handler_run(
        "?12 Hel PL proszę", lb.geocode_city_labels_status, city_name=None,
        resolve_labels_fn=_forward_labels_resolver,
    )
    assert handled is True and photos
    assert forward_calls()[-1]["extras"].get("country_codes") == "pl"

# ============================================================================
# 11. NAZWY WIELOWYRAZOWE, KTÓRYCH OSTATNI TOKEN JEST INNĄ NAZWĄ KRAJU
# ============================================================================

@pytest.mark.parametrize(
    "query,code",
    [
        ("X, Papua New Guinea", "pg"),          # ostatni token "guinea" => GN
        ("X, Papua-Nowa Gwinea", "pg"),
        ("X, Equatorial Guinea", "gq"),
        ("X, Gwinea Równikowa", "gq"),
        ("X, South Sudan", "ss"),
        ("X, Sudan Południowy", "ss"),
        ("X, Caribbean Netherlands", "bq"),
        ("X, Bouvet Island", "bv"),
        ("X, South Georgia & South Sandwich Islands", "gs"),
    ],
)
def test_long_names_win_over_their_last_token(query, code):
    """Najpierw najdłuższy sufiks: pełna nazwa kraju wygrywa z jej ostatnim tokenem."""
    parsed = lb._geocode_query_country(query)
    assert parsed == ("X", code), (query, parsed)


def test_democratic_republic_of_the_congo_full_name_with_the():
    """Pełna nazwa z "the" daje cd (wcześniej spadała do sufiksu "congo" = cg)."""
    assert lb._geocode_query_country("Kinshasa, Democratic Republic of the Congo") == (
        "Kinshasa", "cd",
    )
    assert lb._geocode_query_parse("Kinshasa, Democratic Republic of the Congo") == (
        "Kinshasa", [], "cd",
    )


def test_democratic_republic_of_the_congo_full_name_without_the():
    """Wariant bez "the" jest osobnym, jawnym aliasem — nie zgadywaniem."""
    assert lb._geocode_query_country("Kinshasa, Democratic Republic of Congo") == (
        "Kinshasa", "cd",
    )
    assert lb._geocode_query_country("Kinshasa, democratic republic of congo") == (
        "Kinshasa", "cd",
    )


@pytest.mark.parametrize(
    "query",
    [
        "X, Democratic Republic of the Congo",
        "X, Democratic Republic of Congo",
        "X, DEMOCRATIC REPUBLIC OF THE CONGO",
        "X,Democratic Republic of the Congo",
    ],
)
def test_full_congo_name_wins_over_the_shorter_congo_suffix(query):
    """Najdłuższy sufiks wygrywa: pełna nazwa (cd) przed krótkim "congo" (cg)."""
    assert lb._geocode_query_country(query) == ("X", "cd")
    assert ci.classify("democratic republic of the congo").codes == ("cd",)


def test_kinshasa_is_not_rejected_because_congo_was_matched_first():
    """Regresja, którą ta korekta zamyka.

    Wcześniej: ostatni token "congo" => filtr cg => kandydat Kinszasy (cd)
    odrzucony => NO_MATCH mimo poprawnego zapytania.
    Teraz: filtr cd, więc kandydat z DR Konga przechodzi, a kandydat z Republiki
    Konga (cg) jest odrzucany.
    """
    FakeNominatim.responses.append([
        FakeResult({"city": "Kinshasa", "country": "Democratic Republic of the Congo",
                    "country_code": "cd"}, lat=-4.32, lon=15.31,
                   display="Kinshasa, Democratic Republic of the Congo"),
    ])
    status, lat, lon, short_label, display = geo(
        "Kinshasa, Democratic Republic of the Congo"
    )
    assert status == lb.GEOCODE_OK
    assert (lat, lon) == (-4.32, 15.31)
    assert short_label == "Kinshasa"
    assert "Democratic Republic" in display
    call = forward_calls()[-1]
    assert call["query"] == "Kinshasa"
    assert call["extras"].get("country_codes") == "cd"


def test_kinshasa_with_brazzaville_candidate_is_rejected():
    """Filtr cd nadal jest twardy: kandydat z cg przy pełnej nazwie DR Konga odpada."""
    FakeNominatim.responses.append([
        FakeResult({"city": "Kinshasa", "country": "Kongo", "country_code": "cg"},
                   lat=-4.32, lon=15.31, display="Kinshasa, Kongo"),
    ])
    assert geo("Kinshasa, Democratic Republic of Congo") == (
        lb.GEOCODE_NO_MATCH, None, None, None, None,
    )


def test_short_form_congo_is_unchanged():
    """Krótkie "congo" zostaje przy swoim kodzie (dane CLDR), bez arbitralnej zmiany."""
    assert lb._geocode_query_country("X, Congo") == ("X", "cg")
    assert ci.classify("congo").codes == ("cg",)


def test_kongo_stays_ambiguous():
    assert lb._geocode_query_country("X, Kongo") == ("X, Kongo", None), "kolizja cd/cg"
    assert ci.classify("Kongo").codes == ("cd", "cg")


@pytest.mark.parametrize(
    "query", ["X, Bosnia and Herzegovina", "X, Antigua and Barbuda"]
)
def test_clrd_ampersand_style_is_a_documented_limitation(query):
    """CLDR używa "&", które normalizacja zamienia na spację.

    Te dwie nazwy NIE mają ręcznego aliasu z "and" (świadomie: bez zamiany "and"
    na "&" i bez fuzzy matchingu), więc pozostają kontekstem administracyjnym —
    tak jak przed fazą 1.
    """
    assert lb._geocode_query_country(query) == (query, None)


@pytest.mark.parametrize(
    "query,code",
    [
        ("X, Trinidad & Tobago", "tt"),
        ("X, Trinidad and Tobago", "tt"),
        ("X, TRINIDAD AND TOBAGO", "tt"),
        ("X, Bosnia & Herzegovina", "ba"),
        ("X, Antigua & Barbuda", "ag"),
        ("X, Trynidad i Tobago", "tt"),
        ("X, Bośnia i Hercegowina", "ba"),
    ],
)
def test_clrd_ampersand_forms_do_resolve(query, code):
    assert lb._geocode_query_country(query) == ("X", code)
