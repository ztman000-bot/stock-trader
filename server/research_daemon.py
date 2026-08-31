"""Automatic shadow research runner. Never mutates Control/live rules."""
import json, threading, time
from datetime import datetime
from pathlib import Path
from market_lab import run_market_lab
from strategy_lab import run_lab

OUT=Path(__file__).resolve().parent/'research_latest.json'
_LOCK=threading.Lock()
_STATE={'running':False,'lastRun':None,'lastError':None,'intervalMin':60}

def _fmt(x,d=2):
 try:return f'{float(x):.{d}f}'
 except:return '-'

def _summary(m,s):
 f=m.get('failureAnalysis',{}); rg=m.get('marketRegime',{}).get('groups',{}); su=m.get('surgeDiscovery',{})
 strategies=s.get('strategies',[]); v2=next((x for x in strategies if x.get('id')=='cross_trend_v2'),{})
 lines=[]
 lines.append(f"Cross Trend 2.0은 {v2.get('trades',0)}건, 승률 {_fmt(v2.get('winRate'))}%, PF {_fmt(v2.get('profitFactor'))}, 기대값 {_fmt(v2.get('expectancyPct'),3)}%, MDD {_fmt(v2.get('maxDrawdownPct'))}%입니다.")
 flags=f.get('lossFlags',{}); top=sorted(flags.items(),key=lambda kv:kv[1],reverse=True)[:3]
 if top: lines.append('통과 후 손실의 주요 관찰 신호는 '+', '.join(f'{k} {v}회' for k,v in top)+'입니다. 이는 원인 확정이 아니라 2.1 후보조건 탐색용입니다.')
 if rg:
  txt=[]
  for k in ('NORMAL','CAUTION','RED'):
   x=rg.get(k,{})
   txt.append(f"{k} PF {_fmt(x.get('profitFactor'))}/기대값 {_fmt(x.get('expectancyPct'),3)}%")
  lines.append('시장상태별 결과는 '+', '.join(txt)+'입니다. 실제 KOSPI/KOSDAQ 지수 수집 전에는 breadth proxy이므로 실전 차단 규칙으로 자동 승격하지 않습니다.')
 lines.append(f"Early Surge는 실제 급등 {su.get('actualSurges',0)}건, 사전후보 {su.get('candidates',0)}건, 적중 {su.get('hits',0)}건입니다. 09:00~09:30 데이터가 축적될수록 사전선별 성능을 재평가합니다.")
 pf=float(v2.get('profitFactor') or 0); ex=float(v2.get('expectancyPct') or 0)
 verdict='아직 실전 승격 금지' if pf<=1 or ex<=0 else '1차 연구 게이트 통과 — 추가 Shadow 검증 필요'
 lines.append(f"최종판정: {verdict}. Control v0.8.0은 LOCKED이며 연구 결과가 실전 규칙을 자동 변경하지 않습니다.")
 return '\n\n'.join(lines)

def run_once():
 with _LOCK:
  if _STATE['running']: return {'ok':False,'error':'research already running'}
  _STATE['running']=True
 try:
  m=run_market_lab(40); s=run_lab(40)
  data={'ok':True,'generatedAt':datetime.now().isoformat(timespec='seconds'),'summary':_summary(m,s),'market':m,'strategy':s,'safety':{'control':'v0.8.0 LOCKED','liveMutation':False,'realOrder':False}}
  OUT.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
  _STATE.update({'lastRun':data['generatedAt'],'lastError':None})
  return data
 except Exception as e:
  _STATE['lastError']=f'{type(e).__name__}: {e}'; return {'ok':False,'error':_STATE['lastError']}
 finally:_STATE['running']=False

def latest():
 if OUT.exists():
  try:return json.loads(OUT.read_text(encoding='utf-8'))
  except:pass
 return {'ok':True,'generatedAt':None,'summary':'아직 자동 연구 결과가 없습니다. 서버가 자동 연구를 수행하면 이곳에 최종 결과가 표시됩니다.','safety':{'control':'v0.8.0 LOCKED','liveMutation':False,'realOrder':False}}

def status():return dict(_STATE)
def _loop():
 time.sleep(20)
 while True:
  run_once(); time.sleep(_STATE['intervalMin']*60)
def start():
 threading.Thread(target=_loop,daemon=True,name='research-daemon').start()
