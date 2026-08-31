"""Automatic research pipeline: accumulate -> backtest/labs -> final prose.
Research/data only. Never mutates Control/live rules and never sends orders.
"""
import json, threading, time
from datetime import datetime
from pathlib import Path
from market_lab import run_market_lab
from strategy_lab import run_lab
from historical_accumulator import start as history_start, status as history_status

OUT=Path(__file__).resolve().parent/'research_latest.json'
_LOCK=threading.Lock()
_STATE={'running':False,'lastRun':None,'lastError':None,'intervalMin':60,'historyAuto':True,'historyDays':30,'historyCodes':40,'phase':'idle'}

def _fmt(x,d=2):
 try:return f'{float(x):.{d}f}'
 except:return '-'

def _summary(m,s,hs):
 f=m.get('failureAnalysis',{}); rg=m.get('marketRegime',{}).get('groups',{}); su=m.get('surgeDiscovery',{})
 strategies=s.get('strategies',[]); v2=next((x for x in strategies if x.get('id')=='cross_trend_v2'),{})
 ranked=sorted(strategies,key=lambda x:(float(x.get('expectancyPct') or -999),float(x.get('profitFactor') or 0)),reverse=True)
 best=ranked[0] if ranked else {}
 lines=[]
 lines.append(f"자동 데이터 축적: 최근 {hs.get('requestedDays',30)}거래일 × 최대 {hs.get('requestedCodes',40)}종목을 점검했습니다. 새/갱신 5분봉 {hs.get('writtenBars',0):,}개, 캐시/건너뜀 {hs.get('skippedBars',0):,}개입니다.")
 lines.append(f"Cross Trend 2.0은 {v2.get('trades',0)}건, 승률 {_fmt(v2.get('winRate'))}%, PF {_fmt(v2.get('profitFactor'))}, 기대값 {_fmt(v2.get('expectancyPct'),3)}%, MDD {_fmt(v2.get('maxDrawdownPct'))}%입니다.")
 if best: lines.append(f"현재 비교 전략 중 기대값 기준 선두는 {best.get('name') or best.get('id')}이며 PF {_fmt(best.get('profitFactor'))}, 기대값 {_fmt(best.get('expectancyPct'),3)}%입니다. 표본·비용·MDD를 함께 통과하기 전에는 승격하지 않습니다.")
 flags=f.get('lossFlags',{}); top=sorted(flags.items(),key=lambda kv:kv[1],reverse=True)[:3]
 if top: lines.append('통과 후 손실의 주요 관찰 신호는 '+', '.join(f'{k} {v}회' for k,v in top)+'입니다. 원인 확정이 아니라 다음 Challenger 후보조건 탐색용입니다.')
 if rg:
  txt=[]
  for k in ('NORMAL','CAUTION','RED'):
   x=rg.get(k,{})
   txt.append(f"{k} PF {_fmt(x.get('profitFactor'))}/기대값 {_fmt(x.get('expectancyPct'),3)}%")
  lines.append('시장상태별 결과는 '+', '.join(txt)+'입니다. 실제 지수 데이터가 충분해지기 전에는 시장상태만으로 실전 거래를 자동 차단하지 않습니다.')
 lines.append(f"Early Surge는 실제 급등 {su.get('actualSurges',0)}건, 사전후보 {su.get('candidates',0)}건, 적중 {su.get('hits',0)}건입니다. 장초 데이터가 쌓일수록 사전선별 성능을 자동 재평가합니다.")
 pf=float(best.get('profitFactor') or 0); ex=float(best.get('expectancyPct') or 0); n=int(best.get('trades') or 0)
 if pf>1 and ex>0 and n>=200: verdict='연구 게이트 1차 통과 — 추가 Shadow/기간외 검증 권고'
 else: verdict='아직 실전 승격 금지'
 lines.append(f"최종판정: {verdict}. Control v0.8.0 LOCKED · 자동 전략변경 OFF · REAL ORDER OFF.")
 return '\n\n'.join(lines)

def _accumulate_and_wait():
 _STATE['phase']='historical-accumulation'
 hs=history_status()
 if not hs.get('running'): history_start(_STATE['historyDays'],_STATE['historyCodes'])
 deadline=time.time()+45*60
 while time.time()<deadline:
  hs=history_status()
  if not hs.get('running'): return hs
  time.sleep(10)
 return history_status()

def run_once():
 with _LOCK:
  if _STATE['running']: return {'ok':False,'error':'research already running'}
  _STATE['running']=True
 try:
  hs=_accumulate_and_wait() if _STATE['historyAuto'] else history_status()
  _STATE['phase']='strategy-backtest'
  s=run_lab(40)
  _STATE['phase']='market-failure-surge'
  m=run_market_lab(40)
  data={'ok':True,'generatedAt':datetime.now().isoformat(timespec='seconds'),'summary':_summary(m,s,hs),'history':hs,'market':m,'strategy':s,'pipeline':['historical-5m','strategy-backtest','failure-analysis','market-regime','early-surge','final-summary'],'safety':{'control':'v0.8.0 LOCKED','liveMutation':False,'realOrder':False}}
  OUT.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
  _STATE.update({'lastRun':data['generatedAt'],'lastError':None,'phase':'idle'})
  return data
 except Exception as e:
  _STATE.update({'lastError':f'{type(e).__name__}: {e}','phase':'error'}); return {'ok':False,'error':_STATE['lastError']}
 finally:_STATE['running']=False

def latest():
 if OUT.exists():
  try:return json.loads(OUT.read_text(encoding='utf-8'))
  except:pass
 return {'ok':True,'generatedAt':None,'summary':'자동 데이터 축적과 백테스트를 준비 중입니다. 완료되면 이곳에 최종 결론만 표시됩니다.','safety':{'control':'v0.8.0 LOCKED','liveMutation':False,'realOrder':False}}
def status():return dict(_STATE)
def _loop():
 time.sleep(20)
 while True:
  run_once(); time.sleep(_STATE['intervalMin']*60)
def start():threading.Thread(target=_loop,daemon=True,name='research-daemon').start()
