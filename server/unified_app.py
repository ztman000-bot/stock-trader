from pathlib import Path
import sqlite3
import subprocess
import threading
import time
from datetime import datetime

from fastapi import Request
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles

from app import app
from collector import DB_PATH

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
DASHBOARD = BASE_DIR / 'unified_dashboard.html'
CLASSIC_INDEX = ROOT_DIR / 'index.html'
UPDATE_SCRIPT = BASE_DIR / 'remote_update.cmd'
UPDATE_LAUNCHER = BASE_DIR / 'remote_update.vbs'
UI_VERSION = '0.11.4'
_UPDATE = {'running': False, 'requestedAt': None, 'lastError': None}
_UPDATE_LOCK = threading.Lock()

def _remote_allowed(request: Request):
    host=(request.client.host if request.client else '') or ''
    return host in ('127.0.0.1','::1') or host.startswith('100.')

def _has_open_positions():
    try:
        conn=sqlite3.connect(DB_PATH,timeout=2)
        try:
            row=conn.execute("SELECT 1 FROM paper_trades WHERE status='OPEN' LIMIT 1").fetchone(); return bool(row),None
        finally: conn.close()
    except sqlite3.OperationalError as exc:
        if 'no such table' in str(exc).lower(): return False,None
        return None,f'Paper DB 확인 실패: {exc}'
    except Exception as exc: return None,f'Paper DB 확인 실패: {type(exc).__name__}: {exc}'

def _launch_update_after_response():
    time.sleep(2.0)
    try:
        subprocess.Popen(['wscript.exe',str(UPDATE_LAUNCHER)],cwd=str(ROOT_DIR),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=False)
    except Exception as exc:
        with _UPDATE_LOCK: _UPDATE.update({'running':False,'lastError':f'{type(exc).__name__}: {exc}'})

async def update_run(request: Request):
    if not _remote_allowed(request): return JSONResponse({'detail':'업데이트는 localhost 또는 Tailscale 접속에서만 허용됩니다.'},403)
    has_open,db_error=_has_open_positions()
    if db_error: return JSONResponse({'detail':db_error+' · 안전을 위해 업데이트를 보류합니다.'},409)
    if has_open: return JSONResponse({'detail':'열린 Paper 포지션이 있어 업데이트를 차단했습니다.'},409)
    if _UPDATE.get('running'): return JSONResponse({'detail':'업데이트가 이미 진행 중입니다.'},409)
    if not UPDATE_SCRIPT.exists() or not UPDATE_LAUNCHER.exists(): return JSONResponse({'detail':'원격 업데이트 파일이 없습니다. 노트북에서 통합 업데이트를 한 번 실행하세요.'},409)
    with _UPDATE_LOCK: _UPDATE.update({'running':True,'requestedAt':datetime.now().isoformat(),'lastError':None})
    threading.Thread(target=_launch_update_after_response,daemon=True,name='remote-update-launcher').start()
    return JSONResponse({'ok':True,'accepted':True,'uiVersion':UI_VERSION,'message':'업데이트 요청 접수 완료. 안전 재시작을 시작합니다.'})

async def update_status(request: Request):
    return JSONResponse({'ok':True,'uiVersion':UI_VERSION,**_UPDATE})

def _ensure_route(path, endpoint, methods=None, name=None):
    for r in app.router.routes:
        if getattr(r,'path',None)==path:
            return
    app.router.routes.append(Route(path,endpoint=endpoint,methods=methods or ['GET'],name=name))

_ensure_route('/api/system/update/run',update_run,['POST'],'system_update_run')
_ensure_route('/api/system/update/status',update_status,['GET'],'system_update_status')

for r in list(app.router.routes):
    if getattr(r,'path',None)=='/': app.router.routes.remove(r)

async def root_redirect(request: Request): return RedirectResponse(url='/classic')
async def classic(request: Request): return FileResponse(CLASSIC_INDEX)
async def dashboard(request: Request): return FileResponse(DASHBOARD)
async def root_file(request: Request):
    name=request.path_params['name']; p=ROOT_DIR/name
    return FileResponse(p) if p.is_file() else JSONResponse({'detail':'Not found'},404)

app.router.routes.append(Route('/',root_redirect,methods=['GET']))
app.router.routes.append(Route('/classic',classic,methods=['GET']))
app.router.routes.append(Route('/classic/',classic,methods=['GET']))
app.router.routes.append(Route('/mobile',dashboard,methods=['GET']))
app.router.routes.append(Route('/dashboard',dashboard,methods=['GET']))
for name in ['styles.css','manifest.webmanifest','sw.js']:
    app.router.routes.append(Route('/'+name,root_file,methods=['GET']))
if (ROOT_DIR/'js').exists(): app.router.routes.append(Mount('/js',app=StaticFiles(directory=str(ROOT_DIR/'js')),name='root-js'))
if (ROOT_DIR/'icons').exists(): app.router.routes.append(Mount('/icons',app=StaticFiles(directory=str(ROOT_DIR/'icons')),name='root-icons'))
