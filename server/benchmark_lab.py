"""Public Strategy Benchmark Lab v0.17.3.

Research-only comparison on the SAME locally stored Korean 5-minute data and cost model.
This is an adaptation/benchmark, not an exact reproduction of any published US result.

Benchmarks:
A) Public-style 5m ORB + first-5m RVOL(14d) Stocks-in-Play Top20 + ATR stop + EOD exit.
B) Our fixed ORB improvement: ORB+RVOL -> Stocks-in-Play 60+ -> no RED -> EMA9/VWAP pullback -> dynamic winner/failure exit.
C) Cross Trend 2.0 fixed baseline with the existing control exit.

All three use GOOD data only, next-bar execution, commission/tax/slippage, chronological
walk-forward-style time slices, untouched final lockbox, and 2x-slippage + 1-bar-late stress.
No broker/order call exists here and no live rule is mutated.
"""
from collections import defaultdict
from statistics import mean

from backtest_engine import _load_rows,_dt,available_codes
from data_quality import quality_map
from market_lab import _build_regime
from strategy_lab import _signals
from stocks_in_play import historical_score
from profitability_lab import (
    _metrics,_pnl,_resolve_entry_index,_atr_pct,
    EXIT_CONFIGS,ENTRY_MODES,BASE_SLIPPAGE,COMMISSION,SELL_TAX,
)

PUBLIC_TOP_N=20
PUBLIC_RVOL_LOOKBACK=14
PUBLIC_MIN_RVOL=1.0
PUBLIC_ENTRY_CUTOFF=14*60+50


def _cfg(cid):return next(x for x in EXIT_CONFIGS if x['id']==cid)
def _entry_mode(mid):return next(x for x in ENTRY_MODES if x['id']==mid)


def _net_public(rows,i,stop_pct,slippage=BASE_SLIPPAGE,late_bars=0):
    """Fixed public-style ATR-stop/EOD exit, conservative next-bar execution."""
    day=_dt(rows[i]['bucket']).date();i=i+max(0,int(late_bars))
    if i>=len(rows) or _dt(rows[i]['bucket']).date()!=day:return None
    entry=float(rows[i]['open'])*(1+slippage);last=float(rows[i]['open']);exitp=None
    stop=max(.004,min(.020,float(stop_pct or .010)))
    for j in range(i,min(len(rows),i+78)):
        r=rows[j];dt=_dt(r['bucket'])
        if dt.date()!=day:break
        lo=float(r['low']);cl=float(r['close']);last=cl
        if lo<=entry*(1-stop):exitp=entry*(1-stop);break
        if dt.hour*60+dt.minute>=915:exitp=cl;break
    if exitp is None:exitp=last
    return ((exitp*(1-slippage)/entry)-1-(2*COMMISSION)-SELL_TAX)*100


def _first5_records(code,rows,qmap):
    days=defaultdict(list)
    for idx,r in enumerate(rows):
        dt=_dt(r['bucket']);hm=dt.hour*60+dt.minute
        if 540<=hm<=930:days[dt.date()].append((idx,r))
    out=[];history=[]
    for day,arr in sorted(days.items()):
        arr=sorted(arr,key=lambda x:x[1]['bucket']);key=(str(code),day.isoformat())
        first=next(((idx,r) for idx,r in arr if 540<=(_dt(r['bucket']).hour*60+_dt(r['bucket']).minute)<545),None)
        if first is None:continue
        idx,r=first;vol=max(0,int(r['volume']));prev=list(history[-PUBLIC_RVOL_LOOKBACK:])
        if qmap.get(key)=='GOOD' and len(prev)>=PUBLIC_RVOL_LOOKBACK:
            av=mean(prev) if prev else 0;rvol=(vol/av) if av>0 else 0
            out.append({'code':code,'date':day,'first_i':idx,'open_high':float(r['high']),'rvol':rvol,'rows':rows})
        if qmap.get(key)=='GOOD':history.append(vol)
    return out


def _public_candidates(codes,qmap):
    by_day=defaultdict(list)
    for code in codes:
        rows=_load_rows(code)
        for rec in _first5_records(code,rows,qmap):by_day[rec['date']].append(rec)
    selected=[]
    for day,arr in sorted(by_day.items()):
        ranked=sorted((x for x in arr if x['rvol']>=PUBLIC_MIN_RVOL),key=lambda x:x['rvol'],reverse=True)[:PUBLIC_TOP_N]
        for rec in ranked:
            rows=rec['rows'];first_i=rec['first_i'];entry_i=None
            for j in range(first_i+1,min(len(rows)-1,first_i+70)):
                dt=_dt(rows[j]['bucket']);hm=dt.hour*60+dt.minute
                if dt.date()!=day or hm>=PUBLIC_ENTRY_CUTOFF:break
                if float(rows[j]['high'])>rec['open_high']:
                    nxt=j+1
                    if nxt<len(rows) and _dt(rows[nxt]['bucket']).date()==day:entry_i=nxt
                    break
            if entry_i is None:continue
            ap=_atr_pct(rows,max(1,entry_i-1),14);stop=max(.006,min(.015,ap if ap is not None else .010))
            selected.append({**rec,'i':entry_i,'atrStop':stop})
    return selected


def _our_orb_candidates(codes,qmap,evaluation_dates):
    regime=_build_regime(codes);out=[]
    for code in codes:
        rows,sigs=_signals(code,'orb_rvol');used=set()
        for i in sigs:
            if i<=0 or i>=len(rows):continue
            day=_dt(rows[i]['bucket']).date();key=(str(code),day.isoformat())
            if day not in evaluation_dates or qmap.get(key)!='GOOD' or day in used:continue
            used.add(day);sig_bucket=rows[i-1]['bucket']
            if regime.get(sig_bucket,{}).get('label','UNKNOWN')=='RED':continue
            sip=historical_score(rows,i)
            if not sip or float(sip.get('score') or 0)<60:continue
            ei=_resolve_entry_index(rows,i,_entry_mode('pullback_support'))
            if ei is None:continue
            out.append({'code':code,'date':day,'rows':rows,'i':ei,'sipScore':sip.get('score')})
    return out


def _cross_candidates(codes,qmap,evaluation_dates):
    out=[]
    for code in codes:
        rows,sigs=_signals(code,'cross_trend_v2');used=set()
        for i in sigs:
            if i<=0 or i>=len(rows):continue
            day=_dt(rows[i]['bucket']).date();key=(str(code),day.isoformat())
            if day not in evaluation_dates or qmap.get(key)!='GOOD' or day in used:continue
            used.add(day);out.append({'code':code,'date':day,'rows':rows,'i':i})
    return out


def _records_public(cands,dates=None,slippage=BASE_SLIPPAGE,late_bars=0):
    out=[]
    for x in cands:
        if dates is not None and x['date'] not in dates:continue
        p=_net_public(x['rows'],x['i'],x['atrStop'],slippage,late_bars)
        if p is not None:out.append({'date':x['date'],'pnl':p})
    return out


def _records_cfg(cands,cfg,dates=None,slippage=BASE_SLIPPAGE,late_bars=0):
    out=[]
    for x in cands:
        if dates is not None and x['date'] not in dates:continue
        p=_pnl(x['rows'],x['i'],cfg,slippage,late_bars)
        if p is not None:out.append({'date':x['date'],'pnl':p})
    return out


def _met(records):return _metrics([x['pnl'] for x in records])


def _date_plan(dates,folds=4):
    dates=sorted(set(dates));n=len(dates)
    if n<10:return [],set(dates[-max(1,n//5):]) if dates else set()
    lock_n=max(2,int(n*.20));dev=dates[:-lock_n];lock=set(dates[-lock_n:]);tests=[]
    if len(dev)<4:return [],lock
    step=max(1,len(dev)//(folds+1))
    for k in range(folds):
        start=min(len(dev),step*(k+1));end=min(len(dev),start+step)
        if end>start:tests.append(set(dev[start:end]))
    return tests,lock


def _evaluate(name,kind,cands,dates,tests,lock,cfg=None):
    if kind=='public_orb':
        base=lambda ds=None,sl=BASE_SLIPPAGE,late=0:_records_public(cands,ds,sl,late)
    else:
        base=lambda ds=None,sl=BASE_SLIPPAGE,late=0:_records_cfg(cands,cfg,ds,sl,late)
    full=_met(base(set(dates)));fold_rows=[]
    for ds in tests:fold_rows.append(_met(base(ds)))
    positive=sum(1 for m in fold_rows if m['profitFactor']>1 and m['expectancyPct']>0)
    lock_m=_met(base(lock));stress=_met(base(lock,BASE_SLIPPAGE*2,1))
    return {
        'id':kind,'name':name,'full':full,
        'walkForward':{'folds':len(fold_rows),'positiveFolds':positive,'results':fold_rows,'selectionIndependent':True},
        'lockbox':lock_m,'lockboxStress':stress,
        'pass':bool(lock_m['trades']>=10 and lock_m['profitFactor']>1 and lock_m['expectancyPct']>0 and stress['profitFactor']>=1 and stress['expectancyPct']>=0)
    }


def run_benchmark_lab(max_codes=40,profitability=None,robust=None):
    max_codes=max(10,min(int(max_codes),100));codes=[x['code'] for x in available_codes()[:max_codes]];qmap=quality_map(120)
    public=_public_candidates(codes,qmap);evaluation_dates=sorted({x['date'] for x in public});tests,lock=_date_plan(evaluation_dates)
    our=_our_orb_candidates(codes,qmap,set(evaluation_dates));cross=_cross_candidates(codes,qmap,set(evaluation_dates))
    a=_evaluate('Public-style 5m ORB + RVOL14 Top20','public_orb',public,evaluation_dates,tests,lock)
    b=_evaluate('Our ORB + SIP60 + Pullback + Winner/Fast-Failure','our_orb_pf',our,evaluation_dates,tests,lock,_cfg('winner_extension'))
    c=_evaluate('Cross Trend 2.0 fixed baseline','cross_v2',cross,evaluation_dates,tests,lock,_cfg('control_exit'))
    rows=[a,b,c]
    rows.sort(key=lambda x:(x['lockbox']['profitFactor']>1,x['lockbox']['expectancyPct'],x['full']['profitFactor']),reverse=True)
    leader=rows[0]['id'] if rows else None;current={}
    if profitability:
        best=profitability.get('best') or {}
        current['profitabilityLeader']={'strategy':best.get('strategy'),'entry':best.get('entryMode'),'exit':best.get('exit'),'full':best.get('full'),'oos':best.get('oos')}
    if robust:
        current['currentRobustLockbox']=robust.get('lockbox');current['currentRobustStress']=robust.get('lockboxStress');current['currentRobustPass']=robust.get('pass')
    return {
        'ok':True,'version':'0.17.3','researchOnly':True,'controlStrategy':'v0.8.0 LOCKED','realOrderEnabled':False,
        'adaptationNotice':'Published US ORB concepts are adapted to the locally stored Korean 5m safe-universe subset. This is not an exact paper replication and published US performance must not be compared directly.',
        'commonEvaluationDays':len(evaluation_dates),'lockboxDays':len(lock),'codesTested':len(codes),
        'costModel':{'commissionEachSide':COMMISSION,'sellTax':SELL_TAX,'baseSlippageEachSide':BASE_SLIPPAGE,'stress':'2x slippage + 1 bar late'},
        'benchmarks':rows,'benchmarkLeader':leader,'currentResearch':current,
        'interpretation':'If a simple public-style benchmark beats the complex strategy on lockbox/stress, complexity is not yet justified. Prefer the simpler strategy until the added filters improve out-of-sample payoff robustly.'
    }
