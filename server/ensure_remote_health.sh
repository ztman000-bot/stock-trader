#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$HOME/stock-trader"
SERVER="$ROOT/server"
PIDFILE="$HOME/stock-trader-remote-health.pid"
LOG="$HOME/stock-trader-remote-health.log"
PY="/data/data/com.termux/files/usr/bin/python"

pid_alive(){
  local p="${1:-}"
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null
}

pid_valid(){
  local p="${1:-}" cmd=""
  pid_alive "$p" || return 1
  cmd=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null || true)
  echo "$cmd" | grep -q 'remote_health_daemon.py --daemon'
}

old=""
[ -f "$PIDFILE" ] && old=$(cat "$PIDFILE" 2>/dev/null || true)
if pid_valid "$old"; then
  exit 0
fi
rm -f "$PIDFILE" 2>/dev/null || true

mkdir -p "$(dirname "$LOG")"
cd "$SERVER"
nohup "$PY" remote_health_daemon.py --daemon >>"$LOG" 2>&1 &
echo $! > "$PIDFILE"
sleep 1
if ! pid_valid "$(cat "$PIDFILE" 2>/dev/null || true)"; then
  echo '[WARN] remote health daemon failed to start; Stock Trader server will continue.'
  tail -n 20 "$LOG" 2>/dev/null || true
  exit 1
fi

echo "[OK] remote health daemon started PID=$(cat "$PIDFILE")"
