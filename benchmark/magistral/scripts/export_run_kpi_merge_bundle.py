#!/usr/bin/env python3
"""Export KPI-focused run bundle for external report merging.

This export intentionally excludes heavy prompt/response text fields and keeps
stable merge keys so an external LLM (or post-processor) can join with an
answer-level file.
"""

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


JSONISH_COLUMNS = {
    "hardware_profile",
    "compliance_json",
    "flags",
    "recommendations",
    "models_json",
    "datasets_json",
}


def _try_parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if not ((text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]"))):
        return value
    try:
        return json.loads(text)
    except Exception:
        return value


def _rows(con: sqlite3.Connection, query: str, params: tuple[Any, ...]) -> List[Dict[str, Any]]:
    cur = con.execute(query, params)
    out: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        rec: Dict[str, Any] = {}
        for key in row.keys():
            val = row[key]
            rec[key] = _try_parse_json(val) if key in JSONISH_COLUMNS else val
        out.append(rec)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Export KPI-only merge bundle for one run")
    parser.add_argument("--db", default="db/benchmark.db")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    try:
        run_meta = _rows(
            con,
            """
            SELECT run_id, started_at, completed_at, status, hardware_profile, ollama_version, notes
            FROM benchmark_runs
            WHERE run_id = ?
            """,
            (args.run_id,),
        )
        if not run_meta:
            raise SystemExit(f"run_id not found: {args.run_id}")

        model_warmup = _rows(
            con,
            """
            SELECT run_id, model_name, model_id, parameters_b, quantization, size_gb, warmup_ttft_ms
            FROM benchmark_models
            WHERE run_id = ?
            ORDER BY model_name
            """,
            (args.run_id,),
        )

        shortform_task_kpis = _rows(
            con,
            """
            SELECT run_id, model_name, task_name, category, samples_total, samples_correct, accuracy,
                   avg_ttft_ms, avg_tokens_per_sec, avg_total_time_ms,
                   disk_io_avg_mbps, disk_io_max_mbps, disk_metrics_available,
                   gpu_util_avg, gpu_util_max, gpu_metrics_available,
                   gpu_temp_avg, gpu_temp_max,
                   cpu_util_avg, cpu_util_max, cpu_metrics_available,
                   started_at, completed_at
            FROM benchmark_tasks
            WHERE run_id = ?
            ORDER BY model_name, task_name
            """,
            (args.run_id,),
        )

        shortform_sample_kpis = _rows(
            con,
            """
            SELECT run_id, model_name, task_name, sample_id,
                   correct, input_tokens, output_tokens, tokens_out,
                   ttft_ms, tokens_per_sec, total_time_ms,
                   timeout_killed, timeout_type, timeout_reason, tokens_before_timeout,
                   cooldown_wait_sec, rerun_flag, eval_method, error, created_at
            FROM benchmark_samples
            WHERE run_id = ?
            ORDER BY model_name, task_name, sample_id
            """,
            (args.run_id,),
        )

        chat_dialog_kpis = _rows(
            con,
            """
            SELECT run_id, model_name, dataset, dialog_id,
                   turns_total, turns_compliant, session_compliant,
                   avg_ttft_ms, p95_ttft_ms, avg_tokens_per_sec, jitter_ms, late_turn_recall,
                   disk_io_avg_mbps, disk_io_max_mbps, disk_metrics_available,
                   gpu_util_avg, gpu_util_max, gpu_metrics_available,
                   gpu_temp_avg, gpu_temp_max,
                   cpu_util_avg, cpu_util_max, cpu_metrics_available,
                   started_at, completed_at, error
            FROM chat_dialogs
            WHERE run_id = ?
            ORDER BY model_name, dataset, dialog_id
            """,
            (args.run_id,),
        )

        chat_turn_kpis = _rows(
            con,
            """
            SELECT run_id, model_name, dataset, dialog_id, turn_index,
                   input_tokens, output_tokens, ttft_ms, tokens_per_sec, total_time_ms, jitter_ms,
                   compliance_json, violations,
                   timeout_killed, timeout_type, timeout_reason, tokens_before_timeout,
                   cooldown_wait_sec, rerun_flag, error, created_at
            FROM chat_turns
            WHERE run_id = ?
            ORDER BY model_name, dataset, dialog_id, turn_index
            """,
            (args.run_id,),
        )

        run_task_status = _rows(
            con,
            """
            SELECT run_id, model_name, dataset_name, task_name, task_label, task_kind,
                   sample_id, dialog_id, turn_index, status, started_at, completed_at, error
            FROM benchmark_run_tasks
            WHERE run_id = ?
            ORDER BY model_name, dataset_name, task_index
            """,
            (args.run_id,),
        )

        run_scope = _rows(
            con,
            """
            SELECT run_id, config_path, config_name, light_mode, models_json, datasets_json,
                   models_total, datasets_total, tasks_total
            FROM benchmark_run_scope
            WHERE run_id = ?
            """,
            (args.run_id,),
        )

        judge_opinions = _rows(
            con,
            """
            SELECT run_id, model_name, task_name, sample_id, judge, is_aggregate,
                   verdict, quality, speed, resources, flags, recommendations, reasoning, created_at
            FROM benchmark_opinions
            WHERE run_id = ?
            ORDER BY judge, is_aggregate, model_name, task_name, sample_id
            """,
            (args.run_id,),
        )
    finally:
        con.close()

    bundle = {
        "meta": {
            "run_id": args.run_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source_db": str(Path(args.db).resolve()),
            "purpose": "KPI merge bundle for external report generation",
        },
        "merge_keys": {
            "shortform_item": ["run_id", "model_name", "task_name", "sample_id"],
            "chat_turn_item": ["run_id", "model_name", "dataset", "dialog_id", "turn_index"],
            "shortform_task": ["run_id", "model_name", "task_name"],
            "chat_dialog": ["run_id", "model_name", "dataset", "dialog_id"],
            "run_task": ["run_id", "model_name", "dataset_name", "task_name"],
        },
        "counts": {
            "shortform_sample_kpis": len(shortform_sample_kpis),
            "shortform_task_kpis": len(shortform_task_kpis),
            "chat_turn_kpis": len(chat_turn_kpis),
            "chat_dialog_kpis": len(chat_dialog_kpis),
            "run_task_status": len(run_task_status),
            "judge_opinions": len(judge_opinions),
        },
        "run_meta": run_meta[0],
        "run_scope": run_scope[0] if run_scope else None,
        "model_warmup": model_warmup,
        "shortform_task_kpis": shortform_task_kpis,
        "shortform_sample_kpis": shortform_sample_kpis,
        "chat_dialog_kpis": chat_dialog_kpis,
        "chat_turn_kpis": chat_turn_kpis,
        "run_task_status": run_task_status,
        "judge_opinions": judge_opinions,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")

    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "out": str(out_path),
                "counts": bundle["counts"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
