#!/usr/bin/env python3
"""Ablation runner for captured app payloads.

Goal: isolate which system prefixes impact response visibility/reliability.
"""

import argparse
import base64
import itertools
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests


def _load_entries(path: str) -> List[Dict[str, Any]]:
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    raise ValueError("input must be JSON object or array")


def _pick_entry(entries: List[Dict[str, Any]], index: int) -> Dict[str, Any]:
    if not entries:
        raise ValueError("no entries in input")
    if index < 0:
        index = len(entries) + index
    if index < 0 or index >= len(entries):
        raise IndexError(f"index out of range: {index}")
    return entries[index]


def _auth_headers(user: str, password: str) -> Dict[str, str]:
    if not user or not password:
        return {}
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _extract_system_slots(messages: List[Dict[str, Any]]) -> Tuple[int, int]:
    """Return indexes for (rules_system_idx, guard_system_idx)."""
    rules_idx = -1
    guard_idx = -1
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "system":
            continue
        content = str(msg.get("content") or "")
        lc = content.lower()
        if "you must respond only with the final answer" in lc:
            rules_idx = i
        if "latest user message is in language code" in lc:
            guard_idx = i
    return rules_idx, guard_idx


def _build_variant_messages(
    messages: List[Dict[str, Any]],
    rules_idx: int,
    guard_idx: int,
    keep_rules: bool,
    keep_guard: bool,
) -> List[Dict[str, Any]]:
    out = []
    for i, msg in enumerate(messages):
        if i == rules_idx and not keep_rules:
            continue
        if i == guard_idx and not keep_guard:
            continue
        out.append(msg)
    return out


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
        rec["response_preview"] = text[:400].replace("\n", " ")
    except Exception as exc:
        rec["elapsed_sec"] = round(time.time() - started, 2)
        rec["exception"] = str(exc)
    return rec


def main() -> int:
    parser = argparse.ArgumentParser(description="Run system-prefix ablation against app payload")
    parser.add_argument("--input", required=True, help="Extracted payload JSON path")
    parser.add_argument("--index", type=int, default=-1, help="Entry index (default: latest)")
    parser.add_argument("--base-url", default="http://127.0.0.1:4200")
    parser.add_argument("--endpoint", default="/api/stream")
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--read-timeout", type=float, default=180.0)
    parser.add_argument("--repeats", type=int, default=1, help="Repeats per ablation condition")
    parser.add_argument("--user", default="", help="Basic auth user")
    parser.add_argument("--password", default="", help="Basic auth pass")
    parser.add_argument("--out", default="", help="Output JSON path")
    args = parser.parse_args()

    entries = _load_entries(args.input)
    entry = _pick_entry(entries, args.index)
    messages = entry.get("messages") or []
    model = entry.get("model") or ""
    if not model:
        raise ValueError("model missing in entry")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages missing in entry")

    rules_idx, guard_idx = _extract_system_slots(messages)
    if rules_idx < 0:
        raise ValueError("final-answer rules system message not found")
    if guard_idx < 0:
        raise ValueError("language guard system message not found")

    headers = {
        "Content-Type": "application/json",
        "X-Benchmark": "1",
        **_auth_headers(args.user.strip(), args.password.strip()),
    }

    matrix = list(itertools.product([True, False], [True, False]))
    results: List[Dict[str, Any]] = []
    for keep_rules, keep_guard in matrix:
        condition = {
            "keep_final_answer_rules": keep_rules,
            "keep_language_guard": keep_guard,
        }
        variant_messages = _build_variant_messages(
            messages, rules_idx, guard_idx, keep_rules, keep_guard
        )
        for rep in range(1, max(1, args.repeats) + 1):
            payload = {
                "id": f"ablate-{uuid.uuid4().hex[:12]}",
                "model": model,
                "messages": variant_messages,
            }
            rec = _run_stream(
                args.base_url.rstrip("/"),
                args.endpoint,
                payload,
                headers,
                args.connect_timeout,
                args.read_timeout,
            )
            rec["condition"] = condition
            rec["repeat"] = rep
            rec["message_count"] = len(variant_messages)
            results.append(rec)

    summary = []
    for keep_rules, keep_guard in matrix:
        subset = [
            r for r in results
            if r["condition"]["keep_final_answer_rules"] == keep_rules
            and r["condition"]["keep_language_guard"] == keep_guard
        ]
        chars = [int(r.get("response_chars", 0) or 0) for r in subset]
        ok = [r for r in subset if r.get("http_status") == 200 and "exception" not in r]
        summary.append(
            {
                "condition": {
                    "keep_final_answer_rules": keep_rules,
                    "keep_language_guard": keep_guard,
                },
                "runs": len(subset),
                "ok_runs": len(ok),
                "non_empty_runs": sum(1 for c in chars if c > 0),
                "avg_response_chars": (sum(chars) / len(chars)) if chars else 0,
            }
        )

    out = {
        "source_input": args.input,
        "source_index": args.index,
        "source_timestamp": entry.get("timestamp"),
        "source_request_id": entry.get("request_id"),
        "model": model,
        "rules_index": rules_idx,
        "guard_index": guard_idx,
        "summary": summary,
        "results": results,
    }
    text = json.dumps(out, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
