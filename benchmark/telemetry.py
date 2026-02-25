import json
import logging
import math
import os
import threading
import time
from uuid import uuid4
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

from benchmark.tasks import EmptyResponseError, TimeoutError, PROMPT_PREVIEW_LEN, RESPONSE_PREVIEW_LEN

ROOT_DIR = Path(__file__).parent.parent

logger = logging.getLogger(__name__)


class ResourceSampler:
    """Sample resource metrics during inference and aggregate them."""

    def __init__(self, runner: "BenchmarkRunner", interval_sec: float):
        self._runner = runner
        self._interval_sec = max(interval_sec, 0.1)
        self._samples: List[Dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._start_ts = None
        self._end_ts = None

    def start(self) -> None:
        self._start_ts = time.monotonic()
        self._sample_once()
        self._thread.start()

    def stop(self, wait: bool = False, timeout: float = 0.05) -> Dict[str, Any]:
        """Stop sampler.
        
        Non-blocking by default to avoid delaying request finalization paths.
        """
        self._stop.set()
        if wait and self._thread.is_alive():
            self._thread.join(timeout=max(timeout, 0.0))
            self._end_ts = time.monotonic()
            self._sample_once()
        else:
            self._end_ts = time.monotonic()
        return self._summarize()

    def _sample_once(self) -> None:
        gpu_util, _ = self._runner._check_gpu_utilization()
        gpu_temp = self._runner._read_gpu_temperature()
        disk_io = self._runner._read_disk_io_mbps()
        cpu_util = self._runner._read_cpu_utilization()
        self._samples.append({
            "ts": time.monotonic(),
            "iso": datetime.utcnow().isoformat(),
            "gpu_util": gpu_util,
            "gpu_temp": gpu_temp,
            "disk_io": disk_io,
            "cpu_util": cpu_util
        })

    def _run(self) -> None:
        while not self._stop.is_set():
            loop_start = time.monotonic()
            self._sample_once()
            elapsed = time.monotonic() - loop_start
            wait = max(0.0, self._interval_sec - elapsed)
            if self._stop.wait(wait):
                break

    def _summarize(self) -> Dict[str, Any]:
        end_ts = self._end_ts or time.monotonic()
        samples = self._samples
        return self._runner._aggregate_resource_samples(samples, end_ts)

class RequestWatchdog:
    """
    Watchdog kills hung/slow models because some LLMs make Ollama hang on hard tasks.
    
    - TTFT timeout: Models >45s unsuitable for chat UX (also prevents thermal buildup)
    - Stall detection: No tokens for 180s = frozen output
    - Hang detection: 100% GPU + no output = infinite thinking loop (e.g., magistral on math)
    - Max timeout: 300s safety limit
    """
    
    def __init__(self, runner: "BenchmarkRunner", model_name: str):
        self.runner = runner
        self.model_name = model_name
        self.config = runner._get_timeout_config(model_name)
        
        # State tracking
        self.last_token_time = time.monotonic()
        self.start_time = time.monotonic()
        self.first_token_time = None
        self.total_tokens = 0
        self.killed = False
        self.kill_reason = None
        self.timeout_type = None  # 'ttft', 'stall', 'hang', 'max'
        
        # Threading
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._monitor, daemon=True)
    
    def notify_first_token(self) -> Optional[str]:
        """Called when first token arrives. Returns kill reason if TTFT exceeded."""
        if self.first_token_time is None:
            self.first_token_time = time.monotonic()
            ttft_sec = self.first_token_time - self.start_time
            
            max_ttft = self.config.get('max_ttft_sec', 45)
            if ttft_sec > max_ttft:
                reason = (
                    f"TTFT {ttft_sec:.1f}s exceeds threshold ({max_ttft}s). "
                    f"Model too slow for chat-oriented app (UX + thermal protection)"
                )
                self._kill_request(reason, timeout_type='ttft')
                return reason
        return None
    
    def notify_token(self, count: int = 1) -> None:
        """Called when tokens are produced."""
        self.last_token_time = time.monotonic()
        self.total_tokens += count
    
    def start(self) -> None:
        """Start monitoring thread."""
        self.thread.start()
    
    def stop(self, wait: bool = False, timeout: float = 0.1) -> None:
        """Stop monitoring thread.
        
        By default this is non-blocking so request finalization is not delayed
        by watchdog cleanup. Call with wait=True if synchronous shutdown is needed.
        """
        self.stop_event.set()
        if wait and self.thread.is_alive():
            self.thread.join(timeout=max(timeout, 0.0))
    
    def _monitor(self) -> None:
        """Check for timeout conditions every 5 seconds."""
        check_interval = 5.0
        
        while not self.stop_event.wait(check_interval):
            if self.killed:
                return  # Already killed by TTFT check
            
            now = time.monotonic()
            elapsed_total = now - self.start_time
            time_since_token = now - self.last_token_time
            
            # Check 1: Absolute maximum timeout
            max_timeout = self.config.get('max_request_timeout', 300)
            if elapsed_total > max_timeout:
                self._kill_request(
                    f"Request exceeded max timeout ({max_timeout}s). "
                    f"Elapsed: {elapsed_total:.0f}s, tokens: {self.total_tokens}",
                    timeout_type='max'
                )
                return
            
            # Check 2: Token production stall
            stall_timeout = self.config.get('token_stall_timeout', 180)
            if time_since_token > stall_timeout:
                self._kill_request(
                    f"No tokens produced for {time_since_token:.0f}s "
                    f"(threshold: {stall_timeout}s). Total tokens: {self.total_tokens}",
                    timeout_type='stall'
                )
                return
            
            # Check 3: GPU saturation + stall = infinite thinking loop
            gpu_util, _ = self.runner._check_gpu_utilization()
            if gpu_util is not None:
                gpu_threshold = self.config.get('gpu_saturation_threshold', 95)
                gpu_stall_timeout = self.config.get('gpu_stall_kill_timeout', 120)
                
                if gpu_util >= gpu_threshold and time_since_token > gpu_stall_timeout:
                    self._kill_request(
                        f"Infinite thinking loop detected: GPU at {gpu_util}% "
                        f"for {time_since_token:.0f}s without producing tokens. "
                        f"Total tokens before hang: {self.total_tokens}",
                        timeout_type='hang'
                    )
                    return
    
    def _kill_request(self, reason: str, timeout_type: str) -> None:
        """Mark request as killed and restart Ollama."""
        self.killed = True
        self.kill_reason = reason
        self.timeout_type = timeout_type
        
        logger.error(
            f"[TIMEOUT-{timeout_type.upper()}] Killing {self.model_name}: {reason}"
        )
        
        # Force restart Ollama to clear hung state
        self.runner._restart_ollama(self.model_name, f"timeout-{timeout_type}: {reason}")

class TimeoutError(Exception):
    """Raised when a request is killed by the watchdog."""
    pass

class TelemetryMixin:
    def _get_timeout_config(self, model_name: str) -> Dict[str, Any]:
        """
        Timeout config with per-model override support.
        Generic thresholds handle all models (no model-specific exclusions needed).
        """
        # Global defaults
        global_timeout = self.config.get('timeout', {})
        defaults = {
            'max_ttft_sec': global_timeout.get('max_ttft_sec', 45),
            'token_stall_timeout': global_timeout.get('token_stall_timeout', 180),
            'max_request_timeout': global_timeout.get('max_request_timeout', 300),
            'gpu_saturation_threshold': global_timeout.get('gpu_saturation_threshold', 95),
            'gpu_stall_kill_timeout': global_timeout.get('gpu_stall_kill_timeout', 120),
        }
    
        # Check for per-model overrides
        for model_cfg in self.config.get('models', []):
            if model_cfg.get('name') == model_name:
                overrides = model_cfg.get('timeout_overrides', {})
                if overrides:
                    logger.info(
                        f"Using timeout overrides for {model_name}: {overrides}"
                    )
                    defaults.update(overrides)
                break
    
        return defaults

    def _load_resource_config(self) -> Dict[str, Any]:
        """Load resource sampling configuration with defaults."""
        res = self.config.get("resources", {})
        return {
            "interval_sec": float(res.get("resource_sample_interval_sec", 3.0)),
            "disk_bands_mbps": res.get("disk_io_bands_mbps", [1, 5]),
            "gpu_temp_bands": res.get("gpu_temp_bands", [50, 70, 85, 95]),
            "gpu_util_bands": res.get("gpu_util_bands", [80, 95]),
            "cpu_util_bands": res.get("cpu_util_bands", [20, 50, 70]),
        }

    def _read_gpu_temperature(self) -> Optional[float]:
        """Read GPU temperature (°C) with data hygiene filters."""
        try:
            import importlib.util
            module_path = ROOT_DIR / "src" / "services" / "hardware_macos.py"
            spec = importlib.util.spec_from_file_location("hardware_macos_bench", module_path)
            if spec is None or spec.loader is None:
                raise ImportError("Unable to load hardware_macos module")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            read_macmon_temperature = getattr(module, "read_macmon_temperature")
            read_mac_temperature = getattr(module, "read_mac_temperature")
        except Exception as exc:
            raise RuntimeError(f"GPU temperature probe unavailable: {exc}") from exc
        try:
            temp, source, kind, _unit, _label = read_macmon_temperature()
            if temp is None:
                temp, source, kind, _unit, _label = read_mac_temperature()
        except Exception:
            return None
        if kind != "temperature" or temp is None:
            return None
        try:
            temp_val = float(temp)
        except (TypeError, ValueError):
            return None
        if temp_val < 10 or temp_val > 110:
            return None
        return temp_val

    def _read_disk_io_mbps(self) -> Optional[float]:
        """Read instantaneous disk I/O MB/s using iostat."""
        import subprocess
        import shutil
        import os

        iostat_path = shutil.which("iostat")
        if not iostat_path:
            for candidate in ("/usr/sbin/iostat", "/usr/bin/iostat"):
                if os.path.exists(candidate):
                    iostat_path = candidate
                    break
        if not iostat_path:
            raise RuntimeError("Disk I/O read failed: iostat_not_found")

        def read_once(args: list[str], timeout_sec: float) -> tuple[Optional[float], str]:
            result = subprocess.run(
                [iostat_path, *args],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                env={**os.environ, "LC_ALL": "C"},
            )
            output = (result.stdout or "").strip()
            if not output:
                return None, output
            mbps = None
            for line in output.splitlines():
                parts = line.strip().split()
                if len(parts) < 3:
                    continue
                try:
                    float(parts[0])
                    float(parts[1])
                    mbps = float(parts[2])
                except ValueError:
                    continue
            return mbps, output

        try:
            # Command logging kept for error paths only
            value, raw = read_once(["-d", "1", "2"], 4.0)
        except Exception as exc:
            value, raw = None, ""
            logger.debug("Disk I/O command failed (first attempt)", exc_info=True)
            first_exc = exc
        else:
            first_exc = None
        if value is None:
            try:
                # Command logging kept for error paths only
                value, raw = read_once(["-d", "-w", "1", "2"], 6.0)
            except Exception as exc:
                value, raw = None, raw
                logger.debug("Disk I/O command failed (retry)", exc_info=True)
                if first_exc is None:
                    first_exc = exc
        if value is None:
            return None
        return value

    def _read_cpu_utilization(self) -> Optional[float]:
        """Read CPU busy percentage using iostat (2-sample window)."""
        import subprocess
        import shutil
        import os

        iostat_path = shutil.which("iostat")
        if not iostat_path:
            for candidate in ("/usr/sbin/iostat", "/usr/bin/iostat"):
                if os.path.exists(candidate):
                    iostat_path = candidate
                    break
        if not iostat_path:
            raise RuntimeError("CPU util read failed: iostat_not_found")

        cmd = f"{iostat_path} -w 1 | awk '$1 ~ /^[0-9]/ {{n++; if (n==2) {{print 100-$6; exit}}}}'"
        try:
            # Command logging kept for error paths only
            result = subprocess.run(
                ["/bin/sh", "-lc", cmd],
                capture_output=True,
                text=True,
                timeout=4.0,
                env={**os.environ, "LC_ALL": "C"},
            )
            output = (result.stdout or "").strip()
        except Exception:
            return None
        if not output:
            return None
        try:
            value = float(output.splitlines()[-1].strip())
        except ValueError:
            return None
        if value < 0 or value > 100:
            return None
        return value

    def _aggregate_resource_samples(self, samples: List[Dict[str, Any]], end_ts: float) -> Dict[str, Any]:
        """Aggregate resource samples into per-request stats."""
        disk_bands = self._resource_cfg["disk_bands_mbps"]
        gpu_temp_bands = self._resource_cfg["gpu_temp_bands"]
        gpu_util_bands = self._resource_cfg["gpu_util_bands"]
        cpu_util_bands = self._resource_cfg["cpu_util_bands"]

        def aggregate_metric(key: str, band_labels: List[str], band_thresholds: List[float], above_thresholds: List[float]):
            total_duration = 0.0
            sum_value_sec = 0.0
            max_value = None
            time_bands = {label: 0.0 for label in band_labels}
            time_above = {threshold: 0.0 for threshold in above_thresholds}

            for idx, sample in enumerate(samples):
                value = sample.get(key)
                if value is None:
                    continue
                start = sample.get("ts")
                if start is None:
                    continue
                next_ts = end_ts
                if idx + 1 < len(samples):
                    next_ts = samples[idx + 1].get("ts") or end_ts
                duration = max(0.0, next_ts - start)
                if duration <= 0:
                    continue
                total_duration += duration
                sum_value_sec += float(value) * duration
                if max_value is None or value > max_value:
                    max_value = float(value)

                band_idx = 0
                for threshold in band_thresholds:
                    if value < threshold:
                        break
                    band_idx += 1
                band_label = band_labels[min(band_idx, len(band_labels) - 1)]
                time_bands[band_label] += duration

                for threshold in above_thresholds:
                    if value > threshold:
                        time_above[threshold] += duration

            available = total_duration > 0
            return {
                "available": available,
                "duration_sec": total_duration,
                "sum_value_sec": sum_value_sec,
                "max": max_value,
                "time_bands": time_bands,
                "time_above": time_above
            }

        def aggregate_disk() -> Dict[str, Any]:
            total_duration = 0.0
            sum_value_sec = 0.0
            max_value = None
            time_bands = {"normal": 0.0, "warning": 0.0, "critical": 0.0}
            time_above = {disk_bands[0]: 0.0, disk_bands[1]: 0.0}

            for idx, sample in enumerate(samples):
                value = sample.get("disk_io")
                if value is None:
                    continue
                start = sample.get("ts")
                if start is None:
                    continue
                next_ts = end_ts
                if idx + 1 < len(samples):
                    next_ts = samples[idx + 1].get("ts") or end_ts
                duration = max(0.0, next_ts - start)
                if duration <= 0:
                    continue
                total_duration += duration
                sum_value_sec += float(value) * duration
                if max_value is None or value > max_value:
                    max_value = float(value)

                if value <= disk_bands[0]:
                    time_bands["normal"] += duration
                elif value <= disk_bands[1]:
                    time_bands["warning"] += duration
                else:
                    time_bands["critical"] += duration

                if value > disk_bands[0]:
                    time_above[disk_bands[0]] += duration
                if value > disk_bands[1]:
                    time_above[disk_bands[1]] += duration

            available = total_duration > 0
            return {
                "available": available,
                "duration_sec": total_duration,
                "sum_value_sec": sum_value_sec,
                "max": max_value,
                "time_bands": time_bands,
                "time_above": time_above
            }

        disk_stats = aggregate_disk()
        gpu_util_stats = aggregate_metric(
            "gpu_util",
            ["normal", "high", "saturated"],
            [gpu_util_bands[0], gpu_util_bands[1]],
            [gpu_util_bands[0], gpu_util_bands[1]],
        )
        gpu_temp_stats = aggregate_metric(
            "gpu_temp",
            ["cool", "normal", "high", "very_hot", "critical"],
            [gpu_temp_bands[0], gpu_temp_bands[1], gpu_temp_bands[2], gpu_temp_bands[3]],
            [70.0, 85.0, 95.0],
        )
        cpu_util_stats = aggregate_metric(
            "cpu_util",
            ["ideal", "normal", "watch", "bottleneck"],
            [cpu_util_bands[0], cpu_util_bands[1], cpu_util_bands[2]],
            [cpu_util_bands[0], cpu_util_bands[1], cpu_util_bands[2]],
        )

        sample_records = []
        for sample in samples:
            iso = sample.get("iso")
            if not iso:
                continue
            sample_records.append({
                "timestamp": iso,
                "disk_io_mbps": sample.get("disk_io"),
                "gpu_util": sample.get("gpu_util"),
                "gpu_temp": sample.get("gpu_temp"),
                "cpu_util": sample.get("cpu_util"),
            })

        return {
            "disk_io": disk_stats,
            "gpu_util": gpu_util_stats,
            "gpu_temp": gpu_temp_stats,
            "cpu_util": cpu_util_stats,
            "samples": sample_records
        }

    def _append_resource_samples(self, resource_stats: Dict[str, Any]) -> None:
        """Append resource samples for live charts."""
        samples = resource_stats.get("samples") if resource_stats else None
        if not samples:
            return
        for sample in samples:
            disk_io = sample.get("disk_io_mbps")
            gpu_util = sample.get("gpu_util")
            gpu_temp = sample.get("gpu_temp")
            cpu_util = sample.get("cpu_util")
            timestamp = sample.get("timestamp")
            if timestamp and disk_io is not None:
                self.state["recent_disk_io"].append({
                    "timestamp": timestamp,
                    "mbps": disk_io
                })
            if timestamp and gpu_util is not None:
                self.state["recent_gpu"].append({
                    "timestamp": timestamp,
                    "utilization": gpu_util
                })
            if timestamp and gpu_temp is not None:
                self.state["recent_gpu_temp"].append({
                    "timestamp": timestamp,
                    "temperature": gpu_temp
                })
            if timestamp and cpu_util is not None:
                self.state["recent_cpu"].append({
                    "timestamp": timestamp,
                    "utilization": cpu_util
                })
        if len(self.state["recent_disk_io"]) > 100:
            self.state["recent_disk_io"] = self.state["recent_disk_io"][-100:]
        if len(self.state["recent_gpu"]) > 100:
            self.state["recent_gpu"] = self.state["recent_gpu"][-100:]
        if len(self.state["recent_gpu_temp"]) > 100:
            self.state["recent_gpu_temp"] = self.state["recent_gpu_temp"][-100:]
        if len(self.state["recent_cpu"]) > 100:
            self.state["recent_cpu"] = self.state["recent_cpu"][-100:]

    def _init_resource_totals(self) -> Dict[str, Any]:
        """Initialize accumulators for resource metrics."""
        return {
            "disk_io": {
                "available": False,
                "sum_value_sec": 0.0,
                "duration_sec": 0.0,
                "max": None,
                "time_bands": {"normal": 0.0, "warning": 0.0, "critical": 0.0},
                "time_above": {"1": 0.0, "5": 0.0}
            },
            "gpu_util": {
                "available": False,
                "sum_value_sec": 0.0,
                "duration_sec": 0.0,
                "max": None,
                "time_bands": {"normal": 0.0, "high": 0.0, "saturated": 0.0},
                "time_above": {"80": 0.0, "95": 0.0}
            },
            "gpu_temp": {
                "available": False,
                "sum_value_sec": 0.0,
                "duration_sec": 0.0,
                "max": None,
                "time_bands": {"cool": 0.0, "normal": 0.0, "high": 0.0, "very_hot": 0.0, "critical": 0.0},
                "time_above": {"70": 0.0, "85": 0.0, "95": 0.0}
            },
            "cpu_util": {
                "available": False,
                "sum_value_sec": 0.0,
                "duration_sec": 0.0,
                "max": None,
                "time_bands": {"ideal": 0.0, "normal": 0.0, "watch": 0.0, "bottleneck": 0.0},
                "time_above": {"20": 0.0, "50": 0.0, "70": 0.0}
            }
        }

    def _accumulate_resource_totals(self, totals: Dict[str, Any], resource_stats: Optional[Dict[str, Any]]) -> None:
        """Accumulate per-request resource stats into task totals."""
        if not resource_stats:
            return
        for key, above_map in (
            ("disk_io", {"1": self._resource_cfg["disk_bands_mbps"][0], "5": self._resource_cfg["disk_bands_mbps"][1]}),
            ("gpu_util", {"80": self._resource_cfg["gpu_util_bands"][0], "95": self._resource_cfg["gpu_util_bands"][1]}),
            ("gpu_temp", {"70": 70.0, "85": 85.0, "95": 95.0}),
            ("cpu_util", {"20": self._resource_cfg["cpu_util_bands"][0],
                          "50": self._resource_cfg["cpu_util_bands"][1],
                          "70": self._resource_cfg["cpu_util_bands"][2]}),
        ):
            stats = resource_stats.get(key)
            if not stats or not stats.get("available"):
                continue
            totals[key]["available"] = True
            totals[key]["sum_value_sec"] += stats.get("sum_value_sec", 0.0)
            totals[key]["duration_sec"] += stats.get("duration_sec", 0.0)
            max_value = stats.get("max")
            if max_value is not None:
                current_max = totals[key]["max"]
                totals[key]["max"] = max(max_value, current_max) if current_max is not None else max_value
            for band, value in (stats.get("time_bands") or {}).items():
                if band in totals[key]["time_bands"]:
                    totals[key]["time_bands"][band] += value
            for label, threshold in above_map.items():
                totals[key]["time_above"][label] += (stats.get("time_above") or {}).get(threshold, 0.0)

    def _finalize_resource_totals(self, totals: Dict[str, Any]) -> Dict[str, Any]:
        """Compute averages from accumulated totals."""
        def finalize_metric(metric: Dict[str, Any]) -> Dict[str, Any]:
            duration = metric["duration_sec"]
            avg = None
            if duration > 0:
                avg = metric["sum_value_sec"] / duration
            if not metric["available"]:
                return {
                    "available": False,
                    "avg": None,
                    "max": None,
                    "time_bands": {key: None for key in metric["time_bands"].keys()},
                    "time_above": {key: None for key in metric["time_above"].keys()}
                }
            return {
                "available": True,
                "avg": avg,
                "max": metric["max"],
                "time_bands": metric["time_bands"],
                "time_above": metric["time_above"]
            }

        return {
            "disk_io": finalize_metric(totals["disk_io"]),
            "gpu_util": finalize_metric(totals["gpu_util"]),
            "gpu_temp": finalize_metric(totals["gpu_temp"]),
            "cpu_util": finalize_metric(totals["cpu_util"]),
        }

    def _update_temp_summaries(self, resource_stats: Optional[Dict[str, Any]]) -> None:
        """Update model/dataset/task GPU temp summaries in state."""
        if not resource_stats:
            return
        temp_stats = resource_stats.get("gpu_temp")
        if not temp_stats or not temp_stats.get("available"):
            return
        duration = temp_stats.get("duration_sec", 0.0)
        if duration <= 0:
            return
        sum_value = temp_stats.get("sum_value_sec", 0.0)
        self._model_temp_sum += sum_value
        self._model_temp_duration += duration
        self._dataset_temp_sum += sum_value
        self._dataset_temp_duration += duration
        model_avg = self._model_temp_sum / self._model_temp_duration if self._model_temp_duration > 0 else None
        dataset_avg = self._dataset_temp_sum / self._dataset_temp_duration if self._dataset_temp_duration > 0 else None
        task_avg = sum_value / duration if duration > 0 else None
        self.state["avg_gpu_temp_model"] = round(model_avg, 1) if model_avg is not None else None
        self.state["avg_gpu_temp_dataset"] = round(dataset_avg, 1) if dataset_avg is not None else None
        self.state["avg_gpu_temp_task"] = round(task_avg, 1) if task_avg is not None else None

    def _gpu_guard_config(self) -> Tuple[str, int, int, int, int]:
        """Resolve GPU guard settings (endpoint, threshold, poll, timeout, http timeout)."""
        thermal = self.config.get('thermal', {})
        threshold = int(thermal.get('gpu_utilization_guard', 10))
        poll_sec = int(thermal.get('gpu_guard_poll_sec', 3))
        timeout_sec = int(thermal.get('gpu_guard_timeout_sec', 600))
        http_timeout_sec = int(thermal.get('gpu_guard_http_timeout_sec', 8))
        endpoint = thermal.get('gpu_guard_endpoint') or os.environ.get('BENCHMARK_GPU_ENDPOINT')
        if not endpoint:
            port = os.environ.get('CHAT_PORT', '4200')
            endpoint = f"http://127.0.0.1:{port}/api/gpu"
        return endpoint, threshold, poll_sec, timeout_sec, http_timeout_sec

    def _app_base_url(self) -> str:
        """Resolve the local app base URL for benchmark traffic."""
        base = os.environ.get("BENCHMARK_APP_URL") or os.environ.get("APP_URL")
        if base:
            return base.rstrip("/")
        host = os.environ.get("BENCHMARK_APP_HOST", "127.0.0.1")
        port = os.environ.get("CHAT_PORT", "4200")
        return f"http://{host}:{port}"

    def _wait_for_app_ready(self) -> None:
        """Wait briefly for the app to be reachable before starting.

        Readiness uses only `/health` (lightweight endpoint).
        """
        import requests
        timeout_sec = float(os.environ.get("BENCHMARK_APP_WAIT_SEC", "20") or 20)
        probe_timeout_sec = float(os.environ.get("BENCHMARK_APP_PROBE_TIMEOUT_SEC", "3") or 3)
        deadline = time.time() + timeout_sec
        last_err = None
        base = self._app_base_url()
        app_headers = self._app_auth_headers()
        while time.time() < deadline:
            try:
                resp = requests.get(
                    f"{base}/health",
                    timeout=probe_timeout_sec,
                    headers=app_headers,
                )
                if resp.ok:
                    return
            except Exception as exc:
                last_err = exc
            time.sleep(1.0)
        if last_err:
            raise RuntimeError(f"App not reachable at {self._app_base_url()} (last error: {last_err})")
        raise RuntimeError(f"App not reachable at {self._app_base_url()} (no response)")

    def _gpu_guard_headers(self) -> dict:
        """Build optional auth headers for GPU endpoint."""
        user = os.environ.get('BENCHMARK_GPU_USER') or os.environ.get('APP_USER')
        password = os.environ.get('BENCHMARK_GPU_PASS') or os.environ.get('APP_PASS')
        if not user or not password:
            return {}
        import base64
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _app_auth_headers(self) -> dict:
        """Build optional auth headers for app endpoints."""
        user = os.environ.get('BENCHMARK_APP_USER') or os.environ.get('APP_USER')
        password = os.environ.get('BENCHMARK_APP_PASS') or os.environ.get('APP_PASS')
        import base64
        headers = {"X-Benchmark": "1"}
        if user and password:
            token = base64.b64encode(f"{user}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        return headers

    def _wait_for_gpu_ready(self, context: str):
        """
        GPU guard waits for utilization to drop before starting tasks.
        Prevents thermal issues and ensures fair benchmarking conditions.
        """
        import requests

        endpoint, threshold, poll_sec, timeout_sec, http_timeout_sec = self._gpu_guard_config()
        headers = self._gpu_guard_headers()
        start_time = time.time()
        while True:
            try:
                resp = requests.get(endpoint, timeout=http_timeout_sec, headers=headers)
                if resp.status_code in {401, 403}:
                    logger.warning("GPU guard skipped (auth required).")
                    return
                if not resp.ok:
                    return
                data = resp.json()
                util = data.get("utilization")
                if not data.get("available") or util is None:
                    return
                if util <= threshold:
                    return
                elapsed = time.time() - start_time
                if elapsed >= timeout_sec:
                    logger.warning(
                        "GPU guard timed out after %ss (util=%s%%).",
                        timeout_sec,
                        util,
                    )
                    return
                logger.info(
                    "GPU %s%% above %s%%. Waiting %ss before %s.",
                    util,
                    threshold,
                    poll_sec,
                    context,
                )
                time.sleep(poll_sec)
            except Exception as exc:
                logger.warning("GPU guard check failed: %s", exc)
                return

    def _check_gpu_utilization(self) -> Tuple[Optional[int], Optional[str]]:
        """Return GPU utilization using the shared endpoint."""
        import requests

        endpoint, _, _, _, http_timeout_sec = self._gpu_guard_config()
        headers = self._gpu_guard_headers()
        try:
            resp = requests.get(endpoint, timeout=http_timeout_sec, headers=headers)
        except Exception as exc:
            logger.warning("GPU utilization read failed: %s", exc)
            return None, "error"
        if resp.status_code in {401, 403}:
            return None, "auth_required"
        if not resp.ok:
            return None, "unavailable"
        data = resp.json()
        if not data.get("available"):
            return None, data.get("source")
        return data.get("utilization"), data.get("source")

    def _restart_ollama(self, model_name: str, reason: str):
        """Attempt to restart Ollama if configured."""
        logger.warning("Restarting Ollama due to %s.", reason)
        try:
            import subprocess
            subprocess.run(['ollama', 'stop', model_name], capture_output=True)
        except Exception as exc:
            logger.warning("Failed to stop Ollama model: %s", exc)

        restart_cmd = self.config.get('ollama', {}).get('restart_cmd')
        if restart_cmd:
            try:
                import subprocess
                subprocess.Popen(restart_cmd, shell=True)
                logger.info("Ollama restart command issued.")
                return
            except Exception as exc:
                logger.warning("Failed to restart Ollama via command: %s", exc)

        try:
            import subprocess
            subprocess.run(['ollama', 'serve'], capture_output=True)
            logger.info("Ollama serve invoked.")
        except Exception as exc:
            logger.warning("Failed to restart Ollama: %s", exc)

    def _set_last_request(self, info: Optional[Dict[str, Any]]):
        """Update last request info in state."""
        self.state["last_request"] = info
        self._update_state()

    def _record_gpu_utilization(self):
        """Append a GPU utilization sample for live charts."""
        util, _source = self._check_gpu_utilization()
        if util is None:
            return
        self.state["recent_gpu"].append({
            "timestamp": datetime.utcnow().isoformat(),
            "utilization": util
        })
        if len(self.state["recent_gpu"]) > 100:
            self.state["recent_gpu"].pop(0)
        temp = self._read_gpu_temperature()
        if temp is not None:
            self.state["recent_gpu_temp"].append({
                "timestamp": datetime.utcnow().isoformat(),
                "temperature": temp
            })
            if len(self.state["recent_gpu_temp"]) > 100:
                self.state["recent_gpu_temp"].pop(0)

    def _set_cooldown_state(self, active: bool, seconds: float, reason: str):
        """Update cooldown state for live monitoring."""
        payload = {
            "active": bool(active),
            "seconds": round(float(seconds), 1) if seconds else 0,
            "reason": reason or ""
        }
        if active:
            payload["started_at"] = datetime.utcnow().isoformat()
        self.state["cooldown"] = payload
        self._update_state()

    def _apply_cooldown(self, seconds: int):
        """Cooldown prevents thermal buildup between heavy tasks."""
        if seconds <= 0:
            return
        logger.info(f"Cooling down for {seconds}s...")
        self._set_cooldown_state(True, seconds, "inter-model")
        time.sleep(seconds)
        self._set_cooldown_state(False, 0, "")

    def _apply_request_cooldown(self, hardness: str, input_tokens: int, output_tokens: int):
        """Apply a cooldown that scales with request size."""
        thermal = self.config.get('thermal', {})
        base = thermal.get('cooldowns', {}).get(hardness, 0)
        token_factor = (input_tokens / 1500) + (output_tokens / 500)
        extra = max(0.0, min(5.0, token_factor))
        total = base + extra
        if total <= 0:
            return
        logger.info(f"Cooling down for {total:.1f}s (hardness={hardness})...")
        self._set_cooldown_state(True, total, f"{hardness} request")
        time.sleep(total)
        self._set_cooldown_state(False, 0, "")

    def _apply_inter_turn_delay(self):
        """Apply a small delay between chat turns when configured."""
        delay_ms = self.config.get('thermal', {}).get('inter_turn_delay_ms', 0)
        if delay_ms <= 0:
            return
        self._set_cooldown_state(True, delay_ms / 1000.0, "inter-turn")
        time.sleep(delay_ms / 1000.0)
        self._set_cooldown_state(False, 0, "")

    def _maybe_batch_cooldown(self, scope: str):
        """Apply periodic batch cooldowns for long runs."""
        thermal = self.config.get('thermal', {})
        interval = int(thermal.get('batch_cooldown_interval', 0) or 0)
        duration = int(thermal.get('batch_cooldown_duration', 0) or 0)
        if interval <= 0 or duration <= 0:
            return
        self._request_count += 1
        if self._request_count % interval == 0:
            logger.info(f"Batch cooldown after {self._request_count} {scope} requests...")
            self._set_cooldown_state(True, duration, f"batch {scope}")
            time.sleep(duration)
            self._set_cooldown_state(False, 0, "")

    def _call_ollama(self, model_name: str, prompt: str, config: dict) -> str:
        """Call Ollama API."""
        import requests

        request_info = {
            "model": model_name,
            "task": "warmup",
            "kind": "warmup",
            "attempt": 1,
            "endpoint": f"{self._app_base_url()}/api/chat",
            "status": "running",
            "started_at": datetime.utcnow().isoformat(),
            "prompt_preview": self._preview_text(prompt, PROMPT_PREVIEW_LEN)
        }
        self._set_last_request(request_info)
        logger.info("[ollama] warmup start model=%s", model_name)
        self._wait_for_gpu_ready(f"warmup {model_name}")

        payload = {
            "model": model_name,
            "mode": self._app_mode,
            "messages": [{"role": "user", "content": str(prompt)}],
            "options": {
                "temperature": config.get('temperature', 0.0),
                "top_p": config.get('top_p', 1.0),
                "top_k": config.get('top_k', 1),
                "num_predict": config.get('max_tokens', 2048),
                "seed": config.get('seed', 42)
            }
        }

        if 'num_ctx' in config:
            payload['options']['num_ctx'] = config['num_ctx']

        if 'stop' in config:
            payload['options']['stop'] = config['stop']

        endpoint = f"{self._app_base_url()}/api/chat"
        response = requests.post(
            endpoint,
            json=payload,
            timeout=300,
            headers=self._app_auth_headers(),
        )
        response.raise_for_status()

        request_info["status"] = "completed"
        request_info["ended_at"] = datetime.utcnow().isoformat()
        self._set_last_request(request_info)
        logger.info("[ollama] warmup complete model=%s", model_name)
        payload = response.json()
        response.close()  # Close connection to prevent file descriptor leak
        if isinstance(payload.get("message"), dict):
            return payload["message"].get("content", "")
        return payload.get("response", "")

    def _call_ollama_with_metrics(
        self,
        model_name: str,
        prompt,
        config: Dict[str, Any],
        multi_turn: bool = False,
        request_meta: Optional[Dict[str, Any]] = None,
        mode_override: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Call Ollama with a hard per-request timeout to avoid hangs."""
        import requests
        import socket
        import threading
        import queue

        ollama_cfg = self.config.get('ollama', {})
        call_timeout = int(ollama_cfg.get('request_timeout_seconds', 180))
        idle_timeout = int(ollama_cfg.get('stream_idle_timeout_seconds', 60))
        retries = int(ollama_cfg.get('request_retries', 3))
        retry_backoff = float(ollama_cfg.get('retry_backoff_seconds', 2.0))
        hang_gpu_wait = int(ollama_cfg.get('hang_gpu_wait_seconds', 30))

        def is_retryable(exc: Exception) -> bool:
            if isinstance(exc, (TimeoutError, requests.exceptions.ChunkedEncodingError, requests.exceptions.ReadTimeout)):
                return True
            if isinstance(exc, EmptyResponseError):
                return True
            if isinstance(exc, RuntimeError) and "Empty response from Ollama" in str(exc):
                return True
            return False

        def run_once(attempt: int):
            self._wait_for_gpu_ready(f"request {model_name}")
            base_url = self._app_base_url()
            endpoint = f"{base_url}/api/stream"
            metrics_endpoint = f"{base_url}/api/metrics"
            request_id = uuid4().hex
            request_info = {
                "model": model_name,
                "task": request_meta.get("task") if request_meta else None,
                "sample_id": request_meta.get("sample_id") if request_meta else None,
                "dialog_id": request_meta.get("dialog_id") if request_meta else None,
                "turn_idx": request_meta.get("turn_idx") if request_meta else None,
                "kind": request_meta.get("kind") if request_meta else None,
                "request_id": request_id,
                "attempt": attempt,
                "endpoint": endpoint,
                "status": "running",
                "started_at": datetime.utcnow().isoformat(),
                "prompt_preview": self._preview_prompt(prompt, multi_turn)
            }
            self._set_last_request(request_info)
            logger.info(
                "[ollama] request start model=%s endpoint=%s task=%s attempt=%s request_id=%s",
                model_name,
                endpoint,
                request_info.get("task"),
                attempt,
                request_info.get("request_id"),
            )

            def do_call(result_queue):
                """Execute the streaming call and push result/exception to queue."""
                old_timeout = socket.getdefaulttimeout()
                socket.setdefaulttimeout(120)
                resp = None
                sampler = None
                sampler_stopped = False
                resource_stats = None
                watchdog = None
                watchdog_stopped = False
                payload = None
                ttft = None
                start_time = time.time()
                try:
                    messages = prompt if multi_turn else [{"role": "user", "content": str(prompt)}]
                    payload = {
                        "id": request_id,
                        "model": model_name,
                        "mode": mode_override or self._app_mode,
                        "messages": messages,
                        "options": {
                            "temperature": config.get('temperature', 0.0),
                            "top_p": config.get('top_p', 1.0),
                            "top_k": config.get('top_k', 1),
                            "num_predict": config.get('max_tokens', 2048),
                            "seed": config.get('seed', 42)
                        }
                    }

                    if 'num_ctx' in config:
                        payload['options']['num_ctx'] = config['num_ctx']

                    if 'stop' in config:
                        payload['options']['stop'] = config['stop']

                    self._log_full_prompt(request_info, payload)

                    response_text = ""
                    chunk_count = 0
                    last_chunk_len = None

                    sampler = ResourceSampler(self, self._resource_cfg["interval_sec"])
                    sampler.start()

                    # Initialize watchdog for timeout detection
                    watchdog = RequestWatchdog(self, model_name)
                    watchdog.start()

                    resp = requests.post(
                        endpoint,
                        json=payload,
                        stream=True,
                        timeout=(10, idle_timeout),
                        headers=self._app_auth_headers(),
                    )
                    resp.raise_for_status()
                    
                    # Capture response metadata for empty response diagnosis
                    http_status = resp.status_code
                    content_type = resp.headers.get('Content-Type', 'unknown')
                    transfer_encoding = resp.headers.get('Transfer-Encoding', 'none')
                    stream_start_time = time.time()

                    # Small chunk size reduces client-side buffering latency for short answers.
                    for chunk in resp.iter_content(chunk_size=16, decode_unicode=True):
                        if chunk:
                            chunk_count += 1
                            last_chunk_len = len(chunk)
                        
                            # Notify watchdog of progress
                            watchdog.notify_token(len(chunk))
                        
                            if ttft is None:
                                ttft = int((time.time() - start_time) * 1000)
                            
                                # Check TTFT timeout (model too slow for chat UX)
                                ttft_kill_reason = watchdog.notify_first_token()
                                if ttft_kill_reason:
                                    # Stop monitoring
                                    if watchdog and not watchdog_stopped:
                                        watchdog.stop()
                                        watchdog_stopped = True
                                    if sampler and not sampler_stopped:
                                        resource_stats = sampler.stop()
                                        sampler_stopped = True
                                    raise TimeoutError(ttft_kill_reason)
                            
                                if sampler and not sampler_stopped:
                                    resource_stats = sampler.stop()
                                    sampler_stopped = True
                            response_text += chunk
                            
                            # Update last_request with streaming response every 10 chunks for live display
                            if chunk_count % 10 == 0:
                                request_info["response"] = response_text
                                request_info["response_preview"] = self._preview_text(response_text, RESPONSE_PREVIEW_LEN)
                                self._set_last_request(request_info)
                
                    # Check if watchdog killed the request
                    if watchdog.killed:
                        if watchdog and not watchdog_stopped:
                            watchdog.stop()
                            watchdog_stopped = True
                        if sampler and not sampler_stopped:
                            resource_stats = sampler.stop()
                            sampler_stopped = True
                        raise TimeoutError(watchdog.kill_reason)

                    total_time = time.time() - start_time
                    stream_duration = time.time() - stream_start_time
                    self._log_full_answer(request_info, response_text)
                    if not response_text.strip():
                        logger.warning(
                            "[ollama] EMPTY RESPONSE DIAGNOSIS: model=%s task=%s attempt=%s sample_id=%s dialog_id=%s turn_idx=%s | total_time_ms=%d stream_duration_ms=%d chunks=%d last_chunk_len=%s | http_status=%d content_type=%s transfer_encoding=%s | This is a TRUE EMPTY RESPONSE (not a timeout) - Ollama returned HTTP %d but sent 0 data chunks",
                            model_name,
                            request_info.get("task"),
                            attempt,
                            request_info.get("sample_id"),
                            request_info.get("dialog_id"),
                            request_info.get("turn_idx"),
                            int(total_time * 1000),
                            int(stream_duration * 1000),
                            chunk_count,
                            last_chunk_len,
                            http_status,
                            content_type,
                            transfer_encoding,
                            http_status,
                        )
                        raise EmptyResponseError(f"Empty response from Ollama (HTTP {http_status}, {content_type}, 0 chunks in {int(stream_duration * 1000)}ms)")
                    if sampler and not sampler_stopped:
                        resource_stats = sampler.stop()

                    output_tokens = None
                    tokens_per_sec = None
                    input_tokens = len(str(prompt).split())
                    try:
                        metrics_resp = requests.get(
                            metrics_endpoint,
                            params={"id": request_id},
                            timeout=10,
                            headers=self._app_auth_headers(),
                        )
                        try:
                            if metrics_resp.ok:
                                metrics_data = metrics_resp.json()
                                ollama_metrics = metrics_data.get("ollama") or {}
                                output_tokens = ollama_metrics.get("output_tokens")
                                input_tokens = ollama_metrics.get("prompt_tokens") or input_tokens
                                tokens_per_sec = ollama_metrics.get("tokens_per_s")
                        finally:
                            metrics_resp.close()  # Always close connection to prevent file descriptor leak
                    except Exception:
                        metrics_data = None

                    if output_tokens is None:
                        output_tokens = max(1, int(len(response_text) / 4))
                    if tokens_per_sec is None:
                        tokens_per_sec = output_tokens / total_time if total_time > 0 else 0

                    metrics = {
                        'ttft_ms': ttft or 0,
                        'tokens_per_sec': tokens_per_sec,
                        'total_time_ms': int(total_time * 1000),
                        'input_tokens': input_tokens,
                        'output_tokens': output_tokens
                    }
                    metrics['request_payload'] = payload
                    if resource_stats:
                        metrics['resource_stats'] = resource_stats
                        self._append_resource_samples(resource_stats)
                
                    # Stop watchdog successfully
                    if watchdog and not watchdog_stopped:
                        watchdog.stop()
                        watchdog_stopped = True

                    # Close streaming response to prevent file descriptor leak
                    if resp is not None:
                        try:
                            resp.close()
                        except Exception:
                            pass

                    result_queue.put((response_text, metrics))
                except TimeoutError as te:
                    # Request was killed by watchdog (TTFT, stall, hang, or max timeout)
                    logger.error(f"Request timed out: {te}")
                
                    # Stop monitoring
                    if watchdog and not watchdog_stopped:
                        watchdog.stop()
                        watchdog_stopped = True
                    if sampler and not sampler_stopped:
                        try:
                            resource_stats = sampler.stop()
                            sampler_stopped = True
                        except Exception:
                            pass
                
                    # Return error response with timeout metadata
                    timeout_type = watchdog.timeout_type if watchdog else 'unknown'
                    error_text = f"[TIMEOUT-{timeout_type.upper()}] {str(te)}"
                    error_metrics = {
                        'ttft_ms': ttft,
                        'total_time_ms': int((time.time() - start_time) * 1000),
                        'output_tokens': watchdog.total_tokens if watchdog else 0,
                        'tokens_per_sec': 0,
                        'timeout_killed': True,
                        'timeout_type': timeout_type,
                        'timeout_reason': str(te),
                        'tokens_before_timeout': watchdog.total_tokens if watchdog else 0,
                        'request_payload': payload
                    }
                    if resource_stats:
                        error_metrics['resource_stats'] = resource_stats
                        self._append_resource_samples(resource_stats)
                
                    result_queue.put((error_text, error_metrics))
                except Exception as e:
                    if resp is not None:
                        try:
                            resp.close()
                        except Exception:
                            pass
                    if watchdog and not watchdog_stopped:
                        try:
                            watchdog.stop()
                        except Exception:
                            pass
                    if sampler and resource_stats is None:
                        try:
                            sampler.stop()
                        except Exception:
                            pass
                    result_queue.put(e)
                finally:
                    socket.setdefaulttimeout(old_timeout)

            q = queue.Queue()
            t = threading.Thread(target=do_call, args=(q,), daemon=True)
            t.start()
            t.join(call_timeout)

            if t.is_alive():
                logger.error("Ollama call exceeded %ss, aborting request", call_timeout)
                raise TimeoutError(f"Ollama call exceeded {call_timeout}s")

            result = q.get_nowait()
            if isinstance(result, Exception):
                raise result
            return result, request_info

        last_exc = None
        for attempt in range(1, retries + 1):
            try:
                result, request_info = run_once(attempt)
                request_info["status"] = "completed"
                request_info["ended_at"] = datetime.utcnow().isoformat()
                response_text, metrics = result
                request_info["response_preview"] = self._preview_text(
                    response_text, RESPONSE_PREVIEW_LEN
                )
                self._set_last_request(request_info)
                logger.info(
                    "[ollama] request complete model=%s task=%s attempt=%s request_id=%s",
                    model_name,
                    request_info.get("task"),
                    attempt,
                    request_info.get("request_id"),
                )
                return response_text, metrics
            except Exception as exc:
                last_exc = exc
                request_info = {
                    "model": model_name,
                    "task": request_meta.get("task") if request_meta else None,
                    "sample_id": request_meta.get("sample_id") if request_meta else None,
                    "dialog_id": request_meta.get("dialog_id") if request_meta else None,
                    "turn_idx": request_meta.get("turn_idx") if request_meta else None,
                    "kind": request_meta.get("kind") if request_meta else None,
                    "attempt": attempt,
                    "endpoint": f"{self._app_base_url()}/api/stream",
                    "status": "failed",
                    "error": str(exc),
                    "ended_at": datetime.utcnow().isoformat(),
                    "prompt_preview": self._preview_prompt(prompt, multi_turn)
                }
                self._set_last_request(request_info)
                logger.warning(
                    "[ollama] request failed model=%s task=%s attempt=%s error=%s",
                    model_name,
                    request_info.get("task"),
                    attempt,
                    exc,
                )
                if is_retryable(exc):
                    if attempt < retries:
                        logger.warning("Retrying request after error...")
                        time.sleep(retry_backoff)
                        continue
                    continue
                break

        if last_exc and is_retryable(last_exc):
            logger.error("Ollama request failed after %s retries.", retries)
            logger.info("Waiting %ss to confirm GPU state...", hang_gpu_wait)
            time.sleep(hang_gpu_wait)
            util, source = self._check_gpu_utilization()
            if util is not None:
                logger.warning("GPU utilization after hang: %s%% (%s).", util, source)
                threshold = self.config.get('thermal', {}).get('gpu_utilization_guard', 10)
                if util > threshold:
                    self._restart_ollama(model_name, "hung request and GPU still high")
            else:
                logger.warning("GPU utilization unavailable after hang (%s).", source)
        if last_exc:
            raise last_exc
        raise RuntimeError("Ollama request failed without exception context.")

    def _classify_hardness(self, input_tokens: int, output_tokens: int) -> str:
        """Classify request hardness for cooldown."""
        if input_tokens > 16000 or output_tokens > 1000:
            return 'extreme'
        elif input_tokens > 2000 or output_tokens > 500:
            return 'heavy'
        elif input_tokens > 500 or output_tokens > 100:
            return 'medium'
        return 'light'
