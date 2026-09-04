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

pid_state(){
  local label="$1" file="$2" pid=""
  if [ -f "$file" ]; then pid=$(cat "$file" 2>/dev/null || true); fi
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then ok "$label PID=$pid alive"; else fail "$label not alive (pid=${pid:-none})"; fi
}

watchdog_state(){
  local pid="" cmd=""
  [ -f "$WDPIDFILE" ] && pid=$(cat "$WDPIDFILE" 2>/dev/null || true)
  if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
    fail "watchdog not alive (pid=${pid:-none})"
    return
  fi
  cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
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
echo "[2] Server health"
"$PREFIX/bin/python" - <<'PY'
import json,urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=8) as r:
        d=json.loads(r.read().decode('utf-8','ignore'))
    c=d.get('collector') or {}; p=d.get('paperLoop') or {}; h=d.get('historical') or {}; u=d.get('usCollector') or {}
    print(f"HTTP={r.status} ok={d.get('ok')} mode={d.get('mode')} tradingEnabled={d.get('tradingEnabled')} credentials={d.get('credentialsConfigured')}")
    print(f"collector.running={c.get('running')} lastSuccessAt={c.get('lastSuccessAt')} lastError={c.get('lastError')}")
    print(f"paper.running={p.get('running')} lastCycleAt={p.get('lastCycleAt')} lastError={p.get('lastError')}")
    print(f"historical.running={h.get('running')} jobs={h.get('completedJobs')}/{h.get('totalJobs')} failed={h.get('failedJobs')} lastError={h.get('lastError')}")
    print(f"usCollector.running={u.get('running')} lastError={u.get('lastError')}")
    healthy=(r.status==200 and d.get('ok') and str(d.get('mode','')).lower()=='paper' and not d.get('tradingEnabled') and d.get('credentialsConfigured') and (not d.get('autoPaper') or p.get('running')) and (not d.get('autoStartCollector') or c.get('running')))
    raise SystemExit(0 if healthy else 1)
except Exception as e:
    print('health_error='+repr(e))
    raise SystemExit(1)
PY
[ "$?" = "0" ] && ok 'deep health passed' || fail 'deep health failed'

echo
echo "[3] Process supervision"
pid_state 'server' "$SERVER_PIDFILE"
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
else
  fail 'watchdog status endpoint unavailable'
fi

echo
echo "[5] Storage / database"
df -h "$HOME" 2>/dev/null | tail -1 || true
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
