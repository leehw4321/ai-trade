"""2026-11-16 사용자 지시 "이 창들은 접을수 있으면 좋겠어": 현황 상단 실험·관찰 상자 접기."""
import re
import unittest
from pathlib import Path

import bot

SRC = (Path(__file__).resolve().parent / 'bot.py').read_text(encoding='utf-8')


class Fold(unittest.TestCase):
    def test_wraps_box_with_key(self):
        html = bot.wfold('trend', "<div style='border:2px solid'>주요 코인 추세 모의</div>")
        self.assertTrue(html.startswith("<div class=wf data-k='trend'>"))
        self.assertIn('주요 코인 추세 모의', html)

    def test_empty_panel_stays_empty(self):
        self.assertEqual(bot.wfold('step', ''), '')

    def test_every_experiment_panel_is_foldable(self):
        # 스크린샷의 상자: 추세 모의, 매수 휴식 관찰, 급락 중 매수 보류, 손실 유예, 2+5(계단), 정체 탈출·매수 필터 기록 줄
        for key, module in (('trend', 'trend_shadow'), ('pause', 'pause_watch'), ('drop', 'drop_watch'),
                            ('defer', 'rank_defer'), ('step', 'step_watch'), ('records', 'exit_watch')):
            self.assertRegex(SRC, rf"wfold\('{key}', {module}\.render\(", key)

    def test_page_has_toggle_script_and_style(self):
        self.assertIn("watch_line += WFOLD_JS", SRC)
        self.assertIn('{WFOLD_CSS}', SRC)
        self.assertIn("localStorage.setItem('wfShut:'", bot.WFOLD_JS)  # 3분 자동 갱신 뒤에도 접힘 유지
        self.assertIn('모두 접기/펼치기', bot.WFOLD_JS)
        self.assertIn('.wf.shut', bot.WFOLD_CSS)

    def test_css_braces_balanced(self):
        self.assertEqual(bot.WFOLD_CSS.count('{'), bot.WFOLD_CSS.count('}'))


if __name__ == '__main__':
    unittest.main()
