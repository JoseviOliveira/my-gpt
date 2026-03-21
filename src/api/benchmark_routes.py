"""
api/benchmark_routes.py — Benchmark status endpoints
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

benchmark_bp = Blueprint("benchmark", __name__)
logger = logging.getLogger(__name__)


def _get_file_descriptor_count() -> dict[str, Any]:
    """Count open file descriptors for the current process."""
    pid = os.getpid()
    try:
        # Fast path on Unix-like systems (including macOS): no subprocess needed.
        fd_dir = "/dev/fd"
        if os.path.isdir(fd_dir):
            # Filter out non-numeric entries defensively.
            count = sum(1 for name in os.listdir(fd_dir) if str(name).isdigit())
            if count >= 0:
                return {"count": count, "pid": pid}
    except Exception:
        pass

    try:
        # Fallback: use lsof if /dev/fd is unavailable.
        result = subprocess.run(
            ["lsof", "-nP", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            # Count lines (minus header)
            fd_count = len(result.stdout.strip().split("\n")) - 1
            return {"count": max(0, fd_count), "pid": pid}
        return {"count": -1, "pid": pid, "error": "lsof failed"}
    except subprocess.TimeoutExpired:
        return {"count": -1, "pid": pid, "error": "timeout"}
    except Exception as e:
        return {"count": -1, "pid": pid, "error": str(e)}


def _db_path() -> str:
    env_path = os.environ.get("BENCHMARK_DB_PATH")
    if env_path:
        return os.path.abspath(env_path)
    root_dir = Path(__file__).resolve().parents[2]
    return str(root_dir / "db" / "benchmark.db")


@contextlib.contextmanager
def _connect_db():
    """Context manager that properly closes the database connection."""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _select_default_run(cur: sqlite3.Cursor) -> sqlite3.Row | None:
    """Pick the run to display when no explicit run_id is provided.

    Preference:
    1) Fresh active runs (running/initializing), most recently updated.
    2) Otherwise, most recently updated historical run.
    """
    cur.execute(
        """
        SELECT
            br.run_id,
            br.started_at,
            br.completed_at,
            br.status,
            rs.updated_at,
            datetime(
                REPLACE(REPLACE(COALESCE(rs.updated_at, br.completed_at, br.started_at), 'T', ' '), 'Z', '')
            ) AS sort_ts
        FROM benchmark_runs br
        LEFT JOIN benchmark_run_state rs ON rs.run_id = br.run_id
        ORDER BY
            CASE
                WHEN br.status IN ('running', 'initializing')
                     AND sort_ts >= datetime('now', '-15 minutes') THEN 0
                ELSE 2
            END,
            sort_ts DESC
        LIMIT 1
        """
    )
    return cur.fetchone()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _row_value(row: sqlite3.Row, key: str) -> Any:
    return row[key] if key in row.keys() else None


def _safe_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _list_from_json(value: str | None) -> list[Any]:
    parsed = _safe_json(value)
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return parsed
    return [parsed]


def _normalize_dataset_label(value: str | None) -> str | None:
    if not value:
        return None
    label = os.path.basename(value)
    if "." in label:
        label = label.rsplit(".", 1)[0]
    label = re.sub(r"_\\d+$", "", label)
    return label


def _preview(value: Any, limit: int = 280) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        cleaned = value.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except ValueError:
        pass
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _normalize_since(value: str | None) -> str | None:
    if not value:
        return None
    ts = _parse_timestamp(value)
    if not ts:
        return None
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _task_passed_from_compliance(compliance: Any, violations: list[Any], error: str | None) -> bool:
    if error:
        return False
    if violations:
        return False
    if isinstance(compliance, dict):
        return all(bool(value) for value in compliance.values()) if compliance else True
    return True


def _build_server_status(state: dict[str, Any] | None, conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    """Build server status section for live KPIs."""
    if not state:
        return {}
    
    # Get current GPU temp from recent_gpu_temp
    gpu_temp = None
    if state.get("recent_gpu_temp"):
        temps = state["recent_gpu_temp"]
        if temps and len(temps) > 0:
            gpu_temp = temps[-1]  # Latest temp
    
    # Determine thermal status
    thermal_status = "idle"
    cooling_target = None
    
    # Check if we're in cooling state
    if state.get("status") == "cooling" or (state.get("current_task") or "").startswith("cooling"):
        thermal_status = "cooling"
        # Try to get target from config (default 70°C)
        cooling_target = 70
    
    return {
        "model_name": state.get("current_model"),
        "model_status": "loaded" if state.get("current_model") else "cold",
        "gpu_temp": gpu_temp,
        "thermal_status": thermal_status,
        "cooling_target": cooling_target
    }


def _build_dashboard_status(state: dict[str, Any] | None, conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    """Build dataset dashboard section for live KPIs."""
    if not state:
        return {}
    
    # Calculate quality from completed tasks only
    cur = conn.cursor()
    cur.execute("""
        SELECT
            COUNT(*) AS total_completed,
            SUM(CASE WHEN LOWER(COALESCE(status, '')) = 'passed' THEN 1 ELSE 0 END) AS passed_count,
            SUM(CASE WHEN LOWER(COALESCE(status, '')) IN ('failed', 'fail') THEN 1 ELSE 0 END) AS false_count
        FROM benchmark_run_tasks
        WHERE run_id = ? AND completed_at IS NOT NULL
    """, (run_id,))
    quality_row = cur.fetchone()

    total_completed = int(quality_row["total_completed"] or 0)
    passed_count = int(quality_row["passed_count"] or 0)
    false_count = int(quality_row["false_count"] or 0)
    others_count = max(0, total_completed - passed_count - false_count)

    passed_pct = round((passed_count / total_completed * 100) if total_completed > 0 else 0, 1)
    false_pct = round((false_count / total_completed * 100) if total_completed > 0 else 0, 1)
    others_pct = round((others_count / total_completed * 100) if total_completed > 0 else 0, 1)

    return {
        "model_index": state.get("model_index", 0),
        "models_total": state.get("models_total", 0),
        "dataset_index": state.get("dataset_index", 0),
        "datasets_total": state.get("datasets_total", 0),
        "quality": {
            "passed_pct": passed_pct,
            "false_pct": false_pct,
            "others_pct": others_pct,
            # Backward-compatible aliases used by older UI bindings.
            "correct_pct": passed_pct,
            "failed_pct": false_pct,
            "completed_count": total_completed
        }
    }


def _build_task_details(state: dict[str, Any] | None) -> dict[str, Any]:
    """Build task details section for live KPIs."""
    if not state:
        return {}

    req = state.get("last_request") or {}
    started = _parse_timestamp(req.get("started_at"))
    ended = _parse_timestamp(req.get("ended_at"))
    if started and not ended and req.get("status") == "running":
        ended = datetime.utcnow()

    e2e_ms = None
    if started and ended:
        e2e_ms = max(0, int((ended - started).total_seconds() * 1000))
    elif req.get("total_time_ms") is not None:
        with contextlib.suppress(Exception):
            e2e_ms = int(req.get("total_time_ms"))

    ttft_ms = req.get("ttft_ms")
    tokens_per_sec = req.get("tokens_per_sec")
    if ttft_ms is None or tokens_per_sec is None:
        recent = state.get("recent_metrics") or []
        if recent:
            last = recent[-1]
            if ttft_ms is None:
                ttft_ms = last.get("ttft_ms")
            if tokens_per_sec is None:
                tokens_per_sec = last.get("tokens_per_sec")

    status = (req.get("status") or state.get("status") or "pending").lower()
    status_map = {
        "completed": "done",
        "failed": "error",
        "running": "running",
        "pending": "pending",
        "initializing": "pending",
        "interrupted": "interrupted",
    }
    status = status_map.get(status, status)

    return {
        "attempt": req.get("attempt"),
        "max_retries": 3,
        "ttft_ms": ttft_ms,
        "tokens_per_sec": tokens_per_sec,
        "e2e_ms": e2e_ms,
        "status": status,
        "error": req.get("error"),
    }


def _build_workflow_status(state: dict[str, Any] | None) -> dict[str, Any]:
    """Build workflow pills status for live KPIs."""
    if not state:
        return {
            "cooling": {"status": "pending", "time": None, "live": False},
            "thinking": {"status": "pending", "time": None, "live": False},
            "streaming": {"status": "pending", "time": None, "live": False},
            "evaluating": {"status": "pending", "time": None, "live": False},
            "done": {"status": "pending"}
        }
    
    # For now, return basic structure
    # TODO: Enhance with actual workflow state tracking from runner
    current_status = state.get("status", "").lower()
    
    workflow = {
        "cooling": {"status": "skipped", "time": None, "live": False},
        "thinking": {"status": "pending", "time": None, "live": False},
        "streaming": {"status": "pending", "time": None, "live": False},
        "evaluating": {"status": "pending", "time": None, "live": False},
        "done": {"status": "pending"}
    }
    
    # Map current status to workflow stage
    if "cool" in current_status:
        workflow["cooling"]["status"] = "active"
        workflow["cooling"]["live"] = True
    elif "think" in current_status or current_status == "running":
        workflow["thinking"]["status"] = "active"
        workflow["thinking"]["live"] = True
    elif "stream" in current_status:
        workflow["streaming"]["status"] = "active"
        workflow["streaming"]["live"] = True
    elif "evaluat" in current_status:
        workflow["evaluating"]["status"] = "active"
        workflow["evaluating"]["live"] = True
    elif current_status == "completed":
        workflow["done"]["status"] = "completed"
    
    return workflow


def _build_datasets_pills(conn: sqlite3.Connection, run_id: str, current_model: str | None) -> dict[str, Any]:
    """Build datasets pills lists for live KPIs."""
    cur = conn.cursor()

    # Resolve model context for pills. If current_model is missing/stale, fall back
    # to the latest started model for this run, then to the first model in scope.
    resolved_model = (current_model or "").strip() or None

    def _fetch_dataset_rows(model_name: str | None) -> list[sqlite3.Row]:
        if not model_name:
            return []
        cur.execute(
            """
            SELECT
                model_name,
                dataset_name,
                dataset_label,
                tasks_total,
                status,
                started_at,
                completed_at
            FROM benchmark_run_datasets
            WHERE run_id = ? AND model_name = ?
            ORDER BY dataset_index
            """,
            (run_id, model_name),
        )
        return cur.fetchall()

    rows = _fetch_dataset_rows(resolved_model)
    if not rows:
        cur.execute(
            """
            SELECT model_name
            FROM benchmark_run_datasets
            WHERE run_id = ? AND started_at IS NOT NULL
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (run_id,),
        )
        latest_started = cur.fetchone()
        resolved_model = latest_started["model_name"] if latest_started else None
        rows = _fetch_dataset_rows(resolved_model)

    if not rows:
        cur.execute(
            """
            SELECT model_name
            FROM benchmark_run_datasets
            WHERE run_id = ?
            ORDER BY model_name ASC
            LIMIT 1
            """,
            (run_id,),
        )
        first_model = cur.fetchone()
        resolved_model = first_model["model_name"] if first_model else None
        rows = _fetch_dataset_rows(resolved_model)

    datasets = []
    for row in rows:
        # Get completed tasks count for this dataset
        cur.execute("""
            SELECT COUNT(*) as completed
            FROM benchmark_run_tasks
            WHERE run_id = ? AND model_name = ? AND dataset_name = ? 
            AND completed_at IS NOT NULL
        """, (run_id, row["model_name"], row["dataset_name"]))
        completed = cur.fetchone()["completed"]
        
        status_icon = "pending"
        if row["completed_at"]:
            status_icon = "completed"
        elif row["started_at"]:
            status_icon = "active"
        
        datasets.append({
            "dataset_label": row["dataset_label"],
            "status": status_icon,
            "completed": completed,
            "total": row["tasks_total"]
        })
    
    return {"model_name": resolved_model, "datasets": datasets}


def _build_tasks_pills(conn: sqlite3.Connection, run_id: str, current_model: str | None, current_dataset: str | None) -> dict[str, Any]:
    """Build task pills list for current dataset."""
    if not current_dataset:
        return {"tasks": []}
    
    cur = conn.cursor()
    
    # Get all tasks for current dataset
    cur.execute("""
        SELECT 
            task_label,
            status,
            started_at,
            completed_at,
            error
        FROM benchmark_run_tasks
        WHERE run_id = ? AND model_name = ? AND dataset_name LIKE ?
        ORDER BY task_index
    """, (run_id, current_model or "", f"%{current_dataset}%"))
    
    tasks = []
    for row in cur.fetchall():
        status_icon = "pending"
        if row["error"] and row["completed_at"]:
            status_icon = "failed"
        elif row["completed_at"]:
            status_icon = "completed"
        elif row["started_at"]:
            status_icon = "active"
        
        tasks.append({
            "label": row["task_label"],
            "status": status_icon
        })
    
    return {"tasks": tasks}


@benchmark_bp.get("/api/benchmark/status")
def benchmark_status():
    """Return summary info for the latest (or requested) benchmark run."""
    run_id = (request.args.get("run_id") or "").strip()
    db_path = _db_path()
    if not os.path.exists(db_path):
        return jsonify({"ok": False, "error": "benchmark_db_missing", "db_path": db_path}), 404

    with _connect_db() as conn:
        cur = conn.cursor()
        if not run_id:
            row = _select_default_run(cur)
            if not row:
                return jsonify({"ok": False, "error": "no_runs_found"}), 404
            run_id = row["run_id"]
            run_info = _row_to_dict(row)
        else:
            cur.execute(
                "SELECT run_id, started_at, completed_at, status "
                "FROM benchmark_runs WHERE run_id = ? LIMIT 1",
                (run_id,),
            )
            run_info = _row_to_dict(cur.fetchone())
            if not run_info:
                return jsonify({"ok": False, "error": "run_not_found", "run_id": run_id}), 404

        cur.execute(
            "SELECT COUNT(*) AS models_total "
            "FROM benchmark_run_models WHERE run_id = ?",
            (run_id,),
        )
        models_total = int(cur.fetchone()["models_total"] or 0)

        cur.execute(
            "SELECT COUNT(*) AS tasks_total "
            "FROM benchmark_run_tasks WHERE run_id = ?",
            (run_id,),
        )
        tasks_total = int(cur.fetchone()["tasks_total"] or 0)

        cur.execute(
            "SELECT COUNT(*) AS tasks_completed "
            "FROM benchmark_run_tasks WHERE run_id = ? AND completed_at IS NOT NULL",
            (run_id,),
        )
        tasks_completed = int(cur.fetchone()["tasks_completed"] or 0)

        cur.execute(
            "SELECT COUNT(*) AS samples_total, "
            "SUM(CASE WHEN correct = 1 THEN 1 ELSE 0 END) AS samples_correct, "
            "SUM(CASE WHEN error IS NOT NULL AND error != '' THEN 1 ELSE 0 END) AS samples_error "
            "FROM benchmark_samples WHERE run_id = ?",
            (run_id,),
        )
        sample_counts = cur.fetchone()
        samples_total = int(sample_counts["samples_total"] or 0)
        samples_correct = int(sample_counts["samples_correct"] or 0)
        samples_error = int(sample_counts["samples_error"] or 0)

        cur.execute(
            "SELECT COUNT(*) AS chat_total, "
            "SUM(CASE WHEN error IS NOT NULL AND error != '' THEN 1 ELSE 0 END) AS chat_error "
            "FROM chat_turns WHERE run_id = ?",
            (run_id,),
        )
        chat_counts = cur.fetchone()
        chat_total = int(chat_counts["chat_total"] or 0)
        chat_error = int(chat_counts["chat_error"] or 0)

        cur.execute(
            "SELECT model_name, task_name, sample_id, created_at, error "
            "FROM benchmark_samples WHERE run_id = ? ORDER BY id DESC LIMIT 1",
            (run_id,),
        )
        last_sample = _row_to_dict(cur.fetchone())

        cur.execute(
            "SELECT model_name, dataset, dialog_id, turn_index, created_at, error "
            "FROM chat_turns WHERE run_id = ? ORDER BY id DESC LIMIT 1",
            (run_id,),
        )
        last_chat = _row_to_dict(cur.fetchone())

        cur.execute(
            "SELECT * FROM benchmark_run_scope WHERE run_id = ? ORDER BY id DESC LIMIT 1",
            (run_id,),
        )
        scope_row = _row_to_dict(cur.fetchone())
        if scope_row:
            scope_row["models"] = _safe_json(scope_row.get("models_json"))
            scope_row["datasets"] = _safe_json(scope_row.get("datasets_json"))

        cur.execute(
            "SELECT * FROM benchmark_run_state WHERE run_id = ? LIMIT 1",
            (run_id,),
        )
        state_row = _row_to_dict(cur.fetchone())
        if state_row:
            state_row["last_update"] = state_row.pop("updated_at", None)
            for key in (
                "recent_metrics_json",
                "recent_gpu_json",
                "recent_gpu_temp_json",
                "recent_cpu_json",
                "recent_disk_io_json",
                "last_request_json",
            ):
                parsed = _safe_json(state_row.pop(key, None))
                if key == "recent_metrics_json":
                    state_row["recent_metrics"] = parsed or []
                elif key == "recent_gpu_json":
                    state_row["recent_gpu"] = parsed or []
                elif key == "recent_gpu_temp_json":
                    state_row["recent_gpu_temp"] = parsed or []
                elif key == "recent_cpu_json":
                    state_row["recent_cpu"] = parsed or []
                elif key == "recent_disk_io_json":
                    state_row["recent_disk_io"] = parsed or []
                elif key == "last_request_json":
                    state_row["last_request"] = parsed or None
                elif key == "recent_cpu_json":
                    state_row["recent_cpu"] = parsed or []
                elif key == "recent_disk_io_json":
                    state_row["recent_disk_io"] = parsed or []
            if run_info and run_info.get("started_at"):
                state_row.setdefault("start_time", run_info.get("started_at"))

        # Build new Live KPIs sections (inside with block to keep conn alive)
        server_status = _build_server_status(state_row, conn, run_id)
        dashboard_status = _build_dashboard_status(state_row, conn, run_id)
        task_details = _build_task_details(state_row)
        workflow_status = _build_workflow_status(state_row)
        datasets_pills = _build_datasets_pills(conn, run_id, state_row.get("current_model") if state_row else None)
        tasks_pills = _build_tasks_pills(conn, run_id, 
                                         state_row.get("current_model") if state_row else None,
                                         state_row.get("current_dataset") if state_row else None)

    # Optional diagnostics: avoid spawning `lsof` on every poll by default.
    include_fd = (request.args.get("fd") or "").strip().lower() in ("1", "true", "yes")
    fd_info = _get_file_descriptor_count() if include_fd else None

    payload = {
        "ok": True,
        "run": run_info,
        "scope": scope_row,
        "state": state_row,
        "file_descriptors": fd_info,
        "counts": {
            "models_total": models_total,
            "tasks_total": tasks_total,
            "tasks_completed": tasks_completed,
            "samples_total": samples_total,
            "samples_correct": samples_correct,
            "samples_error": samples_error,
            "chat_turns_total": chat_total,
            "chat_turns_error": chat_error,
        },
        "latest": {
            "sample": last_sample,
            "chat_turn": last_chat,
        },
        # New Live KPIs sections
        "server": server_status,
        "dashboard": dashboard_status,
        "task": task_details,
        "workflow": workflow_status,
        "datasets_overview": datasets_pills,
        "current_dataset_tasks": tasks_pills,
    }
    return jsonify(payload)


@benchmark_bp.get("/api/benchmark/datasets")
def benchmark_datasets():
    """Return dataset-level details and per-task status for a run."""
    run_id = (request.args.get("run_id") or "").strip()
    model_filter = (request.args.get("model") or "").strip()
    dataset_filter = (request.args.get("dataset") or "").strip().lower()
    since = _normalize_since((request.args.get("since") or "").strip())
    db_path = _db_path()
    if not os.path.exists(db_path):
        return jsonify({"ok": False, "error": "benchmark_db_missing", "db_path": db_path}), 404

    with _connect_db() as conn:
        cur = conn.cursor()
        if not run_id:
            row = _select_default_run(cur)
            if not row:
                return jsonify({"ok": False, "error": "no_runs_found"}), 404
            run_id = row["run_id"]
            run_info = _row_to_dict(row)
        else:
            cur.execute(
                "SELECT run_id, started_at, completed_at, status "
                "FROM benchmark_runs WHERE run_id = ? LIMIT 1",
                (run_id,),
            )
            run_info = _row_to_dict(cur.fetchone())
            if not run_info:
                return jsonify({"ok": False, "error": "run_not_found", "run_id": run_id}), 404

        cur.execute(
            "SELECT * FROM benchmark_run_scope WHERE run_id = ? ORDER BY id DESC LIMIT 1",
            (run_id,),
        )
        scope_row = _row_to_dict(cur.fetchone())
        scope_models = _safe_json(scope_row.get("models_json")) if scope_row else None
        scope_datasets = _safe_json(scope_row.get("datasets_json")) if scope_row else None

        params = [run_id]
        model_clause = ""
        if model_filter:
            model_clause = " AND model_name = ?"
            params.append(model_filter)
        since_clause = ""
        if since:
            since_clause = " AND datetime(COALESCE(completed_at, started_at)) >= datetime(?)"

        cur.execute(
            "SELECT * FROM benchmark_run_datasets WHERE run_id = ?"
            + model_clause
            + " ORDER BY model_name, dataset_index",
            params,
        )
        run_dataset_rows = cur.fetchall()

        cur.execute(
            "SELECT * FROM benchmark_run_tasks WHERE run_id = ?"
            + model_clause
            + " ORDER BY model_name, dataset_name, task_index",
            params,
        )
        run_task_rows = cur.fetchall()

        cur.execute(
            "SELECT * FROM benchmark_tasks WHERE run_id = ?"
            + model_clause
            + since_clause
            + " ORDER BY model_name, task_name",
            params + ([since] if since else []),
        )
        task_rows = cur.fetchall()
        task_map = {
            (row["model_name"], row["task_name"]): _row_to_dict(row)
            for row in task_rows
        }

        dialog_since_clause = ""
        if since:
            dialog_since_clause = " AND datetime(COALESCE(completed_at, started_at)) >= datetime(?)"
        cur.execute(
            "SELECT * FROM chat_dialogs WHERE run_id = ?"
            + model_clause
            + dialog_since_clause
            + " ORDER BY model_name, dataset, id",
            params + ([since] if since else []),
        )
        dialog_rows = cur.fetchall()

        # Get aggregate metrics from samples for datasets that don't have task-level aggregates
        cur.execute(
            "SELECT model_name, task_name, "
            "AVG(ttft_ms) as avg_ttft_ms, "
            "AVG(tokens_per_sec) as avg_tokens_per_sec, "
            "AVG(total_time_ms) as avg_total_time_ms, "
            "COUNT(*) as sample_count "
            "FROM benchmark_samples "
            "WHERE run_id = ?"
            + model_clause
            + " AND ttft_ms IS NOT NULL "
            "GROUP BY model_name, task_name",
            params,
        )
        sample_agg_rows = cur.fetchall()
        sample_agg_map = {
            (row["model_name"], row["task_name"]): _row_to_dict(row)
            for row in sample_agg_rows
        }

    datasets: list[dict[str, Any]] = []
    dataset_map: dict[tuple[str, str], dict[str, Any]] = {}
    label_map: dict[tuple[str, str], dict[str, Any]] = {}

    for row in run_dataset_rows:
        entry = {
            "type": row["dataset_kind"] or "shortform",
            "model_name": row["model_name"],
            "dataset_id": row["dataset_name"],
            "dataset_label": row["dataset_label"] or row["dataset_name"],
            "completed": row["status"] == "completed",
            "tasks": [],
            "tasks_total": row["tasks_total"] or 0,
            "tasks_passed": 0,
            "tasks_failed": 0,
            "aggregate": {},
        }
        datasets.append(entry)
        dataset_map[(row["model_name"], row["dataset_name"])] = entry
        label_key = _normalize_dataset_label(entry.get("dataset_label") or entry.get("dataset_id"))
        if label_key:
            label_map[(row["model_name"], label_key)] = entry

    for key, task_meta in task_map.items():
        model_name, task_name = key
        entry = dataset_map.get(key)
        if entry is None:
            entry = {
                "type": "shortform",
                "model_name": model_name,
                "dataset_id": task_name,
                "dataset_label": task_name,
                "completed": bool(task_meta.get("completed_at")),
                "tasks": [],
                "tasks_total": 0,
                "tasks_passed": 0,
                "tasks_failed": 0,
                "aggregate": {},
            }
            datasets.append(entry)
            dataset_map[key] = entry
            label_key = _normalize_dataset_label(entry.get("dataset_label") or entry.get("dataset_id"))
            if label_key:
                label_map[(model_name, label_key)] = entry
        entry["aggregate"] = {
            "category": task_meta.get("category"),
            "samples_total": task_meta.get("samples_total"),
            "samples_correct": task_meta.get("samples_correct"),
            "accuracy": task_meta.get("accuracy"),
            "avg_ttft_ms": task_meta.get("avg_ttft_ms"),
            "avg_tokens_per_sec": task_meta.get("avg_tokens_per_sec"),
            "avg_total_time_ms": task_meta.get("avg_total_time_ms"),
            "disk_io_avg_mbps": task_meta.get("disk_io_avg_mbps"),
            "disk_io_max_mbps": task_meta.get("disk_io_max_mbps"),
            "gpu_util_avg": task_meta.get("gpu_util_avg"),
            "gpu_util_max": task_meta.get("gpu_util_max"),
            "gpu_temp_avg": task_meta.get("gpu_temp_avg"),
            "gpu_temp_max": task_meta.get("gpu_temp_max"),
            "cpu_util_avg": task_meta.get("cpu_util_avg"),
            "cpu_util_max": task_meta.get("cpu_util_max"),
            "started_at": task_meta.get("started_at"),
            "completed_at": task_meta.get("completed_at"),
        }
        if task_meta.get("completed_at"):
            entry["completed"] = True

    chat_agg: dict[tuple[str, str], dict[str, Any]] = {}
    dialog_counts: dict[tuple[str, str], int] = {}
    for row in dialog_rows:
        key = (row["model_name"], row["dataset"])
        label_key = _normalize_dataset_label(row["dataset"])
        entry = dataset_map.get(key)
        if entry is None and label_key:
            entry = label_map.get((row["model_name"], label_key))
        if entry is None:
            label = _normalize_dataset_label(row["dataset"])
            entry = {
                "type": "chat",
                "model_name": row["model_name"],
                "dataset_id": row["dataset"],
                "dataset_label": label or row["dataset"],
                "completed": bool(row["completed_at"]),
                "tasks": [],
                "tasks_total": 0,
                "tasks_passed": 0,
                "tasks_failed": 0,
                "aggregate": {},
            }
            datasets.append(entry)
            dataset_map[key] = entry
            if label:
                label_map[(row["model_name"], label)] = entry
        agg = chat_agg.get(key)
        if agg is None:
            agg = {
                "turns_total": 0,
                "turns_compliant": 0,
                "session_compliant": 0,
                "avg_ttft_ms": 0,
                "p95_ttft_ms": 0,
                "avg_tokens_per_sec": 0,
                "jitter_ms": 0,
                "late_turn_recall": 0,
                "disk_io_avg_mbps": 0,
                "gpu_util_avg": 0,
                "gpu_temp_avg": 0,
                "cpu_util_avg": 0,
            }
            chat_agg[key] = agg
        agg["turns_total"] += row["turns_total"] or 0
        agg["turns_compliant"] += row["turns_compliant"] or 0
        agg["session_compliant"] += row["session_compliant"] or 0
        agg["avg_ttft_ms"] += row["avg_ttft_ms"] or 0
        agg["p95_ttft_ms"] += row["p95_ttft_ms"] or 0
        agg["avg_tokens_per_sec"] += row["avg_tokens_per_sec"] or 0
        agg["jitter_ms"] += row["jitter_ms"] or 0
        agg["late_turn_recall"] += row["late_turn_recall"] or 0
        agg["disk_io_avg_mbps"] += _row_value(row, "disk_io_avg_mbps") or 0
        agg["gpu_util_avg"] += _row_value(row, "gpu_util_avg") or 0
        agg["gpu_temp_avg"] += _row_value(row, "gpu_temp_avg") or 0
        agg["cpu_util_avg"] += _row_value(row, "cpu_util_avg") or 0
        dialog_counts[key] = dialog_counts.get(key, 0) + 1

    for key, agg in chat_agg.items():
        entry = dataset_map.get(key)
        count = dialog_counts.get(key, 0)
        if entry and count > 0:
            for metric_key in (
                "avg_ttft_ms",
                "p95_ttft_ms",
                "avg_tokens_per_sec",
                "jitter_ms",
                "late_turn_recall",
                "disk_io_avg_mbps",
                "gpu_util_avg",
                "gpu_temp_avg",
                "cpu_util_avg",
            ):
                agg[metric_key] = agg[metric_key] / count
            entry["aggregate"] = agg

    for row in run_task_rows:
        key = (row["model_name"], row["dataset_name"])
        entry = dataset_map.get(key)
        if entry is None:
            entry = {
                "type": row["task_kind"] or "shortform",
                "model_name": row["model_name"],
                "dataset_id": row["dataset_name"],
                "dataset_label": row["dataset_name"],
                "completed": False,
                "tasks": [],
                "tasks_total": 0,
                "tasks_passed": 0,
                "tasks_failed": 0,
                "aggregate": {},
            }
            datasets.append(entry)
            dataset_map[key] = entry
        raw_status = row["status"] or "pending"
        if raw_status == "passed":
            status = "pass"
        elif raw_status == "failed":
            status = "fail"
        elif raw_status == "error":
            status = "error"
        else:
            status = raw_status
        task_entry = {
            "task_name": row["task_name"],
            "task_label": row["task_label"],
            "sample_id": row["sample_id"],
            "dialog_id": row["dialog_id"],
            "turn_index": row["turn_index"],
            "status": status,
            "error": row["error"],
            "created_at": row["created_at"],
        }
        entry["tasks"].append(task_entry)
        entry["tasks_total"] = max(int(entry.get("tasks_total") or 0), len(entry["tasks"]))
        if status == "pass":
            entry["tasks_passed"] += 1
        elif status in ("fail", "error"):
            entry["tasks_failed"] += 1

    # Populate aggregate metrics from samples for datasets without aggregates
    for entry in datasets:
        if not entry.get("aggregate") or not any(entry["aggregate"].values()):
            # No aggregate data - try to get from sample aggregations
            key = (entry["model_name"], entry["dataset_id"])
            sample_agg = sample_agg_map.get(key)
            if sample_agg and sample_agg.get("sample_count", 0) > 0:
                entry["aggregate"] = {
                    "avg_ttft_ms": sample_agg.get("avg_ttft_ms"),
                    "avg_tokens_per_sec": sample_agg.get("avg_tokens_per_sec"),
                    "avg_total_time_ms": sample_agg.get("avg_total_time_ms"),
                }

    if dataset_filter:
        filtered = []
        for entry in datasets:
            label = (entry.get("dataset_label") or "").lower()
            dataset_id = (entry.get("dataset_id") or "").lower()
            if dataset_filter in (label, dataset_id):
                filtered.append(entry)
        datasets = filtered

    return jsonify({"ok": True, "run": run_info, "datasets": datasets})


@benchmark_bp.get("/api/benchmark/last_task")
def benchmark_last_task():
    """Return the most recently executed task sample or chat turn."""
    run_id = (request.args.get("run_id") or "").strip()
    since = _normalize_since((request.args.get("since") or "").strip())
    db_path = _db_path()
    if not os.path.exists(db_path):
        return jsonify({"ok": False, "error": "benchmark_db_missing", "db_path": db_path}), 404

    with _connect_db() as conn:
        cur = conn.cursor()
        if not run_id:
            row = _select_default_run(cur)
            if not row:
                return jsonify({"ok": False, "error": "no_runs_found"}), 404
            run_id = row["run_id"]
            run_info = _row_to_dict(row)
        else:
            cur.execute(
                "SELECT run_id, started_at, completed_at, status "
                "FROM benchmark_runs WHERE run_id = ? LIMIT 1",
                (run_id,),
            )
            run_info = _row_to_dict(cur.fetchone())
            if not run_info:
                return jsonify({"ok": False, "error": "run_not_found", "run_id": run_id}), 404

        task_params = [run_id]
        task_since_clause = ""
        if since:
            task_since_clause = " AND datetime(completed_at) >= datetime(?)"
            task_params.append(since)
        cur.execute(
            "SELECT * FROM benchmark_run_tasks WHERE run_id = ? AND completed_at IS NOT NULL"
            + task_since_clause
            + " ORDER BY completed_at DESC, id DESC LIMIT 1",
            task_params,
        )
        last_task = cur.fetchone()

        if not last_task:
            payload = None
        else:
            task_kind = last_task["task_kind"] or "shortform"
            raw_status = last_task["status"] or "pending"
            status = "pass" if raw_status == "passed" else ("fail" if raw_status == "failed" else raw_status)
            if raw_status == "error":
                status = "error"

            if task_kind == "shortform":
                cur.execute(
                    "SELECT * FROM benchmark_samples WHERE run_id = ? AND model_name = ? AND task_name = ? AND sample_id = ? "
                    "ORDER BY created_at DESC, id DESC LIMIT 1",
                    (run_id, last_task["model_name"], last_task["dataset_name"], last_task["sample_id"]),
                )
                row = cur.fetchone()
                if not row:
                    payload = None
                else:
                    cur.execute(
                        "SELECT * FROM benchmark_tasks "
                        "WHERE run_id = ? AND model_name = ? AND task_name = ? "
                        "ORDER BY id DESC LIMIT 1",
                        (run_id, row["model_name"], row["task_name"]),
                    )
                    task_meta = _row_to_dict(cur.fetchone()) or {}
                    payload = {
                        "kind": "shortform",
                        "model_name": row["model_name"],
                        "task_name": row["task_name"],
                        "dataset_label": row["task_name"],
                        "sample_id": row["sample_id"],
                        "created_at": row["created_at"],
                        "status": status,
                        "error": row["error"],
                        "metrics": {
                            "ttft_ms": row["ttft_ms"],
                            "tokens_per_sec": row["tokens_per_sec"],
                            "total_time_ms": row["total_time_ms"],
                            "input_tokens": row["input_tokens"],
                            "output_tokens": row["output_tokens"],
                            "timeout_killed": _row_value(row, "timeout_killed"),
                            "timeout_type": _row_value(row, "timeout_type"),
                            "timeout_reason": _row_value(row, "timeout_reason"),
                            "tokens_before_timeout": _row_value(row, "tokens_before_timeout"),
                        },
                        "evaluation": {"correct": row["correct"]},
                        "payload": {
                            "prompt_preview": _preview(row["prompt"]),
                            "prompt": row["prompt"],  # Full prompt
                            "expected_preview": _preview(row["expected"]),
                            "response_preview": _preview(row["response"]),
                            "response": row["response"],  # Full response
                            "prompt_len": len(row["prompt"] or ""),
                            "expected_len": len(row["expected"] or ""),
                            "response_len": len(row["response"] or ""),
                        },
                        "aggregate": {
                            "category": task_meta.get("category"),
                            "samples_total": task_meta.get("samples_total"),
                            "samples_correct": task_meta.get("samples_correct"),
                            "accuracy": task_meta.get("accuracy"),
                            "avg_ttft_ms": task_meta.get("avg_ttft_ms"),
                            "avg_tokens_per_sec": task_meta.get("avg_tokens_per_sec"),
                            "avg_total_time_ms": task_meta.get("avg_total_time_ms"),
                            "disk_io_avg_mbps": task_meta.get("disk_io_avg_mbps"),
                            "disk_io_max_mbps": task_meta.get("disk_io_max_mbps"),
                            "gpu_util_avg": task_meta.get("gpu_util_avg"),
                            "gpu_util_max": task_meta.get("gpu_util_max"),
                            "gpu_temp_avg": task_meta.get("gpu_temp_avg"),
                            "gpu_temp_max": task_meta.get("gpu_temp_max"),
                            "cpu_util_avg": task_meta.get("cpu_util_avg"),
                            "cpu_util_max": task_meta.get("cpu_util_max"),
                            "started_at": task_meta.get("started_at"),
                            "completed_at": task_meta.get("completed_at"),
                        },
                    }
            else:
                cur.execute(
                    "SELECT * FROM chat_turns WHERE run_id = ? AND model_name = ? AND dataset = ? AND dialog_id = ? AND turn_index = ? "
                    "ORDER BY created_at DESC, id DESC LIMIT 1",
                    (run_id, last_task["model_name"], last_task["dataset_name"], last_task["dialog_id"], last_task["turn_index"]),
                )
                row = cur.fetchone()
                if not row:
                    cur.execute(
                        "SELECT * FROM chat_turns WHERE run_id = ? AND model_name = ? AND dialog_id = ? AND turn_index = ? "
                        "ORDER BY created_at DESC, id DESC LIMIT 1",
                        (run_id, last_task["model_name"], last_task["dialog_id"], last_task["turn_index"]),
                    )
                    row = cur.fetchone()
                if not row:
                    payload = None
                else:
                    compliance = _safe_json(row["compliance_json"])
                    violations = _list_from_json(row["violations"])
                    cur.execute(
                        "SELECT * FROM chat_dialogs "
                        "WHERE run_id = ? AND model_name = ? AND dataset = ? AND dialog_id = ? "
                        "ORDER BY id DESC LIMIT 1",
                        (run_id, row["model_name"], row["dataset"], row["dialog_id"]),
                    )
                    dialog_meta = _row_to_dict(cur.fetchone()) or {}
                    dataset_label = _normalize_dataset_label(row["dataset"]) or row["dataset"]
                    payload = {
                        "kind": "chat",
                        "model_name": row["model_name"],
                        "dataset_id": row["dataset"],
                        "dataset_label": dataset_label,
                        "dialog_id": row["dialog_id"],
                        "turn_index": row["turn_index"],
                        "created_at": row["created_at"],
                        "status": status,
                        "error": row["error"],
                        "metrics": {
                            "ttft_ms": row["ttft_ms"],
                            "tokens_per_sec": row["tokens_per_sec"],
                            "total_time_ms": row["total_time_ms"],
                            "input_tokens": row["input_tokens"],
                            "output_tokens": row["output_tokens"],
                            "jitter_ms": _row_value(row, "jitter_ms"),
                            "timeout_killed": _row_value(row, "timeout_killed"),
                            "timeout_type": _row_value(row, "timeout_type"),
                            "timeout_reason": _row_value(row, "timeout_reason"),
                            "tokens_before_timeout": _row_value(row, "tokens_before_timeout"),
                        },
                        "evaluation": {
                            "compliance": compliance,
                            "violations": violations,
                        },
                        "payload": {
                            "user_preview": _preview(row["user_text"]),
                            "user_text": row["user_text"],  # Full user text
                            "response_preview": _preview(row["response"]),
                            "response": row["response"],  # Full response
                            "user_len": len(row["user_text"] or ""),
                            "response_len": len(row["response"] or ""),
                        },
                        "aggregate": {
                            "turns_total": dialog_meta.get("turns_total"),
                            "turns_compliant": dialog_meta.get("turns_compliant"),
                            "session_compliant": dialog_meta.get("session_compliant"),
                            "avg_ttft_ms": dialog_meta.get("avg_ttft_ms"),
                            "p95_ttft_ms": dialog_meta.get("p95_ttft_ms"),
                            "avg_tokens_per_sec": dialog_meta.get("avg_tokens_per_sec"),
                            "jitter_ms": dialog_meta.get("jitter_ms"),
                            "late_turn_recall": dialog_meta.get("late_turn_recall"),
                            "disk_io_avg_mbps": dialog_meta.get("disk_io_avg_mbps"),
                            "gpu_util_avg": dialog_meta.get("gpu_util_avg"),
                            "gpu_temp_avg": dialog_meta.get("gpu_temp_avg"),
                            "cpu_util_avg": dialog_meta.get("cpu_util_avg"),
                            "started_at": dialog_meta.get("started_at"),
                            "completed_at": dialog_meta.get("completed_at"),
                        },
                    }

    return jsonify({"ok": True, "run": run_info, "task": payload})
