#!/usr/bin/env python3
"""Phased Magistral ad-hoc retest runner with transport gates and GPT-OSS rejudge."""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


def _auth_headers(user: str, password: str) -> Dict[str, str]:
    if not user or not password:
        return {}
    import base64

    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _classify_transport_failure(row: Dict[str, Any]) -> Optional[str]:
    http_status = int(row.get("http_status") or 0)
    exc = str(row.get("exception") or "")
    response_chars = int(row.get("response_chars") or 0)
    if "timed out" in exc.lower():
        return "timeout"
    if http_status and http_status != 200:
        return "HTTP error"
    if http_status == 200 and response_chars == 0:
        return "empty"
    if exc:
        return "other exception"
    return None


def _is_transport_ok(row: Dict[str, Any]) -> bool:
    return (
        int(row.get("http_status") or 0) == 200
        and not row.get("exception")
        and int(row.get("response_chars") or 0) > 0
    )


def _pct(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    vals = sorted(values)
    idx = int(round((p / 100.0) * (len(vals) - 1)))
    idx = max(0, min(idx, len(vals) - 1))
    return round(vals[idx], 3)


def _redact_cmd(cmd: List[str]) -> List[str]:
    redacted = list(cmd)
    for i, token in enumerate(redacted):
        if token == "--password" and i + 1 < len(redacted):
            redacted[i + 1] = "***REDACTED***"
    return redacted


def _run_cmd(cmd: List[str]) -> None:
    print("[exec]", " ".join(_redact_cmd(cmd)))
    subprocess.run(cmd, check=True)


def _print_planned_cmd(cmd: List[str]) -> None:
    print("[dry-run]", " ".join(_redact_cmd(cmd)))


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def _preflight(
    app_base_url: str,
    judge_base_url: str,
    datasets_glob: str,
    user: str,
    password: str,
) -> Dict[str, Any]:
    headers = _auth_headers(user, password)
    endpoint_results = {}
    for endpoint in ("/health", "/api/temperature", "/api/gpu"):
        url = f"{app_base_url.rstrip('/')}{endpoint}"
        last_err = None
        status = None
        # Retry transient endpoint timeouts/connection hiccups.
        for _ in range(3):
            try:
                resp = requests.get(url, headers=headers, timeout=8)
                status = resp.status_code
                if status == 200:
                    break
                last_err = f"http_status={status}"
            except Exception as exc:
                last_err = str(exc)
        endpoint_results[endpoint] = {
            "ok": status == 200,
            "status": status,
            **({"error": last_err} if status != 200 and last_err else {}),
        }

    model_results = {"magistral:24b": False, "gpt-oss:20b": False}
    try:
        resp = requests.get(f"{judge_base_url.rstrip('/')}/api/tags", timeout=8)
        resp.raise_for_status()
        tags = resp.json().get("models") or []
        names = {m.get("name") for m in tags if isinstance(m, dict)}
        for model in model_results:
            model_results[model] = model in names
    except Exception as exc:
        return {
            "ok": False,
            "endpoints": endpoint_results,
            "models": model_results,
            "datasets": [],
            "error": f"model check failed: {exc}",
        }

    dataset_files = sorted(str(p) for p in Path().glob(datasets_glob))
    # /api/temperature can intermittently timeout under load; treat it as advisory.
    required_endpoints = ("/health", "/api/gpu")
    optional_endpoints = ("/api/temperature",)
    ok = (
        all(endpoint_results.get(ep, {}).get("ok") for ep in required_endpoints)
        and all(model_results.values())
        and len(dataset_files) == 8
    )
    return {
        "ok": ok,
        "endpoints": endpoint_results,
        "required_endpoints": list(required_endpoints),
        "optional_endpoints": list(optional_endpoints),
        "models": model_results,
        "datasets": dataset_files,
    }


def _build_summary_markdown(
    timestamp: str,
    smoke: Dict[str, Any],
    full_raw: Dict[str, Any],
    rejudge: Dict[str, Any],
    artifacts: Dict[str, str],
) -> str:
    failure_tax = rejudge.get("summary", {}).get("failure_taxonomy", {})
    by_dataset = rejudge.get("summary", {}).get("by_dataset", {})
    top_fails = rejudge.get("summary", {}).get("top_fail_examples", [])
    hotspots = rejudge.get("summary", {}).get("disagreement_hotspots", [])

    lines = []
    lines.append(f"## {timestamp} Magistral Ad-Hoc Retest (Phased)")
    lines.append("")
    lines.append("### Artifacts")
    lines.append(f"- Smoke JSON: `{artifacts['smoke']}`")
    lines.append(f"- Full raw JSON: `{artifacts['raw']}`")
    lines.append(f"- Rejudge JSON: `{artifacts['rejudge']}`")
    lines.append(f"- Summary Markdown: `{artifacts['summary_md']}`")
    lines.append("")
    lines.append("### Smoke Gate")
    lines.append(f"- dataset_files: {smoke['summary'].get('dataset_files')}")
    lines.append(f"- runs: {smoke['summary'].get('runs')}")
    lines.append(f"- ok_runs: {smoke['summary'].get('ok_runs')}")
    lines.append(f"- non_empty_runs: {smoke['summary'].get('non_empty_runs')}")
    lines.append(f"- timeout_runs: {smoke['summary'].get('timeout_runs')}")
    lines.append("")
    lines.append("### Run Health")
    lines.append(f"- rows: {full_raw.get('summary', {}).get('rows')}")
    lines.append(f"- ok_rate: {full_raw.get('summary', {}).get('ok_rate')}")
    lines.append(f"- non_empty_rate: {full_raw.get('summary', {}).get('non_empty_rate')}")
    lines.append(f"- timeout_rate: {full_raw.get('summary', {}).get('timeout_rate')}")
    lines.append(f"- p50 elapsed_sec: {full_raw.get('summary', {}).get('elapsed_p50_sec')}")
    lines.append(f"- p90 elapsed_sec: {full_raw.get('summary', {}).get('elapsed_p90_sec')}")
    lines.append("")
    lines.append("### Failure Taxonomy")
    for key in ("transport_timeout", "transport_http_error", "transport_empty", "transport_other_exception", "judge_error", "judge_parse_failed", "semantic_fail"):
        lines.append(f"- {key}: {failure_tax.get(key)}")
    lines.append("")
    lines.append("### Per-Dataset Reliability")
    lines.append("| dataset | rows | semantic_denominator | pass_rate |")
    lines.append("|---|---:|---:|---:|")
    for name in sorted(by_dataset.keys()):
        row = by_dataset[name]
        rate = row.get("pass_rate")
        rate_text = "n/a" if rate is None else f"{rate:.3f}"
        lines.append(f"| {name} | {row.get('rows')} | {row.get('semantic_denominator')} | {rate_text} |")
    lines.append("")
    lines.append("### Discrepancy Hotspots")
    for row in hotspots:
        lines.append(f"- {row.get('dataset_name')}: pass_rate={row.get('pass_rate')} denominator={row.get('semantic_denominator')}")
    lines.append("")
    lines.append("### Top 10 Fail Exemplars")
    for row in top_fails[:10]:
        lines.append(
            f"- {row.get('dataset_name')}/{row.get('sample_id')} | expected={json.dumps(row.get('expected'), ensure_ascii=False)} | "
            f"response={json.dumps(str(row.get('response') or '')[:180], ensure_ascii=False)} | reason={row.get('judge_reason')}"
        )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run phased Magistral ad-hoc retest and GPT-OSS rejudge")
    parser.add_argument("--model", default="magistral:24b")
    parser.add_argument("--judge-model", default="gpt-oss:20b")
    parser.add_argument("--app-base-url", default="http://127.0.0.1:4200")
    parser.add_argument("--judge-base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--endpoint", default="/api/stream")
    parser.add_argument("--datasets-glob", default="benchmark/datasets/canonical_full_80/*.jsonl")
    parser.add_argument("--read-timeout", type=int, default=180)
    parser.add_argument("--judge-timeout-sec", type=int, default=60)
    parser.add_argument("--judge-timeout-fallback-sec", type=int, default=90)
    parser.add_argument("--judge-instability-threshold", type=float, default=0.05)
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--results-dir", default="benchmark/magistral/results")
    parser.add_argument("--date-prefix", default="")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run preflight and print planned commands/artifacts without executing phases 1-3",
    )
    args = parser.parse_args()
    user = args.user or os.environ.get("APP_USER", "")
    password = args.password or os.environ.get("APP_PASS", "")

    now = datetime.now(timezone.utc)
    date_prefix = args.date_prefix.strip() or now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y-%m-%d %H:%M:%SZ")
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    smoke_path = results_dir / f"{date_prefix}_magistral_adhoc_smoke_one_each.json"
    raw_path = results_dir / f"{date_prefix}_magistral_adhoc_canonical_80_raw.json"
    rejudge_path = results_dir / f"{date_prefix}_magistral_adhoc_canonical_80_rejudge_gptoss.json"
    summary_md_path = results_dir / f"{date_prefix}_magistral_adhoc_retest_summary.md"
    preflight_path = results_dir / f"{date_prefix}_magistral_adhoc_preflight.json"

    preflight = _preflight(
        args.app_base_url,
        args.judge_base_url,
        args.datasets_glob,
        user,
        password,
    )
    _write_json(preflight_path, preflight)
    if not preflight.get("ok"):
        raise SystemExit(f"preflight failed; see {preflight_path}")

    smoke_cmd = [
        sys.executable,
        "benchmark/magistral/scripts/run_magistral_adhoc.py",
        "--datasets-glob", args.datasets_glob,
        "--sample-index", "0",
        "--model", args.model,
        "--base-url", args.app_base_url,
        "--endpoint", args.endpoint,
        "--read-timeout", str(args.read_timeout),
        "--user", user,
        "--password", password,
        "--out", str(smoke_path),
    ]
    part_cmds = []
    for sample_index in range(10):
        part_path = results_dir / f"{date_prefix}_magistral_adhoc_canonical_part_{sample_index:02d}.json"
        part_cmds.append([
            sys.executable,
            "benchmark/magistral/scripts/run_magistral_adhoc.py",
            "--datasets-glob", args.datasets_glob,
            "--sample-index", str(sample_index),
            "--model", args.model,
            "--base-url", args.app_base_url,
            "--endpoint", args.endpoint,
            "--read-timeout", str(args.read_timeout),
            "--user", user,
            "--password", password,
            "--out", str(part_path),
        ])
    rejudge_cmd = [
        sys.executable,
        "benchmark/magistral/scripts/rejudge_run_with_llm.py",
        "--input-json", str(raw_path),
        "--judge-model", args.judge_model,
        "--judge-name", "gpt_oss_20b_judge",
        "--judge-base-url", args.judge_base_url,
        "--judge-timeout-sec", str(args.judge_timeout_sec),
        "--out", str(rejudge_path),
    ]

    if args.dry_run:
        print("[dry-run] preflight OK; planned artifacts:")
        for p in (preflight_path, smoke_path, raw_path, rejudge_path, summary_md_path):
            print(f"[dry-run]   {p}")
        _print_planned_cmd(smoke_cmd)
        for cmd in part_cmds:
            _print_planned_cmd(cmd)
        _print_planned_cmd(rejudge_cmd)
        return 0

    # Phase 1: smoke gate (one sample per dataset)
    _run_cmd(smoke_cmd)
    smoke = _read_json(smoke_path)
    smoke_summary = smoke.get("summary", {})
    smoke_gate_ok = (
        int(smoke_summary.get("dataset_files") or 0) == 8
        and int(smoke_summary.get("runs") or 0) == 8
        and int(smoke_summary.get("ok_runs") or 0) == 8
        and int(smoke_summary.get("non_empty_runs") or 0) >= 7
        and int(smoke_summary.get("timeout_runs") or 0) == 0
    )
    if not smoke_gate_ok:
        failed = []
        for row in smoke.get("results", []):
            reason = _classify_transport_failure(row)
            if reason:
                failed.append({
                    "dataset_file": row.get("dataset_file"),
                    "sample_id": row.get("sample_id"),
                    "classification": reason,
                    "http_status": row.get("http_status"),
                    "exception": row.get("exception"),
                })
        smoke["gate"] = {"passed": False, "failure_rows": failed}
        _write_json(smoke_path, smoke)
        raise SystemExit(f"smoke gate failed; see {smoke_path}")
    smoke["gate"] = {"passed": True}
    _write_json(smoke_path, smoke)

    # Phase 2: full canonical ad-hoc run (10 samples x 8 datasets)
    all_rows: List[Dict[str, Any]] = []
    part_paths: List[str] = []
    for sample_index, cmd in enumerate(part_cmds):
        part_path = results_dir / f"{date_prefix}_magistral_adhoc_canonical_part_{sample_index:02d}.json"
        part_paths.append(str(part_path))
        _run_cmd(cmd)
        part = _read_json(part_path)
        for row in part.get("results", []):
            all_rows.append({
                "run_id": f"magistral-adhoc-{date_prefix}",
                "model_name": row.get("model"),
                "task_name": row.get("task_name"),
                "dataset_file": row.get("dataset_file"),
                "sample_index": row.get("sample_index"),
                "sample_id": row.get("sample_id"),
                "grading": row.get("grading"),
                "prompt": row.get("prompt"),
                "expected": row.get("expected"),
                "response": row.get("response"),
                "http_status": row.get("http_status"),
                "elapsed_sec": row.get("elapsed_sec"),
                "response_chars": row.get("response_chars"),
                "exception": row.get("exception"),
                "response_preview": row.get("response_preview"),
            })

    ok_rows = [r for r in all_rows if int(r.get("http_status") or 0) == 200 and not r.get("exception")]
    non_empty_rows = [r for r in ok_rows if int(r.get("response_chars") or 0) > 0]
    timeout_rows = [r for r in all_rows if "timed out" in str(r.get("exception") or "").lower()]
    elapsed_vals = [float(r.get("elapsed_sec")) for r in all_rows if r.get("elapsed_sec") is not None]

    raw = {
        "summary": {
            "run_id": f"magistral-adhoc-{date_prefix}",
            "rows": len(all_rows),
            "ok_rows": len(ok_rows),
            "non_empty_rows": len(non_empty_rows),
            "timeout_rows": len(timeout_rows),
            "ok_rate": round(len(ok_rows) / len(all_rows), 4) if all_rows else 0.0,
            "non_empty_rate": round(len(non_empty_rows) / len(all_rows), 4) if all_rows else 0.0,
            "timeout_rate": round(len(timeout_rows) / len(all_rows), 4) if all_rows else 0.0,
            "elapsed_p50_sec": _pct(elapsed_vals, 50),
            "elapsed_p90_sec": _pct(elapsed_vals, 90),
            "part_files": part_paths,
        },
        "results": all_rows,
    }
    _write_json(raw_path, raw)

    # Phase 3: rejudge with GPT-OSS
    _run_cmd(rejudge_cmd)
    rejudge = _read_json(rejudge_path)
    instability = float(rejudge.get("summary", {}).get("judge_instability_rate") or 0.0)
    reran_with_fallback = False
    if instability > args.judge_instability_threshold:
        reran_with_fallback = True
        _run_cmd([
            sys.executable,
            "benchmark/magistral/scripts/rejudge_run_with_llm.py",
            "--input-json", str(raw_path),
            "--judge-model", args.judge_model,
            "--judge-name", "gpt_oss_20b_judge",
            "--judge-base-url", args.judge_base_url,
            "--judge-timeout-sec", str(args.judge_timeout_fallback_sec),
            "--out", str(rejudge_path),
        ])
        rejudge = _read_json(rejudge_path)
        rejudge.setdefault("summary", {})["fallback_timeout_used"] = args.judge_timeout_fallback_sec
        _write_json(rejudge_path, rejudge)

    artifacts = {
        "smoke": str(smoke_path),
        "raw": str(raw_path),
        "rejudge": str(rejudge_path),
        "summary_md": str(summary_md_path),
        "preflight": str(preflight_path),
    }
    md = _build_summary_markdown(timestamp, smoke, raw, rejudge, artifacts)
    summary_md_path.write_text(md)

    readme_path = Path("benchmark/magistral/results/README.md")
    if readme_path.exists():
        readme_path.write_text(readme_path.read_text().rstrip() + "\n\n" + md)

    final_summary = {
        "phase0_preflight_ok": bool(preflight.get("ok")),
        "phase1_smoke_gate_passed": True,
        "phase2_rows": len(all_rows),
        "phase3_reran_with_fallback_timeout": reran_with_fallback,
        "artifacts": artifacts,
    }
    print(json.dumps(final_summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
