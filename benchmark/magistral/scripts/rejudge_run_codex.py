#!/usr/bin/env python3
"""Codex semantic re-evaluation pass for benchmark sample rows.

This pass is deterministic and transparent:
- Normalizes trivial formatting artifacts.
- Applies robust rule-based checks per grading type.
- Includes explicit semantic heuristics for llm_judge tags used in canonical_full_80.
"""

import argparse
import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional


def _normalize_text(value: Any) -> str:
    text = str(value or "")
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(answer|final answer)\s*:\s*", "", text, flags=re.IGNORECASE)
    return text


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


def _has_any(text: str, terms: List[str]) -> bool:
    low = text.lower()
    return any(t.lower() in low for t in terms)


def _has_all(text: str, terms: List[str]) -> bool:
    low = text.lower()
    return all(t.lower() in low for t in terms)


def _judge_llm_tag(tag: str, question: str, response: str) -> tuple[bool, str]:
    low = response.lower()
    tag = tag.lower().strip()

    if tag == "divide_three_groups_strategy":
        ok = (
            (_has_any(low, ["three groups", "3 groups", "group of 4", "four"])) and
            _has_any(low, ["weigh", "balance", "scale"]) and
            _has_any(low, ["heavier", "lighter", "counterfeit"])
        )
        return ok, "llm_judge:divide_three_groups_strategy"

    if tag == "identifies_strawman_and_false_dichotomy":
        ok = _has_any(low, ["straw man", "strawman"]) and _has_any(low, ["false dichotomy", "false dilemma"])
        return ok, "llm_judge:identifies_strawman_and_false_dichotomy"

    if tag == "false_dilemma_and_hasty_generalization":
        ok = _has_any(low, ["false dilemma", "false dichotomy"]) and _has_any(low, ["hasty generalization", "generalization"])
        return ok, "llm_judge:false_dilemma_and_hasty_generalization"

    if tag == "mentions_correlation_not_causation_or_confounding":
        ok = (
            (_has_all(low, ["correlation", "causation"]) and _has_any(low, ["not", "doesn't", "does not"])) or
            _has_any(low, ["confound", "third variable"])
        )
        return ok, "llm_judge:mentions_correlation_not_causation_or_confounding"

    if tag == "economic_inequality_or_enlightenment":
        econ = _has_any(low, ["economic", "inequality", "tax", "bread", "debt", "poverty"])
        enlight = _has_any(low, ["enlightenment", "philosoph", "liberty", "rights"])
        ok = econ or enlight
        return ok, "llm_judge:economic_inequality_or_enlightenment"

    return False, f"llm_judge:unsupported_tag:{tag}"


def _judge_row(sample_id: str, grading: str, expected: str, question: str, response: str) -> tuple[bool, str]:
    if not response:
        return False, "empty_response"

    low = response.lower()
    if "httpconnectionpool(" in low and "timed out" in low:
        return False, "response_is_timeout_error"

    g = (grading or "").strip()
    gl = g.lower()
    if not g:
        return False, "missing_grading"

    if gl.startswith("exact_match"):
        ok = expected.lower() == response.lower()
        return ok, "exact_match_normalized"

    if gl.startswith("contains:"):
        tokens = [t.strip().lower() for t in g.split(":", 1)[1].split(",") if t.strip()]
        # Known synonym buckets in this dataset pack.
        if sample_id == "logic_033":
            ok = _has_any(low, ["both", "mixed"]) or _has_all(low, ["apple", "orange"])
            return ok, "contains_semantic_logic_033"
        if sample_id in {"logic_021", "crit_010", "crit_011", "crit_013"}:
            ok = any(tok in low for tok in tokens)
            return ok, "contains_or_semantic"
        ok = all(tok in low for tok in tokens)
        return ok, "contains_all_tokens"

    if gl.startswith("numerical_tolerance:"):
        mt = re.search(r"numerical_tolerance:\s*([0-9]+(?:\.[0-9]+)?)", gl)
        me = re.search(r"-?[0-9]+(?:\.[0-9]+)?", expected)
        mr = re.search(r"-?[0-9]+(?:\.[0-9]+)?", response)
        if not (mt and me and mr):
            return False, "numerical_parse_failed"
        tol = float(mt.group(1))
        ev = float(me.group(0))
        rv = float(mr.group(0))
        ok = abs(ev - rv) <= tol
        return ok, f"numerical_tolerance:{tol}"

    if gl.startswith("llm_judge:"):
        tag = g.split(":", 1)[1].strip()
        ok, reason = _judge_llm_tag(tag, question, response)
        return ok, reason

    return False, "unsupported_grading"


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex semantic rejudge for a benchmark run")
    parser.add_argument("--db", default="db/benchmark.db")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--datasets-root", default="benchmark/datasets/canonical_full_80")
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--write-db",
        action="store_true",
        help="Persist sample-level and aggregate Codex opinions into benchmark_opinions",
    )
    parser.add_argument(
        "--judge-name",
        default="codex_semantic_v1",
        help="Judge identifier stored in benchmark_opinions (default: codex_semantic_v1)",
    )
    args = parser.parse_args()

    meta = _load_sample_metadata(Path(args.datasets_root))

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT run_id, model_name, task_name, sample_id, prompt, expected, response, correct, created_at
        FROM benchmark_samples
        WHERE run_id = ?
        ORDER BY created_at, id
        """,
        (args.run_id,),
    ).fetchall()
    con.close()
    if not rows:
        raise SystemExit(f"no samples found for run_id={args.run_id}")

    results: List[Dict[str, Any]] = []
    for row in rows:
        rec = dict(row)
        m = meta.get(rec["sample_id"], {})
        rec["grading"] = m.get("grading")
        rec["dataset_file"] = m.get("dataset_file")
        if not rec.get("expected"):
            rec["expected"] = m.get("answer")
        if not rec.get("prompt"):
            rec["prompt"] = m.get("question")

        norm_expected = _normalize_text(rec.get("expected"))
        norm_prompt = _normalize_text(rec.get("prompt"))
        norm_response = _normalize_text(rec.get("response"))
        rec["normalized_expected"] = norm_expected
        rec["normalized_prompt"] = norm_prompt
        rec["normalized_response"] = norm_response

        ok, reason = _judge_row(
            rec.get("sample_id", ""),
            rec.get("grading") or "",
            norm_expected,
            norm_prompt,
            norm_response,
        )
        rec["codex_pass"] = bool(ok)
        rec["codex_reason"] = reason
        rec["original_correct"] = int(rec["correct"]) if rec.get("correct") is not None else None
        rec["rejudged_correct"] = 1 if ok else 0
        rec["flipped"] = (
            rec["original_correct"] is not None
            and rec["original_correct"] != rec["rejudged_correct"]
        )
        results.append(rec)

    summary: Dict[str, Any] = {
        "run_id": args.run_id,
        "judge_model": args.judge_name,
        "rows": len(results),
        "original_correct": sum(1 for r in results if r["original_correct"] == 1),
        "rejudged_correct": sum(1 for r in results if r["rejudged_correct"] == 1),
        "flipped_total": sum(1 for r in results if r["flipped"]),
        "flipped_to_pass": sum(1 for r in results if r["flipped"] and r["original_correct"] == 0 and r["rejudged_correct"] == 1),
        "flipped_to_fail": sum(1 for r in results if r["flipped"] and r["original_correct"] == 1 and r["rejudged_correct"] == 0),
    }

    per_model: Dict[str, Dict[str, int]] = {}
    for r in results:
        mname = r["model_name"]
        if mname not in per_model:
            per_model[mname] = {"rows": 0, "orig_correct": 0, "rejudged_correct": 0, "flipped": 0}
        per_model[mname]["rows"] += 1
        per_model[mname]["orig_correct"] += 1 if r["original_correct"] == 1 else 0
        per_model[mname]["rejudged_correct"] += 1 if r["rejudged_correct"] == 1 else 0
        per_model[mname]["flipped"] += 1 if r["flipped"] else 0
    summary["per_model"] = per_model

    out = {"summary": summary, "results": results}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")

    if args.write_db:
        con = sqlite3.connect(args.db)
        try:
            # Idempotent write for this run/judge.
            con.execute(
                "DELETE FROM benchmark_opinions WHERE run_id = ? AND judge = ?",
                (args.run_id, args.judge_name),
            )

            sample_rows = []
            for r in results:
                flags = {
                    "grading": r.get("grading"),
                    "dataset_file": r.get("dataset_file"),
                    "original_correct": r.get("original_correct"),
                    "rejudged_correct": r.get("rejudged_correct"),
                    "flipped": bool(r.get("flipped")),
                }
                sample_rows.append(
                    (
                        r.get("run_id"),
                        r.get("model_name"),
                        r.get("task_name"),
                        args.judge_name,
                        0,
                        "pass" if r.get("rejudged_correct") == 1 else "fail",
                        None,  # quality
                        None,  # speed
                        None,  # resources
                        json.dumps(flags, ensure_ascii=False),
                        None,  # recommendations
                        r.get("sample_id"),
                        r.get("codex_reason"),
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
                }
                aggregate_rows.append(
                    (
                        args.run_id,
                        model_name,
                        "__shortform_total__",
                        args.judge_name,
                        1,
                        "pass" if score >= 0.5 else "fail",
                        round(score, 6),
                        None,
                        None,
                        json.dumps(flags, ensure_ascii=False),
                        None,
                        None,
                        "aggregate semantic pass-rate over shortform rows",
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

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
