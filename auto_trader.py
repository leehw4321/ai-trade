"""V3 isolated funded paper account. No private API, no live order transport.

Fast monitor has its own lock/book cache; legacy account/candle I/O cannot block it.
Reservations are persisted local simulation orders, explicitly NOT exchange stops.
"""
import copy
import datetime as dt
import json
import math
import queue
import threading
import time
import uuid
from collections import deque
from decimal import Decimal, ROUND_DOWN, ROUND_CEILING

from trend_engine import atomic, http, ApiError, KST
from portfolio import tick
from scalping_strategy import VERSION, MARKETS, RULES, evaluate_intraday, entry_plan, initial_stop, exit_decision
from scalp_universe import valid_code, screen, choose_candidates, LIMITS
from scalp_details import candidate_details
from investment_profiles import PROFILES
from research_v4 import adjust_plan
from allocation_settings import AllocationSettings
from profile_candles import load_closed
from adaptive_model import indicators
from scalping_strategy import validate_bars
from breakout_strategy import TradeWindow, prepare_breakout, breakout_plan, BREAKOUT


def fresh_state():
    return {'version':VERSION,'mode':'paper','funded':0.,'cash':0.,'markets':list(MARKETS),'universe_mode':'all',
            'positions':{},'orders':[],'fills':[],'closed':[],'cooldown':{},'last_signal':{},
            'day':'','day_start':0.,'day_stop':False,'day_liquidate':False,'peak':0.,'review':False,
            'loss_streak':0,'pause_until':0.,'events':[],'samples':[]}


def rounded(value, market, up=False):
    p = Decimal(str(value))
    for _ in range(3):
        unit = tick(p,market)
        result = (p/unit).to_integral_value(rounding=ROUND_CEILING if up else ROUND_DOWN)*unit
        if tick(result,market)==unit:
            return float(result)
        p=result
    raise ValueError('호가 단위 확인 필요')


def qty(value):
    return float(Decimal(str(value)).quantize(Decimal('.00000001'),rounding=ROUND_DOWN))


class AutoTrader:
    def __init__(self, root, stop_event=None, public=None, record=False):
        self.allocation = AllocationSettings(root)
        self.profiles = lambda: PROFILES
        self.path = root/'auto_v4.json'
        self.stop = stop_event or threading.Event()
        self.public = public or (lambda path,params=None: http('GET',path,params))
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.running = False  # NEVER resume entries across restart
        self.state = fresh_state()
        if self.path.exists():
            try:
                self.state = json.loads(self.path.read_text(encoding='utf-8'))
                previous=self.state.get('version')
                self.state.setdefault('universe_mode','all')
                self.validate()
                if previous=='scalp-v3.0':
                    # Preserve management horizon for already-owned legacy positions.
                    for p in self.state['positions'].values():
                        p.setdefault('max_seconds',7200);p.setdefault('progress_seconds',1800)
                self.state['version']=VERSION
            except Exception:
                raise RuntimeError('V3 상태 파일 손상: 초기화하지 말고 주문·자금 기록을 복구하세요.') from None
        self.quotes = {}; self.signals = {}; self.series = {}; self.contexts = {}
        self.status = '자동거래 v4 모의 대기 · 실거래 신규 매수 검증 미통과'
        self.feed_error = ''; self.scan_error = ''; self.ws_received = 0.
        self.last_tick = 0.; self.last_sample = 0.; self.last_save = 0.
        self.dirty = False; self.started = False; self.threads=[]
        self.metrics = {'monitor_ms':deque(maxlen=4000),'data_age_ms':deque(maxlen=4000),
                        'decision_ms':deque(maxlen=4000),'lock_wait_ms':deque(maxlen=4000)}
        self.post_watch = []
        self.record=record;self.record_queue=queue.Queue(maxsize=10000);self.record_dropped=0
        self.catalog={};self.tickers={};self.screened={};self.candidates=[]
        self.catalog_time=0.;self.universe_time=0.;self.rotation=0;self.last_rotation=-1
        self.last_scan_market={};self.universe_error='전체 원화마켓 수집 중'
        self.trade_windows={};self.breakout_contexts={};self.breakout_signals={}
        self.breakout_recorded={}
        self.fast_candidates=[]
        self.entry_blocks={}

    def ingest_trade(self, data, now=None):
        """Public real-time fills only; no order submission or private API."""
        now = time.time() if now is None else now
        with self.lock:
            if data is None:
                self.trade_windows.clear();self.breakout_signals.clear()
                return
            market = data.get('code')
            if market not in self.state['markets'] or self.stop.is_set():return
            window = self.trade_windows.setdefault(market, TradeWindow())
            window.ingest(data, now)

    def breakout_observations(self, now):
        active = set(self.candidates)|set(self.protected_markets())
        for market in list(self.breakout_signals):
            if market not in active:self.breakout_signals.pop(market,None)
        for market in active:
            window = self.trade_windows.get(market)
            if not window:continue
            signal = window.signal(self.breakout_contexts.get(market), now)
            self.breakout_signals[market] = signal
            key = (int(now), signal.get('id'), signal['reason'])
            if key != self.breakout_recorded.get(market):
                self.breakout_recorded[market] = key
                self.record_event({'type':'breakout_signal','market':market,'signal':signal})

    def record_event(self,event):
        if not self.record or not self.state['funded']:return
        # Caller holds the small V3 lock. Delivery time defines causal replay order.
        try:self.record_queue.put_nowait({'time':time.time(),**event})
        except queue.Full:self.record_dropped+=1

    def protected_markets(self):
        return list(dict.fromkeys([*self.state['positions'],*(o['market'] for o in self.active())]))

    def book_markets(self):
        with self.lock:
            return sorted(set(self.state['markets'])|set(self.protected_markets()))

    def entry_allowed(self,m,now):
        if m not in self.state['markets']:return False
        # Deterministic offline/replay injects signals; production always starts workers.
        if not self.started and not self.catalog:return self.state['universe_mode']=='selected'
        return (0<=now-self.universe_time<=LIMITS['ticker_ttl'] and 0<=now-self.catalog_time<=LIMITS['catalog_ttl']
                and self.screened.get(m,{}).get('eligible',False) and m in self.candidates)

    def refresh_universe(self,now=None):
        supplied=now
        now=time.time() if now is None else now
        with self.lock:catalog=self.catalog.copy();previous=self.catalog_time
        if not catalog or now-previous>=600:
            raw=self.public('/v1/market/all',{'is_details':'true'})
            if not isinstance(raw,list):raise ValueError('원화마켓 목록 응답 오류')
            catalog={r['market']:r for r in raw if valid_code(r.get('market'))}
            if not catalog:raise ValueError('원화마켓 목록 비어 있음')
            previous=now
        ticks=self.public('/v1/ticker/all',{'quote_currencies':'KRW'})
        if not isinstance(ticks,list):raise ValueError('전체 시세 응답 오류')
        tickers={r['market']:r for r in ticks if valid_code(r.get('market'))}
        # Trades can occur while HTTP is in flight; measure freshness at receipt.
        if supplied is None:now=time.time()
        screened=screen(catalog,tickers,now)
        with self.lock:
            self.catalog=catalog;self.catalog_time=previous;self.tickers=tickers
            self.screened=screened;self.universe_time=now;self.universe_error=''
            if self.state['universe_mode']=='all':
                markets=sorted(catalog)
                if self.state['markets']!=markets:
                    self.state['markets']=markets;self.dirty=True
            scope=set(self.state['markets'])
            candidates={m:r for m,r in screened.items() if m in scope}
            minute=int(now//60)
            if self.last_rotation!=minute:
                self.candidates,self.rotation=choose_candidates(candidates,self.rotation,self.protected_markets())
                self.last_rotation=minute
            else:
                # A new warning must immediately prevent entries even between rotations.
                protected=self.protected_markets()
                self.candidates=list(dict.fromkeys(protected+[m for m in self.candidates if candidates.get(m,{}).get('eligible')]))
            # Promote new activity beyond the old 24h-turnover top list. This is
            # screening only; entry still requires the completed candle baseline.
            activity=[]
            for m, window in self.trade_windows.items():
                if (not candidates.get(m,{}).get('eligible') or window.first is None
                        or now-window.first<60 or not 0<=now-window.last<=2):continue
                recent=sum(b['value'] for b in window.seconds if b['second']>=math.ceil(now-60))
                average=candidates[m]['turnover']/1440
                if recent>=BREAKOUT['min_turnover'] and average>0 and recent>=2*average:
                    activity.append((recent/average,m))
            self.fast_candidates=[m for _,m in sorted(activity,reverse=True)[:4]]
            self.candidates=list(dict.fromkeys(self.protected_markets()+self.fast_candidates+self.candidates))
            # Bound both candle I/O and fast-monitor work even across rotations.
            self.candidates=self.candidates[:len(self.protected_markets())+16]
            scope=set(self.state['markets'])
            for cache in (self.trade_windows,self.breakout_contexts,self.breakout_signals):
                for m in list(cache):
                    if m not in scope and m not in self.protected_markets():cache.pop(m,None)
            # Drop old signals from entry consideration when their candidate slot rotates out.
            for m in list(self.signals):
                if m not in self.candidates:self.signals.pop(m,None)
            self.record_event({'type':'universe','markets':self.state['markets'],
                               'screened':self.screened,'candidates':self.candidates,
                               'catalog_time':self.catalog_time})
        self.wake.set()

    def universe_loop(self):
        delay=15
        while not self.stop.is_set():
            try:self.refresh_universe();delay=15
            except ApiError as e:
                delay=60 if e.status==418 else min(60,max(15,delay*2))
                with self.lock:self.universe_error='전체 시세 요청 제한 · 이전 목록 유지, 신규 진입 대기'
            except Exception:
                delay=min(60,max(15,delay*2))
                with self.lock:self.universe_error='전체 시세 조회 지연 · 신규 진입 대기'
            self.stop.wait(delay)

    def validate(self):
        s=self.state
        if not isinstance(s,dict) or set(fresh_state())-set(s) or s['version'] not in (VERSION,'scalp-v3.0','scalp-v3.1') or s['mode']!='paper':
            raise ValueError('상태 형식 오류')
        if (s['universe_mode'] not in ('all','selected') or not isinstance(s['markets'],list)
                or any(not valid_code(m) for m in s['markets']) or len(set(s['markets']))!=len(s['markets'])):
            raise ValueError('종목 오류')
        def finite(v):
            if type(v) not in (int,float) or not math.isfinite(v) or v<0:raise ValueError('자금 오류')
        for k in ('cash','funded','day_start','peak','pause_until'):finite(s[k])
        for k in ('review','day_stop','day_liquidate'):
            if type(s[k]) is not bool:raise ValueError('위험 상태 오류')
        for m,p in s['positions'].items():
            if not valid_code(m):raise ValueError('보유 종목 오류')
            if p.get('bucket','short') not in PROFILES:raise ValueError('보유 투자 분류 오류')
            for k in ('entry','volume','cost','stop','r','atr','since','peak','trough'):finite(p[k])
            if min(p['entry'],p['volume'],p['r'],p['atr'])<=0:raise ValueError('보유량 오류')
            if p.get('strategy','pullback') not in ('pullback','breakout'):raise ValueError('전략 오류')
            if p.get('strategy')=='breakout':finite(p['breakout_level'])
        seen=set();orders={}
        for o in s['orders']:
            if (o['id'] in seen or not valid_code(o['market']) or o['side'] not in ('bid','ask')
                    or o['status'] not in ('wait','done','cancel') or o['kind'] not in ('entry','partial','exit')):
                raise ValueError('주문 오류')
            seen.add(o['id'])
            orders[o['id']]=o
            for k in ('price','volume','filled','funds','fee','created'):finite(o[k])
            if not 0<=o['filled']<=o['volume']+1e-10:raise ValueError('체결량 오류')
            if min(o['price'],o['volume'])<=0 or type(o['ioc']) is not bool:raise ValueError('예약 형식 오류')
            if o['kind']=='entry':
                for k in ('atr','low','cost_rate','expires','breakout','ceiling'):finite(o[k])
                if o.get('strategy')=='breakout':
                    finite(o['stop_price'])
                    if not o['ioc'] or not 0<o['stop_price']<o['price']:raise ValueError('돌파 주문 보호선 오류')
                if not isinstance(o['signal'],str):raise ValueError('신호 오류')
        totals={key:[0.,0.,0.] for key in orders};positions={}
        for f in s['fills']:
            if (f['order'] not in orders or f['side']!=orders[f['order']]['side']
                    or f['market']!=orders[f['order']]['market']):raise ValueError('체결 식별 오류')
            for k in ('time','volume','funds','fee','price'):finite(f[k])
            for i,k in enumerate(('volume','funds','fee')):totals[f['order']][i]+=f[k]
            m=f['market'];v,c=positions.get(m,(0.,0.))
            if f['side']=='bid':positions[m]=(v+f['volume'],c+f['funds']+f['fee'])
            else:
                if f['volume']>v+1e-8:raise ValueError('소유량 초과 체결')
                remaining=max(0,v-f['volume'])
                positions[m]=(remaining,c*(remaining/v) if v else 0)
        for key,o in orders.items():
            if any(abs(o[k]-totals[key][i])>max(1e-7,totals[key][i]*1e-9)
                   for i,k in enumerate(('filled','funds','fee'))):raise ValueError('주문·체결 장부 불일치')
        for m in set(positions)|set(s['positions']):
            v,c=positions.get(m,(0.,0.));p=s['positions'].get(m,{'volume':0,'cost':0})
            if abs(v-p['volume'])>1e-7 or abs(c-p['cost'])>max(.01,c*1e-9):raise ValueError('보유 장부 불일치')
        # Cash is a fill-derived ledger, never a fresh copy of current account balance.
        expected = s['funded']+sum((-f['funds']-f['fee']) if f['side']=='bid' else (f['funds']-f['fee']) for f in s['fills'])
        if abs(expected-s['cash'])>max(.01,abs(expected)*1e-9):raise ValueError('자금 장부 불일치')

    def save(self):
        atomic(self.path,self.state)
        self.dirty=False;self.last_save=time.monotonic()

    def event(self, message, now):
        self.state['events'].append({'time':now,'message':message})
        self.state['events']=self.state['events'][-200:]
        self.dirty=True

    def active(self, market=None, side=None):
        return [o for o in self.state['orders'] if o['status']=='wait'
                and (market is None or o['market']==market) and (side is None or o['side']==side)]

    def free_cash(self):
        locked=sum((o['volume']-o['filled'])*o['price']*(1+RULES['fee']) for o in self.active(side='bid'))
        return max(0,self.state['cash']-locked)

    def equity(self):
        return self.state['cash']+sum(p['volume']*self.bid(m,p['entry'])*(1-RULES['fee']-RULES['impact'])
                                      for m,p in self.state['positions'].items())

    def bid(self,m,fallback=0.):
        q=self.quotes.get(m)
        return q['units'][0]['bid_price'] if q else fallback

    def ingest(self, market, units, stamp, source='websocket', now=None):
        now=time.time() if now is None else now
        try:
            if not valid_code(market) or not 0<=now-stamp<=2 or not units:return
            cleaned=[]
            for u in units:
                v={k:float(u[k]) for k in ('ask_price','bid_price','ask_size','bid_size')}
                if (not all(math.isfinite(x) for x in v.values()) or min(v.values())<0
                        or not 0<v['bid_price']<=v['ask_price']):return
                cleaned.append(v)
            if any(b['ask_price']<a['ask_price'] or b['bid_price']>a['bid_price'] for a,b in zip(cleaned,cleaned[1:])):return
            with self.lock:
                if stamp <= self.quotes.get(market,{}).get('time',0):return
                self.quotes[market]={'time':stamp,'received':now,'units':cleaned,'source':source}
                self.quotes[market]['received_mono']=time.monotonic()
                if source=='websocket':self.ws_received=now
                important=market in self.state['positions'] or market in self.candidates or bool(self.active(market))
                if important:self.record_event({'type':'book','market':market,'book':{'time':stamp,'units':cleaned},'source':source})
            if important:self.wake.set()
        except (KeyError,TypeError,ValueError):return

    def action(self, body):
        with self.lock:
            if body.get('mode','paper')!='paper':
                raise ValueError('V3 실거래 신규 매수는 수익성·실시간 검증 미통과로 차단됩니다. 모의만 가능합니다.')
            now=time.time();a=body['action']
            if a=='auto_v3_allocation':
                if self.running or self.active(side='bid'):
                    raise ValueError('신규 매수를 정지하고 매수 예약을 취소한 뒤 저장하세요.')
                self.allocation.update(body)
                self.event('v4 투자금 배분 설정 저장 · 이후 신규 진입 적용',now)
                return '저장 완료 · 기존 보유분 손절·수량은 유지하며 이후 신규 진입부터 적용합니다.'
            if a=='auto_v3_position_profile':
                return self.move_position_profile(body.get('market'),body.get('bucket'),now)
            if a=='auto_v3_config':
                if self.running or self.state['positions'] or self.active():
                    raise ValueError('신규 매수 정지 후 보유분·예약을 정리해야 자금/종목을 변경할 수 있습니다.')
                budget=float(body.get('budget',0));markets=body.get('markets')
                if not math.isfinite(budget) or not 20000<=budget<=1e9:raise ValueError('모의 운용자금은 2만~10억 원입니다.')
                mode=body.get('scope','selected')
                if mode not in ('all','selected'):raise ValueError('감시 범위 오류')
                if mode=='all':markets=sorted(self.catalog) or self.state['markets']
                elif (not isinstance(markets,list) or not markets or len(set(markets))!=len(markets)
                      or not set(markets)<=set(self.catalog or MARKETS)):
                    raise ValueError('현재 원화마켓에 등록된 종목을 선택하세요.')
                if self.state['fills'] and abs(budget-self.state['funded'])>.001:
                    raise ValueError('거래 후 원금 변경은 초기화로만 가능합니다. 손익이 반영된 자금을 계속 사용합니다.')
                self.state.update(markets=markets,universe_mode=mode)
                if not self.state['fills']:
                    self.state.update(funded=budget,cash=budget,day_start=budget,peak=budget)
                self.event('모의 권한 부여: '+f'{budget:,.0f}원 · '+('원화마켓 전체' if mode=='all' else ', '.join(markets)),now);self.save()
                return '독립 모의 운용자금 설정 완료 · 실제 계좌 자금은 이동하지 않습니다.'
            if a=='auto_v3_start':
                if not self.state['funded']:raise ValueError('운용자금을 먼저 지정하세요.')
                self.risk(now)
                if self.state['review'] or self.state['day_stop'] or now<self.state['pause_until']:
                    raise ValueError('손실 한도/연속 손실 대기 중입니다.')
                self.running=True;self.event('V3 자동 신규 매수 시작',now);self.save();self.wake.set()
                return 'V3 모의 신규 매수 시작 · 보유분 청산 감시는 별도로 계속됩니다.'
            if a=='auto_v3_pause':
                self.running=False;self.cancel_buys('신규 매수 정지',now)
                self.event('V3 신규 매수 정지 · 예약 매수 취소 · 보유분 보호 유지',now);self.save()
                return 'V3 예약 매수 취소 · 보유분 손절/매도 예약은 계속 관리합니다.'
            if a=='auto_v3_flatten':
                self.running=False;self.cancel_buys('전량 청산',now)
                for p in self.state['positions'].values():p['exiting']='수동 전량 청산'
                self.event('V3 봇 소유분 전량 청산 요청',now);self.save();self.wake.set()
                return '모의 소유분 청산 요청 · 최신 호가에서 IOC로 처리합니다.'
            if a=='auto_v3_cancel':
                o=next((o for o in self.active() if o['id']==body.get('id')),None)
                if not o:raise ValueError('대기 중인 V3 예약이 아닙니다.')
                self.cancel(o,'사용자 취소',now)
                if o['side']=='ask' and o['market'] in self.state['positions']:
                    self.state['positions'][o['market']]['partial_disabled']=True
                self.save();return 'V3 예약 취소 · 손절 감시는 계속됩니다.'
            if a=='auto_v3_reset':
                if self.running or self.state['positions'] or self.active():raise ValueError('정지 후 보유분·예약을 먼저 정리하세요.')
                archive=self.path.with_name('auto_v3_archive_'+str(time.time_ns())+'.json')
                atomic(archive,self.state);self.state=fresh_state();self.save()
                return '기존 V3 장부 보관 후 모의 초기화 완료'
            raise ValueError('지원하지 않는 V3 작업입니다.')

    def move_position_profile(self,market,bucket,now):
        with self.lock:
            p=self.state['positions'].get(market)
            if not p or bucket not in PROFILES:raise ValueError('보유 중인 V3 종목과 투자 분류를 확인하세요.')
            if p.get('exiting'):raise ValueError('청산 처리 중인 종목은 분류를 변경할 수 없습니다.')
            if p.get('bucket','short')==bucket:return '현재 투자 분류와 같습니다.'
            if self.active(market,'bid'):raise ValueError('미체결 매수 잔량을 먼저 취소하세요.')
            for order in self.active(market,'ask'):
                if order['kind']!='partial':raise ValueError('청산 주문이 진행 중입니다.')
            for order in self.active(market,'ask'):self.cancel(order,'투자 기간 변경: 익절 예약 재평가',now)
            rule=self.profiles()[bucket]
            p.update(bucket=bucket,profile_since=now,max_seconds=rule['max_hours']*3600,
                     progress_seconds=rule['progress_seconds'],profile_pending=True)
            if bucket=='short':p.pop('partial_disabled',None)
            self.contexts.pop(market,None);self.signals.pop(market,None);self.last_scan_market.pop(market,None)
            self.event(market+' '+rule['name']+' 이동 · 기존 손절선 유지 · 새 기간 ATR 준비',now)
            self.record_event({'type':'profile','market':market,'bucket':bucket})
            self.save();self.wake.set()
            return rule['name']+' 분류 적용 · 보유량·원금 유지, 새 기간 기준은 다음 봉 조회에 반영됩니다.'

    def cancel(self,o,reason,now):
        if o['status']!='wait':return
        o.update(status='cancel',ended=now,message=reason)
        self.event(o['market']+' '+reason,now)

    def cancel_buys(self,reason,now):
        for o in self.active(side='bid'):self.cancel(o,reason,now)

    def risk(self,now):
        s=self.state;eq=self.equity()
        day=dt.datetime.fromtimestamp(now,KST).date().isoformat()
        # Freeze day baseline when held asset prices are stale; never clear a halt on stale marks.
        fresh=all(m in self.quotes and 0<=now-self.quotes[m]['time']<=2 for m in s['positions'])
        if s['day']!=day and fresh:
            s.update(day=day,day_start=eq,day_stop=False,day_liquidate=False);self.dirty=True
        s['peak']=max(s['peak'],eq)
        if s['day_start'] and eq<=s['day_start']*.985:
            if not s['day_stop']:self.event('일일 -1.5%: 신규 매수 중단',now)
            s['day_stop']=True;self.cancel_buys('일일 손실 중단',now)
        if s['day_start'] and eq<=s['day_start']*.98:
            s['day_liquidate']=True
        if s['peak'] and eq<=s['peak']*.95:
            s['review']=True;s['day_liquidate']=True
        if s['day_liquidate']:
            for p in s['positions'].values():p['exiting']='일일/누적 손실 방어'
        return not s['review'] and not s['day_stop'] and now>=s['pause_until'] and fresh

    def new_order(self,m,side,price,volume,kind,now,**extra):
        volume=qty(volume);price=rounded(price,m,up=side=='ask')
        if volume<=0 or price*volume<RULES['minimum']:
            return None
        if self.active(m,side):raise ValueError('중복 V3 예약 차단')
        if side=='bid' and price*volume*(1+RULES['fee'])>self.free_cash()+1e-6:
            raise ValueError('예약 포함 운용자금 초과')
        if side=='ask' and volume>self.state['positions'][m]['volume']+1e-10:
            raise ValueError('V3 소유 수량 초과')
        o={'id':'v3-'+uuid.uuid4().hex,'market':m,'side':side,'price':price,'volume':volume,
           'kind':kind,'created':now,'status':'wait','filled':0.,'funds':0.,'fee':0.,
           'message':'모의 지정가 예약','ioc':False,**extra}
        self.state['orders'].append(o);self.event(m+' '+kind+' 지정가 예약',now)
        self.save()  # Durable intent before simulated fills; no POST exists in this service.
        return o

    def match(self,o,book,now):
        if o['status']!='wait':return
        q,funds=0.,0.;left=o['volume']-o['filled'];side='ask' if o['side']=='bid' else 'bid'
        for u in book:
            # Adverse simulated impact must still respect the reservation limit.
            price=u[side+'_price']*(1+RULES['impact'] if side=='ask' else 1-RULES['impact'])
            if (o['side']=='bid' and price>o['price']) or (o['side']=='ask' and price<o['price']):break
            take=qty(min(left-q,u[side+'_size']))
            if take<=0:continue
            if side=='ask':take=qty(min(take,self.state['cash']/(price*(1+RULES['fee']))))
            q+=take;funds+=take*price;u[side+'_size']-=take
            if left-q<1e-8:break
        if q>0:
            fee=funds*RULES['fee'];s=self.state;m=o['market']
            if o['side']=='bid':
                old=s['positions'].get(m)
                volume=(old['volume'] if old else 0)+q
                cost=(old['cost'] if old else 0)+funds+fee
                raw_funds=(old['entry']*old['volume'] if old else 0)+funds
                entry=raw_funds/volume
                # Never lower an already protected stop on partial fill.
                planned_stop=o['stop_price'] if o.get('strategy')=='breakout' else initial_stop(entry,o['atr'],o['low'])
                stop=max(old['stop'] if old else 0,planned_stop)
                p=old or {'since':now,'peak':entry,'trough':entry,'part_done':False,'net':0.,
                          'max_seconds':self.profiles()['short']['max_hours']*3600,
                          'progress_seconds':self.profiles()['short']['progress_seconds'],
                          'buy_cost':0.,'signal':o['signal'],'atr':o['atr'],'original_stop':stop,
                          'strategy':o.get('strategy','pullback'),'breakout_level':o.get('breakout'),
                          'cost_rate':o['cost_rate']}
                p.update(volume=volume,cost=cost,entry=entry,stop=stop,r=entry-stop)
                p['buy_cost']+=funds+fee;s['positions'][m]=p;s['cash']-=funds+fee
                distance=(entry-stop)/entry
                max_distance=BREAKOUT['max_distance'] if p.get('strategy')=='breakout' else RULES['max_distance']
                if (not RULES['min_distance']<=distance<=max_distance or p['r']<=0
                        or cost*(max(0,distance)+o['cost_rate'])>self.equity()*self.allocation.value['risk']+1):
                    p['exiting']='체결 후 위험 한도 초과';self.cancel(o,'잔여 매수 차단',now)
            else:
                p=s['positions'][m];basis=p['cost']*q/p['volume']
                p['cost']-=basis;p['volume']=max(0,p['volume']-q)
                s['cash']+=funds-fee;p['net']+=funds-fee-basis
                if o['kind']=='partial':p['part_done']=True
                if p['volume']<1e-8:
                    result={'market':m,'since':p['since'],'time':now,'net':p['net'],
                            'cost':p['buy_cost'],'mfe_pct':(p['peak']/p['entry']-1)*100,
                            'mae_pct':(p['trough']/p['entry']-1)*100,'entry':p['entry'],
                            'exit':funds/q,'reason':o.get('reason',o['kind']),'post':{},
                            'strategy':p.get('strategy','pullback')}
                    s['closed'].append(result);del s['positions'][m]
                    loss=result['net']<0
                    s['loss_streak']=s['loss_streak']+1 if loss else 0
                    s['cooldown'][m]=now+RULES['cooldown_seconds']
                    if s['loss_streak']>=3:
                        s['pause_until']=now+3600;s['loss_streak']=0
                        self.cancel_buys('3회 연속 손실 대기',now)
            o.update(filled=o['filled']+q,funds=o['funds']+funds,fee=o['fee']+fee)
            s['fills'].append({'order':o['id'],'time':now,'market':m,'side':o['side'],
                               'volume':q,'funds':funds,'fee':fee,'price':funds/q,'kind':o['kind'],
                               'strategy':o.get('strategy',s['positions'].get(m,{}).get('strategy','pullback'))})
            self.event(m+' '+o['kind']+f' 체결 {q:.8f}',now)
        if o['volume']-o['filled']<1e-8:o.update(status='done',ended=now)
        elif o['ioc']:self.cancel(o,'IOC 잔량 취소',now)
        if q>0 or o['ioc']:self.save()

    def step(self,now=None):
        requested=time.monotonic();supplied=now
        with self.lock:
            now=time.time() if supplied is None else supplied
            started=time.monotonic()
            self.metrics['lock_wait_ms'].append((started-requested)*1000)
            if self.last_tick:self.metrics['monitor_ms'].append((started-self.last_tick)*1000)
            self.last_tick=started
            healthy=self.risk(now)
            self.breakout_observations(now)
            effective=self.signals.copy()
            for m, signal in self.breakout_signals.items():
                if signal.get('ok') and not effective.get(m,{}).get('ok'):effective[m]=signal
            # Hundreds of passive observations must never delay owned-position exits.
            protected=list(self.state['positions'])+[o['market'] for o in self.active()]
            ready=[m for m,s in effective.items() if s.get('ok') and 0<=now-s.get('closed_at',0)<=30]
            work=list(dict.fromkeys(protected+ready))
            for m in work:
                q=self.quotes.get(m);sig=effective.get(m,{})
                if not q or not 0<=now-q['time']<=2:
                    for o in self.active(m,'bid'):self.cancel(o,'호가 지연: 매수 취소',now)
                    continue
                self.metrics['data_age_ms'].append((now-q['time'])*1000)
                # A quote's displayed liquidity may be consumed only ONCE.
                new_book=q.get('processed')!=q['time']
                book=copy.deepcopy(q['units']) if new_book else []
                if new_book:q['processed']=q['time']
                bid=q['units'][0]['bid_price']
                p=self.state['positions'].get(m)
                ctx=self.contexts.get(m,{})
                context={**ctx,'fresh':0<=now-ctx.get('closed_at',0)<ctx.get('unit_seconds',60)}
                if p:
                    if p.get('strategy')=='breakout' and p.get('bucket','short')=='short':
                        bc=self.breakout_contexts.get(m)
                        if bc and 0<=now-bc['closed_at']<75:
                            context={**context,'atr':bc['atr']}
                    prior=(p['stop'],p['peak'],p['trough'])
                    reason=p.get('exiting') or exit_decision(p,bid,context,now)
                    self.dirty |= prior!=(p['stop'],p['peak'],p['trough'])
                    if reason:
                        p['exiting']=reason
                        for o in self.active(m):self.cancel(o,'청산 우선: 기존 예약 취소',now)
                        if new_book:
                            o=self.new_order(m,'ask',bid*.995,p['volume'],'exit',now,ioc=True,reason=reason,
                                             strategy=p.get('strategy','pullback'))
                            if o:
                                self.metrics['decision_ms'].append((time.monotonic()-q['received_mono'])*1000)
                                self.match(o,book,now)
                            else:
                                self.status=m+' 최소 주문금액 미달 잔량 · 자동 청산 불가'
                        continue
                # Expire/revalidate passive entries before matching a newly crossed book.
                for o in list(self.active(m,'bid')):
                    if (not self.running or not healthy or not self.entry_allowed(m,now) or now>o['expires'] or bid<=o['breakout']
                            or q['units'][0]['ask_price']>o['ceiling']
                            or (q['units'][0]['ask_price']-bid)/((q['units'][0]['ask_price']+bid)/2)>RULES['max_spread']):
                        self.cancel(o,'매수 예약 만료/조건 이탈',now)
                    elif now-o.get('repriced',o['created'])>=3 and not p:
                        # Confirm cancel locally before a replacement. No simultaneous reservations.
                        updated=rounded(min(bid,o['ceiling']),m)
                        if updated!=o['price']:
                            self.cancel(o,'호가 변화에 따른 예약 조정',now)
                            volume=(o['volume']-o['filled'])*o['price']/updated
                            extra={k:o[k] for k in ('atr','low','cost_rate','signal','expires','breakout','ceiling')}
                            self.new_order(m,'bid',updated,volume,'entry',now,**extra)
                if new_book:
                    for o in list(self.active(m)):self.match(o,book,now)
                p=self.state['positions'].get(m)
                if p and p.get('bucket','short')=='short' and not p.get('exiting') and not self.active(m,'bid'):
                    if not p.get('part_done') and not p.get('partial_disabled') and not self.active(m,'ask'):
                        target=p['entry']+RULES['partial_r']*p['r']
                        partial=qty(p['volume']*RULES['partial_ratio'])
                        if partial*target>=RULES['minimum'] and (p['volume']-partial)*bid>=RULES['minimum']:
                            self.new_order(m,'ask',target,partial,'partial',now,strategy=p.get('strategy','pullback'))
                        else:p['partial_disabled']=True;self.dirty=True
                # Positions and outstanding entries BOTH consume slot, exposure and risk.
                occupied=set(self.state['positions'])|{o['market'] for o in self.active(side='bid')}
                if (self.running and healthy and self.entry_allowed(m,now) and m not in occupied and len(occupied)<self.allocation.value['max_positions']
                        and (sig.get('strategy')!='breakout' or new_book)
                        and now>=self.state['cooldown'].get(m,0) and sig.get('id')!=self.state['last_signal'].get(m)):
                    try:
                        exposure=sum(p['volume']*self.bid(k,p['entry']) for k,p in self.state['positions'].items())
                        risk=sum(p['volume']*max(0,p['entry']-p['stop'])+p['cost']*p.get('cost_rate',.002)
                                 for p in self.state['positions'].values())
                        for o in self.active(side='bid'):
                            rem=o['volume']-o['filled'];exposure+=rem*o['price']
                            stop=o['stop_price'] if o.get('strategy')=='breakout' else initial_stop(o['price'],o['atr'],o['low'])
                            risk+=rem*(max(0,o['price']-stop)+o['price']*o['cost_rate'])
                        if sig.get('strategy')=='breakout':
                            plan=breakout_plan(sig,q,self.equity(),self.free_cash(),exposure,risk,now,
                                               float(tick(Decimal(str(bid)),m)), rules=self.allocation.rules(RULES))
                        else:
                            try:
                                plan=entry_plan(sig,q,self.equity(),self.free_cash(),exposure,risk,now,rules=self.allocation.rules(RULES))
                                plan['ceiling']=sig['close']+.25*sig['atr']
                                plan['strategy']='pullback'
                            except ValueError:
                                burst=self.breakout_signals.get(m,{})
                                if not burst.get('ok') or not new_book:raise
                                sig=burst
                                plan=breakout_plan(sig,q,self.equity(),self.free_cash(),exposure,risk,now,
                                                   float(tick(Decimal(str(bid)),m)), rules=self.allocation.rules(RULES))
                        price=rounded(plan.pop('price'),m,up=sig.get('strategy')=='breakout')
                        if sig.get('strategy')=='breakout' and price>plan['ceiling']:
                            raise ValueError('돌파 호가 반올림 후 추격 가격 초과')
                        plan=adjust_plan(plan, price, self.allocation.value['volatility_target'])
                        amount=plan.pop('amount');plan.pop('stop')
                        order=self.new_order(m,'bid',price,amount/price,'entry',now,**plan)
                        self.entry_blocks.pop(m,None)
                        if order and order['ioc']:
                            # Never reuse liquidity already consumed by another simulated fill.
                            if new_book:self.match(order,book,now)
                            else:self.cancel(order,'돌파 IOC 신규 호가 대기',now)
                        self.state['last_signal'][m]=sig['id'];self.dirty=True
                    except ValueError as e:
                        self.status=m+': '+str(e)
                        self.entry_blocks[m]={'time':now,'strategy':sig.get('strategy','pullback'),'reason':str(e)}
            for r in self.state['closed']:
                q=self.quotes.get(r['market'])
                if not q or now-q['time']>2:continue
                for minutes in (5,15,30):
                    key=str(minutes)
                    if key not in r['post'] and 0<=now-r['time']-minutes*60<=30:
                        r['post'][key]={'pct':(self.bid(r['market'])/r['exit']-1)*100,'time':now};self.dirty=True
            if now-self.last_sample>=60 and self.state['funded']:
                self.state['samples'].append({'time':now,'equity':self.equity()})
                self.state['samples']=self.state['samples'][-50000:];self.last_sample=now;self.dirty=True
            if self.dirty and time.monotonic()-self.last_save>=1:self.save()

    def snapshot(self):
        with self.lock:
            now=time.time();source=self.state;eq=self.equity()
            # Slice BEFORE copying so months of audit fills cannot stall the fast monitor.
            bounded={k:v for k,v in source.items() if k not in ('orders','fills','closed','samples')}
            bounded.update(orders=source['orders'][-100:]+[o for o in source['orders'][:-100] if o['status']=='wait'],
                           fills=source['fills'][-100:],closed=source['closed'][-100:],samples=source['samples'][-1440:])
            s=copy.deepcopy(bounded)
            def percentiles(values):
                v=sorted(values)
                return {k:v[min(len(v)-1,int((len(v)-1)*p))] if v else None for k,p in (('p50',.5),('p95',.95),('p99',.99))}
            results=source['closed'];profits=sum(max(0,r['net']) for r in results);losses=-sum(min(0,r['net']) for r in results)
            stats={'closed':len(results),'wins':sum(r['net']>0 for r in results),
                   'profit_factor':profits/losses if losses else None,'net':sum(r['net'] for r in results)}
            return {'version':VERSION,'mode':'paper','running':self.running,'state':s,'equity':eq,
                    'free_cash':self.free_cash(),'pnl':eq-s['funded'],'status':self.status,
                    'allocation':self.allocation.value.copy(),
                    'signals':copy.deepcopy(self.signals),'contexts':copy.deepcopy(self.contexts),
                    'breakout_signals':copy.deepcopy(self.breakout_signals),'breakout_rules':BREAKOUT,
                    'entry_blocks':copy.deepcopy(self.entry_blocks),
                    'strategy_stats':{strategy:{'closed':sum(r.get('strategy','pullback')==strategy for r in results),
                        'net':sum(r['net'] for r in results if r.get('strategy','pullback')==strategy),
                        'wins':sum(r['net']>0 for r in results if r.get('strategy','pullback')==strategy)}
                        for strategy in ('pullback','breakout')},
                    'quotes':{m:{'bid':q['units'][0]['bid_price'],'ask':q['units'][0]['ask_price'],
                                 'age':max(0,now-q['time']),'source':q['source']} for m,q in self.quotes.items()},
                    'metrics':{k:percentiles(v) for k,v in self.metrics.items()},'stats':stats,
                    'feed_error':self.feed_error,'scan_error':self.scan_error,
                    'record_dropped':self.record_dropped,
                    'universe':{'total':len(source['markets']),'listed':len(self.catalog),
                                'eligible':sum(r['eligible'] for m,r in self.screened.items() if m in source['markets']),
                                'candidates':self.candidates.copy(),'time':self.universe_time,
                                'fast_candidates':self.fast_candidates.copy(),
                                'details':candidate_details(source['markets'], self.screened, self.tickers,
                                                            self.candidates, self.protected_markets()),
                                'error':self.universe_error,'scope':source['universe_mode'],
                                'rows':copy.deepcopy(self.screened),'last_scanned':self.last_scan_market.copy()},
                    'live_entry_approved':False,'rules':RULES,
                    'feed_state':'장애' if any(now-self.quotes.get(m,{}).get('time',0)>5 for m in self.protected_markets()+self.candidates)
                                 else '지연' if any(now-self.quotes.get(m,{}).get('time',0)>2 for m in self.protected_markets()+self.candidates)
                                 else '정상' if self.quotes else '준비 중'}

    def scan_once(self):
        # Only this worker owns series; never use the legacy engine's shared candle cache.
        now=time.time();boundary=int(now//60)*60
        with self.lock:jobs=list(dict.fromkeys(self.protected_markets()+self.candidates))
        # A bounded round lets owned coins refresh first without delaying the quote monitor.
        for m in jobs:
            if self.stop.is_set():break
            with self.lock:
                if self.last_scan_market.get(m,0)>=boundary:continue
            try:
                with self.lock:bucket=self.state['positions'].get(m,{}).get('bucket','short')
                if bucket!='short':
                    unit=PROFILES[bucket]['unit']
                    rows=load_closed(self.public,self.series,m,unit,200)
                    checked=time.time();validate_bars(rows,unit*60,200,checked)
                    f=indicators(rows)
                    ctx={'atr':f['atr'],'close':f['close'],'ema20':f['ema20'],
                         'closed_at':rows[-1]['t']+unit*60,'bucket':bucket,'unit_seconds':unit*60}
                    with self.lock:
                        if self.state['positions'].get(m,{}).get('bucket')==bucket:
                            self.contexts[m]=ctx
                            self.state['positions'][m]['profile_pending']=False
                            self.state['positions'][m]['management_atr']=f['atr']
                            self.state['positions'][m]['stop']=max(self.state['positions'][m]['stop'],
                                self.bid(m,self.state['positions'][m]['entry'])-PROFILES[bucket]['stop_atr']*f['atr'])
                            self.last_scan_market[m]=boundary;self.dirty=True
                            self.record_event({'type':'signal','market':m,'signal':ctx|{'ok':False,'time':checked}})
                    continue
                for unit,count in ((15,200),(5,300),(1,200)):
                    key=(m,unit);old=self.series.get(key,[])
                    if old and old[-1]['t']+unit*60==int(now//(unit*60))*unit*60 and len(old)>=count:continue
                    data={b['t']:b for b in old};params={'market':m,'count':200}
                    for _ in range(1 if len(old)>=count else math.ceil((count+1)/200)):
                        raw=self.public('/v1/candles/minutes/'+str(unit),params)
                        if not raw:break
                        for r in raw:
                            stamp=dt.datetime.fromisoformat(r['candle_date_time_utc']).replace(tzinfo=dt.timezone.utc).timestamp()
                            if stamp+unit*60<=time.time():
                                b={'t':stamp,'h':float(r['high_price']),'l':float(r['low_price']),
                                   'c':float(r['trade_price']),'v':float(r['candle_acc_trade_volume'])}
                                if 'candle_acc_trade_price' in r:b['value']=float(r['candle_acc_trade_price'])
                                data[stamp]=b
                        params['to']=raw[-1]['candle_date_time_utc']+'Z'
                    self.series[key]=sorted(data.values(),key=lambda b:b['t'])[-count:]
                sig=evaluate_intraday(self.series[(m,15)],self.series[(m,5)],self.series[(m,1)],time.time())
                try:breakout_context=prepare_breakout(self.series[(m,1)],time.time())
                except (KeyError,ValueError,TypeError,ZeroDivisionError):breakout_context=None
                with self.lock:
                    if breakout_context:self.breakout_contexts[m]=breakout_context
                    else:self.breakout_contexts.pop(m,None)
                    self.signals[m]=sig
                    if 'atr' in sig:
                        self.contexts[m]={k:sig[k] for k in ('atr','close','ema20','closed_at')}|{'bucket':'short','unit_seconds':60}
                        if m in self.state['positions']:self.state['positions'][m]['profile_pending']=False
                    self.last_scan_market[m]=boundary
                    self.record_event({'type':'signal','market':m,'signal':sig})
                    self.scan_error=''
            except Exception:
                with self.lock:
                    self.breakout_contexts.pop(m,None)
                    self.signals[m]={'ok':False,'reason':'V3 봉 조회 지연 · 신규 매수 대기','time':time.time()}
                    self.scan_error='V3 지표 조회 지연'
        self.wake.set()
        return boundary

    def scan_loop(self):
        while not self.stop.is_set():
            self.scan_once()
            self.stop.wait(2)

    def feed_loop(self):
        backoff=1
        while not self.stop.is_set():
            with self.lock:
                # Feed only owned/candidate books via REST; WS covers the whole universe.
                targets=list(dict.fromkeys(self.protected_markets()+self.candidates))
                stale=[m for m in targets if time.time()-self.quotes.get(m,{}).get('time',0)>1]
            if stale:
                try:
                    rows=self.public('/v1/orderbook',{'markets':','.join(stale[:30]),'level':0})
                    for r in rows:self.ingest(r['market'],r['orderbook_units'],float(r['timestamp'])/1000,'rest')
                    with self.lock:self.feed_error=''
                    backoff=1
                except ApiError as e:
                    backoff=60 if e.status==418 else min(30,max(2,backoff*2))
                    with self.lock:self.feed_error='V3 시세 요청 제한/지연 · 신규 매수 중지'
                except Exception:
                    backoff=min(30,max(2,backoff*2))
                    with self.lock:self.feed_error='V3 시세 연결 확인 중'
            self.stop.wait(backoff)

    def monitor_loop(self):
        while not self.stop.is_set():
            try:self.step()
            except Exception:
                with self.lock:
                    self.running=False;self.status='V3 처리 오류 · 신규 매수 중지 · 기록 확인 필요'
                    self.cancel_buys('처리 오류',time.time());self.save()
            self.wake.wait(.25);self.wake.clear()
        with self.lock:
            self.running=False;self.cancel_buys('서버 종료',time.time());self.save()

    def start(self):
        if self.started:return
        self.started=True
        with self.lock:
            # Old passive entries must not fill after a stopped/restarted session.
            self.cancel_buys('재시작: 신규 매수 정지',time.time());self.save()
        for fn in ((self.record_loop,) if self.record else ())+(self.monitor_loop,self.universe_loop,self.scan_loop,self.feed_loop):
            thread=threading.Thread(target=fn,daemon=True,name='v3-'+fn.__name__)
            self.threads.append(thread);thread.start()

    def record_loop(self):
        # Rotated daily in KST. Only public market data; no account data or keys.
        compact_books={}
        while not self.stop.is_set() or not self.record_queue.empty():
            try:event=self.record_queue.get(timeout=.25)
            except queue.Empty:continue
            date=dt.datetime.fromtimestamp(event['time'],KST).date().isoformat()
            path=self.path.parent/('auto_v3_replay_'+date+'.jsonl')
            try:
                compact=self.path.parent/('auto_v3_compact_'+date+'.jsonl')
                is_book=event.get('type')=='book';market=event.get('market')
                if not is_book or event['time']-compact_books.get(market,0)>=.25:
                    if not compact.exists() or compact.stat().st_size<64*1024*1024:
                        with compact.open('a',encoding='utf-8') as f:
                            f.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n')
                        if is_book:compact_books[market]=event['time']
                if path.exists() and path.stat().st_size>=256*1024*1024:
                    with self.lock:self.record_dropped+=1
                    continue
                with path.open('a',encoding='utf-8') as f:
                    f.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n')
            except OSError:
                with self.lock:self.record_dropped+=1

    def close(self):
        self.stop.set();self.wake.set()
        for thread in self.threads:thread.join(timeout=5)
