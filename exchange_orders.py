"""Exchange-wide open orders and durable, explicitly confirmed amendments."""
import copy
import datetime as dt
import time
import uuid

from trend_engine import ApiError
import upbit_bot as legacy


class ExchangeOrders:
    def exchange_pending(self):
        return [r for r in self.state.get('exchange_requests',[]) if r['status'] in ('submitting','unknown','accepted')]

    def exchange_guard(self):
        if self.exchange_pending():
            raise ValueError('업비트 주문 취소·수정 결과를 확인 중입니다. 재전송 없이 조회를 기다리세요.')

    def exchange_sync(self):
        # Network reads stay outside the portfolio lock so balances keep streaming.
        try:
            rows=[];seen=set()
            for page in range(1,101):
                batch=self.api().get('/v1/orders/open',{'states[]':['wait','watch'],'limit':100,'page':page,'order_by':'desc'})
                if not isinstance(batch,list):raise ValueError('미체결 목록 응답 확인 필요')
                for row in batch:
                    if not row.get('uuid') or row['uuid'] in seen:raise ValueError('미체결 목록 중복·누락 확인 필요')
                    seen.add(row['uuid']);rows.append(row)
                if len(batch)<100:break
            else:raise ValueError('미체결 주문이 많아 전체 조회를 완료하지 못했습니다.')
            with self.lock:
                self.exchange_open=rows;self.exchange_time=time.time();self.exchange_error=''
        except Exception:
            with self.lock:self.exchange_error='업비트 미체결 조회 지연 · 이전 목록입니다. 주문조회 권한·연결을 확인하세요.'
        with self.lock:requests=copy.deepcopy(self.exchange_pending())
        for request in requests:
            try:self.exchange_reconcile(request['request'])
            except Exception:pass  # Retain uncertainty; never repeat a mutation.

    def exchange_loop(self):
        while not self.engine.stop_event.is_set():
            self.exchange_sync()
            self.exchange_wake.wait(5);self.exchange_wake.clear()

    def exchange_order(self,order_uuid):
        if not isinstance(order_uuid,str) or not order_uuid or len(order_uuid)>128:
            raise ValueError('업비트 주문 식별자를 확인하세요.')
        row=self.api().get('/v1/order',{'uuid':order_uuid})
        if row.get('uuid')!=order_uuid:raise ValueError('업비트 주문 식별 불일치')
        return row

    def exchange_view(self):
        rows=[]
        for r in self.exchange_open:
            try:
                created=dt.datetime.fromisoformat(r['created_at']).timestamp()
                rows.append({'uuid':r['uuid'],'identifier':r.get('identifier'),'market':r['market'],
                    'side':r['side'],'ord_type':r['ord_type'],'state':r['state'],'price':r.get('price'),
                    'volume':r.get('volume'),'remaining':r['remaining_volume'],'filled':r['executed_volume'],
                    'created':created,'locked':r.get('locked',0),
                    'owned':any(o['identifier']==r.get('identifier') for o in self.state['orders']['live'])})
            except (KeyError,ValueError,TypeError):continue
        return {'orders':rows,'time':self.exchange_time,'error':self.exchange_error,
                'stale':time.time()-self.exchange_time>15,'requests':self.state.get('exchange_requests',[])}

    def exchange_preview(self,body):
        from portfolio import number,tick,D
        with self.lock:
            self.exchange_guard();self.legacy_guard()
            if any(o['status'] in ('unknown','submitting') for o in self.active('live')):
                raise ValueError('실거래 미확인 주문을 먼저 조회·복구하세요. 중복 주문을 차단합니다.')
            row=self.exchange_order(body.get('uuid'))
            if row.get('state')!='wait' or row.get('ord_type')!='limit' or not row.get('market','').startswith('KRW-'):
                raise ValueError('수정은 체결 대기 중인 원화 지정가 주문에서 가능합니다. 다른 주문은 취소할 수 있습니다.')
            market=row['market'];side=row['side'];self.valid_market(market)
            if legacy.paper_day_active():raise ValueError('실거래 차단일입니다.')
            price=number(body.get('price'),True);remaining=number(row['remaining_volume'],True)
            remain_only=body.get('remain_only',True)
            if type(remain_only) is not bool:raise ValueError('잔량 유지 설정을 확인하세요.')
            volume=remaining if remain_only else number(body.get('volume'),True)
            if volume>remaining:raise ValueError('새 수량은 현재 미체결 잔량 이하여야 합니다.')
            if volume!=volume.quantize(D('.00000001')) or price%tick(price,market):raise ValueError('가격 호가 단위·수량 소수점 8자리를 확인하세요.')
            self.quote(market,str(price));self.fresh_account()
            chance=self.api().get('/v1/orders/chance',{'market':market})
            terms=chance.get('market',{})
            if terms.get('state')!='active':raise ValueError('현재 거래 가능한 마켓이 아닙니다.')
            fee=number(chance[side+'_fee'],True);total=price*volume
            if not number(terms[side]['min_total'],True)<=total<=number(terms.get('max_total',1000000000),True):
                raise ValueError('수정 주문 금액이 거래소 최소·최대 주문 금액에 맞지 않습니다.')
            # Only the selected order's reserved funds are released, not other orders' locks.
            currency='KRW' if side=='bid' else market[4:]
            release=number(row['locked']) if side=='bid' else remaining
            needed=total*(1+fee) if side=='bid' else volume
            if needed>self.available('live',currency)+release:raise ValueError('수정 후 주문에 필요한 잔고가 부족합니다.')
            key=uuid.uuid4().hex
            review={'ticket':key,'uuid':row['uuid'],'market':market,'side':side,'price':str(price),
                'volume':str(volume),'remain_only':remain_only,'remaining':str(remaining),'old_price':row['price'],
                'total':float(total),'estimated_fee':float(total*fee),'rate':float(fee),'expires':time.time()+30,
                'time_in_force':row.get('time_in_force'),'smp_type':row.get('smp_type')}
            self.exchange_reviews={k:v for k,v in self.exchange_reviews.items() if v['expires']>time.time()}
            self.exchange_reviews[key]=review
            return review.copy()

    def exchange_submit(self,ticket):
        with self.lock:
            if any(r.get('ticket')==ticket for r in self.state.get('exchange_requests',[])):
                return '이미 제출한 주문 수정입니다. 결과만 조회하며 재전송하지 않습니다.'
            review=self.exchange_reviews.get(ticket)
            if not review or review['expires']<time.time():raise ValueError('수정 검토가 만료됐습니다. 다시 검토하세요.')
            # Fresh exchange/account checks immediately before recording intent.
            current=self.exchange_preview({'uuid':review['uuid'],'price':review['price'],
                'volume':review['volume'],'remain_only':review['remain_only']})
            if any(current[k]!=review[k] for k in ('market','side','old_price','time_in_force','smp_type')):
                raise ValueError('기존 주문 조건이 변경됐습니다. 다시 검토하세요.')
            request_id=uuid.uuid4().hex;identifier='pf-'+uuid.uuid4().hex
            avg=next((h['avg'] for h in self.view('live')['holdings'] if h['market']==review['market']),0)
            order={k:current[k] for k in ('market','side','price','volume','rate','total','estimated_fee')}
            order.update(mode='live',identifier=identifier,status='submitting',created=time.time(),
                filled=0,funds=0,fee=0,basis=avg,automatic=False,message='업비트 주문 수정 확인 중',exchange_request=request_id)
            request={'request':request_id,'ticket':ticket,'kind':'replace','uuid':review['uuid'],
                'market':review['market'],'new_identifier':identifier,'status':'submitting','created':time.time(),'message':''}
            self.state['orders']['live'].append(order);self.state.setdefault('exchange_requests',[]).append(request)
            self.save()  # Both intent and new identifier survive a timeout or restart.
            params={'prev_order_uuid':review['uuid'],'new_identifier':identifier,'new_ord_type':'limit',
                'new_price':review['price'],'new_volume':'remain_only' if review['remain_only'] else current['volume']}
            if current['time_in_force']:params['new_time_in_force']=current['time_in_force']
            if current['smp_type']:params['new_smp_type']=current['smp_type']
            else:params['new_smp_type']='cancel_taker'
            if params.get('new_time_in_force')=='post_only':params.pop('new_smp_type',None)
            try:
                result=self.private('POST','/v1/orders/cancel_and_new',params)
                if result.get('uuid')!=review['uuid']:raise ValueError('수정 응답 식별 불일치')
                request.update(status='accepted',new_uuid=result.get('new_order_uuid'))
                if result.get('state')=='done' and not result.get('new_order_uuid') and not result.get('new_order_identifier'):
                    request.update(status='done',message='취소 전 전량 체결 · 새 주문 없음')
                    order.update(status='rejected',message=request['message'])
            except ApiError as e:
                if e.status in (400,401,403,404,422):
                    request.update(status='rejected',message='수정 요청 거절 · 기존 주문 상태를 확인하세요.')
                    order.update(status='rejected',message=request['message']);self.save();raise
                request['status']='unknown'
            except Exception:request['status']='unknown'
            if order['status']=='submitting':order['status']='unknown'
            self.save();self.account_wake.set();self.exchange_wake.set()
            return '수정 요청 전송 · 기존 주문 취소 후 새 주문의 접수 결과를 조회합니다. 재전송하지 마세요.'

    def exchange_cancel(self,order_uuid):
        with self.lock:
            if any(r['uuid']==order_uuid and r['status'] in ('submitting','unknown','accepted') for r in self.state.get('exchange_requests',[])):
                return '이 주문의 이전 요청 결과를 조회 중입니다. 취소를 재전송하지 않습니다.'
            row=self.exchange_order(order_uuid)
            if row.get('state') not in ('wait','watch'):raise ValueError('이미 체결·취소된 주문입니다. 목록을 갱신하세요.')
            request={'request':uuid.uuid4().hex,'kind':'cancel','uuid':order_uuid,'market':row['market'],
                'status':'submitting','created':time.time(),'message':''}
            self.state.setdefault('exchange_requests',[]).append(request);self.save()
            try:
                result=self.private('DELETE','/v1/order',{'uuid':order_uuid})
                if result.get('uuid')!=order_uuid:raise ValueError('취소 응답 식별 불일치')
                request['status']='accepted'
            except ApiError as e:
                if e.status in (400,401,403,404,422):
                    request.update(status='rejected',message='취소 요청 거절 · 거래소 상태를 다시 확인하세요.');self.save();raise
                request['status']='unknown'
            except Exception:request['status']='unknown'
            self.save();self.account_wake.set();self.exchange_wake.set()
            return '취소 요청 전송 · 최종 상태는 업비트 조회 결과를 확인하세요.'

    def exchange_reconcile(self,request_id):
        with self.lock:
            request=next(r.copy() for r in self.exchange_pending() if r['request']==request_id)
        old=self.exchange_order(request['uuid'])
        new=None
        if request['kind']=='replace':
            try:new=self.api().get('/v1/order',{'identifier':request['new_identifier']})
            except ApiError as e:
                if e.status!=404:raise
        with self.lock:
            stored=next(r for r in self.state['exchange_requests'] if r['request']==request_id)
            owned=next((o for o in self.state['orders']['live'] if o['identifier']==old.get('identifier')),None)
            if owned:self.apply_live(owned,old)
            if request['kind']=='cancel' and old['state'] in ('cancel','done'):
                stored.update(status='done',message='취소 확인' if old['state']=='cancel' else '취소 전 체결 완료')
            elif new is not None:
                target=next(o for o in self.state['orders']['live'] if o['identifier']==request['new_identifier'])
                self.apply_live(target,new);stored.update(status='done',message='새 주문 접수 확인',new_uuid=new.get('uuid'))
            else:stored['message']='거래소 최종 결과 확인 중 · 재전송 없음'
            self.save()
