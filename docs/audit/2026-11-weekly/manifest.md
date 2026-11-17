# Manifest artefaktów audytu tygodniowego 2026-11-09…17

Lista głównych analizowanych artefaktów i ich statusu. Kolumna „W repozytorium"
mówi, czy artefakt znajduje się w tym katalogu, czy pozostał w archiwum audytu
poza repozytorium (celowo — patrz `README.md`, sekcja „Czego tu nie ma").

Archiwizacja dokumentów tego katalogu: commity archiwizacji `314cdf0` (pierwsza
archiwizacja), `61c92ba` (nota archiwalna), `7155fc0` (archiwizacja noty o braku
pełnej wersji i planu etapu 1) oraz `632f1fc` (korekta spójności metadanych);
aktualny końcowy commit archiwizacji: `632f1fc`; bieżący stan archiwum: repo
czyste po `632f1fc`. Stan repozytorium w momencie analizy offline opisuje raport
końcowy — rozróżnienie momentów: `README.md`, sekcja „Stan repozytorium
a treść archiwum".

## Dokumenty

| Artefakt | Zakres / liczba | Status analizy | W repozytorium |
|---|---|---|---|
| `raport_koncowy.md` (pierwotnie raport sesji 3) | pełny raport końcowy audytu | Zakończona; domyka sesje 1–2 | **Tak** (ten katalog) |
| `dokument_decyzji_architektonicznej.md` | decyzje D-TERAZ / D-SHADOW, model 7 warstw, mapa etapów 1–5 | D-TERAZ zaakceptowane; D-SHADOW odroczone. Wykaz zadań §6 niekompletny w źródle (nota archiwalna w dokumencie) | **Tak** (ten katalog) |
| `README.md` | zakres, status, ograniczenia audytu | Aktualny na dzień utrwalenia | **Tak** (ten katalog) |
| Raporty sesji 1 i 2 (`raport_sesja1.md`, `raport_sesja2.md`) | wyniki częściowe sprzed domknięcia tygodnia | Historyczne; wnioski przejęte i miejscami skorygowane przez raport końcowy (m.in. fazy Księżyca, liczba logów 462) | Nie (archiwum audytu) |
| `plan_etapu1.md` | plan realizacji etapu 1 na podstawie dokumentu decyzji i rozpoznania kodu | Zapisany jako planowanie; żadne zadanie nie zostało wdrożone | **Tak** (ten katalog) |
| `brak_pełnej_wersji_dokumentu.md` | nota o poszukiwaniach pełnej wersji dokumentu decyzji (archiwum lokalne, historia git, Google Drive) | Pełnej wersji nie znaleziono; braku nie odtworzono | **Tak** (ten katalog) |
| Manifest roboczy audytu (`manifest.json`, 114 runów / 33 sesje kolektora + 10 sesji tylko-logowych) | inwentaryzacja całego pakietu | Zakończona; źródło rozliczeń w raportach | Nie (archiwum audytu) |

## Dane wejściowe pakietu (przeanalizowane)

| Artefakt | Zakres / liczba | Status analizy | W repozytorium |
|---|---|---|---|
| Payloady pogodowe JSON (`debug_pogoda.json` z trybu `/day`) | **114 runów = 1710 payload-dni** (10 lokalizacji × sesje 2026-11-09…17) | Przeanalizowane w 100%: diagnostyka `daily_diag` i `daily_diag_matched`, źródła godzin, `owm_current`, alerty, Airly, UV | Nie (rozmiar i charakter danych) |
| `audit_spread.jsonl` | **7955 linii, 531 partii**, fetched 2026-10-30…2026-11-17 | Zinwentaryzowany; **5100 linii jednoznacznie dopasowanych** do payloadów (1710 co do sekundy, reszta ≤60 s); 0 duplikatów; 2 partie niepełne (po 10/15) spoza tygodnia | Nie |
| Wpisy coast (`*_coast.json`) | **110/114 runów**, 10 lokalizacji | Przeanalizowane: po 1 stabilnym wariancie sygnatury na lokalizację | Nie |
| Globalny cache coast (`*_coast_cache_all.json`, luźny `Lagos 9-11 coast.json`) | kopie tego samego cache (1 hash treści) | Zidentyfikowany jako cache pomocniczy, nie dane jednej lokalizacji | Nie |
| Karty PNG (kolektor, 3 tryby × run) | 336 w pakiecie | Obejrzano 8 (Wiązowna `/day`, `/now`, `/future` z 2026-11-10, -12, -16); brakuje 6 PNG z 2026-11-09 | Nie (pliki graficzne) |
| Ręczne karty JPG z 2026-11-09 | 8 plików | Zinwentaryzowane; brak 1 (Hel `/now`) | Nie |
| Logi kolektora | **462 pliki** (154 run-lokacje × 3 tryby) | Treść: 63/462 (2026-11-10…12, wszystkie zakończone wysyłką karty, 0 błędów); reszta: inwentaryzacja po nazwach/sesjach | Nie |
| Luźne pliki pakietu | 2 payloady ręczne (Lagos, Wiązowna 2026-11-09) — włączone do 114; `collect_weather_samples.py` — opis kolektora; `nowy 301.json` — notatka tekstowa z błędnym rozszerzeniem, nie JSON, poza zakresem | Rozliczone w raporcie końcowym | Nie |

## Artefakty pochodne audytu

| Artefakt | Zawartość | Status | W repozytorium |
|---|---|---|---|
| Layouty przeliczone kodem repozytorium (`/day`, `/now`, `/future` dla 114 payloadów) | weryfikacja źródeł dni, tagów, faz Księżyca, wpływu `daily_diag_matched` | Wyniki liczbowe w raporcie końcowym (m.in. inwariantność layoutu 114/114) | Nie (efemeryczne wyniki analiz) |
| Ablacja korekty OWM (10 payloadów z aktywną korektą) | porównanie layoutów z `owm_current` i bez | Wyniki w raporcie końcowym (`/now`: Hero+1 blok; `/day`: tylko linia snapshotu) | Nie |
| Statystyki dzienne (`dni.jsonl`: 1710 wierszy dzień-poziom; `statystyki.json`) | statusy pokrycia, rozrzut wg par, maskowanie, powtarzalność 1486 par runów | Podstawa tabel raportu końcowego | Nie (pliki robocze) |
| Zapis klasyfikacji logów (`logi_wyniki.json`, SHA-256 i czasy 63 logów) | klasyfikacja uruchomień 2026-11-10…12 | 0 duplikatów treści; szczegóły w raportach sesji | Nie |

## Braki pakietu (dla kompletności)

| Brak | Dotyczy | Klasyfikacja z audytu |
|---|---|---|
| 2 payloady (Rio de Janeiro, Punta Arenas) | sesja częściowa kolektora 2026-11-09 14:00 | `partial_run` po stronie kolektora/archiwizacji; brak dowodu błędu aplikacji |
| 6 kart PNG (sesja 2026-11-09) + 1 ręczny JPG (Hel `/now`) | jw. | jak wyżej |
| Treść 399/462 logów | dni 2026-11-09 oraz 13…17 | Nieprzeanalizowana (inwentaryzacja po nazwach); wnioski raportu jej nie dotyczą |
| Karty lokalizacji nadmorskich | próba kart tylko dla Wiązowny (śródlądowa) | Wpływ coast oceniony z kodu i cache, nie z kart |
