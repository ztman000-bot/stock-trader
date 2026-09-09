from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding='utf-8')


class FastResumeHotfixTests(unittest.TestCase):
    def test_full_dashboard_last_known_state_is_cached_read_only(self):
        src = text('js/classic-fast-start.js')
        self.assertIn("STATUS_KEY='stock-trader-classic-status-cache-v2'", src)
        self.assertIn('compactStatus', src)
        for field in (
            'collector:data.collector',
            'paperLoop:data.paperLoop',
            'daily:data.daily',
            'validation:data.validation',
            'positions:data.positions',
            'scanner:(data.scanner||[]).slice(0,10)',
            'recentTrades:(data.recentTrades||[]).slice(0,30)',
            'risk:data.risk',
        ):
            self.assertIn(field, src)
        self.assertIn("url.pathname==='/api/mobile/status'", src)
        self.assertIn('refreshStatusInBackground', src)
        self.assertIn('CACHED · SYNCING', src)
        self.assertIn('공기계 최신 상태 동기화 중', src)

    def test_cached_state_never_changes_server_control(self):
        src = text('js/classic-fast-start.js')
        self.assertNotIn('/api/nh/order', src)
        self.assertNotIn('ENABLE_TRADING=True', src.replace(' ', ''))
        self.assertNotIn('paper_enter(', src)
        self.assertNotIn('method:\'POST\'', src)
        self.assertIn('tradingEnabled:false', src)

    def test_data_health_uses_stale_while_refresh_display(self):
        src = text('js/data-health-ui.js')
        self.assertIn("DATA_HEALTH_CACHE_KEY='stock-trader-data-health-cache-v1'", src)
        self.assertIn('cacheRead()', src)
        self.assertIn('cacheWrite(h)', src)
        self.assertIn('restoreCachedHealth()', src)
        self.assertIn('CACHED ${score.toFixed(1)}', src)
        self.assertIn('setTimeout(refresh,restored?250:1000)', src)
        self.assertNotIn('/api/nh/order', src)

    def test_service_worker_does_not_cache_api_responses(self):
        src = text('sw.js')
        self.assertIn("if(url.pathname.startsWith('/api/'))", src)
        self.assertIn("event.respondWith(fetch(event.request,{cache:'no-store'}))", src)
        self.assertIn('instant-resume', src)

    def test_control_v080_is_still_locked(self):
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
