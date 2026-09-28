"""
coast_detector.py — geometria wybrzeża + decyzja o trybie alertu nadmorskiego.

Dwa niezależne tryby (get_coastal_alert_mode):

  * "marine_storm" — GLOBALNIE, cały rok. Odpala się tylko blisko morza/oceanu
    (distance guard), tylko przy wietrze onshore i tylko przy naprawdę wysokich
    progach. To ostrzeżenie sztormowe, a NIE lifestyle'owe "Wybrzeże".

  * "beach" — lifestyle. Tylko Polska (strefa czasowa) i tylko sezon
    01.06–15.09, z niskimi progami "na plaży wieje mocniej".

Konfiguracja przez ENV (czytana przy każdym wywołaniu):

  ENABLE_GLOBAL_MARINE_STORM          domyślnie 1  (0 wyłącza tryb marine_storm)
  MARINE_STORM_WIND_KMH               domyślnie 75
  MARINE_STORM_GUST_KMH               domyślnie 90
  MARINE_STORM_MIN_GUST_WITH_WIND_KMH domyślnie 80

Warunek sztormu: gust >= MARINE_STORM_GUST_KMH
                 albo (wind >= MARINE_STORM_WIND_KMH i gust >= MARINE_STORM_MIN_GUST_WITH_WIND_KMH).
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Callable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------
# Stos geo (shapely / pyproj / pyshp) jest ciężki i potrzebny WYŁĄCZNIE
# do liczenia sygnatury wybrzeża. Logika decyzyjna (progi, sezon, tryby)
# musi dać się zaimportować także tam, gdzie tych bibliotek nie ma
# (testy jednostkowe, lekkie workery).
# ---------------------------------------------------------------------
try:
    import shapefile
    from shapely.geometry import shape, Point, box
    from shapely.strtree import STRtree
    from pyproj import Geod, Transformer

    GEO_STACK_AVAILABLE = True
    GEO_STACK_ERROR: Optional[Exception] = None
except Exception as _geo_err:  # pragma: no cover - zależne od środowiska
    shapefile = None  # type: ignore[assignment]
    shape = Point = box = None  # type: ignore[assignment]
    STRtree = None  # type: ignore[assignment]
    Geod = Transformer = None  # type: ignore[assignment]

    GEO_STACK_AVAILABLE = False
    GEO_STACK_ERROR = _geo_err

GEO_STACK_PACKAGES = ("pyshp", "shapely", "pyproj")
COAST_DISABLED_MSG = (
    "[SYSTEM] Moduł nadmorski WYŁĄCZONY (coast / marine_storm / beach): brak stosu geo "
    f"({', '.join(GEO_STACK_PACKAGES)}). Karty wygenerują się normalnie, ale alerty od morza "
    "NIE pojawią się nigdy. Napraw: pip install -r requirements.txt"
)

_COAST_WARNED = False


def coast_stack_status() -> dict:
    """Stan stosu geo w jednym miejscu — dla diagnostyki i ostrzeżeń runtime."""
    return {
        "available": GEO_STACK_AVAILABLE,
        "error": None if GEO_STACK_AVAILABLE else repr(GEO_STACK_ERROR),
        "packages": list(GEO_STACK_PACKAGES),
        "message": None if GEO_STACK_AVAILABLE else COAST_DISABLED_MSG,
    }


def warn_coast_disabled(context: str = "") -> bool:
    """Ostrzega RAZ na proces, że alerty nadmorskie są wyłączone.

    Fallback importu zostaje (lekkie workery i testy muszą móc zaimportować moduł),
    ale cicha awaria jest gorsza od braku funkcji — bez tego komunikatu brak alertów
    wygląda w logach identycznie jak spokojna pogoda.
    """
    global _COAST_WARNED
    if GEO_STACK_AVAILABLE or _COAST_WARNED:
        return False
    _COAST_WARNED = True
    suffix = f" | kontekst: {context}" if context else ""
    print(f"{COAST_DISABLED_MSG}{suffix}", file=sys.stderr)
    if GEO_STACK_ERROR is not None:
        print(f"[SYSTEM] Powód importu: {GEO_STACK_ERROR!r}", file=sys.stderr)
    return True


_WGS84_GEOD = None


def _geod():
    """Leniwy singleton geodezyjny (pyproj ładuje się dopiero przy liczeniu)."""
    global _WGS84_GEOD
    if _WGS84_GEOD is None:
        if not GEO_STACK_AVAILABLE:
            raise RuntimeError(f"Brak stosu geo (shapely/pyproj/pyshp): {GEO_STACK_ERROR}")
        _WGS84_GEOD = Geod(ellps="WGS84")
    return _WGS84_GEOD


# =====================================================================
# WERSJA SYGNATURY — JEDNA, JEDYNA DEFINICJA
# =====================================================================
# Musi odpowiadać datasetowi faktycznie ładowanemu w runtime
# (coast_runtime.py / main_card.py -> data/natural_earth/ne_50m_ocean).
# v2 + marine75g90 = rozdzielenie trybów beach / marine_storm
# oraz nowe progi sztormowe (wind 75 / gust 90 / gust-with-wind 80).
COAST_SIG_VERSION = "ne_50m_ocean:50m;radar:v2;r25;step10;minw20;marine75g90"

# Domyślne parametry skanu otoczenia
DEFAULT_RADIUS_KM = 25.0
DEFAULT_STEP_DEG = 10
DEFAULT_MIN_SECTOR_WIDTH_DEG = 20.0

# =====================================================================
# TRYBY ALERTU NADMORSKIEGO
# =====================================================================
MODE_MARINE_STORM = "marine_storm"   # globalnie, cały rok, tylko naprawdę groźny wiatr od wody
MODE_BEACH = "beach"                 # lifestyle: tylko PL, tylko sezon plażowy

# --- Progi marine_storm (domyślne; nadpisywalne z ENV) ---
MARINE_STORM_WIND_KMH_DEFAULT = 75.0
MARINE_STORM_GUST_KMH_DEFAULT = 90.0
MARINE_STORM_MIN_GUST_WITH_WIND_KMH_DEFAULT = 80.0

# --- Progi lifestyle beach (PL, sezon) ---
BEACH_WIND_KMH = 18.0
BEACH_GUST_KMH = 25.0
BEACH_FAR_DIST_KM = 10.0     # dalej od wody niż to -> podnosimy poprzeczkę
BEACH_FAR_PENALTY_KMH = 8.0

# Strefy czasowe traktowane jako "Polska" dla trybu beach
PL_TIMEZONES = {"Europe/Warsaw", "Poland", "PL"}

# Sezon plażowy: 01.06 – 15.09
BEACH_SEASON_START = (6, 1)
BEACH_SEASON_END = (9, 15)


def _env_float(name: str, default: float) -> float:
    """Czyta próg z ENV; przy śmieciach wraca do wartości domyślnej."""
    raw = os.environ.get(name)
    if raw is None:
        return float(default)
    try:
        return float(str(raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        return float(default)


def _env_flag(name: str, default: str = "1") -> bool:
    """Czyta flagę on/off z ENV (1/true/yes/on == włączone)."""
    raw = os.environ.get(name, default)
    if raw is None:
        raw = default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def marine_storm_thresholds() -> Tuple[float, float, float]:
    """(wind_kmh, gust_kmh, min_gust_with_wind_kmh) — czytane z ENV przy każdym wywołaniu."""
    return (
        _env_float("MARINE_STORM_WIND_KMH", MARINE_STORM_WIND_KMH_DEFAULT),
        _env_float("MARINE_STORM_GUST_KMH", MARINE_STORM_GUST_KMH_DEFAULT),
        _env_float("MARINE_STORM_MIN_GUST_WITH_WIND_KMH", MARINE_STORM_MIN_GUST_WITH_WIND_KMH_DEFAULT),
    )


def is_global_marine_storm_enabled() -> bool:
    return _env_flag("ENABLE_GLOBAL_MARINE_STORM", "1")


def is_poland_tz(tz_str: Optional[str]) -> bool:
    return (tz_str or "").strip() in PL_TIMEZONES


def in_beach_season(dt: datetime) -> bool:
    """Sezon plażowy 01.06–15.09 (włącznie)."""
    md = (int(dt.month), int(dt.day))
    return BEACH_SEASON_START <= md <= BEACH_SEASON_END


# Jedyna słuszna funkcja do tworzenia klucza (ok. 111 m x 111 m siatki)
def coast_cache_key(lat: float, lon: float) -> str:
    return f"coast:{lat:.3f}:{lon:.3f}"

def _deg_bbox_around(lat: float, lon: float, km: float) -> Tuple[float, float, float, float]:
    """Zgrubny bbox w stopniach do wstępnego query w STRtree."""
    lat_delta = km / 111.0
    coslat = max(0.1, abs(math.cos(math.radians(lat))))
    lon_delta = km / (111.0 * coslat)
    return (lon - lon_delta, lat - lat_delta, lon + lon_delta, lat + lat_delta)

def _make_local_aeqd_transformer(lat0: float, lon0: float) -> Transformer:
    """Lokalna projekcja metryczna (Azimuthal Equidistant) centrowana na punkcie."""
    proj = (
        f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} "
        f"+datum=WGS84 +units=m +no_defs"
    )
    return Transformer.from_crs("EPSG:4326", proj, always_xy=True)

def _flags_to_sectors(flags: Sequence[bool], step_deg: int) -> List[Tuple[float, float]]:
    """Przetwarza listę flag (True/False) na ciągłe sektory kątowe w stopniach."""
    n = len(flags)
    sectors: List[Tuple[float, float]] = []
    i = 0
    while i < n:
        if not flags[i]:
            i += 1
            continue
        start_i = i
        while i < n and flags[i]:
            i += 1
        end_i = i - 1

        start_deg = start_i * step_deg
        end_deg = (end_i + 1) * step_deg
        sectors.append((float(start_deg), float(end_deg)))
    return sectors

def bearing_in_sector(bearing_deg: float, start_deg: float, end_deg: float) -> bool:
    """Sprawdza czy dany kąt mieści się w sektorze (obsługuje zawijanie przez północ)."""
    b = bearing_deg % 360.0
    s = start_deg % 360.0
    e = end_deg % 360.0
    if s <= e:
        return s <= b <= e
    return (b >= s) or (b <= e)

@dataclass(frozen=True)
class CoastSignature:
    is_coastal: bool
    distance_to_ocean_km: Optional[float]
    sea_sectors: List[Tuple[float, float]]
    radius_km: float
    step_deg: int

class CoastIndex:
    """Indeks oceanów oparty o shapefile z Natural Earth."""
    
    def __init__(self, ocean_shapefile_path: str):
        self._ocean_geoms = self._load_ocean_geoms(ocean_shapefile_path)
        self._tree = STRtree(self._ocean_geoms)

    @staticmethod
    def _load_ocean_geoms(path: str):
        geoms = []
        # Używamy lekkiego pyshp zamiast fiony
        with shapefile.Reader(path) as sf:
            for shape_rec in sf.shapeRecords():
                geom = shape_rec.shape.__geo_interface__
                if geom:
                    geoms.append(shape(geom))
        if not geoms:
            raise RuntimeError(f"Brak geometrii oceanów w pliku: {path}")
        return geoms

    def is_ocean(self, lat: float, lon: float) -> bool:
        p = Point(lon, lat)
        # Shapely 2.x: Predykat w C eliminuje pętlę w Pythonie
        return len(self._tree.query(p, predicate="intersects")) > 0

    def is_coastal_bbox_precheck(self, lat: float, lon: float, max_dist_km: float = 25.0) -> bool:
        # Błyskawiczny kwadrat poszukiwań (~111km to 1 stopień)
        deg_margin = (max_dist_km / 111.0) + 0.05
        search_box = box(lon - deg_margin, lat - deg_margin, lon + deg_margin, lat + deg_margin)
        return len(self._tree.query(search_box)) > 0

    

    def compute_signature(
        self,
        lat: float,
        lon: float,
        radius_km: float = DEFAULT_RADIUS_KM,
        step_deg: int = DEFAULT_STEP_DEG,
        sample_radii_km: tuple = (1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 25.0),
        min_sector_width_deg: float = DEFAULT_MIN_SECTOR_WIDTH_DEG,
    ):
        # --- 1. BŁYSKAWICZNY PRE-CHECK (Inland odpada w 0.001s) ---
        if not self.is_coastal_bbox_precheck(lat, lon, radius_km):
            return CoastSignature(
                is_coastal=False,
                distance_to_ocean_km=None,
                sea_sectors=[],
                radius_km=radius_km,
                step_deg=step_deg,
            )

        # SZYBKI FILTR LĄDOWY: Czy w ogóle mamy ocean w promieniu 25 km?
        minx, miny, maxx, maxy = _deg_bbox_around(lat, lon, radius_km)
        candidate_indices = self._tree.query(box(minx, miny, maxx, maxy))
        
        if len(candidate_indices) == 0:
            # Jesteśmy w głębi lądu! Błyskawiczny powrót, zero testowania promieni.
            return CoastSignature(False, None, [], radius_km, step_deg)

        # Dopiero jeśli w pobliżu jest woda, zaczynamy precyzyjne badanie kątów
        geod = _geod()
        flags = []
        min_dist = None

        for bearing in range(0, 360, step_deg):
            sea = False
            for r in sample_radii_km:
                lon2, lat2, _ = geod.fwd(lon, lat, bearing, r * 1000.0)
                if self.is_ocean(lat2, lon2):
                    sea = True
                    min_dist = r if min_dist is None else min(min_dist, r)
                    break
            flags.append(sea)

        if min_dist is None:
            return CoastSignature(False, None, [], radius_km, step_deg)

        sectors = _flags_to_sectors(flags, step_deg=step_deg)

        # Filtr szerokości z użyciem matematyki okrężnej (modulo 360)
        normalized = []
        for s, e in sectors:
            s2 = max(0.0, min(360.0, float(s)))
            e2 = max(0.0, min(360.0, float(e)))
            
            width = (e2 - s2) % 360.0
            if width == 0:
                width = 360.0
                
            if width >= min_sector_width_deg:
                normalized.append((s2, e2))

        normalized.sort(key=lambda x: x[0])

        # Merge wrap-around (łączenie sektorów przez północ 360/0 stopni)
        if normalized:
            first = normalized[0]
            last = normalized[-1]
            if abs(first[0] - 0.0) < 1e-9 and abs(last[1] - 360.0) < 1e-9:
                merged = (last[0] % 360.0, first[1] % 360.0)
                normalized = [merged] + normalized[1:-1]

        return CoastSignature(True, float(min_dist), normalized, radius_km, step_deg)



class JsonCoastSigStore:
    """Prosty adapter zapisujący wyliczenia wybrzeża do pliku JSON."""
    def __init__(self, filename="coast_cache.json"):
        self.filename = filename
        self._cache = self._load()

    def _load(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save(self):
        with open(self.filename, 'w', encoding='utf-8') as f:
            # Usunięto indent=2 - zapisuje wszystko w jednej, płaskiej i szybkiej linii
            json.dump(self._cache, f, ensure_ascii=False)

    def get(self, key: str) -> Optional[dict]:
        return self._cache.get(key)

    def set(self, key: str, value: dict) -> None:
        self._cache[key] = value
        self._save()

def get_or_compute_coast_signature(
    idx: CoastIndex,
    store: JsonCoastSigStore,
    lat: float,
    lon: float
) -> CoastSignature:
    """Sprawdza czy mamy gotowy wynik w cache. Jeśli nie, odpala skomplikowaną matematykę."""
    key = coast_cache_key(lat, lon)
    cached = store.get(key)

    if cached and cached.get("version") == COAST_SIG_VERSION:
        return CoastSignature(
            is_coastal=bool(cached["is_coastal"]),
            distance_to_ocean_km=cached.get("distance_to_ocean_km"),
            sea_sectors=[tuple(x) for x in cached.get("sea_sectors", [])],
            radius_km=float(cached.get("radius_km", DEFAULT_RADIUS_KM)),
            step_deg=int(cached.get("step_deg", DEFAULT_STEP_DEG)),
        )

    # Liczymy na nowo
    sig = idx.compute_signature(
        lat=lat, lon=lon,
        radius_km=DEFAULT_RADIUS_KM, step_deg=DEFAULT_STEP_DEG,
        min_sector_width_deg=DEFAULT_MIN_SECTOR_WIDTH_DEG,
    )

    # Zapisujemy do pamięci
    payload = {
        "version": COAST_SIG_VERSION,
        "computed_at": int(time.time()),
        "is_coastal": sig.is_coastal,
        "distance_to_ocean_km": sig.distance_to_ocean_km,
        "sea_sectors": [list(x) for x in sig.sea_sectors],
        "radius_km": sig.radius_km,
        "step_deg": sig.step_deg,
    }
    store.set(key, payload)
    return sig

def is_onshore(wind_dir_deg: float, sea_sectors: List[Tuple[float,float]]) -> bool:
    """Prosty helper logiczny: czy wiatr wieje nam od strony morza?"""
    return any(bearing_in_sector(wind_dir_deg, s, e) for s, e in sea_sectors)
    
    
def get_or_compute_coast_signature_lazy(
    store,
    lat: float,
    lon: float,
    idx_factory: Callable[[], 'CoastIndex'],
) -> 'CoastSignature':
    
    key = coast_cache_key(lat, lon)
    cached = store.get(key)
    
    # 1. Mamy to w Cache i wersja się zgadza -> Zwracamy od razu!
    if cached and cached.get("version") == COAST_SIG_VERSION:
        return CoastSignature(
            is_coastal=bool(cached.get("is_coastal", False)),
            distance_to_ocean_km=cached.get("distance_to_ocean_km"),
            sea_sectors=[tuple(x) for x in cached.get("sea_sectors", [])],
            radius_km=float(cached.get("radius_km", DEFAULT_RADIUS_KM)),
            step_deg=int(cached.get("step_deg", DEFAULT_STEP_DEG)),
        )

    # 2. Cache MISS -> Blokujemy działanie, ładujemy mapę i liczymy
    print(f"[COAST] Cache MISS dla {key}. Inicjuję mapę...")
    idx = idx_factory()
    
    t0 = perf_counter()
    sig = idx.compute_signature(
        lat=lat, lon=lon,
        radius_km=DEFAULT_RADIUS_KM, step_deg=DEFAULT_STEP_DEG,
        min_sector_width_deg=DEFAULT_MIN_SECTOR_WIDTH_DEG,
    )
    t1 = perf_counter()
    
    # Zapis do Cache
    payload = {
        "version": COAST_SIG_VERSION,
        "computed_at": int(time.time()),
        "is_coastal": getattr(sig, "is_coastal", False),
        "distance_to_ocean_km": getattr(sig, "distance_to_ocean_km", None),
        "sea_sectors": [list(x) for x in getattr(sig, "sea_sectors", [])],
        "radius_km": getattr(sig, "radius_km", 25.0),
        "step_deg": getattr(sig, "step_deg", 10),
    }
    
    store.set(key, payload)
    t2 = perf_counter()
    
    print(f"[PERF COAST] compute: {t1-t0:.3f}s | store.set: {t2-t1:.3f}s | total: {t2-t0:.3f}s")
    
    return sig
    
    
def coastal_distance_guard(sig) -> bool:
    """
    True == punkt jest realnie blisko morza/oceanu.

    Sygnatura bywa policzona dla większego promienia niż domyślne 25 km
    (albo przyjechała ze starego cache), więc zanim cokolwiek ogłosimy,
    sprawdzamy dystans do wody względem promienia skanu.
    """
    dist = getattr(sig, "distance_to_ocean_km", None)
    dist = 999.0 if dist is None else float(dist)

    max_dist = getattr(sig, "radius_km", None)
    max_dist = DEFAULT_RADIUS_KM if not max_dist else float(max_dist)

    return dist <= max_dist


def get_coastal_alert_mode(
    sig,
    wind_spd_kmh: float,
    gust_kmh: float,
    wind_dir_deg: float,
    tz_str: str,
    current_dt: datetime,
) -> Optional[str]:
    """
    Zwraca: "marine_storm" | "beach" | None

    - "marine_storm": GLOBALNIE, cały rok, ale tylko blisko morza/oceanu,
      tylko przy wietrze onshore i tylko przy naprawdę wysokich progach
      (domyślnie gust >= 90 km/h albo wind >= 75 i gust >= 80 km/h).
      To NIE jest "Wybrzeże" — to ostrzeżenie sztormowe.
    - "beach": lifestyle, TYLKO Polska i TYLKO sezon 01.06–15.09,
      z dotychczasowymi, niskimi progami.
    """
    if sig is None:
        return None
    if not getattr(sig, "is_coastal", False):
        return None

    sea_sectors = getattr(sig, "sea_sectors", None) or []
    if not sea_sectors:
        return None

    # 1) DISTANCE GUARD — bez tego "wybrzeże" łapie miasta 40+ km od wody
    if not coastal_distance_guard(sig):
        return None

    # 2) ONSHORE GUARD — wiatr musi wiać OD strony wody
    if wind_dir_deg is None:
        return None
    try:
        if not is_onshore(float(wind_dir_deg), sea_sectors):
            return None
    except (TypeError, ValueError):
        return None

    wind_spd = float(wind_spd_kmh or 0.0)
    gust = float(gust_kmh or 0.0)
    if gust < wind_spd:
        gust = wind_spd  # poryw nigdy nie może być słabszy od wiatru średniego

    # 3) MARINE STORM (globalnie, cały rok, wysokie progi)
    if is_global_marine_storm_enabled():
        wind_thr, gust_thr, gust_with_wind_thr = marine_storm_thresholds()
        if gust >= gust_thr or (wind_spd >= wind_thr and gust >= gust_with_wind_thr):
            return MODE_MARINE_STORM

    # 4) BEACH (tylko PL + tylko sezon + lifestyle progi)
    if is_poland_tz(tz_str) and in_beach_season(current_dt):
        dist = getattr(sig, "distance_to_ocean_km", None)
        dist = 999.0 if dist is None else float(dist)
        add = BEACH_FAR_PENALTY_KMH if dist > BEACH_FAR_DIST_KM else 0.0

        if wind_spd >= (BEACH_WIND_KMH + add) or gust >= (BEACH_GUST_KMH + add):
            return MODE_BEACH

    return None


# --- BLOK TESTOWY ---
if __name__ == "__main__":
    import time
    
    print("Wczytywanie mapy oceanów (to zajmie chwilę)...")
    t0 = time.time()
    try:
        idx = CoastIndex("data/natural_earth/ne_50m_ocean/ne_50m_ocean.shp")
        print(f"Mapa wczytana w {time.time() - t0:.2f} s!\n")
        
        # Test Kąty Rybackie (Zatoka Gdańska / Bałtyk)
        print("Skanowanie otoczenia dla: Kąty Rybackie (54.332, 19.227)...")
        t1 = time.time()
        sig = idx.compute_signature(lat=54.332, lon=19.227)
        
        print(f"Wynik obliczono w {time.time() - t1:.3f} s:")
        print(f" -> Czy nad morzem? {sig.is_coastal}")
        print(f" -> Odległość do wody: {sig.distance_to_ocean_km:.2f} km")
        print(f" -> Azymuty morza (skąd wieje bryza): {sig.sea_sectors}")
        
    except Exception as e:
        print(f"BŁĄD: {e}")