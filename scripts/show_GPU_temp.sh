#!/usr/bin/env zsh
set -euo pipefail

# Print GPU temp from macmon (rounded to 0.1 C).
# Exit codes:
#   0 => valid non-negative reading
#   2 => invalid sensor reading (e.g. negative value)
#   3 => probe/parsing failure

line="$(macmon pipe -s 1 -i 1000 2>/dev/null | tail -n 1 || true)"
if [[ -z "$line" ]]; then
  echo "nan"
  exit 3
fi

temp="$(printf '%s\n' "$line" | jq -r '.temp.gpu_temp_avg // empty' 2>/dev/null || true)"
if [[ -z "$temp" ]]; then
  echo "nan"
  exit 3
fi

rounded="$(printf '%.1f' "$temp" 2>/dev/null || true)"
if [[ -z "$rounded" ]]; then
  echo "nan"
  exit 3
fi

echo "$rounded"

if awk "BEGIN {exit !($rounded < 0)}"; then
  exit 2
fi
