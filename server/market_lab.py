"""Market Research Lab v0.17.0. Research only; never changes live rules or sends orders."""
from collections import defaultdict
from statistics import mean, median
from backtest_engine import _load_rows,_dt,available_codes,_ema,_rsi,_wilder_adx,_vwap
from strategy_lab import _signals,_exit_trade

SURGE_PCT=5.0
REGIME_PROXY_CODES={'069500':'KODEX200','229200':'KODEX_KOSDAQ150'}

def _f(v,d=4):
 try:return round(float(v),d)
 except:return 0.0

def _feature(rows, signal_i):
 i=max(0,signal_i-1); cr=rows[max(0,i-159):i+1]
 if len(cr)<30:return None
 day=_dt(rows[i]['bucket']).date(); session=[x for x in rows[:i+1] if _dt(x['bucket']).date()==day]
 cl=[float(x['close']) for x in cr]; price=float(rows[i]['close']); e5=_ema(cl[-50:],5); ma15=sum(cl[-15:])/15 if len(cl)>=15 else None
 rs=_rsi(cl); adx,pdi,mdi=_wilder_adx(cr); vw=_vwap(session) if session else None; prior=cr[-7:-1]
 if not all(x is not None for x in (e5,ma15,rs,adx,pdi,mdi,vw)) or len(prior)<6:return None
 av=sum(int(x['volume']) for x in prior)/len(prior); rv=int(rows[i]['volume'])/av if av else 0
 return {'rsi':_f(rs,2),'adx':_f(adx,2),'dmiSpread':_f(pdi-mdi,2),'rvol':_f(rv,2),'vwapDistPct':_f((price/vw-1)*100,3),'emaSpreadPct':_f((e5/ma15-1)*100,3),'time':_dt(rows[i]['bucket']).strftime('%H:%M')}

def _failure_analysis(codes):
 wins=[]; losses=[]; reasons=defaultdict(int); trades=[]
 for code in codes:
  rows,sigs=_signals(code,'cross_trend_v2');used=set()
  for i in sigs:
   d=_dt(rows[i]['bucket']).date()
   if d in used:continue
   used.add(d);feat=_feature(rows,i)
   if not feat:continue
   pnl=_exit_trade(rows,i,0);rec={**feat,'pnlPct':_f(pnl,3)};trades.append(rec);(wins if pnl>0 else losses).append(rec)
   if pnl<=0:
    if feat['rvol']<1.5:reasons['LOW_RVOL_AFTER_FILTER']+=1
    if feat['adx']<27:reasons['MARGINAL_ADX']+=1
    if feat['dmiSpread']<8:reasons['NARROW_DMI_SPREAD']+=1
    if feat['vwapDistPct']>1.0:reasons['VWAP_CHASE']+=1
    if feat['rsi']>68:reasons['HIGH_RSI']+=1
    hh=int(feat['time'][:2]);mm=int(feat['time'][3:])
    if hh*60+mm>=840:reasons['LATE_ENTRY_AFTER_14']+=1
 def avg(arr,key):return _f(mean([x[key] for x in arr]),3) if arr else 0
 compare={k:{'winAvg':avg(wins,k),'lossAvg':avg(losses,k)} for k in ('rsi','adx','dmiSpread','rvol','vwapDistPct','emaSpreadPct')}
 return {'trades':len(trades),'wins':len(wins),'losses':len(losses),'winRate':_f(len(wins)/len(trades)*100,2) if trades else 0,'compare':compare,'lossFlags':dict(sorted(reasons.items(),key=lambda x:x[1],reverse=True)),'note':'손실 플래그는 원인 확정이 아니라 Cross Trend 2.1 후보조건 탐색용입니다.'}

def _proxy_returns():
 out=defaultdict(list);coverage={}
 for code,name in REGIME_PROXY_CODES.items():
  rows=_load_rows(code);day_open={};n=0
  for r in rows:
   dt=_dt(r['bucket']);d=dt.date();hm=dt.hour*60+dt.minute
   if not 540<=hm<=930:continue
   day_open.setdefault(d,float(r['open']));op=day_open[d]
   if op>0:out[r['bucket']].append((float(r['close'])/op-1)*100);n+=1
  coverage[name]=n
 return out,coverage

def _build_regime(codes):
 by=defaultdict(list)
 for code in codes:
  if code in REGIME_PROXY_CODES:continue
  rows=_load_rows(code);day_open={}
  for r in rows:
   dt=_dt(r['bucket']);d=dt.date();hm=dt.hour*60+dt.minute
   if not 540<=hm<=930:continue
   day_open.setdefault(d,float(r['open']));op=day_open[d]
   if op>0:by[r['bucket']].append((float(r['close'])/op-1)*100)
 proxy,coverage=_proxy_returns();out={}
 for b,vals in by.items():
  if len(vals)<10:continue
  breadth=sum(v>0 for v in vals)/len(vals);med=median(vals);pvals=proxy.get(b,[]);pret=median(pvals) if pvals else None
  if pret is not None:
   if (pret<-1.0) or (pret<-.70 and breadth<.40):label='RED'
   elif pret<-.30 or breadth<.45 or med<-.25:label='CAUTION'
   else:label='NORMAL'
  else:
   if breadth<.35 and med<-.50:label='RED'
   elif breadth<.45 or med<-.25:label='CAUTION'
   else:label='NORMAL'
  out[b]={'label':label,'breadth':breadth,'medianRetPct':med,'proxyRetPct':pret,'n':len(vals)}
 return out

def _regime_test(codes):
 regime=_build_regime(codes);buckets={k:[] for k in ('NORMAL','CAUTION','RED','UNKNOWN')}
 for code in codes:
  if code in REGIME_PROXY_CODES:continue
  rows,sigs=_signals(code,'cross_trend_v2');used=set()
  for i in sigs:
   d=_dt(rows[i]['bucket']).date()
   if d in used:continue
   used.add(d);sig_bucket=rows[max(0,i-1)]['bucket'];lab=regime.get(sig_bucket,{}).get('label','UNKNOWN');buckets[lab].append(_exit_trade(rows,i,0))
 def met(a):
  w=[x for x in a if x>0];l=[x for x in a if x<=0];gp=sum(w);gl=abs(sum(l));pf=gp/gl if gl else (999 if gp else 0)
  return {'trades':len(a),'winRate':_f(len(w)/len(a)*100,2) if a else 0,'profitFactor':_f(pf,3),'expectancyPct':_f(mean(a),3) if a else 0}
 proxy_buckets=sum(1 for x in regime.values() if x.get('proxyRetPct') is not None);_,coverage=_proxy_returns();method='ETF_INDEX_PROXY+BREADTH' if proxy_buckets else 'BREADTH_PROXY'
 return {'method':method,'proxyCodes':REGIME_PROXY_CODES,'proxyCoverage':coverage,'proxyBuckets':proxy_buckets,'thresholds':{'red':'ETF proxy <-1.0% OR proxy <-0.70% + breadth <40%','caution':'proxy <-0.30% OR breadth <45% OR median intraday return <-0.25%'},'groups':{k:met(v) for k,v in buckets.items()},'warning':'KODEX200/KODEX KOSDAQ150 5분봉을 KOSPI/KOSDAQ 방향 보조 proxy로 사용합니다. 실제 지수 자체가 아니므로 Control 실전 게이트로 자동 승격하지 않습니다.'}

def _surge_lab(codes):
 rows_out=[]
 for code in codes:
  if code in REGIME_PROXY_CODES:continue
  rows=_load_rows(code);days=defaultdict(list)
  for r in rows:days[_dt(r['bucket']).date()].append(r)
  prev_first_vol=None;prev_close=None
  for d,arr in sorted(days.items()):
   arr=sorted(arr,key=lambda x:x['bucket']);regular=[x for x in arr if 540<=(_dt(x['bucket']).hour*60+_dt(x['bucket']).minute)<=930];first=[x for x in regular if 540<=(_dt(x['bucket']).hour*60+_dt(x['bucket']).minute)<570];after=[x for x in regular if (_dt(x['bucket']).hour*60+_dt(x['bucket']).minute)>=570]
   if len(first)<6 or not after:
    if regular:prev_close=float(regular[-1]['close'])
    continue
   op=float(first[0]['open']);p30=float(first[-1]['close']);hi=max(float(x['high']) for x in first);lo=min(float(x['low']) for x in first);v30=sum(int(x['volume']) for x in first);gap=(op/prev_close-1)*100 if prev_close else 0;openret=(p30/op-1)*100;rvol=v30/prev_first_vol if prev_first_vol else 0;future=(max(float(x['high']) for x in after)/p30-1)*100;score=0
   if gap>=1:score+=15
   if openret>=1:score+=25
   if openret>=2:score+=10
   if rvol>=1.5:score+=25
   if rvol>=2.5:score+=10
   if (hi/lo-1)*100>=2:score+=10
   if p30>=hi*.995:score+=15
   candidate=score>=60;surge=future>=SURGE_PCT;rows_out.append({'candidate':candidate,'surge':surge,'score':score,'future':future,'gap':gap,'openret':openret,'rvol':rvol});prev_first_vol=v30;prev_close=float(regular[-1]['close'])
 tp=sum(x['candidate'] and x['surge'] for x in rows_out);fp=sum(x['candidate'] and not x['surge'] for x in rows_out);actual=sum(x['surge'] for x in rows_out);cand=sum(x['candidate'] for x in rows_out)
 return {'definition':f'09:30 이후 당일 추가 고가 +{SURGE_PCT:.0f}% 이상','daysObserved':len(rows_out),'actualSurges':actual,'candidates':cand,'hits':tp,'precisionPct':_f(tp/cand*100,2) if cand else 0,'recallPct':_f(tp/actual*100,2) if actual else 0,'falsePositives':fp,'scoreRule':'갭 + 첫30분 수익률 + 첫30분 상대거래량 + 변동폭 + 고가근접도. 60점 이상 후보','researchOnly':True}

def run_market_lab(max_codes=40):
 codes=[x['code'] for x in available_codes()[:max(10,min(int(max_codes),100))]]
 return {'ok':True,'labVersion':'0.17.0','controlStrategy':'v0.8.0 LOCKED','researchOnly':True,'liveRuleAutoMutation':False,'realOrderEnabled':False,'codesTested':len(codes),'failureAnalysis':_failure_analysis(codes),'marketRegime':_regime_test(codes),'surgeDiscovery':_surge_lab(codes),'recommendation':'ETF 시장 proxy + breadth, RED 회피, Early Surge 사전선별을 Shadow 검증합니다. OOS/비용 스트레스에서 재현되기 전 Control에는 반영하지 않습니다.'}
