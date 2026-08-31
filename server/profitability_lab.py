"""Profitability Lab v0.17.0.
Research only: exit optimization + Cross Trend 2.1 challengers + chronological holdout + execution stress.
Never mutates Control/live rules and never sends orders.
"""
from math import sqrt
from statistics import mean
from backtest_engine import _dt, available_codes
from strategy_lab import _signals
from market_lab import _feature

COMMISSION=.0001
SELL_TAX=.0015
BASE_SLIPPAGE=.0005

EXIT_CONFIGS=[
 {'id':'control_exit','name':'기존 Exit','stop':.010,'trail_act':.015,'trail':.008,'be_act':.008,'be_lock':.0035,'time_bars':0,'min_mfe':0,'atr':False},
 {'id':'fast_fail','name':'Fast-Fail','stop':.007,'trail_act':.014,'trail':.0065,'be_act':.007,'be_lock':.0025,'time_bars':6,'min_mfe':.003,'atr':False},
 {'id':'balanced','name':'Balanced','stop':.008,'trail_act':.016,'trail':.007,'be_act':.008,'be_lock':.0030,'time_bars':8,'min_mfe':.0035,'atr':False},
 {'id':'let_winners_run','name':'Let Winners Run','stop':.008,'trail_act':.020,'trail':.008,'be_act':.010,'be_lock':.0035,'time_bars':10,'min_mfe':.004,'atr':False},
 {'id':'atr_adaptive','name':'ATR Adaptive','stop':.009,'trail_act':.017,'trail':.0075,'be_act':.008,'be_lock':.0030,'time_bars':8,'min_mfe':.0035,'atr':True},
]

FILTERS=[
 {'id':'cross_v2','name':'Cross Trend 2.0','rvol':0,'ema':-999,'vwap':999,'cutoff':9999},
 {'id':'cross_v21_bal','name':'Cross Trend 2.1 Balanced','rvol':1.60,'ema':.12,'vwap':1.20,'cutoff':14*60},
 {'id':'cross_v21_strict','name':'Cross Trend 2.1 Strict','rvol':2.00,'ema':.18,'vwap':.90,'cutoff':13*60+30},
]

def _metrics(xs):
 n=len(xs);w=[x for x in xs if x>0];l=[x for x in xs if x<=0];gp=sum(w);gl=abs(sum(l));pf=gp/gl if gl else (999 if gp else 0);eq=peak=mdd=0
 for x in xs:eq+=x;peak=max(peak,eq);mdd=min(mdd,eq-peak)
 return {'trades':n,'winRate':round(len(w)/n*100,2) if n else 0,'profitFactor':round(pf,3),'expectancyPct':round(mean(xs),4) if xs else 0,'maxDrawdownPct':round(mdd,3)}

def _atr_pct(rows,i,p=14):
 if i<2:return None
 a=max(1,i-p);sub=rows[a:i+1];trs=[]
 for x,y in zip(sub[:-1],sub[1:]):
  ph=float(x['close']);h=float(y['high']);l=float(y['low']);trs.append(max(h-l,abs(h-ph),abs(l-ph)))
 if not trs:return None
 price=float(rows[i]['open']);return mean(trs)/price if price>0 else None

def _passes(feat,f):
 if not feat:return False
 hm=int(feat['time'][:2])*60+int(feat['time'][3:])
 return feat['rvol']>=f['rvol'] and feat['emaSpreadPct']>=f['ema'] and feat['vwapDistPct']<=f['vwap'] and hm<f['cutoff']

def _pnl(rows,i,cfg,slippage=BASE_SLIPPAGE,late_bars=0):
 i=i+max(0,int(late_bars))
 if i>=len(rows):return None
 entry_day=_dt(rows[i]['bucket']).date();entry=float(rows[i]['open'])*(1+slippage);peak=entry;exitp=None
 stop=cfg['stop']
 if cfg.get('atr'):
  ap=_atr_pct(rows,i)
  if ap is not None:stop=max(.006,min(.012,ap*1.25))
 for j in range(i,min(len(rows),i+78)):
  r=rows[j];dt=_dt(r['bucket']);d=dt.date();hm=dt.hour*60+dt.minute
  if d!=entry_day:break
  hi=float(r['high']);lo=float(r['low']);cl=float(r['close']);peak=max(peak,hi);mfe=peak/entry-1
  # Conservative intrabar order: stop/protection is checked before favorable exits.
  if lo<=entry*(1-stop):exitp=entry*(1-stop);break
  if peak>=entry*(1+cfg['be_act']) and lo<=entry*(1+cfg['be_lock']):exitp=entry*(1+cfg['be_lock']);break
  if peak>=entry*(1+cfg['trail_act']) and lo<=peak*(1-cfg['trail']):exitp=peak*(1-cfg['trail']);break
  bars_held=j-i+1
  if cfg['time_bars'] and bars_held>=cfg['time_bars'] and mfe<cfg['min_mfe']:exitp=cl;break
  if hm>=915:exitp=cl;break
 if exitp is None:
  j=min(len(rows)-1,i+77);exitp=float(rows[j]['close'])
 net=(exitp*(1-slippage)/entry)-1-(2*COMMISSION)-SELL_TAX
 return net*100

def _candidates(max_codes=40):
 out=[]
 for code in [x['code'] for x in available_codes()[:max_codes]]:
  rows,sigs=_signals(code,'cross_trend_v2');used=set()
  for i in sigs:
   d=_dt(rows[i]['bucket']).date()
   if d in used:continue
   used.add(d);feat=_feature(rows,i)
   if feat:out.append({'code':code,'date':d,'i':i,'rows':rows,'feat':feat})
 return sorted(out,key=lambda x:(x['date'],x['code']))

def _split(cands):
 dates=sorted({x['date'] for x in cands})
 if len(dates)<5:return set(dates),set()
 k=max(1,min(len(dates)-1,int(len(dates)*.70)));return set(dates[:k]),set(dates[k:])

def _eval(cands,f,cfg,dates=None,slippage=BASE_SLIPPAGE,late_bars=0):
 xs=[]
 for x in cands:
  if dates is not None and x['date'] not in dates:continue
  if not _passes(x['feat'],f):continue
  p=_pnl(x['rows'],x['i'],cfg,slippage,late_bars)
  if p is not None:xs.append(p)
 return _metrics(xs)

def _readiness(full,oos,stress):
 n=full['trades'];pf=full['profitFactor'];ex=full['expectancyPct'];mdd=abs(full['maxDrawdownPct']);op=oos['profitFactor'];oe=oos['expectancyPct'];sp=stress['profitFactor'];se=stress['expectancyPct']
 score=0
 score+=min(20,n/300*20)
 score+=max(0,min(20,(pf-1)/.20*20))
 score+=max(0,min(15,ex/.15*15))
 score+=max(0,min(20,10*(op-1)/.20+10*(oe/.10)))
 score+=max(0,min(15,7.5*(sp-1)/.15+7.5*(se/.10)))
 score+=max(0,min(10,(15-mdd)/10*10))
 gate=n>=200 and pf>=1.20 and ex>0 and oos['trades']>=40 and op>1 and oe>0 and sp>=1 and se>=0
 return {'score':round(max(0,min(100,score)),1),'pass':bool(gate),'gate':'trades>=200, PF>=1.20, expectancy>0, OOS PF>1/expectancy>0, stress PF>=1/expectancy>=0'}

def run_profitability_lab(max_codes=40):
 cands=_candidates(max(10,min(int(max_codes),100)));train_dates,oos_dates=_split(cands);rows=[]
 for f in FILTERS:
  for cfg in EXIT_CONFIGS:
   tr=_eval(cands,f,cfg,train_dates)
   if tr['trades']<8:continue
   objective=tr['expectancyPct']*min(1,sqrt(tr['trades']/40))+max(-.05,min(.05,(tr['profitFactor']-1)*.05))
   rows.append({'filter':f,'exit':cfg,'train':tr,'objective':objective})
 rows.sort(key=lambda x:(x['objective'],x['train']['profitFactor'],x['train']['trades']),reverse=True);best=rows[0] if rows else None
 if not best:return {'ok':True,'labVersion':'0.17.0','researchOnly':True,'best':None,'readiness':{'score':0,'pass':False},'warning':'검증 가능한 거래가 부족합니다.'}
 f=best['filter'];cfg=best['exit'];full=_eval(cands,f,cfg);oos=_eval(cands,f,cfg,oos_dates) if oos_dates else _metrics([]);stress=_eval(cands,f,cfg,None,BASE_SLIPPAGE*2,0);late=_eval(cands,f,cfg,None,BASE_SLIPPAGE,1);rd=_readiness(full,oos,stress)
 top=[]
 for x in rows[:5]:top.append({'strategy':x['filter']['name'],'exit':x['exit']['name'],'train':x['train']})
 return {'ok':True,'labVersion':'0.17.0','controlStrategy':'v0.8.0 LOCKED','researchOnly':True,'liveRuleAutoMutation':False,'realOrderEnabled':False,'candidateTrades':len(cands),'trainDays':len(train_dates),'oosDays':len(oos_dates),'best':{'strategyId':f['id'],'strategy':f['name'],'exitId':cfg['id'],'exit':cfg['name'],'full':full,'oos':oos,'stress2xSlippage':stress,'oneBarLate':late},'topTrain':top,'readiness':rd,'notes':['Cross 2.1은 RVOL/EMA확산/VWAP추격/늦은진입 조건을 Challenger로만 비교합니다.','최적 조합은 앞 70% 날짜에서만 선택하고 뒤 30% 날짜를 OOS로 평가합니다.','2배 슬리피지와 1봉 늦은 체결에서도 성과를 별도 확인합니다.','실전 승격 기준 미달 시 Control은 변경하지 않습니다.']}
