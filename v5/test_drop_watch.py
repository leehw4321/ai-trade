"""급락 중 매수 보류(strategy.drop_block)와 관찰(drop_watch)."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path

import drop_watch as dw
import missed_ledger
import strategy as st


def bars_from(prices, start=0):
    out = []
    for i, (o, h, l, c) in enumerate(prices):
        out.append({'t': dt.datetime.fromtimestamp(start + i * 900, dt.timezone.utc).isoformat(), 'o': o, 'h': h, 'l': l, 'c': c})
    return out


class DropBlock(unittest.TestCase):
    def test_recent_change_uses_n_bars(self):
        b = bars_from([(100, 100, 100, 100)] * 10 + [(100, 100, 90, 93)])
        self.assertAlmostEqual(st.recent_change(b, 120, 15), 0.93 - 1)
        self.assertIsNone(st.recent_change(b[:5], 120, 15))

    def test_block_only_at_or_below_limit(self):
        p = st.Params(drop2h_block_pct=0.06)
        self.assertIn('급락', st.drop_block({'r2': -0.06}, p))
        self.assertIsNone(st.drop_block({'r2': -0.059}, p))
        self.assertIsNone(st.drop_block({'r2': None}, p))
        self.assertIsNone(st.drop_block({'r2': -0.5}, st.Params()))   # 기본은 꺼짐

    def test_rank_rows_carry_r2_r4(self):
        p = st.Params(L=8, drop2h_block_pct=0.06)
        b = bars_from([(100, 101, 99, 100)] * 30 + [(100, 100, 90, 92)])
        r = st.rank({'KRW-A': b}, p)[0]
        self.assertAlmostEqual(r['r2'], -0.08)
        self.assertIsNotNone(r['r4'])

    def test_missed_ledger_names_block(self):
        p = st.Params(drop2h_block_pct=0.06, entry_top=3)
        r = {'market': 'KRW-A', 'rank': 1, 'rs': 0.05, 'r2': -0.07}
        b = missed_ledger.blocks_for(r, p, set(), set(), {}, None, {'KRW-A'}, False, 3, 0)
        self.assertEqual(b, ['급락 중(2시간)'])


class Watch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_only_clean_or_drop_blocked_and_dedup(self):
        ranked = [{'market': 'KRW-A', 'r2': -0.07, 'r4': -0.12}, {'market': 'KRW-B', 'r2': -0.045, 'r4': 0.0},
                  {'market': 'KRW-C', 'r2': 0.0, 'r4': 0.0}]
        rows = [{'market': 'KRW-A', 'signal': True, 'close': 10, 'blocks': ['급락 중(2시간)'], 'outcome': '차단'},
                {'market': 'KRW-B', 'signal': True, 'close': 10, 'blocks': [], 'outcome': '매수'},
                {'market': 'KRW-C', 'signal': True, 'close': 10, 'blocks': ['재진입 대기'], 'outcome': '차단'}]
        self.assertEqual(dw.record(self.d, 1000, ranked, rows), 2)
        obs = {o['m']: o for o in dw.load(self.d)['obs']}
        self.assertEqual(obs['KRW-A']['hits'], ['2h6', '2h4', '4h10'])
        self.assertEqual(obs['KRW-B']['hits'], ['2h4'])
        self.assertTrue(obs['KRW-B']['bought'])
        self.assertEqual(dw.record(self.d, 1000 + 3600, ranked, rows), 0)        # 6시간 안 같은 종목은 다시 세지 않음
        self.assertEqual(dw.record(self.d, 1000 + 7 * 3600, ranked, rows), 2)

    def test_later_drop_hit_recorded_even_if_seen_clean(self):
        row = [{'market': 'KRW-P', 'signal': True, 'close': 10, 'blocks': [], 'outcome': '후보였으나 미매수(자리·현금·후보 수)'}]
        self.assertEqual(dw.record(self.d, 0, [{'market': 'KRW-P', 'r2': 0.0, 'r4': 0.0}], row), 1)
        row[0]['blocks'] = ['급락 중(2시간)']
        self.assertEqual(dw.record(self.d, 3600, [{'market': 'KRW-P', 'r2': -0.07, 'r4': 0.0}], row), 1)
        self.assertEqual(dw.record(self.d, 7200, [{'market': 'KRW-P', 'r2': -0.08, 'r4': 0.0}], row), 0)

    def test_simulate_stop_guard_time(self):
        flat = [(100, 100.5, 99.5, 100)] * 24
        self.assertEqual(dw.simulate(100, bars_from([(100, 100, 96, 97)] + flat), 0, 0.035, 0.03, 0.3)[1], '손절')
        up = [(100, 105, 100, 104), (104, 104, 103, 103)] + flat
        r = dw.simulate(100, bars_from(up), 0, 0.035, 0.03, 0.3)
        self.assertEqual(r[1], '이익보호'); self.assertAlmostEqual(r[0], 0.03 - 0.001)
        self.assertEqual(dw.simulate(100, bars_from(flat), 0, 0.035, 0.03, 0.3)[1], '6시간')
        self.assertIsNone(dw.simulate(100, bars_from(flat[:5]), 0, 0.035, 0.03, 0.3))

    def test_resolve_and_render(self):
        ranked = [{'market': 'KRW-A', 'r2': -0.07, 'r4': -0.02}]
        rows = [{'market': 'KRW-A', 'signal': True, 'close': 100, 'blocks': ['급락 중(2시간)'], 'outcome': '차단'}]
        dw.record(self.d, 0, ranked, rows, now=0)
        n = dw.resolve(self.d, lambda m: bars_from([(100, 100, 96, 97)] + [(97, 97, 97, 97)] * 30), 0.035, 0.03, 0.3, now=8 * 3600)
        self.assertEqual(n, 1)
        html = dw.render(self.d, 0.06, now=8 * 3600)
        self.assertIn('막은 효과 +2,520원', html)
        self.assertIn('[적용 6%]', html)
        self.assertIn('재결정', html)

    def test_render_before_start(self):
        self.assertIn('기록 시작', dw.render(self.d))


if __name__ == '__main__':
    unittest.main()
