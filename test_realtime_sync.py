import copy
import json
import tempfile
import time
import unittest
from pathlib import Path

from portfolio import Portfolio
from realtime_sync import RealtimeSync
from trend_engine import Engine
from test_portfolio import FakeBroker,Public


class RealtimeTests(unittest.TestCase):
    def test_public_realtime_trade_forwarding_and_subscription(self):
        received=[]
        self.r.trade_listener=lambda data,now:received.append(data)
        request=self.r.subscription('public')
        trades=next(item for item in request if item.get('type')=='trade')
        self.assertTrue(trades['is_only_realtime'])
        self.assertIn('KRW-ONG',trades['codes'])
        message={'type':'trade','code':'KRW-ONG','trade_timestamp':time.time()*1000,
                 'trade_price':5000,'trade_volume':1,'ask_bid':'BID','sequential_id':1,'stream_type':'REALTIME'}
        self.r.message('public',json.dumps(message))
        self.r.message('private',json.dumps(message))
        self.assertEqual(received,[message])
        self.assertFalse(self.b.sent)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.e=Engine(self.tmp.name,network=False)
        self.b=FakeBroker();self.p=Portfolio(Path(self.tmp.name),self.e,self.b,Public());self.p.refresh()
        self.p.catalog['KRW-ONG']={'market':'KRW-ONG','korean_name':'온톨로지가스'}
        self.r=RealtimeSync(self.p)
    def tearDown(self):self.tmp.cleanup()
    def asset(self,stamp=None,balance=20):
        self.r.message('private',json.dumps({'type':'myAsset','asset_timestamp':stamp or time.time()*1000,
            'assets':[{'currency':'ONG','balance':balance,'locked':0}]}));self.r.apply()

    def test_external_buy_appears_live_without_changing_virtual_assets(self):
        before=copy.deepcopy(self.p.state['paper']);self.asset()
        self.r.message('public',json.dumps({'type':'ticker','code':'KRW-ONG','timestamp':time.time()*1000,'trade_price':5000}))
        self.r.apply();h=next(h for h in self.p.view('live')['holdings'] if h['currency']=='ONG')
        self.assertEqual(h['qty'],20);self.assertEqual(h['value'],100000)
        self.assertTrue(h['avg_pending']);self.assertIsNone(h['roi'])
        self.assertEqual(before,self.p.state['paper']);self.assertTrue(self.p.account_wake.is_set())
        self.assertEqual(self.b.sent,[])

    def test_older_rest_response_does_not_overwrite_new_asset_event(self):
        start=time.time()+.01;self.p.account_request_started=start-.1
        self.asset(stamp=(start+.1)*1000)
        self.p.apply_accounts(copy.deepcopy(self.b.rows),start)
        self.assertTrue(any(a['currency']=='ONG' for a in self.p.accounts))

    def test_rest_confirmation_sets_actual_average_price(self):
        self.asset();now=time.time()+1
        rows=copy.deepcopy(self.b.rows)+[{'currency':'ONG','balance':'20','locked':'0','avg_buy_price':'5000','unit_currency':'KRW'}]
        self.p.apply_accounts(rows,now)
        h=next(h for h in self.p.view('live')['holdings'] if h['currency']=='ONG')
        self.assertEqual(h['avg'],5000);self.assertFalse(h['avg_pending'])

    def test_old_asset_event_does_not_roll_back_rest_state(self):
        self.asset(stamp=(self.p.account_request_started-10)*1000)
        self.assertFalse(any(a['currency']=='ONG' for a in self.p.accounts))

    def test_order_event_only_wakes_read_only_account_sync(self):
        self.p.account_wake.clear();self.r.message('private',json.dumps({'type':'myOrder','code':'KRW-ONG','state':'trade'}))
        self.assertTrue(self.p.account_wake.is_set());self.assertFalse(self.p.state['orders']['live']);self.assertEqual(self.b.sent,[])

    def test_invalid_asset_or_public_injected_asset_is_ignored(self):
        self.asset(balance=float('nan'))
        self.r.message('public',json.dumps({'type':'myAsset','assets':[{'currency':'ONG','balance':20,'locked':0}]}))
        self.r.apply();self.assertFalse(any(a['currency']=='ONG' for a in self.p.accounts))

    def test_stale_market_message_cannot_replace_current_price(self):
        self.r.message('public',json.dumps({'type':'ticker','code':'KRW-BTC','timestamp':(time.time()-30)*1000,'trade_price':1}))
        self.r.apply();self.assertEqual(self.p.tickers['KRW-BTC']['trade_price'],100000)

    def test_private_subscription_has_no_asset_codes_or_credentials(self):
        request=self.r.subscription('private')
        self.assertEqual([r.get('type') for r in request[1:]],['myAsset','myOrder'])
        self.assertFalse(any('codes' in r for r in request));self.assertNotIn('Authorization',json.dumps(request))

    def test_market_messages_are_coalesced_in_bounded_maps(self):
        for i in range(500):
            self.r.message('public',json.dumps({'type':'ticker','code':'KRW-BTC','timestamp':time.time()*1000,'trade_price':100000+i}))
        self.assertEqual(len(self.r.ticks),1);self.r.apply();self.assertEqual(self.p.tickers['KRW-BTC']['trade_price'],100499)


if __name__=='__main__':unittest.main()
