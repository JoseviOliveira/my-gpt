"""
api/analytics_routes.py — Analytics action logging endpoint
"""

import re

from flask import Blueprint, jsonify, request, g

from src.core.analytics import log_analytics_event
from src.services.geoip import resolve_country

analytics_bp = Blueprint("analytics", __name__)


def _client_ip() -> str:
    """Get the client IP from headers or remote address."""
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if forwarded:
        return forwarded
    return request.remote_addr or ""


def _analytics_username() -> str:
    """Get the username for analytics logging."""
    from src.core.config import ANONYMOUS_ANALYTICS_USER
    user = getattr(g, "current_user", None)
    if user:
        return user
    return ANONYMOUS_ANALYTICS_USER


@analytics_bp.post("/api/analytics/action")
def analytics_log_action():
    """Record a client-side user action with custom labels."""
    payload = request.get_json(silent=True) or {}
    group = (payload.get("group") or payload.get("group_label") or "").strip() or "App"
    subgroup = (payload.get("action") or payload.get("subgroup") or payload.get("subgroup_label") or "").strip() or "Action"
    detail = (payload.get("detail") or "").strip()
    client_ip = _client_ip()
    slug = ""
    if detail:
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", detail).strip("-")
        if safe:
            slug = safe[:80]
    path = "/api/analytics/action"
    if slug:
        path = f"{path}/{slug}"
    log_analytics_event(
        _analytics_username(),
        "POST",
        path,
        client_ip,
        request.headers.get("User-Agent", ""),
        resolve_country(client_ip),
        group,
        subgroup,
    )
    return jsonify({"ok": True})
