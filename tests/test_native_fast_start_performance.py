from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT / path).read_text(encoding='utf-8')


class NativeFastStartPerformanceTests(unittest.TestCase):
    def test_shared_mobile_status_stream_is_exposed(self):
        src = text('js/classic-fast-start.js')
        self.assertIn('let latestData=null', src)
        self.assertIn('stocktrader:status-data', src)
        self.assertIn('get latest(){return latestData}', src)
        self.assertIn('refreshStatusInBackground', src)

    def test_shadow_ui_does_not_trigger_second_scanner_pass(self):
        src = text('js/native-compact-ui.js')
        self.assertNotIn('/api/paper/scan', src)
        self.assertIn('window.stockClassicFastStart?.latest', src)
        self.assertIn('stocktrader:status-data', src)

    def test_native_boot_prioritizes_compact_first_paint(self):
        src = text('js/app-safe.js')
        self.assertIn('native-compact-ui.js?v=${ASSET_VERSION}', src)
        self.assertIn('afterFirstPaint', src)
        self.assertIn('if(!nativeClient)import(`./pwa-install.js', src)
        self.assertIn('data-health-ui.js?v=${ASSET_VERSION}', src)

    def test_hidden_research_panels_do_not_fetch_during_first_paint(self):
        final = text('js/final-results-ui.js')
        hist = text('js/history-ui.js')
        health = text('js/data-health-ui.js')
        self.assertIn('연구 탭을 열면 최신 결과를 불러옵니다.', final)
        self.assertNotIn('wrap.appendChild(s);load();setInterval(load,60000)', final)
        self.assertIn("document.body?.dataset.mobileTab==='learn'", final)
        self.assertIn('연구 탭을 열면 상태를 확인합니다.', hist)
        self.assertIn("const isLearn=()=>document.body?.dataset.mobileTab==='learn'", hist)
        self.assertIn('firstDelay', health)
        self.assertIn('Cached health is enough for the home summary', health)

    def test_control_and_order_safety_are_unchanged(self):
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
