"""Strategy / Exit Lab v0.16.2. Research only; never places orders or mutates Control v0.8.0."""
from backtest_engine import _load_rows,_dt,available_codes,_ema,_rsi,_wilder_adx,_vwap,run_backtest
COST=.0027

def _metrics(rows):
 n=len(rows); wins=[x for x in rows if x>0]; losses=[x for x in rows if x<=0]; gp=sum(wins);gl=abs(sum(losses));pf=gp/gl if gl else (999 if gp else 0);eq=0;peak=0;mdd=0
 for x in rows:eq+=x;peak=max(peak,eq);mdd=min(mdd,eq-peak)
 return {'trades':n,'winRate':round(len(wins)/n*100,2) if n else 0,'profitFactor':round(pf,3),'expectancyPct':round(sum(rows)/n,4) if n else 0,'maxDrawdownPct':round(mdd,3)}

def _signals(code,kind):
 rows=_load_rows(code);out=[];hist=[];session=[];day=None
 for i,r in enumerate(rows[:-1]):
  dt=_dt(r['bucket']);d=dt.date();hm=dt.hour*60+dt.minute
  if d!=day:day=d;session=[]
  hist.append(r);session.append(r)
  if len(hist)<35 or len(session)<6 or not 570<=hm<890:continue
  cr=hist[-120:];cl=[float(x['close']) for x in cr];price=float(r['close']);e9=_ema(cl[-60:],9);e20=_ema(cl[-80:],20);rs=_rsi(cl);adx,pdi,mdi=_wilder_adx(cr);vw=_vwap(session);prior=cr[-7:-1]
  if len(prior)<6 or not all(x is not None for x in (e9,e20,rs,adx,pdi,mdi,vw)):continue
  av=sum(int(x['volume']) for x in prior)/6;vr=int(r['volume'])/av if av else 0;rh=max(float(x['high']) for x in prior);oh=max(float(x['high']) for x in session[:6]);bull=e9>e20 and price>vw and pdi>mdi;buy=False
  if kind=='orb_rvol':buy=bull and price>oh and vr>=1.8 and 55<=rs<=78 and adx>=22
  elif kind=='vwap_pullback':
   prev=float(prior[-1]['close']);buy=e9>e20 and adx>=20 and pdi>mdi and 50<=rs<=72 and min(float(r['low']),prev)<=vw*1.003 and price>vw and price>prev and vr>=1.0
  elif kind=='momentum_adx':buy=bull and price>rh and vr>=1.35 and 55<=rs<=75 and adx>=25
  elif kind=='first_pullback':
   high=max(float(x['high']) for x in cr[-5:]);buy=e9>e20 and price>vw and adx>=22 and pdi>mdi and 52<=rs<=72 and price>=float(prior[-1]['high']) and price<high*1.003 and vr>=1.1
  if buy and _dt(rows[i+1]['bucket']).date()==d:out.append(i+1)
 return rows,out

def _exit_trade(rows,i,mode):
 entry=float(rows[i]['open'])*1.0005;peak=entry;bars=0;exitp=None
 cfg={'control':(.010,.015,.008,.008,.0035,999),'tight_stop':(.0075,.015,.008,.008,.0035,999),'wide_target':(.010,.020,.010,.010,.0045,999),'fast_protect':(.009,.012,.006,.006,.0030,999),'time_45m':(.009,.015,.007,.007,.0035,9),'mfe_guard':(.009,.012,.005,.006,.0035,999)}[mode]
 stop,trail_on,trail,be_on,be_buf,maxbars=cfg
 for r in rows[i:i+72]:
  rd=_dt(r['bucket']);
  if rd.date()!=_dt(rows[i]['bucket']).date():break
  bars+=1;hi=float(r['high']);lo=float(r['low']);cl=float(r['close']);peak=max(peak,hi);hm=rd.hour*60+rd.minute
  if lo<=entry*(1-stop):exitp=entry*(1-stop);break
  if peak>=entry*(1+trail_on) and lo<=peak*(1-trail):exitp=peak*(1-trail);break
  if peak>=entry*(1+be_on) and lo<=entry*(1+be_buf):exitp=entry*(1+be_buf);break
  if bars>=maxbars:exitp=cl;break
  if hm>=915:exitp=cl;break
 if exitp is None:exitp=float(rows[min(len(rows)-1,i+71)]['close'])
 return ((exitp*.9995/entry)-1-.0002-.0015)*100

def _replay(kind,mode):
 pnl=[]
 for c in [x['code'] for x in available_codes()[:40]]:
  rows,sigs=_signals(c,kind);used=set()
  for i in sigs:
   d=_dt(rows[i]['bucket']).date()
   if d in used:continue
   used.add(d);pnl.append(_exit_trade(rows,i,mode))
 return _metrics(pnl)

def run_lab(max_codes=40):
 cov=available_codes()[:max(1,min(int(max_codes),100))];control=run_backtest(max_codes=len(cov));strategies=[{'id':'control','name':'Control v0.8.0','role':'CONTROL','trades':control['trades'],'winRate':control['winRate'],'profitFactor':control['profitFactor'],'expectancyPct':control['expectancyPct'],'maxDrawdownPct':control['maxDrawdownPct']}]
 for kind,name in [('orb_rvol','ORB + RVOL'),('vwap_pullback','VWAP Pullback'),('momentum_adx','Momentum + ADX/DMI'),('first_pullback','First Pullback')]:strategies.append({'id':kind,'name':name,'role':'CHALLENGER',**_replay(kind,'control')})
 ranked=sorted(strategies[1:],key=lambda x:(x['profitFactor'],x['expectancyPct']),reverse=True)
 return {'ok':True,'labVersion':'0.16.2','controlStrategy':'v0.8.0 LOCKED','liveRuleAutoMutation':False,'researchOnly':True,'codesTested':len(cov),'strategies':strategies,'bestChallenger':ranked[0]['id'] if ranked else None,'warning':'Challenger는 1차 스크리닝입니다. 최종 후보는 portfolio-high 재검증 필요.'}

def run_exit_lab(strategy='orb_rvol'):
 allowed={'orb_rvol','vwap_pullback','momentum_adx','first_pullback'}
 if strategy not in allowed:strategy='orb_rvol'
 modes=[('control','현재 청산'),('tight_stop','손절 -0.75%'),('wide_target','수익확대/느린 트레일'),('fast_protect','빠른 수익보호'),('time_45m','45분 시간청산'),('mfe_guard','MFE 수익보호')]
 results=[]
 for mid,name in modes:results.append({'id':mid,'name':name,**_replay(strategy,mid)})
 ranked=sorted(results,key=lambda x:(x['profitFactor'],x['expectancyPct']),reverse=True);best=ranked[0] if ranked else None
 return {'ok':True,'labVersion':'0.16.2','strategy':strategy,'controlStrategy':'v0.8.0 LOCKED','researchOnly':True,'liveRuleAutoMutation':False,'roundTripCostPct':0.27,'results':results,'bestExit':best['id'] if best else None,'passGate':bool(best and best['profitFactor']>1 and best['expectancyPct']>0),'gate':'PF > 1.0 AND expectancy > 0','warning':'Exit Lab은 원인분해/후보선별용입니다. 통과 후보도 portfolio-high 정밀 재검증 전 실전 적용 금지.'}
