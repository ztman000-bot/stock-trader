from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT/path).read_text(encoding='utf-8')


class NativeCompactShadowUiTests(unittest.TestCase):
    def test_native_client_loads_compact_mode_and_injects_shadow_ui(self):
        src=text('android-client/app/src/main/java/com/ztman/stocktraderremote/MainActivity.java')
        self.assertIn('appendQueryParameter("native", "1")',src)
        self.assertIn('/js/native-compact-ui.js',src)
        self.assertIn('injectNativeUi(view)',src)
        self.assertIn('root.addView(bar, new LinearLayout.LayoutParams(-1, dp(46)))',src)

    def test_compact_ui_hides_apk_redundant_sections_but_has_detail_toggle(self):
        js=text('js/native-compact-ui.js')
        self.assertIn("qs.get('native')!=='1'",js)
        self.assertIn('connection-panel',js)
        self.assertIn('#dataHealthGrid',js)
        self.assertIn('.riskbox',js)
        self.assertIn('상세 보기',js)
        self.assertIn('stock-trader-native-detail',js)

    def test_shadow_panel_reuses_shared_status_and_avoids_duplicate_scan(self):
        js=text('js/native-compact-ui.js')
        self.assertIn('/api/mobile/status',js)
        self.assertNotIn('/api/paper/scan',js)
        self.assertIn('stocktrader:status-data',js)
        self.assertIn('window.stockClassicFastStart?.latest',js)
        self.assertIn('SHADOW_ONLY',js)
        self.assertIn('동일종목 재진입 연구',js)
        self.assertIn('Control v0.8.0',js)
        self.assertNotIn("method:'POST'",js.replace(' ',''))
        self.assertNotIn('/api/nh/order',js)
        self.assertNotIn('/api/paper/enter',js)
        self.assertNotIn('/api/paper/mark',js)

    def test_locked_control_constants_remain_unchanged(self):
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
