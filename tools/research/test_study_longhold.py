import unittest

import pit_universe as pit
import study_longhold as lh

B, H = pit.BAR, lh.HOUR


def bar(o, h, l, c):
    return (o, h, l, c, 1.0)


class Candidates(unittest.TestCase):
    ST = {'KRW-A': (0.20, 1), 'KRW-B': (0.17, 2), 'KRW-C': (0.10, 3), 'KRW-D': (0.05, 4), 'KRW-E': (0.02, 5), 'KRW-F': (0.01, 6),
          'KRW-G': (0.008, 7), 'KRW-H': (0.006, 8), 'KRW-I': (0.004, 9), 'KRW-J': (-0.01, 10)}

    def test_overheated_names_are_dropped_after_cutting_by_rank(self):
        c5, hot5 = lh.candidates(self.ST, 5)
        self.assertEqual([m for m, _ in c5], ['KRW-C', 'KRW-D', 'KRW-E'])
        self.assertEqual(hot5, 2)

    def test_wider_scan_fills_more_slots_but_never_beyond_want(self):
        st = dict(self.ST, **{'KRW-C': (0.30, 3), 'KRW-D': (0.20, 4)})
        self.assertEqual([m for m, _ in lh.candidates(st, 5)[0]], ['KRW-E'])
        self.assertEqual([m for m, _ in lh.candidates(st, 8)[0]], ['KRW-E', 'KRW-F', 'KRW-G'])
        self.assertEqual(len(lh.candidates(st, 10)[0]), lh.WANT)

    def test_negative_or_zero_rs_is_not_a_candidate(self):
        self.assertEqual(lh.candidates({'KRW-A': (0.0, 1), 'KRW-B': (-0.02, 2)}, 5)[0], [])


class Simulate(unittest.TestCase):
    T = 1_700_000_100 // H * H

    def bars(self, fn, n=24 * 4 * 10):
        return {self.T + i * B: fn(i) for i in range(n)}

    def test_stop_fills_at_stop_or_lower_open(self):
        bars = self.bars(lambda i: bar(100, 101, 90 if i == 4 else 99, 100))
        g, hours, why = lh.simulate(bars, 'KRW-A', self.T, 100.0, 5, 0.045, 168, {})
        self.assertEqual(why, 'stop')
        self.assertAlmostEqual(g, -0.045)
        self.assertAlmostEqual(hours, 1.0)
        gap = self.bars(lambda i: bar(80 if i == 4 else 100, 100, 79 if i == 4 else 99, 90))
        self.assertAlmostEqual(lh.simulate(gap, 'KRW-A', self.T, 100.0, 5, 0.045, 168, {})[0], -0.2)

    def test_rank_exit_uses_the_given_limit_and_time_exit_otherwise(self):
        bars = self.bars(lambda i: bar(100, 101, 99, 100))
        state = {self.T + k * H: ({'KRW-A': (0.05, 7)}, 25) for k in range(1, 240)}
        self.assertEqual(lh.simulate(bars, 'KRW-A', self.T, 100.0, 5, 0.045, 168, state)[2], 'rank')   # 7위 > 5
        self.assertEqual(lh.simulate(bars, 'KRW-A', self.T, 100.0, 8, 0.045, 24, state)[2], 'time')    # 7위 ≤ 8
        self.assertEqual(lh.simulate(bars, 'KRW-A', self.T, 100.0, None, 0.08, 24, state)[2], 'time')   # 순위 이탈 없음

    def test_missing_data_returns_none(self):
        self.assertIsNone(lh.simulate({}, 'KRW-A', self.T, 100.0, None, 0.045, 24, {}))


if __name__ == '__main__':
    unittest.main()
