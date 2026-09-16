"""Offline order journal and recovery exercises. No broker/network/Paper access.

This is a simulation component, not an NH execution adapter. Its own database is
identified before any schema is created, so an unrelated database is rejected.
"""
import argparse
import json
import sqlite3
import tempfile
from hashlib import sha256
from pathlib import Path

from execution_readiness import apply_transition, client_order_key, reconcile_orders

APPLICATION_ID = 0x53545349
MODEL_VERSION = 'offline-execution-simulation-1'
DB_PATH = Path(__file__).with_name('execution_simulation.db')
_CACHED_REPORT = None


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _validate(snapshot):
    code = str(snapshot['code'])
    if len(code) != 6 or not code.isdigit() or code == '068270':
        raise ValueError('invalid or protected simulation instrument')
    if snapshot['side'] not in ('BUY', 'SELL') or not snapshot['clientOrderKey']:
        raise ValueError('invalid simulation identity')
    qty, filled = snapshot['qty'], snapshot['filledQty']
    if type(qty) is not int or type(filled) is not int or not 0 <= filled <= qty or qty <= 0:
        raise ValueError('invalid simulation quantities')
    state = snapshot['status']
    if state == 'FILLED' and filled != qty:
        raise ValueError('FILLED requires the entire quantity')
    if state == 'PARTIALLY_FILLED' and not 0 < filled < qty:
        raise ValueError('invalid partial fill')
    if state in ('INITIALIZED', 'SUBMITTED', 'ACCEPTED', 'REJECTED') and filled:
        raise ValueError('pre-fill/rejected state cannot contain fills')


class SimulationJournal:
    def __init__(self, path=DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            app_id = conn.execute('PRAGMA application_id').fetchone()[0]
            existing = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if app_id != APPLICATION_ID and (app_id or existing):
                raise ValueError('refusing to use a non-simulation database')
            conn.execute(f'PRAGMA application_id={APPLICATION_ID}')
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS simulation_events(
                  seq INTEGER PRIMARY KEY AUTOINCREMENT,
                  event_id TEXT NOT NULL UNIQUE, client_key TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS simulation_key ON simulation_events(client_key,seq);
                CREATE TRIGGER IF NOT EXISTS simulation_append_only_update BEFORE UPDATE ON simulation_events
                  BEGIN SELECT RAISE(ABORT,'simulation events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS simulation_append_only_delete BEFORE DELETE ON simulation_events
                  BEGIN SELECT RAISE(ABORT,'simulation events are append-only'); END;
            ''')

    def snapshots(self):
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute('''SELECT payload FROM simulation_events WHERE seq IN
              (SELECT MAX(seq) FROM simulation_events GROUP BY client_key) ORDER BY client_key''').fetchall()
        return [json.loads(row[0]) for row in rows]

    def get(self, key):
        with sqlite3.connect(self.path) as conn:
            row = conn.execute('SELECT payload FROM simulation_events WHERE client_key=? ORDER BY seq DESC LIMIT 1', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def append(self, event_id, snapshot):
        _validate(snapshot)
        if not event_id:
            raise ValueError('event id required')
        encoded = _json(snapshot)
        key = snapshot['clientOrderKey']
        with sqlite3.connect(self.path, timeout=15) as conn:
            conn.execute('BEGIN IMMEDIATE')
            duplicate = conn.execute('SELECT payload FROM simulation_events WHERE event_id=?', (event_id,)).fetchone()
            if duplicate:
                if duplicate[0] != encoded:
                    raise ValueError('conflicting duplicate event')
                return False
            row = conn.execute('SELECT payload FROM simulation_events WHERE client_key=? ORDER BY seq DESC LIMIT 1', (key,)).fetchone()
            if row:
                previous = json.loads(row[0])
                if any(previous[k] != snapshot[k] for k in ('code', 'side', 'qty')):
                    raise ValueError('order identity is immutable')
                if snapshot['filledQty'] < previous['filledQty']:
                    raise ValueError('cumulative fill cannot decrease')
                if previous != snapshot:
                    apply_transition(previous['status'], snapshot['status'])
            elif snapshot['status'] != 'INITIALIZED':
                raise ValueError('persist an intent before any simulated submission')
            conn.execute('INSERT INTO simulation_events(event_id,client_key,payload) VALUES(?,?,?)', (event_id, key, encoded))
        return True

    def intent(self, *, code, side, qty, trading_date, signal_bucket, attempt=0):
        key = client_order_key(code=code, side=side, trading_date=trading_date,
                               signal_bucket=signal_bucket, attempt=attempt, strategy='offline-simulation')
        row = {'clientOrderKey': key, 'code': str(code), 'side': side,
               'qty': qty, 'filledQty': 0, 'status': 'INITIALIZED'}
        self.append('intent:' + key, row)
        return key

    def record(self, key, event_id, state, filled=0):
        previous = self.get(key)
        if previous is None:
            raise ValueError('unknown simulation order')
        return self.append(event_id, {**previous, 'status': state, 'filledQty': filled})

    def recover(self, external_snapshots):
        """Reconcile supplied fake-broker snapshots; never resubmit missing orders."""
        external_snapshots = list(external_snapshots)
        # Validate duplicate identities before applying any report.
        reconcile_orders(self.snapshots(), external_snapshots)
        rejected = []
        for row in external_snapshots:
            key = row['clientOrderKey']
            if self.get(key) is None:
                rejected.append({'clientOrderKey': key, 'reason': 'unknown-external-order'})
                continue
            try:
                self.append('snapshot:' + sha256(_json(row).encode()).hexdigest(), row)
            except (ValueError, KeyError) as exc:
                rejected.append({'clientOrderKey': key, 'reason': str(exc)})
        comparison = reconcile_orders(self.snapshots(), external_snapshots)
        return {**comparison, 'ok': bool(comparison['ok'] and not rejected),
                'blocked': bool(not comparison['ok'] or rejected), 'rejected': rejected,
                'resubmissions': 0, 'simulationOnly': True}


class FakeBroker:
    def __init__(self):
        self.orders = {}
        self.created = 0

    def submit(self, snapshot):
        key = snapshot['clientOrderKey']
        if key not in self.orders:
            self.orders[key] = {**snapshot, 'status': 'ACCEPTED'}
            self.created += 1
        return dict(self.orders[key])


def run_simulations(repetitions=20):
    if type(repetitions) is not int or not 1 <= repetitions <= 1000:
        raise ValueError('simulation repetitions must be between 1 and 1000')
    def require(condition):
        if not condition:
            raise AssertionError('offline recovery invariant failed')
    cases = ('ACK_LOST', 'PARTIAL_CANCEL', 'DUPLICATE_FILL', 'CANCEL_FILL_RACE',
             'TIMEOUT_UNKNOWN', 'RESTART', 'OUT_OF_ORDER')
    counts = {case: 0 for case in cases}
    failures = []
    with tempfile.TemporaryDirectory(prefix='stock-order-simulation-') as temp:
        for attempt in range(repetitions):
            for case in cases:
                try:
                    path = Path(temp) / f'{case}-{attempt}.db'
                    journal = SimulationJournal(path)
                    key = journal.intent(code='005930', side='BUY', qty=10, trading_date='2026-09-16',
                                         signal_bucket='2026-09-16T10:00:00+09:00', attempt=attempt)
                    journal.record(key, 'submit', 'SUBMITTED')
                    broker = FakeBroker()
                    broker.submit(journal.get(key))
                    broker.submit(journal.get(key))
                    require(broker.created == 1)
                    if case == 'TIMEOUT_UNKNOWN':
                        report = SimulationJournal(path).recover([])
                        require(report['blocked'] and report['resubmissions'] == 0)
                    elif case in ('ACK_LOST', 'RESTART'):
                        if case == 'RESTART':
                            broker.orders[key].update(status='PARTIALLY_FILLED', filledQty=4)
                        require(SimulationJournal(path).recover(broker.orders.values())['ok'])
                    else:
                        require(journal.recover(broker.orders.values())['ok'])
                        if case in ('PARTIAL_CANCEL', 'CANCEL_FILL_RACE', 'OUT_OF_ORDER'):
                            broker.orders[key].update(status='PARTIALLY_FILLED', filledQty=4)
                            require(journal.recover(broker.orders.values())['ok'])
                        if case == 'OUT_OF_ORDER':
                            try:
                                journal.record(key, 'stale-fill', 'PARTIALLY_FILLED', 2)
                            except ValueError:
                                pass
                            else:
                                raise AssertionError('stale fill accepted')
                            require(journal.get(key)['filledQty'] == 4)
                        else:
                            if case != 'DUPLICATE_FILL':
                                journal.record(key, 'cancel-request', 'CANCEL_PENDING', 4)
                            target = 'CANCELED' if case == 'PARTIAL_CANCEL' else 'FILLED'
                            filled = 4 if target == 'CANCELED' else 10
                            broker.orders[key].update(status=target, filledQty=filled)
                            require(journal.recover(broker.orders.values())['ok'])
                            require(SimulationJournal(path).recover(broker.orders.values())['ok'])
                            require(journal.get(key)['filledQty'] == filled)
                    counts[case] += 1
                except Exception as exc:
                    failures.append({'case': case, 'attempt': attempt, 'error': f'{type(exc).__name__}: {exc}'})
    return {'ok': not failures, 'model': MODEL_VERSION, 'trials': repetitions * len(cases),
            'passed': sum(counts.values()), 'cases': counts, 'failures': failures,
            'simulationOnly': True, 'realOrderEnabled': False, 'microLiveReady': False,
            'nhBrokerValidated': False, 'scope': 'synthetic order quantity/state recovery; no real fills or network'}


def cached_simulation_report():
    global _CACHED_REPORT
    if _CACHED_REPORT is None:
        _CACHED_REPORT = run_simulations()
    return _CACHED_REPORT


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Offline execution recovery only')
    parser.add_argument('command', choices=('selftest',))
    parser.parse_args()
    report = run_simulations()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['ok'] else 1)
