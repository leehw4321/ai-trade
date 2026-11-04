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


class Boundaries(unittest.TestCase):
    def test_bar_count_thresholds_89_90_96_and_missing_last_six(self):
        T = T0 + 300 * B
        full = series(T - 96 * B, 96, 1.0)

        def without(starts):
            return {s: v for s, v in full.items() if s not in starts}
        all_slots = sorted(full)
        data = {'KRW-F96': full, 'KRW-F90': without(set(all_slots[:6])), 'KRW-F89': without(set(all_slots[:7])),
                'KRW-LAST6': without(set(all_slots[-6:]))}
        got = {m for m, _ in pit.universe_at(data, T)}
        self.assertEqual(got, {'KRW-F96', 'KRW-F90', 'KRW-LAST6'})  # 89개는 탈락, 90개와 '최근 6봉 결측'은 허용(정책 명시)
        self.assertAlmostEqual(dict(pit.universe_at(data, T))['KRW-F90'], 90.0)  # 결측은 0으로 채우지 않고 합에서 빠짐

    def test_ties_and_input_order_do_not_change_the_result(self):
        T = T0 + 300 * B
        names = [f'KRW-T{i}' for i in range(10)]
        a = {m: series(T - 96 * B, 96, 3.0) for m in names}
        b = {m: a[m] for m in reversed(names)}
        self.assertEqual(pit.universe_at(a, T, top_n=5), pit.universe_at(b, T, top_n=5))
        self.assertEqual([m for m, _ in pit.universe_at(a, T, top_n=5)], names[:5])

    def test_invalid_T_and_parameters_are_rejected(self):
        data = {'KRW-A': series(T0, 96, 1.0)}
        for T in (T0 + 1, float(T0 + 96 * B), None, 'x', float('nan'), T0 + 96 * B + 450):
            with self.assertRaises(ValueError, msg=repr(T)):
                pit.universe_at(data, T)
        for kw in ({'top_n': 0}, {'window': 0}, {'min_bars': 0}, {'min_bars': 97}, {'top_n': 2.5}, {'window': True and 1.5}):
            with self.assertRaises(ValueError, msg=repr(kw)):
                pit.universe_at(data, T0 + 96 * B, **kw)

    def test_same_instant_in_kst_and_utc_gives_the_same_epoch(self):
        from datetime import datetime, timezone
        kst = pit.to_epoch('2026-11-04T09:00:00')
        utc = int(datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual(kst, utc)
        self.assertEqual(pit.from_epoch_kst(utc), '2026-11-04T09:00:00')
        self.assertEqual(kst % B, 0)


class RawDataValidation(unittest.TestCase):
    def write(self, d, lines):
        (Path(d) / 'KRW-X.jsonl').write_text('\n'.join(lines), encoding='utf-8')

    @staticmethod
    def row(t, val=10.0, **kw):
        base = {'t': t, 'o': 1, 'h': 2, 'l': 0.5, 'c': 1.5, 'v': 3, 'val': val}
        base.update(kw)
        return json.dumps(base)

    def test_bad_rows_are_counted_and_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, [self.row('2026-11-03T00:00:00'),
                           self.row('2026-11-03T00:15:00', val=-1),
                           self.row('2026-11-03T00:30:00', o=0),
                           self.row('2026-11-03T00:45:00', h='nan'),
                           self.row('2026-11-03T01:07:00'),  # 격자 밖
                           'garbage'])
            stats = {}
            data = pit.load_dir(d, stats=stats)
            self.assertEqual(len(data['KRW-X']), 1)
            self.assertEqual((stats['bad_value'], stats['off_grid'], stats['bad_json']), (3, 1, 1))

    def test_conflicting_duplicates_are_reported_last_row_wins(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, [self.row('2026-11-03T00:00:00', val=1.0), self.row('2026-11-03T00:00:00', val=2.0),
                           self.row('2026-11-03T00:15:00', val=5.0), self.row('2026-11-03T00:15:00', val=5.0)])
            stats = {}
            data = pit.load_dir(d, stats=stats)
            self.assertEqual(stats['conflict_dup'], 1)  # 같은 값의 중복은 충돌이 아니다
            self.assertEqual(list(data['KRW-X'].values())[0][4], 2.0)

    def test_cutoff_drops_incomplete_and_late_bars(self):
        with tempfile.TemporaryDirectory() as d:
            self.write(d, [self.row('2026-11-03T00:00:00'), self.row('2026-11-03T00:15:00'), self.row('2026-11-03T00:30:00')])
            cutoff = pit.to_epoch('2026-11-03T00:30:00')  # 00:15 봉(마감 00:30)까지만 완결
            stats = {}
            data = pit.load_dir(d, cutoff=cutoff, stats=stats)
            self.assertEqual(len(data['KRW-X']), 2)
            self.assertEqual(stats['after_cutoff'], 1)
            with self.assertRaises(ValueError):
                pit.load_dir(d, cutoff=cutoff + 1)


class IndependentReimplementation(unittest.TestCase):
    """같은 정의를 다른 방식(전체 정렬 후 슬라이스)으로 구현해 무작위 자료에서 결과를 대조한다."""

    def test_matches_a_naive_implementation_on_random_data(self):
        import random
        rng = random.Random(11)
        T = T0 + 400 * B
        data = {}
        for i in range(40):
            start = T - rng.randint(60, 300) * B
            bars = {s: (1.0, 1.0, 1.0, 1.0, rng.random() * 100) for s in range(start, T + 5 * B, B) if rng.random() > 0.08}
            data[f'KRW-R{i}'] = bars
        mine = pit.universe_at(data, T, top_n=15)
        naive = []
        for m, bars in data.items():
            seq = [bars[s][4] for s in sorted(bars) if T - 96 * B <= s <= T - B]
            if len(seq) >= 90:
                naive.append((m, sum(seq)))
        naive.sort(key=lambda x: (-x[1], x[0]))
        self.assertEqual([m for m, _ in mine], [m for m, _ in naive[:15]])
        for (m1, v1), (m2, v2) in zip(mine, naive[:15]):
            self.assertAlmostEqual(v1, v2)


if __name__ == '__main__':
    unittest.main()
