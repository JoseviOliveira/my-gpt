# Benchmark Runbook (Current)

## 1. Preflight

1. Start Ollama and verify:
   - `curl -s http://127.0.0.1:11434/api/tags >/dev/null`
2. Use project venv Python:
   - `./chat_env/bin/python --version`
3. Ensure benchmark config exists:
   - `benchmark/config_full.yaml`
4. Pull required models (from config):
   - `gemma3:4b`, `qwen3:4b`, `qwen3:8b`, `deepseek-r1:8b`, `gemma3:12b`, `deepseek-r1:14b`, `qwen3:14b`, `gpt-oss:20b`, `magistral:24b`
5. Set keep-alive for long runs:
   - `export OLLAMA_KEEP_ALIVE=-1`

## 2. Run

### Interactive script

```bash
./benchmark/run_full_benchmark.sh
```

### Direct CLI

```bash
./chat_env/bin/python benchmark/run_benchmark.py \
  --config benchmark/config_full.yaml
```

### Useful flags

- Fresh run without auto-resume:
```bash
./chat_env/bin/python benchmark/run_benchmark.py \
  --config benchmark/config_full.yaml \
  --no-resume
```

- Resume specific run:
```bash
./chat_env/bin/python benchmark/run_benchmark.py \
  --config benchmark/config_full.yaml \
  --resume-run-id <RUN_ID>
```

- Run only selected tasks:
```bash
./chat_env/bin/python benchmark/run_benchmark.py \
  --config benchmark/config_full.yaml \
  --task logic_deduction --task chat_safety
```

- Skip all on-the-fly grading/evaluation (raw outputs + KPI collection only):
```bash
./chat_env/bin/python benchmark/run_benchmark.py \
  --config benchmark/config_full.yaml \
  --no-evaluation
```

## 3. Monitor

- Live monitor page: `static/docs/benchmark_monitor.html`
- Main benchmark report page: `static/docs/benchmark_guided.html`
- API endpoints used by monitor:
  - `/api/benchmark/status`
  - `/api/benchmark/datasets`
  - `/api/benchmark/last_task`

## 4. Resume Behavior (DB-Driven)

- Runner checks latest run (or explicit `--resume-run-id`) and resumes when policy allows.
- Progress comes from `benchmark_run_tasks` completed rows, not only in-memory state.
- Interrupted/in-flight task recovery is supported.

## 5. Troubleshooting

### Problem: very large TTFT spikes (minutes)
Solution:
- Ensure `OLLAMA_KEEP_ALIVE=-1` before run.
- Confirm no conflicting env override resets keep-alive.

### Problem: benchmark appears stalled
Solution:
- Check `log/benchmark.log`.
- Check Ollama health: `curl -s http://127.0.0.1:11434/api/tags`.
- If stuck due timeout/hang, runner watchdog restarts Ollama automatically.

### Problem: repeated timeout errors
Solution:
- Review `timeout` block in `benchmark/config_full.yaml`.
- Reduce concurrent load on system and rerun with resume.

### Problem: monitor looks stale after restart
Solution:
- Refresh monitor page.
- Verify same `run_id` is still active via `/api/benchmark/status`.

## 6. Post-Run

1. Reset keep-alive if needed:
```bash
unset OLLAMA_KEEP_ALIVE
```
2. Optional quick DB check:
```bash
sqlite3 db/benchmark.db "SELECT run_id,status,started_at,completed_at FROM benchmark_runs ORDER BY id DESC LIMIT 5;"
```
