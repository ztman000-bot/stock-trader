from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]


def text(path):
    return (ROOT/path).read_text(encoding='utf-8')


class NativeUpdateButtonWebViewHotfixTests(unittest.TestCase):
    def test_hotfix_avoids_javascript_confirm_dialog(self):
        src=text('js/native-update-button-hotfix.js')
        self.assertNotIn('confirm(',src)
        self.assertIn('한 번 더 누르기',src)
        self.assertIn("btn.dataset.confirmUntil",src)

    def test_hotfix_calls_only_guarded_server_update_mutation(self):
        src=text('js/native-update-button-hotfix.js')
        self.assertIn("'/api/system/update/run'",src)
        self.assertIn("method:'POST'",src.replace(' ',''))
        self.assertNotIn('/api/nh/order',src)
        self.assertNotIn('/api/paper/enter',src)
        self.assertNotIn('ENABLE_TRADING',src)

    def test_hotfix_gives_immediate_visible_feedback(self):
        src=text('js/native-update-button-hotfix.js')
        self.assertIn('업데이트 요청을 서버로 전송 중',src)
        self.assertIn('업데이트 요청 접수됨',src)
        self.assertIn('업데이트 완료 · 새 서버 재시작 확인',src)

    def test_control_remains_locked(self):
        app=text('server/app.py').replace(' ','')
        paper=text('server/paper_engine.py').replace(' ','')
        self.assertIn("VERSION='0.8.0'",app)
        self.assertIn('ENABLE_TRADING=False',app)
        for marker in ('STOP_PCT=.010','TRAIL_ACTIVATE_PCT=.015','TRAIL_PCT=.008','MAX_CONSECUTIVE_LOSSES=2'):
            self.assertIn(marker,paper)


if __name__=='__main__':
    unittest.main()
