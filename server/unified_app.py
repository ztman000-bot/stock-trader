from pathlib import Path
import sqlite3,subprocess,threading,time,os
from datetime import datetime
from fastapi import Request
from fastapi.responses import FileResponse,RedirectResponse,JSONResponse
from starlette.routing import Route,Mount
from starlette.staticfiles import StaticFiles
from app import app
from collector import DB_PATH
from historical_accumulator import start as history_start_job, stop as history_stop_job, status as history_job_status

BASE_DIR=Path(__file__).resolve().parent
ROOT_DIR=BASE_DIR.parent
DASHBOARD=BASE_DIR/'unified_dashboard.html'
CLASSIC_INDEX=ROOT_DIR/'index.html'
UPDATE_SCRIPT=BASE_DIR/'remote_update.cmd'
UI_VERSION='0.15.8'
_UPDATE={'running':False,'requestedAt':None,'lastError':None,'launcher':'cmd-direct'}
_UPDATE_LOCK=threading.Lock()

def _remote_allowed(request):
    host=(request.client.host if request.client else '') or ''
    return host in ('127.0.0.1','::1') or host.startswith('100.')

def _has_open_positions():
    try:
        conn=sqlite3.connect(DB_PATH,timeout=2)
        try:return bool(conn.execute("SELECT 1 FROM paper_trades WHERE status='OPEN' LIMIT 1").fetchone()),None
        finally:conn.close()
    except sqlite3.OperationalError as exc:
        if 'no such table' in str(exc).lower():return False,None
        return None,f'Paper DB 확인 실패: {exc}'

def _git_state():
    try:
        r=subprocess.run(['git','status','--porcelain','--untracked-files=no'],cwd=ROOT_DIR,capture_output=True,text=True,timeout=8)
        return [x for x in r.stdout.splitlines() if x.strip()]
    except Exception as exc:return [f'git status 실패: {exc}']

def _run_update():
    try:
        if not UPDATE_SCRIPT.exists():raise FileNotFoundError(f'업데이트 스크립트 없음: {UPDATE_SCRIPT}')
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'DETACHED_PROCESS',0)
        subprocess.Popen(['cmd.exe','/d','/c',str(UPDATE_SCRIPT)],cwd=str(ROOT_DIR),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags,close_fds=True)
        time.sleep(12)
        with _UPDATE_LOCK:
            if _UPDATE.get('running'):_UPDATE.update({'lastError':'업데이트 프로세스가 서버를 재시작하지 못했습니다. TEMP 로그를 확인하세요.','running':False})
    except Exception as exc:
        with _UPDATE_LOCK:_UPDATE.update({'running':False,'lastError':f'{type(exc).__name__}: {exc}'})

def update_request(request):
    if not _remote_allowed(request):return JSONResponse({'ok':False,'error':'Tailscale/localhost only'},403)
    with _UPDATE_LOCK:
        if _UPDATE['running']:return JSONResponse({'ok':False,'error':'이미 업데이트 중입니다.'},409)
        opened,err=_has_open_positions()
        if err:return JSONResponse({'ok':False,'error':err},503)
        if opened:return JSONResponse({'ok':False,'error':'열린 Paper 포지션이 있어 업데이트를 차단했습니다.'},409)
        dirty=_git_state()
        if dirty:return JSONResponse({'ok':False,'error':'추적 파일에 로컬 변경이 있어 업데이트를 차단했습니다.','dirty':dirty},409)
        if not UPDATE_SCRIPT.exists():return JSONResponse({'ok':False,'error':'remote_update.cmd를 찾을 수 없습니다.'},500)
        _UPDATE.update({'running':True,'requestedAt':datetime.now().isoformat(),'lastError':None,'launcher':'cmd-direct'})
    threading.Thread(target=_run_update,daemon=True).start()
    return JSONResponse({'ok':True,'message':'업데이트를 시작했습니다. 서버 재시작 후 자동으로 새 버전을 확인합니다.','target':'latest-main'})

def update_status(request):return JSONResponse({'ok':True,'uiVersion':UI_VERSION,'update':dict(_UPDATE)})
def history_status(request):return JSONResponse({'ok':True,'status':history_job_status()})
def history_start(request):
    try:
        days=int(request.query_params.get('days','20'));codes=int(request.query_params.get('max_codes','40'))
        result=history_start_job(days,codes)
        if not result.get('ok'):return JSONResponse({'ok':False,'error':result.get('message','수집 시작 거부'),'status':result.get('status',history_job_status())},409)
        return JSONResponse(result)
    except ValueError:return JSONResponse({'ok':False,'error':'days/max_codes는 숫자여야 합니다.'},400)
    except Exception as exc:return JSONResponse({'ok':False,'error':f'Historical accumulator 시작 오류: {type(exc).__name__}: {exc}','status':history_job_status()},500)
def history_stop(request):
    try:return JSONResponse(history_stop_job())
    except Exception as exc:return JSONResponse({'ok':False,'error':f'중지 오류: {type(exc).__name__}: {exc}'},500)
def ui_health(request):return JSONResponse({'ok':True,'uiVersion':UI_VERSION,'scannerPolicy':{'safeUniverse':180,'activeFocus':40,'top':10},'validation':{'mfeMae':True,'entrySnapshots':True,'scoreBuckets':True,'profitFactor':True,'expectancy':True,'mdd':True,'failureClassification':True,'controlStrategy':'v0.8.0'},'backtest':{'enabled':True,'ui':True,'historicalAccumulator':True,'maxDays':120,'maxCodes':100,'resumableCache':True,'diagnostics':True,'controlStrategy':'v0.8.0 LOCKED','costsIncluded':True,'liveMutation':False,'sampleWarningBelow':200},'usMarket':{'collector':True,'paper':False,'realOrder':False},'updater':{'launcher':'cmd-direct','wscriptRequired':False,'openPositionGuard':True,'dirtyTreeGuard':True,'compatRoute':True}})
def root(request):return RedirectResponse('/classic')
def classic(request):return FileResponse(CLASSIC_INDEX,headers={'Cache-Control':'no-store, max-age=0'})
def dashboard(request):return FileResponse(DASHBOARD,headers={'Cache-Control':'no-store, max-age=0'})
def static_file(request):
    p=ROOT_DIR/request.url.path.lstrip('/')
    if not p.exists() or not p.is_file():return JSONResponse({'error':'not found'},404)
    return FileResponse(p,headers={'Cache-Control':'no-store, max-age=0'})
app.router.routes.extend([Route('/',root),Route('/classic',classic),Route('/dashboard',dashboard),Route('/api/system/update',update_request,methods=['POST']),Route('/api/system/update/run',update_request,methods=['POST']),Route('/api/system/update/status',update_status),Route('/api/system/ui-health',ui_health),Route('/api/history/status',history_status),Route('/api/history/start',history_start,methods=['POST']),Route('/api/history/stop',history_stop,methods=['POST']),Route('/styles.css',static_file),Route('/manifest.webmanifest',static_file),Route('/sw.js',static_file),Mount('/js',app=StaticFiles(directory=ROOT_DIR/'js'),name='js'),Mount('/icons',app=StaticFiles(directory=ROOT_DIR/'icons'),name='icons')])
