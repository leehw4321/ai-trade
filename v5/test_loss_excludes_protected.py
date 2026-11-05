import tempfile
import unittest
from pathlib import Path
from unittest import mock

import live
import strategy as st

COST = 500000.0


def make(tmp, pct=0.0):
    lv = live.Live(Path(tmp), {'loss_stop_krw': 15000, 'loss_stop_pct': pct}, st.Params())
    lv.d['positions'] = {'KRW-BTC': {'qty': 1.0, 'cost': COST, 'stop': 1.0, 'time': 't'},
                         'KRW-A': {'qty': 1.0, 'cost': 100000.0, 'stop': 1.0, 'time': 't'}}
    return lv


def px(btc, a=100000.0):
    k = 1 / (1 - 0.0005)
    return {'KRW-BTC': btc * k, 'KRW-A': a * k}


class LossExcludesProtected(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lv = make(self.tmp.name)
        p = mock.patch.object(self.lv, 'protected_markets', return_value={'KRW-BTC'})
        p.start()
        self.addCleanup(p.stop)

    def test_protected_price_drop_does_not_count(self):
        self.assertEqual(self.lv.check_loss(px(COST)), 0)
        self.assertEqual(self.lv.check_loss(px(COST * 0.9)), 0)       # 보호 종목 -10%: 손실 0
        self.assertFalse(self.lv.d['halted'])

    def test_bot_position_loss_still_counts(self):
        self.lv.check_loss(px(COST))
        self.assertAlmostEqual(self.lv.check_loss(px(COST, 90000.0)), 10000.0)
        self.assertTrue(self.lv.check_loss(px(COST * 0.5, 80000.0)) >= 15000 and self.lv.d['halted'])

    def test_first_run_keeps_todays_loss_unchanged(self):
        self.lv.d['risk_baseline_unrealized'] = 5000.0
        self.lv.d['risk_loss_offset'] = 3000.0
        prices = px(COST * 0.98, 95000.0)                            
        old_today = self.lv.raw_loss(prices, False) - 3000.0
        self.assertAlmostEqual(self.lv.check_loss(prices), old_today)  # 전환 순간에는 값이 그대로
        self.assertEqual(self.lv.d['loss_scope'], 'bot_only')
        self.assertAlmostEqual(self.lv.check_loss(px(COST * 0.90, 95000.0)), old_today)  # 이후 보호 종목 변동은 무시

    def test_resume_before_first_check_is_consistent(self):
        self.lv.d['realized'] = -16000.0
        self.lv.d['halted'] = 'x'
        ok, _ = self.lv.resume_buying(px(COST, 100000.0))
        self.assertTrue(ok)
        self.assertEqual(self.lv.check_loss(px(COST * 0.8, 100000.0)), 0)

    def test_unreadable_protection_list_uses_last_known(self):
        self.lv.check_loss(px(COST))
        with mock.patch.object(self.lv, 'protected_markets', side_effect=live.ProtectedAssetError('x')):
            self.assertEqual(self.lv.check_loss(px(COST * 0.9)), 0)    # 마지막으로 읽은 목록 사용


if __name__ == '__main__':
    unittest.main()
