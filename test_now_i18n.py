"""Testy regresyjne dla /now i /day: i18n, gołoledź, VS16, reuse OWM.

Uruchomienie: pytest test_now_i18n.py -v

Chronią przed czterema błędami, które realnie trafiły na produkcję:
  1. radar gołoledzi nigdy się nie odpalał (nieosiągalny warunek na family=="rain"),
  2. alert gołoledzi zostawał po polsku we wszystkich językach,
  3. Hero było tłumaczone dwa razy (ES: "Soleado a 14:00" -> "Soleado y 14:00"),
  4. /now wykonywał blokujący call do OWM mimo gotowego payload["owm_current"].
"""
import re
from datetime import datetime, timedelta

import pytest

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo

import prepare_now_layout
from prepare_now_layout import prepare_now_layout_data
from prepare_layout import prepare_layout_data
from forecast_text import is_freezing_precip
from i18n import t, translate_weather_text

TZ = "Europe/Warsaw"
LANGS = ["pl", "en", "de", "fr", "es", "no"]
FOREIGN = [l for l in LANGS if l != "pl"]
# "ó" celowo pominięte: występuje też w ES/FR (np. "Precipitación").
PL_DIACRITICS = re.compile(r"[ąćęłńśźż]", re.IGNORECASE)


# ─────────────────────────── helpers ───────────────────────────

def _hour(dt, **over):
    h = {
        "time_local": dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "openmeteo",
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
        "wind_dir_deg": 270,
        "rh_pct": 70,
        "dewpoint_c": 3.0,
        "pressure_hpa": 1015,
        "symbol_code": "clearsky_day",
        "weather_code": 0,
    }
    h.update(over)
    return h


def _payload(hours, lang, **extra):
    p = {
        "location": {"name": "Warszawa", "lat": 52.23, "lon": 21.01, "tz": TZ},
        "lang": lang,
        "hours": hours,
        "forecast_source": "OpenMeteo + Yr.no",
        "model_updated_at_local": "2026-01-15T03:00:00",
        "alerts": [],
    }
    p.update(extra)
    return p


START = datetime(2026, 1, 15, 12, 0, tzinfo=ZoneInfo(TZ))


def _clear_then_cloudy():
    cloudy = {"clouds_pct": 95.0, "clouds_low_pct": 80.0, "clouds_mid_pct": 15.0,
              "symbol_code": "cloudy", "weather_code": 3}
    return [_hour(START + timedelta(hours=i), **({} if i < 2 else cloudy)) for i in range(12)]


def _freezing_hours():
    frz = {"temp_c": 0.3, "precip_mm": 0.6, "precip_eff_mm": 0.6, "rh_pct": 96,
           "dewpoint_c": -0.1, "clouds_pct": 95.0, "clouds_low_pct": 85.0,
           "clouds_mid_pct": 10.0, "symbol_code": "lightrain", "weather_code": 66}
    return [_hour(START + timedelta(hours=i), **(frz if i < 4 else {"temp_c": 0.5}))
            for i in range(12)]


def _now_card(hours, lang):
    return prepare_now_layout_data(_payload(hours, lang), now=START + timedelta(minutes=5))


def _all_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _all_strings(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _all_strings(v)


# ─────────────────── 1. detektor gołoledzi żyje ───────────────────

class TestFreezingDetector:
    def test_wmo_freezing_rain_codes_detected(self):
        for code in (56, 57, 66, 67):
            assert is_freezing_precip(0.6, 0.3, symbol_code="lightrain",
                                      weather_code=code, rh_pct=96, dewpoint_c=-0.1), code

    def test_cold_rain_with_saturated_air(self):
        assert is_freezing_precip(0.6, 0.5, symbol_code="rain", weather_code=61,
                                  rh_pct=96, dewpoint_c=0.2)

    def test_dry_air_is_not_freezing(self):
        assert not is_freezing_precip(0.6, 0.5, symbol_code="rain", weather_code=61,
                                      rh_pct=50, dewpoint_c=-8.0)

    def test_warm_rain_is_not_freezing(self):
        assert not is_freezing_precip(2.0, 9.0, symbol_code="rain", weather_code=61,
                                      rh_pct=99, dewpoint_c=8.0)

    def test_trace_precip_is_not_freezing(self):
        assert not is_freezing_precip(0.01, 0.1, weather_code=66, rh_pct=99)

    def test_now_card_emits_the_alert(self):
        # Regresja: przed poprawką warunek był nieosiągalny i context_line był None.
        card = _now_card(_freezing_hours(), "pl")
        assert card["context_line"], "radar gołoledzi nie odpalił się w /now"
        assert "gołoledzi" in card["context_line"]


# ─────────────────── 2. alert gołoledzi jest tłumaczony ───────────────────

class TestFreezingI18n:
    @pytest.mark.parametrize("lang", FOREIGN)
    def test_now_alert_is_not_polish(self, lang):
        line = _now_card(_freezing_hours(), lang)["context_line"]
        assert line
        assert not PL_DIACRITICS.search(line), f"[{lang}] polski tekst w /now: {line}"
        assert line == t(lang, "freezing_alert", when=line.split("(")[-1].rstrip(").)"))

    @pytest.mark.parametrize("lang", FOREIGN)
    def test_day_alert_is_not_polish(self, lang):
        now = datetime(2026, 1, 15, 8, 0, tzinfo=ZoneInfo(TZ))
        hours = []
        for i in range(48):
            dt = now.replace(hour=0) + timedelta(hours=i)
            frz = dt.date() == now.date() and 14 <= dt.hour <= 17
            hours.append(_hour(dt, **({"temp_c": 0.3, "precip_mm": 0.6, "precip_eff_mm": 0.6,
                                       "rh_pct": 96, "dewpoint_c": -0.1, "weather_code": 66,
                                       "symbol_code": "lightrain", "clouds_pct": 90.0,
                                       "clouds_low_pct": 80.0, "clouds_mid_pct": 10.0}
                                      if frz else {"temp_c": 3.0, "clouds_pct": 90.0,
                                                   "clouds_low_pct": 80.0, "clouds_mid_pct": 10.0,
                                                   "symbol_code": "cloudy", "weather_code": 3})))
        card = prepare_layout_data(_payload(hours, lang), now=now)
        frozen = [a for a in card.get("alerts", []) if "⚠" in a]
        assert frozen, "brak alertu gołoledzi w /day"
        assert not PL_DIACRITICS.search(frozen[0]), f"[{lang}] polski alert: {frozen[0]}"


# ─────────────────── 3. Hero tłumaczone dokładnie raz ───────────────────

class TestHeroSingleTranslation:
    @pytest.mark.parametrize("lang", FOREIGN)
    def test_summary_is_idempotent(self, lang):
        """Drugi przebieg tłumacza nie może już nic zmienić."""
        summary = _now_card(_clear_then_cloudy(), lang)["summary"]
        assert translate_weather_text(summary, lang) == summary, \
            f"[{lang}] Hero nie jest idempotentne: {summary!r}"

    def test_spanish_preposition_survives(self):
        # Regresja: "Soleado hasta 14:00" -> drugi przebieg robił "Soleado y 14:00".
        summary = _now_card(_clear_then_cloudy(), "es")["summary"]
        assert "hasta" in summary and " y 15:00" not in summary, summary

    def test_english_uses_until_not_to(self):
        summary = _now_card(_clear_then_cloudy(), "en")["summary"]
        assert "until" in summary, summary

    @pytest.mark.parametrize("lang", FOREIGN)
    def test_prefix_comes_from_i18n(self, lang):
        # Regresja: REPLACEMENTS["fr"] mapował "obecnie" -> "Currently".
        summary = _now_card(_clear_then_cloudy(), lang)["summary"]
        assert summary.startswith(t(lang, "currently")), summary

    @pytest.mark.parametrize("lang", FOREIGN)
    def test_no_placeholder_leaks(self, lang):
        card = _now_card(_clear_then_cloudy(), lang)
        for s in _all_strings(card):
            assert "\u2e24" not in s and "\u2e25" not in s, f"token przyimka wyciekł: {s!r}"


# ─────────────────── 4. brak polskich resztek i VS16 ───────────────────

class TestRenderSafety:
    @pytest.mark.parametrize("lang", FOREIGN)
    def test_no_vs16_in_card_payload(self, lang):
        card = _now_card(_freezing_hours(), lang)
        for s in _all_strings(card):
            assert "\ufe0f" not in s, f"VS16 w tekście karty: {s!r}"

    def test_card_strings_use_only_renderable_glyphs(self):
        """Karta jest rysowana wyłącznie fontem Inter — emoji spoza jego cmap
        wychodzą jako tofu z kodem hex (tak wpadło 🌬 w notkach nadmorskich)."""
        fontTools = pytest.importorskip("fontTools.ttLib")
        from i18n import STRINGS
        font = fontTools.TTFont("assets/fonts/Inter-Regular.ttf")
        supported = set()
        for table in font["cmap"].tables:
            supported.update(table.cmap.keys())

        offenders = {}
        for lang, kv in STRINGS.items():
            if not isinstance(kv, dict):
                continue
            for key, value in kv.items():
                if not isinstance(value, str):
                    continue
                for ch in value:
                    cp = ord(ch)
                    if cp >= 0x2000 and cp != 0xFE0F and cp not in supported:
                        offenders.setdefault(ch, []).append(f"{lang}.{key}")
        assert not offenders, f"znaki nieobsługiwane przez Inter: { {k: v[:2] for k, v in offenders.items()} }"

    def test_strip_vs16_is_recursive(self):
        pytest.importorskip("PIL")
        from image_generator import strip_vs16
        data = {"summary": "⚠️ x", "alerts": ["🌬️ y"],
                "today_blocks": [{"extra_lines": [{"spans": [{"text": "⚠️ z"}]}]}]}
        cleaned = strip_vs16(data)
        assert "\ufe0f" not in "".join(_all_strings(cleaned))


# ─────────────────── 5. OWM: reuse zamiast nowego calla ───────────────────

class TestOwmReuse:
    def test_payload_snapshot_is_reused(self, monkeypatch):
        def _boom(*a, **kw):
            raise AssertionError("/now nie powinien wołać OWM, gdy payload ma owm_current")

        monkeypatch.setattr(prepare_now_layout, "get_current_weather", _boom)
        payload = _payload(_clear_then_cloudy(), "pl",
                           owm_current={"dt": int(START.timestamp()), "clouds": 10})
        card = prepare_now_layout_data(payload, now=START + timedelta(minutes=5))
        assert card["summary"]

    def test_fetch_failure_does_not_break_card(self, monkeypatch):
        monkeypatch.setattr(prepare_now_layout, "get_current_weather",
                            lambda *a, **kw: (_ for _ in ()).throw(TimeoutError("down")))
        card = _now_card(_clear_then_cloudy(), "pl")
        assert card["summary"]
