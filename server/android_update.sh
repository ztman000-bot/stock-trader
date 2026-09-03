#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$HOME/stock-trader"
SERVER="$ROOT/server"
PREFIX=/data/data/com.termux/files/usr
SERVER_PID="${1:-}"
PIDFILE="$HOME/stock-trader-server.pid"
LOG="$HOME/stock-trader-update.log"
SERVER_LOG="$HOME/stock-trader-server.log"
OLD_HEAD=""
NEWPID=""
OLD_STOPPED=0

exec >>"$LOG" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Android update start"

fail(){
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] UPDATE FAILED: $*"
  exit 1
}

env_has(){
  local key="$1" value="$2"
  grep -Eq "^${key}[[:space:]]*=[[:space:]]*${value}[[:space:]]*$" "$SERVER/.env"
}

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

start_server(){
  cd "$SERVER"
  command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true
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
      >> "$SERVER_LOG" 2>&1 &
  NEWPID=$!
  echo "$NEWPID" > "$PIDFILE"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] server start PID=$NEWPID profile=${PHONE_PERFORMANCE_PROFILE:-dedicated}"
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

emergency_exit(){
  local rc=$?
  if [ "$rc" -ne 0 ] && [ "$OLD_STOPPED" = "1" ] && ! health_ok; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] emergency restart guard triggered"
    start_server || true
    wait_health 30 || true
  fi
  exit "$rc"
}
trap emergency_exit EXIT

cd "$SERVER"
env_has APP_MODE paper || fail 'APP_MODE=paper not verified'
env_has ENABLE_TRADING false || fail 'ENABLE_TRADING=false not verified'

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
"$PREFIX/bin/python" -m py_compile app.py unified_app.py android_unified_app.py || rollback_and_restart
"$PREFIX/bin/python" preflight.py || rollback_and_restart

env_has APP_MODE paper || rollback_and_restart
env_has ENABLE_TRADING false || rollback_and_restart

if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] stopping old server PID=$SERVER_PID"
  kill "$SERVER_PID" 2>/dev/null || true
  OLD_STOPPED=1
  for _ in 1 2 3 4 5 6 7 8; do
    kill -0 "$SERVER_PID" 2>/dev/null || break
    sleep 1
  done
fi

start_server

if ! wait_health 60; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] new server health failed"
  [ -n "$NEWPID" ] && kill "$NEWPID" 2>/dev/null || true
  rollback_and_restart
fi

OLD_STOPPED=0
trap - EXIT
echo "[$(date '+%Y-%m-%d %H:%M:%S')] UPDATE OK HEAD=$(git -C "$ROOT" rev-parse HEAD) PID=$NEWPID"
exit 0
