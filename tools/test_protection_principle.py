import sys
import unittest
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protection_principle as P

BASE = {'holdings': {'BTC': {'qty': 0.01, 'avg': 1}, 'GTC': {'qty': 0.07, 'avg': 1}}}


def o(market, side, vol, ident=''):
    return {'market': market, 'side': side, 'executed_volume': str(vol), 'identifier': ident}


class Judge(unittest.TestCase):
    def test_user_principle(self):
        orders = {'1': o('KRW-DOGE', 'bid', 5000), '2': o('KRW-FLOCK', 'bid', 1500, 'v5-a'),
                  '3': o('KRW-BTC', 'ask', 0.01, 'v5-b'), '4': o('KRW-SUI', 'bid', 2)}
        balances = {'KRW-DOGE': D(5000), 'KRW-FLOCK': D(1500), 'KRW-GTC': D('0.07'), 'KRW-SUI': D(2), 'KRW-BTC': D(0)}
        prices = {'KRW-DOGE': 100, 'KRW-FLOCK': 50, 'KRW-GTC': 300, 'KRW-SUI': 1100}
        keep, _ = P.judge(BASE, orders, balances, prices)
        self.assertEqual(keep, {'KRW-DOGE'})       # 직접 거래·5천 원 초과만. 봇 매수 FLOCK, 자투리, 미보유 BTC 제외

    def test_bot_sells_do_not_reduce_direct_and_mixed_holding_counts_only_direct_part(self):
        orders = {'1': o('KRW-X', 'bid', 100), '2': o('KRW-X', 'bid', 100, 'v5-a')}
        keep, rows = P.judge({'holdings': {}}, orders, {'KRW-X': D(200)}, {'KRW-X': 40})
        self.assertEqual(keep, set())                 # 직접 분 100개×40원 = 4,000원 ≤ 5,000원
        keep, _ = P.judge({'holdings': {}}, orders, {'KRW-X': D(200)}, {'KRW-X': 60})
        self.assertEqual(keep, {'KRW-X'})             # 6,000원 > 5,000원

    def test_exactly_5000_and_no_price_not_protected(self):
        keep, _ = P.judge({'holdings': {}}, {'1': o('KRW-X', 'bid', 50)}, {'KRW-X': D(50), 'KRW-Y': D(1)}, {'KRW-X': 100})
        self.assertEqual(keep, set())

    def test_baseline_holding_counts_as_direct(self):
        keep, _ = P.judge({'holdings': {'ETH': {'qty': 0.1, 'avg': 1}}}, {}, {'KRW-ETH': D('0.1')}, {'KRW-ETH': 3000000})
        self.assertEqual(keep, {'KRW-ETH'})


if __name__ == '__main__':
    unittest.main()
