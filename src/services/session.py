"""
services/session.py — Session storage and management
"""

import json
import pathlib
import uuid
from datetime import datetime, timezone

from src.core.config import LOGDIR as CHAT_LOGDIR

LOGDIR = CHAT_LOGDIR
DEFAULT_USER = "guest"


def _utcnow_iso() -> str:
    """Return the current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _user_dir(user: str | None, *, create: bool = True) -> pathlib.Path:
    """Return the directory for a user's sessions."""
    username = (user or DEFAULT_USER).strip() or DEFAULT_USER
    if username == DEFAULT_USER:
        directory = LOGDIR
    else:
        directory = LOGDIR / username
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def session_path(sid: str, user: str | None = None, *, ensure_dir: bool = True) -> pathlib.Path:
    """Return the filesystem path for a stored chat session."""
    directory = _user_dir(user, create=ensure_dir)
    return directory / f"{sid}.json"


def locate_existing_session(sid: str) -> pathlib.Path | None:
    """Search all known user directories for a session file."""
    direct = LOGDIR / f"{sid}.json"
    if direct.exists():
        return direct
    for entry in LOGDIR.iterdir():
        if entry.is_dir():
            candidate = entry / f"{sid}.json"
            if candidate.exists():
                return candidate
    return None


def _read_session_file(path: pathlib.Path) -> dict:
    """Read and parse a session file, setting defaults."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not data.get("owner"):
        if path.parent == LOGDIR:
            data["owner"] = DEFAULT_USER
        else:
            data["owner"] = path.parent.name
    data.setdefault("pinned", False)
    return data


def load_session(sid: str, user: str | None = None) -> dict | None:
    """Load a saved session from disk if it exists."""
    if user:
        p = session_path(sid, user, ensure_dir=False)
        if not p.exists():
            return None
        return _read_session_file(p)
    p = locate_existing_session(sid)
    if not p:
        return None
    return _read_session_file(p)


def write_session(obj: dict, user: str | None = None) -> dict:
    """Persist the provided session payload to disk."""
    obj.setdefault("title", None)
    obj.setdefault("title_ai", None)
    obj.setdefault("summary_ai", None)
    obj.setdefault("pinned", False)
    owner = user or obj.get("owner") or DEFAULT_USER
    obj["owner"] = owner
    p = session_path(obj["id"], owner)
    obj["updated_at"] = _utcnow_iso()
    with p.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return obj


def list_sessions(user: str | None = None) -> list[dict]:
    """Enumerate stored sessions ordered by most recent update."""
    directory = _user_dir(user, create=True)
    items = []
    for p in sorted(directory.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            o = _read_session_file(p)
            title = o.get("title") or o.get("title_ai") or "(untitled)"
            items.append({
                "id": o.get("id", p.stem),
                "title": title,
                "title_ai": o.get("title_ai"),
                "summary_ai": o.get("summary_ai"),
                "updated_at": o.get("updated_at"),
                "pinned": bool(o.get("pinned")),
            })
        except Exception:
            continue
    return items


def create_session(user: str | None = None) -> dict:
    """Create a new empty chat session."""
    sid = uuid.uuid4().hex[:12]
    obj = {
        "id": sid,
        "owner": user or DEFAULT_USER,
        "title": None,
        "title_ai": None,
        "summary_ai": None,
        "messages": [],
        "updated_at": _utcnow_iso(),
        "pinned": False,
    }
    write_session(obj, user=user)
    return obj


def delete_session(sid: str, user: str | None = None) -> bool:
    """Remove a saved chat session from disk if present."""
    p = session_path(sid, user, ensure_dir=False)
    if p.exists():
        p.unlink()
        return True
    return False
