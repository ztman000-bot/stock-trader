#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

HOME=/data/data/com.termux/files/home
PIDFILE="$HOME/stock-trader-krx-official.pid"
LOGFILE="$HOME/stock-trader-krx-official.log"
INSTANCE_VERSION="0.17.15-krx-official-1"

pid_valid(){
  local pid="${1:-}" cmd=""
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 1
  cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
  echo "$cmd" | grep -q 'krx_official_collector.py' &&
    echo "$cmd" | grep -q -- "--instance-version $INSTANCE_VERSION"
}

pid_project(){
  local pid="${1:-}" cmd=""
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 1
  cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
  echo "$cmd" | grep -q 'krx_official_collector.py'
}

pid=""
[ -f "$PIDFILE" ] && pid=$(cat "$PIDFILE" 2>/dev/null || true)
if pid_valid "$pid"; then
  echo "[OK] Official KRX research collector already running (pid=$pid)."
  exit 0
fi

if pid_project "$pid"; then
  kill -TERM "$pid" 2>/dev/null || true
  for _ in $(seq 1 10); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.2
  done
  pid_project "$pid" && kill -KILL "$pid" 2>/dev/null || true
fi

rm -f "$PIDFILE" 2>/dev/null || true
nohup python "$PWD/krx_official_collector.py" daemon --instance-version "$INSTANCE_VERSION" >>"$LOGFILE" 2>&1 &
pid=$!
echo "$pid" > "$PIDFILE"
sleep 0.5
if pid_valid "$pid"; then
  echo "[OK] Official KRX research collector started (pid=$pid, version=$INSTANCE_VERSION)."
  exit 0
fi

echo '[WARN] Official KRX research collector failed to stay running.'
exit 1
