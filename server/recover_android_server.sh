#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$HOME/stock-trader"
SERVER="$ROOT/server"
PREFIX=/data/data/com.termux/files/usr
LOG="$HOME/stock-trader-server.log"
PIDFILE="$HOME/stock-trader-server.pid"

cd "$SERVER"

if [ ! -f .env ]; then
  echo "[ERROR] $SERVER/.env not found"
  exit 10
fi

grep -Eq '^APP_MODE[[:space:]]*=[[:space:]]*paper[[:space:]]*$' .env || { echo '[ERROR] APP_MODE=paper not verified'; exit 11; }
grep -Eq '^ENABLE_TRADING[[:space:]]*=[[:space:]]*false[[:space:]]*$' .env || { echo '[ERROR] ENABLE_TRADING=false not verified'; exit 12; }

health_ok(){
  "$PREFIX/bin/python" - <<'PY' >/dev/null 2>&1
import urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3) as r:
        body=r.read(512).decode('utf-8','ignore').replace(' ','').lower()
        raise SystemExit(0 if r.status==200 and '"ok":true' in body else 1)
except Exception:
    raise SystemExit(1)
PY
}

if health_ok; then
  echo '[OK] Android Stock Trader server is already healthy.'
  exit 0
fi

command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true
pkill -f 'uvicorn android_unified_app:app' >/dev/null 2>&1 || true
sleep 2

nohup env \
  APP_MODE=paper \
  ENABLE_TRADING=false \
  AUTO_BACKFILL=false \
  MASTER_PRESELECT="${MASTER_PRESELECT:-120}" \
  FOCUS_SIZE="${FOCUS_SIZE:-20}" \
  NH_REST_MIN_INTERVAL="${NH_REST_MIN_INTERVAL:-0.30}" \
  TEMP_PHONE_SERVER=true \
  PHONE_PERFORMANCE_PROFILE="${PHONE_PERFORMANCE_PROFILE:-dedicated}" \
  FAST_RESEARCH_START_DELAY_SEC="${FAST_RESEARCH_START_DELAY_SEC:-90}" \
  FAST_RESEARCH_INTERVAL_MIN="${FAST_RESEARCH_INTERVAL_MIN:-60}" \
  RESEARCH_INTERVAL_MIN="${RESEARCH_INTERVAL_MIN:-120}" \
  HISTORY_MIN_INTERVAL_MIN="${HISTORY_MIN_INTERVAL_MIN:-240}" \
  PYTHONUNBUFFERED=1 \
  "$PREFIX/bin/python" -m uvicorn android_unified_app:app \
    --host 0.0.0.0 --port 8000 --workers 1 --no-access-log \
    >> "$LOG" 2>&1 &
PID=$!
echo "$PID" > "$PIDFILE"
echo "[INFO] start requested PID=$PID"

for _ in $(seq 1 60); do
  if health_ok; then
    echo '[OK] Android Stock Trader server recovered and is healthy.'
    exit 0
  fi
  sleep 2
done

echo '[ERROR] Server did not become healthy. Last log lines:'
tail -n 40 "$LOG" 2>/dev/null || true
exit 20
