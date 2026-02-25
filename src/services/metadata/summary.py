"""
services/metadata/summary.py — AI-driven title and summary generation
"""

import json
import logging
import math
import os
import time

import requests
from flask import current_app

from src.core.config import OLLAMA_URL, SUMMARY_MODEL
from .utils import strip_think_markers, log_llm_call, extract_json_block, conversation_to_text
from .language import detect_lang_from_messages

# Constants
_PROMPT_PREVIEW_MAX_LENGTH = 1000

# Output constraints
SUMMARY_MAX_CHARS = int(os.getenv("SUMMARY_MAX_CHARS", "8000"))
_summary_words_env = os.getenv("SUMMARY_MAX_WORDS")
if _summary_words_env:
    SUMMARY_MAX_WORDS = max(5, int(_summary_words_env))
else:
    _legacy_chars = os.getenv("SUMMARY_CARD_MAX_CHARS")
    default_words = math.ceil(max(20, int(_legacy_chars or "200")) / 5)
    SUMMARY_MAX_WORDS = max(5, default_words)

# Context window
METADATA_CONTEXT_WINDOW = int(os.getenv("METADATA_CONTEXT_WINDOW", "4"))


def metadata_context(session: dict) -> list:
    """Extract the last N exchanges for metadata generation."""
    messages = session.get("messages") or []
    trimmed = []
    for msg in reversed(messages):
        role = (msg.get("role") or "").lower()
        if role not in {"user", "assistant"}:
            continue
        text = (msg.get("content") or "").strip()
        if not text:
            continue
        trimmed.append({"role": role, "content": text})
        if len(trimmed) >= METADATA_CONTEXT_WINDOW:
            break
    trimmed.reverse()
    return trimmed or messages


def generate_metadata(messages: list, previous_summary: str | None = None) -> tuple[str | None, str | None]:
    """Generate title and summary for a conversation using LLM."""
    transcript = conversation_to_text(messages, SUMMARY_MAX_CHARS)
    prev_summary = (previous_summary or "").strip()
    if not transcript and not prev_summary:
        return None, None
    user_lang = detect_lang_from_messages(messages) or "en"

    sections: list[str] = []
    if prev_summary:
        sections.append("Prev summary:\n" + prev_summary)
    if transcript:
        sections.append("Latest exchange:\n" + transcript)
    sections.append(
        f"Respond with JSON: title (≤4 words, Title Case, no trailing punctuation) and summary (≤{SUMMARY_MAX_WORDS} words). "
        f"Both fields must use lang={user_lang}, be impersonal and give a high-level description using Prev summary and Latest exchange contents. No references to user/assistant."
    )
    user_prompt = "\n\n".join(sections)
    logger = current_app.logger
    logger.debug(
        "[metadata] summary prompt lang=%s transcript_len=%d body=%s",
        user_lang,
        len(transcript),
        user_prompt.replace("\n", "\\n"),
    )
    payload = {
        "model": SUMMARY_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You condense chat conversations into concise metadata. "
                    f"Always respond in language code '{user_lang}'. "
                    "Output must be valid JSON with keys \"title\" and \"summary\" only."
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.2, "top_p": 0.9},
        "keep_alive": "10m",
    }
    try:
        t0 = time.perf_counter()
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=180)
        r.raise_for_status()
        data = r.json()
        text = ""
        if isinstance(data, dict):
            if isinstance(data.get("message"), dict):
                text = data["message"].get("content") or ""
            else:
                text = data.get("response") or ""
        text = strip_think_markers(text or "")
        log_llm_call(
            "metadata",
            SUMMARY_MODEL,
            [
                {
                    "role": "summary_input",
                    "lang": user_lang,
                    "has_prev": bool(prev_summary),
                    "len_prev": len(prev_summary),
                    "len_transcript": len(transcript),
                }
            ],
            text,
            time.perf_counter() - t0,
        )
        raw_words = len(text.split())
        raw_body = text.replace("\n", "\\n")
        text = extract_json_block(text)
        if not text:
            return None, None
        meta = json.loads(text)
        title = (meta.get("title") or "").strip()
        summary = (meta.get("summary") or "").strip()
        summary_words = len(summary.split()) if summary else 0
        max_overflow = max(1, int(round(SUMMARY_MAX_WORDS * 1.25)))
        logger.debug(
            "[metadata] summary raw response model=%s summary_words=%d max_words=%d max_overflow=%d raw_words=%d body=%s",
            SUMMARY_MODEL,
            summary_words,
            SUMMARY_MAX_WORDS,
            max_overflow,
            raw_words,
            raw_body,
        )
        if title:
            words = title.split()
            if len(words) > 4:
                title = " ".join(words[:4])
        elif summary:
            words = summary.split()
            title = " ".join(words[:4]).rstrip(",.") if words else ""
        if not title and transcript:
            first_line = transcript.split("\n", 1)[0]
            words = first_line.split()
            title = " ".join(words[:4])
        if summary:
            summary = summary.replace("\n", " ").strip()
            words = summary.split()
            max_overflow = max(1, int(round(SUMMARY_MAX_WORDS * 1.25)))
            if len(words) > max_overflow:
                summary = " ".join(words[:SUMMARY_MAX_WORDS]).rstrip()
        return title or None, summary or None
    except requests.RequestException as exc:
        current_app.logger.warning("Metadata LLM request failed: %s", exc)
        return None, None
    except (json.JSONDecodeError, KeyError) as exc:
        current_app.logger.warning("Metadata invalid response: %s", exc)
        return None, None
    except Exception:
        current_app.logger.exception("Metadata generation unexpected error")
        raise
