#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f ".env" ]; then
  echo "[ERROR] server/.env not found. Create it locally on the phone; never commit credentials."
  exit 10
fi

# Temporary phone-server profile: research/Paper only, lower background load.
export APP_MODE="${APP_MODE:-paper}"
export ENABLE_TRADING="false"
export AUTO_BACKFILL="${AUTO_BACKFILL:-false}"
export MASTER_PRESELECT="${MASTER_PRESELECT:-120}"
export FOCUS_SIZE="${FOCUS_SIZE:-20}"
export NH_REST_MIN_INTERVAL="${NH_REST_MIN_INTERVAL:-0.30}"
export TEMP_PHONE_SERVER="true"

# Keep Android from sleeping Termux while the server is running when the command exists.
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true

echo "Stock Day Trader temporary Android server"
echo "- Paper/research only"
echo "- REAL ORDER forced OFF"
echo "- Listen: 0.0.0.0:8000 (use Tailscale IP from another device)"

exec python -m uvicorn unified_app:app --host 0.0.0.0 --port 8000
