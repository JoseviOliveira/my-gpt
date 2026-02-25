"""Environment helpers for the TTS backend."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

from flask import current_app

from src.audio.tts.runtime import TTS_MODEL_DEFAULT, TTS_QUALITY_DEFAULT

ROOT_DIR = Path(__file__).resolve().parents[3]
COQUI_TTS_PY = os.getenv("COQUI_TTS_PY", str(ROOT_DIR / "tts_env" / "bin" / "python"))
COQUI_TTS_SCRIPT = os.getenv("COQUI_TTS_SCRIPT", str(ROOT_DIR / "scripts" / "tts_synthesize.py"))


TTS_CHUNK_CHAR_LIMIT_DEFAULT = int(os.getenv("TTS_CHUNK_CHAR_LIMIT", "150"))
try:
    _chunk_flex = float(os.getenv("TTS_CHUNK_LEN_FLEX", "1.33"))
except ValueError:
    _chunk_flex = 1.33
TTS_CHUNK_LEN_FLEX_DEFAULT = max(1.0, min(_chunk_flex, 2.0))
_ENV_LOGGED = False


def env_snapshot() -> Dict[str, Any]:
    """Capture useful environment details for diagnostics."""
    env = os.environ
    return {
        "pid": os.getpid(),
        "cwd": str(os.getcwd()),
        "user": os.getenv("USER") or os.getenv("LOGNAME"),
        "python": sys.executable,
        "coqui_available": bool(current_app.config.get("COQUI_CLASS")),
        "TTS_MODEL_DEFAULT": TTS_MODEL_DEFAULT,
        "TTS_QUALITY_DEFAULT": TTS_QUALITY_DEFAULT,
        "PATH": env.get("PATH"),
        "ESPEAK_PATH": env.get("ESPEAK_PATH"),
        "PHONEMIZER_ESPEAK_PATH": env.get("PHONEMIZER_ESPEAK_PATH"),
        "LC_ALL": env.get("LC_ALL"),
        "LANG": env.get("LANG"),
        "COQUI_TTS_CACHE_DIR": env.get("COQUI_TTS_CACHE_DIR"),
        "COQUI_TTS_CACHE": env.get("COQUI_TTS_CACHE"),
        "XDG_CACHE_HOME": env.get("XDG_CACHE_HOME"),
        "HF_HUB_CACHE": env.get("HF_HUB_CACHE"),
        "HF_HOME": env.get("HF_HOME"),
    }


def ensure_env_logged(logger: logging.Logger) -> None:
    """Log environment details once per process."""
    global _ENV_LOGGED
    if _ENV_LOGGED:
        return
    info = {
        "model_default": TTS_MODEL_DEFAULT,
        "quality_default": TTS_QUALITY_DEFAULT,
        "python_executable": sys.executable,
        "external_helper": external_tts_available(),
    }
    logger.info("[TTS] backend environment", extra=info)
    _ENV_LOGGED = True


def external_tts_available() -> bool:
    return bool(
        COQUI_TTS_PY
        and os.path.exists(COQUI_TTS_PY)
        and COQUI_TTS_SCRIPT
        and os.path.exists(COQUI_TTS_SCRIPT)
    )


__all__ = [
    "TTS_CHUNK_CHAR_LIMIT_DEFAULT",
    "TTS_CHUNK_LEN_FLEX_DEFAULT",
    "COQUI_TTS_PY",
    "COQUI_TTS_SCRIPT",
    "env_snapshot",
    "ensure_env_logged",
    "external_tts_available",
]
