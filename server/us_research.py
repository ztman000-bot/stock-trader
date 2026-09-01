"""US Research Data Collector v0.17.4.

Build US point-in-time research data before US Paper/live trading.
Official NH individual-stock period endpoint: /gbstock/quote/v1/period.
Research only: no order endpoint, KR/US statistics remain separate.
"""
import os
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from collector import regular_session as kr_regular_session
from us_collector import NY, US_DATA_ENABLED, US_DB_PATH, US_WATCHLIST, us_call, us_regular_session

US_RESEARCH_ENABLED = US_DATA_ENABLED and os.getenv('US_RESEARCH_ENABLED', 'true').lower() == 'true'
US_RESEARCH_PROXIES = [x.strip().upper() for x in os.getenv('US_RESEARCH_PROXIES', 'SPY,QQQ,IWM').split(',') if x.strip()]
US_RESEARCH_CODES = list(dict.fromkeys(US_WATCHLIST + US_RESEARCH_PROXIES))
US_HISTORY_DAYS = max(10, min(int(os.getenv('US_HISTORY_DAYS', '60')), 120))
US_LIVE_BAR_REFRESH_SEC = max(30, int(os.getenv('US_LIVE_BAR_REFRESH_SEC', '60')))
US_BACKFILL_INTERVAL_HOURS = max(4, int(os.getenv('US_BACKFILL_INTERVAL_HOURS', '12')))
US_PERIOD_COUNT = max(390, min(int(os.getenv('US_PERIOD_COUNT', '0480')), 9999))

_STOP = threading.Event()
_THREAD = None
_LOCK = threading.RLock()
_STATUS = {
    'enabled': US_RESEARCH_ENABLED, 'running': False, 'phase': 'idle',
    'lastCycleAt': None, 'lastSuccessAt': None, 'lastBackfillAt': None,
    'lastError': None, 'liveRefreshes': 0, 'historyCalls': 0,
    'bars1mWritten': 0, 'bars5mWritten': 0, 'snapshotsWritten': 0,
    'paperEnabled': False, 'realOrderEnabled': False,
    'pointInTime': True, 'krUsStatsSeparate': True,
}


def _conn():
    c = sqlite3.connect(US_DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def init_db():
    with _conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS us_bars_1m(
          ticker TEXT NOT NULL,bucket TEXT NOT NULL,session_date TEXT NOT NULL,
          open REAL NOT NULL,high REAL NOT NULL,low REAL NOT NULL,close REAL NOT NULL,
          volume REAL NOT NULL DEFAULT 0,dollar_volume REAL NOT NULL DEFAULT 0,
          source TEXT NOT NULL DEFAULT 'nh_period_1m',PRIMARY KEY(ticker,bucket));
        CREATE INDEX IF NOT EXISTS idx_us_1m_date ON us_bars_1m(session_date,ticker,bucket);
        CREATE TABLE IF NOT EXISTS us_bars_5m(
          ticker TEXT NOT NULL,bucket TEXT NOT NULL,session_date TEXT NOT NULL,
          open REAL NOT NULL,high REAL NOT NULL,low REAL NOT NULL,close REAL NOT NULL,
          volume REAL NOT NULL DEFAULT 0,dollar_volume REAL NOT NULL DEFAULT 0,
          source TEXT NOT NULL DEFAULT 'agg_1m',PRIMARY KEY(ticker,bucket));
        CREATE INDEX IF NOT EXISTS idx_us_5m_date ON us_bars_5m(session_date,ticker,bucket);
        CREATE TABLE IF NOT EXISTS us_research_snapshots(
          ticker TEXT NOT NULL,bucket TEXT NOT NULL,session_date TEXT NOT NULL,
          gap_pct REAL,change_pct REAL,vwap REAL,ema9 REAL,ema20 REAL,orb30_high REAL,
          rvol_tod REAL,volume_accel REAL,dollar_volume REAL,range_pct REAL,high_proximity REAL,
          stocks_in_play_score REAL,spy_change_pct REAL,qqq_change_pct REAL,iwm_change_pct REAL,
          created_at TEXT NOT NULL,PRIMARY KEY(ticker,bucket));
        CREATE INDEX IF NOT EXISTS idx_us_snap_date_score ON us_research_snapshots(session_date,stocks_in_play_score DESC);
        CREATE TABLE IF NOT EXISTS us_research_fetch_log(
          ticker TEXT NOT NULL,session_date TEXT NOT NULL,status TEXT NOT NULL,rows INTEGER NOT NULL DEFAULT 0,
          fetched_at TEXT NOT NULL,PRIMARY KEY(ticker,session_date));
        ''')


def _num(v):
    try:
        if v is None or v == '': return None
        return float(str(v).replace(',', '').strip())
    except: return None


def _output1(data):
    if not isinstance(data, dict): return []
    for k in ('Output_1','output_1'):
        v=data.get(k)
        if isinstance(v,list): return v
    return []


def _period_1m(ticker,end_dt,today_only=False):
    return us_call('/gbstock/quote/v1/period',{
        'iem_cd':ticker,'end_dt':end_dt,'count':f'{US_PERIOD_COUNT:04d}','maxavg':'000',
        'gubun':'2','xtick':'0001','today_cls':'1' if today_only else '0','market_cls':'1'})


def _parse_rows(data,wanted_date=None):
    out=[]
    for r in _output1(data):
        ds=str(r.get('trade_date') or r.get('bsop_date') or '').strip();ts=str(r.get('trade_time') or '').strip().zfill(6)
        if len(ds)!=8 or len(ts)!=6: continue
        try:
            dt=datetime.strptime(ds+ts,'%Y%m%d%H%M%S').replace(tzinfo=NY)
            if wanted_date and dt.date().isoformat()!=wanted_date: continue
            hm=dt.hour*60+dt.minute
            if not 570<=hm<960: continue
            o,h,l,cl=[_num(r.get(k)) for k in ('open_prc','high','low','close_prc')]
            if not all(x is not None and x>0 for x in (o,h,l,cl)): continue
            vol=max(0.0,_num(r.get('movolume')) or 0.0);value=max(0.0,_num(r.get('movalue')) or cl*vol)
            out.append({'bucket':dt.isoformat(),'session_date':dt.date().isoformat(),'open':o,'high':h,'low':l,'close':cl,'volume':vol,'dollar_volume':value})
        except: continue
    out.sort(key=lambda x:x['bucket']);return out


def _write_1m(ticker,rows):
    if not rows:return 0
    with _conn() as c:
        c.executemany('''INSERT INTO us_bars_1m(ticker,bucket,session_date,open,high,low,close,volume,dollar_volume)
          VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(ticker,bucket) DO UPDATE SET open=excluded.open,high=excluded.high,
          low=excluded.low,close=excluded.close,volume=excluded.volume,dollar_volume=excluded.dollar_volume''',
          [(ticker,r['bucket'],r['session_date'],r['open'],r['high'],r['low'],r['close'],r['volume'],r['dollar_volume']) for r in rows])
    return len(rows)


def _aggregate_5m(ticker,session_date):
    with _conn() as c:src=[dict(r) for r in c.execute('SELECT * FROM us_bars_1m WHERE ticker=? AND session_date=? ORDER BY bucket',(ticker,session_date))]
    groups=defaultdict(list)
    for r in src:
        try:
            dt=datetime.fromisoformat(r['bucket']).astimezone(NY);b=dt.replace(minute=(dt.minute//5)*5,second=0,microsecond=0).isoformat();groups[b].append(r)
        except:pass
    rows=[]
    for b,items in sorted(groups.items()):
        if len(items)<5:continue
        items.sort(key=lambda x:x['bucket'])
        rows.append((ticker,b,session_date,float(items[0]['open']),max(float(x['high']) for x in items),min(float(x['low']) for x in items),
                     float(items[-1]['close']),sum(float(x['volume']) for x in items),sum(float(x['dollar_volume']) for x in items)))
    if rows:
        with _conn() as c:
            c.executemany('''INSERT INTO us_bars_5m(ticker,bucket,session_date,open,high,low,close,volume,dollar_volume)
              VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(ticker,bucket) DO UPDATE SET open=excluded.open,high=excluded.high,
              low=excluded.low,close=excluded.close,volume=excluded.volume,dollar_volume=excluded.dollar_volume''',rows)
    return len(rows)


def _proxy_map(session_date,ticker):
    with _conn() as c:rows=[dict(r) for r in c.execute('SELECT * FROM us_bars_5m WHERE ticker=? AND session_date=? ORDER BY bucket',(ticker,session_date))]
    if not rows:return {}
    op=float(rows[0]['open']);out={}
    for r in rows:
        dt=datetime.fromisoformat(r['bucket']).astimezone(NY);out[dt.strftime('%H:%M')]=(float(r['close'])/op-1)*100 if op>0 else 0
    return out


def _score(change_pct,gap_pct,rvol,accel,high_prox,above_vwap):
    rvol_pts=max(0,min(30,(rvol-1)*20)) if rvol is not None else 0
    accel_pts=max(0,min(20,(accel-1)*15)) if accel is not None else 0
    return round(max(0,min(100,rvol_pts+accel_pts+max(0,min(20,change_pct*5))+max(0,min(10,gap_pct*2.5))+
                               max(0,min(10,(high_prox-.97)/.03*10))+(10 if above_vwap else 0))),2)


def _build_snapshots(ticker,target_date):
    if ticker in US_RESEARCH_PROXIES:return 0
    with _conn() as c:rows=[dict(r) for r in c.execute('SELECT * FROM us_bars_5m WHERE ticker=? ORDER BY bucket',(ticker,))]
    if not rows:return 0
    spy=_proxy_map(target_date,'SPY');qqq=_proxy_map(target_date,'QQQ');iwm=_proxy_map(target_date,'IWM')
    tod_hist=defaultdict(list);ema9=ema20=None;alpha9=2/10;alpha20=2/21;day=None;prev_close=None;session=[];cum_pv=cum_vol=0.0;written=[];last_close_by_day={}
    for r in rows:
        dt=datetime.fromisoformat(r['bucket']).astimezone(NY);d=dt.date().isoformat();cl=float(r['close']);vol=float(r['volume'])
        if d!=day:
            if day and session:last_close_by_day[day]=float(session[-1]['close'])
            prev_close=last_close_by_day.get(day) if day else None;day=d;session=[];cum_pv=cum_vol=0.0
        ema9=cl if ema9 is None else alpha9*cl+(1-alpha9)*ema9;ema20=cl if ema20 is None else alpha20*cl+(1-alpha20)*ema20
        if d!=target_date:
            tod_hist[dt.strftime('%H:%M')].append(vol);session.append(r);continue
        session.append(r);op=float(session[0]['open']);hi=max(float(x['high']) for x in session);lo=min(float(x['low']) for x in session)
        typical=(float(r['high'])+float(r['low'])+cl)/3;cum_pv+=typical*vol;cum_vol+=vol;vw=cum_pv/cum_vol if cum_vol>0 else cl
        tod=dt.strftime('%H:%M');prior_tod=tod_hist[tod][-20:];rvol=vol/(sum(prior_tod)/len(prior_tod)) if prior_tod and sum(prior_tod)>0 else None
        prior4=[float(x['volume']) for x in session[-5:-1]];accel=vol/(sum(prior4)/len(prior4)) if prior4 and sum(prior4)>0 else None
        gap=((op/prev_close)-1)*100 if prev_close and prev_close>0 else 0.0;change=((cl/op)-1)*100 if op>0 else 0.0
        range_pct=((hi-lo)/op)*100 if op>0 else 0.0;prox=cl/hi if hi>0 else 0;orb=max(float(x['high']) for x in session[:6]) if session else None
        score=_score(change,gap,rvol,accel,prox,cl>vw)
        written.append((ticker,r['bucket'],d,gap,change,vw,ema9,ema20,orb,rvol,accel,float(r['dollar_volume']),range_pct,prox,score,
                        spy.get(tod),qqq.get(tod),iwm.get(tod),datetime.now(timezone.utc).isoformat()))
        tod_hist[tod].append(vol)
    if written:
        with _conn() as c:
            c.executemany('''INSERT INTO us_research_snapshots(ticker,bucket,session_date,gap_pct,change_pct,vwap,ema9,ema20,orb30_high,rvol_tod,volume_accel,dollar_volume,range_pct,high_proximity,stocks_in_play_score,spy_change_pct,qqq_change_pct,iwm_change_pct,created_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ticker,bucket) DO UPDATE SET gap_pct=excluded.gap_pct,change_pct=excluded.change_pct,
              vwap=excluded.vwap,ema9=excluded.ema9,ema20=excluded.ema20,orb30_high=excluded.orb30_high,rvol_tod=excluded.rvol_tod,
              volume_accel=excluded.volume_accel,dollar_volume=excluded.dollar_volume,range_pct=excluded.range_pct,high_proximity=excluded.high_proximity,
              stocks_in_play_score=excluded.stocks_in_play_score,spy_change_pct=excluded.spy_change_pct,qqq_change_pct=excluded.qqq_change_pct,
              iwm_change_pct=excluded.iwm_change_pct,created_at=excluded.created_at''',written)
    return len(written)


def _fetch_logged(ticker,session_date):
    with _conn() as c:r=c.execute('SELECT status,rows FROM us_research_fetch_log WHERE ticker=? AND session_date=?',(ticker,session_date)).fetchone()
    return dict(r) if r else None


def _log_fetch(ticker,session_date,n):
    status='COMPLETE' if n>=300 else 'PARTIAL' if n>0 else 'EMPTY'
    with _conn() as c:c.execute('''INSERT INTO us_research_fetch_log(ticker,session_date,status,rows,fetched_at) VALUES(?,?,?,?,?)
      ON CONFLICT(ticker,session_date) DO UPDATE SET status=excluded.status,rows=excluded.rows,fetched_at=excluded.fetched_at''',
      (ticker,session_date,status,n,datetime.now(timezone.utc).isoformat()))


def _refresh_date(session_date,today_only=False,skip_complete=False):
    total1=total5=0
    for ticker in US_RESEARCH_CODES:
        if _STOP.is_set():break
        if skip_complete:
            old=_fetch_logged(ticker,session_date)
            if old and old.get('status')=='COMPLETE':continue
        data=_period_1m(ticker,session_date.replace('-',''),today_only=today_only);rows=_parse_rows(data,session_date);n=_write_1m(ticker,rows);total1+=n
        total5+=_aggregate_5m(ticker,session_date);_log_fetch(ticker,session_date,n)
        with _LOCK:_STATUS['historyCalls']+=1
    snaps=sum(_build_snapshots(ticker,session_date) for ticker in US_WATCHLIST)
    with _LOCK:_STATUS['bars1mWritten']+=total1;_STATUS['bars5mWritten']+=total5;_STATUS['snapshotsWritten']+=snaps
    return {'bars1m':total1,'bars5m':total5,'snapshots':snaps}


def _history_dates(n):
    now=datetime.now(NY);d=now.date()
    if now.weekday()<5 and now.hour*60+now.minute<960:d-=timedelta(days=1)
    out=[]
    while len(out)<n:
        if d.weekday()<5:out.append(d.isoformat())
        d-=timedelta(days=1)
    return list(reversed(out))


def latest_us_research(limit=20):
    init_db();limit=max(1,min(int(limit),100))
    with _conn() as c:
        rows=[dict(r) for r in c.execute('''SELECT s.* FROM us_research_snapshots s
          WHERE s.bucket=(SELECT MAX(s2.bucket) FROM us_research_snapshots s2 WHERE s2.ticker=s.ticker)
          ORDER BY s.stocks_in_play_score DESC LIMIT ?''',(limit,))]
    return rows


def research_status():
    init_db()
    with _conn() as c:
        one=c.execute('SELECT COUNT(*),COUNT(DISTINCT session_date) FROM us_bars_1m').fetchone();five=c.execute('SELECT COUNT(*),COUNT(DISTINCT session_date) FROM us_bars_5m').fetchone()
        snap=c.execute('SELECT COUNT(*),COUNT(DISTINCT session_date) FROM us_research_snapshots').fetchone();latest=c.execute('SELECT MAX(bucket) FROM us_research_snapshots').fetchone()[0]
        quality=[dict(r) for r in c.execute('SELECT status,COUNT(*) n FROM us_research_fetch_log GROUP BY status')]
    with _LOCK:s=dict(_STATUS)
    return {**s,'watchlist':list(US_WATCHLIST),'researchProxies':list(US_RESEARCH_PROXIES),'historyDaysTarget':US_HISTORY_DAYS,
            'bars1m':int(one[0]),'bar1mDays':int(one[1]),'bars5m':int(five[0]),'bar5mDays':int(five[1]),'snapshots':int(snap[0]),
            'snapshotDays':int(snap[1]),'latestSnapshot':latest,'fetchQuality':quality,'usRegularSession':us_regular_session(),
            'krRegularSession':kr_regular_session(),'researchOnly':True,'paperEnabled':False,'realOrderEnabled':False}


def _backfill_due():
    last=_STATUS.get('lastBackfillAt')
    if not last:return True
    try:return (datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds()>=US_BACKFILL_INTERVAL_HOURS*3600
    except:return True


def _run():
    with _LOCK:_STATUS.update({'running':True,'phase':'startup','lastError':None})
    _STOP.wait(8)
    while not _STOP.is_set():
        try:
            now=datetime.now(NY);today=now.date().isoformat()
            if us_regular_session():
                with _LOCK:_STATUS['phase']='us-live-1m-point-in-time'
                _refresh_date(today,today_only=True,skip_complete=False)
                with _LOCK:_STATUS['liveRefreshes']+=1;_STATUS['lastSuccessAt']=datetime.now(timezone.utc).isoformat();_STATUS['lastError']=None
                _STOP.wait(US_LIVE_BAR_REFRESH_SEC);continue
            if kr_regular_session():
                with _LOCK:_STATUS['phase']='kr-live-priority-deferred'
                _STOP.wait(60);continue
            if _backfill_due():
                with _LOCK:_STATUS['phase']='us-history-backfill'
                for d in _history_dates(US_HISTORY_DAYS):
                    if _STOP.is_set() or us_regular_session() or kr_regular_session():break
                    _refresh_date(d,today_only=False,skip_complete=True)
                with _LOCK:_STATUS['lastBackfillAt']=datetime.now(timezone.utc).isoformat();_STATUS['lastSuccessAt']=_STATUS['lastBackfillAt'];_STATUS['lastError']=None
            with _LOCK:_STATUS['phase']='idle';_STATUS['lastCycleAt']=datetime.now(timezone.utc).isoformat()
            _STOP.wait(120)
        except Exception as e:
            with _LOCK:_STATUS.update({'phase':'error','lastError':f'{type(e).__name__}: {e}'[:700],'lastCycleAt':datetime.now(timezone.utc).isoformat()})
            _STOP.wait(30)
    with _LOCK:_STATUS.update({'running':False,'phase':'stopped'})


def start():
    global _THREAD
    init_db()
    if not US_RESEARCH_ENABLED:return research_status()
    with _LOCK:
        if _THREAD and _THREAD.is_alive():return research_status()
        _STOP.clear();_THREAD=threading.Thread(target=_run,name='us-research-collector',daemon=True);_THREAD.start()
    return research_status()


def stop():
    _STOP.set()
    if _THREAD and _THREAD.is_alive():_THREAD.join(timeout=5)
    return research_status()


init_db()
