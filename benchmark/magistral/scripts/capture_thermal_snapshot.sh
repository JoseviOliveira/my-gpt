#!/usr/bin/env bash
set -euo pipefail

TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
GPU_TEMP="$(./scripts/show_GPU_temp.sh 2>/dev/null || echo n/a)"
GPU_UTIL="$(./scripts/show_GPU_util.sh 2>/dev/null || echo n/a)"

cat <<JSON
{
  "timestamp_utc": "${TS}",
  "gpu_temp_c": "${GPU_TEMP}",
  "gpu_util_percent": "${GPU_UTIL}"
}
JSON
