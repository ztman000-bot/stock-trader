"""Historical backtest for Control v0.8.0 using locally stored NH 5m bars.

This module NEVER sends orders and NEVER mutates the live strategy.
It intentionally reports data coverage so small samples cannot be mistaken for proof.
"""
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from collector import DB_PATH, PROTECTED_CODES, instrument_meta
from paper_engine import (
    BUY_SCORE, MIN_BARS, MIN_SESSION_BARS, MIN_RSI, MAX_RSI, MIN_ADX,
    MIN_VOLUME_RATIO, STRONG_VOLUME_RATIO, STOP_PCT, TRAIL_ACTIVATE_PCT,
    TRAIL_PCT, BREAKEVEN_ACTIVATE_PCT, BREAKEVEN_BUFFER_PCT,
    COMMISSION_RATE, SELL_TAX_RATE, SLIPPAGE_RATE, ROUND_TRIP_COST_EST,
    _ema, _rsi, _wilder_adx, _vwap,
)

KST = ZoneInfo('Asia/Seoul')
CONTROL_STRATEGY = 'v0.8.0 LOCKED'
ENTRY_START = 9 * 60 + 30
ENTRY_CUTOFF = 14 * 60 + 50
EOD_EXIT = 15 * 60 + 15


def _dt(value):
    return datetime.fromisoformat(str(value)).astimezone(KST)


def _load_rows(code, start=None, end=None):
    sql = 'SELECT bucket,open,high,low,close,volume FROM bars_5m WHERE code=?'
    args = [code]
    if start:
        sql += ' AND bucket>=?'; args.append(start)
    if end:
        sql += ' AND bucket<?'; args.append(end)
    sql += ' ORDER BY bucket'
    with sqlite3.connect(DB_PATH, timeout=10) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(sql, args)]


def available_codes():
    with sqlite3.connect(DB_PATH, timeout=10) as c:
        rows = c.execute('SELECT code,COUNT(*) n,MIN(bucket),MAX(bucket) FROM bars_5m GROUP BY code ORDER BY n DESC').fetchall()
    return [{'code':r[0],'name':instrument_meta(r[0]).get('name') or r[0],'bars':r[1],'first':r[2],'last':r[3]} for r in rows if r[0] not in PROTECTED_CODES]


def _signal(history, session):
    if len(history) < MIN_BARS or len(session) < MIN_SESSION_BARS: return None
    cr = history[-120:]; cl = [float(r['close']) for r in cr]
    e9 = _ema(cl[-60:], 9); e20 = _ema(cl[-80:], 20); rsi = _rsi(cl); adx,pdi,mdi = _wilder_adx(cr)
    last = cr[-1]; price = float(last['close']); vw = _vwap(session)
    prior = cr[-7:-1]
    if len(prior) < 6: return None
    rh = max(float(r['high']) for r in prior); av = sum(int(r['volume']) for r in prior)/len(prior)
    vr = int(last['volume'])/av if av > 0 else 0
    opening = session[:MIN_SESSION_BARS]; oh = max(float(r['high']) for r in opening)
    orb = price > oh; rb = price > rh; above = bool(vw and price > vw); bull = e9 is not None and e20 is not None and e9 > e20
    rok = rsi is not None and MIN_RSI <= rsi <= MAX_RSI
    dok = adx is not None and adx >= MIN_ADX and pdi is not None and mdi is not None and pdi > mdi
    sv = vr >= STRONG_VOLUME_RATIO
    score = 5 + (15 if above else 0) + (15 if bull else 0) + (max(0,min(15,(vr-1)*18.75)) if vr>1 else 0) + (20 if orb else 10 if rb else 0) + (10 if rok else 0) + (min(15,8+max(0,adx-MIN_ADX)*.35) if dok else 0)
    score = min(90, score)
    gate = above and bull and (orb or (rb and sv)) and rok and dok and vr >= MIN_VOLUME_RATIO
    return {'buy':gate and score>=BUY_SCORE,'score':score,'price':price,'rsi':rsi,'adx':adx,'volumeRatio':vr,'orb':orb}


def _simulate_trade(day_rows, entry_idx, signal):
    raw_entry = float(day_rows[entry_idx]['close']); entry = raw_entry*(1+SLIPPAGE_RATE)
    peak = entry; trough = entry; exit_price = None; reason = 'EOD_EXIT'; exit_i = len(day_rows)-1
    for j in range(entry_idx+1, len(day_rows)):
        r=day_rows[j]; t=_dt(r['bucket']); hi=float(r['high']); lo=float(r['low']); cl=float(r['close']); peak=max(peak,hi); trough=min(trough,lo)
        if lo <= entry*(1-STOP_PCT): exit_price=entry*(1-STOP_PCT)*(1-SLIPPAGE_RATE); reason='STOP_LOSS'; exit_i=j; break
        if peak >= entry*(1+TRAIL_ACTIVATE_PCT):
            trail=peak*(1-TRAIL_PCT)
            if lo <= trail: exit_price=trail*(1-SLIPPAGE_RATE); reason='TRAIL_STOP'; exit_i=j; break
        if peak >= entry*(1+BREAKEVEN_ACTIVATE_PCT):
            protect=entry*(1+BREAKEVEN_BUFFER_PCT)
            if lo <= protect: exit_price=protect*(1-SLIPPAGE_RATE); reason='COST_PROTECT'; exit_i=j; break
        if t.hour*60+t.minute >= EOD_EXIT: exit_price=cl*(1-SLIPPAGE_RATE); exit_i=j; break
    if exit_price is None: exit_price=float(day_rows[-1]['close'])*(1-SLIPPAGE_RATE)
    gross=exit_price/entry-1
    net=gross-2*COMMISSION_RATE-SELL_TAX_RATE
    return {'entryAt':day_rows[entry_idx]['bucket'],'exitAt':day_rows[exit_i]['bucket'],'entry':entry,'exit':exit_price,'pnlPct':net*100,'mfePct':(peak/entry-1)*100,'maePct':(trough/entry-1)*100,'reason':reason,'score':signal['score']}


def run_backtest(codes=None, start=None, end=None, max_codes=40):
    coverage=available_codes(); chosen=[x['code'] for x in coverage if not codes or x['code'] in codes][:max(1,min(int(max_codes),100))]
    trades=[]; days=set(); per_code=defaultdict(list)
    for code in chosen:
        rows=_load_rows(code,start,end); by_day=defaultdict(list)
        for r in rows:
            d=_dt(r['bucket']); hm=d.hour*60+d.minute
            if 540 <= hm <= 930: by_day[d.date().isoformat()].append(r)
        history=[]
        for day,dr in sorted(by_day.items()):
            days.add(day); entered=False
            for i,r in enumerate(dr):
                d=_dt(r['bucket']); hm=d.hour*60+d.minute; history.append(r)
                if entered or hm<ENTRY_START or hm>=ENTRY_CUTOFF: continue
                sig=_signal(history,dr[:i+1])
                if sig and sig['buy']:
                    tr=_simulate_trade(dr,i,sig);tr['code']=code;tr['date']=day;tr['name']=instrument_meta(code).get('name') or code;trades.append(tr);per_code[code].append(tr);entered=True
    wins=[t for t in trades if t['pnlPct']>0]; losses=[t for t in trades if t['pnlPct']<=0]
    gp=sum(t['pnlPct'] for t in wins); gl=abs(sum(t['pnlPct'] for t in losses)); pf=gp/gl if gl else (999 if gp else 0)
    equity=peak=1.0;mdd=0
    for t in sorted(trades,key=lambda x:x['entryAt']):
        equity*=1+t['pnlPct']/100;peak=max(peak,equity);mdd=min(mdd,(equity/peak-1)*100)
    buckets=[]
    for lo,hi,label in [(78,85,'78-84'),(85,90,'85-89'),(90,95,'90-94'),(95,101,'95+')]:
        a=[t for t in trades if lo<=t['score']<hi];buckets.append({'bucket':label,'trades':len(a),'winRate':round(sum(t['pnlPct']>0 for t in a)/len(a)*100,1) if a else 0,'avgPnlPct':round(sum(t['pnlPct'] for t in a)/len(a),3) if a else 0})
    return {'ok':True,'controlStrategy':CONTROL_STRATEGY,'liveRuleAutoMutation':False,'source':'NH/local bars_5m','costsIncluded':True,'roundTripCostEstimatePct':round(ROUND_TRIP_COST_EST*100,3),'codesTested':len(chosen),'tradingDays':len(days),'trades':len(trades),'wins':len(wins),'losses':len(losses),'winRate':round(len(wins)/len(trades)*100,2) if trades else 0,'profitFactor':round(pf,3),'expectancyPct':round(sum(t['pnlPct'] for t in trades)/len(trades),4) if trades else 0,'maxDrawdownPct':round(mdd,3),'avgMfePct':round(sum(t['mfePct'] for t in trades)/len(trades),3) if trades else 0,'avgMaePct':round(sum(t['maePct'] for t in trades)/len(trades),3) if trades else 0,'scoreBuckets':buckets,'coverage':coverage[:max(1,min(int(max_codes),100))],'sampleWarning':('표본 부족: 최소 200거래 이상 축적 후 판단 권장' if len(trades)<200 else None),'recentTrades':trades[-50:]}
