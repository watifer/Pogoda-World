"""
Testy pytest dla forecast_text.py v3.1
Uruchomienie: pytest test_forecast.py -v
"""

import pytest
from forecast_text import (
    WxEvent, BlockForecast, build_block_copy,
    should_show_feels_like, build_feels_like_payload,
    merge_adjacent_same_kind, describe_precip,
    classify_precip, classify_from_api,
    fmt_temp_range, time_suffix, qualifies_feels_value,
    normalize_interval, normalize_block_and_events,
)


def make_block(label="Rano", hours="06–10", start=6, end=10,
               tmin=2, tmax=9, fmin=None, fmax=None,
               events=None, sky=None):
    return BlockForecast(label=label, hours_range=hours, start=start, end=end,
                         temp_min=tmin, temp_max=tmax,
                         feels_min=fmin, feels_max=fmax,
                         sky_label=sky, events=events or [])


# ═══════════════════════════════════════
# 1. Brak opadów → sky_label
# ═══════════════════════════════════════
class TestNoEvents:
    def test_sky_fallback(self):
        r = build_block_copy(make_block(sky="pogodnie"))
        assert r["primary_desc"] == "pogodnie"

    def test_empty(self):
        assert build_block_copy(make_block())["primary_desc"] == ""

    def test_separate_fields(self):
        r = build_block_copy(make_block(label="Rano", hours="06–10"))
        assert r["label"] == "Rano"
        assert r["hours"] == "06–10"


# ═══════════════════════════════════════
# 2. Deszcz cały blok
# ═══════════════════════════════════════
class TestWholeBlock:
    def test_rain(self):
        assert build_block_copy(make_block(events=[WxEvent("rain",6,10)]))["primary_desc"] == "deszcz"

    def test_snow(self):
        assert build_block_copy(make_block(events=[WxEvent("snow",6,10)]))["primary_desc"] == "śnieg"


# ═══════════════════════════════════════
# 3. Jedna godzina
# ═══════════════════════════════════════
class TestSingleHour:
    def test_03_04_night(self):
        b = make_block(label="Noc", hours="22–06", start=22, end=6,
                       events=[WxEvent("rain",3,4)])
        r = build_block_copy(b)
        assert "03–04" in r["primary_desc"]
        assert "03–03" not in r["primary_desc"]


# ═══════════════════════════════════════
# 4. Eskalacja
# ═══════════════════════════════════════
class TestEscalation:
    def test_rain_stronger(self):
        b = make_block(start=11, end=16, events=[
            WxEvent("light_rain",11,14), WxEvent("heavy_rain",14,16)])
        r = build_block_copy(b)
        assert "deszcz" in r["primary_desc"]
        assert any("silniej po 14" in e["text"] for e in r["extra_lines"])
        
    def test_snow_stronger(self):
        b = make_block(start=6, end=10, events=[
            WxEvent("light_snow", 6, 8),
            WxEvent("snow", 8, 10),
        ])
        r = build_block_copy(b)
        assert "śnieg" in r["primary_desc"]
        assert any("silniej po 08" in e["text"] for e in r["extra_lines"])

    def test_downpour_keeps_name(self):
        b = make_block(start=11, end=16, events=[
            WxEvent("light_rain", 11, 14),
            WxEvent("downpour", 14, 16),
        ])
        r = build_block_copy(b)
        assert "deszcz" in r["primary_desc"]
        assert any(
            "ulewa po 14" in e["text"] or "silniej po 14" in e["text"]
            for e in r["extra_lines"]
        )


# ═══════════════════════════════════════
# 5. Burze + deszcz + odczuwalna (priorytet + limit)
# ═══════════════════════════════════════
class TestStormAndFeels:
    def test_rain_storm_feels(self):
        """deszcz primary, burze extra[0], odczuwalna extra[1]."""
        b = make_block(label="Pop", hours="11–16", start=11, end=16,
                       tmin=5, tmax=12, fmin=-1, fmax=8,
                       events=[WxEvent("rain",11,15), WxEvent("storm",15,16)])
        r = build_block_copy(b)
        assert "deszcz" in r["primary_desc"]
        assert len(r["extra_lines"]) == 2
        assert r["extra_lines"][0]["type"] == "meta"
        assert "burze" in r["extra_lines"][0]["text"]
        assert r["extra_lines"][1]["type"] == "feels_like"
        assert "odcz." in r["extra_lines"][1]["text"]


# ═══════════════════════════════════════
# 6. Częściowy blok
# ═══════════════════════════════════════
class TestPartial:
    def test_snow_until(self):
        r = build_block_copy(make_block(events=[WxEvent("snow",6,8)]))
        assert "do 08" in r["primary_desc"]

    def test_rain_from(self):
        r = build_block_copy(make_block(events=[WxEvent("rain",8,10)]))
        assert "od 08" in r["primary_desc"]

    def test_middle(self):
        b = make_block(start=11, end=16, events=[WxEvent("rain",13,15)])
        assert "13–15" in build_block_copy(b)["primary_desc"]


# ═══════════════════════════════════════
# 7. Odczuwalna ukryta
# ═══════════════════════════════════════
class TestFeelsHidden:
    def test_diff_1(self):
        assert not should_show_feels_like(5, 10, 4, 9)

    def test_exact_2_shown(self):
        """diff=2 >= 2 → POKAZUJEMY (PUNKT 4 fix)."""
        assert qualifies_feels_value(5, 3, threshold=2)
        assert should_show_feels_like(5, 10, 3, 8)

    def test_feels_higher(self):
        assert not should_show_feels_like(5, 10, 7, 12)

    def test_equal(self):
        assert not should_show_feels_like(5, 10, 5, 10)

    def test_payload_none(self):
        t, s = build_feels_like_payload(5, 10, None, None)
        assert t is None and s == []

    def test_payload_higher(self):
        t, s = build_feels_like_payload(28, 32, 30, 35)
        assert t is None


# ═══════════════════════════════════════
# 8. Odczuwalna pokazana
# ═══════════════════════════════════════
class TestFeelsShown:
    def test_range(self):
        t, s = build_feels_like_payload(2, 9, -1, 7)
        assert t == "odcz. -1°/7°"
        assert s[0]["style"] == "feels_like_accent"

    def test_single(self):
        t, s = build_feels_like_payload(5, 5, 1, 1)
        assert t == "odcz. 1°"

    def test_partial_color(self):
        """temp 1/7, feels -1/7: min fiolet, max biały."""
        t, spans = build_feels_like_payload(1, 7, -1, 7)
        assert spans[1]["style"] == "feels_like_accent"  # -1°
        assert spans[2]["style"] == "default"             # /7°

    def test_partial_substitution(self):
        """
        temp 2/6, feels -1/5: 
        min spada o 3 (-1 wchodzi, fiolet), max spada o 1 (6 zostaje, białe).
        Wynik na ekranie: odcz. -1°/6°.
        """
        text, spans = build_feels_like_payload(2, 6, -1, 5)
        assert text == "odcz. -1°/6°"
        assert spans[1]["style"] == "feels_like_accent"  # -1°
        assert spans[2]["style"] == "default"             # /6°

# ═══════════════════════════════════════
# 9. Priorytet i spójność feels_like_text
# ═══════════════════════════════════════
class TestPriority:
    def test_precip_primary(self):
        b = make_block(tmin=2, tmax=9, fmin=-2, fmax=5,
                       events=[WxEvent("rain",6,10)])
        r = build_block_copy(b)
        assert r["primary_desc"] == "deszcz"

    def test_escalation_before_feels(self):
        b = make_block(label="P", hours="11–16", start=11, end=16,
                       tmin=2, tmax=9, fmin=-3, fmax=5,
                       events=[WxEvent("rain",11,15), WxEvent("storm",15,16)])
        r = build_block_copy(b)
        types = [e["type"] for e in r["extra_lines"]]
        assert types == ["meta", "feels_like"]

    def test_feels_dropped_when_full(self):
        """2 różne rodziny opadów: 1. → primary, 2. → meta.
        Zostaje 1 slot → feels wchodzi (limit 2 extra_lines)."""
        b = make_block(label="P", hours="11–16", start=11, end=16,
                       tmin=2, tmax=9, fmin=-5, fmax=3,
                       events=[WxEvent("rain",11,13), WxEvent("snow",13,16)])
        r = build_block_copy(b)
        types = [e["type"] for e in r["extra_lines"]]
        assert types == ["meta", "feels_like"]
        assert r["feels_like_text"] is not None
        assert "odcz." in r["feels_like_text"]
        assert len(r["feels_like_spans"]) > 0

    def test_feels_visible_when_room(self):
        """1 opad + odczuwalna → feels wchodzi."""
        b = make_block(tmin=5, tmax=12, fmin=0, fmax=8,
                       events=[WxEvent("rain",6,10)])
        r = build_block_copy(b)
        assert r["feels_like_text"] is not None
        assert any(e["type"] == "feels_like" for e in r["extra_lines"])


# ═══════════════════════════════════════
# 10. Skracanie
# ═══════════════════════════════════════
class TestFitting:
    def test_short(self):
        b = make_block(events=[WxEvent("light_rain",8,10)])
        assert len(build_block_copy(b, inline_max_chars=15)["primary_desc"]) <= 15

    def test_full(self):
        b = make_block(events=[WxEvent("light_rain",8,10)])
        assert "lekki deszcz" in build_block_copy(b, inline_max_chars=50)["primary_desc"]


# ═══════════════════════════════════════
# NOC PRZEZ PÓŁNOC (PUNKT 1)
# ═══════════════════════════════════════
class TestNightNormalization:
    def test_normalize_interval(self):
        assert normalize_interval(22, 6, 22) == (22, 30)
        assert normalize_interval(0, 2, 22) == (24, 26)
        assert normalize_interval(3, 4, 22) == (27, 28)
        assert normalize_interval(22, 2, 22) == (22, 26)

    def test_normalize_block_and_events(self):
        bs, be, evs = normalize_block_and_events(22, 6, [
            WxEvent("rain", 22, 24),
            WxEvent("rain", 0, 2),
        ])
        assert bs == 22 and be == 30
        assert evs[0].start == 22 and evs[0].end == 24
        assert evs[1].start == 24 and evs[1].end == 26

    def test_merge_across_midnight(self):
        """rain 22–24 + rain 0–2 → po normalizacji → rain 22–26 → merged."""
        _, _, evs = normalize_block_and_events(22, 6, [
            WxEvent("rain", 22, 24), WxEvent("rain", 0, 2)])
        merged = merge_adjacent_same_kind(evs)
        assert len(merged) == 1
        assert merged[0].start == 22 and merged[0].end == 26

    def test_describe_rain_across_midnight(self):
        """Blok 22–06, rain 22–24 + rain 0–2 → scalony → 'deszcz do 02'."""
        b = make_block(label="Noc", hours="22–06", start=22, end=6,
                       events=[WxEvent("rain",22,24), WxEvent("rain",0,2)])
        r = build_block_copy(b)
        assert "deszcz" in r["primary_desc"]
        assert "do 02" in r["primary_desc"]

    def test_event_02_04(self):
        b = make_block(label="Noc", hours="22–06", start=22, end=6,
                       events=[WxEvent("rain",2,4)])
        assert "02–04" in build_block_copy(b)["primary_desc"]

    def test_whole_night(self):
        b = make_block(label="Noc", hours="22–06", start=22, end=6,
                       events=[WxEvent("snow",22,6)])
        assert build_block_copy(b)["primary_desc"] == "śnieg"

    def test_22_to_02(self):
        b = make_block(label="Noc", hours="22–06", start=22, end=6,
                       events=[WxEvent("rain",22,2)])
        assert "do 02" in build_block_copy(b)["primary_desc"]

    def test_suffix_from_03(self):
        # Na znormalizowanej osi: block 22–30, event 27–30
        assert "od 03" in time_suffix(27, 30, 22, 30)


# ═══════════════════════════════════════
# MGŁA (PUNKT 5)
# ═══════════════════════════════════════
class TestFog:
    def test_fog_alone(self):
        """Mgła bez opadów → primary_desc."""
        b = make_block(events=[WxEvent("fog", 6, 10)])
        assert build_block_copy(b)["primary_desc"] == "mgła"

    def test_fog_partial(self):
        b = make_block(events=[WxEvent("fog", 6, 8)])
        r = build_block_copy(b)
        assert "mgła" in r["primary_desc"]
        assert "do 08" in r["primary_desc"]

    def test_fog_with_rain(self):
        """Deszcz + mgła → deszcz primary, mgła extra."""
        b = make_block(events=[WxEvent("rain",6,10), WxEvent("fog",6,8)])
        r = build_block_copy(b)
        assert "deszcz" in r["primary_desc"]
        assert any("mgła" in e["text"] for e in r["extra_lines"])

    def test_fog_not_in_precip_families(self):
        """Mgła nie jest w PRECIP_FAMILIES."""
        from forecast_text import PRECIP_FAMILIES
        assert "fog" not in PRECIP_FAMILIES


# ═══════════════════════════════════════
# KLASYFIKATOR + MERGE + TEMP
# ═══════════════════════════════════════
class TestClassify:
    def test_api_primary(self):
        assert classify_precip(0.5, 10, symbol_code="heavyrain_day") == "heavy_rain"

    def test_wmo(self):
        assert classify_precip(0.5, 10, weather_code=95) == "storm"

    def test_fallback(self):
        assert classify_precip(3.0, 10) == "rain"

    def test_api_overrides(self):
        assert classify_precip(0.3, 10, weather_code=95) == "storm"

    def test_none(self):
        assert classify_precip(0.05, 10) is None

    def test_fog_wmo(self):
        assert classify_precip(0, 10, weather_code=45) == "fog"


class TestMerge:
    def test_adjacent(self):
        m = merge_adjacent_same_kind([WxEvent("rain",6,8), WxEvent("rain",8,10)])
        assert len(m) == 1 and m[0].end == 10

    def test_different(self):
        assert len(merge_adjacent_same_kind([WxEvent("rain",6,8), WxEvent("snow",8,10)])) == 2


class TestTemp:
    def test_range(self): assert fmt_temp_range(-5, 3) == "-5°/3°"
    def test_single(self): assert fmt_temp_range(3, 3) == "3°"