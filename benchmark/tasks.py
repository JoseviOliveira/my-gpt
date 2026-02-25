import json
import logging
import math
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

logger = logging.getLogger(__name__)

PROMPT_PREVIEW_LEN = 2000  # Increased from 220 to show full prompts
RESPONSE_PREVIEW_LEN = 2000  # Increased from 260 to show full responses

class EmptyResponseError(RuntimeError):
    """Raised when a streamed Ollama response yields no content."""

class TimeoutError(Exception):
    """Raised when a request is killed by the watchdog."""
    pass

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge dictionaries, preferring override values."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged

class TaskMixin:

    def _preview_text(self, text: str, max_len: int) -> str:
        """Create a short, single-line preview of text."""
        if text is None:
            return ""
        cleaned = " ".join(str(text).replace("\r", " ").replace("\n", " ").split())
        if len(cleaned) <= max_len:
            return cleaned
        return cleaned[: max_len - 3] + "..."

    def _serialize_log_payload(self, payload: Any) -> str:
        """Serialize payloads for one-line logs."""
        try:
            return json.dumps(payload, ensure_ascii=False)
        except Exception:
            return str(payload)

    def _log_full_prompt(self, request_info: Dict[str, Any], payload: Any) -> None:
        """Log the full prompt payload sent to the app."""
        logger.info(
            "[trace] prompt model=%s task=%s dataset=%s sample_id=%s dialog_id=%s turn_idx=%s request_id=%s payload=%s",
            request_info.get("model"),
            request_info.get("task"),
            request_info.get("dataset"),
            request_info.get("sample_id"),
            request_info.get("dialog_id"),
            request_info.get("turn_idx"),
            request_info.get("request_id"),
            self._serialize_log_payload(payload),
        )

    def _log_full_answer(self, request_info: Dict[str, Any], response_text: str) -> None:
        """Log the full answer returned by the app."""
        logger.info(
            "[trace] answer model=%s task=%s dataset=%s sample_id=%s dialog_id=%s turn_idx=%s request_id=%s response=%s",
            request_info.get("model"),
            request_info.get("task"),
            request_info.get("dataset"),
            request_info.get("sample_id"),
            request_info.get("dialog_id"),
            request_info.get("turn_idx"),
            request_info.get("request_id"),
            self._serialize_log_payload(response_text),
        )

    def _preview_prompt(self, prompt, multi_turn: bool) -> str:
        """Return a short preview for prompts/messages."""
        if multi_turn and isinstance(prompt, list):
            for item in reversed(prompt):
                if isinstance(item, dict) and item.get("role") == "user":
                    return self._preview_text(item.get("content", ""), PROMPT_PREVIEW_LEN)
            return self._preview_text(str(prompt), PROMPT_PREVIEW_LEN)
        return self._preview_text(prompt, PROMPT_PREVIEW_LEN)

    def _format_override_prompt(self, task_config: dict, sample: Optional[dict] = None) -> Optional[str]:
        """Return a strict format instruction for academic tasks when needed.

        Strategy:
        - Keep strong "final value only" for genuine exact-match value tasks.
        - Avoid value-only forcing for strategy/judge tasks (e.g. logic puzzles
          requiring a method), where that instruction is semantically wrong.
        """
        evaluator = (task_config.get("evaluator") or "").strip().lower()
        if evaluator == "multiple_choice":
            return "Answer with a single letter (A, B, C, or D) and nothing else."
        if evaluator == "exact_match":
            if not sample:
                return "Answer with only the final value. No explanation, no units."

            grading = (sample.get("grading") or "").strip().lower()
            answer_type = (sample.get("answer_type") or "").strip().lower()
            question = (sample.get("question") or sample.get("prompt") or "").strip().lower()

            if answer_type in {"strategy", "procedure", "plan"}:
                return "Give a concise final strategy. No chain-of-thought."
            if grading.startswith("llm_judge:"):
                return "Give a concise final answer that directly satisfies the task."
            if grading.startswith("contains:"):
                return "Give one concise final answer (short phrase or sentence) with no extra explanation."
            if grading == "logical_equivalence":
                return "Give one concise final statement logically equivalent to the conclusion."

            # Guardrail for optimization/how-type prompts even when answer_type is missing.
            if any(token in question for token in ("how can", "strategy", "using only", "how do you")):
                return "Give a concise final strategy. No chain-of-thought."

            return "Answer with only the final value. No explanation, no units."
        if evaluator == "extractive_qa":
            return "Answer with the exact short span from the passage. No extra words."
        if evaluator == "retrieval":
            return "Answer with the exact retrieved text span only."
        if evaluator == "code_execution":
            return "Respond with only the Python code. No markdown or explanation."
        return None

    def _mode_override_for_task(self, task_config: dict) -> Optional[str]:
        """Choose a lighter mode for strict-answer academic tasks."""
        evaluator = (task_config.get("evaluator") or "").strip().lower()
        if evaluator in {"multiple_choice", "exact_match", "extractive_qa", "retrieval"}:
            return "fast"
        return None

    def _normalize_text(self, text: Optional[str]) -> str:
        if text is None:
            return ""
        return " ".join(str(text).lower().split())

    def _extract_number(self, text: Optional[str]) -> Optional[float]:
        if not text:
            return None
        match = re.search(r'####\s*(-?[\d,\.]+)', text)
        if match:
            return float(match.group(1).replace(',', ''))
        match = re.search(r'(?:answer|result)(?:\s*is)?[\s:=]+(-?[\d,\.]+)', text, re.IGNORECASE)
        if match:
            return float(match.group(1).replace(',', ''))
        match = re.search(r'\\boxed\{(-?[\d,\.]+)\}', text)
        if match:
            return float(match.group(1).replace(',', ''))
        numbers = re.findall(r'-?[\d,]+\.?\d*', text)
        if numbers:
            try:
                return float(numbers[-1].replace(',', ''))
            except ValueError:
                return None
        return None

    def _contains_tokens(self, response: str, tokens: list[str], require_all: bool) -> bool:
        response_lower = self._normalize_text(response)
        if not tokens:
            return False
        hits = [token for token in tokens if token and token in response_lower]
        if require_all:
            return len(hits) == len(tokens)
        return len(hits) > 0

    def _extract_grading_slice(self, grading: str, key: str, keys: list[str]) -> Optional[str]:
        start = grading.find(key)
        if start == -1:
            return None
        start += len(key)
        next_positions = []
        for other in keys:
            if other == key:
                continue
            pos = grading.find(other, start)
            if pos != -1:
                next_positions.append(pos)
        end = min(next_positions) if next_positions else len(grading)
        return grading[start:end].strip(" ,")

    def _evaluate_with_grading(self, response: str, expected: Optional[str], grading: str, sample: dict) -> dict:
        grading_raw = (grading or "").strip()
        grading_lower = grading_raw.lower()
        violations = []
        correct = True
        skipped_rules: list[str] = []
        rules = [
            "word_count:",
            "line_count:",
            "uppercase_contains:",
            "contains:",
            "numerical_tolerance:",
            "translation_match:",
            "llm_judge",
            "logical_equivalence",
            "exact_match",
            "valid_json",
            "no_markers",
        ]

        word_count_val = self._extract_grading_slice(grading_lower, "word_count:", rules)
        if word_count_val:
            target = int(word_count_val.split(",", 1)[0])
            count = len((response or "").split())
            if count != target:
                correct = False
                violations.append(f"word_count expected {target}, got {count}")

        line_count_val = self._extract_grading_slice(grading_lower, "line_count:", rules)
        if line_count_val:
            target = int(line_count_val.split(",", 1)[0])
            lines = [line for line in (response or "").splitlines() if line.strip()]
            if len(lines) != target:
                correct = False
                violations.append(f"line_count expected {target}, got {len(lines)}")

        uppercase_val = self._extract_grading_slice(grading_lower, "uppercase_contains:", rules)
        if uppercase_val:
            token = uppercase_val.split(",", 1)[0].strip()
            text_alpha = "".join(ch for ch in response if ch.isalpha())
            if text_alpha and text_alpha != text_alpha.upper():
                correct = False
                violations.append("response not uppercase")
            if token and token not in self._normalize_text(response):
                correct = False
                violations.append(f"missing token {token}")

        contains_val = self._extract_grading_slice(grading_lower, "contains:", rules)
        if contains_val:
            tokens = [t.strip() for t in contains_val.split(",") if t.strip()]
            expected_norm = self._normalize_text(expected)
            require_all = bool(expected_norm) and all(t in expected_norm for t in tokens)
            if not self._contains_tokens(response, tokens, require_all):
                correct = False
                violations.append(f"missing contains tokens ({'all' if require_all else 'any'})")

        tolerance_val = self._extract_grading_slice(grading_lower, "numerical_tolerance:", rules)
        if tolerance_val:
            tol = float(tolerance_val.split(",", 1)[0])
            resp_num = self._extract_number(response)
            exp_num = self._extract_number(expected)
            if resp_num is None or exp_num is None or abs(resp_num - exp_num) > tol:
                correct = False
                violations.append(f"numerical tolerance ±{tol} failed")

        if "valid_json" in grading_lower:
            try:
                json.loads(response.strip())
            except Exception:
                correct = False
                violations.append("invalid json")

        if "no_markers" in grading_lower:
            if re.search(r'^[\\s]*[\\*\\-•]|^\\s*\\d+\\.', response or "", re.MULTILINE):
                correct = False
                violations.append("markers present")

        if "exact_match" in grading_lower and expected is not None:
            if self._normalize_text(response) != self._normalize_text(expected):
                correct = False
                violations.append("exact match failed")

        if "logical_equivalence" in grading_lower and expected is not None:
            stop = {"the", "a", "an", "is", "are", "was", "were", "and", "or", "not", "to", "of"}
            expected_tokens = [t for t in re.findall(r"[a-zA-Z0-9\\^]+", expected.lower()) if t not in stop]
            if not self._contains_tokens(response, expected_tokens, True):
                correct = False
                violations.append("logical equivalence failed")

        if "translation_match:" in grading_lower and expected is not None:
            if self._normalize_text(expected) not in self._normalize_text(response):
                correct = False
                violations.append("translation mismatch")

        if "llm_judge" in grading_lower:
            if not getattr(self, "_judge_enabled", True):
                skipped_rules.append("llm_judge")
            else:
                judge = self._llm_judge(
                    sample.get("question") or sample.get("prompt") or "",
                    response,
                    expected,
                    grading_raw,
                    sample,
                )
                if not judge.get("pass"):
                    correct = False
                    reason = judge.get("reason") or "llm_judge failed"
                    violations.append(reason)

        result = {"correct": correct, "violations": violations, "grading": grading_raw}
        if skipped_rules:
            result["skipped_rules"] = skipped_rules
        return result

    def _extract_sample_fields(self, sample: dict) -> tuple[str, Optional[str], Optional[str]]:
        prompt = sample.get("prompt") or sample.get("question")
        expected = sample.get("expected") or sample.get("answer") or sample.get("expected_content") or sample.get("answer_type")
        grading = sample.get("grading")
        if prompt is None:
            raise KeyError("prompt")
        return prompt, expected, grading

    def _parse_json_blob(self, text: str) -> Optional[dict]:
        if not text:
            return None
        text = text.strip()
        try:
            return json.loads(text)
        except Exception:
            pass
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except Exception:
            return None

    def _llm_judge(self, question: str, response: str, expected: Optional[str], grading: str, sample: dict) -> dict:
        import requests

        if not self._judge_model:
            return {"pass": False, "reason": "no_judge_model"}

        system = (
            "You are a strict benchmark grader. "
            "Decide if the RESPONSE satisfies the grading_tag and the question intent. "
            "Return JSON only: {\"pass\": true/false, \"confidence\": 0-1, \"reason\": \"...\"}."
        )
        payload = {
            "model": self._judge_model,
            "mode": "fast",
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "grading_tag": grading,
                            "question": question,
                            "expected_answer": expected,
                            "answer_type": sample.get("answer_type"),
                            "metadata": sample.get("metadata"),
                            "response": response,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "options": {
                "temperature": float(self._judge_cfg.get("temperature", 0.0)),
                "top_p": float(self._judge_cfg.get("top_p", 1.0)),
                "top_k": int(self._judge_cfg.get("top_k", 1)),
                "num_predict": int(self._judge_cfg.get("max_tokens", 256)),
            },
        }
        timeout = int(self._judge_cfg.get("timeout_sec", 60))
        endpoint = f"{self._app_base_url()}/api/chat"
        try:
            resp = requests.post(endpoint, json=payload, timeout=timeout, headers=self._app_auth_headers())
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("LLM judge failed (%s): %s", type(exc).__name__, exc)
            return {"pass": False, "reason": f"judge_error: {exc}"}
        content = ""
        if isinstance(data.get("message"), dict):
            content = data["message"].get("content", "")
        else:
            content = data.get("response", "")
        parsed = self._parse_json_blob(content)
        if not parsed or "pass" not in parsed:
            return {"pass": False, "reason": f"judge_parse_failed: {content[:160]}"}
        return {
            "pass": bool(parsed.get("pass")),
            "reason": parsed.get("reason", ""),
            "confidence": parsed.get("confidence"),
        }

    def _log_request_trace(self, phase: str, model_name: str, dataset: str, task: str,
                           position: str, response: Optional[str], metrics: Dict[str, Any],
                           expected: Optional[str] = None) -> None:
        """Emit a compact, grep-friendly request trace."""
        def fmt(value: Optional[float], precision: int = 1) -> str:
            if value is None:
                return "na"
            try:
                num = float(value)
            except (TypeError, ValueError):
                return "na"
            if not math.isfinite(num):
                return "na"
            return f"{num:.{precision}f}"

        def avg_from(stats: Optional[Dict[str, Any]]) -> Optional[float]:
            if not stats or not stats.get("available"):
                return None
            duration = stats.get("duration_sec") or 0.0
            if duration <= 0:
                return None
            return (stats.get("sum_value_sec") or 0.0) / duration

        if phase == "start":
            expected_preview = ""
            if expected is not None:
                expected_preview = self._preview_text(str(expected), 60)
                if expected_preview:
                    expected_preview = f" expected={expected_preview}"
            logger.info(
                "[track] start model=%s dataset=%s task=%s %s%s",
                model_name,
                dataset,
                task,
                position,
                expected_preview
            )
            return

        resource_stats = metrics.get("resource_stats") if isinstance(metrics, dict) else None
        disk_stats = resource_stats.get("disk_io") if resource_stats else None
        gpu_util_stats = resource_stats.get("gpu_util") if resource_stats else None
        gpu_temp_stats = resource_stats.get("gpu_temp") if resource_stats else None
        cpu_util_stats = resource_stats.get("cpu_util") if resource_stats else None
        answer_state = "ok" if response and not str(response).startswith("[error]") else "error"
        expected_preview = ""
        if expected is not None:
            expected_preview = self._preview_text(str(expected), 60)
        logger.info(
            "[track] end model=%s dataset=%s task=%s %s answer=%s out_tokens=%s ttft_ms=%s total_ms=%s gpu_util_avg=%s gpu_util_max=%s gpu_temp_avg=%s gpu_temp_max=%s disk_avg=%s disk_max=%s cpu_util_avg=%s cpu_util_max=%s expected=%s",
            model_name,
            dataset,
            task,
            position,
            answer_state,
            metrics.get("output_tokens") if isinstance(metrics, dict) else "na",
            metrics.get("ttft_ms") if isinstance(metrics, dict) else "na",
            metrics.get("total_time_ms") if isinstance(metrics, dict) else "na",
            fmt(avg_from(gpu_util_stats)),
            fmt(gpu_util_stats.get("max") if gpu_util_stats and gpu_util_stats.get("available") else None),
            fmt(avg_from(gpu_temp_stats)),
            fmt(gpu_temp_stats.get("max") if gpu_temp_stats and gpu_temp_stats.get("available") else None),
            fmt(avg_from(disk_stats)),
            fmt(disk_stats.get("max") if disk_stats and disk_stats.get("available") else None),
            fmt(avg_from(cpu_util_stats)),
            fmt(cpu_util_stats.get("max") if cpu_util_stats and cpu_util_stats.get("available") else None),
            expected_preview or "na",
        )

    def _run_shortform_tasks(self, model_name: str, step_offset: int, total_steps: int):
        """Run all academic benchmark tasks."""
        logger.info("Running academic benchmarks...")
        for task in self.config['shortform_tasks']:
            self._run_task(model_name, task, step_offset, total_steps)
            step_offset += 1

    def _run_chat_tasks(self, model_name: str, step_offset: int, total_steps: int):
        """Run all chat UX tasks."""
        logger.info("Running chat UX evaluation...")
        for task in self.config['chat_tasks']:
            self._run_chat_task(model_name, task, step_offset, total_steps)
            step_offset += 1

    def _run_task(self, model_name: str, task_config: dict, step_offset: int, total_steps: int):
        """Run a single academic task with sample-level progress."""
        task_name = task_config['name']
        logger.info(f"  Task: {task_name}")
        dataset_label = Path(task_config['dataset']).name
        self.state["current_task"] = task_name
        self.state["current_task_label"] = task_name
        self.state["current_sample_id"] = None
        self.state["current_dialog_id"] = None
    
        # Load dataset
        dataset_path = Path(__file__).parent /  task_config['dataset']
        if not dataset_path.exists():
            logger.warning(f"Dataset not found: {dataset_path}, skipping...")
            return
        
        # Clear task results unless task is already completed (skip check happens in runner)
        # When resuming, we preserve completed tasks but re-run incomplete ones from scratch
        if not self.resume_run or not self._is_task_completed(model_name, task_name):
            self._clear_task_results(model_name, task_name)
    
        with open(dataset_path) as f:
            samples = [json.loads(line) for line in f]
    
        # Apply sample limit if specified in config
        max_samples = task_config.get('samples')
        if max_samples is not None and max_samples > 0:
            samples = samples[:max_samples]
            logger.info(f"    Limiting to first {max_samples} sample(s) from config")
    
        # Get evaluator and config
        evaluator_name = task_config['evaluator']
        evaluator = self.evaluators[evaluator_name]
        decoding_config = self.config['decoding_configs'][task_config['config']]
        mode_override = self._mode_override_for_task(task_config)
    
        # Run samples
        task_start = datetime.utcnow().isoformat()
        correct_count = 0
        total_ttft = 0
        total_tokens_per_sec = 0
        resource_totals = self._init_resource_totals()
        total_samples = len(samples)
        self.state["tasks_total"] = total_samples
        self.state["task_index"] = 0
        self._update_state()

        start_index = 0
        if self.resume_run and self._resume_task:
            if self._resume_task.get("model_name") == model_name and self._resume_task.get("dataset_name") == task_name:
                resume_sample_id = self._resume_task.get("sample_id") or self._resume_task.get("task_name")
                for idx, sample in enumerate(samples):
                    sample_id = sample.get('id', f"{task_name}_{idx}")
                    if sample_id == resume_sample_id:
                        start_index = idx
                        break
                self._resume_task = None
                if start_index:
                    self.state["task_index"] = start_index
                    self._update_state()

        for idx, sample in enumerate(samples[start_index:], start=start_index):
            self.state["task_index"] = idx + 1
            # Update progress from database
            self.state["progress_percent"] = self._get_current_progress()
            self._update_state()
            sample_id = sample.get('id', f"{task_name}_{idx}")
            self.state["current_sample_id"] = sample_id
            self.state["current_task_label"] = sample_id
            self.state["current_dialog_id"] = None
            self._update_state()
            prompt, expected, grading = self._extract_sample_fields(sample)
            format_override = self._format_override_prompt(task_config, sample)
            if format_override:
                prompt = f"Instruction: {format_override}\n\n{prompt}"
            messages = [{"role": "user", "content": prompt}]

            request_meta = {
                "model": model_name,
                "task": task_name,
                "dataset": dataset_label,
                "sample_id": sample_id,
                "kind": "academic"
            }
            position = f"sample={idx + 1}/{total_samples} id={sample_id}"
            self._mark_task_started(model_name, task_name, sample_id, task_label=sample_id)
            self._log_request_trace("start", model_name, dataset_label, task_name, position, None, {}, expected)
            try:
                response, metrics = self._call_ollama_with_metrics(
                    model_name,
                    messages,
                    decoding_config,
                    multi_turn=True,
                    request_meta=request_meta,
                    mode_override=mode_override,
                )
            except EmptyResponseError as e:
                logger.error(f"Sample {sample_id} returned empty response: {e}")
                if self._stop_on_empty_response:
                    raise
                response = f"[error] {e}"
                metrics = {
                    'ttft_ms': 0,
                    'tokens_per_sec': 0,
                    'total_time_ms': 0,
                    'input_tokens': len(str(prompt).split()),
                    'output_tokens': 0
                }
                correct = False
            except Exception as e:
                logger.error(f"Sample {sample_id} failed: {e}")
                response = f"[error] {e}"
                metrics = {
                    'ttft_ms': 0,
                    'tokens_per_sec': 0,
                    'total_time_ms': 0,
                    'input_tokens': len(str(prompt).split()),
                    'output_tokens': 0
                }
                correct = False
            else:
                if getattr(self, "_evaluation_enabled", True):
                    if grading:
                        result = self._evaluate_with_grading(response, expected, grading, sample)
                    else:
                        result = evaluator.evaluate(response, expected)
                    correct = result.get('correct', False)
                    if correct:
                        correct_count += 1
                else:
                    result = {"correct": None, "evaluation_skipped": True, "grading": grading}
                    correct = None
                total_ttft += metrics['ttft_ms']
                total_tokens_per_sec += metrics['tokens_per_sec']
                self._accumulate_resource_totals(resource_totals, metrics.get('resource_stats'))
                self._update_temp_summaries(metrics.get('resource_stats'))
            self._log_request_trace("end", model_name, dataset_label, task_name, position, response, metrics, expected)

            # Update metrics in state
            self.state["recent_metrics"].append({
                "timestamp": datetime.utcnow().isoformat(),
                "ttft_ms": metrics['ttft_ms'],
                "tokens_per_sec": metrics['tokens_per_sec']
            })
            if len(self.state["recent_metrics"]) > 100:
                self.state["recent_metrics"].pop(0)
            self._record_gpu_utilization()
            self._update_state()

            # Record sample
            self._record_sample(model_name, task_name, sample_id, prompt, expected, 
                              response, correct, metrics)
            if isinstance(response, str) and response.startswith("[error]"):
                status = "error"
            elif metrics.get("timeout_killed"):
                status = "error"
            elif not getattr(self, "_evaluation_enabled", True):
                status = "completed_no_eval"
            else:
                status = "passed" if correct else "failed"
            self._mark_task_completed(model_name, task_name, sample_id, status, error=metrics.get("timeout_reason"))

            # Cooldown removed - GPU temperature guard handles thermal management
    
        # Record task aggregates
        self.state["current_sample_id"] = None
        self.state["current_dialog_id"] = None
        self.state["current_task_label"] = None
        self._update_state()
        resource_final = self._finalize_resource_totals(resource_totals)
        accuracy = (correct_count / len(samples)) if samples and getattr(self, "_evaluation_enabled", True) else None
        avg_ttft = total_ttft / len(samples) if samples else 0
        avg_tps = total_tokens_per_sec / len(samples) if samples else 0
        samples_correct = correct_count if getattr(self, "_evaluation_enabled", True) else None
    
        task_end = datetime.utcnow().isoformat()
    
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO benchmark_tasks 
                (run_id, model_name, task_name, category, samples_total, samples_correct, 
                 accuracy, avg_ttft_ms, avg_tokens_per_sec, started_at, completed_at,
                 disk_io_avg_mbps, disk_io_max_mbps, disk_metrics_available,
                 gpu_util_avg, gpu_util_max, gpu_metrics_available,
                 gpu_temp_avg, gpu_temp_max,
                 cpu_util_avg, cpu_util_max, cpu_metrics_available)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (self.run_id, model_name, task_name, task_config['category'], 
                  len(samples), samples_correct, accuracy, avg_ttft, avg_tps, task_start, task_end,
                  resource_final["disk_io"]["avg"], resource_final["disk_io"]["max"],
                  1 if resource_final["disk_io"]["available"] else 0,
                  resource_final["gpu_util"]["avg"], resource_final["gpu_util"]["max"],
                  1 if resource_final["gpu_util"]["available"] else 0,
                  resource_final["gpu_temp"]["avg"], resource_final["gpu_temp"]["max"],
                  resource_final["cpu_util"]["avg"], resource_final["cpu_util"]["max"],
                  1 if resource_final["cpu_util"]["available"] else 0))
        if not resource_final["gpu_util"]["available"] or not resource_final["disk_io"]["available"] or not resource_final["gpu_temp"]["available"] or not resource_final["cpu_util"]["available"]:
            logger.warning(
                "Task telemetry incomplete model=%s task=%s gpu_util=%s gpu_temp=%s disk_io=%s cpu_util=%s",
                model_name,
                task_name,
                "ok" if resource_final["gpu_util"]["available"] else "missing",
                "ok" if resource_final["gpu_temp"]["available"] else "missing",
                "ok" if resource_final["disk_io"]["available"] else "missing",
                "ok" if resource_final["cpu_util"]["available"] else "missing",
            )
        if accuracy is None:
            logger.info("    Accuracy: skipped (--no-evaluation)")
        else:
            logger.info(f"    Accuracy: {accuracy:.1%} ({correct_count}/{len(samples)})")

    def _run_chat_task(self, model_name: str, task_config: dict, step_offset: int, total_steps: int):
        """Run a single chat task (multi-turn dialogs) with progress updates."""
        task_name = task_config['name']
        logger.info(f"  Chat task: {task_name}")
        dataset_label = Path(task_config['dataset']).name
        self.state["current_task"] = task_name
        self.state["current_task_label"] = task_name
        self.state["current_sample_id"] = None
        self.state["current_dialog_id"] = None
    
        dataset_path = Path(__file__).parent / task_config['dataset']
        if not dataset_path.exists():
            logger.warning(f"Dataset not found: {dataset_path}, skipping...")
            return
        
        # Clear chat results unless task is already completed (skip check happens in runner)
        # When resuming, we preserve completed tasks but re-run incomplete ones from scratch
        if not self.resume_run or not self._is_chat_task_completed(model_name, task_config):
            self._clear_chat_results(model_name, task_name)
    
        with open(dataset_path) as f:
            dialogs = [json.loads(line) for line in f]
    
        # Light mode: filter to only the hardest dialog
        if self.light_mode:
            hardest = [d for d in dialogs if d.get('is_hardest')]
            if hardest:
                dialogs = hardest
                logger.info(f"    Light mode: using {len(dialogs)} hardest dialog(s)")
            else:
                # Fallback: use last dialog (usually more complex)
                dialogs = dialogs[-1:]
                logger.info(f"    Light mode: no 'is_hardest' flag, using last dialog")

        evaluator = self.evaluators['chat']
        decoding_config = self.config['decoding_configs'][task_config['config']]

        single_turn = bool(dialogs) and 'turns' not in dialogs[0] and (
            'question' in dialogs[0] or 'prompt' in dialogs[0]
        )
        total_turns = len(dialogs) if single_turn else sum(len(dialog.get('turns', [])) for dialog in dialogs)
        self.state["tasks_total"] = total_turns
        self.state["task_index"] = 0
        self._update_state()
        turn_counter = 0

        if single_turn:
            start_index = 0
            if self.resume_run and self._resume_task:
                if self._resume_task.get("model_name") == model_name and self._resume_task.get("dataset_name") == task_name:
                    resume_dialog_id = self._resume_task.get("dialog_id") or self._resume_task.get("task_name")
                    for idx, sample in enumerate(dialogs):
                        dialog_id = sample.get('id', f"{task_name}_{idx}")
                        if dialog_id == resume_dialog_id:
                            start_index = idx
                            break
                    self._resume_task = None
                    if start_index:
                        self.state["task_index"] = start_index
                        self._update_state()
            if start_index:
                turn_counter = start_index
            for idx, sample in enumerate(dialogs[start_index:], start=start_index):
                turn_counter += 1
                self.state["task_index"] = turn_counter
                self.state["progress_percent"] = self._get_current_progress()
                self._update_state()
                dialog_id = sample.get('id', f"{task_name}_{idx}")
                self.state["current_dialog_id"] = dialog_id
                self.state["current_task_label"] = f"dialog {dialog_id}"
                self.state["current_sample_id"] = None
                self._update_state()
                user_msg = sample.get('question') or sample.get('prompt') or ''
                expected = sample.get('answer') or sample.get('answer_type')
                grading = sample.get('grading')

                request_meta = {
                    "model": model_name,
                    "task": task_name,
                    "dataset": dataset_label,
                    "dialog_id": dialog_id,
                    "turn_idx": 0,
                    "kind": "chat"
                }
                position = f"dialog={idx + 1}/{len(dialogs)} id={dialog_id}"
                self._mark_task_started(model_name, task_name, dialog_id, task_label=f"dialog {dialog_id}")
                self._log_request_trace("start", model_name, dataset_label, task_name, position, None, {}, expected)
                dialog_start = datetime.utcnow().isoformat()
                resource_totals = self._init_resource_totals()
                try:
                    response, metrics = self._call_ollama_with_metrics(
                        model_name,
                        [{"role": "user", "content": user_msg}],
                        decoding_config,
                        multi_turn=True,
                        request_meta=request_meta,
                        mode_override=self._mode_override_for_task(task_config),
                    )
                except Exception as e:
                    logger.error(f"Chat sample {dialog_id} failed: {e}")
                    response = f"[error] {e}"
                    metrics = {
                        'ttft_ms': 0,
                        'tokens_per_sec': 0,
                        'total_time_ms': 0,
                        'input_tokens': len(str(user_msg).split()),
                        'output_tokens': 0
                    }
                if getattr(self, "_evaluation_enabled", True):
                    if grading:
                        eval_result = self._evaluate_with_grading(response, expected, grading, sample)
                        correct = eval_result.get('correct', False)
                    else:
                        eval_result = evaluator.evaluate_turn(response, {})
                        correct = eval_result.get('all_compliant', False)
                else:
                    eval_result = {
                        "all_compliant": None,
                        "evaluation_skipped": True,
                        "compliance": {"grading": None, "compliant": None},
                        "violations": ["evaluation skipped (--no-evaluation)"],
                    }
                    correct = None

                if correct is True:
                    turn_counter_compliant = 1
                elif correct is False:
                    turn_counter_compliant = 0
                    eval_result.setdefault('violations', []).append("grading failed")
                else:
                    turn_counter_compliant = -1
                eval_result.setdefault('compliance', {})["grading"] = correct
                eval_result.setdefault("compliance", {}).setdefault("compliant", correct)
                eval_result["all_compliant"] = correct

                self._accumulate_resource_totals(resource_totals, metrics.get('resource_stats'))
                resource_final = self._finalize_resource_totals(resource_totals)
                self._record_chat_turn(
                    model_name,
                    task_name,
                    dialog_id,
                    0,
                    user_msg,
                    response,
                    metrics,
                    eval_result,
                )
                self._record_chat_dialog(
                    model_name,
                    task_name,
                    dialog_id,
                    1,
                    turn_counter_compliant,
                    [metrics['ttft_ms']],
                    [metrics['tokens_per_sec']],
                    dialog_start,
                    resource_final,
                )
                self._log_request_trace("end", model_name, dataset_label, task_name, position, response, metrics, expected)

                self.state["recent_metrics"].append({
                    "timestamp": datetime.utcnow().isoformat(),
                    "ttft_ms": metrics['ttft_ms'],
                    "tokens_per_sec": metrics['tokens_per_sec']
                })
                if len(self.state["recent_metrics"]) > 100:
                    self.state["recent_metrics"].pop(0)
                self._record_gpu_utilization()
                self._update_state()

                if isinstance(response, str) and response.startswith("[error]"):
                    status = "error"
                elif metrics.get("timeout_killed"):
                    status = "error"
                elif not getattr(self, "_evaluation_enabled", True):
                    status = "completed_no_eval"
                else:
                    status = "passed" if eval_result.get("all_compliant") else "failed"
                self._mark_task_completed(model_name, task_name, dialog_id, status, error=metrics.get("timeout_reason"))

                # Cooldown removed - GPU temperature guard handles thermal management
            self.state["current_dialog_id"] = None
            self.state["current_task_label"] = None
            self._update_state()
            return
        start_dialog_index = 0
        start_turn_index = None
        resume_dialog_id = None
        if self.resume_run and self._resume_task:
            if self._resume_task.get("model_name") == model_name and self._resume_task.get("dataset_name") == task_name:
                resume_dialog_id = self._resume_task.get("dialog_id")
                resume_turn = self._resume_task.get("turn_index")
                if resume_dialog_id:
                    for idx, dialog in enumerate(dialogs):
                        if dialog.get("id") == resume_dialog_id:
                            start_dialog_index = idx
                            start_turn_index = resume_turn
                            break
                self._resume_task = None
        if start_dialog_index or start_turn_index:
            resume_completed = 0
            for dialog in dialogs[:start_dialog_index]:
                resume_completed += len(dialog.get("turns", []))
            if start_turn_index:
                resume_completed += max(start_turn_index - 1, 0)
            turn_counter = resume_completed
            if resume_completed:
                self.state["task_index"] = resume_completed
                self._update_state()
        for idx, dialog in enumerate(dialogs):
            if idx < start_dialog_index:
                continue
            # Update progress from database
            self.state["progress_percent"] = self._get_current_progress()
            self._update_state()
            dialog_id = dialog['id']
            self.state["current_dialog_id"] = dialog_id
            self.state["current_sample_id"] = None
            self._update_state()
            turns = dialog['turns']
        
            dialog_start = datetime.utcnow().isoformat()
            context = []
            ttfts = []
            tps_values = []
            compliant_turns = 0 if getattr(self, "_evaluation_enabled", True) else -1
            resource_totals = self._init_resource_totals()
        
            for turn_idx, turn in enumerate(turns):
                if start_turn_index is not None and resume_dialog_id == dialog_id:
                    if turn_idx + 1 < start_turn_index:
                        continue
                turn_counter += 1
                self.state["task_index"] = turn_counter
                self.state["current_task_label"] = f"dialog {dialog_id} turn {turn_idx + 1}"
                user_msg = turn['user']
                turn_spec = turn.get('spec', {})
            
                # Build conversation context
                context.append({"role": "user", "content": user_msg})
            
                request_meta = {
                    "model": model_name,
                    "task": task_name,
                    "dataset": dataset_label,
                    "dialog_id": dialog_id,
                    "turn_idx": turn_idx,
                    "kind": "chat"
                }
                position = f"dialog={idx + 1}/{len(dialogs)} turn={turn_idx + 1}/{len(turns)} id={dialog_id}"
                task_name_id = f"{dialog_id}:{turn_idx + 1}"
                self._mark_task_started(
                    model_name,
                    task_name,
                    task_name_id,
                    task_label=f"dialog {dialog_id} turn {turn_idx + 1}",
                )
                self._log_request_trace("start", model_name, dataset_label, task_name, position, None, {})
                try:
                    # Call model
                    response, metrics = self._call_ollama_with_metrics(
                        model_name, context, decoding_config, multi_turn=True, request_meta=request_meta
                    )
                except EmptyResponseError as e:
                    logger.error(f"Chat turn {dialog_id}:{turn_idx} returned empty response: {e}")
                    if self._stop_on_empty_response:
                        raise
                    response = f"[error] {e}"
                    metrics = {
                        'ttft_ms': 0,
                        'tokens_per_sec': 0,
                        'total_time_ms': 0,
                        'input_tokens': len(str(context).split()),
                        'output_tokens': 0
                    }
                except Exception as e:
                    logger.error(f"Chat turn {dialog_id}:{turn_idx} failed: {e}")
                    response = f"[error] {e}"
                    metrics = {
                        'ttft_ms': 0,
                        'tokens_per_sec': 0,
                        'total_time_ms': 0,
                        'input_tokens': len(str(context).split()),
                        'output_tokens': 0
                    }
                self._log_request_trace("end", model_name, dataset_label, task_name, position, response, metrics)

                context.append({"role": "assistant", "content": response})
            
                # Evaluate turn
                if getattr(self, "_evaluation_enabled", True):
                    eval_result = evaluator.evaluate_turn(response, turn_spec)
                    if eval_result.get('all_compliant'):
                        compliant_turns += 1
                else:
                    eval_result = {
                        "all_compliant": None,
                        "evaluation_skipped": True,
                        "compliance": {"compliant": None},
                        "violations": ["evaluation skipped (--no-evaluation)"],
                    }
            
                ttfts.append(metrics['ttft_ms'])
                tps_values.append(metrics['tokens_per_sec'])
                self._accumulate_resource_totals(resource_totals, metrics.get('resource_stats'))
                self._update_temp_summaries(metrics.get('resource_stats'))

                # Update metrics in state
                self.state["recent_metrics"].append({
                    "timestamp": datetime.utcnow().isoformat(),
                    "ttft_ms": metrics['ttft_ms'],
                    "tokens_per_sec": metrics['tokens_per_sec']
                })
                if len(self.state["recent_metrics"]) > 100:
                    self.state["recent_metrics"].pop(0)
                self._record_gpu_utilization()
                self._update_state()
            
                # Record turn
                self._record_chat_turn(model_name, task_name, dialog_id, turn_idx, 
                                      user_msg, response, metrics, eval_result)
                if isinstance(response, str) and response.startswith("[error]"):
                    status = "error"
                elif metrics.get("timeout_killed"):
                    status = "error"
                elif not getattr(self, "_evaluation_enabled", True):
                    status = "completed_no_eval"
                else:
                    status = "passed" if eval_result.get("all_compliant") else "failed"
                self._mark_task_completed(model_name, task_name, task_name_id, status, error=metrics.get("timeout_reason"))

                # Cooldown removed - GPU temperature guard handles thermal management
        
            # Record dialog aggregates
            resource_final = self._finalize_resource_totals(resource_totals)
            self._record_chat_dialog(model_name, task_name, dialog_id, len(turns), 
                                    compliant_turns, ttfts, tps_values, dialog_start,
                                    resource_final)
            self.state["current_dialog_id"] = None
            self.state["current_task_label"] = None
            self._update_state()
