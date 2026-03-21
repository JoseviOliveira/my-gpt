import json
import logging
import os
import re
import signal
import sys
import time
import sqlite3
from uuid import uuid4
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

import yaml

from benchmark.db import DBMixin
from benchmark.tasks import TaskMixin, _deep_merge
from benchmark.telemetry import TelemetryMixin
from benchmark.evaluators import (
    MultipleChoiceEvaluator,
    ExactMatchEvaluator,
    CodeExecutionEvaluator,
    ExtractivevQAEvaluator,
    RetrievalEvaluator,
    InstructionEvaluator,
    ChatEvaluator,
)

logger = logging.getLogger(__name__)


class BenchmarkRunner(DBMixin, TaskMixin, TelemetryMixin):
    @staticmethod
    def _scope_signature(scope: dict[str, Any] | None) -> tuple[tuple[str, ...], tuple[str, ...], int]:
        """Return a compact signature for comparing benchmark scopes."""
        scope = scope or {}
        models = scope.get("models") or []
        datasets = scope.get("datasets") or []

        model_names: list[str] = []
        for model in models:
            if isinstance(model, dict):
                name = str(model.get("name") or "").strip()
            else:
                name = str(model or "").strip()
            if name:
                model_names.append(name)

        dataset_names: list[str] = []
        for dataset in datasets:
            if isinstance(dataset, dict):
                name = str(dataset.get("dataset_name") or dataset.get("dataset_label") or "").strip()
            else:
                name = str(dataset or "").strip()
            if name:
                dataset_names.append(name)

        tasks_total = int(scope.get("tasks_total") or 0)
        return (tuple(model_names), tuple(dataset_names), tasks_total)

    def _can_auto_resume_run(self, conn: sqlite3.Connection, run_id: str) -> bool:
        """Allow auto-resume only when latest run scope matches current config scope."""
        try:
            row = conn.execute(
                """
                SELECT models_json, datasets_json, tasks_total
                FROM benchmark_run_scope
                WHERE run_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if not row:
                return False
            old_scope = {
                "models": json.loads(row["models_json"] or "[]"),
                "datasets": json.loads(row["datasets_json"] or "[]"),
                "tasks_total": row["tasks_total"] or 0,
            }
            current_sig = self._scope_signature(self._run_scope)
            old_sig = self._scope_signature(old_scope)
            return old_sig == current_sig
        except Exception as exc:
            logger.warning("Failed to validate resume scope compatibility: %s", exc)
            return False

    def __init__(self, config_path: str, stop_on_empty: bool = False,
                 task_filter: Optional[set[str]] = None,
                 resume_run_id: Optional[str] = None,
                 judge_model_override: Optional[str] = None,
                 no_judge: bool = False,
                 no_evaluation: bool = False):
        self._stop_on_empty_response = stop_on_empty
        self._task_filter = task_filter
        self._requested_resume_run_id = (resume_run_id or "").strip() or None
        self._app_mode = os.environ.get("BENCHMARK_APP_MODE", "normal").strip().lower() or "normal"
        self._evaluation_enabled = not no_evaluation
        self.config_path = os.path.abspath(config_path)
        self.config_name = os.path.basename(config_path)
        with open(config_path) as f:
            config = yaml.safe_load(f)

        defaults_path = Path(__file__).parent / 'robustness_defaults.yaml'
        if defaults_path.exists():
            with open(defaults_path) as f:
                defaults = yaml.safe_load(f) or {}
            self.config = _deep_merge(defaults, config)
        else:
            self.config = config
        self.light_mode = bool(self.config.get("benchmark", {}).get("light_mode", False))
        self._judge_cfg = self.config.get("judge", {}) or {}
        self._judge_enabled = self._evaluation_enabled and (not no_judge)
        self._judge_model = None
        if self._judge_enabled:
            self._judge_model = (judge_model_override or "").strip() or None
            if not self._judge_model:
                self._judge_model = self._judge_cfg.get("model") or os.environ.get("BENCHMARK_JUDGE_MODEL")
            if not self._judge_model:
                available = [m.get("name") for m in self.config.get("models", []) if m.get("name")]
                for preferred in ("gemma3:4b", "qwen3:4b"):
                    if preferred in available:
                        self._judge_model = preferred
                        break
                if not self._judge_model and available:
                    self._judge_model = available[0]
        if self._judge_enabled:
            logger.info("Judge enabled (model=%s)", self._judge_model or "none")
        else:
            logger.info("Judge disabled for this run (--no-judge)")
        if self._evaluation_enabled:
            logger.info("On-the-fly evaluation enabled")
        else:
            logger.info("On-the-fly evaluation disabled (--no-evaluation)")
    
        self.run_id = str(uuid4())
        self.db_path = os.path.abspath(self.config['benchmark']['database_path'])
        self._datasets_total = len(self.config.get('shortform_tasks', [])) + len(self.config.get('chat_tasks', []))
        self._init_database()
        self._run_scope = self._build_run_scope()
        self._resource_cfg = self._load_resource_config()
        self._resume_step_offset = 0
        
        # Setup signal handlers after critical attributes initialized
        self._setup_signal_handlers()
        
        self._resume_total_steps = None
        self._reset_progress_on_resume = os.environ.get('BENCHMARK_RESET_PROGRESS_ON_RESUME', '1').lower() in (
            '1', 'true', 'yes'
        )
    
        # Check for resume (DB-driven)
        self.resume_run = False
        self._resume_state = None
        self._resume_task = None
        disable_resume = bool(self.config.get("benchmark", {}).get("no_resume", False))
        if self._task_filter is not None and not self._requested_resume_run_id:
            disable_resume = True
        if os.environ.get("BENCHMARK_NO_RESUME", "").lower() in ("1", "true", "yes") and not self._requested_resume_run_id:
            disable_resume = True
        if not disable_resume:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    if self._requested_resume_run_id:
                        row = conn.execute(
                            "SELECT run_id, status FROM benchmark_runs WHERE run_id = ? LIMIT 1",
                            (self._requested_resume_run_id,),
                        ).fetchone()
                    else:
                        row = conn.execute(
                            "SELECT run_id, status FROM benchmark_runs ORDER BY started_at DESC LIMIT 1"
                        ).fetchone()
                if row:
                    old_run_id = row["run_id"]
                    old_status = row["status"]
                    if self._requested_resume_run_id:
                        self.run_id = old_run_id
                        self.resume_run = True
                        self._resume_state = self._load_run_state(old_run_id)
                        self._resume_task = self._find_inflight_task()
                        logger.info(f"Resuming requested run_id: {self.run_id} (status={old_status})")
                    else:
                        resume_failed = os.environ.get('BENCHMARK_RESUME_FAILED', '').lower() in ('1', 'true', 'yes')
                        resume_completed = os.environ.get('BENCHMARK_RESUME_COMPLETED', '').lower() in ('1', 'true', 'yes')
                        resume_statuses = {'running', 'initializing', 'interrupted'}
                        if resume_failed:
                            resume_statuses.add('failed')
                        resume_missing_telemetry = False
                        if old_status == "completed":
                            resume_missing_telemetry = self._has_missing_telemetry(old_run_id)
                        is_resumable_by_status = old_status in resume_statuses or (resume_completed and old_status == "completed") or resume_missing_telemetry
                        scope_compatible = self._can_auto_resume_run(conn, old_run_id) if is_resumable_by_status else False
                        if is_resumable_by_status and scope_compatible:
                            self.run_id = old_run_id
                            self.resume_run = True
                            self._resume_state = self._load_run_state(old_run_id)
                            self._resume_task = self._find_inflight_task()
                            if resume_missing_telemetry and old_status == 'completed':
                                logger.info(f"Resuming completed run to fill missing telemetry: {self.run_id}")
                            else:
                                logger.info(f"Resuming run: {self.run_id}")
                        elif is_resumable_by_status and not scope_compatible:
                            logger.info(
                                "Skipping auto-resume for run %s: scope mismatch with current config (%s)",
                                old_run_id,
                                self.config_name,
                            )
                        else:
                            logger.info(f"Latest run {old_run_id} not resumable by current policy (status={old_status})")
                elif self._requested_resume_run_id:
                    logger.warning(f"Requested run_id not found for resume: {self._requested_resume_run_id}")
            except Exception as e:
                logger.warning(f"Failed to read previous state to resume: {e}")

        # Initialize evaluators
        self.evaluators = {
            'multiple_choice': MultipleChoiceEvaluator(),
            'exact_match': ExactMatchEvaluator(),
            'code_execution': CodeExecutionEvaluator(),
            'extractive_qa': ExtractivevQAEvaluator(),
            'retrieval': RetrievalEvaluator(),
            'instruction': InstructionEvaluator(),
            'chat': ChatEvaluator()
        }
    
        # State for live reporting
        self.state = {
            "run_id": self.run_id,
            "status": "initializing",
            "light_mode": self.light_mode,
            "start_time": datetime.utcnow().isoformat(),
            "models_total": len(self.config['models']),
            "models_completed": 0,
            "model_index": 0,
            "dataset_index": 0,
            "datasets_total": self._datasets_total,
            "task_index": 0,
            "tasks_total": 0,
            "current_model": None,
            "current_dataset": None,
            "current_task": None,
            "current_task_label": None,
            "current_sample_id": None,
            "current_dialog_id": None,
            "progress_percent": 0,
            "recent_metrics": [],  # For graphs: {timestamp, ttft, tps}
            "recent_gpu": [],
            "recent_gpu_temp": [],
            "recent_disk_io": [],
            "recent_cpu": [],
            "avg_gpu_temp_model": None,
            "avg_gpu_temp_dataset": None,
            "avg_gpu_temp_task": None,
            "last_request": None
        }
        self._run_state_ready = bool(self.resume_run)
        if self.resume_run and self._resume_state:
            self._restore_state(self._resume_state)
            if self._reset_progress_on_resume:
                self.state["start_time"] = datetime.utcnow().isoformat()
                self.state["progress_percent"] = 0.0
            else:
                db_started_at = self._get_run_started_at()
                if db_started_at:
                    self.state["start_time"] = db_started_at
        self._request_count = 0
        self._reset_model_temp_summary()
        self._reset_dataset_temp_summary()
        self._update_state()

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            sig_name = signal.Signals(signum).name
            logger.warning(f"Received {sig_name}, shutting down gracefully...")
            self._interrupted = True
            # Mark run as interrupted
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("""
                        UPDATE benchmark_runs
                        SET status = 'interrupted'
                        WHERE run_id = ? AND status = 'running'
                    """, (self.run_id,))
            except Exception:
                pass
            sys.exit(1)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def _reset_stuck_tasks(self):
        """Reset tasks stuck in 'running' state from previous interrupted run."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    SELECT COUNT(*) FROM benchmark_run_tasks
                    WHERE run_id = ? AND status = 'running'
                """, (self.run_id,))
                stuck_count = cursor.fetchone()[0]
                
                if stuck_count > 0:
                    logger.info(f"Resetting {stuck_count} tasks stuck in 'running' state")
                    conn.execute("""
                        UPDATE benchmark_run_tasks
                        SET status = 'pending',
                            started_at = NULL,
                            completed_at = NULL
                        WHERE run_id = ? AND status = 'running'
                    """, (self.run_id,))
                    conn.commit()
        except Exception as e:
            logger.warning(f"Failed to reset stuck tasks: {e}")

    def _load_run_state(self, run_id: str) -> Optional[dict]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM benchmark_run_state WHERE run_id=? LIMIT 1",
                    (run_id,),
                ).fetchone()
            if not row:
                return None
            state = {k: row[k] for k in row.keys()}
            state["last_update"] = state.pop("updated_at", None)
            for key in (
                "recent_metrics_json",
                "recent_gpu_json",
                "recent_gpu_temp_json",
                "recent_cpu_json",
                "recent_disk_io_json",
            ):
                raw = state.pop(key, None)
                try:
                    state_name = key.replace("_json", "")
                    state[state_name] = json.loads(raw) if raw else []
                except Exception:
                    state[state_name] = []
            return state
        except Exception:
            return None

    def _restore_state(self, old_state: dict) -> None:
        """Restore progress fields for resume runs."""
        if not isinstance(old_state, dict):
            return
        for key in (
            "start_time",
            "models_completed",
            "model_index",
            "dataset_index",
            "datasets_total",
            "task_index",
            "tasks_total",
            "current_model",
            "current_dataset",
            "current_task",
            "current_task_label",
            "current_sample_id",
            "current_dialog_id",
            "progress_percent",
            "avg_gpu_temp_model",
            "avg_gpu_temp_dataset",
            "avg_gpu_temp_task",
        ):
            if key in old_state:
                self.state[key] = old_state[key]
        self.state["recent_metrics"] = list(old_state.get("recent_metrics") or [])[-100:]
        self.state["recent_gpu"] = list(old_state.get("recent_gpu") or [])[-100:]
        self.state["recent_gpu_temp"] = list(old_state.get("recent_gpu_temp") or [])[-100:]
        self.state["recent_disk_io"] = list(old_state.get("recent_disk_io") or [])[-100:]
        self.state["recent_cpu"] = list(old_state.get("recent_cpu") or [])[-100:]
        # Don't restore last_request - it should start fresh, not show stale data from previous run
        self.state["last_request"] = None

    def _reset_model_temp_summary(self) -> None:
        self._model_temp_sum = 0.0
        self._model_temp_duration = 0.0
        self.state["avg_gpu_temp_model"] = None

    def _reset_dataset_temp_summary(self) -> None:
        self._dataset_temp_sum = 0.0
        self._dataset_temp_duration = 0.0
        self.state["avg_gpu_temp_dataset"] = None

    def _progress_for_step(self, step_count: float, total_steps: int) -> float:
        """Compute progress percent with optional resume offset."""
        if self._resume_total_steps is not None:
            step_count = max(0.0, step_count - self._resume_step_offset)
            total_steps = max(int(self._resume_total_steps), 1)
        return round((step_count / max(total_steps, 1)) * 100, 3)
    
    def _get_current_progress(self) -> float:
        """Get current progress from benchmark_run_tasks (matches API calculation)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM benchmark_run_tasks WHERE run_id = ?",
                    (self.run_id,)
                )
                total = cursor.fetchone()[0] or 0
                
                cursor = conn.execute("""
                    SELECT COUNT(*) FROM benchmark_run_tasks 
                    WHERE run_id = ? AND completed_at IS NOT NULL
                """, (self.run_id,))
                completed = cursor.fetchone()[0] or 0
                
                if total == 0:
                    return 0.0
                
                progress = (completed / total) * 100
                
                # Apply resume offset if in reset mode
                if self._reset_progress_on_resume and self._resume_step_offset is not None:
                    # In reset mode, show progress from resume point
                    since_resume = max(0, completed - self._resume_step_offset)
                    remaining_total = max(1, total - self._resume_step_offset)
                    progress = (since_resume / remaining_total) * 100
                
                return round(progress, 3)
        except Exception as e:
            logger.warning(f"Failed to calculate progress from database: {e}")
            return self.state.get("progress_percent", 0.0)

    def _task_budget_per_model(self) -> int:
        """Count configured tasks per model after applying optional task filter."""
        budget = 0
        for task in self.config.get('shortform_tasks', []):
            name = task.get('name')
            if not name:
                continue
            if self._task_filter and name not in self._task_filter:
                continue
            budget += 1
        for task in self.config.get('chat_tasks', []):
            name = task.get('name')
            if not name:
                continue
            if self._task_filter and name not in self._task_filter:
                continue
            budget += 1
        return budget

    def _remaining_tasks_for_model(self, model_name: str) -> list[str]:
        """Return ordered task names that still need execution for this model."""
        remaining: list[str] = []
        for task in self.config.get('shortform_tasks', []):
            name = task.get('name')
            if not name:
                continue
            if self._task_filter and name not in self._task_filter:
                continue
            if not self._is_task_completed(model_name, name):
                remaining.append(name)
        for task in self.config.get('chat_tasks', []):
            name = task.get('name')
            if not name:
                continue
            if self._task_filter and name not in self._task_filter:
                continue
            if not self._is_chat_task_completed(model_name, task):
                remaining.append(name)
        return remaining

    def _log_resume_remaining_plan(self, total_steps: int, completed_steps: int) -> None:
        """Log resume execution plan: per-model remaining tasks and global total."""
        if not self.resume_run:
            return
        per_model_budget = self._task_budget_per_model()
        total_remaining_tasks = 0
        logger.info(
            "[resume-plan] run_id=%s completed_samples=%d/%d",
            self.run_id,
            completed_steps,
            total_steps,
        )
        for model in self.config.get('models', []):
            model_name = model.get('name')
            if not model_name:
                continue
            remaining = self._remaining_tasks_for_model(model_name)
            total_remaining_tasks += len(remaining)
            remaining_text = ", ".join(remaining) if remaining else "(none)"
            logger.info(
                "[resume-plan] model=%s remaining_tasks=%d/%d tasks=[%s]",
                model_name,
                len(remaining),
                per_model_budget,
                remaining_text,
            )
        logger.info(
            "[resume-plan] total_remaining_tasks=%d",
            total_remaining_tasks,
        )

    def _update_state(self) -> None:
        self.state["last_update"] = datetime.utcnow().isoformat()
        if self._run_state_ready:
            self._write_run_state()

    def run(self):
        """Execute the full benchmark suite."""
        logger.info(f"Starting benchmark run: {self.run_id}")
        if self.light_mode:
            logger.info("🔦 LIGHT MODE: running only hardest sample per dataset")
        self.state["status"] = "running"
        if not self.resume_run:
            self._record_run_start()
        else:
            self._reset_stuck_tasks()
            self._mark_run_resumed()
            if self._run_state_ready:
                self._write_run_state()
    
        try:
            # Query benchmark_run_tasks to get actual sample count (matches API)
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM benchmark_run_tasks WHERE run_id = ?",
                    (self.run_id,)
                )
                total_steps = cursor.fetchone()[0] or 0
                
                cursor = conn.execute("""
                    SELECT COUNT(*) FROM benchmark_run_tasks 
                    WHERE run_id = ? AND completed_at IS NOT NULL
                """, (self.run_id,))
                completed_steps = cursor.fetchone()[0] or 0
            
            # Calculate total tasks per model (always needed for resume logic and state tracking)
            total_tasks_per_model = len(self.config['shortform_tasks']) + len(self.config['chat_tasks'])
            
            if total_steps == 0:
                # Fallback to config-based calculation if benchmark_run_tasks not yet populated
                total_steps = len(self.config['models']) * total_tasks_per_model
            
            step_count = completed_steps
            if self.resume_run:
                self._log_resume_remaining_plan(total_steps=total_steps, completed_steps=completed_steps)
                self.state["models_completed"] = self._count_completed_models(total_tasks_per_model)
                
                if self._reset_progress_on_resume:
                    self._resume_step_offset = completed_steps
                    self._resume_total_steps = total_steps
                    self.state["progress_percent"] = 0.0
                    self.state["start_time"] = datetime.utcnow().isoformat()
                else:
                    self.state["progress_percent"] = self._get_current_progress()
                self._update_state()
        
            # Run for each model
            for model_idx, model_config in enumerate(self.config['models'], start=1):
                model_name = model_config['name']
                self._reset_model_temp_summary()
                self._reset_dataset_temp_summary()
                self.state["avg_gpu_temp_task"] = None
                self.state["current_model"] = model_name
                self.state["current_dataset"] = None
                self.state["model_index"] = model_idx
                self.state["dataset_index"] = 0
                self.state["datasets_total"] = total_tasks_per_model
                self.state["task_index"] = 0
                self.state["tasks_total"] = 0
                self.state["current_task_label"] = None
                self._update_state()

                if self.resume_run and self._is_model_completed(model_name, total_tasks_per_model):
                    self.state["progress_percent"] = self._get_current_progress()
                    self._update_state()
                    self._mark_model_completed(model_name)
                    continue
            
                logger.info(f"Benchmarking model: {model_name}")
                self._mark_model_started(model_name)
                self._wait_for_app_ready()
            
                # Warm up model
                self._warmup_model(model_name)
            
                # Part A: Academic benchmarks
                logger.info("Running academic benchmarks...")
                dataset_idx = 0
                for task in self.config['shortform_tasks']:
                    if self._task_filter and task.get("name") not in self._task_filter:
                        continue
                    dataset_idx += 1
                    dataset_name = task["name"]
                    self.state["dataset_index"] = dataset_idx
                    self.state["task_index"] = 0
                    self.state["tasks_total"] = 0
                    self.state["current_dataset"] = dataset_name
                    self.state["current_task_label"] = None
                    self._reset_dataset_temp_summary()
                    self.state["avg_gpu_temp_task"] = None
                    self._update_state()
                    self._mark_dataset_started(model_name, dataset_name)
                
                    # Check if already completed
                    if self._is_task_completed(model_name, task['name']):
                        logger.info(f"Skipping completed task: {task['name']}")
                        self.state["progress_percent"] = self._get_current_progress()
                        self._update_state()
                        self._mark_dataset_completed(model_name, dataset_name)
                        continue

                    self.state["current_task"] = task['name']
                    self.state["progress_percent"] = self._get_current_progress()
                    self._update_state()
                
                    self._run_task(model_name, task, step_count, total_steps)
                    step_count += 1
                    self.state["progress_percent"] = self._get_current_progress()
                    self._mark_dataset_completed(model_name, dataset_name)
                    self._update_state()
            
                # Part B: Chat UX evaluation
                logger.info("Running chat UX evaluation...")
                for task in self.config['chat_tasks']:
                    if self._task_filter and task.get("name") not in self._task_filter:
                        continue
                    dataset_idx += 1
                    dataset_name = task["name"]
                    self.state["dataset_index"] = dataset_idx
                    self.state["task_index"] = 0
                    self.state["tasks_total"] = 0
                    self.state["current_dataset"] = dataset_name
                    self.state["current_task_label"] = None
                    self._reset_dataset_temp_summary()
                    self.state["avg_gpu_temp_task"] = None
                    self._update_state()
                    self._mark_dataset_started(model_name, dataset_name)
                
                    # Check if completed (basic check)
                    if self._is_chat_task_completed(model_name, task):
                        logger.info(f"Skipping completed chat task: {task['name']}")
                        self.state["progress_percent"] = self._get_current_progress()
                        self._update_state()
                        self._mark_dataset_completed(model_name, dataset_name)
                        continue

                    self.state["current_task"] = task['name']
                    self.state["progress_percent"] = self._get_current_progress()
                    self._update_state()
                
                    self._run_chat_task(model_name, task, step_count, total_steps)
                    step_count += 1
                    self.state["progress_percent"] = self._get_current_progress()
                    self._mark_dataset_completed(model_name, dataset_name)
                    self._update_state()
            
                # Unload model and cooldown
                self._unload_model(model_name)
                self.state["models_completed"] += 1
                self._mark_model_completed(model_name)
                self._apply_cooldown(self.config['thermal']['inter_model_cooldown'])
        
            self._record_run_complete()
            self.state["status"] = "completed"
            self.state["progress_percent"] = 100.0
            self._update_state()
            logger.info("Benchmark completed successfully")
        
        except KeyboardInterrupt:
            logger.warning("Benchmark interrupted by user (Ctrl+C)")
            self._record_run_interrupted()
            self.state["status"] = "interrupted"
            self._update_state()
            raise
        
        except Exception as e:
            logger.error(f"Benchmark failed: {e}", exc_info=True)
            self._record_run_failed(str(e))
            self.state["status"] = "failed"
            self.state["error"] = str(e)
            self._update_state()
            raise

    def _scope_dataset_label(self, dataset_path: Path) -> str:
        label = dataset_path.name
        if "." in label:
            label = label.rsplit(".", 1)[0]
        label = re.sub(r"_\\d+$", "", label)
        return label

    def _build_run_scope(self) -> dict:
        """Build run scope metadata from config and datasets."""
        base_dir = Path(__file__).parent
        model_names = [m.get("name") for m in self.config.get("models", []) if m.get("name")]
        models = [
            {"name": name, "index": idx + 1}
            for idx, name in enumerate(model_names)
        ]
        datasets: list[dict[str, Any]] = []
        tasks: list[dict[str, Any]] = []
        tasks_total = 0

        for model in models:
            dataset_index = 0
            for task in self.config.get("shortform_tasks", []):
                dataset_index += 1
                dataset_path = base_dir / task["dataset"]
                samples = []
                if dataset_path.exists():
                    with open(dataset_path) as f:
                        samples = [json.loads(line) for line in f]
                max_samples = task.get("samples")
                if max_samples is not None and max_samples > 0:
                    samples = samples[:max_samples]
                sample_count = len(samples)
                datasets.append({
                    "type": "shortform",
                    "model_name": model["name"],
                    "dataset_name": task["name"],
                    "dataset_label": task["name"],
                    "dataset_index": dataset_index,
                    "tasks_total": sample_count,
                    "category": task.get("category"),
                })
                for idx, sample in enumerate(samples):
                    sample_id = sample.get("id", f"{task['name']}_{idx}")
                    tasks.append({
                        "model_name": model["name"],
                        "dataset_name": task["name"],
                        "task_name": sample_id,
                        "task_label": sample_id,
                        "task_index": idx + 1,
                        "task_kind": "shortform",
                        "sample_id": sample_id,
                    })
                tasks_total += sample_count

            for task in self.config.get("chat_tasks", []):
                dataset_index += 1
                dataset_path = base_dir / task["dataset"]
                dialogs = []
                if dataset_path.exists():
                    with open(dataset_path) as f:
                        dialogs = [json.loads(line) for line in f]
                if self.light_mode and dialogs:
                    hardest = [d for d in dialogs if d.get("is_hardest")]
                    if hardest:
                        dialogs = hardest
                    else:
                        dialogs = dialogs[-1:]
                total_turns = 0
                task_index = 0
                single_turn = False
                if dialogs:
                    single_turn = "turns" not in dialogs[0] and (
                        "question" in dialogs[0] or "prompt" in dialogs[0]
                    )
                    if single_turn:
                        total_turns = len(dialogs)
                    else:
                        total_turns = sum(len(dialog.get("turns", [])) for dialog in dialogs)
                datasets.append({
                    "type": "chat",
                    "model_name": model["name"],
                    "dataset_name": task["name"],
                    "dataset_label": task["name"],
                    "dataset_index": dataset_index,
                    "tasks_total": total_turns,
                })
                if single_turn:
                    for idx, dialog in enumerate(dialogs):
                        task_index += 1
                        dialog_id = dialog.get("id", f"{task['name']}_{idx}")
                        tasks.append({
                            "model_name": model["name"],
                            "dataset_name": task["name"],
                            "task_name": dialog_id,
                            "task_label": f"dialog {dialog_id}",
                            "task_index": task_index,
                            "task_kind": "chat",
                            "dialog_id": dialog_id,
                            "turn_index": 0,
                        })
                else:
                    for dialog in dialogs:
                        dialog_id = dialog.get("id")
                        for turn_idx, _turn in enumerate(dialog.get("turns", []), start=1):
                            task_index += 1
                            task_name = f"{dialog_id}:{turn_idx}"
                            tasks.append({
                                "model_name": model["name"],
                                "dataset_name": task["name"],
                                "task_name": task_name,
                                "task_label": f"dialog {dialog_id} turn {turn_idx}",
                                "task_index": task_index,
                                "task_kind": "chat",
                                "dialog_id": dialog_id,
                                "turn_index": turn_idx,
                            })
                tasks_total += total_turns

        return {
            "config_path": self.config_path,
            "config_name": self.config_name,
            "light_mode": int(self.light_mode),
            "models": models,
            "datasets": datasets,
            "tasks": tasks,
            "models_total": len(models),
            "datasets_total": int(self._datasets_total),
            "tasks_total": tasks_total,
        }

    def _warmup_model(self, model_name: str):
        """Warm up model and record TTFT."""
        logger.info(f"Warming up {model_name}...")
        start_time = time.time()
    
        # Simple warm-up prompt
        self._call_ollama(model_name, "Hello", {"temperature": 0, "max_tokens": 10})
    
        warmup_ttft = int((time.time() - start_time) * 1000)
    
        # Record model metadata
        model_config = next(m for m in self.config['models'] if m['name'] == model_name)
    
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                DELETE FROM benchmark_models
                WHERE run_id = ? AND model_name = ?
            """, (self.run_id, model_name))
            conn.execute("""
                INSERT INTO benchmark_models (run_id, model_name, parameters_b, quantization, warmup_ttft_ms)
                VALUES (?, ?, ?, ?, ?)
            """, (self.run_id, model_name, model_config.get('params_b'),
                  model_config.get('quantization'), warmup_ttft))
    
        logger.info(f"Warm-up TTFT: {warmup_ttft}ms")

    def _unload_model(self, model_name: str):
        """Unload model from memory."""
        import subprocess
        logger.info(f"Unloading {model_name}")
        subprocess.run(['ollama', 'stop', model_name], capture_output=True)
