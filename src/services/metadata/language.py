"""
services/metadata/language.py — Language detection for user messages
"""

import re
import time

import requests
from flask import current_app

from src.core.config import OLLAMA_URL, SUMMARY_MODEL
from .utils import strip_think_markers, log_llm_call

LANG_DETECT_MAX_CHARS = 200

def detect_language_for_text(text: str) -> str:
    """Detect ISO language code for the given text using LLM."""
    snippet = (text or "").strip()
    if not snippet:
        return "en"
    if len(snippet) > LANG_DETECT_MAX_CHARS:
        head = snippet[:LANG_DETECT_MAX_CHARS]
        tail = snippet[-LANG_DETECT_MAX_CHARS:]
        snippet = f"{head}\n...\n{tail}"
    try:
        t0 = time.perf_counter()
        payload = {
            "model": SUMMARY_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "Detect the language of the user's text. Respond only with ISO code (en, es, fr).",
                },
                {"role": "user", "content": snippet},
            ],
            "stream": False,
            "options": {"temperature": 0.0},
            "keep_alive": "1m",
        }
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=45)
        r.raise_for_status()
        data = r.json()
        response = ""
        if isinstance(data, dict):
            if isinstance(data.get("message"), dict):
                response = data["message"].get("content", "")
            else:
                response = data.get("response", "")
        response = strip_think_markers(response or "")
        log_llm_call(
            "lang-detect",
            SUMMARY_MODEL,
            payload["messages"],
            response,
            time.perf_counter() - t0,
        )
        meta_clean = re.sub(
            r"(?im)^\s*(thinking\.{3}|…done thinking\.|\.{3}done thinking\.|done thinking\.)\s*$",
            "",
            response,
        ).strip()
        code = meta_clean.lower()
        matches = re.findall(r"\b(en|es|fr)\b", code)
        if matches:
            lang_code = matches[-1]
            current_app.logger.debug("[LANG] detection code=%s snippet_len=%d", lang_code, len(snippet))
            return lang_code
        current_app.logger.warning("[LANG] detection missing code raw=%s snippet_len=%d", code or "(empty)", len(snippet))
    except Exception as exc:  # pragma: no cover - network
        current_app.logger.warning("[LANG] detection failed; defaulting to en", exc_info=True)
    return "en"


def detect_lang_from_messages(messages: list) -> str:
    """Detect language from the latest user message in a conversation."""
    for msg in reversed(messages or []):
        if (msg.get("role") or "").lower() == "user":
            existing = (msg.get("language_ai") or "").strip()
            if existing:
                return existing
            text = (msg.get("content") or "").strip()
            if text:
                return detect_language_for_text(text)
    return "en"


def detect_lang_for_request(messages: list) -> str:
    """Detect language code for a streaming request (caches on message)."""
    for msg in reversed(messages or []):
        if (msg.get("role") or "").lower() != "user":
            continue
        lang = (msg.get("language_ai") or "").strip()
        if lang:
            return lang[:2].lower()
        text = (msg.get("content") or "").strip()
        if text:
            return detect_language_for_text(text)
    return "en"


def latest_user_message(messages: list) -> dict | None:
    """Return the most recent user message from the list."""
    if not isinstance(messages, list):
        return None
    for msg in reversed(messages):
        if (msg.get("role") or "").lower() == "user":
            return msg
    return None


def ensure_latest_user_language(messages: list):
    """Ensure the latest user message has a cached language_ai field."""
    msg = latest_user_message(messages)
    if not msg:
        return
    lang = (msg.get("language_ai") or "").strip()
    if lang:
        return
    text = (msg.get("content") or "").strip()
    if not text:
        return
    try:
        msg["language_ai"] = detect_language_for_text(text)
    except Exception:
        msg["language_ai"] = "en"


def language_guard_message(messages: list) -> dict | None:
    """Generate a system message instructing the LLM to reply in the detected language."""
    msg = latest_user_message(messages)
    if not msg:
        return None
    lang = (msg.get("language_ai") or "").strip().lower()
    if not lang:
        text = (msg.get("content") or "").strip()
        if text:
            try:
                lang = detect_language_for_text(text)
                msg["language_ai"] = lang
            except Exception:
                lang = "en"
        else:
            lang = "en"
    return {
        "role": "system",
        "content": (
            f"The latest user message is in language code '{lang}'. "
            "Reply exclusively in that language, regardless of earlier conversation."
        ),
    }
