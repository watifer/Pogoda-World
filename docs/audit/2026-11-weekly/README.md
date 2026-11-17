# Audyt tygodniowy 2026-11-09…17 — pakiet produkcyjny Pogoda-World

Ten katalog utrwala w repozytorium wyniki zakończonego, read-only audytu
tygodniowego pakietu produkcyjnego. Zawiera wyłącznie dokumenty tekstowe —
bez danych pogodowych, kart, logów ani plików roboczych.

## Status

- **Audyt: ZAKOŃCZONY.** Nie zbiera się kolejnego pakietu danych.
- **Brak zmian produkcyjnych.** Audyt nie zmienił kodu, konfiguracji, payloadów,
  kart ani alertów; jedyna zmiana w repozytorium to dodanie tego katalogu dokumentacji
  (stan repozytorium w momencie archiwizacji — patrz też sekcja
  „Stan repozytorium a treść archiwum" poniżej).
- **Dokument architektoniczny: zaakceptowany wyłącznie w zakresie D-TERAZ** (§2
  `dokument_decyzji_architektonicznej.md` — decyzje zamrażające/dokumentacyjne).
  **Decyzje D-SHADOW (§3) pozostają odroczone** i wymagają shadow mode oraz danych UX.
- **`daily_diag_matched` pozostaje offline/shadow** (`observation_only=True`) —
  nie jest podłączony do żadnego konsumenta produkcyjnego i nie wolno go
  przełączać bez osobnej decyzji po shadow mode.
- **`is_volatile`, WK (worth knowing) i alerty pozostają bez zmian**, tak samo
  progi, wybór źródła dnia w `daily_source`, reguły korekty OWM, reguły coast
  i rendering kart.

## Stan repozytorium a treść archiwum

Wzmianki o stanie repozytorium w dokumentach tego katalogu odnoszą się do dwóch
różnych momentów i nie należy ich mieszać:

- **Analiza offline:** stan historyczny wskazany w raporcie końcowym — repozytorium
  w momencie analizy było bez zmian (`git status` czysty, HEAD `33f0662`), a audyt nie
  wprowadzał żadnych commitów, pushów ani PR.
- **Archiwizacja dokumentów:** zapis tego katalogu w repozytorium nastąpił wyłącznie
  commitami dokumentacyjnymi: `314cdf0` (pierwsza archiwizacja), `61c92ba`
  (nota archiwalna), `7155fc0` (archiwizacja noty o braku pełnej wersji i planu
  etapu 1) oraz `632f1fc` (korekta spójności metadanych); **aktualny końcowy
  commit archiwizacji: `632f1fc`**. Żaden z nich nie zmienia kodu aplikacji.
- **Bieżący stan archiwum:** repo czyste po `632f1fc`.

## Zakres audytu

- Okres i dane: pakiet kolektora z dni **2026-11-09…2026-11-17** (10 lokalizacji:
  Algier, Buenos Aires, Hel, Lagos, Las Vegas, Los Angeles, Punta Arenas,
  Rio de Janeiro, São Paulo, Wiązowna), sesje ok. 10:00 / 14:00 / 19:00 czasu PL
  oraz dogrywki 14:15 / 19:15 / 23:00 od 2026-11-14.
- Przeanalizowano **114 payloadów = 1710 payload-dni**, plik `audit_spread.jsonl`
  (**7955 linii**, z tego **5100 jednoznacznie dopasowanych** do payloadów),
  110 wpisów coast oraz — częściowo — karty i logi (szczegóły w `manifest.md`).
- Metoda: analizy offline na pobranych payloadach oraz przeliczenie layoutów
  `/day`, `/now`, `/future` prawdziwym kodem repozytorium (import modułów,
  bez sieci i bez modyfikacji kodu). Audyt był prowadzony w trzech sesjach;
  ten katalog zawiera raport końcowy (sesja 3, domykająca sesje 1–2).

## Najważniejsze ustalenia (skrót)

- Maskowanie starej diagnostyki: `spread=0` przy rzeczywistej różnicy matched
  w **954/5100** dopasowanych linii (w payloadach: 554/1710); stara volatility:
  **108** przypadków, z czego 67% przy <6 parach; rzeczywiste duże rozbieżności
  na wspólnych timestampach (≥4 °C przy ≥10 parach): **69** przypadków.
- Źródła `/future`: Yr.no zasila summary tylko +1 (zawsze) i +2 (zazwyczaj,
  poza Hel), od +3 Open-Meteo; marker źródła dnia i tag diagnostyczny nie
  docierają dziś do karty.
- Korekta OWM/nowcast: obserwacja bieżąca OpenWeatherMap, wyłącznie zachmurzenie
  bieżącej godziny (+ rzadka notka o opadzie); aktywna w 51/114 payloadów (45%);
  wpływ: `/now` — Hero i pierwszy blok, `/day` — linia „Teraz: …", `/future` — brak.
  Pola `*_eff` to filtr opadów widmowych, nie korekta satelitarna.
- Fazy Księżyca na `/future` są warstwą astronomiczną, oddzieloną od diagnostyki
  (pełnia 2026-11-24 w 108 layoutach, 0 niezgodności).
- Coast: wpisy stabilne i zgodne z geografią; wpływ na `/day` i `/now`,
  brak na `/future`; `*_coast_cache_all.json` to globalny cache pomocniczy.

## Ograniczenia

- Treść logów przeanalizowano dla 63/462 plików (2026-11-10…12); klasyfikacja
  pozostałych uruchomień oparta jest o nazwy i strukturę sesji.
- Karty oglądano tylko dla Wiązowny (8 PNG); wnioski o layoutach pozostałych
  lokalizacji oparto na przeliczeniu kodem repozytorium wszystkich payloadów.
- Zapisany payload pochodzi tylko z trybu `/day`; uruchomienia `/now` i `/future`
  dopasowywano czasowo (≤60 s).
- Pakiet nie zawiera payloadów 2 uruchomień z 2026-11-09 (sesja częściowa
  kolektora) oraz 6 kart PNG z tego dnia; wnioski o błędach aplikacji wysnuto
  wyłącznie tam, gdzie dowody to uzasadniały.

## Pliki w tym katalogu

| Plik | Zawartość |
|---|---|
| `README.md` | ten opis: zakres, status, ograniczenia |
| `raport_koncowy.md` | pełny raport końcowy audytu (sesja 3, domknięcie tygodnia) |
| `dokument_decyzji_architektonicznej.md` | decyzje D-TERAZ (zaakceptowane), D-SHADOW (odroczone), model 7 warstw, mapa etapów 1–5 z bramkami. **Uwaga:** szczegółowy wykaz zadań z §6 nie zachował się w archiwum źródłowym (nota archiwalna w dokumencie) |
| `manifest.md` | lista głównych analizowanych artefaktów i ich statusu (także tych poza repozytorium) |
| `plan_etapu1.md` | plan realizacji etapu 1 (kontrakt danych i statusy coverage): inwentaryzacja plików/funkcji/testów, mapowanie na 7 warstw, kontrakty danych, raport w czterech kategoriach wdrożeniowych; wyłącznie planowanie, bez wdrożeń |
| `brak_pełnej_wersji_dokumentu.md` | nota o poszukiwaniach pełnej wersji dokumentu decyzji: gdzie szukano, czego brakuje, które decyzje pozostają ważne |
| `rejestr_etapu1.md` | dokument kontraktowy etapu 1 (**nowy dokument — nie odzyskany**): kontrakty danych, semantyka `coverage_status`, rejestr konsumentów, mapowanie 7 warstw, kryteria bramki A; **ZAAKCEPTOWANY — bramka A (2026-11-17); etap 2 nierozpoczęty** |

## Czego tu nie ma (świadomie)

Payloady JSON, karty PNG/JPG, pełne logi, cache coast, skrypty i pliki robocze
analiz oraz jakiekolwiek dane osobowe lub sekrety pozostały poza repozytorium
(archiwum audytu) i nie są potrzebne do odtworzenia wniosków z raportu.
