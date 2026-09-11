from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT / path).read_text(encoding='utf-8')


class NativeOpsUsVisibilityTests(unittest.TestCase):
    def test_native_visibility_is_read_only(self):
        src = text('js/native-ops-us-visibility.js')
        for marker in (
            '/api/mobile/status',
            '/api/us/status',
            '/api/us/research/status',
            '자동매매 제어 · SERVER PAPER',
            'US 매매 상태: PAPER OFF · REAL ORDER OFF',
        ):
            self.assertIn(marker, src)
        for forbidden in (
            '/api/nh/order',
            '/api/paper/enter',
            '/api/paper/mark',
            "method:'POST'",
            'method: "POST"',
        ):
            self.assertNotIn(forbidden, src)

    def test_bootstrap_loads_visibility_module_for_native_client(self):
        src = text('js/app-safe.js')
        self.assertIn("native-ops-us-visibility.js?v=${ASSET_VERSION}", src)
        self.assertIn("new URLSearchParams(location.search).get('native')==='1'", src)

    def test_control_constants_are_unchanged(self):
        paper = text('server/paper_engine.py').replace(' ', '')
        app = text('server/app.py').replace(' ', '')
        for marker in (
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
            self.assertIn(marker, paper)
        self.assertIn("VERSION='0.8.0'", app)
        self.assertIn('ENABLE_TRADING=False', app)

    def test_us_trading_remains_disabled(self):
        src = text('server/us_collector.py').replace(' ', '')
        self.assertIn('US_PAPER_ENABLED=False', src)
        self.assertIn('US_REAL_ORDER_ENABLED=False', src)
        research = text('server/us_research.py')
        self.assertIn("'paperEnabled': False", research)
        self.assertIn("'realOrderEnabled': False", research)


if __name__ == '__main__':
    unittest.main()
