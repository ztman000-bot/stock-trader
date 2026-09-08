"""Research/Data Health monitor v0.17.15.

Research/operations only. Never sends orders or changes Control v0.8.0.
Adds after-market official NH 5m repair toward 95%, Paper-trade-aware 1m
repair, forward snapshot coverage, and backup freshness visibility.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from collector import DB_PATH, KST, regular_session
from data_quality import audit as five_minute_audit, quality_details as five_minute_details
from db_backup import status as backup_status
from historical_accumulator import _download_day as five_minute_download_day
from historical_accumulator import status as five_minute_history_status
from kr_1m_research import (
    _download_day as one_minute_download_day,
    _five_minute_history_busy,
    _history_codes,
    coverage as one_minute_coverage,
    research_status as one_minute_status,
)
from one_minute_exit_replay import validation_status as exit_replay_status

VERSION = '0.17.15'
SNAPSHOT_EXPECTED_FULL = 78
SNAPSHOT_MIN_COVERAGE_PCT = 95.0
OFFICIAL_5M_TARGET_PCT = 95.0
OFFICIAL_5M_AUDIT_DAYS = max(10, min(int(os.getenv('DATA_HEALTH_5M_AUDIT_DAYS', '30')), 120))
OFFICIAL_5M_REPAIR_LIMIT = max(1, min(int(os.getenv('DATA_HEALTH_5M_REPAIR_LIMIT', '8')), 20))
OFFICIAL_5M_RETRY_HOURS = max(2, min(int(os.getenv('DATA_HEALTH_5M_RETRY_HOURS', '12')), 72))
EXIT_MIN_REPLAY_TRADES = 30
EXIT_MIN_REPLAY_COVERAGE_PCT = 95.0
EXIT_MIN_PATH_AGREEMENT_PCT = 85.0
EXIT_MIN_REASON_MATCH_PCT = 90.0
REPLAY_REPAIR_LIMIT = max(1, min(int(os.getenv('DATA_HEALTH_REPLAY_REPAIR_LIMIT', '2')), 6))
REPLAY_RETRY_HOURS = max(6, min(int(os.getenv('DATA_HEALTH_REPLAY_RETRY_HOURS', '24')), 168))
REPAIR_LIMIT_PER_CYCLE = max(1, min(int(os.getenv('DATA_HEALTH_PARTIAL_REPAIR_LIMIT', '4')), 12))
MONITOR_INTERVAL_SEC = max(300, min(int(os.getenv('DATA_HEALTH_INTERVAL_SEC', '900')), 3600))
DB_AUDIT_AFTER_MINUTE = max(
    16 * 60,
    min(int(os.getenv('DATA_HEALTH_DB_AUDIT_AFTER_MINUTE', str(16 * 60 + 10))), 23 * 60 + 59),
)
CACHE_SEC = max(15, min(int(os.getenv('DATA_HEALTH_CACHE_SEC', '60')), 300))

_LOCK = threading.RLock()
_THREAD = None
_STOP = threading.Event()
_CACHE = {'at': 0.0, 'value': None}
_STATE = {
    'running': False,
    'lastCycleAt': None,
    'lastError': None,
    'lastRepairAt': None,
    'lastRepair': None,
    'last5mRepairAt': None,
    'last5mRepair': None,
    'lastReplayRepairAt': None,
    'lastReplayRepair': None,
    'last1mRepairAt': None,
    'last1mRepair': None,
    'lastDbAuditDate': None,
    'lastDbAuditAt': None,
    'lastDbQuickCheck': None,
    'lastDbAuditError': None,
    'researchOnly': True,
    'controlMutation': False,
    'realOrder': False,
}


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def _pct(num, den):
    return round(100.0 * num / den, 2) if den else None


def _safe_table_exists(c, table):
    return bool(c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def _ensure_meta_tables():
    with _conn() as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS data_health_meta("
            "key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL)"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS data_health_repair_attempts("
            "kind TEXT NOT NULL,code TEXT NOT NULL,session_date TEXT NOT NULL,"
            "status TEXT NOT NULL,rows INTEGER NOT NULL DEFAULT 0,detail TEXT,"
            "attempted_at TEXT NOT NULL,PRIMARY KEY(kind,code,session_date))"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_data_health_repair_attempts_at "
            "ON data_health_repair_attempts(kind,attempted_at)"
        )


def _next_trading_day(day):
    d = day
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _default_forward_baseline():
    now = datetime.now(KST)
    if now.weekday() < 5 and now.hour * 60 + now.minute < 9 * 60:
        return now.date()
    return _next_trading_day(now.date() + timedelta(days=1))


def _forward_baseline_date():
    _ensure_meta_tables()
    with _conn() as c:
        row = c.execute(
            "SELECT value FROM data_health_meta WHERE key='forward_snapshot_baseline'"
        ).fetchone()
        if row and row[0]:
            return str(row[0])
        value = _default_forward_baseline().isoformat()
        c.execute(
            "INSERT OR REPLACE INTO data_health_meta(key,value,updated_at) VALUES(?,?,?)",
            ('forward_snapshot_baseline', value, datetime.now(KST).isoformat(timespec='seconds')),
        )
        return value


def _trading_dates(limit=8):
    dates = set()
    try:
        with _conn() as c:
            if _safe_table_exists(c, 'bars_1m'):
                dates.update(str(r[0]) for r in c.execute(
                    'SELECT DISTINCT session_date FROM bars_1m ORDER BY session_date DESC LIMIT ?',
                    (max(1, int(limit) * 2),)
                ).fetchall() if r[0])
            if _safe_table_exists(c, 'bars_5m'):
                dates.update(str(r[0]) for r in c.execute(
                    "SELECT DISTINCT substr(bucket,1,10) d FROM bars_5m ORDER BY d DESC LIMIT ?",
                    (max(1, int(limit) * 2),)
                ).fetchall() if r[0])
    except Exception:
        pass
    return sorted(dates)[-max(1, int(limit)):]


def _expected_snapshots(day):
    now = datetime.now(KST)
    today = now.date().isoformat()
    if day < today:
        return SNAPSHOT_EXPECTED_FULL
    if day > today or now.weekday() >= 5:
        return 0
    hm = now.hour * 60 + now.minute
    if hm < 540:
        return 0
    capped = min(hm, 15 * 60 + 25)
    return min(SNAPSHOT_EXPECTED_FULL, max(1, (capped - 540) // 5 + 1))


def _snapshot_coverage(table, day_col='trade_date', at_col='snapshot_at', limit=5):
    dates = _trading_dates(max(limit, 5))
    baseline = _forward_baseline_date()
    rows = []
    try:
        with _conn() as c:
            if not _safe_table_exists(c, table):
                raise sqlite3.OperationalError(f'missing table {table}')
            for day in dates[-limit:]:
                expected = _expected_snapshots(day)
                actual = int(c.execute(
                    f'SELECT COUNT(DISTINCT {at_col}) FROM {table} WHERE {day_col}=?',
                    (day,),
                ).fetchone()[0])
                coverage = min(100.0, _pct(actual, expected) or 0.0) if expected else None
                rows.append({
                    'date': day,
                    'expectedSnapshots': expected,
                    'actualSnapshots': actual,
                    'coveragePct': coverage,
                    'state': 'PENDING' if not expected else (
                        'COMPLETE' if coverage >= SNAPSHOT_MIN_COVERAGE_PCT else 'INCOMPLETE_DAY'
                    ),
                    'forwardCohort': day >= baseline,
                })
    except Exception as exc:
        return {
            'ok': False,
            'error': f'{type(exc).__name__}: {exc}',
            'days': rows,
            'minCoveragePct': SNAPSHOT_MIN_COVERAGE_PCT,
            'forwardBaselineDate': baseline,
        }
    usable = [x for x in rows if x['coveragePct'] is not None]
    forward = [x for x in usable if x['forwardCohort']]
    avg = round(sum(x['coveragePct'] for x in usable) / len(usable), 2) if usable else None
    forward_avg = (
        round(sum(x['coveragePct'] for x in forward) / len(forward), 2)
        if forward else None
    )
    latest = usable[-1] if usable else None
    return {
        'ok': True,
        'expectedFullDaySnapshots': SNAPSHOT_EXPECTED_FULL,
        'minCoveragePct': SNAPSHOT_MIN_COVERAGE_PCT,
        'averageCoveragePct': avg,
        'forwardBaselineDate': baseline,
        'forwardAverageCoveragePct': forward_avg,
        'forwardDays': len(forward),
        'latestTradingDay': latest,
        'incompleteDays': [x['date'] for x in usable if x['state'] == 'INCOMPLETE_DAY'],
        'forwardIncompleteDays': [x['date'] for x in forward if x['state'] == 'INCOMPLETE_DAY'],
        'days': rows,
        'futureDataBackfillAllowed': False,
    }


def _outcome_label_quality():
    try:
        with _conn() as c:
            if not _safe_table_exists(c, 'decision_observations'):
                return {
                    'ok': True, 'observations': 0, 'labeled': 0,
                    'fullyOfficial': 0, 'officialLabelPct': None,
                }
            total = int(c.execute(
                'SELECT COUNT(*) FROM decision_observations'
            ).fetchone()[0])
            labeled = int(c.execute(
                'SELECT COUNT(*) FROM decision_observations WHERE label_count>0'
            ).fetchone()[0])
            official = int(c.execute(
                'SELECT COUNT(*) FROM decision_observations WHERE labels_official=1'
            ).fetchone()[0])
        return {
            'ok': True, 'observations': total, 'labeled': labeled,
            'fullyOfficial': official, 'officialLabelPct': _pct(official, labeled),
        }
    except Exception as exc:
        return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}


def _strict_exit_readiness():
    try:
        r = exit_replay_status()
    except Exception as exc:
        return {'ok': False, 'ready': False, 'error': f'{type(exc).__name__}: {exc}'}
    closed = int(r.get('closedPaperTrades') or 0)
    replayable = int(r.get('replayableTrades') or 0)
    coverage = _pct(replayable, closed) or 0.0
    path = float(r.get('pathAgreementPct') or 0.0)
    match = float(r.get('actualReasonMatchPct') or 0.0)
    ready = bool(
        replayable >= EXIT_MIN_REPLAY_TRADES
        and coverage >= EXIT_MIN_REPLAY_COVERAGE_PCT
        and path >= EXIT_MIN_PATH_AGREEMENT_PCT
        and match >= EXIT_MIN_REASON_MATCH_PCT
    )
    reasons = []
    if replayable < EXIT_MIN_REPLAY_TRADES:
        reasons.append(f'replayable {replayable}/{EXIT_MIN_REPLAY_TRADES}')
    if coverage < EXIT_MIN_REPLAY_COVERAGE_PCT:
        reasons.append(f'coverage {coverage:.1f}%<{EXIT_MIN_REPLAY_COVERAGE_PCT:.0f}%')
    if path < EXIT_MIN_PATH_AGREEMENT_PCT:
        reasons.append(f'path {path:.1f}%<{EXIT_MIN_PATH_AGREEMENT_PCT:.0f}%')
    if match < EXIT_MIN_REASON_MATCH_PCT:
        reasons.append(f'reason {match:.1f}%<{EXIT_MIN_REASON_MATCH_PCT:.0f}%')
    return {
        'ok': True,
        'ready': ready,
        'closedPaperTrades': closed,
        'replayableTrades': replayable,
        'replayCoveragePct': round(coverage, 1),
        'pathAgreementPct': path,
        'actualReasonMatchPct': match,
        'requirements': {
            'minReplayTrades': EXIT_MIN_REPLAY_TRADES,
            'minReplayCoveragePct': EXIT_MIN_REPLAY_COVERAGE_PCT,
            'minPathAgreementPct': EXIT_MIN_PATH_AGREEMENT_PCT,
            'minActualReasonMatchPct': EXIT_MIN_REASON_MATCH_PCT,
        },
        'reason': 'READY' if ready else '; '.join(reasons),
        'engine': r.get('engine'),
        'intrabarAmbiguityExplicit': True,
        'controlMutation': False,
    }


def _backup_freshness(backup):
    latest = (backup or {}).get('latest') or {}
    value = latest.get('modifiedAt')
    if not value:
        return {'backupAgeHours': None, 'backupFresh': False, 'backupFreshLimitHours': None}
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        now = datetime.now(KST)
        age = max(0.0, (now - dt.astimezone(KST)).total_seconds() / 3600)
        limit = 84 if now.weekday() >= 5 else 36
        return {
            'backupAgeHours': round(age, 1),
            'backupFresh': age <= limit,
            'backupFreshLimitHours': limit,
        }
    except Exception:
        return {'backupAgeHours': None, 'backupFresh': False, 'backupFreshLimitHours': None}


def _db_file_status():
    p = Path(DB_PATH)
    wal = Path(str(p) + '-wal')
    shm = Path(str(p) + '-shm')
    try:
        b = backup_status()
    except Exception as exc:
        b = {'ok': False, 'error': f'{type(exc).__name__}: {exc}', 'orderAccess': False}
    with _LOCK:
        audit = {
            'lastDbAuditDate': _STATE.get('lastDbAuditDate'),
            'lastDbAuditAt': _STATE.get('lastDbAuditAt'),
            'lastDbQuickCheck': _STATE.get('lastDbQuickCheck'),
            'lastDbAuditError': _STATE.get('lastDbAuditError'),
        }
    quick = audit.get('lastDbQuickCheck')
    return {
        'ok': p.exists() and quick not in ('corrupt', 'failed'),
        'dbBytes': p.stat().st_size if p.exists() else 0,
        'walBytes': wal.stat().st_size if wal.exists() else 0,
        'shmBytes': shm.stat().st_size if shm.exists() else 0,
        'journalModeExpected': 'wal',
        'dailyQuickCheck': audit,
        'backup': b,
        **_backup_freshness(b),
        'orderAccess': False,
    }


def _score_component(value, weight, bucket):
    if value is None:
        return 0.0, 0.0
    bucket.append({'valuePct': round(float(value), 2), 'weight': weight})
    return max(0.0, min(100.0, float(value))) * weight, weight


def _snapshot_score_value(x):
    return (
        x.get('forwardAverageCoveragePct')
        if x.get('forwardAverageCoveragePct') is not None
        else x.get('averageCoveragePct')
    )


def report(force=False):
    started = time.monotonic()
    with _LOCK:
        if (
            not force and _CACHE.get('value') is not None
            and started - float(_CACHE.get('at') or 0) < CACHE_SEC
        ):
            return dict(_CACHE['value'])
    try:
        q5 = five_minute_audit(OFFICIAL_5M_AUDIT_DAYS)
    except Exception as exc:
        q5 = {'ok': False, 'officialGoodPct': None, 'error': f'{type(exc).__name__}: {exc}'}
    try:
        q1 = one_minute_coverage()
    except Exception as exc:
        q1 = {'bars': 0, 'completeBars': 0, 'dataReady': False, 'error': f'{type(exc).__name__}: {exc}'}
    scanner = _snapshot_coverage('scanner_intel_snapshots')
    decision = _snapshot_coverage('decision_intel_snapshots')
    outcomes = _outcome_label_quality()
    exit_ready = _strict_exit_readiness()
    db = _db_file_status()
    one_pct = _pct(int(q1.get('completeBars') or 0), int(q1.get('bars') or 0))

    components = []
    total = weights = 0.0
    for value, weight in (
        (q5.get('officialGoodPct'), 0.25),
        (one_pct, 0.25),
        (_snapshot_score_value(scanner), 0.20),
        (_snapshot_score_value(decision), 0.15),
        (outcomes.get('officialLabelPct'), 0.10),
        (100.0 if db.get('ok') else 0.0, 0.05),
    ):
        s, w = _score_component(value, weight, components)
        total += s
        weights += w
    score = round(total / weights, 1) if weights else 0.0
    grade = (
        'HEALTHY' if score >= 95 else 'GOOD' if score >= 90 else
        'WATCH' if score >= 80 else 'NEEDS_ATTENTION'
    )
    scanner_latest = scanner.get('latestTradingDay') or {}
    decision_latest = decision.get('latestTradingDay') or {}
    foundation_ready = bool(
        q1.get('dataReady')
        and float(q5.get('officialGoodPct') or 0) >= OFFICIAL_5M_TARGET_PCT
        and float(scanner_latest.get('coveragePct') or 0) >= SNAPSHOT_MIN_COVERAGE_PCT
        and float(decision_latest.get('coveragePct') or 0) >= SNAPSHOT_MIN_COVERAGE_PCT
        and db.get('ok')
    )
    with _LOCK:
        monitor = dict(_STATE)
    q5 = {
        **q5,
        'autoRepairTargetPct': OFFICIAL_5M_TARGET_PCT,
        'autoRepairActive': True,
        'lastAutoRepair': monitor.get('last5mRepair'),
        'liveRepairRestCalls': False,
    }
    out = {
        'ok': True,
        'version': VERSION,
        'generatedAt': datetime.now(KST).isoformat(timespec='seconds'),
        'score': score,
        'grade': grade,
        'scoreMeaning': 'Operational/research data health only; not profitability or live-readiness certification.',
        'fiveMinuteOfficial': q5,
        'oneMinute': {**q1, 'completePct': one_pct},
        'scannerSnapshotCoverage': scanner,
        'decisionSnapshotCoverage': decision,
        'outcomeLabels': outcomes,
        'exitReplay': exit_ready,
        'database': db,
        'monitor': monitor,
        'readiness': {
            'dataFoundationReady': foundation_ready,
            'exitValidationReady': bool(exit_ready.get('ready')),
            'controlPromotionDecision': 'SEPARATE_VALIDATION_REQUIRED',
            'strategyValidatedByThisModule': False,
        },
        'safety': {
            'researchOnly': True,
            'control': 'v0.8.0 LOCKED',
            'controlMutation': False,
            'entryExitMutation': False,
            'realOrder': False,
            'liveRepairRestCalls': False,
        },
    }
    out['computeMs'] = round((time.monotonic() - started) * 1000, 1)
    with _LOCK:
        _CACHE['at'] = started
        _CACHE['value'] = out
    return dict(out)


def _attempt_allowed(kind, code, day, retry_hours):
    _ensure_meta_tables()
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT attempted_at FROM data_health_repair_attempts "
                "WHERE kind=? AND code=? AND session_date=?",
                (kind, str(code), str(day)),
            ).fetchone()
        if not row:
            return True
        at = datetime.fromisoformat(str(row[0]))
        if at.tzinfo is None:
            at = at.replace(tzinfo=KST)
        return (
            datetime.now(KST) - at.astimezone(KST)
        ).total_seconds() >= retry_hours * 3600
    except Exception:
        return True


def _record_attempt(kind, code, day, status, rows=0, detail=None):
    _ensure_meta_tables()
    with _conn() as c:
        c.execute(
            "INSERT INTO data_health_repair_attempts("
            "kind,code,session_date,status,rows,detail,attempted_at) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(kind,code,session_date) DO UPDATE SET "
            "status=excluded.status,rows=excluded.rows,detail=excluded.detail,"
            "attempted_at=excluded.attempted_at",
            (
                kind, str(code), str(day), str(status), int(rows or 0),
                None if detail is None else str(detail)[:600],
                datetime.now(KST).isoformat(timespec='seconds'),
            ),
        )


def _1m_backfill_busy():
    return str((one_minute_status() or {}).get('phase') or '') == 'historical-1m-backfill'


def _repair_window_reason():
    if regular_session():
        return 'live-session-no-extra-rest'
    if (five_minute_history_status() or {}).get('running'):
        return '5m-history-busy'
    if _1m_backfill_busy():
        return '1m-backfill-busy'
    return None


def _official_5m_targets(limit):
    details = five_minute_details(OFFICIAL_5M_AUDIT_DAYS)
    candidates = [
        x for x in details
        if x.get('researchGrade') != 'GOOD'
        and int(x.get('bars') or 0) > 0
        and _attempt_allowed('5m-official', x.get('code'), x.get('date'), OFFICIAL_5M_RETRY_HOURS)
    ]
    groups = []
    for grade in ('GOOD', 'PARTIAL', 'BAD'):
        groups.extend(sorted(
            (x for x in candidates if x.get('grade') == grade),
            key=lambda x: x.get('date') or '',
            reverse=True,
        ))
    return groups[:max(1, int(limit))], len(candidates)


def official_5m_repair_once(limit=OFFICIAL_5M_REPAIR_LIMIT):
    before = five_minute_audit(OFFICIAL_5M_AUDIT_DAYS)
    before_pct = float(before.get('officialGoodPct') or 0.0)
    if before_pct >= OFFICIAL_5M_TARGET_PCT:
        result = {
            'ok': True, 'skipped': True, 'reason': 'target-reached',
            'targetPct': OFFICIAL_5M_TARGET_PCT, 'beforePct': before_pct,
            'afterPct': before_pct, 'repaired': [], 'liveRestCalls': False,
        }
        with _LOCK:
            _STATE['last5mRepair'] = result
        return result
    busy = _repair_window_reason()
    if busy:
        result = {
            'ok': True, 'skipped': True, 'reason': busy,
            'targetPct': OFFICIAL_5M_TARGET_PCT, 'beforePct': before_pct,
            'afterPct': before_pct, 'repaired': [], 'liveRestCalls': False,
        }
        with _LOCK:
            _STATE['last5mRepair'] = result
        return result

    targets, queue = _official_5m_targets(limit)
    repaired = []
    for target in targets:
        if regular_session() or (five_minute_history_status() or {}).get('running') or _1m_backfill_busy():
            break
        code, day_s = str(target['code']), str(target['date'])
        try:
            r = five_minute_download_day(code, datetime.fromisoformat(day_s).date()) or {}
            official = int(r.get('official') or 0)
            status = 'GOOD' if official >= 76 else 'NO_PROGRESS'
            _record_attempt(
                '5m-official', code, day_s, status, official,
                f"grade={target.get('grade')} beforeOfficial={target.get('officialBars')}",
            )
            repaired.append({
                'code': code, 'date': day_s,
                'beforeGrade': target.get('grade'),
                'beforeOfficialBars': int(target.get('officialBars') or 0),
                'afterOfficialBars': official,
                'written': int(r.get('written') or 0),
                'status': status,
            })
        except Exception as exc:
            _record_attempt('5m-official', code, day_s, 'ERROR', 0, f'{type(exc).__name__}: {exc}')
            repaired.append({'code': code, 'date': day_s, 'error': f'{type(exc).__name__}: {exc}'})

    after_pct = float(five_minute_audit(OFFICIAL_5M_AUDIT_DAYS).get('officialGoodPct') or 0.0)
    result = {
        'ok': True, 'skipped': False, 'reason': 'repair-cycle',
        'targetPct': OFFICIAL_5M_TARGET_PCT,
        'beforePct': before_pct, 'afterPct': after_pct,
        'queueBefore': queue, 'attempted': len(repaired),
        'repaired': repaired,
        'priority': 'structural GOOD unverified -> PARTIAL -> BAD, recent first',
        'sharedRestThrottle': True, 'liveRestCalls': False, 'controlMutation': False,
    }
    with _LOCK:
        now = datetime.now(KST).isoformat(timespec='seconds')
        _STATE['last5mRepairAt'] = now
        _STATE['last5mRepair'] = result
        _STATE['lastRepairAt'] = now
        _STATE['lastRepair'] = {'kind': '5m-official', **result}
        _CACHE['at'] = 0.0
    return result


def _replay_missing_targets(limit):
    _ensure_meta_tables()
    try:
        with _conn() as c:
            if not _safe_table_exists(c, 'paper_trades') or not _safe_table_exists(c, 'bars_1m'):
                return []
            trades = c.execute(
                "SELECT id,code,entry_at FROM paper_trades "
                "WHERE status='CLOSED' AND entry_at IS NOT NULL "
                "ORDER BY id DESC LIMIT 500"
            ).fetchall()
        out, seen = [], set()
        for t in trades:
            try:
                dt = datetime.fromisoformat(str(t['entry_at']))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=KST)
                day_s = dt.astimezone(KST).date().isoformat()
            except Exception:
                continue
            key = (str(t['code']), day_s)
            if key in seen:
                continue
            seen.add(key)
            with _conn() as c:
                n = int(c.execute(
                    "SELECT COUNT(*) FROM bars_1m "
                    "WHERE code=? AND session_date=? AND complete=1",
                    key,
                ).fetchone()[0])
            if n >= 360 or not _attempt_allowed('1m-replay', key[0], key[1], REPLAY_RETRY_HOURS):
                continue
            out.append({'tradeId': int(t['id']), 'code': key[0], 'date': key[1], 'rows': n})
            if len(out) >= max(1, int(limit)):
                break
        return out
    except Exception:
        return []


def replay_coverage_repair_once(limit=REPLAY_REPAIR_LIMIT):
    if regular_session():
        return {'ok': True, 'skipped': True, 'reason': 'live-session-no-extra-rest', 'repaired': []}
    if _five_minute_history_busy():
        return {'ok': True, 'skipped': True, 'reason': '5m-history-busy', 'repaired': []}
    if _1m_backfill_busy():
        return {'ok': True, 'skipped': True, 'reason': '1m-backfill-busy', 'repaired': []}
    targets = _replay_missing_targets(limit)
    repaired = []
    for target in targets:
        if regular_session() or _five_minute_history_busy() or _1m_backfill_busy():
            break
        try:
            written = int(one_minute_download_day(
                target['code'], datetime.fromisoformat(target['date']).date()
            ) or 0)
            with _conn() as c:
                after_rows = int(c.execute(
                    "SELECT COUNT(*) FROM bars_1m "
                    "WHERE code=? AND session_date=? AND complete=1",
                    (target['code'], target['date']),
                ).fetchone()[0])
            status = 'COMPLETE' if after_rows >= 360 else 'PARTIAL'
            _record_attempt('1m-replay', target['code'], target['date'], status, after_rows, f'written={written}')
            repaired.append({**target, 'written': written, 'afterRows': after_rows, 'status': status})
        except Exception as exc:
            _record_attempt(
                '1m-replay', target['code'], target['date'], 'ERROR', 0,
                f'{type(exc).__name__}: {exc}',
            )
            repaired.append({**target, 'error': f'{type(exc).__name__}: {exc}'})
    result = {
        'ok': True, 'skipped': False, 'targets': len(targets), 'repaired': repaired,
        'priority': 'closed Paper trade days missing full official 1m coverage',
        'liveRestCalls': False,
    }
    with _LOCK:
        now = datetime.now(KST).isoformat(timespec='seconds')
        _STATE['lastReplayRepairAt'] = now
        _STATE['lastReplayRepair'] = result
        if repaired:
            _STATE['lastRepairAt'] = now
            _STATE['lastRepair'] = {'kind': '1m-replay', **result}
        _CACHE['at'] = 0.0
    return result


def _partial_targets(limit):
    codes = [str(x) for x in (_history_codes() or [])]
    if not codes:
        return []
    placeholders = ','.join('?' for _ in codes)
    try:
        with _conn() as c:
            if not _safe_table_exists(c, 'bars_1m'):
                return []
            rows = c.execute(
                f"SELECT code,session_date,COUNT(*) n FROM bars_1m "
                f"WHERE complete=1 AND code IN ({placeholders}) "
                "GROUP BY code,session_date HAVING n>0 AND n<360 "
                "ORDER BY session_date DESC,n DESC LIMIT ?",
                (*codes, max(1, int(limit))),
            ).fetchall()
        return [{'code': str(r['code']), 'date': str(r['session_date']), 'rows': int(r['n'])} for r in rows]
    except Exception:
        return []


def priority_repair_once(limit=REPAIR_LIMIT_PER_CYCLE):
    if regular_session():
        return {'ok': True, 'skipped': True, 'reason': 'live-session-no-extra-rest', 'repaired': []}
    if _five_minute_history_busy():
        return {'ok': True, 'skipped': True, 'reason': '5m-history-busy', 'repaired': []}
    if _1m_backfill_busy():
        return {'ok': True, 'skipped': True, 'reason': '1m-backfill-busy', 'repaired': []}
    targets = _partial_targets(limit)
    repaired = []
    for target in targets:
        if regular_session() or _five_minute_history_busy() or _1m_backfill_busy():
            break
        try:
            written = int(one_minute_download_day(
                target['code'], datetime.fromisoformat(target['date']).date()
            ) or 0)
            repaired.append({**target, 'written': written})
        except Exception as exc:
            repaired.append({**target, 'error': f'{type(exc).__name__}: {exc}'})
    result = {
        'ok': True, 'skipped': False, 'targets': len(targets), 'repaired': repaired,
        'priority': 'recent PARTIAL code-days first', 'liveRestCalls': False,
    }
    with _LOCK:
        now = datetime.now(KST).isoformat(timespec='seconds')
        _STATE['last1mRepairAt'] = now
        _STATE['last1mRepair'] = result
        if repaired:
            _STATE['lastRepairAt'] = now
            _STATE['lastRepair'] = {'kind': '1m-partial', **result}
        _CACHE['at'] = 0.0
    return result


def _daily_db_quick_check():
    now = datetime.now(KST)
    if now.weekday() >= 5 or now.hour * 60 + now.minute < DB_AUDIT_AFTER_MINUTE or regular_session(now):
        return None
    day = now.date().isoformat()
    with _LOCK:
        if _STATE.get('lastDbAuditDate') == day:
            return _STATE.get('lastDbQuickCheck')
    try:
        with _conn() as c:
            row = c.execute('PRAGMA quick_check(1)').fetchone()
        result = str(row[0] if row else 'missing-result')
        error = None if result == 'ok' else f'quick_check={result}'
    except Exception as exc:
        result = 'failed'
        error = f'{type(exc).__name__}: {exc}'
    with _LOCK:
        _STATE['lastDbAuditDate'] = day
        _STATE['lastDbAuditAt'] = now.isoformat(timespec='seconds')
        _STATE['lastDbQuickCheck'] = result
        _STATE['lastDbAuditError'] = error
        _CACHE['at'] = 0.0
    return result


def _loop():
    with _LOCK:
        _STATE['running'] = True
    try:
        _ensure_meta_tables()
        _forward_baseline_date()
        _STOP.wait(120)
        while not _STOP.is_set():
            try:
                with _LOCK:
                    _STATE['lastCycleAt'] = datetime.now(KST).isoformat(timespec='seconds')
                    _STATE['lastError'] = None
                _daily_db_quick_check()
                r5 = official_5m_repair_once()
                if r5.get('reason') == 'target-reached':
                    rr = replay_coverage_repair_once()
                    if not rr.get('repaired'):
                        priority_repair_once()
            except Exception as exc:
                with _LOCK:
                    _STATE['lastError'] = f'{type(exc).__name__}: {exc}'[:600]
            _STOP.wait(MONITOR_INTERVAL_SEC)
    finally:
        with _LOCK:
            _STATE['running'] = False


def start():
    global _THREAD
    _ensure_meta_tables()
    _forward_baseline_date()
    with _LOCK:
        if _THREAD and _THREAD.is_alive():
            return dict(_STATE)
        _STOP.clear()
        _THREAD = threading.Thread(target=_loop, daemon=True, name='research-data-health')
        _THREAD.start()
        return dict(_STATE)


def stop():
    _STOP.set()
    return {'ok': True, 'status': dict(_STATE)}
