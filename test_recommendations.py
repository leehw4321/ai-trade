import copy
import datetime as dt
import tempfile
import time
import unittest
import types
from pathlib import Path
from unittest.mock import patch

from recommendations import Recommender,eligibility,features,closed_bars,score,VERSION

NOW=1793070000


def bars(unit,count=200,incomplete=True):
    end=NOW//unit*unit
    rows=[]
    for i in range(count):
        value=100000*(1+(count-i)/1000)
        rows.append({'candle_date_time_utc':dt.datetime.fromtimestamp(end-i*unit,dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'),
                     'opening_price':value*.999,'high_price':value*1.002,'low_price':value*.998,
                     'trade_price':value,'candle_acc_trade_price':1000000000*(1+(count-i)/100)})
    return rows


def meta(m='KRW-NEW'):
    return {'market':m,'korean_name':m[4:],'english_name':m[4:],
            'market_event':{'warning':False,'caution':{'PRICE_FLUCTUATIONS':False}}}


def ticker(m='KRW-NEW'):
    return {'market':m,'trade_price':120000,'trade_timestamp':NOW*1000,'acc_trade_price_24h':10000000000,
            'signed_change_rate':.01,'acc_trade_volume_24h':50000}


class Data:
    def __init__(self,count=35):
        self.catalog=[meta('KRW-NEW'+str(i)) for i in range(count)]
        self.calls=[];self.bad_book=False;self.bad_candle=set()
    def __call__(self,path,params=None):
        self.calls.append((path,copy.deepcopy(params)))
        if path=='/v1/market/all':return self.catalog
        if path=='/v1/ticker/all':
            return [ticker(m['market'])|{'acc_trade_price_24h':1000000000*(i+1)} for i,m in enumerate(self.catalog)]
        if '/candles/' in path:
            if params['market'] in self.bad_candle:raise RuntimeError('offline')
            unit=86400 if path.endswith('/days') else int(path.rsplit('/',1)[1])*60
            return bars(unit)
        if path=='/v1/orderbook':
            return [{'market':m,'timestamp':NOW*1000,'orderbook_units':[{'bid_price':100000,'ask_price':101000 if self.bad_book else 100050}]} for m in params['markets'].split(',')]
        raise AssertionError('Unexpected endpoint '+path)
    def external(self,path):
        if path=='/v2/chains':return [{'name':m['english_name'],'tokenSymbol':m['market'][4:],'tvl':10000000} for m in self.catalog]
        if path=='/protocols':return []
        if path.startswith('/v2/historicalChainTvl/'):
            return [{'date':NOW-i*86400,'tvl':10000000-i*100000} for i in range(40)]
        raise AssertionError(path)


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=Data();self.r=Recommender(self.root,self.source,self.source.external)
        self.clock=patch('time.time',return_value=NOW);self.clock.start()
    def tearDown(self):self.clock.stop();self.tmp.cleanup()

    def test_complete_universe_is_compared_not_fixed_symbols(self):
        self.r.scan(pace=False);s=self.r.snapshot();self.assertFalse(s['error'])
        self.assertEqual(s['counts']['universe'],35);self.assertEqual(s['counts']['daily_compared'],35)
        scanned={q['market'] for p,q in self.source.calls if p=='/v1/candles/days'}
        self.assertEqual(scanned,{m['market'] for m in self.source.catalog})
        for bucket in ('all','long','swing','short'):
            self.assertEqual(len(s['rankings'][bucket]),10)
            self.assertEqual(len({r['market'] for r in s['rankings'][bucket]}),10)
        self.assertTrue(all(r['market'].startswith('KRW-NEW') for r in s['rankings']['all']))

    def test_exchange_cautions_stables_stale_and_illiquid_are_excluded(self):
        t=ticker();m=meta()
        self.assertEqual(eligibility(m,t,NOW),'')
        m['market_event']['caution']['PRICE_FLUCTUATIONS']=True
        self.assertIn('경보',eligibility(m,t,NOW))
        self.assertIn('가격 고정',eligibility(meta('KRW-USDT'),t,NOW))
        self.assertIn('지연',eligibility(meta(),t|{'trade_timestamp':(NOW-301)*1000},NOW))
        self.assertIn('5억',eligibility(meta(),t|{'acc_trade_price_24h':1000},NOW))
        self.assertIn('확인 불가',eligibility({'market':'KRW-NEW'},t,NOW))

    def test_unclosed_candles_never_influence_features(self):
        raw=bars(86400);raw[0]['trade_price']=999999999
        rows=closed_bars(raw,86400,NOW)
        self.assertEqual(len(rows),199);self.assertLess(features(rows)['close'],200000)

    def test_recent_gaps_fail_closed(self):
        raw=bars(86400);del raw[5]
        with self.assertRaisesRegex(ValueError,'봉 부족'):closed_bars(raw,86400,NOW)

    def test_non_finite_data_rejected(self):
        raw=bars(900);raw[10]['trade_price']=float('nan')
        with self.assertRaises(ValueError):closed_bars(raw,900,NOW)

    def test_failed_symbol_does_not_abort_scan(self):
        self.source.bad_candle={'KRW-NEW0'};self.r.scan(pace=False)
        s=self.r.snapshot();self.assertEqual(s['counts']['daily_failed'],1)
        self.assertNotIn('KRW-NEW0',[r['market'] for r in s['rankings']['all']])

    def test_missing_growth_is_unknown_and_long_does_not_invent_fundamentals(self):
        self.r.external=lambda path:[];self.r.scan(pace=False);s=self.r.snapshot()
        self.assertEqual(s['rankings']['long'],[])
        self.assertTrue(s['rankings']['short'])
        r=s['rankings']['short'][0];self.assertEqual(r['fundamentals'],{})
        self.assertIn('실사용 성장 자료 미확인',r['risks'])

    def test_ambiguous_symbol_mapping_is_not_assumed(self):
        self.r.external=lambda path:([{'tokenSymbol':'NEW0','name':'Different project','tvl':10000}] if path=='/v2/chains' else [])
        self.assertEqual(self.r.fundamentals({m['market']:m for m in self.source.catalog}),{})

    def test_stale_chain_history_not_presented_as_current_growth(self):
        old=self.source.external
        self.r.external=lambda path:([{'date':NOW-10*86400,'tvl':10000}] if 'historicalChain' in path else old(path))
        self.assertEqual(self.r.fundamentals({m['market']:m for m in self.source.catalog}),{})

    def test_wide_or_missing_orderbook_disqualifies_final_candidates(self):
        self.source.bad_book=True;self.r.scan(pace=False)
        self.assertTrue(all(not rows for rows in self.r.snapshot()['rankings'].values()))

    def test_cached_last_result_is_marked_stale(self):
        self.r.scan(pace=False);r=Recommender(self.root,self.source,self.source.external)
        with patch('time.time',return_value=NOW+901):self.assertTrue(r.snapshot()['stale'])
        self.assertEqual(r.snapshot()['version'],VERSION)

    def test_candle_cache_expires_when_next_bar_can_close(self):
        self.r.bars('KRW-NEW0',86400);count=len(self.source.calls)
        self.r.bars('KRW-NEW0',86400);self.assertEqual(count,len(self.source.calls))

    def test_score_is_bounded_and_overheating_penalized(self):
        f=features(closed_bars(bars(86400),86400,NOW))
        r={'daily':f,'liquidity_score':100,'day_change':1,'fundamentals':{}}
        base=score(r,'short')[0];hot=score(r|{'day_change':30},'short')[0]
        self.assertLess(hot,base);self.assertTrue(0<=hot<=100)

    def test_monitor_interval_is_ten_minutes_between_starts(self):
        elapsed={'value':1000};waits=[]
        class Stop:
            done=False
            def is_set(self):return self.done
            def wait(self,seconds):waits.append(seconds);self.done=True;return True
        self.r.scan=lambda:elapsed.update(value=elapsed['value']+30)
        with patch('recommendations.time.monotonic',side_effect=lambda:elapsed['value']),patch('recommendations.threading.Thread',side_effect=lambda target,daemon:types.SimpleNamespace(start=target)):
            self.r.start(Stop())
        self.assertEqual(waits,[570]);self.assertEqual(self.r.snapshot()['next_check'],NOW+600)


if __name__=='__main__':unittest.main()
