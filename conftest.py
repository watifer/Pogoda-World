"""Offline tests: no external sockets, secrets, or checkout runtime artifacts.

Install requirements-dev.txt. pytest-socket enforces the transport boundary;
individual tests can still inject deterministic HTTP fakes.
"""
import os

import pytest


def pytest_configure(config):
    # Applied before collection (test_ai.py executes its client on import).
    for key in ("GROQ_API_KEY", "OWM_API_KEY", "AIRLY_API_KEY", "GOOGLE_CREDS_JSON",
                "TG_TOKEN", "TELEGRAM_BOT_TOKEN"):
        os.environ[key] = ""
    for key in ("ENABLE_AUDIT_LOG", "ENABLE_OWM_NOWCAST", "ENABLE_WK_AI_CANDIDATE",
                "OWM_DEBUG", "DEBUG_PAYLOAD_JSON"):
        os.environ[key] = "0"


@pytest.fixture(scope="session", autouse=True)
def isolate_runtime_artifacts(tmp_path_factory):
    import coast_runtime
    import image_generator
    import main_card
    import owm_nowcast
    import weather_payload

    root = tmp_path_factory.mktemp("runtime")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(image_generator, "OUTPUT_DIR", str(root / "images"))
        patch.setattr(owm_nowcast, "USAGE_FILE", str(root / "owm_usage.json"))
        patch.setattr(weather_payload, "AUDIT_PATH", root / "audit_spread.jsonl")
        patch.setattr(weather_payload, "ENABLE_AUDIT_LOG", False)
        patch.setattr(main_card, "CACHE_FILE", str(root / "sent_today.json"))
        patch.setenv("BOT_OFFSET_FILE", str(root / "offset.json"))
        for module in (coast_runtime, main_card):
            if module.GLOBAL_COAST_STORE:
                patch.setattr(module.GLOBAL_COAST_STORE, "filename",
                              str(root / (module.__name__ + "_coast.json")))
        yield
