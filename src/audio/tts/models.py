"""Model resolution helpers for the TTS backend."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Tuple

from flask import current_app

from src.audio.tts.runtime import load_tts_model

from .constants import (
    DEFAULT_SPEAKER_BY_LANG as CONST_DEFAULT_SPEAKER_BY_LANG,
    MODEL_BY_LANG as CONST_MODEL_BY_LANG,
    VCTK_SPEAKERS,
    VOICE_ALIASES,
)


def _load_lang_overrides(prefix: str) -> Dict[str, str]:
    overrides: Dict[str, str] = {}
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        lang = key[len(prefix):].lower()
        val = (value or "").strip()
        if not lang or not val:
            continue
        overrides[lang] = val
    return overrides


MODEL_BY_LANG = dict(CONST_MODEL_BY_LANG)
MODEL_BY_LANG.update(_load_lang_overrides("TTS_MODEL_LANG_"))

DEFAULT_SPEAKER_BY_LANG = dict(CONST_DEFAULT_SPEAKER_BY_LANG)
DEFAULT_SPEAKER_BY_LANG.update(_load_lang_overrides("TTS_SPEAKER_LANG_"))


def resolve_voice_and_language(payload_lang: str, payload_voice: str) -> Tuple[str, str]:
    """Resolve language and speaker applying UI aliases and defaults."""
    lang = (payload_lang or "").strip().lower() or "en"
    voice = (payload_voice or "").strip()

    if voice and voice in VOICE_ALIASES:
        alias = VOICE_ALIASES[voice]
        if alias.get("lang"):
            lang = alias["lang"].lower()
        if alias.get("speaker"):
            voice = alias["speaker"]

    if not voice:
        voice = DEFAULT_SPEAKER_BY_LANG.get(lang, "p225")
    if lang == "en" and voice not in VCTK_SPEAKERS:
        voice = "p225"
    return lang, voice


def apply_model_overrides(coqui_ctor: Any, preset: Dict[str, Any], model: Any, lang: str, quality_key: str, logger: logging.Logger) -> Tuple[Dict[str, Any], Any]:
    """Override preset model based on language defaults and reload if needed."""
    requested_model = MODEL_BY_LANG.get(lang)
    current_model = (preset.get("model") or "").strip()
    if requested_model and requested_model != current_model:
        preset["model"] = requested_model
        if lang:
            preset["language"] = lang
        try:
            model = load_tts_model(coqui_ctor, preset, logger=logger)
            logger.info(
                "[TTS] switched model for language request",
                extra={"lang": lang, "model": requested_model, "quality": quality_key},
            )
            return preset, model
        except Exception as exc:
            logger.warning(
                "[TTS] failed to load override model; keeping previous preset",
                extra={"lang": lang, "model": requested_model, "error": str(exc)},
            )
            preset["model"] = current_model
            return preset, model
    return preset, model


def detect_model_capabilities(model: Any) -> Tuple[bool, bool, list[str]]:
    """Return (supports_multi_language, supports_multi_speaker, available_speakers)."""
    synth = getattr(model, "synthesizer", None)
    speaker_mgr = getattr(synth, "speaker_manager", None)

    multi_lang = bool(
        getattr(model, "is_multi_lingual", False)
        or getattr(synth, "is_multi_lingual", False)
    )

    speakers: list[str] = []
    candidate_attrs = ("speaker_ids", "speakers", "speaker_names")
    for src in (speaker_mgr, synth, model):
        if not src:
            continue
        for attr in candidate_attrs:
            data = getattr(src, attr, None)
            if not data:
                continue
            if isinstance(data, dict):
                data = list(data.keys())
            speakers = [str(s) for s in list(data)]
            if speakers:
                break
        if speakers:
            break

    num_speakers = (
        getattr(model, "num_speakers", None)
        or getattr(synth, "num_speakers", None)
        or getattr(speaker_mgr, "num_speakers", None)
    )
    try:
        numeric = int(num_speakers) if num_speakers is not None else None
    except (TypeError, ValueError):
        numeric = None

    multi_speaker = bool((numeric and numeric > 1) or len(speakers) > 1)
    return multi_lang, multi_speaker, speakers


__all__ = [
    "resolve_voice_and_language",
    "apply_model_overrides",
    "detect_model_capabilities",
]
