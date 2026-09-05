import ast
import sqlite3
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'server'


def text(name):
    return (SERVER / name).read_text(encoding='utf-8')


def observer_init_script():
    tree = ast.parse(text('research_observer.py'))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == 'init_observer_db':
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr == 'executescript' and call.args
                        and isinstance(call.args[0], ast.Constant)
                        and isinstance(call.args[0].value, str)):
                    return call.args[0].value
    raise AssertionError('init_observer_db executescript not found')


class ResearchDataExpansionTests(unittest.TestCase):
    def test_changed_python_sources_parse(self):
        for name in ('research_observer.py', 'paper_engine.py', 'unified_app.py'):
            ast.parse(text(name), filename=name)

    def test_control_and_real_order_invariants_unchanged(self):
        app = text('app.py')
        paper = text('paper_engine.py')
        self.assertIn("VERSION='0.8.0'", app)
        self.assertIn('ENABLE_TRADING=False', app)
        self.assertIn('RISK_PER_TRADE=.0035', paper)
        self.assertIn('MAX_CONSECUTIVE_LOSSES=2', paper)
        self.assertIn('MAX_OPEN_POSITIONS=2', paper)
        self.assertIn('MAX_DAILY_TRADES=8', paper)
        self.assertIn('DAILY_MAX_LOSS_PCT=.0075', paper)
        self.assertIn("len(closed)>=MAX_DAILY_TRADES", paper)
        self.assertIn("entryLimitSemantics':'CLOSED_TRADES_CURRENT'", paper)

    def test_entry_count_is_observational_not_a_new_gate(self):
        paper = text('paper_engine.py')
        self.assertIn("'entry_sequence':'INTEGER'", paper)
        self.assertIn("'entriesToday':entries", paper)
        self.assertIn('entry_seq=', paper)
        self.assertNotIn("entries>=MAX_DAILY_TRADES", paper)
        self.assertNotIn("entry_seq>MAX_DAILY_TRADES", paper)

    def test_decision_observer_records_point_in_time_context(self):
        observer = text('research_observer.py')
        for marker in (
            'decision_observations', 'market_observation_snapshots',
            'research_universe_snapshots_v01710',
            'BUY_CANDIDATE', 'SETUP', 'WATCH', 'SHADOW_ONLY', 'BLOCKED',
            'ret_5m', 'ret_10m', 'ret_30m', 'ret_60m', 'ret_eod',
            "OFFICIAL_5M_SOURCE = 'nh_period_5m'",
        ):
            self.assertIn(marker, observer)
        self.assertIn('UNIQUE(code,signal_bucket,action)', observer)
        self.assertIn('capture_universe_snapshot()', observer)

    def test_legacy_universe_snapshot_schema_does_not_block_init(self):
        script = observer_init_script()
        with sqlite3.connect(':memory:') as c:
            # Mirrors the upgrade failure class: an older table uses the same
            # generic name but does not have the new selected_rank column.
            c.execute('''CREATE TABLE universe_snapshots(
                         snapshot_date TEXT NOT NULL,
                         code TEXT NOT NULL,
                         legacy_rank INTEGER,
                         captured_at TEXT NOT NULL,
                         PRIMARY KEY(snapshot_date,code))''')
            c.executescript(script)
            legacy_cols = {r[1] for r in c.execute('PRAGMA table_info(universe_snapshots)')}
            new_cols = {r[1] for r in c.execute('PRAGMA table_info(research_universe_snapshots_v01710)')}
        self.assertNotIn('selected_rank', legacy_cols)
        self.assertIn('selected_rank', new_cols)
        self.assertIn('snapshot_date', new_cols)
        self.assertIn('code', new_cols)

    def test_observer_has_no_broker_or_order_dependency(self):
        tree = ast.parse(text('research_observer.py'))
        imported = set()
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or '')
            elif isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name):
                    called.add(fn.id.lower())
                elif isinstance(fn, ast.Attribute):
                    called.add(fn.attr.lower())
        self.assertFalse(any('nhplug' in x or 'broker' in x for x in imported))
        self.assertFalse({'order', 'buy', 'sell', 'place_order', 'send_order'} & called)

    def test_ui_release_and_observation_endpoint(self):
        ui = text('unified_app.py')
        self.assertIn("UI_VERSION='0.17.10'", ui)
        self.assertIn("'version':'0.17.10'", ui)
        self.assertIn("Route('/api/research/observations',observation_state)", ui)
        self.assertIn('research_observer_start()', ui)
        self.assertIn("'pointInTimeDecisionLog':True", ui)
        self.assertIn("'forwardOutcomeLabels':True", ui)
        self.assertIn("'entrySequenceTelemetry':True", ui)

    def test_existing_1m_collector_is_already_selective(self):
        one_min = text('kr_1m_research.py')
        self.assertIn('KR_1M_LIVE_FOCUS', one_min)
        self.assertIn("collector, 'priority_codes'", one_min)
        self.assertIn('active_candidates(KR_1M_LIVE_FOCUS * 2)', one_min)
        self.assertIn('return ordered[:KR_1M_LIVE_FOCUS]', one_min)


if __name__ == '__main__':
    unittest.main()
