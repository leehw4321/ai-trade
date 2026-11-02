import unittest

import strategy as st


def bars_from(closes, spread=0.02):
    return [{'t': str(i), 'o': c, 'h': c * (1 + spread), 'l': c * (1 - spread), 'c': c} for i, c in enumerate(closes)]


class Indicators(unittest.TestCase):
    def test_atr_needs_n_true_ranges(self):
        self.assertIsNone(st.wilder_atr(bars_from([100] * 14)))
        self.assertAlmostEqual(st.wilder_atr(bars_from([100] * 15)), 4.0)

    def test_atr_wilder_smoothing(self):
        bars = bars_from([100] * 15) + [{'t': 'x', 'o': 100, 'h': 118, 'l': 100, 'c': 100}]
        self.assertAlmostEqual(st.wilder_atr(bars), (4.0 * 13 + 18) / 14)

    def test_relative_strength(self):
        self.assertAlmostEqual(st.relative_strength(bars_from([100, 110, 120]), 2), 0.2)
        self.assertIsNone(st.relative_strength(bars_from([100, 110]), 2))


class Rotation(unittest.TestCase):
    p = st.Params(L=2, M=3, slots=3, entry_top=3)

    def ranked(self, *markets):
        return [{'market': m, 'rank': i, 'rs': 0.1} for i, m in enumerate(markets, 1)]

    def test_held_inside_keep_rank_is_not_replaced(self):
        sells, buys, _ = st.plan(self.ranked('A', 'B', 'C', 'D'), ['C'], set(), {'A', 'B', 'D'}, self.p)
        self.assertEqual(sells, [])
        self.assertEqual(buys, ['A', 'B'])

    def test_held_outside_keep_rank_is_sold_and_slot_filled_from_top3_only(self):
        sells, buys, _ = st.plan(self.ranked('A', 'B', 'C', 'D', 'E'), ['A', 'B', 'E'], set(), {'C', 'D'}, self.p)
        self.assertEqual(sells, [('E', '순환')])
        self.assertEqual(buys, ['C'])

    def test_no_fill_below_top3(self):
        _, buys, _ = st.plan(self.ranked('A', 'B', 'C', 'D'), [], {'A', 'B', 'C'}, {'A', 'B', 'C', 'D'}, self.p)
        self.assertEqual(buys, [])

    def test_missing_data_keeps_holding(self):
        sells, _, unknown = st.plan(self.ranked('A'), ['Z'], set(), {'A'}, self.p)
        self.assertEqual((sells, unknown), ([], ['Z']))

    def test_negative_strength_not_bought(self):
        ranked = [{'market': 'A', 'rank': 1, 'rs': -0.01}]
        self.assertEqual(st.plan(ranked, [], set(), {'A'}, self.p)[1], [])


class Sizing(unittest.TestCase):
    p = st.Params()

    def test_risk_limited(self):
        qty = st.size(1_000_000, 0, 1_000_000, 100.0, 90.0, self.p)
        self.assertAlmostEqual(qty, 200.0)  # 2,000원 위험 / 10원 거리

    def test_coin_cap(self):
        qty = st.size(1_000_000, 0, 1_000_000, 100.0, 99.99, self.p)
        self.assertAlmostEqual(qty * 100, 250_000)

    def test_min_order_skip(self):
        self.assertEqual(st.size(190_000, 0, 190_000, 100.0, 90.0, self.p), 0.0)  # 380원 위험 → 3,800원 주문

    def test_bad_stop(self):
        self.assertEqual(st.size(1_000_000, 0, 1_000_000, 100.0, 0.0, self.p), 0.0)


if __name__ == '__main__':
    unittest.main()
