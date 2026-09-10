#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

HOME=/data/data/com.termux/files/home
WATCHDOG="$PWD/android_watchdog_v2.sh"
WDPIDFILE="$HOME/stock-trader-watchdog.pid"
REMOTE_HEALTH_ENSURE="$PWD/ensure_remote_health.sh"
REMOTE_HEALTH_GUARDIAN="$PWD/remote_health_guardian.sh"
REMOTE_HEALTH_GUARDIAN_PIDFILE="$HOME/stock-trader-remote-health-guardian.pid"
REMOTE_HEALTH_GUARDIAN_VERSION="0.17.13"
OFFSITE_BACKUP_ENSURE="$PWD/ensure_offsite_backup.sh"
SHADOW_CONTINUATION_ENSURE="$PWD/ensure_shadow_continuation.sh"
SKIP_WATCHDOG="${ANDROID_SKIP_WATCHDOG:-0}"

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
export SHADOW_CONTINUATION_ENABLED="${SHADOW_CONTINUATION_ENABLED:-true}"

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

# Independent remote health process. It stays alive when uvicorn alone dies, so
# remote monitoring can distinguish SERVER_DOWN from a fully offline phone.
if [ -f "$REMOTE_HEALTH_ENSURE" ]; then
  bash "$REMOTE_HEALTH_ENSURE" || echo '[WARN] Remote health beacon unavailable; Stock Trader will continue.'
fi

remote_guardian_pid_valid(){
  local pid="${1:-}" cmd=""
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 1
  cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
  echo "$cmd" | grep -q 'remote_health_guardian.sh' &&
    echo "$cmd" | grep -q -- "--instance-version $REMOTE_HEALTH_GUARDIAN_VERSION"
}

remote_guardian_pid_project(){
  local pid="${1:-}" cmd=""
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 1
  cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
  echo "$cmd" | grep -q 'remote_health_guardian.sh'
}

# The guardian is independent of FastAPI. v0.17.13 also lets it restore the
# watchdog if uvicorn and its in-process watchdog guardian are both unavailable.
if [ -f "$REMOTE_HEALTH_GUARDIAN" ]; then
  chmod +x "$REMOTE_HEALTH_GUARDIAN" 2>/dev/null || true
  RGPID=""
  [ -f "$REMOTE_HEALTH_GUARDIAN_PIDFILE" ] && RGPID=$(cat "$REMOTE_HEALTH_GUARDIAN_PIDFILE" 2>/dev/null || true)
  if ! remote_guardian_pid_valid "$RGPID"; then
    if remote_guardian_pid_project "$RGPID"; then
      kill -TERM "$RGPID" 2>/dev/null || true
      for _ in $(seq 1 10); do
        kill -0 "$RGPID" 2>/dev/null || break
        sleep 0.2
      done
      remote_guardian_pid_project "$RGPID" && kill -KILL "$RGPID" 2>/dev/null || true
    fi
    rm -f "$REMOTE_HEALTH_GUARDIAN_PIDFILE" 2>/dev/null || true
    nohup "$REMOTE_HEALTH_GUARDIAN" --instance-version "$REMOTE_HEALTH_GUARDIAN_VERSION" >/dev/null 2>&1 &
    echo $! > "$REMOTE_HEALTH_GUARDIAN_PIDFILE"
  fi
fi

# Encrypted off-device DB backup is deliberately opt-in. The ensure script reads
# only OFFSITE_BACKUP_ENABLED from the local .env; if false, no upload process is
# kept alive and local WAL-safe backups continue as before.
if [ -f "$OFFSITE_BACKUP_ENSURE" ]; then
  bash "$OFFSITE_BACKUP_ENSURE" || echo '[WARN] Optional offsite backup unavailable; local DB backup remains active.'
fi

# Research-only continuation ledger. It polls only the localhost scanner API and
# local quote/SQLite state. It deliberately keeps collecting counterfactual
# signals after Paper daily locks but never calls an order endpoint or changes
# Control v0.8.0.
if [ -f "$SHADOW_CONTINUATION_ENSURE" ]; then
  chmod +x "$SHADOW_CONTINUATION_ENSURE" 2>/dev/null || true
  bash "$SHADOW_CONTINUATION_ENSURE" || echo '[WARN] Shadow continuation research unavailable; normal Paper/Control remains unchanged.'
fi

watchdog_pid_valid(){
  local pid="${1:-}" cmd=""
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 1
  cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
  echo "$cmd" | grep -q 'android_watchdog_v2.sh'
}

# Every normal Android start guarantees a watchdog launcher. The watchdog child
# owns singleton locking and canonical PID registration; launchers do not write
# the watchdog PID file and therefore cannot race each other.
if [ "$SKIP_WATCHDOG" != "1" ] && [ -f "$WATCHDOG" ]; then
  chmod +x "$WATCHDOG" 2>/dev/null || true
  WDPID=""
  [ -f "$WDPIDFILE" ] && WDPID=$(cat "$WDPIDFILE" 2>/dev/null || true)
  if ! watchdog_pid_valid "$WDPID"; then
    rm -f "$WDPIDFILE" 2>/dev/null || true
    nohup "$WATCHDOG" >/dev/null 2>&1 &
  fi
fi

# ANDROID_SKIP_WATCHDOG is a launch-coordination flag only. Do not leak it into
# uvicorn; otherwise the in-app guardian would stay disabled after update/recovery.
unset ANDROID_SKIP_WATCHDOG

echo "Stock Day Trader temporary Android server"
echo "- Paper/research only"
echo "- REAL ORDER forced OFF"
echo "- Dedicated phone performance profile: $PHONE_PERFORMANCE_PROFILE"
echo "- Realtime/API first, heavy research staggered"
echo "- Android watchdog v0.17.13 stability guard + safe updater enabled"
echo "- Remote health beacon v0.17.12 + independent guardian v0.17.13 enabled"
echo "- Same-stock reentry/after-lock Shadow continuation research enabled"
echo "- API guard: localhost or Tailscale 100.64.0.0/10 only"
echo "- Encrypted offsite DB backup: opt-in only (default OFF)"
echo "- Listen: 0.0.0.0:8000 (use Tailscale IP from another device)"

# Keep one worker only. Multiple workers would duplicate collectors/research engines and NH sessions.
exec python -m uvicorn android_unified_app:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log
