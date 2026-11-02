import unittest

import strategy as st
from shadow import proposal

CAPS = {'max_order_krw': 30000, 'max_total_buy_krw': 100000, 'max_sell_krw_per_plan': 300000}


def r(m, rank, rs):
    return {'market': m, 'rank': rank, 'rs': rs}


class Proposal(unittest.TestCase):
    p = st.Params(M=5, entry_top=3, coin_cap=1.0, total_cap=1.0)  # 고정 금액 한도만 시험(비율 한도는 RatioCaps에서)

    def test_weak_holdings_sold_and_strong_bought(self):
        ranked = [r('KRW-A', 1, .2), r('KRW-B', 2, .1), r('KRW-C', 3, .05), r('KRW-BTC', 4, .01), r('KRW-ETH', 9, -.02)]
        text, reasons = proposal({'KRW-BTC': 400000, 'KRW-ETH': 100000, 'KRW-ONG': 170000}, ranked, {'KRW-ONG': -.05},
                                 {'KRW-A', 'KRW-B', 'KRW-C'}, self.p, CAPS, 38000, 0)
        # A(상승 +20%)는 과열 추격 방지(15% 이상)로 보류, 다음 후보 B·C가 매수된다
        self.assertEqual(text, '실행 주문안: 매도 ONG 100%; 매도 ETH 100%; 매수 B 30000원; 매수 C 30000원')
        self.assertTrue(any('A 매수 보류(과열 추격 방지)' in x for x in reasons))

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


class RatioCaps(unittest.TestCase):
    def test_fixed_caps_released_but_ratio_caps_hold(self):
        big = {'max_order_krw': 10 ** 9, 'max_total_buy_krw': 10 ** 9, 'max_sell_krw_per_plan': 10 ** 9}
        ranked = [r('KRW-A', 1, .10), r('KRW-B', 2, .08), r('KRW-C', 3, .05)]
        p = st.Params(entry_top=3)  # coin_cap 25%, total_cap 50%
        text, reasons = proposal({}, ranked, {}, {'KRW-A', 'KRW-B', 'KRW-C'}, p, big, 1_000_000, 0)
        self.assertIn('매수 A 250000원', text)
        self.assertIn('매수 B 250000원', text)
        self.assertNotIn('매수 C', text)                       # 총투입 50% 도달: 세 번째는 금액 부족
        self.assertTrue(any('C 매수 불가' in x for x in reasons))

    def test_existing_holding_counts_toward_coin_cap(self):
        big = {'max_order_krw': 10 ** 9, 'max_total_buy_krw': 10 ** 9, 'max_sell_krw_per_plan': 10 ** 9}
        ranked = [r('KRW-A', 1, .10), r('KRW-B', 2, .05)]
        p = st.Params(entry_top=3)
        text, _ = proposal({'KRW-A': 200000}, ranked, {}, {'KRW-A', 'KRW-B'}, p, big, 800_000, 0)
        # 이미 유지 중인 A는 신규 매수 후보가 아님(keep). B만 매수: 계좌 100만 → 종목 25% = 250,000
        self.assertIn('매수 B 250000원', text)
