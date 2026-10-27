# Indeks krajów — pochodzenie danych i licencje

Plik `country_index.json` jest **wygenerowany** i wersjonowany w repozytorium.
Bot produkcyjny czyta wyłącznie ten plik: nie importuje `babel` ani `pycountry`.

## Zawartość

| Element | Źródło | Wersja użyta do wygenerowania |
|---|---|---|
| Kody ISO 3166‑1 (alpha‑2, alpha‑3), zbiór krajów i terytoriów | `pycountry` | `26.2.16` |
| Nazwy krajów w językach `pl, en, de, fr, es, no` | CLDR, dystrybuowany w pakiecie `Babel` (`Locale.territories`) | CLDR `47`, `Babel 2.18.0` |
| Aliasy ręczne (np. `uk → gb`) i lista `context_ambiguous` | `tools/country_index_overrides.json` | wersja `1` |

Wersje i sumę kontrolną treści zapisuje `meta` w samym pliku JSON
(`source.cldr`, `source.iso3166`, `source.overrides`, `payload_sha256`).

## Licencje źródeł

* **CLDR / Unicode** — dane lokalizacyjne CLDR są udostępniane na licencji
  Unicode License Agreement (patrz `LICENSE.unicode` w dystrybucji Babel).
  W repozytorium dystrybuowane są **nazwy krajów pochodne od CLDR**.
* **pycountry** — licencja LGPL‑2.1‑only. W repozytorium dystrybuowane są
  wyłącznie **kody ISO** (fakty), nie kod biblioteki.
* **Babel** — BSD‑3‑Clause (używany tylko jako nośnik danych CLDR w generatorze).

Żadna z tych bibliotek nie jest zależnością runtime — `requirements.txt` pozostaje
bez zmian. Wymagania generatora: `requirements-tools.txt`.

## Regeneracja (deterministyczna)

```bash
pip install -r requirements-tools.txt
python tools/build_country_index.py            # zapis pliku
python tools/build_country_index.py --check    # kontrola: kod 1 = rozjazd
```

Dwa uruchomienia dają identyczny plik bit w bit. CI (`.github/workflows/country_index.yml`)
uruchamia tryb `--check`, żeby plik nie mógł „po cichu” rozjechać się z danymi.

## Czego indeks NIE robi

Pełne dopasowanie frazy po normalizacji (`casefold`, bez diakrytyków), bez odmiany,
bez transliteracji, bez prefiksów, bez fuzzy matchingu, wyłącznie alfabet łaciński.
Kody alpha‑3 są przechowywane, ale nie służą do dopasowania.
