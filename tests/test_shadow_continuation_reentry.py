from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding='utf-8')


class ShadowContinuationResearchTests(unittest.TestCase):
    def test_shadow_module_compiles_and_is_research_only(self):
        src = text('server/shadow_continuation.py')
        ast.parse(src, filename='server/shadow_continuation.py')
        self.assertIn("VERSION = '0.17.15-shadow-reentry-1'", src)
        self.assertIn("'researchOnly': True", src)
        self.assertIn("'controlStrategy': 'v0.8.0 LOCKED'", src)
        self.assertIn("'controlChangeApplied': False", src)
        self.assertNotIn('/api/nh/order', src)
        self.assertNotIn('nh_call(', src)
        self.assertNotIn('INSERT INTO paper_trades', src)
        self.assertNotIn('UPDATE paper_trades', src)
        self.assertNotIn('DELETE FROM paper_trades', src)

    def test_same_stock_reentry_cohort_is_recorded_not_blocked_in_control(self):
        src = text('server/shadow_continuation.py')
        self.assertIn('reentry_after_shadow_loss', src)
        self.assertIn('reentry_after_shadow_win', src)
        self.assertIn('prior_control_loss', src)
        self.assertIn("'sameStockReentryAfterLoss'", src)
        self.assertIn("MIN_REENTRY_SAMPLE = 50", src)
        self.assertIn("'ruleUnderStudy': 'STOP/LOSS 후 동일 종목 당일 재진입 금지'", src)
        self.assertIn("action not in ('BUY_CANDIDATE', 'SHADOW_ONLY')", src)
        self.assertIn("WHERE code=? AND status='OPEN'", src)
        self.assertIn('UNIQUE(code,signal_bucket)', src)

    def test_continuation_collects_after_daily_locks_without_changing_control(self):
        src = text('server/shadow_continuation.py')
        for marker in (
            "'ignoresDailyLossLockForResearch': True",
            "'ignoresConsecutiveLossLockForResearch': True",
            "'ignoresMaxDailyTradesForResearch': True",
            "'ignoresGlobalMaxOpenForResearch': True",
            "'oneOpenPositionPerCode': True",
            "'dedupeOneEntryPerCodeSignalBucket': True",
        ):
            self.assertIn(marker, src)
        paper = text('server/paper_engine.py').replace(' ', '')
        self.assertIn('MAX_CONSECUTIVE_LOSSES=2', paper)
        self.assertIn('MAX_OPEN_POSITIONS=2', paper)
        self.assertIn('MAX_DAILY_TRADES=8', paper)
        self.assertIn('DAILY_MAX_LOSS_PCT=.0075', paper)

    def test_shadow_exit_thresholds_reuse_locked_control_constants(self):
        src = text('server/shadow_continuation.py')
        for marker in (
            'STOP_PCT,', 'TRAIL_ACTIVATE_PCT,', 'TRAIL_PCT,',
            'BREAKEVEN_ACTIVATE_PCT,', 'BREAKEVEN_BUFFER_PCT,',
            'COMMISSION_RATE,', 'SELL_TAX_RATE,', 'SLIPPAGE_RATE,',
        ):
            self.assertIn(marker, src)
        self.assertIn("'STOP_LOSS'", src)
        self.assertIn("'TRAILING_STOP'", src)
        self.assertIn("'COST_COVER_PROTECT'", src)
        self.assertIn("force_close_all('EOD_EXIT'", text('server/shadow_continuation_daemon.py'))

    def test_android_daemon_uses_local_scan_api_only(self):
        src = text('server/shadow_continuation_daemon.py')
        ast.parse(src, filename='server/shadow_continuation_daemon.py')
        self.assertIn("LOCAL_SCAN_URL = 'http://127.0.0.1:8000/api/paper/scan'", src)
        self.assertNotIn('/api/nh/order', src)
        self.assertNotIn('/api/nh/quote', src)
        self.assertNotIn('https://', src)
        self.assertIn('shadow_continuation_report.json', src)

    def test_android_launcher_is_version_aware_and_preflighted(self):
        start = text('server/start_android.sh')
        ensure = text('server/ensure_shadow_continuation.sh')
        preflight = text('server/preflight.py')
        workflow = text('.github/workflows/safety-invariants.yml')
        self.assertIn('ensure_shadow_continuation.sh', start)
        self.assertIn('SHADOW_CONTINUATION_ENABLED', start)
        self.assertIn('INSTANCE_VERSION="0.17.15-shadow-reentry-1"', ensure)
        self.assertIn('--instance-version "$INSTANCE_VERSION"', ensure)
        self.assertIn("'shadow_continuation'", preflight)
        self.assertIn('bash -n server/ensure_shadow_continuation.sh', workflow)

    def test_control_and_real_order_invariants_stay_locked(self):
        app = text('server/app.py').replace(' ', '')
        self.assertIn("VERSION='0.8.0'", app)
        self.assertIn('ENABLE_TRADING=False', app)
        self.assertIn("RISK_PER_TRADE=.0035", text('server/paper_engine.py').replace(' ', ''))


if __name__ == '__main__':
    unittest.main()
