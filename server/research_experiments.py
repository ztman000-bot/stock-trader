"""Immutable, precommitted future research windows in a separate SQLite registry.

No strategy promotion. A final window is consumed once, even if evaluation crashes.
Historical rolling holdouts must never be advertised as an untouched final test.
"""
import argparse
import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DB_PATH = Path(__file__).with_name('research_experiments.db')
PROTOCOL_VERSION = 'future-holdout-1'
WINDOW_DAYS = 42
CODE_FILES = ('profitability_lab.py', 'research_fills.py', 'research_portfolio.py',
              'robust_validation.py', 'research_experiments.py', 'backtest_engine.py',
              'strategy_lab.py', 'market_lab.py', 'stocks_in_play.py', 'data_quality.py',
              'market_state_engine.py', 'collector.py', 'paper_engine.py')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def code_digest():
    root = Path(__file__).parent
    return digest({name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in CODE_FILES})


def snapshot_digest(candidates, through):
    """Hash candidate inputs and their completed historical bars, excluding later bars."""
    records, bars = [], {}
    for candidate in candidates:
        if str(candidate['date']) > through:
            continue
        records.append({k: v for k, v in candidate.items() if k != 'rows'})
        for row in candidate.get('rows', []):
            if str(row['bucket'])[:10] <= through:
                bars[(str(candidate['code']), str(row['bucket']))] = dict(row)
    records.sort(key=lambda r: (str(r['date']), str(r['code'])))
    return digest({'candidates': records, 'bars': [(key, bars[key]) for key in sorted(bars)]})


def _connect(path):
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS experiments(
          id TEXT PRIMARY KEY, manifest TEXT NOT NULL, status TEXT NOT NULL,
          final_data_hash TEXT, result TEXT, result_hash TEXT);
        CREATE TABLE IF NOT EXISTS experiment_events(
          seq INTEGER PRIMARY KEY, id TEXT NOT NULL, event TEXT NOT NULL,
          at TEXT NOT NULL, detail TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS immutable_manifest BEFORE UPDATE OF manifest ON experiments
          BEGIN SELECT RAISE(ABORT,'experiment manifest is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_result BEFORE UPDATE OF result ON experiments
          WHEN OLD.result IS NOT NULL BEGIN SELECT RAISE(ABORT,'final result is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS no_experiment_delete BEFORE DELETE ON experiments
          BEGIN SELECT RAISE(ABORT,'experiment history cannot be deleted'); END;
        CREATE TRIGGER IF NOT EXISTS no_event_update BEFORE UPDATE ON experiment_events
          BEGIN SELECT RAISE(ABORT,'experiment event is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS no_event_delete BEFORE DELETE ON experiment_events
          BEGIN SELECT RAISE(ABORT,'experiment event is append-only'); END;
    ''')
    return conn


def _event(conn, experiment_id, kind, detail):
    conn.execute('INSERT INTO experiment_events(id,event,at,detail) VALUES(?,?,?,?)',
                 (experiment_id, kind, datetime.now(ZoneInfo('Asia/Seoul')).isoformat(), canonical(detail)))


def _unpack(row):
    if row is None:
        return None
    result = dict(row)
    result['manifest'] = json.loads(result['manifest'])
    result['result'] = json.loads(result['result']) if result['result'] else None
    return result


def active(path=None):
    if not Path(path or DB_PATH).exists():
        return None
    with _connect(path) as conn:
        return _unpack(conn.execute("SELECT * FROM experiments WHERE status!='RETIRED' ORDER BY rowid DESC LIMIT 1").fetchone())


def development_candidates(candidates, path=None):
    record = active(path)
    if not record:
        return candidates
    cutoff = record['manifest']['developmentEnd']
    start = record['manifest']['developmentStart']
    return [x for x in candidates if start <= str(x['date']) <= cutoff]


def candidate_scope(path=None):
    record = active(path)
    if not record:
        return {}
    manifest = record['manifest']
    return {'codes': manifest['settings'].get('universeCodes'),
            'min_date': manifest['developmentStart'], 'quality_days': 1_000_000}


def freeze(candidates, selection, settings, *, path=None, today=None, source_hash=None):
    today = today or datetime.now(ZoneInfo('Asia/Seoul')).date()
    closed = [x for x in candidates if str(x['date']) < today.isoformat()]
    if not closed:
        raise ValueError('no completed development dates')
    development_end = max(str(x['date']) for x in closed)
    manifest = {'protocol': PROTOCOL_VERSION, 'createdDate': today.isoformat(),
                'developmentStart': min(str(x['date']) for x in closed),
                'developmentEnd': development_end, 'developmentDataHash': snapshot_digest(closed, development_end),
                'codeHash': source_hash or code_digest(), 'settingsHash': digest(settings),
                'settings': settings, 'selection': selection,
                'lockboxStart': (today + timedelta(days=1)).isoformat(),
                'lockboxEnd': (today + timedelta(days=WINDOW_DAYS)).isoformat(),
                'researchOnly': True, 'realOrderEnabled': False}
    with _connect(path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute("SELECT * FROM experiments WHERE status!='RETIRED' ORDER BY rowid DESC LIMIT 1").fetchone()
        if current:
            return _unpack(current)
        # Retirements remain visible; a subsequent attempt always uses future dates.
        parent = conn.execute('SELECT id FROM experiments ORDER BY rowid DESC LIMIT 1').fetchone()
        manifest['previousExperimentId'] = parent['id'] if parent else None
        experiment_id = 'exp-' + digest(manifest)[:24]
        conn.execute('INSERT INTO experiments(id,manifest,status) VALUES(?,?,?)',
                     (experiment_id, canonical(manifest), 'FROZEN'))
        _event(conn, experiment_id, 'FROZEN', manifest)
        return _unpack(conn.execute('SELECT * FROM experiments WHERE id=?', (experiment_id,)).fetchone())


def evaluate_once(record, candidates, settings, evaluator, *, path=None, today=None, source_hash=None):
    today = today or datetime.now(ZoneInfo('Asia/Seoul')).date()
    manifest = record['manifest']
    status = {'experimentId': record['id'], 'protocol': PROTOCOL_VERSION,
              'developmentEnd': manifest['developmentEnd'],
              'lockboxStart': manifest['lockboxStart'], 'lockboxEnd': manifest['lockboxEnd'],
              'status': record['status'], 'finalEvidence': False, 'researchOnly': True,
              'codeHash': manifest['codeHash'], 'settingsHash': manifest['settingsHash'],
              'developmentDataHash': manifest['developmentDataHash']}
    current_code = source_hash or code_digest()
    mismatches = []
    if current_code != manifest['codeHash']: mismatches.append('code')
    if digest(settings) != manifest['settingsHash']: mismatches.append('settings')
    if snapshot_digest(candidates, manifest['developmentEnd']) != manifest['developmentDataHash']:
        mismatches.append('development-data')
    if mismatches:
        return {**status, 'status': 'INTEGRITY_BLOCKED', 'mismatches': mismatches}
    if today.isoformat() <= manifest['lockboxEnd']:
        return {**status, 'status': 'WAITING_FOR_CLOSED_WINDOW'}
    final_hash = snapshot_digest(candidates, manifest['lockboxEnd'])
    if record['result'] is not None:
        if final_hash != record['final_data_hash'] or digest(record['result']) != record['result_hash']:
            return {**status, 'status': 'INTEGRITY_BLOCKED', 'mismatches': ['final-data-or-result']}
        return {**status, 'status': 'EVALUATED', 'finalEvidence': True, 'cached': True,
                'finalDataHash': final_hash, 'result': record['result']}
    with _connect(path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        changed = conn.execute("UPDATE experiments SET status='CONSUMED',final_data_hash=? WHERE id=? AND status='FROZEN'",
                               (final_hash, record['id'])).rowcount
        if not changed:
            return {**status, 'status': 'CONSUMED_OR_RUNNING', 'reason': 'No automatic final-test retry.'}
        _event(conn, record['id'], 'CONSUMED', {'finalDataHash': final_hash})
    # Claim is committed before invoking research code: a crash cannot silently retry.
    try:
        result = evaluator(manifest)
        encoded = canonical(result)
    except Exception as exc:
        with _connect(path) as conn:
            _event(conn, record['id'], 'EVALUATION_FAILED', {'errorType': type(exc).__name__})
        raise
    with _connect(path) as conn:
        conn.execute('UPDATE experiments SET status=?,result=?,result_hash=? WHERE id=? AND result IS NULL',
                     ('EVALUATED', encoded, digest(result), record['id']))
        _event(conn, record['id'], 'EVALUATED', {'resultHash': digest(result)})
    return {**status, 'status': 'EVALUATED', 'finalEvidence': True, 'cached': False,
            'finalDataHash': final_hash, 'result': result}


def retire(experiment_id, reason, path=None):
    if not reason.strip():
        raise ValueError('a reason is required; history is retained')
    with _connect(path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute("UPDATE experiments SET status='RETIRED' WHERE id=? AND status!='RETIRED'", (experiment_id,)).rowcount != 1:
            raise ValueError('active experiment not found')
        _event(conn, experiment_id, 'MANUAL_RETIREMENT', {'reason': reason})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Research experiment registry; no orders or promotion')
    parser.add_argument('command', choices=('status', 'retire'))
    parser.add_argument('--id')
    parser.add_argument('--reason', default='')
    args = parser.parse_args()
    if args.command == 'retire':
        retire(args.id, args.reason)
    print(json.dumps({'researchOnly': True, 'realOrderEnabled': False, 'active': active()}, ensure_ascii=False, indent=2))
