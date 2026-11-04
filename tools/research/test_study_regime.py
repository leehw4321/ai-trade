import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pit_universe as pit
import study_regime as rg
import study_reversal as sr
from test_study_reversal import synthetic_bars, T0

B = pit.BAR


class Regime(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(rg.regime24({'m24': 0.012}), 'up')
        self.assertEqual(rg.regime24({'m24': 0.01}), 'up')
        self.assertEqual(rg.regime24({'m24': -0.01}), 'down')
        self.assertEqual(rg.regime24({'m24': 0.0}), 'flat')
        self.assertIsNone(rg.regime24({'m24': None}))
        self.assertEqual(rg.regime72({'m72': 0.02}), 'up')
        self.assertEqual(rg.regime72({'m72': -0.03}), 'down')

    def test_market_return_uses_only_past_bars(self):
        T = T0 + 400 * sr.STEP // sr.STEP * 0 + 300 * B  # 임의 격자 시점
        T = T // sr.STEP * sr.STEP
        data = {f'KRW-C{i}': synthetic_bars(T - 400 * B, 500, seed=i) for i in range(20)}
        ev = [{'T': T, 'm': m, 'rank': {}, 'raw': {}, 'fwd': 0.0} for m in data]
        rg.add_regime(data, ev)
        before = ev[0]['m24']
        for m in data:
            for s in list(data[m]):
                if s >= T:  # 미래 봉(마감이 T 이후)을 바꿔도 장세 판정은 같아야 한다
                    o = data[m][s]
                    data[m][s] = (o[0] * 5, o[1] * 5, o[2] * 5, o[3] * 5, o[4])
        ev2 = [{'T': T, 'm': m, 'rank': {}, 'raw': {}, 'fwd': 0.0} for m in data]
        rg.add_regime(data, ev2)
        self.assertEqual(before, ev2[0]['m24'])
        self.assertIsNotNone(before)

    def test_too_few_coins_gives_no_regime(self):
        T = (T0 + 300 * B) // sr.STEP * sr.STEP
        data = {f'KRW-C{i}': synthetic_bars(T - 400 * B, 500, seed=i) for i in range(10)}
        ev = [{'T': T, 'm': m, 'rank': {}, 'raw': {}, 'fwd': 0.0} for m in data]
        rg.add_regime(data, ev)
        self.assertIsNone(ev[0]['m24'])


class Tables(unittest.TestCase):
    def events(self):
        rng = random.Random(9)
        out = []
        for t in range(40):
            for i in range(30):
                x = rng.uniform(-0.5, 0.5)
                rank = {k: (x if k == 'ret_1h' else rng.uniform(-0.5, 0.5)) for k in sr.FEATS}
                out.append({'T': T0 + t * sr.STEP, 'm': f'KRW-C{i:02d}', 'raw': dict(rank, rs_18h=rng.uniform(-0.05, 0.2)), 'm24': 0.02,
                            'rank': rank, 'fwd': -0.02 * x + rng.uniform(-0.005, 0.005)})
        return out

    def test_planted_negative_relation_shows_up_in_ic_and_quintiles(self):
        ev = self.events()
        ics = rg.spearman_by_t(ev, 'ret_1h', lambda e: True)
        self.assertLess(sum(ics) / len(ics), -0.8)
        q = rg.quintiles(ev, rg.regime24, 'up')
        self.assertGreater(q['ret_1h']['means'][0], q['ret_1h']['means'][4])

    def test_knn_finds_the_planted_pattern_in_a_time_split(self):
        ev = self.events()
        res = rg.knn_test(ev, rg.regime24)
        self.assertGreater(res['pred_corr'], 0.5)
        self.assertGreater(res['knn']['mean_excess'], 0)


if __name__ == '__main__':
    unittest.main()
