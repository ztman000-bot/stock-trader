from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'server'


def text(name):
    return (SERVER / name).read_text(encoding='utf-8')


class WatchdogStabilityV01713Tests(unittest.TestCase):
    def test_lightweight_liveness_route_is_registered_and_db_free(self):
        src = text('android_unified_app.py')
        self.assertIn("ANDROID_RELIABILITY_VERSION = '0.17.13'", src)
        self.assertIn("Route('/api/system/liveness', android_liveness)", src)

        tree = ast.parse(src)
        fn = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == 'android_liveness'
        )
        body = ast.get_source_segment(src, fn) or ''
        self.assertIn("'ok': True", body)
        self.assertIn("'tradingEnabled': False", body)
        for forbidden in ('db_backup', 'latest_quotes', 'collector', 'research_', 'sqlite'):
            self.assertNotIn(forbidden, body.lower())

    def test_watchdog_uses_atomic_singleton_and_diagnostic_reason_codes(self):
        src = text('android_watchdog_v2.sh')
        self.assertIn('WATCHDOG_COMPONENT_VERSION="0.17.13"', src)
        self.assertIn('LOCKDIR="$HOME/.stock-trader-watchdog-v2.lock"', src)
        self.assertIn('mkdir "$LOCKDIR"', src)
        self.assertIn('duplicate watchdog terminated', src)
        self.assertIn('/api/system/liveness', src)
        self.assertIn('LIVENESS_HTTP_TIMEOUT', src)
        self.assertIn('HEALTH_HTTP_TIMEOUT', src)
        self.assertIn('PAPER_STALE', src)
        self.assertIn('COLLECTOR_STALE', src)
        self.assertIn('RUNTIME_STALE', src)
        self.assertIn('restartSuppressed=true', src)
        self.assertIn('startup_grace', src)

    def test_expensive_health_timeout_does_not_trigger_immediate_restart(self):
        src = text('android_watchdog_v2.sh')
        self.assertRegex(
            src,
            r'HEALTH_HTTP_TIMEOUT\|HEALTH_ERROR_\*\|HEALTH_HTTP_\*\|RUNTIME_\*\)'
        )
        self.assertIn('SERVICE_RESTART_LIMIT="${WATCHDOG_SERVICE_FAILURES_BEFORE_RESTART:-20}"', src)
        self.assertIn('persistentServiceFailure=true', src)

    def test_independent_remote_guardian_can_restore_watchdog(self):
        guardian = text('remote_health_guardian.sh')
        start = text('start_android.sh')
        update = text('android_update.sh')
        android = text('android_unified_app.py')
        self.assertIn('GUARDIAN_COMPONENT_VERSION="0.17.13"', guardian)
        self.assertIn('WATCHDOG="$SERVER/android_watchdog_v2.sh"', guardian)
        self.assertIn('ensure_watchdog', guardian)
        self.assertIn('watchdog restored pid=', guardian)
        self.assertIn('watchdogSupervision=true', guardian)
        self.assertIn('stale update flag removed by guardian', guardian)
        self.assertIn('REMOTE_HEALTH_GUARDIAN_VERSION="0.17.13"', start)
        self.assertIn('--instance-version "$REMOTE_HEALTH_GUARDIAN_VERSION"', start)
        self.assertNotIn('echo $! > "$WDPIDFILE"', start)
        self.assertNotIn('WATCHDOG_PIDFILE.write_text(str(proc.pid)', android)
        self.assertNotIn('echo $! > "$WDPIDFILE"', update)
        self.assertIn('watchdog v2 refreshed PID=$current launcherPid=$launched', update)

    def test_control_v080_and_real_order_lock_are_unchanged(self):
        app = text('app.py')
        paper = text('paper_engine.py')
        self.assertIn("VERSION='0.8.0'", app)
        self.assertIn('ENABLE_TRADING=False', app)
        self.assertIn('MAX_CONSECUTIVE_LOSSES=2', paper)
        self.assertIn('MAX_OPEN_POSITIONS=2', paper)
        self.assertIn('DAILY_MAX_LOSS_PCT=.0075', paper)


if __name__ == '__main__':
    unittest.main()
