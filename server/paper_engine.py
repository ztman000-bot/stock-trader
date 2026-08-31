import math
import sqlite3
from datetime import datetime

from collector import (
    DB_PATH,
    FOCUS_SIZE,
    PROTECTED_CODES,
    active_candidates,
    bars,
    candidate_meta,
    collector,
    instrument_meta,
    is_safe_code,
    latest_quotes,
    universe_verified,
)

KST = __import__('zoneinfo').ZoneInfo('Asia/Seoul')
INITIAL_CAPITAL = 10_000_000.0
RISK_PER_TRADE = 0.0035
STOP_PCT = 0.010
TRAIL_ACTIVATE_PCT = 0.015
TRAIL_PCT = 0.008
BREAKEVEN_ACTIVATE_PCT = 0.008
MAX_CONSECUTIVE_LOSSES = 2
MAX_OPEN_POSITIONS = 2
MAX_DAILY_TRADES = 8
DAILY_MAX_LOSS_PCT = 0.0075
MIN_BARS = 35
MIN_SESSION_BARS = 6          # First 30 minutes form the opening range.
BUY_SCORE = 78
STRONG_VOLUME_RATIO = 1.80
MIN_VOLUME_RATIO = 1.20
MIN_RSI = 55
MAX_RSI = 78
MIN_ADX = 22
MIN_MARKET_BREADTH = 0.35
STRONG_OVERRIDE_SCORE = 90
COMMISSION_RATE = 0.0001
SELL_TAX_RATE = 0.0015
SLIPPAGE_RATE = 0.0005
ROUND_TRIP_COST_EST = 2 * COMMISSION_RATE + SELL_TAX_RATE + 2 * SLIPPAGE_RATE
# +0.1% is not true breakeven after commission/tax/slippage. Protect a small net gain instead.
BREAKEVEN_BUFFER_PCT = max(0.0035, ROUND_TRIP_COST_EST + 0.0005)


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
          status TEXT NOT NULL DEFAULT 'OPEN',
          peak_price REAL
        );
        CREATE TABLE IF NOT EXISTS paper_state (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_signals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,code TEXT NOT NULL,at TEXT NOT NULL,
          score REAL NOT NULL,action TEXT NOT NULL,reasons TEXT,shadow INTEGER NOT NULL DEFAULT 0
        );
        ''')
        cols = {r['name'] for r in conn.execute('PRAGMA table_info(paper_trades)').fetchall()}
        if 'peak_price' not in cols:
            conn.execute('ALTER TABLE paper_trades ADD COLUMN peak_price REAL')


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
    for a, b in zip(values[-period - 1:-1], values[-period:]):
        d = b - a
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains) / period
    al = sum(losses) / period
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def _wilder_adx(rows, period=14):
    if len(rows) < period * 2 + 1:
        return None, None, None
    trs, pdms, mdms = [], [], []
    for prev, cur in zip(rows[:-1], rows[1:]):
        ph, pl, pc = float(prev['high']), float(prev['low']), float(prev['close'])
        ch, cl = float(cur['high']), float(cur['low'])
        up, down = ch - ph, pl - cl
        pdms.append(up if up > down and up > 0 else 0.0)
        mdms.append(down if down > up and down > 0 else 0.0)
        trs.append(max(ch - cl, abs(ch - pc), abs(cl - pc)))
    tr_s, pdm_s, mdm_s = sum(trs[:period]), sum(pdms[:period]), sum(mdms[:period])
    dxs = []
    pdi = mdi = 0.0
    for i in range(period, len(trs)):
        if i > period:
            tr_s = tr_s - tr_s / period + trs[i]
            pdm_s = pdm_s - pdm_s / period + pdms[i]
            mdm_s = mdm_s - mdm_s / period + mdms[i]
        if tr_s <= 0:
            pdi = mdi = 0.0
        else:
            pdi, mdi = 100 * pdm_s / tr_s, 100 * mdm_s / tr_s
        denom = pdi + mdi
        dxs.append(100 * abs(pdi - mdi) / denom if denom else 0.0)
    if len(dxs) < period:
        return None, pdi, mdi
    adx = sum(dxs[:period]) / period
    for dx in dxs[period:]:
        adx = ((adx * (period - 1)) + dx) / period
    return adx, pdi, mdi


def _vwap(rows):
    total_v = sum(max(0, int(r['volume'])) for r in rows)
    if total_v <= 0:
        return None
    return sum(((float(r['high']) + float(r['low']) + float(r['close'])) / 3.0) * max(0, int(r['volume'])) for r in rows) / total_v


def _parse_bucket(value):
    return datetime.fromisoformat(str(value)).astimezone(KST)


def _completed_rows(code):
    rows = bars(code, 220)
    now = datetime.now(KST)
    current_bucket = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
    # Ignore any accidental samples outside the regular KRX session.
    out = []
    for r in rows:
        dt = _parse_bucket(r['bucket'])
        hm = dt.hour * 60 + dt.minute
        if 9 * 60 <= hm <= 15 * 60 + 30 and dt < current_bucket:
            out.append(r)
    return out


def indicators(code):
    rows = _completed_rows(code)
    if len(rows) < MIN_BARS:
        return {'ready': False, 'bars': len(rows), 'need': MIN_BARS, 'sessionBars': 0}

    today = datetime.now(KST).date()
    session = [r for r in rows if _parse_bucket(r['bucket']).date() == today]
    calc_rows = rows[-120:]
    closes = [float(r['close']) for r in calc_rows]
    ema9 = _ema(closes[-60:], 9)
    ema20 = _ema(closes[-80:], 20)
    rsi = _rsi(closes, 14)
    adx, pdi, mdi = _wilder_adx(calc_rows, 14)
    last, prev = calc_rows[-1], calc_rows[-2]
    price = float(last['close'])
    vwap = _vwap(session) if session else _vwap(calc_rows)

    prior6 = calc_rows[-7:-1]
    rolling_high = max(float(r['high']) for r in prior6)
    avg_vol = sum(int(r['volume']) for r in prior6) / len(prior6)
    volume_ratio = int(last['volume']) / avg_vol if avg_vol > 0 else 0.0

    session_ready = len(session) >= MIN_SESSION_BARS
    opening_range = session[:MIN_SESSION_BARS] if session_ready else []
    opening_high = max((float(r['high']) for r in opening_range), default=None)
    opening_low = min((float(r['low']) for r in opening_range), default=None)
    orb_breakout = bool(opening_high and price > opening_high)
    rolling_breakout = price > rolling_high
    above_vwap = bool(vwap and price > vwap)
    ema_bull = ema9 is not None and ema20 is not None and ema9 > ema20
    rsi_ok = rsi is not None and MIN_RSI <= rsi <= MAX_RSI
    dmi_ok = adx is not None and adx >= MIN_ADX and pdi is not None and mdi is not None and pdi > mdi
    volume_up = volume_ratio >= MIN_VOLUME_RATIO
    strong_volume = volume_ratio >= STRONG_VOLUME_RATIO
    trend_gate = above_vwap and ema_bull
    trigger_gate = orb_breakout or (rolling_breakout and strong_volume)

    score = 0.0
    if session_ready: score += 5
    if above_vwap: score += 15
    if ema_bull: score += 15
    if volume_ratio > 1:
        score += max(0, min(15, (volume_ratio - 1.0) * 18.75))
    if orb_breakout:
        score += 20
    elif rolling_breakout:
        score += 10
    if rsi_ok: score += 10
    if dmi_ok: score += min(15, 8 + max(0, adx - MIN_ADX) * 0.35)
    score = round(min(90.0, score), 2)

    checks = {
        'session_ready': session_ready,
        'above_vwap': above_vwap,
        'ema_bull': ema_bull,
        'volume_up': volume_up,
        'strong_volume': strong_volume,
        'orb_breakout': orb_breakout,
        'rolling_breakout': rolling_breakout,
        'rsi_ok': rsi_ok,
        'dmi_ok': dmi_ok,
        'trend_gate': trend_gate,
        'trigger_gate': trigger_gate,
    }
    return {
        'ready': True,
        'bars': len(rows),
        'sessionBars': len(session),
        'price': price,
        'vwap': vwap,
        'ema9': ema9,
        'ema20': ema20,
        'rsi': rsi,
        'adx': adx,
        'plusDI': pdi,
        'minusDI': mdi,
        'volumeRatio': volume_ratio,
        'rollingHigh': rolling_high,
        'openingRangeHigh': opening_high,
        'openingRangeLow': opening_low,
        'checks': checks,
        'score': score,
        'bucket': last['bucket'],
        'previousClose': float(prev['close']),
        'signalBarComplete': True,
    }


def _today_prefix():
    return datetime.now(KST).date().isoformat()


def daily_stats():
    init_paper_db()
    with _conn() as conn:
        closed = conn.execute("SELECT * FROM paper_trades WHERE status='CLOSED' AND exit_at LIKE ? ORDER BY id", (_today_prefix() + '%',)).fetchall()
    pnl = sum(float(r['pnl'] or 0) for r in closed)
    consecutive = 0
    for r in reversed(closed):
        if float(r['pnl'] or 0) < 0:
            consecutive += 1
        else:
            break
    loss_limit = -INITIAL_CAPITAL * DAILY_MAX_LOSS_PCT
    return {
        'date': _today_prefix(),
        'closedTrades': len(closed),
        'pnl': round(pnl, 2),
        'consecutiveLosses': consecutive,
        'lossLimit': round(loss_limit, 2),
        'lossLimitHit': pnl <= loss_limit,
        'maxDailyTrades': MAX_DAILY_TRADES,
        'locked': consecutive >= MAX_CONSECUTIVE_LOSSES or pnl <= loss_limit or len(closed) >= MAX_DAILY_TRADES,
    }


def open_positions():
    init_paper_db()
    with _conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY id").fetchall()]


def recent_trades(limit=20):
    init_paper_db()
    limit = max(1, min(int(limit), 100))
    with _conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def _evaluate_base(code, market=None, stats=None):
    stats = stats or daily_stats()
    market = market or candidate_meta(code)
    name = market.get('name') or instrument_meta(code).get('name') or code
    ind = indicators(code)

    if code in PROTECTED_CODES:
        return {'code': code, 'name': name, 'action': 'PROTECTED', 'score': 0, 'indicators': ind, 'market': market, 'daily': stats}
    if not universe_verified():
        return {'code': code, 'name': name, 'action': 'SAFETY_WAIT', 'score': 0, 'indicators': ind, 'market': market, 'daily': stats,
                'blockedReasons': ['종목마스터 안전검증 대기']}
    if not is_safe_code(code):
        return {'code': code, 'name': name, 'action': 'BLOCKED', 'score': 0, 'indicators': ind, 'market': market, 'daily': stats,
                'blockedReasons': ['안전필터 제외 종목']}
    if not ind.get('ready'):
        return {'code': code, 'name': name, 'action': 'WAIT_DATA', 'score': 0, 'indicators': ind, 'market': market, 'daily': stats}

    c = ind['checks']
    liquidity_ok = bool(market.get('liquidityOk'))
    activity_bonus = min(10.0, max(0.0, float(market.get('activityScore') or 0) / 10.0))
    score = round(min(100.0, float(ind['score']) + activity_bonus), 2)
    blocked = []
    if not c['session_ready']: blocked.append('개장30분 미완료')
    if not liquidity_ok: blocked.append('유동성/거래대금/스프레드 기준 미달')
    if not c['trend_gate']: blocked.append('VWAP·EMA 추세 미충족')
    if not c['trigger_gate']: blocked.append('ORB30 또는 강한 돌파 미발생')
    if not c['rsi_ok']: blocked.append('RSI 범위 밖')
    if not c['dmi_ok']: blocked.append('ADX/DMI 약함')

    entry_gate = c['session_ready'] and liquidity_ok and c['trend_gate'] and c['trigger_gate'] and c['rsi_ok'] and c['dmi_ok']
    if entry_gate and score >= BUY_SCORE:
        action = 'BUY_CANDIDATE'
    elif c['session_ready'] and liquidity_ok and c['trend_gate'] and score >= 62:
        action = 'SETUP'
    else:
        action = 'WATCH'

    return {
        'code': code,
        'name': name,
        'action': action,
        'score': score,
        'reasons': [k for k, v in c.items() if v] + (['liquidity_ok'] if liquidity_ok else []),
        'blockedReasons': blocked,
        'indicators': ind,
        'market': market,
        'daily': stats,
    }


def evaluate(code):
    return _evaluate_base(code)


def scan():
    stats = daily_stats()
    market_rows = active_candidates(FOCUS_SIZE)
    evaluations = [_evaluate_base(m['code'], m, stats) for m in market_rows]
    ready = [e for e in evaluations if e.get('indicators', {}).get('ready') and e.get('indicators', {}).get('checks', {}).get('session_ready')]
    trend_count = sum(1 for e in ready if e['indicators']['checks'].get('trend_gate'))
    breadth = trend_count / len(ready) if ready else 0.0

    for ev in evaluations:
        ev['marketBreadth'] = round(breadth, 3)
        if ev['action'] == 'BUY_CANDIDATE' and breadth < MIN_MARKET_BREADTH and ev['score'] < STRONG_OVERRIDE_SCORE:
            ev['action'] = 'SETUP'
            ev.setdefault('blockedReasons', []).append('시장 breadth 약함')
        if stats['locked'] and ev['action'] == 'BUY_CANDIDATE':
            ev['action'] = 'SHADOW_ONLY'
    evaluations.sort(key=lambda e: (float(e.get('score') or 0), float((e.get('market') or {}).get('activityScore') or 0)), reverse=True)
    return evaluations


def paper_enter(code, capital=INITIAL_CAPITAL, evaluation=None):
    init_paper_db()
    if evaluation is None:
        evaluation = next((e for e in scan() if e['code'] == code), _evaluate_base(code))
    ev = evaluation
    if ev['action'] not in ('BUY_CANDIDATE', 'SHADOW_ONLY'):
        return {'ok': False, 'message': '진입 조건 미충족', 'evaluation': ev}

    shadow = ev['action'] == 'SHADOW_ONLY'
    price = float(ev['indicators']['price'])
    capital = max(0.0, float(capital))
    risk_cash = capital * RISK_PER_TRADE
    # Size from expected stop loss INCLUDING friction, otherwise 0.35% risk is understated.
    effective_stop_risk = STOP_PCT + ROUND_TRIP_COST_EST
    risk_qty = math.floor(risk_cash / (price * effective_stop_risk)) if price > 0 else 0
    cash_qty = math.floor(capital / (price * (1 + COMMISSION_RATE + SLIPPAGE_RATE))) if price > 0 else 0
    qty = max(0, min(risk_qty, cash_qty))
    if qty < 1:
        return {'ok': False, 'message': '가용 자본 대비 주문 가능 수량이 0입니다.', 'evaluation': ev}

    reasons = ','.join(ev.get('reasons', []))
    now = datetime.now(KST).isoformat()
    with _conn() as conn:
        conn.execute('INSERT INTO paper_signals(code,at,score,action,reasons,shadow) VALUES(?,?,?,?,?,?)',
                     (code, now, ev['score'], ev['action'], reasons, 1 if shadow else 0))
        if shadow:
            return {'ok': True, 'shadow': True, 'message': 'Daily Lock: 신호만 기록, 신규 Paper 진입 없음', 'evaluation': ev}
        open_count = conn.execute("SELECT COUNT(*) AS n FROM paper_trades WHERE status='OPEN'").fetchone()['n']
        if int(open_count) >= MAX_OPEN_POSITIONS:
            return {'ok': False, 'message': f'동시 포지션 {MAX_OPEN_POSITIONS}개 제한', 'evaluation': ev}
        if conn.execute("SELECT 1 FROM paper_trades WHERE code=? AND status='OPEN'", (code,)).fetchone():
            return {'ok': False, 'message': '이미 열린 Paper 포지션이 있습니다.', 'evaluation': ev}
        entry_fill = price * (1 + SLIPPAGE_RATE)
        cur = conn.execute(
            "INSERT INTO paper_trades(code,entry_at,entry_price,qty,score,reasons,status,peak_price) VALUES(?,?,?,?,?,?, 'OPEN',?)",
            (code, now, entry_fill, qty, ev['score'], reasons, entry_fill),
        )
        trade_id = cur.lastrowid

    collector.set_priority_codes([p['code'] for p in open_positions()])
    return {
        'ok': True,
        'shadow': False,
        'tradeId': trade_id,
        'code': code,
        'price': entry_fill,
        'qty': qty,
        'stopPrice': entry_fill * (1 - STOP_PCT),
        'breakevenProtectPrice': entry_fill * (1 + BREAKEVEN_BUFFER_PCT),
        'trailActivatePrice': entry_fill * (1 + TRAIL_ACTIVATE_PCT),
        'evaluation': ev,
    }


def close_position(trade_id, market_price, reason='MANUAL'):
    init_paper_db()
    market = float(market_price)
    if market <= 0:
        return None
    with _conn() as conn:
        p = conn.execute("SELECT * FROM paper_trades WHERE id=? AND status='OPEN'", (trade_id,)).fetchone()
        if not p:
            return None
        entry = float(p['entry_price'])
        qty = int(p['qty'])
        peak = max(float(p['peak_price'] or entry), market)
        exit_fill = market * (1 - SLIPPAGE_RATE)
        gross = (exit_fill - entry) * qty
        fees = (entry * qty * COMMISSION_RATE) + (exit_fill * qty * (COMMISSION_RATE + SELL_TAX_RATE))
        pnl = gross - fees
        pnl_pct = pnl / (entry * qty) * 100 if entry * qty else 0
        now = datetime.now(KST).isoformat()
        conn.execute("UPDATE paper_trades SET exit_at=?,exit_price=?,exit_reason=?,pnl=?,pnl_pct=?,status='CLOSED',peak_price=? WHERE id=?",
                     (now, exit_fill, reason, pnl, pnl_pct, peak, trade_id))
    collector.set_priority_codes([p['code'] for p in open_positions()])
    return {'id': trade_id, 'code': p['code'], 'reason': reason, 'pnl': round(pnl, 2), 'pnlPct': round(pnl_pct, 4), 'exitPrice': exit_fill}


def force_close_all(reason='EOD_EXIT'):
    latest = {r['code']: float(r['price']) for r in latest_quotes()}
    closed = []
    for p in open_positions():
        market = latest.get(p['code'])
        if not market:
            continue
        result = close_position(p['id'], market, reason)
        if result:
            closed.append(result)
    collector.set_priority_codes([])
    return closed


def mark_positions():
    positions = open_positions()
    collector.set_priority_codes([p['code'] for p in positions])
    latest = {r['code']: float(r['price']) for r in latest_quotes([p['code'] for p in positions])}
    closed = []
    for p in positions:
        market = latest.get(p['code'])
        if not market:
            continue
        entry = float(p['entry_price'])
        peak = max(float(p['peak_price'] or entry), market)
        if peak != float(p['peak_price'] or entry):
            with _conn() as conn:
                conn.execute('UPDATE paper_trades SET peak_price=? WHERE id=?', (peak, p['id']))
        reason = None
        if market <= entry * (1 - STOP_PCT):
            reason = 'STOP_LOSS'
        elif peak >= entry * (1 + TRAIL_ACTIVATE_PCT) and market <= peak * (1 - TRAIL_PCT):
            reason = 'TRAILING_STOP'
        elif peak >= entry * (1 + BREAKEVEN_ACTIVATE_PCT) and market <= entry * (1 + BREAKEVEN_BUFFER_PCT):
            reason = 'COST_COVER_PROTECT'
        if reason:
            result = close_position(p['id'], market, reason)
            if result:
                closed.append(result)
    collector.set_priority_codes([p['code'] for p in open_positions()])
    return {'closed': closed, 'daily': daily_stats(), 'open': open_positions()}


init_paper_db()
