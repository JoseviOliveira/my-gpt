"""
routes/chat_routes.py — Chat and streaming endpoints
"""

import json
import time
import uuid
import datetime
import threading
from typing import Any

from flask import Blueprint, jsonify, request, Response, current_app, g
import requests

from src.core.config import (
    OLLAMA_URL,
    MODEL,
    resolve_model_choice,
    is_guest_user,
    is_admin_user,
    NON_ADMIN_ALLOWED_MODES,
    NON_ADMIN_MODEL_ALLOWLIST,
    NON_ADMIN_MODEL_DEFAULTS,
    NON_ADMIN_DAILY_PROMPT_LIMIT,
    NON_ADMIN_CHAT_PROMPT_LIMIT,
)
from src.services.metadata import (
    ensure_latest_user_language,
    detect_lang_for_request,
    language_guard_message,
    detect_language_for_text,
    notify_stream_start,
    notify_stream_end,
)
from src.services.gpu import read_gpu_utilization
from src.services.temperature import read_server_temperature
from src.services.ollama import (
    strip_think_markers,
    sanitize_ollama_payload,
    extract_ollama_visible_text,
    extract_ollama_reasoning_text,
    log_llm_call,
    get_system_prompt,
    merge_options,
    apply_model_option_defaults,
)

chat_bp = Blueprint("chat", __name__)

# Metrics storage for streaming requests
METRICS: dict[str, dict] = {}
STREAM_CANCEL: dict[str, threading.Event] = {}
_NON_ADMIN_DAILY_COUNT: dict[str, dict[str, int | str]] = {}
_NON_ADMIN_LOCK = threading.Lock()


def _count_user_prompts(messages: Any) -> int:
    if not isinstance(messages, list):
        return 0
    return sum(1 for msg in messages if (msg.get("role") or "").lower() == "user")


def _consume_daily_prompt_budget(username: str) -> tuple[bool, int]:
    if NON_ADMIN_DAILY_PROMPT_LIMIT <= 0:
        return True, -1
    today = datetime.date.today().isoformat()
    with _NON_ADMIN_LOCK:
        entry = _NON_ADMIN_DAILY_COUNT.get(username)
        if not entry or entry.get("date") != today:
            entry = {"date": today, "count": 0}
            _NON_ADMIN_DAILY_COUNT[username] = entry
        count = int(entry.get("count", 0))
        if count >= NON_ADMIN_DAILY_PROMPT_LIMIT:
            return False, 0
        entry["count"] = count + 1
        remaining = NON_ADMIN_DAILY_PROMPT_LIMIT - entry["count"]
        return True, remaining


def _enforce_non_admin_limits(data: dict, username: str) -> tuple[Response | None, int]:
    if is_admin_user(username) or is_guest_user(username):
        return None, 200
    mode = (data.get("mode") or "fast").lower()
    if NON_ADMIN_ALLOWED_MODES and mode not in NON_ADMIN_ALLOWED_MODES:
        return jsonify({
            "error": "mode_not_allowed",
            "detail": "Mode not available for this account.",
        }), 403
    requested_model = (data.get("model") or "").strip()
    if not requested_model and NON_ADMIN_MODEL_ALLOWLIST:
        default_for_mode = NON_ADMIN_MODEL_DEFAULTS.get(mode)
        if default_for_mode and default_for_mode in NON_ADMIN_MODEL_ALLOWLIST:
            data["model"] = default_for_mode
        else:
            data["model"] = NON_ADMIN_MODEL_ALLOWLIST[0]
        requested_model = data["model"]
    if requested_model and NON_ADMIN_MODEL_ALLOWLIST and requested_model not in NON_ADMIN_MODEL_ALLOWLIST:
        return jsonify({
            "error": "model_not_allowed",
            "detail": "Model not available for this account.",
        }), 403
    prompt_count = _count_user_prompts(data.get("messages"))
    if NON_ADMIN_CHAT_PROMPT_LIMIT > 0 and prompt_count > NON_ADMIN_CHAT_PROMPT_LIMIT:
        return jsonify({
            "error": "chat_prompt_limit",
            "detail": "Chat prompt limit reached.",
        }), 429
    ok, _ = _consume_daily_prompt_budget(username)
    if not ok:
        return jsonify({
            "error": "daily_prompt_limit",
            "detail": "Daily prompt limit reached.",
        }), 429
    return None, 200


def _ns_to_seconds(nanoseconds):
    """Convert nanosecond measurements to seconds."""
    try:
        return float(nanoseconds) / 1e9
    except Exception:
        return None


def _estimate_missing_tokens(gen_count, gen_seconds, clean_text, started, finished):
    """Estimate token count and timing when Ollama metadata is missing."""
    if gen_count is None or gen_count == 0:
        if clean_text:
            gen_count = max(1, int(len(clean_text) / 4))
            if not gen_seconds or gen_seconds <= 0:
                gen_seconds = (finished - started).total_seconds()
    
    tps = None
    if gen_count and gen_seconds and gen_seconds > 0:
        tps = gen_count / gen_seconds
    
    return gen_count, gen_seconds, tps


def _build_metrics_dict(req_id, mode, model, started, finished, meta, clean_text):
    """Build metrics dictionary from streaming response metadata."""
    total_s = _ns_to_seconds(meta.get("total_duration"))
    load_s = _ns_to_seconds(meta.get("load_duration"))
    pe_s = _ns_to_seconds(meta.get("prompt_eval_duration"))
    gen_s = _ns_to_seconds(meta.get("eval_duration"))
    pe_cnt = meta.get("prompt_eval_count")
    gen_cnt = meta.get("eval_count")
    
    gen_cnt, gen_s, tps = _estimate_missing_tokens(gen_cnt, gen_s, clean_text, started, finished)
    
    return {
        "id": req_id,
        "mode": mode,
        "model": model,
        "started": started.isoformat(),
        "finished": finished.isoformat(),
        "elapsed_s": (finished - started).total_seconds(),
        "ollama": {
            "total_s": total_s,
            "load_s": load_s,
            "prompt_tokens": pe_cnt,
            "prompt_s": pe_s,
            "output_tokens": gen_cnt,
            "output_s": gen_s,
            "tokens_per_s": tps,
        },
        "chars": len(clean_text),
    }


def _ollama_error_response(err: Exception):
    """Build a standardized error response for Ollama failures."""
    current_app.logger.exception("Ollama request failed")
    return jsonify({"error": "ollama_unavailable", "detail": str(err)}), 503


@chat_bp.post("/api/chat")
def chat():
    """Proxy a non-streaming chat completion request to Ollama."""
    if is_guest_user(getattr(g, "current_user", None)):
        return jsonify({"error": "guest_read_only"}), 403
    is_benchmark = (request.headers.get("X-Benchmark") or "").strip() == "1"
    data = request.get_json(force=True)
    current_user = getattr(g, "current_user", None) or "guest"
    blocked, status = _enforce_non_admin_limits(data, current_user)
    if blocked:
        return blocked, status
    # Benchmark runs must honor the exact model provided by runner config,
    # even when it is not present in the UI allowlist.
    if is_benchmark:
        selected_model = (data.get("model") or "").strip() or MODEL
    else:
        selected_model = resolve_model_choice(data)
    messages_in = data.get("messages", [])
    if not is_benchmark:
        ensure_latest_user_language(messages_in)
    opts, mode = merge_options(data)
    opts = apply_model_option_defaults(selected_model, opts)

    if is_benchmark:
        messages = messages_in
    else:
        lang_code = detect_lang_for_request(messages_in)
        sys_text = get_system_prompt(mode, lang_code)
        mode_msg = {"role": "system", "content": sys_text}
        guard_msg = language_guard_message(messages_in)
        if guard_msg:
            messages = [mode_msg, guard_msg] + messages_in
        else:
            messages = [mode_msg] + messages_in

    try:
        t0 = time.perf_counter()
        r = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": selected_model,
                "messages": messages,
                "stream": False,
                "options": opts,
                "keep_alive": "10m",
            },
            timeout=180,
        )
        r.raise_for_status()
    except requests.RequestException as err:
        return _ollama_error_response(err)
    raw_payload = r.json()
    payload = sanitize_ollama_payload(raw_payload)
    resp_text = extract_ollama_visible_text(payload)
    if not (resp_text or "").strip():
        raw_text = extract_ollama_visible_text(raw_payload)
        if raw_text.strip():
            resp_text = raw_text
            if isinstance(payload.get("message"), dict):
                payload["message"]["content"] = raw_text
            elif "response" in payload:
                payload["response"] = raw_text
    if not (resp_text or "").strip():
        reasoning_text = extract_ollama_reasoning_text(raw_payload)
        if reasoning_text.strip():
            resp_text = reasoning_text
            if isinstance(payload.get("message"), dict):
                payload["message"]["content"] = reasoning_text
            elif "response" in payload:
                payload["response"] = reasoning_text
    log_llm_call("chat", selected_model, messages, resp_text, time.perf_counter() - t0)
    return jsonify(payload)


@chat_bp.post("/api/stream")
def stream():
    """Stream chat completions from Ollama while capturing simple metrics."""
    if is_guest_user(getattr(g, "current_user", None)):
        return jsonify({"error": "guest_read_only"}), 403
    is_benchmark = (request.headers.get("X-Benchmark") or "").strip() == "1"
    data = request.get_json(force=True)
    current_user = getattr(g, "current_user", None) or "guest"
    blocked, status = _enforce_non_admin_limits(data, current_user)
    if blocked:
        return blocked, status
    # Benchmark runs must honor the exact model provided by runner config,
    # even when it is not present in the UI allowlist.
    if is_benchmark:
        selected_model = (data.get("model") or "").strip() or MODEL
    else:
        selected_model = resolve_model_choice(data)
    messages_in = data.get("messages", [])
    if not is_benchmark:
        ensure_latest_user_language(messages_in)
    req_id = data.get("id") or uuid.uuid4().hex
    opts, mode = merge_options(data)
    opts = apply_model_option_defaults(selected_model, opts)

    started = datetime.datetime.now(datetime.UTC)

    if is_benchmark:
        messages = messages_in
    else:
        lang_code = detect_lang_for_request(messages_in)
        sys_text = get_system_prompt(mode, lang_code)
        mode_msg = {"role": "system", "content": sys_text}
        guard_msg = language_guard_message(messages_in)
        if guard_msg:
            messages = [mode_msg, guard_msg] + messages_in
        else:
            messages = [mode_msg] + messages_in

    log_obj = current_app.logger
    notify_stream_start()
    cancel_event = threading.Event()
    STREAM_CANCEL[req_id] = cancel_event
    try:
        upstream = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": selected_model,
                "messages": messages,
                "stream": True,
                "options": opts,
                "keep_alive": "10m",
            },
            stream=True,
            timeout=600,
        )
        upstream.raise_for_status()
    except requests.RequestException as err:
        STREAM_CANCEL.pop(req_id, None)
        notify_stream_end()
        return _ollama_error_response(err)

    def gen():
        """Yield response chunks from Ollama and gather completion metadata."""
        raw_accum = ""
        clean_accum = ""
        reasoning_accum = ""
        meta = {}
        try:
            try:
                for line in upstream.iter_lines(decode_unicode=True):
                    if cancel_event.is_set():
                        return
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue

                    if isinstance(obj, dict):
                        txt = extract_ollama_visible_text(obj)
                        if txt:
                            raw_accum += txt
                            clean_total = strip_think_markers(raw_accum)
                            if len(clean_total) > len(clean_accum):
                                delta = clean_total[len(clean_accum):]
                                clean_accum = clean_total
                                if delta:
                                    yield delta

                        reasoning_txt = extract_ollama_reasoning_text(obj)
                        if reasoning_txt:
                            reasoning_accum += reasoning_txt

                        if obj.get("done"):
                            if not clean_accum and raw_accum:
                                # Preserve non-empty raw output when marker
                                # stripping would otherwise emit no chunks.
                                fallback = strip_think_markers(raw_accum).strip()
                                if not fallback:
                                    fallback = raw_accum.strip()
                                if fallback:
                                    clean_accum = fallback
                                    yield fallback
                            if not clean_accum and reasoning_accum:
                                fallback = strip_think_markers(reasoning_accum).strip()
                                if not fallback:
                                    fallback = reasoning_accum.strip()
                                if fallback:
                                    clean_accum = fallback
                                    yield fallback
                            meta = obj
                            break
            finally:
                upstream.close()

            if cancel_event.is_set():
                return

            finished = datetime.datetime.now(datetime.UTC)
            
            METRICS[req_id] = _build_metrics_dict(
                req_id, mode, selected_model, started, finished, meta, clean_accum
            )
            
            log_llm_call(
                "stream",
                selected_model,
                messages,
                clean_accum,
                (finished - started).total_seconds(),
                logger_obj=log_obj,
                request_id=req_id,
                benchmark=is_benchmark,
            )
        finally:
            STREAM_CANCEL.pop(req_id, None)
            notify_stream_end()

    return Response(
        gen(),
        mimetype="text/plain; charset=utf-8",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
        },
    )


@chat_bp.get("/api/metrics")
def metrics():
    """Expose captured streaming metrics for a given request id."""
    rid = request.args.get("id")
    if not rid:
        return jsonify({"error": "missing id"}), 400
    m = METRICS.get(rid)
    if not m:
        return jsonify({"error": "not_found"}), 404
    return jsonify(m)


@chat_bp.post("/api/stop")
def stop_stream():
    """Stop a streaming response early."""
    data = request.get_json(force=True) or {}
    rid = data.get("id")
    if not rid:
        return jsonify({"error": "missing id"}), 400
    event = STREAM_CANCEL.get(rid)
    if event:
        event.set()
    return jsonify({"stopped": bool(event)})


@chat_bp.get("/api/gpu")
def gpu_utilization():
    """Expose GPU utilization metrics when available."""
    util, source = read_gpu_utilization()
    if util is None:
        return jsonify({"available": False, "utilization": None, "source": source})
    return jsonify({"available": True, "utilization": util, "source": source})


@chat_bp.get("/api/temperature")
def server_temperature():
    """Expose server temperature metrics when available."""
    temp, source, kind, unit, label = read_server_temperature()
    if temp is None:
        return jsonify(
            {
                "available": False,
                "temperature": None,
                "source": source,
                "unit": unit or "C",
                "kind": kind or "temperature",
                "label": label,
            }
        )
    return jsonify(
        {
            "available": True,
            "temperature": temp,
            "source": source,
            "unit": unit or "C",
            "kind": kind or "temperature",
            "label": label,
        }
    )


@chat_bp.post("/api/detect-language")
def detect_language_api():
    """Detect the language for the supplied text (used by instant TTS playback)."""
    if is_guest_user(getattr(g, "current_user", None)):
        return jsonify({"error": "guest_read_only"}), 403
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "missing text"}), 400
    snippet = text[:2000]
    try:
        lang = detect_language_for_text(snippet)
    except Exception as exc:
        current_app.logger.warning("[LANG] detection endpoint failure", exc_info=True)
        return jsonify({"error": "detect_failed"}), 500
    return jsonify({"lang": (lang or "").strip().lower()})
