import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

STAGED = Path(__file__).resolve().parent
sys.path.insert(0, 'C:/Users/User/Desktop/ai-trade/v5')
spec = importlib.util.spec_from_file_location('staged_live_entry_review', STAGED / 'live.py')
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


class SamePlanReceiver(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.worker = live.Live(self.temp.name, {'enabled': True}, live.st.Params())
        self.markets = {'KRW-A': {'warning': False}}

    def test_conflicting_old_plan_is_rejected_before_any_account_or_order_call(self):
        with patch.object(live.upbit, 'accounts', side_effect=AssertionError('unexpected account call')), \
             patch.object(self.worker, '_trade', side_effect=AssertionError('unexpected order')):
            result = self.worker.execute(('old', '매도 A 100%; 매수 A 10000원'), self.markets)
        self.assertIn('종목 충돌', result)
        self.assertEqual(self.worker.d['positions'], {})

    def test_actual_recent_sale_blocks_old_producer_snapshot(self):
        self.worker.d['last_sell'] = {'KRW-A': {'time': 1000., 'price': 100., 'profit': False}}
        with patch.object(live.time, 'time', return_value=1100), \
             patch.object(live.upbit, 'accounts', side_effect=AssertionError('unexpected account call')):
            why = self.worker.buy_block('KRW-A', 10000, self.markets)
        self.assertIn('재진입 대기', why)

    def test_profit_sale_after_wait_still_requires_existing_dip_rule_at_actual_ask(self):
        self.worker.d['last_sell'] = {'KRW-A': {'time': 1000., 'price': 100., 'profit': True}}
        with patch.object(live.time, 'time', return_value=10000), \
             patch.object(live.upbit, 'accounts', return_value=[{'currency': 'KRW', 'balance': '20000'}]), \
             patch.object(live.upbit, 'orderbooks', return_value={'KRW-A': {'ask': 100., 'bid': 99.9}}), \
             patch.object(live.upbit, 'completed_candles', return_value=[]), \
             patch.object(live.st, 'wilder_atr', return_value=2.):
            why = self.worker.buy_block('KRW-A', 10000, self.markets)
        self.assertIn('눌려야', why)


if __name__ == '__main__':
    unittest.main()
