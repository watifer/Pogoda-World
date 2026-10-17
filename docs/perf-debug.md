# Tymczasowa instrumentacja PERF_DEBUG

Wyłącznie diagnostyka, bez optymalizacji i bez zmiany polityk, kolejności wywołań,
cache/TTL, retry, timeoutów, komunikatów ani wyboru źródeł. Domyślnie wyłączona.

## Uruchomienie

PowerShell (ustaw zmienną w procesie, z którego uruchamiasz bota):

```powershell
$env:PERF_DEBUG = "1"
python main_card.py --smoke <TEST_CHAT_ID> --now
# albo --future; bez flag pozostaje dotychczasowa karta dzienna

# Wyłączenie po pomiarach:
$env:PERF_DEBUG = "0"
```

Dla obsługi Telegrama tę samą zmienną musi otrzymać proces `location_bot.py`.
Wyłącznie dokładne `PERF_DEBUG=1` włącza nowe pomiary. Stan jest wybierany na
wejściu do poll/smoke/card; nie przełączaj go w trakcie pojedynczego requestu.
Przy wyłączonym trybie nie są generowane identyfikatory, odczytywane nowe zegary
ani emitowane nowe rekordy PERF. Istniejące timery i logi aplikacji nie zmieniają się.

Ta zmiana **nie naprawia kwalifikacji użytkownika w smoke**. Smoke nadal stosuje
istniejący parser Users schedulera, w tym wymóg godzin raportów. Jeśli odrzuci
rekord, zobaczysz snapshot Users i `smoke_after` z `skipped`, ale bez pomiarów
pogody/renderu. Instrumentacja nie zmienia tej decyzji.

## Format i ochrona danych

Każdy nowy rekord to jedna linia: prefiks `PERF_DEBUG ` i obiekt JSON.

```json
{"request_id":"93df55a2d31544ab9c70c46670ce8074","operation":"weather.airly","stage":"weather_source_after","elapsed_ms":0.019,"outcome":"skipped","cache":"not_applicable"}
```

Pola:

- `request_id`: losowe UUID4 hex, niezależne od chat_id/update_id/wiadomości;
- `operation`, `stage`: stałe etykiety, nigdy nazwa komendy, miejscowość ani URL;
- `elapsed_ms`: wyłącznie różnica odczytów `time.monotonic_ns()` / 1 000 000;
- `outcome`: `started` przed operacją; po niej `ok`, `error`, `skipped`,
  `unavailable` (brak danych źródła). `ok` oznacza ukończenie obserwowanego etapu,
  nie gwarancję dostarczenia karty do urządzenia odbiorcy;
- `cache`: `hit`/`miss`, gdy zaobserwowano istniejącą decyzję cache;
  `not_applicable`, gdy nie ma takiego lookupu; `unknown` przed rozstrzygnięciem;
- `poll_request_id`: opcjonalny losowy identyfikator wspólnego pollu, łączący
  wcześniejszy bootstrap Bot_State z późniejszymi requestami update'ów.

Rekordy snapshotów dodatkowo zawierają jawne pola `snapshot_users_cache`,
`snapshot_users_outcome` lub analogiczne `snapshot_bot_state_*`. Ich wartości
końcowe odczytuj ze znacznika `*_after`; przed wykonaniem stan jest jeszcze
`unknown`/`started`.

Nowe rekordy nie serializują argumentów, wyników, wyjątków, nagłówków, URL-i,
tokenów, chat_id, współrzędnych, nazw miejsc ani treści wiadomości. Błąd wyjścia
loggera nie przerywa obserwowanej operacji.

**Istniejące logi aplikacji nie są objęte redakcją w tym zadaniu** i mogą zawierać
identyfikatory/lokalizację/treść wyjątku. Do udostępniania wybieraj tylko linie
zaczynające się od `PERF_DEBUG `, nie cały stdout/stderr. Nie włączaj przy okazji
`OWM_DEBUG` ani debugowego eksportu payloadów.

## Jak czytać chronologię

1. `update_received`: po odkodowaniu odpowiedzi getUpdates, przed Users. To czas
   lokalnego odbioru, nie wysłania wiadomości przez użytkownika; nie obejmuje
   wcześniejszego long-pollingu.
2. `snapshot_users_before/after`: całość istniejącego loadera (otwarcie zakładki,
   odczyt wartości, parsowanie lub trafienie w TTL). Jedna rzeczywista operacja
   dla paczki; jej pomiar jest przypisany do każdego update'u tej paczki. Nie
   sumuj tych powielonych rekordów jako dodatkowych odczytów Sheets.
3. `dispatcher_started.elapsed_ms`: od lokalnego odebrania update'u do jego
   dispatchera, łącznie ze snapshotem i ewentualnym oczekiwaniem na wcześniejsze
   update'y w tej samej paczce.
4. `dispatcher_before/after`: czas obsługi konkretnego update'u bez wcześniejszego
   snapshotu. Bramka ciszy może zakończyć go jako `skipped`.
5. `waiting_message_before/after`: tylko istniejące wiadomości oczekiwania;
   `waiting_message_http_before/after` mierzy ich transport, jeśli faktycznie nastąpił.
6. `geocode_before/after`: `operation` rozróżnia forward, reverse, blok gościa
   i cache nazw gościa. Zakresy mogą być zagnieżdżone — nie sumuj czasów rodzica
   i dziecka. Nie ponawiamy geokodowania dla pomiaru.
7. `payload_before/after`, a wewnątrz `weather_source_before/after` osobno dla
   `weather.openmeteo`, `weather.yrno`, `weather.airly`, `weather.owm`.
8. `layout_before/after`, `render_before/after`, `send_photo_before/after`.
   `send_photo_http_before/after` pokazuje granicę istniejącego transportu.

### Bot_State

`snapshot_bot_state_before/after` otacza uzyskanie offsetu przed getUpdates:

- pamięć procesu lub plik lokalny: `cache=hit`, `outcome=skipped` — nie czytano
  Bot_State;
- rzeczywisty bootstrap z Bot_State: `cache=miss`, `outcome=ok/error`.

Dzieje się to **przed otrzymaniem update'ów**, dlatego używa request_id pollu.
Pole `poll_request_id` pozwala później połączyć te rekordy z update'ami. Nie ma
fikcyjnego odczytu Bot_State dla każdej komendy. Lokalny zapis offsetu i zasady
bootstrapu pozostały niezmienione.

### Źródło a faktyczne HTTP, szczególnie Airly

Wejście do `_fetch_airly()` nie oznacza HTTP. Przy rzeczywiście pustym/brakującym
kluczu jest `weather_source_after: skipped` i **brak** `weather.airly.http`.
Instrumentacja nie zmienia ładowania `.env` ani interpretacji kluczy. Białe znaki
w kluczu nadal są interpretowane przez dotychczasowy kod — nie normalizujemy ich.

`weather_http_before/after` oznacza istniejące wywołanie `requests.get` lub
`Session.get`. Dla Airly mogą być dwa kolejne zakresy HTTP (instalacje i pomiary);
nie zapisujemy adresów, ID instalacji ani danych stacji. Dla Session.get zakres
obejmuje również istniejące retry adaptera, **nie** rozbija ich ani nie zmienia.

Open-Meteo: hit cache nie robi HTTP; aktywny circuit breaker daje `skipped`.
OWM: rozróżniamy oba istniejące cache, brak klucza/limit oraz wyłączenie źródła.
Przy wyłączonym OWM emitowany jest wyłącznie znacznik diagnostyczny `skipped`,
bez wejścia do źródła/HTTP. Błędy połykane przez istniejące handlery otrzymują
`error`, a zwracane wartości i zachowanie fallbacków pozostają takie jak wcześniej.

## Testy

```sh
python -m pip install -r requirements-dev.txt
PERF_DEBUG=0 python -m pytest -q -rs
PERF_DEBUG=1 python -m pytest -q -rs
python -m pytest -q test_perf_debug.py
```

Testy korzystają z istniejącej blokady socketów (`pytest.ini`, pytest-socket),
sztucznych profili i odpowiedzi HTTP. Nie uruchamiają produkcyjnego smoke testu
ani rzeczywistych usług. Obejmują m.in. wyłączenie bez zegara/UUID, niezmienione
wyniki/kolejność/parametry, końcowe znaczniki błędów, bezpieczny schemat logów,
paczkę update'ów z jednym odczytem Users, TTL, Bot_State, źródła pogody, geokodowanie,
obsługę day/now/future, skróty i smoke. Brak pomiarów Formularza.

Instrumentacja jest przeznaczona do krótkiego zebrania próbek. Samo logowanie ma
koszt, więc z czasów testów ani pojedynczego przebiegu nie wyciągamy wniosków o
przyspieszeniu produkcji. Na tym etapie nie zmieniamy żadnego mechanizmu pogody.
