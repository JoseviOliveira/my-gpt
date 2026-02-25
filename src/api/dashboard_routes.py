"""
api/dashboard_routes.py — Analytics dashboard routes
"""

from flask import Blueprint, send_from_directory, jsonify, request, current_app, Response

from src.core.config import ADMIN_USERS
from src.core.analytics import get_analytics_summary

dashboard_bp = Blueprint("dashboard", __name__)


def _current_username() -> str:
    """Get the current authenticated username from Flask g."""
    from flask import g
    return getattr(g, "current_user", None) or "guest"


def _require_admin_access():
    """Check if the current user has admin access."""
    user = _current_username()
    if user not in ADMIN_USERS:
        current_app.logger.warning(
            "[analytics] forbidden admin access user=%s path=%s",
            user,
            request.path,
        )
        return Response("Forbidden", 403)
    return None


@dashboard_bp.get("/dashboard")
@dashboard_bp.get("/dashboard/")
def dashboard_page():
    """Serve the analytics dashboard."""
    enforcement = _require_admin_access()
    if enforcement is not None:
        return enforcement
    return send_from_directory("static", "dashboard.html")


@dashboard_bp.get("/api/dashboard/analytics/summary")
def dashboard_analytics_summary():
    """Return aggregated analytics data for dashboard."""
    enforcement = _require_admin_access()
    if enforcement is not None:
        return enforcement
    limit = request.args.get("limit", type=int)
    data = get_analytics_summary(limit or 200)
    return jsonify(data)
