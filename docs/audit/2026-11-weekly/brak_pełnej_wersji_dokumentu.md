# Nota: brak pełnej wersji dokumentu decyzji architektonicznej

## Stan faktyczny

`dokument_decyzji_architektonicznej.md` w tym katalogu jest **częściowy**. Tekst
urywa się w §6 „Zadania", zaraz po legendzie kolumn tabeli zadań, a po fragmencie
legendy występuje dosłowny artefakt `[truncated 15821 chars]`. W zachowanym
dokumencie **brakuje**:

- szczegółowego wykazu zadań etapów 1–5 (w dokumencie roboczym oznaczanych jako
  zadania Z1.1–Z5.3),
- końcowej listy zakazów (w sesji roboczej opisywanej jako §7 z listą zakazów
  obecnych).

Braku **nie odtworzono** i nie wolno go odtwarzać z domysłów. W §6 dokumentu
decyzji znajduje się nota archiwalna oznaczająca dokument jako niekompletny;
niniejsza nota utrwala wynik poszukiwań pełnej wersji.

## Gdzie szukano pełnej wersji

| Miejsce | Zakres przeszukania | Wynik |
|---|---|---|
| `/home/user/audit-tydzien/` (archiwum audytu) | całe drzewo: `raporty/` (raporty sesji 1–3, dokument decyzji, plan etapu 1), `dane/` (payloady, karty, logi, `audit_spread.jsonl`), skrypty i pliki wynikowe JSON/JSONL | tylko znana, ucięta kopia dokumentu decyzji (27 975 B) |
| Eksporty z wcześniejszych sesji | katalog `raporty/` zawiera wyłącznie raporty sesji 1–3, dokument decyzji i plan etapu 1 | brak innego eksportu dokumentu decyzji |
| Repozytorium Pogoda-World | kopia w `docs/audit/2026-11-weekly/`; historia `git log --all` dla tego katalogu | kopia jest pochodną źródła audytu (różni się tylko banerami statusu i notą archiwalną); w historii istnieją wyłącznie commity archiwizacji `314cdf0` i `61c92ba` — brak wcześniejszej, pełnej wersji |
| Historia lokalnych kopii | pliki `*.bak`, `*~`, `*.orig`, `*.swp`, ukryte pliki w archiwum audytu | nie znaleziono żadnej kopii zapasowej |
| Pliki zawierające zadania Z1.1–Z5.3 | wyszukiwanie tekstowe po `/home/user` (nazwy i treść: `Z1.1`…`Z5.3`, `Z1.1`, `Z5.3`) | jedyne wystąpienia identyfikatorów to odwołanie „zadanie Z1.2" w §4 zachowanego dokumentu oraz nota archiwalna; **wykazu zadań nie znaleziono** |
| Google Drive — po nazwie | zapytania o nazwy zawierające: `decyzji`, `architektonicznej`, `dokument`, `plan_etapu`, `etapu`, `decyzj`, `audyt`, `audit`, `raport`, `Pogoda` | brak dokumentu decyzji; dwa dokumenty ze stycznia 2026 o innej tematyce audytu nie dotyczą; plan etapu 1 nie występuje |
| Google Drive — po treści | pełnotekstowo: `Z1.1`, `Z5.3`, `coverage_status`, `daily_diag_matched`, `D-TERAZ` | 0 wyników |
| Google Drive — folder pakietu `Pogoda-Dane` | pełny listing (685 plików) wraz z podfolderem `logs` | wyłącznie payloady JSON, karty PNG/JPG, cache coast, `audit_spread.jsonl` i `collect_weather_samples.py`; brak jakichkolwiek dokumentów tekstowych |

Wniosek: pełna wersja dokumentu decyzji **nie istnieje** w żadnym z przeszukanych
miejsc. Nie jest to powód do powtarzania audytu — to brak pojedynczego artefaktu
dokumentacyjnego.

## Które decyzje pozostają ważne

Zachowana część dokumentu decyzji (§0–§5) jest kompletna i obowiązuje w całości:

- **problemy P1–P7** (§1),
- **decyzje D-TERAZ (§2) — zaakceptowane**: zamrożenie i dokumentowanie stanu
  obecnego bez zmiany zachowania produkcyjnego,
- **decyzje D-SHADOW (§3) — odroczone**: do czasu shadow mode i danych UX,
- **docelowy model 7 warstw** (§4),
- **mapa etapów 1–5 i bramki A–E** (§5).

**Akceptacja D-TERAZ nie zależy od brakującej listy zadań.** Akceptacja dotyczy
treści decyzji z §2, które zachowały się w całości; brakujący §6 to harmonogram
prac wynikający z tych decyzji, a nie ich treść. Statusy operacyjne pozostają
bez zmian: brak zmian produkcyjnych, `daily_diag_matched` pozostaje offline/shadow
(`observation_only=True`), `is_volatile`, WK i alerty bez zmian, progi bez zmian.

## Jak pracować dalej mimo braku

- `raport_koncowy.md` w tym katalogu jest kompletny i pozostaje źródłem prawdy
  o wynikach audytu.
- `plan_etapu1.md` w tym katalogu wynika z §5 dokumentu decyzji oraz z read-only
  rozpoznania kodu; **nie jest** kopią brakującego wykazu zadań Z1.1–Z5.3 i nie
  należy go tak traktować.
- Jeżeli wykaz zadań będzie potrzebny przed etapem 2, należy go utworzyć jako
  **nowy dokument** na podstawie raportu końcowego, §5 dokumentu decyzji i planu
  etapu 1 — z wyraźnym oznaczeniem, że jest to dokument nowy, a nie odzyskany
  oryginał.
