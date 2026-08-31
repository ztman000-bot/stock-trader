import math
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from collector import DB_PATH, PROTECTED_CODES, bars, latest_quotes

KST = ZoneInfo('Asia/Seoul')
INITIAL_CAPITAL = 10_000_000.0
RISK_PER_TRADE = 0.0035
STOP_PCT = 0.010
TARGET_PCT = 0.015
TRAIL_PCT = 0.008
MAX_CONSECUTIVE_LOSSES = 2
MIN_BARS = 25


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def init_paper_db():
    with _conn() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS paper_trades (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          code TEXT NOT NULL,
          entry_at TEXT NOT NULL,
          entry_price REAL NOT NULL,
          qty INTEGER NOT NULL,
          score REAL NOT NULL,
          reasons TEXT,
          exit_at TEXT,
          exit_price REAL,
          exit_reason TEXT,
          pnl REAL,
          pnl_pct REAL,
          status TEXT NOT NULL DEFAULT 'OPEN'
        );
        CREATE TABLE IF NOT EXISTS paper_state (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS paper_signals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          code TEXT NOT NULL,
          at TEXT NOT NULL,
          score REAL NOT NULL,
          action TEXT NOT NULL,
          reasons TEXT,
          shadow INTEGER NOT NULL DEFAULT 0
        );
        ''')


def _ema(values, period):
    if not values:
        return None
    alpha = 2.0 / (period + 1)
    out = float(values[0])
    for v in values[1:]:
        out = alpha * float(v) + (1 - alpha) * out
    return out


def _rsi(values, period=14):
    if len(values) <= period:
        return None
    gains, losses = [], []
    for a, b in zip(values[-period-1:-1], values[-period:]):
        d = b - a
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains) / period
    al = sum(losses) / period
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - 100 / (1 + rs)


def _adx(rows, period=14):
    if len(rows) < period + 2:
        return None, None, None
    trs, plus_dm, minus_dm = [], [], []
    for prev, cur in zip(rows[-period-1:-1], rows[-period:]):
        up = cur['high'] - prev['high']
        down = prev['low'] - cur['low']
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
        trs.append(max(cur['high']-cur['low'], abs(cur['high']-prev['close']), abs(cur['low']-prev['close'])))
    tr = sum(trs)
    if tr <= 0:
        return 0.0, 0.0, 0.0
    pdi = 100 * sum(plus_dm) / tr
    mdi = 100 * sum(minus_dm) / tr
    denom = pdi + mdi
    dx = 100 * abs(pdi-mdi) / denom if denom else 0.0
    return dx, pdi, mdi


def _vwap(rows):
    total_v = sum(max(0, int(r['volume'])) for r in rows)
    if total_v <= 0:
        return None
    pv = sum(((r['high']+r['low']+r['close'])/3.0) * max(0, int(r['volume'])) for r in rows)
    return pv / total_v


def indicators(code):
    rows = bars(code, 120)
    if len(rows) < MIN_BARS:
        return {'ready': False, 'bars': len(rows), 'need': MIN_BARS}
    closes = [float(r['close']) for r in rows]
    ema9 = _ema(closes[-60:], 9)
    ema20 = _ema(closes[-80:], 20)
    rsi = _rsi(closes, 14)
    adx, pdi, mdi = _adx(rows, 14)
    vwap = _vwap(rows)
    last = rows[-1]
    prev = rows[-2]
    prev_high = max(float(r['high']) for r in rows[-7:-1])
    avg_vol = sum(int(r['volume']) for r in rows[-7:-1]) / 6.0
    volume_ratio = int(last['volume']) / avg_vol if avg_vol > 0 else 0.0
    price = float(last['close'])
    checks = {
        'above_vwap': bool(vwap and price > vwap),
        'ema_bull': ema9 > ema20,
        'volume_up': volume_ratio >= 1.20,
        'breakout': price > prev_high,
        'rsi_ok': rsi is not None and 52 <= rsi <= 75,
        'dmi_ok': adx is not None and adx >= 20 and pdi > mdi,
    }
    weights = {'above_vwap':20,'ema_bull':20,'volume_up':15,'breakout':20,'rsi_ok':10,'dmi_ok':15}
    score = sum(weights[k] for k,v in checks.items() if v)
    return {
        'ready': True, 'bars': len(rows), 'price': price, 'vwap': vwap,
        'ema9': ema9, 'ema20': ema20, 'rsi': rsi, 'adx': adx,
        'plusDI': pdi, 'minusDI': mdi, 'volumeRatio': volume_ratio,
        'previousHigh': prev_high, 'checks': checks, 'score': score,
        'bucket': last['bucket'], 'previousClose': float(prev['close'])
    }


def _today_prefix():
    return datetime.now(KST).date().isoformat()


def daily_stats():
    init_paper_db()
    with _conn() as conn:
        closed = conn.execute("SELECT * FROM paper_trades WHERE status='CLOSED' AND exit_at LIKE ? ORDER BY id", (_today_prefix()+'%',)).fetchall()
    pnl = sum(float(r['pnl'] or 0) for r in closed)
    consecutive = 0
    for r in reversed(closed):
        if float(r['pnl'] or 0) < 0:
            consecutive += 1
        else:
            break
    return {'date': _today_prefix(), 'closedTrades': len(closed), 'pnl': pnl, 'consecutiveLosses': consecutive, 'locked': consecutive >= MAX_CONSECUTIVE_LOSSES}


def open_positions():
    init_paper_db()
    with _conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY id").fetchall()]


def evaluate(code):
    ind = indicators(code)
    stats = daily_stats()
    if code in PROTECTED_CODES:
        return {'code':code,'action':'PROTECTED','score':0,'indicators':ind,'daily':stats}
    if not ind.get('ready'):
        return {'code':code,'action':'WAIT_DATA','score':0,'indicators':ind,'daily':stats}
    score = ind['score']
    action = 'BUY_CANDIDATE' if score >= 75 else 'WATCH'
    if stats['locked'] and action == 'BUY_CANDIDATE':
        action = 'SHADOW_ONLY'
    reasons = [k for k,v in ind['checks'].items() if v]
    return {'code':code,'action':action,'score':score,'reasons':reasons,'indicators':ind,'daily':stats}


def scan():
    quotes = latest_quotes()
    return [evaluate(q['code']) for q in quotes]


def paper_enter(code, capital=INITIAL_CAPITAL):
    init_paper_db()
    ev = evaluate(code)
    if ev['action'] not in ('BUY_CANDIDATE','SHADOW_ONLY'):
        return {'ok':False,'message':'진입 조건 미충족','evaluation':ev}
    shadow = ev['action'] == 'SHADOW_ONLY'
    price = float(ev['indicators']['price'])
    risk_cash = float(capital) * RISK_PER_TRADE
    qty = max(1, math.floor(risk_cash / (price * STOP_PCT)))
    reasons = ','.join(ev.get('reasons', []))
    now = datetime.now(KST).isoformat()
    with _conn() as conn:
        conn.execute('INSERT INTO paper_signals(code,at,score,action,reasons,shadow) VALUES(?,?,?,?,?,?)', (code,now,ev['score'],ev['action'],reasons,1 if shadow else 0))
        if shadow:
            return {'ok':True,'shadow':True,'message':'2연속 손실 잠금: 신호만 기록, 신규 가상진입 없음','evaluation':ev}
        exists = conn.execute("SELECT 1 FROM paper_trades WHERE code=? AND status='OPEN'", (code,)).fetchone()
        if exists:
            return {'ok':False,'message':'이미 열린 Paper 포지션이 있습니다.','evaluation':ev}
        cur = conn.execute('INSERT INTO paper_trades(code,entry_at,entry_price,qty,score,reasons,status) VALUES(?,?,?,?,?,?,'"'OPEN'"')', (code,now,price,qty,ev['score'],reasons))
        trade_id = cur.lastrowid
    return {'ok':True,'shadow':False,'tradeId':trade_id,'code':code,'price':price,'qty':qty,'stopPrice':price*(1-STOP_PCT),'targetPrice':price*(1+TARGET_PCT),'evaluation':ev}


def mark_positions():
    init_paper_db()
    latest = {r['code']: float(r['price']) for r in latest_quotes()}
    closed = []
    with _conn() as conn:
        positions = conn.execute("SELECT * FROM paper_trades WHERE status='OPEN'").fetchall()
        for p in positions:
            price = latest.get(p['code'])
            if not price:
                continue
            entry = float(p['entry_price'])
            reason = None
            if price <= entry*(1-STOP_PCT): reason='STOP_LOSS'
            elif price >= entry*(1+TARGET_PCT): reason='TARGET'
            if reason:
                pnl = (price-entry)*int(p['qty'])
                pnl_pct = (price/entry-1)*100
                now = datetime.now(KST).isoformat()
                conn.execute("UPDATE paper_trades SET exit_at=?,exit_price=?,exit_reason=?,pnl=?,pnl_pct=?,status='CLOSED' WHERE id=?", (now,price,reason,pnl,pnl_pct,p['id']))
                closed.append({'id':p['id'],'code':p['code'],'reason':reason,'pnl':pnl,'pnlPct':pnl_pct})
    return {'closed':closed,'daily':daily_stats(),'open':open_positions()}


init_paper_db()
