"""Cross-independent Stocks-in-Play research radar.
Live snapshots are recorded every 5 minutes during KRX hours. Historical scoring uses
only bars available up to the signal, so it can be compared without look-ahead.
No broker/order calls exist here.
"""
import sqlite3
import threading
from datetime import datetime
from collector import (
    DB_PATH,KST,latest_quotes,activity_metrics,instrument_meta,is_safe_code,
    universe_verified,collector,PROTECTED_CODES,regular_session
)

_RECORDER_STARTED=False
_RECORDER_STOP=threading.Event()
_RECORDER_STATE={'running':False,'lastSnapshotAt':None,'lastError':None,'snapshots':0,'rows':0,'intervalSec':300}

def scan(limit=40):
    rows=[]
    for q in latest_quotes(getattr(collector,'watchlist',[]) or None):
        code=str(q.get('code') or '')
        if not code or code in PROTECTED_CODES:continue
        if universe_verified() and not is_safe_code(code):continue
        m=activity_metrics(q);price=float(q.get('price') or 0);op=float(q.get('day_open') or 0);hi=float(q.get('day_high') or 0);change=float(q.get('change_rate') or 0);score=float(m.get('activityScore') or 0)
        if op>0 and price>0:
            openret=(price/op-1)*100
            if openret>=1:score+=10
            if openret>=2:score+=8
            if hi>0 and price>=hi*.995:score+=8
        if abs(change)>=2:score+=8
        if float(m.get('turnoverEok') or 0)>=20:score+=8
        rows.append({
            'code':code,'name':instrument_meta(code).get('name') or q.get('name') or code,
            'score':round(min(100,score),2),'price':price,'changeRate':change,
            'turnoverEok':m.get('turnoverEok'),'rangePct':m.get('rangePct'),
            'spreadPct':m.get('spreadPct'),'liquidityOk':m.get('liquidityOk'),
            'source':'SAFE_UNIVERSE_ACTIVITY','crossRequired':False
        })
    rows.sort(key=lambda x:(bool(x['liquidityOk']),x['score'],x['turnoverEok'] or 0),reverse=True)
    return {'ok':True,'researchOnly':True,'crossIndependent':True,'universeSize':len(getattr(collector,'watchlist',[]) or []),'rows':rows[:max(1,min(int(limit),100))]}

def historical_score(rows,signal_i):
    """Point-in-time score using only completed bars through signal_i-1."""
    i=max(0,int(signal_i)-1)
    if i>=len(rows):return None
    try:day=datetime.fromisoformat(str(rows[i]['bucket'])).astimezone(KST).date()
    except Exception:return None
    start=max(0,i-90);session=[]
    for r in rows[start:i+1]:
        try:
            dt=datetime.fromisoformat(str(r['bucket'])).astimezone(KST)
        except Exception:
            continue
        hm=dt.hour*60+dt.minute
        if dt.date()==day and 540<=hm<=930:session.append(r)
    if len(session)<6:return None
    op=float(session[0]['open']);price=float(session[-1]['close']);hi=max(float(x['high']) for x in session);lo=min(float(x['low']) for x in session)
    if min(op,price,lo)<=0:return None
    turnover=sum(float(x['close'])*max(0,int(x['volume'])) for x in session)/100_000_000
    openret=(price/op-1)*100;rng=(hi/lo-1)*100;score=0.0
    if turnover>=10:score+=20
    if turnover>=30:score+=10
    if openret>=1:score+=15
    if openret>=2:score+=10
    if rng>=1.5:score+=10
    if rng>=3:score+=5
    if hi>0 and price>=hi*.995:score+=15
    vols=[max(0,int(x['volume'])) for x in session]
    accel=0.0
    if len(vols)>=9:
        base=sum(vols[-9:-3])/6
        recent=sum(vols[-3:])/3
        accel=recent/base if base>0 else 0
        if accel>=1.5:score+=15
        if accel>=2.5:score+=10
    return {'score':round(min(100,score),2),'turnoverEok':round(turnover,2),'openReturnPct':round(openret,3),'rangePct':round(rng,3),'volumeAcceleration':round(accel,3)}

def _init_snapshot_db():
    with sqlite3.connect(DB_PATH,timeout=10) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS stocks_in_play_snapshots(
            snapshot_at TEXT NOT NULL,trade_date TEXT NOT NULL,code TEXT NOT NULL,
            score REAL NOT NULL,price REAL,turnover_eok REAL,change_rate REAL,
            source TEXT NOT NULL DEFAULT 'SAFE_UNIVERSE_ACTIVITY',
            PRIMARY KEY(snapshot_at,code))''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_sip_day_code ON stocks_in_play_snapshots(trade_date,code,snapshot_at)')

def record_snapshot(limit=100):
    _init_snapshot_db();now=datetime.now(KST)
    bucket=now.replace(minute=(now.minute//5)*5,second=0,microsecond=0)
    result=scan(limit);rows=result.get('rows') or []
    with sqlite3.connect(DB_PATH,timeout=10) as c:
        for r in rows:
            c.execute('''INSERT OR REPLACE INTO stocks_in_play_snapshots(
                snapshot_at,trade_date,code,score,price,turnover_eok,change_rate,source)
                VALUES(?,?,?,?,?,?,?,?)''',
                (bucket.isoformat(),bucket.date().isoformat(),r['code'],r['score'],r.get('price'),
                 r.get('turnoverEok'),r.get('changeRate'),r.get('source') or 'SAFE_UNIVERSE_ACTIVITY'))
    _RECORDER_STATE['lastSnapshotAt']=bucket.isoformat();_RECORDER_STATE['snapshots']+=1;_RECORDER_STATE['rows']+=len(rows);_RECORDER_STATE['lastError']=None
    return {'ok':True,'snapshotAt':bucket.isoformat(),'rows':len(rows)}

def snapshot_stats():
    _init_snapshot_db()
    with sqlite3.connect(DB_PATH,timeout=10) as c:
        total=int(c.execute('SELECT COUNT(*) FROM stocks_in_play_snapshots').fetchone()[0])
        days=int(c.execute('SELECT COUNT(DISTINCT trade_date) FROM stocks_in_play_snapshots').fetchone()[0])
        latest=c.execute('SELECT MAX(snapshot_at) FROM stocks_in_play_snapshots').fetchone()[0]
    return {'ok':True,'rows':total,'days':days,'latestSnapshotAt':latest,'recorder':dict(_RECORDER_STATE)}

def _recorder_loop():
    _RECORDER_STATE['running']=True
    while not _RECORDER_STOP.is_set():
        try:
            if regular_session():record_snapshot(100)
        except Exception as exc:
            _RECORDER_STATE['lastError']=f'{type(exc).__name__}: {exc}'[:500]
        _RECORDER_STOP.wait(_RECORDER_STATE['intervalSec'])
    _RECORDER_STATE['running']=False

def start_recorder():
    global _RECORDER_STARTED
    if _RECORDER_STARTED:return dict(_RECORDER_STATE)
    _RECORDER_STARTED=True;_RECORDER_STOP.clear()
    threading.Thread(target=_recorder_loop,daemon=True,name='stocks-in-play-recorder').start()
    return dict(_RECORDER_STATE)
