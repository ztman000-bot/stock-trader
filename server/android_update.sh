#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$HOME/stock-trader"
SERVER="$ROOT/server"
PREFIX=/data/data/com.termux/files/usr
SERVER_PID="${1:-}"
PIDFILE="$HOME/stock-trader-server.pid"
LOG="$HOME/stock-trader-update.log"
OLD_HEAD=""
NEWPID=""

exec >>"$LOG" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Android update start"

fail(){
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] UPDATE FAILED: $*"
  exit 1
}

start_server(){
  cd "$SERVER"
  nohup env \
    APP_MODE=paper \
    ENABLE_TRADING=false \
    AUTO_BACKFILL=false \
    MASTER_PRESELECT="${MASTER_PRESELECT:-120}" \
    FOCUS_SIZE="${FOCUS_SIZE:-20}" \
    NH_REST_MIN_INTERVAL="${NH_REST_MIN_INTERVAL:-0.30}" \
    TEMP_PHONE_SERVER=true \
    "$PREFIX/bin/python" -m uvicorn android_unified_app:app \
      --host 0.0.0.0 --port 8000 \
      >> "$HOME/stock-trader-server.log" 2>&1 &
  NEWPID=$!
  echo "$NEWPID" > "$PIDFILE"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] server start PID=$NEWPID"
}

rollback_and_restart(){
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] rolling back to $OLD_HEAD"
  cd "$ROOT"
  [ -n "$OLD_HEAD" ] && git reset --hard "$OLD_HEAD" || true
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] old server still alive; rollback complete without duplicate restart"
  else
    start_server
  fi
  exit 1
}

cd "$SERVER"
grep -q '^APP_MODE=paper$' .env || fail 'APP_MODE=paper not verified'
grep -q '^ENABLE_TRADING=false$' .env || fail 'ENABLE_TRADING=false not verified'

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

grep -q '^APP_MODE=paper$' .env || rollback_and_restart
grep -q '^ENABLE_TRADING=false$' .env || rollback_and_restart

if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] stopping old server PID=$SERVER_PID"
  kill "$SERVER_PID" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8; do
    kill -0 "$SERVER_PID" 2>/dev/null || break
    sleep 1
  done
fi

start_server

READY=0
for _ in $(seq 1 120); do
  if curl -fsS --max-time 3 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 2
done

if [ "$READY" != "1" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] new server health timeout"
  [ -n "$NEWPID" ] && kill "$NEWPID" 2>/dev/null || true
  rollback_and_restart
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] UPDATE OK HEAD=$(git -C "$ROOT" rev-parse HEAD) PID=$NEWPID"
exit 0
