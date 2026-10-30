import copy
import datetime as dt
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from observations import period,until,KST
from portfolio import Portfolio
from trend_engine import Engine
from test_portfolio import FakeBroker,Public


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.e=Engine(self.tmp.name,network=False)
        self.b=FakeBroker();self.pub=Public();self.p=Portfolio(Path(self.tmp.name),self.e,self.b,self.pub);self.p.refresh()
    def tearDown(self):self.tmp.cleanup()
    def item(self,market='KRW-BTC',mode='paper',amount='10000',price='100000'):
        return {'market':market,'mode':mode,'amount':amount,'price':price,'bucket':'short','period_value':1,'period_unit':'hour'}
    def reviewed(self,items=None):return self.p.preview_selection({'items':items or [self.item()]})

    def test_calendar_month_clamps_leap_and_non_leap_dates(self):
        for year,day in ((2028,29),(2027,28)):
            start=dt.datetime(year,1,31,14,20,tzinfo=KST).timestamp()
            end=dt.datetime.fromtimestamp(until(start,period(1,'month')),KST)
            self.assertEqual((end.month,end.day,end.hour,end.minute),(2,day,14,20))

    def test_period_validation_and_fixed_units(self):
        for value,unit in [(0,'day'),(-1,'week'),(1.5,'month'),(float('nan'),'hour'),(61,'month'),(1,'year')]:
            with self.assertRaises(ValueError):period(value,unit)
        self.assertEqual(until(100,period(2,'week')),100+14*86400)

    def test_preview_has_no_order_side_effect_and_uses_requested_modes(self):
        r=self.reviewed([self.item(),self.item('KRW-ETH','live')])
        self.assertEqual([t['mode'] for t in r['items']],['paper','live'])
        self.assertFalse(self.p.state['orders']['paper']);self.assertFalse(self.p.state['orders']['live']);self.assertEqual(self.b.sent,[])

    def test_combined_budget_checked_before_any_order(self):
        with self.assertRaisesRegex(ValueError,'합계'):
            self.reviewed([self.item(mode='live',amount='60000'),self.item('KRW-ETH','live','60000')])
        self.assertEqual(self.b.sent,[]);self.assertFalse(self.p.tickets)

    def test_duplicate_symbol_same_account_rejected(self):
        with self.assertRaisesRegex(ValueError,'동일 종목'):self.reviewed([self.item(),self.item()])

    def test_invalid_period_cannot_create_order(self):
        item=self.item();item['period_unit']='year'
        with self.assertRaises(ValueError):self.reviewed([item])
        self.assertEqual(self.b.sent,[])

    def test_unfilled_limit_does_not_start_observation_clock(self):
        r=self.reviewed([self.item(price='99900')]);self.p.submit_selection(r['group'])
        o=self.p.observation_view()[0];self.assertEqual(o['status'],'waiting');self.assertIsNone(o['started']);self.assertIsNone(o['ends'])
        order=self.p.state['orders']['paper'][0];self.p.cancel('paper',order['identifier'])
        self.assertEqual(self.p.observation_view()[0]['status'],'cancelled')

    def test_first_fill_starts_clock_and_expiry_never_sells(self):
        r=self.reviewed();self.p.submit_selection(r['group']);obs=self.p.state['observations'][0]
        self.assertEqual(obs['ends']-obs['started'],3600)
        before=copy.deepcopy(self.p.state['orders']);self.p.tickers['KRW-BTC']['trade_price']=105000
        with patch('time.time',return_value=obs['ends']+1):
            self.p.market_time=time.time();self.p.settle_observations();view=self.p.observation_view()[0]
        self.assertEqual(view['status'],'completed');self.assertAlmostEqual(view['price_return_pct'],5)
        self.assertEqual(before,self.p.state['orders']);self.assertEqual(self.b.sent,[])

    def test_partial_fills_do_not_restart_clock(self):
        self.pub.size=.03;r=self.reviewed();self.p.submit_selection(r['group'])
        obs=self.p.state['observations'][0];end=obs['ends'];o=self.p.state['orders']['paper'][0]
        self.p.quotes['KRW-BTC']['time']+=.001;self.p.paper_match(o)
        self.assertEqual(obs['ends'],end);self.assertAlmostEqual(self.p.observation_view()[0]['volume'],.06)

    def test_group_replay_after_restart_does_not_resubmit(self):
        r=self.reviewed([self.item(mode='live')]);self.p.submit_selection(r['group']);self.p.submit_selection(r['group'])
        restored=Portfolio(Path(self.tmp.name),self.e,self.b,self.pub)
        restored.submit_selection(r['group']);self.assertEqual(len(self.b.sent),1)

    def test_expired_review_cannot_submit(self):
        r=self.reviewed();self.p.selection_reviews[r['group']]['expires']=0
        with self.assertRaises(ValueError):self.p.submit_selection(r['group'])
        self.assertFalse(self.p.state['orders']['paper'])

    def test_unknown_live_result_stops_remaining_orders(self):
        r=self.reviewed([self.item(mode='live'),self.item('KRW-ETH','live')]);self.b.fail=TimeoutError()
        result=self.p.submit_selection(r['group']);self.assertEqual(len(result['results']),1)
        self.assertEqual(result['results'][0]['status'],'unknown');self.assertEqual(len(self.b.sent),1)


if __name__=='__main__':unittest.main()
