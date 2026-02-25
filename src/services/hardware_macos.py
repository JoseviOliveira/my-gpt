"""
services/hardware_macos.py — macOS/Apple Silicon hardware probes.

This module isolates macOS-specific commands (powermetrics, macmon). If running
on non-Apple hardware, replace these implementations with platform-appropriate
probes and update callers accordingly.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import logging
import os
import time

_TEMP_RE = re.compile(r"temperature[^0-9]*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_THERMAL_LEVEL_RE = re.compile(r"(?:thermal|pressure).*?level[^0-9]*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_MACMON_WARN_EVERY_SEC = float(os.environ.get("MACMON_WARN_EVERY_SEC", "300"))
_MACMON_RETRY_SEC = float(os.environ.get("MACMON_RETRY_SEC", "30"))
_macmon_last_warn_at: dict[str, float] = {}
_macmon_disabled_until: float = 0.0


def _macmon_warn_throttled(key: str, message: str, *args) -> None:
    now = time.monotonic()
    last = _macmon_last_warn_at.get(key, 0.0)
    if now - last >= _MACMON_WARN_EVERY_SEC:
        logging.getLogger(__name__).warning(message, *args)
        _macmon_last_warn_at[key] = now


def _backoff_macmon() -> None:
    global _macmon_disabled_until
    _macmon_disabled_until = time.monotonic() + max(0.0, _MACMON_RETRY_SEC)


def read_mac_gpu_utilization() -> tuple[int | None, str | None]:
    """Read Apple Silicon GPU busy percentage via powermetrics when available."""
    if not shutil.which("powermetrics"):
        return None, "powermetrics"
    try:
        output = subprocess.check_output(
            ["sudo", "-n", "/usr/bin/powermetrics", "-n", "1", "--samplers", "gpu_power"],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=6.0,
        )
    except Exception:
        return None, "powermetrics"
    for line in output.splitlines():
        if "GPU HW active residency" not in line:
            continue
        parts = line.split(":")
        if len(parts) < 2:
            continue
        value = parts[1].strip().split("%", 1)[0].strip()
        try:
            return int(round(float(value))), "powermetrics"
        except ValueError:
            continue
    return None, "powermetrics"


def read_mac_temperature() -> tuple[float | None, str | None, str | None, str | None, str | None]:
    """Read Apple Silicon temperature via powermetrics when available."""
    if not shutil.which("powermetrics"):
        return None, "powermetrics", None, None, None
    samplers = ["thermal", "cpu_power", "smc"]
    fallback: tuple[float, str, str, str, str] | None = None
    for sampler in samplers:
        output = _read_powermetrics_output(["-n", "1", "-i", "1000", "--samplers", sampler])
        if output is None:
            output = _read_powermetrics_output(["-n", "1", "--samplers", sampler])
        if output is None:
            continue
        if sampler == "thermal":
            level = _extract_thermal_level(output)
            if level is not None:
                value, label = level
                if fallback is None:
                    fallback = (value, "powermetrics:thermal", "thermal_pressure", "%", label)
        temp_c = _extract_temperature_c(output)
        if temp_c is not None:
            return round(temp_c, 1), f"powermetrics:{sampler}", "temperature", "C", None
    if fallback is not None:
        return fallback
    return None, "powermetrics", None, None, None


def read_macmon_temperature() -> tuple[float | None, str | None, str | None, str | None, str | None]:
    """Read Apple Silicon GPU temperature via macmon when available."""
    if time.monotonic() < _macmon_disabled_until:
        return None, "macmon(backoff)", None, None, None

    macmon_path = os.environ.get("MACMON_PATH") or shutil.which("macmon")
    if not macmon_path:
        for candidate in ("/opt/homebrew/bin/macmon", "/usr/local/bin/macmon"):
            if shutil.which(candidate) or os.path.exists(candidate):
                macmon_path = candidate
                break
    if not macmon_path:
        return None, "macmon", None, None, None
    try:
        cmd = [macmon_path, "pipe", "-s", "1", "-i", "1000"]
        base_path = os.environ.get("PATH", "")
        safe_path = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"
        if base_path:
            safe_path = f"{base_path}:{safe_path}"
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5.0,
            env={**os.environ, "PATH": safe_path},
        )
    except Exception:
        _backoff_macmon()
        return None, "macmon", None, None, None
    output = (result.stdout or "").strip()
    if result.returncode != 0 or not output:
        _macmon_warn_throttled(
            "rc_or_empty",
            "macmon failed rc=%s stdout=%s stderr=%s",
            result.returncode,
            (result.stdout or "").strip(),
            (result.stderr or "").strip(),
        )
        _backoff_macmon()
        return None, "macmon", None, None, None
    try:
        last_line = output.splitlines()[-1]
        payload = json.loads(last_line)
        temp = payload.get("temp", {}).get("gpu_temp_avg")
        if temp is None:
            _macmon_warn_throttled("missing_gpu_temp", "macmon missing gpu_temp_avg line=%s", last_line)
            _backoff_macmon()
            return None, "macmon", None, None, None
        try:
            temp_value = float(temp)
        except (TypeError, ValueError):
            _macmon_warn_throttled("parse_failed", "macmon gpu_temp_avg parse failed value=%s", temp)
            _backoff_macmon()
            return None, "macmon", None, None, None
        if temp_value < 10 or temp_value > 110:
            _macmon_warn_throttled("out_of_range", "macmon gpu_temp_avg out of range value=%s", temp_value)
            _backoff_macmon()
            return None, "macmon", None, None, None
        return temp_value, "macmon", "temperature", "C", None
    except Exception:
        _macmon_warn_throttled(
            "json_parse_failed",
            "macmon JSON parse failed line=%s",
            output.splitlines()[-1] if output else "",
        )
        _backoff_macmon()
        return None, "macmon", None, None, None


def _read_powermetrics_output(args: list[str]) -> str | None:
    try:
        return subprocess.check_output(
            ["sudo", "-n", "/usr/bin/powermetrics", *args],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=6.0,
        )
    except Exception:
        return None


def _extract_temperature_c(output: str) -> float | None:
    temps: list[float] = []
    for line in output.splitlines():
        if "temp" not in line.lower():
            continue
        if "c" not in line.lower() and "°" not in line:
            continue
        match = _TEMP_RE.search(line)
        if not match:
            continue
        try:
            temps.append(float(match.group(1)))
        except ValueError:
            continue
    if not temps:
        return None
    return max(temps)


def _extract_thermal_level(output: str) -> tuple[float, str] | None:
    for line in output.splitlines():
        lower = line.lower()
        if "pressure level" in lower or "thermal pressure" in lower:
            label = line.split(":", 1)[-1].strip() if ":" in line else line.strip()
            label_norm = label.lower()
            if "nominal" in label_norm:
                return 0.0, "Nominal"
            if "moderate" in label_norm:
                return 50.0, "Moderate"
            if "heavy" in label_norm:
                return 75.0, "Heavy"
            if "critical" in label_norm:
                return 95.0, "Critical"
        match = _THERMAL_LEVEL_RE.search(line)
        if match:
            try:
                value = float(match.group(1))
                return value, f"{value:.0f}%"
            except ValueError:
                continue
    return None
