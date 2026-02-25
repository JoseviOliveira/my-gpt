import re
import secrets
import datetime
from typing import Tuple, Dict
from src.core.config import USERS, AUTH_TOKEN_TTL

# In-memory token storage: token -> (username, expiry)
AUTH_TOKENS: Dict[str, Tuple[str, datetime.datetime]] = {}

_SAFE_USER_RE = re.compile(r"[^A-Za-z0-9._-]")

def sanitize_username(name: str) -> str:
    cleaned = _SAFE_USER_RE.sub("_", (name or "").strip())
    return cleaned or "default"

def check_auth(username, password):
    return USERS.get(username) == password

def create_token(username: str) -> str:
    token = secrets.token_hex(32)
    expiry = datetime.datetime.now() + AUTH_TOKEN_TTL
    AUTH_TOKENS[token] = (username, expiry)
    return token

def verify_token(token: str) -> str | None:
    if not token:
        return None
    record = AUTH_TOKENS.get(token)
    if not record:
        return None
    username, expiry = record
    if datetime.datetime.now() > expiry:
        del AUTH_TOKENS[token]
        return None
    return username

def cleanup_tokens():
    now = datetime.datetime.now()
    expired = [t for t, (_, exp) in AUTH_TOKENS.items() if now > exp]
    for t in expired:
        del AUTH_TOKENS[t]
