import unittest

import bot
import strategy as st


class RankingView(unittest.TestCase):
    P = st.Params()

    def rows(self, ranking, held=(), warned=()):
        return bot.ranking_rows(ranking, set(held), set(warned), self.P)

    def test_held_is_blue_and_warning_is_red_and_tagged(self):
        ranking = [{'market': 'KRW-MPLX', 'rs': .30, 'rank': 1}, {'market': 'KRW-SOPH', 'rs': .17, 'rank': 2},
                   {'market': 'KRW-POD', 'rs': .12, 'rank': 3}, {'market': 'KRW-WAVES', 'rs': .05, 'rank': 4}]
        html = self.rows(ranking, held={'KRW-MPLX', 'KRW-POD'}, warned={'KRW-SOPH'})
        lines = html.split('</tr>')
        self.assertIn("class='rk-held'", lines[0]); self.assertIn('보유', lines[0])
        self.assertIn("class='rk-alert'", lines[1]); self.assertIn('경보', lines[1])
        self.assertIn("class='rk-held'", lines[2])
        self.assertIn("class='rk-watch'", lines[3])           # 4위: 후보는 아니지만 5위 안 정상 종목 = 관찰
        self.assertIn('관찰', lines[3])
        self.assertNotIn('매수 후보', lines[3])

    def test_candidate_and_overheat_tags_follow_buy_rule(self):
        ranking = [{'market': 'KRW-A', 'rs': .20, 'rank': 1}, {'market': 'KRW-B', 'rs': .08, 'rank': 2},
                   {'market': 'KRW-C', 'rs': .04, 'rank': 5}]
        lines = self.rows(ranking).split('</tr>')
        self.assertIn('과열', lines[0])                         # 15% 이상
        self.assertIn('rk-target', lines[1]); self.assertIn('투자 대상', lines[1])
        self.assertNotIn('rk-target', lines[2])                 # 5위는 후보 아님(관찰)

    def test_warning_wins_when_held_and_warned(self):
        html = self.rows([{'market': 'KRW-X', 'rs': .1, 'rank': 1}], held={'KRW-X'}, warned={'KRW-X'})
        self.assertIn("class='rk-alert'", html); self.assertIn('보유 · 매수 불가', html)

    def test_warned_shows_buy_candidate_or_blocked_reason(self):
        ranking = [{'market': 'KRW-A', 'rs': .1, 'rank': 1}, {'market': 'KRW-B', 'rs': .09, 'rank': 2}]
        warned = {'KRW-A': {'warning': True, 'trade_blocked': False, 'warning_reasons': ['거래량 급등']},
                  'KRW-B': {'warning': True, 'trade_blocked': True, 'warning_reasons': ['투자유의']}}
        lines = bot.ranking_rows(ranking, set(), warned, self.P).split('</tr>')
        self.assertIn('rk-target', lines[0]); self.assertIn('매수 후보', lines[0]); self.assertIn('거래량 급등(매수 가능)', lines[0])
        self.assertIn('rk-alert', lines[1]); self.assertIn('매수 불가(투자유의)', lines[1])

    def test_empty(self):
        self.assertEqual(self.rows([]), '')


if __name__ == '__main__':
    unittest.main()
