"""Statyczny indeks krajów — runtime bez żadnych zależności trzecich.

Dane pochodzą z pliku ``data/countries/country_index.json``, generowanego offline
przez ``tools/build_country_index.py`` (CLDR przez Babel + kody ISO 3166-1 przez
pycountry). **Produkcja nie importuje Babel ani pycountry** — czyta wyłącznie
wygenerowany JSON. Szczegóły źródeł i licencji: ``data/countries/NOTICE.md``.

Uzgodniony kontrakt (faza 1):

* dopasowanie jest **wyłącznie pełno-frazowe** po znormalizowanej nazwie —
  bez prefiksów, bez fuzzy matchingu, bez odmiany i bez transliteracji
  (``niger`` nie pasuje do ``nigeria``, ``guinea`` nie pasuje do ``guinea bissau``),
* kolizja nazwy (np. ``kongo`` -> ``{cd, cg}``) **nie jest rozstrzygana**:
  kod nie jest wybierany, segment zachowuje dotychczasową rolę w parserze,
* nazwa z listy ``context_ambiguous`` (w fazie 1: ``georgia``, ``wlochy``) nie jest
  traktowana jako filtr kraju — override istnieje tylko tam, gdzie udowodniono
  regresję istniejącej formy zapytania,
* kody ISO alpha-3 są przechowywane w danych, ale **nie służą do dopasowania**
  (decyzja D5),
* brak lub uszkodzenie pliku = indeks **niedostępny**. Nie ma fallbacku do
  starego słownika: ``location_bot.geocode_city_accepted`` zgłasza wtedy
  ``GEOCODE_ERROR`` (fail-closed), a nie ciche wyszukiwanie bez filtra kraju.
  GPS, pinezka i WebApp nie korzystają z tego modułu, więc działają bez zmian.

Moduł jest zgodny z Pythonem 3.8 (produkcja: 3.8.10) — bez PEP 585/604,
bez ``str.removeprefix``, bez ``match``.
"""

import hashlib
import json
import os
import re
import threading
import unicodedata
from collections import namedtuple

# --- Klasyfikacja nazwy ------------------------------------------------------

NONE = "none"
UNIQUE = "unique"
AMBIGUOUS = "ambiguous"
CONTEXT_AMBIGUOUS = "context_ambiguous"

#: Klasyfikacja pojedynczej nazwy. ``codes`` jest zawsze krotką (posortowaną),
#: także dla ``NONE`` (wtedy pusta). ``CountryMatch.code`` zwraca kod wyłącznie
#: dla jednoznacznego trafienia — nigdy „pierwszy z listy”.
CountryMatch = namedtuple("CountryMatch", "kind codes")


class _CountryMatch(CountryMatch):
    """``CountryMatch`` z wygodnym ``code`` (None, gdy nie ma jednoznaczności)."""

    __slots__ = ()

    @property
    def code(self):
        if self.kind == UNIQUE and len(self.codes) == 1:
            return self.codes[0]
        return None


NO_MATCH = _CountryMatch(NONE, ())

# --- Stałe formatu -----------------------------------------------------------

SCHEMA_VERSION = 1
LANGUAGES = ("pl", "en", "de", "fr", "es", "no")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(BASE_DIR, "data", "countries", "country_index.json")

_ALPHA2_RE = re.compile(r"^[a-z]{2}$")
_ALPHA3_RE = re.compile(r"^[a-z]{3}$")
_KEY_RE = re.compile(r"^[a-z0-9]+(?: [a-z0-9]+)*$")

_MAX_TOKENS_LIMIT = 12


class CountryIndexError(Exception):
    """Indeks krajów jest nieużywalny (brak pliku, zły JSON, zła walidacja)."""


def normalize_country_name(text):
    """Znormalizowana postać nazwy kraju — **ten sam** algorytm co dla miast.

    Kontrakt jest celowo identyczny z ``location_bot._normalize_location_text``
    (casefold, ``ł``/``ø``/``æ``/``œ``, NFKD, usunięcie znaków łączących,
    ``[^a-z0-9]`` -> spacja, wielokrotne spacje do jednej). Dzięki temu resolver
    krajów nie zmienia globalnej normalizacji nazw miejscowości — a test
    krzyżowy pilnuje, żeby obie implementacje nie rozjechały się w przyszłości.

    Świadome ograniczenia (nie są długiem do spłaty w tej zmianie): brak odmiany,
    brak transliteracji, brak alfabetów niełacińskich (są usuwane).
    """
    text = str(text or "").casefold().replace("ł", "l").replace("ø", "o")
    text = text.replace("æ", "ae").replace("œ", "oe")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return " ".join(text.split())


def canonical_payload_sha256(payload):
    """SHA-256 kanonicznej treści indeksu (bez ``meta``, gdzie siedzi suma).

    Używane przez generator i przez walidację runtime — jedna implementacja,
    żeby suma nie mogła „rozjechać się” między narzędziem a loaderem.
    """
    body = {key: value for key, value in payload.items() if key != "meta"}
    blob = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class CountryIndex(object):
    """Niemutowalny (po walidacji) widok indeksu krajów."""

    def __init__(self, payload):
        meta = payload["meta"]
        self.schema_version = int(meta["schema_version"])
        self.languages = tuple(meta["languages"])
        self.max_name_tokens = int(meta["max_name_tokens"])
        self.sources = dict(meta.get("source") or {})
        self.countries = tuple(payload["countries"])
        self.country_count = len(self.countries)
        self._lookup = {
            key: tuple(codes) for key, codes in payload["lookup"].items()
        }
        self._context_ambiguous = {
            key: tuple(entry["codes"])
            for key, entry in payload["context_ambiguous"].items()
        }

    # -- widok diagnostyczny (testy, log) -----------------------------------

    @property
    def name_count(self):
        return len(self._lookup)

    def codes_for(self, name):
        """Surowa krotka kodów dla nazwy (bez interpretacji) albo ``()``."""
        return self._lookup.get(normalize_country_name(name), ())

    def is_context_ambiguous(self, name):
        return normalize_country_name(name) in self._context_ambiguous

    # -- klasyfikacja -------------------------------------------------------

    def classify(self, name):
        """``CountryMatch`` dla pojedynczej nazwy (wejście normalizowane w środku)."""
        key = normalize_country_name(name)
        if not key:
            return NO_MATCH
        ambiguous = self._context_ambiguous.get(key)
        if ambiguous is not None:
            return _CountryMatch(CONTEXT_AMBIGUOUS, ambiguous)
        codes = self._lookup.get(key)
        if not codes:
            return NO_MATCH
        if len(codes) == 1:
            return _CountryMatch(UNIQUE, codes)
        return _CountryMatch(AMBIGUOUS, codes)

    def resolve_suffix(self, raw):
        """``(rozmiar sufiksu w surowych tokenach, CountryMatch)``.

        Liczy sufiksy od najdłuższego do jednego tokenu, ale **nigdy nie bierze
        całego zapytania** (``len(tokens) - 1``) — samo ``Polska`` czy ``Japonia``
        pozostaje zapytaniem o miejscowość. Pierwsza rozpoznana nazwa (także
        wieloznaczna albo z listy ``context_ambiguous``) kończy wyszukiwanie:
        to ona określa intencję użytkownika.
        """
        tokens = str(raw or "").replace(",", " ").split()
        max_tokens = min(self.max_name_tokens, len(tokens) - 1)
        for size in range(max_tokens, 0, -1):
            match = self.classify(" ".join(tokens[-size:]))
            if match.kind != NONE:
                return size, match
        return 0, NO_MATCH


# --- Walidacja ---------------------------------------------------------------

def _require(condition, message):
    if not condition:
        raise CountryIndexError(message)


def validate_payload(payload):
    """Buduje ``CountryIndex`` albo rzuca ``CountryIndexError`` z powodem.

    Walidacja jest kompletna (schema, języki, kody, spójność nazw z ``lookup``,
    suma kontrolna), bo indeks jest jedynym źródłem prawdy o krajach: częściowo
    poprawny plik byłby gorszy niż jego brak.
    """
    _require(isinstance(payload, dict), "indeks nie jest obiektem JSON")
    for key in ("meta", "countries", "lookup", "context_ambiguous"):
        _require(key in payload, "brak sekcji %r" % (key,))

    meta = payload["meta"]
    _require(isinstance(meta, dict), "sekcja 'meta' nie jest obiektem")
    _require(
        int(meta.get("schema_version", -1)) == SCHEMA_VERSION,
        "nieznana schema_version: %r" % (meta.get("schema_version"),),
    )
    _require(
        tuple(meta.get("languages") or ()) == LANGUAGES,
        "niezgodny zestaw języków: %r" % (meta.get("languages"),),
    )
    max_tokens = int(meta.get("max_name_tokens", 0) or 0)
    _require(
        1 <= max_tokens <= _MAX_TOKENS_LIMIT,
        "max_name_tokens poza zakresem 1..%d: %r" % (_MAX_TOKENS_LIMIT, max_tokens),
    )

    countries = payload["countries"]
    _require(isinstance(countries, list) and countries, "lista krajów jest pusta")
    _require(
        int(meta.get("country_count", -1)) == len(countries),
        "country_count (%r) nie zgadza się z liczbą krajów (%d)"
        % (meta.get("country_count"), len(countries)),
    )

    seen_alpha2 = set()
    seen_alpha3 = set()
    for country in countries:
        _require(isinstance(country, dict), "wpis kraju nie jest obiektem")
        alpha2 = str(country.get("alpha_2") or "")
        alpha3 = str(country.get("alpha_3") or "")
        _require(_ALPHA2_RE.match(alpha2), "nieprawidłowy alpha_2: %r" % (alpha2,))
        _require(_ALPHA3_RE.match(alpha3), "nieprawidłowy alpha_3: %r" % (alpha3,))
        _require(alpha2 not in seen_alpha2, "duplikat alpha_2: %r" % (alpha2,))
        _require(alpha3 not in seen_alpha3, "duplikat alpha_3: %r" % (alpha3,))
        seen_alpha2.add(alpha2)
        seen_alpha3.add(alpha3)
        names = country.get("names")
        _require(isinstance(names, dict), "brak mapy nazw dla %r" % (alpha2,))
        for language in LANGUAGES:
            value = names.get(language)
            _require(
                isinstance(value, str) and value.strip(),
                "brak nazwy %s dla kraju %r" % (language, alpha2),
            )

    lookup = payload["lookup"]
    _require(isinstance(lookup, dict) and lookup, "sekcja 'lookup' jest pusta")
    for key, codes in lookup.items():
        _require(
            isinstance(key, str) and _KEY_RE.match(key),
            "klucz 'lookup' nie jest znormalizowany: %r" % (key,),
        )
        _require(
            normalize_country_name(key) == key,
            "klucz 'lookup' nie jest w postaci kanonicznej: %r" % (key,),
        )
        _require(
            isinstance(codes, list) and codes,
            "pusta lista kodów dla nazwy %r" % (key,),
        )
        _require(
            tuple(codes) == tuple(sorted(set(codes))),
            "kody nazwy %r nie są posortowanym zbiorem: %r" % (key, codes),
        )
        for code in codes:
            _require(
                isinstance(code, str) and _ALPHA2_RE.match(code),
                "nieprawidłowy kod %r dla nazwy %r" % (code, key),
            )
            _require(code in seen_alpha2, "kod %r nie istnieje na liście krajów" % (code,))

    ambiguous = payload["context_ambiguous"]
    _require(isinstance(ambiguous, dict), "sekcja 'context_ambiguous' nie jest obiektem")
    for key, entry in ambiguous.items():
        _require(
            isinstance(key, str) and normalize_country_name(key) == key and key,
            "klucz 'context_ambiguous' nie jest znormalizowany: %r" % (key,),
        )
        _require(isinstance(entry, dict), "wpis 'context_ambiguous' nie jest obiektem")
        codes = entry.get("codes")
        _require(
            isinstance(codes, list) and codes,
            "brak kodów dla nazwy kontekstowej %r" % (key,),
        )
        _require(
            tuple(codes) == tuple(sorted(set(codes))),
            "kody nazwy kontekstowej %r nie są posortowanym zbiorem" % (key,),
        )
        for code in codes:
            _require(code in seen_alpha2, "kod %r nie istnieje na liście krajów" % (code,))
        _require(key in lookup, "nazwa kontekstowa %r nie występuje w 'lookup'" % (key,))

    expected_hash = meta.get("payload_sha256")
    if expected_hash:
        actual = canonical_payload_sha256(payload)
        _require(
            actual == expected_hash,
            "suma kontrolna indeksu nie zgadza się (oczekiwano %s, jest %s)"
            % (expected_hash[:12], actual[:12]),
        )

    # Spójność nazw krajów z mapą 'lookup' — to wyłapuje ręczne edycje pliku.
    for country in countries:
        for language, value in country["names"].items():
            key = normalize_country_name(value)
            codes = lookup.get(key, ())
            _require(
                country["alpha_2"] in codes,
                "nazwa %r (%s) nie wskazuje kraju %r w 'lookup'"
                % (value, language, country["alpha_2"]),
            )

    return CountryIndex(payload)


# --- Ładowanie (singleton) ---------------------------------------------------

_LOCK = threading.Lock()
_INDEX = None
_ERROR = None
_LOADED = False


def load_from_path(path):
    """Wczytuje i waliduje indeks z podanej ścieżki (bez cache'owania)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        raise CountryIndexError("brak pliku indeksu krajów: %s" % (path,))
    except OSError as exc:
        raise CountryIndexError("nie można odczytać indeksu krajów: %s" % (exc,))
    except ValueError as exc:
        raise CountryIndexError("uszkodzony JSON indeksu krajów: %s" % (exc,))
    return validate_payload(payload)


def reset():
    """Czyści singleton (testy i diagnostyka). Nie dotyka pliku na dysku."""
    global _INDEX, _ERROR, _LOADED
    with _LOCK:
        _INDEX = None
        _ERROR = None
        _LOADED = False


def get_index():
    """``CountryIndex`` albo ``None``, gdy indeks jest niedostępny.

    Ładowanie następuje raz na proces. Pierwsza próba loguje jedną linię:
    sukces (liczba krajów/nazw/schema) albo błąd z powodem i informacją, że
    tekstowe wyszukiwanie miejscowości jest wyłączone (fail-closed).
    """
    global _INDEX, _ERROR, _LOADED
    if _LOADED:
        return _INDEX
    with _LOCK:
        if _LOADED:
            return _INDEX
        try:
            index = load_from_path(INDEX_PATH)
            sources = index.sources
            _INDEX = index
            _ERROR = None
            print(
                "[COUNTRY_INDEX] Wczytano indeks krajów: %d krajów, %d nazw, "
                "schema=%d, cldr=%s, iso3166=%s"
                % (
                    index.country_count,
                    index.name_count,
                    index.schema_version,
                    sources.get("cldr", "?"),
                    sources.get("iso3166", "?"),
                )
            )
        except CountryIndexError as exc:
            _INDEX = None
            _ERROR = str(exc)
            print(
                "[COUNTRY_INDEX] BŁĄD indeksu krajów: %s. Tekstowe wyszukiwanie "
                "miejscowości jest WYŁĄCZONE (fail-closed); GPS i pinezka działają "
                "bez zmian." % (_ERROR,)
            )
        _LOADED = True
    return _INDEX


def index_available():
    return get_index() is not None


def index_error():
    """Powód niedostępności indeksu (albo ``None``, gdy indeks działa)."""
    get_index()
    return _ERROR


def max_name_tokens():
    """Najdłuższa nazwa kraju w tokenach; ``0`` gdy indeks jest niedostępny."""
    index = get_index()
    return index.max_name_tokens if index is not None else 0


def classify(name):
    index = get_index()
    return index.classify(name) if index is not None else NO_MATCH


def resolve_suffix(raw):
    index = get_index()
    return index.resolve_suffix(raw) if index is not None else (0, NO_MATCH)


def index_path():
    """Ścieżka, z której czytany jest indeks (diagnostyka i testy)."""
    return INDEX_PATH
