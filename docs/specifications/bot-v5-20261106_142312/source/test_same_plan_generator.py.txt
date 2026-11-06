"""Offline staged generator review; never imports or changes the running copy."""
import importlib.util
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

STAGED = Path(__file__).resolve().parent
CANONICAL = STAGED
sys.path.insert(0, str(CANONICAL))
spec = importlib.util.spec_from_file_location('staged_shadow_entry_review', STAGED / 'shadow.py')
shadow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shadow)
st = shadow.st


class SamePlanGenerator(unittest.TestCase):
    CAPS = {'max_order_krw': 10 ** 9, 'max_total_buy_krw': 10 ** 9,
            'max_sell_krw_per_plan': 10 ** 9}

    def setUp(self):
        # Any unexpected account/price/order access fails this offline review.
        for method in ('accounts', 'tickers', 'orderbooks', 'market_buy', 'market_sell'):
            block = patch.object(shadow.upbit, method, side_effect=AssertionError('unexpected external call'))
            block.start()
            self.addCleanup(block.stop)
        self.params = st.Params(M=5, entry_want=3, entry_scan=8,
                                coin_cap=1., total_cap=1.)
        self.ranked = [{'market': f'KRW-HOT{i}', 'rank': i, 'rs': .2} for i in range(1, 6)]
        self.ranked += [{'market': 'KRW-SIX', 'rank': 6, 'rs': .1},
                        {'market': 'KRW-SEVEN', 'rank': 7, 'rs': .09},
                        {'market': 'KRW-EIGHT', 'rank': 8, 'rs': .08}]
        self.eligible = {row['market'] for row in self.ranked}

    def plan(self, holdings, ranked=None, eligible=None, rs_out=None, params=None):
        return shadow.proposal(holdings, self.ranked if ranked is None else ranked,
            {} if rs_out is None else rs_out, self.eligible if eligible is None else eligible,
            self.params if params is None else params, self.CAPS, 990_000, 0)

    def test_rank_six_queued_sale_does_not_reenter_same_scan_eight_plan(self):
        text, reasons = self.plan({'KRW-SIX': 10_000})
        self.assertIn('매도 SIX 100%', text)
        self.assertNotIn('매수 SIX ', text)
        self.assertTrue(any('SIX 동일 주문안 매도 예정' in reason for reason in reasons))
        # The approved scan-eight policy remains intact: this fix does not add rank<=M.
        self.assertIn('매수 SEVEN ', text)
        self.assertIn('매수 EIGHT ', text)

    def test_multiple_queued_sales_have_no_buy_side_intersection(self):
        text, _ = self.plan({'KRW-SIX': 9_000, 'KRW-EIGHT': 10_000})
        sells = set(re.findall(r'매도 ([A-Z0-9]+) 100%', text))
        buys = set(re.findall(r'매수 ([A-Z0-9]+) \d+원', text))
        self.assertEqual(sells, {'SIX', 'EIGHT'})
        self.assertFalse(sells & buys)
        self.assertEqual(buys, {'SEVEN'})

    def test_dust_below_min_order_is_buyable_but_real_holding_is_not(self):
        ranked = [{'market': 'KRW-DUST', 'rank': 1, 'rs': .1},
                  {'market': 'KRW-NEW', 'rank': 2, 'rs': .09}]
        text, _ = self.plan({'KRW-DUST': 1000}, ranked, {'KRW-DUST', 'KRW-NEW'})
        self.assertNotIn('매도 DUST', text)
        # 2026-11-06: 5,000원 미만 자투리는 보유로 보지 않는다(손절 뒤 남은 1e-08개가 재매수를 사유 없이 막았다). 진짜 보유는 아래 검사가 지킨다.
        self.assertIn('매수 DUST ', text)
        self.assertIn('매수 NEW ', text)
        text, _ = self.plan({'KRW-HELD': 50_000}, [{'market': 'KRW-HELD', 'rank': 1, 'rs': .1}], {'KRW-HELD'})
        self.assertNotIn('매수 HELD', text)

    def test_missing_data_holding_is_preserved_without_blocking_other_eligible(self):
        holdings = {'KRW-MISSING': 10_000}
        original = holdings.copy()
        ranked = [{'market': 'KRW-NEW', 'rank': 1, 'rs': .1}]
        text, reasons = self.plan(holdings, ranked, {'KRW-MISSING', 'KRW-NEW'})
        self.assertNotIn('매도 MISSING', text)
        self.assertNotIn('매수 MISSING', text)
        self.assertIn('매수 NEW ', text)
        self.assertTrue(any('MISSING 자료 누락' in reason for reason in reasons))
        self.assertEqual(holdings, original)


if __name__ == '__main__':
    unittest.main()
