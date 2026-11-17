# Plan realizacji etapu 1 — kontrakt danych i statusy coverage

**Podstawa:** `docs/audit/2026-11-weekly/dokument_decyzji_architektonicznej.md` (§4 model
7 warstw, §5 mapa etapów i bramek) oraz `raport_koncowy.md` w tym samym katalogu.
**Charakter tego dokumentu:** wyłącznie planowanie na podstawie read-only rozpoznania
kodu (gałąź `arena/228fdd32-pogoda-world`, HEAD `61c92ba`). **Nie zmieniono żadnego
pliku kodu, testu ani konfiguracji; z tego etapu nie powstał żaden commit.**
**Cel etapu 1 (bramka A):** zaakceptowana na piśmie specyfikacja kontraktu warstw,
semantyki `coverage_status` i rejestru konsumentów. Żaden konsument nie jest
przełączany; `daily_diag_matched` pozostaje `observation_only=True`.

> Uwaga o podstawie planowania: szczegółowy wykaz zadań Z1.x–Z5.x z §6 dokumentu
> decyzji nie zachował się w archiwum (oznaczono notą archiwalną w kopii w repo).
> Niniejszy plan odtwarza zakres etapu 1 z §5 dokumentu decyzji oraz z rozpoznania
> kodu i może służyć jako podstawa do odtworzenia wykazu.

---

## 1. Inwentaryzacja: pliki, funkcje i testy per obszar

Odnośniki `plik:linia` dotyczą stanu zweryfikowanego read-only (grep/odczyt).

### 1.1 Canonical weather values (godziny po fuzji OM + Yr.no, pola kanoniczne, `*_eff`)

| Element | Lokalizacja |
|---|---|
| Budowa payloadu (fetch + fuzja + kolejność etapów) | `weather_payload.build_payload_for_location` (`weather_payload.py:565`); fuzja i nadpisanie `weather_code` z `yrno_symbol_code` ok. `:600–672`; `_select_hours` (`:392`) |
| Parsery źródłowe | `_fetch_openmeteo` (`:302`), `_fetch_yrno` (`:237`) |
| Utrwalanie surowych wartości przed fuzją | `_add_om_raw_and_provenance` (`:193`), `_add_yr_raw_and_provenance` (`:206`), listy `_OM_RAW_FIELDS`, `_YR_PROVENANCE_FIELDS`, mapowanie `_YR_RAW_ON_OM` (`:141–190`) |
| Filtr opadów widmowych `*_eff` (precip_mm ≤0,2 mm → trace; podmiana kodów/symboli) | `main_card._send_card_to_user` (`main_card.py:544–601`) — wykonywany **poza** `build_payload_for_location`, na żywym payloadzie, przed wyborem layoutu |
| Blokada jakości „dwa modele" dla `/day` | `main_card._send_card_to_user` (`:533–541`) |
| Konsumenci pól kanonicznych | `prepare_layout`, `prepare_now_layout`, `prepare_future_layout` (przez `payload["hours"]`), `confidence_gate.compute_trust_report`, `worth_knowing` |

**Testy istniejące:** `test_weather_payload_raw_sources.py` (fuzja zachowuje surowe
wartości i provenance; brak danych ≠ zero; fallback OM-only/Yr-only; brak fuzji bez
dokładnego dopasowania czasu; cache nie nadpisuje surowych wartości;
`test_future_layout_does_not_mutate_original_or_raw_records`),
`test_daily_diagnostics_matched.py` m.in.
`test_audited_payload_uses_raw_fields_not_canonical_fused_values`,
`test_fused_only_payload_reports_matched_from_raw_pairs`,
`test_fused_row_without_raw_om_field_never_reconstructs_om_from_canonical`.

### 1.2 Provenance (pochodzenie rekordów i pól, wybór źródła dnia)

| Element | Lokalizacja |
|---|---|
| `source` rekordu (`openmeteo`/`yrno`) po fuzji | parsery `weather_payload.py` (rekord OM po fuzji może mieć pola ze źródłem Yr.no — patrz `_add_yr_raw_and_provenance`) |
| Provenance per pole | `*_source` (`_OM_PROVENANCE_FIELDS`, `_YR_PROVENANCE_FIELDS`), `uv_index_semantics` (`standard` vs `clear_sky`) |
| Provenance payloadu | `forecast_source` (`:577–604`), `model_updated_at_local` |
| Wynik wyboru źródła dnia | `daily_source.DailyPick` (`source`, `marker=" *"` dla fallbacku) — `day_entry` w `/future` używa `label` bez markera; renderer rysuje `label`, więc marker źródła jest dziś niewidoczny |

**Testy istniejące:** provenance głównie przez `test_weather_payload_raw_sources.py`
(pola `*_om/*_yr/*_source`, semantyka UV). **Brak testu**, który pilnuje, że wynik
wyboru źródła dnia jest (albo świadomie nie jest) widoczny w layoucie `/future`.

### 1.3 `daily_diag` (legacy diagnostics z pól kanonicznych)

| Element | Lokalizacja |
|---|---|
| Producent | `weather_payload.build_daily_diagnostics` (`weather_payload.py:38`); ekstrema OM/Yr z pól kanonicznych `temp_c`; zapis `audit_spread.jsonl` przy `ENABLE_AUDIT_LOG` (`:90–104`) |
| Progi volatility | `is_volatile = spread_max >= VOLATILITY_SPREAD_C` (env, domyślnie 5,0 °C) `lub spread_min >= 4,0 °C` (`:75`) |
| Konsumenci | `daily_source.pick_hours_for_daily_summary` (przez `n_yr`), `prepare_layout` (wybór dni `:1269`, alert weekendowy `:1583–1647`), `prepare_future_layout` (wybór dni `:55`, tag `:61–104`) |

**Testy istniejące:** kształt legacy jest zamrożony asercjami w
`test_daily_diagnostics_matched.py` (pełny zestaw kluczy `:147–157`; reguła
`spread_min >= 4,0` `:485–497`) oraz `test_weather_payload_raw_sources.py:253`.
Brak dedykowanych testów jednostkowych samego `build_daily_diagnostics`
(ekstrema, brak modelu → `spread=0,0`, zapis audit log wyłączany flagą).

### 1.4 `daily_diag_matched` (matched comparison, offline/shadow)

| Element | Lokalizacja |
|---|---|
| Moduł | `daily_diagnostics_matched.py`: `_timestamp_identity` (`:80`), `classify_record` (`:113`), `raw_om_temperature` (`:133`), `raw_yr_temperature` (`:144`), `_coverage_status` (`:191`), `build_daily_diagnostics_matched` (`:214`) |
| Podłączenie | `weather_payload.py:692–705` (try/except — błąd matched nie może wywrócić payloadu), klucz `payload["daily_diag_matched"]` (`:733`) |
| Flagi bezpieczeństwa | `observation_only=True`, `coverage_threshold=None`, `DIAGNOSTICS_VERSION="matched_v1"` |
| Konsumenci produkcyjni | **brak** (pilnowane testem parametrycznym) |

**Testy istniejące:** `test_daily_diagnostics_matched.py` — 21 funkcji testowych,
w tym dopasowanie po pełnym timestampie, DST/fold, rozróżnienie None/0, statusy
pokrycia, niemutowanie wejścia, brak zapisu audit logu oraz strażnicy:
`test_consumers_do_not_reference_matched_diagnostics` (daily_source, confidence_gate,
prepare_layout, prepare_future_layout, worth_knowing),
`test_layout_and_worth_knowing_are_insensitive_to_the_new_key`,
`test_confidence_gate_result_is_identical_for_both_payload_variants`,
`test_payload_gains_only_the_new_key`.

### 1.5 `is_volatile` — uwaga: trzy różne znaczenia jednej nazwy

| Znaczenie | Lokalizacja | Konsument |
|---|---|---|
| (a) legacy volatility z dziennych ekstremów modeli | `daily_diag[date]["is_volatile"]` (`weather_payload.py:75`) | alert weekendowy `/day`, tag `/future` (flaga `ENABLE_VOLATILITY_UI`) |
| (b) detektor rozjazdu godzinowego (opad/wiatr/chmury) — inna semantyka | `confidence_gate.TrustReport.is_volatile` (`confidence_gate.py:107–108`) | `prepare_layout` (`:1461` notka zgodności, `:1473` age-note), łagodzenie Hero |
| (c) lokalna zmienność opadów na `/now` | `prepare_now_layout` (`is_volatile_precip`, `:231`) | logika Hero/bloków `/now` |

**Testy istniejące:** dla (a) tylko pośrednie (zamrożenie kształtu w suite matched);
dla (b) wyłącznie test inwariantności względem `daily_diag_matched`
(`test_daily_diagnostics_matched.py:452`) — **brak testów jednostkowych
`compute_trust_report`**; dla (c) brak wydzielonych testów.

### 1.6 `coverage_status`

| Element | Lokalizacja |
|---|---|
| Jedyna definicja | `daily_diagnostics_matched._coverage_status` (`:191`) na bazie timestampów, nie liczby rekordów; stałe `STATUS_*`, `ALL_STATUSES` (6 statusów) |
| Obecność w wyniku | per dzień jako `coverage_status`; zliczane w `coverage_status_counts` |
| Konsumenci produkcyjni | **brak**; `coverage_threshold` celowo `None` |

**Testy istniejące:** statusy w `test_daily_diagnostics_matched.py` (m.in.
`test_no_common_hours_has_explicit_status_and_no_fake_zero_spread`,
`test_partial_coverage_ignores_extremes_outside_common_window`).

### 1.7 `daily_source` (wybór godzin do summary dobowego)

| Element | Lokalizacja |
|---|---|
| Reguła | `daily_source.pick_hours_for_daily_summary` (`daily_source.py:16`): Yr.no tylko gdy `n_yr >= MIN_YR_SAMPLES_FOR_DAILY_SUMMARY` (=18) **z legacy `daily_diag`** i ≥6 rekordów `source="yrno"`; inaczej OM; ostatecznie `mixed`; `DailyPick.marker=" *"` dla fallbacku |
| Konsumenci | `prepare_layout` (`:1269`, `:1612`, weekend `:1658–1659`), `prepare_future_layout` (`:55`) dla wszystkich dni +1…+14 |

**Testy istniejące:** **brak dedykowanego pliku testów**; `daily_source` występuje
tylko w strażniku `test_consumers_do_not_reference_matched_diagnostics`.

### 1.8 OWM / nowcast

| Element | Lokalizacja |
|---|---|
| Pobranie i cache | `owm_nowcast.get_current_weather` (`:207`); reużycie w payloadzie: `weather_payload._get_owm_cached` (`:220`, TTL 180 s), `payload["owm_current"]`, flaga `ENABLE_OWM_NOWCAST` |
| Świeżość | `extract_owm_current` (`:48`), `extract_owm_dt_seconds` (`:57`, obsługa ms), `owm_is_fresh` (`:74`, `OWM_FRESH_MAX_SEC` domyślnie 5400 s) |
| Klasyfikacja korekty | `classify_owm_cloud_correction` (`:131`): fresh-gate, high-only gate, próg różnicy ≥25 pkt, warunek innej etykiety nieba, ograniczenie live ≥85% przy UV>1,2 → 65%; zwraca `should_override_hour0`, `should_show_snapshot`, `high_only_gate`, `reason` |
| Notka o ukrytym opadzie | `nowcast_note` (`:254`): model <0,1 mm i OWM ≥0,2 mm |
| Zastosowanie `/now` | `prepare_now_layout` (`:112–203`): własny fallback pobrania (timeout 3 s), override tylko bieżącej godziny, `_cld_override` wpisywany w rekord godziny `h0` |
| Zastosowanie `/day` | `prepare_layout` (`:1679–1780`): leniwe bramkowanie wywołania (`should_call_owm`), wyłącznie linia snapshotu „Teraz: …"; `cld_overrides` do bloków |
| Martwy kod | `apply_cloud_correction` (`owm_nowcast.py:287`) — bez wywołań w kodzie i testach; docstring `nowcast_note` błędnie do niego odsyła |
| `/future` | brak konsumenta (zgodnie z kontraktem warstwy 3) |

**Testy istniejące:** **brak jakichkolwiek testów** dla `classify_owm_cloud_correction`,
`nowcast_note`, `owm_is_fresh`, `find_model_hour_for_now` (grep po testach: 0 trafień).
Pośrednio: bramka kosztowa w `test_card_invariants.py`
(`test_ghost_precip_does_not_trigger_owm_call`,
`test_genuinely_dry_and_damp_hour_still_triggers_owm`) oraz wyłączenia sieci
w `conftest.py`. To jest problem P5 z dokumentu decyzji.

### 1.9 Coast

| Element | Lokalizacja |
|---|---|
| Detekcja | `coast_detector`: `CoastIndex` (`:252`), `compute_signature` (`:285`), `get_or_compute_coast_signature` (`:387`), wariant leniwy (`:431`), `get_coastal_alert_mode` (`:499`), `coastal_distance_guard` (`:482`), progi `marine_storm_thresholds` (`:170`), sezon plażowy `in_beach_season` (`:187`), `coast_stack_status`/`warn_coast_disabled` |
| Runtime i cache | `coast_runtime` (`GLOBAL_COAST_STORE`, `ensure_coast_index`); **duplikacja setupu** w `main_card.py:51–68` (własny store i własny `ensure_coast_index`) |
| Konsumenci | `/day`: alerty marine/beach w `prepare_layout` (`:1492–1577`); `/now`: `coastal_note` i priorytet linii kontekstu w `prepare_now_layout` (`:604–675`, `:732`); `/future`: brak |
| Martwa flaga | `payload["coastal_day_alert"]` czytana w `worth_knowing.py:1840` (przekazywana do `_candidates_level1`), **nigdzie niezapisywana** |

**Testy istniejące:** obszerne — `test_coast.py` (47 testów: progi, sezony, sektory,
guard odległości, brak stosu geo, wersje cache) oraz `test_day_beach.py`
(m.in. `test_only_day_application_call_site_opts_in`,
`test_layout_no_additional_weather_requests_or_payload_mutations`).

### 1.10 Renderery `/day`, `/now`, `/future` i renderer PNG

| Element | Lokalizacja |
|---|---|
| `/day` | `prepare_layout.prepare_layout_data` (`prepare_layout.py:1070`) |
| `/now` | `prepare_now_layout.prepare_now_layout_data` (`:60`) |
| `/future` | `prepare_future_layout.prepare_future_layout_data` (`:18`); kopie godzin, przeszczep POP OM→Yr (`:40–46`) |
| Martwa ścieżka tagu `/future` | `prepare_future_layout:104` ustawia `summary["diag_tag"]`/`diag_severity`, ale `day_entry` (`:108–117`) ich **nie kopiuje**; renderer (`image_generator:442–447`, `:678–683`) nigdy ich nie dostaje z `/future` |
| Kolizja tag ↔ Księżyc w rendererze | `image_generator._plan_days_card_layout` (`:407`): `has_moon and not diag_tag` (`:460–463`) — Moon wygrywa przy kolizji |
| Renderer | `image_generator.generate_weather_card` (`:710`), `_plan_hero_moon_line` (`:524`), `draw_days_card` (`:573`) |
| Po-renderowa kontrola | `sanity_checker.run_sanity_check` — wynik tylko logowany (`main_card.py:631`), nie blokuje wysyłki |

**Testy istniejące:** `test_trend_moon_icons.py` (Moon w wierszu dnia i hero,
niezależność od pogody, brak mutacji), `test_card_invariants.py` (inwarianty tekstu
kart), layouty pośrednio w wielu suite'ach. Brak testu utrwalającego martwość
ścieżki `diag_tag` jako zachowanie udokumentowane.

### 1.11 WK (worth knowing) i alerty

| Element | Lokalizacja |
|---|---|
| Alerty payloadu | `weather_payload._generate_alerts` (`:473`, horyzont 36 h: wiatr, upał itd.) |
| Alert weekendowy diagnostyczny `/day` | `prepare_layout:1582–1647` — doklejany do listy alertów **przed** budową WK (bramki: `is_volatile` + `n_om>=6` + `n_yr>=3` + horyzont 1…`MAX_VOLATILITY_DAY_HORIZON` (domyślnie 3) + różnica ≥2°) |
| WK | `worth_knowing.build_worth_knowing` (`:1768`), kandydaci L1/L2/future, `enforce_priority_policy` (`:1744`), kandydat księżycowy `_build_moon_night_candidate` (`:606`) poza rankingiem; wywołanie z `prepare_layout:1802` |
| WK a coast | flaga `coastal_day_alert` (martwa, §1.9) |

**Testy istniejące:** `test_worth_knowing_priority.py`, `test_worth_knowing_validators.py`,
`test_worth_knowing_moon.py`, `test_worth_knowing_future_hours.py`,
`test_info_tips_audit.py`; brak testów samego alertu weekendowego.

---

## 2. Przypisanie do warstw z dokumentu decyzji

| Warstwa | Elementy dziś (pliki/funkcje) | Dopasowanie |
|---|---|---|
| 1. Canonical weather values | `weather_payload` fetch + fuzja + `_select_hours`; filtr `*_eff` w `main_card` | częściowe — filtr `*_eff` żyje poza builderem payloadu |
| 2. Provenance | `*_source`, `forecast_source`, `model_updated_at_local`, `DailyPick.source/marker` | pola istnieją; wynik wyboru dnia nie ląduje jawnie w layoucie |
| 3. Nowcast | `owm_nowcast` + zastosowanie w `prepare_now_layout`/`prepare_layout` | klasyfikacja istnieje i jest czysta; zastosowanie miesza się z layoutem |
| 4. Coast | `coast_detector`, `coast_runtime`, konsumenci w dwóch layoutach | detekcja wydzielona; decyzje o alertach wewnątrz layoutów; zduplikowany setup w `main_card` |
| 5. Model comparison diagnostics | `build_daily_diagnostics` (legacy), `daily_diagnostics_matched` (shadow) | matched spełnia kontrakt; legacy zamrożony; brak konsumentów matched — zgodnie z planem |
| 6. Consumer policy | **nie istnieje jako miejsce** — decyzje rozproszone: `daily_source` (próg 18), bramki w `prepare_future_layout` i `prepare_layout`, `confidence_gate`, bramki OWM w `owm_nowcast` | główny brak etapu 1: najpierw rejestr i nazwy decyzji, bez przenoszenia kodu |
| 7. Rendering and alerts | layouty, `image_generator`, `_generate_alerts`, WK, Moon | layouty liczą część polityki (warstwa 6); renderer dostaje martwy `diag_tag` (a raczej go nie dostaje) |

---

## 3. Miejsca, w których warstwy się dziś mieszają

1. **`build_payload_for_location` robi wszystko naraz** (warstwy 1, 2, 3-fetch, 5 i 7):
   buduje wartości kanoniczne, provenance, diagnostykę legacy (z zapisem pliku
   `audit_spread.jsonl`), diagnostykę matched (shadow), alerty pogodowe i pobranie OWM.
2. **`daily_source` czyta legacy diagnostykę zamiast provenance/coverage**: wybór
   źródła dnia (decyzja) zależy od `n_yr` liczonego z pól kanonicznych po fuzji —
   stąd Hel na +2 dostaje OM przy 15 rekordach Yr.no (raport końcowy).
3. **`prepare_future_layout` zawiera politykę volatility wewnątrz buildera layoutu**
   (bramki `n_om/n_yr`, „dzień spokojny", próg 2°) i produkuje `diag_tag`, który
   następnie ginie przy budowie `day_entry` — decyzja bez konsumenta.
4. **`prepare_layout` jest jednocześnie layoutem i polityką**: trust (`compute_trust_report`),
   weekendowy alert diagnostyczny, alerty coast, leniwe bramkowanie kosztowe OWM,
   snapshot OWM oraz orchestracja WK w jednej funkcji (~850 linii).
5. **`prepare_now_layout` stosuje nowcast mutując rekord godziny** (`h0["_cld_override"]`),
   a reguła wpływu korekty na Hero/bloki jest rozproszona po layoutcie.
6. **Renderer rozstrzyga kolizję diagnostyka ↔ astronomia**: warunek
   `has_moon and not diag_tag` w `_plan_days_card_layout` oznacza, że ewentualny
   przyszły tag diagnostyczny konkurowałby o ten sam kanał co ikony Księżyca (P6).
7. **Dwie (trzy) semantyki „volatility"** o tej samej nazwie (§1.5) — ryzyko, że
   przyszły konsument sięgnie po niewłaściwą flagę.
8. **`confidence_gate` paruje modele po gołym `time_local`** bez offsetu/strefy
   (inaczej niż matched) i czyta pola kanoniczne oraz pola `*_yr` jednocześnie.
9. **Martwe ścieżki maskują obraz całości**: `apply_cloud_correction` (z błędnym
   odesłaniem w docstringu `nowcast_note`), flaga `coastal_day_alert` w WK,
   `DailyPick.marker`, `summary["diag_tag"]`. Audyt wykazał, że właśnie one
   utrudniały ocenę, co naprawdę działa.
10. **Setup coast zduplikowany** (`coast_runtime` vs `main_card:51–68`) — dwa miejsca
    prawdy o store i indeksie.

---

## 4. Tabela przekrojowa: odpowiedzialności, ryzyko, testy

Ryzyko = ryzyko przypadkowej zmiany zachowania przy porządkowaniu w etapach 1–2
(nie przy przełączaniu konsumentów — to etap 5).

| Plik → funkcja | Obecna odpowiedzialność | Docelowa warstwa | Ryzyko | Istniejące testy | Brakujące testy |
|---|---|---|---|---|---|
| `weather_payload._fetch_openmeteo/_fetch_yrno` | pobranie i wstępne ukształtowanie rekordów, provenance surowa | 1+2 | średnie | `test_weather_payload_raw_sources` | granice braku danych per pole; zachowanie przy częściowych odpowiedziach API |
| `weather_payload` fuzja (`build_payload_for_location`) | kanoniczne wartości dnia/godzin | 1 | **wysokie** | fuzja i provenance (raw sources) | inwariant: komplet pól kanonicznych po fuzji dla fixtures tygodniowych |
| `weather_payload._add_*_raw_and_provenance` | surowe `*_om/*_yr`, `*_source` | 2 | niskie | raw sources | kompletność mapowania `_YR_RAW_ON_OM` (każde pole ma odpowiednik albo świadomy brak, np. UV clear-sky) |
| `weather_payload.build_daily_diagnostics` | legacy `daily_diag` + zapis `audit_spread.jsonl` | 5 (zamrożone) | średnie | pośrednie (kształt w suite matched) | jednostkowe: ekstrema, brak modelu, `spread=0` przy braku par jako zachowanie legacy **udokumentowane**, flaga `ENABLE_AUDIT_LOG` |
| `daily_diagnostics_matched.build_daily_diagnostics_matched` | matched comparison + coverage | 5 | niskie | 21 testów (dopasowanie, DST, statusy, inwarianty) | fixtures z prawdziwego tygodnia (regresja na próbkach 2026-11); test `coverage_status_counts` sumuje się do liczby dni |
| `daily_source.pick_hours_for_daily_summary` | wybór źródła dnia | 6 (decyzja) na danych 2/5 | **wysokie** | brak dedykowanych | granice: `n_yr` 17/18, `len(hy_day)` 5/6, fallback OM, fallback `mixed`, marker |
| `confidence_gate.compute_trust_report` | godzinowy rozjazd modeli → łagodzenie UI | 6 | średnie | tylko inwariantność vs matched | progi opad/wiatr/chmury, minimum 3 par, okno 06–22, brak par → pusto |
| `weather_payload._generate_alerts` | alerty 36 h | 7 (produkcja alertów — docelowo za polityką 6) | średnie | pośrednie (karty/WK) | progi wiatru/upału per poziom; format czasu dziś/jutro/pojutrze |
| `owm_nowcast.classify_owm_cloud_correction` | decyzja korekty chmur bieżącej godziny | 3 (wynik) / 6 (zastosowanie) | **wysokie** | **brak** | pełna macierz: stale/no_clouds, high-only, diff 24/25/26, label równa/różna, cap 65%, noc, UV; `reason` per gałąź |
| `owm_nowcast.nowcast_note` | notka o ukrytym opadzie | 3 | średnie | **brak** | progi 0,1/0,2 mm, brak godziny modelu, nieswieży snapshot |
| `owm_nowcast.owm_is_fresh/extract_*` | świeżość i normalizacja odpowiedzi | 3 | niskie | **brak** | dt w sekundach vs milisekundach, wariant `data[0]`, brak `dt` |
| `owm_nowcast.find_model_hour_for_now` | dopasowanie godziny modelu bez fallbacku | 3 | niskie | **brak** | naive vs aware, brak dopasowania → None |
| `owm_nowcast.apply_cloud_correction` | martwy kod | — (kandydat do usunięcia po regresji) | niskie | brak (i brak wywołań) | najpierw dowód braku dynamicznych wywołań; po usunięciu — test importu modułu |
| `main_card` filtr `*_eff` | reguła kanoniczna opadów widmowych | 1 | **wysokie** | pośrednie (karty, bramka OWM) | progi 0,2 mm / POP / RH / chmury niskie na fixture godzinowym; niemutowanie pozostałych pól |
| `prepare_layout` alert weekendowy | polityka alertu diagnostycznego | 6→7 | **wysokie** | brak | bramki `n_om/n_yr`, horyzont 1…3, różnica <2°, flaga środowiskowa, wybór max/min wg `pick.source` |
| `prepare_future_layout` tag volatility | polityka tagu (martwa) | 6→7 | średnie | brak | test utrwalający stan obecny: brak `diag_tag` w `day_entry` (zachowanie udokumentowane, nie poprawiane po cichu) |
| `prepare_future_layout` Moon + `day_entry` | astronomia w wierszu dnia | 7 | niskie | `test_trend_moon_icons`, `test_worth_knowing_moon` | — (pokryte) |
| `prepare_layout`/`prepare_now_layout` coast | alerty/note od morza | 4→6→7 | średnie | `test_coast`, `test_day_beach` | brak wpisu cache przy istniejącym wpisie ≠ błąd; `/future` bez coast |
| `worth_knowing.build_worth_knowing` | wybór treści WK | 7 | średnie | priority/validators/moon/future_hours | flaga `coastal_day_alert` jako martwa (test dokumentujący albo usunięcie po decyzji) |
| `image_generator._plan_days_card_layout` | kolizja tag/Moon, severity | 7 | średnie | `test_trend_moon_icons` | zachowanie przy samym `diag_tag` (gdyby doszedł), przy obu naraz — jako specyfikacja przed decyzją UX |
| `sanity_checker.run_sanity_check` | kontrole po layoucie (tylko log) | 7 (obserwacja) | niskie | pośrednie | jawna lista anomalii, które **nie** blokują wysyłki |

---

## 5. Minimalne kontrakty danych (propozycja do bramki A)

Kontrakty opisują **kształt i semantykę**, nie progi. Progi pozostają decyzjami
D-SHADOW (etap 5) i nie są tu zatwierdzane.

### 5.1 Coverage (per dzień)

```
date: str (YYYY-MM-DD, lokalna)
coverage_status: enum {no_om_records, no_yr_records, no_common_hours,
                       no_matched_temperature, partial_coverage, matched}
n_om_records, n_yr_records: int          # rekordy źródłowe
n_om_timestamps, n_yr_timestamps: int    # unikalne timestampy
n_common_timestamps: int
matched_temperature_count: int           # pary z poprawnymi temperaturami
n_conflicting_om_values, n_conflicting_yr_values: int
timestamp_key_mode: enum {local_timestamp_with_offset, naive_local_timestamp,
                          no_timestamps, mixed}   # na poziomie całego wyniku
coverage_threshold: null                 # do decyzji D-SHADOW; dziś bez znaczenia
observation_only: true                   # do osobnej decyzji per konsument
```
Semantyka: status liczony z timestampów, nie z liczby rekordów; brak par nigdy
nie jest zerowym spreadem (patrz 5.2).

### 5.2 Matched comparison (per dzień, wewnątrz wyniku z 5.1)

```
version: "matched_v1"
unit: "C"
timestamp_matching: "exact_local_timestamp_with_offset_or_utc_instant"
max_abs_spread_c: float | null           # null, gdy matched_temperature_count == 0
mean_abs_spread_c: float | null
max_abs_spread_at_local: str | null
```
Źródło wartości: wyłącznie surowe `temp_c_om`/`temp_c_yr` (albo rekordy
`source="yrno"` przed fuzją). **Zakaz rekonstruowania wartości OM z pól
kanonicznych** — już pilnowany testem
`test_fused_row_without_raw_om_field_never_reconstructs_om_from_canonical`.

### 5.3 Provenance (rekord godziny i dzień)

```
hour.source: "openmeteo" | "yrno"                  # rekord przed fuzją
hour.<pole>_source: "openmeteo" | "yrno"           # per pole po fuzji
hour.temp_c_om / temp_c_yr / ...: float | null     # surowe, null = brak (nie 0)
hour.uv_index_semantics: "standard" | "clear_sky"
payload.forecast_source: str
payload.model_updated_at_local: str | null
day_pick.source: "yrno" | "openmeteo" | "mixed"    # docelowo jawne pole layoutu
```
Niezmiennik: po fuzji żaden wniosek o modelu nie wolno wysnuwać z samego
`hour.source`; konsument czyta provenance per pole/dzień. Widoczność `day_pick.source`
na karcie to osobna decyzja UX (nie etap 1).

### 5.4 Nowcast (wynik klasyfikacji, w pamięci layoutu)

```
fresh: bool
reason: enum {no_owm, stale_owm, no_clouds, ok, ...}
model_clouds, live_clouds, effective_live_clouds: float | null
label_model, label_live, icon_live: str | null
should_override_hour0: bool        # tylko bieżąca godzina, tylko /now
should_show_snapshot: bool         # linia „Teraz: …" na /day
high_only_gate: bool
note_key: str | null               # notka o ukrytym opadzie przez nowcast_note
```
Niezmienniki: nowcast nigdy nie zapisuje do godzin kanonicznych ani payloadu
poza surowym `payload["owm_current"]`; nieswieży snapshot = brak korekty;
nie dotyczy `/future`.

### 5.5 Coast (sygnatura i zdarzenie)

```
signature.key: "coast:<lat>:<lon>"  (+ wersja sygnatury)
signature.is_coastal: bool
signature.distance_to_ocean_km: float | null
signature.sea_sectors: list[(start_deg, end_deg)]
event.mode: "marine_storm" | "beach" | null
event: {wind/gust wartości, godziny wystąpienia, sezon, timezone}
geo_stack_available: bool           # brak stosu geo = brak zdarzeń, z ostrzeżeniem w logu
```
Niezmienniki: coast nigdy nie modyfikuje wartości pogodowych; globalny cache
(`coast:lat:lon`) nie jest daną jednej lokalizacji; brak wpisu cache przy
istniejącym wpisie nie jest błędem.

---

## 6. Kolejność prac etapu 1 (bez zmiany zachowania)

1. **Specyfikacja kontraktu** (ten dokument §5) → akceptacja = bramka A.
2. **Rejestr konsumentów**: dla każdego sygnału (`daily_diag`, matched, trust,
   nowcast, coast, pick źródła dnia) lista: producent, konsumenci, flaga
   środowiskowa, test strażniczy. Uzupełnienie §1 tego planu o brakujące wpisy
   po przeglądzie `location_bot.py` (ścieżki komend poza `main_card`).
3. **Testy charakteryzujące** pod etap 2 (kolejność wg §4, najpierw OWM i
   `daily_source`, bo tam ryzyko/luki są największe).
4. **Dokumentacja martwych ścieżek** jako zachowania udokumentowanego
   (`diag_tag`, `apply_cloud_correction`, `coastal_day_alert`, `DailyPick.marker`)
   — bez fizycznego czyszczenia przed regresją.
5. Odtworzenie brakującego wykazu zadań §6 dokumentu decyzji na podstawie §5
   dokumentu decyzji i tego planu (decyzja właściciela, czy jako errata w repo).

---

## 7. Raport końcowy — cztery kategorie

### 7.1 Można wdrożyć bez zmiany zachowania

- Specyfikacja kontraktów §5 i rejestr konsumentów (dokumentacja; bramka A).
- Testy jednostkowe OWM (`classify_owm_cloud_correction`, `nowcast_note`,
  `owm_is_fresh`, `find_model_hour_for_now`) na fixture'ach — zamrożenie stanu
  przed etapem 3.
- Testy `daily_source.pick_hours_for_daily_summary` (granice 17/18 i 5/6) oraz
  `compute_trust_report` — charakterystyka przed jakimkolwiek porządkowaniem warstwy 6.
- Testy utrwalające martwe ścieżki jako zachowanie udokumentowane (brak `diag_tag`
  w `day_entry`, brak konsumentów matched — strażnik już istnieje).
- Rozszerzenie strażników z `test_daily_diagnostics_matched.py` o fixtures tygodnia
  2026-11 (regresja matched na realnych kształtach payloadów).
- Jednoznaczne nazewnictwo w dokumentacji: trzy semantyki „volatility" (§1.5) —
  bez zmiany nazw w kodzie na tym etapie.

### 7.2 Wymaga shadow mode

- Odpowiednik `is_volatile` liczony na matched + bramka pokrycia
  (`coverage_status`/`matched_temperature_count`) — najpierw wyłącznie offline,
  z dystrybucjami per status, horyzont i lokalizacja (dane z raportu końcowego:
  mediana par +3…+14 to 4 lub 0).
- Kandydat tagu diagnostycznego `/future` i weekendowy alert `/day` na matched —
  porównanie legacy vs kandydat na tych samych payloadach, bez renderowania.
- Ewentualna zmiana wejścia `daily_source` z `n_yr` legacy na liczniki timestampów
  — wyłącznie jako obliczenie równoległe do porównania wyników wyboru.

### 7.3 Wymaga osobnej decyzji UX

- Czy i jak pokazywać źródło dnia na `/future` (dziś marker ` *` nie dociera do karty).
- Jakikolwiek tag/oznaczenie diagnostyczne na karcie oraz jego kanał wizualny
  względem ikon Księżyca (kolizja w `_plan_days_card_layout`).
- Treść i zrozumiałość oznaczeń typu „⚠️ Możliwe: 31° dzień" — dopiero na
  prototypach kart, z dystrybucjami z shadow mode.

### 7.4 Nie ruszać przed regresją

- Przełączenie **jakiegokolwiek** konsumenta na `daily_diag_matched`
  (`daily_source`, layouty, WK, alerty) — `observation_only=True` zostaje.
- Progi i semantyka: `VOLATILITY_SPREAD_C`/`spread_min≥4`, bramki
  `confidence_gate`, próg `MIN_YR_SAMPLES_FOR_DAILY_SUMMARY=18`, bramki OWM
  (≥25 pkt, high-only, cap 65%, świeżość 90 min), progi coast marine/beach,
  filtr `*_eff` ≤0,2 mm, reguły fuzji i provenance.
- Alert weekendowy `/day` i cała ścieżka `ENABLE_VOLATILITY_UI`.
- Fizyczne usuwanie martwego kodu (`apply_cloud_correction`, flaga
  `coastal_day_alert`) — dopiero po teście regresyjnym i dowodzie braku
  dynamicznych wywołań.
- Zapis `audit_spread.jsonl` — pozostaje jedynym źródłem historii legacy do czasu
  osobnej decyzji o jego losie.

---

*Plan przygotowany read-only na podstawie kodu repozytorium i raportów audytu.
Żaden plik repozytorium nie został zmodyfikowany na potrzeby tego dokumentu;
archiwizacja audytu w `docs/audit/2026-11-weekly/` (commity `314cdf0`, `61c92ba`)
jest od tego planu niezależna.*
