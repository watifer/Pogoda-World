"""UX tests for safe, separate short and display location labels."""

import pytest

import i18n
import location_bot as lb

LANGS = ("pl", "en", "de", "es", "fr", "no")
SHORT_LABEL = "Wiązowna"
DISPLAY_LOCATION = (
    "Wiązowna, gmina Wiązowna, powiat otwocki, województwo mazowieckie, "
    "05-462, Polska"
)
# HOTFIX: postcode gating — widoczny tylko, gdy query zawiera TEN SAM kod.
# Zapytania poniżej ("Wiązowna" bez "05-462") nie wymieniają kodu wprost, więc
# publiczna etykieta nie może go pokazać tylko dlatego, że istnieje w adresie.
DISPLAY_LOCATION_NO_POSTCODE = (
    "Wiązowna, gmina Wiązowna, powiat otwocki, województwo mazowieckie, Polska"
)
POISON = "Biblioteka publiczna, Kościelna 41, Osiedle Parkowe"
ADDRESS = {
    "city": "Wiązowna",
    "municipality": "Wiązowna",
    "county": "otwocki",
    "state": "mazowieckie",
    "postcode": "05-462",
    "country": "Polska",
    "country_code": "pl",
    "road": "Kościelna",
    "house_number": "41",
    "suburb": "Osiedle Parkowe",
    "amenity": "Biblioteka publiczna w Wiązownie",
}


@pytest.fixture(autouse=True)
def _clean_geo_cache():
    lb._GEO_DETAILS_CACHE.clear()
    yield
    lb._GEO_DETAILS_CACHE.clear()


# ============================================================================
# UI strings: only the clean display label is shown below a card / after /city
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", ["location_saved", "used_location"])
def test_messages_use_display_location_placeholder(lang, key):
    text = i18n.UI_TEXTS[lang][key]
    assert "{display_location}" in text
    assert "{address}" not in text
    assert "{city}" not in text
    assert "`" not in text
    assert "/save_location" not in text and "/oneoff" not in text
    assert text.count("*") % 2 == 0


def test_polish_location_messages_are_exact():
    assert i18n.UI_TEXTS["pl"]["used_location"] == (
        "📍 *Użyta lokalizacja:*\n{display_location}\n\n"
        "Pomyłka? Powtórz jeszcze raz komendę."
    )
    assert i18n.UI_TEXTS["pl"]["location_saved"] == (
        "✅ *Zapisana lokalizacja:*\n{display_location}\n\n"
        "Pomyłka? Powtórz jeszcze raz komendę."
    )
    # Legacy Formularz path uses the same safe saved-location wording.
    assert i18n.UI_TEXTS["pl"]["search_success"] == i18n.UI_TEXTS["pl"]["location_saved"]


def test_changed_location_messages_are_translated_and_do_not_advertise_hidden_aliases():
    for lang in LANGS:
        for key in ("search_success", "used_location", "location_saved"):
            text = i18n.UI_TEXTS[lang][key]
            assert "{display_location}" in text
            assert "/save_location" not in text
            assert "/oneoff" not in text
            assert "`" not in text


def test_short_variants_are_gone():
    for lang in LANGS:
        assert "location_saved_short" not in i18n.UI_TEXTS[lang]
        assert "used_location_short" not in i18n.UI_TEXTS[lang]


def test_message_helpers_escape_dynamic_display_location():
    used = lb._used_location_message("pl", SHORT_LABEL, "Miasto_test *x* [link]")
    saved = i18n.t_ui(
        "pl", "location_saved",
        display_location=lb._md_safe("Miasto_test *x* [link]"),
    )
    escaped = r"Miasto\_test \*x\* \[link]"
    assert escaped in used and escaped in saved
    assert "/save_location" not in used and "/oneoff" not in used
    assert "/save_location" not in saved and "/oneoff" not in saved


# ============================================================================
# Geocoder status + safe labels (fake Nominatim; no network)
# ============================================================================

class _FakeLocation:
    def __init__(self, address_dict=None, address_text=POISON):
        self.raw = {"address": address_dict} if address_dict is not None else {}
        # Deliberately misleading: public output must never read this string.
        self.address = address_text


class _FakeNominatim:
    calls = []
    results = []

    def __init__(self, user_agent=None):
        pass

    def reverse(self, query, language=None):
        _FakeNominatim.calls.append(("reverse", query, language))
        result = _FakeNominatim.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def geocode(self, name, exactly_one=None, language=None, limit=None,
                addressdetails=None):
        _FakeNominatim.calls.append(("geocode", name, language))
        result = _FakeNominatim.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeGeocodeResult:
    latitude = 52.229721
    longitude = 21.012234
    raw = {
        # ETAP 1: klasa musi być bezpieczna (miejscowość), inaczej kandydat
        # zostaje odrzucony zanim w ogóle zobaczylibyśmy jego nazwę.
        "class": "place",
        "type": "city",
        "display_name": POISON,
        "address": ADDRESS,
    }
    address = POISON


@pytest.fixture
def fake_nominatim(monkeypatch):
    _FakeNominatim.calls = []
    _FakeNominatim.results = []
    monkeypatch.setattr(lb, "Nominatim", _FakeNominatim)
    return _FakeNominatim


def test_reverse_uses_structured_fields_and_formats_exact_polish_label(fake_nominatim):
    fake_nominatim.results.append(_FakeLocation(ADDRESS))
    # HOTFIX: postcode gating — query musi wprost wymieniać TEN SAM kod, żeby
    # był publicznie widoczny, więc dopisujemy "05-462" do zapytania.
    short_label, display_location, status = lb.get_location_details_from_coords(
        52.15, 21.29, "pl", query="Wiązowna 05-462"
    )

    assert (short_label, display_location, status) == (
        SHORT_LABEL, DISPLAY_LOCATION, lb.GEO_OK
    )
    assert _FakeNominatim.calls == [("reverse", "52.15, 21.29", "pl")]
    for forbidden in ("Biblioteka publiczna", "41", "Kościelna", "Osiedle Parkowe"):
        assert forbidden not in display_location


def test_city_query_never_shows_road_house_number_suburb_or_poi(fake_nominatim):
    fake_nominatim.results.append(_FakeLocation(ADDRESS))
    # HOTFIX: jak wyżej — postcode w query, żeby DISPLAY_LOCATION nadal
    # zawierał "05-462" i test mógł sprawdzać pozostałe pola bez zmian.
    short_label, display_location, status = lb.get_location_details_from_coords(
        52.15, 21.29, "pl", query="Wiązowna 05-462"
    )

    assert status == lb.GEO_OK
    assert short_label == SHORT_LABEL
    assert display_location == DISPLAY_LOCATION
    assert "Biblioteka publiczna" not in display_location
    assert "41" not in display_location
    assert "Kościelna" not in display_location
    assert "Osiedle Parkowe" not in display_location


def test_poi_never_reaches_used_or_saved_location_message(fake_nominatim):
    fake_nominatim.results.append(_FakeLocation(ADDRESS))
    short_label, display_location, status = lb.get_location_details_from_coords(
        52.15, 21.29, "pl", query="Wiązowna"
    )
    assert status == lb.GEO_OK

    messages = (
        lb._used_location_message("pl", short_label, display_location),
        i18n.t_ui(
            "pl", "location_saved",
            display_location=lb._md_safe(display_location),
        ),
    )
    for message in messages:
        assert "Biblioteka publiczna" not in message
        assert "Kościelna" not in message
        assert "Osiedle Parkowe" not in message
        assert "41" not in message


def test_explicit_matching_street_query_may_show_road_but_never_number_or_poi(fake_nominatim):
    street_address = dict(ADDRESS, road="Lipowa")
    fake_nominatim.results.append(_FakeLocation(street_address))
    short_label, display_location, status = lb.get_location_details_from_coords(
        52.15, 21.29, "pl", query="ul. Lipowa 41, Wiązowna"
    )

    assert status == lb.GEO_OK
    assert short_label == SHORT_LABEL
    assert "Wiązowna, Lipowa, gmina Wiązowna" in display_location
    assert "41" not in display_location
    assert "Biblioteka publiczna" not in display_location
    assert "Osiedle Parkowe" not in display_location


def test_house_number_is_removed_even_if_it_is_embedded_in_road_field(fake_nominatim):
    address_with_number_in_road = dict(ADDRESS, road="Lipowa 41")
    fake_nominatim.results.append(_FakeLocation(address_with_number_in_road))
    short_label, display_location, status = lb.get_location_details_from_coords(
        52.15, 21.29, "pl", query="ul. Lipowa 41, Wiązowna"
    )

    assert status == lb.GEO_OK
    assert short_label == SHORT_LABEL
    assert "Lipowa" in display_location
    assert "41" not in display_location


def test_pin_or_gps_mode_never_includes_a_road(fake_nominatim):
    fake_nominatim.results.append(_FakeLocation(ADDRESS))
    short_label, display_location, status = lb.get_location_details_from_coords(
        52.15, 21.29, "pl"
    )

    assert status == lb.GEO_OK
    assert short_label == SHORT_LABEL
    # HOTFIX: pinezka/GPS nie ma query — postcode nie może pojawić się
    # publicznie tylko dlatego, że istnieje w reverse adresie.
    assert display_location == DISPLAY_LOCATION_NO_POSTCODE
    assert "05-462" not in display_location
    assert "Kościelna" not in display_location


def test_missing_locality_uses_safe_administrative_components(fake_nominatim):
    safe_admin = {
        "county": "otwocki",
        "state": "mazowieckie",
        "postcode": "05-462",
        "country": "Polska",
        "country_code": "pl",
        "road": "Kościelna",
        "house_number": "41",
        "suburb": "Osiedle Parkowe",
        "amenity": "Biblioteka publiczna",
    }
    fake_nominatim.results.append(_FakeLocation(safe_admin))
    short_label, display_location, status = lb.get_location_details_from_coords(
        52.15, 21.29, "pl"
    )

    assert status == lb.GEO_OK
    assert short_label == "powiat otwocki"
    # HOTFIX: brak query (pin/GPS) => brak postcode, mimo że adres go ma.
    assert display_location == (
        "powiat otwocki, województwo mazowieckie, Polska"
    )
    assert "05-462" not in display_location
    for forbidden in ("Biblioteka publiczna", "41", "Kościelna", "Osiedle Parkowe"):
        assert forbidden not in display_location


def test_missing_structured_fields_does_not_fall_back_to_raw_address_or_query(fake_nominatim):
    fake_nominatim.results.append(_FakeLocation({}, "Biblioteka publiczna, Wiązowna"))
    assert lb.get_location_details_from_coords(
        52.15, 21.29, "pl", query="Wiązowna"
    ) == (None, None, lb.GEO_NO_CITY)


def test_reverse_none_means_connection_error(fake_nominatim):
    fake_nominatim.results.append(None)
    assert lb.get_location_details_from_coords(52.15, 21.29, "pl") == (
        None, None, lb.GEO_ERROR
    )


def test_reverse_exception_means_connection_error(fake_nominatim):
    fake_nominatim.results.append(TimeoutError("timeout"))
    assert lb.get_location_details_from_coords(52.15, 21.29, "pl") == (
        None, None, lb.GEO_ERROR
    )


def test_connection_error_is_not_cached_but_success_is(fake_nominatim):
    fake_nominatim.results.append(TimeoutError("timeout"))
    assert lb.get_location_details_from_coords(52.15, 21.29, "pl")[2] == lb.GEO_ERROR

    fake_nominatim.results.append(_FakeLocation(ADDRESS))
    assert lb.get_location_details_from_coords(52.15, 21.29, "pl")[2] == lb.GEO_OK
    assert lb.get_location_details_from_coords(52.15, 21.29, "pl")[2] == lb.GEO_OK
    assert len(fake_nominatim.calls) == 2


def test_query_is_part_of_reverse_cache_key(fake_nominatim):
    fake_nominatim.results.extend([_FakeLocation(ADDRESS), _FakeLocation(ADDRESS)])
    lb.get_location_details_from_coords(52.15, 21.29, "pl", query="Wiązowna")
    lb.get_location_details_from_coords(52.15, 21.29, "pl", query="ul. Lipowa 41, Wiązowna")
    assert len(fake_nominatim.calls) == 2


def test_forward_geocode_and_guest_adapter_never_return_raw_address(fake_nominatim):
    fake_nominatim.results.extend([_FakeGeocodeResult(), _FakeGeocodeResult()])
    # HOTFIX: postcode gating — query musi wprost wymieniać "05-462", żeby
    # DISPLAY_LOCATION (z postcode) nadal pasował dokładnie.
    lat, lon, display_location, ok = lb.geocode_city_details("Wiązowna 05-462", "pl")
    assert (lat, lon, ok) == (52.229721, 21.012234, True)
    assert display_location == DISPLAY_LOCATION
    assert POISON not in display_location

    lat, lon, safe_label = lb.get_coords_from_city("Wiązowna 05-462", "pl")
    assert (lat, lon, safe_label) == (52.229721, 21.012234, DISPLAY_LOCATION)
    assert POISON not in safe_label


def test_forward_geocode_failure_and_not_found_status(fake_nominatim):
    fake_nominatim.results.extend([TimeoutError("timeout"), None])
    assert lb.geocode_city_details("Wiązowna", "pl") == (None, None, None, False)
    assert lb.geocode_city_details("Xyzzz", "pl") == (None, None, None, True)


# ============================================================================
# Label resolution + user messages
# ============================================================================

def test_resolve_labels_preserves_reverse_short_and_display_pair(monkeypatch):
    monkeypatch.setattr(
        lb, "get_location_details_from_coords",
        lambda lat, lon, lang, query=None, mode=None:
            (SHORT_LABEL, DISPLAY_LOCATION, lb.GEO_OK),
    )
    assert lb._resolve_location_labels(
        52.15, 21.29, "pl", query="Wiązowna"
    ) == (SHORT_LABEL, DISPLAY_LOCATION, lb.GEO_OK)


def test_resolve_labels_uses_field_message_without_locality_or_forward_fallback(monkeypatch):
    monkeypatch.setattr(
        lb, "get_location_details_from_coords",
        lambda lat, lon, lang, query=None, mode=None: (None, None, lb.GEO_NO_CITY),
    )
    short_label, display_location, status = lb._resolve_location_labels(
        23.0, 12.0, "de"
    )
    assert status == lb.GEO_NO_CITY
    assert short_label == lb.FIELD_LOCATION_LABEL
    assert display_location == i18n.UI_TEXTS["de"]["location_field"]


def test_query_is_not_used_as_fallback_after_reverse_failure(monkeypatch):
    monkeypatch.setattr(
        lb, "get_location_details_from_coords",
        lambda lat, lon, lang, query=None, mode=None: (None, None, lb.GEO_ERROR),
    )
    assert lb._resolve_location_labels(
        52.15, 21.29, "pl", query="Wiązowna"
    ) == (None, None, lb.GEO_ERROR)


def test_safe_forward_pair_is_fallback_when_reverse_fails(monkeypatch):
    monkeypatch.setattr(
        lb, "get_location_details_from_coords",
        lambda lat, lon, lang, query=None, mode=None: (None, None, lb.GEO_ERROR),
    )
    assert lb._resolve_location_labels(
        52.15, 21.29, "pl",
        fallback_short_label=SHORT_LABEL,
        fallback_display_location=DISPLAY_LOCATION,
        query="Wiązowna",
    ) == (SHORT_LABEL, DISPLAY_LOCATION, lb.GEO_OK)


def test_message_helper_uses_full_display_but_not_as_short_label():
    message = lb._used_location_message("pl", SHORT_LABEL, DISPLAY_LOCATION)
    assert message == (
        "📍 *Użyta lokalizacja:*\n"
        f"{DISPLAY_LOCATION}\n\n"
        "Pomyłka? Powtórz jeszcze raz komendę."
    )
    assert SHORT_LABEL in message
    assert "/save_location" not in message and "/oneoff" not in message


def test_markdown_dynamic_text_is_escaped_not_stripped():
    assert lb._md_safe(r"Wiąz*owna_test`x`[y]\\") == (
        r"Wiąz\*owna\_test\`x\`\[y]\\\\"
    )
    safe = lb._md_safe("Dąbrowa_Górnicza *test*")
    assert safe == r"Dąbrowa\_Górnicza \*test\*"
    message = lb._used_location_message("pl", "X", "Dąbrowa_Górnicza *test*")
    assert r"Dąbrowa\_Górnicza \*test\*" in message
