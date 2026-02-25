"""
services/ollama.py — Ollama API client and streaming helpers
"""
import os
import re
import json
import time
import logging
import requests
from typing import Any
from flask import jsonify, current_app, has_app_context

from src.core.config import OLLAMA_URL, MODEL, SUMMARY_MODEL

# Constants
FAST_SENTENCE_LIMIT = int(os.getenv("FAST_SENTENCE_LIMIT", "2"))
NORMAL_SENTENCE_LIMIT = int(os.getenv("NORMAL_SENTENCE_LIMIT", "8"))
_ERROR_DETAIL_MAX_LENGTH = 400

BASE_MODE_PROMPT = """You must respond ONLY with the final answer. CRITICAL RULES:
- NO internal reasoning, thinking, or chain-of-thought
- NO <think> tags or similar markers
- NO explanations of your reasoning process
- NO step-by-step breakdowns unless explicitly requested
- Provide ONLY the direct answer to the question
- Be concise and factual"""

MODE_PROMPTS = {
    "en": {
        "fast": f"Answer in ≤{FAST_SENTENCE_LIMIT} plain sentences.",
        "normal": f"Reply plainly in ≤{NORMAL_SENTENCE_LIMIT} sentences; no tables.",
        "deep": "Explain thoroughly with brief lists when useful.",
    },
    "es": {
        "fast": f"Responde en ≤{FAST_SENTENCE_LIMIT} frases en texto plano.",
        "normal": f"Responde en texto plano y ≤{NORMAL_SENTENCE_LIMIT} frases; sin tablas.",
        "deep": "Explica con detalle y listas breves cuando ayuden.",
    },
    "fr": {
        "fast": f"Réponds en ≤{FAST_SENTENCE_LIMIT} phrases simples.",
        "normal": f"Texte simple en ≤{NORMAL_SENTENCE_LIMIT} phrases; pas de tableaux.",
        "deep": "Explique en détail avec de courtes listes si nécessaire.",
    },
}

# Regex patterns for stripping <think> blocks
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_TRAIL_RE = re.compile(r"<think>.*$", re.IGNORECASE | re.DOTALL)
_THINK_CLOSE_RE = re.compile(r"</think>", re.IGNORECASE)
_THINK_OPTION_MODEL_PREFIXES = (
    "qwen3:",
    "deepseek-r1:",
    "gpt-oss:",
)


def strip_think_markers(text: str) -> str:
    """Remove <think> blocks and stray tags from generated text."""
    if not text:
        return ""
    if "<think" not in text and "</think" not in text:
        return text
    cleaned = _THINK_BLOCK_RE.sub("", text)
    cleaned = _THINK_TRAIL_RE.sub("", cleaned)
    cleaned = _THINK_CLOSE_RE.sub("", cleaned)
    return cleaned


def sanitize_ollama_payload(obj: Any) -> Any:
    """Strip hidden reasoning markers from Ollama responses in-place."""
    if isinstance(obj, dict):
        msg = obj.get("message")
        if isinstance(msg, dict) and "content" in msg:
            msg["content"] = strip_think_markers(msg.get("content") or "")
        if isinstance(obj.get("response"), str):
            obj["response"] = strip_think_markers(obj["response"])
    return obj


def extract_ollama_visible_text(obj: Any) -> str:
    """Extract user-visible text from an Ollama response chunk/payload."""
    if not isinstance(obj, dict):
        return ""
    msg = obj.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            return content
    response = obj.get("response")
    if isinstance(response, str):
        return response
    content = obj.get("content")
    if isinstance(content, str):
        return content
    return ""


def extract_ollama_reasoning_text(obj: Any) -> str:
    """Extract reasoning text fields exposed by some Ollama models."""
    if not isinstance(obj, dict):
        return ""
    msg = obj.get("message")
    if isinstance(msg, dict):
        for key in ("thinking", "reasoning", "reasoning_content"):
            value = msg.get(key)
            if isinstance(value, str) and value:
                return value
    for key in ("thinking", "reasoning", "reasoning_content"):
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def log_llm_call(
    event: str,
    model: str,
    messages: Any,
    response_text: str,
    elapsed: float,
    *,
    logger_obj=None,
    request_id: str | None = None,
    benchmark: bool | None = None,
):
    """Emit a structured log for every Ollama request."""
    try:
        prompt_repr = json.dumps(messages, ensure_ascii=False)
    except Exception:
        prompt_repr = str(messages)
    logger = logger_obj
    if logger is None:
        if has_app_context():
            logger = current_app.logger
        else:
            logger = logging.getLogger("app")
    suffix = ""
    if request_id:
        suffix += f" id={request_id}"
    if benchmark is not None:
        suffix += f" benchmark={int(bool(benchmark))}"
    logger.info(
        "[LLM] %s model=%s elapsed=%.3fs%s prompt=%s response=%s",
        event,
        model,
        elapsed,
        suffix,
        prompt_repr,
        (response_text or "").strip(),
    )


def get_system_prompt(mode: str, lang: str) -> str:
    """Return the localized system prompt for the selected mode."""
    lang_key = (lang or "en").strip().lower()
    if lang_key not in MODE_PROMPTS:
        lang_key = "en"
    prompts = MODE_PROMPTS[lang_key]
    mode_key = (mode or "fast").strip().lower()
    if mode_key not in prompts:
        mode_key = "fast"
    return f"{BASE_MODE_PROMPT} {prompts[mode_key]}"


def ollama_error_response(err: requests.RequestException):
    """Convert upstream Ollama errors into JSON responses."""
    resp = getattr(err, "response", None)
    status = getattr(resp, "status_code", 502) or 502
    try:
        detail = ""
        if resp is not None:
            try:
                data = resp.json()
                detail = data.get("error") or data.get("message") or ""
            except ValueError:
                detail = resp.text or ""
        if not detail:
            detail = str(err)
    except Exception:
        detail = str(err)
    code = "ollama_model_not_found" if status == 404 else "ollama_upstream_error"
    payload = {"error": code, "detail": detail.strip()[:_ERROR_DETAIL_MAX_LENGTH]}
    current_app.logger.warning("[OLLAMA] upstream error status=%s detail=%s", status, payload["detail"])
    safe_status = status if 400 <= status < 600 else 502
    return jsonify(payload), safe_status


def merge_options(data: dict) -> tuple[dict, str]:
    """Extract mode and options payload from the incoming request body."""
    mode = (data.get("mode") or "fast").lower()
    opts = (data.get("options") or {}).copy()
    
    # Note: DeepSeek R1 models have internal thinking mode that cannot be fully disabled
    # via Ollama options. The "think" parameter only controls how thinking is exposed,
    # not whether it occurs. We rely on system prompts to guide model behavior.
    
    return opts, mode


def apply_model_option_defaults(model: str, opts: dict) -> dict:
    """Apply safe per-model defaults for Ollama generation options."""
    effective = (opts or {}).copy()
    model_name = (model or "").strip().lower()

    supports_think_option = any(
        model_name.startswith(prefix) for prefix in _THINK_OPTION_MODEL_PREFIXES
    )
    if supports_think_option:
        effective.setdefault("think", False)
    else:
        # Some models reject unknown options (e.g. magistral:*), so never forward
        # think when unsupported.
        effective.pop("think", None)

    return effective


def detect_language_for_text(text: str) -> str:
    """Detect the language of a text snippet using the LLM."""
    snippet = (text or "").strip()
    if not snippet:
        return "en"
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
        meta_clean = re.sub(r"(?im)^\s*(thinking\.{3}|…done thinking\.|\.{3}done thinking\.|done thinking\.)\s*$", "", response).strip()
        code = meta_clean.lower()
        matches = re.findall(r"\b(en|es|fr)\b", code)
        if matches:
            lang_code = matches[-1]
            if has_app_context():
                current_app.logger.info("[LANG] detection code=%s snippet_len=%d", lang_code, len(snippet))
            return lang_code
        if has_app_context():
            current_app.logger.warning("[LANG] detection missing code raw=%s snippet_len=%d", code or "(empty)", len(snippet))
    except Exception as exc:
        if has_app_context():
            current_app.logger.warning("[LANG] detection failed; defaulting to en", exc_info=exc)
    return "en"
