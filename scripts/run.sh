#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$PROJECT_DIR/data/run"
PID_FILE="$RUNTIME_DIR/research.pid"
LOG_FILE="$RUNTIME_DIR/research.log"
PYTHON="$PROJECT_DIR/.venv/bin/python"

export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
cd "$PROJECT_DIR"

is_running() {
  local pid
  [[ -f "$PID_FILE" ]] || return 1
  pid="$(<"$PID_FILE")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  [[ "$(readlink "/proc/$pid/cwd" 2>/dev/null || true)" == "$PROJECT_DIR" ]] || return 1
  tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -q "mastermind_tick.web"
}

stop_server() {
  if ! is_running; then
    rm -f "$PID_FILE"
    echo "Research server is not running."
    return 0
  fi

  local pid
  pid="$(<"$PID_FILE")"
  kill "$pid"
  for _ in {1..50}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      echo "Research server stopped."
      return 0
    fi
    sleep 0.1
  done
  echo "Research server did not stop within 5 seconds (PID $pid)." >&2
  return 1
}

mode="detach"
server_args=()
for argument in "$@"; do
  case "$argument" in
    --foreground) mode="foreground" ;;
    --stop) mode="stop" ;;
    --status) mode="status" ;;
    *) server_args+=("$argument") ;;
  esac
done

case "$mode" in
  foreground)
    exec "$PYTHON" -m mastermind_tick.web "${server_args[@]}"
    ;;
  stop)
    stop_server
    ;;
  status)
    if is_running; then
      echo "Research server is running (PID $(<"$PID_FILE"))."
      echo "Log: $LOG_FILE"
    else
      rm -f "$PID_FILE"
      echo "Research server is not running."
      exit 1
    fi
    ;;
  detach)
    mkdir -p "$RUNTIME_DIR"
    if is_running; then
      echo "Research server is already running (PID $(<"$PID_FILE"))."
      echo "Log: $LOG_FILE"
      exit 1
    fi
    rm -f "$PID_FILE"
    nohup "$PYTHON" -m mastermind_tick.web "${server_args[@]}" \
      </dev/null >>"$LOG_FILE" 2>&1 &
    pid=$!
    echo "$pid" >"$PID_FILE"
    for _ in {1..20}; do
      if ! is_running; then
        rm -f "$PID_FILE"
        echo "Research server failed to start. Check $LOG_FILE" >&2
        exit 1
      fi
      sleep 0.1
    done
    echo "Research server started in detached mode (PID $pid)."
    echo "Log: $LOG_FILE"
    echo "Stop: $PROJECT_DIR/scripts/run.sh --stop"
    ;;
esac
