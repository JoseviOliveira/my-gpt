"""
services/metadata/scheduling.py — Idle-aware background job scheduler
"""

import os
import threading
import time
from collections import deque

from flask import current_app

# Timing constants for metadata scheduling
METADATA_IDLE_DELAY = float(os.getenv("METADATA_IDLE_DELAY", "10"))
METADATA_IDLE_CHECK_INTERVAL = float(os.getenv("METADATA_IDLE_CHECK_INTERVAL", "2"))

# Internal scheduler state
_METADATA_LOCK = threading.Lock()
_METADATA_PENDING: set[tuple[str, str]] = set()
_METADATA_QUEUE: deque[tuple[str, str]] = deque()
_METADATA_SCHED_LOCK = threading.Lock()
_METADATA_SCHEDULER: threading.Thread | None = None

# Activity tracking for idle detection
_ACTIVITY_LOCK = threading.Lock()
_LAST_ACTIVITY_TS = time.monotonic()
_ACTIVE_STREAMS = 0


# ---------------------------------------------------------------------------
# Activity tracking helpers (called by route handlers)
# ---------------------------------------------------------------------------

def notify_stream_start():
    """Record that a streaming response has begun."""
    global _ACTIVE_STREAMS, _LAST_ACTIVITY_TS
    with _ACTIVITY_LOCK:
        _ACTIVE_STREAMS += 1
        _LAST_ACTIVITY_TS = time.monotonic()


def notify_stream_end():
    """Record that a streaming response has finished."""
    global _ACTIVE_STREAMS, _LAST_ACTIVITY_TS
    with _ACTIVITY_LOCK:
        _ACTIVE_STREAMS = max(0, _ACTIVE_STREAMS - 1)
        _LAST_ACTIVITY_TS = time.monotonic()


def metadata_idle_ready() -> bool:
    """Return True when the server has been idle long enough for metadata jobs."""
    with _ACTIVITY_LOCK:
        idle = _ACTIVE_STREAMS == 0 and (time.monotonic() - _LAST_ACTIVITY_TS) >= METADATA_IDLE_DELAY
    return idle


# ---------------------------------------------------------------------------
# Scheduler functions
# ---------------------------------------------------------------------------

def enqueue_metadata_job(user: str, sid: str):
    """Add a session to the metadata generation queue."""
    with _METADATA_LOCK:
        key = (user, sid)
        if key in _METADATA_PENDING:
            return
        _METADATA_PENDING.add(key)
        _METADATA_QUEUE.append(key)
    ensure_metadata_scheduler()


def pop_pending_job() -> tuple[str, str] | None:
    """Pop the next job from the queue (called by scheduler)."""
    with _METADATA_LOCK:
        if not _METADATA_QUEUE:
            return None
        key = _METADATA_QUEUE.popleft()
        _METADATA_PENDING.discard(key)
        return key


def pending_count() -> int:
    """Return the number of pending metadata jobs."""
    with _METADATA_LOCK:
        return len(_METADATA_QUEUE)


def ensure_metadata_scheduler():
    """Start the idle scheduler thread if it's not already running."""
    global _METADATA_SCHEDULER
    with _METADATA_SCHED_LOCK:
        if _METADATA_SCHEDULER and _METADATA_SCHEDULER.is_alive():
            return
        from app import app  # delayed import to avoid circular dependency

        def scheduler_loop():
            while True:
                time.sleep(METADATA_IDLE_CHECK_INTERVAL)
                try:
                    if not metadata_idle_ready():
                        continue
                    job = pop_pending_job()
                    if not job:
                        continue
                    user, sid = job
                    pending = pending_count()
                    app.logger.info(
                        "[metadata] scheduler dispatch sid=%s user=%s queue=%d",
                        sid,
                        user,
                        pending,
                    )
                    threading.Thread(target=_run_metadata_worker, args=(sid, user, app), daemon=True).start()
                except Exception as exc:  # pragma: no cover - scheduler guard
                    app.logger.exception("[metadata] scheduler failure")

        _METADATA_SCHEDULER = threading.Thread(target=scheduler_loop, daemon=True)
        _METADATA_SCHEDULER.start()


def _run_metadata_worker(sid: str, user: str, app):
    """Worker function that runs metadata generation within app context."""
    # Import session helpers from services to avoid circular imports
    from src.services.session import load_session, write_session
    from .summary import metadata_context, generate_metadata

    with app.app_context():
        try:
            session = load_session(sid, user=user)
            if not session:
                current_app.logger.debug("[metadata] session not found sid=%s user=%s", sid, user)
                return
            messages = session.get("messages") or []
            if not messages:
                current_app.logger.debug("[metadata] no messages sid=%s user=%s", sid, user)
                return
            manual_title = (session.get("title") or "").strip()
            removed_default_title = False
            title_ai_old = (session.get("title_ai") or "").strip()
            summary_old = (session.get("summary_ai") or "").strip()

            if manual_title:
                first_user = ""
                for msg in messages:
                    if (msg.get("role") or "").lower() == "user":
                        first_user = (msg.get("content") or "").strip()
                        break
                default_guess = ""
                if first_user:
                    default_guess = first_user[:40] + ("…" if len(first_user) > 40 else "")
                if manual_title.lower() in {"(untitled)", default_guess.lower()}:
                    manual_title = ""
                    session["title"] = None
                    removed_default_title = True

            context_messages = metadata_context(session)
            current_app.logger.debug(
                "[metadata] generating sid=%s user=%s context_len=%d",
                sid,
                user,
                len(context_messages),
            )
            title_new, summary_new = generate_metadata(context_messages, summary_old)
            updated = False

            if title_new and title_new != title_ai_old:
                session["title_ai"] = title_new
                updated = True
            if summary_new and summary_new != summary_old:
                session["summary_ai"] = summary_new
                updated = True
            if removed_default_title:
                updated = True

            if updated:
                write_session(session, user=user)
                current_app.logger.info(
                    "[metadata] updated sid=%s user=%s title=%s summary_len=%d",
                    sid,
                    user,
                    title_new or "(none)",
                    len(summary_new or ""),
                )
            else:
                current_app.logger.debug("[metadata] no changes sid=%s user=%s", sid, user)
        except Exception as exc:
            current_app.logger.exception("[metadata] worker failure sid=%s", sid)
