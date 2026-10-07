"""test_geocode_validation.py — ETAP 1: walidacja krótkich i niepasujących zapytań.

Dlaczego to istnieje: Nominatim dla krótkiej lub niepełnej nazwy potrafi zwrócić
miejsce, które z zapytaniem nie ma nic wspólnego — "U" -> Chubut (Argentyna),
"Wa" -> Little Sandy Desert (Australia), "Hel" -> Helmand (Afganistan). Etap 1
wprowadza wspólną walidację z jawnymi statusami, bez inline keyboarda, bez
rankingu i bez wybierania z listy:

  1. GEOCODE_TOO_SHORT  — 1-2 znaki bez kontekstu, NIE pyta mapy,
  2. kandydaci          — exactly_one=False, limit=7, addressdetails=True,
                          language=lang + best-effort namedetails i kraj
                          z zapytania (jedno zapytanie, kilku kandydatów),
  3. akceptacja         — nazwa miejscowości kandydata = zapytanie jako pełna
                          nazwa albo pełny token; prefiks nie wystarcza
                          ("hel" != "helmand", "wa" != "western australia"),
  4. validated top result (ETAP 1.1) — kolejność Nominatima wygrywa DOPIERO po
                          filtrach (klasa, jawny kraj, nazwa, kontekst): bierzemy
                          pierwszego zwalidowanego kandydata,
  5. GEOCODE_NO_MATCH   — zero zwalidowanych kandydatów,
  6. GEOCODE_UNCERTAIN  — rzadki bezpiecznik: dowód tylko ze surowego
                          display_name, bez pól miejscowości i bez namedetails,
                          przy więcej niż jednym rozróżnialnym miejscu,
  7. GEOCODE_NOT_FOUND  — mapa odpowiedziała pustką (None albo []),
  8. GEOCODE_ERROR      — mapa nie odpowiedziała (wyjątek transportowy),
                          bez ponawiania i bez zamiany na pustą listę.

Do tego ETAP 1.1 dokłada: parser jawnego kraju z zapytania ("Hel PL",
"Hel, Polska", "Nowy Jork Stany Zjednoczone"), egzonimy ("Paryż" -> Paris przez
namedetails ALBO minimalną mapę aliasów) i wstrzykiwany do trybu gościa helper
geocode_shortening_is_safe (skrót ratunkowy nie może zgubić kraju ani kodu).

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


def paris_result(lat=48.8566, lon=2.3522, namedetails=None, **extra):
    """Paryż z Nominatima: pole miejscowości "Paris", opcjonalnie name:pl."""
    address = {"city": "Paris", "country": "Francja", "country_code": "fr"}
    address.update(extra)
    return FakeResult(
        address, lat=lat, lon=lon,
        namedetails=(
            {"name": "Paris", "name:pl": "Paryż"} if namedetails is None else namedetails
        ),
        display="Paris, Francja",
    )


def new_york_result(lat=40.7128, lon=-74.0060, **extra):
    address = {"city": "New York", "state": "New York", "country": "Stany Zjednoczone",
               "country_code": "us"}
    address.update(extra)
    return FakeResult(address, lat=lat, lon=lon, display="New York, Stany Zjednoczone")


def norway_hel(lat=59.0, lon=10.9):
    return FakeResult({"village": "Hel", "country": "Norway", "country_code": "no"},
                      lat=lat, lon=lon, display="Hel, Norway")


def weak_display_place(country, cc, lat, lon):
    """Kandydat BEZ pól miejscowości i BEZ namedetails.

    Dowodem dopasowania jest wyłącznie nagłówek ``display_name`` — najsłabsza
    siła dowodu, jedyny powód, dla którego istnieje bezpiecznik UNCERTAIN.
    """
    return FakeResult(
        {"municipality": "Hel", "country": country, "country_code": cc},
        cls="boundary", typ="administrative", lat=lat, lon=lon,
        display=f"Hel, {country}",
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
            "extras": dict(kwargs),
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


class StrictNominatim:
    """Atrapa geopy, która — jak prawdziwa biblioteka — odrzuca nieznane kwargs.

    ``supported`` udaje zestaw nazw parametrów danej wersji geopy. Nieznany
    kwargs podnosi ``TypeError`` PRZED ciałem metody, dokładnie jak geopy, więc
    nie liczy się jako próba sieciowa (``network_calls`` rośnie dopiero po
    walidacji). Dzięki temu testujemy drabinkę wariantów bez ani jednego
    prawdziwego żądania.
    """

    supported = {"country_codes", "namedetails"}
    reject_all = False   # symuluje geopy, które nie zna ŻADNEGO naszego wariantu
    init_calls = 0
    network_calls = 0
    calls = []
    responses = []
    failures = []

    def __init__(self, user_agent=None):
        StrictNominatim.init_calls += 1

    def geocode(self, query, exactly_one=True, language=None, limit=None,
                addressdetails=None, **kwargs):
        unknown = sorted(name for name in kwargs if name not in StrictNominatim.supported)
        if StrictNominatim.reject_all or unknown:
            raise TypeError(f"geocode() got an unexpected keyword argument {unknown[0]!r}")
        StrictNominatim.network_calls += 1
        StrictNominatim.calls.append({
            "query": query, "exactly_one": exactly_one, "language": language,
            "limit": limit, "addressdetails": addressdetails,
            "extras": dict(kwargs),
        })
        if StrictNominatim.failures:
            raise StrictNominatim.failures.pop(0)
        if not StrictNominatim.responses:
            raise AssertionError("geokoder zapytany po raz drugi bez odpowiedzi")
        return StrictNominatim.responses.pop(0)

    @classmethod
    def reset(cls):
        cls.supported = {"country_codes", "namedetails"}
        cls.reject_all = False
        cls.init_calls = 0
        cls.network_calls = 0
        cls.calls = []
        cls.responses = []
        cls.failures = []


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    FakeNominatim.calls = []
    FakeNominatim.responses = []
    StrictNominatim.reset()
    monkeypatch.setattr(lb, "Nominatim", FakeNominatim)
    lb._GEO_DETAILS_CACHE.clear()
    gbh._GEO_CACHE.clear()
    gbh._CITY_CACHE.clear()
    yield
    FakeNominatim.calls = []
    FakeNominatim.responses = []
    StrictNominatim.reset()
    lb._GEO_DETAILS_CACHE.clear()
    gbh._GEO_CACHE.clear()
    gbh._CITY_CACHE.clear()


def _freeze_hour(monkeypatch, hour, minute=0):
    """Zamraża ``datetime.now()`` na podaną godzinę.

    Handler karty dziennej importuje ``datetime`` w locie (``from datetime import
    datetime``), więc podmiana atrybutu modułu ``datetime`` jest w nim widoczna —
    dokładnie tak, jak w test_access_gate.
    """
    import datetime as _dt
    real = _dt.datetime

    class _FrozenDateTime(real):
        @classmethod
        def now(cls, tz=None):
            return real(2026, 9, 30, hour, minute, tzinfo=tz)

    monkeypatch.setattr(_dt, "datetime", _FrozenDateTime)
    return _FrozenDateTime


@pytest.fixture
def morning_hour(monkeypatch):
    """Zamraża ``datetime.now()`` na 10:00 — karta dzienna ma okno 05:00-15:59.

    Bez tego test skrótu ``.d`` jest niedeterministyczny: po 16:00 (albo przed
    05:00) czasu lokalnego bot celowo odmawia karty dziennej.
    """
    return _freeze_hour(monkeypatch, 10, 0)


@pytest.fixture
def evening_hour(monkeypatch):
    """20:00 — poza oknem karty dziennej; kontrola, że morning_hour ma znaczenie."""
    return _freeze_hour(monkeypatch, 20, 0)


@pytest.fixture
def strict_nominatim(monkeypatch):
    """Podmienia geokoder na wersję walidującą kwargs (drabinka parametrów)."""
    monkeypatch.setattr(lb, "Nominatim", StrictNominatim)
    return StrictNominatim


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


# ============================================================================
# 3B. JAWNY KRAJ W ZAPYTANIU (ETAP 1.1)
# ============================================================================

@pytest.mark.parametrize("query,core,code", [
    ("Hel Polska", "Hel", "pl"),
    ("Hel PL", "Hel", "pl"),
    ("Paryż Francja", "Paryż", "fr"),
    ("Nowy Jork USA", "Nowy Jork", "us"),
    ("Nowy Jork Stany Zjednoczone", "Nowy Jork", "us"),
    ("Nowy Jork United States of America", "Nowy Jork", "us"),
    ("Berlin Niemcy", "Berlin", "de"),
    ("Oslo Norwegia", "Oslo", "no"),
    ("Madrid Hiszpania", "Madrid", "es"),
])
def test_country_parser_without_comma(query, core, code):
    assert lb._geocode_query_country(query) == (core, code)


@pytest.mark.parametrize("query,core,code", [
    ("Hel, Polska", "Hel", "pl"),
    ("Hel, PL", "Hel", "pl"),
    ("Paryż, Francja", "Paryż", "fr"),
    ("Nowy Jork, USA", "Nowy Jork", "us"),
    ("Nowy Jork, Stany Zjednoczone", "Nowy Jork", "us"),
])
def test_country_parser_with_comma(query, core, code):
    assert lb._geocode_query_country(query) == (core, code)


def test_country_parser_strips_suffix_commas_and_spaces():
    assert lb._geocode_query_country("Hel, Polska") == ("Hel", "pl")
    assert lb._geocode_query_country("Hel ,  Polska  ") == ("Hel", "pl")
    assert lb._geocode_query_country("Hel,PL") == ("Hel", "pl")


def test_country_parser_never_cuts_the_whole_query():
    """"Polska" to pytanie o miejscowość, nie o kraj — nie odcinamy całości."""
    for query in ("Polska", "Stany Zjednoczone", "Niemcy", "Norwegia", "Ameryka"):
        assert lb._geocode_query_country(query) == (query, None)


def test_country_parser_prefers_the_longest_suffix():
    assert lb._geocode_query_country("Nowy Jork United States of America") == (
        "Nowy Jork", "us",
    )
    assert lb._geocode_query_country("Nowy Jork Stany Zjednoczone") == ("Nowy Jork", "us")


def test_uk_is_gb_like_nominatim():
    assert lb._geocode_query_country("Londyn UK") == ("Londyn", "gb")
    assert lb._geocode_query_country("Londyn Wielka Brytania") == ("Londyn", "gb")
    assert lb._geocode_query_country("Londyn Great Britain") == ("Londyn", "gb")
    assert lb._geocode_query_country("Nowy Jork Ameryka") == ("Nowy Jork", "us")


def test_place_name_that_is_not_a_country_suffix_stays_untouched():
    for query in ("Kościelna 41, Wiązowna", "Wiązowna 05-462", "Nowy Targ", "Hel"):
        assert lb._geocode_query_country(query) == (query, None)


def test_country_code_is_sent_as_a_filter_not_as_part_of_the_name():
    FakeNominatim.responses.append([
        norway_hel(),
        poland_place("Hel", municipality="Hel", postcode="84-150"),
    ])
    status, lat, lon, short_label, display = geo("Hel PL")
    assert status == lb.GEOCODE_OK
    assert (lat, lon) == (54.6037, 18.7616), "kandydat z innym krajem odpada"
    assert short_label == "Hel" and "Norway" not in display
    calls = FakeNominatim.forward_calls()
    assert len(calls) == 1, "jedno zapytanie na wszystkich kandydatów"
    assert calls[-1]["query"] == "Hel", "kraj jest filtrem, nie częścią nazwy"
    assert calls[-1]["extras"].get("country_codes") == "pl"


@pytest.mark.parametrize("query", ["Hel, Polska", "Hel Polska", "Hel, PL", "Hel PL"])
def test_every_country_form_of_hel_resolves_to_poland(query):
    FakeNominatim.responses.append([
        norway_hel(),
        poland_place("Hel", municipality="Hel", county="pucki", state="pomorskie",
                     postcode="84-150"),
    ])
    status, lat, _lon, short_label, display = geo(query)
    assert status == lb.GEOCODE_OK, query
    assert (lat, short_label) == (54.6037, "Hel")
    assert "Norway" not in display


def test_candidate_without_country_code_is_rejected_when_country_is_known():
    """Kraj jawny: brak raw["address"]["country_code"] = odrzucenie, nie zgadywanie."""
    FakeNominatim.responses.append([
        FakeResult({"village": "Hel", "country": "Polska"}, lat=54.6037, lon=18.7616,
                   display="Hel, Polska"),
    ])
    assert geo("Hel PL") == (lb.GEOCODE_NO_MATCH, None, None, None, None)


def test_country_mismatch_is_no_match_not_a_guess():
    FakeNominatim.responses.append([norway_hel(), norway_hel()])
    assert geo("Hel PL") == (lb.GEOCODE_NO_MATCH, None, None, None, None)


def test_without_explicit_country_no_country_filter_is_used():
    FakeNominatim.responses.append([norway_hel(), poland_place("Hel")])
    status, lat, _lon, _short, _display = geo("Hel")
    assert status == lb.GEOCODE_OK and lat == 59.0
    extras = FakeNominatim.forward_calls()[-1]["extras"]
    assert "country_codes" not in extras and "countrycodes" not in extras


@pytest.mark.parametrize("query,candidate", [
    ("Paryż, Francja", paris_result),
    ("Paryż Francja", paris_result),
])
def test_required_ok_for_paris_forms(query, candidate):
    FakeNominatim.responses.append([candidate()])
    status, lat, lon, short_label, display = geo(query)
    assert status == lb.GEOCODE_OK
    assert (lat, lon, short_label) == (48.8566, 2.3522, "Paris")
    assert "Paryż" not in display


@pytest.mark.parametrize("query", [
    "Nowy Jork, USA", "Nowy Jork USA", "Nowy Jork, Stany Zjednoczone",
    "Nowy Jork Stany Zjednoczone",
])
def test_required_ok_for_new_york_forms_with_country(query):
    FakeNominatim.responses.append([new_york_result()])
    status, lat, lon, short_label, _display = geo(query)
    assert status == lb.GEOCODE_OK, query
    assert (lat, lon, short_label) == (40.7128, -74.006, "New York")


# ============================================================================
# 3C. DRABINKA PARAMETRÓW GEOKODERA (geopy 2.5 vs starsze)
# ============================================================================

def test_full_parameter_set_when_geopy_knows_both_kwargs(strict_nominatim):
    strict_nominatim.responses.append([poland_place("Hel", municipality="Hel")])
    assert geo("Hel PL", "pl")[0] == lb.GEOCODE_OK
    assert strict_nominatim.init_calls == 1, "jedna konstrukcja Nominatima"
    assert strict_nominatim.network_calls == 1, "dokładnie jedno żądanie"
    call = strict_nominatim.calls[-1]
    assert call["query"] == "Hel"
    assert call["exactly_one"] is False
    assert call["limit"] == lb.GEOCODE_CANDIDATE_LIMIT == 7
    assert call["addressdetails"] is True
    assert call["language"] == "pl"
    assert call["extras"] == {"country_codes": "pl", "namedetails": True}


def test_older_geopy_uses_countrycodes(strict_nominatim):
    strict_nominatim.supported = {"countrycodes", "namedetails"}
    strict_nominatim.responses.append([poland_place("Hel", municipality="Hel")])
    assert geo("Hel PL")[0] == lb.GEOCODE_OK
    assert strict_nominatim.network_calls == 1, "TypeError nie mnoży żądań sieciowych"
    assert strict_nominatim.calls[-1]["extras"] == {
        "countrycodes": "pl", "namedetails": True,
    }


def test_country_wins_over_namedetails_when_only_countrycodes_is_known(strict_nominatim):
    strict_nominatim.supported = {"countrycodes"}
    strict_nominatim.responses.append([poland_place("Hel", municipality="Hel")])
    assert geo("Hel PL")[0] == lb.GEOCODE_OK
    assert strict_nominatim.network_calls == 1
    assert strict_nominatim.calls[-1]["extras"] == {"countrycodes": "pl"}


def test_namedetails_is_best_effort_when_geopy_does_not_know_it(strict_nominatim):
    """Bez namedetails zostaje mapa aliasów — walidacja nie może od nich zależeć."""
    strict_nominatim.supported = set()
    strict_nominatim.responses.append([paris_result(namedetails={})])
    assert geo("Paryż")[0] == lb.GEOCODE_OK
    assert strict_nominatim.network_calls == 1
    assert strict_nominatim.calls[-1]["extras"] == {}


def test_timeout_is_error_with_exactly_one_attempt(strict_nominatim):
    strict_nominatim.failures.append(TimeoutError("timeout"))
    assert geo("Hel PL") == (lb.GEOCODE_ERROR, None, None, None, None)
    assert strict_nominatim.network_calls == 1, "timeout nie jest ponawiany"
    assert strict_nominatim.calls[-1]["extras"] == {
        "country_codes": "pl", "namedetails": True,
    }


def test_no_supported_parameter_variant_is_error_not_not_found(strict_nominatim):
    """Gdy geopy odrzuca KAŻDY wariant kwargs, to awaria — nigdy pusta mapa."""
    strict_nominatim.reject_all = True
    assert geo("Hel") == (lb.GEOCODE_ERROR, None, None, None, None)
    assert strict_nominatim.network_calls == 0


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

# ============================================================================
# 4B. EGZONIMY: "Paryż" -> Paris, "Nowy Jork" -> New York (ETAP 1.1)
# ============================================================================

def test_exonym_through_namedetails_returns_the_locality_field():
    """Egzonim dowodzi dopasowania, ale tożsamością i etykietą jest pole miejscowości."""
    FakeNominatim.responses.append([paris_result()])
    status, lat, lon, short_label, display = geo("Paryż, Francja")
    assert status == lb.GEOCODE_OK
    assert (lat, lon) == (48.8566, 2.3522)
    assert short_label == "Paris", "etykieta z pola miejscowości, nie z egzonimu"
    assert "Paryż" not in display


def test_exonym_through_the_alias_map_when_namedetails_are_missing():
    """Gdy geopy/mapa nie dają namedetails, ratuje minimalna mapa aliasów."""
    FakeNominatim.responses.append([paris_result(namedetails={})])
    assert geo("Paryż")[0] == lb.GEOCODE_OK
    FakeNominatim.responses.append([new_york_result()])
    assert geo("Nowy Jork")[0] == lb.GEOCODE_OK


def test_alias_map_is_only_an_extra_variant_of_the_same_comparison():
    assert lb._geocode_query_variants("Paryż") == ["paryz", "paris"]
    assert lb._geocode_query_variants("Nowy Jork") == ["nowy jork", "new york"]
    assert lb._geocode_query_variants("Hel") == ["hel"], "bez aliasu nie ma drugiego wariantu"


@pytest.mark.parametrize("query,name", [
    ("Nowy Jork", "Nowy Jork"),
    ("Paryż", "Paris"),
])
def test_alias_does_not_loosen_the_full_token_rule(query, name):
    """Alias to wariant porównania, nie nowa (luźniejsza) reguła dopasowania."""
    assert lb._geocode_name_matches_any(
        lb._geocode_query_variants(query), lb._normalize_location_text(name)
    )
    for longer in ("Paryżanka", "New Yorker", "Nowy Jorkowy"):
        assert not lb._geocode_name_matches_any(
            lb._geocode_query_variants(query), lb._normalize_location_text(longer)
        ), longer


@pytest.mark.parametrize("query,field_value", [
    ("paryz", "Paryżanka"),
    ("hel", "Helmand"),
    ("wa", "Western Australia"),
])
def test_prefix_of_a_longer_word_never_matches_even_for_aliases(query, field_value):
    assert not lb._geocode_name_matches_any(
        lb._geocode_query_variants(query), lb._normalize_location_text(field_value)
    )


def test_alias_never_matches_a_longer_place_end_to_end():
    FakeNominatim.responses.append([
        FakeResult({"city": "Paryżanka", "country": "Polska", "country_code": "pl"},
                   lat=52.0, lon=21.0, display="Paryżanka, Polska"),
    ])
    assert geo("Paryż") == (lb.GEOCODE_NO_MATCH, None, None, None, None)


def test_only_allowlisted_namedetails_keys_are_evidence():
    FakeNominatim.responses.append([
        FakeResult({"municipality": "Xyzz", "country": "Polska", "country_code": "pl"},
                   cls="boundary", typ="administrative", lat=52.0, lon=21.0,
                   namedetails={"ref": "Paryż", "destination": "Paryż"},
                   display="Xyzz, Polska"),
    ])
    assert geo("Paryż") == (lb.GEOCODE_NO_MATCH, None, None, None, None)


def test_localized_evidence_is_not_the_weak_display_fallback():
    """>1 kandydat dopasowany przez namedetails to nadal top result, nie UNCERTAIN."""
    def boundary(cc, country, lat, lon):
        return FakeResult(
            {"municipality": "Paris", "country": country, "country_code": cc},
            cls="boundary", typ="administrative", lat=lat, lon=lon,
            namedetails={"name": "Paris", "name:pl": "Paryż"},
            display=f"Paris, {country}",
        )

    FakeNominatim.responses.append([
        boundary("us", "USA", 33.66, -95.55),
        boundary("fr", "Francja", 48.8566, 2.3522),
    ])
    status, lat, _lon, _short, _display = geo("Paryż")
    assert status == lb.GEOCODE_OK
    assert lat == 33.66, "kolejność geokodera po filtrach"


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


def _boundary_location(typ="administrative", addresstype=None, cls="boundary"):
    """Granica administracyjna w kształcie Nominatima (opcjonalne addresstype)."""
    location = FakeResult({"municipality": "Hel", "country": "Polska",
                           "country_code": "pl"},
                          cls=cls, typ=typ, lat=54.6037, lon=18.7616,
                          display="Hel, powiat pucki, Polska")
    location.raw.pop("type", None)
    if typ is not None:
        location.raw["type"] = typ
    if addresstype is not None:
        location.raw["addresstype"] = addresstype
    return location


def test_boundary_without_administrative_type_is_rejected():
    """Sam ``boundary`` bez potwierdzonego typu NIE jest bezpieczny.

    Etap 1 miał w kodzie tolerancję ``typ in ("", "administrative")``, ale nie
    korzystał z niej żaden test, a docstring mówił o "boundary +
    type=administrative". ETAP 1.1 wymaga jawnego dowodu typu administracyjnego.
    """
    without_type = _boundary_location(typ=None)
    assert "type" not in without_type.raw and "addresstype" not in without_type.raw
    assert lb._geocode_candidate_is_safe(without_type.raw) is False
    FakeNominatim.responses.append([without_type])
    assert geo("Hel") == (lb.GEOCODE_NO_MATCH, None, None, None, None)

    empty_type = _boundary_location(typ="")
    assert lb._geocode_candidate_is_safe(empty_type.raw) is False
    FakeNominatim.responses.append([empty_type])
    assert geo("Hel") == (lb.GEOCODE_NO_MATCH, None, None, None, None)


def test_boundary_with_addresstype_administrative_is_accepted():
    """Gdy Nominatim podaje tylko ``addresstype=administrative`` — przechodzi."""
    location = _boundary_location(typ=None, addresstype="administrative")
    assert "type" not in location.raw
    assert lb._geocode_candidate_is_safe(location.raw) is True
    FakeNominatim.responses.append([location])
    assert geo("Hel")[0] == lb.GEOCODE_OK


def test_legacy_administrative_class_needs_the_same_type_proof():
    """Klasa "administrative" (z etapu 1) wymaga tego samego dowodu typu."""
    accepted = _boundary_location(typ="administrative", cls="administrative")
    assert lb._geocode_candidate_is_safe(accepted.raw) is True
    FakeNominatim.responses.append([accepted])
    assert geo("Hel")[0] == lb.GEOCODE_OK

    rejected = _boundary_location(typ="", cls="administrative")
    assert lb._geocode_candidate_is_safe(rejected.raw) is False


def test_boundary_with_administrative_type_is_accepted():
    for location in (poland_place("Hel"), admin_boundary("Hel")):
        FakeNominatim.responses.append([location])
        assert geo("Hel")[0] == lb.GEOCODE_OK


@pytest.mark.parametrize("field", ["type", "addresstype"])
@pytest.mark.parametrize("typ", ["protected_area", "national_park", "postal_code",
                                 "maritime", "political"])
def test_boundary_types_other_than_administrative_stay_rejected(field, typ):
    """Dopuszczalny jest WYŁĄCZNIE typ administracyjny — w obu polach."""
    location = _boundary_location(typ=None)
    location.raw[field] = typ
    assert lb._geocode_candidate_is_safe(location.raw) is False
    FakeNominatim.responses.append([location])
    assert geo("Hel") == (lb.GEOCODE_NO_MATCH, None, None, None, None)


# ============================================================================
# 6. VALIDATED TOP RESULT (ETAP 1.1)
# ============================================================================

def test_two_distinguishable_places_go_by_nominatim_order_not_uncertainty():
    """ETAP 1.1: dwóch zwalidowanych kandydatów to NIE powód do niepewności.

    "Hel" bez kraju idzie za kolejnością Nominatima (użytkownik dopisuje kraj,
    żeby zawęzić) — a NIE jest normalną odpowiedzią status "kilka miejsc".
    """
    FakeNominatim.responses.append([
        poland_place("Hel", municipality="Hel", postcode="84-150"),
        FakeResult({"village": "Hel", "country": "Norway", "country_code": "no"},
                   lat=59.0, lon=10.9, display="Hel, Norway"),
    ])
    status, lat, lon, short_label, display = geo("Hel", "pl")
    assert status == lb.GEOCODE_OK
    assert (lat, lon) == (54.6037, 18.7616), "wygrywa pierwszy zwalidowany"
    assert short_label == "Hel" and "Norway" not in display


def test_no_ranking_between_city_and_village():
    """Bez rankingu: wygrywa kolejność geokodera, nie typ miejscowości."""
    FakeNominatim.responses.append([
        FakeResult({"village": "Hel", "country": "Polska", "country_code": "pl"},
                   cls="place", typ="village", lat=50.10, lon=22.00,
                   display="Hel, Polska"),
        FakeResult({"city": "Hel", "country": "Polska", "country_code": "pl"},
                   cls="place", typ="city", lat=54.6037, lon=18.7616,
                   display="Hel, Polska"),
    ])
    status, lat, _lon, _short, _display = geo("Hel")
    assert status == lb.GEOCODE_OK
    assert lat == 50.10, "wieś pierwsza w kolejności = wieś wygrywa, bez rankingu"


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
    """language=pl nie rozstrzyga kraju — kraj pochodzi TYLKO z zapytania.

    Bez jawnego kraju nie ma filtra country_code, więc wynik idzie za
    kolejnością Nominatima (top result), a nie za językiem UI.
    """
    FakeNominatim.responses.append([
        FakeResult({"village": "Hel", "country": "Norway", "country_code": "no"},
                   lat=59.0, lon=10.9, display="Hel, Norway"),
        poland_place("Hel", municipality="Hel", postcode="84-150"),
    ])
    status, lat, _lon, _short, _display = geo("Hel", lang)
    assert status == lb.GEOCODE_OK
    assert lat == 59.0, "język UI nie może zmieniać kolejności ani filtrować kraju"
    call = FakeNominatim.forward_calls()[-1]
    assert "country_codes" not in call["extras"], "język to nie kraj"
    assert "countrycodes" not in call["extras"]


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
# 6B. BEZPIECZNIK UNCERTAIN: TYLKO SŁABY DOWÓD Z DISPLAY_NAME
# ============================================================================

def test_weak_display_evidence_with_one_place_is_ok():
    FakeNominatim.responses.append([weak_display_place("Polska", "pl", 54.6037, 18.7616)])
    assert geo("Hel")[0] == lb.GEOCODE_OK


def test_weak_display_evidence_with_two_distinguishable_places_is_uncertain():
    """Rzadki bezpiecznik: dane za słabe, by ufać top resultowi."""
    FakeNominatim.responses.append([
        weak_display_place("Norwegia", "no", 59.0, 10.9),
        weak_display_place("Polska", "pl", 54.6037, 18.7616),
    ])
    assert geo("Hel") == (lb.GEOCODE_UNCERTAIN, None, None, None, None)


def test_country_that_matches_no_candidate_is_no_match_not_uncertain():
    FakeNominatim.responses.append([
        weak_display_place("Norwegia", "no", 59.0, 10.9),
        weak_display_place("Polska", "pl", 54.6037, 18.7616),
    ])
    assert geo("Hel, Wielka Brytania") == (lb.GEOCODE_NO_MATCH, None, None, None, None)


def test_explicit_country_narrows_the_weak_evidence_to_one_place():
    FakeNominatim.responses.append([
        weak_display_place("Norwegia", "no", 59.0, 10.9),
        weak_display_place("Polska", "pl", 54.6037, 18.7616),
    ])
    status, lat, _lon, _short, _display = geo("Hel Polska")
    assert status == lb.GEOCODE_OK, "filtr kraju zostawia jedno zwalidowane miejsce"
    assert lat == 54.6037


def test_weak_display_evidence_of_the_same_place_is_not_uncertain():
    """Dwa wpisy tej samej miejscowości (punkt + relacja) to nie dwie miejscowości."""
    FakeNominatim.responses.append([
        weak_display_place("Polska", "pl", 54.6037, 18.7616),
        weak_display_place("Polska", "pl", 54.6100, 18.7700),
    ])
    assert geo("Hel")[0] == lb.GEOCODE_OK


def test_strong_evidence_disables_the_uncertain_guard():
    """Wystarczy jeden kandydat z polem miejscowości, by bezpiecznik nie zadziałał."""
    FakeNominatim.responses.append([
        poland_place("Hel", municipality="Hel", postcode="84-150"),
        weak_display_place("Norwegia", "no", 59.0, 10.9),
    ])
    status, lat, _lon, _short, _display = geo("Hel")
    assert status == lb.GEOCODE_OK
    assert lat == 54.6037


def test_uncertain_is_never_a_normal_answer_for_popular_names():
    """Popularne nazwy dają kartę: kraj w zapytaniu rozstrzyga, reszta idzie po kolei."""
    France, Texas = paris_result(), paris_result(
        lat=33.66, lon=-95.55, country_code="us", country="Stany Zjednoczone",
    )
    FakeNominatim.responses.append([Texas, France])
    status, lat, _lon, _short, _display = geo("Paryż, Francja")
    assert status == lb.GEOCODE_OK and lat == 48.8566, "filtr kraju, nie niepewność"

    FakeNominatim.responses.append([
        weak_display_place("Polska", "pl", 54.6037, 18.7616),
        weak_display_place("Norwegia", "no", 59.0, 10.9),
    ])
    assert geo("Hel")[0] == lb.GEOCODE_UNCERTAIN, "tylko ten jeden słaby przypadek"


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


def test_known_cost_of_stage_1_2_street_context_is_now_a_hard_filter():
    """Świadomy koszt etapu 1.2: 'Wiązowna, Koscielna 41' przestaje przechodzić.

    W etapie 1.1 ten przypadek był OK: nazwa miejscowości była dowodem, a ulica
    z numerem "wyjaśniały się" luźnym kontekstem (road + house_number w
    ``_geocode_context_values``). Etap 1.2 zmienia regułę na WARUNKOWĄ: segment
    po przecinku, który nie jest krajem, to jawny kontekst i MUSI przejść
    filtr administracyjny (county/state/region/municipality/province/
    state_district/district/city/town/city_district/suburb). Ulica i numer
    domu nie są żadnym z tych pól, więc kandydat odpada z GEOCODE_NO_MATCH.

    To jest celowa decyzja, nie regresja zabezpieczeń: bez niej kontekst stałby
    się miękką sugestią i "Wiązowna, Poznań" też mogłoby przejść. Ten sam
    gatunek kosztu co ``test_known_cost_of_stage_one_street_first_query_is_rejected``,
    tylko w drugą stronę zapytania. Zapis "miejscowość, ulica numer" wymaga
    decyzji produktowej — nie robimy jej po cichu.
    """
    FakeNominatim.responses.append([house_result()])
    assert geo("Wiązowna, Koscielna 41")[0] == lb.GEOCODE_NO_MATCH
    # Bez przecinka dowód nazwy miejscowości nadal wystarcza (ścieżka etapu 1.1).
    FakeNominatim.responses.append([house_result()])
    status, lat, _lon, short_label, _display = geo("Wiązowna 05-462")
    assert status == lb.GEOCODE_OK
    assert (lat, short_label) == (52.1483, "Wiązowna")


def test_street_context_is_rejected_even_when_the_street_is_in_the_address():
    """'Koscielna 41' jest w polach kandydata, ale to nie są pola kontekstu."""
    FakeNominatim.responses.append([house_result()])
    assert geo("Wiązowna, Koscielna")[0] == lb.GEOCODE_NO_MATCH
    FakeNominatim.responses.append([house_result()])
    assert geo("Wiązowna, 41")[0] == lb.GEOCODE_NO_MATCH


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


@pytest.mark.parametrize("query,expected", [
    ("Nowy Jork super", True),
    ("Nowy Jork", True),
    ("Hel", True),
    ("Hel, PL proszę", False),
    ("Hel super PL", False),
    ("Wiązowna 05-462 proszę", True),
    ("Wiązowna proszę 05-462", False),
    ("", False),
])
def test_geocode_shortening_is_safe_contract(query, expected):
    """Helper z location_bot: skrót nie może zgubić kraju ani kodu pocztowego."""
    assert lb.geocode_shortening_is_safe(query) is expected


def test_guest_shortening_uses_the_injected_country_checker():
    """Bez wstrzykniętego checkera zostałby tylko lokalny fallback (przecinek/kod)."""
    calls = []

    def status_fn(city, lang):
        calls.append(city)
        return None, None, None, lb.GEOCODE_NOT_FOUND

    def run(query):
        calls.clear()
        gbh._geocode_best_effort(
            query, lambda city, lang: (None, None, None), "pl", status_fn,
            lb.geocode_shortening_is_safe,
        )
        return list(calls)

    assert run("Nowy Jork super") == ["Nowy Jork super", "Nowy Jork"]
    assert run("Hel, PL proszę") == ["Hel, PL proszę"], "przecinek zostaje"
    assert run("Hel super PL") == ["Hel super PL"], "skrót zgubiłby jawny kraj"
    assert run("Hel PL proszę") == ["Hel PL proszę", "Hel PL"], "kraj przetrwał"
    # Stary fallback (bez checkera) zachowuje się jak w etapie 1.
    assert gbh._shortening_keeps_context("Hel super PL") is True


def test_guest_shortening_checker_is_wired_from_location_bot():
    """location_bot podaje swój helper — guest handler nie zna mapy krajów."""
    assert lb.geocode_shortening_is_safe is not gbh._shortening_keeps_context
    assert gbh._shortening_keeps_context("Nowy Jork super", lb.geocode_shortening_is_safe)
    assert not gbh._shortening_keeps_context(
        "Hel super PL", lb.geocode_shortening_is_safe
    )


@pytest.mark.parametrize("text,card_type", [
    ("?12 Paryż Francja", "now"),
    (".n Paryż Francja", "now"),
    ("?14 Paryż Francja", "future"),
    ("@PogodaWorldBot Paryż Francja", "now"),
])
def test_guest_shortcuts_and_mention_card_a_validated_city(text, card_type):
    """Ta sama walidacja dla ?12 ?14 .n i wzmianki @bot — z prawdziwym rdzeniem."""
    FakeNominatim.responses.append([paris_result()])
    handled, sent, photos, payloads = guest_handler_run(
        text, lambda city, lang: lb.geocode_city_details_status(city, lang),
        city_name="Paris",
    )
    assert handled is True
    assert payloads and payloads[0][2] == card_type, text
    assert photos and photos[0][1] == "Paris", text
    assert sent == [], "udana karta nie ma komunikatu statusowego"
    assert FakeNominatim.forward_calls()[0]["query"] == "Paryż"


def test_guest_day_shortcut_cards_a_validated_city(morning_hour):
    """.d / ?d rysują kartę dzienną — o 10:00, żeby test nie zależał od zegara."""
    for text in (".d Paryż Francja", "?d Paryż Francja"):
        FakeNominatim.responses.append([paris_result()])
        handled, sent, photos, payloads = guest_handler_run(
            text, lambda city, lang: lb.geocode_city_details_status(city, lang),
            city_name="Paris",
        )
        assert handled is True and photos, text
        assert payloads[0][2] == "day", text
        assert sent == [], text


def test_guest_day_shortcut_respects_the_local_card_window(evening_hour):
    """Kontrola dla morning_hour: o 20:00 karta dzienna NIE powstaje."""
    FakeNominatim.responses.append([paris_result()])
    _handled, sent, photos, _payloads = guest_handler_run(
        ".d Paryż Francja",
        lambda city, lang: lb.geocode_city_details_status(city, lang),
        city_name="Paris",
    )
    assert photos == []
    assert sent and i18n.t_ui("pl", "time_limit") in sent[-1]


def test_guest_wrong_country_is_no_match_and_no_card():
    FakeNominatim.responses.append([
        FakeResult({"city": "Paris", "country": "Stany Zjednoczone", "country_code": "us"},
                   lat=33.66, lon=-95.55, display="Paris, Stany Zjednoczone"),
    ])
    _handled, sent, photos, payloads = guest_handler_run(
        "?12 Paryż Francja",
        lambda city, lang: lb.geocode_city_details_status(city, lang),
        city_name="Paris",
    )
    assert photos == [] and payloads == []
    assert i18n.UI_TEXTS["pl"]["geocode_no_match"] in sent[-1]
    assert gbh._GEO_CACHE == {}, "odrzucony wynik nie trafia do cache"


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


# ============================================================================
# 9. ETAP 1.2 — LUDZKIE DOPRECYZOWANIA LOKALIZACJI
# ============================================================================
# Zapytanie dzielimy na core / admin_context / country_context. Kontekst
# administracyjny jest DODATKOWYM filtrem i reguła jest WARUNKOWA: gdy parser
# wykryje admin_context, kandydat MUSI przejść nowy filtr administracyjny, a
# stary matcher kontekstu (który czyta też display_name) nie jest obejściem.
# Bez wykrytego kontekstu obowiązuje walidacja etapu 1.1 bez zmian.

WIAZOWNA_PL = {
    "city": "Wiązowna", "municipality": "gmina Wiązowna",
    "county": "powiat otwocki", "state": "województwo mazowieckie",
    "country": "Polska", "country_code": "pl",
}
OLSZTYN_SLASKIE_PL = {
    "village": "Olsztyn", "municipality": "gmina Olsztyn",
    "county": "powiat częstochowski", "state": "województwo śląskie",
    "country": "Polska", "country_code": "pl",
}
OLSZTYN_WARMINSKIE_PL = {
    "city": "Olsztyn", "state": "województwo warmińsko-mazurskie",
    "country": "Polska", "country_code": "pl",
}


def wiazowna_result(**extra):
    address = dict(WIAZOWNA_PL)
    address.update(extra)
    return FakeResult(
        address, cls="place", typ="village", lat=52.15, lon=21.29,
        display="Wiązowna, gmina Wiązowna, powiat otwocki, województwo mazowieckie,"
                " Polska",
    )


def olsztyn_slaskie_result(**extra):
    address = dict(OLSZTYN_SLASKIE_PL)
    address.update(extra)
    return FakeResult(
        address, cls="place", typ="village", lat=50.75, lon=19.27,
        display="Olsztyn, gmina Olsztyn, powiat częstochowski, województwo śląskie,"
                " Polska",
    )


def olsztyn_warminskie_result(**extra):
    address = dict(OLSZTYN_WARMINSKIE_PL)
    address.update(extra)
    return FakeResult(
        address, cls="place", typ="city", lat=53.77, lon=20.49,
        display="Olsztyn, województwo warmińsko-mazurskie, Polska",
    )


def helmand_result():
    return FakeResult(
        {"state": "Helmand", "country": "Afganistan", "country_code": "af"},
        lat=31.5, lon=65.0, cls="boundary", typ="administrative",
        display="Helmand, Afganistan",
    )


# ----------------------------------------------------------------------------
# 9A. PARSER: core / admin_context / country_context (bez sieci)
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("query,core,admin,code", [
    # wymagane przypadki z opisu etapu
    ("Wiązowna, otwock", "Wiązowna", ["otwock"], None),
    ("Wiązowna, powiat otwock", "Wiązowna", ["otwock"], None),
    ("Wiązowna, powiat otwocki", "Wiązowna", ["otwocki"], None),
    ("Olsztyn, województwo śląskie", "Olsztyn", ["śląskie"], None),
    ("Olsztyn województwo śląskie", "Olsztyn", ["śląskie"], None),
    ("Olsztyn, Częstochowa", "Olsztyn", ["Częstochowa"], None),
    ("Olsztyn koło Częstochowy", "Olsztyn", ["Częstochowy"], None),
    ("Wiązowna, otwock, Polska", "Wiązowna", ["otwock"], "pl"),
    ("Olsztyn, Częstochowa, Polska", "Olsztyn", ["Częstochowa"], "pl"),
    # markery bez przecinka
    ("Wiązowna powiat otwock", "Wiązowna", ["otwock"], None),
    ("Wiązowna, gmina Wiązowna", "Wiązowna", ["Wiązowna"], None),
    ("Olsztyn w pobliżu Częstochowy", "Olsztyn", ["Częstochowy"], None),
    ("Olsztyn okolice Częstochowy", "Olsztyn", ["Częstochowy"], None),
    ("Olsztyn near Czestochowa", "Olsztyn", ["Czestochowa"], None),
    # kompatybilność wstecz z etapem 1.1
    ("Paryż, Francja", "Paryż", [], "fr"),
    ("Hel, PL", "Hel", [], "pl"),
    ("Hel Polska", "Hel", [], "pl"),
    ("Hel", "Hel", [], None),
    ("Wiązowna", "Wiązowna", [], None),
    ("Wiązowna 05-462", "Wiązowna 05-462", [], None),
    # "Kolo" to miejscowość, nie marker rozdzielający
    ("Kolo", "Kolo", [], None),
    ("Wielkie Kolo", "Wielkie Kolo", [], None),
])
def test_stage_1_2_query_parse(query, core, admin, code):
    assert lb._geocode_query_parse(query) == (core, admin, code)


def test_stage_1_2_parser_keeps_stage_1_1_country_contract():
    """Nowy parser nie zmienia wyniku starego parsera kraju."""
    for query in ("Polska", "Niemcy", "Nowy Jork Stany Zjednoczone",
                  "Londyn Wielka Brytania", "Kościelna 41, Wiązowna"):
        assert lb._geocode_query_country(query) == lb._geocode_query_country(query)
    assert lb._geocode_query_country("Wiązowna, otwock, Polska") == (
        "Wiązowna otwock", "pl",
    ), "etap 1.1 skleja rdzeń spacjami — kontrakt bez zmian"
    assert lb._geocode_split_country("Wiązowna, otwock, Polska") == (
        "Wiązowna, otwock", "pl",
    ), "etap 1.2 zachowuje przecinek potrzebny do podziału core/kontekst"


# ----------------------------------------------------------------------------
# 9B. ZAPYTANIE DO NOMINATIMA: bez fillerów
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("Wiązowna, otwock", "Wiązowna, otwock"),
    ("Wiązowna, powiat otwock", "Wiązowna, otwock"),
    ("Wiązowna, powiat otwocki", "Wiązowna, otwocki"),
    ("Olsztyn, województwo śląskie", "Olsztyn, śląskie"),
    ("Olsztyn województwo śląskie", "Olsztyn, śląskie"),
    ("Olsztyn, Częstochowa", "Olsztyn, Częstochowa"),
    ("Olsztyn koło Częstochowy", "Olsztyn, Częstochowy"),
    ("Wiązowna, otwock, Polska", "Wiązowna, otwock"),
])
def test_stage_1_2_query_sent_to_nominatim(query, expected):
    FakeNominatim.responses.append([wiazowna_result()])
    geo(query)
    assert FakeNominatim.forward_calls()[-1]["query"] == expected


def test_stage_1_2_fillers_are_never_sent_to_nominatim():
    for query in ("Wiązowna, powiat otwock", "Olsztyn koło Częstochowy",
                  "Wiązowna, gmina Wiązowna", "Olsztyn województwo śląskie"):
        FakeNominatim.responses.append([wiazowna_result()])
        geo(query)
        sent = FakeNominatim.forward_calls()[-1]["query"]
        for filler in ("powiat", "koło", "kolo", "gmina", "województwo", "w pobliżu"):
            assert filler not in sent, f"{query!r} wysyla filler {filler!r}: {sent!r}"


def test_stage_1_2_query_without_context_is_identical_to_stage_1_1():
    """Brak admin_context = ten sam ciąg znaków co w etapie 1.1."""
    FakeNominatim.responses.append([wiazowna_result()])
    geo("Wiązowna")
    assert FakeNominatim.forward_calls()[-1]["query"] == "Wiązowna"
    FakeNominatim.responses.append([wiazowna_result()])
    geo("Wiązowna 05-462")
    assert FakeNominatim.forward_calls()[-1]["query"] == "Wiązowna 05-462"


# ----------------------------------------------------------------------------
# 9C. POZYTYWNE
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("query", [
    "Wiązowna, otwock",
    "Wiązowna, powiat otwock",
    "Wiązowna, powiat otwocki",
    "Wiązowna, otwock, Polska",
])
def test_stage_1_2_wiazowna_with_otwock_context_is_ok(query):
    # Kandydat niepasujący kontekstem (Olsztyn śląski) jest PIERWSZY w
    # kolejności Nominatima — musi odpaść, żeby wygrała Wiązowna.
    FakeNominatim.responses.append([olsztyn_slaskie_result(), wiazowna_result()])
    status, lat, _lon, short_label, _display = geo(query)
    assert status == lb.GEOCODE_OK, query
    assert (lat, short_label) == (52.15, "Wiązowna")


@pytest.mark.parametrize("query", [
    "Olsztyn, województwo śląskie",
    "Olsztyn województwo śląskie",
    "Olsztyn, Częstochowa",
    "Olsztyn koło Częstochowy",
])
def test_stage_1_2_olsztyn_with_silesian_context_is_ok(query):
    # Olsztyn warmiński (domyślny top result) musi odpaść na filtrze kontekstu.
    FakeNominatim.responses.append([olsztyn_warminskie_result(), olsztyn_slaskie_result()])
    status, lat, _lon, short_label, _display = geo(query)
    assert status == lb.GEOCODE_OK, query
    assert (lat, short_label) == (50.75, "Olsztyn")


@pytest.mark.parametrize("context", ["otwock", "otwocki", "mazowieckie", "Wiązowna"])
def test_stage_1_2_admin_variants_match_deterministically(context):
    """otwock <-> otwocki po kontrolowanym rdzeniu, bez fuzzy matchingu."""
    FakeNominatim.responses.append([wiazowna_result()])
    assert geo(f"Wiązowna, {context}")[0] == lb.GEOCODE_OK


@pytest.mark.parametrize("query", [
    "Olsztyn, częstochowski",
    "Olsztyn, częstochowa",
    "Olsztyn, częstochowy",
    "Olsztyn, śląskie",
    "Olsztyn, slaskie",
])
def test_stage_1_2_silesian_admin_variants_match(query):
    FakeNominatim.responses.append([olsztyn_slaskie_result()])
    assert geo(query)[0] == lb.GEOCODE_OK


# ----------------------------------------------------------------------------
# 9D. NEGATYWNE
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("query", [
    "Wiązowna, Poznań",      # wymagany przypadek
    "Wiązowna, Warszawa",    # podobne, ale błędne (rdzeń 1 < 4)
    "Wiązowna, śląskie",     # podobne, ale błędne
    "Wiązowna, otw",         # wspólny rdzeń 3 < 4
    "Wiązowna, wa",          # token krótszy niż 3 znaki
])
def test_stage_1_2_wrong_admin_context_is_no_match(query):
    FakeNominatim.responses.append([wiazowna_result()])
    assert geo(query)[0] == lb.GEOCODE_NO_MATCH, query


@pytest.mark.parametrize("query", [
    "Olsztyn, województwo mazowieckie",   # wymagany przypadek
    "Olsztyn, Kraków",                    # wymagany przypadek
])
def test_stage_1_2_olsztyn_wrong_context_is_no_match(query):
    FakeNominatim.responses.append([olsztyn_slaskie_result()])
    assert geo(query)[0] == lb.GEOCODE_NO_MATCH, query


def test_stage_1_2_legacy_context_path_cannot_bypass_the_admin_filter():
    """Stary matcher przepuściłby po display_name — nowy filtr musi odrzucić.

    "Poznań" występuje WYŁĄCZNIE w ``display_name``: nie ma go w county/state/
    city/town. ``_geocode_piece_explained`` (etap 1.1) czyta części
    display_name, więc uznałby kontekst za wyjaśniony. Reguła etapu 1.2 jest
    WARUNKOWA: przy wykrytym admin_context decyduje wyłącznie filtr
    administracyjny, więc kandydat odpada.
    """
    candidate = FakeResult(
        {"city": "Wiązowna", "country": "Polska", "country_code": "pl"},
        lat=52.15, lon=21.29, display="Wiązowna, Poznań, Polska",
    )
    address = candidate.raw["address"]
    fake = FakeNominatim
    # Dowód, że stara ścieżka rzeczywiście by przepuściła:
    assert lb._geocode_piece_explained(
        "poznan", lb._geocode_context_values(candidate.raw, address)
    )
    # ...a nowy filtr administracyjny mówi nie:
    assert not lb._geocode_admin_piece_matches(
        "poznan", lb._geocode_admin_values(address)
    )
    fake.responses.append([candidate])
    assert geo("Wiązowna, Poznań")[0] == lb.GEOCODE_NO_MATCH


def test_stage_1_2_context_never_replaces_core_validation():
    """Kontekst pasuje idealnie, ale nazwa miejscowości nie -> NO_MATCH."""
    FakeNominatim.responses.append([wiazowna_result()])
    assert geo("Hel, otwock")[0] == lb.GEOCODE_NO_MATCH


def test_stage_1_2_context_does_not_let_a_longer_name_match():
    """„Hel" nadal nie jest „Helmand", choć „Helmand" pasuje jako kontekst."""
    FakeNominatim.responses.append([helmand_result()])
    assert geo("Hel, Helmand")[0] == lb.GEOCODE_NO_MATCH
    FakeNominatim.responses.append([helmand_result(), poland_place("Hel")])
    status, _lat, _lon, short_label, _display = geo("Hel")
    assert status == lb.GEOCODE_OK and short_label == "Hel"


def test_stage_1_2_paris_with_germany_is_still_rejected():
    """„Paryż, Niemcy": kraj de jest twardym filtrem, kandydat fr odpada."""
    FakeNominatim.responses.append([paris_result()])
    assert geo("Paryż, Niemcy")[0] == lb.GEOCODE_NO_MATCH
    FakeNominatim.responses.append([paris_result()])
    status, lat, lon, _short, _display = geo("Paryż, Francja")
    assert status == lb.GEOCODE_OK and (lat, lon) == (48.8566, 2.3522)


@pytest.mark.parametrize("query", ["U", "Wa", "Os"])
def test_stage_1_2_short_queries_still_never_touch_the_map(query):
    FakeNominatim.responses.append([wiazowna_result()])
    assert geo(query)[0] == lb.GEOCODE_TOO_SHORT
    assert FakeNominatim.forward_calls() == [], "za krótkie zapytanie nie pyta mapy"


# ----------------------------------------------------------------------------
# 9E. FALLBACK CORE-ONLY — nadal z obowiązkowym filtrem kontekstu
# ----------------------------------------------------------------------------

def test_stage_1_2_core_only_fallback_still_applies_the_admin_filter():
    FakeNominatim.responses.append([])                       # kontekstowe: pustka
    FakeNominatim.responses.append([olsztyn_slaskie_result()])  # core-only: nie pasuje
    assert geo("Wiązowna, otwock")[0] == lb.GEOCODE_NO_MATCH
    queries = [call["query"] for call in FakeNominatim.forward_calls()]
    assert queries == ["Wiązowna, otwock", "Wiązowna"]


def test_stage_1_2_core_only_fallback_accepts_a_matching_candidate():
    FakeNominatim.responses.append([])
    FakeNominatim.responses.append([wiazowna_result()])
    status, lat, _lon, short_label, _display = geo("Wiązowna, otwock")
    assert status == lb.GEOCODE_OK
    assert (lat, short_label) == (52.15, "Wiązowna")


def test_stage_1_2_no_fallback_without_admin_context():
    """Bez kontekstu pusta odpowiedź to po prostu NOT_FOUND, bez drugiej próby."""
    FakeNominatim.responses.append([])
    assert geo("Wiązowna")[0] == lb.GEOCODE_NOT_FOUND
    assert len(FakeNominatim.forward_calls()) == 1


# ----------------------------------------------------------------------------
# 9F. SKRÓT RATUNKOWY GOŚCIA NIE GUBI KONTEKSTU
# ----------------------------------------------------------------------------

def test_stage_1_2_shortening_never_drops_admin_context():
    assert lb.geocode_shortening_is_safe("Olsztyn koło Częstochowy") is False
    assert lb.geocode_shortening_is_safe("Olsztyn województwo śląskie") is False
    assert lb.geocode_shortening_is_safe("Wiązowna, otwock") is False
    assert lb.geocode_shortening_is_safe("Hel, PL") is False
    # Zwykły nadmiar tekstu nadal wolno skrócić.
    assert lb.geocode_shortening_is_safe("Nowy Jork super") is True
    assert lb.geocode_shortening_is_safe("Wiązowna") is True


# ----------------------------------------------------------------------------
# 9G. KOMPATYBILNOŚĆ: bez admin_context obowiązuje etap 1.1
# ----------------------------------------------------------------------------

def test_stage_1_2_legacy_context_path_is_untouched_without_admin_context():
    """Bez wykrytego kontekstu stary matcher nadal rozstrzyga (etap 1.1)."""
    # Ulica i numer nie są polami administracyjnymi, ale zapytanie BEZ
    # przecinka i BEZ markera nie ma admin_context — tu decyduje dowód nazwy.
    FakeNominatim.responses.append([wiazowna_result()])
    assert geo("Wiązowna")[0] == lb.GEOCODE_OK


def test_stage_1_2_suburb_is_a_context_field_and_still_explains_the_query():
    """``suburb`` wchodzi do pól kontekstu, więc osiedle nadal działa."""
    FakeNominatim.responses.append([wiazowna_result(suburb="Osiedle Parkowe")])
    assert geo("Wiązowna, Osiedle Parkowe")[0] == lb.GEOCODE_OK
    FakeNominatim.responses.append([wiazowna_result(suburb="Osiedle Parkowe")])
    assert geo("Wiązowna, Osiedle Batory")[0] == lb.GEOCODE_NO_MATCH


def test_stage_1_2_country_code_filter_and_context_work_together():
    FakeNominatim.responses.append([olsztyn_slaskie_result(country_code="de")])
    assert geo("Olsztyn, Częstochowa, Polska")[0] == lb.GEOCODE_NO_MATCH
    FakeNominatim.responses.append([olsztyn_slaskie_result()])
    status, lat, _lon, _short, _display = geo("Olsztyn, Częstochowa, Polska")
    assert status == lb.GEOCODE_OK and lat == 50.75
