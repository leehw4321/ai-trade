"""Profit giveback protection: synthetic prices and mocked order transport only."""
import math
import tempfile
import unittest
from unittest.mock import patch

import live
import strategy as st


class ProfitGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lv = live.Live(self.tmp.name, {'enabled': True, 'profit_guard_enabled': True,
                                          'trail_enabled': True}, st.Params())
        self.pos = {'entry': 100., 'qty': 100., 'cost': 10000., 'stop': 90.,
                    'stop0': 90., 'trail': True, 'time': 't', 'peak': 140.}
        self.lv.d['positions']['KRW-X'] = self.pos

    def test_old_peak_is_not_retroactively_armed(self):
        self.lv.update_profit_guard({'KRW-X': 100.1})
        self.assertEqual(self.pos['profit_guard_peak'], 100.1)
        self.assertNotIn('profit_guard_stop', self.pos)

    def test_arm_below_atr_threshold_preserves_sixty_percent_gain(self):
        self.lv.update_profit_guard({'KRW-X': 103.})
        self.assertAlmostEqual(self.pos['profit_guard_stop'], 101.8)

    def test_peak_and_stop_never_decline_and_survive_reload(self):
        self.lv.update_profit_guard({'KRW-X': 105.})
        self.lv.update_profit_guard({'KRW-X': 101.})
        again = live.Live(self.tmp.name, self.lv.cfg, self.lv.p)
        pos = again.d['positions']['KRW-X']
        self.assertEqual(pos['profit_guard_peak'], 105.)
        self.assertEqual(pos['profit_guard_stop'], 103.)

    def test_cost_floor_uses_market_specific_cost(self):
        self.lv.cfg['profit_guard_giveback'] = 0.99
        self.lv.update_profit_guard({'KRW-X': 102.})
        self.assertAlmostEqual(self.pos['profit_guard_stop'],
                               100 * (1 + 2*self.lv.p.fee + st.cost('KRW-X', self.lv.p)))

    def test_disabled_does_not_activate_or_use_saved_guard(self):
        self.lv.cfg['profit_guard_enabled'] = False
        self.pos['profit_guard_stop'] = 110.
        self.lv.update_profit_guard({'KRW-X': 120.})
        self.assertNotIn('profit_guard_peak', self.pos)
        self.assertEqual(self.lv.stop_limit(self.pos), 90.)

    def test_raised_trail_stop_is_checked_in_real_time(self):
        # 사용자 지시(2026-11-04): 손절 기준을 넘으면 인식 즉시 매도. 올라간 손절가도 실시간 평가한다.
        self.pos.update(stop=120., profit_guard_stop=102.)
        self.assertEqual(self.lv.stop_limit(self.pos), 120.)

    def test_invalid_quotes_do_not_arm(self):
        for value in (math.nan, math.inf, 0, -1, True, None):
            self.lv.update_profit_guard({'KRW-X': value})
        self.assertNotIn('profit_guard_stop', self.pos)

    def test_invalid_config_cannot_break_fixed_stop_protection(self):
        for value in ('bad', None, True, math.nan, math.inf):
            self.lv.cfg['profit_guard_arm_pct'] = value
            self.lv.update_profit_guard({'KRW-X': 105.})
        self.assertNotIn('profit_guard_stop', self.pos)

    def test_rebased_stop0_never_decreases_and_runs_only_once(self):
        self.lv.p.stop_max_pct = .04
        self.pos.update(stop=95., stop0=90., stop_rebased=True)
        self.lv.rebase_adopted_stops({'KRW-X': 100.})
        self.assertEqual(self.pos['stop0'], 96.)
        self.lv.rebase_adopted_stops({'KRW-X': 110.})
        self.assertEqual(self.pos['stop0'], 96.)

    def test_gap_exit_reuses_risk_order_path(self):
        self.lv.update_profit_guard({'KRW-X': 105.})
        with patch.object(self.lv, 'recover_all_pending'), patch.object(self.lv, 'rebase_adopted_stops'), \
             patch.object(live.upbit, 'accounts', return_value=[{'currency': 'X', 'balance': '100'}]), \
             patch.object(self.lv, '_trade', return_value={}) as trade, \
             patch.object(self.lv, '_fill', return_value=(100., 10200., 5.)), patch.object(self.lv, 'share'):
            self.lv.stop_check({'KRW-X': 102.})
        trade.assert_called_once_with('ask', 'KRW-X', 100., '이익보호', risk=True)

    def test_uncertain_order_suppresses_duplicate_exit(self):
        self.lv.update_profit_guard({'KRW-X': 105.})
        with patch.object(self.lv, 'recover_all_pending'), patch.object(self.lv, 'rebase_adopted_stops'), \
             patch.object(self.lv, 'market_has_uncertain_order', return_value=True), \
             patch.object(self.lv, '_trade') as trade, patch.object(live.upbit, 'accounts') as accounts:
            self.lv.stop_check({'KRW-X': 102.})
        trade.assert_not_called()
        accounts.assert_not_called()

    def test_additional_fill_keeps_guard_and_uses_remaining_average_cost(self):
        self.lv.update_profit_guard({'KRW-X': 105.})
        self.lv.d['pending_order'] = {'identifier': 'synthetic', 'side': 'bid', 'market': 'KRW-X',
                                      'atr': 1., 'time': 't', 'reason': 'synthetic additional fill'}
        self.lv._apply_pending({'state': 'done', 'executed_volume': '10', 'paid_fee': '0',
                               'trades': [{'volume': '10', 'funds': '1200'}]})
        pos = self.lv.d['positions']['KRW-X']
        self.assertEqual(pos['profit_guard_stop'], 103.)
        self.lv.update_profit_guard({'KRW-X': 104.})
        average = 11200 / 110
        self.assertAlmostEqual(pos['profit_guard_stop'], average + (105-average)*.6)

    def test_partial_exit_preserves_guard_for_remaining_quantity(self):
        self.lv.update_profit_guard({'KRW-X': 105.})
        self.lv.d['pending_order'] = {'identifier': 'synthetic', 'side': 'ask', 'market': 'KRW-X',
                                      'reason': '이익보호', 'time': 't'}
        self.lv._apply_pending({'state': 'cancel', 'executed_volume': '40', 'paid_fee': '2',
                               'trades': [{'volume': '40', 'funds': '4120'}]})
        pos = self.lv.d['positions']['KRW-X']
        self.assertEqual(pos['qty'], 60.)
        self.assertEqual(pos['profit_guard_stop'], 103.)


if __name__ == '__main__':
    unittest.main()
