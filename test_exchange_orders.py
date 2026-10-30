import copy
import json
import tempfile
import time
import unittest
from pathlib import Path

from portfolio import Portfolio
from trend_engine import Engine,ApiError
from test_portfolio import FakeBroker,Public


def row(key='external-1',side='bid',market='KRW-BTC'):
    return {'uuid':key,'market':market,'side':side,'ord_type':'limit','state':'wait','price':'100000',
        'volume':'.2','remaining_volume':'.15','executed_volume':'.05','locked':'15007.5' if side=='bid' else '.15',
        'paid_fee':'2.5','created_at':'2026-10-27T10:00:00+09:00','trades':[{'volume':'.05','funds':'5000'}]}


class ExchangeBroker(FakeBroker):
    def __init__(self):
        super().__init__();self.exchange={'external-1':row()};self.fail_after=False;self.reject=False;self.before_send=None;self.full_fill=False

    def get(self,path,params=None):
        if path=='/v1/orders/open':
            rows=[copy.deepcopy(o) for o in self.exchange.values() if o['state'] in ('wait','watch') and (not params.get('market') or o['market']==params['market'])]
            page=params.get('page',1);return rows[(page-1)*100:page*100]
        if path=='/v1/order':
            o=next((o for o in self.exchange.values() if o['uuid']==params.get('uuid') or o.get('identifier')==params.get('identifier','__none__')),None)
            if not o:raise ApiError(404)
            return copy.deepcopy(o)
        return super().get(path,params)

    def request(self,method,path,params):
        self.sent.append((method,path,copy.deepcopy(params)))
        if self.before_send:self.before_send(params)
        if self.reject:raise ApiError(400)
        old=self.exchange[params.get('prev_order_uuid',params.get('uuid'))]
        old['state']='done' if self.full_fill else 'cancel'
        result=copy.deepcopy(old)
        if method=='POST' and not self.full_fill:
            new=row('new-1',old['side'],old['market']);new.update(identifier=params['new_identifier'],price=params['new_price'],
                volume=old['remaining_volume'] if params['new_volume']=='remain_only' else params['new_volume'],executed_volume='0',paid_fee='0',trades=[])
            new['remaining_volume']=new['volume'];self.exchange[new['uuid']]=new
            result.update(new_order_uuid=new['uuid'],new_order_identifier=new['identifier'])
        if self.fail_after:raise TimeoutError()
        return result


class ExchangeOrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.engine=Engine(self.root,network=False)
        self.b=ExchangeBroker();self.p=Portfolio(self.root,self.engine,broker=self.b,public=Public());self.p.refresh()
    def tearDown(self):self.tmp.cleanup()
    def review(self,**kw):return self.p.exchange_preview({'uuid':'external-1','price':'99000',**kw})

    def test_lists_entire_exchange_with_pagination_without_importing_or_trading(self):
        self.b.exchange={str(i):row(str(i)) for i in range(101)}
        self.p.exchange_sync();x=self.p.snapshot()['exchange']
        self.assertEqual(len(x['orders']),101);self.assertFalse(x['stale']);self.assertEqual(self.b.sent,[])
        self.assertFalse(x['orders'][0]['owned']);self.assertEqual(self.p.state['orders']['live'],[])

    def test_failed_sync_retains_old_rows_with_visible_error(self):
        self.p.exchange_sync();before=self.p.exchange_view()['orders'];self.b.exchange={'bad':{}}
        self.p.exchange_sync();self.assertEqual(self.p.exchange_view()['orders'],before);self.assertTrue(self.p.exchange_error)

    def test_external_cancel_persists_before_delete_and_never_reposts_after_timeout(self):
        self.b.before_send=lambda _:self.assertEqual(json.loads(self.p.path.read_text(encoding='utf-8'))['exchange_requests'][-1]['status'],'submitting')
        self.b.fail_after=True;self.p.exchange_cancel('external-1');self.p.exchange_cancel('external-1')
        self.assertEqual(len(self.b.sent),1);self.assertEqual(self.p.exchange_pending()[0]['status'],'unknown')
        restored=Portfolio(self.root,self.engine,broker=self.b,public=Public());restored.exchange_sync()
        self.assertFalse(restored.exchange_pending());self.assertEqual(len(self.b.sent),1)
        self.assertEqual(restored.state['exchange_requests'][0]['status'],'done')

    def test_replacement_preserves_remaining_volume_and_commits_both_intents_before_post(self):
        review=self.review();self.assertEqual(self.b.sent,[])
        def check(params):
            state=json.loads(self.p.path.read_text(encoding='utf-8'));self.assertEqual(state['orders']['live'][-1]['identifier'],params['new_identifier'])
            self.assertEqual(state['exchange_requests'][-1]['status'],'submitting')
        self.b.before_send=check;self.p.exchange_submit(review['ticket']);self.p.exchange_submit(review['ticket'])
        self.assertEqual(len(self.b.sent),1);params=self.b.sent[0][2]
        self.assertEqual(params['new_volume'],'remain_only');self.assertEqual(params['new_price'],'99000')
        self.p.exchange_sync();self.assertFalse(self.p.exchange_pending());self.assertEqual(self.p.active('live')[0]['status'],'wait')
        self.assertEqual(self.p.state['journal']['live'],[])  # Original external fills are not invented app trades.

    def test_timeout_after_new_order_is_created_recovers_without_post_on_restart(self):
        self.b.fail_after=True;self.p.exchange_submit(self.review()['ticket'])
        restored=Portfolio(self.root,self.engine,broker=self.b,public=Public());restored.exchange_sync()
        self.assertEqual(len(self.b.sent),1);self.assertFalse(restored.exchange_pending());self.assertEqual(restored.active('live')[0]['status'],'wait')

    def test_partial_fill_during_review_reduces_remain_only_order(self):
        review=self.review();self.b.exchange['external-1']['remaining_volume']='.1'
        self.p.exchange_submit(review['ticket']);self.p.exchange_sync()
        self.assertEqual(float(self.p.state['orders']['live'][-1]['volume']),.1)

    def test_custom_quantity_is_revalidated_after_intervening_fill(self):
        review=self.review(remain_only=False,volume='.12');self.b.exchange['external-1']['remaining_volume']='.1'
        with self.assertRaisesRegex(ValueError,'잔량'):self.p.exchange_submit(review['ticket'])
        self.assertEqual(self.b.sent,[])

    def test_price_precision_balance_and_expiry_rejected_without_post(self):
        for kw in ({'price':'NaN'},{'price':'99001'},{'volume':'.16','remain_only':False},{'volume':'.123456789','remain_only':False},{'price':'1000000'}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):self.review(**kw)
        review=self.review();self.p.exchange_reviews[review['ticket']]['expires']=0
        with self.assertRaises(ValueError):self.p.exchange_submit(review['ticket'])
        self.assertEqual(self.b.sent,[])

    def test_exchange_rejection_does_not_cancel_original_and_clears_new_intent(self):
        self.b.reject=True
        with self.assertRaises(ApiError):self.p.exchange_submit(self.review()['ticket'])
        self.assertEqual(self.b.exchange['external-1']['state'],'wait');self.assertFalse(self.p.exchange_pending())
        self.assertEqual(self.p.state['orders']['live'][-1]['status'],'rejected')

    def test_fully_filled_before_cancel_creates_no_new_order(self):
        self.b.full_fill=True;self.p.exchange_submit(self.review()['ticket'])
        self.assertFalse(self.p.exchange_pending());self.assertFalse(self.p.active('live'));self.assertEqual(len(self.b.exchange),1)

    def test_other_currency_order_can_be_listed_and_cancelled_but_not_modified(self):
        self.b.exchange['external-1']['market']='BTC-ETH';self.p.exchange_sync()
        self.assertEqual(self.p.exchange_view()['orders'][0]['market'],'BTC-ETH')
        with self.assertRaises(ValueError):self.review()
        self.p.exchange_cancel('external-1');self.p.exchange_sync();self.assertFalse(self.p.exchange_pending())

    def test_unresolved_exchange_operation_blocks_new_live_orders_but_not_paper(self):
        self.b.fail_after=True;self.p.exchange_cancel('external-1')
        with self.assertRaisesRegex(ValueError,'취소·수정'):self.p.terms('live','KRW-ETH','bid','100000','.1')
        self.assertEqual(self.p.terms('paper','KRW-ETH','bid','100000','.1')['mode'],'paper')

    def test_unknown_existing_bot_order_blocks_exchange_replacement(self):
        self.p.state['orders']['live'].append({'identifier':'pf-pending','status':'unknown','market':'KRW-ETH'})
        with self.assertRaisesRegex(ValueError,'미확인'):self.review()
        self.assertEqual(self.b.sent,[])


if __name__=='__main__':unittest.main()
