"""
api/session_routes.py — Session CRUD API routes
"""

from flask import Blueprint, jsonify, request, g

from src.core.config import is_guest_user, is_admin_user, NON_ADMIN_CHAT_LIMIT
from src.services.session import (
    load_session,
    write_session,
    list_sessions,
    create_session,
    delete_session,
)
from src.services.metadata import (
    detect_language_for_text,
    enqueue_metadata_job,
)

session_bp = Blueprint("session", __name__)


def _current_username() -> str:
    """Get the current authenticated username from Flask g."""
    return getattr(g, "current_user", None) or "guest"


def _utcnow_iso() -> str:
    """Return current UTC time as ISO string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@session_bp.get("/api/sessions")
def sessions_list():
    """Return the list of available chat sessions for the UI sidebar."""
    return jsonify({"sessions": list_sessions(_current_username())})


@session_bp.get("/api/session/<sid>")
def session_get(sid):
    """Retrieve a full chat session by its identifier."""
    s = load_session(sid, user=_current_username())
    if not s:
        return jsonify({"error": "not_found"}), 404
    s.setdefault("title", None)
    s.setdefault("title_ai", None)
    s.setdefault("summary_ai", None)
    s.setdefault("pinned", False)
    return jsonify(s)


@session_bp.post("/api/session")
def session_new():
    """Create a new empty chat session file and return its id."""
    current_user = _current_username()
    if is_guest_user(current_user):
        return jsonify({"error": "guest_read_only"}), 403
    if (not is_admin_user(current_user)
            and NON_ADMIN_CHAT_LIMIT > 0
            and len(list_sessions(current_user)) >= NON_ADMIN_CHAT_LIMIT):
        return jsonify({"error": "chat_count_limit", "detail": "Chat limit reached."}), 429
    obj = create_session(user=current_user)
    return jsonify({"id": obj["id"]})


@session_bp.post("/api/save")
def session_save():
    """Persist chat messages (and optional title) for a session."""
    data = request.get_json(force=True)
    sid = data.get("id")
    messages = data.get("messages")
    title = data.get("title")
    pinned = data.get("pinned")
    if not sid:
        return jsonify({"error": "missing id"}), 400
    current_user = _current_username()
    if is_guest_user(current_user):
        return jsonify({"error": "guest_read_only"}), 403
    s = load_session(sid, user=current_user) or {
        "id": sid,
        "owner": current_user,
        "title": None,
        "title_ai": None,
        "summary_ai": None,
        "messages": [],
    }
    last_assistant_lang = None
    if isinstance(messages, list):
        last_lang = None
        for msg in messages:
            role = (msg.get("role") or "").lower()
            text = (msg.get("content") or "").strip()
            lang = (msg.get("language_ai") or "").strip().lower()
            if role in {"user", "assistant"} and text:
                if not lang:
                    try:
                        lang = detect_language_for_text(text)
                    except Exception:
                        lang = ""
                lang = (lang or "").strip().lower()
            if not lang:
                lang = last_lang or "en"
            msg["language_ai"] = lang
            if role == "assistant":
                last_assistant_lang = lang
            last_lang = lang
        s["messages"] = messages
    else:
        messages = s.get("messages", [])
    if title is not None:
        trimmed = title.strip()
        s["title"] = trimmed or None
    if pinned is not None:
        s["pinned"] = bool(pinned)
    s["updated_at"] = _utcnow_iso()
    write_session(s, user=current_user)
    if isinstance(data.get("messages"), list) and messages:
        enqueue_metadata_job(current_user, sid)
    return jsonify({"ok": True, "last_assistant_lang": last_assistant_lang})


@session_bp.delete("/api/session/<sid>")
def session_delete_route(sid):
    """Remove a saved chat session from disk if present."""
    current_user = _current_username()
    if is_guest_user(current_user):
        return jsonify({"error": "guest_read_only"}), 403
    delete_session(sid, current_user)
    return jsonify({"ok": True})
