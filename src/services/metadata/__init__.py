"""
services/metadata — AI-driven metadata generation with idle-aware scheduling.

Public API for language detection, summary generation, and background scheduling.
"""

from .language import (
    detect_language_for_text,
    detect_lang_from_messages,
    detect_lang_for_request,
    ensure_latest_user_language,
    language_guard_message,
)
from .summary import generate_metadata
from .scheduling import (
    notify_stream_start,
    notify_stream_end,
    enqueue_metadata_job,
    ensure_metadata_scheduler,
)

__all__ = [
    # Language detection
    "detect_language_for_text",
    "detect_lang_from_messages",
    "detect_lang_for_request",
    "ensure_latest_user_language",
    "language_guard_message",
    # Summary generation
    "generate_metadata",
    # Scheduling
    "notify_stream_start",
    "notify_stream_end",
    "enqueue_metadata_job",
    "ensure_metadata_scheduler",
]
