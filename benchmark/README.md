# Local LLM Benchmark Documentation

This benchmark folder contains only the canonical, production-facing benchmark stack.

## What Is Canonical Now

- Primary run config: `benchmark/config_full.yaml`
- Scope: **9 models**, **8 datasets**, **80 samples** (40% easy / 40% medium / 20% hard)
- Total prompt evaluations: **720** (80 x 9)
- Runner entrypoint: `benchmark/run_benchmark.py`

## Documentation Map

- `benchmark/READY_TO_RUN.md`: practical runbook (preflight, run, resume, troubleshooting)
- `benchmark/datasets/README.md`: dataset structure and grading format
