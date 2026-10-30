import tempfile
import time
import unittest
from unittest.mock import patch

import adaptive_model as model
from trend_engine import Engine


class RegimeEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.e=Engine(self.tmp.name,network=False)
        self.quote()

    def tearDown(self):
        self.tmp.cleanup()

    def quote(self,bid=100000,ask=100050):
        self.e.quotes={m:{'bid':bid,'ask':ask,'spread':(ask-bid)/ask,'time':time.time(),
                          'units':[{'ask_price':ask,'ask_size':1000}]} for m in ('KRW-BTC','KRW-ETH')}

    def own_position(self):
        self.e.order('paper','KRW-BTC','bid',100000,1000)

    def candidate(self):
        self.e.signals={'KRW-BTC':{'ok':True,'id':'new','time':time.time(),'closed_at':time.time()-1,
                                  'atr':1000,'close':100000,'breakout':99900}}

    def test_unknown_regime_cannot_enter_even_with_base_signal(self):
        self.candidate()
        self.e.enter('paper')
        self.assertFalse(self.e.states['paper']['positions'])

    def test_each_non_rising_phase_blocks_new_buys(self):
        self.candidate()
        for phase in ('range','fall','surge','crash'):
            self.e.policy_snapshot=model.policy(phase,'test',time.time(),hour_bar='x')
            self.e.enter('paper')
            self.assertFalse(self.e.states['paper']['positions'])

    def test_confirmed_fall_liquidates_only_bot_owned_paper_position(self):
        self.own_position()
        self.e.policy_snapshot=model.policy('fall','test',time.time())
        self.e.manage('paper')
        self.assertFalse(self.e.states['paper']['positions'])
        self.assertIn('하락',self.e.states['paper']['trades'][-1]['reason'])
        self.assertFalse(self.e.states['live']['positions'])

    def test_surge_tightens_stop_and_never_loosens_after_recovery(self):
        self.own_position()
        self.quote(104000,104050)
        self.e.policy_snapshot=model.policy('surge','test',time.time())
        self.e.manage('paper')
        p=self.e.states['paper']['positions']['KRW-BTC']
        self.assertEqual(p['stop'],102000)
        self.e.policy_snapshot=model.policy('rise','test',time.time())
        self.e.manage('paper')
        self.assertEqual(p['stop'],102000)

    def test_intrabar_btc_shock_overrides_old_rising_signal(self):
        now=time.time()
        self.e.cache[('KRW-BTC',60)]=[{'t':now-3700,'c':110000}]
        self.e.policy_snapshot=model.policy('rise','test',now)
        p=self.e.trade_policy('paper',record=True)
        self.assertEqual(p['phase'],'crash')
        self.assertGreater(p['embargo_until'],now+86390)

    def test_stale_quote_never_triggers_emergency_sell(self):
        now=time.time()
        self.e.cache[('KRW-BTC',60)]=[{'t':now-3700,'c':110000}]
        self.e.quotes['KRW-BTC']['time']-=20
        self.e.policy_snapshot=model.policy('rise','test',now)
        self.assertEqual(self.e.trade_policy('paper')['phase'],'rise')

    def test_panic_embargo_survives_restart_and_mode_switch(self):
        self.e.policy_snapshot=model.policy('crash','test',time.time(),hour_bar=123)
        self.e.trade_policy('paper',record=True)
        e2=Engine(self.tmp.name,network=False)
        e2.policy_snapshot=model.policy('rise','test',time.time())
        self.assertEqual(e2.trade_policy('live')['phase'],'crash')
        e2.reset_paper()
        self.assertEqual(e2.trade_policy('paper')['phase'],'crash')

    def test_same_panic_bar_does_not_extend_embargo_each_poll(self):
        self.e.policy_snapshot=model.policy('crash','test',time.time(),hour_bar=123)
        self.e.trade_policy('paper',record=True)
        until=self.e.regime_guard['embargo_until']
        self.e.trade_policy('live',record=True)
        self.assertEqual(self.e.regime_guard['embargo_until'],until)

    def test_old_policy_blocks_new_entry(self):
        self.e.policy_snapshot=model.policy('rise','test',time.time()-100)
        self.candidate()
        self.e.enter('paper')
        self.assertFalse(self.e.states['paper']['positions'])

    def test_live_start_rejected_when_economic_validation_failed(self):
        self.e.mode='live'
        with patch.object(self.e,'model_validation',return_value={'live_entry_approved':False}):
            with self.assertRaises(RuntimeError):
                self.e.start()
        self.assertFalse(self.e.running)
        self.assertIsNone(self.e.broker)

    def test_pending_order_snapshots_do_not_cause_repeat_trades(self):
        self.e.policy_snapshot=model.policy('crash','test',time.time(),hour_bar=123)
        for _ in range(3):
            self.e.snapshot()
        self.assertFalse(self.e.regime_guard['event'])  # reading is side effect free


if __name__=='__main__':
    unittest.main()
