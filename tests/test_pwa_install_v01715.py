from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding='utf-8')


class PwaInstallTests(unittest.TestCase):
    def test_manifest_is_standalone_and_scoped_to_classic(self):
        manifest = json.loads(text('manifest.webmanifest'))
        self.assertEqual(manifest['id'], '/classic')
        self.assertTrue(manifest['start_url'].startswith('/classic'))
        self.assertEqual(manifest['scope'], '/')
        self.assertEqual(manifest['display'], 'standalone')
        self.assertFalse(manifest['prefer_related_applications'])
        sizes = {icon.get('sizes') for icon in manifest.get('icons', [])}
        self.assertIn('192x192', sizes)
        self.assertIn('512x512', sizes)
        for icon in manifest['icons']:
            self.assertIn('maskable', icon.get('purpose', ''))

    def test_install_ui_requires_secure_context_and_uses_browser_prompt(self):
        src = text('js/pwa-install.js')
        self.assertIn('beforeinstallprompt', src)
        self.assertIn('appinstalled', src)
        self.assertIn('window.isSecureContext', src)
        self.assertIn("'(display-mode: standalone)'", src)
        self.assertIn('HTTPS', src)
        self.assertIn('deferredPrompt', src)

    def test_install_ui_is_display_only(self):
        src = text('js/pwa-install.js')
        forbidden = (
            '/api/nh/order', 'paper_enter(', 'force_close_all(',
            'ENABLE_TRADING', "method:'POST'", 'fetch(',
        )
        for token in forbidden:
            self.assertNotIn(token, src)

    def test_service_worker_keeps_api_network_authoritative(self):
        src = text('sw.js')
        self.assertIn("if(url.pathname.startsWith('/api/'))", src)
        self.assertIn("fetch(event.request,{cache:'no-store'})", src)
        self.assertIn('pwa-install.js?v=${ASSET_VERSION}', src)

    def test_docs_warn_http_tailscale_ip_is_not_installable_pwa(self):
        docs = text('docs/PWA_INSTALL_ANDROID.md')
        self.assertIn('http://100.x.x.x:8000/classic', docs)
        self.assertIn('valid HTTPS origin', docs)
        self.assertIn('Do not expose the trading bridge to the public Internet', docs)


if __name__ == '__main__':
    unittest.main()
