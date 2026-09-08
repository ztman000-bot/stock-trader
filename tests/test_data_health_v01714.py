from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding='utf-8')


class DataHealthV01715Tests(unittest.TestCase):
    def test_data_health_thresholds_match_monitoring_policy(self):
        src = text('server/research_data_health.py')
        self.assertIn("VERSION = '0.17.15'", src)
        self.assertIn('SNAPSHOT_MIN_COVERAGE_PCT = 95.0', src)
        self.assertIn('OFFICIAL_5M_TARGET_PCT = 95.0', src)
        self.assertIn('EXIT_MIN_REPLAY_COVERAGE_PCT = 95.0', src)
        self.assertIn('EXIT_MIN_PATH_AGREEMENT_PCT = 85.0', src)
        self.assertIn('EXIT_MIN_REASON_MATCH_PCT = 90.0', src)
        self.assertIn("'INCOMPLETE_DAY'", src)
        self.assertIn("'futureDataBackfillAllowed': False", src)

    def test_official_5m_repair_is_after_market_only_and_conflict_aware(self):
        src = text('server/research_data_health.py')
        self.assertIn('def official_5m_repair_once', src)
        self.assertIn("'live-session-no-extra-rest'", src)
        self.assertIn("'5m-history-busy'", src)
        self.assertIn("'1m-backfill-busy'", src)
        self.assertIn('five_minute_download_day', src)
        self.assertIn("'sharedRestThrottle': True", src)
        self.assertIn("'liveRestCalls': False", src)
        self.assertIn('OFFICIAL_5M_RETRY_HOURS', src)
        self.assertIn('data_health_repair_attempts', src)

    def test_repair_priority_and_exit_replay_coverage_repair(self):
        src = text('server/research_data_health.py')
        self.assertIn("for grade in ('GOOD', 'PARTIAL', 'BAD')", src)
        self.assertIn('def replay_coverage_repair_once', src)
        self.assertIn("'1m-replay'", src)
        self.assertIn("status='CLOSED'", src)
        self.assertIn('n >= 360', src)
        self.assertIn('REPLAY_RETRY_HOURS', src)

    def test_forward_snapshot_cohort_does_not_rewrite_legacy_history(self):
        src = text('server/research_data_health.py')
        self.assertIn('forward_snapshot_baseline', src)
        self.assertIn("'forwardAverageCoveragePct'", src)
        self.assertIn("'forwardIncompleteDays'", src)
        self.assertIn("'futureDataBackfillAllowed': False", src)
        self.assertIn('def _snapshot_score_value', src)

    def test_db_monitor_is_observation_only_and_tracks_backup_freshness(self):
        src = text('server/research_data_health.py')
        self.assertIn('PRAGMA quick_check(1)', src)
        self.assertIn("'journalModeExpected': 'wal'", src)
        self.assertIn("'backupFresh'", src)
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

    def test_unified_app_exposes_v01715_data_health_safely(self):
        src = text('server/unified_app.py')
        compact = src.replace(' ', '')
        self.assertIn("RELEASE_VERSION='0.17.15'", src)
        self.assertIn("Route('/api/research/data-health',data_health_state)", src)
        self.assertIn('data_health_start()', src)
        self.assertIn("'official5mAutoRepair':True", compact)
        self.assertIn("'official5mAutoRepairTargetPct':95", compact)
        self.assertIn("'official5mRepairLiveCalls':False", compact)
        self.assertIn("'partial1mRepairLiveCalls':False", compact)

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
