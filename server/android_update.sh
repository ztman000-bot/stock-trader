#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$HOME/stock-trader"
SERVER="$ROOT/server"
PREFIX=/data/data/com.termux/files/usr
SERVER_PID="${1:-}"
PIDFILE="$HOME/stock-trader-server.pid"
WDPIDFILE="$HOME/stock-trader-watchdog.pid"
UPDATE_FLAG="$HOME/.stock-trader-update-in-progress"
LOG="$HOME/stock-trader-update.log"
SERVER_LOG="$HOME/stock-trader-server.log"
WATCHDOG="$SERVER/android_watchdog_v2.sh"
OLD_HEAD=""
NEWPID=""
OLD_STOPPED=0
MAX_UPDATE_LOG_BYTES="${UPDATE_MAX_LOG_BYTES:-4194304}"
MAX_SERVER_LOG_BYTES="${SERVER_MAX_LOG_BYTES:-8388608}"

rotate_one(){
  local file="$1" limit="$2" size=0
  [ -f "$file" ] || return 0
  size=$(wc -c < "$file" 2>/dev/null || echo 0)
  if [ "${size:-0}" -gt "$limit" ]; then
    mv -f "$file" "$file.1" 2>/dev/null || true
    : > "$file"
  fi
}
rotate_one "$LOG" "$MAX_UPDATE_LOG_BYTES"
rotate_one "$SERVER_LOG" "$MAX_SERVER_LOG_BYTES"
exec >>"$LOG" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Android update start pid=$$"

fail(){
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] UPDATE FAILED: $*"
  exit 1
}

env_has(){
  local key="$1" value="$2"
  grep -Eq "^${key}[[:space:]]*=[[:space:]]*${value}[[:space:]]*$" "$SERVER/.env"
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

is_watchdog_pid(){
  local p="${1:-}" cmd=""
  pid_alive "$p" || return 1
  cmd=$(pid_cmdline "$p")
  echo "$cmd" | grep -q 'android_watchdog_v2.sh'
}

is_update_pid(){
  local p="${1:-}" cmd=""
  pid_alive "$p" || return 1
  cmd=$(pid_cmdline "$p")
  echo "$cmd" | grep -q 'android_update.sh'
}

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
        age=age_seconds(paper.get('lastCycleAt')); started=age_seconds(paper.get('startedAt'))
        if age is None:
            if started is None or started > 45: raise SystemExit(6)
        elif age > 45: raise SystemExit(6)
    collector=d.get('collector') or {}
    if d.get('autoStartCollector'):
        if not collector.get('running'): raise SystemExit(5)
        age=age_seconds(collector.get('lastCycleAt')); started=age_seconds(collector.get('startedAt'))
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

start_server(){
  cd "$SERVER"
  command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true
  nohup env ANDROID_SKIP_WATCHDOG=1 bash "$SERVER/start_android.sh" >> "$SERVER_LOG" 2>&1 &
  NEWPID=$!
  echo "$NEWPID" > "$PIDFILE"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] server start PID=$NEWPID"
}

wait_health(){
  local tries="${1:-60}"
  for _ in $(seq 1 "$tries"); do
    if health_ok; then return 0; fi
    if [ -n "$NEWPID" ] && ! kill -0 "$NEWPID" 2>/dev/null; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] server process exited before health became ready"
      return 1
    fi
    sleep 2
  done
  return 1
}

stop_android_server(){
  local pids="" matched="" saved="" any=0
  if is_server_pid "$SERVER_PID"; then pids="$SERVER_PID"; fi
  if [ -f "$PIDFILE" ]; then
    saved=$(cat "$PIDFILE" 2>/dev/null || true)
    if is_server_pid "$saved"; then
      pids="$pids $saved"
    else
      rm -f "$PIDFILE" 2>/dev/null || true
    fi
  fi
  matched=$(pgrep -f 'python.*-m uvicorn android_unified_app:app' 2>/dev/null || true)
  for p in $matched; do is_server_pid "$p" && pids="$pids $p" || true; done
  pids=$(echo "$pids" | tr ' ' '\n' | awk 'NF && !seen[$1]++ {print $1}')
  [ -z "$pids" ] && return 0
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] stopping validated server PIDs: $(echo "$pids" | tr '\n' ' ')"
  for p in $pids; do is_server_pid "$p" && kill -TERM "$p" 2>/dev/null || true; done
  OLD_STOPPED=1
  for _ in $(seq 1 12); do
    any=0
    for p in $pids; do is_server_pid "$p" && any=1 || true; done
    [ "$any" = "0" ] && return 0
    sleep 1
  done
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] graceful stop timeout; forcing remaining validated PIDs"
  for p in $pids; do is_server_pid "$p" && kill -KILL "$p" 2>/dev/null || true; done
  sleep 2
}

restart_watchdog(){
  [ -f "$WATCHDOG" ] || return 0
  chmod +x "$WATCHDOG" 2>/dev/null || true
  local old=""
  [ -f "$WDPIDFILE" ] && old=$(cat "$WDPIDFILE" 2>/dev/null || true)
  if is_watchdog_pid "$old"; then
    kill -TERM "$old" 2>/dev/null || true
    sleep 1
  fi
  rm -f "$WDPIDFILE" 2>/dev/null || true
  nohup "$WATCHDOG" >/dev/null 2>&1 &
  echo $! > "$WDPIDFILE"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] watchdog v2 refreshed PID=$!"
}

rollback_and_restart(){
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] rolling back to $OLD_HEAD"
  cd "$ROOT"
  [ -n "$OLD_HEAD" ] && git reset --hard "$OLD_HEAD" || true
  if health_ok; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] server already healthy after rollback"
    OLD_STOPPED=0
    exit 1
  fi
  start_server
  if wait_health 60; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] rollback server healthy"
    OLD_STOPPED=0
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] rollback restart FAILED"
    tail -n 60 "$SERVER_LOG" 2>/dev/null || true
  fi
  exit 1
}

cleanup_exit(){
  local rc=$?
  trap - EXIT
  if [ "$rc" -ne 0 ] && [ "$OLD_STOPPED" = "1" ] && ! health_ok; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] emergency restart guard triggered"
    start_server || true
    wait_health 30 || true
  fi
  rm -f "$UPDATE_FLAG"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] update flag cleared rc=$rc"
  exit "$rc"
}

cd "$SERVER"
env_has APP_MODE paper || fail 'APP_MODE=paper not verified'
env_has ENABLE_TRADING false || fail 'ENABLE_TRADING=false not verified'

if [ -f "$UPDATE_FLAG" ]; then
  EXISTING=$(awk '{print $1}' "$UPDATE_FLAG" 2>/dev/null || true)
  if is_update_pid "$EXISTING"; then
    fail "another Android update is active pid=$EXISTING"
  fi
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] removing stale/unrelated update flag pid=${EXISTING:-none}"
  rm -f "$UPDATE_FLAG"
fi
echo "$$ $(date +%s)" > "$UPDATE_FLAG"
trap cleanup_exit EXIT INT TERM

cd "$ROOT"
DIRTY="$(git status --porcelain --untracked-files=no)"
[ -z "$DIRTY" ] || fail "tracked local changes found: $DIRTY"

OLD_HEAD="$(git rev-parse HEAD)"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] old HEAD=$OLD_HEAD"

git fetch --prune origin main || fail 'git fetch failed'
TARGET="$(git rev-parse origin/main)"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] target HEAD=$TARGET"

if [ "$OLD_HEAD" != "$TARGET" ]; then
  git merge --ff-only "$TARGET" || fail 'fast-forward merge failed'
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] already up to date"
fi

if ! git diff --quiet "$OLD_HEAD" HEAD -- server/requirements-android.txt; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Android requirements changed; installing"
  cd "$SERVER"
  "$PREFIX/bin/python" -m pip install -r requirements-android.txt || rollback_and_restart
fi

cd "$SERVER"
chmod +x start_android.sh recover_android_server.sh android_watchdog_v2.sh android_stability_check.sh 2>/dev/null || true
"$PREFIX/bin/python" -m py_compile app.py unified_app.py android_unified_app.py || rollback_and_restart
"$PREFIX/bin/python" preflight.py || rollback_and_restart
env_has APP_MODE paper || rollback_and_restart
env_has ENABLE_TRADING false || rollback_and_restart

stop_android_server
start_server

if ! wait_health 60; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] new server health failed"
  [ -n "$NEWPID" ] && is_server_pid "$NEWPID" && kill "$NEWPID" 2>/dev/null || true
  rollback_and_restart
fi

OLD_STOPPED=0
restart_watchdog
HEAD_NOW=$(git -C "$ROOT" rev-parse HEAD)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] UPDATE OK HEAD=$HEAD_NOW PID=$NEWPID"
exit 0
