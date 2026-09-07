from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding='utf-8')


class DataHealthV01714Tests(unittest.TestCase):
    def test_data_health_thresholds_match_monitoring_policy(self):
        src = text('server/research_data_health.py')
        self.assertIn("VERSION = '0.17.14'", src)
        self.assertIn('SNAPSHOT_MIN_COVERAGE_PCT = 95.0', src)
        self.assertIn('EXIT_MIN_REPLAY_COVERAGE_PCT = 95.0', src)
        self.assertIn('EXIT_MIN_PATH_AGREEMENT_PCT = 85.0', src)
        self.assertIn('EXIT_MIN_REASON_MATCH_PCT = 90.0', src)
        self.assertIn("'INCOMPLETE_DAY'", src)
        self.assertIn("'futureDataBackfillAllowed': False", src)

    def test_partial_repair_is_after_market_only_and_prioritized(self):
        src = text('server/research_data_health.py')
        self.assertIn('def priority_repair_once', src)
        self.assertIn("'live-session-no-extra-rest'", src)
        self.assertIn("phase == 'historical-1m-backfill'", src)
        self.assertIn('HAVING n>0 AND n<360', src)
        self.assertIn('ORDER BY session_date DESC,n DESC', src)
        self.assertIn('_five_minute_history_busy()', src)

    def test_db_monitor_is_observation_only(self):
        src = text('server/research_data_health.py')
        self.assertIn('PRAGMA quick_check(1)', src)
        self.assertIn("'journalModeExpected': 'wal'", src)
        self.assertIn("'orderAccess': False", src)
        self.assertNotIn('VACUUM', src)
        self.assertNotIn('wal_checkpoint(TRUNCATE)', src)

    def test_data_health_never_mutates_control_or_orders(self):
        src = text('server/research_data_health.py')
        self.assertIn("'control': 'v0.8.0 LOCKED'", src)
        self.assertIn("'controlMutation': False", src)
        self.assertIn("'entryExitMutation': False", src)
        self.assertIn("'realOrder': False", src)
        self.assertNotIn('/api/nh/order', src)
        self.assertNotIn('ENABLE_TRADING=True', src.replace(' ', ''))

    def test_unified_app_exposes_read_only_data_health(self):
        src = text('server/unified_app.py')
        self.assertIn("RELEASE_VERSION='0.17.14'", src)
        self.assertIn("Route('/api/research/data-health',data_health_state)", src)
        self.assertIn('data_health_start()', src)
        self.assertIn("'partial1mRepairLiveCalls':False", src.replace(' ', ''))

    def test_control_v080_constants_remain_locked(self):
        app = text('server/app.py').replace(' ', '')
        paper = text('server/paper_engine.py')
        self.assertIn("VERSION='0.8.0'", app)
        self.assertIn('ENABLE_TRADING=False', app)
        for constant in (
            'RISK_PER_TRADE=.0035',
            'STOP_PCT=.010',
            'TRAIL_ACTIVATE_PCT=.015',
            'TRAIL_PCT=.008',
            'BREAKEVEN_ACTIVATE_PCT=.008',
            'MAX_CONSECUTIVE_LOSSES=2',
            'MAX_OPEN_POSITIONS=2',
            'MAX_DAILY_TRADES=8',
            'DAILY_MAX_LOSS_PCT=.0075',
        ):
            self.assertIn(constant, paper)


if __name__ == '__main__':
    unittest.main()