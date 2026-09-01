from pathlib import Path
import sqlite3,subprocess,threading,time
from datetime import datetime
from fastapi.responses import FileResponse,RedirectResponse,JSONResponse
from starlette.routing import Route,Mount
from starlette.staticfiles import StaticFiles
from app import app
from collector import DB_PATH,regular_session,latest_quotes,collector
from historical_accumulator import start as history_start_job,stop as history_stop_job,status as history_job_status
from strategy_lab import run_lab,run_exit_lab
from market_lab import run_market_lab
from profitability_lab import run_profitability_lab
from robust_validation import run_robust_validation
from data_quality import audit as data_quality_audit
from stocks_in_play import scan as stocks_in_play_scan,start_recorder as stocks_recorder_start,snapshot_stats as stocks_snapshot_stats
from research_daemon import start as research_start,latest as research_latest,status as research_status

BASE_DIR=Path(__file__).resolve().parent
ROOT_DIR=BASE_DIR.parent
DASHBOARD=BASE_DIR/'unified_dashboard.html'
CLASSIC_INDEX=ROOT_DIR/'index.html'
UPDATE_SCRIPT=BASE_DIR/'remote_update.cmd'
UI_VERSION='0.17.2'
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
    except Exception as exc:
        return [f'git status 실패: {exc}']

def _run_update():
    try:
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'DETACHED_PROCESS',0)
        subprocess.Popen(['cmd.exe','/d','/c',str(UPDATE_SCRIPT)],cwd=str(ROOT_DIR),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags,close_fds=True)
        time.sleep(12)
        with _UPDATE_LOCK:
            if _UPDATE.get('running'):_UPDATE.update({'lastError':'업데이트 프로세스가 서버를 재시작하지 못했습니다.','running':False})
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
        _UPDATE.update({'running':True,'requestedAt':datetime.now().isoformat(),'lastError':None,'launcher':'cmd-direct'})
    threading.Thread(target=_run_update,daemon=True).start()
    return JSONResponse({'ok':True,'message':'업데이트를 시작했습니다.'})

def update_status(request):return JSONResponse({'ok':True,'uiVersion':UI_VERSION,'update':dict(_UPDATE)})

def strategy_lab(request):
    try:return JSONResponse(run_lab(int(request.query_params.get('max_codes','40'))))
    except Exception as exc:return JSONResponse({'ok':False,'error':f'Strategy Lab 오류: {type(exc).__name__}: {exc}'},500)

def exit_lab(request):
    try:return JSONResponse(run_exit_lab(request.query_params.get('strategy','cross_trend_v2')))
    except Exception as exc:return JSONResponse({'ok':False,'error':f'Cross/Overnight Lab 오류: {type(exc).__name__}: {exc}'},500)

def market_lab(request):
    try:return JSONResponse(run_market_lab(int(request.query_params.get('max_codes','40'))))
    except Exception as exc:return JSONResponse({'ok':False,'error':f'Market Research Lab 오류: {type(exc).__name__}: {exc}'},500)

def profitability_lab(request):
    try:return JSONResponse(run_profitability_lab(int(request.query_params.get('max_codes','40'))))
    except Exception as exc:return JSONResponse({'ok':False,'error':f'Profitability Lab 오류: {type(exc).__name__}: {exc}'},500)

def robust_lab(request):
    try:return JSONResponse(run_robust_validation(int(request.query_params.get('max_codes','40'))))
    except Exception as exc:return JSONResponse({'ok':False,'error':f'Robust Validation 오류: {type(exc).__name__}: {exc}'},500)

def quality_lab(request):
    try:return JSONResponse(data_quality_audit())
    except Exception as exc:return JSONResponse({'ok':False,'error':f'Data Quality 오류: {type(exc).__name__}: {exc}'},500)

def stocks_lab(request):
    try:return JSONResponse({'scan':stocks_in_play_scan(int(request.query_params.get('limit','40'))),'snapshots':stocks_snapshot_stats()})
    except Exception as exc:return JSONResponse({'ok':False,'error':f'Stocks-in-Play 오류: {type(exc).__name__}: {exc}'},500)

def runtime_health(request):
    rows=latest_quotes(getattr(collector,'watchlist',[]) or None);ages=[];now=datetime.now().astimezone()
    for q in rows:
        try:ages.append((now-datetime.fromisoformat(str(q.get('sampled_at'))).astimezone()).total_seconds())
        except:pass
    max_age=max(ages) if ages else None;live=regular_session()
    fresh=(max_age is not None and max_age<=180) if live else True
    hs=history_job_status();ok=bool(fresh and not (live and hs.get('running') and not hs.get('pausedForLive')))
    return JSONResponse({
        'ok':ok,'liveSession':live,'quoteRows':len(rows),
        'maxQuoteAgeSec':round(max_age,1) if max_age is not None else None,
        'quotesFresh':fresh,'historyPausedForLive':bool(hs.get('pausedForLive')),
        'historyLivePriority':bool(hs.get('liveSessionPriority')),
        'newEntriesAllowedByRuntime':ok,'realOrderEnabled':False
    },200 if ok else 503)

def final_research(request):return JSONResponse(research_latest())
def research_state(request):return JSONResponse({'ok':True,'status':research_status(),'history':history_job_status()})
def history_status(request):return JSONResponse({'ok':True,'status':history_job_status()})

def history_start(request):
    try:
        result=history_start_job(int(request.query_params.get('days','20')),int(request.query_params.get('max_codes','40')))
        return JSONResponse(result,200 if result.get('ok') else 409)
    except Exception as exc:return JSONResponse({'ok':False,'error':str(exc)},500)

def history_stop(request):return JSONResponse(history_stop_job())

def ui_health(request):
    return JSONResponse({
        'ok':True,'uiVersion':UI_VERSION,
        'strategyLab':{
            'enabled':True,'version':'0.17.2','control':'v0.8.0 LOCKED',
            'crossTrendV2':True,'falseSignalFilter':True,'marketRegimeLab':True,
            'surgeDiscoveryLab':True,'stocksInPlay':True,'stocksInPlaySnapshots':True,
            'profitabilityLab':True,'exitIntelligenceV2':True,'pullbackEntry':True,
            'payoffTracking':True,'goodDataGate':True,'walkForward':True,'finalLockbox':True,
            'dataQualityAudit':True,'oneMinuteExitGate':True,'automaticResearch':True,
            'liveSessionResearchDefer':True,'overnightLab':True,'liveMutation':False
        },
        'backtest':{
            'enabled':True,'engine':'precise-portfolio-v1','executionModel':'next-bar-open',
            'costsIncluded':True,'stressTests':True,'qualityGate':'GOOD_ONLY','liveMutation':False
        },
        'security':{'localhostBindExpected':True,'tailscaleProxyExpected':True},
        'usMarket':{'collector':True,'paper':False,'realOrder':False}
    })

def root(request):return RedirectResponse('/classic')
def classic(request):return FileResponse(CLASSIC_INDEX,headers={'Cache-Control':'no-store, max-age=0'})
def dashboard(request):return FileResponse(DASHBOARD,headers={'Cache-Control':'no-store, max-age=0'})

def static_file(request):
    p=ROOT_DIR/request.url.path.lstrip('/')
    return FileResponse(p,headers={'Cache-Control':'no-store, max-age=0'}) if p.exists() and p.is_file() else JSONResponse({'error':'not found'},404)

app.router.routes.extend([
    Route('/',root),Route('/classic',classic),Route('/dashboard',dashboard),
    Route('/api/system/update',update_request,methods=['POST']),
    Route('/api/system/update/run',update_request,methods=['POST']),
    Route('/api/system/update/status',update_status),
    Route('/api/system/ui-health',ui_health),
    Route('/api/system/runtime-health',runtime_health),
    Route('/api/strategy-lab/run',strategy_lab),
    Route('/api/strategy-lab/exit',exit_lab),
    Route('/api/research/market-lab',market_lab),
    Route('/api/research/profitability',profitability_lab),
    Route('/api/research/robust',robust_lab),
    Route('/api/research/data-quality',quality_lab),
    Route('/api/research/stocks-in-play',stocks_lab),
    Route('/api/research/final',final_research),
    Route('/api/research/status',research_state),
    Route('/api/history/status',history_status),
    Route('/api/history/start',history_start,methods=['POST']),
    Route('/api/history/stop',history_stop,methods=['POST']),
    Route('/styles.css',static_file),Route('/manifest.webmanifest',static_file),Route('/sw.js',static_file),
    Mount('/js',app=StaticFiles(directory=ROOT_DIR/'js'),name='js'),
    Mount('/icons',app=StaticFiles(directory=ROOT_DIR/'icons'),name='icons')
])

stocks_recorder_start()
research_start()
