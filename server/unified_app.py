from pathlib import Path
import sqlite3,subprocess,threading,time
from datetime import datetime
from fastapi import Request
from fastapi.responses import FileResponse,RedirectResponse,JSONResponse
from starlette.routing import Route,Mount
from starlette.staticfiles import StaticFiles
from app import app
from collector import DB_PATH
BASE_DIR=Path(__file__).resolve().parent;ROOT_DIR=BASE_DIR.parent;DASHBOARD=BASE_DIR/'unified_dashboard.html';CLASSIC_INDEX=ROOT_DIR/'index.html';UPDATE_SCRIPT=BASE_DIR/'remote_update.cmd';UPDATE_LAUNCHER=BASE_DIR/'remote_update.vbs';UI_VERSION='0.15.2';_UPDATE={'running':False,'requestedAt':None,'lastError':None};_UPDATE_LOCK=threading.Lock()
def _remote_allowed(request):
 host=(request.client.host if request.client else '') or '';return host in ('127.0.0.1','::1') or host.startswith('100.')
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
  r=subprocess.run(['git','status','--porcelain','--untracked-files=no'],cwd=ROOT_DIR,capture_output=True,text=True,timeout=8);return [x for x in r.stdout.splitlines() if x.strip()]
 except Exception:return []
def _run_update():
 try:subprocess.Popen(['wscript.exe',str(UPDATE_LAUNCHER)],cwd=BASE_DIR,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 except Exception as exc:
  with _UPDATE_LOCK:_UPDATE['running']=False;_UPDATE['lastError']=str(exc)
def update_request(request):
 if not _remote_allowed(request):return JSONResponse({'ok':False,'error':'Tailscale/localhost only'},403)
 with _UPDATE_LOCK:
  if _UPDATE['running']:return JSONResponse({'ok':False,'error':'이미 업데이트 중입니다.'},409)
  opened,err=_has_open_positions()
  if err:return JSONResponse({'ok':False,'error':err},503)
  if opened:return JSONResponse({'ok':False,'error':'열린 Paper 포지션이 있어 업데이트를 차단했습니다.'},409)
  dirty=_git_state()
  if dirty:return JSONResponse({'ok':False,'error':'추적 파일에 로컬 변경이 있어 업데이트를 차단했습니다.','dirty':dirty},409)
  _UPDATE.update({'running':True,'requestedAt':datetime.now().isoformat(),'lastError':None})
 threading.Thread(target=_run_update,daemon=True).start();return JSONResponse({'ok':True,'message':'업데이트를 시작했습니다.'})
def update_status(request):return JSONResponse({'ok':True,'uiVersion':UI_VERSION,'update':dict(_UPDATE)})
def ui_health(request):return JSONResponse({'ok':True,'uiVersion':UI_VERSION,'scannerPolicy':{'safeUniverse':180,'activeFocus':40,'top':10},'validation':{'mfeMae':True,'entrySnapshots':True,'scoreBuckets':True,'profitFactor':True,'expectancy':True,'mdd':True,'failureClassification':True,'controlStrategy':'v0.8.0'},'usMarket':{'collector':True,'paper':False,'realOrder':False}})
def root(request):return RedirectResponse('/classic')
def classic(request):return FileResponse(CLASSIC_INDEX,headers={'Cache-Control':'no-store, max-age=0'})
def dashboard(request):return FileResponse(DASHBOARD,headers={'Cache-Control':'no-store, max-age=0'})
def static_file(request):
 p=ROOT_DIR/request.url.path.lstrip('/')
 if not p.exists() or not p.is_file():return JSONResponse({'error':'not found'},404)
 return FileResponse(p,headers={'Cache-Control':'no-store, max-age=0'})
app.router.routes.extend([Route('/',root),Route('/classic',classic),Route('/dashboard',dashboard),Route('/api/system/update',update_request,methods=['POST']),Route('/api/system/update/status',update_status),Route('/api/system/ui-health',ui_health),Route('/styles.css',static_file),Route('/manifest.webmanifest',static_file),Route('/sw.js',static_file),Mount('/js',app=StaticFiles(directory=ROOT_DIR/'js'),name='js'),Mount('/icons',app=StaticFiles(directory=ROOT_DIR/'icons'),name='icons')])
