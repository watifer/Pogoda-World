# Rejestr etapu 1 — kontrakt danych, semantyka coverage, rejestr konsumentów

> **Charakter dokumentu: NOWY.** Niniejszy rejestr został utworzony w ramach etapu 1
> prac architektonicznych (2026-11-17) i **nie jest** odzyskaną ani odtworzoną kopią
> brakującego wykazu zadań Z1.1–Z5.3 z §6 dokumentu decyzji (patrz
> `brak_pełnej_wersji_dokumentu.md` w tym katalogu). Dokument powstał na podstawie
> raportu końcowego (źródło prawdy o wynikach audytu), §2–§5 dokumentu decyzji,
> planu etapu 1 oraz ponownego, read-only rozpoznania kodu.
>
> **Zakres zmian w repozytorium:** wyłącznie dokumentacja. Ten etap nie zmienił
> kodu aplikacji, testów, konfiguracji, payloadów produkcyjnych, kart ani alertów.

| Pole | Wartość |
|---|---|
| Status etapu 1 | **ZAAKCEPTOWANY — bramka A przekroczona decyzją właściciela 2026-11-17**; akceptacja dotyczy wyłącznie treści tego dokumentu i nie jest zgodą na etap 2 ani na jakiekolwiek zmiany zachowania produkcyjnego |
| Data sporządzenia | 2026-11-17 |
| Gałąź robocza | `arena/83356822-pogoda-world` (HEAD `33f0662` — kod identyczny ze stanem analizy audytu) |
| Punkt referencyjny audytu | tag `audit-2026-11-weekly-final` → commit `1654e0e` |
| Decyzje progowe | **Żadnych progów nie zatwierdzono tym dokumentem** |

## Oświadczenia wiążące (stan zamrożony)

Niniejszy dokument jednoznacznie stwierdza, że na koniec etapu 1:

- **`daily_diag_matched` pozostaje `observation_only=True`** — żaden konsument
  produkcyjny go nie czyta i nie został przełączony;
- **`is_volatile` pozostaje bez zmian** (wszystkie trzy znaczenia, patrz §4.2);
- **WK (worth knowing) pozostaje bez zmian**;
- **alerty pozostają bez zmian** (w tym alert weekendowy `/day` i alerty coast);
- **progi pozostają bez zmian** (m.in. `VOLATILITY_SPREAD_C=5,0`, `spread_min≥4,0`,
  `MIN_YR_SAMPLES_FOR_DAILY_SUMMARY=18`, bramki OWM, progi coast, filtr `*_eff`);
- **`daily_source` pozostaje bez zmian** (reguła wyboru źródła dnia nietknięta);
- **OWM pozostaje bez zmian** (bramki korekty, świeżość, zakres zastosowania);
- **coast pozostaje bez zmian** (reguły marine/beach, cache, setup).

---

## 1. Status etapu

Etap 1 obejmuje wyłącznie: (1) kontrakt danych, (2) semantykę `coverage_status`,
(3) rejestr konsumentów, (4) mapowanie odpowiedzialności między siedmioma
warstwami, (5) przygotowanie bramki A. Żaden konsument nie jest przełączany;
żaden nowy sygnał nie powstaje; testy charakteryzujące należą do etapu 2
(po akceptacji bramki A).

**Stan na koniec tej sesji:** dokument kompletny, oczekuje na akceptację
właściciela. Po akceptacji: wyłącznie commit dokumentacyjny na bieżącej gałęzi.
Etap 2 nie rozpocznie się bez osobnej akceptacji.

## 2. Podstawa audytu

| Element | Wartość |
|---|---|
| Źródło prawdy o wynikach | `raport_koncowy.md` (audyt 2026-11-09…17: 114 payloadów = 1710 payload-dni, `audit_spread.jsonl` 7955 linii / 5100 dopasowanych, 110 wpisów coast) |
| Decyzje wiążące | `dokument_decyzji_architektonicznej.md` §2 (D-TERAZ — zaakceptowane), §3 (D-SHADOW — odroczone), §4 (7 warstw), §5 (etapy i bramki) |
| Plan realizacyjny | `plan_etapu1.md` (inwentaryzacja read-only kodu) |
| Stan kodu | HEAD `33f0662` — identyczny ze stanem w momencie analizy audytu; rozpoznanie w tej sesji potwierdziło zgodność odnośników `plik:linia` z planem etapu 1 |
| Braki | §6 dokumentu decyzji nieodtwarzalne — nie odtwarzano (nota archiwalna); niniejszy rejestr jest dokumentem nowym |

## 3. Kontrakty danych

Kontrakty opisują **kształt i semantykę**, nie progi. Progi pozostają decyzjami
D-SHADOW (etap 5) i nie są tu zatwierdzane.

### 3.1 Coverage (per dzień, `daily_diag_matched["days"][date]`)

| Pole | Typ | Semantyka |
|---|---|---|
| `coverage_status` | enum (6) | status pokrycia liczony **z timestampów, nie z liczby rekordów** (`daily_diagnostics_matched._coverage_status`, `:191`) |
| `n_om_records` | int | rekordy źródłowe Open-Meteo tego dnia |
| `n_yr_records` | int | rekordy źródłowe `source="yrno"` tego dnia |
| `n_om_timestamps` | int | unikalne timestampy Open-Meteo |
| `n_yr_timestamps` | int | unikalne timestampy Yr.no — wlicza wartości Yr osadzone na rekordach OM jako `temp_c_yr`, nawet gdy osobnego rekordu `source="yrno"` nie ma |
| `n_common_timestamps` | int | timestampy wspólne obu modeli (pełny lokalny timestamp z offsetem albo jednoznaczna chwila UTC) |
| `matched_temperature_count` | int | pary wspólnych timestampów z poprawnymi temperaturami **obu** modeli |
| `n_conflicting_om_values` / `n_conflicting_yr_values` | int | konflikty surowych wartości na tym samym timestampie (telemetria) |

**Porządek statusów** (pierwszy spełniony warunek wygrywa):

| Status | Warunek | Znaczenie |
|---|---|---|
| `no_om_records` | `n_om_records == 0` lub `n_om_timestamps == 0` | **brak danych** Open-Meteo |
| `no_yr_records` | `n_yr_timestamps == 0` | **brak danych** Yr.no (w tygodniu: 546 payload-dni) |
| `no_common_hours` | `n_common_timestamps == 0` | oba źródła mają dane, lecz **zero wspólnych godzin** — porównanie niemożliwe |
| `no_matched_temperature` | `matched_temperature_count == 0` przy wspólnych godzinach | wspólne godziny istnieją, ale żadna nie ma poprawnej pary temperatur — **to nie jest zgodność modeli** |
| `partial_coverage` | `matched_temperature_count < n_common_timestamps` lub `n_om_timestamps != n_yr_timestamps` | **częściowe pokrycie** (w tygodniu: 998 payload-dni) |
| `matched` | wszystkie wspólne timestampy mają poprawne pary i liczby timestampów obu źródeł są równe | **pełne dopasowanie** (w tygodniu: 166 payload-dni) |

**Znaczenie `None`:** `max_abs_spread_c`, `mean_abs_spread_c` i
`max_abs_spread_at_local` są `None`, gdy `matched_temperature_count == 0`.
`None` oznacza „nie obliczono, bo nie ma par" — **nigdy zero**.

**Rozróżnienie przesądzone:** brak danych (statusy `no_*`) ≠ częściowe pokrycie
(`partial_coverage`) ≠ pełne dopasowanie (`matched`).

**Charakter statusów:** wyłącznie opisowy (telemetria/raport), nigdy decyzyjny
(D-TERAZ-3). `coverage_threshold` jest `None` i pozostaje `None` do decyzji
D-SHADOW-5.

> **Niezmiennik:** *brak par nigdy nie oznacza spread=0; brak danych nie może
> być interpretowany jako zgodność modeli.*

### 3.2 Matched comparison (`daily_diag_matched`)

| Pole (poziom wyniku) | Wartość |
|---|---|
| `version` | `"matched_v1"` (`DIAGNOSTICS_VERSION`) |
| `unit` | `"C"` |
| `timestamp_matching` | `"exact_local_timestamp_with_offset_or_utc_instant"` |
| `timestamp_key_mode` | `local_timestamp_with_offset` / `naive_local_timestamp` / `no_timestamps` / `mixed` |
| `coverage_threshold` | `null` — próg niezdefiniowany, bez znaczenia produkcyjnego |
| `observation_only` | **`true`** — do osobnej decyzji per konsument (etap 5) |
| `days` / `totals` / `coverage_status_counts` | wyniki per dzień, sumy, zliczenie statusów |

| Pole (per dzień) | Semantyka |
|---|---|
| `max_abs_spread_c` | float \| null — maksymalna bezwzględna różnica temperatury na parze; `null`, gdy `matched_temperature_count == 0` |
| `mean_abs_spread_c` | float \| null — średnia bezwzględna różnica; `null` jak wyżej |
| `max_abs_spread_at_local` | str \| null — timestamp maksimum |
| `coverage_status` | patrz §3.1 |
| `matched_temperature_count` | liczba par — podstawa wiarygodności spreadu |

**Źródło wartości:** wyłącznie surowe `temp_c_om` / `temp_c_yr` oraz rekordy
`source="yrno"` przed fuzją. **Zakaz rekonstruowania wartości OM z pól
kanonicznych** — pilnowany testem
`test_fused_row_without_raw_om_field_never_reconstructs_om_from_canonical`.

**Rozdzielenie (zamrożone):**

- `daily_diag` — **legacy, zamrożone** (D-TERAZ-1): ekstrema z pól kanonicznych
  po fuzji; zapis `audit_spread.jsonl` przy `ENABLE_AUDIT_LOG`; zachowane wyłącznie
  dla dotychczasowych konsumentów i odtworzenia historii. Znane wady metody
  (udokumentowane w raporcie końcowym): maskowanie spread=0 przez fuzję
  (954/5100 linii), volatility na 3–4 parach (67–72% przypadków), ekstrema bez
  wymogu wspólnych timestampów.
- `daily_diag_matched` — **referencyjna diagnostyka offline/shadow**:
  surowe pola, dopasowanie po pełnym timestampie, statusy pokrycia;
  bez konsumentów produkcyjnych (strażnik testowy).

**W etapie 1 nie wprowadza się** progów ani flagi matched `is_volatile`.

### 3.3 Provenance

| Element | Semantyka |
|---|---|
| `hour.source` (`openmeteo`/`yrno`) | pochodzenie **rekordu** przed fuzją |
| `hour.<pole>_source` (np. `temp_c_source`) | provenance **pola** po fuzji — rekord `source="openmeteo"` może mieć pola ze źródłem `yrno` |
| `hour.temp_c_om` / `temp_c_yr` / … | surowe wartości modeli; `null` = brak (nie 0) |
| `hour.uv_index_semantics` | `standard` / `clear_sky` |
| `payload.forecast_source` | provenance payloadu (np. `"OpenMeteo + Yr.no"`, `weather_payload.py:577–604`) |
| `payload.model_updated_at_local` | znacznik aktualizacji modelu (Yr.no) |
| `DailyPick.source` (`yrno`/`openmeteo`/`mixed`) + `DailyPick.marker` | wynik wyboru źródła dnia (`daily_source.py`); dziś nie trafia jawnie na kartę (marker ` *` ginie w `label`) |

**Niezmiennik:** po fuzji żaden wniosek o modelu nie wolno wysnuwać z samego
`hour.source`; konsument decyzyjny czyta provenance **per pole/dzień**, nie per
rekord. Rozróżnienie „provenance rekordu" i „provenance pola" jest wiążące
(D-TERAZ-4, D-TERAZ-9).

### 3.4 Nowcast OWM

Cztery rozdzielone semantyki (nie wolno ich mieszać):

1. **model forecast** — rekordy OM (`temp_c_om` itd.) i Yr.no (`temp_c_yr`,
   rekordy `source="yrno"`);
2. **fused canonical value** — kanoniczne pola rekordu po fuzji
   (`weather_payload.py:614–668`); to **nie** jest korekta satelitarna;
3. **OWM current observation** — punktowa obserwacja bieżąca z API
   OpenWeatherMap (`owm_nowcast.get_current_weather`, w payloadzie `owm_current`);
   **nie jest obrazem satelitarnym** ani radarowym;
4. **render value** — `_cld_override` wpisywany wyłącznie w pamięci do rekordu
   bieżącej godziny (`prepare_now_layout.py:188`) oraz linia snapshotu na `/day`.

**Obecna korekta OWM** (`classify_owm_cloud_correction`, `owm_nowcast.py:131`):

- dotyczy **bieżącego zachmurzenia** (chmury % bieżącej godziny) oraz — osobno —
  rzadkiej notki o ukrytym opadzie (`nowcast_note`, model <0,1 mm i OWM ≥0,2 mm);
- **może wpływać na `/now`** (Hero + pierwszy blok godzinowy);
- **może wpływać na linię snapshotu `/day`** („Teraz: …") oraz override bieżącej
  godziny bloków (`ENABLE_DAY_OWM_BLOCK_OVERRIDE`);
- **nie wpływa na `/future`** (kod `/future` nie importuje OWM);
- **nie jest tym samym co pola `*_eff`** — `*_eff` to filtr opadów widmowych
  (`main_card.py:551–601`, opady ≤0,2 mm przy suchych przesłankach → `trace`),
  stosowany względem wszystkich trybów, niezależnie od OWM.

Bramki korekty (zamrożone, D-TERAZ-8): świeżość OWM ≤ 90 min
(`OWM_FRESH_MAX_SEC`, domyślnie 5400 s); różnica efektywnych chmur ≥ 25 pkt proc.
**i** inna etykieta nieba; bramka high-only; przy `live ≥85%` i UV>1,2 za dnia
efektywne chmury obniżane do 65%. `apply_cloud_correction`
(`owm_nowcast.py:287`) to martwy kod — patrz §7.

### 3.5 Coast

Coast pozostaje **osobnym modułem kontekstowym** (D-TERAZ-6):

- **nie zmienia wartości pogodowych** — dostarcza sygnaturę geograficzną
  (`is_coastal`, `distance_to_ocean_km`, `sea_sectors`, wersja
  `ne_50m_ocean:50m;radar:v2;r25;step10;minw20;marine75g90;full360`) i reguły
  alertów wiatru od morza;
- **może wpływać na `/day`** (alerty `coast_marine_storm_day` / `coast_beach_day_*`,
  `prepare_layout.py:1492–1577`) **i na `/now`** (`coastal_note` w kaskadzie
  `context_line`, `prepare_now_layout.py:604–675,732`);
- **nie wpływa na `/future`** (moduł nie importuje coast);
- **`*_coast_cache_all.json` jest globalnym cache'em pomocniczym** — wpis
  lokalizacji `*_coast.json` **nie może być utożsamiany z całym cache'em**;
  brak aktualizacji pliku cache przy istniejącym wpisie nie jest błędem;
- brak stosu geo (`GEO_STACK_AVAILABLE=False`) = brak zdarzeń coast, z jednym
  ostrzeżeniem w logu na proces — nigdy błąd danych pogodowych.

## 4. Rejestr konsumentów

### 4.1 Tabela sygnałów

Legenda statusu: **legacy** = zamrożony, tylko dotychczasowi konsumenci;
**shadow** = liczony, bez konsumentów produkcyjnych; **aktywny** = wpływa dziś
na kartę/payload; **martwy** = produkowany lub czytany, ale bez efektu
end-to-end (D-TERAZ-7: nie używać, nie usuwać przed testami regresyjnymi).

| Sygnał | Producent | Plik / funkcja | Aktualni konsumenci | Flaga środowiskowa | Wpływ na payload | Wpływ na kartę | Wpływ na alert/WK | Istniejące testy | Brakujące testy | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| `daily_diag` | `weather_payload` | `build_daily_diagnostics` (`weather_payload.py:38`); zapis `audit_spread.jsonl` (`:90–104`) | `daily_source.pick_hours_for_daily_summary` (przez `n_yr`); `prepare_layout` (`:1262`, `:1583`); `prepare_future_layout` (`:48`, `:61`) | `ENABLE_AUDIT_LOG`, `VOLATILITY_SPREAD_C` | tak — klucz `daily_diag` | pośredni (źródło dnia, alert weekendowy) | alert weekendowy `/day` (0/114 w tygodniu) | kształt zamrożony w `test_daily_diagnostics_matched.py`; `test_weather_payload_raw_sources.py:253` | jednostkowe `build_daily_diagnostics` (ekstrema, brak modelu → `spread=0` jako zachowanie legacy, flaga audytu) | **legacy** |
| `daily_diag_matched` | `daily_diagnostics_matched` | `build_daily_diagnostics_matched` (`:214`); podłączenie `weather_payload.py:692–705`, klucz `:733` | **brak** (strażnik: `test_consumers_do_not_reference_matched_diagnostics`) | brak (liczony zawsze, pod `try/except`) | tak — klucz `daily_diag_matched` (`observation_only`) | **brak** (dowód: layout identyczny dla 114/114 payloadów) | brak | 20 testów w `test_daily_diagnostics_matched.py` (dopasowanie, DST, statusy, inwarianty) | fixtures z tygodnia 2026-11; suma `coverage_status_counts` = liczba dni | **shadow** |
| `is_volatile` (a) — z `daily_diag` | `weather_payload` | `weather_payload.py:75` (`spread_max ≥ 5,0` lub `spread_min ≥ 4,0`) | alert weekendowy `/day` (`prepare_layout.py:1603`); tag `/future` (`prepare_future_layout.py:63` — dalej martwy) | `ENABLE_VOLATILITY_UI`, `VOLATILITY_SPREAD_C`, `MAX_VOLATILITY_DAY_HORIZON` | tak (wewnątrz `daily_diag`) | potencjalny, przez alert weekendowy | alert weekendowy doklejany przed budową WK | pośrednie (zamrożenie reguły w suite matched) | bramki `n_om≥6`/`n_yr≥3`, horyzont 1…3, różnica ≥2 °C, wybór max/min wg `pick.source` | **legacy/aktywny** (żywy tylko w alercie weekendowym) |
| `is_volatile` (b) — z `confidence_gate` | `confidence_gate` | `compute_trust_report` (`confidence_gate.py:107–108`: precip_hard/wind_hard/clouds_soft) | `prepare_layout` (`:1401` soften Hero, `:1461` notka zgodności, `:1473` age-note, `:1780` hide_block_details) | brak | nie | tak (`/day`: łagodzenie Hero, notki, ukrycie szczegółów bloków) | pośredni (context line) | tylko inwariantność vs matched (`test_daily_diagnostics_matched.py:452`) | jednostkowe `compute_trust_report` (progi opad/wiatr/chmury, min. 3 pary, okno 06–22) | **aktywny** |
| `is_volatile_precip` (c) — z `/now` | `prepare_now_layout` | `prepare_now_layout.py:231` (`prc_transitions > 1`) | logika Hero/bloków `/now` (`:282`, `:294`) | brak | nie | tak (`/now`) | nie | brak wydzielonych | testy przejść stanów opadowych w oknie 4 h | **aktywny** |
| `coverage_status` | `daily_diagnostics_matched` | `_coverage_status` (`daily_diagnostics_matched.py:191`) | **brak** | brak | tak (w `daily_diag_matched.days` + `coverage_status_counts`) | brak | brak | statusy w `test_daily_diagnostics_matched.py` | fixtures tygodnia; charakter opisowy pilnowany dokumentacyjnie | **shadow** |
| `daily_source` (`DailyPick`) | `daily_source` | `pick_hours_for_daily_summary` (`daily_source.py:16`; próg `MIN_YR_SAMPLES_FOR_DAILY_SUMMARY=18`) | `prepare_layout` (`:1269`, `:1612`, weekend `:1658–1659`); `prepare_future_layout` (`:55`) dla dni +1…+14 | brak env (próg w kodzie) | nie | tak (źródło summary dni `/day` i `/future`; marker ` *` niewidoczny — §7) | pośredni (alert weekendowy czyta `pick.source`) | brak dedykowanego pliku | granice `n_yr` 17/18, `len(hy_day)` 5/6, fallback OM, fallback `mixed`, marker | **aktywny** |
| OWM nowcast | `owm_nowcast` | `get_current_weather` (`:207`), `classify_owm_cloud_correction` (`:131`), `nowcast_note` (`:254`); snapshot `weather_payload._get_owm_cached` (`:220`, TTL 180 s) | `prepare_now_layout` (`:112–203`, `_cld_override` `:188`); `prepare_layout` (`:1679–1780`: linia snapshotu + `cld_overrides`) | `ENABLE_OWM_NOWCAST`, `OWM_FRESH_MAX_SEC`, `ENABLE_DAY_OWM_BLOCK_OVERRIDE` | tak — klucz `owm_current` (surowy snapshot) | tak (`/now`: Hero + 1. blok; `/day`: linia „Teraz: …" + override bieżącej godziny bloków) | notka o ukrytym opadzie może trafić do context line | pośrednie: `test_now_i18n.py::TestOwmReuse`, bramka kosztowa w `test_card_invariants.py`, wyłączenia w `conftest.py` | jednostkowe `classify_owm_cloud_correction`, `nowcast_note`, `owm_is_fresh`, `find_model_hour_for_now`; test z **aktywną** korektą | **aktywny** |
| coast | `coast_detector` + `coast_runtime` | `CoastIndex` (`coast_detector.py:252`), `compute_signature` (`:285`), `get_coastal_alert_mode` (`:499`); store `coast_cache.json` | `prepare_layout` (`:1492–1577`); `prepare_now_layout` (`:604–675`, `:732`); `dev_preview` | brak env; `GEO_STACK_AVAILABLE` (dostępność stosu geo), `COAST_SIG_VERSION` | nie | tak (`/day`: alerty marine/beach; `/now`: notka) | alerty coast doklejane do alertów `/day` | `test_coast.py` (47), `test_day_beach.py` (34) | „brak aktualizacji cache ≠ błąd"; weryfikacja na kartach lokalizacji nadmorskich | **aktywny** |
| `diag_tag` | `prepare_future_layout` | ustawiany na `summary` (`prepare_future_layout.py:104–105`) | **brak** — `day_entry` (`:108–117`) nie kopiuje pól; renderer czyta je z `next_days` (`image_generator.py:442`, `:678`) | `ENABLE_VOLATILITY_UI` (pośrednio, przez warunek `is_volatile`) | nie | **brak** (0/1596 dni w layoucie przy 15 spełniających bramki) | brak | brak | test utrwalający martwość ścieżki (etap 2) | **martwy** |
| `moon_phase` | `worth_knowing` | `get_moon_phase_for_local_date` (`worth_knowing.py:467–524`, algorytm Meeusa); dokłada `prepare_future_layout.py:119–126` | `image_generator` (`:452–456` wiersz dnia, hero moon line) | brak | nie | tak (ikona `wk_full_moon`/`wk_new_moon` na `/future` i w hero) | kandydat księżycowy WK (`_build_moon_night_candidate`, `:606`) poza rankingiem | `test_trend_moon_icons.py` (10), `test_worth_knowing_moon.py` | — (pokryte) | **aktywny** |
| `coastal_day_alert` | **brak** (nikt nie zapisuje) | czytane w `worth_knowing.py:1840` → `_candidates_level1` (parametr domyślny `False`, `:1056`) | WK (formalnie; zawsze `False`) | brak | nie (pole nigdy nie występuje w payloadzie) | brak | brak | brak | test dokumentujący martwość (etap 2) | **martwy** |
| `forecast_source` | `weather_payload` | `weather_payload.py:577–604` | stopka renderera (`image_generator.py:901`, `:1202`); `prepare_layout` (`:1478–1479`, bramka kosztowa OWM `:1681–1684`); `prepare_now_layout` (`:113`, `:589`) | brak | tak | tak (stopka „Źródło: …") | pośredni (bramka kosztowa OWM) | pośrednie (karty) | dedykowane | **aktywny** |
| `model_updated_at_local` | `weather_payload` | `weather_payload.py` (z `model_updated` Yr.no) | `prepare_layout:1101`; `prepare_now_layout:568`; `prepare_future_layout:178` (notki o wieku modelu) | brak | tak | tak (notka o świeżości modelu) | pośredni | pośrednie | dedykowane | **aktywny** |

### 4.2 Trzy znaczenia `is_volatile` — nie mieszać

| Znaczenie | Definicja | Konsument | Powiązanie z diagnostyką modeli |
|---|---|---|---|
| (a) legacy `daily_diag[date]["is_volatile"]` | dzienne ekstrema spread (`weather_payload.py:75`) | alert weekendowy `/day`; martwy tag `/future` | tak — to sygnał diagnostyki legacy |
| (b) `confidence_gate.TrustReport.is_volatile` | godzinowy rozjazd opadów/wiatru/chmur (`confidence_gate.py:107`) | `prepare_layout` (łagodzenie UI) | nie — inna semantyka (opady/wiatr/chmury, nie temperatura) |
| (c) `is_volatile_precip` (`/now`) | przejścia stanów opadowych w oknie 4 h (`prepare_now_layout.py:231`) | logika Hero/bloków `/now` | nie — lokalna zmienność opadów |

Mieszanie tych znaczeń jest zabronione; przyszły odpowiednik volatility na
matched (jeśli powstanie) otrzyma własną nazwę decyzji w warstwie 6
(D-SHADOW-1), nie nazwę `is_volatile`.

## 5. Mapowanie siedmiu warstw

Zasada przewodnia (§4 dokumentu decyzji): **każda warstwa wyżej widzi tylko
jawne wyjścia warstwy niżej, nigdy jej surowe wnętrze.**

| # | Warstwa | Elementy dziś (pliki/funkcje) | Dopasowanie stanu obecnego |
|---|---|---|---|
| 1 | Canonical weather values | `weather_payload.build_payload_for_location` (fetch + fuzja + `_select_hours`); filtr `*_eff` w `main_card._send_card_to_user` (`:544–601`) | częściowe — filtr `*_eff` żyje poza builderem payloadu |
| 2 | Provenance | `*_source`, `temp_c_om/temp_c_yr`, `uv_index_semantics`, `forecast_source`, `model_updated_at_local`, `DailyPick.source/marker` | pola istnieją; wynik wyboru dnia nie ląduje jawnie w layoucie (marker ginie w `label`) |
| 3 | Nowcast | `owm_nowcast` (klasyfikacja czysta) + zastosowanie w `prepare_now_layout` / `prepare_layout` | klasyfikacja wydzielona; zastosowanie miesza się z layoutem (mutacja `h0["_cld_override"]`) |
| 4 | Coast | `coast_detector`, `coast_runtime`, konsumenci w `prepare_layout` i `prepare_now_layout` | detekcja wydzielona; decyzje o alertach wewnątrz layoutów; zduplikowany setup w `main_card.py:51–68` |
| 5 | Model comparison diagnostics | `build_daily_diagnostics` (legacy, zamrożone) ‖ `daily_diagnostics_matched` (shadow) | matched spełnia kontrakt; brak konsumentów matched — zgodnie z decyzją; legacy zachowane do historii |
| 6 | Consumer policy | **nie istnieje jako miejsce** — decyzje rozproszone: `daily_source` (próg 18), bramki w `prepare_future_layout` i `prepare_layout`, `confidence_gate`, bramki OWM w `owm_nowcast` | główny brak: najpierw rejestr i nazwy decyzji (niniejszy dokument), bez przenoszenia kodu |
| 7 | Rendering and alerts | `prepare_layout`, `prepare_now_layout`, `prepare_future_layout`, `image_generator`, `_generate_alerts`, WK, Moon | layouty liczą część polityki; renderer nigdy nie dostaje `diag_tag` z `/future`; kanał ikon Księżyca zarezerwowany (D-TERAZ-5) |

## 6. Miejsca mieszania odpowiedzialności (udokumentowane, bez zmian)

1. `build_payload_for_location` robi wszystko naraz (warstwy 1, 2, 3-fetch, 5 i 7):
   wartości kanoniczne, provenance, diagnostykę legacy z zapisem
   `audit_spread.jsonl`, diagnostykę matched, alerty pogodowe i pobranie OWM.
2. `daily_source` czyta legacy `daily_diag` (`n_yr`) zamiast danych
   provenance/coverage — decyzja źródła dnia zależy od metody z udokumentowanym
   maskowaniem (skutek: Hel na +2 zawsze Open-Meteo przy 15 rekordach Yr.no).
3. `prepare_future_layout` zawiera politykę volatility wewnątrz buildera layoutu
   (bramki `n_om/n_yr`, „dzień spokojny", próg 2 °C) i produkuje `diag_tag`,
   który ginie przy budowie `day_entry` — decyzja bez konsumenta.
4. `prepare_layout` jest jednocześnie layoutem i polityką: trust
   (`compute_trust_report`), weekendowy alert diagnostyczny, alerty coast,
   leniwa bramka kosztowa OWM, snapshot OWM i orkiestracja WK w jednej funkcji.
5. `prepare_now_layout` stosuje nowcast mutując rekord godziny
   (`h0["_cld_override"]`), a reguła wpływu korekty na Hero/bloki jest
   rozproszona po layoutcie.
6. Renderer rozstrzygałby kolizję diagnostyka ↔ astronomia
   (`has_moon and not diag_tag`, `image_generator.py:460–463`) — ewentualny
   przyszły tag diagnostyczny konkurowałby o kanał zarezerwowany dla faz
   Księżyca (P6, D-TERAZ-5).
7. Trzy semantyki „volatility" o tej samej nazwie (§4.2) — ryzyko sięgnięcia po
   niewłaściwą flagę.
8. `confidence_gate` paruje modele po gołym `time_local` (bez offsetu/strefy —
   inaczej niż matched) i czyta pola kanoniczne oraz `*_yr` jednocześnie.
9. Martwe ścieżki maskują obraz całości (§7) — audyt wykazał, że utrudniały
   ocenę, co naprawdę działa.
10. Setup coast zduplikowany (`coast_runtime` vs `main_card.py:51–68`) — dwa
    miejsca prawdy o store i indeksie.

## 7. Lista martwych ścieżek

Status każdej: **„martwa — nie używać, nie usuwać przed testami regresyjnymi"**
(D-TERAZ-7). Udokumentowanie jako zachowanie należy do etapu 2 (testy
charakteryzujące), nie do tego etapu.

| Ścieżka | Opis | Dowód |
|---|---|---|
| `diag_tag` / `diag_severity` na `/future` | tag powstaje w `summary` (`prepare_future_layout.py:104–105`), ginie przy budowie `day_entry`; renderer nigdy go nie dostaje | 0/1596 dni w layoucie przy 15 dniach spełniających bramki |
| marker źródła dnia ` *` | `DailyPick.marker` trafia do pola `name`; renderer używa `label` (`image_generator.py`: `label or name`) | brak gwiazdek na kartach 11-10/11-12/11-16 |
| `apply_cloud_correction` | `owm_nowcast.py:287` — brak wywołań w kodzie i testach; docstring `nowcast_note` błędnie do niego odsyła | grep: 0 wywołań |
| flaga `coastal_day_alert` | czytana w `worth_knowing.py:1840`, nigdzie niezapisywana — zawsze `False` | grep: tylko odczyt i parametr domyślny |
| `model_agreement` | zapisywane jako zawsze `None` (`weather_payload.py:727`); odczyt `prepare_layout.py:1458` z `or {}` — inercyjny | wartość stała w kodzie |

## 8. Lista niezmienników

1. **Brak par nigdy nie oznacza spread=0.**
2. **Brak danych nie może być interpretowany jako zgodność modeli.**
3. `daily_diag_matched` pozostaje `observation_only=True`; żaden konsument
   produkcyjny go nie czyta; `coverage_threshold` pozostaje `None`.
4. Statusy `coverage_status` mają charakter wyłącznie opisowy — nigdy decyzyjny
   (D-TERAZ-3).
5. Diagnostyka (warstwa 5) nie prognozuje, nie renderuje i nie wpływa na
   warstwy 1–4; nie czyta pól kanonicznych jako dowodu wartości modelu OM.
6. Wartości kanoniczne są jedynymi, z których wolno budować prognozę dla
   użytkownika.
7. Po fuzji żaden wniosek o modelu nie wolno wysnuwać z samego `hour.source`;
   provenance czyta się per pole/dzień.
8. Nowcast nigdy nie zapisuje do godzin kanonicznych ani do payloadu poza
   surowym `owm_current`; nieswieży snapshot = brak korekty (nigdy „na zapas");
   nie dotyczy `/future`, temperatury, wiatru ani opadów w rekordach.
9. Coast nigdy nie zmienia wartości pogodowych; globalny cache nie jest daną
   jednej lokalizacji; brak aktualizacji istniejącego wpisu cache nie jest
   błędem.
10. Fazy Księżyca są warstwą astronomiczną niezależną od pogody i diagnostyki;
    kanał żółtej ikony przy dniu `/future` jest dla nich zarezerwowany
    (D-TERAZ-5).
11. Zakaz nowych konsumentów legacy `daily_diag`/`spread`/`is_volatile` do
    czasu decyzji D-SHADOW (D-TERAZ-2).
12. Żaden próg nie zostaje zatwierdzony w etapie 1; zmiana progu = jedna
    decyzja w jednym miejscu (docelowo warstwa 6), z telemetrią przed/po.

## 9. Kryteria bramki A

Bramka A (wg §5 dokumentu decyzji): *zaakceptowana na piśmie specyfikacja:
kontrakt warstw, semantyka `coverage_status`, rejestr konsumentów. Bez tego nie
powstaje żaden nowy sygnał.*

Kryteria spełnienia dla niniejszego dokumentu:

- **A1.** Kontrakt danych opisany dla wszystkich obszarów etapu 1: coverage
  (§3.1), matched comparison (§3.2), provenance (§3.3), nowcast OWM (§3.4),
  coast (§3.5) — ze znaczeniem `None` i rozróżnieniem brak/częściowe/pełne.
- **A2.** Semantyka `coverage_status` opisana wyczerpująco: 6 statusów,
  warunki, liczniki, charakter opisowy.
- **A3.** Rejestr konsumentów (§4) kompletny dla sygnałów wymaganych —
  `daily_diag`, `daily_diag_matched`, trzy znaczenia `is_volatile`,
  `coverage_status`, `daily_source`, OWM nowcast, coast, `diag_tag`,
  `moon_phase`, `coastal_day_alert` — z producentem, konsumentami, flagami,
  wpływem, testami i statusem.
- **A4.** Udokumentowane rozdzielenie `daily_diag` (legacy, zamrożone) i
  `daily_diag_matched` (referencyjne offline/shadow) oraz czterech semantyk
  nowcast (§3.4).
- **A5.** Mapowanie odpowiedzialności na 7 warstw (§5) wraz z listą miejsc
  mieszania (§6) i martwych ścieżek (§7).
- **A6.** Zero zmian wykonania: `git diff` ogranicza się do `docs/`; kod,
  testy i konfiguracja nietknięte; żaden konsument nie czyta
  `daily_diag_matched`; żaden próg nie zatwierdzony.
- **A7.** Listy odroczeń kompletne: shadow mode (§10) i decyzje UX (§11).

**Sposób akceptacji:** pisemna akceptacja właściciela — **uzyskana 2026-11-17**.
Po akceptacji dozwolony był wyłącznie commit dokumentacyjny na bieżącej gałęzi.
Przejście do etapu 2 (testy charakteryzujące) wymaga osobnej akceptacji.

## 10. Odroczone do shadow mode

(za D-SHADOW z §3 dokumentu decyzji i §7.2 planu etapu 1 — **niepodejmowane
w tym etapie**)

1. Odpowiednik volatility na matched + bramka pokrycia
   (`coverage_status`/`matched_temperature_count`) — liczone wyłącznie offline,
   z dystrybucjami per status, horyzont i lokalizacja (D-SHADOW-1, D-SHADOW-5).
2. Kandydat tagu diagnostycznego `/future` i weekendowego alertu `/day` na
   matched — porównanie legacy vs kandydat na tych samych payloadach, bez
   renderowania (D-SHADOW-1).
3. Ewentualna zmiana wejścia `daily_source` z `n_yr` legacy na liczniki
   timestampów — wyłącznie jako obliczenie równoległe do porównania wyników
   wyboru (D-SHADOW-4; kandydat szczególny: Hel +2).
4. Telemetria korekty OWM (model vs live vs wynik) i ocena trafności
   (D-SHADOW-6) oraz czułości reguł coast (D-SHADOW-7).
5. Progi: minimalna liczba par, rola `max` vs `mean`, ograniczenia horyzontu,
   kwalifikujące statusy `coverage_status` (D-SHADOW-5).

## 11. Wymagające osobnej decyzji UX

(za D-SHADOW z §3 dokumentu decyzji i §7.3 planu etapu 1 — **niepodejmowane
w tym etapie**)

1. Czy i jak pokazywać źródło dnia na `/future` (dziś marker ` *` nie dociera
   do karty) — prototypy + test zrozumienia (D-SHADOW-3).
2. Czy tag diagnostyczny na `/future` ma istnieć i w jakiej formie słownej;
   kanał wizualny odrębny od ikon Księżyca (kolizja w
   `_plan_days_card_layout`) (D-SHADOW-2, P6).
3. Treść i zrozumiałość oznaczeń typu „⚠️ Możliwe: 31° dzień" — dopiero na
   prototypach kart, z dystrybucjami z shadow mode (D-SHADOW-2).
4. Czy jakikolwiek sygnał diagnostyczny/coast ma trafić do WK (D-SHADOW-8).
5. Zasięg korekty OWM na `/day` (linia snapshotu vs bieżąca godzina bloków)
   oraz trafność korekty `/now` „oczami użytkownika" (D-SHADOW-6).
6. Sprostowanie nazwy „korekty satelitarnej" w UI/dokumentacji — to obserwacja
   OWM, nie obraz satelitarny (D-SHADOW-9).

---

*Dokument sporządzony read-only względem kodu. Jedyne zmiany w repozytorium
w tej sesji to: przywrócenie katalogu `docs/audit/2026-11-weekly/` z tagu
`audit-2026-11-weekly-final` (commity archiwizacji `314cdf0`…`1654e0e`,
wyłącznie dokumentacja) oraz dodanie niniejszego rejestru.*
