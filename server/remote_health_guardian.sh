#!/data/data/com.termux/files/usr/bin/bash
set -u

HOME=/data/data/com.termux/files/home
ROOT="$HOME/stock-trader"
SERVER="$ROOT/server"
ENSURE="$SERVER/ensure_remote_health.sh"
PIDFILE="$HOME/stock-trader-remote-health-guardian.pid"
LOG="$HOME/stock-trader-remote-health-guardian.log"
INTERVAL="${REMOTE_HEALTH_GUARDIAN_SEC:-60}"
MAX_LOG_BYTES="${REMOTE_HEALTH_GUARDIAN_LOG_BYTES:-1048576}"

pid_alive(){
  local p="${1:-}"
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null
}

pid_valid(){
  local p="${1:-}" cmd=""
  pid_alive "$p" || return 1
  cmd=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null || true)
  echo "$cmd" | grep -q 'remote_health_guardian.sh'
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

log(){
  rotate_log
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"
}

cleanup(){
  local current=""
  [ -f "$PIDFILE" ] && current=$(cat "$PIDFILE" 2>/dev/null || true)
  [ "$current" = "$$" ] && rm -f "$PIDFILE" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [ -f "$PIDFILE" ]; then
  old=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ "$old" != "$$" ] && pid_valid "$old"; then
    exit 0
  fi
  rm -f "$PIDFILE" 2>/dev/null || true
fi
echo $$ > "$PIDFILE"
log "remote-health guardian started pid=$$ interval=${INTERVAL}s"

while true; do
  if [ -x "$ENSURE" ] || [ -f "$ENSURE" ]; then
    if ! bash "$ENSURE" >/dev/null 2>&1; then
      log 'remote-health ensure failed; will retry'
    fi
  else
    log "ensure script missing: $ENSURE"
  fi
  sleep "$INTERVAL"
done
