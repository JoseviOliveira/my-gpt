"""
services — Business logic services for local-chat
"""

from src.services.geoip import (
    init_geoip_reader,
    lookup_country,
    is_private_ip,
    resolve_country,
)
from src.services.user_agent import parse_user_agent
from src.services.session import (
    load_session,
    write_session,
    list_sessions,
    create_session,
    delete_session,
    session_path,
    locate_existing_session,
)
from src.services.metadata import (
    notify_stream_start,
    notify_stream_end,
    detect_language_for_text,
    detect_lang_from_messages,
    detect_lang_for_request,
    generate_metadata,
    enqueue_metadata_job,
    ensure_metadata_scheduler,
)

__all__ = [
    # geoip
    "init_geoip_reader",
    "lookup_country",
    "is_private_ip",
    "resolve_country",
    # user_agent
    "parse_user_agent",
    # session
    "load_session",
    "write_session",
    "list_sessions",
    "create_session",
    "delete_session",
    "session_path",
    "locate_existing_session",
    # metadata
    "notify_stream_start",
    "notify_stream_end",
    "detect_language_for_text",
    "detect_lang_from_messages",
    "detect_lang_for_request",
    "generate_metadata",
    "enqueue_metadata_job",
    "ensure_metadata_scheduler",
]
