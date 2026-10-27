#!/usr/bin/env python3
"""Generator OFFLINE indeksu krajów: ``data/countries/country_index.json``.

Uruchamiać tylko w środowisku narzędziowym (``requirements-tools.txt``):

    pip install -r requirements-tools.txt
    python tools/build_country_index.py            # zapis pliku
    python tools/build_country_index.py --check    # tylko porównanie (CI)

Źródła danych:

* **kody ISO 3166-1** (alpha-2, alpha-3, zbiór krajów/terytoriów) — ``pycountry``,
* **nazwy krajów w 6 językach interfejsu** (``pl, en, de, fr, es, no``) — CLDR
  dostarczany przez pakiet ``Babel`` (``Locale.territories``),
* **aliasy ręczne** i lista ``context_ambiguous`` — ``tools/country_index_overrides.json``.

Produkcja tych bibliotek NIE importuje: bot czyta wyłącznie wygenerowany JSON
(``country_index.py``). Wyjście jest deterministyczne — dwa uruchomienia dają
identyczny plik bit w bit (stąd tryb ``--check`` w CI).
"""

import argparse
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import country_index  # noqa: E402  (moduł runtime: normalizacja + walidacja)

TOOLS_DIR = os.path.join(BASE_DIR, "tools")
OVERRIDES_PATH = os.path.join(TOOLS_DIR, "country_index_overrides.json")
OUTPUT_PATH = country_index.INDEX_PATH

INVALID_TERRITORY_NAMES = frozenset({"unknown region", "unknown", ""})


def _fail(message):
    print("BŁĄD: %s" % (message,), file=sys.stderr)
    raise SystemExit(2)


def _import_sources():
    try:
        import babel
        import pycountry
        from babel import Locale
        from babel.core import get_global
    except ImportError as exc:
        _fail(
            "brak narzędzi generatora (%s). Zainstaluj: pip install -r requirements-tools.txt"
            % (exc,)
        )
    cldr_version = str((get_global("cldr") or {}).get("version") or "unknown")
    return babel, pycountry, Locale, cldr_version


def _load_overrides():
    if not os.path.exists(OVERRIDES_PATH):
        _fail("brak pliku override'ów: %s" % (OVERRIDES_PATH,))
    with open(OVERRIDES_PATH, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        _fail("override'y muszą być obiektem JSON")
    for key in ("aliases", "context_ambiguous"):
        if not isinstance(payload.get(key), dict):
            _fail("override'y: brak sekcji %r" % (key,))
    return payload


def _check_normalized(mapping, label):
    for key in mapping:
        normalized = country_index.normalize_country_name(key)
        if normalized != key:
            _fail(
                "%s: klucz %r nie jest znormalizowany (powinno być %r)"
                % (label, key, normalized)
            )


def build_payload():
    """Buduje i waliduje payload indeksu (bez zapisu)."""
    babel, pycountry, Locale, cldr_version = _import_sources()
    overrides = _load_overrides()
    _check_normalized(overrides["aliases"], "aliases")
    _check_normalized(overrides["context_ambiguous"], "context_ambiguous")

    locales = {}
    for language in country_index.LANGUAGES:
        try:
            locales[language] = Locale.parse(language)
        except Exception as exc:  # noqa: BLE001 - komunikat dla operatora
            _fail("Babel nie zna języka %r: %s" % (language, exc))

    countries = []
    exceptions = []
    for country in sorted(pycountry.countries, key=lambda item: item.alpha_2):
        if getattr(country, "alpha_2", None) is None or getattr(country, "alpha_3", None) is None:
            exceptions.append("pominięto wpis bez kodów ISO: %r" % (country,))
            continue
        names = {}
        for language, locale in locales.items():
            name = (locale.territories or {}).get(country.alpha_2)
            if not name or country_index.normalize_country_name(name) in INVALID_TERRITORY_NAMES:
                _fail(
                    "CLDR %s: brak nazwy kraju %s (%s)"
                    % (language, country.alpha_2, country.name)
                )
            names[language] = name
        countries.append(
            {
                "alpha_2": country.alpha_2.lower(),
                "alpha_3": country.alpha_3.lower(),
                "names": {language: names[language] for language in country_index.LANGUAGES},
            }
        )

    if len(countries) != 249:
        exceptions.append(
            "UWAGA: liczba krajów zmieniła się z 249 na %d (nowa wersja pycountry?)"
            % (len(countries),)
        )

    known_codes = {country["alpha_2"] for country in countries}
    lookup = {}

    def _add(key, codes, origin):
        normalized = country_index.normalize_country_name(key)
        if not normalized:
            _fail("%s: klucz %r normalizuje się do pustego napisu" % (origin, key))
        for code in codes:
            if code not in known_codes:
                _fail("%s: kod %r nie istnieje w ISO 3166-1" % (origin, code))
        lookup.setdefault(normalized, set()).update(codes)

    # 1. Własne kody alpha-2 (także jako samodzielny końcowy token: "Hel PL").
    for country in countries:
        _add(country["alpha_2"], [country["alpha_2"]], "alpha_2")
    # 2. Nazwy CLDR w językach interfejsu.
    for country in countries:
        for language, name in country["names"].items():
            _add(name, [country["alpha_2"]], "nazwa %s" % (language,))
    # 3. Aliasy ręczne — wyłącznie te, których wymaga dotychczasowe zachowanie.
    for key, codes in overrides["aliases"].items():
        _add(key, codes, "alias")

    ambiguous = {}
    for key, entry in overrides["context_ambiguous"].items():
        codes = entry["codes"]
        for code in codes:
            if code not in known_codes:
                _fail("context_ambiguous: kod %r nie istnieje w ISO 3166-1" % (code,))
        if key not in lookup:
            _fail("context_ambiguous: nazwa %r nie występuje w induksie" % (key,))
        ambiguous[key] = {
            "codes": sorted(codes),
            "reason": str(entry.get("reason") or "").strip(),
        }

    max_name_tokens = max(len(key.split()) for key in lookup)
    payload = {
        "meta": {
            "schema_version": country_index.SCHEMA_VERSION,
            "source": {
                "cldr": "CLDR %s (Babel %s)" % (cldr_version, babel.__version__),
                "iso3166": "pycountry %s" % (pycountry.__version__,),
                "overrides": "tools/country_index_overrides.json@%s"
                % (str(overrides.get("version", 1)),),
            },
            "languages": list(country_index.LANGUAGES),
            "country_count": len(countries),
            "max_name_tokens": max_name_tokens,
        },
        "countries": countries,
        "lookup": {key: sorted(codes) for key, codes in sorted(lookup.items())},
        "context_ambiguous": {key: ambiguous[key] for key in sorted(ambiguous)},
    }
    payload["meta"]["payload_sha256"] = country_index.canonical_payload_sha256(payload)

    # Ta sama walidacja, którą wykonuje runtime — plik nie może być „prawie poprawny”.
    country_index.validate_payload(payload)
    return payload, exceptions


def serialize(payload):
    """Deterministyczna serializacja: stała kolejność kluczy, indent 1, UTF-8, LF."""
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, indent=1
    ) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generator indeksu krajów (offline).")
    parser.add_argument(
        "--check",
        action="store_true",
        help="nie zapisuj: sprawdź, czy plik w repo jest aktualny (kod wyjścia 1 = rozjazd)",
    )
    parser.add_argument(
        "--output",
        default=OUTPUT_PATH,
        help="ścieżka wyjściowa (domyślnie data/countries/country_index.json)",
    )
    args = parser.parse_args(argv)

    payload, exceptions = build_payload()
    blob = serialize(payload)
    for message in exceptions:
        print("UWAGA: %s" % (message,), file=sys.stderr)

    if args.check:
        if not os.path.exists(args.output):
            print(
                "ROZJAZD: brak pliku %s — uruchom python tools/build_country_index.py"
                % (args.output,),
                file=sys.stderr,
            )
            return 1
        with open(args.output, "r", encoding="utf-8") as handle:
            current = handle.read()
        if current != blob:
            print(
                "ROZJAZD: %s nie odpowiada wygenerowanemu indeksowi "
                "(uruchom python tools/build_country_index.py i zacommituj wynik)."
                % (args.output,),
                file=sys.stderr,
            )
            return 1
        print("OK: %s jest aktualny (%d bajtów)." % (args.output, len(blob.encode("utf-8"))))
        return 0

    directory = os.path.dirname(args.output)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(blob)

    meta = payload["meta"]
    print(
        "Zapisano %s: %d krajów, %d nazw, max %d tokenów, %d bajtów, cldr=%s, iso3166=%s"
        % (
            args.output,
            meta["country_count"],
            len(payload["lookup"]),
            meta["max_name_tokens"],
            len(blob.encode("utf-8")),
            meta["source"]["cldr"],
            meta["source"]["iso3166"],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
