"""
api/static_routes.py — Static file serving routes
"""

from pathlib import Path

from flask import Blueprint, send_from_directory, jsonify, request, g
import requests

from src.core.config import (
    OLLAMA_URL,
    MODEL,
    STT_MODE,
    TTS_MODE,
    ECO_MODE,
    _STT_ALLOWED,
    _TTS_ALLOWED,
    GUEST_USER,
    is_guest_user,
    is_admin_user,
    NON_ADMIN_ALLOWED_MODES,
    NON_ADMIN_MODEL_ALLOWLIST,
    NON_ADMIN_MODEL_DEFAULTS,
    NON_ADMIN_DAILY_PROMPT_LIMIT,
    NON_ADMIN_CHAT_PROMPT_LIMIT,
    NON_ADMIN_CHAT_LIMIT,
)

static_bp = Blueprint("static", __name__)
HANDWRITE_DOC_PATH = Path(__file__).resolve().parents[2] / "static" / "docs" / "handwrite.html"


@static_bp.get("/")
def index():
    """Serve the single-page web application entry point."""
    return send_from_directory("static", "index.html")


@static_bp.get("/favicon.ico")
def favicon():
    """Serve the current favicon asset for browsers that request /favicon.ico."""
    return send_from_directory("static/icons", "icon-32.png")


@static_bp.get("/apple-touch-icon.png")
def apple_touch_icon():
    """Serve iOS home-screen icon using the app icon set."""
    return send_from_directory("static/icons", "icon-180.png")


@static_bp.get("/apple-touch-icon-precomposed.png")
def apple_touch_icon_precomposed():
    """Serve legacy iOS precomposed touch icon requests."""
    return send_from_directory("static/icons", "icon-180.png")


@static_bp.get("/health")
def health():
    """Probe the backing Ollama server and report availability."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        ok = r.status_code == 200
    except Exception:
        ok = False
    return {"ok": ok, "model": MODEL}


@static_bp.get("/config")
def config_endpoint():
    """Expose minimal runtime configuration to the frontend."""
    current_user = getattr(g, "current_user", None)
    guest_mode = is_guest_user(current_user)
    is_admin = is_admin_user(current_user)
    stt_modes = sorted(_STT_ALLOWED)
    tts_modes = sorted(_TTS_ALLOWED)
    stt_mode = STT_MODE
    tts_mode = TTS_MODE
    if guest_mode:
        stt_modes = ["browser"]
        tts_modes = ["browser"]
        stt_mode = "browser"
        tts_mode = "browser"
    payload = {
        "stt_mode": stt_mode,
        "stt_modes": stt_modes,
        "tts_mode": tts_mode,
        "tts_modes": tts_modes,
        "eco_mode": ECO_MODE,
        "guest_mode": guest_mode,
        "guest_user": GUEST_USER,
        "debug_allowed": not guest_mode,
        "is_admin": is_admin,
    }
    if not guest_mode and not is_admin:
        payload.update({
            "allowed_modes": sorted(NON_ADMIN_ALLOWED_MODES),
            "allowed_models": NON_ADMIN_MODEL_ALLOWLIST,
            "model_defaults": NON_ADMIN_MODEL_DEFAULTS,
            "daily_prompt_limit": NON_ADMIN_DAILY_PROMPT_LIMIT,
            "chat_prompt_limit": NON_ADMIN_CHAT_PROMPT_LIMIT,
            "chat_limit": NON_ADMIN_CHAT_LIMIT,
        })
    return jsonify(payload)


@static_bp.get("/docs/")
def docs_index():
    """Serve the documentation entry page."""
    return send_from_directory("static/docs", "index.html")


@static_bp.get("/api/docs/handwrite")
def handwrite_doc_get():
    """Return the current handwrite HTML document for the editor UI."""
    current_user = getattr(g, "current_user", None)
    if not is_admin_user(current_user):
        return jsonify({"error": "admin_required"}), 403
    try:
        html = HANDWRITE_DOC_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"ok": True, "html": html})


@static_bp.post("/api/docs/handwrite")
def handwrite_doc_save():
    """Persist a full handwrite HTML document sent by the editor UI."""
    current_user = getattr(g, "current_user", None)
    if not is_admin_user(current_user):
        return jsonify({"error": "admin_required"}), 403
    data = request.get_json(force=True, silent=True) or {}
    html = data.get("html")
    if not isinstance(html, str) or not html.strip():
        return jsonify({"error": "missing_html"}), 400
    if len(html) > 2_000_000:
        return jsonify({"error": "payload_too_large"}), 413
    HANDWRITE_DOC_PATH.write_text(html, encoding="utf-8")
    return jsonify({"ok": True, "bytes": len(html)})
