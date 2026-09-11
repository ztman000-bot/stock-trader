from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT/path).read_text(encoding='utf-8')


class NativeInformationArchitectureV2Tests(unittest.TestCase):
    def test_home_is_summary_only(self):
        js=text('js/native-compact-ui.js')
        for marker in (
            'body[data-mobile-tab="home"] #nativeShadowPanel',
            'body[data-mobile-tab="home"] #dataHealthPanel',
            'body[data-mobile-tab="home"] #nativeShadowDeep',
            'body[data-mobile-tab="home"] #dataHealthGrid',
            'body[data-mobile-tab="home"] #dataHealthNote',
            '연구 보기',
        ):
            self.assertIn(marker,js)

    def test_research_tab_owns_detailed_research_panels(self):
        js=text('js/native-compact-ui.js')
        for marker in (
            'body[data-mobile-tab="learn"] #nativeShadowDeep',
            'body[data-mobile-tab="learn"] #nativeShadowGrid',
            'body[data-mobile-tab="learn"] #nativeShadowList',
            'body[data-mobile-tab="learn"] #dataHealthGrid',
            'body[data-mobile-tab="learn"] #dataHealthNote',
            '연구·학습',
        ):
            self.assertIn(marker,js)

    def test_chart_tab_owns_positions_and_history(self):
        js=text('js/native-compact-ui.js')
        for marker in (
            'native-trade-grid',
            'native-paper-history',
            "positions.dataset.nativeRole='trade'",
            "risk.dataset.nativeRole='research'",
            'body[data-mobile-tab="chart"] .native-paper-history',
            'body[data-mobile-tab="learn"] .native-paper-history',
        ):
            self.assertIn(marker,js)

    def test_layout_script_has_no_write_requests(self):
        js=text('js/native-compact-ui.js').replace(' ','')
        self.assertNotIn("method:'POST'",js)
        self.assertNotIn('/api/nh/order',js)
        self.assertNotIn('/api/paper/enter',js)
        self.assertNotIn('/api/paper/mark',js)


if __name__=='__main__':
    unittest.main()
