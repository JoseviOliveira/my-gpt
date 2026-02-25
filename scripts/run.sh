#!/usr/bin/env zsh
set -euo pipefail

ECO_ARG=0
args=()
for arg in "$@"; do
  case "$arg" in
    --eco) ECO_ARG=1 ;;
    *) args+=("$arg") ;;
  esac
done
set -- "${args[@]}"

export TTS_WARMUP_ENABLED=0
export OLLAMA_KEEP_ALIVE=-1

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$APP_DIR/log"
mkdir -p "$LOG_DIR"
: "${OLLAMA_URL:=http://127.0.0.1:11434}"

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export ESPEAK_PATH="/opt/homebrew/bin/espeak-ng"
export PHONEMIZER_ESPEAK_PATH="/opt/homebrew/bin/espeak-ng"

# Loading models before user access helps avoid permission issues.
export COQUI_TTS_CACHE_DIR="$APP_DIR/tts_cache"
export COQUI_TTS_CACHE="$COQUI_TTS_CACHE_DIR"
export XDG_CACHE_HOME="$COQUI_TTS_CACHE_DIR"
export HF_HOME="$COQUI_TTS_CACHE_DIR"
export HF_HUB_CACHE="$COQUI_TTS_CACHE_DIR"

cd "$APP_DIR"

# Load unified environment config (optional)
[[ -f ./.chat.conf ]] && . ./.chat.conf

apply_eco_profile() {
  export OLLAMA_NUM_PARALLEL="${ECO_OLLAMA_NUM_PARALLEL:-1}"
  export OLLAMA_MAX_LOADED_MODELS="${ECO_OLLAMA_MAX_LOADED_MODELS:-1}"
  export OLLAMA_KEEP_ALIVE="${ECO_OLLAMA_KEEP_ALIVE:-1m}"
  export MODEL="${ECO_MODEL:-gemma3:4b}"
  export SUMMARY_MODEL="${ECO_SUMMARY_MODEL:-$MODEL}"
  export SUMMARY_MAX_WORDS="${ECO_SUMMARY_MAX_WORDS:-18}"
  export METADATA_IDLE_DELAY="${ECO_METADATA_IDLE_DELAY:-20}"
  export METADATA_IDLE_CHECK_INTERVAL="${ECO_METADATA_IDLE_CHECK_INTERVAL:-5}"
  export APP_LOG_LEVEL="${ECO_APP_LOG_LEVEL:-INFO}"
  export STT_MODE="${ECO_STT_MODE:-browser}"
  export TTS_MODE="${ECO_TTS_MODE:-browser}"
}

: "${ECO_MODE:=0}"
if [[ $ECO_ARG -eq 1 ]]; then
  ECO_MODE=1
fi
if [[ "$ECO_MODE" == "1" || "$ECO_MODE" == "true" ]]; then
  apply_eco_profile
fi

set_eco_state() {
  if [[ "$ECO_MODE" == "1" || "$ECO_MODE" == "true" ]]; then
    echo "1" > "$ECO_STATE_FILE"
  else
    rm -f "$ECO_STATE_FILE"
  fi
}

is_eco_active() {
  if [[ "$ECO_MODE" == "1" || "$ECO_MODE" == "true" ]]; then
    return 0
  fi
  [[ -f "$ECO_STATE_FILE" ]]
}

: "${MODEL:=gpt-oss:20b}"
: "${CHAT_PORT:=4200}"
PIDFILE="$APP_DIR/.flask.pid"
OLLAMA_PIDFILE="$APP_DIR/.ollama.pid"
ECO_STATE_FILE="$APP_DIR/.eco_mode.state"
: "${OLLAMA_MANAGE:=1}"

: "${CADDY_ENABLE:=1}"
: "${CADDYFILE:=$APP_DIR/deploy/Caddyfile}"

service_line() {
  local action="$1" service="$2" state="$3" detail="${4:-}"
  local icon="ℹ️"
  case "$state" in
    ok) icon="✅" ;;
    warn) icon="⚠️" ;;
    skip) icon="⏭" ;;
    fail) icon="❌" ;;
    info) icon="ℹ️" ;;
    stopped) icon="🛑" ;;
  esac
  if [[ -n "$detail" ]]; then
    printf "%s %s.... %s %s\n" "$action" "$service" "$icon" "$detail"
  else
    printf "%s %s.... %s\n" "$action" "$service" "$icon"
  fi
}

is_caddy_running() {
  pgrep -x caddy >/dev/null 2>&1
}

start_caddy() {
  local action="Starting"
  local service="Caddy"
  if [[ "$CADDY_ENABLE" != "1" && "$CADDY_ENABLE" != "true" ]]; then
    service_line "$action" "$service" "skip" "disabled"
    return 0
  fi
  if ! command -v caddy >/dev/null 2>&1; then
    service_line "$action" "$service" "fail" "not installed"
    exit 1
  fi
  if [[ ! -f "$CADDYFILE" ]]; then
    service_line "$action" "$service" "fail" "Caddyfile missing"
    exit 1
  fi
  if is_caddy_running; then
    service_line "$action" "$service" "ok" "running"
    return 0
  fi
  if ! caddy start --config "$CADDYFILE" > "$LOG_DIR/caddy.out.log" 2> "$LOG_DIR/caddy.err.log"; then
    service_line "$action" "$service" "warn" "start failed (no HTTPS)"
    return 0
  fi
  for ((i=1;i<=5;i++)); do
    if is_caddy_running; then
      service_line "$action" "$service" "ok" "running"
      return 0
    fi
    sleep 1
  done
  service_line "$action" "$service" "warn" "not ready (no HTTPS)"
}

stop_caddy() {
  local action="Stopping"
  local service="Caddy"
  if [[ "$CADDY_ENABLE" != "1" && "$CADDY_ENABLE" != "true" ]]; then
    service_line "$action" "$service" "skip" "disabled"
    return 0
  fi
  if ! command -v caddy >/dev/null 2>&1; then
    service_line "$action" "$service" "skip" "not installed"
    return 0
  fi
  if is_caddy_running; then
    caddy stop >/dev/null 2>&1 || true
    service_line "$action" "$service" "stopped" "stopped"
  else
    service_line "$action" "$service" "info" "not running"
  fi
}

resolve_basic_auth() {
  local first user pass
  if [[ -n "${APP_USERS:-}" ]]; then
    first="${APP_USERS%%,*}"
    if [[ "$first" == *:* ]]; then
      user="${first%%:*}"
      pass="${first#*:}"
      if [[ -n "$user" && -n "$pass" ]]; then
        echo "${user}:${pass}"
        return 0
      fi
    fi
  fi
  if [[ -n "${APP_USER:-}" && -n "${APP_PASS:-}" ]]; then
    echo "${APP_USER}:${APP_PASS}"
    return 0
  fi
  echo "me:change-me"
}

AUTH_PAIR="$(resolve_basic_auth)"
AUTH_CURL_ARGS=()
if [[ -n "$AUTH_PAIR" ]]; then
  AUTH_CURL_ARGS=(-u "$AUTH_PAIR")
fi

show_perf_profile() {
  local eco_label="off"
  if [[ "$ECO_MODE" == "1" || "$ECO_MODE" == "true" ]]; then
    eco_label="on"
  fi
  local summary_display="${SUMMARY_MODEL:-$MODEL}"
  echo "ℹ️  Perf profile: eco=${eco_label} model=${MODEL} summary=${summary_display} summary_max_words=${SUMMARY_MAX_WORDS:-default} ollama_parallel=${OLLAMA_NUM_PARALLEL:-default} ollama_max_loaded=${OLLAMA_MAX_LOADED_MODELS:-default} ollama_keep_alive=${OLLAMA_KEEP_ALIVE:-default} stt=${STT_MODE:-default} tts=${TTS_MODE:-default} metadata_idle=${METADATA_IDLE_DELAY:-default}s metadata_check=${METADATA_IDLE_CHECK_INTERVAL:-default}s log=${APP_LOG_LEVEL:-default}"
}

wait_http() {
  local url="$1" tries="${2:-60}" sleep_s="${3:-1}"
  for ((i=1;i<=tries;i++)); do
    if curl -fsS "${AUTH_CURL_ARGS[@]}" "$url" >/dev/null 2>&1; then return 0; fi
    sleep "$sleep_s"
  done
  return 1
}

get_port_pids() {
  # PIDs listening on CHAT_PORT (may be multiple if something went wrong)
  lsof -nP -t -iTCP:${CHAT_PORT} -sTCP:LISTEN 2>/dev/null | tr '\n' ' ' || true
}

is_running() {
  curl -fsS "${AUTH_CURL_ARGS[@]}" "http://127.0.0.1:${CHAT_PORT}/health" >/dev/null 2>&1 || [[ -n "$(get_port_pids)" ]]
}

wait_port_free() {
  local tries="${1:-30}"
  for ((i=1;i<=tries;i++)); do
    if [[ -z "$(get_port_pids)" ]]; then return 0; fi
    sleep 1
  done
  return 1
}

ensure_ollama() {
  local action="Starting"
  local service="Ollama server"
  if ! command -v ollama >/dev/null 2>&1; then
    service_line "$action" "$service" "fail" "not installed"
    exit 1
  fi
  if [[ "$OLLAMA_MANAGE" != "1" && "$OLLAMA_MANAGE" != "true" ]]; then
    service_line "$action" "$service" "stopped" "stopped"
    return 0
  fi
  if ! curl -fsS "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
    # Use explicit binary to avoid client/server mismatches when multiple installs exist
    nohup /opt/homebrew/bin/ollama serve > "$LOG_DIR/ollama.out.log" 2> "$LOG_DIR/ollama.err.log" &
    echo $! > "$OLLAMA_PIDFILE"
    disown || true
    if ! wait_http "${OLLAMA_URL}/api/tags" 90 1; then
      service_line "$action" "$service" "fail" "not ready"
      exit 1
    fi
  fi
  if ! ollama show "$MODEL" >/dev/null 2>&1; then
    if ! ollama pull "$MODEL" >> "$LOG_DIR/ollama.out.log" 2>> "$LOG_DIR/ollama.err.log"; then
      service_line "$action" "$service" "fail" "model pull failed"
      exit 1
    fi
  fi
  if [[ -n "${SUMMARY_MODEL:-}" && "$SUMMARY_MODEL" != "$MODEL" ]]; then
    if ! ollama show "$SUMMARY_MODEL" >/dev/null 2>&1; then
      if ! ollama pull "$SUMMARY_MODEL" >> "$LOG_DIR/ollama.out.log" 2>> "$LOG_DIR/ollama.err.log"; then
        service_line "$action" "$service" "fail" "summary model pull failed"
        exit 1
      fi
    fi
  fi
  if is_eco_active; then
    service_line "$action" "$service" "ok" "running on eco mode"
  else
    service_line "$action" "$service" "ok" "running"
  fi
}

stop_ollama() {
  local action="Stopping"
  local service="Ollama server"
  if [[ "$OLLAMA_MANAGE" != "1" && "$OLLAMA_MANAGE" != "true" ]]; then
    service_line "$action" "$service" "stopped" "stopped"
    return 0
  fi

  local killed_any=0

  # Only stop Ollama if this script started it (tracked via pidfile)
  if [[ -f "$OLLAMA_PIDFILE" ]]; then
    local opid
    opid="$(cat "$OLLAMA_PIDFILE" 2>/dev/null || true)"
    if [[ -n "$opid" ]] && kill -0 "$opid" 2>/dev/null; then
      kill "$opid" 2>/dev/null || true
      sleep 1
      kill -0 "$opid" 2>/dev/null && kill -9 "$opid" 2>/dev/null || true
      killed_any=1
    fi
    # Stale pidfile
    rm -f "$OLLAMA_PIDFILE"
  fi

  if curl -fsS "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
    service_line "$action" "$service" "warn" "running (not managed by this repo)"
  else
    service_line "$action" "$service" "stopped" "stopped"
  fi
}

start_flask() {
  local action="Starting"
  local service="Web server"
  if is_running; then
    service_line "$action" "$service" "ok" "running"
    return 0
  fi
  # Use Python 3.11 virtual environment
  if [[ -d "$APP_DIR/chat_env/bin" ]]; then
    source "$APP_DIR/chat_env/bin/activate"
  else
    service_line "$action" "$service" "fail" "chat_env missing"
    exit 1
  fi
  nohup python app.py > "$LOG_DIR/server.out.log" 2> "$LOG_DIR/server.err.log" &
  echo $! > "$PIDFILE"
  disown || true
  if ! wait_http "http://127.0.0.1:${CHAT_PORT}/health" 30 1; then
    service_line "$action" "$service" "fail" "not ready"
    exit 1
  fi
  service_line "$action" "$service" "ok" "running"
}

stop_flask() {
  local action="Stopping"
  local service="Web server"
  local killed_any=0
  # 1) PID from our file (if it exists)
  if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    kill "$(cat "$PIDFILE")" || true
    rm -f "$PIDFILE"
    killed_any=1
  fi
  # 2) Anything listening on CHAT_PORT (regardless of name/case)
  local pids
  pids="$(get_port_pids)"
  if [[ -n "$pids" ]]; then
    # Try graceful first
    for p in $pids; do kill "$p" 2>/dev/null || true; done
    sleep 1
    # Force kill leftovers
    pids="$(get_port_pids)"
    if [[ -n "$pids" ]]; then
      for p in $pids; do kill -9 "$p" 2>/dev/null || true; done
    fi
    killed_any=1
  fi
  # Wait until port is free
  wait_port_free 10 || true

  if [[ $killed_any -eq 1 ]]; then
    service_line "$action" "$service" "stopped" "stopped"
  else
    service_line "$action" "$service" "stopped" "stopped"
  fi
}

status_flask() {
  local action="Checking"
  local service="Web server"
  local health_url="http://127.0.0.1:${CHAT_PORT}/health"
  local gpu_url="http://127.0.0.1:${CHAT_PORT}/api/gpu"
  local temp_url="http://127.0.0.1:${CHAT_PORT}/api/temperature"
  local health_ok=0
  local gpu_ok=0
  local temp_ok=0

  if curl -fsS --connect-timeout 1 --max-time 3 "${AUTH_CURL_ARGS[@]}" "$health_url" >/dev/null 2>&1; then
    health_ok=1
  fi
  # Hardware probes behind these endpoints can legitimately take >3s on macOS.
  if curl -fsS --connect-timeout 1 --max-time 15 "${AUTH_CURL_ARGS[@]}" "$gpu_url" >/dev/null 2>&1; then
    gpu_ok=1
  fi
  if curl -fsS --connect-timeout 1 --max-time 15 "${AUTH_CURL_ARGS[@]}" "$temp_url" >/dev/null 2>&1; then
    temp_ok=1
  fi

  # Benchmark readiness considers app reachable when /api/gpu or /api/temperature responds.
  if [[ $health_ok -eq 1 && ( $gpu_ok -eq 1 || $temp_ok -eq 1 ) ]]; then
    service_line "$action" "$service" "ok" "running (health+api ready)"
  elif [[ $health_ok -eq 1 ]]; then
    service_line "$action" "$service" "warn" "health only (api endpoints not ready)"
  else
    service_line "$action" "$service" "stopped" "stopped"
  fi
}

status_ollama() {
  local action="Checking"
  local service="Ollama server"
  if ! command -v ollama >/dev/null 2>&1; then
    service_line "$action" "$service" "skip" "not installed"
    return 0
  fi
  if [[ "$OLLAMA_MANAGE" != "1" && "$OLLAMA_MANAGE" != "true" ]]; then
    service_line "$action" "$service" "skip" "disabled"
    return 0
  fi
  if curl -fsS "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
    if is_eco_active; then
      service_line "$action" "$service" "ok" "running on eco mode"
    else
      service_line "$action" "$service" "ok" "running"
    fi
  else
    service_line "$action" "$service" "stopped" "stopped"
  fi
}

status_caddy() {
  local action="Checking"
  local service="Caddy"
  if [[ "$CADDY_ENABLE" != "1" && "$CADDY_ENABLE" != "true" ]]; then
    service_line "$action" "$service" "skip" "disabled"
    return 0
  fi
  if ! command -v caddy >/dev/null 2>&1; then
    service_line "$action" "$service" "skip" "not installed"
    return 0
  fi
  if is_caddy_running; then
    service_line "$action" "$service" "ok" "running"
  else
    service_line "$action" "$service" "stopped" "stopped"
  fi
}

case "${1:-start}" in
  start)
    set_eco_state
    ensure_ollama
    start_flask
    start_caddy
    show_perf_profile
    ;;
  stop)
    stop_caddy
    stop_flask
    stop_ollama
    set_eco_state
    ;;
  restart)
    set_eco_state
    stop_caddy
    stop_flask
    stop_ollama
    ensure_ollama
    start_flask
    start_caddy
    show_perf_profile
    ;;
  status)
    status_ollama
    status_flask
    status_caddy
    show_perf_profile
    ;;
  *)
    echo "Usage: $0 [--eco] {start|stop|restart|status}"
    exit 2
    ;;
esac
