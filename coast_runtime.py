import os
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

try:
    from coast_detector import (JsonCoastSigStore, CoastIndex, GEO_STACK_AVAILABLE,
                                warn_coast_disabled)
    if not GEO_STACK_AVAILABLE:
        # Fallback importu zostaje, ale nie milczymy: bez stosu geo alerty od morza
        # nie pojawią się nigdy, a w logach wygląda to jak spokojna pogoda.
        warn_coast_disabled("coast_runtime")
    GLOBAL_COAST_STORE = JsonCoastSigStore(str(BASE_DIR / "coast_cache.json"))
    
    _COAST_INDEX = None
    _COAST_LOCK = threading.Lock()
    
    def ensure_coast_index() -> CoastIndex:
        global _COAST_INDEX
        if _COAST_INDEX is None:
            with _COAST_LOCK:
                if _COAST_INDEX is None:
                    shp_path = BASE_DIR / "data" / "natural_earth" / "ne_50m_ocean" / "ne_50m_ocean.shp"
                    _COAST_INDEX = CoastIndex(str(shp_path))
                    print(f"[SYSTEM] Zbudowano indeks morza (50m). PID={os.getpid()} | Ścieżka: {shp_path}")
        return _COAST_INDEX

except Exception as e:
    GLOBAL_COAST_STORE = None
    ensure_coast_index = None
    print(f"[SYSTEM] Błąd runtime wybrzeża: {e}")