#!/usr/bin/env python3
"""Offline LLM-as-judge re-evaluation for benchmark sample rows."""

import argparse
import json
import re
import sqlite3
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


def _load_sample_metadata(root: Path) -> Dict[str, Dict[str, Any]]:
    meta: Dict[str, Dict[str, Any]] = {}
    for path in sorted(root.glob("*.jsonl")):
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line:
                continue
            row = json.loads(line)
            sid = row.get("id")
            if isinstance(sid, str) and sid:
                meta[sid] = {
                    "grading": row.get("grading"),
                    "answer": row.get("answer") or row.get("expected_content"),
                    "question": row.get("question") or row.get("prompt"),
                    "dataset_file": str(path),
                }
    return meta


def _parse_json_blob(text: str) -> Optional[dict]:
    text = (text or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    candidates = re.findall(r"\{[\s\S]*?\}", text)
    for cand in sorted(candidates, key=lambda s: ('"pass"' not in s.lower(), len(s))):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    pass_m = re.search(r'"pass"\s*:\s*(true|false)', text, re.IGNORECASE)
    if not pass_m:
        return None
    conf_m = re.search(r'"confidence"\s*:\s*([0-9]+(?:\.[0-9]+)?)', text, re.IGNORECASE)
    reason_m = re.search(r'"reason"\s*:\s*"([^"]*)"', text, re.IGNORECASE | re.DOTALL)
    return {
        "pass": pass_m.group(1).lower() == "true",
        "confidence": float(conf_m.group(1)) if conf_m else None,
        "reason": reason_m.group(1) if reason_m else "",
    }


def _normalize_text(value: Any) -> str:
    text = str(value or "")
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(answer|final answer)\s*:\s*", "", text, flags=re.IGNORECASE)
    return text


def _judge_parse_repair(
    base_url: str,
    judge_model: str,
    raw_content: str,
    timeout_sec: int,
) -> Optional[dict]:
    payload = {
        "model": judge_model,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Rewrite the input as valid JSON only with schema "
                    "{\"pass\": true/false, \"confidence\": 0-1, \"reason\": \"...\"}. "
                    "No extra text."
                ),
            },
            {"role": "user", "content": raw_content or ""},
        ],
        "options": {"temperature": 0, "top_p": 1, "top_k": 1, "num_predict": 120},
    }
    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/api/chat",
            json=payload,
            timeout=timeout_sec,
        )
        resp.raise_for_status()
        data = resp.json()
        content = ""
        if isinstance(data.get("message"), dict):
            content = data["message"].get("content", "")
        else:
            content = data.get("response", "")
        return _parse_json_blob(content)
    except Exception:
        return None


def _deterministic_normalized_pass(sample: Dict[str, Any]) -> Optional[bool]:
    grading = str(sample.get("grading") or "").strip().lower()
    expected = _normalize_text(sample.get("expected"))
    response = _normalize_text(sample.get("response"))
    if not grading:
        return None
    if grading.startswith("exact_match"):
        return expected.lower() == response.lower()
    if grading.startswith("contains:"):
        tokens = [t.strip().lower() for t in grading.split(":", 1)[1].split(",") if t.strip()]
        low = response.lower()
        return all(tok in low for tok in tokens)
    if grading.startswith("numerical_tolerance:"):
        m_tol = re.search(r"numerical_tolerance:\s*([0-9]+(?:\.[0-9]+)?)", grading)
        if not m_tol:
            return None
        tol = float(m_tol.group(1))
        m_exp = re.search(r"-?[0-9]+(?:\.[0-9]+)?", expected)
        m_res = re.search(r"-?[0-9]+(?:\.[0-9]+)?", response)
        if not m_exp or not m_res:
            return None
        try:
            return abs(float(m_exp.group(0)) - float(m_res.group(0))) <= tol
        except Exception:
            return None
    return None


def _judge_one(
    base_url: str,
    judge_model: str,
    sample: Dict[str, Any],
    timeout_sec: int,
) -> Dict[str, Any]:
    system = (
        "You are a strict but fair benchmark grader. "
        "Judge semantic correctness against question intent and grading rule. "
        "Treat obvious synonyms/paraphrases as correct. "
        "Ignore trivial formatting differences like trailing newlines, markdown wrappers, and case-only changes. "
        "Return JSON only: {\"pass\": true/false, \"confidence\": 0-1, \"reason\": \"...\"}."
    )
    norm_expected = _normalize_text(sample.get("expected"))
    norm_prompt = _normalize_text(sample.get("prompt"))
    norm_response = _normalize_text(sample.get("response"))
    payload = {
        "model": judge_model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task_name": sample.get("task_name"),
                        "sample_id": sample.get("sample_id"),
                        "grading": sample.get("grading"),
                        "expected": sample.get("expected"),
                        "question": sample.get("prompt"),
                        "response": sample.get("response"),
                        "normalized_expected": norm_expected,
                        "normalized_question": norm_prompt,
                        "normalized_response": norm_response,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "options": {
            "temperature": 0,
            "top_p": 1,
            "top_k": 1,
            "num_predict": 220,
        },
    }
    started = time.time()
    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/api/chat",
            json=payload,
            timeout=timeout_sec,
        )
        resp.raise_for_status()
        data = resp.json()
        content = ""
        if isinstance(data.get("message"), dict):
            content = data["message"].get("content", "")
        else:
            content = data.get("response", "")
        parsed = _parse_json_blob(content)
        if not parsed or "pass" not in parsed:
            repaired = _judge_parse_repair(base_url, judge_model, content, timeout_sec)
            if repaired and "pass" in repaired:
                parsed = repaired
            else:
                return {
                    "judge_pass": False,
                    "judge_confidence": None,
                    "judge_reason": f"judge_parse_failed: {content[:180]}",
                    "judge_elapsed_sec": round(time.time() - started, 2),
                    "judge_raw_output": content[:500],
                    "normalized_expected": norm_expected,
                    "normalized_response": norm_response,
                }
        return {
            "judge_pass": bool(parsed.get("pass")),
            "judge_confidence": parsed.get("confidence"),
            "judge_reason": str(parsed.get("reason") or ""),
            "judge_elapsed_sec": round(time.time() - started, 2),
            "judge_raw_output": content[:500],
            "normalized_expected": norm_expected,
            "normalized_response": norm_response,
        }
    except Exception as exc:
        return {
            "judge_pass": False,
            "judge_confidence": None,
            "judge_reason": f"judge_error: {exc}",
            "judge_elapsed_sec": round(time.time() - started, 2),
            "normalized_expected": norm_expected,
            "normalized_response": norm_response,
        }


def _dataset_label(path_or_name: Any) -> str:
    text = str(path_or_name or "")
    if "/" in text:
        text = Path(text).stem
    if text.endswith("_10"):
        text = text[:-3]
    return text


def _row_is_transport_ok(row: Dict[str, Any]) -> bool:
    return (
        int(row.get("http_status") or 0) == 200
        and not row.get("exception")
        and int(row.get("response_chars") or 0) > 0
    )


def _group_pass_rate(rows: List[Dict[str, Any]], key: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        bucket = str(row.get(key) or "unknown")
        grp = out.setdefault(bucket, {"rows": 0, "semantic_denominator": 0, "rejudged_pass": 0, "pass_rate": None})
        grp["rows"] += 1
        if row.get("transport_ok"):
            grp["semantic_denominator"] += 1
            grp["rejudged_pass"] += 1 if row.get("rejudged_correct") == 1 else 0
    for grp in out.values():
        denom = grp["semantic_denominator"]
        grp["pass_rate"] = (grp["rejudged_pass"] / denom) if denom else None
    return out


def _elapsed_percentile(rows: List[Dict[str, Any]], p: float) -> Optional[float]:
    vals = sorted(float(r.get("elapsed_sec")) for r in rows if r.get("elapsed_sec") is not None)
    if not vals:
        return None
    idx = int(round((p / 100.0) * (len(vals) - 1)))
    idx = max(0, min(idx, len(vals) - 1))
    return round(vals[idx], 3)


def _build_summary(
    results: List[Dict[str, Any]],
    judge_model: str,
    judge_name: str,
    run_id: Optional[str],
    source: str,
) -> Dict[str, Any]:
    timeout_rows = [
        r for r in results
        if "timed out" in str(r.get("judge_reason") or "").lower()
    ]
    parse_fail_rows = [
        r for r in results
        if str(r.get("judge_reason") or "").startswith("judge_parse_failed")
    ]
    transport_ok_rows = [r for r in results if r.get("transport_ok")]
    transport_fail_rows = [r for r in results if not r.get("transport_ok")]
    error_only_rows = [r for r in results if str(r.get("response") or "").startswith("[error]")]
    semantic_denominator = len(transport_ok_rows) - len(error_only_rows)
    if semantic_denominator < 0:
        semantic_denominator = 0

    summary: Dict[str, Any] = {
        "run_id": run_id,
        "source": source,
        "judge_model": judge_model,
        "judge_name": judge_name,
        "rows": len(results),
        "transport_ok_rows": len(transport_ok_rows),
        "transport_fail_rows": len(transport_fail_rows),
        "error_only_rows": len(error_only_rows),
        "original_correct": sum(1 for r in results if r.get("original_correct") == 1),
        "rejudged_correct": sum(1 for r in results if r.get("rejudged_correct") == 1),
        "flipped_total": sum(1 for r in results if r.get("flipped")),
        "flipped_to_pass": sum(
            1
            for r in results
            if r.get("flipped") and r.get("original_correct") == 0 and r.get("rejudged_correct") == 1
        ),
        "flipped_to_fail": sum(
            1
            for r in results
            if r.get("flipped") and r.get("original_correct") == 1 and r.get("rejudged_correct") == 0
        ),
        "judge_timeout_rows": len(timeout_rows),
        "judge_parse_fail_rows": len(parse_fail_rows),
        "judge_timeout_rate": (len(timeout_rows) / len(results)) if results else 0.0,
        "judge_parse_fail_rate": (len(parse_fail_rows) / len(results)) if results else 0.0,
        "judge_instability_rate": ((len(timeout_rows) + len(parse_fail_rows)) / len(results)) if results else 0.0,
        "semantic_denominator": semantic_denominator,
        "semantic_pass_rate": (
            sum(1 for r in results if r.get("transport_ok") and r.get("rejudged_correct") == 1) / semantic_denominator
            if semantic_denominator
            else None
        ),
        "transport_elapsed_p50_sec": _elapsed_percentile(results, 50),
        "transport_elapsed_p90_sec": _elapsed_percentile(results, 90),
    }

    summary["by_dataset"] = _group_pass_rate(results, "dataset_name")
    summary["by_grading"] = _group_pass_rate(results, "grading_type")
    summary["failure_taxonomy"] = {
        "transport_timeout": sum(
            1 for r in results
            if "timed out" in str(r.get("exception") or "").lower()
        ),
        "transport_http_error": sum(
            1 for r in results
            if int(r.get("http_status") or 0) not in (0, 200)
        ),
        "transport_empty": sum(
            1 for r in results
            if int(r.get("http_status") or 0) == 200 and int(r.get("response_chars") or 0) == 0
        ),
        "transport_other_exception": sum(
            1 for r in results
            if r.get("exception")
            and "timed out" not in str(r.get("exception") or "").lower()
        ),
        "judge_error": sum(
            1 for r in results
            if str(r.get("judge_reason") or "").startswith("judge_error")
        ),
        "judge_parse_failed": len(parse_fail_rows),
        "semantic_fail": sum(
            1 for r in results
            if r.get("transport_ok") and r.get("rejudged_correct") == 0
        ),
    }

    discrepancy_rows = [
        r for r in results if r.get("transport_ok") and r.get("rejudged_correct") == 0
    ]
    ambiguity_rows = [
        r for r in results
        if "ambig" in str(r.get("judge_reason") or "").lower()
        or "inconsisten" in str(r.get("judge_reason") or "").lower()
        or "unclear" in str(r.get("judge_reason") or "").lower()
    ]
    summary["discrepancy_count"] = len(discrepancy_rows)
    summary["ambiguous_count"] = len(ambiguity_rows)
    return summary


def _load_rows_from_db(db_path: str, run_id: str) -> List[Dict[str, Any]]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT run_id, model_name, task_name, sample_id, prompt, expected, response, correct, created_at
        FROM benchmark_samples
        WHERE run_id = ?
        ORDER BY created_at, id
        """,
        (run_id,),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def _load_rows_from_input_json(input_path: str) -> List[Dict[str, Any]]:
    obj = json.loads(Path(input_path).read_text())
    raw_rows = obj.get("results") if isinstance(obj, dict) else None
    if not isinstance(raw_rows, list):
        raise SystemExit(f"input json missing list field 'results': {input_path}")
    out: List[Dict[str, Any]] = []
    for idx, row in enumerate(raw_rows):
        if not isinstance(row, dict):
            continue
        rec = dict(row)
        rec.setdefault("sample_id", f"adhoc_{idx}")
        rec.setdefault("task_name", _dataset_label(rec.get("dataset_file")))
        rec.setdefault("dataset_name", _dataset_label(rec.get("dataset_file")))
        rec.setdefault("model_name", rec.get("model"))
        rec.setdefault("run_id", rec.get("run_id"))
        rec.setdefault("prompt", rec.get("prompt") or "")
        rec.setdefault("expected", rec.get("expected"))
        if "response" not in rec:
            rec["response"] = rec.get("response_preview") or ""
        rec.setdefault("correct", None)
        out.append(rec)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-evaluate benchmark run with LLM judge")
    parser.add_argument("--db", default="db/benchmark.db")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--run-id", help="Benchmark DB run_id")
    input_group.add_argument("--input-json", help="Ad-hoc JSON artifact containing results[] rows")
    parser.add_argument("--judge-model", default="gemma3:4b")
    parser.add_argument(
        "--judge-name",
        default="",
        help="Judge identifier for DB persistence (default: same as --judge-model)",
    )
    parser.add_argument("--judge-base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--judge-timeout-sec", type=int, default=60)
    parser.add_argument(
        "--datasets-root",
        default="benchmark/datasets/canonical_full_80",
        help="Folder used to resolve sample grading metadata",
    )
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--write-db",
        action="store_true",
        help="Persist sample-level and aggregate judge opinions into benchmark_opinions (run-id mode only)",
    )
    args = parser.parse_args()
    judge_name = (args.judge_name or "").strip() or args.judge_model

    sample_meta = _load_sample_metadata(Path(args.datasets_root))
    if args.run_id:
        rows = _load_rows_from_db(args.db, args.run_id)
        source = "db_run_id"
    else:
        rows = _load_rows_from_input_json(args.input_json)
        source = "input_json"

    if not rows:
        if args.run_id:
            raise SystemExit(f"no samples found for run_id={args.run_id}")
        raise SystemExit(f"no samples found in input-json={args.input_json}")

    results: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        rec = dict(row)
        meta = sample_meta.get(rec["sample_id"], {})
        rec["grading"] = meta.get("grading") or rec.get("grading")
        rec["dataset_file"] = meta.get("dataset_file") or rec.get("dataset_file")
        if not rec.get("expected"):
            rec["expected"] = meta.get("answer")
        if not rec.get("prompt"):
            rec["prompt"] = meta.get("question")
        rec["dataset_name"] = _dataset_label(rec.get("dataset_file") or rec.get("task_name"))
        rec["grading_type"] = str(rec.get("grading") or "").split(":", 1)[0] or "unknown"
        rec["transport_ok"] = _row_is_transport_ok(rec)

        judge = _judge_one(
            args.judge_base_url,
            args.judge_model,
            rec,
            args.judge_timeout_sec,
        )
        rec.update(judge)
        det = _deterministic_normalized_pass(rec)
        rec["deterministic_normalized_pass"] = det
        orig = rec.get("correct")
        rec["original_correct"] = int(orig) if orig is not None else None
        # Deterministic normalized pass prevents judge over-penalizing trivial format issues.
        rec["rejudged_correct"] = 1 if (det is True or rec["judge_pass"]) else 0
        rec["flipped"] = (
            rec["original_correct"] is not None
            and rec["original_correct"] != rec["rejudged_correct"]
        )
        results.append(rec)

        if idx % 25 == 0:
            print(f"[progress] {idx}/{len(rows)}")

    summary: Dict[str, Any] = {
        **_build_summary(results, args.judge_model, judge_name, args.run_id, source),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    per_model: Dict[str, Dict[str, int]] = {}
    for r in results:
        model_name = str(r.get("model_name") or "unknown")
        if model_name not in per_model:
            per_model[model_name] = {"rows": 0, "orig_correct": 0, "rejudged_correct": 0, "flipped": 0}
        per_model[model_name]["rows"] += 1
        per_model[model_name]["orig_correct"] += 1 if r["original_correct"] == 1 else 0
        per_model[model_name]["rejudged_correct"] += 1 if r["rejudged_correct"] == 1 else 0
        per_model[model_name]["flipped"] += 1 if r["flipped"] else 0
    summary["per_model"] = per_model

    failed_rows = [
        {
            "dataset_name": r.get("dataset_name"),
            "task_name": r.get("task_name"),
            "sample_id": r.get("sample_id"),
            "expected": r.get("expected"),
            "response": r.get("response"),
            "judge_reason": r.get("judge_reason"),
            "grading": r.get("grading"),
        }
        for r in results
        if r.get("transport_ok") and r.get("rejudged_correct") == 0
    ]
    summary["top_fail_examples"] = failed_rows[:10]
    summary["disagreement_hotspots"] = [
        {
            "dataset_name": dset,
            "pass_rate": vals.get("pass_rate"),
            "semantic_denominator": vals.get("semantic_denominator"),
        }
        for dset, vals in sorted(
            summary.get("by_dataset", {}).items(),
            key=lambda kv: (kv[1].get("pass_rate") is None, kv[1].get("pass_rate") if kv[1].get("pass_rate") is not None else 1.0),
        )[:3]
    ]
    summary["ambiguous_candidates"] = [
        {
            "dataset_name": r.get("dataset_name"),
            "sample_id": r.get("sample_id"),
            "judge_reason": r.get("judge_reason"),
        }
        for r in results
        if "ambig" in str(r.get("judge_reason") or "").lower()
        or "inconsisten" in str(r.get("judge_reason") or "").lower()
        or "unclear" in str(r.get("judge_reason") or "").lower()
    ][:10]

    out = {"summary": summary, "results": results}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")

    if args.write_db and args.run_id:
        con = sqlite3.connect(args.db)
        try:
            # Idempotent write for this run/judge.
            con.execute(
                "DELETE FROM benchmark_opinions WHERE run_id = ? AND judge = ?",
                (args.run_id, judge_name),
            )

            sample_rows = []
            for r in results:
                flags = {
                    "grading": r.get("grading"),
                    "dataset_file": r.get("dataset_file"),
                    "original_correct": r.get("original_correct"),
                    "rejudged_correct": r.get("rejudged_correct"),
                    "judge_confidence": r.get("judge_confidence"),
                    "judge_elapsed_sec": r.get("judge_elapsed_sec"),
                    "deterministic_normalized_pass": r.get("deterministic_normalized_pass"),
                    "flipped": bool(r.get("flipped")),
                }
                sample_rows.append(
                    (
                        r.get("run_id"),
                        r.get("model_name"),
                        r.get("task_name"),
                        judge_name,
                        0,
                        "pass" if r.get("rejudged_correct") == 1 else "fail",
                        None,  # quality
                        None,  # speed
                        None,  # resources
                        json.dumps(flags, ensure_ascii=False),
                        None,  # recommendations
                        r.get("sample_id"),
                        r.get("judge_reason") or "",
                    )
                )

            con.executemany(
                """
                INSERT INTO benchmark_opinions
                (run_id, model_name, task_name, judge, is_aggregate, verdict, quality, speed, resources,
                 flags, recommendations, sample_id, reasoning)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                sample_rows,
            )

            aggregate_rows = []
            for model_name, stats in sorted(per_model.items()):
                rows_n = int(stats.get("rows", 0) or 0)
                score = float(stats.get("rejudged_correct", 0) or 0) / rows_n if rows_n else 0.0
                flags = {
                    "rows": rows_n,
                    "orig_correct": int(stats.get("orig_correct", 0) or 0),
                    "rejudged_correct": int(stats.get("rejudged_correct", 0) or 0),
                    "flipped": int(stats.get("flipped", 0) or 0),
                    "judge_model": args.judge_model,
                }
                aggregate_rows.append(
                    (
                        args.run_id,
                        model_name,
                        "__shortform_total__",
                        judge_name,
                        1,
                        "pass" if score >= 0.5 else "fail",
                        round(score, 6),
                        None,
                        None,
                        json.dumps(flags, ensure_ascii=False),
                        None,
                        None,
                        "aggregate llm-as-judge pass-rate over shortform rows",
                    )
                )

            con.executemany(
                """
                INSERT INTO benchmark_opinions
                (run_id, model_name, task_name, judge, is_aggregate, verdict, quality, speed, resources,
                 flags, recommendations, sample_id, reasoning)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                aggregate_rows,
            )
            con.commit()
        finally:
            con.close()
    elif args.write_db and not args.run_id:
        print("[warn] --write-db ignored for --input-json mode")

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
