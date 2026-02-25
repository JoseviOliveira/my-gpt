#!/usr/bin/env python3
"""Run a Magistral ad-hoc sweep with one sample per dataset file.

Design goals for current phase:
- Use app-equivalent `/api/stream` forwarding via `X-Benchmark: 1`
- Use FINAL-ANSWER-ONLY system prompt
- No retries (single attempt per sample)
"""

import argparse
import base64
import glob
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

import requests


FINAL_ANSWER_ONLY_SYSTEM = (
    "You must respond ONLY with the final answer. CRITICAL RULES:\n"
    "- NO internal reasoning, thinking, or chain-of-thought\n"
    "- NO <think> tags or similar markers\n"
    "- NO explanations of your reasoning process\n"
    "- NO step-by-step breakdowns unless explicitly requested\n"
    "- Provide ONLY the direct answer to the question\n"
    "- Be concise and factual"
)


def _auth_headers(user: str, password: str) -> Dict[str, str]:
    if not user or not password:
        return {}
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def _pick_sample(samples: List[Dict[str, Any]], index: int) -> Dict[str, Any]:
    if not samples:
        raise ValueError("dataset has no samples")
    if index < 0:
        index = len(samples) + index
    if index < 0 or index >= len(samples):
        raise IndexError(f"sample index out of range: {index}")
    return samples[index]


def _extract_prompt(sample: Dict[str, Any]) -> str:
    prompt = sample.get("question") or sample.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("sample missing question/prompt")
    return prompt.strip()


def _run_stream(
    base_url: str,
    endpoint: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    connect_timeout: float,
    read_timeout: float,
) -> Dict[str, Any]:
    started = time.time()
    rec: Dict[str, Any] = {
        "request_id": payload.get("id"),
        "model": payload.get("model"),
    }
    text = ""
    try:
        resp = requests.post(
            f"{base_url}{endpoint}",
            json=payload,
            headers=headers,
            stream=True,
            timeout=(connect_timeout, read_timeout),
        )
        rec["http_status"] = resp.status_code
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=16, decode_unicode=True):
            if chunk:
                text += chunk
        rec["elapsed_sec"] = round(time.time() - started, 2)
        rec["response_chars"] = len(text)
        rec["response_preview"] = text[:500].replace("\n", " ")
        rec["response"] = text
    except Exception as exc:
        rec["elapsed_sec"] = round(time.time() - started, 2)
        rec["exception"] = str(exc)
    return rec


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one-sample-per-dataset Magistral ad-hoc sweep (final-answer-only, no retries)"
    )
    parser.add_argument(
        "--datasets-glob",
        default="benchmark/datasets/canonical_full_80/*.jsonl",
        help="Glob for dataset files",
    )
    parser.add_argument(
        "--sample-index",
        type=int,
        default=0,
        help="Sample index to pick from each dataset file (default: 0)",
    )
    parser.add_argument("--model", default="magistral:24b")
    parser.add_argument("--base-url", default="http://127.0.0.1:4200")
    parser.add_argument("--endpoint", default="/api/stream")
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--read-timeout", type=float, default=120.0)
    parser.add_argument("--user", default="", help="Basic auth user (optional)")
    parser.add_argument("--password", default="", help="Basic auth password (optional)")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    parser.add_argument(
        "--response-preview-chars",
        type=int,
        default=500,
        help="Max chars stored in response_preview (default: 500)",
    )
    args = parser.parse_args()

    dataset_paths = sorted(Path(p) for p in glob.glob(args.datasets_glob))
    if not dataset_paths:
        raise SystemExit(f"no dataset files matched: {args.datasets_glob}")

    headers = {
        "Content-Type": "application/json",
        "X-Benchmark": "1",
        **_auth_headers(args.user.strip(), args.password.strip()),
    }

    results: List[Dict[str, Any]] = []
    for path in dataset_paths:
        samples = _load_jsonl(path)
        sample = _pick_sample(samples, args.sample_index)
        prompt = _extract_prompt(sample)
        payload = {
            "id": f"adhoc-{uuid.uuid4().hex[:12]}",
            "model": args.model,
            "messages": [
                {"role": "system", "content": FINAL_ANSWER_ONLY_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        }
        rec = _run_stream(
            args.base_url.rstrip("/"),
            args.endpoint,
            payload,
            headers,
            args.connect_timeout,
            args.read_timeout,
        )
        dataset_name = path.stem
        if dataset_name.endswith("_10"):
            dataset_name = dataset_name[:-3]
        rec["dataset_file"] = str(path)
        rec["task_name"] = dataset_name
        rec["sample_index"] = args.sample_index
        rec["sample_id"] = sample.get("id")
        rec["difficulty"] = sample.get("difficulty")
        rec["grading"] = sample.get("grading")
        rec["expected"] = sample.get("answer") or sample.get("expected_content")
        rec["prompt"] = prompt
        rec["prompt_preview"] = prompt[:180].replace("\n", " ")
        rec["response_preview"] = (rec.get("response", "")[: args.response_preview_chars]).replace("\n", " ")
        results.append(rec)

    ok_runs = [r for r in results if r.get("http_status") == 200 and "exception" not in r]
    non_empty_runs = [r for r in ok_runs if int(r.get("response_chars", 0) or 0) > 0]
    timeout_runs = [r for r in results if "timed out" in str(r.get("exception", "")).lower()]
    summary = {
        "model": args.model,
        "endpoint": args.endpoint,
        "datasets_glob": args.datasets_glob,
        "dataset_files": len(dataset_paths),
        "runs": len(results),
        "ok_runs": len(ok_runs),
        "non_empty_runs": len(non_empty_runs),
        "timeout_runs": len(timeout_runs),
    }

    out = {
        "summary": summary,
        "results": results,
    }
    text = json.dumps(out, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
