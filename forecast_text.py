"""
forecast_text.py — Deterministyczny generator mikroopisów pogodowych.
Wersja: 3.2 (i18n)

Poprawki vs 3.1:
1. Wprowadzono wsparcie i18n przez funkcję t()
2. Słownik KINDS i FAMILY_LABEL został wyposażony w klucze zamiast twardych stringów
3. Funkcje formatujące "od", "do", "po" otrzymały wsparcie wielojęzyczne
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from i18n import t

# ═══════════════════════════════════════
# MODELE DANYCH
# ═══════════════════════════════════════

@dataclass
class WxEvent:
    kind: str
    start: int
    end: int
    severity: int = 1

@dataclass
class BlockForecast:
    label: str
    hours_range: str
    start: int
    end: int
    temp_min: int
    temp_max: int
    feels_min: Optional[int] = None
    feels_max: Optional[int] = None
    sky_label: Optional[str] = None
    max_wind: Optional[float] = None
    events: List[WxEvent] = field(default_factory=list)


# ═══════════════════════════════════════
# SŁOWNIK ZJAWISK (Teraz używa KLUCZY z i18n)
# ═══════════════════════════════════════

# Słowniki mają teraz klucze, a nie twarde polskie teksty. Tłumaczenie odbywa się w "describe_precip"
KINDS = {
    "drizzle":    {"family": "rain",  "full_key": "drizzle",      "short_key": "drizzle",    "severity": 1},
    "light_rain": {"family": "rain",  "full_key": "light_rain",   "short_key": "rain",       "severity": 2},
    "rain":       {"family": "rain",  "full_key": "rain",         "short_key": "rain",       "severity": 3},
    "heavy_rain": {"family": "rain",  "full_key": "heavy_rain",   "short_key": "rain",       "severity": 4},
    "downpour":   {"family": "rain",  "full_key": "downpour",     "short_key": "downpour",   "severity": 5},
    "light_snow": {"family": "snow",  "full_key": "light_snow",   "short_key": "snow",       "severity": 2},
    "snow":       {"family": "snow",  "full_key": "snow",         "short_key": "snow",       "severity": 3},
    "heavy_snow": {"family": "snow",  "full_key": "heavy_snow",   "short_key": "snow",       "severity": 4},
    "sleet":      {"family": "mixed", "full_key": "sleet",        "short_key": "sleet_short","severity": 3},
    "storm":      {"family": "storm", "full_key": "storms",       "short_key": "storms",     "severity": 5},
    "fog":        {"family": "fog",   "full_key": "fog",          "short_key": "fog",        "severity": 2},
}

FAMILY_LABEL = {
    "rain": "rain", "snow": "snow",
    "mixed": "sleet", "storm": "storms", "fog": "fog",
}

PRECIP_FAMILIES = {"rain", "snow", "mixed", "storm"}

# ═══════════════════════════════════════
# KLASYFIKATOR
# ═══════════════════════════════════════

SYMBOL_TO_KIND = {
    "lightrain": "light_rain", "rain": "rain", "heavyrain": "heavy_rain",
    "lightsleet": "sleet", "sleet": "sleet",
    "lightsnow": "light_snow", "snow": "snow", "heavysnow": "heavy_snow",
    "fog": "fog",
    "lightrainshowers": "light_rain", "rainshowers": "rain", "heavyrainshowers": "heavy_rain",
    "lightsleetshowers": "sleet", "sleetshowers": "sleet",
    "lightsnowshowers": "light_snow", "snowshowers": "snow", "heavysnowshowers": "heavy_snow",
    "rainandthunder": "storm", "heavyrainandthunder": "storm", "lightrainandthunder": "storm",
    "sleetandthunder": "storm", "snowandthunder": "storm",
    "lightrainshowersandthunder": "storm", "rainshowersandthunder": "storm",
    "heavyrainshowersandthunder": "storm",
}

WMO_TO_KIND = {
    51: "drizzle", 53: "drizzle", 55: "light_rain",
    56: "sleet", 57: "sleet",
    61: "light_rain", 63: "rain", 65: "heavy_rain",
    66: "sleet", 67: "sleet",
    71: "light_snow", 73: "snow", 75: "heavy_snow", 77: "light_snow",
    80: "light_rain", 81: "rain", 82: "downpour",
    85: "light_snow", 86: "heavy_snow",
    95: "storm", 96: "storm", 99: "storm",
    45: "fog", 48: "fog",
}


def classify_from_api(symbol_code=None, weather_code=None):
    if symbol_code is not None:
        base = symbol_code.split("_")[0].lower()
        kind = SYMBOL_TO_KIND.get(base)
        if kind is not None:
            return kind
    if weather_code is not None:
        kind = WMO_TO_KIND.get(weather_code)
        if kind is not None:
            return kind
    return None


def classify_precip(mm, temp_c, symbol_code=None, weather_code=None):
    api = classify_from_api(symbol_code, weather_code)
    mm = float(mm or 0)
    temp_c = float(temp_c if temp_c is not None else 10)
    
    if api is not None:
        fam = KINDS.get(api, {}).get("family")
        if fam in PRECIP_FAMILIES and fam != "storm" and mm < 0.1:
            return None
        if fam == "rain":
            if temp_c <= -1.0:
                if 0 < mm < 2: return "light_snow"
                elif mm >= 5: return "heavy_snow"
                else: return "snow"
            elif -1.0 < temp_c <= 2.0:
                return "sleet"
        return api

    if mm < 0.1:
        return None
        
    if temp_c <= 2.0:
        if mm < 2: return "light_snow"
        elif mm < 5: return "snow"
        elif mm < 10: return "heavy_snow"
        else: return "heavy_snow"
    else:
        if mm < 0.5: return "drizzle"
        elif mm < 2: return "light_rain"
        elif mm < 5: return "rain"
        elif mm < 10: return "heavy_rain"
        else: return "downpour"


# ═══════════════════════════════════════
# NORMALIZACJA PÓŁNOCY
# ═══════════════════════════════════════

def normalize_interval(start: int, end: int, anchor: int) -> Tuple[int, int]:
    ns = start
    ne = end
    if ne <= ns:
        ne += 24
    if ns < anchor:
        ns += 24
        ne += 24
    return ns, ne

def normalize_block_and_events(
    block_start: int, block_end: int, events: List[WxEvent]
) -> Tuple[int, int, List[WxEvent]]:
    bs, be = normalize_interval(block_start, block_end, block_start)
    normalized = []
    for e in events:
        es, ee = normalize_interval(e.start, e.end, block_start)
        normalized.append(WxEvent(e.kind, es, ee, e.severity))
    return bs, be, normalized

def fmt_hour(h: int) -> str:
    return f"{h % 24:02d}"

def fmt_hours(start: int, end: int) -> str:
    return f"{fmt_hour(start)}–{fmt_hour(end)}"

def fmt_temp_range(tmin: int, tmax: int) -> str:
    if tmin == tmax:
        return f"{tmin}°"
    return f"{tmin}°/{tmax}°"

# ═══════════════════════════════════════
# ODCZUWALNA
# ═══════════════════════════════════════

def qualifies_feels_value(temp_value: int, feels_value: int, threshold: int = 2) -> bool:
    return (temp_value - feels_value) >= threshold

def should_show_feels_like(temp_min, temp_max, feels_min, feels_max, threshold=2):
    if feels_min is None or feels_max is None:
        return False
    return (qualifies_feels_value(temp_min, feels_min, threshold)
            or qualifies_feels_value(temp_max, feels_max, threshold))

def build_feels_like_payload(temp_min, temp_max, feels_min, feels_max, threshold=2, lang="pl"):
    if feels_min is None or feels_max is None:
        return None, []

    qmin = qualifies_feels_value(temp_min, feels_min, threshold)
    qmax = qualifies_feels_value(temp_max, feels_max, threshold)

    if not (qmin or qmax):
        return None, []

    eff_min = feels_min if qmin else temp_min
    eff_max = feels_max if qmax else temp_max

    if eff_min > eff_max:
        eff_min, eff_max = eff_max, eff_min
        qmin, qmax = qmax, qmin
        
    f_prefix = t(lang, "feels_like_prefix") # "odcz. " lub "feels "

    if eff_min == eff_max and temp_min == temp_max:
        text = f"{f_prefix}{eff_min}°"
        spans = [
            {"text": f_prefix, "style": "feels_like_accent"},
            {"text": f"{eff_min}°", "style": "feels_like_accent" if (qmin or qmax) else "default"},
        ]
        return text, spans

    text = f"{f_prefix}{eff_min}°/{eff_max}°"
    spans = [
        {"text": f_prefix, "style": "feels_like_accent"},
        {"text": f"{eff_min}°", "style": "feels_like_accent" if qmin else "default"},
        {"text": f"/{eff_max}°", "style": "feels_like_accent" if qmax else "default"},
    ]
    return text, spans


def merge_adjacent_same_kind(events: List[WxEvent]) -> List[WxEvent]:
    if not events:
        return []
    events = sorted(events, key=lambda e: (e.start, e.end, e.kind))
    merged = [WxEvent(events[0].kind, events[0].start, events[0].end, events[0].severity)]
    for e in events[1:]:
        last = merged[-1]
        if e.kind == last.kind and e.start <= last.end:
            last.end = max(last.end, e.end)
            last.severity = max(last.severity, e.severity)
        else:
            merged.append(WxEvent(e.kind, e.start, e.end, e.severity))
    return merged


def time_suffix(ev_start: int, ev_end: int, block_start: int, block_end: int, lang: str = "pl") -> str:
    if ev_start <= block_start and ev_end >= block_end:
        return ""
    if ev_start <= block_start and ev_end < block_end:
        return f"{t(lang, 'until')} {fmt_hour(ev_end)}"
    if ev_start > block_start and ev_end >= block_end:
        return f"{t(lang, 'from')} {fmt_hour(ev_start)}"
    return fmt_hours(ev_start, ev_end)


def pick_fit(candidates, measure=None, max_width=None, max_chars=28):
    for c in candidates:
        if measure is not None and max_width is not None:
            if measure(c) <= max_width:
                return c
        elif len(c) <= max_chars:
            return c
    return candidates[-1]


def event_candidates(event: WxEvent, block_start_n: int, block_end_n: int, lang="pl") -> List[str]:
    meta = KINDS[event.kind]
    suffix = time_suffix(event.start, event.end, block_start_n, block_end_n, lang)
    
    full_str = t(lang, meta['full_key'])
    short_str = t(lang, meta['short_key'])
    
    return [
        f"{full_str} {suffix}".strip(),
        f"{short_str} {suffix}".strip(),
    ]

def family_candidates(family: str, start_n: int, end_n: int,
                      block_start_n: int, block_end_n: int, lang="pl") -> List[str]:
    suffix = time_suffix(start_n, end_n, block_start_n, block_end_n, lang)
    base = t(lang, FAMILY_LABEL[family])
    return [f"{base} {suffix}".strip()]


def describe_precip(block: BlockForecast, lang: str = "pl",
                    measure_inline=None, inline_max_width=None,
                    measure_meta=None, meta_max_width=None,
                    inline_max_chars=28, meta_max_chars=24):
    
    bs_n, be_n, norm_events = normalize_block_and_events(block.start, block.end, block.events)
    events = merge_adjacent_same_kind(norm_events)

    precip = [e for e in events if KINDS.get(e.kind, {}).get("family") in PRECIP_FAMILIES]
    fog = [e for e in events if KINDS.get(e.kind, {}).get("family") == "fog"]

    kw = dict(measure=measure_inline, max_width=inline_max_width, max_chars=inline_max_chars)
    kw2 = dict(measure=measure_meta, max_width=meta_max_width, max_chars=meta_max_chars)

    if not precip and fog:
        line1 = pick_fit(event_candidates(fog[0], bs_n, be_n, lang), **kw)
        return line1, []

    if not precip:
        return None, []

    if len(precip) == 1:
        line1 = pick_fit(event_candidates(precip[0], bs_n, be_n, lang), **kw)
        extras = []
        if fog:
            fog_line = pick_fit(event_candidates(fog[0], bs_n, be_n, lang), **kw2)
            extras.append(fog_line)
        return line1, extras

    storm = next((e for e in precip if KINDS[e.kind]["family"] == "storm"), None)
    non_storm = [e for e in precip if KINDS[e.kind]["family"] != "storm"]

    if storm and non_storm:
        line1 = pick_fit(event_candidates(non_storm[0], bs_n, be_n, lang), **kw)
        line2 = pick_fit(
            [f"{t(lang, 'storms')} {t(lang, 'after')} {fmt_hour(storm.start)}",
             f"{t(lang, 'storms')} {fmt_hours(storm.start, storm.end)}"],
            **kw2)
        return line1, [line2]

    families = {KINDS[e.kind]["family"] for e in precip}
    if len(families) == 1:
        fam = next(iter(families))
        s_n = min(e.start for e in precip)
        e_n = max(e.end for e in precip)
        line1 = pick_fit(family_candidates(fam, s_n, e_n, bs_n, be_n, lang), **kw)

        strongest = max(precip, key=lambda ev: KINDS[ev.kind]["severity"])
        first = precip[0]
        if (KINDS[strongest.kind]["severity"] > KINDS[first.kind]["severity"]
                and strongest.start > first.start):
            strong_name = t(lang, KINDS[strongest.kind]["full_key"])
            if strongest.kind in ("downpour",):
                variants = [
                    f"{strong_name} {t(lang, 'after')} {fmt_hour(strongest.start)}",
                    f"{t(lang, 'stronger_after')} {fmt_hour(strongest.start)}",
                ]
            else:
                variants = [
                    f"{t(lang, 'stronger_after')} {fmt_hour(strongest.start)}",
                ]
            line2 = pick_fit(variants, **kw2)
            return line1, [line2]
        return line1, []

    line1 = pick_fit(event_candidates(precip[0], bs_n, be_n, lang), **kw)
    line2 = pick_fit(event_candidates(precip[1], bs_n, be_n, lang), **kw2)
    return line1, [line2]


# ═══════════════════════════════════════
# GŁÓWNA FUNKCJA WSTRZYKUJĄCA LANG
# ═══════════════════════════════════════

def build_block_copy(block: BlockForecast, lang: str = "pl",
                     measure_inline=None, inline_max_width=None,
                     measure_meta=None, meta_max_width=None,
                     inline_max_chars=28, meta_max_chars=24):
    
    primary_desc, escalation_lines = describe_precip(
        block, lang=lang,
        measure_inline=measure_inline, inline_max_width=inline_max_width,
        measure_meta=measure_meta, meta_max_width=meta_max_width,
        inline_max_chars=inline_max_chars, meta_max_chars=meta_max_chars,
    )

    if not primary_desc:
        primary_desc = block.sky_label or ""

    extra_lines = []

    for esc in escalation_lines:
        if len(extra_lines) < 2:
            extra_lines.append({
                "type": "meta",
                "text": esc,
                "spans": [{"text": esc, "style": "meta"}],
            })

    if block.max_wind and block.max_wind >= 60:
        wind_text = f"{t(lang, 'wind_up_to')} {round(block.max_wind)} km/h"
        if len(extra_lines) < 2:
            extra_lines.append({
                "type": "meta",
                "text": wind_text,
                "spans": [{"text": wind_text, "style": "meta"}],
            })

    feels_text, feels_spans = build_feels_like_payload(
        block.temp_min, block.temp_max,
        block.feels_min, block.feels_max,
        lang=lang
    )

    feels_added = False
    if feels_text is not None and len(extra_lines) < 2:
        extra_lines.append({
            "type": "feels_like",
            "text": feels_text,
            "spans": feels_spans,
        })
        feels_added = True

    if not feels_added:
        feels_text = None
        feels_spans = []

    return {
        "label": block.label,
        "hours": block.hours_range,
        "temp": fmt_temp_range(block.temp_min, block.temp_max),
        "primary_desc": primary_desc,
        "extra_lines": extra_lines,
        "feels_like_text": feels_text,
        "feels_like_spans": feels_spans,
    }