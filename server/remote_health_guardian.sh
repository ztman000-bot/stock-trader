#!/data/data/com.termux/files/usr/bin/bash
set -u

HOME=/data/data/com.termux/files/home
ROOT="$HOME/stock-trader"
SERVER="$ROOT/server"
ENSURE="$SERVER/ensure_remote_health.sh"
WATCHDOG="$SERVER/android_watchdog_v2.sh"
WDPIDFILE="$HOME/stock-trader-watchdog.pid"
UPDATE_FLAG="$HOME/.stock-trader-update-in-progress"
PIDFILE="$HOME/stock-trader-remote-health-guardian.pid"
LOG="$HOME/stock-trader-remote-health-guardian.log"
INTERVAL="${REMOTE_HEALTH_GUARDIAN_SEC:-60}"
MAX_LOG_BYTES="${REMOTE_HEALTH_GUARDIAN_LOG_BYTES:-1048576}"

pid_alive(){
  local p="${1:-}"
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null
}

pid_cmdline(){
  local p="${1:-}"
  [ -n "$p" ] || return 1
  tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null || true
}

pid_valid(){
  local p="${1:-}" cmd=""
  pid_alive "$p" || return 1
  cmd=$(pid_cmdline "$p")
  echo "$cmd" | grep -q 'remote_health_guardian.sh'
}

watchdog_pid_valid(){
  local p="${1:-}" cmd=""
  pid_alive "$p" || return 1
  cmd=$(pid_cmdline "$p")
  echo "$cmd" | grep -q "$SERVER/android_watchdog_v2.sh"
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

ensure_watchdog(){
  # The Android updater owns watchdog replacement while its flag is present.
  [ -f "$UPDATE_FLAG" ] && return 0
  [ -f "$WATCHDOG" ] || {
    log "watchdog script missing: $WATCHDOG"
    return 1
  }

  local old=""
  [ -f "$WDPIDFILE" ] && old=$(cat "$WDPIDFILE" 2>/dev/null || true)
  if watchdog_pid_valid "$old"; then
    return 0
  fi

  rm -f "$WDPIDFILE" 2>/dev/null || true
  chmod +x "$WATCHDOG" 2>/dev/null || true
  nohup "$WATCHDOG" >/dev/null 2>&1 &
  local launched=$!
  sleep 1

  # The child owns singleton locking/PID registration. A concurrent launcher
  # may exit and point the PID file at the already-running canonical process.
  local current=""
  [ -f "$WDPIDFILE" ] && current=$(cat "$WDPIDFILE" 2>/dev/null || true)
  if watchdog_pid_valid "$current"; then
    log "watchdog restored pid=$current launcherPid=$launched"
    return 0
  fi
  log "watchdog restore failed launcherPid=$launched"
  return 1
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
log "remote-health guardian started pid=$$ interval=${INTERVAL}s watchdogSupervision=true"

while true; do
  if [ -x "$ENSURE" ] || [ -f "$ENSURE" ]; then
    if ! bash "$ENSURE" >/dev/null 2>&1; then
      log 'remote-health ensure failed; will retry'
    fi
  else
    log "ensure script missing: $ENSURE"
  fi

  if ! ensure_watchdog; then
    log 'watchdog ensure failed; will retry'
  fi
  sleep "$INTERVAL"
done
