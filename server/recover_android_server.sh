#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$HOME/stock-trader"
SERVER="$ROOT/server"
PREFIX=/data/data/com.termux/files/usr
LOG="$HOME/stock-trader-server.log"
PIDFILE="$HOME/stock-trader-server.pid"
HEARTBEAT="$HOME/.stock-trader-app-heartbeat"
UPDATE_FLAG="$HOME/.stock-trader-update-in-progress"
MAX_LOG_BYTES="${SERVER_MAX_LOG_BYTES:-8388608}"
FORCE_RESTART=0
for arg in "$@"; do
  case "$arg" in
    --force|--restart) FORCE_RESTART=1 ;;
  esac
done

rotate_log(){
  [ -f "$LOG" ] || return 0
  local size
  size=$(wc -c < "$LOG" 2>/dev/null || echo 0)
  if [ "${size:-0}" -gt "$MAX_LOG_BYTES" ]; then
    mv -f "$LOG" "$LOG.1" 2>/dev/null || true
    : > "$LOG"
  fi
}

pid_alive(){
  local p="${1:-}"
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null
}

pid_cmdline(){
  local p="${1:-}"
  [ -n "$p" ] || return 1
  tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null || true
}

is_server_pid(){
  local p="${1:-}" cmd=""
  pid_alive "$p" || return 1
  cmd=$(pid_cmdline "$p")
  echo "$cmd" | grep -qE 'python(3)? .*[-]m uvicorn android_unified_app:app|uvicorn android_unified_app:app'
}

discover_server_pid(){
  local p=""
  if [ -f "$PIDFILE" ]; then
    p=$(cat "$PIDFILE" 2>/dev/null || true)
    if is_server_pid "$p"; then
      echo "$p"
      return 0
    fi
  fi
  for p in $(pgrep -f 'python.*-m uvicorn android_unified_app:app' 2>/dev/null || true); do
    if is_server_pid "$p"; then
      echo "$p"
      return 0
    fi
  done
  return 1
}

repair_server_pidfile(){
  local p="" old=""
  old=$(cat "$PIDFILE" 2>/dev/null || true)
  p=$(discover_server_pid 2>/dev/null || true)
  if [ -n "$p" ]; then
    if [ "$old" != "$p" ]; then
      printf '%s\n' "$p" > "$PIDFILE"
      echo "[OK] server PID file self-repaired: PID=$p (previous=${old:-none})"
    fi
    echo "$p"
    return 0
  fi
  [ -f "$PIDFILE" ] && rm -f "$PIDFILE" 2>/dev/null || true
  return 1
}

is_update_pid(){
  local p="${1:-}" cmd=""
  pid_alive "$p" || return 1
  cmd=$(pid_cmdline "$p")
  echo "$cmd" | grep -q 'android_update.sh'
}

update_active(){
  [ -f "$UPDATE_FLAG" ] || return 1
  local upid="" started=""
  read -r upid started < "$UPDATE_FLAG" 2>/dev/null || true
  if is_update_pid "$upid"; then
    return 0
  fi
  echo "[WARN] stale/unrelated update flag removed (pid=${upid:-none}, started=${started:-unknown})"
  rm -f "$UPDATE_FLAG" 2>/dev/null || true
  return 1
}

cd "$SERVER"

if [ ! -f .env ]; then
  echo "[ERROR] $SERVER/.env not found"
  exit 10
fi

grep -Eq '^APP_MODE[[:space:]]*=[[:space:]]*paper[[:space:]]*$' .env || { echo '[ERROR] APP_MODE=paper not verified'; exit 11; }
grep -Eq '^ENABLE_TRADING[[:space:]]*=[[:space:]]*false[[:space:]]*$' .env || { echo '[ERROR] ENABLE_TRADING=false not verified'; exit 12; }

if update_active; then
  echo '[INFO] Android update is in progress; recovery is deferred.'
  exit 30
fi

health_ok(){
  "$PREFIX/bin/python" - <<'PY' >/dev/null 2>&1
import json,time,urllib.request
from datetime import datetime

def age_seconds(value):
    if not value:
        return None
    try:
        dt=datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt=dt.astimezone()
        return max(0.0, time.time()-dt.timestamp())
    except Exception:
        return None

try:
    with urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=8) as r:
        d=json.loads(r.read().decode('utf-8','ignore'))
    if r.status != 200 or not d.get('ok'): raise SystemExit(1)
    if str(d.get('mode','')).lower() != 'paper' or bool(d.get('tradingEnabled')): raise SystemExit(2)
    if not d.get('credentialsConfigured'): raise SystemExit(3)
    paper=d.get('paperLoop') or {}
    if d.get('autoPaper'):
        if not paper.get('running'): raise SystemExit(4)
        age=age_seconds(paper.get('lastCycleAt'))
        started=age_seconds(paper.get('startedAt'))
        if age is None:
            if started is None or started > 45: raise SystemExit(6)
        elif age > 45: raise SystemExit(6)
    collector=d.get('collector') or {}
    if d.get('autoStartCollector'):
        if not collector.get('running'): raise SystemExit(5)
        age=age_seconds(collector.get('lastCycleAt'))
        started=age_seconds(collector.get('startedAt'))
        if age is None:
            if started is None or started > 180: raise SystemExit(7)
        elif age > 180: raise SystemExit(7)
    raise SystemExit(0)
except SystemExit:
    raise
except Exception:
    raise SystemExit(1)
PY
}

if [ "$FORCE_RESTART" != "1" ] && health_ok; then
  repair_server_pidfile >/dev/null || true
  echo '[OK] Android Stock Trader server is already healthy.'
  exit 0
fi
if [ "$FORCE_RESTART" = "1" ]; then
  echo '[INFO] Forced safe restart requested.'
fi

command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true
rotate_log

# Terminate only validated Stock Trader uvicorn processes. Android can reuse a
# stale PID, so a PID file alone is never permission to kill a process.
PIDS=""
if [ -f "$PIDFILE" ]; then
  SAVED="$(cat "$PIDFILE" 2>/dev/null || true)"
  if is_server_pid "$SAVED"; then
    PIDS="$SAVED"
  else
    rm -f "$PIDFILE" 2>/dev/null || true
  fi
fi
MATCHED=$(pgrep -f 'python.*-m uvicorn android_unified_app:app' 2>/dev/null || true)
for p in $MATCHED; do
  is_server_pid "$p" && PIDS=$(printf '%s\n%s\n' "$PIDS" "$p" | awk 'NF && !seen[$1]++ {print $1}')
done
for p in $PIDS; do kill -TERM "$p" 2>/dev/null || true; done
for _ in $(seq 1 10); do
  any=0
  for p in $PIDS; do is_server_pid "$p" && any=1 || true; done
  [ "$any" = "0" ] && break
  sleep 1
done
for p in $PIDS; do is_server_pid "$p" && kill -KILL "$p" 2>/dev/null || true; done
sleep 2
rm -f "$HEARTBEAT"

nohup bash "$SERVER/start_android.sh" >> "$LOG" 2>&1 &
PID=$!
echo "$PID" > "$PIDFILE"
echo "[INFO] start requested PID=$PID"

for _ in $(seq 1 60); do
  if health_ok; then
    repair_server_pidfile >/dev/null || true
    echo '[OK] Android Stock Trader server recovered and is healthy.'
    exit 0
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    echo '[ERROR] Server process exited before becoming healthy. Last log lines:'
    tail -n 60 "$LOG" 2>/dev/null || true
    exit 21
  fi
  sleep 2
done

echo '[ERROR] Server did not become healthy. Last log lines:'
tail -n 60 "$LOG" 2>/dev/null || true
exit 20
