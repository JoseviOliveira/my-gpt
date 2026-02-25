"""Chunked streaming helpers for the TTS API."""

from __future__ import annotations

import base64
import io
import json
import time
from typing import Dict, Generator, Iterable, Tuple

import soundfile as sf

from src.audio.tts.runtime import synthesise_audio


def build_streaming_response(
    chunks: Iterable[str],
    total_chunks: int,
    synthesis_payload: Dict[str, object],
    model,
    preset: Dict[str, object],
    *,
    logger,
    quality_key: str,
    selected_voice: str,
    effective_lang: str,
) -> Generator[str, None, None]:
    """Yield NDJSON messages for each synthesised chunk."""
    sample_rate = None
    total_audio_dur = 0.0
    start_time = time.time()

    metadata = {
        "type": "meta",
        "chunks": total_chunks,
        "language": effective_lang,
        "quality": quality_key,
        "model": preset.get("model"),
        "speaker": selected_voice or "",
    }
    yield json.dumps(metadata) + "\n"

    for idx, ch in enumerate(chunks):
        chunk_start = time.time()
        local_payload = dict(synthesis_payload)
        try:
            audio, sr = synthesise_audio(model, ch, preset, local_payload, logger=logger)
        except Exception as err:  # pragma: no cover - defensive
            logger.exception(
                "[TTS] synthesis failed",
                extra={"quality": quality_key, "chunk_index": idx, "chunks_total": total_chunks},
            )
            yield json.dumps({"type": "error", "detail": str(err)}) + "\n"
            return

        if sample_rate is None:
            sample_rate = sr
        duration = len(audio) / float(sr) if sr else 0.0
        total_audio_dur += duration

        buf = io.BytesIO()
        sf.write(buf, audio, sr, format="WAV")
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")

        chunk_payload = {
            "type": "chunk",
            "index": idx,
            "total": total_chunks,
            "duration": duration,
            "elapsed": time.time() - chunk_start,
            "sample_rate": sr,
            "audio": encoded,
        }
        yield json.dumps(chunk_payload) + "\n"

    total_elapsed = time.time() - start_time
    logger.info(
        "[TTS] synthesis complete",
        extra={
            "quality": quality_key,
            "model": preset.get("model"),
            "speaker": selected_voice or preset.get("speaker"),
            "language": effective_lang or preset.get("language"),
            "duration": total_audio_dur,
            "elapsed": total_elapsed,
            "rtf": round((total_audio_dur / total_elapsed) if total_elapsed > 0 else 0.0, 3),
            "sample_rate": sample_rate,
            "chunks": total_chunks,
        },
    )
    yield json.dumps(
        {
            "type": "done",
            "duration": total_audio_dur,
            "elapsed": total_elapsed,
            "chunks": total_chunks,
            "sample_rate": sample_rate,
        }
    ) + "\n"


__all__ = ["build_streaming_response"]
