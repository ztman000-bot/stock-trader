"""Automatic research pipeline v0.17.1. Research only; never mutates live rules/orders."""
import json,sqlite3,threading,time
from datetime import datetime
from pathlib import Path
from market_lab import run_market_lab
from strategy_lab import run_lab
from profitability_lab import run_profitability_lab
from robust_validation import run_robust_validation
from data_quality import audit as data_quality_audit
from historical_accumulator import start as history_start,status as history_status
from collector import DB_PATH,collector,regular_session
OUT=Path(__file__).resolve().parent/'research_latest.json';_LOCK=threading.Lock();_STARTED=False
_STATE={'running':False,'lastRun':None,'lastError':None,'intervalMin':60,'historyAuto':True,'historyDays':60,'historyCodes':40,'historyMinIntervalMin':180,'lastHistoryStart':None,'phase':'idle','liveSessionPriority':True}
def _fmt(x,d=2):
 try:return f'{float(x):.{d}f}'
 except:return '-'
def _snapshot_universe():
 codes=list(dict.fromkeys(getattr(collector,'watchlist',[]) or []));now=datetime.now();day=now.date().isoformat()
 try:
  with sqlite3.connect(DB_PATH,timeout=10) as c:
   c.execute('CREATE TABLE IF NOT EXISTS universe_snapshots(snapshot_date TEXT NOT NULL,captured_at TEXT NOT NULL,code TEXT NOT NULL,source TEXT NOT NULL DEFAULT \'collector_watchlist\',PRIMARY KEY(snapshot_date,code))')
   for code in codes:c.execute('INSERT OR IGNORE INTO universe_snapshots(snapshot_date,captured_at,code,source) VALUES(?,?,?,?)',(day,now.isoformat(timespec='seconds'),str(code),'collector_watchlist'))
   total=c.execute('SELECT COUNT(*) FROM universe_snapshots').fetchone()[0];days=c.execute('SELECT COUNT(DISTINCT snapshot_date) FROM universe_snapshots').fetchone()[0]
  return {'ok':True,'todayCodes':len(codes),'snapshotRows':int(total),'snapshotDays':int(days)}
 except Exception as e:return {'ok':False,'error':f'{type(e).__name__}: {e}','todayCodes':len(codes)}
def _summary(p,r,q,hs,us):
 b=p.get('best') or {};rd=p.get('readiness') or {};full=b.get('full') or {};oos=b.get('oos') or {};wf=r.get('walkForward') or {};lock=r.get('lockbox') or {};counts=q.get('counts') or {}
 lines=[f"자동 연구 데이터 {hs.get('writtenBars',0):,}개 갱신, Universe Snapshot {us.get('snapshotDays',0)}일/{us.get('snapshotRows',0)}행. 데이터 품질 GOOD/PARTIAL/BAD={counts.get('GOOD',0)}/{counts.get('PARTIAL',0)}/{counts.get('BAD',0)}."]
 if b:lines.append(f"선두 {b.get('strategy')} + {b.get('exit')}: {full.get('trades',0)}건, PF {_fmt(full.get('profitFactor'))}, 기대값 {_fmt(full.get('expectancyPct'),3)}%, OOS PF {_fmt(oos.get('profitFactor'))}.")
 lines.append(f"Walk-Forward {wf.get('folds',0)}구간 중 양수 {wf.get('positiveFolds',0)}구간. 개발에 사용하지 않은 Final Lockbox PF {_fmt(lock.get('profitFactor'))}, 기대값 {_fmt(lock.get('expectancyPct'),3)}%.")
 one=r.get('oneMinuteExitValidation') or {};lines.append('1분봉 Exit 검증: '+('완료' if one.get('ready') else '대기 — '+str(one.get('reason','1분봉 데이터 필요'))))
 lines.append(f"실전 준비도 {rd.get('score',0)}/100. 최종판정: {'강건성 연구 게이트 통과' if r.get('pass') else '실전 승격 금지'}. Control v0.8.0 LOCKED · 자동 전략변경 OFF · REAL ORDER OFF.")
 return '\n\n'.join(lines)
def _history_due():
 last=_STATE.get('lastHistoryStart')
 if not last:return True
 try:return (datetime.now()-datetime.fromisoformat(last)).total_seconds()>=_STATE['historyMinIntervalMin']*60
 except:return True
def _accumulate_and_wait():
 hs=history_status()
 if regular_session():_STATE['phase']='live-session-history-deferred';return hs
 _STATE['phase']='historical-accumulation'
 if not hs.get('running') and _history_due():
  r=history_start(_STATE['historyDays'],_STATE['historyCodes']);_STATE['lastHistoryStart']=datetime.now().isoformat(timespec='seconds')
  if not r.get('ok'):return history_status()
 elif not hs.get('running'):return hs
 deadline=time.time()+45*60
 while time.time()<deadline:
  if regular_session():return history_status()
  hs=history_status()
  if not hs.get('running'):return hs
  time.sleep(10)
 return history_status()
def run_once():
 with _LOCK:
  if _STATE['running']:return {'ok':False,'error':'research already running'}
  _STATE['running']=True
 try:
  hs=_accumulate_and_wait() if _STATE['historyAuto'] else history_status();_STATE['phase']='universe-snapshot';us=_snapshot_universe();_STATE['phase']='data-quality';q=data_quality_audit();_STATE['phase']='strategy-backtest';s=run_lab(40);_STATE['phase']='market-research';m=run_market_lab(80);_STATE['phase']='profitability';p=run_profitability_lab(40);_STATE['phase']='robust-validation';r=run_robust_validation(40)
  data={'ok':True,'generatedAt':datetime.now().isoformat(timespec='seconds'),'summary':_summary(p,r,q,hs,us),'history':hs,'dataQuality':q,'universeSnapshot':us,'market':m,'strategy':s,'profitability':p,'robustValidation':r,'safety':{'control':'v0.8.0 LOCKED','liveMutation':False,'realOrder':False,'liveSessionPriority':True}}
  OUT.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');_STATE.update({'lastRun':data['generatedAt'],'lastError':None,'phase':'idle'});return data
 except Exception as e:_STATE.update({'lastError':f'{type(e).__name__}: {e}','phase':'error'});return {'ok':False,'error':_STATE['lastError']}
 finally:_STATE['running']=False
def latest():
 if OUT.exists():
  try:return json.loads(OUT.read_text(encoding='utf-8'))
  except:pass
 return {'ok':True,'generatedAt':None,'summary':'자동 연구 준비 중','safety':{'control':'v0.8.0 LOCKED','liveMutation':False,'realOrder':False}}
def status():return dict(_STATE)
def _loop():
 time.sleep(20)
 while True:run_once();time.sleep(_STATE['intervalMin']*60)
def start():
 global _STARTED
 if _STARTED:return
 _STARTED=True;threading.Thread(target=_loop,daemon=True,name='research-daemon').start()
