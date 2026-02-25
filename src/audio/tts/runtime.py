"""
tts_runtime.py — minimal Coqui TTS helpers shared by the API and CLI.
Keeps a tiny preset system, lazy model caching, and a thin wrapper that
normalises the waveform returned by ``model.tts``.
"""
from __future__ import annotations

import inspect
import logging
import os
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np

from src.audio.common import env_bool, env_optional_float, env_str

# ---------------------------------------------------------------------------
# Environment-derived defaults
# ---------------------------------------------------------------------------
TTS_MODEL_DEFAULT = env_str("TTS_MODEL_DEFAULT", "tts_models/en/vctk/vits")
TTS_DEVICE_DEFAULT = env_str("TTS_DEVICE", "cpu").lower() or "cpu"
TTS_SPEAKER_DEFAULT = env_str("TTS_SPEAKER_DEFAULT")
TTS_LANGUAGE_DEFAULT = env_str("TTS_LANGUAGE_DEFAULT")
TTS_QUALITY_DEFAULT = (env_str("TTS_QUALITY_DEFAULT", os.getenv("TTS_QUALITY", "normal")) or "normal").lower()
if TTS_QUALITY_DEFAULT not in {"normal", "better", "best"}:
    TTS_QUALITY_DEFAULT = "normal"

Preset = Dict[str, str | float | None]
ModelCacheKey = Tuple[str, str, str]


def _make_preset(name: str, fallback: Preset) -> Preset:
    upper = name.upper()
    return {
        "model": env_str(f"TTS_QUALITY_{upper}_MODEL", fallback.get("model", TTS_MODEL_DEFAULT)),
        "config": env_str(f"TTS_QUALITY_{upper}_CONFIG", fallback.get("config", "")),
        "speaker": env_str(f"TTS_QUALITY_{upper}_SPEAKER", fallback.get("speaker", TTS_SPEAKER_DEFAULT)),
        "language": env_str(f"TTS_QUALITY_{upper}_LANGUAGE", fallback.get("language", TTS_LANGUAGE_DEFAULT)),
        "device": env_str(f"TTS_QUALITY_{upper}_DEVICE", fallback.get("device", TTS_DEVICE_DEFAULT)),
        "style_wav": env_str(f"TTS_QUALITY_{upper}_STYLE_WAV", fallback.get("style_wav", "")),
        "emo": env_str(f"TTS_QUALITY_{upper}_EMOTION", fallback.get("emo", "")),
        "speed": env_optional_float(f"TTS_QUALITY_{upper}_SPEED", fallback.get("speed")),
        "length_scale": env_optional_float(f"TTS_QUALITY_{upper}_LENGTH_SCALE", fallback.get("length_scale")),
        "noise_scale": env_optional_float(f"TTS_QUALITY_{upper}_NOISE_SCALE", fallback.get("noise_scale")),
        "noise_scale_w": env_optional_float(f"TTS_QUALITY_{upper}_NOISE_SCALE_W", fallback.get("noise_scale_w")),
        "denoiser_strength": env_optional_float(
            f"TTS_QUALITY_{upper}_DENOISER_STRENGTH", fallback.get("denoiser_strength")
        ),
        "normalize": env_bool(f"TTS_QUALITY_{upper}_NORMALIZE", fallback.get("normalize", True)),
        "sample_rate": env_optional_float(f"TTS_QUALITY_{upper}_SAMPLE_RATE", fallback.get("sample_rate")),
    }


_TTS_PRESET_BASE: Preset = {
    "model": TTS_MODEL_DEFAULT,
    "device": TTS_DEVICE_DEFAULT,
    "speaker": TTS_SPEAKER_DEFAULT,
    "language": TTS_LANGUAGE_DEFAULT,
    "normalize": True,
}

TTS_QUALITY_PRESETS: Dict[str, Preset] = {
    "normal": _make_preset("normal", _TTS_PRESET_BASE),
    "better": _make_preset("better", _TTS_PRESET_BASE),
    "best": _make_preset("best", _TTS_PRESET_BASE),
}

_MODEL_CACHE: Dict[ModelCacheKey, Any] = {}


def resolve_tts_preset(value: str | None) -> tuple[str, Preset]:
    key = (value or TTS_QUALITY_DEFAULT).strip().lower()
    preset = dict(TTS_QUALITY_PRESETS.get(key, TTS_QUALITY_PRESETS["normal"]))
    if not preset.get("model"):
        preset["model"] = TTS_MODEL_DEFAULT
    preset["device"] = (preset.get("device") or TTS_DEVICE_DEFAULT or "cpu").strip().lower()
    preset["config"] = (preset.get("config") or "").strip()
    return key, preset


def load_tts_model(coqui_ctor: Any, preset: Preset, *, logger: logging.Logger | None = None) -> Any:
    if coqui_ctor is None:
        raise RuntimeError("Coqui TTS not available")

    model_spec = (preset.get("model") or TTS_MODEL_DEFAULT).strip()
    config_path = (preset.get("config") or "").strip()
    device = (preset.get("device") or TTS_DEVICE_DEFAULT or "cpu").strip().lower()

    cache_key: ModelCacheKey = (model_spec or "default", device, config_path or "")
    cached = _MODEL_CACHE.get(cache_key)
    if cached is not None:
        return cached

    kwargs: Dict[str, object] = {"progress_bar": False}
    if device.startswith(("cuda", "gpu")):
        kwargs["gpu"] = True
    if config_path:
        kwargs["config_path"] = config_path
    if model_spec and Path(model_spec).exists():
        kwargs["model_path"] = model_spec
    else:
        kwargs["model_name"] = model_spec or TTS_MODEL_DEFAULT

    if logger:
        logger.debug("[TTS] loading model", extra={"kwargs": kwargs})
    model = coqui_ctor(**kwargs)
    _MODEL_CACHE[cache_key] = model
    return model


def get_tts_model(coqui_ctor: Any, value: str | None, *, logger: logging.Logger | None = None) -> tuple[str, Preset, Any]:
    key, preset = resolve_tts_preset(value)
    model = load_tts_model(coqui_ctor, preset, logger=logger)
    return key, preset, model


def _maybe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_tts_kwargs(model: Any, preset: Preset, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        params = set(inspect.signature(model.tts).parameters.keys())
    except (TypeError, ValueError):
        params = set()

    def pick(name: str, key: str | None = None, *, cast_float: bool = False) -> Any:
        source_key = key or name
        value = payload.get(source_key)
        if value in (None, ""):
            value = preset.get(source_key)
        if value in (None, ""):
            return None
        if cast_float:
            value = _maybe_float(value)
        if value is None:
            return None
        return value if name in params else None

    kwargs: Dict[str, Any] = {}
    for field in ("speaker", "language", "style_wav", "emotion"):
        val = pick(field)
        if val is not None:
            kwargs[field if field != "emotion" else "emotion"] = val

    for field in ("speed", "length_scale", "noise_scale", "noise_scale_w", "denoiser_strength"):
        val = pick(field, cast_float=True)
        if val is not None:
            kwargs[field] = val

    if "split_sentences" in params and "split_sentences" in payload:
        kwargs["split_sentences"] = bool(payload["split_sentences"])

    return kwargs


def synthesise_audio(
    model: Any,
    text: str,
    preset: Preset,
    payload: Dict[str, Any],
    *,
    logger: logging.Logger | None = None,
) -> tuple[np.ndarray, int]:
    kwargs = build_tts_kwargs(model, preset, payload)
    try:
        audio = model.tts(text, **kwargs)
    except TypeError:
        # Some models reject extra kwargs; retry without them.
        audio = model.tts(text)

    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = np.mean(audio, axis=0)

    if bool(preset.get("normalize", True)):
        max_abs = float(np.max(np.abs(audio))) if audio.size else 0.0
        if max_abs > 0:
            audio = (audio / max_abs).astype(np.float32)

    sample_rate = getattr(model, "output_sample_rate", None)
    if not sample_rate:
        synth = getattr(model, "synthesizer", None)
        sample_rate = getattr(synth, "output_sample_rate", None) if synth else None
    if not sample_rate:
        sample_rate = int(preset.get("sample_rate") or 22050)

    if logger:
        logger.debug(
            "[TTS] chunk synthesised",
            extra={
                "len": len(audio),
                "sample_rate": sample_rate,
                "kwargs": {k: v for k, v in kwargs.items() if v is not None},
            },
        )

    return audio.astype(np.float32), int(sample_rate)


__all__ = [
    "TTS_MODEL_DEFAULT",
    "TTS_DEVICE_DEFAULT",
    "TTS_SPEAKER_DEFAULT",
    "TTS_LANGUAGE_DEFAULT",
    "TTS_QUALITY_DEFAULT",
    "TTS_QUALITY_PRESETS",
    "resolve_tts_preset",
    "load_tts_model",
    "get_tts_model",
    "build_tts_kwargs",
    "synthesise_audio",
]
