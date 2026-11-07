import json
import tempfile
import unittest
from pathlib import Path

import rebuy_review as rr


def order(uuid, market, side, t, vol, price, fee=0.0, mine=True):
    return {'uuid': uuid, 'market': market, 'side': side, 'state': 'done', 'created_at': t, 'executed_volume': str(vol),
            'paid_fee': str(fee), 'identifier': ('v5-' if mine else 'x-') + uuid,
            'trades': [{'market': market, 'uuid': uuid + 't', 'price': str(price), 'volume': str(vol), 'funds': str(vol * price),
                        'created_at': t, 'side': side}]}


def write(orders):
    p = Path(tempfile.mkdtemp()) / 'orders.json'
    p.write_text(json.dumps({'orders': {o['uuid']: o for o in orders}}), encoding='utf-8')
    return p


def trip(n, market, start_hour, win=True):
    """청산이 이익인 왕복 하나(매수 100 → 매도 105 또는 95)."""
    d = '2026-11-08'
    return [order(f'b{n}', market, 'bid', f'{d}T{start_hour:02d}:00:00+09:00', 10, 100),
            order(f's{n}', market, 'ask', f'{d}T{start_hour:02d}:30:00+09:00', 10, 105 if win else 95)]


class RebuyReview(unittest.TestCase):
    def test_round_trip_pnl(self):
        p = write(trip(1, 'KRW-A', 1))
        t = rr.round_trips(p)
        self.assertEqual(len(t), 1)
        self.assertAlmostEqual(t[0]['pnl'], 50.0)

    def test_gap_filter_and_collecting(self):
        # 이익 왕복 뒤 6시간 지나 재매수(대상), 1시간 뒤 재매수(대상 아님)
        o = trip(1, 'KRW-A', 1) + [order('b2', 'KRW-A', 'bid', '2026-11-08T07:00:00+09:00', 10, 100),
                                  order('s2', 'KRW-A', 'ask', '2026-11-08T08:00:00+09:00', 10, 110)]
        o += trip(3, 'KRW-B', 1) + [order('b4', 'KRW-B', 'bid', '2026-11-08T02:00:00+09:00', 10, 100),
                                    order('s4', 'KRW-B', 'ask', '2026-11-08T03:00:00+09:00', 10, 110)]
        r = rr.evaluate(write(o), since='2026-11-08T00:00:00+09:00')
        self.assertEqual(r['n'], 1)
        self.assertEqual(r['state'], 'collecting')
        self.assertIsNone(rr.notice_text(r))

    def test_before_since_and_loss_exit_excluded(self):
        o = trip(1, 'KRW-A', 1, win=False) + [order('b2', 'KRW-A', 'bid', '2026-11-08T07:00:00+09:00', 10, 100),
                                              order('s2', 'KRW-A', 'ask', '2026-11-08T08:00:00+09:00', 10, 110)]
        self.assertEqual(rr.evaluate(write(o), since='2026-11-08T00:00:00+09:00')['n'], 0)   # 직전이 손실
        o = trip(1, 'KRW-A', 1) + [order('b2', 'KRW-A', 'bid', '2026-11-08T07:00:00+09:00', 10, 100),
                                  order('s2', 'KRW-A', 'ask', '2026-11-08T08:00:00+09:00', 10, 110)]
        self.assertEqual(rr.evaluate(write(o), since='2026-11-08T09:00:00+09:00')['n'], 0)   # 기준 시각 이전 진입

    def _many(self, n, win):
        o = []
        for i in range(n):
            m = f'KRW-C{i}'
            o += trip(i, m, 1) + [order(f'rb{i}', m, 'bid', '2026-11-08T07:00:00+09:00', 10, 100),
                                  order(f'rs{i}', m, 'ask', '2026-11-08T08:00:00+09:00', 10, 104 if win else 97)]
        return write(o)

    def test_states(self):
        keep = rr.evaluate(self._many(15, True), since='2026-11-08T00:00:00+09:00')
        self.assertEqual((keep['n'], keep['state']), (15, 'keep'))
        bad = rr.evaluate(self._many(15, False), since='2026-11-08T00:00:00+09:00')
        self.assertEqual(bad['state'], 'raise')
        t = rr.notice_text(bad)
        self.assertEqual(t[2], 'action')
        self.assertIn('재매수 점검 결과 보고', t[1])
        self.assertIn('승인한 뒤에만', t[1])

    def test_missing_file(self):
        self.assertEqual(rr.evaluate(Path(tempfile.mkdtemp()) / 'none.json')['n'], 0)


if __name__ == '__main__':
    unittest.main()
