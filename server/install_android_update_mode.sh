#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$HOME/stock-trader"
SERVER="$ROOT/server"
PREFIX=/data/data/com.termux/files/usr
BOOTDIR="$HOME/.termux/boot"
BOOT_SERVER="$BOOTDIR/10-stock-trader.sh"
BOOT_WATCHDOG="$BOOTDIR/20-stock-trader-watchdog.sh"
WATCHDOG="$SERVER/android_watchdog_v2.sh"
RECOVER="$SERVER/recover_android_server.sh"
PIDFILE="$HOME/stock-trader-server.pid"
WDPIDFILE="$HOME/stock-trader-watchdog.pid"
LOG="$HOME/stock-trader-boot.log"

env_has(){
  local key="$1" value="$2"
  grep -Eq "^${key}[[:space:]]*=[[:space:]]*${value}[[:space:]]*$" "$SERVER/.env"
}

cd "$SERVER"
env_has APP_MODE paper || { echo 'SAFETY BLOCK: APP_MODE=paper 필요'; exit 1; }
env_has ENABLE_TRADING false || { echo 'SAFETY BLOCK: ENABLE_TRADING=false 필요'; exit 1; }
[ -f "$WATCHDOG" ] || { echo '[ERROR] android_watchdog_v2.sh not found. git pull/update first.'; exit 2; }
[ -f "$RECOVER" ] || { echo '[ERROR] recover_android_server.sh not found. git pull/update first.'; exit 3; }
chmod +x "$WATCHDOG" "$RECOVER" "$SERVER/start_android.sh" "$SERVER/android_update.sh" "$SERVER/android_stability_check.sh" 2>/dev/null || true

mkdir -p "$BOOTDIR"

cat > "$BOOT_SERVER" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
HOME=/data/data/com.termux/files/home
SERVER="$HOME/stock-trader/server"
LOG="$HOME/stock-trader-boot.log"
sleep 25
termux-wake-lock 2>/dev/null || true
cd "$SERVER" || exit 1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Stock Trader Android boot recovery" >> "$LOG"
if ! grep -Eq '^APP_MODE[[:space:]]*=[[:space:]]*paper[[:space:]]*$' .env || ! grep -Eq '^ENABLE_TRADING[[:space:]]*=[[:space:]]*false[[:space:]]*$' .env; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] SAFETY BLOCK" >> "$LOG"
  exit 1
fi
bash "$SERVER/recover_android_server.sh" >> "$LOG" 2>&1 || true
EOF
chmod +x "$BOOT_SERVER"

cat > "$BOOT_WATCHDOG" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
HOME=/data/data/com.termux/files/home
WATCHDOG="$HOME/stock-trader/server/android_watchdog_v2.sh"
PIDFILE="$HOME/stock-trader-watchdog.pid"
LOG="$HOME/stock-trader-watchdog.log"
sleep 90
[ -f "$WATCHDOG" ] || exit 1
chmod +x "$WATCHDOG" 2>/dev/null || true
if [ -f "$PIDFILE" ]; then
  PID=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
    CMD=$(tr '\0' ' ' < "/proc/$PID/cmdline" 2>/dev/null || true)
    if echo "$CMD" | grep -q 'android_watchdog_v2.sh'; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Watchdog v2 already running PID=$PID" >> "$LOG"
      exit 0
    fi
    kill -TERM "$PID" 2>/dev/null || true
    sleep 1
  fi
fi
nohup "$WATCHDOG" >/dev/null 2>&1 &
echo $! > "$PIDFILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Watchdog v2 started PID=$!" >> "$LOG"
EOF
chmod +x "$BOOT_WATCHDOG"

# Refresh watchdog now so the running process uses watchdog v2, replacing any
# legacy generated android_watchdog.sh process that may still be alive.
if [ -f "$WDPIDFILE" ]; then
  WDPID=$(cat "$WDPIDFILE" 2>/dev/null || true)
  if [ -n "${WDPID:-}" ] && kill -0 "$WDPID" 2>/dev/null; then
    kill -TERM "$WDPID" 2>/dev/null || true
    sleep 1
  fi
fi
nohup "$WATCHDOG" >/dev/null 2>&1 &
echo $! > "$WDPIDFILE"
NEW_WD=$!

# Do not restart a healthy server just to install supervision. Recover only if needed.
if "$PREFIX/bin/python" - <<'PY' >/dev/null 2>&1
import json,urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=8) as r:
        d=json.loads(r.read().decode('utf-8','ignore'))
    good=(r.status==200 and d.get('ok') and str(d.get('mode','')).lower()=='paper' and not d.get('tradingEnabled') and d.get('credentialsConfigured') and (not d.get('autoPaper') or (d.get('paperLoop') or {}).get('running')) and (not d.get('autoStartCollector') or (d.get('collector') or {}).get('running')))
    raise SystemExit(0 if good else 1)
except Exception:
    raise SystemExit(1)
PY
then
  echo '기존 Android 서버는 healthy — 재시작하지 않았습니다.'
else
  echo '서버가 healthy가 아니므로 복구를 시도합니다.'
  bash "$RECOVER"
fi

echo "Android 안정화/업데이트 모드 설치 완료"
echo "WATCHDOG v2 PID=$NEW_WD"
echo "Boot scripts: $BOOT_SERVER / $BOOT_WATCHDOG"
echo "대시보드 ↻ 업데이트는 watchdog과 충돌하지 않도록 보호됩니다."
echo "상태점검: bash $SERVER/android_stability_check.sh"
