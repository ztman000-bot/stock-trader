from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT/path).read_text(encoding='utf-8')


class NativeAssetCacheRefreshTests(unittest.TestCase):
    def test_service_worker_invalidates_old_native_ui_cache(self):
        sw=text('sw.js')
        self.assertIn('nativefix-1789140400',sw)
        self.assertIn("keys.filter(key=>key!==CACHE)",sw)

    def test_javascript_is_network_first_after_update(self):
        sw=text('sw.js')
        self.assertIn("url.pathname.endsWith('.js')",sw)
        self.assertIn("networkRefresh(event.request).catch",sw)
        self.assertIn("fetch(request,{cache:'no-store'})",sw)

    def test_api_remains_network_authoritative(self):
        sw=text('sw.js')
        self.assertIn("url.pathname.startsWith('/api/')",sw)
        self.assertIn("fetch(event.request,{cache:'no-store'})",sw)

    def test_native_fast_start_assets_are_cache_busted(self):
        app=text('js/app-safe.js')
        index=text('index.html')
        self.assertIn("const ASSET_VERSION='1789226000'",app)
        self.assertIn("native-compact-ui.js?v=${ASSET_VERSION}",app)
        self.assertIn("native-client-fixes.js?v=${ASSET_VERSION}",app)
        self.assertIn("native-update-button-hotfix.js?v=${ASSET_VERSION}",app)
        self.assertIn("native-ops-us-visibility.js?v=${ASSET_VERSION}",app)
        self.assertIn("new URLSearchParams(location.search).get('native')==='1'",app)
        self.assertIn('/js/app-safe.js?v=1789226000',index)
        self.assertIn('/js/history-ui.js?v=1789226000',index)
        self.assertIn('/js/final-results-ui.js?v=1789226000',index)


if __name__=='__main__':
    unittest.main()
