from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding='utf-8')


class VersionDisplayReleaseTests(unittest.TestCase):
    def test_classic_shell_bootstrap_is_release_versioned(self):
        index = text('index.html')
        self.assertIn('Stock Day Trader v0.17.14', index)
        self.assertIn('v0.17.14 RELEASE', index)
        self.assertIn('/js/app-safe.js?v=1788799200', index)
        self.assertIn('/styles.css?v=1788799200', index)
        self.assertIn('UI v0.17.10', index)
        self.assertIn('Reliability v0.17.13', index)
        self.assertIn('Control v0.8.0 LOCKED', index)
        self.assertNotIn('/js/app-safe.js?v=1788796000', index)

    def test_classic_loads_version_and_data_health_modules(self):
        boot = text('js/app-safe.js')
        self.assertIn("const ASSET_VERSION='1788799200'", boot)
        self.assertIn("version-display.js?v=${ASSET_VERSION}", boot)
        self.assertIn("data-health-ui.js?v=${ASSET_VERSION}", boot)
        self.assertIn("live-app.js?v=${ASSET_VERSION}", boot)

    def test_display_separates_release_ui_reliability_and_control(self):
        src = text('js/version-display.js')
        self.assertIn("FALLBACK_RELEASE_VERSION='0.17.14'", src)
        self.assertIn("FALLBACK_UI_VERSION='0.17.10'", src)
        self.assertIn("CONTROL_VERSION='0.8.0'", src)
        self.assertIn('status?.releaseVersion', src)
        self.assertIn("'/api/system/liveness'", src)
        self.assertIn('RELEASE</span>', src)
        self.assertIn('UI v${v.uiVersion}', src)
        self.assertIn('Reliability ', src)
        self.assertIn('Control v${CONTROL_VERSION} LOCKED', src)
        self.assertIn('REAL ORDER OFF', src)

    def test_display_modules_are_read_only(self):
        for path in ('js/version-display.js', 'js/data-health-ui.js'):
            src = text(path)
            self.assertNotIn('/api/nh/order', src)
            self.assertNotIn("method:'POST'", src)
            self.assertNotIn('ENABLE_TRADING', src)

    def test_service_worker_is_versioned_and_navigation_network_first(self):
        sw = text('sw.js')
        self.assertIn("const ASSET_VERSION='1788799200'", sw)
        self.assertIn('version-display.js?v=${ASSET_VERSION}', sw)
        self.assertIn('data-health-ui.js?v=${ASSET_VERSION}', sw)
        self.assertIn('stock-day-trader-live-v${ASSET_VERSION}-data-health', sw)
        self.assertIn("if(event.request.mode==='navigate')", sw)
        self.assertIn('networkRefresh(event.request).catch', sw)

    def test_control_v080_real_order_lock_unchanged(self):
        app = text('server/app.py').replace(' ', '')
        paper = text('server/paper_engine.py')
        self.assertIn("VERSION='0.8.0'", app)
        self.assertIn('ENABLE_TRADING=False', app)
        self.assertIn('MAX_CONSECUTIVE_LOSSES=2', paper)
        self.assertIn('MAX_OPEN_POSITIONS=2', paper)
        self.assertIn('DAILY_MAX_LOSS_PCT=.0075', paper)


if __name__ == '__main__':
    unittest.main()