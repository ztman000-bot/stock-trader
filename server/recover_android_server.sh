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

cd "$SERVER"

if [ ! -f .env ]; then
  echo "[ERROR] $SERVER/.env not found"
  exit 10
fi

grep -Eq '^APP_MODE[[:space:]]*=[[:space:]]*paper[[:space:]]*$' .env || { echo '[ERROR] APP_MODE=paper not verified'; exit 11; }
grep -Eq '^ENABLE_TRADING[[:space:]]*=[[:space:]]*false[[:space:]]*$' .env || { echo '[ERROR] ENABLE_TRADING=false not verified'; exit 12; }

if [ -f "$UPDATE_FLAG" ]; then
  echo '[INFO] Android update is in progress; recovery is deferred.'
  exit 30
fi

rotate_log(){
  [ -f "$LOG" ] || return 0
  local size
  size=$(wc -c < "$LOG" 2>/dev/null || echo 0)
  if [ "${size:-0}" -gt "$MAX_LOG_BYTES" ]; then
    mv -f "$LOG" "$LOG.1" 2>/dev/null || true
    : > "$LOG"
  fi
}

health_ok(){
  "$PREFIX/bin/python" - <<'PY' >/dev/null 2>&1
import json,urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=8) as r:
        d=json.loads(r.read().decode('utf-8','ignore'))
    if r.status != 200 or not d.get('ok'): raise SystemExit(1)
    if str(d.get('mode','')).lower() != 'paper' or bool(d.get('tradingEnabled')): raise SystemExit(2)
    if not d.get('credentialsConfigured'): raise SystemExit(3)
    if d.get('autoPaper') and not (d.get('paperLoop') or {}).get('running'): raise SystemExit(4)
    if d.get('autoStartCollector') and not (d.get('collector') or {}).get('running'): raise SystemExit(5)
    raise SystemExit(0)
except SystemExit:
    raise
except Exception:
    raise SystemExit(1)
PY
}

if health_ok; then
  echo '[OK] Android Stock Trader server is already healthy.'
  exit 0
fi

command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true
rotate_log

# Terminate every matching Android uvicorn process. A process can still exist
# while /api/health is hung, so PID-alive alone is not treated as healthy.
PIDS=""
if [ -f "$PIDFILE" ]; then PIDS="$(cat "$PIDFILE" 2>/dev/null || true)"; fi
MATCHED=$(pgrep -f 'python.*-m uvicorn android_unified_app:app' 2>/dev/null || true)
PIDS=$(printf '%s\n%s\n' "$PIDS" "$MATCHED" | awk 'NF && !seen[$1]++ {print $1}')
for p in $PIDS; do kill -TERM "$p" 2>/dev/null || true; done
for _ in $(seq 1 10); do
  any=0
  for p in $PIDS; do kill -0 "$p" 2>/dev/null && any=1 || true; done
  [ "$any" = "0" ] && break
  sleep 1
done
for p in $PIDS; do kill -0 "$p" 2>/dev/null && kill -KILL "$p" 2>/dev/null || true; done
sleep 2
rm -f "$HEARTBEAT"

nohup bash "$SERVER/start_android.sh" >> "$LOG" 2>&1 &
PID=$!
echo "$PID" > "$PIDFILE"
echo "[INFO] start requested PID=$PID"

for _ in $(seq 1 60); do
  if health_ok; then
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
