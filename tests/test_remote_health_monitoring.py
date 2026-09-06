from __future__ import annotations

import ast
import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'server'


def text(name):
    return (SERVER / name).read_text(encoding='utf-8')


class RemoteHealthMonitoringTests(unittest.TestCase):
    def test_remote_health_sources_parse(self):
        ast.parse(text('remote_health_daemon.py'), filename='remote_health_daemon.py')
        self.assertIn('remote_health_daemon.py --daemon', text('ensure_remote_health.sh'))

    def test_remote_health_is_independent_from_uvicorn(self):
        start = text('start_android.sh')
        self.assertIn('ensure_remote_health.sh', start)
        self.assertIn('bash "$REMOTE_HEALTH_ENSURE"', start)
        self.assertIn('exec python -m uvicorn android_unified_app:app', start)

    def test_preflight_imports_remote_health(self):
        self.assertIn("'remote_health_daemon'", text('preflight.py'))

    def test_no_broker_order_or_nh_dependency(self):
        tree = ast.parse(text('remote_health_daemon.py'))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or '')
        self.assertFalse(any('broker' in x.lower() or 'nhplug' in x.lower() for x in imported))

    def test_public_payload_is_strict_allowlist(self):
        sys.path.insert(0, str(SERVER))
        try:
            rh = importlib.import_module('remote_health_daemon')
            payload = rh._safe_public_payload({
                'device': 'x', 'state': 'HEALTHY', 'server': True, 'paper': True,
                'realOrder': False, 'quotesFresh': True, 'marketSession': False,
                'version': '0.17.11', 'timestamp': 'now', 'detail': 'ok',
                'account': 'SECRET', 'positions': ['SECRET'], 'pnl': 123,
                'apiKey': 'SECRET', 'symbols': ['005930'],
            })
            self.assertNotIn('account', payload)
            self.assertNotIn('positions', payload)
            self.assertNotIn('pnl', payload)
            self.assertNotIn('apiKey', payload)
            self.assertNotIn('symbols', payload)
            self.assertFalse(payload['realOrder'])
        finally:
            if str(SERVER) in sys.path:
                sys.path.remove(str(SERVER))

    def test_default_heartbeat_respects_ntfy_anonymous_daily_limit(self):
        src = text('remote_health_daemon.py')
        self.assertIn("REMOTE_HEALTH_HEARTBEAT_SEC', '600'", src)
        self.assertIn('max(600,', src)
        self.assertIn("secrets.token_hex(24)", src)
        self.assertIn(".stock-trader-remote-health-topic", src)

    def test_control_invariants_unchanged(self):
        app = text('app.py')
        paper = text('paper_engine.py')
        self.assertIn("VERSION='0.8.0'", app)
        self.assertIn('ENABLE_TRADING=False', app)
        self.assertIn('RISK_PER_TRADE=.0035', paper)
        self.assertIn('MAX_CONSECUTIVE_LOSSES=2', paper)
        self.assertIn('MAX_OPEN_POSITIONS=2', paper)
        self.assertIn('MAX_DAILY_TRADES=8', paper)


if __name__ == '__main__':
    unittest.main()
