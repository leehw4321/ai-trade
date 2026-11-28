"""user_vs_bot 집계: 같은 거래 id 묶기·끝난 거래만 승패·표 형식."""
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import user_vs_bot as U


class T(unittest.TestCase):
    def test_trips_and_table(self):
        d = tempfile.mkdtemp(); f = os.path.join(d, 'u.jsonl')
        rows = [{'event': 'open', 'id': 'U1', 'market': 'KRW-A', 'time': '2026-11-28T20:00:00+09:00', 'krw': 30000, 'context': {'rank': 3, 'rs': .12}},
                {'event': 'add', 'id': 'U1', 'market': 'KRW-A', 'time': '2026-11-28T20:10:00+09:00', 'krw': 10000},
                {'event': 'sell', 'id': 'U1', 'market': 'KRW-A', 'time': '2026-11-28T20:30:00+09:00', 'realized': 300, 'cost_sold': 20000, 'closed': False},
                {'event': 'sell', 'id': 'U1', 'market': 'KRW-A', 'time': '2026-11-28T21:00:00+09:00', 'realized': -100, 'cost_sold': 20000, 'closed': True},
                {'event': 'open', 'id': 'U2', 'market': 'KRW-B', 'time': '2026-11-28T21:30:00+09:00', 'krw': 20000}]
        open(f, 'w', encoding='utf-8').write('\n'.join(json.dumps(r) for r in rows))
        ts = U.user_trips(f)
        self.assertEqual(len(ts), 2); a = ts[0]
        self.assertEqual((a['cost'], a['adds'], a['closed'], a['pnl']), (40000, 1, True, 200))
        self.assertAlmostEqual(a['ret'], 200 / 40000)
        s = U.stat(ts); self.assertEqual((s['n'], s['win']), (1, 1.0))
        self.assertTrue(U.fmt(s).startswith('| 1 | 100% |'))


if __name__ == '__main__':
    unittest.main()
