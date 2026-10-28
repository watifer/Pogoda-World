# -*- coding: utf-8 -*-
"""Ranking dowodów w DEDUPLIKACJI kandydatów (_geocode_select_accepted).

Zakres (decyzje D16/D17):

* **D17 = TAK** — dla kandydatów o tej samej tożsamości (nazwa + kraj) leżących
  w promieniu ``_GEOCODE_SAME_PLACE_KM`` obowiązuje jawny ranking
  ``place > localized > display``: mocniejszy dowód może ZASTĄPIĆ słabszego,
  przy remisie zostaje pierwszy.
* **D16 = NIE** — końcowa selekcja RÓŻNYCH lokalizacji zostaje przy
  ``strong[0]``, czyli przy dotychczasowej kolejności Nominatima. Żadnego
  globalnego rankingu.

Fixtury Hela to LITERALNE payloady Oracle (curl z produkcji:
``q=Hel&countrycodes=pl&format=json&addressdetails=1&namedetails=1&limit=7&
accept-language=pl``).

Fixtura Ełku jest SYNTETYCZNA (nie pochodzi z Oracle) — pełni rolę wartownika
„pojedynczy kandydat z polem miejscowości”, nie jest odwzorowaniem realnej
odpowiedzi Nominatima.

Wszystkie testy są offline (``pytest.ini`` = ``--disable-socket``).
"""
import pytest

import guest_bot_handler as gbh
import location_bot as lb
from test_geocode_validation import FakeResult

# ============================================================================
# LITERALNE PAYLOADY ORACLE — Hel (q=Hel, countrycodes=pl, 2026-10)
# ============================================================================

HEL_ADMIN_LOCALIZED = {
    "place_id": 174425461,
    "licence": "Data © OpenStreetMap contributors, ODbL 1.0. http://osm.org/copyright",
    "osm_type": "relation", "osm_id": 3175144,
    "lat": "54.6237094", "lon": "18.7275009",
    "class": "boundary", "type": "administrative",
    "place_rank": 16, "importance": 0.48514892079054583,
    "addresstype": "administrative", "name": "Hel",
    "display_name": "Hel, powiat pucki, województwo pomorskie, 84-150, Polska",
    "address": {
        "administrative": "Hel", "county": "powiat pucki",
        "state": "województwo pomorskie", "ISO3166-2-lvl4": "PL-22",
        "postcode": "84-150", "country": "Polska", "country_code": "pl",
    },
    "namedetails": {"name": "Hel", "name:de": "Hela", "name:pl": "Hel", "name:csb": "Hél"},
    "boundingbox": ["54.5669034", "54.6808056", "18.6215202", "18.8671011"],
}

HEL_TOWN_PLACE = {
    "place_id": 174885330,
    "licence": "Data © OpenStreetMap contributors, ODbL 1.0. http://osm.org/copyright",
    "osm_type": "relation", "osm_id": 1555429,
    "lat": "54.6066432", "lon": "18.8027076",
    "class": "boundary", "type": "administrative",
    "place_rank": 14, "importance": 0.48514892079054583,
    "addresstype": "town", "name": "Hel",
    "display_name": "Hel, powiat pucki, województwo pomorskie, 84-150, Polska",
    "address": {
        "town": "Hel", "county": "powiat pucki",
        "state": "województwo pomorskie", "ISO3166-2-lvl4": "PL-22",
        "postcode": "84-150", "country": "Polska", "country_code": "pl",
    },
    "namedetails": {
        "name": "Hel", "name:de": "Hela", "name:pl": "Hel",
        "name:ru": "Хел", "name:sr": "Хел", "name:uk": "Хел",
    },
    "boundingbox": ["54.5669034", "54.6808056", "18.6215202", "18.8671011"],
}

# Reverse dla WŁAŚCIWYCH współrzędnych zwycięskiego kandydata town
# (54.6066432, 18.8027076) — literalna odpowiedź Oracle z produkcji.
# Nominatim zwraca tu obiekt o class=building / type=yes, ale address MA pole
# miejscowości (town=Hel) — i to wystarcza do etykiety 'Hel'.
# UWAGA: ``lat``/``lon`` poniżej to współrzędne ZAPYTANIA; współrzędne samego
# obiektu z odpowiedzi nie zostały zanotowane. Nie są używane do etykiet
# (liczą się lat/lon przekazane do resolvera), więc nie wpływają na asercje.
HEL_REVERSE_TOWN = {
    "class": "building",
    "type": "yes",
    "addresstype": "building",
    "lat": "54.6066432",
    "lon": "18.8027076",
    "address": {
        "town": "Hel",
        "county": "powiat pucki",
        "state": "województwo pomorskie",
        "country": "Polska",
        "country_code": "pl",
    },
}

# Reverse w punkcie kandydata administrative (54.6237094, 18.7275009) — stan
# sprzed poprawki, gdy produkcyjnie wybierany był NIEWŁAŚCIWY kandydat.
# Zostaje jako dowód przyczyny: reverse NIE MA pola miejscowości, a forward
# fallback też spadał do county, więc karta pokazywała „powiat pucki”.
HEL_REVERSE_ADMIN_POINT = {
    "place_id": 172907957,
    "licence": "Data © OpenStreetMap contributors, ODbL 1.0. http://osm.org/copyright",
    "osm_type": "way", "osm_id": 234810198,
    "lat": "54.6268313", "lon": "18.7224011",
    "class": "military", "type": "training_area",
    "place_rank": 30, "importance": 5.727244983592e-05,
    "addresstype": "military",
    "name": "Strefa 5 okresowo zamykana dla żeglugi i rybołówstwa",
    "display_name": (
        "Strefa 5 okresowo zamykana dla żeglugi i rybołówstwa, Hel, "
        "powiat pucki, województwo pomorskie, 84-150, Polska"
    ),
    "address": {
        "military": "Strefa 5 okresowo zamykana dla żeglugi i rybołówstwa",
        "administrative": "Hel", "county": "powiat pucki",
        "state": "województwo pomorskie", "ISO3166-2-lvl4": "PL-22",
        "postcode": "84-150", "country": "Polska", "country_code": "pl",
    },
    "boundingbox": ["54.6018386", "54.6668314", "18.6823325", "18.7623415"],
}

HEL_TOWN_LAT, HEL_TOWN_LON = 54.6066432, 18.8027076
HEL_ADMIN_LAT, HEL_ADMIN_LON = 54.6237094, 18.7275009


class HelResult(object):
    """Dokładnie to, co geopy wystawia w Location, z literalnym payloadem."""

    def __init__(self, raw):
        self.raw = raw
        self.latitude = raw["lat"]
        self.longitude = raw["lon"]
        self.address = "POISON location.address"


def hel_admin():
    return HelResult(dict(HEL_ADMIN_LOCALIZED))


def hel_town():
    return HelResult(dict(HEL_TOWN_PLACE))


class HelNominatim(object):
    """Atrapa: forward = dwa kandydaci Oracle, reverse = pomiar Oracle."""

    calls = []

    def __init__(self, *args, **kwargs):
        pass

    def geocode(self, query, exactly_one=True, language=None, limit=None,
                addressdetails=None, **kwargs):
        HelNominatim.calls.append((
            "forward", query,
            kwargs.get("country_codes") or kwargs.get("countrycodes"),
        ))
        return [hel_admin(), hel_town()]

    def reverse(self, query, language=None, zoom=None, **kwargs):
        HelNominatim.calls.append(("reverse", query))
        return HelResult(dict(HEL_REVERSE_TOWN))

    @classmethod
    def reset(cls):
        cls.calls = []


@pytest.fixture
def hel_geocoder(monkeypatch):
    HelNominatim.reset()
    monkeypatch.setattr(lb, "Nominatim", HelNominatim)
    lb._GEO_DETAILS_CACHE.clear()
    gbh._GEO_CACHE.clear()
    gbh._CITY_CACHE.clear()
    yield HelNominatim
    HelNominatim.reset()


# ============================================================================
# Fixtury syntetyczne do testów rankingu (D17 / D16)
# ============================================================================
# Dwa RÓŻNE punkty leżące w progu (≈0,64 km od siebie). Nie mogą mieć
# współrzędnych identycznych co do znaku: odległość 0.0 km jest falsy, a
# ``_geocode_same_place`` liczy ``(odległość or inf) <= próg``, więc zerowa
# odległość zamienia się w nieskończoność (patrz test charakterystyczny niżej).
SPOT_A = (54.6000, 18.7000)
SPOT_B = (54.6050, 18.7050)
FAR_LAT = 54.8                             # ≈ 22 km na północ — POZA progiem 10 km


def _place_candidate(name="Alpha", lat=SPOT_B[0], lon=SPOT_B[1],
                     country="X", cc="xx"):
    """Dowód ``place``: nazwa w polu miejscowości."""
    return FakeResult(
        {"city": name, "state": "Region", "country": country, "country_code": cc},
        cls="place", typ="town", lat=lat, lon=lon, name=name,
        display="%s, Region, %s" % (name, country),
    )


def _localized_candidate(name="Alpha", lat=SPOT_A[0], lon=SPOT_A[1],
                         country="X", cc="xx"):
    """Dowód ``localized``: brak pól miejscowości, nazwa tylko w namedetails."""
    return FakeResult(
        {"state": "Region", "country": country, "country_code": cc},
        cls="boundary", typ="administrative", lat=lat, lon=lon, name=name,
        namedetails={"name:pl": name},
        display="%s, Region, %s" % (name, country),
    )


def _display_candidate(name="Alpha", lat=SPOT_A[0], lon=SPOT_A[1],
                       country="X", cc="xx"):
    """Dowód ``display``: nazwa wyłącznie z name/display_name."""
    return FakeResult(
        {"county": "Region", "country": country, "country_code": cc},
        cls="boundary", typ="administrative", lat=lat, lon=lon, name=name,
        display="%s, Region, %s" % (name, country),
    )


def _strength_of(candidate, query="Alpha", postcode=""):
    query_name, _contexts, default_postcode = lb._geocode_query_context(query)
    address = lb._nominatim_address_components(candidate.raw)
    return lb._geocode_candidate_evidence(
        query_name, address, candidate.raw, postcode or default_postcode)[1]


# ============================================================================
# 0. Stałe i kontrakt rankingu
# ============================================================================
def test_rank_constant_is_explicit_place_over_localized_over_display():
    assert lb._GEOCODE_EVIDENCE_RANK[lb._GEOCODE_EVIDENCE_PLACE] > \
        lb._GEOCODE_EVIDENCE_RANK[lb._GEOCODE_EVIDENCE_LOCALIZED]
    assert lb._GEOCODE_EVIDENCE_RANK[lb._GEOCODE_EVIDENCE_LOCALIZED] > \
        lb._GEOCODE_EVIDENCE_RANK[lb._GEOCODE_EVIDENCE_DISPLAY]


def test_rank_of_unknown_or_missing_strength_is_the_weakest():
    assert lb._geocode_evidence_rank(None) == 0
    assert lb._geocode_evidence_rank("cos-innego") == 0


def test_fixtures_have_the_declared_strength():
    """Gwarancja, że testy 1–6 naprawdę mieszają siłami dowodu."""
    assert _strength_of(_place_candidate()) == lb._GEOCODE_EVIDENCE_PLACE
    assert _strength_of(_localized_candidate()) == lb._GEOCODE_EVIDENCE_LOCALIZED
    assert _strength_of(_display_candidate()) == lb._GEOCODE_EVIDENCE_DISPLAY


def test_hel_oracle_fixtures_have_the_declared_strength():
    assert _strength_of(hel_admin(), query="Hel") == lb._GEOCODE_EVIDENCE_LOCALIZED
    assert _strength_of(hel_town(), query="Hel") == lb._GEOCODE_EVIDENCE_PLACE


def test_hel_oracle_town_is_the_one_with_the_town_field():
    assert hel_town().raw["address"]["town"] == "Hel"
    assert "town" not in hel_admin().raw["address"]
    assert hel_admin().raw["address"]["administrative"] == "Hel"


def test_hel_candidates_are_within_the_same_place_threshold():
    """Warunek konieczny: bez tego dedup w ogóle by nie zadziałał."""
    distance = lb._geocode_distance_km(
        HEL_ADMIN_LAT, HEL_ADMIN_LON, HEL_TOWN_LAT, HEL_TOWN_LON)
    assert distance < lb._GEOCODE_SAME_PLACE_KM
    assert lb._geocode_same_place(hel_admin(), hel_town())


def test_hel_oracle_reverse_for_the_town_point_has_town_hel():
    """Literalny reverse dla 54.6066432, 18.8027076 ma address.town=Hel."""
    assert HEL_REVERSE_TOWN["class"] == "building"
    assert HEL_REVERSE_TOWN["type"] == "yes"
    assert HEL_REVERSE_TOWN["addresstype"] == "building"
    address = lb._nominatim_address_components(HEL_REVERSE_TOWN)
    assert address["town"] == "Hel"
    assert lb._geocode_place_values(address) == ["hel"]


def test_hel_oracle_reverse_class_building_still_yields_the_hel_label():
    """class=building / type=yes nie przeszkadza: liczy się address.town.

    Etykiety biorą się WYŁĄCZNIE ze strukturalnych pól address — klasa
    obiektu nie jest żadnym filtrem w reverse.
    """
    address = lb._nominatim_address_components(HEL_REVERSE_TOWN)
    assert lb._select_short_location_label(address, query="Hel, Polska", lang="pl") == "Hel"
    assert lb._format_public_location_parts(
        address, query="Hel, Polska", mode="city", lang="pl"
    ) == ("Hel", "Hel, powiat pucki, województwo pomorskie, Polska")


def test_hel_oracle_reverse_at_the_admin_point_has_no_settlement_field():
    """Dowód przyczyny produkcyjnej: reverse w punkcie kandydata
    administrative (wybieranego przed poprawką) NIE MA pola miejscowości ani
    municipality — stąd etykieta „powiat pucki”."""
    address = lb._nominatim_address_components(HEL_REVERSE_ADMIN_POINT)
    assert lb._geocode_place_values(address) == []
    assert "municipality" not in address
    assert lb._select_short_location_label(address, query="Hel, Polska", lang="pl") == (
        "powiat pucki")


def test_known_limitation_zero_distance_is_not_the_same_place():
    """Znane ograniczenie POZA zakresem D16/D17 — test charakterystyczny.

    ``_geocode_same_place`` liczy ``(_geocode_distance_km(...) or inf) <= próg``.
    Odległość ``0.0`` jest falsy, więc zamienia się w ``inf`` i dwa kandydaty o
    współrzędnych IDENTYCZNYCH co do znaku NIE są uznane za to samo miejsce.
    Produkcji to nie dotyka (Nominatim nie zwraca dwóch obiektów w jednym
    punkcie), ale każda zmiana tego zachowania musi być świadomą decyzją.
    """
    assert lb._geocode_distance_km(54.6, 18.7, 54.6, 18.7) == 0.0
    first = _place_candidate(lat=54.6, lon=18.7)
    second = _place_candidate(lat=54.6, lon=18.7)
    assert lb._geocode_same_place(first, second) is False
    # ...a dwa punkty w progu, ale nie identyczne — już tak.
    assert lb._geocode_same_place(_place_candidate(), _localized_candidate()) is True


# ============================================================================
# 1–4. Ranking wewnątrz deduplikacji (D17 = TAK)
# ============================================================================
def test_1_localized_first_place_second_same_place_wins_place():
    """Hel w miniaturze: localized (pierwszy) zostaje zastąpiony przez place."""
    status, chosen = lb._geocode_select_accepted(
        "Alpha", [_localized_candidate(), _place_candidate()])
    assert status == lb.GEOCODE_OK
    assert chosen.raw["address"]["city"] == "Alpha"
    assert _strength_of(chosen) == lb._GEOCODE_EVIDENCE_PLACE


def test_2_display_first_place_second_same_place_wins_place():
    status, chosen = lb._geocode_select_accepted(
        "Alpha", [_display_candidate(), _place_candidate()])
    assert status == lb.GEOCODE_OK
    assert _strength_of(chosen) == lb._GEOCODE_EVIDENCE_PLACE


def test_3_place_first_localized_second_same_place_stays_place():
    """Mocniejszy jest pierwszy — słabszy nie może go zastąpić."""
    first, second = _place_candidate(), _localized_candidate()
    status, chosen = lb._geocode_select_accepted("Alpha", [first, second])
    assert status == lb.GEOCODE_OK
    assert chosen is first


def test_4_tie_keeps_the_first_candidate():
    first, second = _place_candidate(), _place_candidate()
    status, chosen = lb._geocode_select_accepted("Alpha", [first, second])
    assert status == lb.GEOCODE_OK
    assert chosen is first


def test_4b_tie_on_weak_evidence_keeps_the_first_candidate():
    """Remis na `display` też zostawia pierwszy (bez wpływu na UNCERTAIN)."""
    first = _display_candidate()
    second = _display_candidate(lat=SPOT_B[0], lon=SPOT_B[1])
    status, chosen = lb._geocode_select_accepted("Alpha", [first, second])
    assert status == lb.GEOCODE_OK
    assert chosen is first


# ============================================================================
# 5–6. Granice deduplikacji i brak globalnego rankingu (D16 = NIE)
# ============================================================================
def test_5_same_name_but_farther_than_the_threshold_is_not_deduplicated():
    """Poza progiem 10 km to NIE jest to samo miejsce — oba kandydaci żyją.

    Dowód obserwowalny: dwa słabe (display) dowody dają UNCERTAIN zamiast OK.
    Gdyby dedup zadziałał, lista miałaby jeden element i status byłby OK.
    """
    assert lb._geocode_distance_km(
        SPOT_A[0], SPOT_A[1], FAR_LAT, SPOT_A[1]) > lb._GEOCODE_SAME_PLACE_KM
    status, chosen = lb._geocode_select_accepted(
        "Alpha", [_display_candidate(),
                  _display_candidate(lat=FAR_LAT, lon=SPOT_A[1])])
    assert status == lb.GEOCODE_UNCERTAIN
    assert chosen is None


def test_5b_same_name_far_apart_both_strong_keeps_nominatim_order():
    first = _place_candidate()
    status, chosen = lb._geocode_select_accepted(
        "Alpha", [first, _place_candidate(lat=FAR_LAT, lon=SPOT_B[1])])
    assert status == lb.GEOCODE_OK
    assert chosen is first


def test_6_different_locations_keep_nominatim_order_not_the_evidence_ranking():
    """D16: RÓŻNE lokalizacje — pierwszy wygrywa, choć ma SŁABSZY dowód.

    Gdyby istniał globalny ranking, wygrałby drugi (place w kraju yy).
    """
    first = _localized_candidate(cc="xx")            # localized
    second = _place_candidate(cc="yy", lat=FAR_LAT, lon=SPOT_A[1])  # place, inny kraj
    status, chosen = lb._geocode_select_accepted("Alpha", [first, second])
    assert status == lb.GEOCODE_OK
    assert chosen is first
    assert _strength_of(chosen) == lb._GEOCODE_EVIDENCE_LOCALIZED


def test_6b_different_locations_keep_nominatim_order_for_equal_strength():
    first = _place_candidate(cc="xx")
    second = _place_candidate(cc="yy", lat=FAR_LAT, lon=SPOT_A[1])
    status, chosen = lb._geocode_select_accepted("Alpha", [first, second])
    assert status == lb.GEOCODE_OK
    assert chosen is first


# ============================================================================
# 7–8. Literalny Hel z Oracle
# ============================================================================
@pytest.mark.parametrize("query", ["Hel", "Hel, Polska", "Hel,PL"])
def test_7_hel_oracle_localized_admin_first_place_town_second_wins_town(query):
    """Kolejność Nominatima z produkcji: administrative/localized, potem
    town/place. Zwycięzcą musi być town=Hel."""
    core, contexts, country_code = lb._geocode_query_parse(query)
    status, chosen = lb._geocode_select_accepted(
        core, [hel_admin(), hel_town()], country_code, contexts)
    assert status == lb.GEOCODE_OK
    assert chosen.raw["addresstype"] == "town"
    assert chosen.raw["address"]["town"] == "Hel"
    assert float(chosen.latitude) == HEL_TOWN_LAT
    assert float(chosen.longitude) == HEL_TOWN_LON


@pytest.mark.parametrize("query", ["Hel", "Hel, Polska", "Hel,PL"])
def test_7b_hel_query_reaches_the_geocoder_without_the_country_suffix(query):
    """Kraj idzie jako countrycodes, nigdy w treści q."""
    core, _contexts, _cc = lb._geocode_query_parse(query)
    assert lb._geocode_geocoder_query(core, _contexts) == "Hel"


@pytest.mark.parametrize("query", ["Hel", "Hel, Polska", "Hel,PL"])
def test_8_hel_card_label_is_hel(hel_geocoder, query):
    status, lat, lon, short_label, display = lb.geocode_city_accepted(query, "pl")
    assert status == lb.GEOCODE_OK
    assert (float(lat), float(lon)) == (HEL_TOWN_LAT, HEL_TOWN_LON)
    assert short_label == "Hel"
    assert display == "Hel, powiat pucki, województwo pomorskie, Polska"


@pytest.mark.parametrize("query", ["Hel", "Hel, Polska", "Hel,PL"])
def test_8b_hel_resolved_card_title_is_hel(hel_geocoder, query):
    status, lat, lon, fallback_short, fallback_display = lb.geocode_city_accepted(
        query, "pl")
    assert status == lb.GEOCODE_OK
    short, display, _reverse_status = lb._resolve_location_labels(
        lat, lon, "pl",
        fallback_short_label=fallback_short,
        fallback_display_location=fallback_display,
        query=query,
    )
    assert (short, display) == ("Hel", "Hel, powiat pucki, województwo pomorskie, Polska")
    # Reverse (town=Hel) pasuje do rdzenia zapytania, więc wygrywa arbitraż —
    # karta i tak pokazuje 'Hel', bo forward fallback niesie tę samą nazwę.
    assert lb._reverse_label_matches_query(short, query) is True


def test_8b_reverse_town_point_is_queried_with_the_town_coordinates(hel_geocoder):
    """Reverse leci we współrzędne kandydata town, nie administrative."""
    status, lat, lon, fallback_short, fallback_display = lb.geocode_city_accepted(
        "Hel, Polska", "pl")
    lb._resolve_location_labels(
        lat, lon, "pl",
        fallback_short_label=fallback_short,
        fallback_display_location=fallback_display,
        query="Hel, Polska",
    )
    reverses = [call for call in HelNominatim.calls if call[0] == "reverse"]
    assert len(reverses) == 1
    assert reverses[0][1] == "54.6066432, 18.8027076"


def test_8c_hel_uses_exactly_one_forward_and_one_reverse(hel_geocoder):
    """Dokładnie 1 forward + 1 reverse: reverse leci w _resolve_location_labels,
    nie w geocode_city_accepted (to jest forward-only)."""
    status, lat, lon, fallback_short, fallback_display = lb.geocode_city_accepted(
        "Hel, Polska", "pl")
    assert status == lb.GEOCODE_OK
    lb._resolve_location_labels(
        lat, lon, "pl",
        fallback_short_label=fallback_short,
        fallback_display_location=fallback_display,
        query="Hel, Polska",
    )
    forwards = [call for call in HelNominatim.calls if call[0] == "forward"]
    reverses = [call for call in HelNominatim.calls if call[0] == "reverse"]
    assert len(forwards) == 1
    assert len(reverses) == 1
    assert forwards[0][1] == "Hel"
    assert forwards[0][2] == "pl"


# ============================================================================
# 9. Brak regresji: Tokio, Ełk, GPS/pinezka
# ============================================================================
TOKYO_FORWARD = {
    "class": "boundary", "type": "administrative", "addresstype": "province",
    "name": "Tokio", "display_name": "Tokio, Japonia",
    "lat": "35.6768601", "lon": "139.7638947",
    "address": {
        "province": "Tokio", "ISO3166-2-lvl4": "JP-13",
        "country": "Japonia", "country_code": "jp",
    },
    "namedetails": {"name": "東京都", "name:pl": "Tokio", "ref": "130001"},
}
TOKYO_REVERSE = {
    "class": "highway", "type": "residential", "name": "",
    "display_name": "Suginami, Tokio, Japonia",
    "lat": "35.6761772", "lon": "139.6502952",
    "address": {
        "neighbourhood": "Honancho", "quarter": "Honan",
        "city": "Suginami", "ISO3166-2-lvl4": "JP-13",
        "postcode": "166-0002", "country": "Japonia", "country_code": "jp",
    },
}
# UWAGA: kształt SYNTETYCZNY (nie payload Oracle) — wartownik dla pojedynczego
# kandydata z polem miejscowości.
ELK_FORWARD = {
    "class": "boundary", "type": "administrative", "addresstype": "town",
    "name": "Ełk", "display_name": "Ełk, powiat ełcki, województwo warmińsko-mazurskie, Polska",
    "lat": "53.828", "lon": "22.3647",
    "address": {
        "town": "Ełk", "county": "powiat ełcki",
        "state": "województwo warmińsko-mazurskie",
        "country": "Polska", "country_code": "pl",
    },
    "namedetails": {"name": "Ełk", "name:pl": "Ełk"},
}


class SingleNominatim(object):
    """Atrapa z jednym kandydatem forward i jednym wynikiem reverse."""

    calls = []

    def __init__(self, *args, **kwargs):
        pass

    def geocode(self, query, **kwargs):
        SingleNominatim.calls.append(("forward", query))
        return [HelResult(dict(SingleNominatim.forward_raw))]

    def reverse(self, query, **kwargs):
        SingleNominatim.calls.append(("reverse", query))
        return HelResult(dict(SingleNominatim.reverse_raw))

    forward_raw = TOKYO_FORWARD
    reverse_raw = TOKYO_REVERSE

    @classmethod
    def reset(cls):
        cls.calls = []


@pytest.fixture
def single_geocoder(monkeypatch):
    SingleNominatim.reset()
    monkeypatch.setattr(lb, "Nominatim", SingleNominatim)
    lb._GEO_DETAILS_CACHE.clear()
    gbh._GEO_CACHE.clear()
    gbh._CITY_CACHE.clear()
    yield SingleNominatim
    SingleNominatim.reset()


def test_9_tokyo_is_unchanged(single_geocoder):
    status, lat, lon, short_label, display = lb.geocode_city_accepted(
        "Tokio, Japonia", "pl")
    assert status == lb.GEOCODE_OK
    assert (float(lat), float(lon)) == (35.6768601, 139.7638947)
    assert (short_label, display) == ("Tokio", "Tokio, Japonia")
    short, resolved_display, _ = lb._resolve_location_labels(
        lat, lon, "pl",
        fallback_short_label=short_label,
        fallback_display_location=display,
        query="Tokio, Japonia",
    )
    assert (short, resolved_display) == ("Tokio", "Tokio, Japonia")
    assert len([c for c in SingleNominatim.calls if c[0] == "forward"]) == 1
    assert len([c for c in SingleNominatim.calls if c[0] == "reverse"]) == 1


def test_9b_elk_single_settlement_candidate_is_unchanged(single_geocoder):
    SingleNominatim.forward_raw = ELK_FORWARD
    status, lat, lon, short_label, display = lb.geocode_city_accepted("Ełk", "pl")
    assert status == lb.GEOCODE_OK
    assert short_label == "Ełk"
    assert display == "Ełk, powiat ełcki, województwo warmińsko-mazurskie, Polska"


def test_9c_gps_and_pin_labels_are_unchanged(single_geocoder):
    """Bez query: czysty reverse, zero forward, etykiety reverse bez zmian."""
    lb._GEO_DETAILS_CACHE.clear()
    short, display, _status = lb._resolve_location_labels(
        35.6761772, 139.6502952, "pl")
    assert (short, display) == ("Suginami", "Suginami, Japonia")
    assert [c for c in SingleNominatim.calls if c[0] == "forward"] == []


def test_9d_reverse_formatter_still_ignores_the_forward_fallback(single_geocoder):
    """Reverse nie dziedziczy `confirmed_admin` ani fallbacków forward."""
    lb._GEO_DETAILS_CACHE.clear()
    short, display, status = lb.get_location_details_from_coords(
        35.6761772, 139.6502952, "pl")
    assert (short, display) == ("Suginami", "Suginami, Japonia")
