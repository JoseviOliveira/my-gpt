import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class DBMixin:
    @staticmethod
    def _clean_text_for_db(value: Any) -> Optional[str]:
        """Normalize noisy text artifacts for auxiliary searchable columns."""
        if value is None:
            return None
        text = str(value).replace("\r\n", "\n").replace("\r", "\n")
        text = text.strip()
        if not text:
            return ""
        text = " ".join(text.split())
        text = re.sub(r"^(answer|final answer)\s*:\s*", "", text, flags=re.IGNORECASE)
        return text

    def _rerun_error_tasks_enabled(self) -> bool:
        """Whether resume should rerun tasks containing error outputs."""
        return os.environ.get("BENCHMARK_RERUN_ERROR_TASKS", "1").lower() in (
            "1",
            "true",
            "yes",
        )

    def _init_database(self):
        """Initialize database with schema."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
    
        # Load and execute schema
        schema_path = Path(__file__).parent / 'db_schema.sql'
        with open(schema_path) as f:
            schema_sql = f.read()
    
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(schema_sql)
            self._ensure_columns(conn)
    
        logger.info(f"Database initialized at {self.db_path}")

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        """Ensure new resource columns exist for existing databases."""
        def add_columns(table: str, columns: Dict[str, str]) -> None:
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, col_type in columns.items():
                if name in existing:
                    continue
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")

        task_columns = {
            "disk_io_avg_mbps": "REAL",
            "disk_io_max_mbps": "REAL",
            "disk_metrics_available": "INTEGER",
            "gpu_util_avg": "REAL",
            "gpu_util_max": "REAL",
            "gpu_metrics_available": "INTEGER",
            "gpu_temp_avg": "REAL",
            "gpu_temp_max": "REAL",
            "cpu_util_avg": "REAL",
            "cpu_util_max": "REAL",
            "cpu_metrics_available": "INTEGER",
        }
        add_columns("benchmark_tasks", task_columns)

        chat_dialog_columns = {
            "disk_io_avg_mbps": "REAL",
            "disk_io_max_mbps": "REAL",
            "disk_metrics_available": "INTEGER",
            "gpu_util_avg": "REAL",
            "gpu_util_max": "REAL",
            "gpu_metrics_available": "INTEGER",
            "gpu_temp_avg": "REAL",
            "gpu_temp_max": "REAL",
            "cpu_util_avg": "REAL",
            "cpu_util_max": "REAL",
            "cpu_metrics_available": "INTEGER",
        }
        add_columns("chat_dialogs", chat_dialog_columns)

        sample_columns = {
            "request_payload": "TEXT",
            "timeout_killed": "INTEGER DEFAULT 0",
            "timeout_type": "TEXT",
            "timeout_reason": "TEXT",
            "tokens_before_timeout": "INTEGER",
            "prompt_clean": "TEXT",
            "expected_clean": "TEXT",
            "response_clean": "TEXT",
        }
        add_columns("benchmark_samples", sample_columns)

        turn_columns = {
            "request_payload": "TEXT",
            "timeout_killed": "INTEGER DEFAULT 0",
            "timeout_type": "TEXT",
            "timeout_reason": "TEXT",
            "tokens_before_timeout": "INTEGER",
            "user_text_clean": "TEXT",
            "response_clean": "TEXT",
        }
        add_columns("chat_turns", turn_columns)

        run_state_columns = {
            "current_sample_id": "TEXT",
            "current_dialog_id": "TEXT",
            "current_dataset": "TEXT",
            "current_task_label": "TEXT",
            "recent_metrics_json": "TEXT",
            "recent_gpu_json": "TEXT",
            "recent_gpu_temp_json": "TEXT",
            "recent_cpu_json": "TEXT",
            "recent_disk_io_json": "TEXT",
        }
        add_columns("benchmark_run_state", run_state_columns)

        dataset_columns = {
            "dataset_kind": "TEXT",
        }
        add_columns("benchmark_run_datasets", dataset_columns)

    def _has_missing_telemetry(self, run_id: Optional[str]) -> bool:
        """Return True if any task/dialog is missing telemetry or outputs."""
        if not run_id:
            return False
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT 1 FROM benchmark_tasks
                    WHERE run_id=? AND completed_at IS NOT NULL
                      AND (gpu_metrics_available=0 OR disk_metrics_available=0 OR cpu_metrics_available=0)
                    LIMIT 1
                """, (run_id,))
                if cursor.fetchone() is not None:
                    return True
                cursor.execute("""
                    SELECT 1 FROM chat_dialogs
                    WHERE run_id=? AND completed_at IS NOT NULL
                      AND (gpu_metrics_available=0 OR disk_metrics_available=0 OR cpu_metrics_available=0)
                    LIMIT 1
                """, (run_id,))
                if cursor.fetchone() is not None:
                    return True
                cursor.execute("""
                    SELECT 1 FROM benchmark_samples
                    WHERE run_id=?
                      AND (response IS NULL OR length(response)=0 OR response LIKE '[error]%')
                    LIMIT 1
                """, (run_id,))
                if cursor.fetchone() is not None:
                    return True
                cursor.execute("""
                    SELECT 1 FROM chat_turns
                    WHERE run_id=?
                      AND (response IS NULL OR length(response)=0 OR response LIKE '[error]%')
                    LIMIT 1
                """, (run_id,))
                return cursor.fetchone() is not None
        except Exception:
            return False

    def _get_run_started_at(self) -> Optional[str]:
        """Fetch original started_at from the database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT started_at FROM benchmark_runs WHERE run_id=?
                """, (self.run_id,))
                row = cursor.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def _record_run_scope(self) -> None:
        """Persist run scope metadata derived from config."""
        scope = self._run_scope or {}
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO benchmark_run_scope
                    (run_id, config_path, config_name, light_mode, models_json, datasets_json,
                     models_total, datasets_total, tasks_total)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    self.run_id,
                    scope.get("config_path"),
                    scope.get("config_name"),
                    scope.get("light_mode", 0),
                    json.dumps(scope.get("models") or []),
                    json.dumps(scope.get("datasets") or []),
                    scope.get("models_total"),
                    scope.get("datasets_total"),
                    scope.get("tasks_total"),
                ))
                self._record_run_scope_tables(conn, scope)
        except Exception as exc:
            logger.warning("Failed to persist run scope: %s", exc)

    def _record_run_scope_tables(self, conn: sqlite3.Connection, scope: dict) -> None:
        """Persist run scope models/datasets/tasks tables."""
        models = scope.get("models") or []
        datasets = scope.get("datasets") or []
        tasks = scope.get("tasks") or []
        if not models and not datasets and not tasks:
            return

        conn.execute("DELETE FROM benchmark_run_models WHERE run_id = ?", (self.run_id,))
        conn.execute("DELETE FROM benchmark_run_datasets WHERE run_id = ?", (self.run_id,))
        conn.execute("DELETE FROM benchmark_run_tasks WHERE run_id = ?", (self.run_id,))

        for model in models:
            conn.execute("""
                INSERT INTO benchmark_run_models
                (run_id, model_name, model_index, status)
                VALUES (?, ?, ?, 'pending')
            """, (self.run_id, model.get("name"), model.get("index")))

        for dataset in datasets:
            conn.execute("""
                INSERT INTO benchmark_run_datasets
                (run_id, model_name, dataset_name, dataset_label, dataset_kind, dataset_index, tasks_total, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            """, (
                self.run_id,
                dataset.get("model_name"),
                dataset.get("dataset_name"),
                dataset.get("dataset_label"),
                dataset.get("type"),
                dataset.get("dataset_index"),
                dataset.get("tasks_total", 0),
            ))

        for task in tasks:
            conn.execute("""
                INSERT OR IGNORE INTO benchmark_run_tasks
                (run_id, model_name, dataset_name, task_name, task_label, task_index, task_kind,
                 sample_id, dialog_id, turn_index, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """, (
                self.run_id,
                task.get("model_name"),
                task.get("dataset_name"),
                task.get("task_name"),
                task.get("task_label"),
                task.get("task_index"),
                task.get("task_kind"),
                task.get("sample_id"),
                task.get("dialog_id"),
                task.get("turn_index"),
            ))

    def _write_run_state(self) -> None:
        """Persist live run state counters in the database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO benchmark_run_state
                    (run_id, status, updated_at, current_model, current_dataset, current_task, current_task_label,
                     current_sample_id, current_dialog_id, model_index, dataset_index, task_index, tasks_total,
                     progress_percent, models_total, datasets_total, models_completed, light_mode,
                     recent_metrics_json, recent_gpu_json, recent_gpu_temp_json, recent_cpu_json, recent_disk_io_json,
                     last_request_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    self.run_id,
                    self.state.get("status"),
                    self.state.get("last_update"),
                    self.state.get("current_model"),
                    self.state.get("current_dataset"),
                    self.state.get("current_task"),
                    self.state.get("current_task_label"),
                    self.state.get("current_sample_id"),
                    self.state.get("current_dialog_id"),
                    self.state.get("model_index"),
                    self.state.get("dataset_index"),
                    self.state.get("task_index"),
                    self.state.get("tasks_total"),
                    self.state.get("progress_percent"),
                    self.state.get("models_total"),
                    self.state.get("datasets_total"),
                    self.state.get("models_completed"),
                    int(bool(self.state.get("light_mode"))),
                    json.dumps(self.state.get("recent_metrics") or []),
                    json.dumps(self.state.get("recent_gpu") or []),
                    json.dumps(self.state.get("recent_gpu_temp") or []),
                    json.dumps(self.state.get("recent_cpu") or []),
                    json.dumps(self.state.get("recent_disk_io") or []),
                    json.dumps(self.state.get("last_request") or {}),
                ))
        except Exception as exc:
            logger.warning("Failed to persist run state: %s", exc)

    def _record_run_start(self):
        """Record benchmark run metadata."""
        import platform
        import subprocess
    
        # Get Ollama version
        try:
            result = subprocess.run(['ollama', '--version'], capture_output=True, text=True)
            ollama_version = result.stdout.strip()
        except:
            ollama_version = "unknown"
    
        hardware_profile = {
            'system': platform.system(),
            'machine': platform.machine(),
            'processor': platform.processor()
        }
    
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO benchmark_runs (run_id, started_at, status, hardware_profile, ollama_version)
                VALUES (?, ?, 'running', ?, ?)
            """, (self.run_id, datetime.utcnow().isoformat(), json.dumps(hardware_profile), ollama_version))
        self._run_state_ready = True
        self._record_run_scope()
        self._write_run_state()

    def _mark_run_resumed(self) -> None:
        """Mark an existing run as running again."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE benchmark_runs
                    SET status = 'running', completed_at = NULL
                    WHERE run_id = ?
                """, (self.run_id,))
        except Exception:
            pass

    def _record_run_complete(self):
        """Mark run as completed."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE benchmark_runs
                SET completed_at = ?, status = 'completed'
                WHERE run_id = ?
            """, (datetime.utcnow().isoformat(), self.run_id))
        self.state["status"] = "completed"
        if self._run_state_ready:
            self._write_run_state()

    def _record_run_interrupted(self):
        """Mark run as interrupted (Ctrl+C or killed)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE benchmark_runs
                    SET status = 'interrupted'
                    WHERE run_id = ?
                """, (self.run_id,))
        except Exception:
            pass

    def _record_run_failed(self, error_msg: str):
        """Mark run as failed."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE benchmark_runs
                SET completed_at = ?, status = 'failed', notes = ?
                WHERE run_id = ?
            """, (datetime.utcnow().isoformat(), error_msg, self.run_id))
        self.state["status"] = "failed"
        if self._run_state_ready:
            self._write_run_state()

    def _mark_model_started(self, model_name: str, started_at: Optional[str] = None) -> None:
        ts = started_at or datetime.utcnow().isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE benchmark_run_models
                    SET status='running', started_at=?
                    WHERE run_id=? AND model_name=?
                """, (ts, self.run_id, model_name))
        except Exception:
            pass

    def _mark_model_completed(self, model_name: str, completed_at: Optional[str] = None) -> None:
        ts = completed_at or datetime.utcnow().isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE benchmark_run_models
                    SET status='completed', completed_at=?
                    WHERE run_id=? AND model_name=?
                """, (ts, self.run_id, model_name))
        except Exception:
            pass

    def _mark_dataset_started(self, model_name: str, dataset_name: str, started_at: Optional[str] = None) -> None:
        ts = started_at or datetime.utcnow().isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE benchmark_run_datasets
                    SET status='running', started_at=?
                    WHERE run_id=? AND model_name=? AND dataset_name=?
                """, (ts, self.run_id, model_name, dataset_name))
        except Exception:
            pass

    def _mark_dataset_completed(self, model_name: str, dataset_name: str, completed_at: Optional[str] = None) -> None:
        ts = completed_at or datetime.utcnow().isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE benchmark_run_datasets
                    SET status='completed', completed_at=?
                    WHERE run_id=? AND model_name=? AND dataset_name=?
                """, (ts, self.run_id, model_name, dataset_name))
        except Exception:
            pass

    def _mark_task_started(
        self,
        model_name: str,
        dataset_name: str,
        task_name: str,
        task_label: Optional[str] = None,
        started_at: Optional[str] = None,
    ) -> None:
        ts = started_at or datetime.utcnow().isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE benchmark_run_tasks
                    SET status='running', started_at=?, task_label=COALESCE(?, task_label)
                    WHERE run_id=? AND model_name=? AND dataset_name=? AND task_name=?
                """, (ts, task_label, self.run_id, model_name, dataset_name, task_name))
        except Exception:
            pass

    def _mark_task_completed(
        self,
        model_name: str,
        dataset_name: str,
        task_name: str,
        status: str,
        error: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> None:
        ts = completed_at or datetime.utcnow().isoformat()
        safe_status = status if status in ("passed", "failed", "error", "completed_no_eval") else "failed"
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE benchmark_run_tasks
                    SET status=?, completed_at=?, error=?
                    WHERE run_id=? AND model_name=? AND dataset_name=? AND task_name=?
                """, (safe_status, ts, error, self.run_id, model_name, dataset_name, task_name))
        except Exception:
            pass

    def _find_inflight_task(self) -> Optional[dict[str, Any]]:
        """Return the latest started task without completion for this run."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("""
                    SELECT *
                    FROM benchmark_run_tasks
                    WHERE run_id=? AND started_at IS NOT NULL AND completed_at IS NULL
                    ORDER BY started_at DESC LIMIT 1
                """, (self.run_id,)).fetchone()
                return {k: row[k] for k in row.keys()} if row else None
        except Exception:
            return None

    def _clear_task_results(self, model_name: str, task_name: str) -> None:
        """Remove existing task rows so reruns replace incomplete telemetry."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    DELETE FROM benchmark_samples
                    WHERE run_id=? AND model_name=? AND task_name=?
                """, (self.run_id, model_name, task_name))
                conn.execute("""
                    DELETE FROM benchmark_tasks
                    WHERE run_id=? AND model_name=? AND task_name=?
                """, (self.run_id, model_name, task_name))
                conn.execute("""
                    UPDATE benchmark_run_tasks
                    SET status='pending', started_at=NULL, completed_at=NULL, error=NULL
                    WHERE run_id=? AND model_name=? AND dataset_name=?
                """, (self.run_id, model_name, task_name))
        except Exception:
            pass

    def _clear_chat_results(self, model_name: str, dataset: str) -> None:
        """Remove existing chat rows so reruns replace incomplete telemetry."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    DELETE FROM chat_turns
                    WHERE run_id=? AND model_name=? AND dataset=?
                """, (self.run_id, model_name, dataset))
                conn.execute("""
                    DELETE FROM chat_dialogs
                    WHERE run_id=? AND model_name=? AND dataset=?
                """, (self.run_id, model_name, dataset))
                conn.execute("""
                    UPDATE benchmark_run_tasks
                    SET status='pending', started_at=NULL, completed_at=NULL, error=NULL
                    WHERE run_id=? AND model_name=? AND dataset_name=?
                """, (self.run_id, model_name, dataset))
        except Exception:
            pass

    def _task_has_missing_output(self, model_name: str, task_name: str) -> bool:
        """Return True if any sample has empty/error output for the task."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT 1 FROM benchmark_samples
                    WHERE run_id=? AND model_name=? AND task_name=?
                      AND (response IS NULL OR length(response)=0 OR response LIKE '[error]%')
                    LIMIT 1
                """, (self.run_id, model_name, task_name))
                return cursor.fetchone() is not None
        except Exception:
            return False

    def _chat_has_missing_output(self, model_name: str, dataset: str) -> bool:
        """Return True if any chat turn has empty/error output for the dataset."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT 1 FROM chat_turns
                    WHERE run_id=? AND model_name=? AND dataset=?
                      AND (response IS NULL OR length(response)=0 OR response LIKE '[error]%')
                    LIMIT 1
                """, (self.run_id, model_name, dataset))
                return cursor.fetchone() is not None
        except Exception:
            return False

    def _is_task_completed(self, model_name, task_name):
        """Check if academic task is already done (telemetry optional)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT completed_at, gpu_metrics_available, disk_metrics_available, cpu_metrics_available
                    FROM benchmark_tasks 
                    WHERE run_id=? AND model_name=? AND task_name=?
                    LIMIT 1
                """, (self.run_id, model_name, task_name))
                row = cursor.fetchone()
                if row is None:
                    return False
                completed_at, gpu_avail, disk_avail, cpu_avail = row
                if completed_at is None:
                    return False
                if self._task_has_missing_output(model_name, task_name):
                    if self._rerun_error_tasks_enabled():
                        return False
                    logger.info(
                        "Task %s has error outputs but BENCHMARK_RERUN_ERROR_TASKS=0, skipping rerun",
                        task_name,
                    )
                # Task is complete even if telemetry is incomplete
                if not (gpu_avail and disk_avail and cpu_avail):
                    logger.info(f"Task {task_name} complete but missing telemetry: gpu={gpu_avail} disk={disk_avail} cpu={cpu_avail}")
                return True
        except Exception as e:
            logger.warning(f"Error checking if task {task_name} is completed: {e}")
            return False

    def _is_chat_task_completed(self, model_name, task_config):
        """Check if chat task is already done (telemetry optional)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN completed_at IS NULL THEN 1 ELSE 0 END) as incomplete,
                        SUM(CASE WHEN gpu_metrics_available=0 OR disk_metrics_available=0 OR cpu_metrics_available=0 THEN 1 ELSE 0 END) as missing_telemetry
                    FROM chat_dialogs
                    WHERE run_id=? AND model_name=? AND dataset=?
                """, (self.run_id, model_name, task_config['name']))
                row = cursor.fetchone()
                if not row:
                    return False
                total = row[0] or 0
                incomplete = row[1] or 0
                missing_telemetry = row[2] or 0
                if total == 0:
                    return False
                if incomplete > 0:
                    return False
                # Task is complete even if some telemetry is missing
                if missing_telemetry > 0:
                    logger.info(f"Chat task {task_config['name']} complete but {missing_telemetry}/{total} dialogs missing telemetry")
                return True
        except Exception as e:
            logger.warning(f"Error checking if chat task {task_config['name']} is completed: {e}")
            return False

    def _completed_tasks_for_model(self, model_name: str) -> int:
        """Count completed tasks (academic + chat) for a model with full telemetry."""
        academic_done = 0
        for task in self.config.get('shortform_tasks', []):
            name = task.get('name')
            if name and self._is_task_completed(model_name, name):
                academic_done += 1
        chat_done = 0
        for task in self.config.get('chat_tasks', []):
            if self._is_chat_task_completed(model_name, task):
                chat_done += 1
        return academic_done + chat_done

    def _count_completed_models(self, total_tasks_per_model: int) -> int:
        """Count models that have completed every task."""
        completed = 0
        for model in self.config.get('models', []):
            model_name = model.get('name')
            if not model_name:
                continue
            if self._completed_tasks_for_model(model_name) >= total_tasks_per_model:
                completed += 1
        return completed

    def _is_model_completed(self, model_name: str, total_tasks_per_model: int) -> bool:
        """Return True when all tasks for a model are complete."""
        return self._completed_tasks_for_model(model_name) >= total_tasks_per_model

    def _record_sample(self, model_name, task_name, sample_id, prompt, expected, 
                      response, correct, metrics):
        """Record individual sample result with timeout detection."""
        # Extract timeout metadata
        timeout_killed = 1 if metrics.get('timeout_killed', False) else 0
        timeout_type = metrics.get('timeout_type')
        timeout_reason = metrics.get('timeout_reason')
        tokens_before_timeout = metrics.get('tokens_before_timeout')
    
        # Force incorrect if timed out
        if timeout_killed:
            correct = 0
    
        # Ensure expected and prompt are formatted as string for storage
        if isinstance(expected, (list, dict)):
            expected = json.dumps(expected)
        if isinstance(prompt, (list, dict)):
            prompt = json.dumps(prompt)
        prompt_clean = self._clean_text_for_db(prompt)
        expected_clean = self._clean_text_for_db(expected)
        response_clean = self._clean_text_for_db(response)
        request_payload = None
        if isinstance(metrics, dict):
            request_payload = metrics.get("request_payload")
        if isinstance(request_payload, (list, dict)):
            request_payload = json.dumps(request_payload)

        with sqlite3.connect(self.db_path) as conn:
            start_time = time.time()
            conn.execute("""
                INSERT INTO benchmark_samples
                (run_id, model_name, task_name, sample_id, request_payload, prompt, prompt_clean, expected, expected_clean, response, response_clean, correct,
                 input_tokens, output_tokens, ttft_ms, tokens_per_sec, total_time_ms,
                 timeout_killed, timeout_type, timeout_reason, tokens_before_timeout)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (self.run_id, model_name, task_name, sample_id, request_payload, prompt, prompt_clean, expected, expected_clean, response, response_clean,
                  correct, metrics.get('input_tokens'), metrics.get('output_tokens'), 
                  metrics.get('ttft_ms'), metrics.get('tokens_per_sec'), metrics.get('total_time_ms'),
                  timeout_killed, timeout_type, timeout_reason, tokens_before_timeout))
            write_time_ms = (time.time() - start_time) * 1000
            if correct is None:
                status_label = "no_eval"
            else:
                status_label = "passed" if correct else "failed"
            logger.info(
                "[DB_WRITE] benchmark_samples sample_id=%s status=%s correct=%s write_time_ms=%.1f",
                sample_id, status_label, correct, write_time_ms
            )

    def _record_chat_turn(self, model_name, dataset, dialog_id, turn_idx, 
                         user_text, response, metrics, eval_result):
        """Record chat turn with timeout detection."""
        # Extract timeout metadata
        timeout_killed = 1 if metrics.get('timeout_killed', False) else 0
        timeout_type = metrics.get('timeout_type')
        timeout_reason = metrics.get('timeout_reason')
        tokens_before_timeout = metrics.get('tokens_before_timeout')
    
        request_payload = None
        if isinstance(metrics, dict):
            request_payload = metrics.get("request_payload")
        if isinstance(request_payload, (list, dict)):
            request_payload = json.dumps(request_payload)
        user_text_clean = self._clean_text_for_db(user_text)
        response_clean = self._clean_text_for_db(response)
        with sqlite3.connect(self.db_path) as conn:
            start_time = time.time()
            conn.execute("""
                INSERT INTO chat_turns
                (run_id, model_name, dataset, dialog_id, turn_index, request_payload, user_text, user_text_clean, response, response_clean,
                 input_tokens, output_tokens, ttft_ms, tokens_per_sec, total_time_ms,
                 timeout_killed, timeout_type, timeout_reason, tokens_before_timeout,
                 compliance_json, violations)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (self.run_id, model_name, dataset, dialog_id, turn_idx, request_payload, user_text, user_text_clean, response, response_clean,
                  metrics['input_tokens'], metrics['output_tokens'], metrics['ttft_ms'],
                  metrics['tokens_per_sec'], metrics['total_time_ms'],
                  timeout_killed, timeout_type, timeout_reason, tokens_before_timeout,
                  json.dumps(eval_result['compliance']), json.dumps(eval_result['violations'])))
            write_time_ms = (time.time() - start_time) * 1000
            compliant = eval_result['compliance'].get('compliant', False)
            logger.info(
                "[DB_WRITE] chat_turns dialog_id=%s turn=%d compliant=%s violations=%d write_time_ms=%.1f",
                dialog_id, turn_idx, compliant, len(eval_result['violations']), write_time_ms
            )

    def _record_chat_dialog(self, model_name, dataset, dialog_id, turns_total, 
                           compliant_turns, ttfts, tps_values, started_at,
                           resource_final: Dict[str, Any]):
        """Record dialog aggregates.
    
        Note: Chat UX score uses turn_compliance_rate, not session_compliance.
        Formula: chat_ux = avg(turns_compliant / turns_total) across all dialogs.
        This provides better granularity than binary session pass/fail,
        especially in light mode where only hardest dialogs are tested.
        """
        import numpy as np
    
        if compliant_turns < 0:
            session_compliant = -1
        else:
            session_compliant = 1 if compliant_turns == turns_total else 0
        avg_ttft = np.mean(ttfts) if ttfts else 0
        p95_ttft = np.percentile(ttfts, 95) if ttfts else 0
        avg_tps = np.mean(tps_values) if tps_values else 0
    
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO chat_dialogs
                (run_id, model_name, dataset, dialog_id, turns_total, turns_compliant,
                 session_compliant, avg_ttft_ms, p95_ttft_ms, avg_tokens_per_sec, started_at, completed_at,
                 disk_io_avg_mbps, gpu_util_avg, gpu_temp_avg, cpu_util_avg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (self.run_id, model_name, dataset, dialog_id, turns_total, compliant_turns,
                  session_compliant, avg_ttft, p95_ttft, avg_tps, started_at, datetime.utcnow().isoformat(),
                  resource_final["disk_io"]["avg"],
                  resource_final["gpu_util"]["avg"],
                  resource_final["gpu_temp"]["avg"],
                  resource_final["cpu_util"]["avg"]))
        if not resource_final["gpu_util"]["available"] or not resource_final["disk_io"]["available"] or not resource_final["gpu_temp"]["available"] or not resource_final["cpu_util"]["available"]:
            logger.warning(
                "Chat telemetry incomplete model=%s dataset=%s dialog=%s gpu_util=%s gpu_temp=%s disk_io=%s cpu_util=%s",
                model_name,
                dataset,
                dialog_id,
                "ok" if resource_final["gpu_util"]["available"] else "missing",
                "ok" if resource_final["gpu_temp"]["available"] else "missing",
                "ok" if resource_final["disk_io"]["available"] else "missing",
                "ok" if resource_final["cpu_util"]["available"] else "missing",
            )
