#!/bin/zsh
set -euo pipefail

# Always use absolute paths under launchd (no inherited PATH or cwd)
APP_DIR="/Users/sevi/local-chat"
cd "$APP_DIR"

# Load unified environment config (if present)
[[ -f ./.chat.conf ]] && . ./.chat.conf

# Exec in FOREGROUND so launchd can supervise/restart it
exec "$APP_DIR/chat_env/bin/python" "$APP_DIR/app.py"
