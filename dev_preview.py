#!/usr/bin/env python3
"""dev_preview.py — podgląd kart /now i /day na danych syntetycznych.

Po co: alerty pogodowe mają bramki (sezon, progi wiatru, pora roku, geometria
brzegu), więc na żywej pogodzie testuje się je tygodniami. Ten skrypt buduje
payload pod konkretne zjawisko i renderuje kartę od razu.

    python dev_preview.py --doctor                      # diagnostyka środowiska
    python dev_preview.py --list                        # lista scenariuszy
    python dev_preview.py -s freezing -l de --save      # karta /now po niemiecku
    python dev_preview.py -s marine_storm --card day    # karta /day
    python dev_preview.py -s beach --date 2026-07-15    # test bramki sezonowej
    python dev_preview.py -s all --lang all             # przemiał wszystkiego

Nic nie woła sieci: OWM jest wyłączony, dane godzinowe są zmyślone.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo

LANGS = ["pl", "en", "de", "fr", "es", "no"]

# lokalizacje: (nazwa, lat, lon, tz)
LOC_COAST = ("Hel", 54.608, 18.801, "Europe/Warsaw")
LOC_INLAND = ("Warszawa", 52.23, 21.01, "Europe/Warsaw")


def _base_hour(dt, source="openmeteo", **over):
    h = {
        "time_local": dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": source,
        "temp_c": 8.0,
        "precip_mm": 0.0,
        "precip_eff_mm": 0.0,
        "precip_prob_pct": 5,
        "clouds_pct": 10.0,
        "clouds_low_pct": 5.0,
        "clouds_mid_pct": 5.0,
        "clouds_high_pct": 0.0,
        "wind_kmh": 12.0,
        "gust_kmh": 20.0,
        "wind_dir_deg": 0,        # 0 = z północy, dla Helu to wiatr od wody
        "rh_pct": 70,
        "dewpoint_c": 3.0,
        "pressure_hpa": 1015,
        "symbol_code": "clearsky_day",
        "weather_code": 0,
        "uv_index": 1.0,
    }
    h.update(over)
    return h


# ─────────────────────────── scenariusze ───────────────────────────
# each: dict(desc, loc, date, hours(fn(start) -> list of per-hour overrides))

RAIN = {"precip_mm": 1.4, "precip_eff_mm": 1.4, "precip_prob_pct": 85, "clouds_pct": 95.0,
        "clouds_low_pct": 85.0, "clouds_mid_pct": 10.0, "symbol_code": "rain", "weather_code": 63}
CLOUDY = {"clouds_pct": 95.0, "clouds_low_pct": 80.0, "clouds_mid_pct": 15.0,
          "symbol_code": "cloudy", "weather_code": 3}
FREEZING = {"temp_c": 0.3, "precip_mm": 0.6, "precip_eff_mm": 0.6, "precip_prob_pct": 80,
            "rh_pct": 96, "dewpoint_c": -0.1, "clouds_pct": 95.0, "clouds_low_pct": 85.0,
            "clouds_mid_pct": 10.0, "symbol_code": "lightrain", "weather_code": 66}
SNOW = {"temp_c": -4.0, "precip_mm": 1.0, "precip_eff_mm": 1.0, "precip_prob_pct": 80,
        "clouds_pct": 95.0, "clouds_low_pct": 85.0, "clouds_mid_pct": 10.0,
        "symbol_code": "snow", "weather_code": 71}
STORM_WIND = {"wind_kmh": 82.0, "gust_kmh": 105.0, "wind_dir_deg": 0, "clouds_pct": 90.0,
              "clouds_low_pct": 80.0, "clouds_mid_pct": 10.0, "symbol_code": "cloudy",
              "weather_code": 3}
BEACH_WIND = {"wind_kmh": 38.0, "gust_kmh": 52.0, "wind_dir_deg": 0, "temp_c": 24.0,
              "clouds_pct": 20.0, "clouds_low_pct": 10.0, "clouds_mid_pct": 10.0}
# "Duch opadu": TYLKO openmeteo widzi 0.6 mm i to przy POP 20% (<30), więc
# _precip_consensus() kasuje ten opad do zera. Warunki termiczne są gołoledziowe.
GHOST_FREEZING = {"temp_c": 0.3, "precip_mm": 0.6, "precip_eff_mm": 0.6, "precip_prob_pct": 20,
                  "rh_pct": 96, "dewpoint_c": -0.1, "clouds_pct": 95.0, "clouds_low_pct": 85.0,
                  "clouds_mid_pct": 10.0, "symbol_code": "lightrain", "weather_code": 66}
GHOST_FREEZING_ALT = {"temp_c": 0.3, "precip_mm": 0.0, "precip_eff_mm": 0.0, "precip_prob_pct": 5,
                      "rh_pct": 96, "dewpoint_c": -0.1, "clouds_pct": 95.0, "clouds_low_pct": 85.0,
                      "clouds_mid_pct": 10.0, "symbol_code": "cloudy", "weather_code": 3}
# Wiatr średni WYŻSZY od porywu (realne w danych modelowych po uśrednieniu porywów).
GUST_BELOW_WIND = {"wind_kmh": 85.0, "gust_kmh": 70.0, "wind_dir_deg": 180, "clouds_pct": 90.0,
                   "clouds_low_pct": 80.0, "clouds_mid_pct": 10.0, "symbol_code": "cloudy",
                   "weather_code": 3}

SCENARIOS = {
    "clear": dict(
        desc="bezchmurnie, potem zachmurzenie (test przyimka 'do/until/hasta')",
        loc=LOC_INLAND, date="2026-01-15 12:00",
        hours=lambda i: {} if i < 2 else CLOUDY),
    "rain": dict(
        desc="deszcz teraz, przejaśnienia później",
        loc=LOC_INLAND, date="2026-01-15 12:00",
        hours=lambda i: RAIN if i < 3 else {}),
    "late_rain": dict(
        desc="sucho teraz, deszcz poza oknem 4h (linia 'Później: ...')",
        loc=LOC_INLAND, date="2026-01-15 12:00",
        hours=lambda i: {} if i < 6 else RAIN),
    "freezing": dict(
        desc="marznący deszcz, WMO 66 (radar gołoledzi: /now context, /day alert)",
        loc=LOC_INLAND, date="2026-01-15 12:00",
        hours=lambda i: FREEZING if i < 4 else {"temp_c": 0.5}),
    "snow": dict(
        desc="śnieg nocą",
        loc=LOC_INLAND, date="2026-01-15 22:00",
        hours=lambda i: SNOW),
    "wind": dict(
        desc="wichura w głębi lądu (zwykłe ostrzeżenie, BEZ alertu nadmorskiego)",
        loc=LOC_INLAND, date="2026-09-28 12:00",
        hours=lambda i: STORM_WIND),
    "marine_storm": dict(
        desc="sztorm od morza na wybrzeżu — wiatr 82, porywy 105 km/h od wody",
        loc=LOC_COAST, date="2026-09-28 12:00",
        hours=lambda i: STORM_WIND),
    "marine_storm_weak": dict(
        desc="wiatr PONIŻEJ progu sztormu (60/76) — alert NIE powinien się pojawić",
        loc=LOC_COAST, date="2026-09-28 12:00",
        hours=lambda i: dict(STORM_WIND, wind_kmh=60.0, gust_kmh=76.0)),
    "beach": dict(
        desc="wiatr od morza w sezonie plażowym (wymaga daty 01.06–15.09 i PL)",
        loc=LOC_COAST, date="2026-07-15 12:00",
        hours=lambda i: BEACH_WIND),
    "ghost_precip": dict(
        desc="duch opadu: 0.6 mm tylko w jednym modelu przy POP 20% + warunki gołoledzi",
        loc=LOC_INLAND, date="2026-01-15 12:00",
        hours=lambda i: GHOST_FREEZING if i < 4 else {"temp_c": 0.5},
        alt_hours=lambda i: GHOST_FREEZING_ALT if i < 4 else {"temp_c": 0.5}),
    "gust_below_wind": dict(
        desc="wiatr 85 km/h, porywy 70 km/h — poryw NIE jest maksimum",
        loc=LOC_INLAND, date="2026-09-28 12:00",
        hours=lambda i: GUST_BELOW_WIND),
}


def build_payload(scen, lang, now, span_hours):
    name, lat, lon, tz = scen["loc"]
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=12)
    hours = [_base_hour(start + timedelta(hours=i), **scen["hours"](max(0, i - 12)))
             for i in range(span_hours + 12)]
    # Drugi model (yrno) budujemy TYLKO tam, gdzie scenariusz go potrzebuje —
    # bez niego _precip_consensus() nie ma z czym porównywać i zwraca wartość bazową.
    alt = scen.get("alt_hours")
    if alt:
        hours += [_base_hour(start + timedelta(hours=i), source="yrno", **alt(max(0, i - 12)))
                  for i in range(span_hours + 12)]
    return {
        "location": {"name": name, "lat": lat, "lon": lon, "tz": tz},
        "lang": lang,
        "hours": hours,
        "forecast_source": "OpenMeteo + Yr.no (preview)",
        "model_updated_at_local": now.strftime("%Y-%m-%dT03:00:00"),
        "alerts": [],
    }


def render(scen_name, lang, card, save):
    scen = SCENARIOS[scen_name]
    tz = ZoneInfo(scen["loc"][3])
    now = datetime.strptime(scen["date"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)

    if card == "now":
        from prepare_now_layout import prepare_now_layout_data
        data = prepare_now_layout_data(build_payload(scen, lang, now, 24), now=now)
    else:
        from prepare_layout import prepare_layout_data
        data = prepare_layout_data(build_payload(scen, lang, now, 60), now=now)

    print(f"\n┌─ {scen_name} · /{card} · {lang} · {scen['loc'][0]} · {scen['date']}")
    print(f"│  {scen['desc']}")
    print(f"├─ summary : {data.get('summary')}".replace("\n", "\n│            "))
    print(f"├─ context : {data.get('context_line')}")
    for a in data.get("alerts", []) or []:
        print(f"├─ ALERT   : {a}")
    for b in (data.get("today_blocks") or [])[:3]:
        extra = " ".join(s.get("text", "") for el in b.get("extra_lines", [])
                         for s in (el.get("spans") or []))
        print(f"│  {b.get('label'):<12} {b.get('icon'):<20} {b.get('primary_desc')}  {extra}")

    if save:
        from image_generator import generate_weather_card
        out = f"output/preview_{scen_name}_{card}_{lang}.png"
        os.makedirs("output", exist_ok=True)
        shutil.copy(generate_weather_card(data), out)
        print(f"└─ PNG: {out}")
    else:
        print("└─")


def doctor(lat, lon, tz_str):
    print("═══ DIAGNOSTYKA ═══\n")
    from coast_detector import (GEO_STACK_AVAILABLE, in_beach_season,
                                is_poland_tz, get_coastal_alert_mode,
                                get_or_compute_coast_signature_lazy, coast_stack_status)

    status = coast_stack_status()
    print(f"1. Stack geo ({'/'.join(status['packages'])}): {'OK' if status['available'] else 'BRAK'}")
    if not status["available"]:
        print(f"   └─ powód importu: {status['error']}")
        print("   └─ coast / marine_storm / beach są WYŁĄCZONE — alerty od morza nie pojawią się nigdy")
        print("   └─ napraw: pip install -r requirements.txt")

    print(f"\n2. Klucz OWM: {'jest' if os.environ.get('OWM_API_KEY') else 'BRAK (nowcast/korekta chmur off)'}")

    now = datetime.now(ZoneInfo(tz_str))
    print(f"\n3. Bramka sezonowa beach (01.06–15.09): dziś {now:%d.%m} -> "
          f"{'OTWARTA' if in_beach_season(now) else 'ZAMKNIĘTA'}")
    print(f"   strefa PL (wymagana dla beach): {is_poland_tz(tz_str)}")

    from coast_detector import (MARINE_STORM_WIND_KMH_DEFAULT, MARINE_STORM_GUST_KMH_DEFAULT,
                                MARINE_STORM_MIN_GUST_WITH_WIND_KMH_DEFAULT)
    print(f"\n4. Progi marine_storm: wiatr >= {MARINE_STORM_WIND_KMH_DEFAULT} km/h "
          f"lub poryw >= {MARINE_STORM_GUST_KMH_DEFAULT} km/h "
          f"(poryw >= {MARINE_STORM_MIN_GUST_WITH_WIND_KMH_DEFAULT} przy silnym wietrze)")

    if GEO_STACK_AVAILABLE:
        from coast_runtime import GLOBAL_COAST_STORE, ensure_coast_index
        sig = get_or_compute_coast_signature_lazy(store=GLOBAL_COAST_STORE, lat=lat, lon=lon,
                                                  idx_factory=ensure_coast_index)
        dist = getattr(sig, "distance_to_ocean_km", None)
        print(f"\n5. Sygnatura brzegowa {lat},{lon}: dystans do wody = "
              f"{'brak (w głębi lądu)' if dist is None else str(round(dist, 1)) + ' km'}")
        print("   reakcja na wiatr (12 kierunków):")
        for wind, gust in [(50, 76), (60, 85), (76, 95), (82, 105)]:
            modes = {get_coastal_alert_mode(sig, wind, gust, wd, tz_str, now)
                     for wd in range(0, 360, 30)} - {None}
            print(f"     wiatr {wind:>3}/poryw {gust:>3} km/h -> {modes or 'brak alertu'}")

    print("\n6. Radar gołoledzi — próbka warunków:")
    from forecast_text import is_freezing_precip
    for label, kw in [("marznący deszcz WMO 66 @0.3°C", dict(mm=0.6, temp_c=0.3, weather_code=66, rh_pct=96)),
                      ("zimny deszcz @0.5°C, RH 96", dict(mm=0.6, temp_c=0.5, weather_code=61, rh_pct=96)),
                      ("zimny deszcz @0.5°C, suche powietrze", dict(mm=0.6, temp_c=0.5, weather_code=61, rh_pct=50, dewpoint_c=-8)),
                      ("deszcz @9°C", dict(mm=2.0, temp_c=9.0, weather_code=61, rh_pct=99))]:
        print(f"     {label:<40} -> {is_freezing_precip(**kw)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-s", "--scenario", default="clear", help="nazwa scenariusza albo 'all'")
    ap.add_argument("-l", "--lang", default="pl", help="kod języka albo 'all'")
    ap.add_argument("-c", "--card", default="now", choices=["now", "day"])
    ap.add_argument("--date", help="nadpisz datę/godzinę scenariusza, np. '2026-07-15 12:00'")
    ap.add_argument("--save", action="store_true", help="zapisz PNG do output/")
    ap.add_argument("--list", action="store_true", help="wypisz scenariusze")
    ap.add_argument("--doctor", action="store_true", help="diagnostyka bramek i zależności")
    ap.add_argument("--lat", type=float, default=LOC_COAST[1])
    ap.add_argument("--lon", type=float, default=LOC_COAST[2])
    ap.add_argument("--tz", default="Europe/Warsaw")
    args = ap.parse_args()

    os.environ.pop("OWM_API_KEY", None)  # zero ruchu sieciowego

    if args.doctor:
        doctor(args.lat, args.lon, args.tz)
        return
    if args.list:
        print("Scenariusze:\n")
        for k, v in SCENARIOS.items():
            print(f"  {k:<20} {v['desc']}")
            print(f"  {'':<20} {v['loc'][0]}, {v['date']}")
        return

    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    langs = LANGS if args.lang == "all" else [args.lang]
    for n in names:
        if n not in SCENARIOS:
            sys.exit(f"nieznany scenariusz: {n} (użyj --list)")
        if args.date:
            SCENARIOS[n]["date"] = args.date
        for lg in langs:
            render(n, lg, args.card, args.save)


if __name__ == "__main__":
    main()
