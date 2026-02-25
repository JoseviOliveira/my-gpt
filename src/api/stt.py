"""
stt_api.py — Whisper-backed speech-to-text endpoints.
Provides:
  • GET  /api/stt/health
  • POST /api/stt
"""
from __future__ import annotations

from flask import Blueprint, request, jsonify, current_app, g
import io
import os
import time
from typing import Any

import numpy as np
import soundfile as sf

from src.audio.common import env_int, env_float, resample_mono
from src.core.config import is_guest_user

try:  # pragma: no cover - torch is an optional heavy dep
    import torch
except Exception as exc:  # pragma: no cover
    torch = None  # type: ignore
    _torch_err: Exception | None = exc
else:
    _torch_err = None

try:  # pragma: no cover - whisper is optional
    import whisper as _WhisperLib
except Exception as exc:
    _WhisperLib = None
    _import_err: Exception | None = exc
else:
    _import_err = _torch_err


stt_bp = Blueprint("stt", __name__)

# --- Configuration (env overridable) ---
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "int8")
WHISPER_THREADS = env_int("WHISPER_THREADS", 6)
WHISPER_DEVICE = (os.getenv("WHISPER_DEVICE", "cpu") or "cpu").strip().lower()
WHISPER_BEAM = max(1, env_int("WHISPER_BEAM", 5))
WHISPER_BEST_OF = max(1, env_int("WHISPER_BEST_OF", WHISPER_BEAM))
WHISPER_TEMPERATURE = env_float("WHISPER_TEMPERATURE", 0.2)
MAX_SECONDS = float(os.getenv("STT_MAX_SECONDS", "20"))

_model_cache: dict[tuple[str, str, str], Any] = {}


def _pick_dtype(device: str, compute: str | None):
    compute_norm = (compute or "").strip().lower()
    if torch is None:
        raise RuntimeError("PyTorch not available for Whisper backend")
    if device.startswith("cuda"):
        if compute_norm in {"fp16", "float16", "half"}:
            return torch.float16, "fp16", True
        return torch.float32, "fp32", False
    # CPU path: force float32 / fp16 not supported
    return torch.float32, "fp32", False


def _get_model() -> tuple[dict[str, Any], Any]:
    if _WhisperLib is None or torch is None:
        raise RuntimeError(f"whisper backend not available: {_import_err or 'torch import failed'}")

    device = (WHISPER_DEVICE or "cpu").strip().lower()
    dtype_obj, dtype_label, fp16_flag = _pick_dtype(device, WHISPER_COMPUTE)
    threads = int(WHISPER_THREADS)

    cache_key = (WHISPER_MODEL, device, dtype_label)
    model = _model_cache.get(cache_key)
    if model is not None:
        cfg = {
            "model": WHISPER_MODEL,
            "device": device,
            "compute": dtype_label,
            "threads": threads,
            "fp16": fp16_flag,
            "beam_size": WHISPER_BEAM,
            "best_of": WHISPER_BEST_OF,
            "temperature": WHISPER_TEMPERATURE,
        }
        return cfg, model

    if device == "cpu":
        torch.set_num_threads(max(1, threads))

    model = _WhisperLib.load_model(WHISPER_MODEL, device=device)
    # Force dtype for consistency
    model = model.to(dtype=dtype_obj)
    model.eval()

    _model_cache[cache_key] = model
    cfg = {
        "model": WHISPER_MODEL,
        "device": device,
        "compute": dtype_label,
        "threads": threads,
        "fp16": fp16_flag,
        "beam_size": WHISPER_BEAM,
        "best_of": WHISPER_BEST_OF,
        "temperature": WHISPER_TEMPERATURE,
    }
    return cfg, model


@stt_bp.get("/api/stt/health")
def stt_health():
    """Lightweight health check; does not load the Whisper model."""
    ok = (_WhisperLib is not None) and (torch is not None)
    cfg = {
        "model": WHISPER_MODEL,
        "compute": WHISPER_COMPUTE,
        "threads": WHISPER_THREADS,
        "device": WHISPER_DEVICE,
        "beam_size": WHISPER_BEAM,
        "best_of": WHISPER_BEST_OF,
        "temperature": WHISPER_TEMPERATURE,
    }
    return jsonify({
        "ok": ok,
        "model": cfg["model"],
        "compute": cfg["compute"],
        "threads": cfg["threads"],
        "device": cfg["device"],
        "max_seconds": MAX_SECONDS,
        "beam_size": cfg["beam_size"],
        "best_of": cfg["best_of"],
        "temperature": cfg["temperature"],
        "import_error": str(_import_err) if not ok else None,
    })


@stt_bp.post("/api/stt")
def stt():
    """
    multipart/form-data with field 'file' (WAV/PCM preferred).
    Returns: {"text","language","duration","rtf"}
    """
    if is_guest_user(getattr(g, "current_user", None)):
        return jsonify({"error": "guest_read_only"}), 403
    upload = request.files.get("file")
    if not upload:
        current_app.logger.warning("STT request missing file")
        return jsonify({"error": "missing file"}), 400
    data = upload.read()
    if not data:
        current_app.logger.warning("STT request empty file")
        return jsonify({"error": "empty file"}), 400

    # Decode to mono float32
    try:
        audio, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)
    except (sf.SoundFileError, ValueError, IOError) as exc:
        current_app.logger.warning("STT decode failed: %s", exc)
        return jsonify({"error": "invalid_audio_format"}), 400
    except Exception:
        current_app.logger.exception("STT unexpected decode error")
        raise

    abs_max = float(np.max(np.abs(audio))) if audio.size else 0.0
    current_app.logger.debug(
        "[STT] decode ok sr=%d len=%d max=%.4f min=%.4f mean=%.4f abs_max=%.4f",
        sr, len(audio), float(np.max(audio)), float(np.min(audio)), float(np.mean(audio)), abs_max
    )

    TARGET_SR = 16000
    if sr != TARGET_SR:
        audio, sr = resample_mono(audio, sr, TARGET_SR)
        current_app.logger.debug("[STT] resampled to %d Hz, len=%d", TARGET_SR, len(audio))

    duration = len(audio) / float(sr or 1)
    if duration > MAX_SECONDS:
        audio = audio[: int(sr * MAX_SECONDS)]
        duration = MAX_SECONDS
        current_app.logger.debug("[STT] trimmed audio to max duration %.2fs", duration)

    # Optional hints from the client
    lang_hint = (request.form.get("lang") or request.form.get("language") or "").strip()
    task = (request.form.get("task") or "transcribe").strip().lower()  # or "translate"
    initial_prompt = (request.form.get("prompt") or "").strip() or None

    # Gentle peak normalization to help ASR on quiet recordings
    if abs_max > 1e-3 and abs_max < 0.97:
        gain = min(2.0, 0.97 / abs_max)
        audio = (audio * gain).astype("float32")
        current_app.logger.debug("[STT] normalized gain=%.2f", gain)

    t0 = time.time()
    try:
        model_cfg, model = _get_model()
    except RuntimeError as exc:
        current_app.logger.exception("STT backend unavailable")
        return jsonify({"error": "backend_unavailable", "detail": str(exc)}), 500

    current_app.logger.debug(
        "[STT] starting transcription",
        extra={
            "model": model_cfg["model"],
            "compute": model_cfg["compute"],
            "threads": model_cfg["threads"],
            "beam_size": model_cfg["beam_size"],
            "best_of": model_cfg["best_of"],
            "temperature": model_cfg["temperature"],
            "duration": duration,
            "amplitude": abs_max,
            "lang_hint": lang_hint,
            "task": task,
            "initial_prompt_len": (len(initial_prompt) if initial_prompt else 0),
            "device": model_cfg["device"],
        },
    )

    def _run_pass(temp: float):
        options = {
            "language": (lang_hint or None),
            "task": task,
            "beam_size": max(model_cfg["beam_size"], 1),
            "best_of": max(model_cfg.get("best_of") or model_cfg["beam_size"], 1),
            "temperature": temp,
            "fp16": bool(model_cfg.get("fp16")),
            "initial_prompt": initial_prompt,
            "verbose": False,
        }
        return model.transcribe(audio, **options)

    try:
        result = _run_pass(model_cfg["temperature"])
    except Exception as exc:
        current_app.logger.exception(
            "STT primary transcription failed",
            extra={"model": model_cfg["model"]},
        )
        return jsonify({
            "error": "transcribe_failed",
            "detail": str(exc),
            "stage": "primary",
        }), 500

    text = (result.get("text") or "").strip()
    segments = result.get("segments") or []
    lang = result.get("language")
    current_app.logger.debug(
        "[STT] pass1 result",
        extra={
            "segments": len(segments),
            "text_len": len(text),
            "language": lang,
            "model": model_cfg["model"],
        },
    )

    if not text:
        try:
            fallback_temp = min(1.0, model_cfg["temperature"] + 0.3)
            result2 = _run_pass(fallback_temp)
            segments2 = result2.get("segments") or []
            text2 = (result2.get("text") or "").strip()
        except Exception as exc:
            current_app.logger.exception(
                "STT fallback transcription failed",
                extra={"model": model_cfg["model"], "stage": "fallback"},
            )
            return jsonify({
                "error": "transcribe_failed",
                "detail": str(exc),
                "stage": "fallback",
            }), 500
        current_app.logger.debug(
            "[STT] pass2 result",
            extra={
                "segments": len(segments2),
                "text_len": len(text2),
                "language": result2.get("language"),
                "model": model_cfg["model"],
            },
        )
        if text2:
            text = text2
            segments = segments2
            lang = result2.get("language") or lang

    elapsed = time.time() - t0
    rtf = round(elapsed / max(0.01, duration), 2)
    response_payload = {
        "text": text,
        "language": lang or (lang_hint or "unknown"),
        "language_auto": False if lang_hint else True,
        "sample_rate": sr,
        "task": task,
        "duration": duration,
        "rtf": rtf,
        "config": {
            "model": model_cfg["model"],
            "compute": model_cfg["compute"],
            "threads": model_cfg["threads"],
            "beam_size": model_cfg["beam_size"],
            "best_of": model_cfg["best_of"],
            "temperature": model_cfg["temperature"],
            "device": model_cfg["device"],
        },
        "segments": [
            {
                "text": seg.get("text", ""),
                "start": seg.get("start"),
                "end": seg.get("end"),
                "avg_logprob": seg.get("avg_logprob"),
                "compression_ratio": seg.get("compression_ratio"),
                "no_speech_prob": seg.get("no_speech_prob"),
            }
            for seg in segments
        ],
    }
    current_app.logger.info(
        "[STT] completed transcription",
        extra={
            "model": model_cfg["model"],
            "rtf": rtf,
            "duration": duration,
            "text_len": len(text),
            "elapsed": elapsed,
        },
    )
    return jsonify(response_payload)
