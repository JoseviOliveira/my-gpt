"""
tts_api.py — Flask blueprint providing the Coqui-backed TTS endpoints.

This refactored version delegates most heavy lifting to ``tts_backend`` modules
so the route handlers remain concise and easier to reason about.
"""

from __future__ import annotations

import datetime
import logging
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable

import soundfile as sf
from flask import Blueprint, current_app, jsonify, request, stream_with_context, g

from src.audio.tts.runtime import TTS_QUALITY_DEFAULT, get_tts_model
from src.core.logging import LOG_TIME_FORMAT, LOG_FORMAT, RequestContextFilter
from src.core.config import is_guest_user

from src.audio.tts import (
    constants,
    TTS_CHUNK_CHAR_LIMIT_DEFAULT,
    TTS_CHUNK_LEN_FLEX_DEFAULT,
    ensure_env_logged,
    env_snapshot,
    external_tts_available,
    COQUI_TTS_PY,
    COQUI_TTS_SCRIPT,
    resolve_voice_and_language,
    apply_model_overrides,
    detect_model_capabilities,
    normalise_text,
    chunk_text,
    build_streaming_response,
)


def _import_coqui_class() -> tuple[Any | None, Exception | None]:
    try:
        from TTS.api import TTS as CoquiClass  # type: ignore
        return CoquiClass, None
    except Exception as exc:  # pragma: no cover - import guard
        return None, exc


_RuntimeCoquiTTS, _tts_import_err = _import_coqui_class()

tts_bp = Blueprint("tts", __name__)


@tts_bp.record
def _register_coqui(setup_state):
    """Expose the Coqui class on the Flask config for downstream helpers."""
    if _RuntimeCoquiTTS is not None:
        setup_state.app.config.setdefault("COQUI_CLASS", _RuntimeCoquiTTS)


metrics_logger = logging.getLogger("tts.metrics")
# Use standard logging format with correlation IDs
if not metrics_logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_TIME_FORMAT)
    handler.setFormatter(formatter)
    handler.addFilter(RequestContextFilter())
    metrics_logger.addHandler(handler)
else:
    for handler in metrics_logger.handlers:
        formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_TIME_FORMAT)
        handler.setFormatter(formatter)
        handler.addFilter(RequestContextFilter())
metrics_logger.setLevel(logging.INFO)
metrics_logger.propagate = False


def _external_tts(text: str, lang: str, speaker: str | None) -> bytes:
    """Invoke the helper script inside the dedicated Coqui virtualenv."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        out_path = tmp.name
    try:
        cmd = [
            COQUI_TTS_PY,
            COQUI_TTS_SCRIPT,
            "--lang",
            (lang or "en"),
            "--text",
            text,
            "--out",
            out_path,
            "--verbose",
        ]
        if speaker and (lang or "en").lower().startswith("en"):
            cmd += ["--speaker", speaker]

        env = os.environ.copy()
        env.setdefault("PATH", "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", ""))
        env.setdefault("ESPEAK_PATH", "/opt/homebrew/bin/espeak-ng")
        env.setdefault("PHONEMIZER_ESPEAK_PATH", "/opt/homebrew/bin/espeak-ng")
        env.setdefault("LC_ALL", "en_US.UTF-8")
        env.setdefault("LANG", "en_US.UTF-8")
        env.setdefault("HOME", str(Path.home()))
        env.setdefault("XDG_CACHE_HOME", str(Path.home() / "local-chat" / "tts_cache"))
        env.setdefault("COQUI_TTS_CACHE", str(Path.home() / "local-chat" / "tts_cache"))
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        if os.path.exists(out_path) and os.path.getsize(out_path) > 44:
            if proc.returncode != 0:
                current_app.logger.warning(
                    "[TTS] helper returned non-zero but WAV exists",
                    extra={"rc": proc.returncode},
                )
            with open(out_path, "rb") as fh:
                return fh.read()
        err = (proc.stderr or proc.stdout or "external TTS failed")
        raise RuntimeError(err[-4000:].strip())
    finally:
        try:
            os.unlink(out_path)
        except Exception:
            pass


@tts_bp.get("/api/tts/health")
def tts_health():
    """Report which TTS path is available and key settings."""
    in_process = _RuntimeCoquiTTS is not None
    external_ok = external_tts_available()
    return jsonify({
        "ok": in_process or external_ok,
        "in_process": in_process,
        "external": external_ok,
        "import_error": str(_tts_import_err) if _tts_import_err else None,
        "script": COQUI_TTS_SCRIPT,
        "python": COQUI_TTS_PY,
        "model": constants.MODEL_BY_LANG.get("en"),
        "quality_default": TTS_QUALITY_DEFAULT,
    })


def _resolve_chunk_length(payload: Dict[str, Any]) -> int:
    raw = payload.get("chunk_char_len") or payload.get("chunk_len")
    if raw is None:
        return TTS_CHUNK_CHAR_LIMIT_DEFAULT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return TTS_CHUNK_CHAR_LIMIT_DEFAULT
    return max(80, min(value, 400))


def _prepare_chunks(text: str, lang: str, payload: Dict[str, Any], logger: logging.Logger) -> tuple[str, Iterable[str], int]:
    normalised = normalise_text(text, lang)
    if normalised != text:
        logger.debug(
            "[TTS] text normalised for language",
            extra={
                "lang": lang,
                "original_len": len(text),
                "normalised_len": len(normalised),
            },
        )
    chunk_len = _resolve_chunk_length(payload)
    chunks = chunk_text(normalised, max_len=chunk_len, flex=TTS_CHUNK_LEN_FLEX_DEFAULT) or [normalised]
    logger.debug(
        "[TTS] chunking text",
        extra={"chunk_len": chunk_len, "chunks": len(chunks), "text_len": len(normalised)},
    )
    return normalised, chunks, len(chunks)


@tts_bp.post("/api/tts/speak")
def tts():
    """Synthesize text using Coqui TTS (or an external helper)."""
    if is_guest_user(getattr(g, "current_user", None)):
        return jsonify({"error": "guest_read_only"}), 403
    logger = current_app.logger
    ensure_env_logged(logger)

    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    if not text:
        logger.warning("[TTS] request missing text")
        return jsonify({"error": "missing text"}), 400

    req_id = uuid.uuid4().hex[:8]
    preview = text[:140].replace("\n", " ") + ("…" if len(text) > 140 else "")
    logger.debug(
        "[TTS] req=%s recv text_len=%d preview=%s",
        req_id,
        len(text),
        preview,
        extra={"env": env_snapshot()},
    )

    requested_quality = payload.get("quality")
    lang, voice = resolve_voice_and_language(
        payload.get("language") or payload.get("lang") or "",
        payload.get("voice") or payload.get("speaker") or "",
    )

    if _RuntimeCoquiTTS is None:
        if not external_tts_available():
            return jsonify({
                "error": str(_tts_import_err),
                "hint": "Install TTS or configure COQUI_TTS_PY/COQUI_TTS_SCRIPT",
            }), 503
        try:
            wav_bytes = _external_tts(text, lang, voice)
        except (OSError, subprocess.CalledProcessError, FileNotFoundError) as exc:
            logger.warning("[TTS] external synthesis failed: %s", exc)
            return jsonify({"error": "tts_failed"}), 500
        except Exception:
            logger.exception("[TTS] unexpected synthesis error")
            raise
        resp = current_app.response_class(wav_bytes, mimetype="audio/wav")
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-TTS-Quality"] = (requested_quality or TTS_QUALITY_DEFAULT)
        resp.headers["X-TTS-Fallback"] = "external"
        resp.headers["X-TTS-Lang"] = lang
        resp.headers["X-TTS-Speaker"] = voice
        return resp

    quality_key, preset, model = get_tts_model(_RuntimeCoquiTTS, requested_quality, logger=logger)
    preset, model = apply_model_overrides(_RuntimeCoquiTTS, preset, model, lang, quality_key, logger)
    cap_multi_lang, cap_multi_speaker, available_speakers = detect_model_capabilities(model)

    effective_lang = lang
    if not cap_multi_lang:
        default_lang = (preset.get("language") or "").strip().lower() or "en"
        if lang != default_lang:
            logger.info(
                "[TTS] model lacks multilingual support; requested language not available (model=%s)",
                preset.get("model"),
                extra={
                    "requested_lang": lang,
                    "fallback_lang": default_lang,
                    "model": preset.get("model"),
                    "quality": requested_quality or TTS_QUALITY_DEFAULT,
                    "payload": {k: payload.get(k) for k in ("language", "lang", "voice", "speaker", "quality")},
                },
            )
            return jsonify({
                "error": "language_not_supported",
                "requested": lang,
                "supported": default_lang,
            }), 422
        preset["language"] = None
        effective_lang = default_lang

    selected_voice = voice or (preset.get("speaker") or "")
    if not cap_multi_speaker:
        preset["speaker"] = None
        selected_voice = ""
    else:
        if not selected_voice and available_speakers:
            selected_voice = available_speakers[0]
        if effective_lang == "en" and selected_voice not in constants.VCTK_SPEAKERS:
            logger.info("[TTS] overriding EN speaker to p225 for model capability check", extra={"voice": selected_voice})
            selected_voice = "p225"

    synthesis_payload = dict(payload)
    synthesis_payload.pop("voice", None)
    if cap_multi_lang and effective_lang:
        synthesis_payload["language"] = effective_lang
    else:
        synthesis_payload.pop("language", None)
    if cap_multi_speaker and selected_voice:
        synthesis_payload["speaker"] = selected_voice
    else:
        synthesis_payload.pop("speaker", None)

    logger.info(
        "[TTS] req=%s resolved lang=%s voice=%s in_process=True external_ok=%s multi_lang=%s multi_speaker=%s",
        req_id,
        effective_lang,
        selected_voice or "",
        external_tts_available(),
        cap_multi_lang,
        cap_multi_speaker,
    )

    tracked_payload = {
        k: synthesis_payload.get(k)
        for k in ("language", "speaker", "style_wav", "emotion", "speed", "length_scale", "noise_scale", "noise_scale_w", "denoiser_strength")
    }
    logger.info(
        "[TTS] synthesiser call prepared",
        extra={
            "req": req_id,
            "quality": quality_key,
            "model": preset.get("model"),
            "payload": {k: v for k, v in tracked_payload.items() if v is not None},
        },
    )

    normalised_text, chunks, total_chunks = _prepare_chunks(text, effective_lang, payload, logger)
    chunk_iterable = list(chunks)

    def _stream():
        yield from build_streaming_response(
            chunk_iterable,
            total_chunks,
            synthesis_payload,
            model,
            preset,
            logger=logger,
            quality_key=quality_key,
            selected_voice=selected_voice,
            effective_lang=effective_lang,
        )

    response = current_app.response_class(
        stream_with_context(_stream()),
        mimetype="application/x-ndjson",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-TTS-Quality"] = quality_key
    model_name = preset.get("model")
    if model_name:
        response.headers["X-TTS-Model"] = str(model_name)
    response.headers["X-TTS-Lang"] = effective_lang
    response.headers["X-TTS-Speaker"] = selected_voice or ""
    response.headers["X-TTS-Chunks"] = str(total_chunks)
    response.headers["X-TTS-Mode"] = "chunked"
    return response


@tts_bp.post("/api/tts/metrics")
def tts_metrics():
    """Capture client-side latency metrics for TTS playback start."""
    if is_guest_user(getattr(g, "current_user", None)):
        return jsonify({"error": "guest_read_only"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        elapsed_ms = float(payload.get("elapsed_ms"))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_elapsed"}), 400

    mode = (payload.get("mode") or "").strip().lower()
    lang_val = (payload.get("lang") or "").strip()
    try:
        text_len = int(payload.get("text_len") or 0)
    except (TypeError, ValueError):
        text_len = 0

    elapsed_sec = round(elapsed_ms / 1000.0, 1)
    metrics_logger.info(
        "[TTS] client playback latency mode=%s lang=%s text_len=%d elapsed_s=%.1f",
        mode,
        lang_val,
        text_len,
        elapsed_sec,
    )
    return jsonify({"ok": True})
