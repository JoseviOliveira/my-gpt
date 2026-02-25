"""
app_refactored.py — Flask entrypoint for the local chat server (modular version)

This is a thin entry point that:
- Creates the Flask app
- Registers blueprints from routes/
- Sets up auth middleware
- Sets up analytics capture
- Runs the server

The business logic lives in services/ and routes/.
"""

import os
import re
import base64
import logging
import secrets
import datetime
import warnings
import uuid

from flask import Flask, request, jsonify, g, current_app
from werkzeug.serving import WSGIRequestHandler
from werkzeug.middleware.proxy_fix import ProxyFix

# Suppress torch warnings
warnings.filterwarnings(
    "ignore",
    message="You are using `torch.load` with `weights_only=False`.*",
    category=FutureWarning,
)

# App configuration and logging
from src.core.config import (
    OLLAMA_URL, MODEL, LOG_LEVEL,
    STT_MODE, TTS_MODE, _STT_ALLOWED, _TTS_ALLOWED,
    AUTH_TOKEN_TTL, ANONYMOUS_ANALYTICS_USER, USERS, DEFAULT_USER, ADMIN_USERS,
)
from src.core.logging import ensure_structured_logging
FORMATTER = ensure_structured_logging(LOG_LEVEL)
from src.core.analytics import init_analytics_db, log_analytics_event
from src.core.auth import (
    AUTH_TOKENS, sanitize_username, verify_token,
)

# Import blueprints
from src.api.stt import stt_bp
from src.api.tts import tts_bp
from src.api import (
    static_bp,
    auth_bp,
    dashboard_bp,
    chat_bp,
    session_bp,
    analytics_bp,
    benchmark_bp,
)

# Import services
from src.services.geoip import resolve_country

# Initialize analytics DB
init_analytics_db()

# ---------------------------------------------------------------------------
# Flask app creation
# ---------------------------------------------------------------------------

app = Flask(__name__, static_url_path="", static_folder="static")
# Trust a single reverse proxy (Caddy) for client IPs and scheme.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.logger.setLevel(LOG_LEVEL)
for handler in app.logger.handlers:
    handler.setLevel(LOG_LEVEL)
    if FORMATTER:
        handler.setFormatter(FORMATTER)

# Register blueprints
app.register_blueprint(stt_bp)
app.register_blueprint(tts_bp)
app.register_blueprint(static_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(session_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(benchmark_bp)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _authenticate(auth_header: str) -> str | None:
    """Validate a Basic Authorization header against configured credentials."""
    if not auth_header or not auth_header.startswith("Basic "):
        return None
    try:
        u, p = base64.b64decode(auth_header.split(" ", 1)[1]).decode().split(":", 1)
    except Exception:
        return None
    if USERS.get(u) == p:
        return u
    return None


def _resolve_bearer_user(header: str) -> str | None:
    """Extract and validate a Bearer token from the Authorization header."""
    if not header:
        return None
    parts = header.split(" ", 1)
    if len(parts) != 2:
        return None
    if parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return _validate_token(token)


def _validate_token(token: str | None) -> str | None:
    """Check if a token is valid and not expired."""
    if not token:
        return None
    entry = AUTH_TOKENS.get(token)
    if not entry:
        return None
    username, expires = entry
    if expires < datetime.datetime.utcnow():
        AUTH_TOKENS.pop(token, None)
        return None
    return username


def _needs_auth(path: str) -> bool:
    """Determine if a path requires authentication."""
    if not path:
        return False
    if path == "/api/login":
        return False
    public_paths = {
        "/",
        "/index.html",
        "/robots.txt",
        "/sitemap.xml",
        "/manifest.webmanifest",
        "/favicon.ico",
        "/apple-touch-icon.png",
        "/apple-touch-icon-precomposed.png",
        "/js/docs_theme.js",
        "/api/gpu",
        "/api/temperature",
        "/benchmark.log",
        "/api/benchmark/status",
        "/api/benchmark/datasets",
        "/api/benchmark/last_task",
    }
    if path.startswith(("/css/", "/js/", "/icons/")):
        return False
    if path.startswith("/log/benchmark.log"):
        return False
    if path.startswith("/docs"):
        if path in {"/docs", "/docs/"} or path.endswith(".html"):
            return False
    if path in public_paths:
        return False
    return True


# ---------------------------------------------------------------------------
# Analytics helpers
# ---------------------------------------------------------------------------

def _derive_action_labels(method: str | None, path: str | None) -> tuple[str, str]:
    """Derive group/subgroup labels from HTTP method and path."""
    method = (method or "").upper()
    path = path or ""

    def label(group: str, subgroup: str) -> tuple[str, str]:
        return group, subgroup

    if path.startswith("/api/stream"):
        return label("Chat", "Generate reply")
    if path.startswith("/api/save"):
        return label("Chat", "Save session")
    if path.startswith("/api/session"):
        if method == "POST":
            return label("Chat", "New session")
        if method == "DELETE":
            return label("Chat", "Delete session")
        return label("Chat", "Load session")
    if path.startswith("/api/sessions"):
        return label("Chat", "List sessions")
    if path.startswith("/api/stt"):
        return label("Speech input", "Whisper transcription")
    if path.startswith("/api/tts/speak"):
        return label("Speech output", "Server read-aloud")
    if path.startswith("/api/tts/metrics"):
        return label("Speech output", "Playback metrics")
    if path.startswith("/api/tts/warmup"):
        return label("Speech output", "Warmup")
    if path.startswith("/api/tts"):
        return label("Speech output", "Other TTS")
    if path.startswith("/api/metrics"):
        return label("Chat", "Fetch metrics")
    if path.startswith("/api/login"):
        return label("Session", "Login")
    if path.startswith("/api/dashboard/analytics"):
        return label("Dashboard", "Analytics API")
    if path.startswith("/api/dashboard"):
        return label("Dashboard", "Dashboard API")
    if path.startswith("/dashboard"):
        return label("Dashboard", "Dashboard UI")
    if path.startswith("/docs"):
        return label("Docs", "Documentation")
    if path.startswith("/config"):
        return label("Settings", "Load configuration")
    if path.startswith("/static"):
        return label("Assets", "Static file")
    if path == "/":
        return label("App", "Home")
    if path.startswith("/api"):
        return label("API", "Other endpoint")
    return label("App", "Other")


def _resolve_action_labels(method: str, path: str) -> tuple[str, str]:
    """Get action labels, checking for override in g first."""
    override = getattr(g, "_analytics_labels", None)
    if override:
        return override
    return _derive_action_labels(method, path)


_ANALYTICS_SKIP_PREFIXES = (
    "/static",
    "/favicon",
    "/log/",
    "/api/analytics/action",
    # High-frequency benchmark monitor polling endpoints.
    "/api/benchmark/",
)


def _should_skip_analytics(path: str) -> bool:
    """Determine if analytics logging should be skipped for this path."""
    return any(path.startswith(prefix) for prefix in _ANALYTICS_SKIP_PREFIXES)


def _client_ip() -> str:
    """Extract the client IP from request headers."""
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if forwarded:
        return forwarded
    return request.remote_addr or ""


def _current_username() -> str:
    """Get the current authenticated username."""
    return getattr(g, "current_user", None) or DEFAULT_USER


def _analytics_username() -> str:
    """Get the username for analytics logging."""
    user = getattr(g, "current_user", None)
    if user:
        return user
    return ANONYMOUS_ANALYTICS_USER


# ---------------------------------------------------------------------------
# Before-request hooks
# ---------------------------------------------------------------------------

@app.before_request
def setup_request_context():
    """Populate request context for logging (req_id, username, session_id)."""
    g.req_id = uuid.uuid4().hex[:8]
    g.username = ANONYMOUS_ANALYTICS_USER  # Will be updated after auth
    g.session_id = request.headers.get('X-Session-ID', '-')


@app.before_request
def require_auth():
    """Enforce authentication on protected endpoints before serving the request."""
    path = request.path or ""
    if path == "/api/login":
        return
    if _needs_auth(path):
        auth_header = request.headers.get("Authorization", "")
        user = None
        if auth_header:
            bearer = _resolve_bearer_user(auth_header)
            if bearer:
                user = bearer
            else:
                user = _authenticate(auth_header)
        if not user:
            token_header = request.headers.get("X-Auth-Token", "")
            user = _validate_token(token_header.strip())
        if not user:
            cookie_token = request.cookies.get("auth_token", "").strip()
            user = _validate_token(cookie_token)
        if not user:
            return jsonify({"error": "auth_required"}), 401
        g.current_user = user
        g.username = user  # Update logging context


@app.before_request
def capture_analytics():
    """Log analytics for incoming requests."""
    path = request.path or "/"
    if _should_skip_analytics(path):
        return
    if request.method == "OPTIONS":
        return
    try:
        authed = bool(getattr(g, "current_user", None))
        username = _analytics_username() if authed else ANONYMOUS_ANALYTICS_USER
        client_ip = _client_ip()
        group_label, subgroup_label = _resolve_action_labels(request.method, path)
        log_analytics_event(
            username,
            request.method,
            path,
            client_ip,
            request.headers.get("User-Agent", ""),
            resolve_country(client_ip),
            group_label,
            subgroup_label,
        )
    except Exception as exc:
        current_app.logger.debug("[analytics] capture failed: %s", exc)
    finally:
        if hasattr(g, "_analytics_labels"):
            try:
                del g._analytics_labels
            except Exception:
                g._analytics_labels = None


# ---------------------------------------------------------------------------
# Minimal request handler for cleaner logs
# ---------------------------------------------------------------------------

class MinimalRequestHandler(WSGIRequestHandler):
    """Suppress the default bracketed timestamp; rely on logger formatting instead."""

    LEVEL_MAP = {
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "debug": logging.DEBUG,
    }

    def log(self, type: str, message: str, *args) -> None:
        logger = logging.getLogger("werkzeug")
        if not logger.handlers:
            return
        level = self.LEVEL_MAP.get(type.lower(), logging.INFO)
        try:
            rendered = message % args if args else message
        except Exception:
            rendered = message
        logger.log(level, "%s - - %s", self.address_string().replace("%", "%%"), rendered.strip())


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("CHAT_PORT", "8080"))
    
    # Display startup configuration
    app.logger.info("=" * 60)
    app.logger.info("Starting local-chat on port %d", port)
    app.logger.info("Configuration:")
    app.logger.info("  OLLAMA_URL: %s", OLLAMA_URL)
    app.logger.info("  MODEL: %s", MODEL)
    app.logger.info("  LOG_LEVEL: %s", logging.getLevelName(LOG_LEVEL))
    app.logger.info("  STT_MODE: %s", STT_MODE)
    app.logger.info("  TTS_MODE: %s", TTS_MODE)
    app.logger.info("  AUTH_TOKEN_TTL: %s", AUTH_TOKEN_TTL)
    app.logger.info("  Users configured: %d", len(USERS))
    app.logger.info("  Admin users: %s", ", ".join(ADMIN_USERS) if ADMIN_USERS else "none")
    app.logger.info("=" * 60)
    
    app.run(host="0.0.0.0", port=port, threaded=True, request_handler=MinimalRequestHandler)
