"""Multi-Strategy Lab v0.16.0. Research only; never places orders or mutates Control v0.8.0."""
from backtest_engine import _load_rows,_dt,available_codes,_ema,_rsi,_wilder_adx,_vwap,run_backtest

def _metrics(rows):
 n=len(rows); wins=[x for x in rows if x>0]; losses=[x for x in rows if x<=0]; gp=sum(wins); gl=abs(sum(losses)); pf=gp/gl if gl else (999 if gp else 0)
 return {'trades':n,'winRate':round(len(wins)/n*100,2) if n else 0,'profitFactor':round(pf,3),'expectancyPct':round(sum(rows)/n,4) if n else 0}

def _signals(code,kind):
 rows=_load_rows(code); out=[]; hist=[]; session=[]; day=None
 for i,r in enumerate(rows[:-1]):
  dt=_dt(r['bucket']); d=dt.date(); hm=dt.hour*60+dt.minute
  if d!=day:day=d;session=[]
  hist.append(r);session.append(r)
  if len(hist)<35 or len(session)<6 or not 570<=hm<890:continue
  cr=hist[-120:]; cl=[float(x['close']) for x in cr]; price=float(r['close']); e9=_ema(cl[-60:],9);e20=_ema(cl[-80:],20);rs=_rsi(cl);adx,pdi,mdi=_wilder_adx(cr);vw=_vwap(session);prior=cr[-7:-1]
  if len(prior)<6 or not all(x is not None for x in (e9,e20,rs,adx,pdi,mdi,vw)):continue
  av=sum(int(x['volume']) for x in prior)/6; vr=int(r['volume'])/av if av else 0; rh=max(float(x['high']) for x in prior); oh=max(float(x['high']) for x in session[:6]); bull=e9>e20 and price>vw and pdi>mdi
  buy=False
  if kind=='orb_rvol':buy=bull and price>oh and vr>=1.8 and 55<=rs<=78 and adx>=22
  elif kind=='vwap_pullback':
   prev=float(prior[-1]['close']); buy=e9>e20 and adx>=20 and pdi>mdi and 50<=rs<=72 and min(float(r['low']),prev)<=vw*1.003 and price>vw and price>prev and vr>=1.0
  elif kind=='momentum_adx':buy=bull and price>rh and vr>=1.35 and 55<=rs<=75 and adx>=25
  elif kind=='first_pullback':
   recent=cr[-5:]; high=max(float(x['high']) for x in recent); buy=e9>e20 and price>vw and adx>=22 and pdi>mdi and 52<=rs<=72 and price>=float(prior[-1]['high']) and price<high*1.003 and vr>=1.1
  if buy:
   nxt=rows[i+1]; nd=_dt(nxt['bucket']);
   if nd.date()==d:out.append((i+1,nxt))
 return out

def _quick_replay(code,kind):
 rows=_load_rows(code); sig={i for i,_ in _signals(code,kind)}; pnls=[]; used=set()
 for i in sorted(sig):
  dt=_dt(rows[i]['bucket']); d=dt.date()
  if d in used:continue
  used.add(d); entry=float(rows[i]['open'])*1.0005; peak=entry; exitp=None
  for r in rows[i:i+72]:
   rd=_dt(r['bucket']);
   if rd.date()!=d:break
   hi=float(r['high']);lo=float(r['low']);cl=float(r['close']);peak=max(peak,hi);hm=rd.hour*60+rd.minute
   if lo<=entry*.99:exitp=entry*.99;break
   if peak>=entry*1.015 and lo<=peak*.992:exitp=peak*.992;break
   if peak>=entry*1.008 and lo<=entry*1.0035:exitp=entry*1.0035;break
   if hm>=915:exitp=cl;break
  if exitp is None:exitp=float(rows[min(len(rows)-1,i+71)]['close'])
  net=(exitp*.9995/entry-1)-.0002-.0015;pnls.append(net*100)
 return pnls

def run_lab(max_codes=40):
 cov=available_codes()[:max(1,min(int(max_codes),100))]; codes=[x['code'] for x in cov]; control=run_backtest(max_codes=len(codes)); strategies=[{'id':'control','name':'Control v0.8.0','role':'CONTROL','trades':control['trades'],'winRate':control['winRate'],'profitFactor':control['profitFactor'],'expectancyPct':control['expectancyPct'],'maxDrawdownPct':control['maxDrawdownPct']}]
 for kind,name in [('orb_rvol','ORB + RVOL'),('vwap_pullback','VWAP Pullback'),('momentum_adx','Momentum + ADX/DMI'),('first_pullback','First Pullback')]:
  pnl=[]
  for c in codes:pnl.extend(_quick_replay(c,kind))
  m=_metrics(pnl);strategies.append({'id':kind,'name':name,'role':'CHALLENGER',**m,'maxDrawdownPct':None})
 ranked=sorted(strategies[1:],key=lambda x:(x['profitFactor'],x['expectancyPct']),reverse=True)
 return {'ok':True,'labVersion':'0.16.0','controlStrategy':'v0.8.0 LOCKED','liveRuleAutoMutation':False,'researchOnly':True,'codesTested':len(codes),'strategies':strategies,'bestChallenger':ranked[0]['id'] if ranked else None,'warning':'Challenger는 1차 스크리닝용 독립 리플레이입니다. 후보 선별 후 정밀 portfolio-high 엔진으로 재검증해야 합니다.'}
