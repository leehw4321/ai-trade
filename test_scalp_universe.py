"""Universe expansion, rolling selection and short-horizon causal rules."""
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_trader import AutoTrader
from scalp_universe import screen, choose_candidates, valid_code, LIMITS
from scalping_strategy import evaluate_intraday, exit_decision, RULES
from test_auto_v3 import book


class UniverseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=time.time()
        self.catalog=[{'market':'KRW-'+str(i),'korean_name':'코인 '+str(i),
                       'market_event':{'warning':False,'caution':{}}} for i in range(25)]
        self.ticks=[{'market':r['market'],'trade_price':1000,'trade_timestamp':self.now*1000,
                     'acc_trade_price_24h':1e10-i*1e8,'signed_change_rate':.01} for i,r in enumerate(self.catalog)]
        def public(path,params=None):
            if path=='/v1/market/all':return copy.deepcopy(self.catalog)
            if path=='/v1/ticker/all':return copy.deepcopy(self.ticks)
            raise RuntimeError('unexpected public request')
        self.a=AutoTrader(self.root,public=public)
    def tearDown(self):self.tmp.cleanup()
    def test_observes_every_pair_and_deep_pool_rotates_all_eligible(self):
        self.a.refresh_universe(self.now)
        self.assertEqual(len(self.a.state['markets']),25)
        self.assertEqual(len(self.a.candidates),12)
        seen=set(self.a.candidates)
        for i in range(1,7):
            for row in self.ticks:row['trade_timestamp']=(self.now+i*60)*1000
            self.a.refresh_universe(self.now+i*60);seen.update(self.a.candidates)
        self.assertEqual(seen,set(self.a.state['markets']))
    def test_recent_warning_low_volume_stable_and_old_quote_block_entry(self):
        c={r['market']:r for r in self.catalog};t={r['market']:r for r in self.ticks}
        c['KRW-0']['market_event']['warning']=True
        c['KRW-1']['market_event']['caution']={'PRICE_FLUCTUATIONS':True}
        t['KRW-2']['acc_trade_price_24h']=1000
        t['KRW-3']['trade_timestamp']=(self.now-61)*1000
        t['KRW-4']['signed_change_rate']=.31
        c['KRW-USDT']={'market':'KRW-USDT'};t['KRW-USDT']=self.ticks[5].copy()
        r=screen(c,t,self.now)
        for m in ('KRW-0','KRW-1','KRW-2','KRW-3','KRW-4','KRW-USDT'):self.assertFalse(r[m]['eligible'],m)
        self.assertTrue(r['KRW-5']['eligible'])
    def test_warned_owned_position_remains_protected(self):
        r=screen({r['market']:r for r in self.catalog},{r['market']:r for r in self.ticks},self.now)
        r['KRW-0']['eligible']=False
        candidates,_=choose_candidates(r,protected=['KRW-0'])
        self.assertEqual(candidates[0],'KRW-0')
    def test_all_configuration_does_not_require_old_btc_eth_list(self):
        self.a.refresh_universe(self.now)
        self.a.action({'action':'auto_v3_config','budget':100000,'scope':'all'})
        self.assertEqual(len(self.a.state['markets']),25)
        self.assertEqual(self.a.state['universe_mode'],'all')
        self.a.validate()
    def test_stale_universe_blocks_new_orders(self):
        self.a.refresh_universe(self.now);m=self.a.candidates[0]
        self.assertTrue(self.a.entry_allowed(m,self.now))
        self.assertFalse(self.a.entry_allowed(m,self.now+46))
    def test_timestamp_roundoff_is_allowed_but_future_trade_is_blocked(self):
        c={r['market']:r for r in self.catalog};t={r['market']:r for r in self.ticks}
        t['KRW-0']['trade_timestamp']=(self.now+.0000002)*1000
        t['KRW-1']['trade_timestamp']=(self.now+1)*1000
        result=screen(c,t,self.now)
        self.assertTrue(result['KRW-0']['eligible']);self.assertFalse(result['KRW-1']['eligible'])
    def test_updated_caution_removes_current_candidate_immediately(self):
        self.a.refresh_universe(self.now);m=self.a.candidates[0]
        self.catalog[0]['market_event']['warning']=True
        self.a.catalog_time=0;self.a.refresh_universe(self.now+1)
        self.assertNotIn(m,self.a.candidates);self.assertFalse(self.a.entry_allowed(m,self.now+1))
    def test_all_market_ingest_accepts_altcoins_without_allowing_non_krw(self):
        self.a.ingest('KRW-XRP',book(),self.now,now=self.now)
        self.a.ingest('BTC-XRP',book(),self.now,now=self.now)
        self.assertIn('KRW-XRP',self.a.quotes);self.assertNotIn('BTC-XRP',self.a.quotes)
    def test_v30_state_migrates_without_losing_cash_or_positions(self):
        self.a.action({'action':'auto_v3_config','budget':500000,'markets':['KRW-BTC']})
        self.a.state['version']='scalp-v3.0';self.a.state.pop('universe_mode');self.a.save()
        restored=AutoTrader(self.root)
        self.assertEqual(restored.state['cash'],500000)
        self.assertEqual(restored.state['universe_mode'],'all');self.assertFalse(restored.running)
    def test_delisted_owned_market_is_not_removed_from_book_watch(self):
        self.a.state['positions']['KRW-OLD']={'volume':1,'entry':10}
        self.a.refresh_universe(self.now)
        self.assertIn('KRW-OLD',self.a.book_markets())
        self.assertNotIn('KRW-OLD',self.a.state['markets'])
    def test_syntactic_codes_do_not_admit_paths_or_cross_markets(self):
        for code in ('../secret','BTC-XRP','KRW-<script>','KRW-abc'):self.assertFalse(valid_code(code))
        self.assertTrue(valid_code('KRW-XRP'))
    def test_whole_universe_still_limits_pending_entries_to_two_slots(self):
        self.a.refresh_universe(self.now)
        self.a.action({'action':'auto_v3_config','budget':1000000,'scope':'all'})
        self.a.running=True
        for m in self.a.candidates:
            self.a.signals[m]={'ok':True,'closed_at':self.now,'close':100000,'atr':650,
                               'low':99500,'breakout':99000,'id':'slot-test'}
            self.a.ingest(m,book(),self.now,now=self.now)
        self.a.step(self.now)
        self.assertEqual(len(self.a.active(side='bid')),2)
        self.assertLessEqual(sum(o['price']*o['volume'] for o in self.a.active(side='bid')),500000)
    def test_candidate_rotation_never_drops_protected_order(self):
        self.a.refresh_universe(self.now)
        market=self.a.candidates[-1]
        self.a.state['orders'].append({'market':market,'status':'wait','side':'bid'})
        for row in self.ticks:row['trade_timestamp']=(self.now+60)*1000
        self.a.refresh_universe(self.now+60)
        self.assertIn(market,self.a.candidates)
        self.assertEqual(self.a.candidates[0],market)


class IntradayTests(unittest.TestCase):
    def series(self,count,seconds,now):
        end=int(now//seconds)*seconds
        return [{'t':end-(count-i)*seconds,'c':100000+i*10,'h':100250+i*10,
                 'l':99750+i*10,'v':1000,'value':(100000+i*10)*1000} for i in range(count)]
    def positive(self):
        now=int(1793239200//900)*900
        quarter=self.series(200,900,now);five=self.series(300,300,now);one=self.series(200,60,now)
        level=max(b['h'] for b in five[-22:-2])
        five[-2].update(h=level+400,l=level-100,c=level+200,v=2000,value=(level+200)*2000)
        five[-1].update(h=level+300,l=level,c=level+150,v=1000,value=(level+150)*1000)
        one[-2].update(h=level+100,l=level-100,c=level)
        one[-1].update(h=level+250,l=level,c=level+180,v=1500,value=(level+180)*1500)
        return quarter,five,one,now
    def test_positive_15m_5m_1m_signal(self):
        q,f,o,n=self.positive();sig=evaluate_intraday(q,f,o,n)
        self.assertTrue(sig['ok'],sig);self.assertEqual(sig['closed_at'],n)
        self.assertEqual(sig['timeframe'],'15m/5m/1m')
    def test_forming_confirmation_bar_is_never_used(self):
        q,f,o,n=self.positive();o.append(o[-1]|{'t':n})
        self.assertFalse(evaluate_intraday(q,f,o,n)['ok'])
    def test_weak_one_minute_volume_blocks_entry(self):
        q,f,o,n=self.positive();o[-1]['v']=100
        sig=evaluate_intraday(q,f,o,n);self.assertFalse(sig['ok']);self.assertFalse(sig['checks']['1분 거래량 1.3배'])
    def test_low_recent_notional_and_chase_are_blocked(self):
        q,f,o,n=self.positive();o[-1]['value']=1000
        self.assertFalse(evaluate_intraday(q,f,o,n)['checks']['최근 거래대금'])
        q,f,o,n=self.positive();o[-1].update(c=o[-2]['c']*1.02,h=o[-2]['c']*1.03)
        self.assertFalse(evaluate_intraday(q,f,o,n)['checks']['급등 추격 차단'])
    def test_forty_five_minute_max_and_fifteen_minute_no_progress(self):
        p={'entry':100,'stop':98,'peak':100,'trough':100,'r':2,'atr':1,'since':0}
        self.assertEqual(exit_decision(p,100,{},2700),'최대 보유시간 종료')
        self.assertEqual(exit_decision(p,100,{'fresh':True,'close':99,'ema20':100},900),'단타 진행 실패')


if __name__=='__main__':unittest.main()
