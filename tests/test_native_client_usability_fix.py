from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT/path).read_text(encoding='utf-8')


class NativeClientUsabilityFixTests(unittest.TestCase):
    def test_app_safe_loads_native_fix_only_for_native_mode(self):
        src=text('js/app-safe.js')
        self.assertIn("new URLSearchParams(location.search).get('native')==='1'",src)
        self.assertIn("import('./native-client-fixes.js",src)

    def test_native_fix_leaves_scroll_clearance_for_fixed_bottom_nav(self):
        src=text('js/native-client-fixes.js')
        self.assertIn('padding-bottom:calc(126px + env(safe-area-inset-bottom))',src)
        self.assertIn('padding-bottom:calc(138px + env(safe-area-inset-bottom))',src)
        self.assertIn('-webkit-overflow-scrolling:touch',src)

    def test_native_update_button_uses_only_guarded_system_updater(self):
        src=text('js/native-client-fixes.js')
        self.assertIn('서버 업데이트',src)
        self.assertIn("'/api/system/update/run'",src)
        self.assertIn('/api/system/update/status',src)
        self.assertIn('/api/system/liveness',src)
        self.assertIn("method:'POST'",src.replace(' ',''))
        self.assertNotIn('/api/nh/order',src)
        self.assertNotIn('/api/paper/enter',src)
        self.assertNotIn('/api/paper/mark',src)
        self.assertNotIn('/api/collector/start',src)

    def test_control_invariants_remain_unchanged(self):
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
