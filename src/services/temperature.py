"""
services/temperature.py — server temperature helpers.
"""

from __future__ import annotations

import platform
import time

from src.services.hardware_macos import read_mac_temperature, read_macmon_temperature

_TEMP_CACHE_TTL = 10.0
_last_temp_read_ts = 0.0
_last_temp_value: float | None = None
_last_temp_source: str | None = None
_last_temp_kind: str | None = None
_last_temp_unit: str | None = None
_last_temp_label: str | None = None
def read_server_temperature() -> tuple[float | None, str | None, str | None, str | None, str | None]:
    """Return server temperature or thermal pressure and a source label when available."""
    global _last_temp_read_ts, _last_temp_value, _last_temp_source, _last_temp_kind, _last_temp_unit, _last_temp_label
    now = time.monotonic()
    if _last_temp_read_ts and (now - _last_temp_read_ts) < _TEMP_CACHE_TTL:
        return _last_temp_value, _last_temp_source, _last_temp_kind, _last_temp_unit, _last_temp_label
    if platform.system().lower() == "darwin":
        value, source, kind, unit, label = read_macmon_temperature()
        if value is None:
            value, source, kind, unit, label = read_mac_temperature()
        _last_temp_read_ts = now
        _last_temp_value = value
        _last_temp_source = source
        _last_temp_kind = kind
        _last_temp_unit = unit
        _last_temp_label = label
        return value, source, kind, unit, label
    return None, None, None, None, None
