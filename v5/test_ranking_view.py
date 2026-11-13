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


class ReentryNoteView(unittest.TestCase):
    P = st.Params(reentry_profit_hours=4.0, reentry_loss_hours=24.0, reentry_window_hours=24.0, reentry_dip_atr=1.0)
    NOW = 1_800_000_000.0

    def rows(self, last_sells, ranking=None):
        ranking = ranking or [{'market': 'KRW-A', 'rs': .08, 'rank': 2}, {'market': 'KRW-B', 'rs': .06, 'rank': 3}]
        return bot.ranking_rows(ranking, set(), set(), self.P, last_sells=last_sells, now=self.NOW).split('</tr>')

    def test_loss_exit_shows_reason_and_release_time_and_is_not_a_candidate(self):
        lines = self.rows({'KRW-A': {'time': self.NOW - 3600, 'price': 100.0, 'profit': False, 'reason': '손절'}})
        self.assertIn('재매수 금지', lines[0]); self.assertIn('손실 청산·손절', lines[0])
        self.assertIn('23.0시간 남음', lines[0]); self.assertIn('해제', lines[0])
        self.assertNotIn('투자 대상', lines[0])
        self.assertIn('투자 대상', lines[1])      # 막힌 종목은 후보 자리를 차지하지 않는다

    def test_banned_row_shows_buy_sell_and_current_price(self):
        last = {'KRW-A': {'time': self.NOW - 3600, 'price': 95.5, 'profit': False, 'reason': '손절', 'entry': 100.0}}
        row = bot.ranking_rows([{'market': 'KRW-A', 'rs': .08, 'rank': 2}], set(), set(), self.P, last_sells=last, now=self.NOW,
                               prices={'KRW-A': 97.25}).split('</tr>')[0]
        self.assertIn('매수 100 → 매도 95.5 (-4.55%)', row)
        self.assertIn('A <span class=sub>97.25원</span>', row)
        old = {'KRW-A': {'time': self.NOW - 3600, 'price': 95.5, 'profit': False, 'reason': '손절'}}   # 예전 기록: 체결 기록에서 평단을 찾는다
        row = bot.ranking_rows([{'market': 'KRW-A', 'rs': .08, 'rank': 2}], set(), set(), self.P, last_sells=old, now=self.NOW,
                               entry_of=lambda m, t: 100.0).split('</tr>')[0]
        self.assertIn('매수 100 → 매도 95.5', row)

    def test_every_held_and_recently_sold_row_shows_unit_prices(self):
        # 사용자 지시(2026-11-10, 11-13 재지시): 순위표에 종목별 매수 단가·매도 단가. 재매수 금지 행에만 붙어 '실종'됐던 회귀 방지.
        rk = [{'market': 'KRW-H', 'rs': .05, 'rank': 1}, {'market': 'KRW-S', 'rs': -.02, 'rank': 12},
              {'market': 'KRW-O', 'rs': -.03, 'rank': 13}]
        last = {'KRW-S': {'time': self.NOW - 20 * 3600, 'price': 110.0, 'profit': True, 'reason': '이익보호', 'entry': 100.0},
                'KRW-O': {'time': self.NOW - 30 * 3600, 'price': 90.0, 'profit': False, 'reason': '손절', 'entry': 100.0}}
        rows = bot.ranking_rows(rk, {'KRW-H'}, set(), self.P, last_sells=last, now=self.NOW,
                                prices={'KRW-H': 105.0}, entries={'KRW-H': 100.0}).split('</tr>')
        self.assertIn('보유 · 매수 100 (현재 +5.00%)', rows[0])          # 보유: 매수 평단과 현재 손익
        self.assertIn('매수 100 → 매도 110', rows[1])                     # 순위 밖·금지 풀린 최근 매도도 표시
        self.assertNotIn('매도 90', rows[2])                              # 재매수 기간(24시간) 지난 매도는 생략
        # 실행권 없는 PC: 봇 장부엔 옛 매도만 있어도 거래소 체결 기록의 최근 매도를 보여 준다
        sold = {'KRW-O': {'time': self.NOW - 600, 'price': 102.0, 'entry': 100.0}}
        rows = bot.ranking_rows(rk, set(), set(), self.P, last_sells=last, now=self.NOW, sold=sold).split('</tr>')
        self.assertIn('매수 100 → 매도 102', rows[2])

    def test_profit_exit_waits_then_needs_a_dip_then_expires(self):
        last = {'KRW-A': {'time': self.NOW - 3600, 'price': 100.0, 'profit': True, 'reason': '이익보호'}}
        self.assertIn('3.0시간 남음', self.rows(last)[0])
        last['KRW-A']['time'] = self.NOW - 5 * 3600
        self.assertIn('눌려야 함', self.rows(last)[0])
        last['KRW-A']['time'] = self.NOW - 25 * 3600
        self.assertNotIn('재매수', self.rows(last)[0]); self.assertIn('투자 대상', self.rows(last)[0])

    def test_no_note_without_sell_record_or_for_bad_record(self):
        self.assertNotIn('재매수', self.rows({})[0])
        self.assertNotIn('재매수', self.rows({'KRW-A': {'time': 'x'}})[0])
