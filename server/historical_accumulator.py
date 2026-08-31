"""Multi-day NH 5m data accumulator for backtesting.

Research/data only: no order endpoint and no live-strategy mutation.
Downloads one requested trading date at a time, caches into bars_5m, and is resumable.
"""
import sqlite3
import threading
from datetime import datetime, timedelta

from collector import DB_PATH, KST, nh_call, collector, PROTECTED_CODES

_LOCK = threading.Lock()
_STOP = threading.Event()
_THREAD = None
STATUS = {
    'running': False, 'startedAt': None, 'finishedAt': None, 'lastError': None,
    'targetDays': 0, 'targetCodes': 0, 'completedJobs': 0, 'totalJobs': 0,
    'writtenBars': 0, 'skippedBars': 0, 'failedJobs': 0, 'currentCode': None,
    'currentDate': None, 'requestedDays': 20, 'requestedCodes': 40,
}


def _period_rows(payload):
    if not isinstance(payload, dict): return []
    for key in ('Output_1','output_1','Output_0','output_0'):
        value = payload.get(key)
        if isinstance(value, list): return value
    return []


def _trading_dates(days):
    # Calendar filter only; exchange holidays simply return no rows and are safely skipped.
    out=[]; d=datetime.now(KST).date()
    while len(out) < days:
        if d.weekday() < 5: out.append(d)
        d -= timedelta(days=1)
    return out


def _existing_count(code, day):
    prefix=day.strftime('%Y-%m-%d')
    with sqlite3.connect(DB_PATH, timeout=10) as c:
        return int(c.execute("SELECT COUNT(*) FROM bars_5m WHERE code=? AND substr(bucket,1,10)=?",(code,prefix)).fetchone()[0])


def _download_day(code, day):
    # A normal full KRX day has ~78 five-minute bars. If >=70 already exist, keep cache.
    existing=_existing_count(code, day)
    if existing >= 70: return {'cached':True,'written':0,'skipped':existing,'received':existing}
    today=datetime.now(KST).date()
    payload=nh_call('/krstock/quote/v1/period',{
        'market_cd':'KRX','iem_cd':code,'edate':day.strftime('%Y%m%d'),
        'array_cnt':'120','gubun':'5','xtick':'5',
        'today_cls_code':'1' if day==today else '0','fake_tick':'1'
    })
    rows=_period_rows(payload); written=skipped=0
    with sqlite3.connect(DB_PATH, timeout=10) as c:
        c.execute('PRAGMA journal_mode=WAL')
        for r in rows:
            ds=str(r.get('bsop_date') or '').strip(); ts=str(r.get('bsop_time') or '').strip().zfill(6)
            if ds != day.strftime('%Y%m%d'): skipped += 1; continue
            try:
                dt=datetime.strptime(ds+ts,'%Y%m%d%H%M%S').replace(tzinfo=KST)
                dt=dt.replace(minute=(dt.minute//5)*5,second=0,microsecond=0)
                hm=dt.hour*60+dt.minute
                o=float(r.get('stck_oprc') or 0); h=float(r.get('stck_hgpr') or 0)
                l=float(r.get('stck_lwpr') or 0); cl=float(r.get('stck_prpr') or 0)
                v=int(float(r.get('vol') or 0))
            except Exception: skipped += 1; continue
            if not 540 <= hm <= 930 or min(o,h,l,cl) <= 0: skipped += 1; continue
            c.execute('''INSERT INTO bars_5m(code,bucket,open,high,low,close,volume,sample_count)
                VALUES(?,?,?,?,?,?,?,0) ON CONFLICT(code,bucket) DO UPDATE SET
                open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
                volume=excluded.volume,sample_count=0''',(code,dt.isoformat(),o,h,l,cl,v))
            written += 1
    return {'cached':False,'written':written,'skipped':skipped,'received':len(rows)}


def _worker(days, max_codes):
    try:
        collector.wait_for_universe(timeout=20)
        codes=[c for c in list(collector.watchlist) if c not in PROTECTED_CODES][:max_codes]
        dates=_trading_dates(days)
        with _LOCK:
            STATUS.update({'running':True,'startedAt':datetime.now(KST).isoformat(),'finishedAt':None,
                'lastError':None,'targetDays':len(dates),'targetCodes':len(codes),'completedJobs':0,
                'totalJobs':len(dates)*len(codes),'writtenBars':0,'skippedBars':0,'failedJobs':0,
                'requestedDays':days,'requestedCodes':max_codes})
        for day in dates:
            for code in codes:
                if _STOP.is_set(): return
                with _LOCK: STATUS.update({'currentCode':code,'currentDate':day.isoformat()})
                try:
                    result=_download_day(code,day)
                    with _LOCK:
                        STATUS['writtenBars'] += result['written']; STATUS['skippedBars'] += result['skipped']
                except Exception as exc:
                    with _LOCK:
                        STATUS['failedJobs'] += 1; STATUS['lastError']=f'{code} {day}: {type(exc).__name__}: {exc}'[:700]
                finally:
                    with _LOCK: STATUS['completedJobs'] += 1
    finally:
        with _LOCK:
            STATUS.update({'running':False,'finishedAt':datetime.now(KST).isoformat(),'currentCode':None,'currentDate':None})


def start(days=20, max_codes=40):
    global _THREAD
    days=max(1,min(int(days),120)); max_codes=max(1,min(int(max_codes),100))
    with _LOCK:
        if STATUS['running']: return {'ok':False,'message':'이미 과거 데이터 수집 중입니다.','status':dict(STATUS)}
        _STOP.clear(); STATUS['running']=True
    _THREAD=threading.Thread(target=_worker,args=(days,max_codes),name='historical-5m-accumulator',daemon=True)
    _THREAD.start(); return {'ok':True,'message':'과거 5분봉 수집을 시작했습니다.','status':status()}


def stop():
    _STOP.set(); return {'ok':True,'message':'중지 요청됨','status':status()}


def status():
    with _LOCK:
        s=dict(STATUS)
    total=max(1,int(s.get('totalJobs') or 0)); s['progressPct']=round(int(s.get('completedJobs') or 0)/total*100,1) if s.get('totalJobs') else 0
    return s
