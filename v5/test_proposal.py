import unittest

import strategy as st
from shadow import proposal

CAPS = {'max_order_krw': 30000, 'max_total_buy_krw': 100000, 'max_sell_krw_per_plan': 300000}


def r(m, rank, rs):
    return {'market': m, 'rank': rank, 'rs': rs}


class Proposal(unittest.TestCase):
    p = st.Params(M=5, entry_top=3)

    def test_weak_holdings_sold_and_strong_bought(self):
        ranked = [r('KRW-A', 1, .2), r('KRW-B', 2, .1), r('KRW-C', 3, .05), r('KRW-BTC', 4, .01), r('KRW-ETH', 9, -.02)]
        text, _ = proposal({'KRW-BTC': 400000, 'KRW-ETH': 100000, 'KRW-ONG': 170000}, ranked, {'KRW-ONG': -.05},
                           {'KRW-A', 'KRW-B', 'KRW-C'}, self.p, CAPS, 38000, 0)
        self.assertEqual(text, '실행 주문안: 매도 ONG 100%; 매도 ETH 100%; 매수 A 30000원; 매수 B 30000원; 매수 C 30000원')

    def test_sell_cap_keeps_strongest_of_the_weak(self):
        ranked = [r('KRW-X', 10, -.01), r('KRW-Y', 11, -.03)]
        text, reasons = proposal({'KRW-X': 250000, 'KRW-Y': 250000}, ranked, {}, set(), self.p, CAPS, 0, 0)
        self.assertEqual(text, '실행 주문안: 매도 Y 100%; 매수 없음')
        self.assertTrue(any('X 매도 보류' in x for x in reasons))

    def test_nothing_to_do(self):
        text, _ = proposal({'KRW-A': 100000}, [r('KRW-A', 1, .1)], {}, set(), self.p, CAPS, 0, 100000)
        self.assertEqual(text, '실행 주문안: 매도 없음; 매수 없음')


if __name__ == '__main__':
    unittest.main()
