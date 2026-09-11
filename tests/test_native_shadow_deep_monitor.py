from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT/path).read_text(encoding='utf-8')


class NativeShadowDeepMonitorTests(unittest.TestCase):
    def test_android_exposes_read_only_shadow_report(self):
        src=text('server/android_unified_app.py')
        self.assertIn('from shadow_continuation import report as shadow_continuation_report',src)
        self.assertIn("Route('/api/research/shadow-continuation', android_shadow_continuation)",src)
        self.assertIn('shadow_continuation_report(limit=limit)',src)
        self.assertNotIn("Route('/api/research/shadow-continuation', android_shadow_continuation, methods=['POST'])",src)

    def test_native_monitor_shows_cumulative_research_metrics(self):
        src=text('js/native-client-fixes.js')
        for marker in (
            '/api/research/shadow-continuation',
            'Shadow 누적 연구',
            '누적 Shadow 표본',
            'Daily Lock 이후',
            '손실 후 동일종목 재진입',
            '재진입 성과',
            '실제 Control 손실 이후',
            'readyForControlReview',
        ):
            self.assertIn(marker,src)
        self.assertIn('setInterval(refreshDeepShadow,30000)',src)

    def test_native_monitor_is_read_only_except_guarded_server_update(self):
        src=text('js/native-client-fixes.js')
        self.assertIn("'/api/system/update/run'",src)
        self.assertIn("method:'POST'",src.replace(' ',''))
        for forbidden in (
            '/api/nh/order','/api/paper/enter','/api/paper/mark',
            '/api/collector/start','/api/history/start','/api/history/stop',
        ):
            self.assertNotIn(forbidden,src)

    def test_control_invariants_remain_locked(self):
        p=text('server/paper_engine.py').replace(' ','')
        a=text('server/app.py').replace(' ','')
        for marker in (
            'RISK_PER_TRADE=.0035','STOP_PCT=.010','TRAIL_ACTIVATE_PCT=.015',
            'TRAIL_PCT=.008','BREAKEVEN_ACTIVATE_PCT=.008',
            'MAX_CONSECUTIVE_LOSSES=2','MAX_OPEN_POSITIONS=2','MAX_DAILY_TRADES=8',
            'DAILY_MAX_LOSS_PCT=.0075',
        ):
            self.assertIn(marker,p)
        self.assertIn("VERSION='0.8.0'",a)
        self.assertIn('ENABLE_TRADING=False',a)


if __name__=='__main__':
    unittest.main()
