#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$HOME/stock-trader"
SERVER="$ROOT/server"
BOOTDIR="$HOME/.termux/boot"
BOOT_SERVER="$BOOTDIR/10-stock-trader.sh"
BOOT_WATCHDOG="$BOOTDIR/20-stock-trader-watchdog.sh"
WATCHDOG="$SERVER/android_watchdog.sh"
PIDFILE="$HOME/stock-trader-server.pid"
WDPIDFILE="$HOME/stock-trader-watchdog.pid"

env_has(){
  local key="$1" value="$2"
  grep -Eq "^${key}[[:space:]]*=[[:space:]]*${value}[[:space:]]*$" "$SERVER/.env"
}

cd "$SERVER"
env_has APP_MODE paper || { echo 'SAFETY BLOCK: APP_MODE=paper 필요'; exit 1; }
env_has ENABLE_TRADING false || { echo 'SAFETY BLOCK: ENABLE_TRADING=false 필요'; exit 1; }

mkdir -p "$BOOTDIR"

cat > "$BOOT_SERVER" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
HOME=/data/data/com.termux/files/home
SERVER="$HOME/stock-trader/server"
LOG="$HOME/stock-trader-boot.log"
sleep 20
termux-wake-lock 2>/dev/null || true
cd "$SERVER" || exit 1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Stock Trader Android boot start" >> "$LOG"
if ! grep -Eq '^APP_MODE[[:space:]]*=[[:space:]]*paper[[:space:]]*$' .env || ! grep -Eq '^ENABLE_TRADING[[:space:]]*=[[:space:]]*false[[:space:]]*$' .env; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] SAFETY BLOCK" >> "$LOG"
  exit 1
fi
if curl -fsS --max-time 3 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Server already running" >> "$LOG"
  exit 0
fi
nohup bash "$SERVER/start_android.sh" >> "$HOME/stock-trader-server.log" 2>&1 &
echo $! > "$HOME/stock-trader-server.pid"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Android server PID=$!" >> "$LOG"
EOF
chmod +x "$BOOT_SERVER"

cat > "$WATCHDOG" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
HOME=/data/data/com.termux/files/home
SERVER="$HOME/stock-trader/server"
HEALTH="http://127.0.0.1:8000/api/health"
LOG="$HOME/stock-trader-watchdog.log"
PIDFILE="$HOME/stock-trader-server.pid"
FAILS=0
while true; do
  sleep 30
  if ! grep -Eq '^APP_MODE[[:space:]]*=[[:space:]]*paper[[:space:]]*$' "$SERVER/.env" || ! grep -Eq '^ENABLE_TRADING[[:space:]]*=[[:space:]]*false[[:space:]]*$' "$SERVER/.env"; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] SAFETY BLOCK" >> "$LOG"
    FAILS=0
    continue
  fi
  if curl -fsS --max-time 5 "$HEALTH" >/dev/null 2>&1; then
    FAILS=0
    continue
  fi
  FAILS=$((FAILS + 1))
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Health failure $FAILS/3" >> "$LOG"
  [ "$FAILS" -lt 3 ] && continue
  FAILS=0
  if [ -f "$PIDFILE" ]; then
    OLDPID=$(cat "$PIDFILE" 2>/dev/null || true)
    if [ -n "${OLDPID:-}" ] && kill -0 "$OLDPID" 2>/dev/null; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] PID $OLDPID alive; restart skipped" >> "$LOG"
      continue
    fi
  fi
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Restarting Android Stock Trader" >> "$LOG"
  cd "$SERVER" || continue
  nohup bash "$SERVER/start_android.sh" >> "$HOME/stock-trader-server.log" 2>&1 &
  echo $! > "$PIDFILE"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] New PID=$!" >> "$LOG"
  sleep 120
done
EOF
chmod +x "$WATCHDOG"

cat > "$BOOT_WATCHDOG" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
HOME=/data/data/com.termux/files/home
WATCHDOG="$HOME/stock-trader/server/android_watchdog.sh"
PIDFILE="$HOME/stock-trader-watchdog.pid"
LOG="$HOME/stock-trader-watchdog.log"
sleep 180
if [ -f "$PIDFILE" ]; then
  PID=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Watchdog already running PID=$PID" >> "$LOG"
    exit 0
  fi
fi
nohup "$WATCHDOG" >/dev/null 2>&1 &
echo $! > "$PIDFILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Watchdog started PID=$!" >> "$LOG"
EOF
chmod +x "$BOOT_WATCHDOG"

if [ -f "$WDPIDFILE" ]; then
  WDPID=$(cat "$WDPIDFILE" 2>/dev/null || true)
  [ -n "${WDPID:-}" ] && kill "$WDPID" 2>/dev/null || true
fi

OLDPID=$(pgrep -f 'uvicorn .*--port 8000' | head -1 || true)
if [ -n "${OLDPID:-}" ]; then
  echo "기존 서버 종료 PID=$OLDPID"
  kill "$OLDPID" 2>/dev/null || true
  sleep 3
fi

cd "$SERVER"
nohup bash "$SERVER/start_android.sh" >> "$HOME/stock-trader-server.log" 2>&1 &
echo $! > "$PIDFILE"
NEWPID=$!
nohup "$WATCHDOG" >/dev/null 2>&1 &
echo $! > "$WDPIDFILE"

echo "Android 업데이트 모드 설치 완료"
echo "SERVER PID=$NEWPID"
echo "앞으로 대시보드의 ↻ 업데이트 버튼을 사용할 수 있습니다."
