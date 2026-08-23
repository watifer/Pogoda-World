from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import shapefile
from shapely.geometry import shape, Point, box
from shapely.strtree import STRtree
from shapely.ops import transform
from pyproj import Geod, Transformer

WGS84_GEOD = Geod(ellps="WGS84")

def _deg_bbox_around(lat: float, lon: float, km: float) -> Tuple[float, float, float, float]:
    """Zgrubny bbox w stopniach do wstępnego query w STRtree."""
    lat_delta = km / 111.0
    coslat = max(0.1, abs(__import__("math").cos(__import__("math").radians(lat))))
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
        # Shapely 2.0 zwraca numery (indeksy), a nie poligony!
        candidate_indices = self._tree.query(p)
        return any(self._ocean_geoms[i].contains(p) for i in candidate_indices)

    def distance_to_ocean_km(self, lat: float, lon: float, search_km: float = 120.0) -> Optional[float]:
        p = Point(lon, lat)
        if self.is_ocean(lat, lon):
            return 0.0

        minx, miny, maxx, maxy = _deg_bbox_around(lat, lon, search_km)
        qbox = box(minx, miny, maxx, maxy)
        candidate_indices = self._tree.query(qbox)

        # Sprawdzamy długość tablicy (czy znaleziono jakiekolwiek poligony)
        if len(candidate_indices) == 0:
            return None

        transformer = _make_local_aeqd_transformer(lat, lon)
        tf = lambda x, y: transformer.transform(x, y)

        p_m = transform(tf, p)

        best_m = None
        for i in candidate_indices:
            # Odpytujemy naszą listę geometrii używając otrzymanego indeksu
            g = self._ocean_geoms[i]
            g_m = transform(tf, g)
            d = g_m.boundary.distance(p_m)
            if best_m is None or d < best_m:
                best_m = d

        if best_m is None:
            return None
        return best_m / 1000.0

    def compute_signature(
        self,
        lat: float,
        lon: float,
        radius_km: float = 25.0,
        step_deg: int = 10,
        sample_radii_km: Sequence[float] = (5, 10, 15, 20, 25),
        min_sector_width_deg: float = 20.0,
    ) -> CoastSignature:
        dist = self.distance_to_ocean_km(lat, lon, search_km=max(120.0, radius_km * 4))
        if dist is None or dist > radius_km:
            return CoastSignature(False, dist, [], radius_km, step_deg)

        flags: List[bool] = []
        for bearing in range(0, 360, step_deg):
            sea = False
            for r in sample_radii_km:
                lon2, lat2, _ = WGS84_GEOD.fwd(lon, lat, bearing, r * 1000.0)
                if self.is_ocean(lat2, lon2):
                    sea = True
                    break
            flags.append(sea)

        sectors = _flags_to_sectors(flags, step_deg=step_deg)

        normalized = []
        for s, e in sectors:
            s2 = max(0.0, min(360.0, s))
            e2 = max(0.0, min(360.0, e))
            if e2 - s2 >= min_sector_width_deg:
                normalized.append((s2, e2))

        normalized = sorted(normalized, key=lambda x: x[0])
        if normalized:
            first = normalized[0]
            last = normalized[-1]
            if abs(first[0] - 0.0) < 1e-9 and abs(last[1] - 360.0) < 1e-9:
                merged = (last[0] % 360.0, first[1] % 360.0)
                normalized = [merged] + normalized[1:-1]

        return CoastSignature(True, dist, normalized, radius_km, step_deg)
        
        
        
import json
import os
import time

COAST_SIG_VERSION = "ne_10m_ocean:10m;radar:v1;r25;step10;minw20"

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
            json.dump(self._cache, f, ensure_ascii=False, indent=2)

    def get(self, key: str) -> Optional[dict]:
        return self._cache.get(key)

    def set(self, key: str, value: dict) -> None:
        self._cache[key] = value
        self._save()

def coast_cache_key(lat: float, lon: float) -> str:
    """Tworzy unikalny klucz dla danej siatki (ok. 111m x 111m)."""
    return f"coast:{round(lat, 3)}:{round(lon, 3)}"

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
            radius_km=float(cached.get("radius_km", 25.0)),
            step_deg=int(cached.get("step_deg", 10)),
        )

    # Liczymy na nowo
    sig = idx.compute_signature(lat=lat, lon=lon, radius_km=25.0, step_deg=10, min_sector_width_deg=20.0)

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

# --- BLOK TESTOWY ---
if __name__ == "__main__":
    import time
    
    print("Wczytywanie mapy oceanów (to zajmie chwilę)...")
    t0 = time.time()
    try:
        idx = CoastIndex("data/natural_earth/ne_10m_ocean/ne_10m_ocean.shp")
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