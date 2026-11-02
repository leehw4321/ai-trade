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


class Ratchet(unittest.TestCase):
    def pos(self):
        # 진입 100, ATR 1, k=3 -> 초기 손절 97, 1R=3
        return {'entry': 100.0, 'stop': 97.0, 'atr': 1.0}

    def test_disabled_matches_fixed_stop(self):
        p = st.Params()
        pos = self.pos()
        self.assertIsNone(st.ratchet('KRW-X', pos, 110, p))
        self.assertEqual(pos['stop'], 97.0)
        self.assertEqual(st.ratchet('KRW-X', pos, 96.9, p), '손절')

    def test_breakeven_lock_then_trailing_stop_never_decreases(self):
        p = st.Params(trail_enabled=True, giveback=0)
        pos = self.pos()
        self.assertIsNone(st.ratchet('KRW-X', pos, 103, p))      # g=1 -> 본전 잠금
        self.assertGreater(pos['stop'], 100.0)
        self.assertIsNone(st.ratchet('KRW-X', pos, 110, p))      # g>=2 -> 110-2*1=108
        self.assertAlmostEqual(pos['stop'], 108.0)
        self.assertIsNone(st.ratchet('KRW-X', pos, 109, p))      # 하락해도 손절가는 유지
        self.assertAlmostEqual(pos['stop'], 108.0)
        self.assertEqual(st.ratchet('KRW-X', pos, 107.9, p), '추적 손절')

    def test_giveback_exit(self):
        p = st.Params(trail_enabled=True, trail_m=10.0, giveback=0.5)  # 추적 거리를 넓혀 반납 규칙만 시험
        pos = self.pos()
        st.ratchet('KRW-X', pos, 110, p)                              # 고점 이익 10
        self.assertIsNone(st.ratchet('KRW-X', pos, 106, p))           # 반납 40%
        self.assertEqual(st.ratchet('KRW-X', pos, 105, p), '수익 반납')  # 반납 50%

    def test_no_trailing_before_start_r(self):
        p = st.Params(trail_enabled=True)
        pos = self.pos()
        st.ratchet('KRW-X', pos, 105, p)                              # g=1.67: 본전 잠금까지만
        self.assertLess(pos['stop'], 105 - 2 * 1.0 + 0.01)
        self.assertIsNone(st.ratchet('KRW-X', pos, 103, p))           # 반납 규칙은 g>=2부터

    def test_metaplex_like_path_exits_before_giving_back_to_plus_4_percent(self):
        # 설명용 합성 경로: 1R=3%, ATR=1%. +10%까지 올랐다가 하락.
        p = st.Params(trail_enabled=True, k=3.0)
        pos = {'entry': 100.0, 'stop': 97.0, 'atr': 1.0}
        reason = None
        for price in [102, 105, 108, 110, 109, 108, 107, 106, 105, 104]:
            reason = st.ratchet('KRW-X', pos, price, p)
            if reason:
                break
        self.assertIsNotNone(reason)
        self.assertGreater(price, 104)


class ChaseAndStopCap(unittest.TestCase):
    def test_chase_block_at_cap_and_disabled(self):
        p = st.Params()
        self.assertIsNone(st.chase_block({'rs': 0.149}, p))
        self.assertIn('과열', st.chase_block({'rs': 0.15}, p))
        self.assertIsNone(st.chase_block({'rs': 0.9}, st.Params(chase_rs_cap=0)))

    def test_plan_skips_overheated_but_keeps_next_candidate(self):
        p = st.Params(slots=2, entry_top=3)
        ranked = [{'market': 'KRW-A', 'rs': 0.30, 'rank': 1}, {'market': 'KRW-B', 'rs': 0.10, 'rank': 2},
                  {'market': 'KRW-C', 'rs': 0.05, 'rank': 3}]
        _, buys, _ = st.plan(ranked, [], {}, {'KRW-A', 'KRW-B', 'KRW-C'}, p)
        self.assertEqual(buys, ['KRW-B', 'KRW-C'])

    def test_cap_stop_only_raises(self):
        p = st.Params()
        self.assertAlmostEqual(st.cap_stop(100, 60, p), 92.0)      # 너무 먼 손절은 -8%로
        self.assertEqual(st.cap_stop(100, 95, p), 95)               # 이미 가까우면 그대로
        self.assertEqual(st.cap_stop(100, 60, st.Params(stop_max_pct=0)), 60)
