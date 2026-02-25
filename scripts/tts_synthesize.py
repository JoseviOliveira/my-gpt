#!/usr/bin/env python3
"""
tts_synthesize.py — external helper executed inside the dedicated Coqui venv.
This version selects the best model per language (EN→VITS; FR/ES→XTTSv2),
normalises punctuation for VITS, and sets a robust default speaker.
It writes the WAV directly using Coqui's API (no tts_runtime dependency),
so it is self‑contained and stable across environments.
"""
from __future__ import annotations

import argparse
import io
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import soundfile as sf

import re
try:
    from num2words import num2words  # optional, improves number reading
except Exception:
    num2words = None  # fallback to simple digit spelling

# -----------------------------
# Config: model choices & voices
# -----------------------------
MODEL_BY_LANG = {
    "en": "tts_models/en/vctk/vits",   # fast, good EN
    "fr": "tts_models/fr/css10/vits",  # fast, clear FR (single speaker)
    "es": "tts_models/es/css10/vits",  # fast, clear ES (single speaker)
    # keep XTTS only if you later add a “rich multilingual” toggle
    # "fr": "tts_models/multilingual/multi-dataset/xtts_v2",
    # "es": "tts_models/multilingual/multi-dataset/xtts_v2",
}

VCTK_DEFAULT = "p225"            # good English VCTK voice
XTTS_DEFAULT = "Alma María"      # robust XTTS name on your checkpoint

# -----------------------------
# Utilities
# -----------------------------

def normalise_text(text: str) -> str:
    """Replace curly quotes and exotic punctuation that VITS vocab rejects."""
    return (
        text.replace("’", "'")
            .replace("“", '"')
            .replace("”", '"')
            .replace("–", "-")
            .replace("—", "-")
    )

DIGIT_WORDS = {
    'es': {"0":"cero","1":"uno","2":"dos","3":"tres","4":"cuatro","5":"cinco","6":"seis","7":"siete","8":"ocho","9":"nueve"},
    'fr': {"0":"zéro","1":"un","2":"deux","3":"trois","4":"quatre","5":"cinq","6":"six","7":"sept","8":"huit","9":"neuf"},
}

def _spell_digits(s: str, lang: str) -> str:
    table = DIGIT_WORDS.get(lang, DIGIT_WORDS['es'])
    return ' '.join(table.get(ch, ch) for ch in s)

def _int_to_words(n_str: str, lang: str) -> str:
    # remove thousands separators
    clean = n_str.replace(',', '').replace('.', '')
    if not clean.isdigit():
        return n_str
    if num2words:
        try:
            n = int(clean)
            return num2words(n, lang=lang)
        except Exception:
            pass
    # fallback: spell digits
    return _spell_digits(clean, lang)

def _decimal_to_words(int_part: str, frac_part: str, lang: str) -> str:
    if lang == 'fr':
        sep = ' virgule '
    else:  # 'es' and default
        sep = ' coma '
    left = _int_to_words(int_part, lang)
    # speak fractional part digit by digit to keep it short & robust
    right = _spell_digits(frac_part, lang)
    return f"{left}{sep}{right}"

_DECIMAL_RE = re.compile(r"(?<![\w/])(\d+)[\.,](\d+)(?![\w/])")
_INT_RE = re.compile(r"(?<![\w/])(\d+)(?![\w/])")
_PCT_RE = re.compile(r"(?<![\w/])(\d+)%")

def verbalise_numbers(text: str, lang: str) -> str:
    # percentages
    if lang == 'fr':
        text = _PCT_RE.sub(lambda m: f"{_int_to_words(m.group(1), lang)} pour cent", text)
    else:
        text = _PCT_RE.sub(lambda m: f"{_int_to_words(m.group(1), lang)} por ciento", text)
    # decimals first
    text = _DECIMAL_RE.sub(lambda m: _decimal_to_words(m.group(1), m.group(2), lang), text)
    # plain integers (limit length to avoid IDs)
    def _int_sub(m):
        s = m.group(1)
        if len(s) > 6:  # avoid very long ids; spell digits only
            return _spell_digits(s, lang)
        return _int_to_words(s, lang)
    text = _INT_RE.sub(_int_sub, text)
    return text


def pick_xtts_speaker(tts) -> str:
    """Choose an XTTS speaker. Prefer explicit names; if none exposed, fall back to a generic acceptable label."""
    # Try internal speaker_manager lists first
    try:
        sm = tts.synthesizer.tts_model.speaker_manager
        # dict mapping or list attributes depending on checkpoint
        if hasattr(sm, "speakers") and isinstance(sm.speakers, dict) and sm.speakers:
            names = list(sm.speakers.keys())
            if XTTS_DEFAULT in names:
                return XTTS_DEFAULT
            return names[0]
        for attr in ("speaker_ids", "speaker_names"):
            if hasattr(sm, attr):
                lst = getattr(sm, attr)
                if lst:
                    if XTTS_DEFAULT in lst:
                        return XTTS_DEFAULT
                    return lst[0]
    except Exception:
        pass
    # Fall back to generic labels commonly accepted by xtts builds
    return XTTS_DEFAULT


def write_wav(bytes_or_array, samplerate: int, out_path: Path) -> None:
    """Write WAV from NumPy array or raw bytes."""
    if isinstance(bytes_or_array, (bytes, bytearray)):
        out_path.write_bytes(bytes_or_array)
        return
    # Assume ndarray float32
    sf.write(str(out_path), bytes_or_array, samplerate, format="WAV")


# -----------------------------
# Main
# -----------------------------

def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render speech audio using Coqui TTS (self-contained).")
    parser.add_argument("--text", required=True, help="Input text to synthesise.")
    parser.add_argument("--out", required=True, help="Destination WAV filepath.")
    parser.add_argument("--lang", "--language", dest="lang", default="en", choices=["en", "fr", "es"], help="Language (default: en).")
    parser.add_argument("--model", dest="model", default=None, help=(
        "Override model id (e.g., 'tts_models/fr/mai/vits'). "
        "If omitted, a default is chosen from MODEL_BY_LANG."
    ),)
    parser.add_argument("--speaker", help="Optional speaker override.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    log = logging.getLogger("tts_helper")

    try:
        from TTS.api import TTS as CoquiTTS  # type: ignore
    except Exception as exc:
        log.error("Failed to import Coqui TTS", exc_info=exc)
        return 2

    text = normalise_text(args.text)
    if args.lang in ("es", "fr"):
        text = verbalise_numbers(text, args.lang)
    # model_id = MODEL_BY_LANG.get(args.lang, MODEL_BY_LANG["en"])  # default to EN VITS
    model_id = args.model or MODEL_BY_LANG.get(args.lang, MODEL_BY_LANG["en"])

    # Load model
    t0 = time.time()
    try:
        tts = CoquiTTS(model_name=model_id, progress_bar=False, gpu=False)
    except Exception:
        log.exception("Failed to load TTS model: %s", model_id)
        return 3

    # Build kwargs depending on model capabilities
    kwargs: Dict[str, Any] = {}

    # Language only for multilingual models (xtts)
    if getattr(tts, "is_multi_lingual", False):
        kwargs["language"] = args.lang

    # Speaker selection
    speaker = args.speaker
    if not speaker:
        if getattr(tts, "is_multi_speaker", False):
            if "xtts" in model_id.lower():
                speaker = pick_xtts_speaker(tts)
            else:
                # VITS EN (VCTK)
                try:
                    spks = getattr(tts, "speakers", None)
                    if spks and VCTK_DEFAULT in spks:
                        speaker = VCTK_DEFAULT
                    elif spks:
                        speaker = spks[0]
                except Exception:
                    speaker = VCTK_DEFAULT
    if speaker:
        kwargs["speaker"] = speaker

    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Directly synthesize to file for stability
        tts.tts_to_file(text=text, file_path=str(out_path), **kwargs)
    except Exception:
        log.exception("Coqui synthesis failed")
        return 3

    # Optional: log simple metrics (duration via reading the file header)
    try:
        import soundfile as _sf
        data, sr = _sf.read(str(out_path))
        duration = len(data) / float(sr or 1)
    except Exception:
        duration = 0.0
    elapsed = time.time() - t0

    log.info(
        "Wrote speech audio",
        extra={
            "path": str(out_path),
            "model": model_id,
            "speaker": speaker or "",
            "language": kwargs.get("language", ""),
            "duration": duration,
            "elapsed": elapsed,
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
