from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT / path).read_text(encoding='utf-8')


class MobileStatusCacheTests(unittest.TestCase):
    def test_cache_module_is_syntax_valid_and_read_only(self):
        src = text('server/mobile_status_cache.py')
        ast.parse(src)
        self.assertIn("path == '/api/mobile/status'", src)
        self.assertIn("bridge._mobile_payload()", src)
        self.assertIn("TEMP_PHONE_SERVER", src)
        self.assertIn("controlMutation': False", src)
        self.assertIn("realOrderEnabled': False", src)
        self.assertNotIn('/api/nh/order', src)
        self.assertNotIn('paper_enter(', src)
        self.assertNotIn('mark_positions(', src)

    def test_android_app_installs_cache_without_replacing_safety_guard(self):
        src = text('server/android_unified_app.py')
        ast.parse(src)
        self.assertIn('from mobile_status_cache import install as install_mobile_status_cache', src)
        self.assertIn('install_mobile_status_cache(app)', src)
        self.assertIn("@app.middleware('http')\nasync def android_mutation_guard", src)
        self.assertIn("Android API: Tailscale/localhost only", src)
        self.assertIn("'realOrderEnabled': False", src)

    def test_native_shell_marks_stale_snapshot_as_syncing(self):
        src = text('js/native-server-cache-state.js')
        index = text('index.html')
        self.assertIn('mobileCache', src)
        self.assertIn('SNAPSHOT · SYNCING', src)
        self.assertIn('매매 엔진은 계속 실행', src)
        self.assertIn('/js/native-server-cache-state.js?v=1789603200', index)

    def test_control_engine_constants_are_unchanged(self):
        app = text('server/app.py').replace(' ', '')
        paper = text('server/paper_engine.py').replace(' ', '')
        self.assertIn("VERSION='0.8.0'", app)
        self.assertIn('ENABLE_TRADING=False', app)
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


if __name__ == '__main__':
    unittest.main()
