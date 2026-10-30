import datetime
import unittest
from unittest.mock import Mock, patch

import upbit_bot as bot


class EventGuardChecks(unittest.TestCase):
    def setUp(self):
        self.cfg = bot.env_cfg()
        self.cfg.update(ev_count=4, ev_window=10, ev_rebuy=0, ev_rebuy_wait=15,
                        ev_recheck=1, ev_signal_age=8, cooldown=15)
        self.pick = dict(market='KRW-TEST', signal_time=1000, rise=.03, r10=.01, r30=.006, spread=.001,
                         accel=2,score=5)
        self.sec = dict(last=103, checked_at=1000, r10=.01, r30=.006, active=5,
                        t10=5_000_000, accel=2, fade=-.001)
        self.state = dict(positions={}, day_pnl=0, cooldown={})
        self.api = Mock()
        self.api.request.side_effect = AssertionError('A unit test must never submit or query real orders')

    def public(self, path, params):
        if path == '/v1/ticker':
            return [dict(opening_price=100, acc_trade_price=100_000_000, acc_trade_price_24h=2_000_000_000)]
        if path == '/v1/candles/minutes/1':
            return [dict(opening_price=102)]
        raise AssertionError('Unexpected endpoint: '+path)

    def mocks(self, **kwargs):
        values=dict(public=Mock(side_effect=self.public), book=Mock(return_value=(.001,1)),
                    sec_stats=Mock(return_value=self.sec), paper_day_active=Mock(return_value=False),
                    event_buy=Mock(return_value=True))
        values.update(kwargs)
        return patch.multiple(bot, **values), values

    def test_first_entry_allowed_when_rebuy_is_off(self):
        self.assertIsNone(bot.event_rebuy_block(self.cfg,self.state,{'bought':[]},'KRW-TEST',0,1001))

    def test_rebuy_off_blocks_even_top_rank(self):
        reason=bot.event_rebuy_block(self.cfg,self.state,{'bought':['KRW-TEST']},'KRW-TEST',0,1001)
        self.assertEqual(reason,'event rebuy disabled')

    def test_rebuy_wait_does_not_bypass_longer_normal_cooldown(self):
        self.cfg.update(ev_rebuy=1,ev_rebuy_wait=1)
        self.state['cooldown']['KRW-TEST']=100
        ev={'bought':['KRW-TEST'],'rebought':[]}
        self.assertEqual(bot.event_rebuy_block(self.cfg,self.state,ev,'KRW-TEST',0,999),'event rebuy cooldown')
        self.assertIsNone(bot.event_rebuy_block(self.cfg,self.state,ev,'KRW-TEST',0,1000))
        self.assertEqual(bot.event_rebuy_block(self.cfg,self.state,ev,'KRW-TEST',3,1000),'event rebuy rank/count limit')
        ev['rebought']=['KRW-TEST']
        self.assertEqual(bot.event_rebuy_block(self.cfg,self.state,ev,'KRW-TEST',0,1000),'event rebuy rank/count limit')

    def test_missing_sale_time_blocks_rebuy(self):
        self.cfg['ev_rebuy']=1
        self.assertEqual(bot.event_rebuy_block(self.cfg,self.state,{'bought':['KRW-TEST']},'KRW-TEST',0,1001),
                         'event rebuy sale time unavailable')

    def test_old_or_future_signal_does_not_query_or_order(self):
        for timestamp in (990,1002,0):
            with self.subTest(timestamp=timestamp),patch.object(bot.time,'time',return_value=1001):
                ctx,m=self.mocks()
                with ctx:
                    self.pick['signal_time']=timestamp
                    self.assertIsNone(bot.guarded_event_buy(self.api,self.cfg,self.state,self.pick,100000,'test'))
                    m['public'].assert_not_called()
                    m['event_buy'].assert_not_called()

    def test_fresh_candidate_passes_and_refreshes_timestamp(self):
        ctx,m=self.mocks()
        with ctx,patch.object(bot.time,'time',return_value=1001):
            self.assertTrue(bot.guarded_event_buy(self.api,self.cfg,self.state,self.pick,100000,'test'))
            m['event_buy'].assert_called_once()
            self.assertEqual(self.pick['price'],103)
            self.assertEqual(self.pick['signal_time'],1000)

    def test_reversed_momentum_cannot_submit_order(self):
        for changes in ({'r10':-.01},{'r30':.001},{'active':2},{'t10':100},{'accel':1},{'fade':-.02}):
            with self.subTest(changes=changes):
                ctx,m=self.mocks(sec_stats=Mock(return_value=dict(self.sec,**changes)))
                with ctx,patch.object(bot.time,'time',return_value=1001):
                    self.assertIsNone(bot.guarded_event_buy(self.api,self.cfg,self.state,self.pick,100000,'test'))
                    m['event_buy'].assert_not_called()

    def test_spread_and_current_minute_checks(self):
        ctx,m=self.mocks(book=Mock(return_value=(.1,1)))
        with ctx,patch.object(bot.time,'time',return_value=1001):
            self.assertIsNone(bot.guarded_event_buy(self.api,self.cfg,self.state,self.pick,100000,'test'))
            m['event_buy'].assert_not_called()
        ctx,m=self.mocks(sec_stats=Mock(return_value=dict(self.sec,last=101.9)))
        with ctx,patch.object(bot.time,'time',return_value=1001):
            self.assertIsNone(bot.guarded_event_buy(self.api,self.cfg,self.state,self.pick,100000,'test'))
            m['event_buy'].assert_not_called()

    def test_network_or_recheck_age_failure_never_orders(self):
        for mocked in (dict(public=Mock(side_effect=TimeoutError)),
                       dict(sec_stats=Mock(return_value=dict(self.sec,checked_at=990)))):
            ctx,m=self.mocks(**mocked)
            with ctx,patch.object(bot.time,'time',return_value=1001):
                self.assertIsNone(bot.guarded_event_buy(self.api,self.cfg,self.state,self.pick,100000,'test'))
                m['event_buy'].assert_not_called()

    def test_paper_day_blocks_before_any_market_query(self):
        ctx,m=self.mocks(paper_day_active=Mock(return_value=True))
        with ctx:
            self.assertIsNone(bot.guarded_event_buy(self.api,self.cfg,self.state,self.pick,100000,'test'))
            m['public'].assert_not_called()
            m['event_buy'].assert_not_called()

    def cycle_context(self,positions):
        now=datetime.datetime(2026,10,26,9,1)
        self.state.update(positions=positions,event={'day':'2026-10-26','per':100000,
                          'bought':[],'failed':[],'rebought':[]})
        return patch.multiple(bot, today=Mock(return_value='2026-10-26'),
              event_scan=Mock(return_value=[self.pick]),write_event_scan=Mock(),save_state=Mock()),now

    def test_filtered_candidate_not_blacklisted_or_counted(self):
        for result in (None,False,True):
            ctx,now=self.cycle_context({})
            with ctx,patch.object(bot.datetime,'datetime') as clock,patch.object(bot.time,'time',return_value=1001),patch.object(bot,'guarded_event_buy',return_value=result):
                clock.now.return_value=now
                self.assertTrue(bot.event_cycle(self.api,self.cfg,self.state))
                ev=self.state['event']
                self.assertEqual(ev['bought'],['KRW-TEST'] if result is True else [])
                self.assertEqual(ev['failed'],['KRW-TEST'] if result is False else [])

    def test_rotation_does_not_sell_into_invalid_candidate(self):
        ctx,now=self.cycle_context({f'KRW-{i}':{'event':True} for i in range(4)})
        with ctx,patch.object(bot.datetime,'datetime') as clock,patch.object(bot.time,'time',return_value=1001),patch.object(bot,'recheck_event_entry',return_value='expired'),patch.object(bot,'event_rotate') as rotate:
            clock.now.return_value=now
            self.assertTrue(bot.event_cycle(self.api,self.cfg,self.state))
            rotate.assert_not_called()


if __name__=='__main__':
    unittest.main()
