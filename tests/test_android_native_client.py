from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT / path).read_text(encoding='utf-8')


class AndroidNativeClientTests(unittest.TestCase):
    def test_android_client_is_display_only_and_local_configured(self):
        src = text('android-client/app/src/main/java/com/ztman/stocktraderremote/MainActivity.java')
        self.assertIn('setJavaScriptEnabled(true)', src)
        self.assertIn('setAllowFileAccess(false)', src)
        self.assertIn('setAllowContentAccess(false)', src)
        self.assertIn('WebView.setWebContentsDebuggingEnabled(false)', src)
        self.assertIn('설정한 Stock Trader 서버 외 이동을 차단했습니다.', src)
        self.assertIn('getSharedPreferences(PREFS, MODE_PRIVATE)', src)
        self.assertNotIn('/api/nh/order', src)
        self.assertNotIn('NHPLUG_APP_KEY', src)
        self.assertNotIn('NHPLUG_APP_SECRET', src)

    def test_manifest_has_only_network_permission_and_no_exported_service(self):
        manifest = text('android-client/app/src/main/AndroidManifest.xml')
        self.assertIn('android.permission.INTERNET', manifest)
        self.assertNotIn('android.permission.WRITE_EXTERNAL_STORAGE', manifest)
        self.assertNotIn('android.permission.READ_EXTERNAL_STORAGE', manifest)
        self.assertNotIn('<service', manifest)
        self.assertNotIn('<receiver', manifest)

    def test_control_remains_locked_and_real_orders_off(self):
        app = text('server/app.py').replace(' ', '')
        self.assertIn("VERSION='0.8.0'", app)
        self.assertIn('ENABLE_TRADING=False', app)
        self.assertIn('MAX_CONSECUTIVE_LOSSES=2', app)
        self.assertIn('MAX_OPEN_POSITIONS=2', app)
        self.assertIn('MAX_DAILY_TRADES=8', app)

    def test_ci_builds_installable_debug_apk(self):
        workflow = text('.github/workflows/android-client-build.yml')
        self.assertIn(':app:assembleDebug', workflow)
        self.assertIn('app-debug.apk', workflow)
        self.assertIn('actions/upload-artifact@v4', workflow)


if __name__ == '__main__':
    unittest.main()
