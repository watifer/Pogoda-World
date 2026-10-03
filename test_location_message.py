"""test_location_message.py — POPRAWKA #4: pełny opis lokalizacji + status geokodera.

Zakres zmian zgłoszonych przez właściciela produktu:
  1. komunikat po /miasto (Zapisana lokalizacja) i po karcie jednorazowej
     (Użyta lokalizacja) pokazuje PEŁNY adres z geokodera, żeby użytkownik
     rozpoznał miejsce (miejscowości o tej samej nazwie bywa wiele),
  2. etykieta 📍 jest pogrubiona („trochę większymi literami”),
  3. akapity opatrzone emoji: 📍 etykieta / 🌍 opis / ⚠️ wskazówka,
  4. awaria geokodera (timeout/sieć) => „Błędy na łączach, spróbuj za chwilę
     ponownie.” i NIC nie zapisujemy,
  5. geokoder odpowiedział, ale to teren bez miejscowości (pustynia, góry)
     => komunikat z kodu: „Lokalizacja w terenie (poza miastem)”.

Uruchomienie: pytest test_location_message.py -v
"""

import pytest

import i18n
import location_bot as lb

LANGS = ("pl", "en", "de", "es", "fr", "no")
LABEL = {"pl": "Zapisana lokalizacja", "used_pl": "Użyta lokalizacja"}
ADDRESS = "Wiązowna, gmina Wiązowna, powiat otwocki, województwo mazowieckie, 05-462, Polska"


@pytest.fixture(autouse=True)
def _clean_geo_cache():
    lb._GEO_DETAILS_CACHE.clear()
    yield
    lb._GEO_DETAILS_CACHE.clear()


# ============================================================================
# 1. TREŚĆ KOMUNIKATÓW (6 języków)
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("key", ["location_saved", "used_location"])
def test_message_structure_three_blocks(lang, key):
    text = i18n.UI_TEXTS[lang][key]
    lines = text.format(address=ADDRESS).split("\n")

    assert lines[0].startswith("*📍 ") and lines[0].endswith(":*"), \
        "etykieta musi być pogrubiona i zakończona dwukropkiem"
    assert lines[1] == f"🌍 {ADDRESS}", "drugi akapit to pełny adres z geokodera"
    assert lines[2] == "", "adres i wskazówka muszą być rozdzielone pustą linią"
    assert lines[3].startswith("⚠️ "), "ostatni akapit zaczynamy emoji ostrzeżenia"
    assert len(lines) == 4, "komunikat ma dokładnie 3 akapity (bez list komend)"


@pytest.mark.parametrize("lang", LANGS)
def test_saved_message_points_to_city_command(lang):
    text = i18n.UI_TEXTS[lang]["location_saved"]
    cmd = "/miasto" if lang == "pl" else "/city"
    assert cmd in text
    assert "ponownie użyj" in text or "use" in text or "nutze" in text \
        or "usa" in text or "utilisez" in text or "bruk" in text


@pytest.mark.parametrize("lang", LANGS)
def test_used_message_points_to_same_command(lang):
    text = i18n.UI_TEXTS[lang]["used_location"]
    assert "samej komendy" in text or "same command" in text or "denselben Befehl" in text \
        or "mismo comando" in text or "même commande" in text or "samme kommando" in text


@pytest.mark.parametrize("lang", LANGS)
def test_no_backticks_and_no_city_placeholder(lang):
    for key in ("location_saved", "used_location"):
        text = i18n.UI_TEXTS[lang][key]
        assert "`" not in text, f"{lang}:{key}: backticki renderują się na czerwono"
        assert "{city}" not in text, f"{lang}:{key}: martwy placeholder {{city}}"
        assert text.count("*") % 2 == 0, f"{lang}:{key}: niedomknięte pogrubienie"


def test_polish_texts_are_exact():
    """Dokładne brzmienie zatwierdzone przez właściciela produktu."""
    assert i18n.UI_TEXTS["pl"]["location_saved"] == (
        "*📍 Zapisana lokalizacja:*\n🌍 {address}\n\n"
        "⚠️ Jeśli to nie to miejsce, ponownie użyj /miasto i wpisz nazwę dokładniej, "
        "np. z kodem pocztowym, krajem, powiatem lub regionem po przecinkach."
    )
    assert i18n.UI_TEXTS["pl"]["used_location"] == (
        "*📍 Użyta lokalizacja:*\n🌍 {address}\n\n"
        "⚠️ Jeśli to nie to miejsce, ponownie użyj tej samej komendy i wpisz nazwę dokładniej, "
        "np. z kodem pocztowym, krajem, powiatem lub regionem po przecinkach."
    )


def test_short_variants_are_gone():
    for lang in LANGS:
        assert "location_saved_short" not in i18n.UI_TEXTS[lang]
        assert "used_location_short" not in i18n.UI_TEXTS[lang]


# ============================================================================
# 2. AWARIA ŁĄCZ I TEREN BEZ MIEJSCOWOŚCI
# ============================================================================

@pytest.mark.parametrize("lang", LANGS)
def test_connection_error_key_exists(lang):
    text = i18n.UI_TEXTS[lang]["geo_conn_err"]
    assert "⚠️" in text
    assert len(text) > 20


def test_connection_error_text_is_standard():
    assert i18n.UI_TEXTS["pl"]["geo_conn_err"] == "⚠️ Błędy na łączach, spróbuj za chwilę ponownie."


def test_field_location_text_is_the_coded_one():
    """Środek komunikatu dla pustyni/gór = napis, który kod zwracał dotychczas."""
    assert i18n.UI_TEXTS["pl"]["location_field"] == lb.FIELD_LOCATION_LABEL
    assert lb.FIELD_LOCATION_LABEL == "Lokalizacja w terenie (poza miastem)"


# ============================================================================
# 3. STATUS GEOKODERA (fake Nominatim — zero sieci)
# ============================================================================

class _FakeLocation:
    def __init__(self, address_dict=None, address_text=""):
        self.raw = {"address": address_dict} if address_dict is not None else {}
        self.address = address_text


class _FakeNominatim:
    """Atrapa Nominatim: kolejne wywołania reverse() zwracają zaplanowane wyniki."""

    calls = []
    results = []

    def __init__(self, user_agent=None):
        pass

    def reverse(self, query, language=None):
        _FakeNominatim.calls.append((query, language))
        result = _FakeNominatim.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def geocode(self, name, exactly_one=None, language=None):
        _FakeNominatim.calls.append((name, language))
        result = _FakeNominatim.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeGeocodeResult:
    latitude = 52.22972
    longitude = 21.01223
    address = "Warszawa, województwo mazowieckie, Polska"


@pytest.fixture
def fake_nominatim(monkeypatch):
    _FakeNominatim.calls = []
    _FakeNominatim.results = []
    monkeypatch.setattr(lb, "Nominatim", _FakeNominatim)
    return _FakeNominatim


def test_reverse_success_returns_name_address_and_ok(fake_nominatim):
    fake_nominatim.results.append(
        _FakeLocation({"city": "Wiązowna", "county": "otwocki"}, ADDRESS)
    )
    name, address, status = lb.get_location_details_from_coords(52.15, 21.29, "pl")
    assert (name, address, status) == ("Wiązowna", ADDRESS, lb.GEO_OK)


def test_reverse_without_city_is_no_city(fake_nominatim):
    fake_nominatim.results.append(_FakeLocation({"desert": "Sahara"}, "Sahara, Algieria"))
    name, address, status = lb.get_location_details_from_coords(23.0, 12.0, "pl")
    assert name is None and status == lb.GEO_NO_CITY
    assert address == "Sahara, Algieria"


def test_reverse_none_means_connection_error(fake_nominatim):
    fake_nominatim.results.append(None)
    assert lb.get_location_details_from_coords(52.15, 21.29, "pl") == (None, None, lb.GEO_ERROR)


def test_reverse_exception_means_connection_error(fake_nominatim):
    fake_nominatim.results.append(TimeoutError("timeout"))
    assert lb.get_location_details_from_coords(52.15, 21.29, "pl") == (None, None, lb.GEO_ERROR)


def test_connection_error_is_not_cached(fake_nominatim):
    """Po awarii ponowienie musi realnie uderzyć do geokodera (nie cache'ujemy błędu)."""
    fake_nominatim.results.append(TimeoutError("timeout"))
    assert lb.get_location_details_from_coords(52.15, 21.29, "pl")[2] == lb.GEO_ERROR

    fake_nominatim.results.append(_FakeLocation({"city": "Wiązowna"}, ADDRESS))
    assert lb.get_location_details_from_coords(52.15, 21.29, "pl")[2] == lb.GEO_OK
    assert len(fake_nominatim.calls) == 2


def test_success_is_cached(fake_nominatim):
    fake_nominatim.results.append(_FakeLocation({"city": "Wiązowna"}, ADDRESS))
    first = lb.get_location_details_from_coords(52.15, 21.29, "pl")
    second = lb.get_location_details_from_coords(52.15, 21.29, "pl")
    assert first == second
    assert len(fake_nominatim.calls) == 1, "drugie pytanie o tę samą parę ma iść z cache"


def test_forward_geocode_flags_connection_error(fake_nominatim):
    fake_nominatim.results.append(TimeoutError("timeout"))
    assert lb.geocode_city_details("Wiązowna", "pl") == (None, None, None, False)

    fake_nominatim.results.append(None)          # brak wyników != awaria
    assert lb.geocode_city_details("Xyzzz", "pl") == (None, None, None, True)

    fake_nominatim.results.append(_FakeGeocodeResult())
    lat, lon, address, ok = lb.geocode_city_details("Warszawa", "pl")
    assert ok is True and address == _FakeGeocodeResult.address


# ============================================================================
# 4. WSPÓLNA LOGIKA ETYKIETY I OPISU
# ============================================================================

def test_resolve_labels_ok(monkeypatch):
    monkeypatch.setattr(lb, "get_location_details_from_coords",
                        lambda lat, lon, lang: ("Wiązowna", ADDRESS, lb.GEO_OK))
    assert lb._resolve_location_labels(52.15, 21.29, "pl") == ("Wiązowna", ADDRESS, lb.GEO_OK)


def test_resolve_labels_no_city_uses_translated_field_message(monkeypatch):
    monkeypatch.setattr(lb, "get_location_details_from_coords",
                        lambda lat, lon, lang: (None, "Sahara", lb.GEO_NO_CITY))
    label, display, status = lb._resolve_location_labels(23.0, 12.0, "de")
    assert status == lb.GEO_NO_CITY
    assert label == lb.FIELD_LOCATION_LABEL
    assert display == i18n.UI_TEXTS["de"]["location_field"]


def test_resolve_labels_error_without_fallback_is_fatal(monkeypatch):
    monkeypatch.setattr(lb, "get_location_details_from_coords",
                        lambda lat, lon, lang: (None, None, lb.GEO_ERROR))
    assert lb._resolve_location_labels(52.15, 21.29, "pl") == (None, None, lb.GEO_ERROR)


def test_resolve_labels_error_with_fallback_keeps_typing_result(monkeypatch):
    """Chwilowa awaria reverse nie gubi nazwy, którą użytkownik właśnie wpisał."""
    monkeypatch.setattr(lb, "get_location_details_from_coords",
                        lambda lat, lon, lang: (None, None, lb.GEO_ERROR))
    label, display, status = lb._resolve_location_labels(
        52.15, 21.29, "pl", fallback_city="Wiązowna", fallback_address="Wiązowna, Polska"
    )
    assert (label, display, status) == ("Wiązowna", "Wiązowna, Polska", lb.GEO_OK)


# ============================================================================
# 5. WARSTWA WIADOMOŚCI
# ============================================================================

def test_used_location_message_always_full_form():
    msg = lb._used_location_message("pl", "Wiązowna", ADDRESS)
    assert msg.startswith("*📍 Użyta lokalizacja:*\n🌍 " + ADDRESS)
    assert msg.endswith("po przecinkach.")


def test_used_location_message_falls_back_to_city_name():
    msg = lb._used_location_message("pl", "Wiązowna", None)
    assert "🌍 Wiązowna" in msg


def test_md_safe_strips_markdown_control_chars():
    # * , _ , ` , [ , ] znikają — resztę adresu zostawiamy bez zmian
    assert lb._md_safe("Wiąz*owna_test`x`[y]") == "Wiązownatestxy"
    assert lb._md_safe("Wiązowna, powiat otwocki") == "Wiązowna, powiat otwocki"
    assert lb._md_safe(None) == ""
    # Adres nie może rozwalić parse_mode=Markdown w send_reply.
    msg = lb._used_location_message("pl", "X", "Dąbrowa_Górnicza *test*")
    assert msg.count("*") % 2 == 0
