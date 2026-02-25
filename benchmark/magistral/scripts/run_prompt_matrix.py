#!/usr/bin/env python3
import argparse
import json
import time
from pathlib import Path

import requests


def run_once(base_url, model, prompt, num_predict, read_timeout, connect_timeout):
    started = time.time()
    rec = {
        "model": model,
        "num_predict": num_predict,
    }
    try:
        resp = requests.post(
            f"{base_url}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0,
                    "top_k": 1,
                    "top_p": 1,
                    "num_predict": num_predict,
                },
            },
            timeout=(connect_timeout, read_timeout),
        )
        rec["http_status"] = resp.status_code
        rec["elapsed_sec"] = round(time.time() - started, 2)
        if resp.ok:
            payload = resp.json()
            text = payload.get("response") or ""
            rec["response_chars"] = len(text)
            rec["response_preview"] = text[:300].replace("\n", " ")
            rec["done"] = payload.get("done")
            rec["done_reason"] = payload.get("done_reason")
            rec["eval_count"] = payload.get("eval_count")
            rec["prompt_eval_count"] = payload.get("prompt_eval_count")
        else:
            rec["error"] = (resp.text or "")[:500]
    except Exception as exc:
        rec["elapsed_sec"] = round(time.time() - started, 2)
        rec["exception"] = str(exc)
    return rec


def main():
    parser = argparse.ArgumentParser(description="Run minimal direct prompt checks against Ollama models")
    parser.add_argument("--prompts", required=True, help="Path to prompts JSON array")
    parser.add_argument("--models", nargs="+", required=True, help="Model names")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434", help="Ollama base URL")
    parser.add_argument("--num-predict", type=int, default=400)
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--read-timeout", type=float, default=75.0)
    parser.add_argument("--out", default="", help="Optional output JSON file")
    args = parser.parse_args()

    prompts = json.loads(Path(args.prompts).read_text())
    results = []
    for prompt_obj in prompts:
        pid = prompt_obj.get("id", "unknown")
        prompt = prompt_obj.get("prompt", "")
        for model in args.models:
            rec = run_once(
                args.base_url,
                model,
                prompt,
                args.num_predict,
                args.read_timeout,
                args.connect_timeout,
            )
            rec["prompt_id"] = pid
            results.append(rec)

    out_text = json.dumps(results, indent=2)
    print(out_text)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(out_text + "\n")


if __name__ == "__main__":
    main()
