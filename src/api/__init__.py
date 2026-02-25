"""
api — Flask route blueprints for local-chat
"""

from src.api.static_routes import static_bp
from src.api.auth_routes import auth_bp
from src.api.dashboard_routes import dashboard_bp
from src.api.chat_routes import chat_bp
from src.api.session_routes import session_bp
from src.api.analytics_routes import analytics_bp
from src.api.benchmark_routes import benchmark_bp

__all__ = [
    "static_bp",
    "auth_bp",
    "dashboard_bp",
    "chat_bp",
    "session_bp",
    "analytics_bp",
    "benchmark_bp",
]
