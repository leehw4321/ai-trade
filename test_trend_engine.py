"""No real credentials or network. Fake exchange exercises order lifecycle."""
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from trend_engine import Engine, ApiError, evaluate, position_size
import adaptive_model


class FakeBroker:
    def __init__(self):
        self.orders = []
        self.rows = [{'currency': 'KRW', 'balance': '1500000', 'locked': '0'},
                     {'currency': 'BTC', 'balance': '.02', 'locked': '0'},
                     {'currency': 'ETH', 'balance': '1', 'locked': '0'}]
        self.response = {'state': 'wait'}
        self.submit_error = None
        self.get_error = None

    def get(self, path, params=None):
        if path == '/v1/accounts':
            return self.rows
        if path == '/v1/orders/chance':
            return {'bid_fee': '.0005', 'ask_fee': '.0005',
                    'market': {'bid': {'min_total': '5000'}, 'ask': {'min_total': '5000'}}}
        if path == '/v1/orders/open':
            return []
        if self.get_error:
            raise self.get_error
        return self.response

    def submit(self, order):
        self.orders.append(copy.deepcopy(order))
        if self.submit_error:
            raise self.submit_error


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.broker = FakeBroker()
        self.e = Engine(self.temp.name, broker=self.broker, network=False)
        self.quote()

    def tearDown(self):
        self.temp.cleanup()

    def quote(self, bid=100000, ask=100050):
        self.e.quotes = {m: {'bid': bid, 'ask': ask, 'spread': (ask-bid)/ask, 'time': time.time(),
                             'units': [{'ask_price': ask, 'ask_size': 1000}]} for m in ('KRW-BTC','KRW-ETH')}

    def signal(self):
        self.e.policy_snapshot=adaptive_model.policy('rise','test',time.time())
        self.e.signals = {'KRW-BTC': {'ok': True, 'id': 'signal1', 'close': 100000,
                                     'breakout': 99900, 'atr': 1000, 'time': time.time(), 'closed_at': time.time()-1}}

    def live(self):
        self.e.switch('live')

    def buy_pending(self):
        self.live()
        self.e.order('live', 'KRW-BTC', 'bid', 100000, 1000)

    def filled(self, side='bid', volume=1, funds=100000, fee=50):
        self.broker.response = {'state': 'done', 'executed_volume': str(volume), 'paid_fee': str(fee),
                                'trades': [{'volume': str(volume), 'price': str(funds/volume), 'funds': str(funds)}]}

    def test_opening_never_starts_live(self):
        self.assertEqual(self.e.mode, 'paper')
        self.assertFalse(self.e.running)
        self.assertEqual(self.broker.orders, [])

    def test_connect_is_read_only_preserves_existing_coins(self):
        self.e.connect()
        self.assertEqual(self.broker.orders, [])
        self.assertEqual(self.e.states['live']['baseline'], {'KRW-BTC': .02, 'KRW-ETH': 1})
        self.assertFalse(self.e.states['live']['positions'])

    def test_less_than_budget_blocks_live_not_api_probe(self):
        self.broker.rows[0]['balance'] = '20000'
        self.assertIn('정상', self.e.connect())
        with self.assertRaises(RuntimeError):
            self.live()
        self.assertEqual(self.e.mode, 'paper')

    def test_paper_order_never_calls_exchange(self):
        self.e.order('paper','KRW-BTC','bid', 100000, 1000)
        self.assertFalse(self.broker.orders)
        self.assertLess(self.e.states['paper']['cash'], 900000)

    def test_live_switch_is_paused(self):
        self.live()
        self.assertFalse(self.e.running)
        self.assertFalse(self.broker.orders)

    def test_live_order_cannot_run_in_paper_mode(self):
        with self.assertRaises(RuntimeError):
            self.e.order('live','KRW-BTC','bid',100000,1000)
        self.assertFalse(self.broker.orders)

    def test_network_timeout_preserves_intent_never_resends(self):
        self.broker.submit_error = TimeoutError()
        self.buy_pending()
        pending = self.e.states['live']['pending']
        self.assertTrue(pending)
        for _ in range(3):
            self.e.reconcile()
            self.e.order('live','KRW-BTC','bid',100000,1000)
        self.assertEqual(len(self.broker.orders),1)
        self.assertEqual(json.loads((Path(self.temp.name)/'live.json').read_text())['pending']['identifier'],pending['identifier'])

    def test_pending_blocks_mode_switch(self):
        self.buy_pending()
        with self.assertRaises(RuntimeError):
            self.e.switch('paper')

    def test_rejected_order_clears_intent_and_pauses(self):
        self.broker.submit_error = ApiError(400)
        self.buy_pending()
        self.assertIsNone(self.e.states['live']['pending'])
        self.assertFalse(self.e.running)

    def test_filled_order_applied_exactly_once(self):
        self.buy_pending()
        self.filled()
        self.e.reconcile()
        self.e.reconcile()
        s = self.e.states['live']
        self.assertEqual(s['positions']['KRW-BTC']['volume'],1)
        self.assertEqual(s['cash'],899950)
        disk = json.loads((Path(self.temp.name)/'live.json').read_text())
        self.assertIsNone(disk['pending'])
        self.assertEqual(disk['cash'],899950)

    def test_incomplete_trade_details_stay_pending(self):
        self.buy_pending()
        self.broker.response = {'state':'done','executed_volume':'1','paid_fee':'50','trades':[]}
        self.e.reconcile()
        self.assertTrue(self.e.states['live']['pending'])
        self.assertFalse(self.e.states['live']['positions'])

    def test_partial_fill_cancel_is_owned_actual_volume(self):
        self.buy_pending()
        self.filled(volume=.25,funds=25000,fee=12.5)
        self.broker.response['state'] = 'cancel'
        self.e.reconcile()
        self.assertEqual(self.e.states['live']['positions']['KRW-BTC']['volume'],.25)
        self.assertEqual(self.e.states['live']['cash'],974987.5)

    def test_restart_recovers_pending_but_does_not_buy(self):
        self.buy_pending()
        restarted = Engine(self.temp.name, broker=self.broker, network=False)
        self.assertEqual(restarted.mode,'live')
        self.assertFalse(restarted.running)
        self.assertTrue(restarted.states['live']['pending'])
        self.assertEqual(len(self.broker.orders),1)

    def test_unknown_order_recovery_requires_404(self):
        self.buy_pending()
        self.e.states['live']['pending']['created'] -= 61
        self.broker.get_error = TimeoutError()
        with self.assertRaises(TimeoutError):
            self.e.clear_unsubmitted()
        self.assertTrue(self.e.states['live']['pending'])

    def test_confirmed_absent_order_can_be_unlocked(self):
        self.buy_pending()
        self.e.states['live']['pending']['created'] -= 61
        self.broker.get_error = ApiError(404)
        with patch('trend_engine.time.sleep'):
            self.e.clear_unsubmitted()
        self.assertIsNone(self.e.states['live']['pending'])
        self.assertFalse(self.e.running)

    def test_manual_coin_change_locks_entries_and_protects_baseline(self):
        self.live()
        self.broker.rows[1]['balance'] = '.01'
        with self.assertRaises(RuntimeError):
            self.e.connect()
        self.assertTrue(self.e.states['live']['review'])
        self.assertFalse(self.broker.orders)

    def test_real_sell_must_not_sell_original_coins_after_external_change(self):
        self.buy_pending()
        self.filled()
        self.e.reconcile()
        # Actual BTC is still baseline .02, not baseline + the tracked 1 coin.
        with self.assertRaises(RuntimeError):
            self.e.order('live','KRW-BTC','ask')
        self.assertEqual(len(self.broker.orders),1)

    def test_fees_and_cooldown_applied_on_paper_exit(self):
        self.e.order('paper','KRW-BTC','bid',100000,1000)
        self.quote(103000,103050)
        self.e.order('paper','KRW-BTC','ask',reason='test')
        s=self.e.states['paper']
        self.assertFalse(s['positions'])
        self.assertGreater(s['cooldown']['KRW-BTC'],time.time()+14390)
        self.assertAlmostEqual(s['cash']-1000000,s['trades'][-1]['net'],places=3)

    def test_stale_quotes_block_orders(self):
        self.e.quotes['KRW-BTC']['time']-=20
        with self.assertRaises(RuntimeError):
            self.e.order('paper','KRW-BTC','bid',100000,1000)

    def test_wide_spread_blocks_entry(self):
        self.signal()
        self.quote(100000,101000)
        self.e.enter('paper')
        self.assertFalse(self.e.states['paper']['positions'])

    def test_broken_breakout_blocks_entry(self):
        self.signal()
        self.quote(99800,99850)
        self.e.enter('paper')
        self.assertFalse(self.e.states['paper']['positions'])

    def test_chasing_blocks_entry(self):
        self.signal()
        self.quote(100400,100450)
        self.e.enter('paper')
        self.assertFalse(self.e.states['paper']['positions'])

    def test_same_signal_cannot_rebuy(self):
        self.signal()
        self.e.enter('paper')
        self.assertTrue(self.e.states['paper']['positions'])
        self.e.order('paper','KRW-BTC','ask',reason='test')
        self.e.states['paper']['cooldown'].clear()
        self.e.enter('paper')
        self.assertFalse(self.e.states['paper']['positions'])

    def test_old_candle_signal_cannot_enter(self):
        self.signal()
        self.e.signals['KRW-BTC']['closed_at'] -= 600
        self.e.enter('paper')
        self.assertFalse(self.e.states['paper']['positions'])

    def test_reversed_signal_price_blocks_entry(self):
        self.signal()
        self.e.signals['KRW-BTC']['breakout'] = 99000
        self.quote(99600,99650)
        self.e.enter('paper')
        self.assertFalse(self.e.states['paper']['positions'])

    def test_risk_budget_and_exposure_limits(self):
        n = position_size(1e6,1e6,0,.02)
        self.assertLessEqual(n * .023,5000.00001)
        self.assertLessEqual(position_size(1e6,1e6,790000,.001),10000)
        self.assertEqual(position_size(1e6,1e6,810000,.01),0)
        self.assertEqual(position_size(1e6,1e6,0,float('nan')),0)

    def test_day_loss_blocks_entry(self):
        self.e.risk('paper')
        self.e.states['paper']['cash']=969000
        self.assertEqual(self.e.risk('paper'),'day_stop')

    def test_ten_percent_halt_is_sticky(self):
        self.e.states['paper']['cash']=899000
        self.e.risk('paper')
        self.e.states['paper']['cash']=1000000
        self.assertEqual(self.e.risk('paper'),'halt')
        with self.assertRaises(RuntimeError):
            self.e.start()

    def test_twenty_percent_requests_liquidation(self):
        self.e.states['paper']['cash']=799000
        self.assertEqual(self.e.risk('paper'),'liquidate')

    def test_stop_never_moves_down(self):
        self.e.order('paper','KRW-BTC','bid',100000,1000)
        self.quote(104000,104050)
        self.e.manage('paper')
        stop = self.e.states['paper']['positions']['KRW-BTC']['stop']
        self.quote(103000,103050)
        self.e.manage('paper')
        self.assertEqual(self.e.states['paper']['positions']['KRW-BTC']['stop'],stop)

    def test_corrupt_state_fails_closed(self):
        (Path(self.temp.name)/'live.json').write_text('{}')
        with self.assertRaises(RuntimeError):
            Engine(self.temp.name,network=False)

    def test_reset_archives_paper_only(self):
        self.e.reset_paper()
        self.assertTrue(list(Path(self.temp.name).glob('paper_archive*')))


class IndicatorTests(unittest.TestCase):
    def fixture(self):
        now=1800000000//14400*14400+100
        four=[{'t':now-100-(600-i)*14400,'c':100+i*.1,'h':101+i*.1,'l':99+i*.1,'v':1} for i in range(600)]
        hour=[{'t':now-100-(200-i)*3600,'c':100,'h':101,'l':99,'v':1} for i in range(200)]
        hour[-1].update(c=103,h=104,v=2)
        return hour,four,now

    def test_prior_high_excludes_current_bar(self):
        h,f,now=self.fixture()
        result=evaluate(h,f,now)
        self.assertTrue(result['ok'])
        self.assertEqual(result['breakout'],101)

    def test_future_bar_rejected(self):
        h,f,now=self.fixture()
        h[-1]['t']=now
        self.assertFalse(evaluate(h,f,now)['ok'])

    def test_gap_rejected(self):
        h,f,now=self.fixture()
        del h[-5]
        self.assertIn('누락',evaluate(h,f,now)['reason'])

    def test_stale_closed_bars_rejected(self):
        h,f,now=self.fixture()
        self.assertFalse(evaluate(h,f,now+15000)['ok'])

    def test_old_hourly_gap_restarts_atr_warmup(self):
        h,f,now=self.fixture()
        del h[5]
        self.assertTrue(evaluate(h,f,now)['ok'])

    def test_insufficient_warmup_rejected(self):
        h,f,now=self.fixture()
        self.assertFalse(evaluate(h,f[-200:],now)['ok'])


if __name__ == '__main__':
    unittest.main()
