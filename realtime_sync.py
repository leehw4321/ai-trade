"""Upbit public/private WebSockets. Read-only; credentials remain server-side."""
import copy
import json
import logging
import math
import threading
import time
import uuid


class RealtimeSync:
    def __init__(self,portfolio):
        self.p=portfolio
        self.stop=portfolio.engine.stop_event
        self.lock=threading.Lock()
        self.channels={c:{'connected':False,'received':0,'messages':0,'error':''} for c in ('public','private')}
        self.ticks={};self.books={};self.assets={};self.sockets={}
        self.subscribed=None
        self.book_listener=None
        self.trade_listener=None
        self.book_provider=None

    def snapshot(self):
        with self.lock:return copy.deepcopy(self.channels)

    def message(self,channel,raw):
        try:
            data=json.loads(raw)
            if not isinstance(data,dict):return
            if data.get('error'):
                with self.lock:
                    self.channels[channel].update(connected=False,error='실시간 구독 오류 · REST 조회 병행')
                return
            kind=data.get('type');now=time.time()
            if channel=='public' and kind=='trade' and self.trade_listener:
                self.trade_listener(data, now)
            # V3 has its own short lock; slow account refresh never delays its book cache.
            if channel=='public' and kind=='orderbook' and self.book_listener:
                self.book_listener(data.get('code'),data.get('orderbook_units',[]),
                                   float(data.get('timestamp',0))/1000,'websocket',now)
            with self.lock:
                self.channels[channel].update(received=now,error='')
                self.channels[channel]['messages']+=1
                if channel=='public' and kind=='ticker':
                    m=data.get('code')
                    if isinstance(m,str) and m.startswith(('KRW-','BTC-','USDT-')):self.ticks[m]=data
                elif channel=='public' and kind=='orderbook':
                    m=data.get('code')
                    if isinstance(m,str) and m.startswith('KRW-'):self.books[m]=data
                elif channel=='private' and kind=='myAsset':
                    stamp=data.get('asset_timestamp',data.get('timestamp',0))
                    if not isinstance(stamp,(int,float)) or not math.isfinite(stamp):return
                    for a in data.get('assets',[]):
                        c=a.get('currency')
                        if isinstance(c,str) and c.isalnum():
                            old=self.assets.get(c)
                            if not old or stamp>old['stamp']:self.assets[c]={'stamp':stamp,'asset':a}
                    self.p.account_wake.set()
                elif channel=='private' and kind=='myOrder':
                    self.p.account_wake.set()
        except (ValueError,TypeError,KeyError):return

    def apply(self):
        if not self.p.lock.acquire(timeout=.05):return
        try:
            with self.lock:
                ticks,self.ticks=self.ticks,{}
                books,self.books=self.books,{}
                assets,self.assets=self.assets,{}
            now=time.time()
            for m,t in ticks.items():
                price=t.get('trade_price');stamp=t.get('timestamp',0)/1000
                if not isinstance(price,(int,float)) or not math.isfinite(price) or price<=0 or not 0<=now-stamp<=10:continue
                old=self.p.tickers.get(m,{})
                if t.get('timestamp',0)<old.get('timestamp',0):continue
                self.p.tickers[m]=old|{k:v for k,v in t.items() if k not in ('type','code')}
                self.p.market_time=now
            for m,b in books.items():
                stamp=b.get('timestamp',0)/1000;units=b.get('orderbook_units',[])
                if units and 0<=now-stamp<=8:
                    self.p.quotes[m]={'time':stamp,'units':units}
            for c,record in assets.items():
                stamp=record['stamp'];a=record['asset']
                if stamp<=self.p.account_events.get(c,0) or stamp<self.p.account_request_started*1000:continue
                try:
                    balance=float(a['balance']);locked=float(a['locked'])
                    if not all(math.isfinite(v) and v>=0 for v in (balance,locked)):continue
                except (ValueError,TypeError,KeyError):continue
                row=next((a for a in self.p.accounts if a['currency']==c),None)
                if row is None:
                    row={'currency':c,'avg_buy_price':'0','unit_currency':'KRW'}
                    self.p.accounts.append(row)
                row.update(balance=str(balance),locked=str(locked),avg_pending=c!='KRW')
                self.p.account_events[c]=stamp
                self.p.account_time=now
        finally:self.p.lock.release()

    def subscription(self,channel):
        if channel=='private':return [{'ticket':'account-'+uuid.uuid4().hex},{'type':'myAsset'},{'type':'myOrder'}]
        with self.p.lock:
            codes=sorted({m for m in self.p.catalog if m.startswith('KRW-')} |
                         {m for m in self.p.tickers if m.startswith(('BTC-','USDT-'))})
            books=self.p.watch()
        if self.book_provider:books=sorted(set(books)|set(self.book_provider()))
        if not codes:return None
        request = [{'ticket':'market-'+uuid.uuid4().hex},{'type':'ticker','codes':codes},
                   {'type':'orderbook','codes':books,'level':0}]
        if self.trade_listener:
            trades = [m for m in codes if m.startswith('KRW-')]
            if trades:request.append({'type':'trade','codes':trades,'is_only_realtime':True})
        return request

    def channel_loop(self,channel):
        try:
            import websocket
            logging.getLogger('websocket').setLevel(logging.CRITICAL)
        except ImportError:
            with self.lock:self.channels[channel]['error']='WebSocket 모듈 미설치 · REST 조회 병행'
            return
        while not self.stop.is_set():
            try:
                subscription=self.subscription(channel)
                if subscription is None:
                    self.stop.wait(1);continue
                headers=[]
                if channel=='private':headers=['Authorization: Bearer '+self.p.api().api.token()]
                def opened(ws):
                    ws.send(json.dumps(subscription))
                    if channel=='public':self.subscribed=(tuple(subscription[1]['codes']),tuple(subscription[2]['codes']))
                    with self.lock:self.channels[channel].update(connected=True,error='')
                    if channel=='private':self.p.account_wake.set()
                def failed(ws,error):
                    with self.lock:self.channels[channel].update(connected=False,error='연결 재시도 중 · REST 조회 병행')
                def closed(ws,*args):
                    with self.lock:self.channels[channel]['connected']=False
                    if channel=='public' and self.trade_listener:
                        self.trade_listener(None, time.time())
                ws=websocket.WebSocketApp('wss://api.upbit.com/websocket/v1'+('/private' if channel=='private' else ''),
                    header=headers,on_open=opened,on_message=lambda ws,m:self.message(channel,m),on_error=failed,on_close=closed)
                self.sockets[channel]=ws
                ws.run_forever(ping_interval=25,ping_timeout=10,suppress_origin=True)
            except Exception:
                with self.lock:self.channels[channel].update(connected=False,error='연결 재시도 중 · REST 조회 병행')
            if not self.stop.is_set():self.stop.wait(5)

    def work_loop(self):
        while not self.stop.is_set():
            self.apply()
            ws=self.sockets.get('public')
            with self.p.lock:
                codes={m for m in self.p.catalog if m.startswith('KRW-')} | {m for m in self.p.tickers if m.startswith(('BTC-','USDT-'))}
                current=(tuple(sorted(codes)),tuple(self.p.watch()))
            if self.book_provider:current=(current[0],tuple(sorted(set(current[1])|set(self.book_provider()))))
            if ws and self.subscribed and current!=self.subscribed:
                try:
                    request=self.subscription('public')
                    if request:
                        ws.send(json.dumps(request))
                        self.subscribed=(tuple(request[1]['codes']),tuple(request[2]['codes']))
                except Exception:pass
            self.stop.wait(.2)
        for ws in self.sockets.values():
            try:ws.close()
            except Exception:pass

    def start(self):
        for channel in ('public','private'):
            threading.Thread(target=self.channel_loop,args=(channel,),daemon=True).start()
        threading.Thread(target=self.work_loop,daemon=True).start()
