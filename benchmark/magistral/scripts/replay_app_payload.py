#!/usr/bin/env python3
"""Replay exact app prompt payloads captured from server logs.

This script replays a captured message array through the app API using
X-Benchmark=1 so the payload is forwarded as-is (no extra prompt injection).
"""

import argparse
import base64
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

import requests


def _load_entries(path: str) -> List[Dict[str, Any]]:
    payload = json.loads(Path(path).read_text())
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    raise ValueError("input payload must be a JSON object or array")


def _pick_entry(entries: List[Dict[str, Any]], index: int) -> Dict[str, Any]:
    if not entries:
        raise ValueError("no entries found in input file")
    if index < 0:
        index = len(entries) + index
    if index < 0 or index >= len(entries):
        raise IndexError(f"index out of range: {index}")
    return entries[index]


def _extract_visible_text(obj: Any) -> str:
    if not isinstance(obj, dict):
        return ""
    msg = obj.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            return content
    response = obj.get("response")
    if isinstance(response, str):
        return response
    return ""


def _auth_headers(user: str, password: str) -> Dict[str, str]:
    if not user or not password:
        return {}
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def replay_stream(
    base_url: str,
    endpoint: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    connect_timeout: float,
    read_timeout: float,
) -> Dict[str, Any]:
    started = time.time()
    rec: Dict[str, Any] = {
        "endpoint": endpoint,
        "request_id": payload.get("id"),
        "model": payload.get("model"),
    }
    response_text = ""
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
                response_text += chunk
        rec["elapsed_sec"] = round(time.time() - started, 2)
        rec["response_chars"] = len(response_text)
        rec["response_preview"] = response_text[:600].replace("\n", " ")
    except Exception as exc:
        rec["elapsed_sec"] = round(time.time() - started, 2)
        rec["exception"] = str(exc)
    return rec


def replay_chat(
    base_url: str,
    endpoint: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    connect_timeout: float,
    read_timeout: float,
) -> Dict[str, Any]:
    started = time.time()
    rec: Dict[str, Any] = {
        "endpoint": endpoint,
        "request_id": payload.get("id"),
        "model": payload.get("model"),
    }
    try:
        resp = requests.post(
            f"{base_url}{endpoint}",
            json=payload,
            headers=headers,
            timeout=(connect_timeout, read_timeout),
        )
        rec["http_status"] = resp.status_code
        rec["elapsed_sec"] = round(time.time() - started, 2)
        if resp.ok:
            data = resp.json()
            text = _extract_visible_text(data)
            rec["response_chars"] = len(text)
            rec["response_preview"] = text[:600].replace("\n", " ")
        else:
            rec["error"] = (resp.text or "")[:500]
    except Exception as exc:
        rec["elapsed_sec"] = round(time.time() - started, 2)
        rec["exception"] = str(exc)
    return rec


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay exact app payload from extracted log entry")
    parser.add_argument("--input", required=True, help="Path to extracted JSON file")
    parser.add_argument("--index", type=int, default=-1, help="Entry index (default: -1 latest)")
    parser.add_argument("--base-url", default="http://127.0.0.1:4200", help="App base URL")
    parser.add_argument("--endpoint", default="/api/stream", choices=["/api/stream", "/api/chat"])
    parser.add_argument("--model", default="", help="Optional model override")
    parser.add_argument("--mode", default="", help="Optional mode field (fast/normal/deep)")
    parser.add_argument("--options-json", default="", help="Optional options JSON object")
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--read-timeout", type=float, default=180.0)
    parser.add_argument("--x-benchmark", action="store_true", default=True, help="Send X-Benchmark: 1 header")
    parser.add_argument("--user", default="", help="Basic auth user (optional)")
    parser.add_argument("--password", default="", help="Basic auth password (optional)")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    entries = _load_entries(args.input)
    entry = _pick_entry(entries, args.index)

    model = args.model.strip() or entry.get("model") or ""
    messages = entry.get("messages") or []
    if not model:
        raise ValueError("model is missing in entry and no --model override provided")
    if not isinstance(messages, list) or not messages:
        raise ValueError("entry.messages must be a non-empty array")

    request_id = f"replay-{uuid.uuid4().hex[:12]}"
    payload: Dict[str, Any] = {
        "id": request_id,
        "model": model,
        "messages": messages,
    }
    if args.mode.strip():
        payload["mode"] = args.mode.strip()
    if args.options_json.strip():
        payload["options"] = json.loads(args.options_json)

    user = args.user.strip()
    password = args.password.strip()
    headers = {
        "Content-Type": "application/json",
        **_auth_headers(user, password),
    }
    if args.x_benchmark:
        headers["X-Benchmark"] = "1"

    if args.endpoint == "/api/stream":
        rec = replay_stream(
            args.base_url.rstrip("/"),
            args.endpoint,
            payload,
            headers,
            args.connect_timeout,
            args.read_timeout,
        )
    else:
        rec = replay_chat(
            args.base_url.rstrip("/"),
            args.endpoint,
            payload,
            headers,
            args.connect_timeout,
            args.read_timeout,
        )

    rec["source_input"] = args.input
    rec["source_index"] = args.index
    rec["source_timestamp"] = entry.get("timestamp")
    rec["source_request_id"] = entry.get("request_id")
    rec["x_benchmark"] = bool(args.x_benchmark)
    rec["message_count"] = len(messages)

    out_text = json.dumps(rec, indent=2, ensure_ascii=False)
    print(out_text)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(out_text + "\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
