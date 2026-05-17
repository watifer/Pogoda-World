"""
forecast_text.py — Deterministyczny generator mikroopisów pogodowych.
Wersja: 3.1

Poprawki vs 3.0:
1. Pełna normalizacja północy w merge/describe/family ranges
2. feels_like_text/spans = None/[] gdy nie dodane do extra_lines
3. pick_fit() z explicit None checks
4. Mgła jawnie wspierana w describe_precip
5. Testy naprawione (exact_2)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


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
# SŁOWNIK ZJAWISK
# ═══════════════════════════════════════

KINDS = {
    "drizzle":    {"family": "rain",  "full": "mżawka",             "short": "mżawka",      "severity": 1},
    "light_rain": {"family": "rain",  "full": "lekki deszcz",       "short": "deszcz",       "severity": 2},
    "rain":       {"family": "rain",  "full": "deszcz",             "short": "deszcz",       "severity": 3},
    "heavy_rain": {"family": "rain",  "full": "silny deszcz",       "short": "deszcz",       "severity": 4},
    "downpour":   {"family": "rain",  "full": "ulewa",              "short": "ulewa",        "severity": 5},
    "light_snow": {"family": "snow",  "full": "lekki śnieg",        "short": "śnieg",        "severity": 2},
    "snow":       {"family": "snow",  "full": "śnieg",              "short": "śnieg",        "severity": 3},
    "heavy_snow": {"family": "snow",  "full": "intensywny śnieg",   "short": "śnieg",        "severity": 4},
    "sleet":      {"family": "mixed", "full": "deszcz ze śniegiem", "short": "deszcz/śnieg", "severity": 3},
    "storm":      {"family": "storm", "full": "burze",              "short": "burze",        "severity": 5},
    "fog":        {"family": "fog",   "full": "mgła",               "short": "mgła",         "severity": 2},
}

FAMILY_LABEL = {
    "rain": "deszcz", "snow": "śnieg",
    "mixed": "deszcz ze śniegiem", "storm": "burze", "fog": "mgła",
}

# Rodziny traktowane jako opady (precip).
# Mgła NIE jest opadem — jest obsługiwana osobno.
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
    
    # === PHYSICAL PLAUSIBILITY REMAP (Żelazna weryfikacja fizyki) ===
    if api is not None:
        fam = KINDS.get(api, {}).get("family")
        
        # --- BRAMKA OPADÓW (Eliminacja fałszywych deszczów z API) ---
        # Jeśli API zwraca kod deszczu/śniegu, ale wody jest mniej niż 0.1mm, ignorujemy to.
        # Zostawiamy burze ("storm"), bo burza może krążyć wokół nas bez opadu na same współrzędne.
        if fam in PRECIP_FAMILIES and fam != "storm" and mm < 0.1:
            return None
        if fam == "rain":
            if temp_c <= -1.0:
                # API mówi "deszcz", ale jest mróz -> Zamieniamy na śnieg
                if 0 < mm < 2: return "light_snow"
                elif mm >= 5: return "heavy_snow"
                else: return "snow"
            elif -1.0 < temp_c <= 2.0:
                # API mówi "deszcz", ale jesteśmy w okolicy zera -> Deszcz ze śniegiem
                return "sleet"
        
        # Jeśli API przetrwało weryfikację, używamy go
        return api

    # === FALLBACK (Jeśli API nie wie, co pada) ===
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
# NORMALIZACJA PÓŁNOCY (PUNKT 1)
# ═══════════════════════════════════════

def normalize_interval(start: int, end: int, anchor: int) -> Tuple[int, int]:
    """Normalizuje przedział do osi rosnącej zakotwiczonej w anchor.

    Przykłady dla anchor=22:
    - 22,06 → 22,30
    - 22,24 → 22,24
    - 00,02 → 24,26
    - 03,04 → 27,28
    - 22,02 → 22,26
    """
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
    """Normalizuje blok i wszystkie eventy do jednej rosnącej osi."""
    bs, be = normalize_interval(block_start, block_end, block_start)
    normalized = []
    for e in events:
        es, ee = normalize_interval(e.start, e.end, block_start)
        normalized.append(WxEvent(e.kind, es, ee, e.severity))
    return bs, be, normalized


# ═══════════════════════════════════════
# FORMATOWANIE
# ═══════════════════════════════════════

def fmt_hour(h: int) -> str:
    return f"{h % 24:02d}"

def fmt_hours(start: int, end: int) -> str:
    return f"{fmt_hour(start)}–{fmt_hour(end)}"

def fmt_temp_range(tmin: int, tmax: int) -> str:
    if tmin == tmax:
        return f"{tmin}°"
    return f"{tmin}°/{tmax}°"


# ═══════════════════════════════════════
# ODCZUWALNA (PUNKT 2, 7)
# ═══════════════════════════════════════

def qualifies_feels_value(temp_value: int, feels_value: int, threshold: int = 2) -> bool:
    """True gdy odczuwalna jest NIŻSZA o >= threshold."""
    return (temp_value - feels_value) >= threshold


def should_show_feels_like(temp_min, temp_max, feels_min, feels_max, threshold=2):
    if feels_min is None or feels_max is None:
        return False
    return (qualifies_feels_value(temp_min, feels_min, threshold)
            or qualifies_feels_value(temp_max, feels_max, threshold))


def build_feels_like_payload(temp_min, temp_max, feels_min, feels_max, threshold=2):
    """Zwraca (text, spans) lub (None, [])."""
    if feels_min is None or feels_max is None:
        return None, []

    qmin = qualifies_feels_value(temp_min, feels_min, threshold)
    qmax = qualifies_feels_value(temp_max, feels_max, threshold)

    if not (qmin or qmax):
        return None, []

    # Fallback: używamy oryginalnej temperatury, jeśli odczuwalna nie spełnia progu
    eff_min = feels_min if qmin else temp_min
    eff_max = feels_max if qmax else temp_max

    # Zabezpieczenie przed odwróceniem przedziału
    if eff_min > eff_max:
        eff_min, eff_max = eff_max, eff_min
        qmin, qmax = qmax, qmin

    # NASZA NOWA ZASADA: Spłaszczamy do jednej liczby TYLKO gdy temperatura rzeczywista też jest jedną liczbą
    if eff_min == eff_max and temp_min == temp_max:
        text = f"odcz. {eff_min}°"
        spans = [
            {"text": "odcz. ", "style": "feels_like_accent"},
            {"text": f"{eff_min}°",
             "style": "feels_like_accent" if (qmin or qmax) else "default"},
        ]
        return text, spans

    # W przeciwnym razie wymuszamy format przedziału (X°/Y°) dla zachowania symetrii
    text = f"odcz. {eff_min}°/{eff_max}°"
    spans = [
        {"text": "odcz. ", "style": "feels_like_accent"},
        {"text": f"{eff_min}°",
         "style": "feels_like_accent" if qmin else "default"},
        {"text": f"/{eff_max}°",
         "style": "feels_like_accent" if qmax else "default"},
    ]
    return text, spans

# ═══════════════════════════════════════
# MERGE (na znormalizowanej osi)
# ═══════════════════════════════════════

def merge_adjacent_same_kind(events: List[WxEvent]) -> List[WxEvent]:
    """Merguje sąsiednie eventy tego samego typu.
    WYMAGA: eventy znormalizowane do jednej osi (przez normalize_block_and_events).
    """
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


# ═══════════════════════════════════════
# SUFFIX CZASU (na znormalizowanej osi)
# ═══════════════════════════════════════

def time_suffix(ev_start: int, ev_end: int, block_start: int, block_end: int) -> str:
    """Generuje suffix. Wszystkie parametry muszą być na znormalizowanej osi."""
    if ev_start <= block_start and ev_end >= block_end:
        return ""
    if ev_start <= block_start and ev_end < block_end:
        return f"do {fmt_hour(ev_end)}"
    if ev_start > block_start and ev_end >= block_end:
        return f"od {fmt_hour(ev_start)}"
    return fmt_hours(ev_start, ev_end)


# ═══════════════════════════════════════
# GENEROWANIE OPISÓW (PUNKT 3, 5)
# ═══════════════════════════════════════

def pick_fit(candidates, measure=None, max_width=None, max_chars=28):
    """Wybiera najdłuższy pasujący opis. (PUNKT 3: explicit None checks)"""
    for c in candidates:
        if measure is not None and max_width is not None:
            if measure(c) <= max_width:
                return c
        elif len(c) <= max_chars:
            return c
    return candidates[-1]


def event_candidates(event: WxEvent, block_start_n: int, block_end_n: int) -> List[str]:
    """Generuje warianty opisu. Parametry na znormalizowanej osi."""
    meta = KINDS[event.kind]
    suffix = time_suffix(event.start, event.end, block_start_n, block_end_n)
    return [
        f"{meta['full']} {suffix}".strip(),
        f"{meta['short']} {suffix}".strip(),
    ]


def family_candidates(family: str, start_n: int, end_n: int,
                      block_start_n: int, block_end_n: int) -> List[str]:
    suffix = time_suffix(start_n, end_n, block_start_n, block_end_n)
    base = FAMILY_LABEL[family]
    return [f"{base} {suffix}".strip()]


def describe_precip(block: BlockForecast,
                    measure_inline=None, inline_max_width=None,
                    measure_meta=None, meta_max_width=None,
                    inline_max_chars=28, meta_max_chars=24):
    """Generuje opis opadów/mgły dla bloku.

    PUNKT 1: Normalizacja północy wewnątrz tej funkcji.
    PUNKT 5: Mgła jest wspierana — traktowana jak samodzielne zjawisko,
             ale NIE jako opad (nie w PRECIP_FAMILIES). Opisywana oddzielnie.
    """
    # Normalizuj blok i eventy
    bs_n, be_n, norm_events = normalize_block_and_events(
        block.start, block.end, block.events
    )

    # Merguj na znormalizowanej osi
    events = merge_adjacent_same_kind(norm_events)

    # Rozdziel opady i mgłę
    precip = [e for e in events if KINDS.get(e.kind, {}).get("family") in PRECIP_FAMILIES]
    fog = [e for e in events if KINDS.get(e.kind, {}).get("family") == "fog"]

    kw = dict(measure=measure_inline, max_width=inline_max_width, max_chars=inline_max_chars)
    kw2 = dict(measure=measure_meta, max_width=meta_max_width, max_chars=meta_max_chars)

    # Mgła bez opadów → mgła jest primary_desc
    if not precip and fog:
        line1 = pick_fit(event_candidates(fog[0], bs_n, be_n), **kw)
        return line1, []

    if not precip:
        return None, []

    # === 1 zjawisko ===
    if len(precip) == 1:
        line1 = pick_fit(event_candidates(precip[0], bs_n, be_n), **kw)
        # Jeśli jest też mgła, dodaj jako extra
        extras = []
        if fog:
            fog_line = pick_fit(event_candidates(fog[0], bs_n, be_n), **kw2)
            extras.append(fog_line)
        return line1, extras

    # === Burze + coś innego ===
    storm = next((e for e in precip if KINDS[e.kind]["family"] == "storm"), None)
    non_storm = [e for e in precip if KINDS[e.kind]["family"] != "storm"]

    if storm and non_storm:
        line1 = pick_fit(event_candidates(non_storm[0], bs_n, be_n), **kw)
        line2 = pick_fit(
            [f"burze po {fmt_hour(storm.start)}",
             f"burze {fmt_hours(storm.start, storm.end)}"],
            **kw2)
        return line1, [line2]

    # === Ta sama rodzina, różne intensywności ===
    families = {KINDS[e.kind]["family"] for e in precip}
    if len(families) == 1:
        fam = next(iter(families))
        s_n = min(e.start for e in precip)
        e_n = max(e.end for e in precip)
        line1 = pick_fit(family_candidates(fam, s_n, e_n, bs_n, be_n), **kw)

        strongest = max(precip, key=lambda ev: KINDS[ev.kind]["severity"])
        first = precip[0]
        if (KINDS[strongest.kind]["severity"] > KINDS[first.kind]["severity"]
                and strongest.start > first.start):
            # Jeśli ma wyraźną własną nazwę (ulewa, nawałnica), użyj jej
            strong_name = KINDS[strongest.kind]["full"]
            if strongest.kind in ("downpour",):
                variants = [
                    f"{strong_name} po {fmt_hour(strongest.start)}",
                    f"silniej po {fmt_hour(strongest.start)}",
                ]
            else:
                variants = [
                    f"silniej po {fmt_hour(strongest.start)}",
                ]
            line2 = pick_fit(variants, **kw2)
            return line1, [line2]
        return line1, []

    # === Różne rodziny ===
    line1 = pick_fit(event_candidates(precip[0], bs_n, be_n), **kw)
    line2 = pick_fit(event_candidates(precip[1], bs_n, be_n), **kw2)
    return line1, [line2]


# ═══════════════════════════════════════
# GŁÓWNA FUNKCJA (PUNKT 2, 7)
# ═══════════════════════════════════════

def build_block_copy(block: BlockForecast,
                     measure_inline=None, inline_max_width=None,
                     measure_meta=None, meta_max_width=None,
                     inline_max_chars=28, meta_max_chars=24):
    """
    Priorytet extra_lines:
    1. eskalacja / dodatkowy detal zjawiska (WYŻSZY)
    2. odczuwalna (NAJNIŻSZY — wypada pierwsza gdy brak miejsca)
    Max 2 extra_lines.

    PUNKT 2: Jeśli odczuwalna NIE weszła do extra_lines:
    - feels_like_text = None
    - feels_like_spans = []
    """
    primary_desc, escalation_lines = describe_precip(
        block,
        measure_inline=measure_inline, inline_max_width=inline_max_width,
        measure_meta=measure_meta, meta_max_width=meta_max_width,
        inline_max_chars=inline_max_chars, meta_max_chars=meta_max_chars,
    )

    if not primary_desc:
        primary_desc = block.sky_label or ""

    extra_lines = []

    # 1. Eskalacja (wyższy priorytet)
    for esc in escalation_lines:
        if len(extra_lines) < 2:
            extra_lines.append({
                "type": "meta",
                "text": esc,
                "spans": [{"text": esc, "style": "meta"}],
            })

    # 1.5. Ekstremalny Wiatr (Ostrzeżenie bezpieczeństwa!)
    if block.max_wind and block.max_wind >= 60:
        wind_text = f"wiatr do {round(block.max_wind)} km/h"
        if len(extra_lines) < 2:
            extra_lines.append({
                "type": "meta",
                "text": wind_text,
                "spans": [{"text": wind_text, "style": "meta"}],
            })

    # 2. Odczuwalna (najniższy priorytet)
    feels_text, feels_spans = build_feels_like_payload(
        block.temp_min, block.temp_max,
        block.feels_min, block.feels_max,
    )

    feels_added = False
    if feels_text is not None and len(extra_lines) < 2:
        extra_lines.append({
            "type": "feels_like",
            "text": feels_text,
            "spans": feels_spans,
        })
        feels_added = True

    # PUNKT 2: Jeśli odczuwalna nie weszła do extra_lines, zerujemy
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