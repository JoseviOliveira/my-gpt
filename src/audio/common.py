"""
audio_common.py — shared helpers for STT/TTS blueprints.
Provides environment readers and lightweight audio utilities that both
pipelines rely on without duplicating code.
"""
from __future__ import annotations

import os
import numpy as np


def env_int(name: str, default: int) -> int:
    """Read an integer environment variable with a defensive fallback."""
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    """Read a float environment variable with a defensive fallback."""
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool) -> bool:
    """Read a boolean environment variable supporting common truthy strings."""
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def env_str(name: str, default: str = "") -> str:
    """Read a string environment variable with whitespace trimming."""
    return (os.getenv(name, default) or "").strip()


def env_optional_float(name: str, default: float | None = None) -> float | None:
    """Read a float environment variable that may be unset/blank."""
    val = os.getenv(name)
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def env_optional_bool(name: str, default: bool | None = None) -> bool | None:
    """Read an optional boolean from the environment."""
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def resample_mono(audio: np.ndarray, sr: int, target_sr: int) -> tuple[np.ndarray, int]:
    """
    Resample mono audio to the requested sample rate using linear interpolation.
    Returns the resampled audio (float32) and the target sample rate.
    """
    if sr == target_sr:
        return audio.astype(np.float32, copy=False), sr
    duration = len(audio) / float(sr or 1)
    if duration <= 0:
        return audio.astype(np.float32, copy=False), target_sr
    x_old = np.linspace(0, duration, len(audio), endpoint=False)
    x_new = np.linspace(0, duration, int(round(duration * target_sr)), endpoint=False)
    resampled = np.interp(x_new, x_old, audio).astype(np.float32)
    return resampled, target_sr


__all__ = [
    "env_int",
    "env_float",
    "env_bool",
    "env_str",
    "env_optional_float",
    "env_optional_bool",
    "resample_mono",
]
