import os
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

try:
    from coast_detector import JsonCoastSigStore, CoastIndex
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