"""Research-only continuous Shadow trade ledger.

Purpose
-------
Collect counterfactual trade-level evidence after normal Paper risk locks and
for same-stock re-entry without changing Control v0.8.0. The engine reads the
same local scanner/quote state, mirrors the current Control exit thresholds,
and writes only to dedicated shadow_continuation_* tables.

Important safety properties
---------------------------
- no broker/order API calls
- no writes to paper_trades or Control state
- no NH REST requests; latest_quotes/scan use the existing local data path
- ignores daily loss / consecutive-loss / max-daily-trade locks only inside
  this research ledger so useful counterfactual samples continue to accumulate
- never opens overlapping Shadow positions in the same code
- all reported PnL is normalized research return, not deployable capital PnL
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime

from collector import DB_PATH, KST, latest_quotes
from paper_engine import (
    BREAKEVEN_ACTIVATE_PCT,
    BREAKEVEN_BUFFER_PCT,
    COMMISSION_RATE,
    SELL_TAX_RATE,
    SLIPPAGE_RATE,
    STOP_PCT,
    TRAIL_ACTIVATE_PCT,
    TRAIL_PCT,
    scan,
)

VERSION = '0.17.15-shadow-reentry-1'
MIN_REENTRY_SAMPLE = 50
LOOP_SEC = max(1.0, float(os.getenv('SHADOW_CONTINUATION_LOOP_SEC', '2')))
SCAN_SEC = max(5.0, float(os.getenv('SHADOW_CONTINUATION_SCAN_SEC', '10')))
ENTRY_START = os.getenv('PAPER_ENTRY_START', '09:30')
ENTRY_CUTOFF = os.getenv('PAPER_ENTRY_CUTOFF', '14:50')
EOD_EXIT = os.getenv('PAPER_EOD_EXIT', '15:15')
SIGNAL_MAX_AGE_SEC = max(60, int(os.getenv('SIGNAL_MAX_AGE_SEC', '420')))

_LOCK = threading.RLock()
_STOP = threading.Event()
_THREAD = None
_STATUS = {
    'enabled': True,
    'running': False,
    'startedAt': None,
    'lastCycleAt': None,
    'lastScanAt': None,
    'lastEntryAt': None,
    'lastExitAt': None,
    'lastError': None,
    'entries': 0,
    'closed': 0,
    'researchOnly': True,
    'controlMutation': False,
    'realOrderEnabled': False,
    'extraNhRestCalls': False,
}


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def init_db():
    with _conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS shadow_continuation_trades(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          code TEXT NOT NULL,
          name TEXT,
          signal_bucket TEXT NOT NULL,
          entry_at TEXT NOT NULL,
          entry_price REAL NOT NULL,
          score REAL NOT NULL DEFAULT 0,
          source_action TEXT NOT NULL,
          reasons_json TEXT NOT NULL DEFAULT '[]',
          daily_lock_continuation INTEGER NOT NULL DEFAULT 0,
          reentry_after_shadow_loss INTEGER NOT NULL DEFAULT 0,
          reentry_after_shadow_win INTEGER NOT NULL DEFAULT 0,
          prior_control_loss INTEGER NOT NULL DEFAULT 0,
          same_code_sequence INTEGER NOT NULL DEFAULT 1,
          day_sequence INTEGER NOT NULL DEFAULT 1,
          prior_shadow_pnl_pct REAL,
          prior_shadow_exit_reason TEXT,
          exit_at TEXT,
          exit_price REAL,
          exit_reason TEXT,
          pnl_pct REAL,
          status TEXT NOT NULL DEFAULT 'OPEN',
          peak_price REAL,
          trough_price REAL,
          created_version TEXT NOT NULL DEFAULT '0.17.15-shadow-reentry-1',
          UNIQUE(code,signal_bucket)
        );
        CREATE INDEX IF NOT EXISTS idx_shadow_continuation_day
          ON shadow_continuation_trades(entry_at,status,code);
        CREATE INDEX IF NOT EXISTS idx_shadow_continuation_reentry
          ON shadow_continuation_trades(reentry_after_shadow_loss,entry_at);
        ''')


def _hm(text):
    h, m = [int(x) for x in str(text).split(':', 1)]
    return h * 60 + m


def _entry_hours(now):
    hm = now.hour * 60 + now.minute
    return now.weekday() < 5 and _hm(ENTRY_START) <= hm < _hm(ENTRY_CUTOFF)


def _eod_due(now):
    hm = now.hour * 60 + now.minute
    return now.weekday() < 5 and _hm(EOD_EXIT) <= hm < 920


def _signal_fresh(bucket, now):
    try:
        dt = datetime.fromisoformat(str(bucket)).astimezone(KST)
    except Exception:
        return False
    return dt.date() == now.date() and 0 <= (now - dt).total_seconds() <= SIGNAL_MAX_AGE_SEC


def _today(now=None):
    return (now or datetime.now(KST)).date().isoformat()


def _prior_shadow(c, code, day):
    row = c.execute('''SELECT pnl_pct,exit_reason FROM shadow_continuation_trades
                       WHERE code=? AND status='CLOSED' AND entry_at LIKE ?
                       ORDER BY id DESC LIMIT 1''', (code, day + '%')).fetchone()
    if not row:
        return None
    return {'pnlPct': float(row['pnl_pct'] or 0), 'exitReason': row['exit_reason']}


def _prior_control_loss(c, code, day):
    try:
        row = c.execute('''SELECT 1 FROM paper_trades
                           WHERE code=? AND status='CLOSED' AND exit_at LIKE ? AND COALESCE(pnl,0)<0
                           ORDER BY id DESC LIMIT 1''', (code, day + '%')).fetchone()
        return bool(row)
    except sqlite3.OperationalError:
        return False


def _net_pct(entry_fill, exit_fill):
    if entry_fill <= 0 or exit_fill <= 0:
        return None
    gross = exit_fill / entry_fill - 1
    fee_ratio = COMMISSION_RATE + (exit_fill / entry_fill) * (COMMISSION_RATE + SELL_TAX_RATE)
    return (gross - fee_ratio) * 100


def _open_positions():
    init_db()
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM shadow_continuation_trades WHERE status='OPEN' ORDER BY id")]


def observe_scan(evaluations, now=None):
    """Open normalized Shadow trades from BUY_CANDIDATE/SHADOW_ONLY signals.

    SHADOW_ONLY is deliberately eligible here: it is the counterfactual cohort
    that would have occurred after the normal Paper daily lock. We do not copy
    Control's daily trade / consecutive loss / max-open-position locks into this
    research ledger, but we still prevent overlapping positions in one code and
    deduplicate a code+5m signal bucket.
    """
    now = now or datetime.now(KST)
    if not _entry_hours(now) or not evaluations:
        return {'ok': True, 'opened': 0}
    init_db()
    opened = 0
    day = _today(now)
    with _conn() as c:
        for ev in evaluations:
            action = str(ev.get('action') or '')
            if action not in ('BUY_CANDIDATE', 'SHADOW_ONLY'):
                continue
            code = str(ev.get('code') or '')
            ind = ev.get('indicators') or {}
            bucket = str(ind.get('bucket') or '')
            try:
                price = float(ind.get('price') or 0)
            except (TypeError, ValueError):
                price = 0.0
            if not code or not bucket or price <= 0 or not _signal_fresh(bucket, now):
                continue
            if c.execute("SELECT 1 FROM shadow_continuation_trades WHERE code=? AND status='OPEN'", (code,)).fetchone():
                continue
            if c.execute("SELECT 1 FROM shadow_continuation_trades WHERE code=? AND signal_bucket=?", (code, bucket)).fetchone():
                continue
            prior = _prior_shadow(c, code, day)
            prior_loss = bool(prior and prior['pnlPct'] < 0)
            prior_win = bool(prior and prior['pnlPct'] > 0)
            same_seq = int(c.execute("SELECT COUNT(*) FROM shadow_continuation_trades WHERE code=? AND entry_at LIKE ?", (code, day + '%')).fetchone()[0]) + 1
            day_seq = int(c.execute("SELECT COUNT(*) FROM shadow_continuation_trades WHERE entry_at LIKE ?", (day + '%',)).fetchone()[0]) + 1
            daily_locked = bool((ev.get('daily') or {}).get('locked')) or action == 'SHADOW_ONLY'
            entry_fill = price * (1 + SLIPPAGE_RATE)
            cur = c.execute('''INSERT OR IGNORE INTO shadow_continuation_trades(
                code,name,signal_bucket,entry_at,entry_price,score,source_action,reasons_json,
                daily_lock_continuation,reentry_after_shadow_loss,reentry_after_shadow_win,
                prior_control_loss,same_code_sequence,day_sequence,prior_shadow_pnl_pct,
                prior_shadow_exit_reason,peak_price,trough_price,created_version)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                code, ev.get('name') or code, bucket, now.isoformat(), entry_fill,
                float(ev.get('score') or 0), action,
                json.dumps(ev.get('reasons') or [], ensure_ascii=False),
                1 if daily_locked else 0, 1 if prior_loss else 0, 1 if prior_win else 0,
                1 if _prior_control_loss(c, code, day) else 0,
                same_seq, day_seq,
                prior['pnlPct'] if prior else None,
                prior['exitReason'] if prior else None,
                entry_fill, entry_fill, VERSION,
            ))
            opened += int(cur.rowcount > 0)
    if opened:
        with _LOCK:
            _STATUS['entries'] += opened
            _STATUS['lastEntryAt'] = now.isoformat(timespec='seconds')
    return {'ok': True, 'opened': opened}


def _close(c, row, market, reason, now):
    entry = float(row['entry_price'])
    peak = max(float(row['peak_price'] or entry), market)
    trough = min(float(row['trough_price'] or entry), market)
    exit_fill = market * (1 - SLIPPAGE_RATE)
    pnl_pct = _net_pct(entry, exit_fill)
    c.execute('''UPDATE shadow_continuation_trades
                 SET exit_at=?,exit_price=?,exit_reason=?,pnl_pct=?,status='CLOSED',peak_price=?,trough_price=?
                 WHERE id=? AND status='OPEN' ''',
              (now.isoformat(), exit_fill, reason, pnl_pct, peak, trough, row['id']))
    return bool(c.total_changes)


def mark_positions(now=None):
    now = now or datetime.now(KST)
    positions = _open_positions()
    if not positions:
        return {'ok': True, 'closed': 0, 'open': 0}
    qmap = {str(q.get('code')): float(q.get('price') or 0) for q in latest_quotes([p['code'] for p in positions]) if float(q.get('price') or 0) > 0}
    closed = 0
    with _conn() as c:
        for p in positions:
            market = qmap.get(p['code'])
            if not market:
                continue
            entry = float(p['entry_price'])
            peak = max(float(p['peak_price'] or entry), market)
            trough = min(float(p['trough_price'] or entry), market)
            c.execute('UPDATE shadow_continuation_trades SET peak_price=?,trough_price=? WHERE id=? AND status=\'OPEN\'', (peak, trough, p['id']))
            reason = (
                'STOP_LOSS' if market <= entry * (1 - STOP_PCT)
                else 'TRAILING_STOP' if peak >= entry * (1 + TRAIL_ACTIVATE_PCT) and market <= peak * (1 - TRAIL_PCT)
                else 'COST_COVER_PROTECT' if peak >= entry * (1 + BREAKEVEN_ACTIVATE_PCT) and market <= entry * (1 + BREAKEVEN_BUFFER_PCT)
                else None
            )
            if reason:
                row = dict(p)
                row['peak_price'] = peak
                row['trough_price'] = trough
                if _close(c, row, market, reason, now):
                    closed += 1
    if closed:
        with _LOCK:
            _STATUS['closed'] += closed
            _STATUS['lastExitAt'] = now.isoformat(timespec='seconds')
    return {'ok': True, 'closed': closed, 'open': max(0, len(positions) - closed)}


def force_close_all(reason='EOD_EXIT', now=None):
    now = now or datetime.now(KST)
    positions = _open_positions()
    if not positions:
        return {'ok': True, 'closed': 0, 'unresolved': 0}
    qmap = {str(q.get('code')): float(q.get('price') or 0) for q in latest_quotes([p['code'] for p in positions]) if float(q.get('price') or 0) > 0}
    closed = 0
    unresolved = 0
    with _conn() as c:
        for p in positions:
            market = qmap.get(p['code'])
            if not market:
                unresolved += 1
                continue
            if _close(c, p, market, reason, now):
                closed += 1
    if closed:
        with _LOCK:
            _STATUS['closed'] += closed
            _STATUS['lastExitAt'] = now.isoformat(timespec='seconds')
    return {'ok': True, 'closed': closed, 'unresolved': unresolved}


def _cohort(rows):
    n = len(rows)
    wins = [r for r in rows if float(r.get('pnl_pct') or 0) > 0]
    losses = [r for r in rows if float(r.get('pnl_pct') or 0) < 0]
    gp = sum(float(r.get('pnl_pct') or 0) for r in wins)
    gl = abs(sum(float(r.get('pnl_pct') or 0) for r in losses))
    return {
        'trades': n,
        'wins': len(wins),
        'losses': len(losses),
        'winRate': round(100 * len(wins) / n, 1) if n else 0,
        'expectancyPct': round(sum(float(r.get('pnl_pct') or 0) for r in rows) / n, 4) if n else 0,
        'profitFactor': round(gp / gl, 3) if gl else (999 if gp else 0),
        'netNormalizedPct': round(sum(float(r.get('pnl_pct') or 0) for r in rows), 4),
    }


def report(limit=50):
    init_db()
    limit = max(1, min(int(limit), 200))
    today = _today()
    with _conn() as c:
        closed = [dict(r) for r in c.execute("SELECT * FROM shadow_continuation_trades WHERE status='CLOSED' ORDER BY id").fetchall()]
        today_rows = [r for r in closed if str(r.get('entry_at') or '').startswith(today)]
        after_loss = [r for r in closed if int(r.get('reentry_after_shadow_loss') or 0) == 1]
        after_win = [r for r in closed if int(r.get('reentry_after_shadow_win') or 0) == 1]
        after_control_loss = [r for r in closed if int(r.get('prior_control_loss') or 0) == 1]
        lock_cont = [r for r in closed if int(r.get('daily_lock_continuation') or 0) == 1]
        first_entries = [r for r in closed if int(r.get('same_code_sequence') or 1) == 1]
        open_count = int(c.execute("SELECT COUNT(*) FROM shadow_continuation_trades WHERE status='OPEN'").fetchone()[0])
        recent = [dict(r) for r in c.execute('''SELECT id,code,name,signal_bucket,entry_at,entry_price,score,source_action,
                    daily_lock_continuation,reentry_after_shadow_loss,reentry_after_shadow_win,prior_control_loss,
                    same_code_sequence,exit_at,exit_price,exit_reason,pnl_pct,status
                    FROM shadow_continuation_trades ORDER BY id DESC LIMIT ?''', (limit,)).fetchall()]
    study = _cohort(after_loss)
    return {
        'ok': True,
        'version': VERSION,
        'researchOnly': True,
        'controlStrategy': 'v0.8.0 LOCKED',
        'realOrderEnabled': False,
        'controlAutoMutation': False,
        'extraNhRestCalls': False,
        'normalizedResearchReturns': True,
        'continuationPolicy': {
            'ignoresDailyLossLockForResearch': True,
            'ignoresConsecutiveLossLockForResearch': True,
            'ignoresMaxDailyTradesForResearch': True,
            'ignoresGlobalMaxOpenForResearch': True,
            'oneOpenPositionPerCode': True,
            'dedupeOneEntryPerCodeSignalBucket': True,
        },
        'allClosed': _cohort(closed),
        'todayClosed': _cohort(today_rows),
        'firstEntries': _cohort(first_entries),
        'sameStockReentryAfterLoss': study,
        'sameStockReentryAfterWin': _cohort(after_win),
        'afterActualControlLoss': _cohort(after_control_loss),
        'dailyLockContinuation': _cohort(lock_cont),
        'openPositions': open_count,
        'policyStudy': {
            'minimumReentryLossSample': MIN_REENTRY_SAMPLE,
            'currentReentryLossSample': study['trades'],
            'readyForControlReview': study['trades'] >= MIN_REENTRY_SAMPLE,
            'controlChangeApplied': False,
            'ruleUnderStudy': 'STOP/LOSS 후 동일 종목 당일 재진입 금지',
        },
        'recent': recent,
    }


def status():
    with _LOCK:
        s = dict(_STATUS)
    try:
        s['report'] = report(limit=5)
    except Exception as exc:
        s['reportError'] = f'{type(exc).__name__}: {exc}'[:500]
    return s


def _loop():
    _STATUS.update({'running': True, 'startedAt': datetime.now(KST).isoformat(timespec='seconds'), 'lastError': None})
    last_scan = 0.0
    while not _STOP.is_set():
        try:
            now = datetime.now(KST)
            _STATUS['lastCycleAt'] = now.isoformat(timespec='seconds')
            mark_positions(now)
            if _eod_due(now):
                force_close_all('EOD_EXIT', now)
            elif _entry_hours(now) and time.monotonic() - last_scan >= SCAN_SEC:
                evs = scan()
                observe_scan(evs, now)
                last_scan = time.monotonic()
                _STATUS['lastScanAt'] = now.isoformat(timespec='seconds')
            _STATUS['lastError'] = None
        except Exception as exc:
            _STATUS['lastError'] = f'{type(exc).__name__}: {exc}'[:500]
        _STOP.wait(LOOP_SEC)
    _STATUS['running'] = False


def start():
    global _THREAD
    with _LOCK:
        if _THREAD and _THREAD.is_alive():
            return status()
        _STOP.clear()
        _THREAD = threading.Thread(target=_loop, daemon=True, name='shadow-continuation-research')
        _THREAD.start()
    return status()


def stop():
    _STOP.set()
    if _THREAD and _THREAD.is_alive():
        _THREAD.join(timeout=5)
    return status()
