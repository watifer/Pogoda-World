"""Testy niezmienników karty — kontrakt SPÓJNOŚCI, nie konkretnych tekstów.

Batch 1 dał nam testy "czy funkcja X zwraca Y". Batch 2 goni inną klasę błędów:
ta sama wielkość liczona niezależnie w kilku miejscach karty, przez co karta
przeczy samej sobie (alert gołoledzi + suche hero + "(0.6 mm)" przy bezopadowej
ikonie; "silny wiatr" w hero i "wichura" w bloku tej samej godziny).

Dlatego tu NIE sprawdzamy wartości. Sprawdzamy relacje między elementami
gotowej karty, na wszystkich scenariuszach z dev_preview.py × 6 językach:

  1. alert/kontekst gołoledzi  => w oknie karty istnieje realny opad (mm > 0),
  2. tekst "(X mm)"            => ikona bloku jest opadowa,
  3. wiatr w hero              => nie niżej niż najwyższy blok godzinowy,
  4. brak U+FE0F (VS16)        => emoji-selektor rozwala metryki w PIL,
  5. lang != "pl"              => brak polskich diakrytyków w tekstach karty.

Scenariusze bierzemy z dev_preview.SCENARIOS, żeby jedno miejsce opisywało
pogodę testową dla podglądu i dla testów (w tym ghost_precip i gust_below_wind,
które odtwarzają oba rozjazdy z Batch 2).

Uruchomienie: pytest test_card_invariants.py -v
"""
import os
import re

import pytest

os.environ.pop("OWM_API_KEY", None)  # zero ruchu sieciowego w testach

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo

from datetime import datetime

import dev_preview
from dev_preview import SCENARIOS, build_payload
from prepare_layout import prepare_layout_data, precip_mm_for_ui, _eff_wind_kmh
from prepare_now_layout import prepare_now_layout_data
from i18n import t, translate_weather_text

LANGS = dev_preview.LANGS
SCEN_NAMES = sorted(SCENARIOS)
CARDS = ["now", "day"]

# "ó" celowo pominięte — występuje również w ES/FR ("Precipitación", "météo").
PL_DIACRITICS = re.compile(r"[ąćęłńśźż]", re.IGNORECASE)
VS16 = "\ufe0f"

# Ikony oznaczające opad. Jeśli w opisie bloku jest "(X mm)", ikona MUSI tu być.
PRECIP_ICONS = {
    "wk_rain", "wk_showers", "wk_showers_night", "wk_drizzle",
    "wk_snow", "wk_snow_showers", "wk_snow_showers_night",
    "wk_sleet", "wk_storm", "wk_sun_storm", "wk_wind_rain",
}

# Złota Skala Wiatru — próg -> polski termin bazowy (tłumaczony przez i18n).
WIND_TIERS = [(100, "potężna wichura"), (80, "wichura"), (60, "silny wiatr"), (40, "wietrznie")]

MM_RE = re.compile(r"\((\d+(?:[.,]\d+)?)\s*mm\)")
KMH_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*km/h")


# ─────────────────────────── helpers ───────────────────────────

def _render(scen_name, lang, card):
    scen = SCENARIOS[scen_name]
    tz = ZoneInfo(scen["loc"][3])
    now = datetime.strptime(scen["date"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    if card == "now":
        payload = build_payload(scen, lang, now, 24)
        data = prepare_now_layout_data(payload, now=now)
    else:
        payload = build_payload(scen, lang, now, 60)
        data = prepare_layout_data(payload, now=now)
    return data, payload, now


def _iter_strings(node):
    """Wszystkie stringi karty — karta miesza dict/list/str na kilku poziomach."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _iter_strings(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _iter_strings(v)


def _block_texts(block):
    out = [block.get("primary_desc") or ""]
    for ex in block.get("extra_lines") or []:
        if isinstance(ex, dict):
            out.append(ex.get("text") or "")
            for span in ex.get("spans") or []:
                out.append(span.get("text") or "")
        else:
            out.append(str(ex))
    return [x for x in out if x]


def _freezing_marker(lang):
    """Rozpoznawalny rdzeń komunikatu o gołoledzi, bez dynamicznego "({when})"."""
    tpl = t(lang, "freezing_alert", when="__WHEN__")
    return tpl.split("__WHEN__")[0].rstrip(" (").strip()


def _payload_max_mm(payload, hours_subset=None):
    hours = payload.get("hours", [])
    subset = hours_subset if hours_subset is not None else hours
    return max([precip_mm_for_ui(h, hours) for h in subset] or [0.0])


def _wind_word(kmh, lang):
    for thr, pl_word in WIND_TIERS:
        if kmh >= thr:
            return pl_word if lang == "pl" else translate_weather_text(pl_word, lang)
    return None


ALL_CASES = [(s, l, c) for s in SCEN_NAMES for l in LANGS for c in CARDS]
NOW_CASES = [(s, l) for s in SCEN_NAMES for l in LANGS]


@pytest.fixture(scope="module")
def rendered():
    """Renderujemy każdą kombinację raz — 11 scenariuszy × 6 języków × 2 karty."""
    return {(s, l, c): _render(s, l, c) for s, l, c in ALL_CASES}


# ───────────────────── 1. gołoledź => istnieje opad ─────────────────────

@pytest.mark.parametrize("scen,lang,card", ALL_CASES)
def test_freezing_alert_implies_precipitation(rendered, scen, lang, card):
    data, payload, _ = rendered[(scen, lang, card)]
    marker = _freezing_marker(lang)
    texts = [data.get("context_line") or ""] + list(data.get("alerts") or [])
    fired = any(marker and marker in txt for txt in texts)
    if not fired:
        return
    assert _payload_max_mm(payload) > 0, (
        f"{scen}/{lang}//{card}: alert gołoledzi bez ani jednej godziny z opadem "
        "wg precip_mm_for_ui() — detektor znowu czyta inne mm niż reszta karty"
    )


def test_ghost_precip_has_no_freezing_alert():
    """Regresja rozjazdu #1: 0.6 mm w jednym modelu przy POP 20% to duch."""
    for card in CARDS:
        data, _, _ = _render("ghost_precip", "pl", card)
        texts = [data.get("context_line") or ""] + list(data.get("alerts") or [])
        assert not any(_freezing_marker("pl") in x for x in texts), \
            f"/{card}: duch opadu nadal odpala alert gołoledzi"


# ───────────────────── 2. "(X mm)" => ikona opadowa ─────────────────────

@pytest.mark.parametrize("scen,lang,card", ALL_CASES)
def test_mm_in_text_implies_precip_icon(rendered, scen, lang, card):
    data, _, _ = rendered[(scen, lang, card)]
    for block in data.get("today_blocks") or []:
        for txt in _block_texts(block):
            m = MM_RE.search(txt)
            if not m:
                continue
            assert float(m.group(1).replace(",", ".")) > 0, \
                f"{scen}/{lang}//{card}: blok deklaruje '(0 mm)' — to nie jest opad"
            assert block.get("icon") in PRECIP_ICONS, (
                f"{scen}/{lang}//{card}: tekst '{txt}' mówi o mm, a ikona "
                f"'{block.get('icon')}' jest bezopadowa"
            )


# ───────────── 3. hero wiatr >= najwyższy blok godzinowy ─────────────

@pytest.mark.parametrize("scen,lang", NOW_CASES)
def test_hero_wind_not_below_hourly_blocks(rendered, scen, lang):
    """W /now hero skanuje 12 h, więc musi być >= każdego bloku z tego okna."""
    data, payload, _ = rendered[(scen, lang, "now")]
    hero = data.get("summary") or ""

    block_max = 0.0
    for block in data.get("today_blocks") or []:
        for txt in _block_texts(block):
            for val in KMH_RE.findall(txt):
                block_max = max(block_max, float(val.replace(",", ".")))

    expected = _wind_word(block_max, lang)
    if not expected or block_max < 60:
        return  # poniżej 60 km/h hero milczy z założenia
    assert expected.lower() in hero.lower(), (
        f"{scen}/{lang}: blok pokazuje {block_max:.0f} km/h ('{expected}'), "
        f"a hero mówi '{hero.splitlines()[-1] if hero else ''}'"
    )


def test_gust_below_wind_hero_matches_blocks():
    """Regresja rozjazdu #2: wiatr 85, poryw 70 — 'gust or wind' dawało 70."""
    data, payload, _ = _render("gust_below_wind", "pl", "now")
    assert max(_eff_wind_kmh(h) for h in payload["hours"]) == 85.0
    assert "wichura" in (data.get("summary") or ""), \
        "hero nadal liczy sam poryw zamiast max(wiatr, poryw)"
    assert "silny wiatr" not in (data.get("summary") or "")


# ───────────────────── 4. brak VS16 (U+FE0F) ─────────────────────

@pytest.mark.parametrize("scen,lang,card", ALL_CASES)
def test_no_variation_selector(rendered, scen, lang, card):
    data, _, _ = rendered[(scen, lang, card)]
    bad = [s for s in _iter_strings(data) if VS16 in s]
    assert not bad, f"{scen}/{lang}//{card}: U+FE0F w {bad[:2]}"


# ───────────── 5. brak polskich diakrytyków poza lang == "pl" ─────────────

@pytest.mark.parametrize("scen,lang,card", [c for c in ALL_CASES if c[1] != "pl"])
def test_no_polish_diacritics_in_foreign_langs(rendered, scen, lang, card):
    data, _, _ = rendered[(scen, lang, card)]
    # Nazwa lokalizacji jest własna ("Hel", "Warszawa") i nie podlega tłumaczeniu.
    checked = {k: v for k, v in data.items() if k not in ("city", "location", "location_name")}
    bad = [s for s in _iter_strings(checked) if PL_DIACRITICS.search(s)]
    assert not bad, f"{scen}/{lang}//{card}: nieprzetłumaczone PL w {bad[:3]}"


# ───────────── 6. bramka kosztowa OWM (wyjątek operacyjny) ─────────────
# OWM to płatny call w ścieżce użytkownika. Reguła "hidden drizzle" ma prawo go
# odpalić tylko wtedy, gdy ŻADEN model nie widzi opadu — sam konsensus UI nie
# wystarcza, bo "duch opadu" też daje 0.0 mm. To wyjątek operacyjny: surowe mm
# NIE trafiają stąd do żadnego tekstu, ikony ani alertu.

def test_models_raw_dry_sees_single_model_precip():
    from prepare_layout import _models_raw_dry
    om = {"time_local": "2026-01-15T12:00:00", "source": "openmeteo", "precip_eff_mm": 0.6}
    yr = {"time_local": "2026-01-15T12:00:00", "source": "yrno", "precip_eff_mm": 0.0}
    assert _models_raw_dry(om, [om, yr]) is False, "duch opadu to nie jest 'sucho'"
    assert _models_raw_dry(yr, [om, yr]) is False, "liczy się godzina, nie model wejściowy"

    dry_om = dict(om, precip_eff_mm=0.0)
    assert _models_raw_dry(dry_om, [dry_om, yr]) is True
    wet_edge = dict(om, precip_eff_mm=0.09)
    assert _models_raw_dry(wet_edge, [wet_edge, yr]) is True


def test_ghost_precip_does_not_trigger_owm_call(monkeypatch):
    """OM 0.6 mm @ POP 20% + Yr 0.0: konsensus 0.0, ale nie ma za co płacić za radar."""
    import prepare_layout

    calls = []
    monkeypatch.setattr(prepare_layout, "get_current_weather",
                        lambda *a, **kw: calls.append((a, kw)))
    _render("ghost_precip", "pl", "day")
    assert not calls, f"bramka 'hidden drizzle' odpaliła OWM na duchu opadu ({len(calls)}x)"


def test_genuinely_dry_and_damp_hour_still_triggers_owm(monkeypatch):
    """Kontrola czułości: gdy OBA modele są suche, a jest mokro i pochmurno — OWM leci."""
    import prepare_layout

    calls = []
    monkeypatch.setattr(prepare_layout, "get_current_weather",
                        lambda *a, **kw: calls.append((a, kw)))

    scen = dict(SCENARIOS["ghost_precip"])
    dry = {"temp_c": 0.3, "precip_mm": 0.0, "precip_eff_mm": 0.0, "precip_prob_pct": 20,
           "rh_pct": 96, "dewpoint_c": -0.1, "clouds_pct": 95.0, "clouds_low_pct": 85.0,
           "clouds_mid_pct": 10.0, "symbol_code": "cloudy", "weather_code": 3}
    scen["hours"] = lambda i: dry
    scen["alt_hours"] = lambda i: dry

    tz = ZoneInfo(scen["loc"][3])
    now = datetime.strptime(scen["date"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    prepare_layout.prepare_layout_data(build_payload(scen, "pl", now, 60), now=now)
    assert calls, "bramka 'hidden drizzle' przestała działać dla realnie suchej godziny"
