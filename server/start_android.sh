#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

HOME=/data/data/com.termux/files/home
WATCHDOG="$PWD/android_watchdog_v2.sh"
WDPIDFILE="$HOME/stock-trader-watchdog.pid"

if [ ! -f ".env" ]; then
  echo "[ERROR] server/.env not found. Create it locally on the phone; never commit credentials."
  exit 10
fi

grep -Eq '^APP_MODE[[:space:]]*=[[:space:]]*paper[[:space:]]*$' .env || { echo '[ERROR] APP_MODE=paper not verified'; exit 11; }
grep -Eq '^ENABLE_TRADING[[:space:]]*=[[:space:]]*false[[:space:]]*$' .env || { echo '[ERROR] ENABLE_TRADING=false not verified'; exit 12; }

# Temporary dedicated-phone server profile: research/Paper only.
# Prioritize realtime collection/UI responsiveness, then run heavy research in staggered windows.
export APP_MODE="paper"
export ENABLE_TRADING="false"
export AUTO_BACKFILL="${AUTO_BACKFILL:-false}"
export MASTER_PRESELECT="${MASTER_PRESELECT:-120}"
export FOCUS_SIZE="${FOCUS_SIZE:-20}"
export NH_REST_MIN_INTERVAL="${NH_REST_MIN_INTERVAL:-0.30}"
export TEMP_PHONE_SERVER="true"
export PHONE_PERFORMANCE_PROFILE="${PHONE_PERFORMANCE_PROFILE:-dedicated}"
export PYTHONUNBUFFERED="1"
export PYTHONFAULTHANDLER="1"

if [ "$PHONE_PERFORMANCE_PROFILE" = "dedicated" ]; then
  # Do not launch CPU-heavy labs immediately after boot. Let API/collector become responsive first.
  export FAST_RESEARCH_START_DELAY_SEC="${FAST_RESEARCH_START_DELAY_SEC:-90}"
  export FAST_RESEARCH_INTERVAL_MIN="${FAST_RESEARCH_INTERVAL_MIN:-60}"
  export RESEARCH_INTERVAL_MIN="${RESEARCH_INTERVAL_MIN:-120}"
  export HISTORY_MIN_INTERVAL_MIN="${HISTORY_MIN_INTERVAL_MIN:-240}"
fi

# Keep Android from sleeping Termux while the server is running when the command exists.
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true
ulimit -n 4096 >/dev/null 2>&1 || true

# Every normal Android start also guarantees one watchdog process. The watchdog
# has its own single-instance PID guard, so recovery/reboot cannot duplicate it.
if [ "${ANDROID_SKIP_WATCHDOG:-0}" != "1" ] && [ -f "$WATCHDOG" ]; then
  chmod +x "$WATCHDOG" 2>/dev/null || true
  WDPID=""
  [ -f "$WDPIDFILE" ] && WDPID=$(cat "$WDPIDFILE" 2>/dev/null || true)
  if [ -z "$WDPID" ] || ! kill -0 "$WDPID" 2>/dev/null; then
    nohup "$WATCHDOG" >/dev/null 2>&1 &
    echo $! > "$WDPIDFILE"
  fi
fi

echo "Stock Day Trader temporary Android server"
echo "- Paper/research only"
echo "- REAL ORDER forced OFF"
echo "- Dedicated phone performance profile: $PHONE_PERFORMANCE_PROFILE"
echo "- Realtime/API first, heavy research staggered"
echo "- Android watchdog v2 + safe updater enabled"
echo "- Listen: 0.0.0.0:8000 (use Tailscale IP from another device)"

# Keep one worker only. Multiple workers would duplicate collectors/research engines and NH sessions.
exec python -m uvicorn android_unified_app:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log
