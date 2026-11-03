"""D-56 실현손익 계산 테스트 (네트워크 없음)."""
import datetime as dt
from decimal import Decimal as D
import unittest
from unittest import mock

import pnl

BASE = {'start': '2026-11-02T00:00:00+09:00', 'source': 't', 'krw': 0, 'holdings': {}}


def order(uid, side, trades, fee, ident='v5-x', market='KRW-X', state='done'):
    return {'uuid': uid, 'market': market, 'side': side, 'state': state, 'created_at': trades[0][0], 'paid_fee': fee,
            'identifier': ident, 'executed_volume': sum(t[1] for t in trades),
            'trades': [{'created_at': t, 'volume': v, 'funds': f} for t, v, f in trades]}


def realize(orders, base=BASE):
    return pnl.rebuild(base, pnl.fills({o['uuid']: o for o in orders}))


class Cost(unittest.TestCase):
    def test_independent_example_fee_counted_once(self):
        pos, sells = realize([order('b', 'bid', [('2026-11-03T10:00:00+09:00', 1, 100)], 1),
                              order('s', 'ask', [('2026-11-03T11:00:00+09:00', 1, 110)], 1)])
        self.assertEqual(sells[0]['realized'], D(8))
        self.assertEqual(pos['X']['qty'], 0)

    def test_moving_average_partial_sell(self):
        pos, sells = realize([order('b1', 'bid', [('2026-11-03T10:00:00+09:00', 1, 100)], 0),
                              order('b2', 'bid', [('2026-11-03T10:05:00+09:00', 1, 200)], 0),
                              order('s', 'ask', [('2026-11-03T11:00:00+09:00', 1, 180)], 0)])
        self.assertEqual(sells[0]['realized'], D(30))          # 평균 150
        self.assertEqual(pos['X']['cost'], D(150))

    def test_cancel_state_market_buy_uses_trades_and_fee_split(self):
        o = order('b', 'bid', [('2026-11-03T10:00:00+09:00', 1, 100), ('2026-11-03T10:00:01+09:00', 1, 300)], 4, state='cancel')
        ev = pnl.fills({'b': o})
        self.assertEqual([e['fee'] for e in ev], [D(1), D(3)])

    def test_baseline_unknown_cost_and_shortage_are_unconfirmed(self):
        base = dict(BASE, holdings={'X': {'qty': 10, 'avg': 0}, 'Y': {'qty': 1, 'avg': 50}})
        _, sells = realize([order('s1', 'ask', [('2026-11-03T11:00:00+09:00', 5, 500)], 0),
                            order('s2', 'ask', [('2026-11-03T11:00:00+09:00', 3, 300)], 0, market='KRW-Y')], base)
        self.assertEqual([s['realized'] for s in sells], [None, None])
        self.assertEqual([s['why'] for s in sells], ['원가 불명', '수량 부족'])


class Periods(unittest.TestCase):
    def test_fill_time_not_order_time_and_week_month(self):
        at = dt.datetime(2026, 11, 3, 12, 0, tzinfo=pnl.KST)        # 화요일
        orders = [order('b', 'bid', [('2026-11-02T09:00:00+09:00', 2, 200)], 0),
                  order('s', 'ask', [('2026-11-02T23:59:50+09:00', 1, 150), ('2026-11-03T00:00:10+09:00', 1, 150)], 0)]
        orders[1]['created_at'] = '2026-11-02T23:59:40+09:00'
        ev = pnl.fills({o['uuid']: o for o in orders})
        _, sells = pnl.rebuild(BASE, ev)
        starts = pnl.period_starts(at)
        agg = pnl.aggregate(sells, ev, starts)
        self.assertEqual(agg['오늘']['realized'], 50.0)                  # 자정 뒤 체결분만
        self.assertEqual(agg['이번 주']['realized'], 100.0)              # 월요일부터
        self.assertEqual(agg['이번 달']['sell_orders'], 1)
        self.assertEqual(starts['이번 주'].date(), dt.date(2026, 11, 2))

    def test_source_split_by_sell_order_identifier(self):
        orders = [order('b', 'bid', [('2026-11-03T09:00:00+09:00', 2, 200)], 0),
                  order('s1', 'ask', [('2026-11-03T10:00:00+09:00', 1, 120)], 0, ident='v5-a'),
                  order('s2', 'ask', [('2026-11-03T10:30:00+09:00', 1, 90)], 0, ident='')]
        ev = pnl.fills({o['uuid']: o for o in orders})
        _, sells = pnl.rebuild(BASE, ev)
        agg = pnl.aggregate(sells, ev, {'누적': pnl.kst(BASE['start'])})['누적']
        self.assertEqual(agg['by_source'], {'V5 식별 주문': 20.0, '출처 불명(수동 등)': -10.0})
        self.assertEqual((agg['wins'], agg['losses']), (1, 1))


class Reconcile(unittest.TestCase):
    def test_mismatch_detected_and_dust_hidden(self):
        pos = {'X': {'qty': D(2), 'cost': D(200), 'known': True}, 'Z': {'qty': D('0.00000001'), 'cost': D(0), 'known': True}}
        accounts = [{'currency': 'X', 'balance': '1', 'locked': '1'}, {'currency': 'Y', 'balance': '5', 'locked': '0'}]
        rows, total, mismatch = pnl.unrealized(pos, accounts, {'KRW-X': 150000})
        self.assertEqual(mismatch, ['Y'])               # 0.00000001 차이는 반올림 오차로 본다
        self.assertEqual(total, 2 * 150000 - 200)


class Paging(unittest.TestCase):
    def test_full_page_continues_and_dedupes(self):
        t0 = dt.datetime(2026, 11, 3, 0, 0, tzinfo=pnl.KST)
        page1 = [{'uuid': f'a{i}', 'created_at': (t0 + dt.timedelta(minutes=1000 - i)).isoformat()} for i in range(1000)]
        page2 = [page1[-1], {'uuid': 'z', 'created_at': t0.isoformat()}]
        with mock.patch.object(pnl, '_get', side_effect=[page1, page2]), mock.patch('time.sleep'):
            rows = pnl.closed_between(t0, t0 + dt.timedelta(days=1))
        self.assertEqual(len(rows), 1001)


if __name__ == '__main__':
    unittest.main()
