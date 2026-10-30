import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from portfolio import Portfolio, tick, step_price, D, profile_signal, PROFILES
from trend_engine import Engine, ApiError


class FakeBroker:
    def __init__(self):
        self.rows=[{'currency':'KRW','balance':'100000','locked':'10000'},
                   {'currency':'BTC','balance':'1','locked':'.2','avg_buy_price':'90000','unit_currency':'KRW'},
                   {'currency':'OLD','balance':'7','locked':'0','avg_buy_price':'10','unit_currency':'KRW'}]
        self.sent=[]; self.results={}; self.external=[]; self.fail=None

    def get(self,path,params=None):
        if path=='/v1/accounts':return copy.deepcopy(self.rows)
        if path=='/v1/orders/chance':return {'bid_fee':'.0005','ask_fee':'.0005',
            'market':{'state':'active','bid':{'min_total':'5000'},'ask':{'min_total':'5000'},'max_total':'1000000000'}}
        if path=='/v1/orders/open':return self.external
        if path=='/v1/order':
            key=params['identifier']
            if key not in self.results:raise ApiError(404)
            return copy.deepcopy(self.results[key])
        raise AssertionError(path)

    def request(self,method,path,params):
        self.sent.append((method,path,params.copy()))
        if self.fail:raise self.fail
        result={'identifier':params['identifier'],'market':params.get('market','KRW-BTC'),
                'side':params.get('side','bid'),'state':'cancel' if method=='DELETE' else 'wait',
                'executed_volume':'0','paid_fee':'0','trades':[]}
        self.results[params['identifier']]=result
        return result


class Public:
    def __init__(self):self.price=100000;self.size=10;self.stale=False;self.current_tick='100'
    def __call__(self,path,params=None):
        if path=='/v1/market/all':return [{'market':'KRW-BTC','korean_name':'비트코인'},{'market':'KRW-ETH','korean_name':'이더리움'}]
        if path=='/v1/ticker/all':return [{'market':'KRW-BTC','trade_price':self.price},{'market':'KRW-ETH','trade_price':10000}]
        if path=='/v1/ticker':return [{'market':params['markets'],'trade_price':self.price}]
        if path=='/v1/orderbook/instruments':return [{'tick_size':self.current_tick}]
        if path=='/v1/orderbook':return [{'market':m,'timestamp':(time.time()-(20 if self.stale else 0))*1000,
            'orderbook_units':[{'ask_price':self.price,'bid_price':self.price,'ask_size':self.size,'bid_size':self.size}]} for m in params['markets'].split(',')]
        raise AssertionError(path)


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.e=Engine(self.root,network=False);self.b=FakeBroker();self.pub=Public()
        self.p=Portfolio(self.root,self.e,broker=self.b,public=self.pub)
        self.p.refresh()

    def tearDown(self):self.tmp.cleanup()
    def preview(self,mode='paper',side='bid',price='100000',volume='.1'):
        self.p.mode=mode
        return self.p.preview({'mode':mode,'market':'KRW-BTC','side':side,'price':price,'volume':volume})
    def order(self,**kw):
        t=self.preview(**kw);self.p.submit(t['ticket']);return self.p.state['orders'][t['mode']][-1]

    def test_whole_account_copy_includes_locked_cash_and_coins(self):
        v=self.p.view('paper');self.assertEqual(v['cash'],110000);self.assertEqual(v['total'],230000)
        self.assertEqual(v['unpriced'],['OLD']);self.assertAlmostEqual(v['pnl'],12000)
        self.assertFalse(self.p.settings('live','KRW-BTC')['auto']);self.assertEqual(self.b.sent,[])

    def test_live_available_excludes_locked(self):
        self.assertEqual(self.p.available('live','KRW'),100000);self.assertEqual(self.p.available('live','BTC'),1)
        with self.assertRaisesRegex(ValueError,'부족'):self.preview('live',volume='1')

    def test_current_price_tick_steps_and_tier_boundaries(self):
        self.assertEqual(step_price(100000,-1),'99950');self.assertEqual(step_price(99950,1),'100000')
        self.assertEqual(step_price(100,-1),'99.9');self.assertEqual(step_price(1000000,-1),'999500')
        self.assertEqual(tick(2000,'KRW-USDT'),1)
        self.assertEqual(self.p.quote('KRW-BTC','100000',-1)['price'],'99950')

    def test_policy_change_and_bad_tick_rejected(self):
        self.pub.current_tick='50'
        with self.assertRaisesRegex(ValueError,'정책 변경'):self.preview()
        self.pub.current_tick='100'
        with self.assertRaisesRegex(ValueError,'호가 단위'):self.preview(price='100001')

    def test_unmarketable_paper_order_reserves_then_cancel_releases_cash(self):
        o=self.order(price='99900');self.assertEqual(o['status'],'wait');self.assertEqual(o['filled'],0)
        self.assertAlmostEqual(float(self.p.available('paper','KRW')),100005.005)
        self.p.cancel('paper',o['identifier']);self.assertEqual(self.p.available('paper','KRW'),110000)
        self.assertEqual(self.b.sent,[])

    def test_paper_partial_fills_are_not_reapplied_for_same_quote(self):
        self.pub.size=.03;o=self.order()
        self.assertAlmostEqual(o['filled'],.03);self.p.paper_match(o);self.assertAlmostEqual(o['filled'],.03)
        before=self.p.state['paper']['cash'];self.p.quotes['KRW-BTC']['time']+=.001
        self.p.paper_match(o);self.assertAlmostEqual(o['filled'],.06)
        self.assertAlmostEqual(before-self.p.state['paper']['cash'],3001.5)

    def test_paper_sell_uses_only_unreserved_quantity(self):
        o=self.order(side='ask',price='100100',volume='1')
        self.assertEqual(o['status'],'wait');self.assertAlmostEqual(float(self.p.available('paper','BTC')),.2)
        with self.assertRaisesRegex(ValueError,'미체결'):self.order(side='ask')

    def test_minimum_and_non_finite_and_precision_rejected(self):
        for price,qty in [('NaN','.1'),('Infinity','.1'),('100000','0.001'),('100000','.123456789'),('-1','1')]:
            with self.subTest(price=price,qty=qty),self.assertRaises(ValueError):self.preview(price=price,volume=qty)

    def test_live_limit_params_and_durable_before_post(self):
        original=self.b.request
        def check(method,path,params):
            import json
            state=json.loads(self.p.path.read_text(encoding='utf-8'))
            self.assertEqual(state['orders']['live'][-1]['identifier'],params['identifier'])
            return original(method,path,params)
        self.b.request=check
        o=self.order(mode='live');params=self.b.sent[0][2]
        self.assertEqual(params['ord_type'],'limit');self.assertEqual(params['volume'],'0.1')
        self.assertEqual(o['status'],'wait');self.assertEqual(len(self.b.sent),1)

    def test_ambiguous_post_is_persisted_and_not_retried(self):
        self.b.fail=TimeoutError();t=self.preview('live');self.p.submit(t['ticket']);self.p.submit(t['ticket'])
        o=self.p.state['orders']['live'][-1];self.assertEqual(o['status'],'unknown');self.assertEqual(len(self.b.sent),1)
        restored=Portfolio(self.root,self.e,broker=self.b,public=self.pub)
        restored.refresh();self.assertEqual(len(self.b.sent),1);self.assertEqual(restored.active('live')[0]['status'],'unknown')

    def test_definite_rejection_remains_idempotent(self):
        self.b.fail=ApiError(400);t=self.preview('live')
        with self.assertRaises(ApiError):self.p.submit(t['ticket'])
        self.p.submit(t['ticket']);self.assertEqual(len(self.b.sent),1)
        self.assertEqual(self.p.state['orders']['live'][-1]['status'],'rejected')

    def test_mode_switch_invalidates_review(self):
        t=self.preview();self.p.action({'action':'portfolio_mode','mode':'live'})
        with self.assertRaisesRegex(ValueError,'만료'):self.p.submit(t['ticket'])
        self.assertEqual(self.b.sent,[])

    def test_expired_review_cannot_submit(self):
        t=self.preview();self.p.tickets[t['ticket']]['expires']=0
        with self.assertRaises(ValueError):self.p.submit(t['ticket'])

    def test_external_orders_block_new_live_orders(self):
        self.b.external=[{'uuid':'external'}]
        with self.assertRaisesRegex(ValueError,'미체결'):self.preview('live')

    def test_live_partial_fill_journal_is_cumulative_and_restart_safe(self):
        o=self.order(mode='live');r=self.b.results[o['identifier']]
        r.update(executed_volume='.04',paid_fee='2',trades=[{'volume':'.04','funds':'4000'}])
        self.p.reconcile(o);self.p.reconcile(o)
        self.assertEqual(len(self.p.state['journal']['live']),1)
        restored=Portfolio(self.root,self.e,broker=self.b,public=self.pub);ro=restored.active('live')[0]
        r.update(state='cancel',executed_volume='.06',paid_fee='3',trades=[{'volume':'.06','funds':'6000'}])
        restored.reconcile(ro)
        self.assertAlmostEqual(sum(t['volume'] for t in restored.state['journal']['live']),.06)
        self.assertAlmostEqual(sum(t['fee'] for t in restored.state['journal']['live']),3)
        self.assertFalse(restored.active('live'))

    def test_mismatched_fill_cannot_clear_pending_or_create_journal(self):
        o=self.order(mode='live');r=self.b.results[o['identifier']];r['executed_volume']='.1'
        with self.assertRaises(ValueError):self.p.reconcile(o)
        self.assertEqual(self.p.state['journal']['live'],[]);self.assertTrue(self.p.active('live'))

    def test_manual_holdings_never_auto_sell(self):
        self.p.settings('paper','KRW-BTC')['stop']=200000
        self.p.manage();self.assertFalse(self.p.state['orders']['paper']);self.assertEqual(self.b.sent,[])

    def test_category_move_preserves_manual_control_and_no_order(self):
        self.p.configure('paper','KRW-BTC','long',False)
        self.assertEqual(self.p.settings('paper','KRW-BTC')['bucket'],'long');self.assertEqual(self.b.sent,[])
        self.assertFalse(self.p.settings('live','KRW-BTC')['auto'])

    def test_delegation_requires_context_and_sets_stop_from_current_price(self):
        with patch.object(self.p,'get_context',return_value={'atr':1000}):
            self.p.configure('paper','KRW-BTC','long',True)
        cfg=self.p.settings('paper','KRW-BTC');self.assertEqual(cfg['stop'],97000)
        self.assertTrue(cfg['auto']);self.assertEqual(self.b.sent,[])
        self.p.configure('paper','KRW-BTC','long',False);self.assertFalse(self.p.settings('paper','KRW-BTC')['auto'])

    def test_live_holding_delegation_is_independent_of_paper_view_and_trial(self):
        self.p.mode='paper';self.p.running=True
        paper=copy.deepcopy(self.p.state['settings']['paper'])
        with patch.object(self.p,'get_context',return_value={'atr':1000}):
            self.p.action({'action':'portfolio_config','mode':'live','market':'KRW-BTC','bucket':'short','auto':True})
        cfg=self.p.settings('live','KRW-BTC')
        self.assertTrue(cfg['auto']);self.assertTrue(cfg['had_position'])
        self.assertEqual(cfg['bucket'],'short');self.assertEqual(cfg['stop'],98500)
        self.assertEqual(self.p.mode,'paper');self.assertTrue(self.p.running)
        self.assertEqual(self.p.state['settings']['paper'],paper);self.assertEqual(self.b.sent,[])

    def test_live_revoke_with_pending_order_does_not_cancel_or_resubmit(self):
        with patch.object(self.p,'get_context',return_value={'atr':1000}):
            self.p.configure('live','KRW-BTC','long',True)
        order=self.order(mode='live');before=copy.deepcopy(order);sent=copy.deepcopy(self.b.sent)
        self.p.mode='paper';self.p.running=True
        with patch.object(self.p,'get_context',side_effect=AssertionError('revocation must not need candles')):
            self.p.configure('live','KRW-BTC','long',False)
        self.assertFalse(self.p.settings('live','KRW-BTC')['auto'])
        self.assertEqual(order,before);self.assertEqual(self.b.sent,sent)
        self.assertTrue(self.p.running);self.assertEqual(self.p.mode,'paper')
        restored=Portfolio(self.root,self.e,broker=self.b,public=self.pub)
        self.assertFalse(restored.settings('live','KRW-BTC')['auto'])

    def test_pending_order_still_blocks_reclassification_or_new_delegation(self):
        self.order(mode='live');self.p.mode='paper'
        for bucket,auto in [('short',True),('long',False)]:
            with self.subTest(bucket=bucket,auto=auto),self.assertRaisesRegex(ValueError,'미체결'):
                self.p.configure('live','KRW-BTC',bucket,auto)
        self.assertFalse(self.p.settings('live','KRW-BTC')['auto'])

    def test_invalid_explicit_configuration_mode_fails_before_setting_changes(self):
        before=copy.deepcopy(self.p.state['settings'])
        with self.assertRaises(ValueError):self.p.configure('unknown','KRW-BTC','long',True)
        self.assertEqual(self.p.state['settings'],before);self.assertEqual(self.b.sent,[])

    def test_delegated_stop_works_on_candle_error_but_manual_coin_untouched(self):
        self.p.state['settings']['paper']['KRW-BTC']={'bucket':'long','auto':True,'stop':101000,'since':time.time()}
        with patch.object(self.p,'get_context',side_effect=ValueError('봉 지연')):self.p.manage()
        self.assertEqual(self.p.state['orders']['paper'][-1]['side'],'ask')
        self.assertEqual(self.b.sent,[])

    def test_live_automatic_new_entry_stays_blocked(self):
        self.p.mode='live'
        with self.assertRaisesRegex(ValueError,'검증'):self.p.action({'action':'portfolio_start'})
        self.assertFalse(self.p.running);self.assertEqual(self.b.sent,[])

    def test_legacy_exposure_blocks_second_order_manager(self):
        self.e.states['live']['positions']['KRW-BTC']={'volume':1}
        with self.assertRaisesRegex(ValueError,'이전 봇'):self.p.refresh()

    def test_stale_quote_blocks_orders(self):
        self.pub.stale=True
        with self.assertRaisesRegex(ValueError,'호가 갱신'):self.preview()

    def test_corrupt_state_fails_closed(self):
        self.p.path.write_text('{broken',encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError,'손상'):Portfolio(self.root,self.e)

    def test_explicit_live_order_does_not_change_view_or_paper_running(self):
        self.p.mode='paper';self.p.running=True
        t=self.p.preview({'mode':'live','market':'KRW-BTC','side':'bid','price':'100000','volume':'.1','bucket':'short'})
        self.p.submit(t['ticket'])
        self.assertEqual(self.p.mode,'paper');self.assertTrue(self.p.running)
        self.assertEqual(len(self.b.sent),1);self.assertEqual(self.p.state['orders']['paper'],[])
        self.assertEqual(self.p.settings('live','KRW-BTC')['bucket'],'short')

    def test_view_switch_does_not_pause_parallel_paper_trial(self):
        self.p.running=True;self.p.action({'action':'portfolio_mode','mode':'live'})
        self.assertTrue(self.p.running)

    def test_explicit_paper_quote_uses_virtual_balance_while_viewing_live(self):
        self.p.mode='live'
        q=self.p.action({'action':'portfolio_quote','mode':'paper','market':'KRW-BTC'})
        self.assertEqual(q['cash'],110000);self.assertEqual(self.b.sent,[])

    def test_new_trial_only_resets_virtual_assets_and_archives_old_ledger(self):
        before=copy.deepcopy(self.p.state['settings']['live']);accounts=copy.deepcopy(self.b.rows)
        rows=[{'market':'KRW-BTC','bucket':'swing'},{'market':'KRW-ETH','bucket':'short'}]
        with patch.object(self.p,'get_context',return_value={'atr':1000}):
            result=self.p.start_trial(rows,time.time())
        self.assertIn('모의 자동 검증 시작',result);self.assertTrue(self.p.running)
        self.assertEqual(self.p.state['paper']['cash'],230000);self.assertFalse(self.p.state['paper']['holdings'])
        self.assertEqual(self.p.state['settings']['live'],before);self.assertEqual(accounts,self.b.rows)
        self.assertEqual(self.b.sent,[]);self.assertEqual(len(list(self.root.glob('portfolio_trial_archive_*.json'))),1)

    def test_stale_ranking_cannot_start_trial(self):
        before=copy.deepcopy(self.p.state)
        with self.assertRaisesRegex(ValueError,'새 추천'):self.p.start_trial([{'market':'KRW-BTC','bucket':'long'}],time.time()-901)
        self.assertEqual(before,self.p.state)

    def test_paper_auto_entry_continues_while_real_view_is_selected(self):
        self.p.mode='live';self.p.running=True
        self.p.state['paper']['cash']=1000000  # Leave room under the new 50% total exposure cap.
        self.p.state['settings']['paper']['KRW-ETH']={'bucket':'short','auto':True,'had_position':False}
        with patch.object(self.p,'get_context',return_value={'atr':1000,'entry':True,'id':'new','close':100000,'closed_at':time.time()}),patch.object(self.e,'trade_policy',return_value={'allow_entry':True}):
            self.p.manage()
        self.assertTrue(self.p.state['orders']['paper']);self.assertFalse(self.p.state['orders']['live'])
        self.assertEqual(self.b.sent,[])

    def test_manual_unsupported_holding_can_move_without_order_permission(self):
        self.p.configure('live','KRW-OLD','long',False)
        self.assertEqual(self.p.settings('live','KRW-OLD')['bucket'],'long')
        self.assertFalse(self.p.settings('live','KRW-OLD')['auto']);self.assertEqual(self.b.sent,[])

    def test_auto_profile_move_never_lowers_existing_stop(self):
        with patch.object(self.p,'get_context',return_value={'atr':1000}):
            self.p.configure('paper','KRW-BTC','short',True)
            previous=self.p.settings('paper','KRW-BTC')['stop']
            self.p.configure('paper','KRW-BTC','long',True)
        cfg=self.p.settings('paper','KRW-BTC')
        self.assertGreaterEqual(cfg['stop'],previous);self.assertEqual(cfg['max_hours'],0)
        self.assertEqual(self.b.sent,[])

    def test_statistics_count_completed_round_trips_not_partial_sales(self):
        self.p.state['trial']={'cash_start':True}
        self.p.state['journal']['paper']=[
            {'market':'KRW-BTC','side':'bid','volume':1,'funds':100,'fee':1},
            {'market':'KRW-BTC','side':'ask','volume':.5,'funds':60,'fee':1}]
        self.assertEqual(self.p.comparison()['paper']['stats']['closed'],0)
        self.p.state['journal']['paper'].append({'market':'KRW-BTC','side':'ask','volume':.5,'funds':50,'fee':1})
        stats=self.p.comparison()['paper']['stats'];self.assertEqual(stats['closed'],1)
        self.assertEqual(stats['wins'],1);self.assertAlmostEqual(stats['net'],7)

    def test_order_classification_cannot_silently_move_existing_investment(self):
        self.p.configure('paper','KRW-BTC','long',False)
        with self.assertRaisesRegex(ValueError,'분류'):
            self.p.preview({'mode':'paper','market':'KRW-BTC','side':'bid','price':'100000','volume':'.1','bucket':'short'})


if __name__=='__main__':unittest.main()
