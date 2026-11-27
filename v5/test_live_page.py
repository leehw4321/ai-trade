import json
import tempfile
import unittest
from pathlib import Path

import live_page


class LivePage(unittest.TestCase):
    """2026-11-27: 자동 새로고침이 페이지 전체를 다시 그려 화면이 주기적으로 깜박였다."""

    def test_js_file_carries_page_for_callback(self):
        page = '<!doctype html><html><body><p>가격 1,234원 </script></p></body></html>'
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(live_page.write_js(page, d))
            s = (Path(d) / live_page.JS_NAME).read_text(encoding='utf-8')
            self.assertEqual(list(Path(d).glob('*.tmp')), [])
        prefix = 'window.__v5page&&window.__v5page('
        self.assertTrue(s.startswith(prefix))
        self.assertEqual(json.loads(s[len(prefix):s.rindex(')')]), page)

    def test_script_keeps_user_state_and_falls_back(self):
        s = live_page.SCRIPT
        for attr in ('open', 'hidden', 'disabled', 'value', 'checked'):
            self.assertIn(attr + ':1', s)          # 펼침·숨김·입력은 그대로
        self.assertIn('location.reload()', s)      # 실패·버튼 추가 때는 전체 새로고침
        self.assertIn("status_live.js?t=", s)
        self.assertIn("posmodal", s)               # 열린 종목 창은 건드리지 않음

    def test_bot_page_uses_soft_refresh_and_writes_js(self):
        src = (Path(__file__).with_name('bot.py')).read_text(encoding='utf-8')
        self.assertIn('window.__v5soft(arm)', src)
        self.assertIn('live_page.SCRIPT', src)
        self.assertIn('live_page.write_js(page, DATA)', src)

    def test_popups_reread_data_after_soft_refresh(self):
        here = Path(__file__).parent
        self.assertIn('function show(m){try{D=JSON.parse(el.textContent)}', (here / 'position_popup.py').read_text(encoding='utf-8'))
        self.assertIn('try{D=JSON.parse(raw.textContent)}catch(x){}', (here / 'protection_panel.py').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
