"""최근 성적 매수 휴식 관찰(pause_watch)과 주요 코인 추세 모의(trend_shadow)."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import pause_watch as pw
import trend_shadow as ts

KST = dt.timezone(dt.timedelta(hours=9))


def order(t, m, side, vol, funds, fee=0.0, ident='v5-x'):
    return json.dumps({'created_at': dt.datetime.fromtimestamp(t, KST).isoformat(), 'market': m, 'side': side,
                       'executed_volume': str(vol), 'executed_funds': str(funds), 'paid_fee': str(fee), 'identifier': ident})


class Pause(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, lines):
        (self.d / 'exchange_orders.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    def test_trips_and_ignore_non_bot(self):
        self.write([order(0, 'KRW-A', 'bid', 10, 10000), order(10, 'KRW-A', 'ask', 10, 9000),
                    order(20, 'KRW-B', 'bid', 1, 10000, ident=''), order(30, 'KRW-B', 'ask', 1, 20000, ident='')])
        t = pw.trips(self.d / 'exchange_orders.jsonl')
        self.assertEqual(len(t), 1); self.assertAlmostEqual(t[0]['pnl'], -1000)

    def test_pause_after_20_losing_trips(self):
        lines = []
        for k in range(20):
            lines += [order(k * 100, f'KRW-L{k}', 'bid', 10, 10000), order(k * 100 + 50, f'KRW-L{k}', 'ask', 10, 9900)]
        lines += [order(5000, 'KRW-N', 'bid', 10, 10000), order(5100, 'KRW-N', 'ask', 10, 10500)]
        self.write(lines)
        rows, now_p, now_s = pw.evaluate(pw.trips(self.d / 'exchange_orders.jsonl'), since=4000, now=6000)
        self.assertEqual(len(rows), 1); self.assertTrue(rows[0]['paused'])
        self.assertTrue(now_p)            # 마지막 20건(손실 19 + 이익 1) 합 −1,400 ≤ 0
        (self.d / pw.STATE).write_text('{"since": 4000}', encoding='utf-8')
        html = pw.render(self.d, now=6000)
        self.assertIn('쉬었어야 할 매수 1건', html)
        self.assertIn('쉬었다면 효과 -500원', html)

    def test_first_render_starts_now(self):
        self.write([])
        self.assertIn('시작 뒤 끝난 매수 없음', pw.render(self.d, now=100))
        self.assertEqual(__import__('json').loads((self.d / pw.STATE).read_text())['since'], 100)

    def test_not_paused_before_20(self):
        self.write([order(0, 'KRW-A', 'bid', 10, 10000), order(10, 'KRW-A', 'ask', 10, 9000)])
        rows, now_p, _ = pw.evaluate(pw.trips(self.d / 'exchange_orders.jsonl'), since=0, now=100)
        self.assertFalse(rows[0]['paused']); self.assertFalse(now_p)


def days(closes):
    return [{'t': f'2026-01-{i + 1:02d}T00:00:00+00:00', 'c': c} for i, c in enumerate(closes)]


class Trend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_signal(self):
        self.assertIsNone(ts.signal(days([1] * 10)))
        self.assertTrue(ts.signal(days([100] * 49 + [110]))[1])
        self.assertFalse(ts.signal(days([100] * 49 + [90]))[1])

    def test_update_buys_above_and_once_per_day(self):
        up = days([100] * 49 + [110]); calls = []
        def daily(m):
            calls.append(m); return up
        now = dt.datetime(2026, 11, 13, 10, tzinfo=KST).timestamp()
        s = ts.update(self.d, daily, lambda m: 110.0, now=now)
        self.assertTrue(all(sl['held'] for sl in s['sleeves'].values()))
        self.assertAlmostEqual(s['sleeves']['KRW-BTC']['qty'], ts.SLEEVE * (1 - ts.COST) / 110)
        ts.update(self.d, daily, lambda m: 120.0, now=now + 3600)      # 같은 날: 일봉 다시 안 읽음
        self.assertEqual(len(calls), 2)
        down = days([100] * 49 + [90])
        s = ts.update(self.d, lambda m: down, lambda m: 90.0, now=now + 86400)
        self.assertFalse(s['sleeves']['KRW-ETH']['held'])
        self.assertEqual(len(s['log']), 4)
        html = ts.render(self.d, {'KRW-BTC': 90.0, 'KRW-ETH': 90.0}, bot_realized=-1234)
        self.assertIn('BTC 현금', html); self.assertIn('-1,234원', html)


if __name__ == '__main__':
    unittest.main()
