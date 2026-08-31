"""Strategy Lab v0.16.3. Research only; Control v0.8.0 remains locked."""
from backtest_engine import _load_rows,_dt,available_codes,_ema,_rsi,_wilder_adx,_vwap,run_backtest

def _metrics(pnls):
 n=len(pnls);wins=[x for x in pnls if x>0];losses=[x for x in pnls if x<=0];gp=sum(wins);gl=abs(sum(losses));pf=gp/gl if gl else (999 if gp else 0);eq=peak=mdd=0
 for x in pnls:eq+=x;peak=max(peak,eq);mdd=min(mdd,eq-peak)
 return {'trades':n,'winRate':round(len(wins)/n*100,2) if n else 0,'profitFactor':round(pf,3),'expectancyPct':round(sum(pnls)/n,4) if n else 0,'maxDrawdownPct':round(mdd,3)}

def _signals(code,kind):
 rows=_load_rows(code);out=[];hist=[];session=[];day=None;prev_fast=None;prev_slow=None
 for i,r in enumerate(rows[:-1]):
  dt=_dt(r['bucket']);d=dt.date();hm=dt.hour*60+dt.minute
  if d!=day:day=d;session=[]
  hist.append(r);session.append(r)
  if len(hist)<130:continue
  cr=hist[-160:];cl=[float(x['close']) for x in cr];price=float(r['close']);e5=_ema(cl[-50:],5);ma15=sum(cl[-15:])/15;e10=_ema(cl[-80:],10);ma120=sum(cl[-120:])/120;rs=_rsi(cl);adx,pdi,mdi=_wilder_adx(cr);vw=_vwap(session) if session else None;prior=cr[-7:-1]
  cross=prev_fast is not None and prev_slow is not None and prev_fast<=prev_slow and e5>ma15;prev_fast=e5;prev_slow=ma15
  if len(session)<6 or not 570<=hm<890 or len(prior)<6 or not all(x is not None for x in (rs,adx,pdi,mdi,vw)):continue
  av=sum(int(x['volume']) for x in prior)/6;vr=int(r['volume'])/av if av else 0;rh=max(float(x['high']) for x in prior);oh=max(float(x['high']) for x in session[:6]);bull=e5>ma15 and e10>ma120 and price>vw and pdi>mdi
  buy=False
  if kind=='cross_trend':buy=cross and bull and adx>=20 and 50<=rs<=76 and vr>=1.05
  elif kind=='orb_cross':buy=bull and price>oh and vr>=1.5 and adx>=22 and 53<=rs<=78 and (cross or abs(e5-ma15)/price<.0025)
  elif kind=='orb_rvol':buy=e10>ma120 and price>vw and pdi>mdi and price>oh and vr>=1.8 and 55<=rs<=78 and adx>=22
  elif kind=='vwap_pullback':buy=e5>ma15 and e10>ma120 and pdi>mdi and adx>=20 and 50<=rs<=72 and float(r['low'])<=vw*1.003 and price>vw and vr>=1
  elif kind=='momentum_adx':buy=bull and price>rh and vr>=1.35 and 55<=rs<=75 and adx>=25
  elif kind=='first_pullback':buy=bull and adx>=22 and 52<=rs<=72 and price>=float(prior[-1]['high']) and vr>=1.1
  if buy and i+1<len(rows):out.append(i+1)
 return rows,out

def _exit_trade(rows,i,mode='control',hold_days=0):
 entry=float(rows[i]['open'])*1.0005;peak=entry;entry_day=_dt(rows[i]['bucket']).date();days=[];exitp=None;max_hold=max(0,int(hold_days));
 for j in range(i,min(len(rows),i+420)):
  r=rows[j];dt=_dt(r['bucket']);d=dt.date();hm=dt.hour*60+dt.minute
  if d not in days:days.append(d)
  day_index=max(0,len(days)-1);hi=float(r['high']);lo=float(r['low']);cl=float(r['close']);peak=max(peak,hi)
  hist=rows[max(0,j-130):j+1];cls=[float(x['close']) for x in hist];e5=_ema(cls[-50:],5) if len(cls)>=15 else None;ma15=sum(cls[-15:])/15 if len(cls)>=15 else None;e10=_ema(cls[-80:],10) if len(cls)>=120 else None;ma120=sum(cls[-120:])/120 if len(cls)>=120 else None
  # Overnight gap risk is represented by the next session open before intraday stops.
  if j>i and d!=_dt(rows[j-1]['bucket']).date():
   op=float(r['open']);
   if op<=entry*.99:exitp=op;break
  if lo<=entry*.99:exitp=entry*.99;break
  if peak>=entry*1.015 and lo<=peak*.992:exitp=peak*.992;break
  if peak>=entry*1.008 and lo<=entry*1.0035:exitp=entry*1.0035;break
  trend_broken=bool(e5 is not None and ma15 is not None and e5<ma15) or bool(e10 is not None and ma120 is not None and e10<ma120)
  if hold_days>0 and trend_broken and j>i:exitp=cl;break
  if hold_days==0 and hm>=915:exitp=cl;break
  if hold_days>0 and day_index>=max_hold and hm>=915:exitp=cl;break
 if exitp is None:exitp=float(rows[min(len(rows)-1,i+419)]['close'])
 return ((exitp*.9995/entry)-1-.0002-.0015)*100

def _replay(kind,hold_days=0):
 pnl=[]
 for c in [x['code'] for x in available_codes()[:40]]:
  rows,sigs=_signals(c,kind);used=set()
  for i in sigs:
   d=_dt(rows[i]['bucket']).date()
   if d in used:continue
   used.add(d);pnl.append(_exit_trade(rows,i,'control',hold_days))
 return _metrics(pnl)

def run_lab(max_codes=40):
 cov=available_codes()[:max(1,min(int(max_codes),100))];control=run_backtest(max_codes=len(cov));strategies=[{'id':'control','name':'Control v0.8.0','role':'CONTROL','trades':control['trades'],'winRate':control['winRate'],'profitFactor':control['profitFactor'],'expectancyPct':control['expectancyPct'],'maxDrawdownPct':control['maxDrawdownPct']}]
 for kind,name in [('orb_rvol','ORB + RVOL'),('vwap_pullback','VWAP Pullback'),('momentum_adx','Momentum + ADX/DMI'),('first_pullback','First Pullback'),('cross_trend','Cross Trend'),('orb_cross','ORB + Cross Trend')]:strategies.append({'id':kind,'name':name,'role':'CHALLENGER',**_replay(kind,0)})
 ranked=sorted(strategies[1:],key=lambda x:(x['profitFactor'],x['expectancyPct']),reverse=True)
 return {'ok':True,'labVersion':'0.16.3','controlStrategy':'v0.8.0 LOCKED','liveRuleAutoMutation':False,'researchOnly':True,'codesTested':len(cov),'strategies':strategies,'bestChallenger':ranked[0]['id'] if ranked else None,'warning':'Cross Trend 포함 1차 스크리닝. 최종 후보는 portfolio-high 재검증 필요.'}

def run_exit_lab(strategy='orb_rvol'):
 allowed={'orb_rvol','vwap_pullback','momentum_adx','first_pullback','cross_trend','orb_cross'}
 if strategy not in allowed:strategy='orb_rvol'
 results=[]
 for days,name in [(0,'당일청산'),(1,'최대 1박'),(2,'최대 2박'),(3,'최대 3박'),(5,'최대 5거래일')]:results.append({'id':f'hold_{days}','name':name,'holdDays':days,**_replay(strategy,days)})
 ranked=sorted(results,key=lambda x:(x['profitFactor'],x['expectancyPct']),reverse=True);best=ranked[0] if ranked else None
 return {'ok':True,'labVersion':'0.16.3','strategy':strategy,'controlStrategy':'v0.8.0 LOCKED','researchOnly':True,'liveRuleAutoMutation':False,'roundTripCostPct':0.27,'results':results,'bestExit':best['id'] if best else None,'passGate':bool(best and best['profitFactor']>1 and best['expectancyPct']>0),'gate':'PF > 1.0 AND expectancy > 0','overnightRisk':'다음 거래일 시가 갭을 우선 반영. 추세 훼손(EMA5<MA15 또는 EMA10<MA120) 시 청산.','warning':'Overnight Lab은 연구용입니다. 통과 후보도 portfolio-high 정밀 재검증 전 실전 적용 금지.'}
