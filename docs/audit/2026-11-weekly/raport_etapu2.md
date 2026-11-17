# Etap 2 — zamrożenie obserwowanego zachowania testami charakteryzującymi

**Status:** zakończony
**Data:** 2026-11-17
**Gałąź:** `arena/2dc6d83d-pogoda-world` (bazuje na `staging` @ `275285b`)

## Cel

Zamrozić testami OBECNE, zaobserwowane zachowanie produkcyjne przed
porządkowaniem warstw (etap 3+), zgodnie z kontraktem `rejestr_etapu1.md`
i dokumentem decyzji architektonicznej. Etap 2 **nie zmienia zachowania
produkcyjnego** — dozwolone były wyłącznie nowe testy, fixture'y testowe
i dokumentacja testów.

## Zakres i pokrycie

| Obszar | Plik | Testy | Zakres |
|---|---|---|---|
| 1. OWM nowcast | `test_owm_nowcast.py` (nowy) | 69 | `classify_owm_cloud_correction` (braki danych, świeżość ±5400 s, timestamp s/ms, próg różnicy 24/25/26 pkt, etykiety nieba, bramka high-only, reguła UV ≥85% → 65%, noc), `nowcast_note` (0,1/0,2 mm, brak godziny, nieświeży), `owm_is_fresh`, `extract_owm_current`, `extract_owm_dt_seconds`, `find_model_hour_for_now`; integracja: /now (Hero + blok 0), /day (tylko linia „Teraz: …"), /future (brak wpływu) |
| 2. Wybór źródła dnia | `test_daily_source.py` (nowy) | 17 | granice `n_yr` 17/18 i liczby rekordów 5/6, priorytet Yr, fallback OM/`mixed`, kształt `DailyPick.source/marker`, przypadek Hel (+2 z 15 rekordami → OM), neutralność wobec `daily_diag_matched`; próg 18 nietknięty |
| 3. Confidence gate | `test_confidence_gate.py` (nowy) | 27 | pary 0/2/3, konflikty opadów (≥2 pary, pop ≥40%, oba kierunki), wiatru (Δ≥30 km/h i max ≥55), chmur (n≥4 i big ≥ max(2, n//2), fallbacki pól), okno 06–22, poza oknem, brak danych, `soften = is_volatile`, `hide = precip_hard`, notki PL/EN, niezależność `TrustReport.is_volatile` od legacy `is_volatile` |
| 4. Legacy `daily_diag` | `test_daily_diagnostics_legacy.py` (nowy) | 17 | ekstrema OM/Yr, brak modelu (spread=0.0 nie None), brak wspólnych timestampów (spread z ekstremów — charakterystyka legacy), reguła `spread_max ≥ 5.0` lub `spread_min ≥ 4.0`, kształt kluczy/typów, zapis `audit_spread.jsonl` wł./wył. |
| 5. Matched diagnostics | `test_daily_diagnostics_matched.py` (rozszerzony, +5) | 30 | regresja: `no_yr_records`, priorytety statusów, `None` nigdy `0.0` przy braku par, **suma `coverage_status_counts` = liczba dni**, stałe koperty (`coverage_threshold=None`, `observation_only=True`) |
| 6. Martwe ścieżki | `test_dead_paths.py` (nowy) | 16 | `diag_tag`/`diag_severity` liczone na summary, nie kopiowane do `day_entry`; renderer gotowy (plan + rysunek na syntetycznym fixture, opisane jako charakterystyka); marker ` *` w `name`, niewidoczny w `label` i na karcie; `apply_cloud_correction` bez wywołań (nie usuwany); `coastal_day_alert` nie ustawiany i bez wpływu; `model_agreement` zawsze `None` |
| 7. Niezależność warstw | `test_layer_independence.py` (nowy) | 12 | OWM ↔ /future (brak), coast ↔ /day+/now (alerty/kontekst) i ↔ /future (brak), coast nie zmienia wartości pogodowych, cache coast kluczowany per lokalizacja, Księżyc niezależny od `daily_diag`/volatility, tag ≠ ikona Księżyca |

**Razem nowe testy: 163** — `test_owm_nowcast` 69, `test_daily_source` 17,
`test_confidence_gate` 27, `test_daily_diagnostics_legacy` 17,
`test_dead_paths` 16, `test_layer_independence` 12 oraz **+5 regresji**
w istniejącym `test_daily_diagnostics_matched` (25 → 30 testów).

## Wynik

- Baseline (przed etapem 2): **3931 passed, 3 skipped** (15,3 s)
- Po etapie 2: **4094 passed, 3 skipped** (14,6 s) — **+163 testy**
- Zmiany wyłącznie w testach: `git status` pokazuje tylko `test_*.py`
- Zero zmian w kodzie produkcyjnym, zero kart PNG/JPG, zero sekretów, zero sieci
  (`pytest.ini` wymusza `--disable-socket`; testy mockują OWM/coast/fetchery)

## Findings zapisane jako charakterystyki (bez napraw w etapie 2)

1. **`extract_owm_current({"data": []})`** zwraca opakowanie zamiast `None`;
   nie-dict wejście podnosi `AttributeError`. Konsumenci zawsze podają dict.
2. **`pick_hours_for_daily_summary(..., daily_diag=None, ...)`** podnosi
   `AttributeError` — funkcja nie toleruje `None` (konsumenci zawsze dict).
3. **`build_daily_diagnostics`** przy `temp_c` będącej nie-parsowalnym stringiem
   podnosi `ValueError`; pomijane jest tylko `None` i brak pola.
4. **`sky_from_clouds` w `forecast_text.py`** i **`apply_cloud_correction`**
   w `owm_nowcast.py` pozostają bez wywołań produkcyjnych (nie usuwane —
   zgodnie z decyzją etapu 1).
5. Marker ` *` (fallback źródła) jest zamrożony jako niewidoczny na karcie —
   renderer czyta `label`, nie `name`. To charakterystyka, nie docelowy UX.

Żaden test nie wymagał osłabienia asercji istniejących; żaden test nie
zakończył się niepowodzeniem z winy kodu produkcyjnego (znalezione brzegowe
zachowania opisano jako charakterystyki zgodnie z wytyczną etapu 2).

## Co dalej (poza tym etapem)

Etapy 3+ (porządkowanie warstw, decyzje o martwych ścieżkach, shadow mode)
nie były rozpoczynane — zgodnie z zakresem zatrzymano się po zweryfikowaniu
testów etapu 2.
