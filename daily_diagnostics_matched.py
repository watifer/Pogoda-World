"""Równoległa diagnostyka surowa modeli (ETAP 1 — obserwacyjna).

Ten moduł liczy rozbieżność temperatury pomiędzy Open-Meteo i Yr.no wyłącznie
na rekordach dopasowanych po pełnym lokalnym timestampie (z offsetem) albo po
jednoznacznej chwili UTC. Korzysta wyłącznie z surowych pól ``temp_c_om`` /
``temp_c_yr`` oraz z rekordów ``source="yrno"``, które nie były fuzjowane.

Moduł jest całkowicie niezależny od produkcji:

* nie zmienia ``daily_diag`` ani funkcji ``build_daily_diagnostics``;
* nie jest używany przez ``daily_source``, ``confidence_gate``, layouty, WK,
  alerty ani wybór źródła;
* nie zapisuje ``audit_spread.jsonl`` ani żadnego innego pliku;
* nie mutuje przekazanej listy godzin.

Wynik nadaje się wyłącznie do obserwacji i późniejszej analizy.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Wersja struktury danych — pozwala odróżnić kolejne etapy audytu.
DIAGNOSTICS_VERSION = "matched_v1"

# Jednostka temperatury używana w tej diagnostyce.
TEMPERATURE_UNIT = "C"

# Sposób dopasowania rekordów (opis w payloadzie, bez logiki przełączania).
TIMESTAMP_MATCHING = "exact_local_timestamp_with_offset_or_utc_instant"

STATUS_NO_OM_RECORDS = "no_om_records"
STATUS_NO_YR_RECORDS = "no_yr_records"
STATUS_NO_COMMON_HOURS = "no_common_hours"
STATUS_NO_MATCHED_TEMPERATURE = "no_matched_temperature"
STATUS_PARTIAL_COVERAGE = "partial_coverage"
STATUS_MATCHED = "matched"

ALL_STATUSES = (
    STATUS_NO_OM_RECORDS,
    STATUS_NO_YR_RECORDS,
    STATUS_NO_COMMON_HOURS,
    STATUS_NO_MATCHED_TEMPERATURE,
    STATUS_PARTIAL_COVERAGE,
    STATUS_MATCHED,
)

# Tryb klucza timestampu — raportowany, aby było jasne, czy dopasowanie
# opierało się na offsetcie (jednoznaczna chwila UTC), czy na gołym czasie
# lokalnym (starsze fixture'y bez offsetu).
KEY_MODE_LOCAL_OFFSET = "local_timestamp_with_offset"
KEY_MODE_NAIVE_LOCAL = "naive_local_timestamp"
KEY_MODE_NONE = "no_timestamps"
KEY_MODE_MIXED = "mixed"

# Nazwy źródeł tak, jak pojawiają się w payloadzie.
_SOURCE_OM = "openmeteo"
_SOURCE_YR = "yrno"

_EPS = 1e-9


def _as_float(value):
    """Zamienia wartość na float, zachowując ``None`` jako brak danych.

    ``0.0`` pozostaje rzeczywistą wartością. Wartości nieliczbowe, ``bool``
    oraz ``NaN`` traktujemy jako brak danych, nigdy jako zero.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    return result


def _timestamp_identity(hour):
    """Zwraca ``(klucz, data_lokalna, tryb, opis_lokalny)`` dla rekordu.

    Klucz jest jednoznaczną chwilą UTC, jeśli timestamp ma offset. Dzięki temu
    ``01:00-04:00`` i ``01:00-05:00`` w dniu zmiany czasu to dwa różne klucze.
    Dla timestampów bez offsetu (starsze fixture'y) nie da się wyliczyć chwili
    UTC, więc używamy pełnego napisu ISO jako klucza i raportujemy tryb
    ``naive_local_timestamp``. Nigdy nie dopasowujemy po samym ``HH`` ani po
    pozycji rekordu na liście.
    """
    raw = hour.get("time_local")
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if len(raw) < 10:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return ("naive", raw), raw[:10], KEY_MODE_NAIVE_LOCAL, raw

    instant = parsed.astimezone(timezone.utc)
    return (
        ("utc", instant.isoformat()),
        parsed.strftime("%Y-%m-%d"),
        KEY_MODE_LOCAL_OFFSET,
        raw,
    )


def classify_record(hour):
    """Klasyfikuje rekord jako ``"om"``, ``"yr"`` albo ``None``.

    Decyduje jawne ``source``. Gdy go brak (starsze payloady), pomagają
    wyłącznie surowe pola ``temp_c_om`` / ``temp_c_yr``.
    """
    src = hour.get("source")
    if src == _SOURCE_OM:
        return "om"
    if src == _SOURCE_YR:
        return "yr"
    has_om_raw = "temp_c_om" in hour
    has_yr_raw = "temp_c_yr" in hour
    if has_om_raw and not has_yr_raw:
        return "om"
    if has_yr_raw and not has_om_raw:
        return "yr"
    return None


def raw_om_temperature(hour):
    """Surowa temperatura Open-Meteo.

    Tylko jawne ``temp_c_om``. Kanoniczne ``temp_c`` bywa nadpisane przez Yr
    podczas fuzji, więc nie jest dowodem wartości modelu OM.
    """
    if "temp_c_om" not in hour:
        return None
    return _as_float(hour.get("temp_c_om"))


def raw_yr_temperature(hour):
    """Surowa temperatura Yr.no.

    Priorytet ma jawne ``temp_c_yr`` (z rekordu po fuzji). Rekord
    ``source="yrno"`` nie jest fuzjowany, więc jego ``temp_c`` jest surową
    wartością Yr — ale tylko przy jawnym pochodzeniu ``yrno``.
    """
    if "temp_c_yr" in hour:
        value = _as_float(hour.get("temp_c_yr"))
        if value is not None:
            return value
    if hour.get("source") != _SOURCE_YR:
        return None
    provenance = hour.get("temp_c_source", _SOURCE_YR)
    if provenance not in (None, _SOURCE_YR):
        return None
    return _as_float(hour.get("temp_c"))


def _empty_slot(time_local):
    return {
        "time_local": time_local,
        "om_seen": False,
        "om_value": None,
        "yr_seen": False,
        "yr_value": None,
        "om_conflicts": 0,
        "yr_conflicts": 0,
    }


def _store(slot, side, value):
    """Zapisuje wartość po stronie OM/Yr, nie nadpisując istniejącej."""
    seen_key = f"{side}_seen"
    value_key = f"{side}_value"
    conflict_key = f"{side}_conflicts"

    slot[seen_key] = True
    if value is None:
        return
    current = slot[value_key]
    if current is None:
        slot[value_key] = value
    elif abs(current - value) > _EPS:
        slot[conflict_key] += 1


def _coverage_status(n_om_records, n_yr_timestamps, n_om_timestamps,
                     n_common_timestamps, matched_temperature_count):
    """Wylicza status pokrycia bez wprowadzania progów produkcyjnych.

    Decydują znaczniki timestampów, nie liczba rekordów: sparowane dane Yr
    mogą pochodzić z rekordu ``source="openmeteo"`` przez ``temp_c_yr``,
    nawet gdy w payloadzie nie ma osobnego rekordu ``source="yrno"``.
    """
    if n_om_records == 0 or n_om_timestamps == 0:
        return STATUS_NO_OM_RECORDS
    if n_yr_timestamps == 0:
        return STATUS_NO_YR_RECORDS
    if n_common_timestamps == 0:
        return STATUS_NO_COMMON_HOURS
    if matched_temperature_count == 0:
        # Wspólne godziny istnieją, ale żadna nie ma poprawnej pary temperatur.
        # To nie jest zgodność modeli — spread pozostaje ``None``.
        return STATUS_NO_MATCHED_TEMPERATURE
    if matched_temperature_count < n_common_timestamps or n_om_timestamps != n_yr_timestamps:
        return STATUS_PARTIAL_COVERAGE
    return STATUS_MATCHED


def build_daily_diagnostics_matched(hours_list):
    """Buduje równoległą diagnostykę surową dopasowaną po timestampach.

    Parametry
    ---------
    hours_list:
        Lista godzin w kształcie payloadu (``time_local``, ``source``,
        ``temp_c``, ``temp_c_om``, ``temp_c_yr``). Lista nie jest mutowana.

    Zwraca
    ------
    dict z kluczami ``version``, ``unit``, ``timestamp_matching``,
    ``timestamp_key_mode``, ``coverage_threshold`` (``None`` — próg nie jest
    jeszcze zdefiniowany), ``days``, ``totals`` i ``coverage_status_counts``.
    """
    days = {}
    key_modes = set()
    n_hours_without_timestamp = 0
    n_hours_unclassified = 0

    for hour in hours_list or []:
        if not isinstance(hour, dict):
            n_hours_without_timestamp += 1
            continue

        identity = _timestamp_identity(hour)
        if identity is None:
            n_hours_without_timestamp += 1
            continue

        key, date_str, key_mode, local_label = identity
        key_modes.add(key_mode)

        side = classify_record(hour)
        if side is None:
            n_hours_unclassified += 1
            continue

        day = days.setdefault(date_str, {
            "timestamp_slots": {},
            "n_om_records": 0,
            "n_yr_records": 0,
            "n_om_conflicts": 0,
            "n_yr_conflicts": 0,
        })
        slots = day["timestamp_slots"]

        slot = slots.get(key)
        if slot is None:
            slot = _empty_slot(local_label)
            slots[key] = slot

        if side == "om":
            day["n_om_records"] += 1
            _store(slot, "om", raw_om_temperature(hour))
            # Obecność klucza temp_c_yr oznacza, że rekord ma sparowane,
            # surowe dane Yr dla tej samej chwili.
            if "temp_c_yr" in hour:
                _store(slot, "yr", _as_float(hour.get("temp_c_yr")))
        else:
            day["n_yr_records"] += 1
            _store(slot, "yr", raw_yr_temperature(hour))

    diagnostics_days = {}
    status_counts = {status: 0 for status in ALL_STATUSES}
    totals = {
        "n_om_records": 0,
        "n_yr_records": 0,
        "n_om_timestamps": 0,
        "n_yr_timestamps": 0,
        "n_common_timestamps": 0,
        "matched_temperature_count": 0,
    }

    for date_str in sorted(days):
        day = days[date_str]
        slots = day["timestamp_slots"]

        n_om_timestamps = 0
        n_yr_timestamps = 0
        n_common_timestamps = 0
        diffs = []
        max_diff = None
        max_diff_at = None
        om_conflicts = 0
        yr_conflicts = 0

        for slot in slots.values():
            om_conflicts += slot["om_conflicts"]
            yr_conflicts += slot["yr_conflicts"]

            if slot["om_seen"]:
                n_om_timestamps += 1
            if slot["yr_seen"]:
                n_yr_timestamps += 1

            if not (slot["om_seen"] and slot["yr_seen"]):
                continue
            n_common_timestamps += 1

            om_value = slot["om_value"]
            yr_value = slot["yr_value"]
            if om_value is None or yr_value is None:
                continue

            diff = abs(om_value - yr_value)
            diffs.append(diff)
            if max_diff is None or diff > max_diff:
                max_diff = diff
                max_diff_at = slot["time_local"]

        matched_count = len(diffs)
        mean_diff = (sum(diffs) / matched_count) if matched_count else None

        status = _coverage_status(
            day["n_om_records"], n_yr_timestamps,
            n_om_timestamps, n_common_timestamps, matched_count,
        )
        status_counts[status] += 1

        diagnostics_days[date_str] = {
            "date": date_str,
            "coverage_status": status,
            "n_om_records": day["n_om_records"],
            "n_yr_records": day["n_yr_records"],
            "n_om_timestamps": n_om_timestamps,
            "n_yr_timestamps": n_yr_timestamps,
            "n_common_timestamps": n_common_timestamps,
            "matched_temperature_count": matched_count,
            "max_abs_spread_c": round(max_diff, 1) if max_diff is not None else None,
            "mean_abs_spread_c": round(mean_diff, 1) if mean_diff is not None else None,
            "max_abs_spread_at_local": max_diff_at,
            "n_conflicting_om_values": om_conflicts,
            "n_conflicting_yr_values": yr_conflicts,
            "unit": TEMPERATURE_UNIT,
        }

        totals["n_om_records"] += day["n_om_records"]
        totals["n_yr_records"] += day["n_yr_records"]
        totals["n_om_timestamps"] += n_om_timestamps
        totals["n_yr_timestamps"] += n_yr_timestamps
        totals["n_common_timestamps"] += n_common_timestamps
        totals["matched_temperature_count"] += matched_count

    if not key_modes:
        key_mode = KEY_MODE_NONE
    elif len(key_modes) == 1:
        key_mode = next(iter(key_modes))
    else:
        key_mode = KEY_MODE_MIXED

    totals["n_days"] = len(diagnostics_days)
    totals["n_hours_without_timestamp"] = n_hours_without_timestamp
    totals["n_hours_unclassified"] = n_hours_unclassified

    return {
        "version": DIAGNOSTICS_VERSION,
        "unit": TEMPERATURE_UNIT,
        "timestamp_matching": TIMESTAMP_MATCHING,
        "timestamp_key_mode": key_mode,
        # Próg "wystarczającego pokrycia" nie jest jeszcze zdefiniowany i nie
        # wpływa na nic produkcyjnego. Zapisywane są tylko liczby i statusy.
        "coverage_threshold": None,
        "days": diagnostics_days,
        "totals": totals,
        "coverage_status_counts": status_counts,
        # Diagnostyka jest wyłącznie obserwacyjna; żaden konsument produkcyjny
        # nie jest ani nie będzie w tym etapie na nią przełączony.
        "observation_only": True,
    }
