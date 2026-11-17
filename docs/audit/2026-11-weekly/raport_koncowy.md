# Raport końcowy audytu tygodniowego 2026-11-09…17 (sesja 3: audit_spread, /future, korekta OWM, Księżyc, domknięcie tygodnia)

> Wersja utrwalona w repozytorium (`docs/audit/2026-11-weekly/`). Raporty z sesji 1–2
> oraz wszystkie dane robocze audytu (payloady, karty, logi, skrypty analityczne)
> pozostały poza repozytorium i nie są częścią tego katalogu — patrz `README.md`
> i `manifest.md` w tym katalogu.

Audyt ściśle read-only: repo bez zmian (`git status` czysty, HEAD `33f0662`), brak commitów/pushów/PR.
Wszystkie artefakty poza repo: `/home/user/audit-tydzien/` (`analiza_sesja3.py`, `sesja3_wyniki.json`,
`ablacja_owm.py/.json`, `konsumenci_diag.py/.json`, `stats_dodatkowe.py/.json`).
Layouty liczono prawdziwym kodem repo (`prepare_future_layout`, `prepare_layout`, `prepare_now_layout`,
`owm_nowcast`, `daily_source`, `worth_knowing`) importowanym w trybie offline (stub `requests`) —
repo nie było modyfikowane.

## Rozliczenie końcowe plików

```
przeanalizowane payloady: 114/114 runów z pakietu (112 z manifestu + 2 luźne: Lagos 9-11, Wiazowna 9-11)
                           = 1710 payload-dni (15 dat × 114); sesja 3 dołożyła 84 runy / 1260 dni
audit_spread.jsonl:        7955 linii (dostarczony w sesji 3 — patrz korekta niżej)
wpisy coast:               110/114 runów ma *_coast.json (brak: Buenos Aires S1 — nigdy nie miał pliku,
                           São Paulo S1 — tylko stara nazwa *_coast_cache.json, 2 luźne payloady)
karty obejrzane:           8 PNG Wiązowny (11-10, 11-12: day/now/future; 11-16: now/future)
logi:                      treść przeanalizowana dla 63/462 (2026-11-10..12, sesja 2);
                           pozostałe 399 tylko zinwentaryzowane po nazwach — bez wniosków o treści
brakujące payloady:        2 (Rio, Punta Arenas — sesja S1 2026-11-09, partial_run kolektora)
brakujące karty:           6 PNG z S1 + 1 ręczny JPG (Hel now 9-11) — bez zmian z sesji 2
duplikaty:                 0 (nazwy, SHA-256 63 logów, linie audit_spread — patrz §1)
plik "nowy 301.json":      NIE jest JSON-em — notatka tekstowa (historia czatu o kotle/Salusie),
                           błędna nazwa/rozszerzenie w pakiecie, poza zakresem danych pogodowych
plik "Lagos 9-11 coast.json": globalny cache coast (11 wpisów coast:lat:lon), odpowiednik
                           *_coast_cache_all.json — nie jest wpisem jednej lokalizacji
błędy Airly / Telegrama:   0 w przeanalizowanych danych (airly obecne w 41/114 payloadów;
                           brak = brak instalacji w pobliżu, nie błąd)
forecast_source:           "OpenMeteo + Yr.no" we wszystkich 114/114 payloadach
alerty w payloadach:       0 w 81, 1 w 33 payloadach
```

## Korekty względem sesji 1–2

1. **`audit_spread.jsonl` istnieje** (dostarczony do tego samego folderu Drive) — wcześniejsze
   „brak pliku w pakiecie" jest nieaktualne. To drugi zapis starej diagnostyki z tego samego
   okresu co payloady (plus starsze uruchomienia developerskie od 2026-10-30) — zinwentaryzowany w §1.
2. **Żółte oznaczenia na `/future` to fazy Księżyca** (wyjaśnienie użytkownika, potwierdzone kodem
   i astronomicznie w §4). „Anomalia żółtej kropki 2026-11-24" z sesji 2 **nie była anomalią**:
   2026-11-24 to pełnia. Oznaczenia wyłączono z analizy diagnostyki/volatility/pokrycia.
3. Liczba payloadów tygodnia to **114 runów** (nie 112): doszło 2 luźnych payloadów ręcznych z 2026-11-09.

## 1. `audit_spread.jsonl` — inwentaryzacja i porównanie ze starą diagnostyką

Inwentaryzacja (7955 linii):

- 0 linii błędnych; jednolity zestaw 11 pól: `fetched_at_local, location, date, max_om, max_yr,
  spread_max, min_om, min_yr, spread_min, n_om, n_yr`. **Brak `is_volatile`** — volatility trzeba
  odtwarzać regułą z kodu (`spread_max ≥ 5,0` lub `spread_min ≥ 4,0`, `weather_payload.py:35,75`).
- Zakres `fetched_at_local`: 2026-10-30T07:17:18-05:00 … 2026-11-17T14:18:32+01:00 (czasy lokalne
  lokalizacji); daty prognoz: 2026-10-30 … 2026-12-01.
- 531 partii (fetched×lokalizacja): 529 × 15 dni + **2 niepełne × 10 dni** (2026-11-02 Buenos Aires
  i Punta Arenas — starsze uruchomienia spoza tygodnia pakietu).
- Lokalizacje: 10 pakietowych (Wiązowna 1185 linii, Hel 1170, Lagos 855, Algier 810, Buenos Aires 790,
  Punta Arenas 775, Rio/São Paulo po 630, Los Angeles 570, Las Vegas 405) + jednorazowe testowe
  (Bogota, Port-au-Prince, Tokio, Sydney, Santa Monica, East Honolulu po 15; Victoria 45).
- **Duplikaty: 0** (ani dokładnych linii, ani kluczy fetched×lokalizacja×data).
- Niepełne wpisy: `max_yr/min_yr = null` w 2549 liniach = dokładnie `n_yr=0` (32,0%);
  `max_om/min_om = null` w 20 liniach = `n_om=0`.

Dopasowanie do payloadów (lokalizacja + data + najbliższe `fetched_at_local`/`generated_at_local`):

- **5100/7955 linii (64%) dopasowano jednoznacznie** do zapisanych payloadów przy delcie ≤ 120 s.
  Struktura: 1710 linii co do sekundy (partie z trybu `/day` — ten sam `now_dt` w payloadzie
  i w diagnostyce, `weather_payload.py:685-690,724`) + 3390 linii ≤ 60 s (partie z trybów
  `/now` i `/future` tego samego runu kolektora; payload zapisano tylko z `/day`).
  113 runów ma po 45 dopasowanych linii (3 tryby × 15 dni), jeden run 15.
- Jednoznaczność potwierdzona: żaden wpis nie ma dwóch kandydatów-payloadów w promieniu 10 min;
  minimalny odstęp do drugiego najbliższego payloadu to **14 317 s (~4 h)**.
- Zgodność pól z `daily_diag` dopasowanego payloadu: `max_yr, min_yr, n_om, n_yr` = **100%**;
  `max_om, spread_max` = 99,88%; `min_om, spread_min` = 99,8%. Odchylenia <0,2% to odświeżenie
  prognozy pomiędzy trybami runu (minuty różnicy), nie błąd zapisu.
- Niedopasowane: 542 linie bez żadnego kandydata (lokalizacje testowe i daty spoza zapisanych
  payloadów) + 2313 linii tylko z dalekimi kandydatami (uruchomienia 2026-10-30…11-09 o tych
  samych datach prognoz co payloady tygodnia) — **nie są to błędy**, tylko zapisy z uruchomień,
  których payloadów pakiet nie zawiera.

Porównanie starej diagnostyki z `daily_diag_matched` (na 5100 dopasowanych liniach; rozdziały zgodne
z wymaganą klasyfikacją):

| kategoria | liczba | interpretacja |
|---|---|---|
| `n_yr=0` (stary zapis) | 1628 (31,9%) | dokładnie te same dni co `coverage_status=no_yr_records` (1628) — stara diagnostyka poprawnie widzi brak Yr.no, ale zapisuje wtedy spread 0,0/null |
| stara `spread_max=spread_min=0` przy matched `max_abs_spread_c > 0` | **954 (18,7%)** | maskowanie: ekstrema liczone z kanonicznego `temp_c` nadpisywanego fuzją wartością Yr.no (`weather_payload.py:617-619`), więc różnica znika |
| — w tym matched ≥ 4 °C | **81** | najsilniejsze maskowanie, np. Las Vegas 2026-11-11: matched 6,2 °C (23 pary), stary spread 0,0 |
| dawna volatility (`spread_max≥5` lub `spread_min≥4`) | 108 | z tego **72 (67%) przy <6 parach matched** — volatility na 3–4 parach 6-godzinnych jest artefaktem próbkowania, nie rozbieżnością modeli |
| matched `max ≥ 4 °C` przy <6 parach | 393 | lustro powyższego po stronie matched: sam max bez bramki liczby par byłby równie mylący |
| **rzeczywiste rozbieżności na wspólnych timestampach** (matched max ≥ 4 °C i ≥10 par) | **69 (1,4%)** | niemal wyłącznie Los Angeles/Las Vegas (+1/+2 dzień); przy tym mean_abs_spread_c zwykle 1,5–2,8 °C, tj. pojedyncze godziny, nie całe dni |
| różnice z nierównego pokrycia | — | `n_yr` starej diagnostyki = `n_yr_records` matched w **100%** dopasowanych linii; różnice biorą się nie z liczenia rekordów, lecz z tego, że stare ekstrema nie wymagają wspólnych timestampów |

Wniosek z §1: `audit_spread.jsonl` i `daily_diag` w payloadach to **ten sam zapis** (100% zgodności
rekordów Yr.no); plik JSONL nie wnosi nowych danych o rozbieżnościach, ale potwierdza na 3× większej
próbie (5100 vs 1710 payload-dni), że maskowanie spread=0 i volatility na małych parach są własnościami
starej metody, a nie danych.

## 2. `/future` — źródła, przełączenie, wpływ diagnostyk

Tabela źródeł per dzień horyzontu (114 layoutów policzonych prawdziwym
`prepare_future_layout_data`; wybór źródła wg `daily_source.pick_hours_for_daily_summary`):

| horyzont | źródło summary | marker ` *` | mediana rekordów Yr.no |
|---|---|---|---|
| +1 | yrno 114/114 | brak | 24 |
| +2 | yrno 106, openmeteo 8 | 8× ` *` | 23 |
| +3 … +9 | openmeteo 114/114 | zawsze ` *` | 4 |
| +10 … +14 | openmeteo 114/114 | zawsze ` *` | 0 |

Mechanizm i ustalenia:

- Hipoteza „~10 dni Yr.no + ~4 dni drugi dostawca" **nie potwierdziła się**. Yr.no zasila summary
  tylko +1 (zawsze) i +2 (zwykle). Reguła (`daily_source.py:18,22`): Yr.no tylko gdy
  `n_yr ≥ 18` (ze starego `daily_diag`) **i** ≥6 rekordów `source="yrno"` tego dnia; inaczej
  Open-Meteo, w ostateczności „mixed" (w tygodniu: 0 przypadków).
- Przełączenie zależy od **horyzontu**, bo tak działa API Yr.no: ~2 doby godzinowo, potem
  rekordy 6-godzinne (4/dobę), po +9 brak. Jedyna zależność od lokalizacji: **Hel** ma na +2
  tylko 15 rekordów Yr.no (we wszystkich 8 runach) i dlatego zawsze spada na Open-Meteo —
  to własność zasięgu szeregu Yr.no dla tej lokalizacji, nie reguła geograficzna.
- Zachowanie przy braku Yr.no: dni +10…+14 nie mają rekordów `source="yrno"` w ogóle
  (`n_yr_records=0`, status `no_yr_records`), a summary i tak powstaje z Open-Meteo —
  brak Yr.no nigdy nie blokuje `/future`. (Na poziomie całego payloadu `/day` ma osobną
  bramkę obu źródeł w `main_card.py:532-540`; `/future` jej nie podlega.)
- **Marker źródła jest niewidoczny na karcie**: trafia do pola `name` (`"Pt *"`), ale renderer
  używa pola `label` (`"Pt 13.11"`) — `image_generator.py` bierze `label or name`.
  Potwierdzone na kartach 11-10/11-12/11-16: brak gwiazdek; nagłówek pokazuje tylko ogólne
  „Źródło: OpenMeteo + Yr.no". Użytkownik nie widzi, który dzień jest z którego modelu.
- `daily_diag_matched` **nie wpływa na layout `/future`** — dowód empiryczny: dla 114/114 payloadów
  layout po usunięciu `daily_diag_matched` oraz po podstawieniu `max_abs_spread_c=99,9`/
  `coverage_status="matched"` jest **identyczny**. Zgodne z kodem: żaden layout nie czyta matched.
- `diag_tag`/`diag_severity` **nie docierają do końcowego layoutu**: `prepare_future_layout.py:104-105`
  ustawia je na lokalnym `summary`, ale `day_entry` (linie 111-118) ich nie kopiuje, a renderer
  czyta je z `next_days` (`image_generator.py:442`). Na 1596 dniach layoutów: **0 wpisów** z
  `diag_tag`. Jednocześnie 15 dni spełnia wszystkie bramki tagu volatility (is_volatile, n_om≥6,
  n_yr≥3, spokojna pogoda, różnica ≥2 °C) — tag powstaje i jest gubiony. To martwa ścieżka kodu,
  a nie cecha danych.
- Jedyny żywy konsument starej volatility poza wyborem źródła: weekendowy alert diagnostyczny
  na `/day` (`prepare_layout.py:1589-1646`, horyzont sob./niedz. 1–3 dni). W tygodniu:
  **0/114 layoutów** go wygenerowało (warunki nie zbiegły się w czasie) — konsument istnieje,
  ale w próbie nie zadziałał. `confidence_gate` nie czyta diagnostyki temperatur
  (jego `is_volatile` dotyczy opadów/wiatru/chmur); WK i Hero też nie.

## 3. Korekta satelitarna / nowcastowa (OWM)

Rozdzielenie warstw (potwierdzone w kodzie i payloadach):

1. **Prognoza modelowa**: rekordy OM (`temp_c_om` itd.) i Yr.no (`temp_c_yr`, rekordy `source="yrno"`).
2. **Wartość po fuzji**: kanoniczne `temp_c/precip_mm/uv_index/symbol_code/weather_code` rekordu OM
   nadpisane z Yr.no na wspólnych timestampach (`weather_payload.py:614-668`). To **nie** jest
   korekta satelitarna.
3. **Korekta satelitarna/nowcastowa**: wyłącznie OpenWeatherMap OneCall „current"
   (`owm_nowcast.py:get_current_weather`, w payloadzie jako `owm_current`). Mimo komentarzy
   „radar satelitarny" w kodzie jest to **punktowa obserwacja bieżąca z API OWM**, nie obraz
   satelitarny/radarowy. Dotyczy **tylko zachmurzenia** (`clouds` %) bieżącej godziny oraz —
   osobno — notki o ukrytym opadzie (`nowcast_note`, gdy OWM widzi ≥0,2 mm, a model 0).
   Nie zmienia temperatury, wiatru ani opadów w rekordach.
4. **Wartość użyta do renderowania**: `/now` — `_cld_override` wpisany tylko w pamięci do rekordu
   godziny 0 (`prepare_now_layout.py:184-190`), wpływa na Hero (ikona/tekst) i pierwszy blok;
   `/day` — linia snapshotu „Teraz: …" + override bieżącej godziny bloków
   (`prepare_layout.py:1751-1773`), Hero `/day` świadomie **bez zmian**; `/future` — brak
   jakiegokolwiek użycia OWM.

Bramki korekty (`classify_owm_cloud_correction`, `owm_nowcast.py:139-207`): świeżość OWM ≤ 90 min;
różnica efektywnych chmur ≥ 25 pkt proc. **i** inna etykieta nieba; bramka high-only (same chmury
wysokie w modelu → brak korekty); przy `live ≥85%` i UV>1,2 za dnia efektywne chmury obniżane do 65%.

Wyniki na 114 payloadach (każdy ma `owm_current`, wszystkie świeże względem `generated_at_local`):

- korekta godziny 0 byłaby aktywna (`should_override_hour0`): **51/114 (45%)** — korekta nie jest
  zjawiskiem marginalnym; przykłady: Lagos 11-10 model 31% → live 97% (efektywnie 65%),
  Buenos Aires 11-10 model 0% → 51%, Hel 11-11 model 98% → 19%.
- high-only gate zablokował korektę w 3 przypadkach; notka o ukrytym opadzie: **1/114**
  (São Paulo 2026-11-15: „Lokalnie możliwe słabe opady poza prognozą.").
- Ablacja na prawdziwych layoutach (10 payloadów z aktywną korektą, layout z `owm_current`
  vs `owm_current=None`): `/now` — w 10/10 zmieniają się `main_icon`, `summary`,
  `today_blocks[0].icon` i `primary_desc` (np. „Obecnie pochmurno" → „Obecnie dużo chmur");
  `/day` — w 10/10 zmienia się **tylko** `current_snapshot_line`, Hero i bloki bez zmian;
  `/future` — brak wpływu z definicji (kod nie importuje OWM).
- **`_eff` nie oznacza korekty satelitarnej**: `precip_eff_mm/weather_code_eff/symbol_code_eff/
  precip_class_eff` powstają w filtrze opadów widmowych w `main_card.py:551-601` (opady ≤0,2 mm
  przy suchych przesłankach → `trace`) i dotyczą wszystkich trybów. Właśnie dlatego zapisany
  payload (dump z `/day`, `prepare_layout.py:1071-1074`) zawiera pola `_eff` — przeszedł filtr
  `main_card`, a nie korektę OWM. `_cld_override` w payloadach **nie występuje** (istnieje tylko
  w pamięci layoutu `/now`).
- `apply_cloud_correction` (`owm_nowcast.py:287`) to **martwy kod** — brak wywołań w repo.
- Logi kolektora **milczą o OWM** (0 wystąpień w 63 logach): aktywności korekty nie da się
  potwierdzić z logów, tylko z `owm_current` w payloadach i klasyfikacji.

Testy a korekta (lista):

- obejmujące (pośrednio): `test_now_i18n.py::TestOwmReuse` (payload z `owm_current` jest
  konsumowany bez nowego wywołania HTTP; awaria pobierania nie psuje karty),
  `test_card_invariants.py::test_ghost_precip_does_not_trigger_owm_call` /
  `test_genuinely_dry_and_damp_hour_still_triggers_owm` (bramka kosztowa pobierania OWM na `/day`),
  `test_weather_payload_raw_sources.py` i `test_daily_diagnostics_matched.py` (wyłączają
  `ENABLE_OWM_NOWCAST`, sprawdzają że payload bez OWM działa), `test_coast.py`/`test_day_beach.py`
  (mockują `get_current_weather → None`, ścieżka nieaktywna).
- **nieobejmujące**: brak testów jednostkowych `classify_owm_cloud_correction`, `owm_is_fresh`,
  `nowcast_note`; brak testu z **aktywną** korektą zmieniającą wynik (żaden test nie sprawdza
  `_cld_override`, linii snapshotu ani zmiany ikony Hero przez OWM); brak przypadku
  high-only gate i reguły UV 85%→65% w testach.

## 4. Fazy Księżyca na `/future` (korekta sekcji z sesji 2)

- Mechanizm: `prepare_future_layout.py:119-126` dodaje `moon_phase` tylko gdy dokładny moment
  syzygii (algorytm Meeusa, `worth_knowing.py:467-524`) wypada w lokalnej dacie dnia; renderer
  rysuje ikonę `wk_full_moon` (żółta kropka) / `wk_new_moon` (żółty okrąg z czarnym środkiem)
  przy prawym marginesie wiersza albo w linii Hero. **Zero związku z `daily_diag`, volatility
  i pokryciem** — pola te nie stykają się w kodzie.
- Weryfikacja na 114 layoutach: **108 wpisów `moon_phase`, wszystkie `full` na 2026-11-24, 0 niezgodności**
  z niezależnym przeliczeniem `get_moon_phase_for_local_date` dla stref lokalizacji.
  108 = wszystkie runy, których okno +1…+14 obejmuje 24.11 (tj. generowane 2026-11-10…17).
- Syzygie w oknie audytu: nów **2026-11-09 08:02 Europe/Warsaw** (dla America/Los_Angeles
  lokalnie jeszcze 2026-11-08 23:02) i pełnia **2026-11-24 15:54 Europe/Warsaw** (LA: 06:54).
  Nów nie pojawia się na żadnej karcie `/future`, bo okna zaczynają się od +1 dnia
  (runy z 11-09 pokazują dni od 11-10) — zgodnie z kodem, nie jest to brak danych.
- Potwierdzenie wizualne: karta Wiązowny `/future` z 2026-11-16 ma żółtą kropkę wyłącznie
  przy „Wt 24.11"; karty z 11-10 i 11-12 (sesja 2) — przy tej samej dacie. Brak jakichkolwiek
  innych żółtych oznaczeń diagnostycznych na kartach.

## 5. Skumulowane statystyki tygodnia (114 runów, 1710 payload-dni)

- Statusy pokrycia matched: `partial_coverage` 998 (58%), `matched` 166 (10%),
  `no_yr_records` 546 (32%). Pary wg horyzontu (mediana): dzień 0: 14, +1: 24, +2: 23,
  +3…+9: 4, +10: 0–3, +11…+14: 0 — kształt z sesji 2 potwierdził się na 3,8× większej próbie.
- Rozrzut wg liczby par (max / dni z max ≥4 °C / dni z mean ≥4 °C): 0–2 pary (566 dni):
  2,9 / 0 / 0; 3–4 pary (750): **10,2 / 131 / 43**; 5–9 par (63): 7,9 / 3 / 1;
  10–23 pary (165): 7,2 / 16 / **0**; 24+ par (166): 7,9 / 7 / **0**.
  Przy ≥10 parach `mean_abs_spread_c` **nigdy** nie przekracza 4 °C — niestabilny jest tylko max.
- Dni z ≥6 parami i max ≥4 °C: **26** (wcześniej 6) — 12× Las Vegas, 11× Los Angeles
  (prawie wszystkie na +1/+2), 2× São Paulo, 1× Buenos Aires; stare `spread=0` w 24/26
  (maks. Los Angeles 2026-11-17: 7,9 °C przy 24 parach, mean 2,8).
- Maskowanie w payloadach: stara `spread=0` przy matched >0 w **554/1710 (32%)**, w tym ≥4 °C: 34.
- Stare `is_volatile=true`: 36 dni, w tym 24 przy <6 parach (67%).
- Powtarzalność (1486 kolejnych par runów, ta sama lokalizacja+data): stabilne 877 (59%);
  zmieniło się tylko pokrycie 271; tylko prognoza (te same pary, max zmienił się ≥1 °C) 202;
  oba 136. Wniosek z sesji 2 utrzymany: zmienność rozrzutu to mniej więcej po równo
  pokrycie i sama prognoza — dogrywki (14:15/19:15/23:00) są dodatkowymi obserwacjami
  tego samego dnia, nie niezależnymi dniami.
- UV (pomocniczo): 51 078 rekordów godzinowych; semantyka `standard` 34 146 vs `clear_sky` 16 932
  (rekordy Yr.no i rekordy OM po fuzji UV) — bez zmian wniosków, bez proponowania progów.

## 6. Coast

- 110 wpisów `*_coast.json`: każda z 10 lokalizacji ma **dokładnie 1 stabilny wariant** we
  wszystkich runach: śródlądowe Wiązowna/São Paulo/Las Vegas `is_coastal=false` ✔; nadmorskie:
  Algier 2,0 km [[260,90]], Buenos Aires 3,0 km [[340,130]], Hel 1,0 km [[0,360]],
  Lagos 1,0 km [[0,360]], Rio 1,0 km [[0,250]], Punta Arenas 1,0 km [[330,250]],
  Los Angeles 25,0 km [[220,270]]; `radius_km=25`, wersja
  `ne_50m_ocean:50m;radar:v2;r25;step10;minw20;marine75g90;full360` wszędzie.
- `cache_file_updated_during_run=false` w 108/110; `true` tylko w 2 pierwszych wpisach pakietu
  (Las Vegas i Los Angeles, 2026-11-09 19:00) — wtedy sygnatury policzono i zapisano;
  potem same cache hity. Brak aktualizacji pliku przy istniejącym wpisie nie jest błędem.
- Wpływ z kodu: `/day` — alerty `coast_marine_storm_day` / `coast_beach_day_*` dopisywane do
  alertów karty (`prepare_layout.py:1492-1580`); `/now` — **też ma wpływ** (koryguję ewentualne
  założenie przeciwne): `coastal_note` w kaskadzie `context_line`
  (`prepare_now_layout.py:604-676,732`), z pierwszeństwem sztormu nad plażą; `/future` —
  **brak wpływu** (moduł nie importuje coast). Listopad jest poza sezonem plażowym PL
  (01.06–15.09), więc realnie mógł wystąpić tylko `marine_storm`.
- Flaga `coastal_day_alert` w WK nigdy nie jest ustawiana w payloadzie (`worth_knowing.py:1840`
  czyta pole, którego nikt nie zapisuje) — martwa ścieżka.
- Ograniczenie: w piaskownicy audytu stos geo (shapefile) był niedostępny, więc lokalne layouty
  miały coast wyłączony; wpływ coast zweryfikowano z kodu i wpisów cache, a nie z kart
  lokalizacji nadmorskich (kart nadmorskich w próbie kart nie było — Wiązowna jest śródlądowa).

## 7. Rozdzielenie zjawisk (podsumowanie)

| obszar | werdykt |
|---|---|
| błędy starej diagnostyki modeli | maskowanie spread=0 przez fuzję (954/5100 linii audit_spread; 554/1710 payload-dni), volatility na 3–4 parach (67–72% przypadków przy <6 parach), ekstrema bez wspólnych timestampów |
| rzeczywiste rozbieżności modeli | rzadkie i godzinowe: 69/5100 linii (max ≥4 °C przy ≥10 parach), głównie Los Angeles/Las Vegas +1/+2; mean przy ≥10 parach nigdy ≥4 °C |
| zachowanie źródeł forecastu | Yr.no godzinowo tylko +1/+2 (Hel: tylko +1), potem 6-godzinnie, brak po +9; Open-Meteo niesie +3…+14; przełączenie niewidoczne na karcie (marker ginie w `label`) |
| korekta satelitarna/nowcastowa | OWM current → tylko chmury bieżącej godziny + rzadki alert opadu; aktywna w 45% payloadów; `/now`: Hero+1. blok; `/day`: tylko linia „Teraz:"; `/future`: brak; `*_eff` to filtr opadów widmowych, nie satelita |
| fazy Księżyca | pełnia 2026-11-24 (108/108 layoutów zgodnych), nów 2026-11-09 poza oknami `/future`; zero związku z diagnostyką — sekcja z sesji 2 skorygowana |
| moduł coast | wpisy stabilne i zgodne z geografią; wpływ na `/day` (alerty) i `/now` (notka), brak na `/future`; 1 martwa flaga WK |
| martwe ścieżki kodu znalezione przy okazji | tag volatility `/future` (15 dni spełnia bramki, 0 w layoucie), `apply_cloud_correction`, marker źródła ` *` na `/future` |

## 8. Czy można bezpiecznie przełączyć konsumenta na `daily_diag_matched`?

**Nie proponuję przełączenia żadnego konsumenta** (zgodnie z zakresem audytu) i stwierdzam,
że na dziś **żaden konsument nie jest gotowy do bezpiecznego przełączenia bez zmian w kodzie**:

- `daily_diag_matched` jest dziś czysto obserwacyjne (`observation_only=True`,
  `coverage_threshold=None`); żaden kod produkcyjny go nie czyta — jedyny ślad to zapis w payloadzie.
- Kandydaci z poprzednich etapów pozostają ci sami: tag volatility `/future` i weekendowy alert
  `/day`. Pierwszy jest obecnie martwy z innego powodu (tag nie trafia do `day_entry`),
  więc samo źródło danych niczego by nie zmieniło; drugi wymaga odpowiednika `is_volatile`
  na matched, którego nie ma (matched nie ma flagi ani progów — zgodnie z decyzją ich nie zatwierdzano).
- Dane matched są wiarygodne tam, gdzie mają pokrycie (zgodność rekordów 100%, rozbieżności
  godzinowe odtwarzalne), ale na horyzontach +3…+14 mediana to 4 pary lub 0 — przełączenie
  bez bramki `matched_temperature_count`/`coverage_status` odtworzyłoby błędy starej
  volatility w nowej formie.
- Wybór źródła w `daily_source` używa `n_yr` starej diagnostyki — to zachowanie źródeł
  forecastu, nie diagnostyka; jego ewentualna zmiana nie jest objęta tym wnioskiem.

Warunki wstępne ewentualnej przyszłej decyzji (poza zakresem tego audytu): zdefiniowanie
odpowiednika volatility na matched + bramki pokrycia, naprawa martwej ścieżki tagu `/future`,
testy z aktywną korektą OWM i przypadkami pokrycia. Żadnych progów w tym raporcie nie zatwierdzono.

## Ograniczenia audytu

- Treść logów tylko dla 63/462 (2026-11-10..12); klasyfikacja pozostałych runów oparta o nazwy/sesje.
- Karty oglądano tylko dla Wiązowny (8 PNG); wnioski o layoucie `/future`, `/now`, `/day` oparto
  dodatkowo na przeliczeniu prawdziwym kodem repo wszystkich 114 payloadów (offline, bez sieci).
- Payload zapisany jest tylko z trybu `/day`; partie audit_spread z `/now` i `/future` dopasowano
  czasowo (≤60 s), co jest wystarczające przy odstępie ≥4 h do kolejnego runu tej lokalizacji.
- Kolektor działał na Windows; audyt nie odtwarzał środowiska produkcyjnego (stos geo coast
  niedostępny lokalnie — wpływ coast oceniono z kodu i cache).

**STOP** — zgodnie z zakresem, po tym raporcie nie podejmuję dalszych działań.
