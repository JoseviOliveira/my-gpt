#!/usr/bin/env python3
"""Export one benchmark run from SQLite into judge-ready JSON.

The export includes:
- Raw rows for every table containing a `run_id` column.
- A normalized list of shortform/chat items with `external_judge` fields to fill.
"""

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


JSONISH_COLUMNS = {
    "hardware_profile",
    "request_payload",
    "compliance_json",
    "flags",
    "recommendations",
    "last_request_json",
    "recent_metrics_json",
    "recent_gpu_json",
    "recent_gpu_temp_json",
    "recent_cpu_json",
    "recent_disk_io_json",
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


def _normalize_row(row: sqlite3.Row) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in row.keys():
        val = row[key]
        if key in JSONISH_COLUMNS:
            out[key] = _try_parse_json(val)
        else:
            out[key] = val
    return out


def _tables_with_run_id(con: sqlite3.Connection) -> List[str]:
    tables = [
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    ]
    selected: List[str] = []
    for table in tables:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
        if "run_id" in cols:
            selected.append(table)
    return selected


def _fetch_table_rows(con: sqlite3.Connection, table: str, run_id: str) -> List[Dict[str, Any]]:
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
    order_col = "id" if "id" in cols else ("created_at" if "created_at" in cols else None)
    if order_col:
        query = f"SELECT * FROM {table} WHERE run_id = ? ORDER BY {order_col}"
    else:
        query = f"SELECT * FROM {table} WHERE run_id = ?"
    rows = con.execute(query, (run_id,)).fetchall()
    return [_normalize_row(r) for r in rows]


def _judge_template() -> Dict[str, Any]:
    return {
        "verdict": None,  # pass | fail | uncertain
        "confidence": None,  # float 0..1
        "reason": None,  # short explanation
        "issue_type": None,  # semantic_error | format_only | safety | timeout_error | ambiguous | other
        "corrected_answer": None,  # optional canonical corrected answer
        "notes": None,  # optional extra context
        "judge_model": None,  # external model identifier
        "judged_at": None,  # ISO8601 timestamp
    }


def _build_shortform_items(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for r in rows:
        item_id = f"shortform:{r.get('model_name')}:{r.get('task_name')}:{r.get('sample_id')}"
        items.append(
            {
                "item_id": item_id,
                "kind": "shortform",
                "run_id": r.get("run_id"),
                "model_name": r.get("model_name"),
                "task_name": r.get("task_name"),
                "sample_id": r.get("sample_id"),
                "prompt": r.get("prompt"),
                "expected": r.get("expected"),
                "response": r.get("response"),
                "original_correct": r.get("correct"),
                "metrics": {
                    "ttft_ms": r.get("ttft_ms"),
                    "tokens_per_sec": r.get("tokens_per_sec"),
                    "total_time_ms": r.get("total_time_ms"),
                    "timeout_killed": r.get("timeout_killed"),
                    "timeout_reason": r.get("timeout_reason"),
                    "error": r.get("error"),
                },
                "external_judge": _judge_template(),
            }
        )
    return items


def _build_chat_items(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for r in rows:
        item_id = (
            f"chat:{r.get('model_name')}:{r.get('dataset')}:"
            f"{r.get('dialog_id')}:{r.get('turn_index')}"
        )
        items.append(
            {
                "item_id": item_id,
                "kind": "chat_turn",
                "run_id": r.get("run_id"),
                "model_name": r.get("model_name"),
                "dataset": r.get("dataset"),
                "dialog_id": r.get("dialog_id"),
                "turn_index": r.get("turn_index"),
                "user_text": r.get("user_text"),
                "response": r.get("response"),
                "stored_compliance": r.get("compliance_json"),
                "stored_violations": r.get("violations"),
                "metrics": {
                    "ttft_ms": r.get("ttft_ms"),
                    "tokens_per_sec": r.get("tokens_per_sec"),
                    "total_time_ms": r.get("total_time_ms"),
                    "timeout_killed": r.get("timeout_killed"),
                    "timeout_reason": r.get("timeout_reason"),
                    "error": r.get("error"),
                },
                "external_judge": _judge_template(),
            }
        )
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="Export one run to external-judge JSON")
    parser.add_argument("--db", default="db/benchmark.db")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    try:
        tables = _tables_with_run_id(con)
        table_data: Dict[str, List[Dict[str, Any]]] = {}
        for table in tables:
            table_data[table] = _fetch_table_rows(con, table, args.run_id)
    finally:
        con.close()

    run_rows = table_data.get("benchmark_runs", [])
    if not run_rows:
        raise SystemExit(f"run_id not found: {args.run_id}")

    shortform_rows = table_data.get("benchmark_samples", [])
    chat_rows = table_data.get("chat_turns", [])
    shortform_items = _build_shortform_items(shortform_rows)
    chat_items = _build_chat_items(chat_rows)

    payload = {
        "meta": {
            "run_id": args.run_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source_db": str(Path(args.db).resolve()),
            "tables_included": tables,
        },
        "external_judge_contract": {
            "goal": "Fill `external_judge` for each item using semantic evaluation.",
            "required_fields": [
                "verdict",
                "confidence",
                "reason",
                "issue_type",
                "judge_model",
                "judged_at",
            ],
            "allowed_verdict": ["pass", "fail", "uncertain"],
            "allowed_issue_type": [
                "semantic_error",
                "format_only",
                "safety",
                "timeout_error",
                "ambiguous",
                "other",
            ],
        },
        "counts": {
            "shortform_items": len(shortform_items),
            "chat_turn_items": len(chat_items),
            "total_items": len(shortform_items) + len(chat_items),
        },
        "external_judge_items": {
            "shortform": shortform_items,
            "chat_turns": chat_items,
        },
        "tables": table_data,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "out": str(out_path),
                "shortform_items": len(shortform_items),
                "chat_turn_items": len(chat_items),
                "tables": len(tables),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
