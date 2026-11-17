"""Testy charakteryzujące martwe ścieżki — ETAP 2.

Zamrażają OBECNY stan ścieżek oznaczonych w audycie (rejestr etapu 1 §3.5,
raport końcowy §2 i §3) jako martwe / nieosiągalne:

  * ``diag_tag`` / ``diag_severity``: liczone w prepare_future_layout na obiekcie
    summary, ale NIE kopiowane do ``day_entry`` → nigdy nie renderowane;
    renderer (image_generator) czyta te pola i potrafi je narysować — gotowość
    renderera potwierdzana syntetycznym fixturem (to charakterystyka, nie
    docelowy UX — zob. dokument decyzji §3 „Testowanie martwych ścieżek");
  * ``DailyPick.marker`` (" *"): dopinany do ``day_entry["name"]``, renderer
    czyta ``label`` (fallback ``name``) — marker niewidoczny na karcie;
  * ``owm_nowcast.apply_cloud_correction``: funkcja wycofana, zero wywołań
    produkcyjnych (NIE usuwać jej — testy pilnują tylko stanu);
  * ``coastal_day_alert``: jedyny parametr i jedyny odczyt w worth_knowing,
    nikt go nie zapisuje — parametr nie ma wpływu na wynik;
  * ``model_agreement``: weather_payload zapisuje zawsze None; prepare_layout
    czyta ``payload.get("model_agreement") or {}``.

Testy NIE wolno czytać jako opisu zamierzonego UX — to zamrożenie stanu,
zanim etap 3+ zdecyduje o przywróceniu lub usunięciu ścieżek.
"""

import copy
import inspect
from datetime import datetime, timedelta

import pytest
from PIL import Image, ImageChops, ImageDraw

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo

import coast_detector
import image_generator
import owm_nowcast
import prepare_future_layout
import prepare_layout
import weather_payload
import worth_knowing
from owm_nowcast import apply_cloud_correction
from prepare_future_layout import prepare_future_layout_data

TZ = ZoneInfo("Europe/Warsaw")
# Piątek 2026-11-20; okno +1..+14 obejmuje pełnię 24.11 (używane w innych plikach).
NOW = datetime(2026, 11, 20, 10, 0, tzinfo=TZ)


@pytest.fixture
def no_coast(monkeypatch):
    monkeypatch.setattr(coast_detector, "get_or_compute_coast_signature_lazy",
                        lambda store, lat, lon, idx_factory: None)


def _hour(dt, source, temp, **over):
    h = {
        "time_local": dt.isoformat(timespec="minutes"),
        "source": source,
        "temp_c": temp,
        "precip_mm": 0.0,
        "precip_prob_pct": 0,
        "clouds_pct": 20.0,
        "clouds_low_pct": 10.0,
        "clouds_mid_pct": 10.0,
        "clouds_high_pct": 0.0,
        "wind_kmh": 10.0,
        "gust_kmh": 15.0,
        "wind_dir_deg": 270,
        "rh_pct": 60,
        "dewpoint_c": 2.0,
        "pressure_hpa": 1015,
        "symbol_code": "clearsky_day",
        "weather_code": 1,
    }
    h.update(over)
    return h


def _future_payload(**extra):
    hours = []
    for day_off in range(1, 15):
        day = NOW + timedelta(days=day_off)
        for i in range(24):
            hours.append(_hour(day.replace(hour=0) + timedelta(hours=i),
                               "openmeteo", 10.0))
            hours.append(_hour(day.replace(hour=0) + timedelta(hours=i),
                               "yrno", 9.0))
    p = {
        "location": {"name": "Wiązowna", "lat": 52.17, "lon": 21.32, "tz": "Europe/Warsaw"},
        "lang": "pl",
        "hours": hours,
        "forecast_source": "OpenMeteo + Yr.no",
        "model_updated_at_local": NOW.isoformat(timespec="minutes"),
        "alerts": [],
        "daily_diag": {},
    }
    p.update(extra)
    return p


VOLATILE_DAY = (NOW + timedelta(days=1)).strftime("%Y-%m-%d")
VOLATILE_DIAG = {
    # Tag wymaga |alt - base| >= 2: pick Yr -> alt = max_om = 12, base = temp_max
    # z rekordów Yr = 9 -> różnica 3.
    "max_om": 12.0, "max_yr": 20.0, "min_om": 5.0, "min_yr": 1.0,
    "spread_max": 8.0, "spread_min": 4.0, "spread": 8.0,
    "is_volatile": True, "n_om": 24, "n_yr": 24,
}


# ─────────────────── diag_tag / diag_severity ───────────────────

class TestDiagTagDeadPath:
    def test_tag_is_computed_on_summary_but_not_copied_to_day_entry(self, monkeypatch, no_coast):
        """Producent liczy tag na `summary`, ale nie kopiuje go do day_entry
        (prepare_future_layout.py:104-105 vs ~:400) — ścieżka martwa po stronie
        producenta. Layout z/bez volatility jest identyczny."""
        captured = {}
        orig = prepare_future_layout._build_day_summary

        def spy(hp, ts, **kw):
            s = orig(hp, ts, **kw)
            captured[ts] = s  # diag_tag dopisywany PO powrocie — trzymamy referencję
            return s

        monkeypatch.setattr(prepare_future_layout, "_build_day_summary", spy)

        plain = prepare_future_layout_data(_future_payload(), now=NOW)
        volatile = prepare_future_layout_data(
            _future_payload(daily_diag={
                # n_yr=24 (jak w VOLATILE_DIAG) utrzymuje wybór źródła na Yr —
                # wariant plain ma pusty daily_diag, co daje OM i marker " *".
                # Layouty porównujemy po ujednoliceniu źródła: różnić może się
                # wyłącznie obecność diag_tag na summary, nigdy day_entry.
                VOLATILE_DAY: dict(VOLATILE_DIAG),
            }), now=NOW)

        # Tag powstaje na summary (warunki: n_om>=6, n_yr>=3, |alt-base|>=2,
        # brak złej pogody, ENABLE_VOLATILITY_UI=1 domyślnie).
        assert captured[VOLATILE_DAY].get("diag_tag") is not None
        assert captured[VOLATILE_DAY]["diag_tag"].startswith("⚠️")
        assert captured[VOLATILE_DAY]["diag_severity"] == "caution"

        # ...ale nie trafia do żadnego day_entry.
        assert all("diag_tag" not in d for d in volatile["next_days"])
        assert all("diag_severity" not in d for d in volatile["next_days"])

        # Karta wygląda identycznie jak bez volatility — to charakterystyka
        # martwej ścieżki, nie ocena UX. Porównanie na wspólnym źródle: wariant
        # plain ma pusty daily_diag (wybór OM + marker " *"), więc dla równości
        # przekazujemy mu ten sam licznik n_yr co wariantom volatile.
        plain_same_source = prepare_future_layout_data(
            _future_payload(daily_diag={
                VOLATILE_DAY: {"is_volatile": False, "n_om": 24, "n_yr": 24},
            }), now=NOW)
        assert volatile["next_days"] == plain_same_source["next_days"]


# ── syntetyczne fixture'y renderera (charakterystyka, nie docelowy UX) ──

CX1, CX2 = 40, 1040  # szerokość kolumny karty jak w głównym layoucie


def _base_entry():
    return {
        "name": "Sob", "label": "Sob 21.11", "descriptor": "Słonecznie",
        "icon": "wk_sun_one_cloud", "temp_max": 12.0, "temp_min": 2.0,
        "precip_badge": None, "severity": "normal",
    }


def _plan(days):
    img = Image.new("RGB", (1080, 600))
    draw = ImageDraw.Draw(img)
    return image_generator._plan_days_card_layout(draw, days, CX1, CX2)


def _render_days_card(days):
    """Renderuje kartę „Następne dni" w izolacji (draw na overlayu, jak produkcja)."""
    img = Image.new("RGB", (1080, 600), (10, 12, 20))
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(ov)
    image_generator.draw_days_card(draw, ov, img, days, "Następne dni", 20,
                                   CX1, CX2, {"card": (25, 30, 45, 255)})
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")


class TestDiagTagRendererSide:
    def test_plan_exposes_tag_only_when_present_on_day_entry(self):
        """Syntetyczny fixture (charakterystyka, nie docelowy UX): planer
        _plan_days_card_layout czyta diag_tag z day dict; przy braku pola
        planuje None. Różnica leży po stronie producenta (tag nigdy nie
        dociera do day_entry — zob. TestDiagTagDeadPath)."""
        plan = _plan([_base_entry()])
        assert plan["rows"][0]["diag_tag"] is None

        tagged = _base_entry() | {"diag_tag": "⚠️ Możliwe: 12° dzień",
                                  "diag_severity": "caution"}
        plan_tagged = _plan([tagged])
        assert plan_tagged["rows"][0]["diag_tag"] is not None
        assert plan_tagged["rows"][0]["diag_tag"].startswith("⚠️")

    def test_tag_is_actually_drawn_on_the_card(self):
        """Charakterystyka: draw_days_card z tagiem na day_entry rysuje go
        przy prawej krawędzi karty (różnica rastra względem wiersza bez tagu)."""
        tagged = _base_entry() | {"diag_tag": "⚠️ Możliwe: 12° dzień",
                                  "diag_severity": "caution"}
        plain = _render_days_card([_base_entry()])
        with_tag = _render_days_card([tagged])
        assert ImageChops.difference(plain, with_tag).getbbox() is not None

    def test_tag_position_wins_over_inline_moon(self):
        """Charakterystyka planera: gdy day dict MA diag_tag, faza Księżyca
        nie wchodzi inline (warunek `not diag_tag`); bez tagu — wchodzi.
        Zob. też test_trend_moon_icons.py."""
        entry = _base_entry() | {"moon_phase": "full", "moon_icon": "wk_full_moon"}
        assert _plan([dict(entry)])["rows"][0]["moon_inline"] is True

        tagged = entry | {"diag_tag": "⚠️ Możliwe: 12° dzień",
                          "diag_severity": "caution"}
        assert _plan([tagged])["rows"][0]["moon_inline"] is False


# ─────────────────── DailyPick.marker niewidoczny na karcie ───────────────────

class TestDailyPickMarkerDeadPath:
    def _two_day_payload(self):
        """Dzień +1: pełne Yr; dzień +2: 24 rekordy OM, ale tylko 15 Yr (jak Hel).
        Liczniki n_yr w daily_diag sterują wyborem źródła (jak w produkcji)."""
        hours = []
        day1, day2 = NOW + timedelta(days=1), NOW + timedelta(days=2)
        for day in (day1, day2):
            for i in range(24):
                hours.append(_hour(day.replace(hour=0) + timedelta(hours=i), "openmeteo", 9.0))
        for i in range(24):
            hours.append(_hour(day1.replace(hour=0) + timedelta(hours=i), "yrno", 7.0))
        for i in range(15):
            hours.append(_hour(day2.replace(hour=0) + timedelta(hours=i), "yrno", 7.0))
        diag = {
            day1.strftime("%Y-%m-%d"): {"n_yr": 24, "n_om": 24},
            day2.strftime("%Y-%m-%d"): {"n_yr": 15, "n_om": 24},
        }
        return _future_payload(hours=hours, daily_diag=diag)

    def test_marker_lands_in_name_but_not_in_label(self, no_coast):
        """Marker " *" (fallback OM/mixed) dopina się do day_entry["name"],
        a renderer czyta "label" (fallback do "name" tylko gdy "label"
        nie istnieje) — w praktyce marker nigdy nie jest renderowany.
        Charakterystyka, nie docelowy UX."""
        lay = prepare_future_layout_data(self._two_day_payload(), now=NOW)

        d1, d2 = lay["next_days"][0], lay["next_days"][1]
        assert d1["name"] == "Sob"           # Yr: bez markera
        assert d2["name"] == "Ndz *"         # OM: marker w "name"
        assert d2["label"] == "Ndz 22.11"    # ...ale nie w "label"

    def test_marker_is_never_drawn_on_the_card(self, no_coast):
        """Charakterystyka: renderer używa `label` (bez markera) — karta
        z fallbackiem OM jest pikselowo identyczna z kartą, w której marker
        usunięto z day_entry['name']."""
        lay = prepare_future_layout_data(self._two_day_payload(), now=NOW)
        days = [dict(d) for d in lay["next_days"][:2]]
        assert days[1]["name"].endswith(" *")

        stripped = [dict(d, name=d["name"].replace(" *", "")) for d in days]
        a = _render_days_card(days)
        b = _render_days_card(stripped)
        assert ImageChops.difference(a, b).getbbox() is None

    def test_renderer_reads_label_with_name_fallback(self):
        src = inspect.getsource(image_generator)
        assert 'd.get("label") or d.get("name", "")' in src

    def test_marker_string_shape(self):
        from daily_source import pick_hours_for_daily_summary
        hours = [dict(_hour(NOW + timedelta(days=1, hours=i), "openmeteo", 9.0))
                 for i in range(24)]
        day = (NOW + timedelta(days=1)).strftime("%Y-%m-%d")
        pick = pick_hours_for_daily_summary(hours, {}, day)
        assert pick.marker == " *"


# ─────────────────── apply_cloud_correction (wycofana) ───────────────────

class TestApplyCloudCorrectionDead:
    def test_no_production_callers(self):
        # Zero wywołań poza definicją/docstringiem — nie usuwać (rejestr §3.5,
        # dokument decyzji: martwy kod charakteryzujemy, nie kasujemy).
        import glob
        import os
        callers = []
        for path in glob.glob(os.path.join(os.path.dirname(__file__) or ".", "*.py")):
            name = os.path.basename(path)
            if name.startswith("test_"):
                continue
            with open(path, encoding="utf-8") as f:
                src = f.read()
            n = src.count("apply_cloud_correction")
            if name == "owm_nowcast.py":
                # definicja + wzmianka w docstringu nowcast_note
                continue
            if n:
                callers.append(name)
        assert callers == []

    def test_function_is_still_importable_and_callable(self):
        assert callable(apply_cloud_correction)

    def test_characterization_if_it_were_called(self):
        """Syntetyczny fixture (charakterystyka, nie docelowy UX): gdyby
        ktokolwiek wywołał funkcję, przy rozjazdzie >= 40 pkt brutalnie
        nadpisuje chmury 3 pierwszych krotek (relaks 0/33/66%), kasując
        też pola *_yr. Zero takich wywołań w produkcji — zob. test wyżej."""
        def row(clouds):
            return (None, {"clouds_pct": clouds, "clouds_low_pct": clouds,
                           "clouds_mid_pct": 0.0, "weather_code": 1})
        ta = [row(10.0), row(10.0), row(10.0)]
        owm = {"data": [{"clouds": 90.0}]}
        apply_cloud_correction(ta, owm)
        assert ta[0][1]["clouds_pct"] == 90.0          # pełna korekta
        assert ta[1][1]["clouds_pct"] == pytest.approx(90.0 - 80.0 / 3.0)
        assert ta[2][1]["clouds_pct"] == pytest.approx(90.0 - 160.0 / 3.0)
        # Rozjazd < 40 pkt: bez zmian.
        ta2 = [row(60.0)]
        apply_cloud_correction(ta2, {"data": [{"clouds": 50.0}]})
        assert ta2[0][1]["clouds_pct"] == 60.0


# ─────────────────── coastal_day_alert (nikt nie zapisuje) ───────────────────

class TestCoastalDayAlertDead:
    def test_only_one_param_and_one_read(self):
        src = inspect.getsource(worth_knowing)
        assert src.count("coastal_day_alert") == 2  # parametr + odczyt payloadu

    def test_nobody_writes_the_payload_key(self):
        import glob
        import os
        writers = []
        for path in glob.glob(os.path.join(os.path.dirname(__file__) or ".", "*.py")):
            name = os.path.basename(path)
            if name.startswith("test_"):
                continue
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if "coastal_day_alert" not in line:
                        continue
                    if ".get(" in line or line.strip().startswith("def ") or \
                            "coastal_day_alert=False)" in line:
                        continue
                    writers.append(f"{name}: {line.strip()}")
        assert writers == []

    def test_flag_value_does_not_change_worth_knowing(self):
        """Charakterystyka: flaga jest czytana do `coastal_boost`, ale żaden
        kandydat WK nie przypisuje jej — True i False dają identyczny wynik."""
        def _wk(flag_present, flag_value):
            hours = [dict(_hour(NOW + timedelta(hours=i), "openmeteo", 8.0))
                     for i in range(12)]
            p = {
                "location": {"name": "Test", "lat": 52.2, "lon": 21.0,
                             "tz": "Europe/Warsaw"},
                "lang": "pl", "hours": hours, "alerts": [], "daily_diag": {},
            }
            if flag_present:
                p["coastal_day_alert"] = flag_value
            return worth_knowing.build_worth_knowing(
                payload=p, blocks=[], alerts=[], temp_min=5.0, temp_max=10.0,
                current_hour=12, ta=hours)
        missing = _wk(False, False)
        assert missing == _wk(True, False) == _wk(True, True)


# ─────────────────── model_agreement (zawsze None) ───────────────────

class TestModelAgreementDead:
    def test_build_payload_writes_none(self, monkeypatch):
        monkeypatch.setattr(weather_payload, "_fetch_openmeteo",
                            lambda *a, **k: [dict(_hour(NOW, "openmeteo", 10.0))])
        monkeypatch.setattr(weather_payload, "_fetch_yrno",
                            lambda *a, **k: ([dict(_hour(NOW, "yrno", 9.0))], "x"))
        monkeypatch.setattr(weather_payload, "_fetch_airly", lambda *a, **k: None)
        monkeypatch.setattr(weather_payload, "ENABLE_OWM_NOWCAST", False)
        monkeypatch.setattr(weather_payload, "ENABLE_AUDIT_LOG", False)
        payload = weather_payload.build_payload_for_location(
            52.23, 21.01, "Europe/Warsaw", "Test", days_ahead=1)
        assert "model_agreement" in payload
        assert payload["model_agreement"] is None

    def test_prepare_layout_treats_none_like_empty(self, monkeypatch, no_coast):
        """prepare_layout czyta `payload.get("model_agreement") or {}` —
        None i {} dają identyczny layout."""
        hours = []
        start = NOW.replace(hour=0)
        for i in range(48):
            hours.append(_hour(start + timedelta(hours=i), "openmeteo", 8.0))
        base = {
            "location": {"name": "Test", "lat": 52.2, "lon": 21.0, "tz": "Europe/Warsaw"},
            "lang": "pl", "hours": hours, "forecast_source": "OpenMeteo + Yr.no",
            "model_updated_at_local": NOW.isoformat(timespec="minutes"),
            "alerts": [], "daily_diag": {},
        }
        monkeypatch.setattr(prepare_layout, "get_current_weather", lambda *a, **k: None)
        lay_none = prepare_layout.prepare_layout_data(
            copy.deepcopy(base) | {"model_agreement": None}, now=NOW)
        lay_empty = prepare_layout.prepare_layout_data(
            copy.deepcopy(base) | {"model_agreement": {}}, now=NOW)
        assert lay_none == lay_empty
