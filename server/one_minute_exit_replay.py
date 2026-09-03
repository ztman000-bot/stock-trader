"""KR 1-minute Exit Replay v0.17.8.

Research/validation only. Replays the existing Control v0.8.0 exit rules against
completed KR 1-minute bars. It never changes entries, exits, sizing, Paper state,
or broker orders.

Because OHLC does not reveal the exact path inside one minute, each bar is replayed
under both O-H-L-C and O-L-H-C paths. Agreement is treated as stronger evidence;
disagreement is explicitly reported as path ambiguity rather than guessed away.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from statistics import mean

from collector import DB_PATH, KST
from paper_engine import (
    STOP_PCT, TRAIL_ACTIVATE_PCT, TRAIL_PCT,
    BREAKEVEN_ACTIVATE_PCT, BREAKEVEN_BUFFER_PCT,
    COMMISSION_RATE, SELL_TAX_RATE, SLIPPAGE_RATE,
)

MIN_REPLAY_TRADES = 30
MIN_PATH_AGREEMENT_PCT = 80.0
MIN_ACTUAL_REASON_MATCH_PCT = 70.0


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def _dt(v):
    d = datetime.fromisoformat(str(v))
    return d.astimezone(KST) if d.tzinfo else d.replace(tzinfo=KST)


def _f(v, n=4):
    try:
        return round(float(v), n)
    except Exception:
        return None


def _load_rows(code, entry_at):
    entry = _dt(entry_at)
    day = entry.date().isoformat()
    floor = entry.replace(second=0, microsecond=0)
    skip_entry_minute = entry != floor
    op = '>' if skip_entry_minute else '>='
    sql = f'''SELECT bucket,open,high,low,close,complete,source FROM bars_1m
              WHERE code=? AND session_date=? AND complete=1 AND bucket {op} ?
              ORDER BY bucket'''
    try:
        with _conn() as c:
            rows = [dict(r) for r in c.execute(sql, (str(code), day, floor.isoformat())).fetchall()]
    except sqlite3.OperationalError:
        rows = []
    return rows, skip_entry_minute


def _levels(entry, peak):
    levels = [('STOP_LOSS', entry * (1 - STOP_PCT), 0)]
    if peak >= entry * (1 + TRAIL_ACTIVATE_PCT):
        levels.append(('TRAILING_STOP', peak * (1 - TRAIL_PCT), 1))
    if peak >= entry * (1 + BREAKEVEN_ACTIVATE_PCT):
        levels.append(('COST_COVER_PROTECT', entry * (1 + BREAKEVEN_BUFFER_PCT), 2))
    return levels


def _gap_exit(entry, peak, price):
    # For a discontinuous sample/gap use the exact live Control check order.
    if price <= entry * (1 - STOP_PCT):
        return 'STOP_LOSS', entry * (1 - STOP_PCT)
    if peak >= entry * (1 + TRAIL_ACTIVATE_PCT) and price <= peak * (1 - TRAIL_PCT):
        return 'TRAILING_STOP', peak * (1 - TRAIL_PCT)
    if peak >= entry * (1 + BREAKEVEN_ACTIVATE_PCT) and price <= entry * (1 + BREAKEVEN_BUFFER_PCT):
        return 'COST_COVER_PROTECT', entry * (1 + BREAKEVEN_BUFFER_PCT)
    return None, None


def _descend_exit(entry, peak, start, end):
    # Continuous descending segment: the highest active threshold is crossed first.
    hits = [(reason, level, priority) for reason, level, priority in _levels(entry, peak)
            if end <= level <= start]
    if not hits:
        return None, None
    reason, level, _ = max(hits, key=lambda x: (x[1], -x[2]))
    return reason, level


def _net_pct(entry, market_exit):
    exit_fill = market_exit * (1 - SLIPPAGE_RATE)
    gross = exit_fill - entry
    fees = entry * COMMISSION_RATE + exit_fill * (COMMISSION_RATE + SELL_TAX_RATE)
    return (gross - fees) / entry * 100 if entry else 0.0


def _simulate(rows, entry, path):
    peak = trough = float(entry)
    prev = float(entry)
    for r in rows:
        bucket = _dt(r['bucket'])
        o, h, l, c = map(float, (r['open'], r['high'], r['low'], r['close']))
        reason, level = _gap_exit(entry, peak, o)
        peak, trough = max(peak, o), min(trough, o)
        if reason:
            return {'reason': reason, 'marketExit': level or o, 'bucket': bucket.isoformat(),
                    'node': 'O', 'peak': peak, 'trough': trough}
        nodes = [('H', h), ('L', l), ('C', c)] if path == 'OHLC' else [('L', l), ('H', h), ('C', c)]
        prev = o
        for node, price in nodes:
            if price < prev:
                reason, level = _descend_exit(entry, peak, prev, price)
                if reason:
                    trough = min(trough, level)
                    return {'reason': reason, 'marketExit': level, 'bucket': bucket.isoformat(),
                            'node': node, 'peak': peak, 'trough': trough}
            peak, trough = max(peak, price), min(trough, price)
            prev = price
        hm = bucket.hour * 60 + bucket.minute
        if hm >= 15 * 60 + 15:
            return {'reason': 'EOD_EXIT', 'marketExit': c, 'bucket': bucket.isoformat(),
                    'node': 'C', 'peak': peak, 'trough': trough}
    if not rows:
        return None
    last = rows[-1]
    return {'reason': 'NO_EXIT_IN_COVERAGE', 'marketExit': float(last['close']),
            'bucket': str(last['bucket']), 'node': 'C', 'peak': peak, 'trough': trough}


def replay_trade(code, entry_at, entry_price):
    rows, skipped = _load_rows(code, entry_at)
    if len(rows) < 5:
        return {'ok': False, 'code': str(code), 'rows': len(rows),
                'reason': 'insufficient-complete-1m-bars', 'entryMinuteSkipped': skipped}
    entry = float(entry_price)
    a = _simulate(rows, entry, 'OHLC')
    b = _simulate(rows, entry, 'OLHC')
    if not a or not b:
        return {'ok': False, 'code': str(code), 'rows': len(rows), 'reason': 'replay-failed'}
    for x in (a, b):
        x['pnlPct'] = _f(_net_pct(entry, float(x['marketExit'])), 4)
        x['mfePct'] = _f((float(x['peak']) / entry - 1) * 100, 3)
        x['maePct'] = _f((float(x['trough']) / entry - 1) * 100, 3)
    same_reason = a['reason'] == b['reason']
    same_minute = str(a['bucket'])[:16] == str(b['bucket'])[:16]
    return {'ok': True, 'code': str(code), 'rows': len(rows), 'entryMinuteSkipped': skipped,
            'paths': {'OHLC': a, 'OLHC': b}, 'pathAgreement': bool(same_reason and same_minute),
            'reasonAgreement': same_reason, 'minuteAgreement': same_minute,
            'pnlRangePct': [_f(min(a['pnlPct'], b['pnlPct']), 4), _f(max(a['pnlPct'], b['pnlPct']), 4)],
            'researchOnly': True, 'controlMutation': False}


def paper_validation(limit=300):
    try:
        with _conn() as c:
            trades = [dict(r) for r in c.execute('''SELECT id,code,entry_at,entry_price,exit_reason,pnl_pct,exit_at
                FROM paper_trades WHERE status='CLOSED' ORDER BY id DESC LIMIT ?''',
                (max(1, min(int(limit), 2000)),)).fetchall()]
    except sqlite3.OperationalError:
        trades = []
    results, replayable = [], []
    for t in trades:
        r = replay_trade(t['code'], t['entry_at'], t['entry_price'])
        rec = {'tradeId': t['id'], 'code': t['code'], 'actualReason': t.get('exit_reason'),
               'actualPnlPct': _f(t.get('pnl_pct'), 4), **r}
        if r.get('ok'):
            reasons = {r['paths']['OHLC']['reason'], r['paths']['OLHC']['reason']}
            rec['actualReasonMatched'] = t.get('exit_reason') in reasons
            replayable.append(rec)
        results.append(rec)
    n = len(replayable)
    agree = sum(1 for x in replayable if x.get('pathAgreement'))
    match = sum(1 for x in replayable if x.get('actualReasonMatched'))
    agreement_pct = agree / n * 100 if n else 0.0
    match_pct = match / n * 100 if n else 0.0
    widths = [x['pnlRangePct'][1] - x['pnlRangePct'][0] for x in replayable if x.get('pnlRangePct')]
    validated = bool(n >= MIN_REPLAY_TRADES and agreement_pct >= MIN_PATH_AGREEMENT_PCT and
                     match_pct >= MIN_ACTUAL_REASON_MATCH_PCT)
    return {'ok': True, 'engine': 'kr-1m-exit-replay-v1', 'engineConnected': True,
            'closedPaperTrades': len(trades), 'replayableTrades': n,
            'pathAgreementPct': _f(agreement_pct, 1), 'actualReasonMatchPct': _f(match_pct, 1),
            'avgPnlRangeWidthPct': _f(mean(widths), 4) if widths else None,
            'validated': validated,
            'validationRule': {'minReplayTrades': MIN_REPLAY_TRADES,
                'minPathAgreementPct': MIN_PATH_AGREEMENT_PCT,
                'minActualReasonMatchPct': MIN_ACTUAL_REASON_MATCH_PCT},
            'note': '1분 OHLC 내부 순서는 알 수 없어 OHLC/OLHC 양쪽 경로를 재현합니다. 불일치는 모호성으로 남기며 임의 확정하지 않습니다.',
            'recent': results[:50], 'liveMutation': False, 'realOrder': False}


def validation_status():
    return paper_validation(500)
