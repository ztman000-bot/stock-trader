"""High-fidelity portfolio backtest for Control v0.8.0.
Research only: never sends orders and never mutates the live strategy.
Signal is evaluated on a completed 5m bar and filled at NEXT bar open.
Portfolio constraints are replayed chronologically across symbols.
"""
import math, sqlite3
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from collector import DB_PATH, PROTECTED_CODES, instrument_meta
from paper_engine import (
 BUY_SCORE,MIN_BARS,MIN_SESSION_BARS,MIN_RSI,MAX_RSI,MIN_ADX,MIN_VOLUME_RATIO,STRONG_VOLUME_RATIO,
 STOP_PCT,TRAIL_ACTIVATE_PCT,TRAIL_PCT,BREAKEVEN_ACTIVATE_PCT,BREAKEVEN_BUFFER_PCT,
 COMMISSION_RATE,SELL_TAX_RATE,SLIPPAGE_RATE,ROUND_TRIP_COST_EST,INITIAL_CAPITAL,RISK_PER_TRADE,
 MAX_CONSECUTIVE_LOSSES,MAX_OPEN_POSITIONS,MAX_DAILY_TRADES,DAILY_MAX_LOSS_PCT,
 _ema,_rsi,_wilder_adx,_vwap)
KST=ZoneInfo('Asia/Seoul');CONTROL_STRATEGY='v0.8.0 LOCKED'
ENTRY_START=570;ENTRY_CUTOFF=890;EOD_EXIT=915
RESEARCH_ONLY_CODES={'069500','229200'}  # market-regime ETF proxies, never trade/backtest candidates

def _dt(v):return datetime.fromisoformat(str(v)).astimezone(KST)
def _load_rows(code,start=None,end=None):
 sql='SELECT bucket,open,high,low,close,volume FROM bars_5m WHERE code=?';args=[code]
 if start:sql+=' AND bucket>=?';args.append(start)
 if end:sql+=' AND bucket<?';args.append(end)
 sql+=' ORDER BY bucket'
 with sqlite3.connect(DB_PATH,timeout=10) as c:
  c.row_factory=sqlite3.Row;return [dict(r) for r in c.execute(sql,args)]
def available_codes():
 with sqlite3.connect(DB_PATH,timeout=10) as c:rows=c.execute('SELECT code,COUNT(*) n,MIN(bucket),MAX(bucket) FROM bars_5m GROUP BY code ORDER BY n DESC').fetchall()
 return [{'code':r[0],'name':instrument_meta(r[0]).get('name') or r[0],'bars':r[1],'first':r[2],'last':r[3]} for r in rows if r[0] not in PROTECTED_CODES and r[0] not in RESEARCH_ONLY_CODES]
def _signal(history,session):
 if len(history)<MIN_BARS or len(session)<MIN_SESSION_BARS:return None
 cr=history[-120:];cl=[float(r['close']) for r in cr];e9=_ema(cl[-60:],9);e20=_ema(cl[-80:],20);rsi=_rsi(cl);adx,pdi,mdi=_wilder_adx(cr);last=cr[-1];price=float(last['close']);vw=_vwap(session);prior=cr[-7:-1]
 if len(prior)<6:return None
 rh=max(float(r['high']) for r in prior);av=sum(int(r['volume']) for r in prior)/len(prior);vr=int(last['volume'])/av if av>0 else 0;opening=session[:MIN_SESSION_BARS];oh=max(float(r['high']) for r in opening);orb=price>oh;rb=price>rh;above=bool(vw and price>vw);bull=e9 is not None and e20 is not None and e9>e20;rok=rsi is not None and MIN_RSI<=rsi<=MAX_RSI;dok=adx is not None and adx>=MIN_ADX and pdi is not None and mdi is not None and pdi>mdi;sv=vr>=STRONG_VOLUME_RATIO
 score=5+(15 if above else 0)+(15 if bull else 0)+(max(0,min(15,(vr-1)*18.75)) if vr>1 else 0)+(20 if orb else 10 if rb else 0)+(10 if rok else 0)+(min(15,8+max(0,adx-MIN_ADX)*.35) if dok else 0);score=min(90,score);gate=above and bull and (orb or (rb and sv)) and rok and dok and vr>=MIN_VOLUME_RATIO
 return {'buy':gate and score>=BUY_SCORE,'score':score,'rsi':rsi,'adx':adx,'volumeRatio':vr,'orb':orb}
def _size(capital,price):
 if price<=0:return 0
 risk_qty=math.floor(capital*RISK_PER_TRADE/(price*(STOP_PCT+ROUND_TRIP_COST_EST)));cash_qty=math.floor(capital/(price*(1+COMMISSION_RATE+SLIPPAGE_RATE)));return max(0,min(risk_qty,cash_qty))
def _close(pos,raw,reason,at):
 exit_price=max(0,float(raw))*(1-SLIPPAGE_RATE);entry=pos['entry'];gross=exit_price/entry-1;net=gross-2*COMMISSION_RATE-SELL_TAX_RATE;pnl=pos['qty']*entry*net
 return {'code':pos['code'],'name':pos['name'],'date':pos['date'],'signalAt':pos['signalAt'],'entryAt':pos['entryAt'],'exitAt':at,'entry':entry,'exit':exit_price,'qty':pos['qty'],'pnl':pnl,'pnlPct':net*100,'mfePct':(pos['peak']/entry-1)*100,'maePct':(pos['trough']/entry-1)*100,'reason':reason,'score':pos['score']}
def run_backtest(codes=None,start=None,end=None,max_codes=40):
 coverage=available_codes();chosen=[x['code'] for x in coverage if not codes or x['code'] in codes][:max(1,min(int(max_codes),100))];data={};days=set()
 for code in chosen:
  rows=_load_rows(code,start,end);data[code]=rows
  for r in rows:days.add(_dt(r['bucket']).date().isoformat())
 pending=defaultdict(list);bar_map={};histories=defaultdict(list);sessions=defaultdict(list);events=[]
 for code,rows in data.items():
  for idx,r in enumerate(rows):events.append((_dt(r['bucket']),code,idx,r))
 events.sort(key=lambda x:(x[0],x[1]))
 for dt,code,idx,r in events:
  hm=dt.hour*60+dt.minute
  if not 540<=hm<=930:continue
  day=dt.date().isoformat();key=(code,day);histories[code].append(r);sessions[key].append(r);bar_map[(code,idx)]=r
  if ENTRY_START<=hm<ENTRY_CUTOFF and idx+1<len(data[code]):
   nxt=data[code][idx+1];nd=_dt(nxt['bucket'])
   if nd.date()==dt.date():
    sig=_signal(histories[code],sessions[key])
    if sig and sig['buy']:pending[nd.isoformat()].append((code,idx+1,sig,r['bucket']))
 positions={};trades=[];cash=INITIAL_CAPITAL;equity_peak=INITIAL_CAPITAL;mdd=0;daily={};seen_entries=set();timestamps=sorted(set(dt.isoformat() for dt,_,_,_ in events));rows_by_time=defaultdict(dict)
 for dt,code,idx,r in events:rows_by_time[dt.isoformat()][code]=(idx,r)
 for ts in timestamps:
  dt=_dt(ts);day=dt.date().isoformat();hm=dt.hour*60+dt.minute;st=daily.setdefault(day,{'closed':0,'pnl':0.0,'consecutiveLosses':0,'locked':False})
  for code,pos in list(positions.items()):
   item=rows_by_time[ts].get(code)
   if not item:continue
   _,r=item;hi=float(r['high']);lo=float(r['low']);cl=float(r['close']);pos['peak']=max(pos['peak'],hi);pos['trough']=min(pos['trough'],lo);reason=None;raw=None;stop=pos['entry']*(1-STOP_PCT)
   if lo<=stop:reason='STOP_LOSS';raw=stop
   elif pos['peak']>=pos['entry']*(1+TRAIL_ACTIVATE_PCT) and lo<=pos['peak']*(1-TRAIL_PCT):reason='TRAIL_STOP';raw=pos['peak']*(1-TRAIL_PCT)
   elif pos['peak']>=pos['entry']*(1+BREAKEVEN_ACTIVATE_PCT) and lo<=pos['entry']*(1+BREAKEVEN_BUFFER_PCT):reason='COST_PROTECT';raw=pos['entry']*(1+BREAKEVEN_BUFFER_PCT)
   elif hm>=EOD_EXIT:reason='EOD_EXIT';raw=cl
   if reason:
    tr=_close(pos,raw,reason,ts);trades.append(tr);cash+=pos['reserved']+tr['pnl'];st['closed']+=1;st['pnl']+=tr['pnl'];st['consecutiveLosses']=st['consecutiveLosses']+1 if tr['pnl']<0 else 0;st['locked']=st['consecutiveLosses']>=MAX_CONSECUTIVE_LOSSES or st['pnl']<=-INITIAL_CAPITAL*DAILY_MAX_LOSS_PCT or st['closed']>=MAX_DAILY_TRADES;positions.pop(code,None)
  if ENTRY_START<=hm<ENTRY_CUTOFF and not st['locked']:
   candidates=sorted(pending.get(ts,[]),key=lambda x:x[2]['score'],reverse=True)
   for code,idx,sig,signal_at in candidates:
    if len(positions)>=MAX_OPEN_POSITIONS or st['locked']:break
    if (code,day) in seen_entries or code in positions:continue
    r=data[code][idx];raw=float(r['open']);fill=raw*(1+SLIPPAGE_RATE);qty=_size(max(cash,0),fill);reserved=qty*fill*(1+COMMISSION_RATE)
    if qty<1 or reserved>cash:continue
    cash-=reserved;positions[code]={'code':code,'name':instrument_meta(code).get('name') or code,'date':day,'signalAt':signal_at,'entryAt':r['bucket'],'entry':fill,'qty':qty,'reserved':reserved,'peak':fill,'trough':fill,'score':sig['score']};seen_entries.add((code,day))
  marked=cash+sum(p['reserved'] for p in positions.values());equity_peak=max(equity_peak,marked);mdd=min(mdd,(marked/equity_peak-1)*100 if equity_peak else 0)
 for code,pos in list(positions.items()):
  r=data[code][-1];tr=_close(pos,float(r['close']),'DATA_END',r['bucket']);trades.append(tr);cash+=pos['reserved']+tr['pnl'];positions.pop(code,None)
 wins=[t for t in trades if t['pnl']>0];losses=[t for t in trades if t['pnl']<=0];gp=sum(t['pnl'] for t in wins);gl=abs(sum(t['pnl'] for t in losses));pf=gp/gl if gl else (999 if gp else 0);buckets=[]
 for lo,hi,label in [(78,85,'78-84'),(85,90,'85-89'),(90,95,'90-94'),(95,101,'95+')]:
  a=[t for t in trades if lo<=t['score']<hi];buckets.append({'bucket':label,'trades':len(a),'winRate':round(sum(t['pnl']>0 for t in a)/len(a)*100,1) if a else 0,'avgPnlPct':round(sum(t['pnlPct'] for t in a)/len(a),3) if a else 0})
 return {'ok':True,'controlStrategy':CONTROL_STRATEGY,'fidelity':'portfolio-high','executionModel':'completed_signal_bar -> next_bar_open','intrabarPolicy':'stop_first_conservative','portfolioRules':{'maxOpenPositions':MAX_OPEN_POSITIONS,'maxDailyTrades':MAX_DAILY_TRADES,'maxConsecutiveLosses':MAX_CONSECUTIVE_LOSSES,'dailyMaxLossPct':DAILY_MAX_LOSS_PCT*100,'entryWindow':'09:30-14:50','eodExit':'15:15'},'survivorshipBias':'current-universe; Universe Snapshot accumulation started in v0.17.0 for future reconstruction','liveRuleAutoMutation':False,'source':'NH/local bars_5m','costsIncluded':True,'roundTripCostEstimatePct':round(ROUND_TRIP_COST_EST*100,3),'codesTested':len(chosen),'tradingDays':len(days),'trades':len(trades),'wins':len(wins),'losses':len(losses),'winRate':round(len(wins)/len(trades)*100,2) if trades else 0,'profitFactor':round(pf,3),'expectancyPct':round(sum(t['pnlPct'] for t in trades)/len(trades),4) if trades else 0,'maxDrawdownPct':round(mdd,3),'avgMfePct':round(sum(t['mfePct'] for t in trades)/len(trades),3) if trades else 0,'avgMaePct':round(sum(t['maePct'] for t in trades)/len(trades),3) if trades else 0,'endingCapital':round(cash,2),'netPnl':round(cash-INITIAL_CAPITAL,2),'scoreBuckets':buckets,'coverage':coverage[:max(1,min(int(max_codes),100))],'sampleWarning':('표본 부족: 최소 200거래 이상 축적 후 판단 권장' if len(trades)<200 else None),'limitations':['v0.17.0 이전 과거 날짜에는 당시 Universe Snapshot이 없어 생존편향이 남음','과거 active-candidate 유동성/activityScore를 완전히 재구성하지 못함'],'recentTrades':trades[-50:]}
