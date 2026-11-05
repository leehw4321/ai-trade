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

    def test_bot_sell_of_baseline_then_bot_rebuy_is_not_direct(self):
        # 실제 사례(ETH 11-05): 기초 0.161개를 봇이 전부 매도 → 봇이 0.0245개 재매수
        orders = {'1': dict(o('KRW-ETH', 'ask', '0.16101555', 'v5-a'), created_at='2026-11-02T22:28:47+09:00'),
                  '2': dict(o('KRW-ETH', 'bid', '0.01230968', 'v5-b'), created_at='2026-11-04T07:55:00+09:00'),
                  '3': dict(o('KRW-ETH', 'ask', '0.01230968', 'v5-c'), created_at='2026-11-04T09:01:59+09:00'),
                  '4': dict(o('KRW-ETH', 'bid', '0.02453235', 'v5-d'), created_at='2026-11-05T03:01:48+09:00')}
        base = {'holdings': {'ETH': {'qty': 0.16101555, 'avg': 1}}}
        keep, _ = P.judge(base, orders, {'KRW-ETH': D('0.02453235')}, {'KRW-ETH': 3270000})
        self.assertEqual(keep, set())

    def test_bot_selling_its_own_coins_keeps_direct(self):
        orders = {'1': dict(o('KRW-X', 'bid', 100, 'v5-a'), created_at='1'),
                  '2': dict(o('KRW-X', 'ask', 100, 'v5-b'), created_at='2')}
        keep, _ = P.judge({'holdings': {'X': {'qty': 100, 'avg': 1}}}, orders, {'KRW-X': D(100)}, {'KRW-X': 100})
        self.assertEqual(keep, {'KRW-X'})

    def test_mixed_holding_counts_only_direct_part(self):
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
