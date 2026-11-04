import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pit_universe as pit
import study_hold as sh

B = pit.BAR
H = 3600
T0 = 1_700_000_000 // H * H


def flat_bars(start, n, price=100.0):
    return {start + i * B: (price, price, price, price, 10.0) for i in range(n)}


class Simulate(unittest.TestCase):
    def setUp(self):
        self.bars = flat_bars(T0 - 10 * B, 400)
        sh._MKT[id(self.bars)] = 'KRW-A'

    def test_stop_fills_at_the_stop_or_the_gap_open(self):
        stop_bar = T0 + 3 * B
        self.bars[stop_bar] = (100.0, 100.0, 94.0, 95.0, 10.0)  # 저가가 손절 95.5 아래
        g, ts, why, px = sh.simulate(self.bars, T0, 100.0, 0.045, 24, False, {})
        self.assertEqual((why, ts), ('stop', stop_bar))
        self.assertAlmostEqual(px, 95.5)
        self.bars[stop_bar] = (92.0, 92.0, 90.0, 91.0, 10.0)  # 시가 갭이 손절 아래면 시가
        g, ts, why, px = sh.simulate(self.bars, T0, 100.0, 0.045, 24, False, {})
        self.assertAlmostEqual(px, 92.0)

    def test_time_exit_uses_the_last_completed_bar(self):
        last = T0 + 24 * H - B
        self.bars[last] = (100.0, 103.0, 100.0, 102.0, 10.0)
        g, ts, why, px = sh.simulate(self.bars, T0, 100.0, 0.045, 24, False, {})
        self.assertEqual((why, ts), ('time', T0 + 24 * H))
        self.assertAlmostEqual(g, 0.02)

    def test_rank_exit_happens_on_the_hour_when_rank_drops_or_coin_leaves(self):
        state = {T0 + H: {'KRW-A': (0.05, 6)}}
        g, ts, why, px = sh.simulate(self.bars, T0, 100.0, 0.045, 24, True, state)
        self.assertEqual((why, ts), ('rank', T0 + H))
        state = {T0 + H: {'KRW-A': (0.05, 3)}, T0 + 2 * H: {}}
        g, ts, why, px = sh.simulate(self.bars, T0, 100.0, 0.045, 24, True, state)
        self.assertEqual((why, ts), ('rank', T0 + 2 * H))  # 상위30 밖이면 청산

    def test_no_stop_policy_never_stops(self):
        self.bars[T0 + B] = (100.0, 100.0, 50.0, 60.0, 10.0)
        g, ts, why, px = sh.simulate(self.bars, T0, 100.0, None, 24, False, {})
        self.assertEqual(why, 'time')


class Bootstrap(unittest.TestCase):
    def test_block_ci_brackets_a_clear_mean(self):
        import random
        rng = random.Random(1)
        vals = [rng.uniform(-0.01, 0.03) for _ in range(600)]
        times = [T0 + i * 4 * H for i in range(600)]
        lo, hi = sh.block_mean_ci(vals, times)
        self.assertLess(lo, sum(vals) / len(vals))
        self.assertGreater(hi, sum(vals) / len(vals))
        self.assertGreater(lo, 0)


if __name__ == '__main__':
    unittest.main()
