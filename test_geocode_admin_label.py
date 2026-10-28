"""test_geocode_admin_label.py — FAZA 2: etykieta potwierdzonej granicy administracyjnej.

Problem produkcyjny: ``/teraz Tokio, Japonia`` znajdowało lokalizację, ale karta
pokazywała samą „Japonię”. Nominatim dla ``Tokio, Japonia`` zwraca relację
``boundary/administrative`` z ``addresstype=province`` — w ``address`` jest
TYLKO ``province`` (+ ``country``), bez ``city``/``town``/``village`` i bez
``municipality``/``county``/``state``, więc ``_select_short_location_label``
spadał do ``country``.

Faza 2 dokłada wąską regułę (R1–R5 w ``_geocode_confirmed_admin_label``):
  R1 kandydat jest potwierdzoną granicą administracyjną,
  R2 brak ŻADNEGO pola miejscowości,
  R3 dowód jest mocny (``place``/``localized``; ``display`` nigdy),
  R4 pole administracyjne jest PEŁNYM dopasowaniem potwierdzonej nazwy,
  R5 etykieta jest surową wartością tego pola, nigdy tekstem użytkownika.

Nowa wartość wchodzi w ``_select_short_location_label`` DOPIERO po istniejącej
pętli ``municipality``/``county``/``state`` i PRZED ``postcode``/``country``,
więc dotychczasowe pierwszeństwa są nienaruszone.

Nie zmieniamy: ``_resolve_location_labels``, GPS/pinezki/WebApp, komunikatów,
kontraktów krotek (``_geocode_candidate_evidence`` = 2‑krotka,
``_geocode_select_accepted`` = 2‑krotka, ``geocode_city_accepted`` = 5‑krotka).

Fixtury to LITERALNE payloady z odpowiedzi Nominatim (Oracle, 2026‑10).
Produkcyjny geopy 2.5.0 wysyła ``format=json`` (JSON v1 → klucz ``class``),
dlatego fixtura forward używa ``class``; wariant ``jsonv2`` (``category``) jest
udokumentowany osobnym testem jako znane ograniczenie (D13 odłożone).

Zero sieci: geokoder jest atrapą, a ``pytest.ini`` ma ``--disable-socket``.

Uruchomienie: pytest test_geocode_admin_label.py -v
"""

import ast
import os

import pytest

import guest_bot_handler as gbh
import location_bot as lb
from test_geocode_validation import FakeResult, guest_handler_run

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================================
# LITERALNE PAYLOADY ORACLE (Nominatim, akcept-language=pl)
# ============================================================================

# forward: q="Tokio, Japonia" -> relacja 1543125, addresstype=province.
ORACLE_FORWARD_ADDRESS = {
    "province": "Tokio",
    "ISO3166-2-lvl4": "JP-13",
    "country": "Japonia",
    "country_code": "jp",
}
ORACLE_FORWARD_NAMEDETAILS = {
    "ref": "130001",
    "name": "東京都",
    "name:be": "Токіа", "name:br": "Tokyo", "name:ca": "Tòquio", "name:cs": "Tokio",
    "name:de": "Tokio", "name:en": "Tokyo", "name:eo": "Tokio", "name:es": "Tokio",
    "name:fi": "Tokio", "name:fr": "Tokyo", "name:gl": "Toquio", "name:hr": "Tokio",
    "name:ia": "Tokyo", "name:it": "Tokyo", "name:ja": "東京", "name:ko": "도쿄도",
    "name:la": "Tokium", "name:mi": "Tōkio", "name:nl": "Tokio", "name:oc": "Tòquio",
    "name:pl": "Tokio", "name:pt": "Tóquio", "name:ru": "Токио", "name:tr": "Tokyo",
    "name:uk": "Префектура Токіо", "name:zh": "东京都/東京都", "name:cnr": "Tokio",
    "name:szl": "Tokjo",
    "ISO3166-2": "JP-13",
    "alt_name:be": "Токіё", "alt_name:hr": "Tokyo", "alt_name:it": "Tochio",
    "alt_name:la": "Tocium", "alt_name:nl": "Tokyo",
    "name:ja-Hira": "とうきょう", "name:ja-Latn": "Tōkyō",
    "name:zh-Hans": "东京都", "name:zh-Hant": "東京都",
    "name:hu": "Tokió", "name:lt": "Tokijas", "name:lv": "Tokija",
    "official_name:en": "Tokyo Metropolis", "official_name:it": "Metropoli di Tokyo",
    "official_name:ja": "東京都",
    "old_name:fr": "Tokio", "old_name:ja": "東亰", "old_name:vi": "Đông Kinh",
    "old_name:zh": "东京",
    "_place_name:ja": "東京都", "_place_name:ja-Hira": "とうきょうと",
    "_place_name:ja-Latn": "Tōkyō-to", "official_name:ja-Latn": "Tōkyō-to",
    "old_name:zh-Hans": "东京", "old_name:zh-Hant": "東京",
}
ORACLE_FORWARD_TOP = {
    "place_id": 261527214,
    "osm_type": "relation",
    "osm_id": 1543125,
    "lat": "35.6768601",
    "lon": "139.7638947",
    "class": "boundary",          # geopy 2.5.0 -> format=json (JSON v1)
    "type": "administrative",
    "place_rank": 8,
    "importance": 0.8337626616070368,
    "addresstype": "province",
    "name": "Tokio",
    "display_name": "Tokio, Japonia",
    "boundingbox": ["20.2145811", "35.8984245", "135.8536855", "154.2055410"],
}

# reverse: punkt w Tokio, zoom=18 (krok 1 w get_location_details_from_coords).
ORACLE_REVERSE_ADDRESS = {
    "neighbourhood": "和泉二丁目",
    "quarter": "和泉",
    "city": "Suginami",
    "ISO3166-2-lvl4": "JP-13",
    "postcode": "168-0063",
    "country": "Japonia",
    "country_code": "jp",
}
ORACLE_REVERSE_TOP = {
    "place_id": 265141856,
    "osm_type": "way",
    "osm_id": 35939428,
    "class": "highway",
    "type": "residential",
    "place_rank": 26,
    "addresstype": "residential",
    "name": "",
    "display_name": "和泉二丁目, 和泉, Suginami, Tokio, 168-0063, Japonia",
}

FORWARD_LAT, FORWARD_LON = 35.6768601, 139.7638947
REVERSE_LAT, REVERSE_LON = 35.6761772, 139.6502952


def oracle_forward(address=None, namedetails=ORACLE_FORWARD_NAMEDETAILS,
                   cls="boundary", typ="administrative", name="Tokio",
                   display="Tokio, Japonia", lat=FORWARD_LAT, lon=FORWARD_LON):
    """Kandydat forward o kształcie DOKŁADNIE takim, jak odpowiedź Nominatima."""
    result = FakeResult(
        ORACLE_FORWARD_ADDRESS if address is None else address,
        lat=lat, lon=lon, cls=cls, typ=typ,
        name=name, namedetails=namedetails, display=display,
    )
    for key, value in ORACLE_FORWARD_TOP.items():
        if key not in ("class", "type", "name", "display_name"):
            result.raw[key] = value
    result.raw["class"] = cls
    result.raw["type"] = typ
    return result


def oracle_reverse():
    """Wynik reverse (krok 1) o kształcie odpowiedzi Nominatima."""
    result = FakeResult(
        ORACLE_REVERSE_ADDRESS, lat=REVERSE_LAT, lon=REVERSE_LON,
        cls="highway", typ="residential", name="",
        display=ORACLE_REVERSE_TOP["display_name"],
    )
    for key, value in ORACLE_REVERSE_TOP.items():
        if key not in ("class", "type", "name", "display_name"):
            result.raw[key] = value
    return result


class OracleNominatim:
    """Atrapa geopy.Nominatim: forward = Oracle, reverse = Oracle, z licznikiem."""

    calls = []
    forward_response = None
    reverse_response = None

    def __init__(self, user_agent=None):
        pass

    def geocode(self, query, exactly_one=True, language=None, limit=None,
                addressdetails=None, **kwargs):
        OracleNominatim.calls.append({
            "kind": "forward", "query": query, "extras": dict(kwargs),
        })
        return OracleNominatim.forward_response

    def reverse(self, query, language=None, zoom=None, **kwargs):
        OracleNominatim.calls.append({"kind": "reverse", "query": query, "zoom": zoom})
        return OracleNominatim.reverse_response

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.forward_response = None
        cls.reverse_response = None

    @classmethod
    def forwards(cls):
        return [call for call in cls.calls if call["kind"] == "forward"]

    @classmethod
    def reverses(cls):
        return [call for call in cls.calls if call["kind"] == "reverse"]


@pytest.fixture(autouse=True)
def oracle_geocoder(monkeypatch):
    OracleNominatim.reset()
    OracleNominatim.forward_response = [oracle_forward()]
    OracleNominatim.reverse_response = oracle_reverse()
    monkeypatch.setattr(lb, "Nominatim", OracleNominatim)
    lb._GEO_DETAILS_CACHE.clear()
    gbh._GEO_CACHE.clear()
    gbh._CITY_CACHE.clear()
    yield
    OracleNominatim.reset()
    lb._GEO_DETAILS_CACHE.clear()
    gbh._GEO_CACHE.clear()
    gbh._CITY_CACHE.clear()


def forward_address():
    return lb._nominatim_address_components(oracle_forward().raw)


def forward_raw():
    return oracle_forward().raw


# ============================================================================
# 1. LITERALNY PAYLOAD ORACLE — fixtura musi mieć kształt rzeczywistej odpowiedzi
# ============================================================================

def test_forward_fixture_is_the_literal_oracle_payload():
    raw = forward_raw()
    assert raw["class"] == "boundary"
    assert raw["type"] == "administrative"
    assert raw["addresstype"] == "province"
    assert raw["name"] == "Tokio"
    assert raw["display_name"] == "Tokio, Japonia"
    assert raw["osm_type"] == "relation" and raw["osm_id"] == 1543125
    assert raw["address"] == {
        "province": "Tokio", "ISO3166-2-lvl4": "JP-13",
        "country": "Japonia", "country_code": "jp",
    }
    assert raw["namedetails"]["name:pl"] == "Tokio"
    assert raw["namedetails"]["name"] == "東京都"
    assert (raw["lat"], raw["lon"]) == ("35.6768601", "139.7638947")


def test_forward_fixture_has_no_settlement_field():
    """Kluczowy fakt: ten kandydat NIE MA city/town/village/hamlet/locality."""
    assert lb._geocode_place_values(forward_address()) == []
    for field in ("city", "town", "village", "hamlet", "locality",
                  "municipality", "county", "state"):
        assert field not in forward_address(), field


def test_reverse_fixture_is_the_literal_oracle_payload():
    raw = oracle_reverse().raw
    assert raw["class"] == "highway" and raw["type"] == "residential"
    assert raw["address"]["city"] == "Suginami"
    assert raw["address"]["country"] == "Japonia"
    assert raw["address"]["country_code"] == "jp"
    assert "Tokio" in raw["display_name"]


def test_phase_1_country_filter_still_selects_the_candidate():
    """Faza 1 bez zmian: rdzeń `Tokio`, filtr `jp`, kandydat zaakceptowany."""
    assert lb._geocode_query_parse("Tokio, Japonia") == ("Tokio", [], "jp")
    status, chosen = lb._geocode_select_accepted(
        "Tokio", [oracle_forward()], "jp", []
    )
    assert status == lb.GEOCODE_OK
    assert chosen.raw["address"]["province"] == "Tokio"


# ============================================================================
# 2. KONTRAKT EVIDENCE
# ============================================================================

def test_evidence_detail_for_the_oracle_payload():
    evidence = lb._geocode_candidate_evidence_with_admin_label(
        "tokio", forward_address(), forward_raw(), ""
    )
    assert evidence.matched == "tokio"
    assert evidence.strength == lb._GEOCODE_EVIDENCE_LOCALIZED
    assert evidence.source == "namedetails"
    # Pierwszym kluczem namedetails, który po normalizacji równa się "tokio",
    # jest w kolejności odpowiedzi Oracle "name:cs" (name=東京都 i name:be=Токіа
    # normalizują się do pustego, name:br=Tokyo i name:ca=Tòquio nie pasują).
    assert evidence.field.startswith("name:")
    assert evidence.raw_value == "Tokio"
    assert evidence.admin_label == "Tokio"
    assert evidence.admin_field == "province"


def test_evidence_contract_two_tuple_is_unchanged():
    """Kontrakt etapu 1: (matched, strength) — bez siódmego pola, bez namedtuple."""
    evidence = lb._geocode_candidate_evidence("tokio", forward_address(), forward_raw(), "")
    assert isinstance(evidence, tuple) and len(evidence) == 2
    assert evidence == ("tokio", lb._GEOCODE_EVIDENCE_LOCALIZED)


def test_evidence_detail_and_wrapper_agree_on_all_strengths():
    cases = [
        ("tokio", forward_address(), forward_raw(), ""),                 # localized
        ("tokio", {"city": "Tokio"}, {"address": {"city": "Tokio"}}, ""),  # place
        ("tokio", {}, {"name": "Tokio"}, ""),                            # display
        ("tokio", {"city": "Suginami"}, {"name": "Tokio"}, ""),          # brak dowodu
        ("", {}, {}, "05-462"),                                          # postcode
        ("", {}, {}, ""),                                                # nic
    ]
    for query_name, address, raw, postcode in cases:
        detail = lb._geocode_candidate_evidence_detail(query_name, address, raw, postcode)
        assert lb._geocode_candidate_evidence(query_name, address, raw, postcode) == (
            detail.matched, detail.strength
        )


def test_value_helpers_keep_their_previous_output():
    """Warianty *_entries są addytywne — stare funkcje zwracają to samo."""
    raw = forward_raw()
    address = forward_address()
    assert lb._geocode_namedetails_values(raw) == [
        value for _key, value in lb._geocode_namedetails_entries(raw)
    ]
    assert lb._geocode_raw_name_values(raw) == [
        value for _field, value in lb._geocode_raw_name_entries(raw)
    ]
    assert lb._geocode_place_values(address) == [
        normalized for _f, _c, normalized in lb._geocode_place_entries(address)
    ]
    assert "Tokio" in lb._geocode_namedetails_values(raw)
    assert lb._geocode_raw_name_values(raw)[0] == "Tokio"
    # Klucze nienazwowe nie są dowodem (ref, ISO3166-2, _place_name:*, alt_name:*).
    keys = [key for key, _value in lb._geocode_namedetails_entries(raw)]
    assert "ref" not in keys and "ISO3166-2" not in keys
    assert "_place_name:ja" not in keys
    assert not any(key.startswith("alt_name:") for key in keys)


def test_place_entries_expose_field_and_raw_value():
    entries = lb._geocode_place_entries({"town": "Hel", "city": ""})
    assert entries == [("town", "Hel", "hel")]


def test_evidence_for_place_candidate_reports_the_address_field():
    evidence = lb._geocode_candidate_evidence_detail(
        "suginami", ORACLE_REVERSE_ADDRESS, {"address": ORACLE_REVERSE_ADDRESS}, ""
    )
    assert (evidence.matched, evidence.strength) == ("suginami", lb._GEOCODE_EVIDENCE_PLACE)
    assert (evidence.source, evidence.field, evidence.raw_value) == (
        "address", "city", "Suginami"
    )
    assert evidence.admin_label == "" and evidence.admin_field == ""


def test_evidence_for_display_only_candidate_reports_raw_source():
    evidence = lb._geocode_candidate_evidence_detail(
        "tokio", {"province": "Tokio"},
        {"class": "boundary", "type": "administrative", "name": "Tokio"}, "",
    )
    assert (evidence.strength, evidence.source, evidence.field) == (
        lb._GEOCODE_EVIDENCE_DISPLAY, "raw", "name"
    )
    assert evidence.admin_label == "", "display nigdy nie awansuje administracji"


def test_evidence_no_match_is_the_empty_singleton():
    assert lb._geocode_candidate_evidence_detail("tokio", {"city": "Suginami"}, {}, "") == (
        lb._GEOCODE_NO_EVIDENCE
    )
    assert lb._GEOCODE_NO_EVIDENCE.matched == ""
    assert lb._GEOCODE_NO_EVIDENCE.strength is None


# ============================================================================
# 3. REGUŁA R1–R5 (unit)
# ============================================================================

def test_rule_accepts_the_oracle_province():
    assert lb._geocode_confirmed_admin_label(
        "tokio", forward_address(), forward_raw(), ""
    ) == ("Tokio", "province")


def test_rule_r1_rejects_class_place():
    raw = oracle_forward(cls="place", typ="province").raw
    assert lb._geocode_candidate_is_administrative(raw) is False
    assert lb._geocode_candidate_is_safe(raw) is True
    assert lb._geocode_confirmed_admin_label(
        "tokio", forward_address(), raw, ""
    ) == ("", "")


def test_rule_r1_rejects_non_administrative_boundary():
    for typ in ("protected_area", "national_park", "postal_code", "maritime", ""):
        raw = oracle_forward(typ=typ).raw
        assert lb._geocode_candidate_is_administrative(raw) is False, typ
        assert lb._geocode_candidate_is_safe(raw) is False, typ
        assert lb._geocode_confirmed_admin_label(
            "tokio", forward_address(), raw, ""
        ) == ("", ""), typ


def test_rule_r1_accepts_legacy_administrative_class_and_addresstype():
    """Legacy ``class=administrative`` oraz ``addresstype`` jako źródło typu."""
    raw = oracle_forward(cls="administrative").raw
    assert lb._geocode_candidate_is_administrative(raw) is True
    raw_no_type = oracle_forward().raw
    del raw_no_type["type"]
    raw_no_type["addresstype"] = "administrative"
    assert lb._geocode_candidate_is_administrative(raw_no_type) is True


def test_rule_r2_rejects_when_a_settlement_field_exists():
    address = dict(ORACLE_FORWARD_ADDRESS)
    address["city"] = "Tokio"
    assert lb._geocode_confirmed_admin_label(
        "tokio", address, forward_raw(), ""
    ) == ("", "")


@pytest.mark.parametrize("field", ["city", "town", "village", "hamlet", "locality"])
def test_rule_r2_rejects_every_settlement_field(field):
    address = dict(ORACLE_FORWARD_ADDRESS)
    address[field] = "Tokio"
    assert lb._geocode_confirmed_admin_label(
        "tokio", address, forward_raw(), ""
    ) == ("", ""), field


def test_rule_r3_rejects_display_only_evidence():
    raw = oracle_forward(namedetails=None).raw
    raw.pop("namedetails", None)
    evidence = lb._geocode_candidate_evidence_detail("tokio", forward_address(), raw, "")
    assert evidence.strength == lb._GEOCODE_EVIDENCE_DISPLAY
    assert lb._geocode_confirmed_admin_label(
        "tokio", forward_address(), raw, ""
    ) == ("", "")


def test_rule_r4_requires_a_full_match_of_the_admin_field():
    for province in ("Tokio Metropolis", "Tokio-To", "Tok", "Tokios", "Metropolia Tokio"):
        address = dict(ORACLE_FORWARD_ADDRESS)
        address["province"] = province
        assert lb._geocode_confirmed_admin_label(
            "tokio", address, forward_raw(), ""
        ) == ("", ""), province


def test_rule_r4_ignores_case_and_diacritics_only():
    """Równość po normalizacji: wielkość liter i diakrytyki nie zmieniają wyniku."""
    address = dict(ORACLE_FORWARD_ADDRESS)
    address["province"] = "TOKIO"
    assert lb._geocode_confirmed_admin_label(
        "tokio", address, forward_raw(), ""
    ) == ("TOKIO", "province")


def test_rule_r4_never_uses_the_user_text():
    """Inna pisownia w polu = brak awansu; etykieta NIE bierze się z zapytania."""
    address = dict(ORACLE_FORWARD_ADDRESS)
    address["province"] = "Tokyo"
    assert lb._geocode_confirmed_admin_label(
        "tokio", address, forward_raw(), ""
    ) == ("", "")
    OracleNominatim.forward_response = [oracle_forward(address=address)]
    status, _lat, _lon, short_label, _display = lb.geocode_city_accepted(
        "Tokio, Japonia", "pl"
    )
    assert status == lb.GEOCODE_OK
    assert short_label == "Japonia", "brak potwierdzenia = dotychczasowy fallback"


def test_rule_does_not_promote_an_unrelated_region():
    """Dowolne province/state NIE jest awansowane (dowód ≠ wartość pola)."""
    address = {
        "state": "województwo mazowieckie", "country": "Polska", "country_code": "pl",
    }
    raw = {
        "class": "boundary", "type": "administrative", "name": "Warszawa",
        "display_name": "Warszawa, województwo mazowieckie, Polska",
        "address": address, "namedetails": {"name:pl": "Warszawa"},
    }
    assert lb._geocode_confirmed_admin_label("warszawa", address, raw, "") == ("", "")
    assert lb._select_short_location_label(
        address, query="Warszawa, Polska", lang="pl"
    ) == "województwo mazowieckie"


@pytest.mark.parametrize("field", list(lb._GEOCODE_LABEL_ADMIN_FIELDS))
def test_rule_covers_exactly_the_accepted_admin_fields(field):
    address = {"country": "Japonia", "country_code": "jp", field: "Tokio"}
    raw = {
        "class": "boundary", "type": "administrative", "name": "Tokio",
        "display_name": "Tokio, Japonia", "address": address,
        "namedetails": {"name:pl": "Tokio"},
    }
    assert lb._geocode_confirmed_admin_label("tokio", address, raw, "") == (
        "Tokio", field
    ), field


def test_rule_returns_the_first_matching_field_in_declared_order():
    address = {"region": "Tokio", "province": "Tokio", "country": "Japonia",
               "country_code": "jp"}
    raw = {
        "class": "boundary", "type": "administrative", "name": "Tokio",
        "display_name": "Tokio, Japonia", "address": address,
        "namedetails": {"name:pl": "Tokio"},
    }
    assert lb._geocode_confirmed_admin_label("tokio", address, raw, "") == (
        "Tokio", "province"
    )


def test_label_admin_fields_include_the_accepted_set():
    assert lb._GEOCODE_LABEL_ADMIN_FIELDS == (
        "province", "state", "state_district", "region", "district",
        "county", "municipality",
    )


# ============================================================================
# 3B. R5 — POTWIERDZONA WARTOŚĆ IDZIE PRZEZ `_format_admin_component`
# ============================================================================
# Ustalenie kontroli R5 (pomiar, nie założenie): mapa prefiksów w
# `_format_admin_component` ma wpisy WYŁĄCZNIE dla municipality/county/state i
# działa tylko dla adresów polskich. Pola osiągalne dla `confirmed_admin`
# (province, state_district, region, district — różnica
# `_GEOCODE_LABEL_ADMIN_FIELDS` minus `_ADMIN_ADDRESS_FIELDS`) prefiksów nie
# mają, a municipality/county/state nigdy do tego miejsca nie docierają, bo
# wcześniejsza pętla zwraca je pierwsze. Dlatego przejście przez wspólny
# formatter jest dziś równoważne zwróceniu wartości dosłownej, a jednocześnie
# prefiksy pozostają w jednym miejscu.

REACHABLE_FIELDS = tuple(
    field for field in lb._GEOCODE_LABEL_ADMIN_FIELDS
    if field not in lb._ADMIN_ADDRESS_FIELDS
)


def test_prefix_map_covers_only_municipality_county_state():
    """Decyzja R5 udokumentowana testem: `province`/`region`/… bez prefiksu."""
    for field in ("municipality", "county", "state"):
        assert lb._format_admin_component(field, "Tokio", polish=True) != "Tokio", field
    for field in REACHABLE_FIELDS:
        assert lb._format_admin_component(field, "Tokio", polish=True) == "Tokio", field
        assert lb._format_admin_component(field, "Tokio", polish=False) == "Tokio", field
    assert REACHABLE_FIELDS == ("province", "state_district", "region", "district")


@pytest.mark.parametrize("field", list(lb._GEOCODE_LABEL_ADMIN_FIELDS))
@pytest.mark.parametrize("country,country_code,polish", [
    ("Polska", "pl", True), ("Japonia", "jp", False),
])
def test_confirmed_admin_uses_the_same_formatter_as_other_admin_fields(
    field, country, country_code, polish
):
    """Dla każdego pola i obu rodzajów adresu wynik == `_format_admin_component`."""
    address = {"country": country, "country_code": country_code, field: "Tokio"}
    assert lb._select_short_location_label(
        address, query="Tokio", lang="pl", confirmed_admin="Tokio"
    ) == lb._format_admin_component(field, "Tokio", polish=polish), field


@pytest.mark.parametrize("field", REACHABLE_FIELDS)
def test_reachable_fields_never_gain_a_prefix(field):
    """Nawet dla polskiego adresu pola osiągalne wracają bez prefiksu."""
    for country, country_code in (("Polska", "pl"), ("Japonia", "jp")):
        address = {"country": country, "country_code": country_code, field: "Tokio"}
        assert lb._select_short_location_label(
            address, query="Tokio", lang="pl", confirmed_admin="Tokio"
        ) == "Tokio", (field, country_code)


def test_tokyo_payload_label_is_the_bare_province_name():
    """Wymagany przypadek 1: payload Tokio z `province` -> `Tokio`, bez prefiksu."""
    assert lb._select_short_location_label(
        forward_address(), query="Tokio, Japonia", lang="pl", confirmed_admin="Tokio"
    ) == "Tokio"
    assert lb._format_public_location_parts(
        forward_address(), query="Tokio, Japonia", mode="city", lang="pl",
        confirmed_admin="Tokio",
    ) == ("Tokio", "Tokio, Japonia")
    _status, _lat, _lon, short_label, _display = lb.geocode_city_accepted(
        "Tokio, Japonia", "pl"
    )
    assert short_label == "Tokio"


def test_polish_administrative_address_keeps_its_prefix():
    """Wymagany przypadek 2: polski adres administracyjny.

    Rzeczywisty polski adres bez miejscowości ma `state` (nie `province`), więc
    obsługuje go ISTNIEJĄCA pętla z prefiksem „województwo” — z potwierdzeniem
    i bez niego wynik jest identyczny.
    """
    address = {"state": "mazowieckie", "country": "Polska", "country_code": "pl"}
    expected = "województwo mazowieckie"
    assert lb._select_short_location_label(
        address, query="mazowieckie", lang="pl"
    ) == expected
    assert lb._select_short_location_label(
        address, query="mazowieckie", lang="pl", confirmed_admin="mazowieckie"
    ) == expected
    assert expected == lb._format_admin_component("state", "mazowieckie", polish=True)


def test_polish_address_with_only_a_province_gets_no_prefix():
    """Hipotetyczny polski adres z samym `province` — pole bez wpisu prefiksu."""
    address = {"province": "Tokio", "country": "Polska", "country_code": "pl"}
    assert lb._select_short_location_label(
        address, query="Tokio", lang="pl", confirmed_admin="Tokio"
    ) == "Tokio"


def test_non_polish_address_never_gains_a_polish_prefix():
    """Wymagany przypadek 3: adres niepolski — brak prefiksów także dla state."""
    address = {"state": "Tokio", "country": "Japonia", "country_code": "jp"}
    assert lb._select_short_location_label(
        address, query="Tokio", lang="pl"
    ) == "Tokio"
    assert lb._select_short_location_label(
        address, query="Tokio", lang="pl", confirmed_admin="Tokio"
    ) == "Tokio"
    for lang in ("en", "de", "es", "fr", "no"):
        assert lb._select_short_location_label(
            address, query="Tokio", lang=lang, confirmed_admin="Tokio"
        ) == "Tokio", lang


@pytest.mark.parametrize("field,prefix", [
    ("municipality", "gmina"), ("county", "powiat"), ("state", "województwo"),
])
def test_existing_municipality_county_state_behaviour_is_unchanged(field, prefix):
    """Wymagany przypadek 4: istniejące pola i ich prefiksy PL bez zmian."""
    address = {"country": "Polska", "country_code": "pl", field: "Hel"}
    expected = "%s Hel" % (prefix,)
    assert lb._select_short_location_label(address, query="Hel", lang="pl") == expected
    assert lb._select_short_location_label(
        address, query="Hel", lang="pl", confirmed_admin="Hel"
    ) == expected, "confirmed_admin nie zmienia wyniku istniejącej pętli"
    assert lb._select_short_location_label(
        address, query="Hel", lang="pl", confirmed_admin="cos innego"
    ) == expected, "nawet inne potwierdzenie nie przejmuje tych pól"
    # Display dokleja kraj — to istniejące zachowanie `_PUBLIC_ADDRESS_FIELDS`,
    # niezależne od fazy 2 (pomiar: "gmina Hel, Polska").
    assert lb._format_public_location_parts(
        address, query="Hel", mode="city", lang="pl"
    ) == (expected, "%s, Polska" % (expected,))


def test_confirmed_admin_none_is_identical_to_omitting_the_argument():
    """Kompatybilność wsteczna: `None` (reverse/GPS/pinezka) nic nie zmienia."""
    for address in (forward_address(), ORACLE_REVERSE_ADDRESS,
                    {"country": "Polska", "country_code": "pl", "state": "mazowieckie"},
                    {"country": "Japonia", "country_code": "jp"}):
        assert lb._select_short_location_label(
            address, query="Tokio", lang="pl", confirmed_admin=None
        ) == lb._select_short_location_label(address, query="Tokio", lang="pl")
        assert lb._select_short_location_label(
            address, query="Tokio", lang="pl", confirmed_admin=""
        ) == lb._select_short_location_label(address, query="Tokio", lang="pl")
        assert lb._format_public_location_parts(
            address, query="Tokio", mode="city", lang="pl", confirmed_admin=None
        ) == lb._format_public_location_parts(address, query="Tokio", mode="city", lang="pl")


def test_confirmed_admin_is_cleaned_before_use():
    """Białe znaki i wartości nie-tekstowe są czyszczone jak każde inne pole."""
    address = dict(ORACLE_FORWARD_ADDRESS)
    assert lb._select_short_location_label(
        address, query="Tokio, Japonia", lang="pl", confirmed_admin="  Tokio\n "
    ) == "Tokio"
    for junk in (None, {}, [], ()):
        assert lb._select_short_location_label(
            address, query="Tokio, Japonia", lang="pl", confirmed_admin=junk
        ) == "Japonia", junk
    # Wartości liczbowe są stringowane dokładnie tak, jak w każdym innym polu
    # adresu — `_clean_location_component` nie został zmieniony w fazie 2.
    assert lb._clean_location_component(0) == "0"
    assert lb._select_short_location_label(
        address, query="Tokio, Japonia", lang="pl", confirmed_admin=0
    ) == "0"


# ============================================================================
# 4. ETYKIETA FORWARD I KARTA (wymagane wyniki 2 i 3)
# ============================================================================

def test_forward_labels_use_the_confirmed_province():
    assert lb.geocode_city_accepted("Tokio, Japonia", "pl") == (
        lb.GEOCODE_OK, FORWARD_LAT, FORWARD_LON, "Tokio", "Tokio, Japonia",
    )


@pytest.mark.parametrize(
    "query", ["Tokio, Japonia", "Tokio, Japan", "Tokio, JP", "Tokio Japonia"]
)
def test_every_country_form_gives_the_same_label(query):
    status, _lat, _lon, short_label, display = lb.geocode_city_accepted(query, "pl")
    assert status == lb.GEOCODE_OK
    assert (short_label, display) == ("Tokio", "Tokio, Japonia"), query
    assert OracleNominatim.forwards()[0]["query"] == "Tokio"


def test_reverse_labels_are_unchanged():
    """Reverse nadal nazywa dzielnicę — formatter reverse nie został zmieniony."""
    assert lb.get_location_details_from_coords(
        FORWARD_LAT, FORWARD_LON, "pl", query="Tokio, Japonia"
    ) == ("Suginami", "Suginami, Japonia", lb.GEO_OK)


def test_card_label_is_not_the_country_alone():
    status, lat, lon, fallback_short, fallback_display = lb.geocode_city_accepted(
        "Tokio, Japonia", "pl"
    )
    assert status == lb.GEOCODE_OK
    short_label, display_location, geo_status = lb._resolve_location_labels(
        lat, lon, "pl",
        fallback_short_label=fallback_short,
        fallback_display_location=fallback_display,
        query="Tokio, Japonia",
    )
    assert geo_status == lb.GEO_OK
    assert short_label == "Tokio"
    assert display_location == "Tokio, Japonia"
    assert short_label != "Japonia"
    assert display_location != "Japonia"


def test_postcode_is_not_exposed_when_the_user_did_not_type_it():
    """Reverse ma postcode, ale query go nie zawiera — nie wolno go pokazać."""
    _short, display, _status = lb._resolve_location_labels(
        FORWARD_LAT, FORWARD_LON, "pl",
        fallback_short_label="Tokio", fallback_display_location="Tokio, Japonia",
        query="Tokio, Japonia",
    )
    assert "168-0063" not in display


# ============================================================================
# 5. ODRZUCENIE BŁĘDNEGO KRAJU (wymagany wynik 3)
# ============================================================================

def test_wrong_country_is_still_rejected():
    assert lb.geocode_city_accepted("Tokio, Polska", "pl") == (
        lb.GEOCODE_NO_MATCH, None, None, None, None
    )


def test_confirmed_admin_cannot_bypass_the_hard_country_filter():
    """Ten sam kandydat z `province=Tokio` odpada, gdy kraj się nie zgadza.

    Reguła etykiety działa DOPIERO po zaakceptowaniu kandydata, więc nie może
    uratować wyniku odrzuconego twardym filtrem `country_code` z fazy 1.
    """
    assert lb.geocode_city_accepted("Tokio, Polska", "pl") == (
        lb.GEOCODE_NO_MATCH, None, None, None, None
    )
    # Dowód, że odrzucenie pochodzi z filtra kraju, a nie z nowej reguły:
    status, chosen = lb._geocode_select_accepted(
        "Tokio", [oracle_forward()], "pl", []
    )
    assert status == lb.GEOCODE_NO_MATCH and chosen is None
    assert lb._geocode_confirmed_admin_label(
        "tokio", forward_address(), forward_raw(), ""
    ) == ("Tokio", "province"), "reguła sama w sobie działa — filtr kraju decyduje"


@pytest.mark.parametrize("query", ["Tokio, Japonia", "Tokio, Polska", "Tokio"])
def test_no_card_is_generated_for_a_rejected_query(query):
    """Kandydat jest japoński (country_code=jp), więc `Tokio, Polska` odpada."""

    def resolver(lat, lon, lang, q, fallback_short, fallback_display):
        return fallback_short, fallback_display, lb.GEO_OK

    handled, _sent, photos, _payloads = guest_handler_run(
        "?12 %s" % (query,), lb.geocode_city_labels_status, city_name=None,
        resolve_labels_fn=resolver,
    )
    assert handled is True
    if query == "Tokio, Polska":
        assert photos == []
    else:
        assert photos and photos[0][1] == "Tokio"


# ============================================================================
# 6. LICZBA REQUESTÓW (wymagany wynik 4)
# ============================================================================

def test_exactly_one_forward_and_one_reverse_for_a_successful_teraz():
    status, lat, lon, fallback_short, fallback_display = lb.geocode_city_accepted(
        "Tokio, Japonia", "pl"
    )
    assert status == lb.GEOCODE_OK
    assert len(OracleNominatim.forwards()) == 1
    assert OracleNominatim.reverses() == []
    lb._resolve_location_labels(
        lat, lon, "pl",
        fallback_short_label=fallback_short,
        fallback_display_location=fallback_display,
        query="Tokio, Japonia",
    )
    assert len(OracleNominatim.forwards()) == 1, "etykieta nie dokłada forwarda"
    assert len(OracleNominatim.reverses()) == 1, "dokładnie jeden reverse"
    assert OracleNominatim.forwards()[0]["extras"].get("country_codes") == "jp"


def test_rejected_query_stops_after_one_forward():
    lb.geocode_city_accepted("Tokio, Polska", "pl")
    assert len(OracleNominatim.calls) == 1
    assert OracleNominatim.calls[0]["kind"] == "forward"


def test_label_helpers_do_not_touch_the_geocoder():
    before = len(OracleNominatim.calls)
    lb._geocode_confirmed_admin_label("tokio", forward_address(), forward_raw(), "")
    lb._geocode_candidate_evidence_with_admin_label(
        "tokio", forward_address(), forward_raw(), ""
    )
    lb._select_short_location_label(
        forward_address(), query="Tokio, Japonia", lang="pl", confirmed_admin="Tokio"
    )
    assert len(OracleNominatim.calls) == before


# ============================================================================
# 7. GPS / PINEZKA / WEBAPP BEZ ZMIAN (wymagany wynik 5)
# ============================================================================

def test_gps_and_pin_keep_the_previous_label():
    assert lb._resolve_location_labels(REVERSE_LAT, REVERSE_LON, "pl") == (
        "Suginami", "Suginami, Japonia", lb.GEO_OK,
    )
    assert len(OracleNominatim.reverses()) == 1
    assert OracleNominatim.forwards() == []


def test_reverse_formatter_ignores_confirmed_admin_by_default():
    """Bez jawnego potwierdzenia formatter reverse zachowuje się jak dotąd."""
    assert lb._format_public_location_parts(
        ORACLE_REVERSE_ADDRESS, query=None, mode="city", lang="pl"
    ) == ("Suginami", "Suginami, Japonia")
    assert lb._format_public_location_parts(
        ORACLE_FORWARD_ADDRESS, query=None, mode="city", lang="pl"
    ) == ("Japonia", "Japonia"), "bez evidence nie ma awansu administracji"


def test_get_city_from_coords_is_unchanged():
    assert lb.get_city_from_coords(REVERSE_LAT, REVERSE_LON, "pl") == "Suginami"


def test_webapp_path_has_no_query_and_no_forward():
    """Ścieżka WebApp woła resolver bez query — nie dotyka forward ani evidence."""
    short, display, status = lb._resolve_location_labels(REVERSE_LAT, REVERSE_LON, "pl")
    assert (short, display, status) == ("Suginami", "Suginami, Japonia", lb.GEO_OK)
    assert OracleNominatim.forwards() == []


# ============================================================================
# 8. PIERWSZEŃSTWA (wymagane wyniki 6, 7, 8)
# ============================================================================

@pytest.mark.parametrize("field", ["city", "town", "village", "hamlet", "locality"])
def test_settlement_fields_keep_absolute_priority(field):
    address = dict(ORACLE_FORWARD_ADDRESS)
    address[field] = "Tokio"
    OracleNominatim.forward_response = [oracle_forward(address=address)]
    status, _lat, _lon, short_label, display = lb.geocode_city_accepted(
        "Tokio, Japonia", "pl"
    )
    assert status == lb.GEOCODE_OK
    assert short_label == "Tokio"
    assert display == "Tokio, Japonia"
    assert lb._geocode_confirmed_admin_label(
        "tokio", address, forward_raw(), ""
    ) == ("", ""), "reguła w ogóle nie startuje przy polu miejscowości"


def test_existing_municipality_county_state_keep_priority_over_confirmed_admin():
    """Nowa reguła wchodzi PO istniejącej pętli administracyjnej (adres PL)."""
    for field, expected in (
        ("municipality", "gmina Hel"),
        ("county", "powiat Hel"),
        ("state", "województwo Hel"),
    ):
        address = {"country": "Polska", "country_code": "pl", field: "Hel"}
        assert lb._select_short_location_label(
            address, query="Hel, Polska", lang="pl", confirmed_admin="Hel"
        ) == expected, field


def test_confirmed_admin_has_no_effect_on_non_polish_admin_prefixes():
    """Dla adresu innego niż polski prefiksy i tak nie są dodawane."""
    for field in ("municipality", "county", "state"):
        address = dict(ORACLE_FORWARD_ADDRESS)
        address[field] = "Tokio"
        assert lb._select_short_location_label(
            address, query="Tokio, Japonia", lang="pl", confirmed_admin="Tokio"
        ) == "Tokio", field


def test_confirmed_admin_beats_postcode_and_country_only():
    address = dict(ORACLE_FORWARD_ADDRESS)
    address["postcode"] = "100-0001"
    assert lb._select_short_location_label(
        address, query="Tokio, Japonia", lang="pl", confirmed_admin="Tokio"
    ) == "Tokio"
    assert lb._select_short_location_label(
        address, query="Tokio, Japonia", lang="pl"
    ) == "100-0001", "bez potwierdzenia zostaje dotychczasowy wynik (postcode)"


def test_display_only_candidate_keeps_the_country_label():
    """Wymagany wynik 6: `display` nie może uruchamiać awansu."""
    raw = oracle_forward(namedetails=None)
    raw.raw.pop("namedetails", None)
    OracleNominatim.forward_response = [raw]
    status, _lat, _lon, short_label, display = lb.geocode_city_accepted(
        "Tokio, Japonia", "pl"
    )
    assert status == lb.GEOCODE_OK
    assert (short_label, display) == ("Japonia", "Japonia")


def test_class_place_candidate_does_not_start_the_new_rule():
    """Wymagany wynik 7: `class=place` nie uruchamia reguły."""
    OracleNominatim.forward_response = [oracle_forward(cls="place", typ="province")]
    status, _lat, _lon, short_label, display = lb.geocode_city_accepted(
        "Tokio, Japonia", "pl"
    )
    assert status == lb.GEOCODE_OK
    assert (short_label, display) == ("Japonia", "Japonia")


def test_greater_london_style_admin_is_untouched():
    """Istniejący przypadek display-only (Greater London) bez zmian."""
    admin = FakeResult(
        {"county": "Greater London", "state": "England",
         "country": "United Kingdom", "country_code": "gb"},
        cls="boundary", typ="administrative", name="Greater London",
        display="Greater London, England, United Kingdom",
    )
    OracleNominatim.forward_response = [admin]
    status, _lat, _lon, short_label, _display = lb.geocode_city_accepted(
        "London", "en"
    )
    assert status == lb.GEOCODE_OK
    assert short_label == "Greater London", "county ma pierwszeństwo jak dotąd"
    assert lb._geocode_confirmed_admin_label(
        "london", lb._nominatim_address_components(admin.raw), admin.raw, ""
    ) == ("", "")


def test_polish_boundary_candidate_keeps_its_existing_label():
    """Polska granica administracyjna z `municipality` — wynik jak przed fazą 2."""
    boundary = FakeResult(
        {"municipality": "Hel", "country": "Polska", "country_code": "pl"},
        cls="boundary", typ="administrative", name="Hel",
        display="Hel, powiat pucki, wojewodztwo pomorskie, Polska",
    )
    OracleNominatim.forward_response = [boundary]
    status, _lat, _lon, short_label, _display = lb.geocode_city_accepted("Hel", "pl")
    assert status == lb.GEOCODE_OK
    assert short_label == "gmina Hel"


# ============================================================================
# 9. KONTRAKTY PUBLICZNE (wymagany wynik 9)
# ============================================================================

def test_public_contracts_keep_their_arity():
    assert len(lb.geocode_city_accepted("Tokio, Japonia", "pl")) == 5
    assert len(lb.geocode_city_labels_status("Tokio, Japonia", "pl")) == 5
    assert len(lb.geocode_city_details_status("Tokio, Japonia", "pl")) == 4
    assert len(lb._geocode_select_accepted("Tokio", [oracle_forward()])) == 2
    assert len(lb._geocode_candidate_evidence(
        "tokio", forward_address(), forward_raw(), ""
    )) == 2
    assert lb._geocode_matched_place_name(
        "tokio", forward_address(), forward_raw(), ""
    ) == "tokio"
    assert len(lb._resolve_location_labels(REVERSE_LAT, REVERSE_LON, "pl")) == 3
    assert len(lb._format_public_location_parts(ORACLE_REVERSE_ADDRESS)) == 2
    assert len(lb._geocode_confirmed_admin_label(
        "tokio", forward_address(), forward_raw(), ""
    )) == 2


def test_evidence_namedtuple_has_the_documented_fields():
    assert lb.GeocodeEvidence._fields == (
        "matched", "strength", "source", "field", "raw_value",
        "admin_label", "admin_field",
    )


def test_labels_status_adapter_carries_the_fixed_label_to_guests():
    lat, lon, short_label, display, status = lb.geocode_city_labels_status(
        "Tokio, Japonia", "pl"
    )
    assert status == lb.GEOCODE_OK
    assert (lat, lon) == (FORWARD_LAT, FORWARD_LON)
    assert (short_label, display) == ("Tokio", "Tokio, Japonia")


# ============================================================================
# 10. TRYB GOŚCIA — te same etykiety co /teraz
# ============================================================================

@pytest.mark.parametrize(
    "text", ["?12 Tokio, Japonia", "?n Tokio, JP", ".n Tokio, Japan",
             "@PogodaWorldBot Tokio, Japonia"]
)
def test_guest_shortcuts_use_the_same_fixed_label(text):
    def resolver(lat, lon, lang, query, fallback_short, fallback_display):
        return fallback_short, fallback_display, lb.GEO_OK

    handled, _sent, photos, payloads = guest_handler_run(
        text, lb.geocode_city_labels_status, city_name=None,
        resolve_labels_fn=resolver,
    )
    assert handled is True
    assert photos, "zweryfikowany kandydat musi dać kartę"
    assert photos[0][1] == "Tokio", "tytuł karty"
    assert photos[0][2] == "Tokio, Japonia", "opis karty"
    assert payloads[0][:2] == (FORWARD_LAT, FORWARD_LON)
    assert len(OracleNominatim.forwards()) == 1


# ============================================================================
# 11. OFFLINE, PUŁAPKI I PYTHON 3.8 (wymagany wynik 11)
# ============================================================================

def test_raw_location_address_never_reaches_the_labels():
    """`location.address` (POISON) nie jest źródłem żadnej etykiety."""
    candidate = oracle_forward()
    assert candidate.address.startswith("RAW ")
    _status, _lat, _lon, short_label, display = lb.geocode_city_accepted(
        "Tokio, Japonia", "pl"
    )
    for label in (short_label, display):
        assert "RAW" not in label and "Koscielna" not in label


def test_labels_never_contain_the_user_query_verbatim_when_unconfirmed():
    """Bez potwierdzenia etykieta nie jest tekstem użytkownika."""
    address = dict(ORACLE_FORWARD_ADDRESS)
    address["province"] = "Tokyo"
    OracleNominatim.forward_response = [oracle_forward(address=address)]
    _status, _lat, _lon, short_label, _display = lb.geocode_city_accepted(
        "Tokio, Japonia", "pl"
    )
    assert short_label == "Japonia"


def test_no_socket_is_needed(monkeypatch):
    """pytest-socket już blokuje sieć; tu dodatkowo pilnujemy braku wywołań."""
    lb.geocode_city_accepted("Tokio, Japonia", "pl")
    assert all(call["kind"] in ("forward", "reverse") for call in OracleNominatim.calls)
    assert len(OracleNominatim.calls) == 1


def test_changed_module_is_python38_compatible():
    """Produkcja ma 3.8.10: bez removeprefix/removesuffix, match i PEP 585/604."""
    path = os.path.join(BASE_DIR, "location_bot.py")
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()
    for forbidden in (".removeprefix(", ".removesuffix("):
        assert forbidden not in source, forbidden
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if hasattr(ast, "Match") and isinstance(node, ast.Match):
            pytest.fail("match/case wymaga Pythona 3.10")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in list(node.args.args) + list(node.args.kwonlyargs):
                annotation = arg.annotation
                if isinstance(annotation, ast.Subscript):
                    name = getattr(annotation.value, "id", None)
                    assert name not in {"list", "dict", "tuple", "set"}, (
                        "PEP 585 w adnotacji: %s" % (name,)
                    )


def test_new_code_does_not_import_anything_new_third_party():
    path = os.path.join(BASE_DIR, "location_bot.py")
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "collections" in imported, "namedtuple jest ze standard library"
    for forbidden in ("babel", "pycountry"):
        assert forbidden not in imported, forbidden


# ============================================================================
# 12. ZNANE OGRANICZENIE (D13 odłożone) — payload jsonv2 używa `category`
# ============================================================================

def test_jsonv2_payload_with_category_is_a_known_limitation():
    """D13 NIE jest zaimplementowane: geopy 2.5.0 wysyła format=json (`class`).

    Test charakterystyczny, nie docelowy: gdy obsługa `category` zostanie
    dodana (osobne zadanie hardeningowe), ten test trzeba odwrócić.
    """
    candidate = oracle_forward()
    candidate.raw.pop("class")
    candidate.raw["category"] = "boundary"
    assert lb._geocode_candidate_is_safe(candidate.raw) is False
    OracleNominatim.forward_response = [candidate]
    assert lb.geocode_city_accepted("Tokio, Japonia", "pl") == (
        lb.GEOCODE_NO_MATCH, None, None, None, None
    )


# ============================================================================
# 13. POMOCNICZE TYPY — spójność atrap
# ============================================================================

def test_oracle_fakes_expose_geopy_location_interface():
    for candidate in (oracle_forward(), oracle_reverse()):
        assert isinstance(candidate.raw, dict)
        assert isinstance(candidate.latitude, float)
        assert isinstance(candidate.longitude, float)
        assert hasattr(candidate, "address")
    assert isinstance(OracleNominatim().reverse("0, 0"), FakeResult)
    assert isinstance(OracleNominatim().geocode("Tokio"), list)
