# Dokument decyzji architektonicznej i plan implementacji
## Diagnostyka modeli, źródła `/future`, nowcast OWM, coast i fazy Księżyca

| Pole | Wartość |
|---|---|
| Status dokumentu | **ZAAKCEPTOWANY w zakresie decyzji D-TERAZ (§2) — decyzja właściciela po zakończeniu audytu; decyzje D-SHADOW (§3) pozostają ODROCZONE; nic nie zostało zaimplementowane** |
| Podstawa | Raport końcowy audytu tygodniowego (`raporty/raport_sesja3.md`), pakiet 2026-11-09…17, 114 payloadów / 1710 payload-dni, `audit_spread.jsonl` (7955 linii) |
| Zakres | Wyłącznie decyzje i plan. Dokument nie zmienia kodu, payloadu, kart ani alertów |
| Tryb prac objętych dokumentem | Read-only na etapie decyzji; implementacja dopiero po przejściu bramek z §5 |
| Decyzje progowe | **Żadnych nowych progów nie zatwierdzono tym dokumentem** |

---

## 0. Streszczenie wykonawcze

Audyt potwierdził, że główne problemy nie leżą w samych danych pogodowych, tylko
w **semantyce warstw pośrednich**: stara diagnostyka porównuje wartości po fuzji modeli
i dlatego ukrywa realne różnice; sygnał volatility powstaje na zbyt małych próbkach;
status pokrycia istnieje technicznie, ale nie ma zatwierdzonego znaczenia dla konsumentów;
część informacji o źródle dnia na `/future` ginie między layoutem a rendererem;
a aktywna korekta nowcastowa (OWM) działa w produkcji bez testów, które sprawdzałyby
jej faktyczny wpływ na kartę.

Rekomendowana kolejność jest zachowawcza:

1. **Najpierw kontrakt danych i jawna semantyka statusów** — bez zmian zachowania.
2. **Potem testy regresyjne i testy aktywnej korekty OWM** — żeby zamrozić obecne,
   potwierdzone zachowanie, zanim ktokolwiek je zmieni.
3. **Potem shadow mode** — nowe sygnały liczone równolegle, ale niewidoczne dla użytkownika.
4. **Dopiero na końcu decyzja o progach i konsumentach** — na danych shadow i UX,
   nie na intuicji ani na samym `max_abs_spread_c`.

Do czasu przejścia tych bramek: **nie przełączamy `daily_diag_matched`, nie zmieniamy
`is_volatile`, WK ani alertów** (pełna lista zakazów w §7).

---

## 1. Potwierdzone problemy

### P1. Maskowanie różnic przez stare `spread=0`

- **Fakt:** stara diagnostyka (`build_daily_diagnostics` w `weather_payload.py`) liczy
  ekstrema dobowe z kanonicznych pól godzinowych. Na rekordach Open-Meteo pola te są
  wcześniej nadpisywane przez fuzję wartościami Yr.no (`weather_payload.py:617-619`),
  więc „różnica modeli" potrafi wyjść `0,0` dokładnie tam, gdzie modele się różnią.
- **Dowód:** na 5100 jednoznacznie dopasowanych liniach `audit_spread.jsonl` stara
  diagnostyka ma `spread_max=spread_min=0` przy matched `max_abs_spread_c > 0` w **954
  przypadkach (18,7%)**, w tym **81 przy ≥4 °C**; w payloadach tygodnia: **554/1710
  payload-dni (32%)**, w tym 34 przy ≥4 °C. Przykład skrajny: Las Vegas 2026-11-11 —
  matched 6,2 °C przy 23 parach, stary spread 0,0.
- **Skutek:** każdy konsument starego `spread`/`is_volatile` może przeoczyć realną
  rozbieżność modeli i jednocześnie dostać fałszywy sygnał tam, gdzie spread nie jest zerowy.
- **Klasyfikacja:** błąd metody diagnostycznej, nie błąd danych ani źródeł.

### P2. Stare `is_volatile` przy małej liczbie par

- **Fakt:** flaga `is_volatile` (`spread_max ≥ 5,0` lub `spread_min ≥ 4,0`,
  progi z `weather_payload.py:35,75`) nie ma bramki liczby wspólnych obserwacji.
  Na horyzontach +3…+9 Yr.no występuje w rozdzielczości 6-godzinnej (ok. 4 rekordy/dobę),
  więc ekstrema porównują de facto pojedyncze godziny.
- **Dowód:** w `audit_spread.jsonl` 108 przypadków dawnej volatility, z czego **72 (67%)
  przy <6 parach matched**; lustro po stronie matched: 393 dni z `max ≥ 4 °C` przy <6 parach.
  Jednocześnie przy ≥10 parach `mean_abs_spread_c` **nigdy** w tygodniu nie przekroczył 4 °C,
  a 26 dni z ≥6 parami i `max ≥ 4 °C` miało stare `spread=0` w 24 przypadkach.
- **Skutek:** volatility ze starej diagnostyki miesza trzy różne zjawiska: realną
  rozbieżność godzinową, artefakt rzadkiego próbkowania i maskowanie przez fuzję.
- **Klasyfikacja:** błąd polityki sygnału (brak bramki pokrycia), nie samodzielny błąd danych.

### P3. Brak jawnej, zatwierdzonej semantyki `coverage_status`

- **Fakt:** `daily_diagnostics_matched` technicznie produkuje statusy
  (`no_om_records`, `no_yr_records`, `no_common_hours`, `no_matched_temperature`,
  `partial_coverage`, `matched`; logika w `daily_diagnostics_matched.py:_coverage_status`),
  ale **nie istnieje zatwierdzona semantyka konsumencka**: co wolno zrobić przy danym
  statusie, czym decyzja przy `partial_coverage` ma się różnić od `matched`, oraz jaki
  jest minimalny dopuszczalny poziom pokrycia. Pole `coverage_threshold` jest `None`,
  a wynik `observation_only=True`.
- **Dowód:** w tygodniu wystąpiły tylko 3 z 6 statusów (`partial_coverage` 998,
  `matched` 166, `no_yr_records` 546 payload-dni); pozostałe trzy statusy nie mają
  ani jednej obserwacji produkcyjnej z pakietu, a żaden kod produkcyjny nie czyta
  `daily_diag_matched`.
- **Skutek:** samo przełączenie konsumenta na matched bez warstwy polityki odtworzyłoby
  błędy P2 w nowej formie (np. decyzja na `matched` przy 3 parach z `partial_coverage`).
- **Klasyfikacja:** brak kontraktu decyzyjnego, nie brak obliczeń.

### P4. Źródła `/future` niewidoczne lub częściowo martwe w rendererze

- **Fakt 1 (źródło dnia):** wybór źródła summary (`daily_source.py:18,22`) zasila Yr.no
  tylko na +1 (114/114) i zwykle +2 (106/114; Hel — z powodu 15 rekordów Yr.no — zawsze
  Open-Meteo), a od +3 praktycznie wyłącznie Open-Meteo. Marker fallbacku ` *` trafia
  do pola `name` dnia, ale renderer używa pola `label` (`image_generator.py`: `label or name`),
  więc **użytkownik nie widzi, który dzień pochodzi z którego modelu**; nagłówek pokazuje
  jedynie ogólne „Źródło: OpenMeteo + Yr.no".
- **Fakt 2 (tag volatility):** `prepare_future_layout.py:104-105` ustawia
  `summary["diag_tag"]`/`diag_severity`, ale `day_entry` ich nie kopiuje, a renderer
  czyta je z `next_days`. Na 1596 policzonych dniach `/future`: **0 tagów** w layoucie
  końcowym, przy **15 dniach spełniających wszystkie bramki** powstania tagu. To martwa
  ścieżka end-to-end.
- **Fakt 3:** jedyny żywy konsument starej volatility (weekendowy alert `/day`,
  `prepare_layout.py:1589-1646`) w tygodniu nie wygenerował **ani jednego** alertu (0/114).
- **Skutek:** warstwa renderingu obiecuje informacje (gwiazdka źródła, tag ⚠️), których
  karta nigdy nie pokazuje; decyzje UX nie mogą opierać się na założeniu, że te elementy działają.
- **Klasyfikacja:** defekt kontraktu layout→renderer oraz brak decyzji UX co do docelowej prezentacji.

### P5. Brak testów aktywnej korekty OWM

- **Fakt:** korekta nowcastowa jest aktywna produkcyjnie — na payloadach tygodnia
  klasyfikacja z kodu repo (`classify_owm_cloud_correction`) dała `should_override_hour0`
  w **51/114 przypadkach (45%)**. Ablacja na prawdziwych layoutach pokazała, że na `/now`
  zmienia Hero i pierwszy blok godzinowy, a na `/day` dodaje linię snapshotu „Teraz: …".
- **Dowód braku pokrycia:** istnieją testy bramki pobierania OWM, reuse snapshotu
  z payloadu i ścieżki nieaktywnej (`owm_current=None`), ale **żaden test nie sprawdza
  aktywnej korekty**: brak testów jednostkowych `classify_owm_cloud_correction`,
  `owm_is_fresh`, `nowcast_note`; brak asercji na `_cld_override`, linię snapshotu,
  zmianę ikony Hero, bramkę high-only i regułę UV 85%→65%.
- **Skutek:** zmiana dowolnej bramki korekty (świeżość 90 min, próg 25 pkt proc.,
  high-only, UV cap) może dziś po cichu zmienić wygląd kart `/now` i `/day`.
- **Klasyfikacja:** luka regresyjna w obszarze o realnym wpływie na użytkownika.

### P6. Fazy Księżyca muszą pozostać oddzielone od diagnostyki pogody

- **Fakt:** żółte oznaczenia na `/future` to ikony faz Księżyca (`wk_full_moon`,
  `wk_new_moon`) z `get_moon_phase_for_local_date` (`worth_knowing.py:467-524`),
  dokładane w `prepare_future_layout.py:119-126` wyłącznie na podstawie daty syzygii
  w strefie lokalizacji. Nie mają żadnego połączenia z `daily_diag`, volatility ani pokryciem.
- **Dowód:** 108/108 wystąpień w tygodniu to `full` na 2026-11-24 (0 niezgodności
  z niezależnym przeliczeniem astronomicznym); nów 2026-11-09 leży przed oknami `/future`.
  Wcześniejsza „anomalia żółtej kropki" z sesji 2 audytu była błędem interpretacji.
- **Skutek:** jakiekolwiek przyszłe oznaczenia diagnostyczne na `/future` nie mogą używać
  tej samej kanałowej sygnalizacji (kolor żółty/ikona przy prawym marginesie) w sposób,
  który myliłby je z fazami Księżyca.
- **Klasyfikacja:** wymóg separacji semantycznej w rendererze i dokumentacji, nie defekt.

### P7. Coast wymaga osobnego traktowania

- **Fakt:** sygnatury coast (`*_coast.json`) są stabilne i zgodne z geografią (110 wpisów,
  po 1 wariancie na lokalizację; `cache_file_updated_during_run=true` tylko przy pierwszych
  obliczeniach 2026-11-09). Moduł wpływa na `/day` (alerty marine_storm/beach) i na `/now`
  (`coastal_note`), nie wpływa na `/future`. Znika sezonowość: listopad jest poza sezonem
  plażowym PL, więc realnie mógł wystąpić tylko `marine_storm`.
- **Fakty uboczne:** flaga `coastal_day_alert` czytana przez WK nigdy nie jest ustawiana
  w payloadzie (martwa ścieżka); wpływ coast nie był weryfikowalny na kartach lokalizacji
  nadmorskich (brak takich kart w próbie; lokalnie brak stosu geo).
- **Skutek:** coast nie wolno mieszać z diagnostyką modeli ani z nowcastem: to osobny
  kontekst geograficzno-wiatrowy z własnym cyklem życia cache i własnymi regułami alertów.
- **Klasyfikacja:** odrębny moduł kontekstowy; wymaga własnych testów i własnych decyzji, nie progów temperatury.

---

## 2. Decyzje, które można podjąć już teraz

> **Status tej sekcji: ZAAKCEPTOWANA** (decyzje D-TERAZ przyjęte przez właściciela
> po zakończeniu audytu; patrz `README.md` w tym katalogu). Akceptacja ma charakter
> dokumentacyjno-architektoniczny i **nie zmienia zachowania produkcyjnego** —
> wdrożenia wynikające z tych decyzji wymagają osobnych zadań z §6.

Decyzje poniżej **nie zmieniają zachowania produkcyjnego** albo ograniczają się do
zamrożenia stanu obecnego; można je przyjąć bez shadow mode i bez danych UX.

| ID | Decyzja (rekomendacja) | Uzasadnienie | Czego decyzja NIE robi |
|---|---|---|---|
| D-TERAZ-1 | `daily_diag_matched` jest **referencyjną diagnostyką porównawczą do analiz offline/raportów**; stara `daily_diag` uzyskuje status **legacy, zamrożony** | matched liczy na surowych polach i wspólnych timestampach; zgodność rekordów 100%, maskowanie legacy udowodnione (P1) | Nie czyni matched źródłem dla żadnego konsumenta produkcyjnego |
| D-TERAZ-2 | **Zakaz nowych konsumentów** starego `daily_diag`/`spread`/`is_volatile` do czasu decyzji z §3 | każdy nowy konsument dziedziczy P1+P2 | Nie usuwa istniejących konsumentów (wybór źródła, alert weekendowy) |
| D-TERAZ-3 | Statusy `coverage_status` mają do odwołania charakter **wyłącznie opisowy** (telemetria/raport), nigdy decyzyjny | brak zatwierdzonej semantyki i progów (P3) | Nie definiuje jeszcze, co wolno przy danym statusie |
| D-TERAZ-4 | Słownik pojęć warstw jest wiążący: `*_eff` = filtr opadów widmowych (`main_card.py:551-601`), **nie** korekta satelitarna; korekta nowcastowa = wyłącznie OWM current (chmury bieżącej godziny + notka o opadzie) | audyt potwierdził mylenie tych mechanizmów w nazewnictwie i komentarzach | Nie zmienia nazw pól w payloadzie (to byłaby zmiana kontraktu) |
| D-TERAZ-5 | Fazy Księżyca są **astronomiczną warstwą prezentacji**, niezależną od pogody i diagnostyki; kanał żółtej ikony przy dniu na `/future` jest dla nich zarezerwowany | P6; unikamy powtórki błędnej „anomalii" i kolizji sygnalizacji | Nie zmienia ikon, pozycji ani reguł pokazywania faz |
| D-TERAZ-6 | Coast to **osobny moduł kontekstowy** (sygnatura geograficzna + reguły wiatru), poza diagnostyką modeli i poza nowcastem; `*_coast_cache_all.json` to globalny cache pomocniczy, nie dane jednej lokalizacji | P7; stabilność wpisów potwierdzona | Nie zmienia reguł marine_storm/beach ani cache |
| D-TERAZ-7 | Elementy martwe (`diag_tag` na `/future`, marker ` *` źródła dnia, `apply_cloud_correction`, flaga `coastal_day_alert`) otrzymują status **„martwe — nie używać, nie usuwać przed testami regresyjnymi"** | P4, P5; usunięcie bez charakterystyki grozi ukrytą zmianą zachowania | Nie naprawia ani nie usuwa żadnej z tych ścieżek już teraz |
| D-TERAZ-8 | Zakres korekty OWM pozostaje zamrożony: **tylko zachmurzenie bieżącej godziny + notka o ukrytym opadzie**; bez rozszerzania na temperaturę, wiatr, opady w rekordach ani na `/future` | ablacja pokazała ograniczony, przewidywalny zasięg; rozszerzenie bez testów byłoby niekontrolowane (P5) | Nie zmienia bramek korekty (świeżość, 25 pkt, high-only, UV cap) |
| D-TERAZ-9 | Wewnętrzny kontrakt layoutu `/future` ma jawnie nieść źródło dnia (`pick.source`) jako **provenance do decyzji UX**, nawet jeśli karta go dziś nie pokazuje | bez tego nie da się później uczciwie zdecydować o widoczności (P4) | Nie zobowiązuje do pokazywania źródła na karcie |

---

## 3. Decyzje wymagające shadow mode i danych UX

> **Status tej sekcji: ODROCZONA.** Żadna z decyzji D-SHADOW nie została podjęta;
> nie wolno ich podejmować na podstawie samego audytu.

Decyzji poniżej **nie wolno podejmować na podstawie samego audytu**. Każda wymaga
równoległego liczenia bez wpływu na użytkownika (shadow mode) i/lub danych o zrozumieniu kart.

| ID | Decyzja odroczona | Dlaczego nie teraz | Co musi dostarczyć shadow/UX przed decyzją |
|---|---|---|---|
| D-SHADOW-1 | Czy i kiedy konsument może użyć sygnału volatility opartego o matched (kandydaci: weekendowy alert `/day`, tag `/future`) | matched nie ma odpowiednika `is_volatile` ani progów; przy <6 parach max jest artefaktem (P2, P3) | dystrybucje sygnału per status pokrycia/horyzont/lokalizacja; odsetek dni, w których nowy sygnał różniłby się od legacy; ręczna ocena próby przypadków granicznych |
| D-SHADOW-2 | Czy tag diagnostyczny na `/future` ma w ogóle istnieć, a jeśli tak — w jakiej formie słownej | ścieżka jest martwa; nie wiadomo, czy ⚠️-tag pomaga, czy straszy; kolizja z ikonami Księżyca (P4, P6) | prototypy kart (warianty tekstu/pozycji), test zrozumienia: czy użytkownik odróżnia „modele się różnią" od ostrzeżenia pogodowego i od fazy Księżyca |
| D-SHADOW-3 | Czy i jak pokazywać źródło dnia na `/future` (gwiazdka, legenda, oznaczenie per dzień) | dziś niewidoczne; pokazanie to zmiana UX, nie poprawka „oczywistego błędu" (P4) | prototypy + test zrozumienia znaczenia oznaczenia; wpływ na czytelność 14 wierszy |
| D-SHADOW-4 | Czy wybór źródła w `daily_source` ma używać liczników matched (`n_yr_timestamps`/`matched_temperature_count`) zamiast starego `n_yr` | to zmiana zachowania źródeł forecastu (wpływa na `/day`, `/future`, teaser weekendowy), nie tylko diagnostyki | shadow pick: dla każdego dnia który `pick` by się zmienił (w tygodniu kandydat szczególny: Hel +2); ocena, czy zmiana jest w ogóle materialna |
| D-SHADOW-5 | Progi: minimalna liczba par, rola `max` vs `mean`, ograniczenia horyzontu, kwalifikujące statusy `coverage_status` | audyt celowo nie zatwierdził progów; mean zachowuje się inaczej niż max i trzeba wybrać semantykę sygnału | pełne dystrybucje z shadow (nie tylko tygodnia), stabilność między runami (audyt: zmiana rozrzutu to ~po równo pokrycie i prognoza), koszt fałszywych alarmów vs przeoczeń |
| D-SHADOW-6 | Czy korekta OWM na `/day` ma pozostać przy linii snapshotu, czy świadomie objąć też bieżącą godzinę bloków; jaka jest trafność korekty `/now` „oczami użytkownika" | aktywna w 45% payloadów; logi milczą o OWM, brak danych o trafności subiektywnej (P5) | telemetria shadow (model vs live vs wynik), próbka ręcznej oceny zgodności z warunkami rzeczywistymi, odsetek korekt w każdą stronę |
| D-SHADOW-7 | Czy reguły coast (marine_storm/beach) mają właściwą czułość w `/day` i `/now` | tydzień listopadowy nie zawiera sezonu plażowego ani zweryfikowanych epizodów sztormowych na kartach (P7) | dane sezonowe + ground truth epizodów wiatru od morza; shadow alertów z oceną precyzji |
| D-SHADOW-8 | Czy jakikolwiek sygnał diagnostyczny/coast ma trafić do WK (worth knowing) | WK dziś nie czyta diagnostyki temperatur; flaga coast w WK jest martwa (P7) | analiza, czy WK ma miejsce na metasygnały; prototypy i test zrozumienia; osobna decyzja produktowa |
| D-SHADOW-9 | Czy nazwa i komunikacja „korekty satelitarnej" ma zostać sprostowana w UI/dokumentacji (to obserwacja OWM, nie obraz satelitarny) | audyt potwierdził rozjazd nazwy i mechanizmu; zmiana tekstów to decyzja UX | przegląd tekstów użytkownika (i18n `(satellite)`), decyzja o nazewnictwie zgodnym ze źródłem danych |

---

## 4. Proponowany model warstw

Docelowy podział odpowiedzialności (stan obecny → kierunek). Zasada przewodnia:
**każda warstwa wyżej widzi tylko jawne wyjścia warstwy niżej, nigdy jej surowe wnętrze.**

```
┌────────────────────────────────────────────────────────────────────┐
│ 7. RENDERING AND ALERTS                                            │
│    layouty /day /now /future, renderer PNG, alerty, WK,            │
│    astronomiczna prezentacja faz Księżyca (osobny kanał)           │
├────────────────────────────────────────────────────────────────────┤
│ 6. CONSUMER POLICY                                                 │
│    jedyne miejsce decyzji: bramki pokrycia, progi, wybór           │
│    konsumenta, kwalifikacja sygnałów (dziś: rozproszone!)          │
├────────────────────────────────────────────────────────────────────┤
│ 5. MODEL COMPARISON DIAGNOSTICS                                    │
│    daily_diag (legacy, zamrożony) ‖ daily_diag_matched             │
│    (referencyjny offline; observation_only do decyzji z §3)        │
├────────────────────────────────────────────────────────────────────┤
│ 4. COAST                        │ 3. NOWCAST                       │
│    sygnatura geograficzna,      │  OWM current: świeżość,          │
│    marine_storm / beach         │  klasyfikacja chmur, notka       │
│    (kontekst, nie pogoda)       │  o opadzie (bieżąca godzina)     │
├────────────────────────────────────────────────────────────────────┤
│ 2. PROVENANCE                                                      │
│    source rekordu, *_source per pole, forecast_source,             │
│    model_updated_at, pick źródła dnia                              │
├────────────────────────────────────────────────────────────────────┤
│ 1. CANONICAL WEATHER VALUES                                        │
│    godziny po fuzji OM+Yr.no (pola kanoniczne) + surowe            │
│    *_om / *_yr zachowane obok; filtr *_eff jako reguła kanoniczna  │
└────────────────────────────────────────────────────────────────────┘
```

### Warstwa 1 — Canonical weather values

- **Odpowiedzialność:** jedna lista godzin na lokalizację z polami kanonicznymi
  używanymi dalej (`temp_c`, `precip_mm`, kody, chmury itd.) po fuzji modeli
  oraz — obok, niemieszane — wartości surowe (`temp_c_om`, `temp_c_yr` itd.).
- **Stan obecny:** `weather_payload.build_payload_for_location`; fuzja nadpisuje
  kanoniczne pola rekordu OM wartościami Yr.no na wspólnych timestampach;
  filtr opadów widmowych dopisuje `*_eff` w `main_card`.
- **Kontrakt docelowy:** reguły fuzji i reguły `*_eff` są opisane jedną specyfikacją;
  żaden konsument nie zgaduje, czy pole kanoniczne pochodzi z OM, Yr.no czy reguły `_eff`.
- **Niezmiennik:** wartości kanoniczne są jedynymi, z których wolno budować prognozę dla użytkownika.

### Warstwa 2 — Provenance

- **Odpowiedzialność:** pochodzenie każdego rekordu i pola: `source` rekordu
  (`openmeteo`/`yrno`), `*_source` per pole, `forecast_source`, `model_updated_at_local`,
  a dla summary dobowych — jawny wynik wyboru źródła dnia.
- **Stan obecny:** provenance per pole istnieje (`temp_c_source` itd.), ale po fuzji
  rekord `source="openmeteo"` może mieć pola ze źródłem `yrno` — mylące dla konsumenta
  czytającego tylko `source`; wybór dnia znika w polu `name`/`label` (P4).
- **Kontrakt docelowy:** konsument decyzyjny czyta provenance **per pole/dzień**,
  nie per rekord; layout niesie `pick.source` jako jawne pole provenance (por. D-TERAZ-9).
- **Niezmiennik:** żaden wniosek o modelu nie wolno wysnuwać z samego `source` rekordu po fuzji.

### Warstwa 3 — Nowcast

- **Odpowiedzialność:** wyłącznie korekta **bieżącej godziny** na podstawie świeżej
  obserwacji OWM: klasyfikacja zachmurzenia i notka o ukrytym opadzie.
- **Stan obecny:** `owm_nowcast` (`get_current_weather`, `classify_owm_cloud_correction`,
  `nowcast_note`); wynik żyje tylko w pamięci layoutu (`_cld_override` na `/now`,
  linia snapshotu na `/day`); payload niesie surowy snapshot `owm_current`.
- **Kontrakt docelowy:** nowcast zwraca jawny, testowalny wynik klasyfikacji
  (świeżość, model/live/efektywne chmury, decyzja, powód bramki); layout tylko go stosuje.
- **Niezmienniki:** nowcast **nigdy nie zapisuje** do kanonicznych godzin ani payloadu;
  nie dotyczy temperatury/wiatru/opadów w rekordach; `/future` go nie konsumuje;
  nieswieży snapshot = brak korekty, nigdy korekta „na zapas".

### Warstwa 4 — Coast

- **Odpowiedzialność:** kontekst geograficzny (sygnatura `is_coastal`,
  `distance_to_ocean_km`, `sea_sectors`) i reguły alertów wiatru od morza
  (`marine_storm`, `beach`) dla `/day` i `/now`.
- **Stan obecny:** `coast_detector`/`coast_runtime` + cache `coast_cache.json`;
  konsumenci w `prepare_layout` i `prepare_now_layout`; `/future` bez coast.
- **Kontrakt docelowy:** coast dostarcza polityce konsumenckiej wyłącznie gotowe
  zdarzenia kontekstowe (tryb, godziny, wartości wiatru), z własną wersją sygnatury.
- **Niezmienniki:** coast nigdy nie zmienia wartości pogodowych; brak wpisu cache
  przy istniejącym wpisie nie jest błędem; globalny cache nie jest daną jednej lokalizacji.

### Warstwa 5 — Model comparison diagnostics

- **Odpowiedzialność:** wyłącznie **porównanie modeli**: rozbieżności temperatur
  OM↔Yr.no, pokrycie, statusy. Diagnostyka niczego nie prognozuje i niczego nie renderuje.
- **Stan obecny:** dwa zapisy równolegle — `daily_diag` (legacy: ekstrema z wartości
  kanonicznych, zapisywany też do `audit_spread.jsonl`) oraz `daily_diag_matched`
  (surowe pola, dopasowanie po pełnym timestampie, `coverage_status`,
  `observation_only=True`, bez konsumentów).
- **Kontrakt docelowy:** matched jest jedyną referencyjną metodą porównania (D-TERAZ-1);
  wynik per dzień zawiera co najmniej: liczby rekordów/timestampów obu modeli,
  liczbę dopasowanych par, `max_abs_spread_c`, `mean_abs_spread_c` i `coverage_status`
  o semantyce z kontraktu (zadanie Z1.2). Legacy pozostaje tylko do odtworzenia historii.
- **Niezmienniki:** diagnostyka nie czyta pól kanonicznych jako dowodu wartości modelu OM;
  brak par = `None`/status, **nigdy zero**; żaden wynik diagnostyki nie wpływa na warstwy 1–4.

### Warstwa 6 — Consumer policy

- **Odpowiedzialność:** **jedyne miejsce decyzji konsumenckich**: kto (który konsument)
  może użyć którego sygnału, przy jakim pokryciu, horyzoncie i progach; kwalifikacja
  sygnałów (np. przyszły odpowiednik volatility na matched); wybór źródła dnia.
- **Stan obecny:** polityki są rozproszone i ukryte u konsumentów (`daily_source` próg
  `n_yr≥18`, bramki w `prepare_future_layout`, alert weekendowy w `prepare_layout`,
  bramki OWM w `owm_nowcast`) — to dlatego martwe ścieżki (P4) nie były widoczne.
- **Kontrakt docelowy:** każdy konsument dostaje z tej warstwy gotową, nazwaną decyzję
  (np. „sygnał kwalifikowany / niekwalifikowany + powód"), a nie surowe liczby.
- **Niezmiennik:** żaden próg nie istnieje poza tą warstwą; zmiana progu = jedna
  decyzja w jednym miejscu, z telemetrią przed/po. **Na tym etapie warstwa nie przełącza
  żadnego konsumenta** — porządkuje jedynie własność decyzji na przyszłość.

### Warstwa 7 — Rendering and alerts

- **Odpowiedzialność:** layouty `/day`, `/now`, `/future`, renderer PNG, alerty karty,
  WK oraz prezentacja astronomiczna (fazy Księżyca).
- **Stan obecny:** layouty miejscami same liczą politykę (patrz warstwa 6); renderer
  czyta `diag_tag`/`moon_phase` z tych samych wierszy dni, przez co tag diagnostyczny
  ginie (P4), a ikony Księżyca dzielą kanał wizualny z potencjalnymi oznaczeniami ⚠️ (P6).
- **Kontrakt docelowy:** renderer rysuje wyłącznie gotowe pola layoutu; każde pole
  layoutu ma znanego producenta (warstwa 6 dla sygnałów pogodowych, astronomia dla faz);
  oznaczenia diagnostyczne — jeśli kiedykolwiek powstaną — mają własny, odrębny od
  księżycowego kanał prezentacji (decyzja D-SHADOW-2).
- **Niezmienniki:** renderer nie interpretuje surowej diagnostyki; fazy Księżyca nie
  zależą od pogody ani diagnostyki; alert nie powstaje w rendererze.

---

## 5. Plan prac w kolejności (z bramkami)

Kolejność jest wiążąca: **nie wolno zaczynać etapu n+1 przed akceptacją etapu n**,
jeśli zadania etapu n+1 zależą od jego kontraktu. Szczegóły zadań wraz z wymaganymi
polami są w §6; tu jest mapa etapów i bramki wyjścia.

```
Etap 1  Kontrakt danych i statusy coverage          → Bramka A: kontrakt zaakceptowany
Etap 2  Testy regresyjne (charakterystyka stanu)    → Bramka B: regresja zielona
Etap 3  Testy aktywnego OWM                         → Bramka C: korekta zamrożona testami
Etap 4  Shadow mode + dane UX                       → Bramka D: raport shadow i wnioski UX
Etap 5  Decyzja o progach i konsumentach            → Bramka E: osobne ADR per konsument
```

- **Bramka A:** zaakceptowana na piśmie specyfikacja: kontrakt warstw, semantyka
  `coverage_status`, rejestr konsumentów. Bez tego nie powstaje żaden nowy sygnał.
- **Bramka B:** testy charakteryzujące obecne zachowanie (w tym martwe ścieżki jako
  zachowanie **udokumentowane**, nie „poprawione po cichu") przechodzą w CI.
- **Bramka C:** aktywna korekta OWM ma pełną macierz testów (§6, Z3.x); dopiero wtedy
  wolno ruszać jej bramki — i tylko przez osobną decyzję.
- **Bramka D:** raport shadow zawiera dystrybucje per status/horyzont/lokalizacja,
  listę rozbieżności legacy vs kandydat oraz wyniki testów zrozumienia prototypów kart.
- **Bramka E:** dla każdego kandydującego konsumenta osobna decyzja (ADR) z progami,
  planem wdrożenia za flagą i planem wycofania. Brak decyzji zbiorczej „przełączamy diagnostykę".

---

## 6. Zadania

> **NOTA ARCHIWALNA (dodana przy utrwaleniu w repozytorium):** archiwum źródłowe
> dokumentu decyzji urywa się w tym miejscu — szczegółowy wykaz zadań etapu 1–5
> (w dokumencie roboczym oznaczany jako Z1.1–Z5.3) oraz końcowa lista zakazów nie
> zachowały się w żadnym z dostępnych archiwów i **nie zostały odtworzone**,
> żeby nie przypisywać dokumentowi treści, której nie da się zweryfikować.
> Zachowane części dokumentu (problemy P1–P7, decyzje D-TERAZ i D-SHADOW, model
> 7 warstw oraz mapa etapów i bramek A–E w §5) są kompletne; akceptacja D-TERAZ
> nie zależy od brakującego wykazu. Przed rozpoczęciem etapu 2 wykaz zadań należy
> odtworzyć na podstawie §5 niniejszego dokumentu oraz planu realizacji etapu 1.

Legenda kolumn: **Payload/Karta/Alerty** = czy wykonanie zadania zmienia odpowiednio:
strukturę lub wartości payloadu produkcyjnego, wygląd kart dla użytkownika, treść lub
występowanie alertów. „Nie" oznacza twardy wymóg zadania. [Treść wykazu zadań nie
zachowała się w archiwum źródłowym — patrz nota archiwalna powyżej.]