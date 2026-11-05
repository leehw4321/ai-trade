import unittest
from unittest.mock import patch

import shadow
import strategy as st


def bar(h, l, c, t='x'):
    return {'h': h, 'l': l, 'c': c, 't': t}


class Range4h(unittest.TestCase):
    def test_uses_the_last_sixteen_closed_bars_over_the_last_close(self):
        bars = [bar(100, 100, 100, str(i)) for i in range(10)] + [bar(110, 100, 105, 'a')] + [bar(105, 95, 100, str(i)) for i in range(15)]
        self.assertAlmostEqual(st.range_4h(bars), (110 - 95) / 100)

    def test_missing_or_bad_data_is_none_not_zero(self):
        self.assertIsNone(st.range_4h([bar(1, 1, 1)] * 15))
        self.assertIsNone(st.range_4h([bar(1, 1, 1)] * 15 + [bar(float('nan'), 1, 1)]))
        self.assertIsNone(st.range_4h([bar(1, 1, 0)] * 16))
        self.assertIsNone(st.range_4h([bar(1, 0, 1)] * 16))

    def test_rank_rows_carry_rng4(self):
        bars = [bar(100 + i * 0.1, 99 + i * 0.1, 100 + i * 0.1, str(i)) for i in range(120)]
        rows = st.rank({'KRW-A': bars}, st.Params())
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]['rng4'])


class Scale(unittest.TestCase):
    def test_scale_only_shrinks_and_off_is_none(self):
        self.assertAlmostEqual(st.vol_scale(0.119, 0.05), 0.05 / 0.119)
        self.assertEqual(st.vol_scale(0.03, 0.05), 1.0)
        self.assertIsNone(st.vol_scale(0.1, 0.0))
        for bad in (None, 0.0, -0.1, float('nan'), float('inf'), True):
            self.assertIsNone(st.vol_scale(bad if bad is not True else 'x', 0.05))


class Proposal(unittest.TestCase):
    CAPS = {'max_order_krw': 10 ** 9, 'max_total_buy_krw': 10 ** 9, 'max_sell_krw_per_plan': 10 ** 9}

    def setUp(self):
        for method in ('accounts', 'tickers', 'orderbooks', 'market_buy', 'market_sell'):
            block = patch.object(shadow.upbit, method, side_effect=AssertionError('unexpected external call'))
            block.start()
            self.addCleanup(block.stop)
        self.ranked = [{'market': 'KRW-CALM', 'rank': 1, 'rs': .05, 'rng4': 0.03},
                       {'market': 'KRW-WILD', 'rank': 2, 'rs': .05, 'rng4': 0.119},
                       {'market': 'KRW-TINY', 'rank': 3, 'rs': .05, 'rng4': 3.0}]
        self.eligible = {r['market'] for r in self.ranked}

    def plan(self, r0, ranked=None, log=None):
        params = st.Params(M=5, entry_want=3, entry_scan=8, coin_cap=1., total_cap=1., entry_risk=0.00184, risk=0.002,
                           size_by_risk=True, stop_max_pct=0.045, vol_size_r0=r0)
        return shadow.proposal({}, ranked or self.ranked, {}, self.eligible, params, self.CAPS, 5_000_000, 0, vol_log=log)

    def amounts(self, text):
        return {m.split()[0]: int(m.split()[1].replace('원', '')) for m in text.split('; ') if m.startswith('매수 ') and '없음' not in m for m in [m[3:]]}

    def test_off_leaves_amounts_unchanged_but_still_logs_reference_scale(self):
        log = []
        text, _ = self.plan(0.0, log=log)
        amt = self.amounts(text)
        self.assertEqual(amt['CALM'], amt['WILD'])
        wild = next(v for v in log if v['market'] == 'KRW-WILD')
        self.assertAlmostEqual(wild['scale_ref_5pct'], 0.05 / 0.119)
        self.assertIsNone(wild['applied_scale'])
        self.assertEqual(wild['final_amt'], wild['base_amt'])

    def test_on_shrinks_only_volatile_picks_and_never_tops_up(self):
        base = self.amounts(self.plan(0.0)[0])['CALM']
        text, reasons = self.plan(0.05)
        amt = self.amounts(text)
        self.assertEqual(amt['CALM'], base)
        self.assertEqual(amt['WILD'], int(base * 0.05 / 0.119 // 1000 * 1000))
        self.assertNotIn('TINY', amt)   # 5,000원 미달은 증액하지 않고 보류
        self.assertTrue(any('TINY 매수 불가' in r for r in reasons))
        self.assertTrue(any('변동성 배율' in r for r in reasons))

    def test_missing_range_holds_the_buy_when_on(self):
        ranked = [dict(self.ranked[0], rng4=None)]
        text, reasons = self.plan(0.05, ranked=ranked)
        self.assertIn('매수 없음', text)
        self.assertTrue(any('자료 없음·비정상' in r for r in reasons))
        text, _ = self.plan(0.0, ranked=ranked)   # 꺼져 있으면 기존 동작 그대로
        self.assertIn('매수 CALM', text)


if __name__ == '__main__':
    unittest.main()
