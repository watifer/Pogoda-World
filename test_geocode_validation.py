"""test_geocode_validation.py — ETAP 1: walidacja krótkich i niepasujących zapytań.

Dlaczego to istnieje: Nominatim dla krótkiej lub niepełnej nazwy potrafi zwrócić
miejsce, które z zapytaniem nie ma nic wspólnego — "U" -> Chubut (Argentyna),
"Wa" -> Little Sandy Desert (Australia), "Hel" -> Helmand (Afganistan). Etap 1
wprowadza wspólną walidację z jawnymi statusami, bez inline keyboarda, bez
rankingu i bez wybierania z listy:

  1. GEOCODE_TOO_SHORT  — 1-2 znaki bez kontekstu, NIE pyta mapy,
  2. kandydaci          — exactly_one=False, limit=7, addressdetails=True,
                          language=lang (jedno zapytanie, kilku kandydatów),
  3. akceptacja         — nazwa miejscowości kandydata = zapytanie jako pełna
                          nazwa albo pełny token; prefiks nie wystarcza
                          ("hel" != "helmand", "wa" != "western australia"),
  4. GEOCODE_NO_MATCH / GEOCODE_OK / GEOCODE_UNCERTAIN — strict: zero
                          dopasowań, jedno rozróżnialne miejsce, więcej niż
                          jedno rozróżnialne miejsce,
  5. GEOCODE_NOT_FOUND  — mapa odpowiedziała pustką (None albo []),
  6. GEOCODE_ERROR      — mapa nie odpowiedziała (wyjątek sieciowy).

Te same statusy czytają /dzien, /teraz, /trend, /miasto, prompty, skróty i
wzmianka @bot. Cache trybu gościa przyjmuje wyłącznie GEOCODE_OK.

Żaden test nie wykonuje prawdziwego zapytania do Nominatima.

Uruchomienie: pytest test_geocode_validation.py -v
"""

import pytest

import guest_bot_handler as gbh
import i18n
import location_bot as lb

LANGS = ("pl", "en", "de", "es", "fr", "no")
ALL_STATUSES = (
    lb.GEOCODE_OK,
    lb.GEOCODE_TOO_SHORT,
    lb.GEOCODE_NO_MATCH,
    lb.GEOCODE_UNCERTAIN,
    lb.GEOCODE_NOT_FOUND,
    lb.GEOCODE_ERROR,
)
# Surowy adres z location.address — jeśli ktokolwiek go przeczyta, test pada.
POISON = "RAW 41 Koscielna, Biblioteka publiczna, Osiedle Parkowe"


# ============================================================================
# ATRAPY
# ============================================================================

class FakeResult:
    """Tyle, ile geopy wystawia w Location: latitude / longitude / raw / address."""

    def __init__(self, address=None, lat=54.6037, lon=18.7616, cls="place", typ="town",
                 name=None, namedetails=None, display=None):
        raw = {"class": cls, "type": typ, "lat": str(lat), "lon": str(lon)}
        if address is not None:
            raw["address"] = dict(address)
        if name is not None:
            raw["name"] = name
        if namedetails is not None:
            raw["namedetails"] = dict(namedetails)
        raw["display_name"] = (
            display if display is not None
            else ", ".join(str(value) for value in (address or {}).values())
        )
        self.raw = raw
        self.latitude = lat
        self.longitude = lon
        # Pułapka: publiczne etykiety nigdy nie mogą czytać tego pola.
        self.address = POISON


def poland_place(name, lat=54.6037, lon=18.7616, **extra):
    address = {"city": name, "country": "Polska", "country_code": "pl"}
    address.update(extra)
    return FakeResult(address, lat=lat, lon=lon, display=f"{name}, Polska")


def admin_boundary(name, extra=None, lat=54.6037, lon=18.7616):
    """Granica administracyjna bez pola miejscowości — typowy wynik relacji."""
    address = {"municipality": name, "country": "Polska", "country_code": "pl"}
    address.update(extra or {})
    return FakeResult(
        address, cls="boundary", typ="administrative", lat=lat, lon=lon,
        display=f"{name}, powiat pucki, wojewodztwo pomorskie, Polska",
    )


class FakeNominatim:
    """Atrapa geopy.geocoders.Nominatim — oddaje dokładnie to, co włożymy."""

    calls = []
    responses = []

    def __init__(self, user_agent=None):
        FakeNominatim.calls.append({"init_user_agent": user_agent})

    def geocode(self, query, exactly_one=True, language=None, limit=None,
                addressdetails=None, **kwargs):
        FakeNominatim.calls.append({
            "query": query, "exactly_one": exactly_one, "language": language,
            "limit": limit, "addressdetails": addressdetails,
        })
        if not FakeNominatim.responses:
            raise AssertionError("geokoder zapytany po raz drugi bez odpowiedzi")
        result = FakeNominatim.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    @staticmethod
    def forward_calls():
        return [call for call in FakeNominatim.calls if "query" in call]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    FakeNominatim.calls = []
    FakeNominatim.responses = []
    monkeypatch.setattr(lb, "Nominatim", FakeNominatim)
    lb._GEO_DETAILS_CACHE.clear()
    gbh._GEO_CACHE.clear()
    gbh._CITY_CACHE.clear()
    yield
    FakeNominatim.calls = []
    FakeNominatim.responses = []
    lb._GEO_DETAILS_CACHE.clear()
    gbh._GEO_CACHE.clear()
    gbh._CITY_CACHE.clear()


def geo(query, lang="pl"):
    """Skrót testowy: pełny wynik rdzenia (status, lat, lon, short, display)."""
    return lb.geocode_city_accepted(query, lang)


# ============================================================================
# 1. STATUSY: JAWNE, ROZŁĄCZNE, WSPÓLNE DLA WSZYSTKICH ŚCIEŻEK
# ============================================================================

def test_statuses_are_distinct_and_shared_by_all_modules():
    assert len(set(ALL_STATUSES)) == 6, "statusy nie mogą się nachodzić"
    # location_bot i guest_bot_handler muszą widzieć TE SAME wartości.
    assert lb.GEOCODE_OK == i18n.GEOCODE_OK == gbh.GEOCODE_OK
    assert lb.GEOCODE_NO_MATCH == i18n.GEOCODE_NO_MATCH == gbh.GEOCODE_NO_MATCH
    assert lb.GEOCODE_NOT_FOUND == i18n.GEOCODE_NOT_FOUND == gbh.GEOCODE_NOT_FOUND
    # NO_MATCH i UNCERTAIN mogą brzmieć podobnie, ale to dwa różne statusy.
    assert lb.GEOCODE_NO_MATCH != lb.GEOCODE_UNCERTAIN
    assert i18n.UI_TEXTS["pl"]["geocode_no_match"] != i18n.UI_TEXTS["pl"]["geocode_uncertain"]


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", ["geocode_too_short", "geocode_no_match", "geocode_uncertain"])
def test_status_messages_exist_in_every_language(lang, key):
    text = i18n.UI_TEXTS[lang][key]
    assert len(text) > 15
    assert "`" not in text, "żadnego formatu, który zabiera klikalność"
    assert "/" not in text, "komunikat statusowy nie reklamuje komend"
    assert text.count("*") % 2 == 0


def test_no_inline_keyboard_and_no_city_list_anywhere_in_the_new_texts():
    for lang in LANGS:
        for key in ("geocode_too_short", "geocode_no_match", "geocode_uncertain"):
            assert "\n1." not in i18n.UI_TEXTS[lang][key], "żadnej listy wyborów"


def test_not_found_and_error_reuse_the_existing_messages():
    assert i18n.GEOCODE_UI_KEYS[i18n.GEOCODE_NOT_FOUND] == "search_fail"
    assert i18n.GEOCODE_UI_KEYS[i18n.GEOCODE_ERROR] == "geo_conn_err"
    assert i18n.t_geocode("pl", i18n.GEOCODE_OK) == "", "sukces nie ma komunikatu"
    assert i18n.t_geocode("pl", "jakis-obcy-status") == i18n.t_ui("pl", "search_fail")
    for lang in LANGS:
        assert i18n.t_geocode(lang, i18n.GEOCODE_ERROR) == i18n.t_ui(lang, "geo_conn_err")
        assert i18n.t_geocode(lang, i18n.GEOCODE_NOT_FOUND) == i18n.t_ui(lang, "search_fail")


# ============================================================================
# 2. ZA KRÓTKIE ZAPYTANIE: ZERO WYWOŁAŃ GEOKODERA
# ============================================================================

@pytest.mark.parametrize("query", ["U", "Wa", "Os", "a b", "S.", "", "  ", "–"])
def test_short_query_is_too_short_without_touching_the_map(query):
    FakeNominatim.responses.append([poland_place("Cokolwiek")])
    assert geo(query) == (lb.GEOCODE_TOO_SHORT, None, None, None, None)
    assert FakeNominatim.forward_calls() == [], "za krótkie zapytanie nie pyta mapy"
    assert lb._geocode_query_is_too_short(query)


@pytest.mark.parametrize("query", [
    "Hel, PL",            # jawny kraj po przecinku
    "Hel, Polska",
    "Wiązowna 05-462",    # kod pocztowy
    "05-462",
    "Warszawa",
    "Nowy Targ",
])
def test_context_allows_the_attempt(query):
    assert not lb._geocode_query_is_too_short(query)


def test_context_still_needs_a_matching_name():
    """Kontekst odblokowuje zapytanie, ale NIE zastępuje dowodu nazwy."""
    FakeNominatim.responses.append([poland_place("Gdynia")])
    assert geo("U, Polska")[0] == lb.GEOCODE_NO_MATCH


# ============================================================================
# 3. KONTRAKT ZAPYTANIA DO NOMINATIMA
# ============================================================================

@pytest.mark.parametrize("lang", ["pl", "de", "no"])
def test_forward_query_parameters(lang):
    FakeNominatim.responses.append([poland_place("Hel")])
    geo("Hel", lang)
    call = FakeNominatim.forward_calls()[-1]
    assert call["query"] == "Hel"
    assert call["exactly_one"] is False, "bez kilku kandydatów nie ma walidacji"
    assert call["limit"] == lb.GEOCODE_CANDIDATE_LIMIT == 7
    assert call["addressdetails"] is True, "dowodem są strukturalne pola adresu"
    assert call["language"] == lang
    assert len(FakeNominatim.forward_calls()) == 1, "jedno zapytanie na wszystkich kandydatów"


def test_public_and_compat_signatures_are_unchanged():
    """Etap 1 nie wolno okupić zmianą starego API (kontrakt, pkt 1)."""
    FakeNominatim.responses.extend([[poland_place("Hel")] for _ in range(4)])
    assert len(lb.get_coords_from_city("Hel", "pl")) == 3
    assert len(lb.geocode_city_details("Hel", "pl")) == 4
    lat, lon, display, status = lb.geocode_city_details_status("Hel", "pl")
    assert (lat, lon, status) == (54.6037, 18.7616, lb.GEOCODE_OK)
    assert "RAW" not in display
    short_lat, short_lon, short_label, short_display, ok = lb._geocode_city_public_details(
        "Hel", "pl"
    )
    assert (short_lat, short_lon, ok) == (54.6037, 18.7616, True)
    assert short_label == "Hel"


def test_compat_bool_ok_only_distinguishes_a_transport_failure():
    """Dlatego nowe ścieżki nie mogą polegać na starym `ok`."""
    for status in (lb.GEOCODE_TOO_SHORT, lb.GEOCODE_NO_MATCH, lb.GEOCODE_UNCERTAIN,
                   lb.GEOCODE_NOT_FOUND):
        monkey_status = status
        assert monkey_status != lb.GEOCODE_ERROR
    FakeNominatim.responses.append([poland_place("Cokolwiek")])
    lat, lon, display, ok = lb.geocode_city_details("Wa", "pl")
    assert (lat, lon, display, ok) == (None, None, None, True), "TOO_SHORT to nie awaria"


# ============================================================================
# 4. DOWÓD PASOWANIA: PEŁNA NAZWA ALBO PEŁNY TOKEN
# ============================================================================

def test_hel_beats_helmand_and_ignores_the_station():
    """Dokładnie ten błąd, po który przyszedł etap 1."""
    FakeNominatim.responses.append([
        FakeResult({"city": "Hel", "road": "Morska", "amenity": "Latarnia morska",
                    "country": "Polska", "country_code": "pl"},
                   cls="railway", typ="station", name="Hel (stacja kolejowa)"),
        FakeResult({"state": "Helmand", "country": "Afganistan", "country_code": "af"},
                   cls="boundary", typ="administrative", lat=31.5, lon=65.0,
                   display="Helmand, Afganistan"),
        poland_place("Hel", municipality="Hel", county="pucki", state="pomorskie",
                     postcode="84-150"),
    ])

    status, lat, lon, short_label, display = geo("Hel")
    assert status == lb.GEOCODE_OK
    assert (lat, lon) == (54.6037, 18.7616)
    assert short_label == "Hel"
    assert "Helmand" not in display and "Afganistan" not in display
    # Poprzednia poprawka nadal obowiązuje: etykiety tylko z pól strukturalnych.
    assert display == (
        "Hel, gmina Hel, powiat pucki, województwo pomorskie, 84-150, Polska"
    )
    assert POISON not in display
    assert "Morska" not in display and "Latarnia" not in display


def test_only_helmand_is_no_match_not_a_card_for_afghanistan():
    FakeNominatim.responses.append([
        FakeResult({"state": "Helmand", "country": "Afganistan", "country_code": "af"},
                   cls="boundary", typ="administrative", lat=31.5, lon=65.0,
                   display="Helmand, Afganistan"),
    ])
    assert geo("Hel") == (lb.GEOCODE_NO_MATCH, None, None, None, None)


@pytest.mark.parametrize("query,expected", [
    ("Hel", lb.GEOCODE_NO_MATCH),        # 3 znaki -> pyta, ale prefiks nie wystarcza
    ("Med", lb.GEOCODE_NO_MATCH),
    ("Warszaw", lb.GEOCODE_NO_MATCH),
    ("Wa", lb.GEOCODE_TOO_SHORT),        # 2 znaki -> nie pyta w ogóle
    ("Os", lb.GEOCODE_TOO_SHORT),
])
def test_prefix_of_a_longer_word_never_matches(query, expected):
    field_value = {"Hel": "Helmand", "Med": "Mediola", "Warszaw": "Warszawa",
                   "Wa": "Western Australia", "Os": "Oslo"}[query]
    country = "Polska" if query in ("Hel", "Med", "Warszaw") else "Australia"
    FakeNominatim.responses.append([
        FakeResult({"city": field_value, "country": country, "country_code": "xx"}),
    ])
    assert geo(query)[0] == expected
    if expected == lb.GEOCODE_NO_MATCH:
        assert lb._geocode_name_matches(
            lb._normalize_location_text(query), lb._normalize_location_text(field_value)
        ) is False


def test_wa_never_matches_western_australia_even_with_context():
    FakeNominatim.responses.append([
        FakeResult({"state": "Western Australia", "country": "Australia",
                    "country_code": "au"}, cls="boundary", typ="administrative",
                   lat=-25.0, lon=129.0,
                   display="Western Australia, Australia"),
    ])
    # Kraj w zapytaniu pozwala zapytać mapę, ale "wa" nie jest pełnym tokenem.
    assert geo("Wa, Australia")[0] == lb.GEOCODE_NO_MATCH


def test_full_name_and_full_token_are_both_accepted():
    FakeNominatim.responses.append([
        FakeResult({"town": "Nowy Targ", "country": "Polska", "country_code": "pl"},
                   lat=49.47, lon=20.03, display="Nowy Targ, Polska"),
    ])
    status, lat, _lon, short_label, _display = geo("Nowy Targ")
    assert status == lb.GEOCODE_OK
    assert (lat, short_label) == (49.47, "Nowy Targ")


@pytest.mark.parametrize("query", ["Wiązowna", "WIĄZOWNA", "wiązowna", "wiązowna."])
def test_diacritics_case_and_punctuation_are_folded_before_comparing(query):
    FakeNominatim.responses.append([
        FakeResult({"city": "Wiązowna", "country": "Polska", "country_code": "pl"},
                   lat=52.15, lon=21.29, display="Wiązowna, Polska"),
    ])
    assert geo(query)[0] == lb.GEOCODE_OK, query


def test_administrative_fields_are_not_evidence_for_a_place():
    """country/state/liczba w polu administracyjnym NIE dowodzą miejscowości."""
    FakeNominatim.responses.append([
        FakeResult({"city": "Warszawa", "state": "mazowieckie", "country": "Polska",
                    "country_code": "pl"}, display="Warszawa, mazowieckie, Polska"),
    ])
    # "Polska" i "mazowieckie" są w adresie kandydata, ale nie w polu miejscowości.
    assert geo("Polska")[0] == lb.GEOCODE_NO_MATCH
    FakeNominatim.responses.append([
        FakeResult({"city": "Warszawa", "state": "mazowieckie", "country": "Polska",
                    "country_code": "pl"}, display="Warszawa, mazowieckie, Polska"),
    ])
    assert geo("Mazowieckie")[0] == lb.GEOCODE_NO_MATCH


def test_raw_name_cannot_override_contradicting_locality_field():
    FakeNominatim.responses.append([
        FakeResult({"city": "Gdynia", "country": "Polska", "country_code": "pl"},
                   name="Hel", display="Hel, Gdynia, Polska"),
    ])
    assert geo("Hel")[0] == lb.GEOCODE_NO_MATCH, (
        "surowa nazwa nie może nadpisać sprzecznych danych miejscowości"
    )


def test_namedetails_are_a_fallback_only_when_no_locality_fields_exist():
    FakeNominatim.responses.append([
        FakeResult({"municipality": "Hel", "country": "Polska", "country_code": "pl"},
                   cls="boundary", typ="administrative",
                   namedetails={"name": "Hel", "name:pl": "Hel"},
                   display="Hel, powiat pucki, Polska"),
    ])
    status, _lat, _lon, short_label, _display = geo("Hel")
    assert status == lb.GEOCODE_OK
    assert short_label, "etykieta i tak pochodzi z pól strukturalnych"


# ============================================================================
# 5. FILTR KLASY KANDYDATA
# ============================================================================

@pytest.mark.parametrize("cls,typ", [
    ("natural", "desert"),
    ("railway", "station"),
    ("amenity", "village_green"),
    ("tourism", "attraction"),
    ("shop", "yes"),
    ("highway", "residential"),
    ("leisure", "park"),
    ("waterway", "river_bank"),
])
def test_unsafe_classes_are_rejected_even_when_the_name_matches(cls, typ):
    """Ten filtr odcina "Wa" -> Little Sandy Desert i cały szum POI."""
    FakeNominatim.responses.append([
        FakeResult({"city": "Wa", "state": "Western Australia", "country": "Australia",
                    "country_code": "au"}, cls=cls, typ=typ, lat=-25.0, lon=129.0,
                   display="Little Sandy Desert, Western Australia, Australia"),
    ])
    assert geo("Wa, Australia")[0] == lb.GEOCODE_NO_MATCH


def test_safe_classes_are_accepted():
    for location in (poland_place("Hel"), admin_boundary("Hel")):
        FakeNominatim.responses.append([location])
        assert geo("Hel")[0] == lb.GEOCODE_OK


# ============================================================================
# 6. STRICT DISAMBIGUATION
# ============================================================================

def test_two_distinguishable_places_are_uncertain_not_a_guess():
    FakeNominatim.responses.append([
        poland_place("Hel", municipality="Hel", postcode="84-150"),
        FakeResult({"village": "Hel", "country": "Norway", "country_code": "no"},
                   lat=59.0, lon=10.9, display="Hel, Norway"),
    ])
    assert geo("Hel", "pl") == (lb.GEOCODE_UNCERTAIN, None, None, None, None)


def test_no_ranking_between_city_and_village():
    """Bez rankingu: nawet 'miasto' + 'wieś' o tej samej nazwie to UNCERTAIN."""
    FakeNominatim.responses.append([
        FakeResult({"city": "Hel", "country": "Polska", "country_code": "pl"},
                   cls="place", typ="city", lat=54.6037, lon=18.7616,
                   display="Hel, Polska"),
        FakeResult({"village": "Hel", "country": "Polska", "country_code": "pl"},
                   cls="place", typ="village", lat=50.10, lon=22.00,
                   display="Hel, Polska"),
    ])
    assert geo("Hel")[0] == lb.GEOCODE_UNCERTAIN


def test_the_same_place_returned_twice_is_one_place():
    """Punkt i relacja granicy to TO samo miasto — nie straszymy niepewnością."""
    FakeNominatim.responses.append([
        FakeResult({"city": "Warszawa", "state": "mazowieckie", "country": "Polska",
                    "country_code": "pl"}, lat=52.2297, lon=21.0122,
                   display="Warszawa, Polska"),
        admin_boundary("Warszawa", {"state": "mazowieckie"}, lat=52.2301, lon=21.0130),
    ])
    status, lat, lon, short_label, _display = geo("Warszawa")
    assert status == lb.GEOCODE_OK
    assert (lat, lon, short_label) == (52.2297, 21.0122, "Warszawa")


@pytest.mark.parametrize("lang", ["pl", "no", "en"])
def test_language_is_not_country_context(lang):
    """language=pl nie rozstrzyga kraju — rozstrzyga dopiero "Hel, PL" w zapytaniu."""
    FakeNominatim.responses.append([
        poland_place("Hel", municipality="Hel", postcode="84-150"),
        FakeResult({"village": "Hel", "country": "Norway", "country_code": "no"},
                   lat=59.0, lon=10.9, display="Hel, Norway"),
    ])
    assert geo("Hel", lang)[0] == lb.GEOCODE_UNCERTAIN


@pytest.mark.parametrize("query", ["Hel, PL", "Hel, Polska"])
def test_explicit_country_context_resolves_the_candidates(query):
    FakeNominatim.responses.append([
        FakeResult({"village": "Hel", "country": "Norway", "country_code": "no"},
                   lat=59.0, lon=10.9, display="Hel, Norway"),
        poland_place("Hel", municipality="Hel", county="pucki", state="pomorskie",
                     postcode="84-150"),
    ])
    status, lat, _lon, short_label, display = geo(query)
    assert status == lb.GEOCODE_OK
    assert (lat, short_label) == (54.6037, "Hel")
    assert "Norway" not in display


def test_context_that_contradicts_every_candidate_is_no_match():
    FakeNominatim.responses.append([
        poland_place("Hel", municipality="Hel"),
        FakeResult({"village": "Hel", "country": "Norway", "country_code": "no"},
                   lat=59.0, lon=10.9, display="Hel, Norway"),
    ])
    # Żaden kandydat nie potwierdza Portugalii: sprzeczny kontekst odrzuca obu.
    assert geo("Hel, Portugalia")[0] == lb.GEOCODE_NO_MATCH


# ============================================================================
# 7. KSZTAŁTY ODPOWIEDZI I AWARIE
# ============================================================================

@pytest.mark.parametrize("response", [[], None, [None], ()])
def test_empty_answers_are_not_found(response):
    FakeNominatim.responses.append(response)
    assert geo("Hel") == (lb.GEOCODE_NOT_FOUND, None, None, None, None)


def test_single_object_answer_is_accepted():
    """geopy bez limitu zwraca POJEDYNCZY obiekt, nie listę — to też musi działać."""
    FakeNominatim.responses.append(poland_place("Hel", municipality="Hel"))
    assert geo("Hel")[0] == lb.GEOCODE_OK


@pytest.mark.parametrize("error", [
    TimeoutError("timeout"),
    ConnectionError("reset"),
    Exception("HTTP 429"),
])
def test_transport_failures_are_error(error):
    FakeNominatim.responses.append(error)
    assert geo("Hel") == (lb.GEOCODE_ERROR, None, None, None, None)


def test_error_is_not_reported_as_not_found():
    FakeNominatim.responses.append(TimeoutError("timeout"))
    assert lb.geocode_city_details("Hel", "pl") == (None, None, None, False)
    FakeNominatim.responses.append([])
    assert lb.geocode_city_details("Hel", "pl") == (None, None, None, True)


def test_result_without_coordinates_is_not_ok():
    FakeNominatim.responses.append([FakeResult(
        {"city": "Hel", "country": "Polska", "country_code": "pl"},
        lat=None, lon=None, display="Hel, Polska",
    )])
    assert geo("Hel")[0] == lb.GEOCODE_NOT_FOUND


# ============================================================================
# 8. KODY POCZTOWE
# ============================================================================

def test_postcode_in_query_is_accepted_and_must_agree():
    FakeNominatim.responses.append([
        FakeResult({"city": "Wiązowna", "municipality": "Wiązowna", "county": "otwocki",
                    "state": "mazowieckie", "postcode": "05-462", "country": "Polska",
                    "country_code": "pl"}, lat=52.15, lon=21.29,
                   display="Wiązowna, 05-462, Polska"),
    ])
    status, lat, lon, short_label, display = geo("Wiązowna 05-462")
    assert status == lb.GEOCODE_OK
    assert (lat, lon, short_label) == (52.15, 21.29, "Wiązowna")
    assert "05-462" in display

    # Ta sama miejscowość z innym kodem = sprzeczność = odrzucenie.
    FakeNominatim.responses.append([
        FakeResult({"city": "Wiązowna", "postcode": "05-463", "country": "Polska",
                    "country_code": "pl"}, lat=52.15, lon=21.29,
                   display="Wiązowna, 05-463, Polska"),
    ])
    assert geo("Wiązowna 05-462")[0] == lb.GEOCODE_NO_MATCH


def test_bare_postcode_is_its_own_evidence():
    FakeNominatim.responses.append([
        FakeResult({"postcode": "05-462", "municipality": "Wiązowna",
                    "country": "Polska", "country_code": "pl"}, lat=52.15, lon=21.29,
                   display="05-462, Wiązowna, Polska"),
    ])
    assert geo("05-462")[0] == lb.GEOCODE_OK



# ============================================================================
# 8b. ADRES JAKO KONTEKST (nie jako dowód nazwy)
# ============================================================================

HOUSE = {
    "house_number": "41",
    "road": "Koscielna",
    "city": "Wiązowna",
    "municipality": "Wiązowna",
    "county": "otwocki",
    "state": "mazowieckie",
    "postcode": "05-462",
    "country": "Polska",
    "country_code": "pl",
}


def house_result():
    return FakeResult(HOUSE, cls="place", typ="house", lat=52.1483, lon=21.2861,
                      display="41, Koscielna, Wiązowna, 05-462, Polska")


def test_city_then_street_is_accepted_because_the_name_still_matches():
    """'Wiązowna, Koscielna 41': nazwa miejscowości jest dowodem, reszta kontekstem."""
    FakeNominatim.responses.append([house_result()])
    status, lat, _lon, short_label, display = geo("Wiązowna, Koscielna 41")
    assert status == lb.GEOCODE_OK
    assert (lat, short_label) == (52.1483, "Wiązowna")
    # Etykieta nadal z formattera: ulica tak (bo była w zapytaniu), numer już nie.
    assert "Koscielna" in display
    for forbidden in ("41,", ", 41", "05-462, Polska, 41", POISON):
        assert forbidden not in display


def test_context_that_names_another_place_is_a_contradiction():
    FakeNominatim.responses.append([house_result()])
    assert geo("Wiązowna, Gdynia")[0] == lb.GEOCODE_NO_MATCH


def test_neighbourhood_token_is_explained_without_being_evidence():
    address = dict(HOUSE)
    address.pop("road")
    address.pop("house_number")
    address["suburb"] = "Osiedle Parkowe"
    FakeNominatim.responses.append([
        FakeResult(address, lat=52.15, lon=21.29,
                   display="Osiedle Parkowe, Wiązowna, Polska"),
    ])
    # "Parkowe" jest w adresie kandydata -> nie jest sprzecznością.
    assert geo("Wiązowna, Osiedle Parkowe")[0] == lb.GEOCODE_OK
    FakeNominatim.responses.append([
        FakeResult(address, lat=52.15, lon=21.29,
                   display="Osiedle Parkowe, Wiązowna, Polska"),
    ])
    assert geo("Wiązowna, Osiedle Batory")[0] == lb.GEOCODE_NO_MATCH


def test_known_cost_of_stage_one_street_first_query_is_rejected():
    """Świadomy koszt etapu 1: "Koscielna 41, Wiązowna" nie ma dowodu nazwy w head.

    Etap 1 akceptuje wyłącznie pełną nazwę/pełny token miejscowości, więc
    adres zapisany "ulica najpierw" odpada z komunikatem geocode_no_match.
    Zmiana tego wymaga decyzji produktowej (patrz raport) — nie robimy jej po
    cichu w tym etapie.
    """
    FakeNominatim.responses.append([house_result()])
    assert geo("Koscielna 41, Wiązowna")[0] == lb.GEOCODE_NO_MATCH



def guest_handler_run(text, geocode_status_fn, chat_type="private", lang="pl",
                      city_name="Hel"):
    """Woła handle_guest_now z atrapami: (obsłużono, teksty, karty, payloady)."""
    sent, photos, payloads = [], [], []

    handled = gbh.handle_guest_now(
        message={
            "text": text,
            "chat": {"id": 555 if chat_type == "private" else -100555,
                     "type": chat_type},
            "from": {"language_code": lang},
        },
        bot_username="PogodaWorldBot",
        get_coords_fn=lambda city, lang="pl": (None, None, None),
        build_payload_fn=lambda lat, lon, lang, card_type, city: (
            payloads.append((lat, lon, card_type, city)) or {"location": {"tz": "UTC"}}
        ),
        prepare_layout_fn=lambda payload, card_type: {"layout": card_type},
        render_png_fn=lambda layout: "/tmp/karta.png",
        send_photo_fn=lambda chat_id, path, city, f_address: photos.append(
            (chat_id, city, f_address)
        ),
        send_reply_fn=lambda chat_id, txt: sent.append(txt),
        get_city_fn=(None if city_name is None
                    else lambda lat, lon, lang: city_name),
        geocode_status_fn=geocode_status_fn,
    )
    return handled, sent, photos, payloads


def status_fn_for(status, lat=None, lon=None, display=None):
    """Fałszywy geocode_city_details_status z listą odebranych zapytań."""
    calls = []

    def fn(city, lang):
        calls.append(city)
        if status == lb.GEOCODE_OK:
            return lat, lon, display or f"{city}, Polska", status
        return None, None, None, status

    fn.calls = calls
    return fn


def test_guest_handler_uses_the_validated_status_adapter():
    """handle_guest_now nie może pominąć walidacji — tylko adapter statusowy."""
    FakeNominatim.responses.append([
        FakeResult({"state": "Helmand", "country": "Afganistan", "country_code": "af"},
                   cls="boundary", typ="administrative", display="Helmand, Afganistan"),
    ])
    handled, sent, photos, payloads = guest_handler_run(
        "?12 Hel", lambda city, lang: lb.geocode_city_details_status(city, lang)
    )
    assert handled is True
    assert photos == [] and payloads == [], "odrzucony wynik nie może dać karty"
    assert i18n.UI_TEXTS["pl"]["geocode_no_match"] in sent[-1]
    assert "Helmand" not in " ".join(sent)


@pytest.mark.parametrize("text", ["?12 Wa", ".n Wa", "?14 Wa", "?d Wa",
                                  "@PogodaWorldBot Wa"])
def test_guest_shortcuts_and_mention_never_card_a_too_short_query(text):
    calls = []

    def fn(city, lang):
        calls.append(city)
        return lb.geocode_city_details_status(city, lang)

    handled, sent, photos, payloads = guest_handler_run(text, fn)
    assert handled is True
    assert photos == [] and payloads == []
    assert calls == ["Wa"]
    assert FakeNominatim.forward_calls() == [], "geokoder nie był zapytany ani razu"
    assert i18n.UI_TEXTS["pl"]["geocode_too_short"] in sent[-1]


@pytest.mark.parametrize("lang", ["pl", "de", "no"])
def test_guest_status_messages_follow_the_user_language(lang):
    FakeNominatim.responses.append([])
    _handled, sent, photos, _payloads = guest_handler_run(
        "?12 Hel", lambda city, l: lb.geocode_city_details_status(city, l), lang=lang
    )
    assert photos == []
    assert i18n.UI_TEXTS[lang]["search_fail"] in sent[-1]


def test_guest_groups_stay_silent_but_still_make_no_card():
    FakeNominatim.responses.append([])
    handled, sent, photos, payloads = guest_handler_run(
        "?12 Hel", lambda city, lang: lb.geocode_city_details_status(city, lang),
        chat_type="group",
    )
    assert handled is True
    assert photos == [] and payloads == []
    assert sent == [], "w grupie błąd geokodera to cisza, ale karta i tak nie powstaje"


@pytest.mark.parametrize("status", [
    lb.GEOCODE_TOO_SHORT, lb.GEOCODE_NO_MATCH, lb.GEOCODE_UNCERTAIN,
    lb.GEOCODE_NOT_FOUND, lb.GEOCODE_ERROR,
])
def test_guest_cache_accepts_only_geocode_ok(status):
    fn = status_fn_for(status)
    for _ in range(2):
        guest_handler_run("?12 Hel", fn)
    assert gbh._GEO_CACHE == {}, f"{status} nie wolno cache'ować"
    assert fn.calls == ["Hel", "Hel"], "odrzucony wynik nie może uciszać drugiej próby"


def test_guest_cache_stores_only_accepted_results_and_is_reused():
    fn = status_fn_for(lb.GEOCODE_OK, lat=54.6037, lon=18.7616,
                       display="Hel, powiat pucki, Polska")
    first, _sent, photos, payloads = guest_handler_run("?12 Hel", fn)
    second, _sent2, photos2, _payloads2 = guest_handler_run("?12 Hel", fn)

    assert first and second
    # Każde wywołanie ma własne atrapę wysyłki — karta idzie w obu, bo cache
    # zawiera GEOCODE_OK.
    assert [p[1] for p in photos + photos2] == ["Hel", "Hel"], "nazwa karty z OK-wyniku"
    assert photos[0][2] == "Hel, powiat pucki, Polska"
    assert payloads[0][:3] == (54.6037, 18.7616, "now")
    assert fn.calls == ["Hel"], "GEOCODE_OK wolno cache'ować"
    assert list(gbh._GEO_CACHE) == [("pl", "hel")]


def test_guest_fallback_to_two_tokens_is_limited_and_context_aware():
    # 1) "Nowy Jork super": NO_MATCH -> deska ratunku przechodzi walidację.
    retry_calls = []

    def fn(city, lang):
        retry_calls.append(city)
        if city == "Nowy Jork":
            return 40.71, -74.0, "Nowy Jork, Stany Zjednoczone", lb.GEOCODE_OK
        return None, None, None, lb.GEOCODE_NO_MATCH

    _handled, _sent, photos, _payloads = guest_handler_run(
        "@PogodaWorldBot Nowy Jork super", fn, city_name=None
    )
    assert retry_calls == ["Nowy Jork super", "Nowy Jork"]
    assert [p[1] for p in photos] == ["Nowy Jork"], "karta dla skróconego, zaakceptowanego"

    # Cache gościa jest kluczowany pełnym zapytaniem, więc każdy podpunkt
    # sprawdza czysty stan (patrz niżej: kasujemy tylko między wariantami).
    gbh._GEO_CACHE.clear()

    # 2) UNCERTAIN: druga próba nic nie rozstrzyga, więc jej nie ma.
    uncertain = status_fn_for(lb.GEOCODE_UNCERTAIN)
    guest_handler_run("@PogodaWorldBot Nowy Jork super", uncertain)
    assert uncertain.calls == ["Nowy Jork super"]

    gbh._GEO_CACHE.clear()
    # 3) TOO_SHORT i ERROR też nie mają drugiej próby.
    too_short = status_fn_for(lb.GEOCODE_TOO_SHORT)
    guest_handler_run("@PogodaWorldBot Nowy Jork super", too_short)
    assert too_short.calls == ["Nowy Jork super"]
    broken = status_fn_for(lb.GEOCODE_ERROR)
    guest_handler_run("@PogodaWorldBot Nowy Jork super", broken)
    assert broken.calls == ["Nowy Jork super"]

    gbh._GEO_CACHE.clear()
    # 4) kod pocztowy wolno skrócić, o ile zostaje; kraj po przecinku — nie wolno.
    not_found = status_fn_for(lb.GEOCODE_NOT_FOUND)
    guest_handler_run("?12 Wiązowna 05-462 proszę", not_found)
    assert not_found.calls == ["Wiązowna 05-462 proszę", "Wiązowna 05-462"]
    with_country = status_fn_for(lb.GEOCODE_NOT_FOUND)
    guest_handler_run("?12 Hel, PL proszę", with_country)
    assert with_country.calls == ["Hel, PL proszę"], "bez skracania, które gubi kraj"
    assert gbh._shortening_keeps_context("Wiązowna 05-462 proszę") is True
    assert gbh._shortening_keeps_context("Hel, PL extra") is False
    assert gbh._shortening_keeps_context("Nowy Jork super") is True


def test_ok_without_coordinates_never_sends_an_empty_message():
    """Adapter nie powinien, ale gość nie ma prawa wysłać pustej wiadomości."""
    fn = status_fn_for(lb.GEOCODE_OK, lat=None, lon=None)
    _handled, sent, photos, payloads = guest_handler_run("?12 Hel", fn)
    assert photos == [] and payloads == []
    assert sent == [], "GEOCODE_OK nie ma własnego komunikatu"
    assert gbh._GEO_CACHE == {}, "wynik bez współrzędnych nie jest wynikiem OK"


def test_guest_without_status_adapter_keeps_the_legacy_path():
    """Stary get_coords_fn zostaje wyłącznie dla kompatybilności."""
    calls, sent, photos = [], [], []

    def legacy(city, lang="pl"):
        calls.append(city)
        return None, None, None

    handled = gbh.handle_guest_now(
        message={"text": "?12 Hel", "chat": {"id": 555, "type": "private"},
                 "from": {"language_code": "pl"}},
        bot_username="PogodaWorldBot",
        get_coords_fn=legacy,
        build_payload_fn=lambda *a, **kw: {},
        prepare_layout_fn=lambda *a, **kw: {},
        render_png_fn=lambda *a, **kw: "/tmp/karta.png",
        send_photo_fn=lambda *a, **kw: photos.append(a),
        send_reply_fn=lambda *a, **kw: sent.append(a[1]),
        get_city_fn=None,
        geocode_status_fn=None,
    )
    assert handled is True
    assert calls == ["Hel"], "bez adaptera statusowego zostaje pojedyncza próba"
    assert photos == [] and sent == [i18n.t_ui("pl", "search_fail")]


# ============================================================================
# 10. POPRZEDNIA POPRAWKA (short_label / display_location) NIE JEST COFNIĘTA
# ============================================================================

def test_accepted_result_still_splits_short_and_display_labels():
    """Etap 1 tylko wybiera kandydata — etykiety formatuje stary formatter."""
    FakeNominatim.responses.append([
        FakeResult({"city": "Wiązowna", "municipality": "Wiązowna", "county": "otwocki",
                    "state": "mazowieckie", "postcode": "05-462", "country": "Polska",
                    "country_code": "pl", "road": "Koscielna", "house_number": "41",
                    "suburb": "Osiedle Parkowe", "amenity": "Biblioteka publiczna"},
                   lat=52.15, lon=21.29,
                   display="Biblioteka publiczna, Koscielna 41, Osiedle Parkowe"),
    ])
    status, _lat, _lon, short_label, display = geo("Wiązowna")
    assert status == lb.GEOCODE_OK
    assert short_label == "Wiązowna"
    assert display == (
        "Wiązowna, gmina Wiązowna, powiat otwocki, województwo mazowieckie, "
        "05-462, Polska"
    )
    for forbidden in ("41", "Koscielna", "Osiedle", "Biblioteka", POISON):
        assert forbidden not in display
    # Kompatybilny adapter oddaje tę samą parę, którą konsumuje /miasto.
    FakeNominatim.responses.append([
        FakeResult({"city": "Wiązowna", "municipality": "Wiązowna", "county": "otwocki",
                    "state": "mazowieckie", "postcode": "05-462", "country": "Polska",
                    "country_code": "pl", "road": "Koscielna", "house_number": "41",
                    "suburb": "Osiedle Parkowe", "amenity": "Biblioteka publiczna"},
                   lat=52.15, lon=21.29,
                   display="Biblioteka publiczna, Koscielna 41, Osiedle Parkowe"),
    ])
    lat, lon, public_short, public_display, ok = lb._geocode_city_public_details(
        "Wiązowna", "pl"
    )
    assert (lat, lon, ok) == (52.15, 21.29, True)
    assert (public_short, public_display) == (short_label, display)


def test_geo_cache_of_reverse_labels_is_not_used_by_forward_validation():
    """Forward nie może czytać ani pisać cache'u reverse (osobne klucze)."""
    lb._GEO_DETAILS_CACHE[("fake",)] = (9e18, ("Wiązowna", "Wiązowna, Polska", lb.GEO_OK))
    FakeNominatim.responses.append([poland_place("Hel", municipality="Hel")])
    status, _lat, _lon, short_label, _display = geo("Hel")
    assert status == lb.GEOCODE_OK
    assert short_label == "Hel"
    assert list(lb._GEO_DETAILS_CACHE) == [("fake",)]
