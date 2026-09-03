import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from fastapi.responses import JSONResponse
from starlette.routing import Route

import unified_app as base

app = base.app
BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
ANDROID_UPDATE_SCRIPT = BASE_DIR / 'android_update.sh'

# unified_app keeps the Windows updater for laptop use. On Android we replace
# only the update POST routes; every trading/research route remains unchanged.
_UPDATE_PATHS = {'/api/system/update', '/api/system/update/run'}
app.router.routes[:] = [
    route for route in app.router.routes
    if getattr(route, 'path', None) not in _UPDATE_PATHS
]


def _android_safety_ok():
    return (
        os.getenv('APP_MODE', 'paper').lower() == 'paper'
        and os.getenv('ENABLE_TRADING', 'false').lower() == 'false'
    )


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
        'message': 'Android 안전 업데이트를 시작했습니다. Paper/안전설정 확인 후 Git fast-forward와 자동 재시작을 수행합니다.',
    })


app.router.routes.extend([
    Route('/api/system/update', android_update_request, methods=['POST']),
    Route('/api/system/update/run', android_update_request, methods=['POST']),
])
