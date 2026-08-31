"""Automatic research pipeline v0.17.0.
Accumulate -> universe snapshot -> strategy/market labs -> profitability/OOS/stress -> final prose.
Research/data only. Never mutates Control/live rules and never sends orders.
"""
import json, sqlite3, threading, time
from datetime import datetime
from pathlib import Path
from market_lab import run_market_lab
from strategy_lab import run_lab
from profitability_lab import run_profitability_lab
from historical_accumulator import start as history_start, status as history_status
from collector import DB_PATH, collector

OUT=Path(__file__).resolve().parent/'research_latest.json'
_LOCK=threading.Lock();_STARTED=False
_STATE={'running':False,'lastRun':None,'lastError':None,'intervalMin':60,'historyAuto':True,'historyDays':60,'historyCodes':40,'historyMinIntervalMin':180,'lastHistoryStart':None,'phase':'idle'}

def _fmt(x,d=2):
 try:return f'{float(x):.{d}f}'
 except:return '-'

def _snapshot_universe():
 codes=list(dict.fromkeys(getattr(collector,'watchlist',[]) or []));now=datetime.now();day=now.date().isoformat()
 try:
  with sqlite3.connect(DB_PATH,timeout=10) as c:
   c.execute('''CREATE TABLE IF NOT EXISTS universe_snapshots(snapshot_date TEXT NOT NULL,captured_at TEXT NOT NULL,code TEXT NOT NULL,source TEXT NOT NULL DEFAULT 'collector_watchlist',PRIMARY KEY(snapshot_date,code))''')
   for code in codes:c.execute('INSERT OR IGNORE INTO universe_snapshots(snapshot_date,captured_at,code,source) VALUES(?,?,?,?)',(day,now.isoformat(timespec='seconds'),str(code),'collector_watchlist'))
   total=c.execute('SELECT COUNT(*) FROM universe_snapshots').fetchone()[0];days=c.execute('SELECT COUNT(DISTINCT snapshot_date) FROM universe_snapshots').fetchone()[0]
  return {'ok':True,'todayCodes':len(codes),'snapshotRows':int(total),'snapshotDays':int(days)}
 except Exception as e:return {'ok':False,'error':f'{type(e).__name__}: {e}','todayCodes':len(codes)}

def _summary(m,s,p,hs,us):
 f=m.get('failureAnalysis',{});mr=m.get('marketRegime',{});rg=mr.get('groups',{});su=m.get('surgeDiscovery',{});pb=p.get('best') or {};rd=p.get('readiness') or {};full=pb.get('full') or {};oos=pb.get('oos') or {};stress=pb.get('stress2xSlippage') or {};late=pb.get('oneBarLate') or {}
 lines=[]
 lines.append(f"자동 데이터 축적: 최근 {hs.get('requestedDays',_STATE['historyDays'])}거래일 × 최대 {hs.get('requestedCodes',_STATE['historyCodes'])}종목을 점검합니다. 이번 수집 새/갱신 5분봉 {hs.get('writtenBars',0):,}개, 캐시/건너뜀 {hs.get('skippedBars',0):,}개입니다. 과거 시점의 종목선택 편향을 줄이기 위한 Universe Snapshot은 {us.get('snapshotDays',0)}일/{us.get('snapshotRows',0)}행 축적 중입니다.")
 if pb:
  lines.append(f"수익성 연구 선두는 {pb.get('strategy')} + {pb.get('exit')}이며 시장조건은 '{pb.get('marketMode')}', 급등 사전선별은 '{pb.get('surgeMode')}'입니다. 전체 {full.get('trades',0)}건, 승률 {_fmt(full.get('winRate'))}%, PF {_fmt(full.get('profitFactor'))}, 기대값 {_fmt(full.get('expectancyPct'),3)}%, MDD {_fmt(full.get('maxDrawdownPct'))}%입니다.")
  lines.append(f"기간외(OOS) 검증은 {oos.get('trades',0)}건, PF {_fmt(oos.get('profitFactor'))}, 기대값 {_fmt(oos.get('expectancyPct'),3)}%입니다. 2배 슬리피지 스트레스는 PF {_fmt(stress.get('profitFactor'))}/기대값 {_fmt(stress.get('expectancyPct'),3)}%, 1봉 늦은 체결은 PF {_fmt(late.get('profitFactor'))}/기대값 {_fmt(late.get('expectancyPct'),3)}%입니다.")
  lines.append(f"실전 준비도는 {rd.get('score',0)}/100입니다. 승격 게이트: {rd.get('gate','표본/OOS/비용 스트레스 검증 필요')}.")
 flags=f.get('lossFlags',{});top=sorted(flags.items(),key=lambda kv:kv[1],reverse=True)[:3]
 if top:lines.append('손실 거래에서 반복 관찰되는 후보 신호는 '+', '.join(f'{k} {v}회' for k,v in top)+'입니다. Cross Trend 2.1은 RVOL·EMA 확산·VWAP 추격·늦은 진입을 Challenger로만 시험하며 Control에는 자동 반영하지 않습니다.')
 if rg:
  txt=[]
  for k in ('NORMAL','CAUTION','RED'):
   x=rg.get(k,{})
   txt.append(f"{k} PF {_fmt(x.get('profitFactor'))}/기대값 {_fmt(x.get('expectancyPct'),3)}%")
  lines.append('시장상태별 결과는 '+', '.join(txt)+f"입니다. 현재 방식은 {mr.get('method','BREADTH_PROXY')}이며 KODEX200/KODEX KOSDAQ150과 breadth를 결합한 보조 시장지표입니다. 실제 지수 자체가 아니므로 실전 차단 규칙으로 자동 승격하지 않습니다.")
 lines.append(f"Early Surge는 {m.get('codesTested',0)}종목 범위에서 실제 급등 {su.get('actualSurges',0)}건, 사전후보 {su.get('candidates',0)}건, 적중 {su.get('hits',0)}건입니다. Profitability Lab의 Early Surge 특징 커버리지는 {p.get('surgeFeatureCoverage',0)}%이며 장초 데이터와 Universe Snapshot이 쌓일수록 자동 재평가합니다.")
 verdict='연구 게이트 1차 통과 — NH 모의주문/추가 기간외 검증 단계로 이동 가능' if rd.get('pass') else '아직 실전 승격 금지'
 lines.append(f"최종판정: {verdict}. Control v0.8.0 LOCKED · 자동 전략변경 OFF · REAL ORDER OFF.")
 return '\n\n'.join(lines)

def _history_due():
 last=_STATE.get('lastHistoryStart')
 if not last:return True
 try:return (datetime.now()-datetime.fromisoformat(last)).total_seconds()>=_STATE['historyMinIntervalMin']*60
 except:return True

def _accumulate_and_wait():
 _STATE['phase']='historical-accumulation';hs=history_status()
 if not hs.get('running') and _history_due():
  result=history_start(_STATE['historyDays'],_STATE['historyCodes']);_STATE['lastHistoryStart']=datetime.now().isoformat(timespec='seconds')
  if not result.get('ok'):return history_status()
 elif not hs.get('running'):return hs
 deadline=time.time()+45*60
 while time.time()<deadline:
  hs=history_status()
  if not hs.get('running'):return hs
  time.sleep(10)
 return history_status()

def run_once():
 with _LOCK:
  if _STATE['running']:return {'ok':False,'error':'research already running'}
  _STATE['running']=True
 try:
  hs=_accumulate_and_wait() if _STATE['historyAuto'] else history_status()
  _STATE['phase']='universe-snapshot';us=_snapshot_universe()
  _STATE['phase']='strategy-backtest';s=run_lab(40)
  _STATE['phase']='market-failure-surge';m=run_market_lab(80)
  _STATE['phase']='profitability-oos-stress';p=run_profitability_lab(40)
  data={'ok':True,'generatedAt':datetime.now().isoformat(timespec='seconds'),'summary':_summary(m,s,p,hs,us),'history':hs,'universeSnapshot':us,'market':m,'strategy':s,'profitability':p,'pipeline':['historical-5m','market-etf-proxies','universe-snapshot','strategy-backtest','failure-analysis','market-regime','early-surge','exit-optimization','cross-v2.1-challengers','regime-gate-research','surge-selection-research','chronological-oos','slippage-stress','late-fill-stress','readiness-score','final-summary'],'safety':{'control':'v0.8.0 LOCKED','liveMutation':False,'realOrder':False}}
  OUT.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');_STATE.update({'lastRun':data['generatedAt'],'lastError':None,'phase':'idle'});return data
 except Exception as e:
  _STATE.update({'lastError':f'{type(e).__name__}: {e}','phase':'error'});return {'ok':False,'error':_STATE['lastError']}
 finally:_STATE['running']=False

def latest():
 if OUT.exists():
  try:return json.loads(OUT.read_text(encoding='utf-8'))
  except:pass
 return {'ok':True,'generatedAt':None,'summary':'자동 데이터 축적·수익성 최적화·기간외 검증을 준비 중입니다. 완료되면 이곳에 최종 결론만 표시됩니다.','safety':{'control':'v0.8.0 LOCKED','liveMutation':False,'realOrder':False}}
def status():return dict(_STATE)
def _loop():
 time.sleep(20)
 while True:run_once();time.sleep(_STATE['intervalMin']*60)
def start():
 global _STARTED
 if _STARTED:return
 _STARTED=True;threading.Thread(target=_loop,daemon=True,name='research-daemon').start()
