import copy
import tempfile
import unittest
from pathlib import Path
from breakout_strategy import TradeWindow, prepare_breakout, breakout_plan
from auto_trader import AutoTrader
from scalping_strategy import RULES, exit_decision
from test_auto_v3 import book
from replay_auto_v3 import replay


NOW = 1793235720.


def trade(stamp, price=100500, side='BID', sequence=None):
    return {'code': 'KRW-BTC', 'stream_type': 'REALTIME', 'trade_timestamp': stamp*1000,
            'trade_price': price, 'trade_volume': 2, 'ask_bid': side,
            'sequential_id': int(stamp*1000) if sequence is None else sequence}


def context():
    return {'closed_at': NOW, 'level': 100000., 'atr': 4000., 'ema20': 99000.,
            'rising': True, 'vwap': 99000., 'baseline': 1_000_000., 'stop_price': 98000.}


def window_ready():
    w=TradeWindow()
    for sec in range(61):w.ingest(trade(NOW-60+sec), NOW-60+sec)
    return w


def signal():
    w=window_ready();w.signal(context(), NOW)
    for sec in range(1,7):w.ingest(trade(NOW+sec),NOW+sec)
    return w.signal(context(), NOW+6)


class BreakoutTests(unittest.TestCase):
    def test_real_flow_confirmed_without_pullback(self):
        s=signal()
        self.assertTrue(s['ok'],s)
        self.assertEqual(s['strategy'],'breakout')
        self.assertEqual(s['buy_ratio'],1)

    def test_below_level_print_and_gap_reset_confirmation(self):
        w=window_ready();w.signal(context(),NOW)
        w.ingest(trade(NOW+1,99900),NOW+1)
        w.ingest(trade(NOW+2),NOW+2)
        self.assertFalse(w.signal(context(),NOW+2)['ok'])
        w.ingest(trade(NOW+10),NOW+10)
        self.assertFalse(w.signal(context(),NOW+10)['checks']['실시간 체결 60초 준비'])

    def test_duplicate_snapshot_stale_future_rejected(self):
        w=TradeWindow();t=trade(NOW)
        self.assertTrue(w.ingest(t,NOW))
        for bad in [t, t|{'stream_type':'SNAPSHOT'}, trade(NOW-3), trade(NOW+1), t|{'trade_price':float('nan')}]:
            self.assertFalse(w.ingest(bad,NOW))
        self.assertEqual(len(w.seconds),1)
        self.assertEqual(w.seconds[0]['value'],201000)

    def test_sell_flow_wide_chase_and_stale_baseline_block(self):
        w=window_ready();w.signal(context(),NOW)
        for sec in range(1,12):w.ingest(trade(NOW+sec,side='ASK'),NOW+sec)
        self.assertFalse(w.signal(context(),NOW+11)['ok'])
        self.assertFalse(w.signal(context(),NOW+80)['ok'])
        w.ingest(trade(NOW+81,103000),NOW+81)
        self.assertFalse(w.signal(context()|{'closed_at':NOW+60},NOW+81)['checks']['돌파선 0.5 ATR 이내'])

    def test_baseline_excludes_forming_or_future_bars(self):
        rows=[{'t':NOW-60*(80-i),'h':100+i*2,'l':95+i*2,'c':98+i*2,'v':10000,'value':1000000} for i in range(80)]
        ctx=prepare_breakout(rows,NOW)
        self.assertEqual(ctx['level'],rows[-1]['h'])
        with self.assertRaises(ValueError):prepare_breakout(rows+[rows[-1]|{'t':NOW}],NOW)

    def test_cost_risk_depth_chase_and_ticks(self):
        s=signal();q={'time':NOW+6,'units':book(100500,100600)}
        plan=breakout_plan(s,q,1_000_000,1_000_000,0,0,NOW+6,100)
        self.assertLessEqual(plan['amount']*((plan['price']-plan['stop'])/plan['price']+plan['cost_rate']),2500)
        self.assertTrue(plan['ioc'])
        for quote in [q|{'time':NOW},q|{'units':book(100500,101000)},q|{'units':book(100500,100600,.00001)}]:
            with self.assertRaises(ValueError):breakout_plan(s,quote,1e6,1e6,0,0,NOW+6,100)
        with self.assertRaises(ValueError):breakout_plan(s|{'stop_price':100400},q,1e6,1e6,0,0,NOW+6,100)

    def test_engine_ioc_ledger_failure_exit_restore(self):
        with tempfile.TemporaryDirectory() as root:
            a=AutoTrader(Path(root));a.action({'action':'auto_v3_config','budget':1000000,'markets':['KRW-BTC']})
            a.running=True;a.candidates=['KRW-BTC'];a.breakout_signals['KRW-BTC']=signal()
            a.ingest('KRW-BTC',book(100500,100600),NOW+6,now=NOW+6)
            a.step(NOW+6)
            self.assertIn('KRW-BTC',a.state['positions'],a.status)
            p=a.state['positions']['KRW-BTC'];self.assertEqual(p['strategy'],'breakout')
            self.assertEqual(p['stop'],98000)
            a.validate();restored=AutoTrader(Path(root));restored.validate()
            self.assertEqual(restored.state['positions']['KRW-BTC']['strategy'],'breakout')
            a.ingest('KRW-BTC',book(99900,100000),NOW+7,now=NOW+7);a.step(NOW+7)
            self.assertFalse(a.state['positions'])
            closed=a.state['closed'][0]
            self.assertEqual(closed['strategy'],'breakout')
            self.assertIn('돌파 실패',closed['reason'])
            a.validate()

    def test_live_ingestion_monitor_and_disconnect_warmup(self):
        with tempfile.TemporaryDirectory() as root:
            a=AutoTrader(Path(root));a.action({'action':'auto_v3_config','budget':1000000,'markets':['KRW-BTC']})
            a.running=True;a.candidates=['KRW-BTC'];a.breakout_contexts['KRW-BTC']=context()
            for sec in range(-60,1):a.ingest_trade(trade(NOW+sec),NOW+sec)
            for sec in range(7):
                a.ingest_trade(trade(NOW+sec,sequence=999000+sec),NOW+sec)
                a.ingest('KRW-BTC',book(100500,100600),NOW+sec,now=NOW+sec)
                a.step(NOW+sec)
            self.assertEqual(a.state['positions']['KRW-BTC']['strategy'],'breakout')
            self.assertEqual(sum(o['side']=='bid' for o in a.state['orders']),1)
            a.validate()
            a.ingest_trade(None,NOW+8)
            self.assertFalse(a.trade_windows)
            self.assertFalse(a.breakout_signals)
            a.ingest_trade(trade(NOW+9),NOW+9)
            a.step(NOW+9)
            self.assertFalse(a.breakout_signals['KRW-BTC']['ok'])

    def test_stopped_engine_observes_but_never_orders(self):
        with tempfile.TemporaryDirectory() as root:
            a=AutoTrader(Path(root));a.action({'action':'auto_v3_config','budget':1000000,'markets':['KRW-BTC']})
            a.candidates=['KRW-BTC'];a.breakout_signals['KRW-BTC']=signal()
            a.ingest('KRW-BTC',book(100500,100600),NOW+6,now=NOW+6);a.step(NOW+6)
            self.assertFalse(a.state['orders'])

    def test_universe_promotes_acceleration_only_after_warmup(self):
        with tempfile.TemporaryDirectory() as root:
            markets=[f'KRW-X{i}' for i in range(20)]
            catalog=[{'market':m} for m in markets]
            ticks=[{'market':m,'trade_price':100500,'trade_timestamp':NOW*1000,
                    'acc_trade_price_24h':2e9-i*1e7,'signed_change_rate':.01} for i,m in enumerate(markets)]
            a=AutoTrader(Path(root),public=lambda path,params:catalog if path=='/v1/market/all' else ticks)
            a.trade_windows[markets[-1]]=window_ready()
            a.refresh_universe(NOW)
            self.assertIn(markets[-1],a.fast_candidates)
            self.assertIn(markets[-1],a.candidates)
            self.assertLessEqual(len(a.candidates),16)
            a.trade_windows[markets[-1]]=TradeWindow()
            a.refresh_universe(NOW)
            self.assertFalse(a.fast_candidates)

    def test_early_failure_only_short_first_minute(self):
        p={'since':NOW,'entry':100500,'stop':98000,'peak':100500,'r':2500,'atr':4000,
           'strategy':'breakout','breakout_level':100000,'bucket':'short'}
        self.assertIn('돌파 실패',exit_decision(copy.deepcopy(p),99900,{},NOW+30))
        self.assertNotIn('돌파 실패',exit_decision(copy.deepcopy(p),99900,{},NOW+61))
        self.assertNotIn('돌파 실패',exit_decision(p|{'bucket':'swing'},99900,{},NOW+30))

    def test_replay_breakout_signal_no_network(self):
        s=signal()
        events=[{'time':NOW+6,'type':'breakout_signal','market':'KRW-BTC','signal':s},
                {'time':NOW+6,'type':'book','market':'KRW-BTC','book':{'time':NOW+6,'units':book(100500,100600)}}]
        result=replay(events)
        self.assertEqual(result['state']['positions']['KRW-BTC']['strategy'],'breakout')
        self.assertEqual(result['real_orders_submitted'],0)
