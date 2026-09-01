"""Decision Intelligence Shadow Layer v0.17.7.

This module strengthens decision context and risk research without changing
Control v0.8.0, entry/exit logic, sizing, or broker execution.

It intentionally reuses existing data instead of duplicating engines:
- Control evaluation: paper_engine.scan()
- Point-in-time features: latest scanner_intel_snapshots already recorded by v0.17.6
- Market regime: shared market_state_engine
- Loss labels/MFE/MAE: existing paper_trades fields
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import mean, median

from collector import DB_PATH, KST
from market_state_engine import classify_market_state
from paper_engine import daily_stats, open_positions, scan as control_scan

SNAPSHOT_SEC = max(60, int(os.getenv('DECISION_INTEL_SNAPSHOT_SEC', '300')))
RETENTION_DAYS = max(30, min(int(os.getenv('DECISION_INTEL_RETENTION_DAYS', '180')), 730))
_STOP = threading.Event()
_THREAD = None
_LOCK = threading.RLock()
_STATE = {
    'running': False, 'lastSnapshotAt': None, 'lastError': None,
    'snapshots': 0, 'rows': 0, 'intervalSec': SNAPSHOT_SEC,
    'metadataSchema': 'decision-metadata-v1',
    'riskScoreMode': 'SHADOW_ONLY',
    'lossAnalysis': 'evidence-hypothesis-v1',
    'sharedMarketStateEngine': True,
    'controlMutation': False, 'entryExitMutation': False, 'realOrder': False,
}


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def init_db():
    with _conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS decision_intel_snapshots(
          snapshot_at TEXT NOT NULL, trade_date TEXT NOT NULL, code TEXT NOT NULL,
          name TEXT, strategy_action TEXT, strategy_score REAL,
          risk_score REAL NOT NULL, risk_band TEXT NOT NULL, risk_confidence REAL NOT NULL,
          market_state TEXT, market_confidence REAL, metadata_json TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT 'DECISION_INTEL_SHADOW',
          PRIMARY KEY(snapshot_at,code));
        CREATE INDEX IF NOT EXISTS idx_decision_intel_day_code
          ON decision_intel_snapshots(trade_date,code,snapshot_at);
        CREATE INDEX IF NOT EXISTS idx_decision_intel_risk
          ON decision_intel_snapshots(trade_date,risk_score DESC);
        ''')


def _num(v):
    try: return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _parse_json(v):
    try: return json.loads(v) if v else {}
    except Exception: return {}


def _latest_scanner_rows():
    """Reuse the v0.17.6 snapshot; do not run Scanner Intelligence twice."""
    try:
        with _conn() as c:
            latest = c.execute('SELECT MAX(snapshot_at) FROM scanner_intel_snapshots').fetchone()[0]
            if not latest: return None, {}
            rows = c.execute('SELECT * FROM scanner_intel_snapshots WHERE snapshot_at=?', (latest,)).fetchall()
        return latest, {str(r['code']): dict(r) for r in rows}
    except sqlite3.OperationalError:
        return None, {}


def _latest_proxy_return(code):
    day = datetime.now(KST).date().isoformat() + '%'
    try:
        with _conn() as c:
            rows = c.execute('SELECT open,close FROM bars_5m WHERE code=? AND bucket LIKE ? ORDER BY bucket',
                             (str(code), day)).fetchall()
        if not rows: return None
        op, cl = _num(rows[0]['open']), _num(rows[-1]['close'])
        return (cl / op - 1) * 100 if op and cl else None
    except Exception:
        return None


def current_market_state(scanner_rows=None):
    rows = scanner_rows or {}
    returns = [_num(r.get('intraday_ret_pct')) for r in rows.values()]
    returns = [x for x in returns if x is not None]
    breadth = sum(x > 0 for x in returns) / len(returns) if returns else None
    med = median(returns) if returns else None
    proxies = [x for x in (_latest_proxy_return('069500'), _latest_proxy_return('229200')) if x is not None]
    proxy = median(proxies) if proxies else None
    state = classify_market_state(proxy, breadth, med)
    state['coverage'] = {'scannerRows': len(rows), 'returnRows': len(returns), 'proxyRows': len(proxies)}
    state['source'] = 'shared-market-state-engine/local-db'
    return state


def _add(parts, name, points, evidence):
    if points > 0: parts.append({'factor': name, 'points': round(float(points), 2), 'evidence': evidence})


def risk_score_shadow(ev, intel, market_state, daily, open_count):
    """Higher score means higher observed execution risk. It never gates a trade."""
    parts, score, observed = [], 0.0, 0
    if daily:
        observed += 1
        streak = int(daily.get('consecutiveLosses') or 0)
        if daily.get('locked'): _add(parts, 'HARD_DAILY_LOCK', 35, 'existing daily lock active'); score += 35
        if streak:
            p = min(20, streak * 10); _add(parts, 'LOSS_STREAK', p, f'{streak} losses'); score += p
    label = str((market_state or {}).get('label') or 'UNKNOWN'); observed += 1
    p = {'RED': 22, 'CAUTION': 10, 'UNKNOWN': 5}.get(label, 0)
    if p: _add(parts, 'MARKET_STATE', p, label); score += p

    market = (ev or {}).get('market') or {}
    if market.get('liquidityOk') is not None:
        observed += 1
        if not market.get('liquidityOk'): _add(parts, 'LIQUIDITY', 15, 'existing liquidity gate failed'); score += 15
    spread = _num((intel or {}).get('spread_pct'))
    if spread is None: spread = _num(market.get('spreadPct'))
    if spread is not None:
        observed += 1
        p = 12 if spread > .40 else 7 if spread > .25 else 0
        if p: _add(parts, 'SPREAD', p, f'{spread:.3f}%'); score += p
    turnover = _num((intel or {}).get('turnover_eok'))
    if turnover is None: turnover = _num(market.get('turnoverEok'))
    if turnover is not None:
        observed += 1
        p = 10 if turnover < 5 else 5 if turnover < 10 else 0
        if p: _add(parts, 'LOW_TURNOVER', p, f'{turnover:.1f}억'); score += p
    atr = _num((intel or {}).get('atr14_pct'))
    if atr is not None:
        observed += 1
        p = 12 if atr > 8 else 7 if atr > 5 else 0
        if p: _add(parts, 'HIGH_VOLATILITY', p, f'ATR14 {atr:.2f}%'); score += p
    gap = _num((intel or {}).get('gap_pct'))
    if gap is not None:
        observed += 1; ag = abs(gap); p = 12 if ag > 8 else 7 if ag > 5 else 0
        if p: _add(parts, 'LARGE_GAP', p, f'{gap:.2f}%'); score += p
    rvol = _num((intel or {}).get('rvol_time'))
    if rvol is not None:
        observed += 1
        p = 6 if rvol < .8 else 4 if rvol > 5 else 0
        if p: _add(parts, 'RVOL_EXTREME', p, f'{rvol:.2f}x'); score += p

    ind = (ev or {}).get('indicators') or {}
    rsi, adx, price, vwap = map(_num, (ind.get('rsi'), ind.get('adx'), ind.get('price'), ind.get('vwap')))
    if rsi is not None or adx is not None:
        observed += 1
        if rsi is not None and rsi > 72: _add(parts, 'OVERBOUGHT_STRETCH', 6, f'RSI {rsi:.1f}'); score += 6
        if adx is not None and adx < 22: _add(parts, 'WEAK_TREND', 6, f'ADX {adx:.1f}'); score += 6
    if price and vwap:
        observed += 1; dist = (price / vwap - 1) * 100
        if dist > 1.5: _add(parts, 'VWAP_STRETCH', 6, f'+{dist:.2f}%'); score += 6
    imb = _num((intel or {}).get('book_imbalance'))
    if imb is not None:
        observed += 1
        if imb < -.30: _add(parts, 'SELL_BOOK_IMBALANCE', 5, f'{imb:.2f}'); score += 5
    if open_count >= 2: _add(parts, 'PORTFOLIO_CAPACITY', 8, f'{open_count} open'); score += 8

    score = round(max(0, min(100, score)), 1)
    band = 'LOW' if score <= 25 else 'MODERATE' if score <= 50 else 'HIGH' if score <= 75 else 'EXTREME'
    return {'score': score, 'band': band, 'confidence': round(min(1, observed / 10), 2),
            'components': sorted(parts, key=lambda x: x['points'], reverse=True),
            'mode': 'SHADOW_ONLY', 'affectsEntry': False, 'affectsExit': False,
            'affectsSizing': False, 'autoPromotion': False}


def build_contexts(limit=40):
    init_db(); scanner_at, scanner_rows = _latest_scanner_rows()
    market_state, daily, positions = current_market_state(scanner_rows), daily_stats(), open_positions()
    out = []
    for ev in control_scan()[:max(1, min(int(limit), 100))]:
        code = str(ev.get('code') or ''); intel = scanner_rows.get(code) or {}
        risk = risk_score_shadow(ev, intel, market_state, daily, len(positions))
        ind, market = ev.get('indicators') or {}, ev.get('market') or {}
        out.append({
            'schema': 'decision-metadata-v1', 'capturedAt': datetime.now(KST).isoformat(timespec='seconds'),
            'code': code, 'name': ev.get('name') or intel.get('name') or code,
            'market': intel.get('market') or market.get('market'),
            'control': {'strategy': 'v0.8.0 LOCKED', 'action': ev.get('action'), 'score': ev.get('score'),
                        'reasons': ev.get('reasons') or [], 'blockedReasons': ev.get('blockedReasons') or [],
                        'marketBreadth': ev.get('marketBreadth'), 'indicatorBucket': ind.get('bucket'),
                        'rsi': ind.get('rsi'), 'adx': ind.get('adx'), 'plusDI': ind.get('plusDI'),
                        'minusDI': ind.get('minusDI'), 'volumeRatio': ind.get('volumeRatio'),
                        'vwap': ind.get('vwap'), 'ema9': ind.get('ema9'), 'ema20': ind.get('ema20')},
            'scannerIntelligence': {'snapshotAt': scanner_at, 'rankSipRvol': intel.get('rank_sip_rvol'),
                'rankRvolGap': intel.get('rank_rvol_gap'), 'rankRvolCatalyst': intel.get('rank_rvol_catalyst'),
                'rankRelativeStrength': intel.get('rank_relative_strength'), 'rvol5': intel.get('rvol5'),
                'rvol15': intel.get('rvol15'), 'rvol30': intel.get('rvol30'), 'rvolTime': intel.get('rvol_time'),
                'gapPct': intel.get('gap_pct'), 'atr14Pct': intel.get('atr14_pct'),
                'relativeStrengthPct': intel.get('relative_strength_pct'), 'spreadPct': intel.get('spread_pct'),
                'bookImbalance': intel.get('book_imbalance'), 'catalystState': intel.get('catalyst_state'),
                'turnoverEok': intel.get('turnover_eok')},
            'marketState': market_state,
            'portfolioRisk': {'dailyLocked': bool(daily.get('locked')),
                'consecutiveLosses': int(daily.get('consecutiveLosses') or 0),
                'dailyPnl': daily.get('pnl'), 'openPositions': len(positions)},
            'riskShadow': risk,
            'sources': ['paper_engine.control_evaluation', 'scanner_intel_snapshots', 'shared_market_state_engine']})
    return {'ok': True, 'generatedAt': datetime.now(KST).isoformat(timespec='seconds'),
            'marketState': market_state, 'daily': daily, 'openPositions': len(positions),
            'scannerSnapshotAt': scanner_at, 'rows': out,
            'safety': {'control': 'v0.8.0 LOCKED', 'riskScoreMode': 'SHADOW_ONLY',
                       'liveMutation': False, 'entryExitMutation': False, 'realOrder': False}}


def record_snapshot(limit=40):
    data = build_contexts(limit); now = datetime.now(KST)
    at, day, rows = now.replace(second=0, microsecond=0).isoformat(), now.date().isoformat(), data.get('rows') or []
    with _conn() as c:
        for m in rows:
            risk, ms, ctl = m.get('riskShadow') or {}, m.get('marketState') or {}, m.get('control') or {}
            c.execute('''INSERT OR REPLACE INTO decision_intel_snapshots(
              snapshot_at,trade_date,code,name,strategy_action,strategy_score,risk_score,risk_band,
              risk_confidence,market_state,market_confidence,metadata_json,source)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (at, day, m.get('code'), m.get('name'), ctl.get('action'), ctl.get('score'), risk.get('score') or 0,
               risk.get('band') or 'LOW', risk.get('confidence') or 0, ms.get('label'), ms.get('confidence'),
               json.dumps(m, ensure_ascii=False), 'DECISION_INTEL_SHADOW'))
        cutoff = (now - timedelta(days=RETENTION_DAYS)).date().isoformat()
        c.execute('DELETE FROM decision_intel_snapshots WHERE trade_date<?', (cutoff,))
    with _LOCK:
        _STATE['lastSnapshotAt'], _STATE['lastError'] = at, None
        _STATE['snapshots'] += 1; _STATE['rows'] += len(rows)
    return {'ok': True, 'snapshotAt': at, 'rows': len(rows), 'marketState': data.get('marketState')}


def _decision_before(code, entry_at):
    try:
        with _conn() as c:
            r = c.execute('''SELECT metadata_json FROM decision_intel_snapshots
              WHERE code=? AND snapshot_at<=? ORDER BY snapshot_at DESC LIMIT 1''',
              (str(code), str(entry_at))).fetchone()
        return _parse_json(r['metadata_json']) if r else {}
    except Exception: return {}


def _hypotheses(t, snap, decision):
    out = {}; pnl = _num(t.get('pnl_pct')) or 0
    mfe, mae = _num(t.get('mfe_pct')), _num(t.get('mae_pct'))
    def put(c, conf, evidence):
        if c not in out or conf > out[c][0]: out[c] = (conf, evidence)
    vr, adx, rsi = _num(snap.get('volumeRatio')), _num(snap.get('adx')), _num(snap.get('rsi'))
    breadth, spread = _num(snap.get('marketBreadth')), _num(snap.get('spreadPct'))
    vwap, entry = _num(snap.get('vwap')), _num(t.get('entry_price'))
    if vr is not None and vr < 1.5: put('LOW_VOLUME', .75, f'volumeRatio={vr:.2f}')
    if adx is not None and adx < 25: put('WEAK_TREND', .70, f'ADX={adx:.1f}')
    if rsi is not None and rsi > 70: put('OVERBOUGHT_ENTRY', .65, f'RSI={rsi:.1f}')
    if breadth is not None and breadth < .35: put('WEAK_MARKET_BREADTH', .75, f'breadth={breadth:.2f}')
    if spread is not None and spread > .25: put('LIQUIDITY_FRICTION', .65, f'spread={spread:.3f}%')
    if entry and vwap and (entry / vwap - 1) * 100 > 1: put('VWAP_CHASE', .70, f'entry-vwap={(entry/vwap-1)*100:.2f}%')
    if mfe is not None and mfe >= .8 and pnl < 0: put('GAVE_BACK_PROFIT', .90, f'MFE={mfe:.2f}% -> pnl={pnl:.2f}%')
    if mfe is not None and mae is not None and mfe < .35 and abs(mae) >= .7: put('NO_FOLLOW_THROUGH', .80, f'MFE={mfe:.2f}% / MAE={mae:.2f}%')
    regime = ((decision.get('marketState') or {}).get('label'))
    if regime in ('RED','CAUTION'): put('ADVERSE_MARKET_STATE', .70 if regime == 'RED' else .55, regime)
    risk = _num((decision.get('riskShadow') or {}).get('score'))
    if risk is not None and risk >= 60: put('HIGH_SHADOW_RISK', .60, f'riskScore={risk:.1f}')
    legacy = str(t.get('failure_type') or '')
    if legacy and legacy != 'WIN': put('LEGACY_' + legacy, .85, 'existing paper_engine failure label')
    return sorted(({'cause': c, 'confidence': round(v[0],2), 'evidence': v[1]} for c,v in out.items()),
                  key=lambda x:x['confidence'], reverse=True)


def loss_analysis(limit=200):
    init_db()
    with _conn() as c:
        trades = [dict(r) for r in c.execute('SELECT * FROM paper_trades WHERE status=\'CLOSED\' ORDER BY id DESC LIMIT ?',
                                             (max(20,min(int(limit),1000)),)).fetchall()]
    losses = [t for t in trades if (_num(t.get('pnl')) or 0) < 0]
    wins = [t for t in trades if (_num(t.get('pnl')) or 0) > 0]
    counts, confs, legacy, examples = Counter(), defaultdict(float), Counter(), []
    groups = {'wins': defaultdict(list), 'losses': defaultdict(list)}
    for t in trades:
        snap = _parse_json(t.get('entry_snapshot')); g = groups['wins' if (_num(t.get('pnl')) or 0) > 0 else 'losses']
        for k in ('score','volumeRatio','adx','rsi','marketBreadth','spreadPct','activityScore'):
            v = _num(snap.get(k))
            if v is not None: g[k].append(v)
    for t in losses:
        snap, decision = _parse_json(t.get('entry_snapshot')), _decision_before(t.get('code'), t.get('entry_at'))
        hs = _hypotheses(t, snap, decision)
        for h in hs: counts[h['cause']] += 1; confs[h['cause']] += h['confidence']
        ft = str(t.get('failure_type') or 'UNCLASSIFIED'); legacy[ft] += 1
        if len(examples) < 20:
            examples.append({'tradeId': t.get('id'), 'code': t.get('code'), 'pnlPct': t.get('pnl_pct'),
                'mfePct': t.get('mfe_pct'), 'maePct': t.get('mae_pct'), 'exitReason': t.get('exit_reason'),
                'legacyFailureType': ft, 'hypotheses': hs[:5], 'decisionContextFound': bool(decision)})
    ranked = [{'cause': c, 'lossTrades': n, 'shareOfLossesPct': round(n/len(losses)*100,1) if losses else 0,
               'avgConfidence': round(confs[c]/n,2)} for c,n in counts.most_common()]
    compare = {}
    for k in ('score','volumeRatio','adx','rsi','marketBreadth','spreadPct','activityScore'):
        w,l = groups['wins'].get(k) or [], groups['losses'].get(k) or []
        compare[k] = {'winAvg': round(mean(w),3) if w else None, 'lossAvg': round(mean(l),3) if l else None,
                      'winN': len(w), 'lossN': len(l)}
    return {'ok': True, 'engine': 'evidence-hypothesis-v1',
            'note': '원인 확정이 아니라 기존 failure_type + MFE/MAE + 진입 메타데이터의 증거 기반 가설입니다.',
            'trades': len(trades), 'wins': len(wins), 'losses': len(losses),
            'legacyFailureTypes': dict(legacy), 'rankedHypotheses': ranked,
            'winnerLoserFeatureCompare': compare, 'recentLossExamples': examples,
            'autoStrategyMutation': False}


def snapshot_stats():
    init_db()
    with _conn() as c:
        total = int(c.execute('SELECT COUNT(*) FROM decision_intel_snapshots').fetchone()[0])
        days = int(c.execute('SELECT COUNT(DISTINCT trade_date) FROM decision_intel_snapshots').fetchone()[0])
        latest = c.execute('SELECT MAX(snapshot_at) FROM decision_intel_snapshots').fetchone()[0]
    return {'rows': total, 'days': days, 'latestSnapshotAt': latest}


def report(limit=40):
    contexts = build_contexts(limit); rows = contexts.get('rows') or []
    risks = [x.get('riskShadow') or {} for x in rows]
    return {'ok': True, 'version': '0.17.7',
            'metadataEngine': {'schema': 'decision-metadata-v1', 'rows': len(rows),
                'sources': ['Control evaluation','Scanner Intelligence snapshot','shared market state','portfolio risk'],
                'contexts': rows},
            'riskScoreEngine': {'mode': 'SHADOW_ONLY',
                'averageScore': round(mean([float(x.get('score') or 0) for x in risks]),1) if risks else 0,
                'highOrExtreme': sum(1 for x in risks if x.get('band') in ('HIGH','EXTREME')),
                'affectsLiveTrading': False},
            'marketState': contexts.get('marketState'), 'lossAnalysis': loss_analysis(),
            'snapshots': snapshot_stats(), 'safety': contexts.get('safety')}


def status():
    with _LOCK: s = dict(_STATE)
    try: s['snapshotStats'] = snapshot_stats()
    except Exception as e: s['snapshotStats'] = {'error': f'{type(e).__name__}: {e}'}
    return s


def _loop():
    with _LOCK: _STATE['running'] = True
    try:
        while not _STOP.is_set():
            now = datetime.now(KST); hm = now.hour*60 + now.minute
            if now.weekday() < 5 and 540 <= hm <= 930:
                try: record_snapshot(40)
                except Exception as e:
                    with _LOCK: _STATE['lastError'] = f'{type(e).__name__}: {e}'[:600]
            _STOP.wait(SNAPSHOT_SEC)
    finally:
        with _LOCK: _STATE['running'] = False


def start():
    global _THREAD
    init_db()
    with _LOCK:
        if _THREAD and _THREAD.is_alive(): return status()
        _STOP.clear(); _THREAD = threading.Thread(target=_loop, daemon=True, name='decision-intelligence-shadow')
        _THREAD.start()
    return status()


def stop():
    _STOP.set(); return {'ok': True, 'status': status()}


init_db()
