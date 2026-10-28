# Raport audytu: globalne, bezpieczne wyszukiwanie miejscowości z krajem

> **UWAGA — dokument dwuczęściowy.**
>
> **Sekcje 1–18 opisują stan audytu i projekt sprzed implementacji.**
> Czytaj je jako zapis tego, jak kod wyglądał PRZED zmianą i co zostało zaakceptowane.
> **Sekcja 19 oraz późniejsze uzupełnienia opisują stan po implementacji fazy 1.**
>
> **Aktualny kod korzysta z `country_index.py` i nie korzysta już ze starego
> `_GEOCODE_COUNTRY_ALIASES` ani ze stałego limitu czterech tokenów**
> (`_GEOCODE_COUNTRY_MAX_TOKENS = 4` zostało usunięte; limit jest wyliczany z danych:
> `meta.max_name_tokens`, dziś 9).
>
> Starsze wyniki audytu są zachowane bez zmian merytorycznych — wszędzie tam, gdzie mowa
> o ręcznym słowniku 7 krajów albo o limicie 4 tokenów, opis dotyczy **stanu sprzed
> implementacji** i jest oznaczony jako taki.

---

**Status historyczny (stan sprzed implementacji, sekcje 1–18): audyt + projekt, bez zmian
w plikach produkcyjnych.** Zero requestów do Nominatim w całej sesji (audyt, implementacja
i korekty).

---

## 1. Stan repozytorium i zakres przeczytanych źródeł

### 1.1 Workspace (sprawdzone na początku sesji)

| Element | Wartość |
|---|---|
| Ścieżka repozytorium | `/home/user/Pogoda-World` |
| Aktywna gałąź | `arena/6d0eae02-pogoda-world` |
| HEAD | `5a289ff3a06583bcf7fbfc24af1ca4133dcfb43e` (`Merge pull request #88 from watifer/main`, 2026‑10‑26, watifer) |
| `git status` | czysto (`## arena/6d0eae02-pogoda-world`, brak zmian staged/unstaged) |
| Lokalne zmiany | brak |
| Pozostałe gałęzie lokalne | `main` @ `3a96181` (origin/main) — nie użyta, nie przełączana |

Nie wykonano: reset, `git clean`, stash, usuwanie plików, przełączanie gałęzi.

### 1.2 Środowisko

| Element | Sandbox (audyt) | Produkcja (wg polecenia) | CI |
|---|---|---|---|
| Python | 3.11.2 (`/usr/bin/python3`) | **3.8.10** (GCC 9.4.0, glibc 2.29) | **3.11** (`.github/workflows/*.yml`, `setup-python@v5`) |
| pip | 23.0.1 | 20.0.2 | – |

Do weryfikacji założyłem `.venv` (jest w `.gitignore`, nie trafi do repo) i zainstalowałem
`requirements-dev.txt` + `babel` i `pycountry` **wyłącznie jako narzędzia offline** (patrz §6).
Nie zmieniono żadnego pliku wersjonowanego.

### 1.3 Pliki requirements i konfiguracja testów

- `requirements.txt`: `requests, python-dateutil, openai, google-generativeai, gspread, google-auth,
  pytz, timezonefinder, python-dotenv, geopy, Pillow, tzdata` + `backports.zoneinfo; python_version < "3.9"`
  + `pyshp, shapely, pyproj` (moduł nadmorski). **Brak wersji przypiętych** (brak `==`).
- `requirements-dev.txt`: `-r requirements.txt`, `pytest`, `pytest-socket`.
- `pytest.ini`: `addopts = --disable-socket` → **testy są offline z definicji**.
- `conftest.py`: zeruje sekrety (`GROQ_API_KEY`, `OWM_API_KEY`, `AIRLY_API_KEY`, `GOOGLE_CREDS_JSON`,
  `TG_TOKEN`, `TELEGRAM_BOT_TOKEN`) i wyłącza flagi audytu/nowcastu **przed kolekcją**; izoluje
  artefakty runtime (`OUTPUT_DIR`, `owm_usage.json`, `audit_spread.jsonl`, `sent_today.json`, offset).
- Brak `pyproject.toml`, `setup.cfg`, `setup.py`, `tox.ini` (projekt nie jest pakietem — moduły
  ładowane z katalogu repo).
- CI: dwa workflowy (`location.yml`, `weather_bot.yml`), oba `pip install -r requirements.txt`
  + Python 3.11. **CI nie uruchamia testów** — nie ma joba `pytest`.

### 1.4 Baseline testów (zweryfikowany)

```
pytest -q   ->  3503 passed, 2 skipped  (11.94 s)
pytest -q test_geocode_validation.py -> 391 passed
```

Liczby testów w plikach istotnych dla tego zadania: `test_geocode_validation.py` 391,
`test_access_gate.py` 242, `test_shortcut_no_argument.py` 136, `test_card_caption.py` 64,
`test_shortcut_search_loc.py` 58, `test_city_prompt.py` 48, `test_location_message.py` 42,
`test_waiting_messages.py` 23.

### 1.5 Zakres przeczytanych źródeł

- `location_bot.py`: blok geokodowania **2140–3290**, etykiety **1728–2140**, ścieżki wykonania
  **540–640, 1230–1330, 3860–3990, 4100–4200, 4300–4420**, definicje statusów **1598–1620**,
  `_norm_lang` **416**.
- `guest_bot_handler.py`: tryb gościa, sanityzacja zapytania, drabinka 2 prób, cache, normalizacja
  kontraktu statusów, wstrzykiwane callbacki.
- `i18n.py`: `UI_TEXTS` (6 języków × 89 kluczy), `t_ui`, `t_geocode`, `GEOCODE_UI_KEYS`, `REPORT_WORDS`.
- `users_store.py`: `CANONICAL_HEADERS`, `LOCATION_SOURCES`, `SUPPORTED_LANGS`, `set_profile`.
- Konwencje danych: `coast_runtime.py`, `coast_detector.py` (`JsonCoastSigStore`), `image_generator.py`.
- Testy: `test_geocode_validation.py` (całość istotnych sekcji), `test_location_message.py`,
  `test_city_prompt.py`, `test_waiting_messages.py`, `test_shortcut_*`, fragmenty
  `test_access_gate.py`, `test_i18n_post_registration.py`, `conftest.py`, `pytest.ini`.
- Konfiguracja: `requirements*.txt`, `.github/workflows/*`, `.gitignore`, `README.md` (pobieżnie).

Wszystkie wymienione w zleceniu symbole **istnieją**; nie założyłem żadnej nazwy ani numeru linii
z wcześniejszych dyskusji — poniżej wszystko potwierdzone w tym checkoucie.

---

## 2. Fakty potwierdzone kodem

### 2.1 Słownik krajów — **STAN SPRZED IMPLEMENTACJI** (usunięty w fazie 1)

> Ten podrozdział dokumentuje słownik, który **został usunięty**. Aktualny kod nie ma
> `_GEOCODE_COUNTRY_ALIASES`; nazwy krajów pochodzą z `country_index.py` (patrz §19).

| Fakt | Lokalizacja |
|---|---|
| `_GEOCODE_COUNTRY_ALIASES` to **30 kluczy / 29 różnych po normalizacji / 7 kodów**: `pl, fr, us, de, gb, no, es` | `location_bot.py:2179-2191` |
| Klucze `"espana"` i `"españa"` to duplikat po normalizacji (drugi klucz jest martwy) | tamże |
| `"uk" → "gb"` i `"gb" → "gb"` (dwa klucze, ten sam kod) | tamże |
| Jedyny konsument słownika: `_geocode_country_suffix` | `location_bot.py:2366` |
| `_GEOCODE_COUNTRY_MAX_TOKENS = 4` | `location_bot.py:2178` |
| Całe zapytanie nigdy nie jest krajem: `max_tokens = min(MAX, len(tokens) - 1)` | `location_bot.py:2360` |

### 2.2 Normalizacja nazw (obecny kontrakt)

`_normalize_location_text` (`location_bot.py:1753-1765`):

```python
casefold -> "ł"→"l", "ø"→"o", "æ"→"ae", "œ"→"oe"
        -> NFKD -> usunięcie znaków łączących (diakrytyki)
        -> re.sub(r"[^a-z0-9]+", " ") -> strip -> split -> join(" ")
```

Konsekwencje (wszystkie potwierdzone uruchomieniem):

- brak odmiany: `Japonii`, `Niemczech`, `Polsce`, `Węgrzech` **nie** pasują do mianowników;
- brak transliteracji: `Tokyo` ≠ `Tokio`, `Paryz` ≠ `Paris` (to drugie obsługuje wyłącznie
  ręczna `_GEOCODE_CITY_ALIASES`);
- znaki spoza `[a-z0-9]` znikają: `Straße` → `stra e`, `R.A.S.` → `r a s`, cyrylica/CJK → `""`;
- `ß` nie jest rozwijane do `ss`; apostrof (także typograficzny) → spacja.

Inne funkcje normalizacji/porównania nazw (komplet):

| Funkcja | Rola | Lokalizacja |
|---|---|---|
| `_clean_location_component` | tylko wybielenie białych znaków/wartości nie-tekstowych | 1747 |
| `_normalize_location_text` | forma porównawcza (wyżej) | 1753 |
| `_compact_alnum` | normalizacja + tylko `[a-z0-9]` (postcode) | 1781 |
| `_geocode_name_matches` | pełna nazwa albo pełny token/fraza, **bez prefiksów** | 2262 |
| `_geocode_query_variants` | zapytanie + `_GEOCODE_CITY_ALIASES` | 2277 |
| `_geocode_common_prefix_len` / `_geocode_admin_stem_matches` | kontrolowany rdzeń (min 3 znaki tokenu, min 4 wspólne) dla kontekstu administracyjnego | 2662 / 2672 |
| `_geocode_clean_admin_text` | usuwa markery (`powiat`, `koło`, `województwo`, `w pobliżu`…), zachowuje pisownię reszty | 2410 |
| `_normalize_command_text` | normalizacja **komend**, nie nazw miejscowości | 539 |

### 2.3 Rozpoznawanie sufiksu kraju

- `_geocode_country_suffix` (`2350`): tokenizacja `raw.replace(",", " ").split()`, potem sufiksy od
  najdłuższego (≤4 tokeny) do 1; klucz = `_normalize_location_text(suffix)`; trafienie → `(size, code)`.
- `",`` jest separatorem, nie częścią nazwy → na poziomie sufiksu `Hel,PL` i `Hel, PL` są równoważne.
- `_geocode_query_country` (`2326`): `(core, code)`; rdzeń sklejany spacjami (kompresuje przecinki).
- `_geocode_split_country` (`2392`): `(reszta z przecinkami, code)` — używany przez parser etapu 1.2.
- `_geocode_query_parse` (`2442`): najpierw kraj, potem podział reszty:
  - z przecinkiem: segment 0 = `core`, dalsze = `admin_context` (po oczyszczeniu markerów),
  - bez przecinka: marker (`powiat|wojewodztwo|woj|gmina|kolo|okolice|near|around|poblizu|w poblizu`)
    tylko jeśli ma token **przed** i **po** sobie (dlatego `Kolo`, `Wielkie Kolo` zostają w całości),
  - brak markera → całość jest `core`.
- `_GEOCODE_ADMIN_FIELDS` (`2243`): `county, state, region, municipality, province, state_district,
  district, city, town, city_district, suburb`. **`country` nie jest polem administracyjnym** — to
  kluczowy fakt dla obecnego błędu (patrz 2.4).

### 2.4 Zachowanie obecne — zmierzone (nie z pamięci)

| Zapytanie | `_geocode_query_parse` | wysłane do geokodera | `country_codes` | Wynik z kandydatem „Tokio, Japonia” |
|---|---|---|---|---|
| `Tokio` | `("Tokio", [], None)` | `Tokio` | — | `GEOCODE_OK` → etykiety `('Tokio','Tokio, Japonia')` |
| `Tokio, Japonia` | `("Tokio", ["Japonia"], None)` | `Tokio, Japonia` | — | **`GEOCODE_NO_MATCH`** |
| `Tokio, Japan` | `("Tokio", ["Japan"], None)` | `Tokio, Japan` | — | **`GEOCODE_NO_MATCH`** |
| `Tokio, JP` | `("Tokio", ["JP"], None)` | `Tokio, JP` | — | **`GEOCODE_NO_MATCH`** |
| `Tokio Japonia` | `("Tokio Japonia", [], None)` | `Tokio Japonia` | — | **`GEOCODE_NO_MATCH`** |
| `Warszawa, Polska` | `("Warszawa", [], "pl")` | `Warszawa` | `pl` | `GEOCODE_NO_MATCH` (kandydat z JP odrzucony — **poprawnie**) |
| `Atlanta, Georgia` | `("Atlanta", ["Georgia"], None)` | `Atlanta, Georgia` | — | filtr administracyjny (dziś działa, gdy `state == "Georgia"`) |

Mechanizm błędu: nierozpoznany kraj **nie** staje się `country_code`, tylko `admin_context`. Filtr
administracyjny wymaga potwierdzenia segmentu w `_GEOCODE_ADMIN_FIELDS`, a `country` tam nie ma →
kandydat zawsze odpada. Czyli objaw to `GEOCODE_NO_MATCH`, nie „ciche wyszukanie bez kraju”.

### 2.5 Dodatkowy, wcześniej nieopisany defekt: `_geocode_strip_country_suffix`

`location_bot.py:2372-2390` używa `re.search(r"\S+\s*$")` — `\S+` jest zachłanne i **nie zna
przecinka**. Zmierzone:

| Wejście | `_geocode_country_suffix` | `_geocode_split_country` | `_geocode_query_parse` | Skutek |
|---|---|---|---|---|
| `Hel, PL` | `(1,'pl')` | `("Hel",'pl')` | `("Hel",[],'pl')` | OK |
| `Hel,PL` | `(1,'pl')` | **`("Hel,PL", None)`** | **`("Hel",["PL"],None)`** | kraj gubiony w ścieżce runtime (test pokrywa tylko `_geocode_query_country`, nie parse) |
| `Wiązowna,otwock,Polska` | `(1,'pl')` | `("Wiązowna,otwock,Polska", None)` | `("Wiązowna",["otwock","Polska"],None)` | jak wyżej |
| `Nowy Jork,USA` | `(1,'us')` | **`("Nowy",'us')`** | **`("Nowy",[],'us')`** | **utrata tokenu `Jork`** — do geokodera idzie `Nowy` |

To jest realna niespójność kontraktu (helper kraju mówi `us`, ścieżka runtime mówi „nie ma kraju”
albo traci token), a `Hel,PL` jest wprost wymienione w zleceniu jako forma do zachowania.

### 2.6 Walidacja kandydata

- Limit: `GEOCODE_CANDIDATE_LIMIT = 7`, `exactly_one=False`, `addressdetails=True`, `language=lang`,
  `namedetails=True` (best-effort), drabinka `country_codes` → `countrycodes` → bez `namedetails`.
- Jedno miejsce w projekcie, które woła forward geokoder: `_geocode_forward_candidates` (`2941`).
  Reverse: dwa wywołania (`2047` coarse zoom=10, `2095` krok 1) w `get_location_details_from_coords`.
- `_geocode_candidate_is_safe` (`2525`): `class=place` albo `class in (boundary, administrative)` z
  `type|addresstype == administrative`.
- `_GEOCODE_PLACE_FIELDS` (`2209`): `city, town, village, hamlet, locality`.
- Twardy filtr kraju w `_geocode_select_accepted` (`2897-2903`): `country_code` z zapytania musi być
  **równy** `raw["address"]["country_code"]` (casefold); brak kodu u kandydata = odrzucenie.
- Kolejność w `_geocode_select_accepted`: (1) kontekst/administracja, (2) klasa, (3) kraj, (4) dowód
  nazwy, (5) deduplikacja tej samej miejscowości (10 km) → „validated top result”; jeśli istnieje
  kandydat z dowodem `place|localized`, kandydat `display` nie wygrywa; wyłącznie słabe dowody i >1
  rozróżnialne miejsce → `GEOCODE_UNCERTAIN`.
- Siły dowodu: `place` > `localized` > `display` (`_geocode_candidate_evidence`, `2800`).
- `_geocode_query_is_too_short` (`2505`): próg `GEOCODE_MIN_QUERY_CHARS = 3`, liczone po usunięciu
  spacji; **kontekstem zwalniającym jest przecinek lub kod pocztowy** (`_geocode_query_context`).

### 2.7 Etykiety

- `_select_short_location_label` (`1816`): najpierw pola miejscowości (`city, town, village, hamlet,
  locality`), z preferencją pełnego dopasowania do zapytania, potem `municipality, county, state`
  (z polskimi prefiksami `gmina/powiat/województwo` gdy adres jest polski), potem `postcode`, potem
  **`country`**.
- `_format_public_location_parts` (`1933`): short label + `municipality, county, state` + `postcode`
  (tylko gdy użytkownik go wpisał — `_query_mentions_candidate_postcode`) + `country`.
- `namedetails` **nie** służy do budowania etykiety — tylko do walidacji (`localized`). Test pinned:
  `test_required_ok_for_paris_forms` wymaga `short_label == "Paris"` i `"Paryż" not in display`
  (`test_geocode_validation.py:495-503`).
- Zmierzone: adres reverse zawierający **tylko kraj** daje etykietę kraju:
  `_format_public_location_parts({"country":"Japan","country_code":"jp"}, query="Tokio, Japonia")`
  → `('Japan','Japan')`; z `state="Tokyo"` → `('Tokyo','Tokyo, Japan')`.
- `_resolve_location_labels` (`3200`): reverse preferowany; forward fallback wygrywa tylko gdy reverse
  technicznie zawiódł (`GEO_ERROR`/`GEO_NO_CITY`) **albo** gdy etykieta reverse nie pasuje do rdzenia
  zapytania (`_reverse_label_matches_query`), mierzone:
  `"Tokyo"` vs `Tokio, Japonia` → **False**, `"Tokio"` vs `Tokio, Japonia` → True,
  `"Tokio"` vs `Tokio Japonia` → **False** (dziś, bo rdzeń to cała fraza).
- Cache reverse: `(lat,lon,lang,query,mode)` → 24 h, RAM.

### 2.8 Ścieżki wykonania (komplet dla tekstowego geokodowania)

| Ścieżka | Wywołanie | Miejsce |
|---|---|---|
| `/miasto` bez argumentu → prompt | `_set_pending_city(CTX_SAVE_PROFILE)` + `city_prompt` | 4309-4340 |
| `/miasto X`, tekst po prompcie (8B) | `_geocode_query_is_too_short` → `search_loc` → `geocode_city_accepted` → `_resolve_location_labels` → zapis profilu lub karta | 4360-4420 |
| `/dzien|/teraz|/trend X` | `_run_city_oneoff` | 1253-1292 |
| prompt kart jednorazowych (`oneoff_prompt_*`) | `_ask_oneoff_city` + ten sam blok 8B | 603, 3860-3870 |
| skróty `?d/.n/?12/?14` i wzmianka @bot | `handle_guest_now(...)` z wstrzykniętymi `geocode_status_fn`, `resolve_labels_fn`, `shortening_ok_fn`, `too_short_fn` | 3873-3902 |
| GPS z WebApp / pinezka | `_resolve_location_labels(lat, lon, lang)` — **bez query**, bez indeksu krajów | 3969, 4167 |
| `PENDING_SAVE` (zapis po GPS/pinezce) | tylko współrzędne + gotowa etykieta, **zero geokodowania** | 1020-1080 |

### 2.9 Tryb gościa (`guest_bot_handler.py`)

- `resolve_shortcut` → `_clean_location_query` (ucina komentarz po spójniku, ogranicza do 4 tokenów;
  w wariancie z przecinkiem zostawia segmenty, jeśli „reszta” ma ≤2 słowa).
- `_geocode_best_effort`: max **2** zapytania (oryginał + skrót do 2 tokenów), skrót tylko gdy
  `geocode_shortening_is_safe`/`_shortening_keeps_context` na to pozwoli; druga próba tylko dla
  `NO_MATCH`/`NOT_FOUND`.
- `_GEO_CACHE` klucz `(lang, query.lower())`, TTL 1 h, zapis **tylko** dla `GEOCODE_OK`;
  `_normalize_geocode_status_result` obsługuje kontrakt 4- i 5-elementowy.
- `location_bot.geocode_shortening_is_safe` liczy się z `_geocode_query_parse` i
  `_geocode_query_country` → **każda zmiana resolvera kraju automatycznie zmienia zachowanie skrótu**.

### 2.10 Komunikaty

- Statusy: `GEOCODE_OK/TOO_SHORT/NO_MATCH/UNCERTAIN/NOT_FOUND/ERROR` (`i18n.py:2908-2913`);
  `GEOCODE_UI_KEYS` mapuje je na istniejące teksty (`NOT_FOUND→search_fail`,
  `ERROR→geo_conn_err`) — brak nowych tekstów.
- 6 języków UI: `pl, en, de, fr, es, no`, po **89 kluczy** w każdym (brak luk).
- `t_ui` fallback: nieznany język → `pl`; brak klucza → `pl` → nazwa klucza.
- Istniejące komunikaty `geocode_no_match` / `geocode_uncertain` **już** instruują „doprecyzuj,
  dodając region albo kraj” — to jedyny kanał wyjaśnienia wieloznaczności bez nowego tekstu.

### 2.11 Dane w repo i konwencje

- `data/` zawiera wyłącznie Natural Earth (`ne_{10,50,110}m_ocean`, shapefile) — **żadnego pliku
  krajów**.
- Ścieżki danych liczone od pliku: `BASE_DIR = Path(__file__).resolve().parent`
  (`coast_runtime.py:5`), `BASE_DIR = os.path.dirname(os.path.abspath(__file__))`
  (`image_generator.py:8`).
- Wzorzec JSON w repo (`JsonCoastSigStore._load`, `coast_detector.py:366-372`) **połyka** uszkodzony
  plik i zwraca `{}` — dla indeksu krajów to antywzorzec (zakaz „cichego wyszukiwania bez kraju”).

### 2.12 Python 3.8

- Kod produkcyjny jest zgodny z 3.8: brak `removeprefix/removesuffix`, brak PEP 585/604 w adnotacjach,
  `zoneinfo` z fallbackiem `backports.zoneinfo`.
- **Testy nie są** zgodne z 3.8: `test_access_gate.py:138` (`str.removeprefix`) i
  `test_coast.py:353` (`str.removesuffix`). CI i tak używa 3.11, ale nowy kod runtime musi być 3.8-safe.
- Nowy plik danych i loader: `json`, `logging`/`print`, `re`, `unicodedata` — wszystko 3.8-safe.

### 2.13 Spójność danych użytkownika

`users_store.CANONICAL_HEADERS` zawiera `location_label`; `set_profile` zapisuje zaokrąglone
współrzędne (`lat_round`, `lon_round`). Etykieta trafia więc do arkusza Users — **każda zmiana
etykiety to zmiana danych w bazie**, nie tylko kosmetyka karty.

---

## 3. Hipotezy dotyczące Nominatim (NIEROZSTRZYGNIĘTE bez sieci)

Poniższego **nie** traktuję jako faktu — audyt i testy nie wykonują requestów:

1. Czy `Tokio` wraca jako `class=place/type=city`, czy jako `boundary/administrative` (relacja).
2. Jaka jest kolejność wyników dla `Tokio` i czy `limit=7` zwraca oba warianty naraz.
3. Czy Nominatim rozpozna `Japonia` jako kraj przy `language=pl` w `display_name`/`address.country`.
4. Czy `address` jest lokalizowany parametrem `language=pl` (wpływa na nazwę kraju w etykiecie).
5. Czy `country_codes=jp` faktycznie zawęża wyniki (i czy `countrycodes` w starszych geopy działa tak samo).
6. Czy reverse dla punktu w Tokio zwraca `city`, czy tylko `state`/`country`.
7. Czy `country_code` zawsze jest małym ISO alpha‑2 (OSM używa m.in. `xk` dla Kosowa oraz `-99`).
8. Czy `namedetails` (`name:pl`) jest zwracane przez tę wersję geopy/Nominatim.

Wszystkie decyzje projektowe są tak skonstruowane, żeby **nie zależeć** od tych hipotez.

---

## 4. Obecny kontrakt normalizacji i kontrakt proponowany

**Obecny (fakt, §2.2):** jeden algorytm dla miast, krajów i kontekstu administracyjnego; brak
odmiany, transliteracji i obsługi alfabetów niełacińskich.

**Proponowany kontrakt krajów (osobny, jawny, bez zmiany globalnej):**

1. `country_index.normalize_country_name(text)` — **identyczny algorytm** jak
   `_normalize_location_text` (casefold, `ł/ø/æ/œ`, NFKD, usunięcie łączących, `[^a-z0-9]`→spacja).
2. Kontrakt jest zamrożony testem krzyżowym: dla korpusu ~60 napisów (PL/DE/FR/ES/NO diakrytyki,
   `ß`, apostrof typograficzny, myślnik, cyrylica, CJK, cyfry, wielokrotne spacje) musi zachodzić
   `country_index.normalize_country_name(x) == location_bot._normalize_location_text(x)`.
   Dzięki temu resolver krajów nie może „po cichu” poprawić normalizacji miast.
3. Świadomie **nie** dodajemy: odmiany, transliteracji, fuzzy, prefiksów, mapowania `ß→ss`.
   To nie jest dług do spłaty w tej zmianie — to jawnie zadeklarowane ograniczenie (§17).

---

## 5. Projekt globalnego indeksu krajów

### 5.1 Zbiór normatywny

- **249 krajów i terytoriów** z ISO 3166‑1 (wszystkie formalnie przypisane kody alpha‑2,
  `pycountry 26.2.16`), każdy z alpha‑2 + alpha‑3.
- Decyzja D2: czy dodać `xk` (Kosovo, kod użytkowy OSM, poza ISO) — Nominatim może go zwrócić.
- Nazwy: **tylko CLDR** dla języków interfejsu (`pl, en, de, fr, es, no`) — bo tylko te języki
  interfejs faktycznie wspiera (`users_store.SUPPORTED_LANGS`, `UI_TEXTS`).
- Alpha‑2 kody jako aliasy (zachowuje dzisiejsze `PL`, `JP`, `UK→gb`).
- Ręczne aliasy: tylko te, których wymaga obecne zachowanie i realne użycie (§7).

### 5.2 Zmierzone właściwości danych (Babel 2.18.0 / CLDR, pycountry 26.2.16)

| Metryka | Wartość |
|---|---|
| Kraje/terytoria | 249 |
| Wiersze `(język, kraj, nazwa)` | 1494 |
| Braki nazw w 6 językach | **0** |
| Różne nazwy po normalizacji | 928 |
| Kolizje w CLDR (`nazwa → wiele kodów`) | **1**: `kongo → {cd, cg}` |
| Nazwy ≤4 tokenów / >4 | 904 / **24** |
| Rozkład długości (różne nazwy) | 1:592, 2:191, 3:83, 4:38, 5:16, 6:5, 7:1, 8:1, 9:1 |
| Obecne klucze pokryte przez CLDR | 19 z 30 |
| Nowych różnych nazw względem obecnego słownika | 910 |

**Wniosek 1 (zrealizowany w fazie 1):** `_GEOCODE_COUNTRY_MAX_TOKENS = 4` było za małe — 24 nazwy (m.in. `Saint Vincent et
les Grenadines`, `Georgia Południowa i Sandwich Południowy`, `Géorgie du Sud-et-les Îles Sandwich du
Sud` = 9 tokenów) nigdy nie zostałyby rozpoznane. Limit jest **wyprowadzony z danych**
(9), nie zaszyty. *Stan po implementacji: stała usunięta, `meta.max_name_tokens = 9`.*

**Wniosek 2:** kolizje w samych danych CLDR są prawie nieistniejące, ale kolizje semantyczne
(kraj ↔ jednostka administracyjna) są **niewidoczne w danych** — dlatego potrzebna jest jawna,
kuratorowana lista (§7.3).

### 5.3 Format pliku (wersjonowany, w repo)

`data/countries/country_index.json` (generowany offline, commitowany):

```json
{
  "meta": {
    "schema_version": 1,
    "source": {"cldr": "babel==2.18.0", "iso3166": "pycountry==26.2.16",
               "overrides": "country_index_overrides.json@1"},
    "languages": ["pl","en","de","fr","es","no"],
    "country_count": 249,
    "max_name_tokens": 9,
    "payload_sha256": "…"
  },
  "countries": [
    {"alpha_2": "jp", "alpha_3": "jpn",
     "names": {"pl": "Japonia", "en": "Japan", "de": "Japan", "fr": "Japon",
               "es": "Japón", "no": "Japan"}}
  ],
  "lookup":  {"japonia": ["jp"], "japan": ["jp"], "kongo": ["cd","cg"], "uk": ["gb"]},
  "context_ambiguous": {
    "georgia": {"codes": ["ge"], "reason": "kraj GE + stan US-GA (nazwa kraju w EN/ES/NO)"}
  }
}
```

Zmierzone rozmiary prototypu: **61 187 B** (compact) / **89 520 B** (`indent=1`), 1188 kluczy
`lookup`, 1 kolizja. Mieści się bez problemu w repo (dla porównania `data/` = 7,9 MB).

Zasady:
- wartością zawsze **zbiór** kodów (lista posortowana), nigdy nadpisanie „ostatnim”;
- `lookup` zawiera wyłącznie klucze znormalizowane **kontraktem kraju** (§4);
- `context_ambiguous` to osobny słownik (nie miesza się z kolizjami kodów);
- kolejność kluczy i kodów deterministyczna (`sort_keys=True`), `ensure_ascii=False`.

### 5.4 Moduł runtime `country_index.py` (nowy, bez zależności)

Publiczne API (minimalne, bez „bogatych obiektów” tam, gdzie wystarczy kod):

```python
NONE, UNIQUE, AMBIGUOUS, CONTEXT_AMBIGUOUS = "none","unique","ambiguous","context_ambiguous"

CountryMatch = namedtuple("CountryMatch", "kind codes")     # codes: tuple[str, ...]

def normalize_country_name(text) -> str          # kontrakt z §4
def get_index() -> CountryIndex                  # lazy singleton, 3.8-safe
def classify(name) -> CountryMatch               # pojedyncza nazwa (znormalizowana wewnątrz)
def resolve_suffix(raw) -> (int, CountryMatch)   # (rozmiar sufiksu w tokenach, klasyfikacja)
def max_name_tokens() -> int                     # z meta indeksu (9)
```

- `resolve_suffix` zwraca `(0, NONE)` gdy brak trafienia; `size` liczony w **surowych** tokenach
  (potrzebne do bezstratnego odcięcia sufiksu).
- Zero importów trzecich; tylko `json`, `re`, `unicodedata`, `os`, `threading` (`lru_cache` /
  `threading.Lock`), `hashlib` (opcjonalna weryfikacja sumy).
- Ładowanie **raz** przy pierwszym użyciu (≈1–3 ms dla ~60–90 KB); brak I/O per zapytanie.

### 5.5 Walidacja kompletności (przy ładowaniu i w testach)

1. `schema_version` znana;
2. `country_count` zgodne z długością `countries`;
3. `alpha_2` unikalne, `^[a-z]{2}$`, `alpha_3` `^[a-z]{3}$`;
4. każdy kraj ma **wszystkie** zadeklarowane języki, wartości niepuste;
5. każdy klucz `lookup` niepusty, kody istnieją w `countries`;
6. każdy kod z `context_ambiguous` istnieje;
7. `payload_sha256` zgodny (jeśli obecny);
8. brak duplikatów `(alpha_3)`. Naruszenie któregokolwiek ⇒ indeks **niedostępny** (§10).

### 5.6 Zachowanie przy braku/uszkodzeniu pliku — fail-closed (D7)

**Brak pliku / zły JSON / walidacja negatywna ⇒ tekstowe geokodowanie zgłasza `GEOCODE_ERROR`
(istniejący komunikat `geo_conn_err`), bez requestu, bez karty, bez zapisu profilu.**
Nigdy nie następuje „ciche wyszukiwanie bez filtra kraju”, **żadnego fallbacku do starego słownika**
(to złamałoby zasadę). Dodatkowo:
- głośny log przy **pierwszym** ładowaniu (sukces i błąd) — bez sekretów;
- GPS/pinezka/WebApp (`_resolve_location_labels` bez query) **nie** korzystają z indeksu → działają;
- `TOO_SHORT` pozostaje pierwszy (nie dotyka mapy i nie potrzebuje indeksu);
- decyzja D7: alternatywa „tylko zapytania z segmentem po przecinku są blokowane” — **odrzucona**:
  nie da się lokalnie odróżnić segmentu kraju od kontekstu, więc byłoby to ciche wyszukiwanie bez filtra.

### 5.7 Wdrożenie pliku danych (wymóg operacyjny)

Ponieważ fail-closed jest ostry, plik `data/countries/country_index.json` **musi** znaleźć się
w artefakcie uruchomieniowym na Oracle (deploy kopiuje całe repo; plik jest commitowany).
Zabezpieczenia:

| Mechanizm | Treść |
|---|---|
| test obecności i poprawności | `test_country_index.py` sprawdza istnienie pliku pod ścieżką liczoną od `country_index.__file__` oraz pełną walidację treści |
| test „commitowany” | ten sam plik sprawdzany przez `git ls-files` (pomijany, gdy brak `.git`) — łapie sytuację „plik jest lokalnie, ale nie trafił do repo” |
| log przy pierwszym ładowaniu | jedna linia `[COUNTRY_INDEX] …` (sukces: liczba krajów/nazw/schema; błąd: powód + informacja o fail-closed) |
| instrukcja wdrożeniowa | sekcja w `README.md` (co deployuje, jak sprawdzić po wdrożeniu) |
| kontrola w CI (D10) | workflow `country_index.yml`: `pip install -r requirements-tools.txt` + `python tools/build_country_index.py --check` (deterministyczna regeneracja bit-w-bit) |


### 5.7 Co indeks pokrywa, a czego nie (jawnie)

| Wymiar | Status |
|---|---|
| Pokrycie krajów/terytoriów | pełne ISO 3166‑1 (249) + ew. `xk` |
| Języki nazw | **tylko** `pl, en, de, fr, es, no` (języki UI); innych nie deklarujemy |
| Alfabety | tylko łaciński (normalizacja odrzuca CJK/cyrylicę) |
| Transliteracja | brak (`Tokyo` ≠ `Tokio` poza `_GEOCODE_CITY_ALIASES`) |
| Aliasy | kod alpha‑2 + krótka, kuratorowana lista (§7.1); alpha‑3 **przechowywane, nie używane do dopasowania** (D5) |
| Odmiana gramatyczna | **brak** (`Japonii`, `Niemczech` nie zadziałają) |
| Literówki / fuzzy | **brak** |

---

## 6. Źródło i wersja danych

| Źródło | Rola | Wersja | Licencja | Runtime? |
|---|---|---|---|---|
| **pycountry** | kody alpha‑2/alpha‑3, zbiór ISO 3166‑1 | `26.2.16` | LGPL‑2.1‑only | **NIE** (tylko generator) |
| **Babel** (pakiet) | dostęp do danych CLDR: `Locale.territories` | `2.18.0` | BSD‑3‑Clause | **NIE** (tylko generator) |
| **CLDR** (dane w Babel) | nazwy krajów w 6 językach | wersja związana z Babel 2.18.0 (Babel dostarcza `LICENSE.unicode`) | Unicode License | dane pochodne w pliku JSON |
| `country_index_overrides.json` | aliasy ręczne + `context_ambiguous` | `1` (własna wersja) | wewnętrzna | – |

Konsekwencje:
- **`requirements.txt` bez zmian** — produkcja nie importuje Babel/pycountry.
- Nowa zależność tylko dla narzędzi: `requirements-tools.txt` z **przypiętymi** wersjami
  (`babel==2.18.0`, `pycountry==26.2.16`). Nie doklejam ich do `requirements-dev.txt`, żeby nie
  wydłużać instalacji testów; decyzja D1.
- Ponieważ w repo commitowane są **nazwy pochodne CLDR**, dodaję `data/countries/NOTICE.md`
  z atrybucją (Unicode License, wersja CLDR/Babel) — wymóg dystrybucji danych.
- Reprodukcja: `python tools/build_country_index.py` (deterministyczne wyjście bit‑w‑bit);
  opcjonalny tryb `--check` porównujący z plikiem w repo (patrz D9/CI).

---

## 7. Aliasy i zasady kolizji

### 7.1 Aliasy ręczne (wymagane do zachowania obecnego zachowania)

Z 30 obecnych kluczy: 19 pochodzi z CLDR, 7 to kody alpha‑2 (warstwa kodów), `españa` to duplikat
po normalizacji. Ręcznie trzeba dopisać **4**:

| Alias | Kod | Powód |
|---|---|---|
| `uk` | `gb` | CLDR nie ma „UK”; test `test_uk_is_gb_like_nominatim` |
| `great britain` | `gb` | test `test_uk_is_gb_like_nominatim` |
| `united states of america` | `us` | test `test_country_parser_prefers_the_longest_suffix` |
| `ameryka` | `us` | test `test_uk_is_gb_like_nominatim` |

Opcjonalne (decyzja D6 — **mapowanie jednostek subnarodowych na kod kraju**): `england/anglia`,
`scotland/szkocja`, `wales/walia`, `northern ireland` → `gb`; `holland/holandia` → `nl`.
Domyślnie **nie** dodaję: to nie są kraje ISO, a `Londyn, Anglia` działa dzisiaj przez kontekst
administracyjny.

### 7.2 Zasady

1. Dopasowanie **wyłącznie pełno‑frazowe** na znormalizowanym całym sufiksie. Niger ≠ Nigeria,
   Guinea ≠ Guinea‑Bissau, Congo ≠ DR Congo, `korea` ≠ `korea poludniowa`.
2. Wartość = zbiór kodów; `len > 1` ⇒ `AMBIGUOUS`, żadnego wyboru.
3. Kody alpha‑2 dopasowywane tylko jako **izolowany końcowy segment** (tak jak dziś).
4. Brak dopasowania prefiksowego i brak „najdłuższego wspólnego początku” dla krajów
   (`_geocode_admin_stem_matches` **nie** jest używany dla krajów).
5. Nazwa kraju **nie** staje się krajem, jeśli jest w `context_ambiguous`.

### 7.3 `context_ambiguous` — **wąska** lista (decyzja D3, skorygowana)

Zasada (po korekcie właściciela produktu): wpis do `context_ambiguous` jest **override'em
wyłączającym filtr kraju** i wolno go dodać **wyłącznie** wtedy, gdy spełnione są jednocześnie:

1. istnieje **realnie obsługiwana forma zapytania** (dziś: `X, Segment` w składni etapu 1.2),
2. da się zbudować **syntetycznego kandydata** odtwarzającego regresję,
3. istnieje **test**, który pokazuje, że globalne rozpoznanie kraju psuje dotychczasowy wynik.

Sama zbieżność nazwy kraju z nazwą obiektu/rzeki/nazwy własnej to **za mało** — takie przypadki
obsługuje pełno-frazowe dopasowanie i testy negatywne, bez wyłączania filtra.

**Faza 1 — dokładnie dwa wpisy** (potwierdzona regresja na istniejącej składni):

| Nazwa | Kod | Uzasadnienie |
|---|---|---|
| `georgia` | `ge` | `Atlanta, Georgia` — stan USA obsługiwany dziś przez `admin_context`; bez override'u kraj `ge` dawałby `NO_MATCH` |
| `wlochy` | `it` | `Warszawa, Włochy` — dzielnica Warszawy obsługiwana dziś przez `admin_context`; bez override'u kraj `it` dawałby `NO_MATCH` |

**Świadomie NIE są** wpisami `context_ambiguous` (mimo że były w pierwszej wersji raportu):
`jordan`, `chad`, `niger`, `guinea`. Dla nich nie wykazano regresji istniejącej formy zapytania,
więc zostają w globalnym resolverze i mają wyłącznie **testy pełnego dopasowania**:

| Test dla `jordan` / `chad` / `niger` / `guinea` | Asercja |
|---|---|
| pełne dopasowanie nazwy | `classify("niger") == UNIQUE(['ne'])`, `classify("guinea") == UNIQUE(['gn'])`, … |
| brak dopasowania prefiksowego | `classify("niger") != classify("nigeria")`; `guinea` ≠ `guinea bissau`; `congo` ≠ `democratic republic of the congo` |
| kandydat z innym `country_code` | `X, Niger` + kandydat z `country_code="ng"` → odrzucony (twardy filtr) |

Pozostałe nazwy z pierwszej wersji raportu są rozstrzygane bez override'u:

| Nazwa | Klasyfikacja | Podstawa |
|---|---|---|
| `kongo` | `AMBIGUOUS` (`cd`, `cg`) | kolizja w danych CLDR — kod **nie** jest wybierany, segment zachowuje dzisiejszą rolę |
| `korea` | `NONE` | brak nazwy kraju „Korea” w CLDR (są tylko `korea poludniowa` / `korea polnocna`) — brak arbitralnego wyboru |
| `macedonia` | `NONE` | CLDR ma tylko `macedonia polnocna` |
| `węgry`, `szwecja`, `dania` | **poza zakresem fazy 1** | brak potwierdzonej regresji istniejącej formy zapytania; mogą dołączyć później wyłącznie według powyższych trzech kryteriów |

Wpis niepotrzebny jest nieszkodliwy (kosztem utraty filtra kraju), wpis brakujący jest szkodliwy
(fałszywe `NO_MATCH`) — ale to nie uzasadnia dopisywania wpisów bez dowodu regresji, bo każdy wpis
osłabia globalne rozpoznawanie kraju. Dlatego lista jest zamknięta na dwa wpisy i pilnowana testem.

---

## 8. Reguły parsera (projekt; gramatyka bez zmian)

Gramatyka **zostaje** (kolejność: kraj → przecinki → markery), wymieniamy wyłącznie silnik kraju.

| # | Reguła |
|---|---|
| R1 | Tokenizacja bez zmian (spacje; przecinek jest separatorem sufiksu). |
| R2 | Suffiksy długości `1 … min(max_name_tokens, len(tokens)-1)`, od najdłuższego. |
| R3 | `max_name_tokens` **z indeksu** (dziś 9), `_GEOCODE_COUNTRY_MAX_TOKENS` staje się pochodną danych. *(zrealizowane: stała usunięta)* |
| R4 | Klasyfikacja = `country_index.resolve_suffix(raw)` → `(size, CountryMatch)`. |
| R5 | `NONE` → dokładnie dzisiejsze zachowanie (segment zostaje kontekstem administracyjnym / częścią core). |
| R6 | `UNIQUE(code)` → odetnij sufiks (z zachowaniem przecinków), przekaż `country_codes=code`, twardy filtr bez zmian. |
| R7 | `AMBIGUOUS(codes)` → **żadnego kodu**, segment zachowuje dzisiejszą rolę (kontekst administracyjny). |
| R8 | `CONTEXT_AMBIGUOUS` → wariant A (minimalny): jak R7; wariant B (rekomendowany): segment wraca do tekstu zapytania, bez `country_codes`, z lokalną walidacją rozłączną „kraj **lub** kontekst” (§8.1). |
| R9 | Kraj nigdy nie jest wycinany z całego zapytania (`len(tokens)-1`) — `Polska`, `Japonia`, `Kongo` pozostają zapytaniem o miejscowość. |
| R10 | **Naprawa `_geocode_strip_country_suffix`**: odcinanie po tokenach z poszanowaniem przecinków (bez `\S+`), zachowanie pisowni reszty, `"Nowy Jork,USA"` → `"Nowy Jork"`, `"Hel,PL"` → `"Hel"`, `"Wiązowna,otwock,Polska"` → `"Wiązowna,otwock"`. |
| R11 | `_geocode_query_country`, `_geocode_split_country`, `_geocode_query_parse` zachowują sygnatury i **wszystkie dotychczas testowane wyniki** (lista w §15.3). |
| R12 | Zero nowych requestów: ta sama pojedyncza próba forward, ta sama drabinka parametrów, `country_codes` tylko dla `UNIQUE`. |
| R13 | Skrót gościa (`geocode_shortening_is_safe`) automatycznie dziedziczy nowe reguły — bez zmian w `guest_bot_handler.py`. |

### 8.1 Wariant B dla nazw `context_ambiguous` (do akceptacji)

Bez drugiego requestu i bez osłabiania filtra dla krajów jednoznacznych:
zapytanie tekstowe pozostaje `"Atlanta, Georgia"`, **bez** `country_codes`, a w
`_geocode_select_accepted` dochodzi jeden warunek lokalny:

```
kandydat OK, jeżeli  country_code == code  LUB  segment pasuje do pól administracyjnych kandydata
```

Zalety: `Atlanta, Georgia` (stan) i `Tbilisi, Georgia` (kraj) oba działają.
Wady: nowy tryb walidacji w `_geocode_select_accepted` (większa powierzchnia zmian), a dla tych nazw
filtr jest słabszy niż „kraj albo nic”.
Wariant A (R7) jest w 100% zgodny z dzisiejszym zachowaniem i nie wymaga nowej logiki.

**Weryfikacja na istniejącym silniku (bez implementacji):**

| Scenariusz | Wynik |
|---|---|
| `_geocode_select_accepted("Tokio", [tokio_jp], "jp", [])` | `geocode_ok`, etykiety `('Tokio','Tokio, Japonia')` |
| `_geocode_select_accepted("Atlanta", [atlanta_us], None, ["Georgia"])` | `geocode_ok` (wariant A) |
| `_geocode_select_accepted("Atlanta", [atlanta_us], "ge", [])` | `geocode_no_match` ← **regresja, której unikamy** |
| `_geocode_select_accepted("Warszawa", [warszawa_włochy], None, ["Włochy"])` | `geocode_ok` (wariant A) |
| `_geocode_select_accepted("Warszawa", [warszawa_włochy], "it", [])` | `geocode_no_match` ← **regresja, której unikamy** |

---

## 9. Tabela przypadków brzegowych (stan obecny → po zmianie)

| # | Zapytanie | Dziś | Po zmianie (wariant A) | Status |
|---|---|---|---|---|
| 1 | `Tokio, Japonia` | `NO_MATCH` | `jp`, rdzeń `Tokio` | **naprawione** |
| 2 | `Tokio, Japan` | `NO_MATCH` | `jp` | **naprawione** |
| 3 | `Tokio, JP` | `NO_MATCH` | `jp` | **naprawione** |
| 4 | `Tokio Japonia` | `NO_MATCH` (cała fraza jako rdzeń) | `jp`, rdzeń `Tokio` | **naprawione** |
| 5 | `Warszawa, Polska` | `pl` | `pl` | bez zmian |
| 6 | `Hel PL` | `pl` | `pl` | bez zmian |
| 7 | `Hel,PL` | kraj **gubiony** w runtime (`["PL"]` jako kontekst) | `pl` + poprawne odcięcie | **naprawione (R10)** |
| 8 | `Nowy Jork,USA` | rdzeń `Nowy` (!) | rdzeń `Nowy Jork` + `us` | **naprawione (R10)** |
| 9 | `Wiązowna, otwock` | kontekst `otwock` | kontekst `otwock` | bez zmian |
| 10 | `Wiązowna, otwock, Polska` | `pl` + kontekst `otwock` | to samo | bez zmian |
| 11 | `Olsztyn koło Częstochowy` | kontekst `Częstochowy` | to samo | bez zmian |
| 12 | `Olsztyn województwo śląskie` | kontekst `śląskie` | to samo | bez zmian |
| 13 | `Polska` / `Japonia` / `Kongo` | rdzeń, bez kraju | to samo (R9) | bez zmian |
| 14 | `Tokio, Japonia, Polska` | `pl` + kontekst `Japonia` → `NO_MATCH` | to samo: **tymczasowa kompatybilność wstecz** (patrz §10.1) | bez zmian — **ograniczenie, nie reguła** |
| 15 | `Tokio, JP, Japonia` | brak kraju, kontekst `JP`,`Japonia` | `jp` + kontekst `JP`? → **do ustalenia w testach**: ostatni wygrywa → `jp`, kontekst `JP` | uwaga: podwójne wskazanie |
| 16 | `Kongo` / `X, Kongo` | brak kraju / kontekst | `AMBIGUOUS` → kontekst (bez zmiany zachowania) | bez zmian |
| 17 | `Korea` / `X, Korea` | brak kraju / kontekst | to samo (brak w indeksie) | bez zmian |
| 18 | `Atlanta, Georgia` | kontekst (działa, gdy `state=Georgia`) | to samo (wariant A) | bez zmian |
| 19 | `Warszawa, Włochy` | kontekst (działa, gdy `city_district=Włochy`) | to samo (wariant A) | bez zmian |
| 20 | `Niger` / `X, Niger` | brak / kontekst | `ne` (lub `CONTEXT_AMBIGUOUS` jeśli D3) | decyzja D3 |
| 21 | `X, Nigeria` | kontekst | `ng`; **Niger nie złapie Nigerii** | naprawione |
| 22 | `X, Guinea-Bissau` | kontekst | `gw`; `Guinea` nie złapie `Guinea-Bissau` | naprawione |
| 23 | `X, Saint Vincent et les Grenadines` (5 tokenów) | brak | `vc` (R3) | naprawione |
| 24 | `Wiązowna, kraj Japonia` | kontekst `kraj Japonia` → `NO_MATCH` | sufiks `japonia` → `jp`, kontekst `kraj` → prawdopodobnie `NO_MATCH` | ograniczenie: `kraj` nie jest markerem |
| 25 | `Tokio, JP,` (przecinek końcowy) | kraj gubiony | `jp` | naprawione |
| 26 | `Tokio, JP.` (kropka) | kraj gubiony w runtime | `jp` | naprawione |
| 27 | `Tokio, Japonii` (dopełniacz) | kontekst → `NO_MATCH` | to samo (brak odmiany — jawne ograniczenie) | bez zmian |
| 28 | `東京, 日本` | normalizacja → `""` | to samo (brak alfabetów niełacińskich) | bez zmian (jawnie) |
| 29 | `Hel, Portugalia` | kontekst → `NO_MATCH` | `pt` → `NO_MATCH` | status bez zmian, mechanizm inny (test zostaje zielony) |
| 30 | `Wa, Australia` | kontekst → `NO_MATCH` | `au` → `NO_MATCH` (dowód nazwy `wa` nie przechodzi) | status bez zmian |
| 31 | `Kinshasa, Democratic Republic of the Congo` | kontekst → `NO_MATCH` | pełna nazwa → `cd`, rdzeń `Kinshasa`, kandydat Kinszasy przechodzi | **naprawione (alias DR Konga, §19.6)** |
| 32 | `X, Papua New Guinea` / `South Sudan` / `Caribbean Netherlands` | kontekst | pełna nazwa wygrywa z ostatnim tokenem → `pg` / `ss` / `bq` | naprawione (najdłuższy sufiks) |
| 33 | `X, Trinidad and Tobago` (z „and”) | kontekst | `tt` (jawny alias) | **naprawione (§19.6)**; `Bosnia and Herzegovina` / `Antigua and Barbuda` pozostają ograniczeniem |
| 34 | `X, Trinidad & Tobago`, `X, Trynidad i Tobago` | kontekst | `tt` | naprawione |
| 35 | `X, Holandia` | kontekst | `nl` (to nazwa CLDR dla NL, nie nasz alias) | naprawione, dane |
| 36 | `X, Anglia` / `X, England` | kontekst | kontekst (D6: bez aliasów subnarodowych) | bez zmian |

---

## 10. Zachowanie dla braku, wieloznaczności i sprzeczności

| Sytuacja | Zachowanie | Uzasadnienie |
|---|---|---|
| Indeks brak/uszkodzony | `GEOCODE_ERROR` (bez requestu), brak karty i zapisu profilu, głośny log; GPS/pinezka działają | zero „cichego wyszukiwania bez filtra kraju” |
| Nazwa nieznana (`NONE`) | segment **nie** jest krajem; dzisiejsza rola | „brak rozpoznania lokalnie nie dowodzi, że to kraj” |
| Nazwa → wiele kodów (`AMBIGUOUS`) | żadnego kodu, żadnego odcięcia; segment jak dziś | zakaz wybierania pierwszego/ostatniego, zakaz wyszukiwania bez ograniczenia |
| Nazwa kraj ↔ kontekst (`CONTEXT_AMBIGUOUS`) | wariant A: jak dziś; wariant B: walidacja rozłączna | decyzja D3 |
| Sprzeczne wskazania (`X, Japonia, Polska`) | **tymczasowa kompatybilność wstecz**: parsowany jest wyłącznie ostatni segment kraju, wcześniejszy zostaje kontekstem administracyjnym; wynik to zwykle `NO_MATCH` | **to nie jest poprawne rozstrzyganie sprzeczności** — patrz §10.1; brak nowych requestów i brak zmian kontraktu |
| Segment nierozpoznany (`X, otwock`) | kontekst administracyjny (jak dziś) | „brak markera «powiat» nie czyni segmentu krajem” |
| Wieloznaczność a komunikaty | **bez nowego komunikatu**; istniejące `geocode_no_match`/`geocode_uncertain` już instruują „dodaj region albo kraj” | §2.10; ewentualny nowy status = decyzja D4 |

### 10.1 Sprzeczne wskazania kraju — jawnie jako dług, nie jako reguła

Dotychczasowe zachowanie („parsowany jest wyłącznie ostatni segment kraju”) **nie jest** sposobem
rozstrzygania sprzeczności i nie wolno go tak opisywać ani tak traktować. Jest to **wyłącznie
kompatybilność wsteczna** istniejącej gramatyki, utrzymana w fazie 1 pod trzema warunkami
zaakceptowanymi przez właściciela produktu:

1. **brak zmiany publicznego kontraktu** (`_geocode_query_parse` zwraca nadal
   `(core, admin_context, code)`),
2. **brak dodatkowego requestu** (nadal dokładnie jedna próba forward),
3. **osobny test regresyjny** dokumentujący to zachowanie jako ograniczenie.

W kodzie i w dokumentacji musi być to nazwane wprost, np. w docstringu: „sprzeczne wskazania kraju:
faza 1 zachowuje dotychczasowe zachowanie (ostatni parsowany segment); to ograniczenie gramatyki do
poprawy w kolejnej fazie, a nie rozstrzygnięcie”. Poprawne rozstrzyganie (lokalne odrzucenie bez
requestu albo nowy status) jest **odłożone** — wymaga zmiany liczby requestów lub kontraktu komunikatów.

---

## 11. Projekt dowodu dopasowania (evidence)

Stan obecny: siła dowodu jest liczona w `_geocode_candidate_evidence` i **wykorzystywana tylko
wewnątrz** `_geocode_select_accepted` (do wyboru strong/weak). Do etykiet nie trafia nic.

### 11.1 Struktura (wewnętrzna, addytywna)

```python
GeocodeEvidence = namedtuple(
    "GeocodeEvidence",
    "matched strength source field raw_value",   # matched: nazwa znormalizowana
)   # source: "address" | "namedetails" | "raw"
    # field : "city" | "town" | … | "name:pl" | "name" | "display_name_head" | "postcode"
    # raw_value: oryginalna (nieznormalizowana) wartość z kandydata
```

### 11.2 Kto produkuje / konsumuje

| Funkcja | Zmiana |
|---|---|
| `_geocode_candidate_evidence_detail(...)` | **nowa**: pełny dowód (źródło + pole + surowa wartość + siła + dopasowana nazwa) |
| `_geocode_candidate_evidence(...)` | zostaje jako cienki wrapper → `(matched, strength)` (**kontrakt testowy bez zmian**) |
| `_geocode_select_accepted_detail(...)` | **nowa**: `(status, location, evidence)` |
| `_geocode_select_accepted(...)` | zostaje: `(status, location)` (7 miejsc w testach) |
| `_geocode_city_accepted_detail(...)` | **nowa**: `(status, lat, lon, short, display, evidence)` |
| `geocode_city_accepted(...)` | **bez zmian**: 5‑krotka (publiczny kontrakt) |
| `_resolve_location_labels(...)` | nowy **opcjonalny** parametr `forward_evidence=None` (zgodność wstecz) |

Źródła i siły — bez zmian: `address.city/town/village/hamlet/locality` (`place`), `namedetails`
(`localized`, tylko przy istniejących polach miejscowości), `name`/`display_name` (`display`, słaby).
Nie promujemy `display` do `place`; nie używamy tekstu użytkownika jako etykiety.

### 11.3 Rozszerzenie kontraktu gościa (tylko jeśli faza 2)

`geocode_city_labels_status` (5 elementów) i `_normalize_geocode_status_result` (4/5) wymagałyby
wariantu 6‑elementowego. To **zmiana kontraktu półpublicznego** → osobna decyzja (D9), poza fazą 1.

---

## 12. Projekt końcowej etykiety

### 12.1 Reguły zachowane bez zmian (faza 1)

- źródło etykiety = wyłącznie strukturalne `address` zaakceptowanego kandydata;
- kolejność: `city/town/village/hamlet/locality` → `municipality/county/state` (+ prefiksy PL) →
  `postcode` (tylko gdy użytkownik go wpisał) → `country`;
- `namedetails` **nie** buduje etykiety (test pinned „Paryż” nie w etykiecie);
- `display_location` = short_label + admin + ew. postcode + country;
- etykiety reverse i forward przechodzą przez **ten sam** formatter.

### 12.2 Dwie luki wskazane przez audyt (do decyzji, faza 2)

| Luka | Dziś | Propozycja oparta na dowodzie |
|---|---|---|
| reverse zwraca **tylko kraj** | tytuł = „Japan”/„Japonia” (`_format_public_location_parts` → fallback `country`) | nie zmieniamy formattera; rozstrzygamy w `_resolve_location_labels` (§13) |
| forward potwierdzony tylko przez `namedetails` (`name:pl = Paryż`), a `address` nie ma pól miejscowości | etykieta spada do `state`/`country` | dopuścić wartość z `namedetails` **jako ostatnią deskę** wyłącznie gdy: brak pól miejscowości **oraz** dowód = `localized` (nie `display`, nie tekst użytkownika) |

Druga zmiana nie łamie istniejącego testu (`Paryż, Francja` ma `city=Paris` → reguła nie działa), ale
**zmienia etykiety w bazie** → wymaga akceptacji (D9), bo dotyka `Users.location_label`.

---

## 13. Analiza reverse-geocoding i reguła wyboru etykiety

Fakty: dwa wywołania reverse (krok 1 `zoom` domyślny, krok 2 `zoom=10` tylko dla POI/pinezki bez
query); `language=lang`; cache 24 h po `(lat,lon,lang,query,mode)`; `query` wpływa na `mode` (road)
i na postcode gating, więc reverse **nie jest** niezależny od zapytania.

| Przypadek | Zachowanie dziś | Propozycja |
|---|---|---|
| reverse = miejsce (`city/town/village`) | zostaje | zostaje (nadrzędne) |
| reverse = kraj tylko | etykieta kraju; forward wygrywa **tylko** dzięki przypadkowej niezgodności stringów | jawna reguła: reverse‑source=`country` + forward `place/localized` ⇒ forward |
| reverse = nazwa wyższego poziomu (`City of Westminster`) | niezgodność z rdzeniem ⇒ forward | jawna reguła: reverse‑source ∈ `state/county/municipality/postcode` + forward `place/localized` ⇒ forward |
| forward potwierdzony w `state/region` (nie w mieście) | etykieta z `state` z prefiksem | bez zmian (nie promujemy regionu do miejscowości) |
| `namedetails name:pl` | nieużywane | ewentualnie §12.2 (faza 2) |
| forward = tylko `display` (słaby dowód) | **może** nadpisać reverse (brak wiedzy o sile w resolverze) | **nie może**: słaby forward nigdy nie wypiera poprawnego reverse |

Reguła (bez zasady „forward zawsze wygrywa”):

```
1. brak forward fallbacku                     -> reverse
2. reverse_source ∈ pola miejscowości         -> reverse
3. forward_evidence.strength == display       -> reverse
4. reverse_source ∈ {state, county, municipality, postcode, country}
   i forward_evidence.strength ∈ {place, localized}
   i forward etykieta != nazwa kraju          -> forward
5. w pozostałych przypadkach                  -> reverse (domyślnie)
```

Wymaga: `_select_short_location_label_detail` (którym polem wybrano etykietę),
`_format_public_location_parts_detail`, opcjonalnych parametrów w `_resolve_location_labels`.
**Faza 1 nie zmienia etykiet** — bez tego ryzyko dryfu `Users.location_label` jest zbyt duże, by
mieszać to z poprawką kraju.

---

## 14. Dokładna lista proponowanych plików do zmiany

### 14.1 Nowe (faza 1)

| Plik | Zawartość |
|---|---|
| `country_index.py` | loader + `classify` / `resolve_suffix` / `normalize_country_name`, walidacja, fail‑closed, zero zależności, 3.8‑safe |
| `data/countries/country_index.json` | generowany, wersjonowany indeks (§5.3) |
| `data/countries/NOTICE.md` | atrybucja CLDR/Unicode + wersje + instrukcja regeneracji |
| `tools/build_country_index.py` | generator offline (Babel + pycountry), deterministyczny, `--check` |
| `tools/country_index_overrides.json` | aliasy ręczne + `context_ambiguous` (kuratorowane, wersjonowane) |
| `requirements-tools.txt` | `babel==2.18.0`, `pycountry==26.2.16` (tylko narzędzia) |
| `test_country_index.py` | testy danych/resolvera (bez sieci) |
| `test_geocode_country_forms.py` | testy parsera, kolizji i integracji przez `geocode_city_accepted` |

### 14.2 Zmieniane (faza 1)

| Plik | Zakres |
|---|---|
| `location_bot.py` | `_geocode_country_suffix` (użycie resolvera, limit z danych), `_geocode_strip_country_suffix` (naprawa R10), `_geocode_country_suffix`/`_geocode_query_country`/`_geocode_split_country`/`_geocode_query_parse` (sygnatury bez zmian), ewentualnie `_geocode_select_accepted` (tylko wariant B), usunięcie `_GEOCODE_COUNTRY_ALIASES` (zastąpione indeksem + overrides) |
| `README.md` | sekcja „Źródła danych krajów i regeneracja” |

### 14.3 Świadomie **bez zmian** w fazie 1

`guest_bot_handler.py` (dziedziczy przez wstrzyknięte callbacki), `i18n.py` (chyba że D4),
`users_store.py`, `main_card.py`, `image_generator.py`, `weather_payload.py`, `prepare_*.py`,
`requirements.txt`, `.github/workflows/*`, `conftest.py`, `pytest.ini`, `test_geocode_validation.py`
(nowe testy w nowych plikach; ewentualnie 1–2 testy dopisane, jeśli okaże się to konieczne).

### 14.4 Faza 2 (osobna akceptacja D9)

`location_bot.py` (evidence + `_resolve_location_labels` + etykiety), ewentualnie
`guest_bot_handler.py` (kontrakt 6‑elementowy), `test_geocode_validation.py`/
`test_card_caption.py` (nowe przypadki).

---

## 15. Macierz testów (offline, `pytest-socket` już aktywny, zero Nominatim)

### 15.1 Dane i resolver (`test_country_index.py`)

| Test | Asercja |
|---|---|
| kompletność | 249 krajów, każdy z alpha‑2/alpha‑3 i 6 językami, 0 braków |
| kody ISO | `^[a-z]{2}$`, `^[a-z]{3}$`, unikalność, zgodność alpha‑2↔alpha‑3 |
| każda nazwa kanoniczna | `classify(name)` dla każdej z 1494 wartości zwraca własny kod |
| aliasy | wszystkie 30 dawnych kluczy zachowują dzisiejszy kod (w tym `uk→gb`, `usa→us`, `ameryka→us`) |
| nazwy wielowyrazowe | 5–9 tokenów (`Saint Vincent…`, `Georgia Południowa…`) rozpoznane |
| kolizje | `kongo → ('cd','cg')` i `kind == AMBIGUOUS`; **żaden** kod nie jest wybierany |
| `context_ambiguous` | każdy wpis → `CONTEXT_AMBIGUOUS`, kod zgodny z indeksem |
| pełna fraza | `niger` nie pasuje do `nigeria`, `guinea` ≠ `guinea bissau`, `congo` ≠ `democratic republic of the congo`, `korea` ≠ `korea poludniowa` |
| normalizacja | test krzyżowy z `_normalize_location_text` (korpus 60 napisów) |
| brak pliku | `IndexUnavailable` / `classify` → `NONE` + flaga `unavailable` (bez wyjątku na ścieżce GPS) |
| uszkodzony JSON / zła schema / zły hash / brak języka / nieznany kod | indeks niedostępny (fail‑closed) |
| duplikaty | dwa kraje z tym samym alpha‑2 → walidacja odrzuca |
| determinizm | dwa uruchomienia generatora → identyczny bajtowo plik |

### 15.2 Parser (`test_geocode_country_forms.py`)

Wymagane formy (wszystkie z asercją `(core, admin_context, code)`):
`Tokio, Japonia`; `Tokio, Japan`; `Tokio, JP`; `Tokio Japonia`; `Warszawa, Polska`; `Hel PL`;
`Hel,PL`; `Hel, PL`; `Tokio,Japonia`; `Wiązowna, otwock`; `Wiązowna, otwock, Polska`;
`Wiązowna,otwock,Polska`; `Olsztyn koło Częstochowy`; `Olsztyn województwo śląskie`;
`Nowy Jork,USA`; `Nowy Jork United States of America`; `X, Saint Vincent et les Grenadines`;
`Tokio, JP,`; `Tokio, Japonia, Polska`; `Tokio, JP, Japonia`;
kraj samodzielnie: `Polska`, `Japonia`, `Kongo`, `Korea` (brak kodu, brak odcięcia);
`Wa, Australia`, `Hel, Portugalia` (status `NO_MATCH` bez zmian).

### 15.3 Kontrakty niezmienne (regresja parsera)

`_geocode_query_country`: wszystkie pary z `test_geocode_validation.py:385-441`;
`_geocode_split_country("Wiązowna, otwock, Polska") == ("Wiązowna, otwock","pl")`;
`_geocode_query_parse` dla parametrów z `test_geocode_validation.py:1710-1731`;
`geocode_shortening_is_safe` dla `test_geocode_validation.py:1445-1462` + `1954-1960`.

### 15.4 Kolizje (integracja)

| Test | Oczekiwanie |
|---|---|
| `Atlanta, Georgia` + kandydat `state=Georgia, cc=us` | `OK` (wariant A), brak filtra `ge` |
| `Atlanta, Georgia` + kandydat `cc=ge` (bez stanu) | wariant A: `NO_MATCH`; wariant B: `OK` |
| `Warszawa, Włochy` + kandydat `city_district=Włochy` | `OK` |
| `X, Kongo` | `AMBIGUOUS` → brak `country_codes`, status jak dziś |
| `X, Korea` | brak kraju, kontekst (bez arbitralnego wyboru) |
| `X, Niger` vs kandydat `ng` | odrzucony, gdy segment = `Niger` |
| `X, Guinea` vs kandydat `gw` | odrzucony |
| prefiksy | `Niger`/`Nigeria`, `Guinea`/`Guinea-Bissau`, `Congo`/`DR Congo`, `Korea`/`Korea Południowa` |

### 15.5 Kandydaci i etykiety

`country_code` niezgodny → odrzucenie; brak `country_code` przy jawnym kraju → odrzucenie;
`city/town/village`; `province/state/region`; `namedetails`; siła `place` vs `display`;
różne schematy `address` (płaski i zagnieżdżony w `raw["address"]`); reverse vs forward;
reverse zwracający tylko kraj; brak użycia surowego `location.address` (istniejąca pułapka `POISON`).

### 15.6 Regresje niefunkcjonalne

| Test | Metoda |
|---|---|
| brak nowych requestów | licznik wywołań atrapy geokodera = 1 dla każdej formy kraju |
| brak zmian GPS/pinezki | `_resolve_location_labels(lat, lon, lang)` bez `query` → identyczne etykiety jak dziś |
| brak zapisu profilu po odrzuceniu | `NO_MATCH`/`ERROR` → `set_profile` niewołany, brak karty |
| brak zmian kolejności komunikatów | `search_loc` → geokoder → karta; asercje z `test_waiting_messages.py` |
| brak zmian kontraktu publicznego | `geocode_city_accepted` nadal 5‑krotka; `_geocode_select_accepted` 2‑krotka; `_geocode_candidate_evidence` 2‑krotka |
| 3.8‑safety | test AST: brak `removeprefix/removesuffix`, brak `match`, brak PEP 585/604 w nowych plikach |
| brak zależności runtime | test AST: `country_index.py` i `location_bot.py` nie importują `babel`/`pycountry` |
| rozmiar danych | plik < ~120 KB, brak sieci w testach (już globalnie) |

Testy danych (**15.1**) są całkowicie odseparowane od testów geokodowania — żaden nie wymaga wyniku
Nominatim dla konkretnego kraju.

---

## 16. Ryzyka regresji

| # | Ryzyko | Ocena / mitygacja |
|---|---|---|
| 1 | Nowe trafienia kraju w istniejących zapytaniach testowych | Skan 3376 literałów: **103** zmieniłyby klasyfikację, ale wśród nich tylko ~8 to zapytania/etykiety związane z geokodowaniem (`Hel, Polen/Pologne/Polonia`, `Hel, Portugalia`, `Wa, Australia`, `London, Kanada`, `Helmand, Afganistan`, `Sahara, Algieria`). Wszystkie nadal dają `NO_MATCH` (dowód nazwy nie przechodzi) — **do potwierdzenia pełnym przebiegiem testów po implementacji** |
| 2 | `Hel,PL` — zmiana zachowania (kraj zacznie działać) | to naprawa wymaganej formy; asercja na `country_codes` i na tekst zapytania |
| 3 | `Nowy Jork,USA` — rdzeń zmienia się z `Nowy` na `Nowy Jork` | naprawa utraty tokenu; nowy test |
| 4 | Kody alpha‑2 jako końcowy token (`to`, `do`, `na`, `ca`, `id`, `in`…) | ryzyko tylko przy zapytaniach kończących się 2‑literowym słowem; dziś identyczne ryzyko dla 7 kodów; test „śmieciowy suffix nie zjada rdzenia” |
| 5 | Nazwy kraj ↔ kontekst (`Georgia`, `Włochy`) | lista `context_ambiguous` + testy; brak wpisu = fałszywe `NO_MATCH` |
| 6 | Podniesienie limitu tokenów 4 → 9 | bezpieczne: odcięcie tylko przy pełnej zgodności całego sufiksu; testy długich nazw |
| 7 | Brak/uszkodzony indeks = `GEOCODE_ERROR` | widoczne natychmiast, ale dotyka wszystkich tekstowych zapytań → wymaga świadomości operacyjnej (D7) |
| 8 | Faza 2 (etykiety) zmienia `Users.location_label` | osobna akceptacja; faza 1 **nie** dotyka etykiet |
| 9 | Rozmiar/„binarność” danych w git | ~61–90 KB JSON, deterministyczny; `.gitignore` bez zmian |
| 10 | Python 3.8 | nowy kod bez API 3.9+; test AST w CI/testach |
| 11 | Licencje (CLDR/pycountry) | tylko generator; `NOTICE.md`; `requirements.txt` bez zmian |
| 12 | Skrót gościa zmienia zachowanie „automatycznie” | to zamierzone (jedno źródło prawdy), ale wymaga testów w `test_shortcut_search_loc.py` (bez modyfikacji kodu) |
| 13 | Duplikacja normalizacji (kontrakt kraju vs miasta) | test krzyżowy korpusu; brak zmian w `_normalize_location_text` |

---

## 17. Decyzje — status po akceptacji właściciela produktu

| ID | Decyzja | Status |
|---|---|---|
| **D1** | CLDR/Babel + pycountry offline, commitowany JSON, zero zależności runtime, `NOTICE.md` | **ZAAKCEPTOWANE** |
| **D2** | 249 krajów ISO 3166‑1, **bez `xk`** w fazie 1 | **ZAAKCEPTOWANE (bez xk)** |
| **D3** | Wariant **A** + **wąska** lista `context_ambiguous`: wyłącznie `georgia`, `wlochy` (override tylko przy potwierdzonej regresji: forma zapytania + syntetyczny kandydat + test). `jordan`, `chad`, `niger`, `guinea` **nie są** override'ami — tylko testy pełnego dopasowania i braku prefiksów. `węgry`/`szwecja`/`dania` poza zakresem. | **ZAAKCEPTOWANE (skorygowane)** |
| **D4** | Bez nowego statusu i nowych tekstów; wieloznaczność → dotychczasowa rola segmentu | **ZAAKCEPTOWANE** |
| **D5** | Alpha‑3 przechowywane, **niedopasowywane** | **ZAAKCEPTOWANE** |
| **D6** | Bez aliasów subnarodowych (`anglia`, `holandia`, …) | **ZAAKCEPTOWANE** |
| **D7** | Fail‑closed: brak/uszkodzony indeks = `GEOCODE_ERROR`; dodatkowo test obecności pliku, log, instrukcja wdrożeniowa, **bez fallbacku do starego słownika** | **ZAAKCEPTOWANE** |
| **D8** | Naprawa `_geocode_strip_country_suffix` i limitu tokenów w fazie 1 | **ZAAKCEPTOWANE** |
| **D9** | Evidence, reguły reverse/forward, `namedetails` w etykiecie, `Users.location_label` — faza 2 (nie implementować teraz) | **ZAAKCEPTOWANE (odłożone)** |
| **D10** | Kontrola deterministycznej regeneracji w CI | **ZAAKCEPTOWANE jako zalecane** |
| **D11 (nowa)** | Sprzeczne wskazania kraju: faza 1 **zachowuje dotychczasowe zachowanie wyłącznie jako kompatybilność wsteczną** (bez zmiany kontraktu, bez dodatkowego requestu, z osobnym testem regresyjnym i jawnym opisem w kodzie/raporcie jako dług do poprawy) | **ZAAKCEPTOWANE z zastrzeżeniem** |

## 18. Elementy wyraźnie odłożone

1. **Structured query** Nominatima jako fallback (także jako nieaktywna flaga) — nie projektujemy.
2. Dodatkowe requesty, retry, nowe timeouty, cache kraju, geokodowanie samego kraju.
3. Transliteracja (`Tokyo`↔`Tokio`), fuzzy matching, obsługa literówek.
4. Odmiana gramatyczna (`Japonii`, `Niemczech`, `Polsce`).
5. Kolejne języki nazw i alfabety niełacińskie.
6. Lokalne wczesne odrzucanie sprzecznych wskazań kraju (bez requestu) — zmienia liczbę requestów.
7. Nowe statusy i komunikaty użytkownika (poza D4).
8. Hierarchia administracyjna i promowanie `region`/`state` do miejscowości.
9. Zmiany w GPS, pinezce, WebApp, harmonogramach, obrazkach, `Users`, `PENDING_SAVE`.
10. Zmiany w `guest_bot_handler.py` w fazie 1.

Punkty 1–10 pozostają odłożone **także po korekcie aliasów**: nie rozpoczęto fazy 2 (brak
przygotowań, flag, martwego kodu ani nowych statusów). Wariant B dla `georgia` (rozpoznawanie
kontekstu zamiast `context_ambiguous`) jest częścią tej listy — `_geocode_select_accepted` pozostał
nietknięty.

---

## 19. Status implementacji fazy 1 (po akceptacji D1–D11)

### 19.1 Co zostało dodane / zmienione

| Plik | Rodzaj | Treść |
|---|---|---|
| `country_index.py` | **nowy** | loader + `normalize_country_name` / `classify` / `resolve_suffix`, pełna walidacja, fail-closed, zero zależności trzecich, 3.8-safe |
| `data/countries/country_index.json` | **nowy (dane)** | 249 krajów, **1184 nazwy**, **90 031 B**, CLDR 47 (Babel 2.18.0) + pycountry 26.2.16, suma SHA-256 `c22036ffd66ca020…` w `meta` (po korekcie aliasów) |
| `data/countries/NOTICE.md` | **nowy** | atrybucja CLDR/Unicode, pycountry LGPL, instrukcja regeneracji, jawne ograniczenia dopasowania |
| `tools/build_country_index.py` | **nowy** | generator offline, deterministyczny (bit w bit), tryb `--check` |
| `tools/country_index_overrides.json` | **nowy** | **7** aliasów ręcznych + **2** wpisy `context_ambiguous` (`georgia`, `wlochy`) + udokumentowane odrzucenia; `version: 2` |
| `requirements-tools.txt` | **nowy** | `babel==2.18.0`, `pycountry==26.2.16` (tylko narzędzia; `requirements.txt` bez zmian) |
| `.github/workflows/country_index.yml` | **nowy** | CI: `--check` (D10) + smoke test „runtime bez Babel/pycountry” |
| `test_country_index.py` | **nowy** | **79** testów danych i resolvera |
| `test_geocode_country_forms.py` | **nowy** | **129** testów parsera, kolizji, integracji, gościa i regresji |
| `location_bot.py` | **zmieniony** | usunięty ręczny słownik; `_geocode_country_suffix` / `_geocode_country_suffix_match` na `country_index`; naprawa `_geocode_strip_country_suffix`; fail-closed w `geocode_city_accepted`; docstring parsera nazywa ograniczenie sprzecznych krajów |
| `README.md` | **zmieniony** | sekcja „Country index (static data file — deployment requirement)” |

### 19.2 Czego świadomie NIE zmieniono

`guest_bot_handler.py` (dziedziczy przez wstrzyknięte callbacki), `i18n.py` (żadnych nowych
statusów/komunikatów), `users_store.py`, `main_card.py`, `image_generator.py`, `weather_payload.py`,
`prepare_*.py`, `conftest.py`, `pytest.ini`, `requirements.txt`, istniejące pliki testów,
formatowanie etykiet (`_format_public_location_parts`, `_select_short_location_label`),
`_resolve_location_labels`, evidence dopasowania, GPS/pinezka/WebApp, `Users.location_label`.
Korekta po akceptacji (aliasy DR Konga i Trynidadu) nie zmieniła **żadnego** z powyższych plików —
objęła wyłącznie `tools/country_index_overrides.json`, `data/countries/country_index.json` i testy.

### 19.3 Weryfikacja

| Sprawdzenie | Wynik |
|---|---|
| pełny zestaw testów (po korekcie aliasów) | **3711 passed, 2 skipped** (baseline przed fazą 1: 3503 passed, 2 skipped) |
| nowe testy | **208** (79 dane/resolver + 129 parser/geokodowanie) |
| determinizm generatora | dwa uruchomienia → plik identyczny bit w bit; `--check` → `OK` |
| runtime bez Babel/pycountry | `python -X importtime -c "import country_index"` → brak tych modułów w `sys.modules` |
| gramatyka 3.8 | `ast.parse(..., feature_version=(3, 8))` OK dla wszystkich nowych/zmienionych plików |
| formy ze zlecenia | `Tokio, Japonia` / `Tokio, Japan` / `Tokio, JP` / `Tokio Japonia` → `cc=jp`, zapytanie `Tokio`, etykiety `Tokio` / `Tokio, Japonia`; `Warszawa, Polska` → `pl`; `Hel PL`, `Hel,PL` → `pl` |
| naprawy D8 | `Hel,PL` → `Hel`; `Nowy Jork,USA` → `Nowy Jork` + `us`; `Wiązowna,otwock,Polska` → `Wiązowna, otwock` + `pl` |
| brak nowych requestów | dokładnie 1 forward na każdą formę kraju (także w trybie gościa) |
| fail-closed | brak pliku / zły JSON → `GEOCODE_ERROR`, **0 requestów**, `TOO_SHORT` nadal pierwszy, GPS/pinezka działają |
| kolizje | `Atlanta, Georgia` i `Warszawa, Włochy` → `OK` bez `country_codes`; `X, Kongo` → brak kodu; `X, Korea` / `X, Macedonia` → brak kodu |

### 19.4 Nowe ustalenia z DANYCH (nie z Nominatima) — do świadomej akceptacji

1. **`kongo` → kolizja (`cd`,`cg`); `congo` → `cg` (bez zmian).** To nie arbitralny wybór:
   CLDR nazywa CG krótko „Congo”, a CD „Congo - Kinshasa”. Krótka forma **pozostaje** przy `cg`;
   korekta fazy 1 nie zmienia jej, tylko dodaje poprawne wejście dla DR Konga (punkt 2).
2. **Pełna nazwa DR Konga jest teraz aliasem ręcznym (korekta po akceptacji).**
   `democratic republic of the congo → cd` oraz `democratic republic of congo → cd`.
   Skutek: `Kinshasa, Democratic Republic of the Congo` → rdzeń `Kinshasa`, `country_codes=cd`,
   kandydat z Kinszasą przechodzi (wcześniej spadał do sufiksu `congo` = `cg` i był odrzucany).
   Pełna nazwa wygrywa z krótszym sufiksem, bo sufiksy idą od najdłuższego. Aliasy są pełnymi
   frazami — `democratic republic`, `republic of the congo` itp. **nie** dopasowują się.
3. **29 nazw krajów ma ostatni token będący nazwą innego kraju** (`Papua New Guinea`, `South Sudan`,
   `Caribbean Netherlands`, `Bouvet Island`, `American Samoa`, …). Reguła „najdłuższy sufiks
   najpierw” sprawia, że pełna nazwa wygrywa (`papua nowa gwinea` → `pg`), a zagrożenie dotyczy
   wyłącznie form skróconych, których nie da się odróżnić lokalnie. Pokryte testami (§19.3).
   Przypadek DR Konga z tej listy został zamknięty aliasem, pozostałe nie mają zgłoszonej regresji.
4. **CLDR używa „&”, nie „and”.** Po normalizacji `&` → spacja. `Trinidad & Tobago` i
   `Trynidad i Tobago` działały z danych; **`Trinidad and Tobago` dostało jawny alias → `tt`**
   (korekta po akceptacji). Nie wprowadzono zamiany `and` → `&` ani fuzzy matchingu: nadal tylko
   pełne frazy. Inne nazwy z „and” (np. `Bosnia and Herzegovina`, `Antigua and Barbuda`) pozostają
   poza indeksem jako **świadomie zadeklarowane ograniczenie** (test).
5. **`Holandia` → `nl`** wynika z danych CLDR (to oficjalna nazwa terytorium NL w CLDR), nie z
   naszego aliasu — D6 dotyczył aliasów ręcznych (`england`, `anglia` nadal `NONE`).
6. **`georgia` jako `context_ambiguous`** oznacza, że `Tbilisi, Georgia` nie dostaje filtra `ge`
   (użytkownik gruziński ma `Tbilisi, Gruzja` → `ge`). To świadomy koszt wariantu A; kandydat z GE
   przy `Georgia` jest odrzucany przez kontekst administracyjny → `NO_MATCH`, bez złej karty.

### 19.5 Wdrożenie (checklista)

1. Zacommitować **cały** katalog `data/countries/` (test `test_index_file_is_not_ignored_by_git`
   pilnuje, że plik nie jest ignorowany).
2. Po deployu sprawdzić jedną linię logu `[COUNTRY_INDEX] Wczytano indeks krajów: 249 krajów, …`.
3. Brak tej linii albo linia `BŁĄD indeksu krajów` = plik nie dotarł na serwer → tekstowe
   wyszukiwanie zwraca `GEOCODE_ERROR` (GPS/pinezka działają).
4. Regeneracja tylko przy zmianie danych: `pip install -r requirements-tools.txt &&
   python tools/build_country_index.py`, potem commit pliku (CI `--check` tego pilnuje).

### Załącznik A. Mapa źródeł (zweryfikowane numery linii)

```
1603  GEO_OK / GEO_NO_CITY / GEO_ERROR
1728  _LOCALITY_ADDRESS_FIELDS     1729 _ADMIN_ADDRESS_FIELDS      1730 _PUBLIC_ADDRESS_FIELDS
1747  _clean_location_component     1753 _normalize_location_text    1781 _compact_alnum
1816  _select_short_location_label  1897 _location_mode_for_query    1901 _is_polish_address
1933  _format_public_location_parts 2047 reverse (coarse)            2070 get_location_details_from_coords
2095  reverse (krok 1)              2172 GEOCODE_CANDIDATE_LIMIT       2173 GEOCODE_MIN_QUERY_CHARS
2178  _GEOCODE_COUNTRY_MAX_TOKENS   2179 _GEOCODE_COUNTRY_ALIASES    2193 _GEOCODE_CITY_ALIASES
2209  _GEOCODE_PLACE_FIELDS         2212 _GEOCODE_CONTEXT_FIELDS     2216 _GEOCODE_EXTRA_CONTEXT_FIELDS
2243  _GEOCODE_ADMIN_FIELDS         2262 _geocode_name_matches       2277 _geocode_query_variants
2301  _geocode_query_context        2326 _geocode_query_country      2350 _geocode_country_suffix
2372  _geocode_strip_country_suffix 2392 _geocode_split_country      2410 _geocode_clean_admin_text
2442  _geocode_query_parse          2487 _geocode_geocoder_query     2505 _geocode_query_is_too_short
2525  _geocode_candidate_is_safe    2554 _geocode_namedetails_values 2593 _geocode_place_values
2605  _geocode_context_values       2642 _geocode_admin_values       2672 _geocode_admin_stem_matches
2692  _geocode_admin_piece_matches  2722 _geocode_context_conflicts  2760 _GEOCODE_SAME_PLACE_KM
2800  _geocode_candidate_evidence   2856 _geocode_select_accepted    2941 _geocode_forward_candidates
3007  geocode_city_accepted         3072 geocode_city_details_status 3085 geocode_city_labels_status
3140  geocode_shortening_is_safe    3183 _reverse_label_matches_query 3200 _resolve_location_labels
4309  /miasto (prompt)              4360–4420 wspólna logika geokodowania po prompcie
3860–3902  wstrzyknięcia callbacków do trybu gościa
```

### Załącznik B. Powtarzalne pomiary

```bash
# baseline
pytest -q                              # 3503 passed, 2 skipped
pytest -q test_geocode_validation.py   # 391 passed

# pomiary indeksu (wymaga babel+pycountry tylko lokalnie)
#   1494 wierszy (język, kraj, nazwa), 928 różnych nazw po normalizacji, 0 braków
#   1 kolizja: kongo -> {cd, cg}; 24 nazwy > 4 tokenów (max 9)
#   prototyp JSON: 61 187 B compact / 89 520 B indent=1; 1188 kluczy lookup (stan audytu)
#   plik po implementacji + korekcie aliasow: 90 031 B, 1184 nazwy, 249 krajow, max 9 tokenow
```

Wszystkie pomiary wykonano **bez** połączenia z Nominatim; testy używają wyłącznie atrap geokodera
(`FakeNominatim`, `StrictNominatim`) i danych z repozytorium.
### 19.6 Korekta fazy 1 — aliasy DR Konga i Trynidadu (po akceptacji)

Zakres korekty: **tylko warstwa ręcznych aliasów** (`tools/country_index_overrides.json`,
wersja `2`) + regeneracja danych + testy. Żadnych zmian w `location_bot.py`,
`guest_bot_handler.py`, `country_index.py`, etykietach, GPS, pinezce, WebApp, profilu ani
komunikatach. Faza 2 nie została rozpoczęta.

| Alias (znormalizowany) | Kod | Źródło / powód |
|---|---|---|
| `democratic republic of the congo` | `cd` | CLDR nazywa to państwo „Congo - Kinshasa”, więc pełna nazwa nie istniała w indeksie, a zapytanie spadało do krótkiego sufiksu `congo` (= `cg`) i było odrzucane przez filtr `country_code=cd`. Alias kuratorowany. |
| `democratic republic of congo` | `cd` | Wariant bez `the` — ta sama nazwa państwa w formie, której używa się potocznie. Alias kuratorowany. |
| `trinidad and tobago` | `tt` | CLDR używa „&” (normalizacja → spacja), a ludzie piszą „and”. **Jeden stabilny alias pełnej frazy**, nie zamiana `and` → `&` i nie fuzzy matching. Alias kuratorowany. |

Zachowane bez zmian:

* krótkie `congo` → `cg` (dane CLDR; **nie** zmienione arbitralnie),
* `kongo` → `AMBIGUOUS` (`cd`,`cg`) — nadal bez wyboru kodu,
* `trinidad & tobago` → `tt` i `trynidad i tobago` → `tt` (odczyt z CLDR),
* `bosnia and herzegovina`, `antigua and barbuda` → **nie** dopasowują się (jawnie
  zadeklarowane ograniczenie, pokryte testem),
* `georgia` w `context_ambiguous` (**wariant A** — patrz niżej).

Metryki danych po przebudowie (`tools/build_country_index.py`):

| Metryka | Przed korektą | Po korekcie |
|---|---|---|
| Kraje | 249 | **249** (bez zmian) |
| Nazwy w `lookup` | 1181 | **1184** (+3 aliasy) |
| Rozmiar pliku | 89 889 B | **90 031 B** |
| `max_name_tokens` | 9 | **9** |
| `payload_sha256` | (poprzednia) | **`c22036ffd66ca020…`** |
| Kolizje nazw | `kongo → cd,cg` | **bez zmian** |
| `context_ambiguous` | `georgia`, `wlochy` | **bez zmian** |
| `overrides.version` | 1 | **2** |

Weryfikacja po korekcie:

| Sprawdzenie | Wynik |
|---|---|
| `python tools/build_country_index.py` | 249 krajów, **1184 nazwy**, 90 031 B; dwa uruchomienia → plik identyczny bit w bit |
| `python tools/build_country_index.py --check` | **OK — plik aktualny (90 031 B)** |
| `pytest -q` | **3711 passed, 2 skipped** |
| `pytest -q test_country_index.py test_geocode_country_forms.py` | **208 passed** (79 + 129) |
| `Kinshasa, Democratic Republic of the Congo` | rdzeń `Kinshasa`, `country_codes=cd`, kandydat Kinszasy **przechodzi**, kandydat z `cg` **odrzucony** |
| `Trinidad and Tobago` | `tt`; `Trinidad & Tobago` i `Trynidad i Tobago` nadal `tt` |
| `X, Congo` | `cg` (bez zmian); `X, Kongo` → brak kodu (bez zmian) |
| Python 3.8 | brak nowych konstrukcji; zmieniono tylko JSON + testy |

**Georgia — potwierdzenie braku zmian (wariant A).** `georgia` pozostaje w `context_ambiguous`,
`_geocode_select_accepted` nie został tknięty, wariant B nie został zaimplementowany:

* `Atlanta, Georgia` → działa jako kontekst administracyjny (bez `country_codes`),
* `Tbilisi, Georgia` → **odłożone do fazy 2**: brak filtra `ge`, kandydat z GE odpada przez
  kontekst administracyjny → `NO_MATCH` (żadnej karty, nigdy złe miasto),
* `Tbilisi, Gruzja` → `ge` (poprawne wejście dla użytkownika gruzińskiego).

**Faza 2 — nadal nie rozpoczęta.** Evidence dopasowania, reguły reverse/forward,
`namedetails` jako źródło etykiety, zmiany `_resolve_location_labels` oraz `Users.location_label`,
nowy status niejednoznacznego kraju, wariant B i automatyczne rozstrzyganie kolizji kraj–kontekst
pozostają odłożone bez żadnych przygotowań w kodzie.

**Pozostałe ograniczenia po korekcie:** brak odmiany (`Japonii`, `Niemczech`), brak transliteracji
(`Tokyo`), brak alfabetów niełacińskich, brak fuzzy/literówek, brak zamiany `and` → `&` dla nazw,
które nie dostały aliasu (`Bosnia and Herzegovina`, `Antigua and Barbuda` itd.), `xk`/Kosowo poza
indeksem (D2), oraz świadomy koszt wariantu A dla `Georgia`.

---

## 20. Faza 2 — audyt stanu po fazie 1 i plan (BEZ zmian w kodzie)

**Status tej sekcji: audyt + plan. Żaden plik produkcyjny nie został zmieniony**
(`git status` czysty poza tym dokumentem). Wszystkie pomiary poniżej wykonano
w tym checkoucie, offline, na atrapach geokodera — zero requestów do Nominatima.

### 20.0 Stan checkoutu (zweryfikowany, nie z pamięci)

| Element | Wartość | Jak sprawdzono |
|---|---|---|
| Gałąź | `arena/152775ef-pogoda-world` | `git status --porcelain -b` |
| HEAD | `b8130bd` = `Merge pull request #90 from watifer/main` | `git log --oneline -1` |
| `origin/staging` | `b8130bd58ecd3ea61a581eb300f519e0465d5f85` | `git ls-remote origin staging` |
| `origin/main` | `3b9688dc…` = `Add global country index for text geocoding (#89)` | `git ls-remote origin main` |
| Commit implementacji fazy 1 `48971cd7…` | **istnieje na GitHubie**, rodzic `5a289ff3…`, `Add global country index for text geocoding`, 2026‑10‑27T22:07:28Z | `gh api repos/…/commits/48971cd7…` |
| Dlaczego `git cat-file` go nie zna | checkout jest **płytki** (`git rev-list --all` = 2 obiekty), więc `48971cd` nie jest w lokalnym grafie; jego **treść** jest w HEAD przez merge #90 | `git cat-file -t 48971cd…` → `bad object` |
| Zakres zmian roboczych | brak (`git status --porcelain` pusty przed edycją tego pliku) | — |
| Baseline testów w tym sandboxie | **3710 passed, 3 skipped** | `pytest -q` |

Rozbieżność `3710+3` vs zgłaszane `3711+2` jest wyjaśniona i nie jest regresją:
suma jest ta sama (3713), a dodatkowy skip to
`test_country_index.py:609: could not import 'babel'` — w tym sandboxie nie ma
Babel (narzędzie generatora), więc ten jeden test jest pomijany zamiast przechodzić.
Pozostałe skipy: `fontTools` (`test_now_i18n.py:210`) i `test_welcome_privacy.py:153`.

Środowisko pomiarowe: `.venv` (w `.gitignore`), `requirements-dev.txt`,
**geopy 2.5.0**, pytest 9.2.0.

### 20.1 Aktualny przepływ forward → reverse → karta (zweryfikowane numery linii)

`/teraz Tokio, Japonia` (identycznie `/dzien`, `/trend`, `/miasto X`, prompt, skróty gościa):

| Krok | Funkcja / linia | Co się dzieje (zmierzone) |
|---|---|---|
| 1 | `_run_city_oneoff` → `geocode_city_accepted(query, lang)` — `location_bot.py:1269`, definicja `3037` | jedyne źródło prawdy forward |
| 2 | `_geocode_query_parse` — `2464` | `('Tokio', [], 'jp')` — **faza 1 działa** |
| 3 | `_geocode_geocoder_query` — `2517` | do geokodera idzie `Tokio` + `country_codes=jp` |
| 4 | `_geocode_forward_candidates` — `2971` | **1 request** forward |
| 5 | `_geocode_select_accepted` — `2886` | filtr klasy → twardy filtr kraju → dowód nazwy → `GEOCODE_OK` |
| 6 | `_format_public_location_parts` — `1934` (wołanie `3094`) | `('Japonia', 'Japonia')` ← **tu ginie Tokio** |
| 7 | `_resolve_location_labels(lat, lon, lang, fallback_short, fallback_display, query)` — `3239` (wołanie `1280`) | reverse + arbitraż |
| 8 | `get_location_details_from_coords` — `2071` | **1 request** reverse → `('Suginami', 'Suginami, Japonia', 'ok')` |
| 9 | `_reverse_label_matches_query` — `3222` | `'Suginami'` vs rdzeń `Tokio` → **False** |
| 10 | arbitraż — `3279` | forward fallback wygrywa → karta `('Japonia', 'Japonia', 'ok')` |
| 11 | `_run_oneoff_report` → `_put_pending_save` → tytuł karty; `_save_profile_from_location` — `1301` | `short_label` trafia do tytułu karty **i** do `Users.location_label` (`1327`) |

Łączna liczba requestów dla `/teraz X`: **2** (1 forward + 1 reverse) — zmierzone.

**Pomiar end‑to‑end na rzeczywistych payloadach Oracle** (atrapa geokodera, bez sieci):

```
--- 'Tokio, Japonia'
    geocode_city_accepted: ('geocode_ok', 35.6768601, 139.7638947, 'Japonia', 'Japonia')
    KARTA (short, display, status): ('Japonia', 'Japonia', 'ok')
    requesty: 2 [('forward','Tokio',('country_codes','namedetails')),
                 ('reverse','35.6768601, 139.7638947', None)]
--- 'Tokio, Polska'
    geocode_city_accepted: ('geocode_no_match', None, None, None, None)
    requesty: 1 [('forward','Tokio',('country_codes','namedetails'))]
--- GPS/pinezka (bez query)
    ('Suginami', 'Suginami, Japonia', 'ok')   requesty: 1 (reverse)
```

Objaw produkcyjny odtworzony **dokładnie**: karta z lokalizacją „Japonia”.

### 20.2 Przyczyna utraty nazwy „Tokio” (dwa fakty, oba zmierzone)

Fakt 1 — `_select_short_location_label` (`1817`) nie zna pola `province`:

```
_LOCALITY_ADDRESS_FIELDS = ("city", "town", "village", "hamlet", "locality")   # 1729
_ADMIN_ADDRESS_FIELDS    = ("municipality", "county", "state")                 # 1730
_PUBLIC_ADDRESS_FIELDS   = _ADMIN_ADDRESS_FIELDS + ("postcode", "country")     # 1731
```

Adres zaakceptowanego kandydata (rzeczywisty forward Oracle) to
`{"province": "Tokio", "ISO3166-2-lvl4": "JP-13", "country": "Japonia", "country_code": "jp"}`:

* pola miejscowości: **brak** (`_geocode_place_values` → `[]`),
* `municipality` / `county` / `state`: **brak** (jest `province`, którego ta lista nie zawiera),
* `postcode`: brak → zostaje **`country` = „Japonia”**.

Pomiar: `_select_short_location_label(TOKIO_FWD, query="Tokio, Japonia", lang="pl")` → `'Japonia'`;
`_format_public_location_parts(...)` → `('Japonia', 'Japonia')`.

Fakt 2 — dowód nazwy **jest** i jest mocny, ale nie dociera do etykiety:

```
_geocode_candidate_evidence("Tokio", TOKIO_FWD, TOKIO_RAW, "") -> ('tokio', 'localized')
```

`localized` pochodzi z `namedetails["name:pl"] = "Tokio"` (dozwolony klucz: reguła
`key.startswith("name:")` w `_geocode_namedetails_values`, `2584`). Siła dowodu jest
używana **wyłącznie** wewnątrz `_geocode_select_accepted` (wybór strong vs weak) —
do etykiet nie trafia z niej nic. To jest dokładnie luka opisana w §11 i §12.2.

Fakt 3 — arbitraż reverse/forward **nie jest** przyczyną i **nie wymaga zmiany**:
reverse daje `Suginami` (poprawnie — to dzielnica pod punktem), `_reverse_label_matches_query`
zwraca `False`, więc forward fallback wygrywa. Gdyby forward fallback niósł `Tokio`,
karta pokazałaby `Tokio` / `Tokio, Japonia` bez dotykania `_resolve_location_labels`.
Symulacja (podstawienie `short_label = "Tokio"` do tego samego formattera):
`_format_public_location_parts` → `('Tokio', 'Tokio, Japonia')` — `province` nie duplikuje się
w display, bo nie ma go w `_PUBLIC_ADDRESS_FIELDS`.

### 20.3 Dlaczego faza 1 tego nie złapała

Fixtura fazy 1 `tokyo_result()` (`test_geocode_country_forms.py:46`) buduje kandydata
`{"city": "Tokio", "state": "Tokio", "country": "Japonia", "country_code": "jp"}` —
czyli **z polem miejscowości**, którego rzeczywisty Nominatim dla `Tokio, Japonia`
nie zwraca (zwraca relację `boundary/administrative` z `addresstype=province`).
Testy fazy 1 asertują `short_label == "Tokio"` na innym kształcie danych niż produkcyjny,
dlatego przeszły. Wniosek: testy fazy 2 muszą używać **dosłownego** payloadu Oracle.

### 20.4 Nowe ustalenie: `class` vs `category` (ryzyko ukryte, zweryfikowane w źródle geopy)

`_geocode_candidate_is_safe` (`2555`) czyta **wyłącznie** `raw.get("class")`:

```python
cls = _clean_location_component(raw.get("class")).casefold()
```

* geopy 2.5.0 wysyła `'format': 'json'` (Nominatim JSON v1) — zarówno w `geocode`
  (`.venv/…/geopy/geocoders/nominatim.py:238`), jak i w `reverse` (`:356`), a `_parse_code`
  (`:374-382`) wkłada surowy dict do `Location.raw` **bez zmian**. JSON v1 używa klucza
  **`class`** → produkcja działa.
* curl Oracle używał `format=jsonv2`, który nazywa to pole **`category`**.
* Pomiar: ten sam payload z `category` zamiast `class` →
  `_geocode_candidate_is_safe` → **False** → `geocode_no_match`, **brak karty**
  (nie „Japonia”, tylko odmowa wyszukiwania).

Konsekwencja: dziś nie ma błędu, ale każda przyszła migracja na `jsonv2` (albo zmiana
domyślnego formatu w geopy) wyłączy **całe** wyszukiwanie tekstowe, a nie tylko Tokio.
To jest osobna decyzja (D13), **nie** część minimalnej poprawki etykiety.

### 20.5 Nowe ustalenie: docstring `localized` jest niezgodny z kodem

Docstring `_geocode_candidate_evidence` (`2830`) mówi, że `localized` to egzonim
„przy istniejących polach miejscowości”, a kod zwraca `localized` **także wtedy, gdy
pól miejscowości nie ma** — i wtedy dopasowaną nazwą jest sama wartość z `namedetails`:

```python
if localized:
    return (place_values[0] if place_values else localized), _GEOCODE_EVIDENCE_LOCALIZED
```

To właśnie przypadek Tokio (`place_values == []`, wynik `('tokio', 'localized')`).
Reguła fazy 2 opiera się na tej gałęzi, więc docstring wymaga sprostowania
(zmiana wyłącznie dokumentacyjna).

### 20.6 Proponowany kontrakt evidence (addytywny, kontrakty stare bez zmian)

```python
GeocodeEvidence = namedtuple(
    "GeocodeEvidence",
    "matched strength source field raw_value admin_label admin_field",
)
# matched     : znormalizowana nazwa, która potwierdziła zapytanie ("tokio")
# strength    : "place" | "localized" | "display"
# source      : "address" | "namedetails" | "raw" | "postcode"
# field       : pole, z którego pochodzi potwierdzenie ("province", "name:pl", …)
# raw_value   : oryginalna (nieznormalizowana) wartość tego pola
# admin_label : surowa wartość POTWIERDZONEGO pola administracyjnego albo ""
# admin_field : nazwa tego pola albo ""
```

Rozszerzenie względem szkicu z §11.1 o `admin_label`/`admin_field` jest celowe:
gating (klasa kandydata + siła dowodu + równość nazwy) dzieje się **przy kandydacie**,
tam gdzie dostępne jest `raw`, a formatter dostaje już zweryfikowaną wartość.

| Funkcja | Zmiana | Kontrakt |
|---|---|---|
| `_geocode_candidate_evidence_detail(query_name, address, raw, postcode)` | **nowa** | `GeocodeEvidence` |
| `_geocode_candidate_evidence(...)` | cienki wrapper | **bez zmian**: `(matched, strength)` — 2 wywołania w testach |
| `_geocode_confirmed_admin_label(query_name, address, raw, postcode)` | **nowa** | `(value, field)` albo `("", "")` |
| `_geocode_candidate_is_administrative(raw)` | **nowa**, wydzielona gałąź administracyjna z `_geocode_candidate_is_safe` | `bool`; `is_safe` **bez zmian** |
| `_geocode_select_accepted(...)` | bez zmian | `(status, location)` — 8 wywołań w testach |
| `geocode_city_accepted(...)` | liczy `confirmed_admin` i przekazuje do formattera | **bez zmian**: 5‑krotka |
| `_select_short_location_label(..., confirmed_admin=None)` | nowy opcjonalny parametr | wstecznie zgodna |
| `_format_public_location_parts(..., confirmed_admin=None)` | nowy opcjonalny parametr | wstecznie zgodna |
| `_resolve_location_labels(...)` | **bez zmian** (patrz 20.2, fakt 3) | — |
| `guest_bot_handler.py` | **bez zmian** (dziedziczy przez `geocode_city_labels_status`) | — |

Evidence jest liczone **raz**, lokalnie, dla już zaakceptowanego kandydata
(`_geocode_query_context(core_query)` jest deterministyczne i bez I/O) — **zero nowych requestów**.

### 20.7 Reguła wyboru końcowej etykiety (wąska, wszystkie warunki łącznie)

`_geocode_confirmed_admin_label` zwraca wartość **tylko** gdy spełnione są jednocześnie:

1. **R1** kandydat jest granicą administracyjną: `_geocode_candidate_is_administrative(raw)`
   (`class ∈ {boundary, administrative}` **i** `type|addresstype == "administrative"`);
   `class == "place"` tej reguły **nie** uruchamia,
2. **R2** brak jakiegokolwiek pola miejscowości: `_geocode_place_values(address) == []`
   (city/town/village/hamlet/locality mają pierwszeństwo absolutne),
3. **R3** dowód jest mocny: `strength ∈ {place, localized}`; `display` **nigdy**
   (to odcina przypadek „Greater London” z `test_geocode_validation.py:2604`),
4. **R4** istnieje pole administracyjne z `_GEOCODE_LABEL_ADMIN_FIELDS`
   (`province, state, state_district, region, district, county, municipality`),
   którego znormalizowana wartość jest **równa** `evidence.matched`
   (równość, nie prefiks, nie podciąg, nie tekst użytkownika),
5. **R5** wartość jest formatowana tym samym `_format_admin_component` co dotąd
   (prefiksy PL tylko dla polskich adresów; dla `province` brak prefiksu).

Kolejność w `_select_short_location_label` — zmiana **ściśle addytywna**:

```
1. city / town / village / hamlet / locality      (bez zmian, z preferencją pełnego dopasowania)
2. municipality / county / state                  (bez zmian, z prefiksami PL)
3. NOWE: confirmed_admin                          (tylko R1–R5, inaczej puste)
4. postcode (gating) → country                    (bez zmian)
```

Punkt 3 siedzi **po** istniejącej pętli administracyjnej, więc żaden obecny wynik
`municipality`/`county`/`state` nie może się zmienić; reguła dotyka wyłącznie adresów,
które dziś spadają do `postcode` albo `country`.

Czego reguła **nie** robi (wprost, zgodnie ze zleceniem):

* nie awansuje dowolnego `province`/`state` — wartość musi być równa potwierdzonej nazwie,
* nie używa tekstu użytkownika jako etykiety (tylko strukturalne pola kandydata),
* nie stosuje zasady „forward zawsze wygrywa” (`_resolve_location_labels` nietknięty),
* nie promuje `display` ani `namedetails` **jako źródła** etykiety — `namedetails`
  pozostaje wyłącznie dowodem; etykieta jest zawsze wartością pola `address`,
* nie dodaje requestów, retry, nowych timeoutów ani nowych statusów/komunikatów.

### 20.8 Lista plików

| Plik | Zmiana |
|---|---|
| `location_bot.py` | `GeocodeEvidence` + `_geocode_candidate_evidence_detail`; `_geocode_candidate_evidence` → wrapper; `_geocode_candidate_is_administrative`; `_GEOCODE_LABEL_ADMIN_FIELDS`; `_geocode_confirmed_admin_label`; `confirmed_admin=None` w `_select_short_location_label` i `_format_public_location_parts`; `geocode_city_accepted` przekazuje `confirmed_admin`; sprostowanie docstringu `localized` |
| `test_geocode_admin_label.py` | **nowy** plik testów fazy 2 (payloady Oracle 1:1) |
| `RAPORT_AUDYT_WYSZUKIWANIE_KRAJOW.md` | ta sekcja + sekcja wdrożeniowa po akceptacji |

**Bez zmian:** `guest_bot_handler.py`, `i18n.py`, `users_store.py`, `country_index.py`,
`data/countries/*`, `tools/*`, `main_card.py`, `image_generator.py`, `weather_payload.py`,
`conftest.py`, `pytest.ini`, `requirements*.txt`, `.github/workflows/*`,
istniejące pliki testów fazy 1 (`test_geocode_validation.py`, `test_geocode_country_forms.py`,
`test_country_index.py`, `test_card_caption.py`).

### 20.9 Testy (offline, `--disable-socket`, zero Nominatima)

Fixtury: **dosłowne** payloady Oracle — forward `class=boundary`, `type=administrative`,
`addresstype=province`, `address={province, ISO3166-2-lvl4, country, country_code}`,
`namedetails={"name": "東京都", "name:pl": "Tokio"}`; reverse `address={neighbourhood,
quarter, city: "Suginami", ISO3166-2-lvl4, postcode, country, country_code}`.

| # | Wymagany test | Asercja |
|---|---|---|
| 1 | forward Tokio jako `province` + `namedetails.name:pl=Tokio` | `geocode_city_accepted("Tokio, Japonia")` → `('geocode_ok', 35.6768601, 139.7638947, 'Tokio', 'Tokio, Japonia')` |
| 2 | reverse `Suginami` + `country=Japonia` | `get_location_details_from_coords(..., query="Tokio, Japonia")` → `('Suginami', 'Suginami, Japonia', 'ok')` (formatter reverse bez zmian) |
| 3 | końcowa etykieta nie może być samą „Japonią” | `_resolve_location_labels(...)` → `('Tokio', 'Tokio, Japonia', 'ok')`; `short != 'Japonia'` |
| 4 | `/teraz Tokio, Polska` nadal odrzucone | `geocode_city_accepted("Tokio, Polska")` → `GEOCODE_NO_MATCH`, brak karty, 1 request |
| 5 | city/town/village mają pierwszeństwo | kandydat `{city: "Tokio", state/province: "Tokio"}` → `'Tokio'` z pola miejscowości; parametryzacja city/town/village/hamlet/locality |
| 6 | GPS i pinezka zachowują etykiety | `_resolve_location_labels(lat, lon, lang)` **bez query** → `('Suginami', 'Suginami, Japonia', 'ok')`; ten sam wynik przed i po zmianie |
| 7 | brak dodatkowych requestów | licznik atrapy: dokładnie 1 forward + 1 reverse dla `/teraz`, 1 forward dla `NO_MATCH`, 1 reverse dla GPS |

Testy wąskości reguły (żeby nie powstał „auto‑awans administracji”):

| # | Przypadek | Oczekiwanie |
|---|---|---|
| 8 | `province` **nie** równa potwierdzonej nazwie (`state="województwo mazowieckie"`, dowód z `name:pl="Warszawa"`) | brak awansu → etykieta jak dziś (`'województwo mazowieckie'`) |
| 9 | dowód wyłącznie `display` (kandydat bez `namedetails`, `name="Greater London"`, `province`) | brak awansu → etykieta jak dziś |
| 10 | `class="place"` z `province` równym nazwie | brak awansu (R1) |
| 11 | `province` równa nazwie, ale istnieje `city` | `city` wygrywa (R2) |
| 12 | kandydat z `municipality`/`county`/`state` | wynik **identyczny** jak dziś (reguła po istniejącej pętli) |
| 13 | brak `province` i brak `city` (tylko `country`) | etykieta = kraj, jak dziś |

Testy kontraktów i regresje niefunkcjonalne:

| # | Test |
|---|---|
| 14 | `_geocode_candidate_evidence` nadal zwraca 2‑krotkę; `_geocode_select_accepted` 2‑krotkę; `geocode_city_accepted` 5‑krotkę |
| 15 | `GeocodeEvidence` dla payloadu Oracle: `matched='tokio'`, `strength='localized'`, `source='namedetails'`, `field='name:pl'`, `admin_label='Tokio'`, `admin_field='province'` |
| 16 | `confirmed_admin` domyślnie `None` → `_select_short_location_label` i `_format_public_location_parts` zachowują się **identycznie** (przegląd istniejących asercji etykiet) |
| 17 | `POISON` (`location.address`) nigdy nie trafia do etykiety |
| 18 | tryb gościa (`?12 Tokio, Japonia`) → ta sama etykieta co `/teraz`, 1 forward |
| 19 | Python 3.8: `ast.parse(feature_version=(3,8))` + brak `removeprefix/removesuffix/match` dla zmienionego pliku |
| 20 | pełny zestaw: `pytest -q` — oczekiwane `3710 + N passed` (N = nowe testy), zero nowych faili |

### 20.10 Wpływ na dane i UI — do wyraźnej akceptacji (D12)

Zmiana **dotyka** `Users.location_label`, ale wyłącznie dla wąskiej klasy zapytań:

| Ścieżka | Przed | Po |
|---|---|---|
| `/teraz Tokio, Japonia` (tytuł karty) | `Japonia` | `Tokio` |
| `/teraz Tokio, Japonia` (caption) | `Japonia` | `Tokio, Japonia` |
| `/miasto Tokio, Japonia` → `Users.location_label` | `Japonia` | `Tokio` |
| skróty gościa `?12/?n/.n/?14` | `Japonia` | `Tokio` |
| GPS / pinezka / WebApp | bez zmian | **bez zmian** |
| `PENDING_SAVE` (zapis po GPS/pinezce) | bez zmian | **bez zmian** |
| komunikaty i statusy | bez zmian | **bez zmian** |

Nie ma migracji istniejących wierszy Users: zapis zdarzy się przy następnym świadomym
`/miasto`. Współrzędne, `location_source`, `lat_round`/`lon_round` — bez zmian.

### 20.11 Decyzje do podjęcia przed implementacją

| ID | Decyzja | Rekomendacja |
|---|---|---|
| **D12** | Zgoda na zmianę `Users.location_label` i tytułu karty dla klasy „zaakceptowana granica administracyjna bez pola miejscowości” (tabela 20.10) | **tak** — to jest istota zgłoszonego błędu |
| **D13** | `_geocode_candidate_is_safe`: dopuścić `category` jako alias `class` (odporność na `jsonv2`) + testy obu kształtów payloadu | **osobno**, nie w tej poprawce; dziś geopy 2.5 wysyła `format=json`, więc to tylko ryzyko przyszłe |
| **D14** | Zakres `_GEOCODE_LABEL_ADMIN_FIELDS`: `province, state, state_district, region, district, county, municipality` (i tak osiągalne są tylko pola spoza `_ADMIN_ADDRESS_FIELDS`) | **tak**, z testem 12 pilnującym braku zmian |
| **D15** | Arbitraż reverse/forward (§13): **nie** zmieniamy w fazie 2 — po naprawie etykiety forward i tak wygrywa poprawnie | **bez zmian** |

### 20.12 Czego faza 2 świadomie NIE robi

1. Zmian w GPS, pinezce, WebApp, `PENDING_SAVE`, harmonogramach, obrazkach, `weather_payload`.
2. Nowych statusów, nowych komunikatów, zmian `i18n.py`.
3. Dodatkowych requestów do Nominatima, retry, structured query, cache kraju.
4. Promowania `namedetails`/`display_name` **jako źródła** etykiety.
5. Hierarchii administracyjnej, fuzzy, transliteracji, odmiany.
6. Wariantu B dla `georgia`, automatycznego rozstrzygania kolizji kraj–kontekst.
7. Zmian w `guest_bot_handler.py` i w plikach testów fazy 1.

Numery linii w sekcji 20 są **aktualne dla HEAD `b8130bd`** i były weryfikowane
greppem; numery w Załączniku A pochodzą z audytu sprzed fazy 1 i po fazie 1 są
przesunięte (np. `_geocode_query_parse` 2442 → 2464) — sekcja 20 jest wiążąca.

---

## 21. Faza 2 — status implementacji (po akceptacji D12/D14/D15, D13 odłożone)

Zakres zrealizowany **dokładnie** zgodnie z akceptacją: `location_bot.py`,
nowy `test_geocode_admin_label.py`, sprostowanie docstringu `localized`,
aktualizacja tego raportu. Nic nie zostało zacommitowane ani wypushowane.

### 21.1 Co zostało zmienione (`location_bot.py`)

| Element | Zmiana |
|---|---|
| `from collections import namedtuple` | nowy import stdlib (jedyny) |
| `GeocodeEvidence` / `_GEOCODE_NO_EVIDENCE` | **nowe**: 7‑polowy namedtuple kontraktu evidence (§20.6) |
| `_GEOCODE_LABEL_ADMIN_FIELDS` | **nowe**: `province, state, state_district, region, district, county, municipality` (D14) |
| `_geocode_candidate_is_administrative(raw)` | **nowa**: wydzielona gałąź administracyjna; `_geocode_candidate_is_safe` woła ją zamiast duplikować warunek (zachowanie identyczne) |
| `_geocode_namedetails_entries` / `_geocode_raw_name_entries` / `_geocode_place_entries` | **nowe**: warianty z kluczem/polem i oryginalną pisownią |
| `_geocode_namedetails_values` / `_geocode_raw_name_values` / `_geocode_place_values` | **bez zmian kontraktu**: teraz cienkie wrappery nad `*_entries` (ta sama kolejność i zawartość) |
| `_geocode_candidate_evidence_detail(...)` | **nowa**: pełny `GeocodeEvidence` |
| `_geocode_confirmed_admin_label(...)` | **nowa**: reguła R1–R5 → `(wartość, pole)` |
| `_geocode_candidate_evidence_with_admin_label(...)` | **nowa**: jedyne miejsce wypełniające `admin_label`/`admin_field` |
| `_geocode_candidate_evidence(...)` | **kontrakt bez zmian**: `(matched, strength)` — wrapper |
| `_select_short_location_label(..., confirmed_admin=None)` | nowy opcjonalny parametr; wartość wchodzi **po** pętli `municipality/county/state` i **przed** `postcode/country`, przez wspólny `_format_admin_component` (kontrola R5, §21.8) |
| `_format_public_location_parts(..., confirmed_admin=None)` | nowy opcjonalny parametr, przekazuje do short label |
| `geocode_city_accepted(...)` | liczy evidence lokalnie i przekazuje `confirmed_admin`; **nadal 5‑krotka** |
| docstring `_GEOCODE_EVIDENCE_LOCALIZED` i `_geocode_candidate_evidence_detail` | **sprostowanie**: `localized` obejmuje też przypadek bez pól miejscowości (dokładnie Tokio) |

Kolejność wyboru krótkiej etykiety po zmianie:

```
1. city / town / village / hamlet / locality    (bez zmian)
2. municipality / county / state                (bez zmian, z prefiksami PL)
3. confirmed_admin                              (NOWE, tylko R1–R5)
4. postcode (gating) → country                  (bez zmian)
```

### 21.2 Czego NIE zmieniono (zweryfikowane `git diff --stat`)

`guest_bot_handler.py`, `i18n.py`, `users_store.py`, `country_index.py`, `data/`,
`tools/`, `.github/`, `conftest.py`, `pytest.ini`, `requirements*.txt`,
`test_geocode_validation.py`, `test_geocode_country_forms.py`,
`test_country_index.py`, `test_card_caption.py` — **diff pusty (0 linii)**.

Nie zmieniono: `_resolve_location_labels`, `_reverse_label_matches_query`,
`get_location_details_from_coords`, `_coarse_reverse_settlement`,
`_geocode_select_accepted`, `_geocode_candidate_is_safe` (zachowanie),
GPS/pinezki/WebApp, `PENDING_SAVE`, komunikatów i statusów, `_GEOCODE_ADMIN_FIELDS`,
`_ADMIN_ADDRESS_FIELDS`, `_LOCALITY_ADDRESS_FIELDS`, `_PUBLIC_ADDRESS_FIELDS`.
**D13 (`category` jako alias `class`) nie zostało zaimplementowane** — jest
natomiast przypięte testem charakterystycznym (§21.4, test 12).

### 21.3 Weryfikacja — 11 wymaganych warunków

| # | Warunek | Wynik | Dowód |
|---|---|---|---|
| 1 | literalny payload Oracle dla Tokio | ✅ | `test_forward_fixture_is_the_literal_oracle_payload`, `test_reverse_fixture_is_the_literal_oracle_payload`, `test_forward_fixture_has_no_settlement_field` |
| 2 | `/teraz Tokio, Japonia` → `Tokio` / `Tokio, Japonia` | ✅ | `test_forward_labels_use_the_confirmed_province`, `test_card_label_is_not_the_country_alone`, `test_every_country_form_gives_the_same_label` |
| 3 | `/teraz Tokio, Polska` → `GEOCODE_NO_MATCH` | ✅ | `test_wrong_country_is_still_rejected`, `test_confirmed_admin_cannot_bypass_the_hard_country_filter` |
| 4 | dokładnie 1 forward + 1 reverse | ✅ | `test_exactly_one_forward_and_one_reverse_for_a_successful_teraz`, `test_label_helpers_do_not_touch_the_geocoder` |
| 5 | GPS/pinezka bez zmian | ✅ | `test_gps_and_pin_keep_the_previous_label`, `test_reverse_formatter_ignores_confirmed_admin_by_default`, `test_webapp_path_has_no_query_and_no_forward` |
| 6 | `display` nie uruchamia awansu | ✅ | `test_rule_r3_rejects_display_only_evidence`, `test_display_only_candidate_keeps_the_country_label`, `test_greater_london_style_admin_is_untouched` |
| 7 | `class=place` nie uruchamia reguły | ✅ | `test_rule_r1_rejects_class_place`, `test_class_place_candidate_does_not_start_the_new_rule` |
| 8 | city/town/village mają pierwszeństwo | ✅ | `test_settlement_fields_keep_absolute_priority` (5 pól), `test_rule_r2_rejects_every_settlement_field` (5 pól) |
| 9 | kontrakty 2‑krotki i 5‑krotki | ✅ | `test_public_contracts_keep_their_arity`, `test_evidence_contract_two_tuple_is_unchanged`, `test_evidence_namedtuple_has_the_documented_fields` |
| 10 | pełny zestaw testów przechodzi | ✅ | **3820 passed, 3 skipped** (baseline 3710 + 110 nowych) |
| 11 | testy offline | ✅ | `pytest.ini` = `--disable-socket`; zero markerów `enable_socket`; `test_no_socket_is_needed` |

Pomiar end‑to‑end po implementacji (atrapa geokodera, payloady Oracle, bez sieci):

```
'Tokio, Japonia' -> geocode_city_accepted: ('geocode_ok', 35.6768601, 139.7638947,
                                            'Tokio', 'Tokio, Japonia')
                    KARTA: ('Tokio', 'Tokio, Japonia', 'ok')      requesty: 2 (1 fwd + 1 rev)
'Tokio, Polska'  -> ('geocode_no_match', None, None, None, None)  requesty: 1
GPS/pinezka      -> ('Suginami', 'Suginami, Japonia', 'ok')       requesty: 1 (reverse)
```

Dodatkowe sprawdzenia: `ast.parse(feature_version=(3, 8))` → OK dla
`location_bot.py`, `test_geocode_admin_label.py`, `country_index.py`;
`python -m compileall` → OK; brak nowych linii >99 znaków w `location_bot.py`.

### 21.4 Nowe testy (`test_geocode_admin_label.py`, 110 testów)

| Sekcja | Zakres |
|---|---|
| 1 | literalność payloadów Oracle (forward + reverse), brak pól miejscowości, filtr kraju z fazy 1 |
| 2 | kontrakt evidence: pola namedtuple, zgodność wrapper↔detail dla 6 kombinacji, `*_entries` vs stare funkcje, klucze nienazwowe (`ref`, `ISO3166-2`, `_place_name:*`, `alt_name:*`) nie są dowodem |
| 3 | reguła R1–R5 jako unit: każde R osobno, wszystkie 7 pól administracyjnych, kolejność pól, normalizacja (case/diakrytyki), brak użycia tekstu użytkownika, brak awansu niepowiązanego regionu |
| 3B | **kontrola R5**: mapa prefiksów, równoważność z `_format_admin_component` dla 7 pól × 2 adresy, brak prefiksu dla pól osiągalnych, payload Tokio, polski adres administracyjny, adres niepolski (6 języków), istniejące `municipality`/`county`/`state`, `confirmed_admin=None`/`""`, czyszczenie wartości |
| 4 | etykiety forward i karta; reverse bez zmian; postcode nadal nieupubliczniany |
| 5 | odrzucenie błędnego kraju; reguła nie omija twardego filtra `country_code`; brak karty |
| 6 | liczba requestów (forward/reverse), helpery etykiet nie dotykają geokodera |
| 7 | GPS / pinezka / WebApp / `get_city_from_coords` bez zmian |
| 8 | pierwszeństwa: 5 pól miejscowości, `municipality`/`county`/`state` (adres PL i nie‑PL), `confirmed_admin` vs postcode/country, Greater London, polska granica z `municipality` |
| 9 | kontrakty publiczne (arytmetyka krotek), adapter `geocode_city_labels_status` |
| 10 | tryb gościa: `?12`, `?n`, `.n`, wzmianka @bot — te same etykiety, 1 forward |
| 11 | `POISON` (`location.address`) nigdy w etykiecie, brak sieci, Python 3.8, brak nowych zależności |
| 12 | **znane ograniczenie D13**: payload `jsonv2` z `category` → `GEOCODE_NO_MATCH` (test charakterystyczny do odwrócenia po hardeningu) |
| 13 | spójność atrap z interfejsem geopy |

### 21.5 Wpływ na dane i UI (zgodnie z D12)

| Ścieżka | Przed | Po |
|---|---|---|
| `/teraz Tokio, Japonia` — tytuł karty | `Japonia` | **`Tokio`** |
| `/teraz Tokio, Japonia` — caption | `Japonia` | **`Tokio, Japonia`** |
| `/miasto Tokio, Japonia` → `Users.location_label` | `Japonia` | **`Tokio`** |
| skróty gościa (`?12`, `?n`, `.n`, `?14`, @bot) | `Japonia` | **`Tokio`** |
| GPS / pinezka / WebApp / `PENDING_SAVE` | `Suginami` | **bez zmian** |
| komunikaty i statusy | — | **bez zmian** |
| współrzędne, `location_source`, `lat_round`/`lon_round` | — | **bez zmian** |

Bez migracji istniejących wierszy Users — nowa etykieta zapisuje się przy
następnym świadomym `/miasto`.

### 21.6 Znane ograniczenia po fazie 2

1. **D13 odłożone**: payload `jsonv2` (`category` zamiast `class`) jest odrzucany
   przez `_geocode_candidate_is_safe`. Produkcja (geopy 2.5.0, `format=json`)
   zwraca `class`, więc dziś nie ma wpływu; przypięte testem charakterystycznym.
2. **R4 wymaga pełnej równości**: `province = "Tokyo Metropolis"` przy
   potwierdzonej nazwie „tokio” NIE awansuje (zakaz prefiksów/podciągów/fuzzy
   wynika wprost z akceptacji D14) — etykieta zostaje dotychczasowa.
3. Brak odmiany, transliteracji, alfabetów niełacińskich i fuzzy — bez zmian
   względem fazy 1.
4. `Tbilisi, Georgia` — bez zmian (wariant A z fazy 1, `context_ambiguous`).

### 21.7 Do kolejnej akceptacji

| Plik | Zmiana (`git diff --numstat`) |
|---|---|
| `location_bot.py` | **+273 / −62** |
| `test_geocode_admin_label.py` | nowy, 1119 linii, **110 testów** |
| `RAPORT_AUDYT_WYSZUKIWANIE_KRAJOW.md` | +546 (sekcje 20 i 21) |

* `pytest -q` → **3820 passed, 3 skipped** (zero regresji względem baseline 3710 + 3),
* `git diff --check` → exit 0, bez uwag (§21.8),
* `git status` → tylko te trzy pliki; zabronione pliki: diff pusty,
* `git log -1` → nadal `b8130bd` — **brak commitu / pusha / PR**, zgodnie z poleceniem.

### 21.8 Kontrola R5 — rozbieżność między raportem a kodem (znaleziona i zamknięta)

**Zgłoszona rozbieżność była rzeczywista.** §20.7 (R5) deklarował: „wartość jest
formatowana tym samym `_format_admin_component` co dotąd”, a zaimplementowany kod
zwracał `confirmed_admin` bezpośrednio, z uzasadnieniem tylko w komentarzu.
Deklaracja ze specyfikacji była wiążąca, więc **kod został ujednolicony ze
specyfikacją** (wariant 1), nie odwrotnie.

**Pomiar rozstrzygający** (`_format_admin_component(field, value, polish)` dla
wszystkich 7 pól × 2 wartości × `polish` ∈ {False, True}):

| Pole | `polish=False` | `polish=True` | Osiągalne dla `confirmed_admin`? |
|---|---|---|---|
| `province` | wartość dosłowna | wartość dosłowna | **tak** |
| `state_district` | wartość dosłowna | wartość dosłowna | **tak** |
| `region` | wartość dosłowna | wartość dosłowna | **tak** |
| `district` | wartość dosłowna | wartość dosłowna | **tak** |
| `state` | wartość dosłowna | **`województwo …`** | nie — wcześniejsza pętla |
| `county` | wartość dosłowna | **`powiat …`** | nie — wcześniejsza pętla |
| `municipality` | wartość dosłowna | **`gmina …`** | nie — wcześniejsza pętla |

Mapa prefiksów w `_format_admin_component` ma wpisy wyłącznie dla
`municipality`/`county`/`state`; pola osiągalne to dokładnie różnica zbiorów
`_GEOCODE_LABEL_ADMIN_FIELDS − _ADMIN_ADDRESS_FIELDS` =
`(province, state_district, region, district)`.

**Odpowiedź na pytanie kontrolne:** zachodzą oba punkty jednocześnie i nie są
w sprzeczności —

1. wartość **przechodzi** przez `_format_admin_component` (jedno źródło prawdy
   dla prefiksów PL, tak jak deklarowała specyfikacja R5),
2. dla pól `province`/`state_district`/`region`/`district` oznacza to **świadomie
   brak prefiksu**, bo tych pól nie ma w mapie prefiksów; jest to więc
   równoważne zwróceniu wartości dosłownej — **dziś zero różnicy w zachowaniu**,
   a przy przyszłej zmianie `_ADMIN_ADDRESS_FIELDS` prefiks doda się sam.

**Zmiana w kodzie** (`_select_short_location_label`, jedyne miejsce):

```python
     confirmed = _clean_location_component(confirmed_admin)
     if confirmed:
-        return confirmed
+        confirmed_field = next(
+            (
+                name for name in _GEOCODE_LABEL_ADMIN_FIELDS
+                if _clean_location_component(address.get(name)) == confirmed
+            ),
+            None,
+        )
+        return _format_admin_component(confirmed_field, confirmed, polish=polish)
```

**Dowód, że zachowanie się nie zmieniło:** `pytest -q` przechodził przed tą
poprawką (3792 passed, 3 skipped) i przechodzi po niej (**3820** = 3792 + 28
nowych testów R5, 3 skipped) — **żaden istniejący test nie wymagał zmiany
oczekiwania**. Pomiar end‑to‑end jest identyczny przed i po:
`Tokio` / `Tokio, Japonia`; `Tokio, Polska` → `NO_MATCH`; GPS/pinezka →
`Suginami`; 1 forward + 1 reverse.

**Nowe testy (28, sekcja 3B pliku testowego):**

| Test | Co pilnuje |
|---|---|
| `test_prefix_map_covers_only_municipality_county_state` | mapa prefiksów: 3 pola z prefiksem, 4 osiągalne bez |
| `test_confirmed_admin_uses_the_same_formatter_as_other_admin_fields` | 7 pól × {PL, JP}: wynik == `_format_admin_component(field, value, polish)` |
| `test_reachable_fields_never_gain_a_prefix` | 4 pola osiągalne × {PL, JP} → wartość bez prefiksu |
| `test_tokyo_payload_label_is_the_bare_province_name` | **payload Tokio z `province`** → `Tokio` / `Tokio, Japonia` |
| `test_polish_administrative_address_keeps_its_prefix` | **polski adres** (`state`) → `województwo mazowieckie`, z potwierdzeniem i bez |
| `test_polish_address_with_only_a_province_gets_no_prefix` | polski adres z samym `province` → bez prefiksu |
| `test_non_polish_address_never_gains_a_polish_prefix` | **adres niepolski** → brak prefiksów, także dla `state`, w 6 językach |
| `test_existing_municipality_county_state_behaviour_is_unchanged` | **istniejące pola**: `gmina`/`powiat`/`województwo` + display z krajem, niezależnie od `confirmed_admin` |
| `test_confirmed_admin_none_is_identical_to_omitting_the_argument` | `None`/`""` == brak argumentu (reverse, GPS, pinezka) |
| `test_confirmed_admin_is_cleaned_before_use` | czyszczenie białych znaków; `None`/`{}`/`[]`/`()` → brak awansu; kontrakty `_clean_location_component` bez zmian |

**Sprawdzenia końcowe:** `pytest -q` → **3820 passed, 3 skipped**;
`git diff --check` → **bez uwag** (sonda kontrolna: plik z ogonami daje exit 3
i komunikat, więc brak komunikatu oznacza czystość); nowy plik testowy sprawdzony
dodatkowo `git diff --check --no-index` + grep — 0 linii z białymi znakami na
końcu, plik kończy się znakiem nowej linii.

**Zakres nie został przekroczony:** zmieniono wyłącznie sposób sformatowania tej
samej, nowej wartości wewnątrz reguły fazy 2. D13 (`category`) nie zostało
zaimplementowane; `guest_bot_handler.py`, `i18n.py`, `users_store.py`,
`country_index.py`, `data/`, `tools/`, workflowy i pliki testów fazy 1 — diff pusty.
