#!/bin/zsh
set -euo pipefail

# Always use absolute paths under launchd (no inherited PATH or cwd)
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd -P)"
APP_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
cd "$APP_DIR"

# Load unified environment config (if present)
[[ -f ./.chat.conf ]] && . ./.chat.conf

# Exec in FOREGROUND so launchd can supervise/restart it
exec "$APP_DIR/chat_env/bin/python" "$APP_DIR/app.py"
