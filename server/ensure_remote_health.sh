#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$HOME/stock-trader"
SERVER="$ROOT/server"
PIDFILE="$HOME/stock-trader-remote-health.pid"
LOG="$HOME/stock-trader-remote-health.log"
PY="/data/data/com.termux/files/usr/bin/python"
COMPONENT_VERSION="0.17.12"
MAX_LOG_BYTES="${REMOTE_HEALTH_MAX_LOG_BYTES:-2097152}"

pid_alive(){
  local p="${1:-}"
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null
}

pid_cmdline(){
  local p="${1:-}"
  [ -n "$p" ] || return 1
  tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null || true
}

pid_is_project_daemon(){
  local p="${1:-}" cmd=""
  pid_alive "$p" || return 1
  cmd=$(pid_cmdline "$p")
  echo "$cmd" | grep -q 'remote_health_daemon.py --daemon'
}

pid_valid(){
  local p="${1:-}" cmd=""
  pid_is_project_daemon "$p" || return 1
  cmd=$(pid_cmdline "$p")
  echo "$cmd" | grep -q -- "--instance-version $COMPONENT_VERSION"
}

rotate_log(){
  [ -f "$LOG" ] || return 0
  local size=0
  size=$(wc -c < "$LOG" 2>/dev/null || echo 0)
  if [ "${size:-0}" -gt "$MAX_LOG_BYTES" ]; then
    mv -f "$LOG" "$LOG.1" 2>/dev/null || true
    : > "$LOG"
  fi
}

old=""
[ -f "$PIDFILE" ] && old=$(cat "$PIDFILE" 2>/dev/null || true)
if pid_valid "$old"; then
  exit 0
fi

# A software update can leave the previous beacon process alive. Terminate only
# a positively identified project daemon; never kill an unrelated reused PID.
if pid_is_project_daemon "$old"; then
  kill -TERM "$old" 2>/dev/null || true
  for _ in $(seq 1 10); do
    pid_alive "$old" || break
    sleep 0.2
  done
  pid_is_project_daemon "$old" && kill -KILL "$old" 2>/dev/null || true
fi
rm -f "$PIDFILE" 2>/dev/null || true

rotate_log
mkdir -p "$(dirname "$LOG")"
cd "$SERVER"
nohup "$PY" remote_health_daemon.py --daemon --instance-version "$COMPONENT_VERSION" >>"$LOG" 2>&1 &
echo $! > "$PIDFILE"
sleep 1
if ! pid_valid "$(cat "$PIDFILE" 2>/dev/null || true)"; then
  echo '[WARN] remote health daemon failed to start; Stock Trader server will continue.'
  tail -n 20 "$LOG" 2>/dev/null || true
  exit 1
fi

echo "[OK] remote health daemon v$COMPONENT_VERSION started PID=$(cat "$PIDFILE")"
