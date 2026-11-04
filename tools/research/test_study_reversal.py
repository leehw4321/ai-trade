import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pit_universe as pit
import study_reversal as sr

B = pit.BAR
T0 = 1_700_000_000 // sr.STEP * sr.STEP


def synthetic_bars(start, n, price=100.0, val=10.0, seed=1, drift=0.0):
    rng = random.Random(seed)
    bars, p = {}, price
    for i in range(n):
        q = p * (1 + drift + rng.uniform(-0.003, 0.003))
        bars[start + i * B] = (p, max(p, q) * 1.001, min(p, q) * 0.999, q, val * (1 + rng.random()))
        p = q
    return bars


class FeaturesAndLabels(unittest.TestCase):
    def setUp(self):
        self.bars = synthetic_bars(T0 - 300 * B, 400)
        self.s0 = T0 - B

    def test_features_do_not_depend_on_future_bars(self):
        before = sr.features(self.bars, self.s0)
        changed = dict(self.bars)
        for s in list(changed):
            if s > self.s0:
                o = changed[s]
                changed[s] = (o[0] * 9, o[1] * 9, o[2] * 9, o[3] * 9, o[4] * 1000)
        self.assertEqual(before, sr.features(changed, self.s0))
        self.assertIsNotNone(before)

    def test_label_is_the_close_four_hours_later_minus_cost(self):
        c0, cf = self.bars[self.s0][3], self.bars[self.s0 + sr.H * B][3]
        self.assertAlmostEqual(sr.forward(self.bars, self.s0), cf / c0 - 1 - sr.COST)
        del self.bars[self.s0 + sr.H * B]
        self.assertIsNone(sr.forward(self.bars, self.s0))

    def test_missing_history_means_no_event(self):
        thin = {s: v for s, v in self.bars.items() if s >= self.s0 - 10 * B}
        self.assertIsNone(sr.features(thin, self.s0))

    def test_ordinal_ranks_are_deterministic_and_bounded(self):
        r = sr.ordinal_ranks([3, 1, 1, 2], ['d', 'c', 'b', 'a'])
        self.assertEqual(r, sr.ordinal_ranks([3, 1, 1, 2], ['d', 'c', 'b', 'a']))
        self.assertAlmostEqual(min(r), -0.5)
        self.assertAlmostEqual(max(r), 0.5)
        self.assertLess(r[2], r[1])  # 동률은 종목 코드순


class Models(unittest.TestCase):
    def events(self, n_t=60):
        rng = random.Random(3)
        out = []
        for t in range(n_t):
            T = T0 + t * sr.STEP
            for i in range(30):
                rank = {k: rng.uniform(-0.5, 0.5) for k in sr.FEATS}
                # ret_1h 순위가 낮을수록(하락) 정답 확률이 높은 합성 규칙
                prob = 0.2 - 0.3 * rank['ret_1h']
                fwd = 0.02 if rng.random() < prob else -0.01
                out.append({'T': T, 'm': f'KRW-C{i:02d}', 'raw': {k: rank[k] for k in sr.FEATS}, 'rank': rank, 'fwd': fwd})
        return out

    def test_logistic_recovers_the_sign_of_a_planted_signal(self):
        w = sr.fit_logistic(self.events(200))
        self.assertLess(w[1 + sr.FEATS.index('ret_1h')], -0.3)
        self.assertLess(abs(w[1 + sr.FEATS.index('vol_ratio')]), abs(w[1 + sr.FEATS.index('ret_1h')]))

    def test_current_rule_only_picks_zero_to_fifteen_percent_risers(self):
        rows = [{'m': f'KRW-C{i}', 'raw': {'rs_18h': v}, 'rank': {}, 'fwd': 0.0} for i, v in enumerate([0.20, 0.12, 0.10, 0.05, 0.02, -0.01, 0.14])]
        got = [e['raw']['rs_18h'] for e in sr.pick('current', None, rows)]
        self.assertEqual(got, [0.14, 0.12, 0.10])

    def test_reversal_score_prefers_pullbacks(self):
        low = {'m': 'KRW-A', 'rank': {k: -0.4 for k in sr.REVERSAL_FEATS}, 'raw': {}}
        high = {'m': 'KRW-B', 'rank': {k: 0.4 for k in sr.REVERSAL_FEATS}, 'raw': {}}
        self.assertEqual(sr.pick('reversal', None, [high, low])[0]['m'], 'KRW-A')

    def test_evaluation_compares_picks_with_the_universe_mean(self):
        ev = self.events(40)
        w = sr.fit_logistic(ev)
        per_t, base = sr.evaluate(ev, 'logistic', w)
        self.assertTrue(per_t)
        self.assertGreater(sum(p[1] - p[2] for p in per_t) / len(per_t), 0)  # 같은 자료로 학습했으므로 초과수익 > 0
        self.assertTrue(0 < base < 1)


class Bootstrap(unittest.TestCase):
    def test_deterministic_and_brackets_the_mean(self):
        rng = random.Random(5)
        vals = [rng.uniform(-1, 1) * 0.01 + 0.002 for _ in range(300)]
        a, b = sr.block_bootstrap(vals), sr.block_bootstrap(vals)
        self.assertEqual(a, b)
        mean = sum(vals) / len(vals)
        self.assertLess(a[0], mean)
        self.assertGreater(a[1], mean)


class LockedGuards(unittest.TestCase):
    def test_locked_refuses_without_gate_or_after_opening(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / 'dev_result.json').write_text(json.dumps({'gate_open_locked': False, 'candidate': 'reversal'}), encoding='utf-8')
            with mock.patch.object(sr, 'OUT_DIR', out):
                self.assertEqual(sr.run_locked(), 1)
                self.assertFalse((out / 'locked_opened.json').exists())
                (out / 'dev_result.json').write_text(json.dumps({'gate_open_locked': True, 'candidate': 'reversal'}), encoding='utf-8')
                (out / 'locked_opened.json').write_text('{}', encoding='utf-8')
                self.assertEqual(sr.run_locked(), 1)

    def test_locked_mode_requires_the_explicit_flag(self):
        with mock.patch.object(sys, 'argv', ['study_reversal.py', 'locked']):
            self.assertEqual(sr.main(), 2)


class Grid(unittest.TestCase):
    def test_label_of_the_last_locked_slot_completes_before_the_cutoff(self):
        data = {'KRW-A': synthetic_bars(pit.to_epoch('2026-05-03T21:30:00'), 10)}
        t_start, dev_end, lock_end = sr.grid(data)
        cut = pit.to_epoch(sr.CUTOFF_KST)
        self.assertLessEqual(lock_end - sr.STEP + sr.H * B, cut)
        self.assertGreater(dev_end, t_start)
        self.assertEqual(dev_end % sr.STEP, 0)


if __name__ == '__main__':
    unittest.main()
