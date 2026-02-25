import sqlite3
import logging
import datetime
import os
from typing import Any, Dict, List, Tuple
from flask import request, current_app
try:
    import geoip2.database
except ImportError:
    geoip2 = None

from src.core.config import ANALYTICS_DB, GEOIP_DB_PATH

# Cache size limits
_CACHE_MAX_SIZE = 1000
_USER_AGENT_MAX_LENGTH = 400

_geoip_reader = None
_geoip_cache = {}
_UA_CACHE: dict[str, tuple[str, str, str, str]] = {}

def init_analytics_db():
    """Ensure the SQLite analytics table exists."""
    try:
        with sqlite3.connect(ANALYTICS_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analytics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT,
                    username TEXT,
                    method TEXT,
                    path TEXT,
                    ip TEXT,
                    country TEXT,
                    user_agent TEXT,
                    ua_browser TEXT,
                    ua_browser_ver TEXT,
                    ua_os TEXT,
                    ua_device TEXT,
                    group_label TEXT,
                    subgroup_label TEXT
                )
            """)
            # Add indices for performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON analytics(ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_username ON analytics(username)")
    except Exception as e:
        logging.error("Failed to init analytics DB: %s", e)

def get_geoip_reader():
    global _geoip_reader
    if _geoip_reader is None and geoip2:
        if os.path.exists(GEOIP_DB_PATH):
            try:
                _geoip_reader = geoip2.database.Reader(GEOIP_DB_PATH)
            except Exception:
                pass
    return _geoip_reader

def lookup_country(ip: str) -> str:
    if not ip or ip in ("127.0.0.1", "::1"):
        return "Local"
    if ip in _geoip_cache:
        return _geoip_cache[ip]
    
    reader = get_geoip_reader()
    country = ""
    if reader:
        try:
            resp = reader.country(ip)
            country = resp.country.iso_code or ""
        except Exception:
            pass
    
    if len(_geoip_cache) > _CACHE_MAX_SIZE:
        _geoip_cache.clear()
    _geoip_cache[ip] = country
    return country

def parse_ua(ua_string: str) -> tuple[str, str, str, str]:
    if not ua_string:
        return "Unknown", "Unknown", "Unknown", ""
    if ua_string in _UA_CACHE:
        return _UA_CACHE[ua_string]

    # Simple heuristic (same as before or improved)
    device, os_name, browser, version = "Desktop", "Unknown", "Unknown", ""
    lower = ua_string.lower()
    
    if "mobile" in lower or "android" in lower or "iphone" in lower:
        device = "Mobile"
    elif "tablet" in lower or "ipad" in lower:
        device = "Tablet"
    
    if "windows" in lower: os_name = "Windows"
    elif "mac os" in lower: os_name = "macOS"
    elif "linux" in lower: os_name = "Linux"
    elif "android" in lower: os_name = "Android"
    elif "ios" in lower or "iphone" in lower: os_name = "iOS"
    
    if "firefox" in lower: browser = "Firefox"
    elif "chrome" in lower: browser = "Chrome"
    elif "safari" in lower: browser = "Safari"
    elif "edge" in lower: browser = "Edge"
    
    result = (browser, version, os_name, device) # Order matches app.py usage: ua_browser, ua_browser_ver, ua_os, ua_device
    if len(_UA_CACHE) > _CACHE_MAX_SIZE:
        _UA_CACHE.clear()
    _UA_CACHE[ua_string] = result
    return result

def log_analytics_event(username: str, method: str, path: str, ip: str, user_agent: str, country: str, group_label: str, subgroup_label: str):
    try:
        ua_browser, ua_browser_ver, ua_os, ua_device = parse_ua(user_agent)
        ts = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
        
        with sqlite3.connect(ANALYTICS_DB) as conn:
            conn.execute(
                "INSERT INTO analytics (ts, username, method, path, ip, country, user_agent, ua_browser, ua_browser_ver, ua_os, ua_device, group_label, subgroup_label) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, username, method, path, ip, country, user_agent[:_USER_AGENT_MAX_LENGTH], ua_browser, ua_browser_ver, ua_os, ua_device, group_label, subgroup_label)
            )
    except Exception as e:
        logging.warning("Analytics log failed: %s", e)

def _build_group_summary(rows):
    summary: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        group = (row["group_label"] or "App").strip() or "App"
        method = (row["method"] or "GET").upper()
        count = int(row["count"] or 0)
        entry = summary.setdefault(group, {"group": group, "total": 0, "methods": {}})
        entry["total"] += count
        entry_methods = entry["methods"]
        entry_methods[method] = entry_methods.get(method, 0) + count
    return sorted(summary.values(), key=lambda item: item["total"], reverse=True)

def _build_group_details(rows, limit: int = 10):
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows or []:
        group = (row["group_label"] or "App").strip() or "App"
        subgroup = (row["subgroup_label"] or "Other").strip() or "Other"
        method = (row["method"] or "GET").upper()
        count = int(row["count"] or 0)
        group_bucket = grouped.setdefault(group, {})
        detail_entry = group_bucket.setdefault(subgroup, {"subgroup": subgroup, "total": 0, "methods": {}})
        detail_entry["total"] += count
        detail_entry["methods"][method] = detail_entry["methods"].get(method, 0) + count
    detail_map: dict[str, list[dict[str, Any]]] = {}
    for group, entries in grouped.items():
        ordered = sorted(entries.values(), key=lambda item: item["total"], reverse=True)
        detail_map[group] = ordered[:limit]
    return detail_map

def get_analytics_summary(limit: int = 200) -> Dict[str, Any]:
    capped = max(1, int(limit or 0))
    with sqlite3.connect(ANALYTICS_DB) as conn:
        conn.row_factory = sqlite3.Row
        
        recent_rows = conn.execute(
            """
            SELECT ts, username, method, path, ip, country, user_agent, ua_browser, ua_browser_ver, ua_os, ua_device, group_label, subgroup_label
            FROM analytics
            ORDER BY id DESC
            LIMIT ?
            """,
            (capped,),
        ).fetchall()
        
        totals_row = conn.execute("SELECT COUNT(*) AS total, COUNT(DISTINCT username) AS users, COUNT(DISTINCT ip) AS ips FROM analytics").fetchone()
        
        country_rows = conn.execute(
            """
            SELECT COALESCE(country, '') AS country, COUNT(*) AS count
            FROM analytics
            GROUP BY COALESCE(country, '')
            ORDER BY count DESC
            LIMIT 50
            """
        ).fetchall()
        
        group_rows = conn.execute(
            """
            SELECT COALESCE(group_label, '') AS group_label, method, COUNT(*) AS count
            FROM analytics
            GROUP BY COALESCE(group_label, ''), method
            """
        ).fetchall()
        
        subgroup_rows = conn.execute(
            """
            SELECT COALESCE(group_label, '') AS group_label,
                   COALESCE(subgroup_label, '') AS subgroup_label,
                   method,
                   COUNT(*) AS count
            FROM analytics
            GROUP BY COALESCE(group_label, ''), COALESCE(subgroup_label, ''), method
            """
        ).fetchall()

    def _row_to_dict(row):
        return {key: row[key] for key in row.keys()}

    return {
        "totals": {
            "requests": totals_row["total"] if totals_row else 0,
            "unique_users": totals_row["users"] if totals_row else 0,
            "unique_ips": totals_row["ips"] if totals_row else 0,
        },
        "recent": [_row_to_dict(row) for row in recent_rows],
        "countries": [_row_to_dict(row) for row in country_rows],
        "group_summary": _build_group_summary(group_rows),
        "group_details": _build_group_details(subgroup_rows),
    }
