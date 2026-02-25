#!/usr/bin/env python3
"""Test thinking behavior across different large language models."""

import requests
import json
import time

SYSTEM_PROMPT = """You must respond ONLY with the final answer. CRITICAL RULES:
- NO internal reasoning, thinking, or chain-of-thought
- NO <think> tags or similar markers
- NO explanations of your reasoning process
- NO step-by-step breakdowns unless explicitly requested
- Provide ONLY the direct answer to the question
- Be concise and factual"""

def test_model(model_name):
    """Test a specific model's thinking behavior."""
    print(f"\n{'='*60}")
    print(f"Testing: {model_name}")
    print(f"{'='*60}")

    # Warm up the model before the measured request
    try:
        requests.post(
            'http://127.0.0.1:11434/api/chat',
            json={
                'model': model_name,
                'messages': [
                    {'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user', 'content': 'Warm up.'}
                ],
                'stream': False,
                'options': {'temperature': 0, 'num_predict': 16}
            },
            timeout=60
        )
    except Exception:
        pass
    
    # Measure wall-clock elapsed time for this model (end-to-end request)
    t0 = time.perf_counter()
    resp = requests.post(
        'http://127.0.0.1:11434/api/chat',
        json={
            'model': model_name,
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': 'What is 2+2?'}
            ],
            'stream': False,
            'options': {'temperature': 0, 'num_predict': 512}
        },
        timeout=60
    ).json()
    t1 = time.perf_counter()
    duration_s = max(t1 - t0, 1e-9)
    
    content = resp.get('message', {}).get('content', '')
    thinking = resp.get('message', {}).get('thinking', '')
    tokens = resp.get('eval_count', 0)
    
    tps = tokens / duration_s if tokens else 0.0
    total_chars = len(content) + len(thinking)
    thinking_pct = (len(thinking) / total_chars * 100.0) if total_chars else 0.0
    content_pct = (len(content) / total_chars * 100.0) if total_chars else 0.0
    
    print(f'Tokens: {tokens}')
    print(f'Elapsed time: {duration_s:.3f} s')
    print(f'Tokens/sec: {tps:.1f}')
    print(f'Content length: {len(content)} chars')
    print(f'Thinking length: {len(thinking)} chars')
    if total_chars:
        print(f'Char split: thinking={thinking_pct:.1f}% content={content_pct:.1f}%')
    print(f'Content preview: {repr(content[:150])}')
    if thinking:
        print(f'Thinking preview: {repr(thinking[:150])}')
    
    return {
        'model': model_name,
        'tokens': tokens,
        'duration_s': duration_s,
        'tps': tps,
        'content_len': len(content),
        'thinking_len': len(thinking),
        'thinking_pct': thinking_pct,
        'content_pct': content_pct,
        'has_thinking': len(thinking) > 0
    }

if __name__ == '__main__':
    models = [
        'qwen3:4b',
        'qwen3:8b',
        'qwen3:14b',
        'gemma3:4b',
        'gemma3:12b',
        'deepseek-r1:8b',
        'deepseek-r1:14b',
        'magistral:24b',
        'gpt-oss:20b'
    ]
    
    results = []
    for model in models:
        try:
            result = test_model(model)
            results.append(result)
        except Exception as e:
            print(f"Error testing {model}: {e}")
            import traceback
            traceback.print_exc()
    
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"{'Model':<20} {'Tok':<6} {'Elapsed':<7} {'Tok/s':<8} {'Content':<8} {'Think':<8} {'Think%':<7} {'Uses CoT'}")
    print("-" * 90)
    for r in results:
        uses_cot = "YES" if r['has_thinking'] else "NO"
        print(
            f"{r['model']:<20} {r['tokens']:<6} {r['duration_s']:<7.3f} {r['tps']:<8.1f} "
            f"{r['content_len']:<8} {r['thinking_len']:<8} {r['thinking_pct']:<7.1f} {uses_cot}"
        )
    
    print(f"\n{'='*60}")
    print("CONCLUSION")
    print(f"{'='*60}")
    thinking_models = [r['model'] for r in results if r['has_thinking']]
    direct_models = [r['model'] for r in results if not r['has_thinking']]
    
    if thinking_models:
        print(f"Models with thinking mode: {', '.join(thinking_models)}")
    if direct_models:
        print(f"Models with direct responses: {', '.join(direct_models)}")
