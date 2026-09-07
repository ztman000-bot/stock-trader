#!/data/data/com.termux/files/usr/bin/bash
set -u

HOME=/data/data/com.termux/files/home
ROOT="$HOME/stock-trader"
SERVER="$ROOT/server"
PREFIX=/data/data/com.termux/files/usr
LOG="$HOME/stock-trader-watchdog.log"
PIDFILE="$HOME/stock-trader-watchdog.pid"
LOCKDIR="$HOME/.stock-trader-watchdog-v2.lock"
LOCKPID="$LOCKDIR/pid"
SERVER_PIDFILE="$HOME/stock-trader-server.pid"
HEARTBEAT="$HOME/.stock-trader-app-heartbeat"
UPDATE_FLAG="$HOME/.stock-trader-update-in-progress"
RECOVER="$SERVER/recover_android_server.sh"
WATCHDOG_COMPONENT_VERSION="0.17.13"
INTERVAL="${WATCHDOG_INTERVAL_SEC:-30}"
HARD_LIMIT="${WATCHDOG_FAILURES_BEFORE_RESTART:-3}"
SOFT_LIMIT="${WATCHDOG_SOFT_FAILURES_BEFORE_RESTART:-6}"
SERVICE_RESTART_LIMIT="${WATCHDOG_SERVICE_FAILURES_BEFORE_RESTART:-20}"
COOLDOWN="${WATCHDOG_COOLDOWN_SEC:-180}"
HEARTBEAT_MAX_AGE="${WATCHDOG_HEARTBEAT_MAX_AGE_SEC:-90}"
LIVENESS_TIMEOUT="${WATCHDOG_LIVENESS_TIMEOUT_SEC:-3}"
SERVICE_TIMEOUT="${WATCHDOG_SERVICE_TIMEOUT_SEC:-8}"
MAX_LOG_BYTES="${WATCHDOG_MAX_LOG_BYTES:-2097152}"
FAILS=0
SERVICE_FAILS=0
LAST_SERVICE_REASON=""
LAST_RESTART=0
UPDATE_LOGGED=0

rotate_log(){
  [ -f "$LOG" ] || return 0
  local size
  size=$(wc -c < "$LOG" 2>/dev/null || echo 0)
  if [ "${size:-0}" -gt "$MAX_LOG_BYTES" ]; then
    mv -f "$LOG" "$LOG.1" 2>/dev/null || true
    : > "$LOG"
  fi
}

log(){
  rotate_log
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"
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
  echo "$cmd" | grep -q "$SERVER/android_watchdog_v2.sh"
}

server_pid(){
  local p="" saved=""
  if [ -f "$SERVER_PIDFILE" ]; then
    saved=$(cat "$SERVER_PIDFILE" 2>/dev/null || true)
    if is_server_pid "$saved"; then
      echo "$saved"
      return 0
    fi
    rm -f "$SERVER_PIDFILE" 2>/dev/null || true
  fi

  for p in $(pgrep -f 'python.*-m uvicorn android_unified_app:app' 2>/dev/null || true); do
    if is_server_pid "$p"; then
      printf '%s\n' "$p" > "$SERVER_PIDFILE"
      log "server PID file self-repaired pid=$p previous=${saved:-none}"
      echo "$p"
      return 0
    fi
  done
  return 1
}

heartbeat_fresh(){
  [ -f "$HEARTBEAT" ] || return 1
  "$PREFIX/bin/python" - "$HEARTBEAT" "$HEARTBEAT_MAX_AGE" <<'PY' >/dev/null 2>&1
import json,sys,time
from pathlib import Path
p=Path(sys.argv[1]); limit=float(sys.argv[2])
try:
    d=json.loads(p.read_text(encoding='utf-8'))
    ts=float(d.get('timestamp') or 0)
    raise SystemExit(0 if 0 <= time.time()-ts <= limit else 1)
except Exception:
    raise SystemExit(1)
PY
}

liveness_reason(){
  "$PREFIX/bin/python" - "$LIVENESS_TIMEOUT" <<'PY'
import json,socket,sys,urllib.error,urllib.request
timeout=float(sys.argv[1])
url='http://127.0.0.1:8000/api/system/liveness'
try:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        raw=r.read().decode('utf-8','ignore')
        d=json.loads(raw)
        if r.status != 200:
            print(f'LIVENESS_HTTP_{r.status}')
        elif not d.get('ok'):
            print('LIVENESS_NOT_OK')
        elif str(d.get('mode','')).lower() != 'paper' or bool(d.get('tradingEnabled')):
            print('LIVENESS_SAFETY_MISMATCH')
        else:
            print('OK')
except urllib.error.HTTPError as e:
    print(f'LIVENESS_HTTP_{e.code}')
except (socket.timeout, TimeoutError):
    print('LIVENESS_HTTP_TIMEOUT')
except Exception as e:
    print(f'LIVENESS_ERROR_{type(e).__name__.upper()}')
PY
}

service_reason(){
  "$PREFIX/bin/python" - "$SERVICE_TIMEOUT" <<'PY'
import json,socket,sys,time,urllib.error,urllib.request
from datetime import datetime
timeout=float(sys.argv[1])
BASE='http://127.0.0.1:8000'

def get_json(path):
    try:
        with urllib.request.urlopen(BASE+path, timeout=timeout) as r:
            raw=r.read().decode('utf-8','ignore')
            return r.status, json.loads(raw), None
    except urllib.error.HTTPError as e:
        raw=e.read().decode('utf-8','ignore')
        try: data=json.loads(raw)
        except Exception: data={}
        return e.code, data, f'HTTP_{e.code}'
    except (socket.timeout, TimeoutError):
        return None, {}, 'TIMEOUT'
    except Exception as e:
        return None, {}, f'ERROR_{type(e).__name__.upper()}'

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

status,d,err=get_json('/api/health')
if err == 'TIMEOUT':
    print('HEALTH_HTTP_TIMEOUT'); raise SystemExit(0)
if err:
    print('HEALTH_'+err); raise SystemExit(0)
if status != 200 or not d.get('ok'):
    print('HEALTH_NOT_OK'); raise SystemExit(0)
if str(d.get('mode','')).lower() != 'paper' or bool(d.get('tradingEnabled')):
    print('SAFETY_MODE_MISMATCH'); raise SystemExit(0)
if not d.get('credentialsConfigured'):
    print('CREDENTIALS_MISSING'); raise SystemExit(0)

paper=d.get('paperLoop') or {}
if d.get('autoPaper'):
    if not paper.get('running'):
        print('PAPER_STOPPED'); raise SystemExit(0)
    paper_age=age_seconds(paper.get('lastCycleAt'))
    started_age=age_seconds(paper.get('startedAt'))
    if paper_age is None:
        if started_age is None or started_age > 45:
            print('PAPER_STALE'); raise SystemExit(0)
    elif paper_age > 45:
        print('PAPER_STALE'); raise SystemExit(0)

collector=d.get('collector') or {}
collector_started_age=age_seconds(collector.get('startedAt'))
if d.get('autoStartCollector'):
    if not collector.get('running'):
        print('COLLECTOR_STOPPED'); raise SystemExit(0)
    cycle_age=age_seconds(collector.get('lastCycleAt'))
    if cycle_age is None:
        if collector_started_age is None or collector_started_age > 180:
            print('COLLECTOR_STALE'); raise SystemExit(0)
    elif cycle_age > 180:
        print('COLLECTOR_STALE'); raise SystemExit(0)

# Runtime quote freshness is a data-quality signal, not an API-liveness signal.
# Query it only during the live session, after the collector startup grace, and
# never restart solely for a runtime freshness failure.
startup_grace=collector_started_age is not None and collector_started_age <= 180
if bool(collector.get('marketSession')) and not startup_grace:
    rstatus,runtime,rerr=get_json('/api/system/runtime-health')
    if rerr == 'TIMEOUT':
        print('RUNTIME_HTTP_TIMEOUT'); raise SystemExit(0)
    if rerr:
        print('RUNTIME_'+rerr); raise SystemExit(0)
    if rstatus != 200 or not runtime.get('ok'):
        print('RUNTIME_NOT_OK'); raise SystemExit(0)
    if not runtime.get('quotesFresh'):
        print('RUNTIME_STALE'); raise SystemExit(0)

print('OK')
PY
}

safe_env(){
  [ -f "$SERVER/.env" ] && \
  grep -Eq '^APP_MODE[[:space:]]*=[[:space:]]*paper[[:space:]]*$' "$SERVER/.env" && \
  grep -Eq '^ENABLE_TRADING[[:space:]]*=[[:space:]]*false[[:space:]]*$' "$SERVER/.env"
}

update_active(){
  [ -f "$UPDATE_FLAG" ] || return 1
  local upid="" started="" cmd=""
  read -r upid started < "$UPDATE_FLAG" 2>/dev/null || true
  if pid_alive "$upid"; then
    cmd=$(pid_cmdline "$upid")
    if echo "$cmd" | grep -q 'android_update.sh'; then
      return 0
    fi
  fi
  log "stale update flag removed pid=${upid:-none} started=${started:-unknown}"
  rm -f "$UPDATE_FLAG" 2>/dev/null || true
  return 1
}

cleanup(){
  local current="" owner=""
  [ -f "$PIDFILE" ] && current=$(cat "$PIDFILE" 2>/dev/null || true)
  [ "$current" = "$$" ] && rm -f "$PIDFILE" 2>/dev/null || true
  [ -f "$LOCKPID" ] && owner=$(cat "$LOCKPID" 2>/dev/null || true)
  [ "$owner" = "$$" ] && rm -rf "$LOCKDIR" 2>/dev/null || true
}

acquire_singleton(){
  local owner=""
  for _ in 1 2 3; do
    if mkdir "$LOCKDIR" 2>/dev/null; then
      echo $$ > "$LOCKPID"
      return 0
    fi
    [ -f "$LOCKPID" ] && owner=$(cat "$LOCKPID" 2>/dev/null || true)
    if [ -n "$owner" ] && [ "$owner" != "$$" ] && is_watchdog_pid "$owner"; then
      printf '%s\n' "$owner" > "$PIDFILE"
      return 1
    fi
    rm -rf "$LOCKDIR" 2>/dev/null || true
    sleep 0.2
  done
  return 1
}

# Atomic singleton ownership prevents concurrent launchers from creating
# multiple active watchdog loops.
acquire_singleton || exit 0
trap cleanup EXIT INT TERM

# v0.17.12 could leave untracked duplicate watchdog processes behind. Once this
# process owns the atomic lock, terminate only positively identified project
# watchdog duplicates and keep this process as the canonical instance.
for p in $(pgrep -f 'android_watchdog_v2.sh' 2>/dev/null || true); do
  if [ "$p" != "$$" ] && is_watchdog_pid "$p"; then
    log "duplicate watchdog terminated pid=$p canonical=$$"
    kill -TERM "$p" 2>/dev/null || true
  fi
done
sleep 0.5
echo $$ > "$PIDFILE"

command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock >/dev/null 2>&1 || true
initial_spid=$(server_pid 2>/dev/null || true)
log "watchdog-v2 v${WATCHDOG_COMPONENT_VERSION} started pid=$$ interval=${INTERVAL}s hard=${HARD_LIMIT} soft=${SOFT_LIMIT} service=${SERVICE_RESTART_LIMIT} livenessTimeout=${LIVENESS_TIMEOUT}s heartbeat=${HEARTBEAT_MAX_AGE}s serverPid=${initial_spid:-none} singletonLock=true"

while true; do
  sleep "$INTERVAL"

  if update_active; then
    FAILS=0
    SERVICE_FAILS=0
    LAST_SERVICE_REASON=""
    if [ "$UPDATE_LOGGED" = "0" ]; then
      log 'update in progress; watchdog restart actions paused'
      UPDATE_LOGGED=1
    fi
    continue
  fi
  if [ "$UPDATE_LOGGED" = "1" ]; then
    log 'update flag cleared; watchdog resumed'
    UPDATE_LOGGED=0
  fi

  if ! safe_env; then
    FAILS=0
    SERVICE_FAILS=0
    LAST_SERVICE_REASON=""
    log 'SAFETY BLOCK: APP_MODE=paper / ENABLE_TRADING=false not verified; restart disabled'
    sleep 60
    continue
  fi

  spid=$(server_pid 2>/dev/null || true)
  alive=0; fresh=0
  is_server_pid "$spid" && alive=1
  heartbeat_fresh && fresh=1

  lreason=$(liveness_reason)
  if [ "$lreason" != "OK" ]; then
    FAILS=$((FAILS + 1))
    SERVICE_FAILS=0
    LAST_SERVICE_REASON=""
    limit="$HARD_LIMIT"
    if [ "$alive" = "1" ] && [ "$fresh" = "1" ]; then
      limit="$SOFT_LIMIT"
    fi
    log "liveness failure reason=$lreason ${FAILS}/${limit} serverPid=${spid:-none} pidAlive=$alive heartbeatFresh=$fresh"

    [ "$FAILS" -lt "$limit" ] && continue
    now=$(date +%s)
    if [ $((now - LAST_RESTART)) -lt "$COOLDOWN" ]; then
      log "restart suppressed by cooldown (${COOLDOWN}s) reason=$lreason"
      continue
    fi

    FAILS=0
    LAST_RESTART="$now"
    if [ ! -x "$RECOVER" ]; then
      log "recovery script missing/not executable: $RECOVER"
      continue
    fi

    log "recovery triggered reason=$lreason"
    if ANDROID_SKIP_WATCHDOG=1 bash "$RECOVER" --watchdog >> "$LOG" 2>&1; then
      log 'recovery completed healthy'
    else
      rc=$?
      log "recovery failed rc=$rc reason=$lreason"
    fi
    sleep 60
    continue
  fi

  FAILS=0
  sreason=$(service_reason)
  if [ "$sreason" = "OK" ]; then
    if [ "$SERVICE_FAILS" -gt 0 ]; then
      log "service recovered previousReason=${LAST_SERVICE_REASON:-unknown} failures=$SERVICE_FAILS"
    fi
    SERVICE_FAILS=0
    LAST_SERVICE_REASON=""
    continue
  fi

  if [ "$sreason" = "$LAST_SERVICE_REASON" ]; then
    SERVICE_FAILS=$((SERVICE_FAILS + 1))
  else
    SERVICE_FAILS=1
    LAST_SERVICE_REASON="$sreason"
  fi

  # Expensive /api/health or runtime freshness timeouts are diagnostics, not
  # proof that the API process is dead. Never restart solely for these reasons.
  case "$sreason" in
    HEALTH_HTTP_TIMEOUT|HEALTH_ERROR_*|HEALTH_HTTP_*|RUNTIME_*)
      if [ "$SERVICE_FAILS" = "1" ] || [ $((SERVICE_FAILS % 5)) -eq 0 ]; then
        log "service degraded reason=$sreason failures=$SERVICE_FAILS liveness=OK restartSuppressed=true"
      fi
      continue
      ;;
    CREDENTIALS_MISSING|SAFETY_MODE_MISMATCH)
      if [ "$SERVICE_FAILS" = "1" ] || [ $((SERVICE_FAILS % 5)) -eq 0 ]; then
        log "service degraded reason=$sreason failures=$SERVICE_FAILS liveness=OK restartSuppressed=true"
      fi
      continue
      ;;
  esac

  # Core Paper/collector loop failures may self-heal. Give them a long grace
  # period (~10 minutes by default) before one safe recovery attempt.
  if [ "$SERVICE_FAILS" = "1" ] || [ $((SERVICE_FAILS % 5)) -eq 0 ]; then
    log "service degraded reason=$sreason failures=$SERVICE_FAILS/${SERVICE_RESTART_LIMIT} liveness=OK restartSuppressed=$([ "$SERVICE_FAILS" -lt "$SERVICE_RESTART_LIMIT" ] && echo true || echo false)"
  fi
  [ "$SERVICE_FAILS" -lt "$SERVICE_RESTART_LIMIT" ] && continue

  now=$(date +%s)
  if [ $((now - LAST_RESTART)) -lt "$COOLDOWN" ]; then
    log "service recovery suppressed by cooldown (${COOLDOWN}s) reason=$sreason"
    continue
  fi
  LAST_RESTART="$now"
  SERVICE_FAILS=0
  LAST_SERVICE_REASON=""
  if [ -x "$RECOVER" ]; then
    log "recovery triggered reason=$sreason persistentServiceFailure=true"
    if ANDROID_SKIP_WATCHDOG=1 bash "$RECOVER" --watchdog >> "$LOG" 2>&1; then
      log 'recovery completed healthy'
    else
      rc=$?
      log "recovery failed rc=$rc reason=$sreason"
    fi
  else
    log "recovery script missing/not executable: $RECOVER"
  fi
  sleep 60
done
