"""
services/gpu.py — GPU utilization helpers.
"""

from __future__ import annotations

import platform
import time

from src.services.hardware_macos import read_mac_gpu_utilization

_GPU_CACHE_TTL = 5.0
_last_gpu_read_ts = 0.0
_last_gpu_value: int | None = None
_last_gpu_source: str | None = None


def read_gpu_utilization() -> tuple[int | None, str | None]:
    """Return average GPU utilization (0-100) and a source label when available."""
    global _last_gpu_read_ts, _last_gpu_value, _last_gpu_source
    now = time.monotonic()
    if _last_gpu_read_ts and (now - _last_gpu_read_ts) < _GPU_CACHE_TTL:
        return _last_gpu_value, _last_gpu_source
    if platform.system().lower() == "darwin":
        value, source = read_mac_gpu_utilization()
        _last_gpu_read_ts = now
        _last_gpu_value = value
        _last_gpu_source = source
        return value, source
    return None, None
