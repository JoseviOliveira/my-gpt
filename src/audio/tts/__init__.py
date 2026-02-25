"""
tts_backend — helper utilities shared by the TTS REST API and CLI tools.

This package keeps larger modules bite-sized so ``tts_api.py`` can stay lean.
It exposes high-level helpers for:
  • environment diagnostics
  • text normalisation and chunking
  • streaming Coqui responses chunk-by-chunk
  • voice/model resolution helpers
"""

from . import constants
from .env import (
    TTS_CHUNK_CHAR_LIMIT_DEFAULT,
    TTS_CHUNK_LEN_FLEX_DEFAULT,
    ensure_env_logged,
    env_snapshot,
    external_tts_available,
    COQUI_TTS_PY,
    COQUI_TTS_SCRIPT,
)
from .models import (
    resolve_voice_and_language,
    apply_model_overrides,
    detect_model_capabilities,
)
from .normalize import normalise_text
from .chunking import chunk_text
from .streaming import build_streaming_response

__all__ = [
    "constants",
    "TTS_CHUNK_CHAR_LIMIT_DEFAULT",
    "TTS_CHUNK_LEN_FLEX_DEFAULT",
    "ensure_env_logged",
    "env_snapshot",
    "external_tts_available",
    "COQUI_TTS_PY",
    "COQUI_TTS_SCRIPT",
    "resolve_voice_and_language",
    "apply_model_overrides",
    "detect_model_capabilities",
    "normalise_text",
    "chunk_text",
    "build_streaming_response",
]
