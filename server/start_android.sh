#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f ".env" ]; then
  echo "[ERROR] server/.env not found. Create it locally on the phone; never commit credentials."
  exit 10
fi

# Temporary dedicated-phone server profile: research/Paper only.
# Prioritize realtime collection/UI responsiveness, then run heavy research in staggered windows.
export APP_MODE="${APP_MODE:-paper}"
export ENABLE_TRADING="false"
export AUTO_BACKFILL="${AUTO_BACKFILL:-false}"
export MASTER_PRESELECT="${MASTER_PRESELECT:-120}"
export FOCUS_SIZE="${FOCUS_SIZE:-20}"
export NH_REST_MIN_INTERVAL="${NH_REST_MIN_INTERVAL:-0.30}"
export TEMP_PHONE_SERVER="true"
export PHONE_PERFORMANCE_PROFILE="${PHONE_PERFORMANCE_PROFILE:-dedicated}"
export PYTHONUNBUFFERED="1"

if [ "$PHONE_PERFORMANCE_PROFILE" = "dedicated" ]; then
  # Do not launch CPU-heavy labs immediately after boot. Let API/collector become responsive first.
  export FAST_RESEARCH_START_DELAY_SEC="${FAST_RESEARCH_START_DELAY_SEC:-90}"
  export FAST_RESEARCH_INTERVAL_MIN="${FAST_RESEARCH_INTERVAL_MIN:-60}"
  export RESEARCH_INTERVAL_MIN="${RESEARCH_INTERVAL_MIN:-120}"
  export HISTORY_MIN_INTERVAL_MIN="${HISTORY_MIN_INTERVAL_MIN:-240}"
fi

# Keep Android from sleeping Termux while the server is running when the command exists.
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true

echo "Stock Day Trader temporary Android server"
echo "- Paper/research only"
echo "- REAL ORDER forced OFF"
echo "- Dedicated phone performance profile: $PHONE_PERFORMANCE_PROFILE"
echo "- Realtime/API first, heavy research staggered"
echo "- Android-safe in-app updater enabled"
echo "- Listen: 0.0.0.0:8000 (use Tailscale IP from another device)"

# Keep one worker only. Multiple workers would duplicate collectors/research engines and NH sessions.
exec python -m uvicorn android_unified_app:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log
