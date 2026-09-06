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


class SafetyHardeningV01712Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SERVER))

    @classmethod
    def tearDownClass(cls):
        if str(SERVER) in sys.path:
            sys.path.remove(str(SERVER))

    def test_exact_tailscale_cidr_policy(self):
        network = importlib.import_module('network_access')
        for host in ('127.0.0.1', '127.9.8.7', '::1', '100.64.0.1', '100.127.255.254', '::ffff:100.64.0.1'):
            self.assertTrue(network.is_trusted_client_host(host), host)
        for host in ('100.63.255.255', '100.128.0.1', '100.200.1.1', '192.168.0.10', '10.0.0.1', '8.8.8.8', 'localhost', ''):
            self.assertFalse(network.is_trusted_client_host(host), host)

    def test_android_guard_covers_get_and_write_api_calls(self):
        src = text('android_unified_app.py')
        self.assertIn("request.url.path.startswith('/api/')", src)
        self.assertIn('is_trusted_client_host(host)', src)
        self.assertNotIn("host.startswith('100.')", src)

    def test_remote_health_change_remains_pending_until_publish_allowed(self):
        rh = importlib.import_module('remote_health_daemon')
        # A change that occurs inside the anti-spam window must not be treated
        # as already published. When the window expires it is still a change.
        now_blocked = rh._publish_decision('SERVER_DOWN', 'HEALTHY', 30, 30)
        later_allowed = rh._publish_decision('SERVER_DOWN', 'HEALTHY', 61, 61)
        self.assertEqual(now_blocked, (False, True))
        self.assertEqual(later_allowed, (True, True))
        # A failed first/heartbeat publication also respects the retry window.
        self.assertEqual(rh._publish_decision('HEALTHY', None, 9999, 30), (False, False))
        self.assertEqual(rh._publish_decision('HEALTHY', None, 9999, 61), (True, False))
        src = text('remote_health_daemon.py')
        self.assertIn('last_published_state = state', src)
        self.assertIn("if result.get('ok')", src)
        self.assertIn("'pendingStateChange'", src)

    def test_remote_health_is_versioned_and_self_supervised(self):
        ensure = text('ensure_remote_health.sh')
        guardian = text('remote_health_guardian.sh')
        start = text('start_android.sh')
        self.assertIn('COMPONENT_VERSION="0.17.12"', ensure)
        self.assertIn('--instance-version "$COMPONENT_VERSION"', ensure)
        self.assertIn('pid_is_project_daemon', ensure)
        self.assertIn('remote_health_guardian.sh', start)
        self.assertIn('ensure_remote_health.sh', guardian)

    def test_offsite_backup_is_opt_in_encrypted_and_orderless(self):
        src = text('offsite_backup.py')
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or '')
        self.assertFalse(any('nhplug' in x.lower() or 'broker' in x.lower() for x in imported))
        self.assertIn("OFFSITE_BACKUP_ENABLED') or 'false'", src)
        self.assertIn("'-aes-256-cbc'", src)
        self.assertIn("'-pbkdf2'", src)
        self.assertIn("'-pass', 'env:STOCK_TRADER_OFFSITE_PASSPHRASE'", src)
        self.assertIn("scheme.lower() == 'https'", src)
        self.assertIn('http.client.HTTPSConnection', src)
        self.assertNotIn('cipher.read_bytes()', src)
        self.assertIn("out['secretsExposed'] = False", src)
        self.assertIn("out['orderAccess'] = False", src)

    def test_offsite_daemon_starts_only_when_explicitly_enabled(self):
        ensure = text('ensure_offsite_backup.sh')
        start = text('start_android.sh')
        self.assertRegex(ensure, r'OFFSITE_BACKUP_ENABLED.*true\|1\|yes\|on')
        self.assertIn('OFFSITE_BACKUP_ENSURE', start)
        self.assertIn('opt-in only (default OFF)', start)

    def test_control_v080_is_unchanged(self):
        app = text('app.py')
        paper = text('paper_engine.py')
        self.assertIn("VERSION='0.8.0'", app)
        self.assertIn('ENABLE_TRADING=False', app)
        self.assertIn('RISK_PER_TRADE=.0035', paper)
        self.assertIn('MAX_CONSECUTIVE_LOSSES=2', paper)
        self.assertIn('MAX_OPEN_POSITIONS=2', paper)
        self.assertIn('MAX_DAILY_TRADES=8', paper)
        self.assertIn('DAILY_MAX_LOSS_PCT=.0075', paper)


if __name__ == '__main__':
    unittest.main()
