#!/data/data/com.termux/files/usr/bin/bash
set -u

HOME=/data/data/com.termux/files/home
ROOT="$HOME/stock-trader"
SERVER="$ROOT/server"
PREFIX=/data/data/com.termux/files/usr
SERVER_PIDFILE="$HOME/stock-trader-server.pid"
WDPIDFILE="$HOME/stock-trader-watchdog.pid"
HEARTBEAT="$HOME/.stock-trader-app-heartbeat"
UPDATE_FLAG="$HOME/.stock-trader-update-in-progress"
DB="$SERVER/market_data.db"
RC=0

ok(){ echo "[OK] $*"; }
warn(){ echo "[WARN] $*"; }
fail(){ echo "[FAIL] $*"; RC=1; }

pid_cmdline(){
  local pid="${1:-}"
  [ -n "$pid" ] || return 1
  tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true
}

is_server_pid(){
  local pid="${1:-}" cmd=""
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 1
  cmd=$(pid_cmdline "$pid")
  echo "$cmd" | grep -qE 'python(3)? .*[-]m uvicorn android_unified_app:app|uvicorn android_unified_app:app'
}

server_state(){
  local pid="" old="" p=""
  [ -f "$SERVER_PIDFILE" ] && old=$(cat "$SERVER_PIDFILE" 2>/dev/null || true)
  pid="$old"

  if ! is_server_pid "$pid"; then
    for p in $(pgrep -f 'python.*-m uvicorn android_unified_app:app' 2>/dev/null || true); do
      if is_server_pid "$p"; then
        pid="$p"
        break
      fi
    done
  fi

  if ! is_server_pid "$pid"; then
    [ -f "$SERVER_PIDFILE" ] && rm -f "$SERVER_PIDFILE" 2>/dev/null || true
    fail "server not alive (pid=${old:-none})"
    return
  fi

  if [ "$old" != "$pid" ]; then
    printf '%s\n' "$pid" > "$SERVER_PIDFILE"
    ok "server PID file self-repaired: ${old:-none} -> $pid"
  fi
  ok "server PID=$pid alive and identity verified"
}

watchdog_state(){
  local pid="" cmd=""
  [ -f "$WDPIDFILE" ] && pid=$(cat "$WDPIDFILE" 2>/dev/null || true)
  if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
    fail "watchdog not alive (pid=${pid:-none})"
    return
  fi
  cmd=$(pid_cmdline "$pid")
  if echo "$cmd" | grep -q 'android_watchdog_v2.sh'; then
    ok "watchdog v2 PID=$pid alive"
  elif echo "$cmd" | grep -q 'android_watchdog.sh'; then
    fail "legacy watchdog still running PID=$pid — run install_android_update_mode.sh once"
  else
    fail "watchdog PID=$pid points to unexpected process"
  fi
}

echo "=== Stock Trader Android Stability Check ==="
echo "time: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "repo: $ROOT"
if [ -d "$ROOT/.git" ]; then echo "HEAD: $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"; fi

echo
echo "[1] Safety configuration"
if [ -f "$SERVER/.env" ]; then
  grep -Eq '^APP_MODE[[:space:]]*=[[:space:]]*paper[[:space:]]*$' "$SERVER/.env" && ok 'APP_MODE=paper' || fail 'APP_MODE=paper not verified'
  grep -Eq '^ENABLE_TRADING[[:space:]]*=[[:space:]]*false[[:space:]]*$' "$SERVER/.env" && ok 'ENABLE_TRADING=false' || fail 'ENABLE_TRADING=false not verified'
else
  fail '.env missing'
fi

echo
echo "[2] Server health / loop liveness"
"$PREFIX/bin/python" - <<'PY'
import json,time,urllib.request
from datetime import datetime

def age(value):
    if not value:
        return None
    try:
        d=datetime.fromisoformat(str(value))
        if d.tzinfo is None:d=d.astimezone()
        return max(0.0,time.time()-d.timestamp())
    except Exception:return None

try:
    with urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=8) as r:
        d=json.loads(r.read().decode('utf-8','ignore'))
    c=d.get('collector') or {}; p=d.get('paperLoop') or {}; h=d.get('historical') or {}; u=d.get('usCollector') or {}
    ca=age(c.get('lastCycleAt')); pa=age(p.get('lastCycleAt'))
    print(f"HTTP={r.status} ok={d.get('ok')} mode={d.get('mode')} tradingEnabled={d.get('tradingEnabled')} credentials={d.get('credentialsConfigured')}")
    print(f"collector.running={c.get('running')} cycleAgeSec={None if ca is None else round(ca,1)} lastSuccessAt={c.get('lastSuccessAt')} lastError={c.get('lastError')}")
    print(f"paper.running={p.get('running')} cycleAgeSec={None if pa is None else round(pa,1)} lastError={p.get('lastError')}")
    print(f"historical.running={h.get('running')} jobs={h.get('completedJobs')}/{h.get('totalJobs')} failed={h.get('failedJobs')} lastError={h.get('lastError')}")
    print(f"usCollector.running={u.get('running')} lastError={u.get('lastError')}")
    healthy=(r.status==200 and d.get('ok') and str(d.get('mode','')).lower()=='paper' and not d.get('tradingEnabled') and d.get('credentialsConfigured'))
    if d.get('autoPaper'):
        healthy=healthy and bool(p.get('running')) and pa is not None and pa<=45
    if d.get('autoStartCollector'):
        healthy=healthy and bool(c.get('running')) and ca is not None and ca<=180
    raise SystemExit(0 if healthy else 1)
except Exception as e:
    print('health_error='+repr(e))
    raise SystemExit(1)
PY
[ "$?" = "0" ] && ok 'deep health and loop liveness passed' || fail 'deep health / loop liveness failed'

echo
echo "[3] Process supervision"
server_state
watchdog_state
if [ -f "$HEARTBEAT" ]; then
  AGE=$("$PREFIX/bin/python" - "$HEARTBEAT" <<'PY'
import json,sys,time
try:
 d=json.load(open(sys.argv[1],encoding='utf-8')); print(max(0,int(time.time()-float(d.get('timestamp') or 0))))
except Exception: print(999999)
PY
)
  if [ "$AGE" -le 90 ]; then ok "app heartbeat age=${AGE}s"; else fail "app heartbeat stale age=${AGE}s"; fi
else
  fail 'app heartbeat file missing'
fi
[ -f "$UPDATE_FLAG" ] && warn 'update flag is present' || ok 'no update in progress'

echo
echo "[4] Watchdog API"
WDJSON=$(curl -fsS --max-time 8 http://127.0.0.1:8000/api/system/android-watchdog 2>/dev/null || true)
if [ -n "$WDJSON" ]; then
  echo "$WDJSON"
  echo "$WDJSON" | grep -q '"watchdogV2":true' && ok 'watchdog API confirms v2' || fail 'watchdog API does not confirm v2'
  echo "$WDJSON" | grep -q '"heartbeatFresh":true' && ok 'watchdog API heartbeat fresh' || fail 'watchdog API heartbeat not fresh'
  echo "$WDJSON" | grep -q '"serverPidValid":true' && ok 'watchdog API confirms server PID identity' || fail 'watchdog API server PID identity invalid'
  echo "$WDJSON" | grep -q '"guardianActive":true' && ok 'in-app watchdog guardian active' || fail 'in-app watchdog guardian inactive'
else
  fail 'watchdog status endpoint unavailable'
fi

echo
echo "[5] Storage / database"
df -h "$HOME" 2>/dev/null | tail -1 || true
FREE_KB=$(df -Pk "$HOME" 2>/dev/null | awk 'NR==2 {print $4}' || true)
if [ -n "${FREE_KB:-}" ] && [ "$FREE_KB" -lt 524288 ]; then
  warn "free storage below 512MB (${FREE_KB}KB) — database/log growth risk"
else
  ok 'free storage is above 512MB'
fi
if [ -f "$DB" ]; then
  ls -lh "$DB"
  DBCHK=$("$PREFIX/bin/python" - "$DB" <<'PY'
import sqlite3,sys
try:
 c=sqlite3.connect(sys.argv[1],timeout=10); r=c.execute('PRAGMA quick_check(1)').fetchone()[0]; c.close(); print(r)
except Exception as e: print('ERROR:'+repr(e))
PY
)
  [ "$DBCHK" = "ok" ] && ok 'SQLite quick_check=ok' || fail "SQLite quick_check=$DBCHK"
else
  fail 'market_data.db missing'
fi

echo
echo "[6] Recent supervision logs"
echo "-- watchdog --"; tail -n 8 "$HOME/stock-trader-watchdog.log" 2>/dev/null || true
echo "-- server --"; tail -n 8 "$HOME/stock-trader-server.log" 2>/dev/null || true

echo
if [ "$RC" = "0" ]; then echo '[OK] ANDROID STABILITY CHECK PASSED'; else echo '[WARN] ANDROID STABILITY CHECK FOUND ITEMS TO REVIEW'; fi
exit "$RC"
