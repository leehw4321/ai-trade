"""Whole-account portfolios. One durable order journal; never retry an ambiguous POST.

The analysis engine is read-only in the web app. This service exclusively owns new
orders. Existing holdings default to manual; delegation is explicit and per mode.
"""
import copy
import datetime as dt
import json
import math
import threading
import time
import uuid
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_CEILING

from trend_engine import Broker, ApiError, atomic, http, ema
import upbit_bot as legacy
from observations import period as observation_period, until as observation_until
from exchange_orders import ExchangeOrders
from investment_profiles import PROFILES
from profile_candles import load_closed
from scalping_strategy import evaluate_intraday

TIERS = [(1000000,1000),(500000,500),(100000,100),(50000,50),(10000,10),
         (5000,5),(100,1),(10,.1),(1,.01),(.1,.001),(.01,.0001),(.001,.00001),
         (.0001,.000001),(.00001,.0000001),(0,.00000001)]
D = lambda x: Decimal(str(x))


def number(x, positive=False):
    try:
        v = D(x)
        if not v.is_finite() or v < 0 or (positive and v == 0):
            raise ValueError()
        return v
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('0 이상의 유효한 숫자를 입력하세요.') from None


def tick(price, market=''):
    p = number(price, True)
    if market in ('KRW-USDT', 'KRW-USDC') and 1000 <= p < 10000:
        return D(1)
    return next(D(t) for floor,t in TIERS if p >= D(floor))


def step_price(price, direction, market=''):
    p = number(price, True)
    # On a tier boundary the preceding tick belongs to the *lower* tier.
    t = tick(p if direction > 0 else max(D('.00000001'), p-D('.000000001')), market)
    return str(max(D('.00000001'), p+t*(1 if direction > 0 else -1)))


def profile_signal(rows, profile, now):
    from research_v4 import trend_assessment
    unit = profile['unit'] * 60
    if len(rows) < 200 or not 0 <= now-(rows[-1]['t']+unit) < unit:
        raise ValueError('마감 봉 준비·갱신 확인 중')
    if any(b['t']-a['t'] != unit for a,b in zip(rows, rows[1:])):
        raise ValueError('봉 누락: 자동 관리 대기')
    closes = [b['c'] for b in rows]
    tr = [max(b['h']-b['l'],abs(b['h']-a['c']),abs(b['l']-a['c'])) for a,b in zip(rows, rows[1:])]
    atr = sum(tr[:14])/14
    for value in tr[14:]:
        atr = (atr*13+value)/14
    if not math.isfinite(atr) or atr<=0:
        raise ValueError('변동성 지표 확인 불가')
    vol = sum(b['v'] for b in rows[-21:-1])/20
    ready = (closes[-1] > ema(closes,50) > ema(closes,200) and ema(closes,50) > ema(closes[:-1],50)
             and closes[-1] > max(b['h'] for b in rows[-21:-1]) and vol > 0
             and rows[-1]['v'] >= vol*profile.get('volume_ratio',1.5))
    research = trend_assessment(rows, profile['unit'], now)
    return {'atr': atr, 'entry': ready and research['trend_ok'], 'id': str(rows[-1]['t']), 'close': closes[-1], 'research_v4': research}


def new_state():
    return {'version': 1, 'seeded': False, 'paper': {'cash': 0, 'holdings': {}, 'initial': 0},
            'settings': {'paper': {}, 'live': {}}, 'orders': {'paper': [], 'live': []},
            'journal': {'paper': [], 'live': []}}


class Portfolio(ExchangeOrders):
    def __init__(self, root, engine, broker=None, public=None):
        self.profiles = lambda: PROFILES
        self.path = root/'portfolio.json'
        self.engine = engine
        self.broker = broker
        self.public = public or (lambda path, params=None: http('GET',path,params))
        self.lock = threading.RLock()
        self.mode = 'paper'
        self.running = False
        self.status = '직접 주문 대기 · 자동 신규 매수 정지'
        self.error = ''
        self.accounts = []
        self.account_time = 0
        self.account_rest_time = 0
        self.account_request_started = 0
        self.account_events = {}
        self.account_wake = threading.Event()
        self.account_error = ''
        self.market_time = 0
        self.catalog = {}
        self.tickers = {}
        self.quotes = {}
        self.context = {}
        self.candle_series = {}
        self.tickets = {}
        self.selection_reviews = {}
        self.exchange_open=[];self.exchange_time=0;self.exchange_error=''
        self.exchange_reviews={};self.exchange_wake=threading.Event()
        self.last_catalog = 0
        self.state = new_state()
        if self.path.exists():
            try:
                self.state = json.loads(self.path.read_text(encoding='utf-8'))
                self._validate()
            except Exception:
                raise RuntimeError('포트폴리오 상태 파일 손상: 주문 기록을 복구해야 합니다.') from None

    def _validate(self):
        s = self.state
        if not isinstance(s,dict) or s.get('version') != 1 or not set(new_state()) <= set(s):
            raise ValueError()
        number(s['paper']['cash'])
        for h in s['paper']['holdings'].values():
            number(h['qty']); number(h['avg'])
        for mode in ('paper','live'):
            for m,c in s['settings'][mode].items():
                if not m.startswith('KRW-') or c['bucket'] not in PROFILES or type(c['auto']) is not bool:
                    raise ValueError()
            for o in s['orders'][mode]:
                if o['side'] not in ('bid','ask') or not o['identifier'].startswith('pf-'):
                    raise ValueError()
                number(o['price'],True); number(o['volume'],True)
                for k in ('filled','funds','fee'):
                    number(o[k])
                if o['status'] not in ('submitting','unknown','wait','done','cancel','rejected'):
                    raise ValueError()
        for r in s.get('exchange_requests',[]):
            if (r.get('kind') not in ('cancel','replace') or not isinstance(r.get('uuid'),str)
                    or not r.get('request') or r.get('status') not in ('submitting','unknown','accepted','done','rejected')):
                raise ValueError()
            if r['kind']=='replace' and not any(o['identifier']==r.get('new_identifier') for o in s['orders']['live']):
                raise ValueError()

    def save(self):
        atomic(self.path,self.state)

    def api(self):
        if self.broker is None:
            self.broker = Broker()
        return self.broker

    def private(self, method, path, params):
        if method == 'GET':
            return self.api().get(path,params)
        if hasattr(self.api(),'request'):
            return self.api().request(method,path,params)
        return http(method,path,params,self.api().api)

    def active(self, mode, market=None):
        return [o for o in self.state['orders'][mode]
                if o['status'] in ('submitting','unknown','wait') and (market is None or o['market']==market)]

    def settings(self, mode, market):
        return self.state['settings'][mode].setdefault(market,{'bucket':'swing','auto':False})

    def legacy_guard(self):
        with self.engine.lock:
            s = self.engine.states['live']
            if s['positions'] or s['pending']:
                raise ValueError('이전 봇의 실거래 포지션·주문을 먼저 복구해야 합니다. 신규 관리와 중복 운용할 수 없습니다.')

    def refresh(self):
        with self.lock:
            self.legacy_guard()
            now = time.time()
            if now-self.last_catalog > 600:
                rows = self.public('/v1/market/all',{'is_details':'true'})
                self.catalog = {r['market']:r for r in rows}
                self.last_catalog = now
            # Reconciliation precedes account refresh. No order POST occurs here.
            for o in list(self.active('live')):
                try:
                    self.reconcile(o)
                except Exception:
                    o['status'] = 'unknown'
                    o['message'] = '거래소 조회 확인 중 · 재전송 금지'
                    self.save()
            if not self.accounts or now-self.account_rest_time>10:
                self.fresh_account()
            # Ticker/all supplies KRW marks. Cross markets are only valuation paths.
            self.tickers = {r['market']:r for r in self.public('/v1/ticker/all',{'quote_currencies':'KRW'})}
            self.market_time=time.time()
            needed = []
            for a in self.accounts:
                c = a['currency']
                if c=='KRW' or 'KRW-'+c in self.tickers:
                    continue
                for q in ('BTC','USDT'):
                    m = q+'-'+c
                    if m in self.catalog and 'KRW-'+q in self.tickers:
                        needed.append(m); break
            if needed:
                self.tickers.update({r['market']:r for r in self.public('/v1/ticker',{'markets':','.join(needed)})})
            markets = self.watch()
            if markets:
                books = self.public('/v1/orderbook',{'markets':','.join(markets),'level':0})
                for b in books:
                    if 0 <= time.time()-b['timestamp']/1000 < 10:
                        self.quotes[b['market']] = {'time': b['timestamp']/1000, 'units':b['orderbook_units']}
            if not self.state['seeded']:
                self.seed()
            for o in list(self.active('paper')):
                self.paper_match(o)
            self.error = ''
            self.save()

    def watch(self):
        owned = ['KRW-'+r['currency'] for r in self.accounts if r['currency']!='KRW'
                 and float(r['balance'])+float(r['locked'])>0]
        paper = ['KRW-'+c for c,h in self.state['paper']['holdings'].items() if h['qty']>0]
        return sorted(set(['KRW-BTC','KRW-ETH']+owned+paper+
                          list(self.state['settings']['paper'])+list(self.state['settings']['live']))
                      & set(self.catalog))

    def mark(self, currency):
        m = 'KRW-'+currency
        if m in self.tickers:
            return float(self.tickers[m]['trade_price'])
        for q in ('BTC','USDT'):
            if q+'-'+currency in self.tickers and 'KRW-'+q in self.tickers:
                return float(self.tickers[q+'-'+currency]['trade_price'])*float(self.tickers['KRW-'+q]['trade_price'])
        return None

    def seed(self):
        p = self.state['paper']
        p['cash'] = sum(float(a['balance'])+float(a['locked']) for a in self.accounts if a['currency']=='KRW')
        p['holdings'] = {a['currency']:{'qty':float(a['balance'])+float(a['locked']),
                         'avg':self.avg(a)} for a in self.accounts if a['currency']!='KRW'
                         and float(a['balance'])+float(a['locked'])>0}
        self.state['seeded'] = True
        p['initial'] = self.view('paper')['total']

    def avg(self, a):
        avg = float(a.get('avg_buy_price',0))
        unit = a.get('unit_currency','KRW')
        if unit!='KRW':
            rate = self.mark(unit)
            return avg*rate if rate else 0
        return avg

    def fresh_account(self):
        self.legacy_guard()
        started=time.time()
        rows=self.api().get('/v1/accounts')
        self.apply_accounts(rows,started)

    def apply_accounts(self,rows,started):
        if not isinstance(rows,list):raise ValueError('계좌 응답 형식 확인 필요')
        for a in rows:
            number(a.get('balance'));number(a.get('locked'))
        if started<self.account_request_started:return
        merged=[]
        for a in rows:
            a=a.copy();c=a['currency']
            old=next((r for r in self.accounts if r['currency']==c),None)
            if old and self.account_events.get(c,0)>started*1000:
                a.update(balance=old['balance'],locked=old['locked'],avg_pending=True)
                self.account_wake.set()
            merged.append(a)
        # A new asset event may arrive after this REST request began.
        for a in self.accounts:
            if self.account_events.get(a['currency'],0)>started*1000 and not any(r['currency']==a['currency'] for r in merged):
                merged.append(a.copy())
        self.accounts=merged;self.account_request_started=started
        self.account_time=time.time();self.account_rest_time=self.account_time;self.account_error=''

    def sync_account(self):
        # Run the HTTP request outside the position lock so stream prices keep updating.
        started=time.time();rows=self.api().get('/v1/accounts')
        with self.lock:self.apply_accounts(rows,started)

    def available(self, mode, currency):
        if mode=='live':
            a = next((a for a in self.accounts if a['currency']==currency),{})
            return max(0,D(a.get('balance',0)))
        p = self.state['paper']
        value = D(p['cash'] if currency=='KRW' else p['holdings'].get(currency,{}).get('qty',0))
        for o in self.active('paper'):
            rem = max(D(0),D(o['volume'])-D(o['filled']))
            if currency=='KRW' and o['side']=='bid':
                value -= rem*D(o['price'])*(1+D(o['rate']))
            elif o['side']=='ask' and currency==o['market'][4:]:
                value -= rem
        return max(D(0),value)

    def book(self, market):
        b = self.public('/v1/orderbook',{'markets':market,'level':0})[0]
        if not 0 <= time.time()-b['timestamp']/1000 <= 8 or not b['orderbook_units']:
            raise ValueError('호가 갱신 지연: 주문을 보류합니다.')
        self.quotes[market] = {'time':b['timestamp']/1000,'units':b['orderbook_units']}
        return b['orderbook_units']

    def quote(self, market, price=None, direction=0):
        with self.lock:
            self.valid_market(market)
            units = self.book(market)
            current = self.public('/v1/ticker',{'markets':market})[0]['trade_price']
            # Verify exchange policy at the current price before using the tier table.
            policy = self.public('/v1/orderbook/instruments',{'markets':market})[0]
            if D(policy['tick_size']) != tick(current,market):
                raise ValueError('거래소 호가 정책 변경 감지: 가격 단위 업데이트가 필요합니다.')
            p = number(price,True) if price is not None else D(current)
            value = step_price(p,direction,market) if direction else str(p//tick(p,market)*tick(p,market))
            return {'price':value,'current':current,'tick':str(tick(value,market)),
                    'bid':units[0]['bid_price'],'ask':units[0]['ask_price']}

    def valid_market(self, m):
        if m not in self.catalog or not m.startswith('KRW-'):
            raise ValueError('지원하는 원화 거래 종목을 선택하세요.')

    def terms(self, mode, market, side, price, volume):
        self.valid_market(market)
        if mode not in ('paper','live') or side not in ('bid','ask'):
            raise ValueError('주문 모드·방향 오류')
        if self.active(mode,market):
            raise ValueError('이 종목에 확인 중·미체결 주문이 있습니다. 체결 또는 취소 후 주문하세요.')
        if mode=='live':self.exchange_guard()
        if mode=='live' and any(o['status'] in ('unknown','submitting') for o in self.active('live')):
            raise ValueError('실거래 미확인 주문을 먼저 조회·복구하세요. 중복 주문을 차단합니다.')
        p, v = number(price,True), number(volume,True)
        if v != v.quantize(D('.00000001'),rounding=ROUND_DOWN):
            raise ValueError('수량은 소수점 8자리까지 입력하세요.')
        if p % tick(p,market):
            raise ValueError('가격이 호가 단위와 맞지 않습니다.')
        self.quote(market,str(p))
        if mode=='live':
            if legacy.paper_day_active():
                raise ValueError('실거래 차단일입니다.')
            self.fresh_account()
            chance = self.api().get('/v1/orders/chance',{'market':market})
            if chance.get('market',{}).get('state') != 'active':
                raise ValueError('현재 거래 가능한 마켓이 아닙니다.')
            rate = number(chance.get(side+'_fee'),True)
            minimum = number(chance['market'][side]['min_total'],True)
            maximum = number(chance['market'].get('max_total',1000000000),True)
        else:
            if not self.state['seeded']:
                raise ValueError('계좌 잔고를 먼저 조회해야 모의를 시작할 수 있습니다.')
            rate, minimum, maximum = D('.0005'),D(5000),D(1000000000)
        total = p*v
        if total<minimum or total>maximum:
            raise ValueError('주문 금액은 '+str(minimum)+'원 이상, '+str(maximum)+'원 이하입니다.')
        needed = total*(1+rate) if side=='bid' else v
        currency = 'KRW' if side=='bid' else market[4:]
        if needed > self.available(mode,currency):
            raise ValueError('주문 가능 잔고가 부족합니다. 매수 수수료와 미체결 주문을 확인하세요.')
        # An external pending order for this coin must not race delegation or manual orders.
        if mode=='live' and self.api().get('/v1/orders/open',{'market':market,'limit':100}):
            raise ValueError('업비트에 이 종목의 미체결 주문이 있습니다. 먼저 확인·취소하세요.')
        return {'market':market,'side':side,'price':str(p),'volume':str(v),'rate':float(rate),
                'total':float(total),'estimated_fee':float(total*rate),'mode':mode}

    def preview(self, body):
        with self.lock:
            mode=body.get('mode')
            t = self.terms(mode,body.get('market'),body.get('side'),body.get('price'),body.get('volume'))
            bucket=body.get('bucket')
            if bucket is not None:
                if bucket not in PROFILES:raise ValueError('투자 분류 오류')
                cfg=self.state['settings'][mode].get(t['market'])
                if cfg and cfg['bucket']!=bucket:
                    raise ValueError('기존 투자 분류와 다릅니다. 포트폴리오에서 분류를 먼저 변경하세요.')
                t['bucket']=bucket
            if body.get('observation') is not None:
                p=body['observation']
                if not isinstance(p,dict):raise ValueError('관찰기간 형식 오류')
                if t['side']!='bid':raise ValueError('관찰기간은 매수 주문에 설정하세요.')
                t['observation']=observation_period(p.get('value'),p.get('unit'))
            key = uuid.uuid4().hex
            t.update(ticket=key,expires=time.time()+30)
            self.tickets = {k:v for k,v in self.tickets.items() if v['expires']>time.time()}
            self.tickets[key] = t
            return t.copy()

    def submit(self, ticket):
        with self.lock:
            for orders in self.state['orders'].values():
                if any(o.get('ticket')==ticket for o in orders):
                    return '이미 접수한 요청입니다. 주문 내역을 확인하세요.'
            t = self.tickets.get(ticket)
            if not t or t['expires']<time.time():
                raise ValueError('주문 검토가 만료·변경되었습니다. 다시 확인하세요.')
            extra={'ticket':ticket}
            for field in ('observation','selection_group'):
                if field in t:extra[field]=t[field]
            if t.get('bucket'):
                cfg=self.state['settings'][t['mode']].get(t['market'])
                if cfg and cfg['bucket']!=t['bucket']:raise ValueError('투자 분류가 변경됐습니다. 주문을 다시 검토하세요.')
                extra['bucket']=t['bucket']
            t = self.terms(t['mode'],t['market'],t['side'],t['price'],t['volume']) | extra
            return self.place(t)

    def place(self, t, automatic=False):
        if self.engine.stop_event.is_set():raise ValueError('프로그램 종료 중: 신규 주문 중단')
        mode = t['mode']
        h = self.view(mode)['holdings']
        avg = next((r['avg'] for r in h if r['currency']==t['market'][4:]),0)
        o = t | {'identifier':'pf-'+uuid.uuid4().hex,'status':'submitting','created':time.time(),
                 'filled':0,'funds':0,'fee':0,'basis':avg,'automatic':automatic,'message':''}
        self.state['orders'][mode].append(o)
        if t.get('bucket') and t['market'] not in self.state['settings'][mode]:
            self.state['settings'][mode][t['market']]={'bucket':t['bucket'],'auto':False}
        if t.get('observation'):
            self.state.setdefault('observations',[]).append({'identifier':o['identifier'],'mode':mode,'market':o['market'],
                'period':t['observation'],'created':o['created'],'started':None,'ends':None})
        self.save()  # Durable identifier BEFORE any mutation at the exchange.
        if mode=='paper':
            o['status']='wait'
            self.paper_match(o)
            self.save()
            return '모의 지정가 주문 접수 · 체결/미체결 내역을 확인하세요.'
        params = {k:o[k] for k in ('market','side','price','volume','identifier')}
        params.update(ord_type='limit',smp_type='cancel_taker')
        if automatic:
            params['time_in_force']='ioc'
        try:
            result = self.private('POST','/v1/orders',params)
        except ApiError as e:
            if e.status in (400,401,403,404,422):
                o['status']='rejected'; o['message']=str(e); self.save()
                raise
            o['status']='unknown'; self.save()
            return '주문 응답 확인 중 · 재전송하지 않습니다. 주문 내역에서 확인하세요.'
        except Exception:
            o['status']='unknown'; self.save()
            return '주문 응답 확인 중 · 재전송하지 않습니다. 주문 내역에서 확인하세요.'
        try:
            self.apply_live(o,result)
        except Exception:
            o['status']='unknown'; self.save()
        return '실거래 지정가 주문 접수 · 체결/미체결 내역을 확인하세요.'

    def journal(self, o, qty, funds, fee, mode):
        if qty<=0:
            return
        self.state['journal'][mode].append({'time':time.time(),'market':o['market'],'side':o['side'],
            'volume':qty,'funds':funds,'fee':fee,'identifier':o['identifier'],
            'pnl':funds-fee-o['basis']*qty if o['side']=='ask' and o['basis'] else None})

    def apply_live(self, o, result):
        if result.get('identifier') != o['identifier'] or result.get('market')!=o['market'] or result.get('side')!=o['side']:
            raise ValueError('주문 식별 불일치')
        status = result.get('state')
        if status not in ('wait','watch','done','cancel'):
            raise ValueError('주문 상태 확인 필요')
        trades = result.get('trades',[])
        qty = sum(float(number(t['volume'])) for t in trades)
        funds = sum(float(number(t['funds'])) for t in trades)
        fee = float(number(result.get('paid_fee',0)))
        executed = float(number(result.get('executed_volume',0)))
        if (abs(qty-executed)>1e-8 or qty+1e-8<o['filled'] or qty>float(o['volume'])+1e-8
                or funds+1e-8<o['funds'] or fee+1e-8<o['fee']):
            raise ValueError('체결 수량 확인 필요')
        self.journal(o,qty-o['filled'],funds-o['funds'],fee-o['fee'],'live')
        o.update(filled=qty,funds=funds,fee=fee,status='wait' if status=='watch' else status,message='')
        self.update_observation(o)
        self.save()  # Fill high-water marks and journal commit together.

    def reconcile(self, o):
        result = self.api().get('/v1/order',{'identifier':o['identifier']})
        self.apply_live(o,result)

    def paper_match(self, o):
        if self.engine.stop_event.is_set():return
        q = self.quotes.get(o['market'])
        if not q or not 0<=time.time()-q['time']<=8:
            return
        if o.get('last_match_time')==q['time']:
            return
        o['last_match_time']=q['time']
        p = self.state['paper']
        qty, funds = D(0),D(0)
        remaining = D(o['volume'])-D(o['filled'])
        for u in q['units']:
            side = 'ask' if o['side']=='bid' else 'bid'
            price = D(u[side+'_price'])
            if (o['side']=='bid' and price>D(o['price'])) or (o['side']=='ask' and price<D(o['price'])):
                break
            take = min(remaining-qty,D(u[side+'_size']))
            qty+=take; funds+=take*price
            if qty>=remaining:
                break
        if qty<=0:
            if o.get('automatic'):
                o['status']='cancel'; self.save()
            return
        fee = funds*D(o['rate'])
        c = o['market'][4:]
        h = p['holdings'].setdefault(c,{'qty':0,'avg':0})
        if o['side']=='bid':
            newq = D(h['qty'])+qty
            h['avg']=float((D(h['qty'])*D(h['avg'])+funds+fee)/newq)
            h['qty']=float(newq); p['cash']=float(D(p['cash'])-funds-fee)
        else:
            h['qty']=max(0,float(D(h['qty'])-qty)); p['cash']=float(D(p['cash'])+funds-fee)
        self.journal(o,float(qty),float(funds),float(fee),'paper')
        o.update(filled=float(D(o['filled'])+qty),funds=float(D(o['funds'])+funds),fee=float(D(o['fee'])+fee))
        if float(o['volume'])-o['filled']<1e-10:
            o['status']='done'
        elif o.get('automatic'):
            o['status']='cancel'
        self.update_observation(o)
        self.save()

    def update_observation(self,o):
        if o['filled']<=0:return
        obs=next((r for r in self.state.get('observations',[]) if r['identifier']==o['identifier']),None)
        if obs and obs['started'] is None:
            obs['started']=time.time();obs['ends']=observation_until(obs['started'],obs['period'])

    def settle_observations(self):
        changed=False;now=time.time()
        for obs in self.state.get('observations',[]):
            if obs.get('ends') and now>=obs['ends'] and 'end_price' not in obs:
                price=self.mark(obs['market'][4:])
                order=next((o for o in self.state['orders'][obs['mode']] if o['identifier']==obs['identifier']),None)
                if price is not None and order and now-self.market_time<=15:
                    obs.update(end_price=price,end_observed_at=now,end_volume=order['filled'],
                        end_funds=order['funds'],end_fee=order['fee']);changed=True
        if changed:self.save()

    def observation_view(self):
        rows=[];now=time.time()
        for obs in self.state.get('observations',[]):
            o=next((o for o in self.state['orders'][obs['mode']] if o['identifier']==obs['identifier']),None)
            if not o:continue
            price=obs.get('end_price',self.mark(obs['market'][4:]))
            volume=obs.get('end_volume',o['filled']);funds=obs.get('end_funds',o['funds'])
            entry=funds/volume if volume else None
            status=('completed' if 'end_price' in obs else 'expired_wait' if obs.get('ends') and now>=obs['ends']
                    else 'observing' if obs['started'] else 'cancelled' if o['status'] in ('cancel','rejected') else 'waiting')
            rows.append(obs|{'status':status,'entry':entry,'price':price,'volume':volume,'funds':funds,
                             'fee':obs.get('end_fee',o['fee']),'order_status':o['status'],
                             'price_return_pct':(price/entry-1)*100 if entry and price is not None else None})
        return rows

    def preview_selection(self,body):
        items=body.get('items')
        if not isinstance(items,list) or not 1<=len(items)<=10:raise ValueError('1~10종목을 선택하세요.')
        group=uuid.uuid4().hex;tickets=[];seen=set();totals={'paper':D(0),'live':D(0)}
        try:
            for item in items:
                if not isinstance(item,dict):raise ValueError('선택 종목 형식 오류')
                mode=item.get('mode');market=item.get('market');key=(mode,market)
                if key in seen:raise ValueError('같은 계좌의 동일 종목은 한 번만 선택하세요.')
                seen.add(key)
                price=number(item.get('price'),True);amount=number(item.get('amount'),True)
                volume=(amount/price).quantize(D('.00000001'),rounding=ROUND_DOWN)
                t=self.preview({'mode':mode,'market':market,'side':'bid','price':str(price),'volume':str(volume),
                    'bucket':item.get('bucket'),'observation':{'value':item.get('period_value'),'unit':item.get('period_unit')}})
                with self.lock:self.tickets[t['ticket']]['selection_group']=group
                tickets.append(t);totals[mode]+=D(t['total'])+D(t['estimated_fee'])
            with self.lock:
                for mode,total in totals.items():
                    if total>self.available(mode,'KRW'):raise ValueError(('실거래' if mode=='live' else '모의')+' 선택 합계가 주문 가능 원화를 초과합니다.')
                expires=time.time()+30
                for t in tickets:self.tickets[t['ticket']]['expires']=expires;t['expires']=expires
                self.selection_reviews={k:v for k,v in self.selection_reviews.items() if v['expires']>time.time()}
                self.selection_reviews[group]={'tickets':[t['ticket'] for t in tickets],'expires':expires}
            return {'group':group,'items':tickets,'expires':expires}
        except Exception:
            with self.lock:
                for t in tickets:self.tickets.pop(t['ticket'],None)
            raise

    def submit_selection(self,group):
        with self.lock:
            receipts=self.state.setdefault('selection_submissions',{})
            if group in receipts:return receipts[group]
            review=self.selection_reviews.get(group)
            if not review or review['expires']<time.time():raise ValueError('선택 주문 검토가 만료됐습니다. 다시 검토하세요.')
            receipt={'group':group,'created':time.time(),'results':[],'requested':len(review['tickets'])}
            receipts[group]=receipt;self.save()  # Never resume a partially submitted group on replay.
        for ticket in review['tickets']:
            try:
                message=self.submit(ticket)
                with self.lock:
                    order=next(o for rows in self.state['orders'].values() for o in rows if o.get('ticket')==ticket)
                    receipt['results'].append({'market':order['market'],'mode':order['mode'],'identifier':order['identifier'],
                        'status':order['status'],'message':message})
                    self.save()
                if order['status']=='unknown':break
            except Exception as e:
                with self.lock:receipt['results'].append({'error':str(e)});self.save()
                break
        return copy.deepcopy(receipt)

    def cancel(self, mode, identifier):
        with self.lock:
            if mode=='live':self.exchange_guard()
            o = next((o for o in self.active(mode) if o['identifier']==identifier),None)
            if not o:
                raise ValueError('진행 중인 주문이 아닙니다.')
            if mode=='paper':
                o['status']='cancel'; self.save()
            else:
                try:
                    result = self.private('DELETE','/v1/order',{'identifier':identifier})
                    self.apply_live(o,result)
                except Exception:
                    o['status']='unknown'; self.save()
                    raise ValueError('취소 결과 확인 중입니다. 체결 여부를 다시 조회합니다. 재주문하지 마세요.') from None
            return '취소 요청 처리 · 최종 상태는 주문 내역에서 확인하세요.'

    def recover(self, mode, identifier):
        with self.lock:
            if mode=='live':self.exchange_guard()
            o = next((o for o in self.active(mode) if o['identifier']==identifier),None)
            if mode!='live' or not o or time.time()-o['created']<60:
                raise ValueError('실거래 미확인 주문은 접수 후 60초 이후 확인할 수 있습니다.')
            for i in range(2):
                try:
                    self.reconcile(o)
                    return '거래소 주문을 확인했습니다.'
                except ApiError as e:
                    if e.status!=404:
                        raise
                if i==0:
                    time.sleep(1)
            o['status']='rejected'; o['message']='두 차례 미접수 확인 · 자동 재전송 없음'; self.save()
            return o['message']

    def configure(self, mode, market, bucket, auto):
        with self.lock:
            if mode not in ('paper','live') or bucket not in PROFILES or type(auto) is not bool:
                raise ValueError('투자 분류·관리 권한·모드를 확인하세요.')
            owned=any(h['market']==market and h['qty']>0 for h in self.view(mode)['holdings'])
            if auto or not owned:self.valid_market(market)
            old = self.settings(mode,market)
            # Revoke future authority immediately, even while an existing order
            # awaits reconciliation. Revocation does not cancel that order.
            revoking = old['auto'] and not auto and bucket==old['bucket']
            if self.active(mode,market) and not revoking:
                raise ValueError('미체결·확인 중 주문을 먼저 처리하세요.')
            cfg = {'bucket':bucket,'auto':auto}
            if auto:
                self.legacy_guard()
                if mode=='live':
                    self.exchange_guard()
                    self.fresh_account()
                    if self.api().get('/v1/orders/open',{'market':market,'limit':100}):
                        raise ValueError('이 종목의 기존 미체결 주문을 먼저 처리하세요.')
                q = self.book(market)
                context = self.get_context(market,bucket,force=True)
                if context['atr']<=0 or not math.isfinite(context['atr']):
                    raise ValueError('손절 계산에 필요한 ATR을 확인할 수 없습니다.')
                price = float(q[0]['bid_price'])
                holding=next((h for h in self.view(mode)['holdings'] if h['market']==market),None)
                cfg.update(peak=max(price,old.get('peak',price)) if old.get('auto') else price,
                           stop=max(old.get('stop',0) if old.get('auto') else 0,price-self.profiles()[bucket]['stop_atr']*context['atr']),
                           since=time.time(),last_signal=old.get('last_signal',''),
                           entry_reference=price,initial_atr=context['atr'],max_hours=self.profiles()[bucket]['max_hours'],progress_seconds=self.profiles()[bucket]['progress_seconds'],
                           had_position=bool(holding and holding['qty']>0))
            self.state['settings'][mode][market]=cfg
            self.save()
            return '분류·권한 적용: '+self.profiles()[bucket]['name']+' / '+('자동 관리 위임' if auto else '직접 관리')

    def get_context(self, market, bucket, force=False):
        now = time.time(); key=(market,bucket)
        if not force and key in self.context and now-self.context[key]['time']<60:
            return self.context[key]
        profile = self.profiles()[bucket]
        if bucket=='short':
            quarter=load_closed(self.public,self.candle_series,market,15,200,now)
            five=load_closed(self.public,self.candle_series,market,5,300,now)
            one=load_closed(self.public,self.candle_series,market,1,200,now)
            signal=evaluate_intraday(quarter,five,one,now)
            if 'atr' not in signal:
                raise ValueError(signal['reason'])
            result=signal | {'entry':signal['ok'],'time':now}
        else:
            rows=load_closed(self.public,self.candle_series,market,profile['unit'],200,now)
            result=profile_signal(rows,profile,now) | {'time':now}
        self.context[key]=result
        return result

    def manage(self):
        if self.engine.stop_event.is_set():return
        # Indicator I/O runs outside the account lock. A slow candle endpoint must
        # not freeze streamed balances/prices or the monitoring window.
        with self.lock:
            jobs={(m,cfg['bucket']) for mode in ('paper','live') for m,cfg in self.state['settings'][mode].items()
                  if cfg['auto'] and not self.active(mode,m)}
        contexts={};failures={}
        for m,bucket in jobs:
            try:contexts[(m,bucket)]=self.get_context(m,bucket)
            except Exception as e:failures[(m,bucket)]=str(e)
        with self.lock:
            self.legacy_guard()
            now=time.time()
            if self.engine.stop_event.is_set():return
            for mode in ('paper','live'):
                view=self.view(mode)
                for market,cfg in list(self.state['settings'][mode].items()):
                    if not cfg['auto'] or self.active(mode,market):
                        continue
                    h=next((h for h in view['holdings'] if h['market']==market),None)
                    q=self.quotes.get(market)
                    if not q or not 0<=now-q['time']<=8:
                        continue
                    bid=float(q['units'][0]['bid_price'])
                    rule=self.profiles()[cfg['bucket']]
                    try:
                        key=(market,cfg['bucket'])
                        if key not in contexts:raise ValueError(failures.get(key,'봉 확인 대기'))
                        ctx=contexts[key]
                        cfg['message']='봉 확인 완료'
                        if h and h['qty']>0:
                            if not cfg.get('had_position',True):
                                cfg.update(since=now,peak=bid,stop=max(0,bid-ctx['atr']*rule['stop_atr']),had_position=True,
                                           entry_reference=bid,initial_atr=ctx['atr'],max_hours=rule['max_hours'],progress_seconds=rule['progress_seconds'])
                            cfg['peak']=max(cfg.get('peak',bid),bid)
                            cfg['stop']=max(cfg.get('stop',0),cfg['peak']-ctx['atr']*rule['trail_atr'])
                    except Exception as e:
                        ctx=None; cfg['message']=str(e)
                    # Persisted stop still works during candle fetch failures.
                    if h and h['qty']>0:
                        if not cfg.get('had_position',True) and ctx is None:
                            continue
                        max_hours=cfg.get('max_hours',2 if cfg['bucket']=='short' else rule['max_hours'])
                        expired=max_hours and now-cfg.get('since',now)>=max_hours*3600
                        reference=cfg.get('entry_reference',0)
                        stalled=(cfg['bucket']=='short' and ctx and reference>0
                                 and cfg.get('progress_seconds',PROFILES['short']['progress_seconds'])>0
                                 and now-cfg.get('since',now)>=cfg.get('progress_seconds',PROFILES['short']['progress_seconds'])
                                 and cfg.get('peak',bid)-reference<.5*rule['stop_atr']*cfg.get('initial_atr',ctx['atr'])
                                 and ctx.get('close',bid)<ctx.get('ema20',0))
                        if bid<=cfg.get('stop',0) or expired or stalled or cfg.get('exiting'):
                            volume=self.available(mode,market[4:]).quantize(D('.00000001'),rounding=ROUND_DOWN)
                            # Marketable IOC limit: cap price impact at 0.5%; retry only
                            # after confirmed terminal state, with a new identifier.
                            limit=D(bid)*D('.995')
                            limit=(limit/tick(limit,market)).to_integral_value(rounding=ROUND_CEILING)*tick(limit,market)
                            try:
                                terms=self.terms(mode,market,'ask',str(limit),str(volume))
                                terms['reason']='기간 만료' if expired else '단타 진행 실패' if stalled else '위임 손절·추적 청산'
                                self.place(terms,True)
                                cfg['exiting']=True
                            except Exception as e:
                                cfg['message']=str(e)
                    elif cfg.get('exiting'):
                        if mode=='paper' and self.state.get('trial',{}).get('cash_start'):
                            cfg.update(exiting=False,had_position=False,cooldown_until=now+4*3600)
                            cfg['message']='모의 검증 계속 · 청산 후 4시간 대기'
                        else:
                            cfg['auto']=False
                            cfg['message']='위임 청산 종료 · 재위임 필요'
                    elif self.running and mode=='paper' and ctx:
                        if now<cfg.get('cooldown_until',0):continue
                        cfg['had_position']=False
                        # New profiles have no out-of-sample approval. Paper only.
                        if mode=='live':
                            cfg['message']='신규 기간별 전략 미검증 · 실거래 자동 매수 차단'
                            continue
                        policy=self.engine.trade_policy(mode,record=True)
                        if not policy.get('allow_entry') or not ctx['entry'] or cfg.get('last_signal')==ctx['id']:
                            continue
                        if cfg['bucket']=='short' and not 0<=now-ctx.get('closed_at',0)<=30:
                            cfg['message']='1분 진입 신호 만료 · 다음 신호 대기'
                            continue
                        ask=float(q['units'][0]['ask_price'])
                        if ask/bid-1>.001 or abs(ask/ctx['close']-1)>.003:
                            continue
                        # Recompute after every fill/reservation within this cycle.
                        # Otherwise several recommendations could each spend the
                        # same exposure allowance from the beginning of the loop.
                        view=self.view(mode)
                        total=view['total']; cash=float(self.available(mode,'KRW'))
                        exposure=sum(h['value'] or 0 for h in view['holdings'])
                        amount=max(0,min(cash/1.0005,total*rule['max_position']/1.0002,(total*rule['max_total']-exposure)/1.0004,
                                         total*rule['risk']/(rule['stop_atr']*ctx['atr']/ask+.003)))
                        price=D(ask); price=price//tick(price,market)*tick(price,market)
                        volume=(D(amount)/price).quantize(D('.00000001'),rounding=ROUND_DOWN)
                        if amount<5000:
                            continue
                        terms=self.terms(mode,market,'bid',str(price),str(volume))
                        self.place(terms,True)
                        cfg.update(last_signal=ctx['id'],since=now,peak=ask,stop=max(0,ask-rule['stop_atr']*ctx['atr']),had_position=True,
                                   entry_reference=ask,initial_atr=ctx['atr'],max_hours=rule['max_hours'],progress_seconds=rule['progress_seconds'])
                self.save()

    def view(self, mode):
        if mode=='live':
            cash=sum(float(a['balance']) for a in self.accounts if a['currency']=='KRW')
            locked=sum(float(a['locked']) for a in self.accounts if a['currency']=='KRW')
            raw=[(a['currency'],float(a['balance'])+float(a['locked']),self.avg(a),float(a['locked']))
                 for a in self.accounts if a['currency']!='KRW' and float(a['balance'])+float(a['locked'])>0]
        else:
            p=self.state['paper']; cash=float(self.available(mode,'KRW')); locked=p['cash']-cash
            raw=[(c,h['qty'],h['avg'],h['qty']-float(self.available(mode,c)))
                 for c,h in p['holdings'].items() if h['qty']>0]
        holdings=[]; total=cash+locked; cost=0; value_known_cost=0; unpriced=[]
        for c,qty,avg,reserved in raw:
            pending=mode=='live' and any(a['currency']==c and a.get('avg_pending') for a in self.accounts)
            price=self.mark(c); value=qty*price if price is not None else None
            if value is None:
                unpriced.append(c)
            else:
                total+=value
                if avg>0 and not pending:
                    cost+=qty*avg; value_known_cost+=value
            holdings.append({'currency':c,'market':'KRW-'+c,'qty':qty,'available':max(0,qty-reserved),'avg_pending':bool(pending),
                'locked':reserved,'avg':avg,'price':price,'value':value,'tradable':'KRW-'+c in self.catalog,
                'pnl':value-qty*avg if avg>0 and value is not None and not pending else None,
                'roi':(price/avg-1)*100 if avg>0 and price is not None and not pending else None})
        return {'cash':cash,'locked':locked,'total':total,'holdings':holdings,'unpriced':unpriced,
                'pnl':value_known_cost-cost,'roi':(value_known_cost/cost-1)*100 if cost else None,
                'cost':cost,'time':self.account_time,'stale':time.time()-self.account_time>15}

    def snapshot(self):
        with self.lock:
            return copy.deepcopy({'mode':self.mode,'running':self.running,'status':self.status,'error':self.error,
                'seeded':self.state['seeded'],'views':{m:self.view(m) for m in ('paper','live')},
                'settings':self.state['settings'],'orders':self.state['orders'],'journal':self.state['journal'],
                'watch':self.watch(),'profiles':self.profiles(),'catalog':[{'market':m,'name':r.get('korean_name',m)}
                    for m,r in self.catalog.items() if m.startswith('KRW-')],
                'prices':{m:r['trade_price'] for m,r in self.tickers.items() if m in self.watch()},
                'live_auto_entry_approved':False,'comparison':self.comparison(),
                'observations':self.observation_view(),'exchange':self.exchange_view(),'sync':{'rest_time':self.account_rest_time,'account_error':self.account_error,
                    'market_time':self.market_time}})

    def comparison(self):
        paper=self.view('paper');live=self.view('live');trial=self.state.get('trial',{})
        initial=self.state['paper']['initial'];stats={'closed':0,'wins':0,'net':0,'profit_factor':None}
        if trial.get('cash_start'):
            inventory={};results=[]
            for t in self.state['journal']['paper']:
                lot=inventory.setdefault(t['market'],{'qty':0,'cost':0,'net':0})
                if t['side']=='bid':
                    lot['qty']+=t['volume'];lot['cost']+=t['funds']+t['fee']
                elif lot['qty']>1e-9:
                    qty=min(t['volume'],lot['qty']);cost=lot['cost']*qty/lot['qty']
                    lot['net']+=t['funds']-t['fee']-cost
                    lot['cost']-=cost;lot['qty']-=qty
                    if lot['qty']<=1e-8:
                        results.append(lot['net']);lot.update(qty=0,cost=0,net=0)
            profits=sum(p for p in results if p>0);losses=-sum(p for p in results if p<0)
            stats.update(closed=len(results),wins=sum(p>0 for p in results),net=sum(results),
                         profit_factor=profits/losses if losses else None)
        return {'paper':{'total':paper['total'],'initial':initial,
                         'return_pct':(paper['total']/initial-1)*100 if initial else None,
                         'fees':sum(t['fee'] for t in self.state['journal']['paper']),
                         'fills':len(self.state['journal']['paper']),'stats':stats,
                         'running':self.running,'trial':trial,
                         'samples':self.state.get('paper_samples',[])[-1440:]},
                'live':{'total':live['total'],'pnl':live['pnl'],'holding_roi':live['roi'],
                        'fills':len(self.state['journal']['live']),
                        'fees':sum(t['fee'] for t in self.state['journal']['live']),
                        'unpriced':live['unpriced']}}

    def record_paper(self):
        if not self.state['seeded']:return
        now=time.time();samples=self.state.setdefault('paper_samples',[])
        if not samples or now-samples[-1]['time']>=300:
            value=self.view('paper')['total']
            samples.append({'time':now,'total':value})
            self.state['paper_samples']=samples[-8640:]  # 30 days at 5 minutes
            self.save()

    def start_trial(self,rows,ranking_time):
        """Explicit UI action: only virtual assets reset. Live state untouched."""
        with self.lock:
            if not rows or not 0<=time.time()-ranking_time<=900:
                raise ValueError('새 추천 조사 결과가 필요합니다. 조사 완료 후 시작하세요.')
            if self.active('paper'):
                raise ValueError('기존 모의 미체결 주문을 먼저 취소하세요.')
            for r in rows[:10]:self.valid_market(r['market'])
        # Candle research must not hold the trading lock and delay live protection.
        prepared={};skipped=[]
        for r in rows[:10]:
            m=r['market'];b=r['bucket']
            try:
                self.get_context(m,b,force=True)
                prepared[m]={'bucket':b,'auto':True,'had_position':False,'since':time.time(),
                             'peak':0,'stop':0,'last_signal':'','message':'모의 검증 대기'}
            except Exception:
                skipped.append(m)
        if not prepared:raise ValueError('200개 연속 마감 봉을 확인한 추천 종목이 없습니다.')
        with self.lock:
            if self.active('paper'):raise ValueError('조사 중 모의 주문이 생겼습니다. 미체결 주문을 먼저 처리하세요.')
            self.fresh_account()
            if not self.tickers or time.time()-self.market_time>15:
                raise ValueError('잔고·시세 갱신 후 다시 시도하세요.')
            initial=self.view('live')['total']
            if initial<5000:raise ValueError('가격을 확인한 평가자산이 최소 주문액보다 적습니다.')
            atomic(self.path.parent/('portfolio_trial_archive_'+str(time.time_ns())+'.json'),self.state)
            # No conversion/trade at the exchange: create a cash-only virtual trial.
            self.state['paper']={'cash':initial,'holdings':{},'initial':initial}
            self.state['orders']['paper']=[];self.state['journal']['paper']=[]
            self.state['observations']=[o for o in self.state.get('observations',[]) if o['mode']!='paper']
            self.state['settings']['paper']=prepared;self.state['paper_samples']=[]
            self.state['trial']={'started':time.time(),'cash_start':True,'ranking_time':ranking_time,
                                 'markets':list(prepared),'skipped':skipped,
                                 'unpriced':self.view('live')['unpriced']}
            self.state['seeded']=True;self.running=True;self.tickets.clear()
            self.record_paper();self.save()
            return '모의 자동 검증 시작: '+str(len(prepared))+'종목 · 실제 계좌 변경 없음'+(' · 봉 부족 제외 '+', '.join(skipped) if skipped else '')

    def action(self, body):
        if body.get('action')=='portfolio_exchange_preview':return self.exchange_preview(body)
        if body.get('action')=='portfolio_exchange_submit':return self.exchange_submit(body.get('ticket'))
        if body.get('action')=='portfolio_exchange_cancel':return self.exchange_cancel(body.get('uuid'))
        if body.get('action')=='portfolio_exchange_sync':
            self.exchange_wake.set();return '업비트 미체결 주문 전체 조회를 요청했습니다.'
        if body.get('action')=='portfolio_sync':
            self.sync_account()
            return '업비트 실제 잔고·매수평균가를 다시 확인했습니다.'
        if body.get('action')=='portfolio_selection_preview':return self.preview_selection(body)
        if body.get('action')=='portfolio_selection_submit':return self.submit_selection(body.get('group'))
        with self.lock:
            a=body['action']
            if a=='portfolio_mode':
                mode=body.get('mode')
                if mode not in ('paper','live'):
                    raise ValueError('지원하지 않는 모드입니다.')
                if mode=='live':
                    self.fresh_account()
                self.mode=mode; self.tickets.clear()
                return '실거래 선택 · 직접 주문 가능 / 자동 신규 매수 차단' if mode=='live' else '시뮬레이션 선택 · 모의 주문만 실행'
            if a=='portfolio_config':
                return self.configure(body.get('mode'),body.get('market'),body.get('bucket'),body.get('auto'))
            if a=='portfolio_quote':
                mode=body.get('mode',self.mode)
                if mode not in ('paper','live'):raise ValueError('주문 모드 오류')
                direction=body.get('direction',0)
                if direction not in (-1,0,1):
                    raise ValueError('틱 방향 오류')
                market=body.get('market')
                result=self.quote(market,body.get('price'),direction)
                if mode=='live':
                    self.fresh_account()
                    chance=self.api().get('/v1/orders/chance',{'market':market})
                    bid_fee=float(chance['bid_fee']); ask_fee=float(chance['ask_fee'])
                else:
                    bid_fee=ask_fee=.0005
                return result | {'cash':float(self.available(mode,'KRW')),
                                 'available':float(self.available(mode,market[4:])),
                                 'bid_fee':bid_fee,'ask_fee':ask_fee}
            if a=='portfolio_preview': return self.preview(body)
            if a=='portfolio_submit': return self.submit(body.get('ticket'))
            if a=='portfolio_cancel': return self.cancel(body.get('mode'),body.get('identifier'))
            if a=='portfolio_recover': return self.recover(body.get('mode'),body.get('identifier'))
            if a=='portfolio_start':
                if body.get('mode',self.mode)=='live':
                    raise ValueError('장기·스윙·단타 신규 전략은 수익성 검증 전입니다. 실거래 자동 신규 매수를 차단합니다. 직접 주문·위임한 보유분 청산은 가능합니다.')
                self.running=True
                return '모의 자동 신규 매수 시작 · 자동 관리로 위임한 종목에만 적용'
            if a=='portfolio_pause':
                self.running=False
                return '자동 신규 매수 정지 · 위임한 보유분의 청산 관리는 계속됩니다.'
            if a=='portfolio_reset':
                if self.active('paper'):
                    raise ValueError('모의 미체결 주문을 먼저 취소하세요.')
                self.fresh_account()
                self.running=False; self.state['settings']['paper']={}; self.state['orders']['paper']=[]
                self.state['journal']['paper']=[]; self.seed(); self.save()
                self.state.pop('trial',None);self.state['paper_samples']=[];self.save()
                self.state['observations']=[o for o in self.state.get('observations',[]) if o['mode']!='paper'];self.save()
                return '현재 계좌 전체 자산을 복사해 모의 잔고를 초기화했습니다.'
            raise ValueError('지원하지 않는 포트폴리오 작업입니다.')

    def loop(self):
        while not self.engine.stop_event.is_set():
            try:
                self.refresh()
            except Exception as e:
                with self.lock:
                    self.error=str(e)
            try:
                self.manage()
                with self.lock:self.record_paper();self.settle_observations()
            except Exception as e:
                with self.lock:
                    self.error=str(e)
            self.engine.stop_event.wait(5)
