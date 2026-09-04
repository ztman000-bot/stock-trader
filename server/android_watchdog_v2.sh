#!/data/data/com.termux/files/usr/bin/bash
set -u

HOME=/data/data/com.termux/files/home
ROOT="$HOME/stock-trader"
SERVER="$ROOT/server"
PREFIX=/data/data/com.termux/files/usr
LOG="$HOME/stock-trader-watchdog.log"
PIDFILE="$HOME/stock-trader-watchdog.pid"
SERVER_PIDFILE="$HOME/stock-trader-server.pid"
HEARTBEAT="$HOME/.stock-trader-app-heartbeat"
UPDATE_FLAG="$HOME/.stock-trader-update-in-progress"
RECOVER="$SERVER/recover_android_server.sh"
INTERVAL="${WATCHDOG_INTERVAL_SEC:-30}"
HARD_LIMIT="${WATCHDOG_FAILURES_BEFORE_RESTART:-3}"
SOFT_LIMIT="${WATCHDOG_SOFT_FAILURES_BEFORE_RESTART:-6}"
COOLDOWN="${WATCHDOG_COOLDOWN_SEC:-180}"
HEARTBEAT_MAX_AGE="${WATCHDOG_HEARTBEAT_MAX_AGE_SEC:-90}"
MAX_LOG_BYTES="${WATCHDOG_MAX_LOG_BYTES:-2097152}"
FAILS=0
LAST_RESTART=0
UPDATE_LOGGED=0

rotate_log(){
  [ -f "$LOG" ] || return 0
  local size
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

pid_alive(){
  local p="${1:-}"
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null
}

server_pid(){
  if [ -f "$SERVER_PIDFILE" ]; then
    cat "$SERVER_PIDFILE" 2>/dev/null || true
    return
  fi
  pgrep -f 'python.*-m uvicorn android_unified_app:app' 2>/dev/null | head -1 || true
}

heartbeat_fresh(){
  [ -f "$HEARTBEAT" ] || return 1
  "$PREFIX/bin/python" - "$HEARTBEAT" "$HEARTBEAT_MAX_AGE" <<'PY' >/dev/null 2>&1
import json,sys,time
from pathlib import Path
p=Path(sys.argv[1]); limit=float(sys.argv[2])
try:
    d=json.loads(p.read_text(encoding='utf-8'))
    ts=float(d.get('timestamp') or 0)
    raise SystemExit(0 if 0 <= time.time()-ts <= limit else 1)
except Exception:
    raise SystemExit(1)
PY
}

health_ok(){
  "$PREFIX/bin/python" - <<'PY' >/dev/null 2>&1
import json,urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=8) as r:
        d=json.loads(r.read().decode('utf-8','ignore'))
    if r.status != 200 or not d.get('ok'):
        raise SystemExit(1)
    if str(d.get('mode','')).lower() != 'paper' or bool(d.get('tradingEnabled')):
        raise SystemExit(2)
    if not d.get('credentialsConfigured'):
        raise SystemExit(3)
    if d.get('autoPaper') and not (d.get('paperLoop') or {}).get('running'):
        raise SystemExit(4)
    if d.get('autoStartCollector') and not (d.get('collector') or {}).get('running'):
        raise SystemExit(5)
    raise SystemExit(0)
except SystemExit:
    raise
except Exception:
    raise SystemExit(1)
PY
}

safe_env(){
  [ -f "$SERVER/.env" ] && \
  grep -Eq '^APP_MODE[[:space:]]*=[[:space:]]*paper[[:space:]]*$' "$SERVER/.env" && \
  grep -Eq '^ENABLE_TRADING[[:space:]]*=[[:space:]]*false[[:space:]]*$' "$SERVER/.env"
}

cleanup(){
  local current=""
  [ -f "$PIDFILE" ] && current=$(cat "$PIDFILE" 2>/dev/null || true)
  [ "$current" = "$$" ] && rm -f "$PIDFILE"
}
trap cleanup EXIT INT TERM

if [ -f "$PIDFILE" ]; then
  old=$(cat "$PIDFILE" 2>/dev/null || true)
  if pid_alive "$old" && [ "$old" != "$$" ]; then
    exit 0
  fi
fi
echo $$ > "$PIDFILE"
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock >/dev/null 2>&1 || true
log "watchdog-v2 started pid=$$ interval=${INTERVAL}s hard=${HARD_LIMIT} soft=${SOFT_LIMIT} heartbeat=${HEARTBEAT_MAX_AGE}s"

while true; do
  sleep "$INTERVAL"

  if [ -f "$UPDATE_FLAG" ]; then
    FAILS=0
    if [ "$UPDATE_LOGGED" = "0" ]; then
      log 'update in progress; watchdog restart actions paused'
      UPDATE_LOGGED=1
    fi
    continue
  fi
  if [ "$UPDATE_LOGGED" = "1" ]; then
    log 'update flag cleared; watchdog resumed'
    UPDATE_LOGGED=0
  fi

  if ! safe_env; then
    FAILS=0
    log 'SAFETY BLOCK: APP_MODE=paper / ENABLE_TRADING=false not verified; restart disabled'
    sleep 60
    continue
  fi

  if health_ok; then
    FAILS=0
    continue
  fi

  FAILS=$((FAILS + 1))
  spid=$(server_pid)
  alive=0; fresh=0
  pid_alive "$spid" && alive=1
  heartbeat_fresh && fresh=1
  limit="$HARD_LIMIT"
  if [ "$alive" = "1" ] && [ "$fresh" = "1" ]; then
    limit="$SOFT_LIMIT"
  fi
  log "health failure ${FAILS}/${limit} serverPid=${spid:-none} pidAlive=$alive heartbeatFresh=$fresh"

  [ "$FAILS" -lt "$limit" ] && continue
  now=$(date +%s)
  if [ $((now - LAST_RESTART)) -lt "$COOLDOWN" ]; then
    log "restart suppressed by cooldown (${COOLDOWN}s)"
    continue
  fi

  FAILS=0
  LAST_RESTART="$now"
  if [ ! -x "$RECOVER" ]; then
    log "recovery script missing/not executable: $RECOVER"
    continue
  fi

  log 'recovery triggered'
  if ANDROID_SKIP_WATCHDOG=1 bash "$RECOVER" --watchdog >> "$LOG" 2>&1; then
    log 'recovery completed healthy'
  else
    rc=$?
    log "recovery failed rc=$rc"
  fi
  sleep 60
done
