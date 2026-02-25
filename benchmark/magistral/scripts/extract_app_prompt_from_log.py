#!/usr/bin/env python3
"""Extract exact app prompt payloads from server log [LLM] stream lines."""

import argparse
import json
import re
from pathlib import Path


LINE_RE = re.compile(
    r"^\s*(?P<timestamp>\d{4}-\d{2}-\d{2} [\d:,.\- ]+).*\[LLM\] stream model=(?P<model>\S+).*?id=(?P<req_id>\S+).*?prompt=(?P<prompt>\[.*\]) response=",
    re.DOTALL,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract app prompt payloads from log/server.out.log"
    )
    parser.add_argument(
        "--log",
        default="log/server.out.log",
        help="Path to server log file (default: log/server.out.log)",
    )
    parser.add_argument("--model", default="", help="Filter by model name")
    parser.add_argument(
        "--contains",
        default="",
        help="Filter by substring contained in any user message",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Return only latest matching entry",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional output JSON file path",
    )
    args = parser.parse_args()

    log_path = Path(args.log)
    if not log_path.exists():
        raise SystemExit(f"log file not found: {log_path}")

    model_filter = args.model.strip()
    contains_filter = args.contains.strip().lower()

    matches = []
    for raw_line in log_path.read_text(errors="replace").splitlines():
        if "[LLM] stream" not in raw_line or " prompt=[" not in raw_line:
            continue

        m = LINE_RE.match(raw_line)
        if not m:
            continue

        model = m.group("model")
        if model_filter and model != model_filter:
            continue

        prompt_json = m.group("prompt")
        try:
            messages = json.loads(prompt_json)
        except Exception:
            continue

        if contains_filter:
            hay = " ".join(
                str(msg.get("content", ""))
                for msg in messages
                if isinstance(msg, dict) and msg.get("role") == "user"
            ).lower()
            if contains_filter not in hay:
                continue

        matches.append(
            {
                "timestamp": m.group("timestamp").strip(),
                "model": model,
                "request_id": m.group("req_id"),
                "messages": messages,
            }
        )

    if args.latest and matches:
        matches = [matches[-1]]

    out_text = json.dumps(matches, indent=2, ensure_ascii=False)
    print(out_text)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(out_text + "\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
