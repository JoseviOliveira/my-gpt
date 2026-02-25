"""
services/metadata/utils.py — Internal utilities for metadata services
"""

import re

from flask import current_app


def strip_think_markers(text: str) -> str:
    """Remove <think></think> blocks and stray markers."""
    if not text:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"</?think>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def log_llm_call(purpose: str, model: str, messages: list, response: str, elapsed: float):
    """Log LLM call for debugging."""
    try:
        logger = current_app.logger
    except RuntimeError:
        return
    logger.info(
        "[LLM] %s model=%s msgs=%d resp_len=%d elapsed=%.2fs",
        purpose,
        model,
        len(messages) if messages else 0,
        len(response) if response else 0,
        elapsed,
    )


def extract_json_block(text: str) -> str:
    """Extract a JSON object from text potentially wrapped in markdown fences."""
    if not text:
        return ""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        fence = text.splitlines()
        text = "\n".join(line for line in fence if not line.strip().startswith("```"))
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def conversation_to_text(messages: list, max_chars: int) -> str:
    """Convert message list to a human-readable transcript."""
    parts = []
    for m in messages:
        content = (m.get("content") or "").strip()
        if not content:
            continue
        role = m.get("role") or ""
        label = "User" if role == "user" else "Assistant" if role == "assistant" else role.title() or "Other"
        parts.append(f"{label}: {content}")
    if not parts:
        return ""
    transcript = "\n".join(parts)
    if len(transcript) > max_chars:
        transcript = transcript[-max_chars:]
    return transcript
