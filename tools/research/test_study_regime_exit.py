import unittest

import pit_universe as pit
import study_regime_exit as rx

B, H = pit.BAR, rx.HOUR


def bar(o, h, l, c):
    return (o, h, l, c, 1.0)


class Stage(unittest.TestCase):
    def test_edges(self):
        self.assertEqual([rx.stage_of(x) for x in (-0.05, -0.02, 0.0, 0.02, 0.05)], [0, 1, 2, 3, 4])
        self.assertEqual([rx.REGIME_OF_STAGE[i] for i in range(5)], ['하락', '하락', '보합', '상승', '상승'])


class Simulate(unittest.TestCase):
    T = 1_700_000_100 // H * H
    POL = dict(stop=0.045, guard=(0.03, 0.30), tp=None, rank=None, hours=24)

    def bars(self, fn, n=24 * 4 * 10):
        return {self.T + i * B: fn(i) for i in range(n)}

    def test_guard_exits_after_arming_and_giveback(self):
        bars = self.bars(lambda i: bar(108 if i >= 3 else 100, 110 if i == 2 else (101 if i < 2 else 109), 105 if i == 4 else (108 if i == 3 else 99.9), 108))
        g, h, why = rx.simulate(bars, 'KRW-A', self.T, 100.0, self.POL, {})
        self.assertEqual(why, 'guard')
        self.assertAlmostEqual(g, 0.07)

    def test_stop_is_checked_before_guard_and_tp_in_the_same_bar(self):
        pol = dict(self.POL, tp=0.03)
        bars = self.bars(lambda i: bar(100, 110, 90, 100) if i == 3 else bar(100, 101, 99.9, 100))
        self.assertEqual(rx.simulate(bars, 'KRW-A', self.T, 100.0, pol, {})[2], 'stop')

    def test_take_profit_and_time_exit(self):
        pol = dict(self.POL, guard=None, tp=0.03)
        up = self.bars(lambda i: bar(100, 104 if i == 5 else 101, 99.9, 100))
        g, h, why = rx.simulate(up, 'KRW-A', self.T, 100.0, pol, {})
        self.assertEqual(why, 'tp')
        self.assertAlmostEqual(g, 0.03)
        flat = self.bars(lambda i: bar(100, 101, 99.9, 100))
        self.assertEqual(rx.simulate(flat, 'KRW-A', self.T, 100.0, dict(pol, tp=None), {})[2], 'time')

    def test_rank_exit_only_when_rank_limit_is_set(self):
        bars = self.bars(lambda i: bar(100, 101, 99.9, 100))
        state = {self.T + k * H: ({'KRW-A': (0.05, 8)}, 25, 0.0) for k in range(1, 240)}
        self.assertEqual(rx.simulate(bars, 'KRW-A', self.T, 100.0, dict(self.POL, rank=7), state)[2], 'rank')
        self.assertEqual(rx.simulate(bars, 'KRW-A', self.T, 100.0, dict(self.POL, rank=9), state)[2], 'time')


class Mapping(unittest.TestCase):
    def test_mapping_is_chosen_only_from_training_periods(self):
        def e(period, reg, a, b):
            return {'period': period, 'regime': reg, 'ret': {'P0 현재': a, 'D 방어': b, 'S 스윙': -1, 'U 추세': -1}}
        entries = [e('P1', '하락', 0.0, 0.01), e('P2', '하락', 0.0, 0.01), e('P3', '하락', 0.5, -0.5)]
        self.assertEqual(rx.choose_mapping(entries, {'P1', 'P2'})['하락'], 'D 방어')   # P3의 값은 보지 않는다
        self.assertEqual(rx.choose_mapping(entries, {'P3'})['하락'], 'P0 현재')

    def test_user_mapping_is_frozen(self):
        self.assertEqual(rx.R3, {'하락': 'D 방어', '보합': 'S 스윙', '상승': 'U 추세'})


class Verdict(unittest.TestCase):
    def test_needs_lower_bound_and_every_period(self):
        self.assertEqual(rx.verdict(0.001, 0.003, [0.002, 0.003, 0.004], 0.001, 0.0005), '실거래 변경 제안 가능')
        self.assertIn('일관성', rx.verdict(0.001, 0.003, [0.002, -0.001, 0.004], 0.001, 0.001))
        self.assertEqual(rx.verdict(-0.001, 0.002, [0.002, 0.003, 0.004], 0.001, 0.001), '불확실/근거 없음')


if __name__ == '__main__':
    unittest.main()
