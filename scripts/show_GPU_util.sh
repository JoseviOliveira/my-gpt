#!/bin/zsh
set -euo pipefail

if ! command -v powermetrics >/dev/null 2>&1; then
  echo "powermetrics not found" >&2
  exit 1
fi

output="$(sudo -n /usr/bin/powermetrics -n 1 --samplers gpu_power 2>&1)" || {
  echo "powermetrics failed" >&2
  exit 1
}

value="$(
  echo "$output" | awk -F':' '
    /GPU HW active residency/ {
      val = $2
      sub(/%.*/, "", val)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)
      print val
      exit
    }
  '
)"

if [[ -z "$value" ]]; then
  echo "GPU HW active residency not found" >&2
  exit 1
fi

printf "%.0f\n" "$value"

