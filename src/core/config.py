import os
import logging
import pathlib
import datetime

# Base paths - go up from src/core to project root
APPDIR = pathlib.Path(__file__).resolve().parent.parent.parent
LOGDIR = APPDIR / "chats"
DBDIR = APPDIR / "db"
LOGDIR.mkdir(exist_ok=True)
DBDIR.mkdir(exist_ok=True)

# Logging
LOG_LEVEL_NAME = os.getenv("APP_LOG_LEVEL", "INFO").strip().upper() or "INFO"
LOG_LEVEL = getattr(logging, LOG_LEVEL_NAME, logging.INFO)
if LOG_LEVEL_NAME not in logging._nameToLevel:
    LOG_LEVEL_NAME = logging.getLevelName(LOG_LEVEL)

# Ollama
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.getenv("MODEL", "gpt-oss:20b")
SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "gpt-oss:20b")
_EXTRA_MODELS = {entry.strip() for entry in os.getenv("MODEL_EXTRA", "").split(",") if entry.strip()}
_UI_MODELS = {"gpt-oss:20b", "qwen3:4b", "qwen3:8b", "qwen3:14b", "gemma3:12b", "gemma3:4b", "deepseek-r1:8b", "deepseek-r1:14b", "magistral:24b"}
MODEL_ALLOWLIST = set(_UI_MODELS)
MODEL_ALLOWLIST.add(MODEL)
MODEL_ALLOWLIST.add(SUMMARY_MODEL)
MODEL_ALLOWLIST.update(_EXTRA_MODELS)

# Metadata / Context
METADATA_CONTEXT_WINDOW = max(4, int(os.getenv("METADATA_CONTEXT_WINDOW", "4")))
METADATA_IDLE_DELAY = float(os.getenv("METADATA_IDLE_DELAY", "6"))
METADATA_IDLE_CHECK_INTERVAL = float(os.getenv("METADATA_IDLE_CHECK_INTERVAL", "2"))

# Audio Modes
_STT_ALLOWED = {"browser", "whisper"}
STT_MODE = os.getenv("STT_MODE", "browser").strip().lower()
if STT_MODE not in _STT_ALLOWED:
    STT_MODE = "browser"

_TTS_ALLOWED = {"browser", "coqui"}
TTS_MODE = os.getenv("TTS_MODE", "browser").strip().lower()
if TTS_MODE not in _TTS_ALLOWED:
    TTS_MODE = "browser"

# Runtime flags
def _env_flag(name: str, default: str = "0") -> bool:
    value = os.getenv(name, default).strip().lower()
    return value in {"1", "true", "yes", "on"}

ECO_MODE = _env_flag("ECO_MODE")

# GeoIP
GEOIP_DB_PATH = os.getenv("GEOIP_DB", str((DBDIR / "GeoLite2-Country.mmdb")))
ANALYTICS_DB = os.getenv("ANALYTICS_DB", str((DBDIR / "analytics.db")))

# Auth
AUTH_TOKEN_TTL = datetime.timedelta(seconds=int(os.getenv("AUTH_TOKEN_TTL", "86400")))
ANONYMOUS_ANALYTICS_USER = "anonymous"

def load_users() -> dict[str, str]:
    raw = os.getenv("APP_USERS", "")
    users: dict[str, str] = {}
    if raw:
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry or ":" not in entry:
                continue
            username, password = entry.split(":", 1)
            username = username.strip()
            if not username:
                continue
            users[username] = password
    if not users:
        username = os.getenv("APP_USER", "me")
        password = os.getenv("APP_PASS", "change-me")
        users[username] = password
    return users

USERS = load_users()
DEFAULT_USER = next(iter(USERS))
GUEST_USER = os.getenv("APP_GUEST_USER", "guest").strip() or "guest"

_ADMIN_USERS = {entry.strip() for entry in os.getenv("ANALYTICS_ADMINS", "").split(",") if entry.strip()}
if not _ADMIN_USERS:
    ADMIN_USERS = {DEFAULT_USER}
else:
    ADMIN_USERS = _ADMIN_USERS

def is_guest_user(username: str | None) -> bool:
    """Return True when the provided username maps to the guest account."""
    if not username:
        return False
    return username.strip().lower() == GUEST_USER.lower()

def is_admin_user(username: str | None) -> bool:
    """Return True when the provided username maps to an admin account."""
    if not username:
        return False
    return username.strip().lower() in {name.lower() for name in ADMIN_USERS}

def _env_list(name: str, fallback: list[str]) -> list[str]:
    raw = os.getenv(name, "")
    values = [entry.strip() for entry in raw.split(",") if entry.strip()]
    return values or fallback

NON_ADMIN_ALLOWED_MODES = {
    entry.strip().lower()
    for entry in _env_list("NON_ADMIN_ALLOWED_MODES", ["fast", "normal"])
    if entry.strip()
}
NON_ADMIN_MODEL_ALLOWLIST = _env_list(
    "NON_ADMIN_MODEL_ALLOWLIST",
    ["deepseek-r1:8b", "gemma3:4b", "magistral:24b"],
)
_NON_ADMIN_FAST_DEFAULT = os.getenv("NON_ADMIN_FAST_MODEL", "deepseek-r1:8b").strip() or "deepseek-r1:8b"
_NON_ADMIN_NORMAL_DEFAULT = os.getenv("NON_ADMIN_NORMAL_MODEL", "gemma3:4b").strip() or "gemma3:4b"
_NON_ADMIN_DEEP_DEFAULT = os.getenv("NON_ADMIN_DEEP_MODEL", "magistral:24b").strip() or "magistral:24b"
NON_ADMIN_MODEL_DEFAULTS = {
    "fast": _NON_ADMIN_FAST_DEFAULT,
    "normal": _NON_ADMIN_NORMAL_DEFAULT,
    "deep": _NON_ADMIN_DEEP_DEFAULT,
}
NON_ADMIN_DAILY_PROMPT_LIMIT = int(os.getenv("NON_ADMIN_DAILY_PROMPT_LIMIT", "30") or 0)
NON_ADMIN_CHAT_PROMPT_LIMIT = int(os.getenv("NON_ADMIN_CHAT_PROMPT_LIMIT", "10") or 0)
NON_ADMIN_CHAT_LIMIT = int(os.getenv("NON_ADMIN_CHAT_LIMIT", "10") or 0)

def resolve_model_choice(payload: dict | None) -> str:
    """Return a safe model name honoring UI overrides when allowed."""
    if isinstance(payload, dict):
        requested = (payload.get("model") or "").strip()
        if requested and requested in MODEL_ALLOWLIST:
            return requested
    return MODEL
