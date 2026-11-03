import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pit_universe as pit

B = pit.BAR
T0 = 1_700_000_100 // B * B  # 임의의 봉 경계


def series(start, n, val, price=100.0):
    return {start + i * B: (price, price, price, price, val) for i in range(n)}


class NoLookahead(unittest.TestCase):
    def test_future_volume_never_enters_the_ranking(self):
        T = T0 + 200 * B  # 판단 시각(봉 마감)
        quiet_then_spike = series(T - 96 * B, 96, 1.0)  # 마감 ≤ T 인 96개는 조용함
        quiet_then_spike.update(series(T, 50, 1e12))  # T 이후 봉에서 폭증: 보여서는 안 된다
        steady = series(T - 96 * B, 96, 5.0)
        data = {'KRW-SPIKE': quiet_then_spike, 'KRW-STEADY': steady}
        ranked = pit.universe_at(data, T, top_n=2)
        self.assertEqual([m for m, _ in ranked], ['KRW-STEADY', 'KRW-SPIKE'])
        self.assertAlmostEqual(dict(ranked)['KRW-SPIKE'], 96.0)

    def test_the_bar_closing_exactly_at_T_is_included_and_the_next_is_not(self):
        T = T0 + 300 * B
        bars = series(T - 96 * B, 96, 1.0)  # 마지막 봉 시작 = T - BAR → 마감 = T
        data = {'KRW-A': dict(bars)}
        self.assertAlmostEqual(dict(pit.universe_at(data, T, top_n=1))['KRW-A'], 96.0)
        data['KRW-A'][T] = (1, 1, 1, 1, 1000.0)  # 마감이 T + BAR: 미래
        self.assertAlmostEqual(dict(pit.universe_at(data, T, top_n=1))['KRW-A'], 96.0)
        self.assertAlmostEqual(dict(pit.universe_at(data, T + B, top_n=1))['KRW-A'], 96.0 - 1.0 + 1000.0)

    def test_recent_listings_and_gappy_markets_are_excluded(self):
        T = T0 + 300 * B
        data = {'KRW-OLD': series(T - 96 * B, 96, 1.0),
                'KRW-NEW': series(T - 10 * B, 10, 1e9),  # 상장 직후: 90봉 미만
                'KRW-GAPPY': {s: v for i, (s, v) in enumerate(series(T - 96 * B, 96, 1.0).items()) if i % 3}}
        self.assertEqual([m for m, _ in pit.universe_at(data, T)], ['KRW-OLD'])

    def test_stablecoins_excluded_and_ties_are_deterministic(self):
        T = T0 + 300 * B
        data = {'KRW-USDT': series(T - 96 * B, 96, 1e9), 'KRW-B': series(T - 96 * B, 96, 2.0), 'KRW-A': series(T - 96 * B, 96, 2.0)}
        self.assertEqual([m for m, _ in pit.universe_at(data, T)], ['KRW-A', 'KRW-B'])

    def test_top_n_limits_and_orders(self):
        T = T0 + 300 * B
        data = {f'KRW-C{i}': series(T - 96 * B, 96, float(i + 1)) for i in range(40)}
        ranked = pit.universe_at(data, T, top_n=30)
        self.assertEqual(len(ranked), 30)
        self.assertEqual(ranked[0][0], 'KRW-C39')
        self.assertEqual(ranked[-1][0], 'KRW-C10')


class Loader(unittest.TestCase):
    def test_roundtrip_and_bad_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            lines = [json.dumps({'t': '2026-11-03T00:00:00', 'o': 1, 'h': 2, 'l': 0.5, 'c': 1.5, 'v': 3, 'val': 4.5}),
                     '{"t": "2026-11-03T00:15:00", "o": 1', 'garbage', json.dumps({'x': 1})]
            (Path(d) / 'KRW-X.jsonl').write_text('\n'.join(lines), encoding='utf-8')
            data = pit.load_dir(d)
            self.assertEqual(list(data), ['KRW-X'])
            bars = data['KRW-X']
            self.assertEqual(len(bars), 1)
            (b,) = bars.values()
            self.assertEqual(b, (1, 2, 0.5, 1.5, 4.5))

    def test_epoch_grid_is_consistent_for_15_minute_steps(self):
        a = pit.to_epoch('2026-11-03T00:00:00')
        self.assertEqual(pit.to_epoch('2026-11-03T00:15:00') - a, B)
        self.assertEqual(pit.to_epoch('2026-11-04T00:00:00') - a, 86400)


if __name__ == '__main__':
    unittest.main()
