import json
import os
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi.responses import JSONResponse
from starlette.routing import Route

import unified_app as base
from collector import KST
from db_backup import snapshot as db_snapshot, status as db_backup_status

app = base.app
BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
ANDROID_UPDATE_SCRIPT = BASE_DIR / 'android_update.sh'
ANDROID_WATCHDOG = BASE_DIR / 'android_watchdog_v2.sh'
HEARTBEAT = Path.home() / '.stock-trader-app-heartbeat'
WATCHDOG_PIDFILE = Path.home() / 'stock-trader-watchdog.pid'
SERVER_PIDFILE = Path.home() / 'stock-trader-server.pid'
UPDATE_FLAG = Path.home() / '.stock-trader-update-in-progress'
_HEARTBEAT_STARTED = False
_HEARTBEAT_LOCK = threading.Lock()
_WATCHDOG_GUARDIAN_STARTED = False
_WATCHDOG_GUARDIAN_LOCK = threading.Lock()
_DB_BACKUP_STARTED = False
_DB_BACKUP_LOCK = threading.Lock()
_DB_BACKUP_STATE = {'lastAttemptAt': None, 'lastResult': None, 'lastError': None}
DB_BACKUP_AFTER_MINUTE = max(15 * 60 + 35, min(int(os.getenv('DB_BACKUP_AFTER_MINUTE', str(15 * 60 + 40))), 23 * 60 + 59))

# unified_app keeps the Windows updater for laptop use. On Android we replace
# only the update POST routes; every trading/research route remains unchanged.
_UPDATE_PATHS = {'/api/system/update', '/api/system/update/run'}
app.router.routes[:] = [
    route for route in app.router.routes
    if getattr(route, 'path', None) not in _UPDATE_PATHS
]


@app.middleware('http')
async def android_mutation_guard(request, call_next):
    """Fail closed for state-changing Android APIs outside localhost/Tailscale."""
    if request.method.upper() in {'POST', 'PUT', 'PATCH', 'DELETE'} and not base._remote_allowed(request):
        return JSONResponse({'ok': False, 'error': 'Android mutation API: Tailscale/localhost only'}, 403)
    return await call_next(request)


def _android_safety_ok():
    return (
        os.getenv('APP_MODE', 'paper').lower() == 'paper'
        and os.getenv('ENABLE_TRADING', 'false').lower() == 'false'
    )


def _pid_alive(pid):
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _read_pid(path):
    try:
        return int(path.read_text(encoding='utf-8').strip())
    except Exception:
        return None


def _pid_cmdline(pid):
    try:
        raw = Path(f'/proc/{int(pid)}/cmdline').read_bytes()
        return raw.replace(b'\x00', b' ').decode('utf-8', 'ignore')
    except Exception:
        return ''


def _ensure_watchdog():
    """Ensure exactly the tracked watchdog v2 is supervised by this app.

    PID liveness alone is insufficient on Android because a stale PID can be
    reused by an unrelated process. Command-line identity is verified before a
    process is trusted or terminated.
    """
    if os.name == 'nt' or os.getenv('ANDROID_SKIP_WATCHDOG') == '1':
        return
    if not _android_safety_ok() or not ANDROID_WATCHDOG.exists():
        return

    old_pid = _read_pid(WATCHDOG_PIDFILE)
    if _pid_alive(old_pid):
        cmd = _pid_cmdline(old_pid)
        if 'android_watchdog_v2.sh' in cmd:
            return
        # Only terminate the known legacy project watchdog. Never kill an
        # unrelated process if Android has reused a stale PID.
        if 'android_watchdog.sh' in cmd:
            try:
                os.kill(old_pid, signal.SIGTERM)
                for _ in range(10):
                    if not _pid_alive(old_pid):
                        break
                    time.sleep(0.2)
            except Exception:
                pass
    try:
        WATCHDOG_PIDFILE.unlink(missing_ok=True)
    except Exception:
        pass

    try:
        ANDROID_WATCHDOG.chmod(0o755)
        proc = subprocess.Popen(
            ['/data/data/com.termux/files/usr/bin/bash', str(ANDROID_WATCHDOG)],
            cwd=str(BASE_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        WATCHDOG_PIDFILE.write_text(str(proc.pid), encoding='utf-8')
    except Exception:
        pass


def _watchdog_guardian_loop():
    while True:
        time.sleep(60)
        try:
            _ensure_watchdog()
        except Exception:
            # Supervision must never take the API process down.
            pass


def _start_watchdog_guardian():
    global _WATCHDOG_GUARDIAN_STARTED
    if os.name == 'nt' or os.getenv('ANDROID_SKIP_WATCHDOG') == '1':
        return
    with _WATCHDOG_GUARDIAN_LOCK:
        if _WATCHDOG_GUARDIAN_STARTED:
            return
        _WATCHDOG_GUARDIAN_STARTED = True
        threading.Thread(
            target=_watchdog_guardian_loop,
            name='android-watchdog-guardian',
            daemon=True,
        ).start()


def _write_heartbeat():
    payload = {
        'timestamp': time.time(),
        'iso': datetime.now().astimezone().isoformat(timespec='seconds'),
        'pid': os.getpid(),
        'mode': os.getenv('APP_MODE', 'paper'),
        'tradingEnabled': False,
        'platform': 'android-termux',
    }
    tmp = HEARTBEAT.with_suffix('.tmp')
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        tmp.replace(HEARTBEAT)
    except Exception:
        pass


def _heartbeat_loop():
    while True:
        _write_heartbeat()
        time.sleep(15)


def _start_heartbeat():
    global _HEARTBEAT_STARTED
    with _HEARTBEAT_LOCK:
        if _HEARTBEAT_STARTED:
            return
        _HEARTBEAT_STARTED = True
        _write_heartbeat()
        threading.Thread(
            target=_heartbeat_loop,
            name='android-app-heartbeat',
            daemon=True,
        ).start()


def _backup_status_safe():
    try:
        return db_backup_status()
    except Exception as exc:
        return {'ok': False, 'error': f'{type(exc).__name__}: {exc}', 'orderAccess': False}


def _backup_done_today(now):
    latest = (_backup_status_safe().get('latest') or {}).get('modifiedAt')
    if not latest:
        return False
    try:
        return datetime.fromisoformat(str(latest)).astimezone(KST).date() == now.date()
    except Exception:
        return False


def _daily_backup_loop():
    while True:
        try:
            now = datetime.now(KST)
            minute = now.hour * 60 + now.minute
            due = now.weekday() < 5 and minute >= DB_BACKUP_AFTER_MINUTE and not base.regular_session(now)
            if due and not _backup_done_today(now):
                _DB_BACKUP_STATE['lastAttemptAt'] = now.isoformat(timespec='seconds')
                result = db_snapshot('daily-after-market')
                _DB_BACKUP_STATE['lastResult'] = result
                _DB_BACKUP_STATE['lastError'] = None if result.get('ok') else result.get('error')
        except Exception as exc:
            _DB_BACKUP_STATE['lastError'] = f'{type(exc).__name__}: {exc}'
        time.sleep(15 * 60)


def _start_daily_backup():
    global _DB_BACKUP_STARTED
    with _DB_BACKUP_LOCK:
        if _DB_BACKUP_STARTED:
            return
        _DB_BACKUP_STARTED = True
        threading.Thread(
            target=_daily_backup_loop,
            name='android-db-backup',
            daemon=True,
        ).start()


def _launch_android_update(server_pid):
    log_path = Path.home() / 'stock-trader-update.log'
    try:
        with log_path.open('a', encoding='utf-8') as log:
            proc = subprocess.Popen(
                ['/data/data/com.termux/files/usr/bin/bash', str(ANDROID_UPDATE_SCRIPT), str(server_pid)],
                cwd=str(ROOT_DIR),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        try:
            rc = proc.wait(timeout=90)
        except subprocess.TimeoutExpired:
            # Normal when dependency/preflight work takes longer; the detached
            # updater remains alive and will restart the server itself.
            return
        if rc != 0:
            with base._UPDATE_LOCK:
                base._UPDATE.update({
                    'running': False,
                    'lastError': f'Android 업데이트 실패 (exit {rc}). stock-trader-update.log 확인',
                    'launcher': 'android-termux',
                })
    except Exception as exc:
        with base._UPDATE_LOCK:
            base._UPDATE.update({
                'running': False,
                'lastError': f'{type(exc).__name__}: {exc}',
                'launcher': 'android-termux',
            })


def android_update_request(request):
    if not base._remote_allowed(request):
        return JSONResponse({'ok': False, 'error': 'Tailscale/localhost only'}, 403)
    if os.name == 'nt':
        return JSONResponse({'ok': False, 'error': 'Android updater is not available on Windows.'}, 409)
    if not ANDROID_UPDATE_SCRIPT.exists():
        return JSONResponse({'ok': False, 'error': 'android_update.sh 파일이 없습니다.'}, 500)
    if not _android_safety_ok():
        return JSONResponse({'ok': False, 'error': 'SAFETY BLOCK: APP_MODE=paper / ENABLE_TRADING=false 확인 필요'}, 409)

    with base._UPDATE_LOCK:
        if base._UPDATE.get('running'):
            return JSONResponse({'ok': False, 'error': '이미 업데이트 중입니다.'}, 409)
        opened, err = base._has_open_positions()
        if err:
            return JSONResponse({'ok': False, 'error': err}, 503)
        if opened:
            return JSONResponse({'ok': False, 'error': '열린 Paper 포지션이 있어 업데이트를 차단했습니다.'}, 409)
        dirty = base._git_state()
        if dirty:
            return JSONResponse({
                'ok': False,
                'error': '추적 파일에 로컬 변경이 있어 업데이트를 차단했습니다.',
                'dirty': dirty,
            }, 409)
        base._UPDATE.update({
            'running': True,
            'requestedAt': datetime.now().isoformat(),
            'lastError': None,
            'launcher': 'android-termux',
        })

    threading.Thread(
        target=_launch_android_update,
        args=(os.getpid(),),
        name='android-safe-updater',
        daemon=True,
    ).start()
    return JSONResponse({
        'ok': True,
        'message': 'Android 안전 업데이트를 시작했습니다. Watchdog은 업데이트 중 일시정지되고 성공/롤백 후 자동 복귀합니다.',
    })


def android_watchdog_status(request):
    now = time.time()
    wd_pid = _read_pid(WATCHDOG_PIDFILE)
    server_pid = _read_pid(SERVER_PIDFILE)
    wd_cmd = _pid_cmdline(wd_pid) if _pid_alive(wd_pid) else ''
    server_cmd = _pid_cmdline(server_pid) if _pid_alive(server_pid) else ''
    heartbeat = None
    heartbeat_age = None
    try:
        heartbeat = json.loads(HEARTBEAT.read_text(encoding='utf-8'))
        heartbeat_age = round(max(0.0, now - float(heartbeat.get('timestamp') or 0)), 1)
    except Exception:
        pass
    return JSONResponse({
        'ok': True,
        'platform': 'android-termux',
        'serverPid': server_pid,
        'serverPidAlive': _pid_alive(server_pid),
        'serverPidValid': 'uvicorn android_unified_app:app' in server_cmd,
        'watchdogPid': wd_pid,
        'watchdogAlive': _pid_alive(wd_pid),
        'watchdogV2': 'android_watchdog_v2.sh' in wd_cmd,
        'guardianActive': _WATCHDOG_GUARDIAN_STARTED,
        'heartbeatAgeSec': heartbeat_age,
        'heartbeatFresh': heartbeat_age is not None and heartbeat_age <= 90,
        'updateInProgress': UPDATE_FLAG.exists(),
        'mutationGuard': 'TAILSCALE_OR_LOCALHOST_ONLY',
        'dailyBackupSchedulerActive': _DB_BACKUP_STARTED,
        'dbBackup': _backup_status_safe(),
        'dbBackupRuntime': dict(_DB_BACKUP_STATE),
        'safety': {
            'paperMode': os.getenv('APP_MODE', 'paper').lower() == 'paper',
            'realOrderEnabled': False,
        },
    })


_start_heartbeat()
_ensure_watchdog()
_start_watchdog_guardian()
_start_daily_backup()

app.router.routes.extend([
    Route('/api/system/update', android_update_request, methods=['POST']),
    Route('/api/system/update/run', android_update_request, methods=['POST']),
    Route('/api/system/android-watchdog', android_watchdog_status),
])
