from pathlib import Path
import sqlite3,subprocess,threading,time
from datetime import datetime
from fastapi import Request
from fastapi.responses import FileResponse,RedirectResponse,JSONResponse
from starlette.routing import Route,Mount
from starlette.staticfiles import StaticFiles
from app import app
from collector import DB_PATH
BASE_DIR=Path(__file__).resolve().parent;ROOT_DIR=BASE_DIR.parent;DASHBOARD=BASE_DIR/'unified_dashboard.html';CLASSIC_INDEX=ROOT_DIR/'index.html';UPDATE_SCRIPT=BASE_DIR/'remote_update.cmd';UPDATE_LAUNCHER=BASE_DIR/'remote_update.vbs';UI_VERSION='0.15.1';_UPDATE={'running':False,'requestedAt':None,'lastError':None};_UPDATE_LOCK=threading.Lock()
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
 except Exception as exc:return None,f'Paper DB 확인 실패: {type(exc).__name__}: {exc}'
def _launch_update_after_response():
 time.sleep(2)
 try:subprocess.Popen(['wscript.exe',str(UPDATE_LAUNCHER)],cwd=str(ROOT_DIR),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=False)
 except Exception as exc:
  with _UPDATE_LOCK:_UPDATE.update({'running':False,'lastError':f'{type(exc).__name__}: {exc}'})
async def update_run(request):
 if not _remote_allowed(request):return JSONResponse({'detail':'업데이트는 localhost 또는 Tailscale 접속에서만 허용됩니다.'},403)
 has_open,err=_has_open_positions()
 if err:return JSONResponse({'detail':err+' · 안전을 위해 업데이트를 보류합니다.'},409)
 if has_open:return JSONResponse({'detail':'열린 Paper 포지션이 있어 업데이트를 차단했습니다.'},409)
 if _UPDATE.get('running'):return JSONResponse({'detail':'업데이트가 이미 진행 중입니다.'},409)
 if not UPDATE_SCRIPT.exists() or not UPDATE_LAUNCHER.exists():return JSONResponse({'detail':'원격 업데이트 파일이 없습니다.'},409)
 with _UPDATE_LOCK:_UPDATE.update({'running':True,'requestedAt':datetime.now().isoformat(),'lastError':None})
 threading.Thread(target=_launch_update_after_response,daemon=True).start();return JSONResponse({'ok':True,'accepted':True,'uiVersion':UI_VERSION,'message':'업데이트 요청 접수 완료.'})
async def update_status(request):return JSONResponse({'ok':True,'uiVersion':UI_VERSION,**_UPDATE})
async def ui_health(request):
 checks={'index':CLASSIC_INDEX.is_file(),'css':(ROOT_DIR/'styles.css').is_file(),'liveJs':(ROOT_DIR/'js'/'live-app.js').is_file(),'usUi':(ROOT_DIR/'js'/'ui-polish.js').is_file()};return JSONResponse({'ok':all(checks.values()),'uiVersion':UI_VERSION,'scannerPolicy':{'safeUniverse':180,'activeFocus':40,'top':10},'markets':{'kr':{'paper':True,'realOrder':False},'us':{'data':True,'paper':False,'realOrder':False}},'validation':{'mfeMae':True,'entrySnapshots':True,'scoreBuckets':True,'profitFactor':True,'expectancy':True,'mdd':True,'failureClassification':True,'controlStrategy':'v0.8.0'},'checks':checks},status_code=200 if all(checks.values()) else 503)
def _ensure_route(path,endpoint,methods=None,name=None):
 if not any(getattr(r,'path',None)==path for r in app.router.routes):app.router.routes.append(Route(path,endpoint=endpoint,methods=methods or ['GET'],name=name))
_ensure_route('/api/system/update/run',update_run,['POST']);_ensure_route('/api/system/update/status',update_status);_ensure_route('/api/system/ui-health',ui_health)
for r in list(app.router.routes):
 if getattr(r,'path',None)=='/':app.router.routes.remove(r)
async def root_redirect(request):return RedirectResponse('/classic')
async def classic(request):return FileResponse(CLASSIC_INDEX)
async def dashboard(request):return FileResponse(DASHBOARD)
async def root_asset(request):
 name=request.url.path.lstrip('/');p=ROOT_DIR/name
 if name not in {'styles.css','manifest.webmanifest','sw.js'}:return JSONResponse({'detail':'Not found'},404)
 return FileResponse(p) if p.is_file() else JSONResponse({'detail':'Not found'},404)
for p,e in [('/',root_redirect),('/classic',classic),('/classic/',classic),('/mobile',dashboard),('/dashboard',dashboard)]:app.router.routes.append(Route(p,e,methods=['GET']))
for name in ['styles.css','manifest.webmanifest','sw.js']:app.router.routes.append(Route('/'+name,root_asset,methods=['GET']))
if (ROOT_DIR/'js').exists():app.router.routes.append(Mount('/js',app=StaticFiles(directory=str(ROOT_DIR/'js')),name='root-js'))
if (ROOT_DIR/'icons').exists():app.router.routes.append(Mount('/icons',app=StaticFiles(directory=str(ROOT_DIR/'icons')),name='root-icons'))