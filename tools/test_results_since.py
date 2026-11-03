import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import results_since as rs

KST = timezone(timedelta(hours=9))


def row(t, side, market, volume, funds, fee, reason=''):
    return {'time': t, 'side': side, 'market': market, 'volume': volume, 'funds': funds, 'fee': fee, 'state': 'done', 'reason': reason}


class Replay(unittest.TestCase):
    def test_profit_and_loss_use_average_cost_with_fees(self):
        rows = [row('2026-11-04T09:00:00+09:00', '매수', 'KRW-A', 10, 1000, 0.5),
                row('2026-11-04T10:00:00+09:00', '매도', 'KRW-A', 10, 1100, 0.55, '순환'),
                row('2026-11-04T09:00:00+09:00', '매수', 'KRW-B', 10, 1000, 0.5),
                row('2026-11-04T11:00:00+09:00', '매도', 'KRW-B', 10, 900, 0.45)]
        closed, open_pos = rs.replay(rows, datetime(2026, 11, 4, 8, 0, tzinfo=KST))
        self.assertEqual(len(closed), 2)
        a = next(c for c in closed if c['market'] == 'KRW-A')
        self.assertAlmostEqual(a['pnl'], 1100 - 0.55 - 1000.5)
        self.assertAlmostEqual(a['hold_h'], 1.0)
        self.assertEqual(open_pos, {})

    def test_only_sales_after_since_are_counted_but_older_buys_set_the_cost(self):
        rows = [row('2026-11-04T07:00:00+09:00', '매수', 'KRW-A', 10, 1000, 0),
                row('2026-11-04T09:00:00+09:00', '매도', 'KRW-A', 10, 1200, 0)]
        closed, _ = rs.replay(rows, datetime(2026, 11, 4, 8, 33, tzinfo=KST))
        self.assertEqual(len(closed), 1)
        self.assertAlmostEqual(closed[0]['pnl'], 200)
        closed, _ = rs.replay(rows, datetime(2026, 11, 4, 9, 30, tzinfo=KST))
        self.assertEqual(closed, [])

    def test_partial_sale_keeps_remaining_position_and_unknown_cost_sales_are_skipped(self):
        rows = [row('2026-11-04T09:00:00+09:00', '매수', 'KRW-A', 10, 1000, 0),
                row('2026-11-04T10:00:00+09:00', '매도', 'KRW-A', 4, 500, 0),
                row('2026-11-04T10:30:00+09:00', '매도', 'KRW-X', 5, 999, 0)]
        closed, open_pos = rs.replay(rows, datetime(2026, 11, 4, 8, 0, tzinfo=KST))
        self.assertEqual(len(closed), 1)
        self.assertAlmostEqual(closed[0]['pnl'], 500 - 400)
        self.assertAlmostEqual(open_pos['KRW-A'][0], 6)
        self.assertAlmostEqual(open_pos['KRW-A'][1], 600)

    def test_summary_text_has_totals_and_warning_free_of_prices_when_unavailable(self):
        rows = [row('2026-11-04T09:00:00+09:00', '매수', 'KRW-A', 10, 10000, 5),
                row('2026-11-04T10:00:00+09:00', '매도', 'KRW-A', 10, 10500, 5)]
        closed, open_pos = rs.replay(rows, datetime(2026, 11, 4, 8, 0, tzinfo=KST))
        text = rs.summarize(closed, open_pos, {})
        self.assertIn('청산 1건', text)
        self.assertIn('이익 1 / 손실 0', text)


if __name__ == '__main__':
    unittest.main()
