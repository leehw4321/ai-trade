"""No private API or real orders. Test funded ledger and causal signal boundaries."""
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_trader import AutoTrader
from scalping_strategy import RULES, evaluate, entry_plan, initial_stop, exit_decision, validate_bars
from replay_auto_v3 import replay


def book(bid=100000,ask=None,size=100):
    return [{'bid_price':bid,'ask_price':ask or bid+10,'bid_size':size,'ask_size':size}]


class V3Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.a=AutoTrader(self.root);self.now=1793235600.
        self.a.action({'action':'auto_v3_config','budget':1000000,'markets':['KRW-BTC','KRW-ETH']})
    def tearDown(self):self.tmp.cleanup()
    def feed(self,bid=100000,ask=None,size=100,delta=0,m='KRW-BTC'):
        self.now+=delta
        self.a.ingest(m,book(bid,ask,size),self.now,now=self.now)
    def entry(self,volume=1):
        return self.a.new_order('KRW-BTC','bid',100000,volume,'entry',self.now,
            atr=650,low=99500,cost_rate=.0021,signal='one',expires=self.now+30,
            breakout=99000,ceiling=101000)
    def fill(self,volume=1):
        o=self.entry(volume)
        self.a.match(o,book(99900,99910),self.now)
        return o
    def test_live_gate_even_direct_http_action(self):
        with self.assertRaisesRegex(ValueError,'실거래'):
            self.a.action({'action':'auto_v3_start','mode':'live'})
        self.assertFalse(self.a.running)
    def test_nan_budget_rejected_and_empty_selection_rejected(self):
        for budget,markets in ((float('nan'),['KRW-BTC']),(100000,[]),(100000,['KRW-X'])):
            with self.assertRaises(ValueError):self.a.action({'action':'auto_v3_config','budget':budget,'markets':markets})
    def test_limit_does_not_fill_at_unfavorable_price(self):
        o=self.entry();self.a.match(o,book(100000,100010),self.now)
        self.assertEqual(o['filled'],0);self.assertEqual(o['status'],'wait')
        self.assertAlmostEqual(self.a.free_cash(),899950)
    def test_fill_cash_fee_and_budget_reinvestment(self):
        self.fill();p=self.a.state['positions']['KRW-BTC']
        self.assertEqual(p['volume'],1)
        self.assertAlmostEqual(self.a.state['cash'],1000000-p['cost'])
        self.a.validate()
        sell=self.a.new_order('KRW-BTC','ask',102000,1,'exit',self.now,ioc=True)
        self.a.match(sell,book(103000),self.now)
        self.assertFalse(self.a.state['positions']);self.assertGreater(self.a.state['cash'],1000000)
        self.assertAlmostEqual(self.a.equity()-1000000,self.a.state['closed'][0]['net'])
        self.a.validate()
        with self.assertRaisesRegex(ValueError,'원금 변경'):
            self.a.action({'action':'auto_v3_config','budget':2000000,'markets':['KRW-BTC']})
    def test_full_signal_reservation_fill_partial_and_stop_path(self):
        self.a.running=True
        self.a.signals['KRW-BTC']={'ok':True,'closed_at':self.now,'close':100000,'atr':650,
                                   'low':99500,'breakout':99000,'id':'e2e'}
        self.feed();self.a.step(self.now)
        self.assertEqual(len(self.a.active('KRW-BTC','bid')),1)
        self.feed(99900,99910,delta=1);self.a.step(self.now)
        self.assertIn('KRW-BTC',self.a.state['positions'])
        self.assertEqual(len(self.a.active('KRW-BTC','ask')),1)
        self.feed(102000,delta=1);self.a.step(self.now)
        self.assertTrue(self.a.state['positions']['KRW-BTC']['part_done'])
        self.feed(99900,delta=1);self.a.step(self.now)
        self.assertFalse(self.a.state['positions']);self.assertEqual(len(self.a.state['closed']),1)
        self.a.validate()
        restored=AutoTrader(self.root);self.assertAlmostEqual(restored.state['cash'],self.a.state['cash'])
    def test_partial_fills_have_one_position_and_journal_is_consistent(self):
        o=self.entry();self.a.match(o,book(99900,99910,.4),self.now)
        self.assertEqual(o['status'],'wait');self.assertAlmostEqual(o['filled'],.4)
        self.a.match(o,book(99900,99910,1),self.now+1)
        self.assertAlmostEqual(self.a.state['positions']['KRW-BTC']['volume'],1)
        self.assertEqual(o['status'],'done');self.a.validate()
    def test_cannot_reserve_more_cash_or_duplicate_order(self):
        self.entry(5)
        with self.assertRaisesRegex(ValueError,'중복'):self.entry()
        with self.assertRaisesRegex(ValueError,'자금 초과'):
            self.a.new_order('KRW-ETH','bid',100000,6,'entry',self.now)
    def test_stop_cancels_partial_and_entire_owned_quantity_exits(self):
        self.fill();self.feed(100000);self.a.step(self.now)
        self.assertTrue(self.a.active('KRW-BTC','ask'))
        self.feed(97000,delta=1);self.a.step(self.now)
        self.assertFalse(self.a.state['positions']);self.assertFalse(self.a.active())
        self.assertEqual(len(self.a.state['closed']),1);self.assertLess(self.a.state['closed'][0]['net'],0)
        self.a.validate()
    def test_ioc_shallow_book_retains_owned_residual_and_retries_new_book(self):
        self.fill();self.feed(97000,size=.2);self.a.step(self.now)
        self.assertAlmostEqual(self.a.state['positions']['KRW-BTC']['volume'],.8)
        fills=len(self.a.state['fills']);self.a.step(self.now+.1)
        self.assertEqual(len(self.a.state['fills']),fills)
        self.feed(96000,size=10,delta=1);self.a.step(self.now)
        self.assertFalse(self.a.state['positions']);self.a.validate()
    def test_minimum_order_residual_not_forgotten(self):
        self.fill(.1);self.feed(97000,size=.06);self.a.step(self.now)
        self.feed(96000,delta=1);self.a.step(self.now)
        self.assertIn('KRW-BTC',self.a.state['positions'])
        self.assertIn('최소 주문',self.a.status)
    def test_pause_cancels_only_buys_keeps_protection(self):
        self.fill();self.feed();self.a.step(self.now)
        self.a.action({'action':'auto_v3_pause'})
        self.assertTrue(self.a.active('KRW-BTC','ask'))
        self.feed(97000,delta=1);self.a.step(self.now)
        self.assertFalse(self.a.state['positions'])
    def test_restart_does_not_resume_entries_and_preserves_positions(self):
        self.fill();self.a.running=True;self.a.save()
        restored=AutoTrader(self.root)
        self.assertFalse(restored.running);self.assertEqual(restored.state['positions'],self.a.state['positions'])
        self.assertEqual(restored.state['cash'],self.a.state['cash'])
    def test_corrupt_cash_fails_closed(self):
        self.a.state['cash']+=100;self.a.save()
        with self.assertRaisesRegex(RuntimeError,'상태 파일 손상'):AutoTrader(self.root)
    def test_stale_quotes_do_not_fake_fill_and_cancel_buy(self):
        self.entry();self.feed();self.a.running=True
        self.a.step(self.now+3)
        self.assertFalse(self.a.active());self.assertFalse(self.a.state['positions'])
    def test_future_old_and_unsorted_books_rejected(self):
        self.feed();q=copy.deepcopy(self.a.quotes)
        self.a.ingest('KRW-BTC',book(100),self.now+5,now=self.now)
        self.a.ingest('KRW-BTC',book(100),self.now-5,now=self.now)
        self.assertEqual(q,self.a.quotes)
    def test_day_stop_latches_until_next_kst_day(self):
        self.a.risk(self.now)
        self.a.state['cash']=984000;self.a.risk(self.now+1)
        self.assertTrue(self.a.state['day_stop'])
        self.a.state['cash']=1000000;self.a.risk(self.now+2)
        self.assertTrue(self.a.state['day_stop'])
        self.a.risk(self.now+86400);self.assertFalse(self.a.state['day_stop'])
    def test_partial_target_is_30_percent_and_no_early_breakeven(self):
        self.fill();self.feed();self.a.step(self.now)
        o=self.a.active('KRW-BTC','ask')[0];p=self.a.state['positions']['KRW-BTC']
        self.assertAlmostEqual(o['volume'],.3)
        self.assertFalse(p['part_done']);self.assertLess(p['stop'],p['entry'])
        self.feed(o['price']+200,delta=1);self.a.step(self.now)
        p=self.a.state['positions']['KRW-BTC'];self.assertTrue(p['part_done']);self.assertAlmostEqual(p['volume'],.7)
        self.feed(o['price']+100,delta=1);self.a.step(self.now)
        self.assertGreater(self.a.state['positions']['KRW-BTC']['stop'],p['entry'])
        self.a.validate()
    def test_slow_legacy_lock_cannot_block_v3_ingestion(self):
        # Service owns no Portfolio/Engine locks and has no broker dependency.
        self.assertFalse(hasattr(self.a,'broker'))
        self.feed();self.assertIn('KRW-BTC',self.a.quotes)
    def test_matching_consumes_snapshot_only_once(self):
        self.a.running=True;self.entry()
        self.feed(99900,99910,.2);self.a.step(self.now)
        n=len(self.a.state['fills']);self.a.step(self.now+.1)
        self.assertEqual(n,len(self.a.state['fills']))
    def test_three_losses_trigger_pause(self):
        for i in range(3):
            self.fill()
            o=self.a.new_order('KRW-BTC','ask',96000,1,'exit',self.now,ioc=True)
            self.a.match(o,book(97000),self.now)
            self.now+=10
        self.assertGreater(self.a.state['pause_until'],self.now)
    def test_time_stop_works_without_indicator_network(self):
        self.fill();self.feed(100000,delta=7201);self.a.step(self.now)
        self.assertFalse(self.a.state['positions'])
    def test_profile_move_preserves_owned_funds_and_stop_and_cancels_partial_only(self):
        self.fill();self.feed();self.a.step(self.now)
        before=copy.deepcopy(self.a.state['positions']['KRW-BTC']);cash=self.a.state['cash']
        self.a.move_position_profile('KRW-BTC','long',self.now+10)
        p=self.a.state['positions']['KRW-BTC']
        self.assertEqual(p['volume'],before['volume']);self.assertEqual(self.a.state['cash'],cash)
        self.assertEqual(p['stop'],before['stop']);self.assertEqual(p['max_seconds'],0)
        self.assertFalse(self.a.active('KRW-BTC','ask'));self.assertTrue(p['profile_pending'])
        self.a.validate()
        restored=AutoTrader(self.root);self.assertEqual(restored.state['positions']['KRW-BTC']['bucket'],'long')
    def test_cannot_move_unknown_or_exiting_v3_position(self):
        with self.assertRaises(ValueError):self.a.move_position_profile('KRW-UNKNOWN','long',self.now)
        self.fill();self.a.state['positions']['KRW-BTC']['exiting']='stop'
        with self.assertRaises(ValueError):self.a.move_position_profile('KRW-BTC','swing',self.now)
    def test_offline_replay_uses_real_engine_and_rejects_future_signal(self):
        sig={'ok':True,'closed_at':self.now,'time':self.now,'close':100000,'atr':650,
             'low':99500,'breakout':99000,'id':'replay','ema20':99900}
        events=[{'time':self.now,'type':'signal','market':'KRW-BTC','signal':sig}]
        for offset,bid,ask in ((0,100000,100010),(1,99900,99910),(2,102000,102010),(3,99900,99910)):
            events.append({'time':self.now+offset,'type':'book','market':'KRW-BTC',
                           'book':{'time':self.now+offset,'units':book(bid,ask)}})
        r=replay(events);self.assertEqual(r['stats']['closed'],1);self.assertFalse(r['profitability_verified'])
        self.assertEqual(r['real_orders_submitted'],0)
        delayed=replay(events,delay=3);self.assertEqual(delayed['stats']['closed'],0)
        events[0]['signal']=sig|{'closed_at':self.now+1}
        with self.assertRaisesRegex(ValueError,'future signal'):replay(events)


class StrategyTests(unittest.TestCase):
    def test_long_profile_has_no_fixed_timeout_and_swing_has_seven_days(self):
        p={'entry':100,'stop':90,'peak':100,'trough':100,'r':10,'atr':1,'since':0,
           'bucket':'long','max_seconds':0,'progress_seconds':0}
        self.assertEqual(exit_decision(p,100,{'bucket':'long','atr':20},10_000_000),'')
        p.update(bucket='swing',max_seconds=7*86400,profile_since=100)
        self.assertEqual(exit_decision(p,100,{'bucket':'swing','atr':20},7*86400+100),'최대 보유시간 종료')
    def test_positive_signal_and_first_retest_are_causal(self):
        now=1793239200.  # UTC aligned 5m/15m/1h boundary
        now=int(now//3600)*3600
        def series(count,sec):
            return [{'t':now-(count-i)*sec,'h':100000+i*10+250,'l':100000+i*10-250,
                     'c':100000+i*10,'v':100} for i in range(count)]
        hour=series(600,3600);quarter=series(200,900);five=series(300,300)
        level=max(b['h'] for b in five[-22:-2])
        five[-2].update(h=level+400,l=level-100,c=level+200,v=200)
        five[-1].update(h=level+300,l=level,c=level+150,v=100)
        signal=evaluate(hour,quarter,five,now)
        self.assertTrue(signal['ok'],signal)
        self.assertEqual(signal['breakout'],level)
        # A forming higher timeframe bar is refused, not used as future information.
        future=hour+[hour[-1]|{'t':now}]
        self.assertFalse(evaluate(future,quarter,five,now)['ok'])
        # A later qualifying retest must not buy the same breakout twice.
        later=five+[five[-1]|{'t':now}]
        self.assertFalse(evaluate(hour,quarter,later,now+300)['ok'])

    def test_stop_combines_structure_and_atr(self):
        self.assertEqual(initial_stop(100,1,99),98.5)
        self.assertEqual(initial_stop(100,1,97),96.8)
    def test_stop_never_moves_down_when_atr_increases(self):
        p={'entry':100,'stop':99,'peak':104,'trough':100,'r':1,'atr':1,'since':0,'part_done':True}
        exit_decision(p,104,{'atr':1},10);s=p['stop']
        exit_decision(p,103,{'atr':10},11);self.assertGreaterEqual(p['stop'],s)
    def test_forming_missing_and_reverse_candles_rejected(self):
        rows=[{'t':i*300,'h':101,'l':99,'c':100,'v':1} for i in range(200)]
        validate_bars(rows,300,200,60000)
        for bad in (rows[:-1],rows[::-1],rows[:20]+rows[21:],rows+[rows[-1]|{'t':60000}]):
            with self.assertRaises(ValueError):validate_bars(bad,300,200,60000)
    def test_entry_cost_and_signal_age(self):
        q={'time':1000,'units':book(100000)}
        sig={'ok':True,'closed_at':990,'close':100000,'atr':650,'low':99500,'breakout':99000,'id':'1'}
        plan=entry_plan(sig,q,1000000,1000000,0,0,1000)
        self.assertLessEqual(plan['amount'],250000);self.assertLessEqual(plan['price'],100000)
        with self.assertRaises(ValueError):entry_plan(sig,q,1000000,1000000,0,0,1021)
        wide={'time':1000,'units':book(100000,100200)}
        with self.assertRaises(ValueError):entry_plan(sig,wide,1000000,1000000,0,0,1000)
    def test_no_capacity_when_pending_risk_uses_limit(self):
        q={'time':1000,'units':book(100000)}
        sig={'ok':True,'closed_at':990,'close':100000,'atr':650,'low':99500,'breakout':99000,'id':'1'}
        with self.assertRaises(ValueError):entry_plan(sig,q,1000000,1000000,0,5000,1000)
    def test_signal_requires_warmup(self):
        self.assertFalse(evaluate([],[],[],1000)['ok'])


if __name__=='__main__':unittest.main()
