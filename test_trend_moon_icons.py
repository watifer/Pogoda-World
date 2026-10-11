import copy
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from PIL import Image, ImageChops, ImageDraw

import image_generator
from i18n import STRINGS, t
from image_generator import (
    HERO_MOON_ICON_GAP,
    HERO_MOON_LINE_H,
    MARGIN,
    MOON_ICON_GAP,
    MOON_ICON_SIZE,
    WIDTH,
    _plan_days_card_layout,
    _plan_hero_moon_line,
    generate_weather_card,
    get_font,
)
from prepare_future_layout import prepare_future_layout_data
from prepare_layout import prepare_layout_data
from worth_knowing import (
    _REF_FULL_MOON_UTC,
    _moon_phase_info,
    build_worth_knowing,
    get_moon_phase_for_local_date,
)

PL_DIACRITICS = re.compile(r"[ąćęłńśźż]", re.IGNORECASE)
ALL_LANGS = ["pl", "en", "de", "fr", "es", "no"]


@pytest.fixture(autouse=True)
def _isolated_output_dir(tmp_path, monkeypatch):
    """Kieruje zapis PNG z generate_weather_card do katalogu tymczasowego (brak artefaktów runtime)."""
    monkeypatch.setattr(image_generator, "OUTPUT_DIR", str(tmp_path))


def _make_hour(dt_local: datetime, source: str = "openmeteo", **overrides) -> dict:
    h = {
        "time_local": dt_local.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": source,
        "temp_c": 12.0,
        "dewpoint_c": 6.0,
        "rh_pct": 70,
        "wind_kmh": 12.0,
        "gust_kmh": 18.0,
        "wind_dir_deg": 180,
        "clouds_pct": 10.0,
        "clouds_low_pct": 5.0,
        "clouds_mid_pct": 5.0,
        "clouds_high_pct": 0.0,
        "pressure_hpa": 1018,
        "uv_index": 2.0,
        "precip_mm": 0.0,
        "precip_eff_mm": 0.0,
        "precip_prob_pct": 5,
        "weather_code": 0,
        "weather_code_eff": 0,
        "symbol_code": "clearsky_day",
        "symbol_code_eff": "clearsky_day",
    }
    h.update(overrides)
    return h


def _build_trend_payload(
    start_dt: datetime,
    *,
    days_count: int = 14,
    lang: str = "pl",
    tz_name: str = "Europe/Warsaw",
    day_overrides: dict | None = None,
) -> dict:
    """Buduje syntetyczny payload dla /future o zadanej liczbie przyszłych dni."""
    tz = ZoneInfo(tz_name)
    base_dt = start_dt.astimezone(tz) if start_dt.tzinfo else start_dt.replace(tzinfo=tz)
    day_overrides = day_overrides or {}

    hours = []
    # Dzień 0 (dziś) + days_count kolejnych dni
    for d_off in range(0, days_count + 1):
        day_dt = (base_dt + timedelta(days=d_off)).replace(hour=0, minute=0, second=0, microsecond=0)
        date_key = day_dt.strftime("%Y-%m-%d")
        ovr = day_overrides.get(d_off, day_overrides.get(date_key, {}))
        for hr in (2, 8, 12, 15, 19, 22):
            dt_h = day_dt.replace(hour=hr)
            t_c = 8.0 if hr in (2, 22) else 16.0
            h_ovr = dict(temp_c=t_c)
            h_ovr.update(ovr)
            hours.append(_make_hour(dt_h, "openmeteo", **h_ovr))
            hours.append(_make_hour(dt_h, "yrno", **h_ovr))

    return {
        "lang": lang,
        "generated_at_local": base_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "model_updated_at_local": base_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "forecast_source": "OpenMeteo + Yr.no",
        "location": {
            "name": "Warszawa",
            "lat": 52.23,
            "lon": 21.01,
            "tz": tz_name,
        },
        "hours": hours,
        "daily_diag": {},
    }


def _region_colors(img: Image.Image, box: tuple[int, int, int, int]) -> list[tuple[int, int, int]]:
    crop = img.crop(box).convert("RGB")
    w, h = crop.size
    return [crop.getpixel((x, y)) for y in range(h) for x in range(w)]


def test_no_circular_imports_and_shared_meeus_helper():
    """Warunek 3 i 5: brak cyklu importów oraz pełna zgodność helpera z Meeusem z worth_knowing.py."""
    import prepare_future_layout

    assert prepare_future_layout.get_moon_phase_for_local_date is get_moon_phase_for_local_date

    # Pełnia referencyjna 2000-01-21 04:40 UTC
    res_full = get_moon_phase_for_local_date(date(2000, 1, 21), timezone.utc)
    ref_full = _moon_phase_info(_REF_FULL_MOON_UTC, tz=timezone.utc, phase="full")
    assert res_full is not None
    assert res_full["phase"] == "full"
    assert res_full["icon"] == "wk_full_moon"
    assert res_full["time_local_hm"] == ref_full["time_local_hm"] == "04:40"

    # Nów 2026-10-10 15:50 UTC = 17:50 Europe/Warsaw
    tz_waw = ZoneInfo("Europe/Warsaw")
    res_new = get_moon_phase_for_local_date(date(2026, 10, 10), tz_waw)
    assert res_new is not None
    assert res_new["phase"] == "new"
    assert res_new["icon"] == "wk_new_moon"
    assert res_new["time_local_hm"] == "17:50"


def test_inline_full_moon_at_right_edge():
    """1. Inline full: krótki tekst dnia -> ikona pełni na stałej pozycji DESC_RIGHT - MOON_ICON_SIZE."""
    tz_waw = ZoneInfo("Europe/Warsaw")
    # Start 2026-10-20 -> w oknie 14 dni wypada pełnia 2026-10-26 (brak nowiu w tym oknie)
    now = datetime(2026, 10, 20, 10, 0, tzinfo=tz_waw)
    payload = _build_trend_payload(now, days_count=14, lang="pl")
    data = prepare_future_layout_data(payload, now=now)

    # Dzień pełni (26.10) ma krótki opis ("Słonecznie") i drugiego dnia ustawmy jeszcze krótszy opis,
    # by potwierdzić, że pozycja X zależy od DESC_RIGHT, a nie od długości tekstu.
    full_days = [i for i, d in enumerate(data["next_days"]) if d.get("moon_phase") == "full"]
    assert len(full_days) == 1
    full_idx = full_days[0]
    assert data["next_days"][full_idx]["moon_icon"] == "wk_full_moon"
    assert data["next_days"][full_idx]["moon_date_short"] == "26.10"

    dummy_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    cx1, cx2 = MARGIN, WIDTH - MARGIN
    plan = _plan_days_card_layout(dummy_draw, data["next_days"], cx1, cx2)

    expected_desc_right = cx2 - plan["CP"]
    expected_moon_x = expected_desc_right - plan["MOON_ICON_SIZE"]

    assert plan["DESC_RIGHT"] == expected_desc_right
    assert plan["MOON_X"] == expected_moon_x
    assert plan["hero_moon_items"] == []

    row_plan = plan["rows"][full_idx]
    assert row_plan["moon_inline"] is True
    assert row_plan["moon_phase"] == "full"
    assert row_plan["moon_icon"] == "wk_full_moon"
    assert row_plan["moon_x"] == plan["DESC_RIGHT"] - plan["MOON_ICON_SIZE"]
    assert plan["C_DESC"] + row_plan["text_w"] + plan["MOON_ICON_GAP"] <= row_plan["moon_x"]

    # Renderujemy kartę i sprawdzamy piksele ikony pełni przy prawej krawędzi wiersza
    data_without_moon = copy.deepcopy(data)
    for d in data_without_moon["next_days"]:
        for k in ("moon_phase", "moon_icon", "moon_date_short", "moon_hero_label"):
            d.pop(k, None)

    img_without = Image.open(generate_weather_card(data_without_moon)).copy()
    img_with = Image.open(generate_weather_card(data)).copy()

    # Brak fallbacku do Hero -> wysokość obrazu identyczna
    assert img_with.size == img_without.size == (WIDTH, img_without.size[1])

    diff_bbox = ImageChops.difference(img_with, img_without).getbbox()
    assert diff_bbox is not None
    assert diff_bbox[0] >= row_plan["moon_x"]
    assert diff_bbox[2] <= row_plan["moon_x"] + plan["MOON_ICON_SIZE"]


def test_inline_new_moon_at_right_edge():
    """2. Inline new: krótki tekst dnia -> ikona nowiu na stałej pozycji DESC_RIGHT - MOON_ICON_SIZE."""
    tz_waw = ZoneInfo("Europe/Warsaw")
    # Start 2026-10-05 -> w oknie 14 dni (06.10..19.10) wypada nów 2026-10-10
    now = datetime(2026, 10, 5, 10, 0, tzinfo=tz_waw)
    payload = _build_trend_payload(now, days_count=14, lang="pl")
    data = prepare_future_layout_data(payload, now=now)

    new_days = [i for i, d in enumerate(data["next_days"]) if d.get("moon_phase") == "new"]
    assert len(new_days) == 1
    new_idx = new_days[0]
    assert data["next_days"][new_idx]["moon_icon"] == "wk_new_moon"
    assert data["next_days"][new_idx]["moon_date_short"] == "10.10"

    dummy_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    cx1, cx2 = MARGIN, WIDTH - MARGIN
    plan = _plan_days_card_layout(dummy_draw, data["next_days"], cx1, cx2)

    assert plan["hero_moon_items"] == []
    row_plan = plan["rows"][new_idx]
    assert row_plan["moon_inline"] is True
    assert row_plan["moon_phase"] == "new"
    assert row_plan["moon_icon"] == "wk_new_moon"
    assert row_plan["moon_x"] == plan["DESC_RIGHT"] - plan["MOON_ICON_SIZE"]

    data_without_moon = copy.deepcopy(data)
    for d in data_without_moon["next_days"]:
        for k in ("moon_phase", "moon_icon", "moon_date_short", "moon_hero_label"):
            d.pop(k, None)

    img_without = Image.open(generate_weather_card(data_without_moon)).copy()
    img_with = Image.open(generate_weather_card(data)).copy()
    diff_bbox = ImageChops.difference(img_with, img_without).getbbox()
    assert diff_bbox is not None
    assert diff_bbox[0] >= row_plan["moon_x"]
    assert diff_bbox[2] <= row_plan["moon_x"] + plan["MOON_ICON_SIZE"]


def test_long_text_triggers_fallback_to_hero():
    """3. Fallback do Hero przy długim tekście: gdy tekst + odstęp + ikona > dostępna szerokość."""
    tz_waw = ZoneInfo("Europe/Warsaw")
    now = datetime(2026, 10, 20, 10, 0, tzinfo=tz_waw)
    payload = _build_trend_payload(now, days_count=14, lang="pl")
    data = prepare_future_layout_data(payload, now=now)

    full_idx = next(i for i, d in enumerate(data["next_days"]) if d.get("moon_phase") == "full")
    long_desc = "Intensywne opady deszczu ze śniegiem oraz bardzo silny, porywisty wiatr"
    data["next_days"][full_idx]["precip_badge"] = long_desc

    dummy_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    f_line = get_font("Inter-Regular.ttf", 30)
    cx1, cx2 = MARGIN, WIDTH - MARGIN
    plan = _plan_days_card_layout(dummy_draw, data["next_days"], cx1, cx2)

    raw_w = dummy_draw.textlength(long_desc, font=f_line)
    assert raw_w > plan["avail_before_moon"]

    row_plan = plan["rows"][full_idx]
    assert row_plan["moon_inline"] is False
    assert row_plan["moon_x"] is None
    # Wiersz zachowuje pełną szerokość kolumny opisów (DESC_RIGHT - C_DESC)
    assert row_plan["desc_avail"] == plan["DESC_RIGHT"] - plan["C_DESC"]

    assert len(plan["hero_moon_items"]) == 1
    assert plan["hero_moon_items"][0]["phase"] == "full"
    assert plan["hero_moon_items"][0]["icon"] == "wk_full_moon"
    assert plan["hero_moon_items"][0]["label"] == "Pełnia 26.10"


def test_single_moon_icon_in_hero():
    """4. Jedna ikona w Hero: powiększa nagłówek o 1 linię (HERO_MOON_LINE_H) i rysuje 1 element."""
    tz_waw = ZoneInfo("Europe/Warsaw")
    # Start 2026-10-09 -> w 14 dniach mamy Nów 10.10 (długi opis -> Hero) i Pełnię 26.10 poza oknem 14 dni
    # Użyjmy okna 18 dni lub startu 2026-10-13? Dla startu 2026-10-09 + 14 dni (10.10..23.10) jest 1 faza (Nów 10.10),
    # a dla startu 2026-10-12 + 14 dni (13.10..26.10) jest 1 faza (Pełnia 26.10).
    # Dodatkowo przetestujmy okno mające jednocześnie 1 fazę inline i 1 fazę w Hero!
    now = datetime(2026, 10, 9, 10, 0, tzinfo=tz_waw)
    payload = _build_trend_payload(now, days_count=14, lang="pl")
    data = prepare_future_layout_data(payload, now=now)

    # Dodajmy na koniec dzień 26.10 (pełnia) z krótkim opisem (inline), a w dniu 10.10 (nów) dajmy długi opis (Hero)
    new_idx = next(i for i, d in enumerate(data["next_days"]) if d.get("moon_phase") == "new")
    data["next_days"][new_idx]["precip_badge"] = (
        "Intensywne opady deszczu i bardzo silne porywy wiatru w ciągu dnia"
    )
    data["next_days"].append({
        "name": "Pn",
        "label": "Pn 26.10",
        "icon": "wk_clear",
        "temp_min": 5,
        "temp_max": 12,
        "precip_badge": None,
        "descriptor": "Słonecznie",
        "severity": "normal",
        "moon_phase": "full",
        "moon_icon": "wk_full_moon",
        "moon_date_short": "26.10",
        "moon_hero_label": "Pełnia 26.10",
    })

    dummy_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    cx1, cx2 = MARGIN, WIDTH - MARGIN
    plan = _plan_days_card_layout(dummy_draw, data["next_days"], cx1, cx2)

    # Nów poszedł do Hero, a Pełnia została inline
    assert plan["rows"][new_idx]["moon_inline"] is False
    assert plan["rows"][-1]["moon_inline"] is True
    assert len(plan["hero_moon_items"]) == 1
    assert plan["hero_moon_items"][0]["label"] == "Nów 10.10"

    # Porównujemy wysokość karty z wersją, w której obie fazy mieszczą się inline
    data_all_inline = copy.deepcopy(data)
    data_all_inline["next_days"][new_idx]["precip_badge"] = None
    data_all_inline["next_days"][new_idx]["descriptor"] = "Słonecznie"

    img_inline = Image.open(generate_weather_card(data_all_inline)).copy()
    img_one_hero = Image.open(generate_weather_card(data)).copy()

    assert img_one_hero.size[0] == WIDTH
    assert img_one_hero.size[1] - img_inline.size[1] == HERO_MOON_LINE_H


def test_two_moon_icons_in_single_hero_line_all_languages():
    """5 & Warunek 2: Dwie ikony w jednej linii Hero, kolejność chronologiczna, wszystkie 6 języków."""
    dummy_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    f_ctx = get_font("Inter-Regular.ttf", 32)
    cx1, cx2 = MARGIN, WIDTH - MARGIN

    for lang in ALL_LANGS:
        days = [
            {
                "name": "Day1",
                "label": "Sat 10.10",
                "icon": "wk_rain",
                "temp_min": 6,
                "temp_max": 11,
                "precip_badge": "Very heavy rain showers and strong wind gusts throughout the entire day",
                "descriptor": None,
                "severity": "caution",
                "moon_phase": "new",
                "moon_icon": "wk_new_moon",
                "moon_date_short": "10.10",
                "moon_hero_label": t(lang, "trend_moon_new_date", date="10.10"),
            },
            {
                "name": "Day2",
                "label": "Mon 26.10",
                "icon": "wk_rain",
                "temp_min": 4,
                "temp_max": 9,
                "precip_badge": "Continuous heavy precipitation and stormy wind gusts all day long",
                "descriptor": None,
                "severity": "caution",
                "moon_phase": "full",
                "moon_icon": "wk_full_moon",
                "moon_date_short": "26.10",
                "moon_hero_label": t(lang, "trend_moon_full_date", date="26.10"),
            },
        ]

        plan = _plan_days_card_layout(dummy_draw, days, cx1, cx2)
        assert len(plan["hero_moon_items"]) == 2
        # Kolejność chronologiczna zgodna z kolejnością dni (10.10 przed 26.10)
        assert [it["phase"] for it in plan["hero_moon_items"]] == ["new", "full"]
        assert [it["label"] for it in plan["hero_moon_items"]] == [
            t(lang, "trend_moon_new_date", date="10.10"),
            t(lang, "trend_moon_full_date", date="26.10"),
        ]

        hero_plan = _plan_hero_moon_line(
            dummy_draw, plan["hero_moon_items"], x=MARGIN, max_width=WIDTH - 2 * MARGIN, font=f_ctx
        )
        assert len(hero_plan["items"]) == 2
        assert len(hero_plan["separators"]) == 1

        first, second = hero_plan["items"]
        sep = hero_plan["separators"][0]

        # Brak kolizji wewnątrz linii Hero oraz zachowanie marginesów karty
        assert first["icon_x"] == MARGIN
        assert first["text_x"] == first["icon_x"] + MOON_ICON_SIZE + HERO_MOON_ICON_GAP
        assert first["right_edge"] <= sep["x"]
        assert sep["x"] < second["icon_x"]
        assert second["text_x"] == second["icon_x"] + MOON_ICON_SIZE + HERO_MOON_ICON_GAP
        assert second["right_edge"] == hero_plan["right_edge"]
        assert hero_plan["right_edge"] <= WIDTH - MARGIN, (
            f"Język {lang}: linia Hero z 2 fazami wychodzi poza prawy margines ({hero_plan['right_edge']} > {WIDTH - MARGIN})"
        )

    # Sprawdzenie, że 2 fazy w Hero zajmują dokładnie 1 linię wysokości (tyle samo co 1 faza w Hero)
    card_base = {
        "city": "Warszawa",
        "weekday": "Prognoza 14-dniowa",
        "date": "",
        "report_type": "dane z 12:00",
        "main_icon": "wk_rain",
        "temp_range": "4°..11°",
        "summary": "Przewaga opadów w kolejnych dniach.",
        "context_line": "",
        "today_blocks": [],
        "alerts": [],
        "worth_knowing": None,
        "weekend_teaser": None,
        "section_title": "",
        "next_days_title": "Trend na kolejne 14 dni",
        "next_days": [
            {
                "name": "Sob",
                "label": "Sob 10.10",
                "icon": "wk_rain",
                "temp_min": 6,
                "temp_max": 11,
                "precip_badge": "Intensywne opady deszczu ze śniegiem oraz silny, porywisty wiatr",
                "descriptor": None,
                "severity": "caution",
                "moon_phase": "new",
                "moon_icon": "wk_new_moon",
                "moon_date_short": "10.10",
                "moon_hero_label": "Nów 10.10",
            },
            {
                "name": "Pn",
                "label": "Pn 26.10",
                "icon": "wk_rain",
                "temp_min": 4,
                "temp_max": 9,
                "precip_badge": "Intensywne opady deszczu ze śniegiem oraz silny, porywisty wiatr",
                "descriptor": None,
                "severity": "caution",
                "moon_phase": "full",
                "moon_icon": "wk_full_moon",
                "moon_date_short": "26.10",
                "moon_hero_label": "Pełnia 26.10",
            },
        ],
        "source_label": "Źródło:",
    }

    card_one_hero = copy.deepcopy(card_base)
    card_one_hero["next_days"][1]["precip_badge"] = None
    card_one_hero["next_days"][1]["descriptor"] = "Słonecznie"

    img_two = Image.open(generate_weather_card(card_base)).copy()
    img_one = Image.open(generate_weather_card(card_one_hero)).copy()
    assert img_two.size == img_one.size


def test_no_moon_phase_in_window():
    """6. Brak fazy: okno dni bez pełni i bez nowiu -> brak ikon księżyca i bez zmian wysokości."""
    tz_waw = ZoneInfo("Europe/Warsaw")
    # Między nowiem 10.10 a pełnią 26.10: okno 10 dni od 11.10 (12.10..21.10) nie ma żadnej fazy
    now = datetime(2026, 10, 11, 10, 0, tzinfo=tz_waw)
    payload = _build_trend_payload(now, days_count=10, lang="pl")
    data = prepare_future_layout_data(payload, now=now)

    assert len(data["next_days"]) == 10
    for d in data["next_days"]:
        assert "moon_phase" not in d
        assert "moon_icon" not in d
        assert "moon_hero_label" not in d

    dummy_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    plan = _plan_days_card_layout(dummy_draw, data["next_days"], MARGIN, WIDTH - MARGIN)
    assert plan["hero_moon_items"] == []
    assert all(not r["has_moon"] and not r["moon_inline"] and r["moon_x"] is None for r in plan["rows"])


def test_local_date_and_timezone_boundaries_and_weather_independence():
    """7 & Warunek 5: Granice lokalnej daty/strefy czasowej oraz brak heurystyk pogodowych w /trend."""
    # Pełnia 2026-10-26 wypada o 04:12 UTC:
    # - Europe/Warsaw (UTC+1): 2026-10-26 05:12 -> dzień 26.10
    # - America/New_York (UTC-4): 2026-10-26 00:12 -> dzień 26.10
    # - Pacific/Honolulu (UTC-10): 2026-10-25 18:12 -> dzień 25.10
    tz_waw = ZoneInfo("Europe/Warsaw")
    tz_nyc = ZoneInfo("America/New_York")
    tz_hnl = ZoneInfo("Pacific/Honolulu")

    assert get_moon_phase_for_local_date(date(2026, 10, 26), tz_waw)["phase"] == "full"
    assert get_moon_phase_for_local_date(date(2026, 10, 25), tz_waw) is None

    assert get_moon_phase_for_local_date(date(2026, 10, 26), tz_nyc)["phase"] == "full"
    assert get_moon_phase_for_local_date(date(2026, 10, 25), tz_nyc) is None

    assert get_moon_phase_for_local_date(date(2026, 10, 25), tz_hnl)["phase"] == "full"
    assert get_moon_phase_for_local_date(date(2026, 10, 26), tz_hnl) is None

    # Sprawdzenie na pełnym prepare_future_layout_data z 100% zachmurzeniem i ulewą (brak bramek pogodowych)
    heavy_rain_ovr = {
        "clouds_pct": 100.0,
        "clouds_low_pct": 100.0,
        "precip_mm": 5.0,
        "precip_eff_mm": 5.0,
        "precip_prob_pct": 95,
        "weather_code": 65,
        "symbol_code": "heavyrain",
    }
    now_hnl = datetime(2026, 10, 20, 10, 0, tzinfo=tz_hnl)
    payload_hnl = _build_trend_payload(
        now_hnl,
        days_count=7,
        lang="en",
        tz_name="Pacific/Honolulu",
        day_overrides={"2026-10-25": heavy_rain_ovr, "2026-10-26": heavy_rain_ovr},
    )
    data_hnl = prepare_future_layout_data(payload_hnl, now=now_hnl)
    full_labels_hnl = [d["moon_date_short"] for d in data_hnl["next_days"] if d.get("moon_phase") == "full"]
    assert full_labels_hnl == ["25.10"]

    now_waw = datetime(2026, 10, 20, 10, 0, tzinfo=tz_waw)
    payload_waw = _build_trend_payload(
        now_waw,
        days_count=7,
        lang="pl",
        tz_name="Europe/Warsaw",
        day_overrides={"2026-10-25": heavy_rain_ovr, "2026-10-26": heavy_rain_ovr},
    )
    data_waw = prepare_future_layout_data(payload_waw, now=now_waw)
    full_labels_waw = [d["moon_date_short"] for d in data_waw["next_days"] if d.get("moon_phase") == "full"]
    assert full_labels_waw == ["26.10"]


def test_no_collision_no_overflow_no_mutation_and_dynamic_days_count():
    """8 & Warunki 1, 4, 5, 6: Brak kolizji, brak wyjścia poza kartę, brak mutacji i dynamiczna długość next_days."""
    tz_waw = ZoneInfo("Europe/Warsaw")
    now = datetime(2026, 10, 20, 10, 0, tzinfo=tz_waw)

    # Pełny trend zachowuje dokładnie 14 dni
    payload_14 = _build_trend_payload(now, days_count=15, lang="pl")
    data_14 = prepare_future_layout_data(payload_14, now=now)
    assert len(data_14["next_days"]) == 14

    # Krótsze dane (np. 6 dni) działają bez błędów
    payload_6 = _build_trend_payload(now, days_count=6, lang="pl")
    data_6 = prepare_future_layout_data(payload_6, now=now)
    assert len(data_6["next_days"]) == 6
    assert any(d.get("moon_phase") == "full" for d in data_6["next_days"])

    # Dodajmy weekend_teaser, żeby upewnić się, że dane nie przeciekają między kartami
    data_6["weekend_teaser"] = {
        "title": "W ten weekend",
        "sat": {"label": "Sob 24.10", "icon": "wk_clear", "temp_min": 7, "temp_max": 14, "desc": "Słonecznie"},
        "sun": {"label": "Ndz 25.10", "icon": "wk_clear", "temp_min": 8, "temp_max": 15, "desc": "Pogodnie"},
    }

    snapshot_before = copy.deepcopy(data_6)
    out_path = generate_weather_card(data_6)

    # Brak mutacji słownika wejściowego i next_days (brak _moon_inline itp.)
    assert data_6 == snapshot_before
    for d in data_6["next_days"]:
        assert not any(k.startswith("_") for k in d.keys())

    img = Image.open(out_path)
    assert img.size[0] == WIDTH

    # Sprawdzamy, że wszystkie ikony inline trzymają stałą pozycję przy prawym marginesie i nie kolidują z tekstem
    dummy_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    cx1, cx2 = MARGIN, WIDTH - MARGIN
    plan = _plan_days_card_layout(dummy_draw, data_6["next_days"], cx1, cx2)
    for r in plan["rows"]:
        if r["moon_inline"]:
            assert r["moon_x"] == plan["DESC_RIGHT"] - plan["MOON_ICON_SIZE"]
            assert r["moon_x"] + plan["MOON_ICON_SIZE"] == plan["DESC_RIGHT"] <= WIDTH - MARGIN
            assert plan["C_DESC"] + r["text_w"] + plan["MOON_ICON_GAP"] <= r["moon_x"]

    # Sprawdzenie, że /day i WK pozostają nienaruszone (brak pól moon_phase w next_days karty /day)
    day_data = prepare_layout_data(payload_6, now=now)
    for d in day_data.get("next_days", []):
        assert "moon_phase" not in d
    wk_res = build_worth_knowing(payload_6, blocks=[], alerts=[])
    assert wk_res is None or isinstance(wk_res, dict)


def test_pl_and_en_localization():
    """9. Lokalizacja PL i EN dla etykiet pełni/nowiu w /trend oraz pełnego renderowania."""
    tz_waw = ZoneInfo("Europe/Warsaw")

    # Okno obejmujące nów (10.10): start 2026-10-05
    now_new = datetime(2026, 10, 5, 10, 0, tzinfo=tz_waw)
    data_new_pl = prepare_future_layout_data(_build_trend_payload(now_new, days_count=7, lang="pl"), now=now_new)
    data_new_en = prepare_future_layout_data(_build_trend_payload(now_new, days_count=7, lang="en"), now=now_new)

    new_day_pl = next(d for d in data_new_pl["next_days"] if d.get("moon_phase") == "new")
    new_day_en = next(d for d in data_new_en["next_days"] if d.get("moon_phase") == "new")
    assert new_day_pl["moon_hero_label"] == "Nów 10.10"
    assert new_day_en["moon_hero_label"] == "New moon 10.10"
    assert not PL_DIACRITICS.search(new_day_en["moon_hero_label"])

    # Okno obejmujące pełnię (26.10): start 2026-10-20
    now_full = datetime(2026, 10, 20, 10, 0, tzinfo=tz_waw)
    data_full_pl = prepare_future_layout_data(_build_trend_payload(now_full, days_count=7, lang="pl"), now=now_full)
    data_full_en = prepare_future_layout_data(_build_trend_payload(now_full, days_count=7, lang="en"), now=now_full)

    full_day_pl = next(d for d in data_full_pl["next_days"] if d.get("moon_phase") == "full")
    full_day_en = next(d for d in data_full_en["next_days"] if d.get("moon_phase") == "full")
    assert full_day_pl["moon_hero_label"] == "Pełnia 26.10"
    assert full_day_en["moon_hero_label"] == "Full moon 26.10"
    assert not PL_DIACRITICS.search(full_day_en["moon_hero_label"])

    # Wymuszenie fallbacku do Hero i wyrenderowanie kart PL oraz EN
    full_day_pl["precip_badge"] = "Intensywne opady deszczu ze śniegiem oraz silny, porywisty wiatr"
    full_day_en["precip_badge"] = "Continuous heavy rain showers and very strong wind gusts all day"

    img_pl = Image.open(generate_weather_card(data_full_pl))
    img_en = Image.open(generate_weather_card(data_full_en))
    assert img_pl.size[0] == WIDTH
    assert img_en.size[0] == WIDTH

    # Wszystkie 6 języków ma zdefiniowane oba klucze w STRINGS
    for lang in ALL_LANGS:
        assert "trend_moon_full_date" in STRINGS[lang]
        assert "trend_moon_new_date" in STRINGS[lang]
        assert "{date}" in STRINGS[lang]["trend_moon_full_date"]
        assert "{date}" in STRINGS[lang]["trend_moon_new_date"]
