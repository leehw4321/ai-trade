"""Read-only, whole-KRW-universe screening with auditable scores.

Ranks are research priorities, never a probability or an order instruction.
Public data only; no broker, account, or order methods are used here.
"""
import copy
import datetime as dt
import json
import math
import re
import threading
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

from trend_engine import atomic, http, ema

VERSION='krw-research-2'
INTERVAL=600
STABLE={'USDT','USDC','DAI','USDE','USD1','FDUSD','TUSD','USDS'}
WEIGHTS={
    'long': {'liquidity':20,'trend':25,'momentum':10,'volume_growth':10,'stability':15,'history':10,'onchain':10},
    'swing':{'liquidity':25,'trend':30,'momentum':15,'volume_growth':15,'stability':10,'history':5},
    'short':{'liquidity':30,'trend':20,'momentum':15,'volume_growth':15,'stability':15,'history':5},
}
LABELS={'liquidity':'거래대금','trend':'추세','momentum':'기간 모멘텀','volume_growth':'거래대금 성장',
        'stability':'변동성 적합도','history':'관측 이력','onchain':'온체인 자금 성장'}
PROJECTS={
 'BTC':{'summary':'중앙 중개자 없이 전송 가능한 비트코인 네트워크 자산',
        'outlook':'결제·보유 수요와 시장 유동성의 지속성을 관찰',
        'risk':'가격 변동과 수요 변화가 크며 성장률을 TVL로 대체 평가할 수 없음',
        'url':'https://bitcoin.org/en/bitcoin-for-individuals','reviewed':'2026-10-27'},
 'XRP':{'summary':'XRP Ledger의 결제·통화 간 이전에 쓰이는 네이티브 자산',
        'outlook':'실제 결제 활용과 유동성의 확대 여부를 관찰',
        'risk':'사업 제휴가 XRP 수요로 직결되는지와 유통 공급을 별도 확인',
        'url':'https://xrpl.org/about/xrp','reviewed':'2026-10-27'},
 'SOL':{'summary':'Solana 거래 수수료에 쓰이는 네트워크 자산',
        'outlook':'앱 이용·네트워크 수수료 수요의 지속성을 관찰',
        'risk':'사용량과 토큰 가격은 별개이며 네트워크·경쟁 위험 존재',
        'url':'https://solana.com/docs/core/fees','reviewed':'2026-10-27'},
 'NEAR':{'summary':'스마트 계약과 멀티체인 기능을 제공하는 NEAR 생태계',
        'outlook':'앱·멀티체인 기능의 실제 이용과 자금 유입을 관찰',
        'risk':'개발 기능의 확대가 토큰 수익으로 연결되는지는 별도 검증 필요',
        'url':'https://docs.near.org/','reviewed':'2026-10-27'},
 'IRYS':{'summary':'저장 데이터와 스마트 계약 실행을 결합하는 데이터체인 설계',
        'outlook':'유료 저장 수요·데이터 활용 앱의 실제 성장을 관찰',
        'risk':'백서는 계획·설계를 포함하며 실사용·매출·유통 물량은 별도 확인 필요',
        'url':'https://irys.xyz/assets/IrysWhitepaper.pdf','reviewed':'2026-10-27'},
 'ORDER':{'summary':'Orderly의 거래 인프라·거버넌스·스테이킹 관련 토큰',
        'outlook':'프로토콜 실제 거래량·수수료 수입의 지속성을 관찰',
        'risk':'스테이킹 조건·보상 정책·거래량 감소와 유통 공급 위험 확인 필요',
        'url':'https://orderly.network/faq','reviewed':'2026-10-27'},
 'LA':{'summary':'Lagrange 증명 생성 비용과 스테이킹·위임에 쓰이는 토큰',
       'outlook':'실제 유료 증명 수요와 고객 확대 여부를 관찰',
       'risk':'단순 보유는 수수료 수입을 주지 않으며 토큰 배출·물량 유통 확인 필요',
       'url':'https://www.lagrangefoundation.org/blog/introducing-the-lagrange-token','reviewed':'2026-10-27'},
 'T':{'summary':'Threshold 네트워크의 tBTC 관련 가치·거버넌스 자산',
      'outlook':'브리지 이용과 네트워크 수요의 지속성을 관찰',
      'risk':'브리지 보안과 토큰 가치 연결을 확인해야 하며 보유 수익 보장 없음',
      'url':'https://docs.threshold.network/t-token','reviewed':'2026-10-27'},
 'VVV':{'summary':'Venice의 스테이킹·DIEM을 통한 AI API 이용 생태계 자산',
        'outlook':'유료 AI 이용과 토큰화된 연산 크레딧 수요를 관찰',
        'risk':'API 크레딧은 현금 수익이 아니며 서비스·토큰 배출 정책 변경 위험 존재',
        'url':'https://venice.ai/faqs/all','reviewed':'2026-10-27'},
 'MEGA':{'summary':'실시간 거래 처리를 지향하는 Ethereum L2 MegaETH 생태계',
         'outlook':'실제 앱 이용·예치자금 증가와 토큰 권리를 분리해 관찰',
         'risk':'기술 성능이 토큰 가격을 보장하지 않으며 권리·유통 일정 추가 확인 필요',
         'url':'https://docs.megaeth.com/','reviewed':'2026-10-27'},
 'PEPE':{'summary':'프로젝트가 오락 목적이라고 명시한 밈 토큰',
         'outlook':'사업 성장 대신 관심·유동성 변화에 좌우되는 투기 수요를 관찰',
         'risk':'공식 사이트가 내재가치·수익 기대가 없다고 명시 · 장기 후보 제외',
         'url':'https://www.pepe.vip/','reviewed':'2026-10-27','exclude_long':True},
 'XEC':{'summary':'작업증명과 Avalanche 계열 합의를 결합한 eCash 결제 네트워크',
        'outlook':'결제 이용·거래 수요와 네트워크 채택의 실제 증가를 관찰',
        'risk':'AVAX 프로젝트와는 별개이며 기술 목표가 가격 수익을 보장하지 않음',
        'url':'https://e.cash/blog/avalanche-faq','reviewed':'2026-10-27'},
 'ETH':{'summary':'네트워크 수수료와 지분증명 보안에 쓰이는 자산',
        'outlook':'앱·결제·스테이킹 수요의 확대 여부를 관찰',
        'risk':'네트워크 사용 증가가 토큰 수익으로 그대로 이어지지 않음',
        'url':'https://ethereum.org/what-is-ether/','reviewed':'2026-10-27'},
 'KAT':{'summary':'Katana의 스테이킹·유동성 보상 배분 참여 토큰',
        'outlook':'DeFi 유동성과 보상 참여의 지속성을 관찰',
        'risk':'인센티브 의존·향후 물량 유통과 토큰 가치 연결 확인 필요',
        'url':'https://docs.katana.network/katana/core-concepts/what-is-kat/','reviewed':'2026-10-27'},
 'UP2':{'summary':'Superform의 볼트 기반 수익 전략 생태계',
        'outlook':'볼트 이용·자금 유입의 지속성과 토큰 권리의 연결을 별도로 확인',
        'risk':'제품의 운용 수익이 해당 토큰 보유자의 수익을 뜻하지 않음',
        'url':'https://docs.superform.xyz/','reviewed':'2026-10-27'},
 'ONDO':{'summary':'Ondo의 토큰화 금융상품·DeFi 생태계',
         'outlook':'토큰화 상품의 실제 자산·이용 확대와 토큰 권리를 따로 관찰',
         'risk':'상품 자산 증가와 ONDO 가격은 별개이며 규제·운영 위험 존재',
         'url':'https://docs.ondo.finance/','reviewed':'2026-10-27'},
}


def finite(x,default=None):
    try:
        v=float(x)
        return v if math.isfinite(v) else default
    except (ValueError,TypeError):return default


def clamp(v,low=0,high=100):return max(low,min(high,v))
def norm(s):return re.sub('[^a-z0-9]','',str(s).lower())


def public_json(path):
    # Fixed origin, no keys, cookies or account data. Never follow article URLs.
    with urlopen(Request('https://api.llama.fi'+path,headers={'User-Agent':'local-coin-research/1'}),timeout=8) as r:
        raw=r.read(16*1024*1024+1)
        if len(raw)>16*1024*1024:raise ValueError('자료 크기 초과')
        return json.loads(raw)


def closed_bars(raw,unit,now):
    rows=[]
    for b in raw:
        t=dt.datetime.fromisoformat(b['candle_date_time_utc']).replace(tzinfo=dt.timezone.utc).timestamp()
        values=[finite(b.get(k)) for k in ('opening_price','high_price','low_price','trade_price','candle_acc_trade_price')]
        if any(v is None or v<0 for v in values) or min(values[:4])<=0:raise ValueError('봉 값 오류')
        if t+unit<=now:
            rows.append(dict(t=t,o=values[0],h=values[1],l=values[2],c=values[3],amount=values[4]))
    rows.sort(key=lambda b:b['t'])
    # After an old trading gap, use only the consecutive recent segment.
    gaps=[i+1 for i,(a,b) in enumerate(zip(rows,rows[1:])) if b['t']-a['t']!=unit]
    if gaps:rows=rows[gaps[-1]:]
    if len(rows)<31 or not 0<=now-(rows[-1]['t']+unit)<unit:
        raise ValueError('마감 봉 부족·지연·공백')
    return rows


def features(rows):
    c=[b['c'] for b in rows];last=c[-1]
    tr=[max(b['h']-b['l'],abs(b['h']-a['c']),abs(b['l']-a['c'])) for a,b in zip(rows,rows[1:])]
    atr=sum(tr[:14])/14
    for value in tr[14:]:atr=(atr*13+value)/14
    e20=ema(c,20);e50=ema(c,50)
    trend=25*sum([last>e20,e20>e50,last>e50,e50>ema(c[:-1],50)])
    before=sum(b['amount'] for b in rows[-14:-7]);recent=sum(b['amount'] for b in rows[-7:])
    mean=sum(b['amount'] for b in rows[-21:-1])/20
    return {'trend':trend,'return7':(last/c[-8]-1)*100,'return30':(last/c[-31]-1)*100,
            'volume_growth':(recent/before-1)*100 if before>0 else None,
            'volume_ratio':rows[-1]['amount']/mean if mean>0 else 0,
            'atr_pct':atr/last*100,'atr':atr,'history':len(rows),
            'drawdown30':(last/max(c[-30:])-1)*100,'close':last,
            'breakout':last>max(b['h'] for b in rows[-21:-1]),'closed_at':rows[-1]['t']}


def eligibility(meta,ticker,now):
    if not meta['market'].startswith('KRW-'):return '원화 마켓 아님'
    if meta['market'][4:] in STABLE:return '가격 고정 목적 자산'
    event=meta.get('market_event')
    if not isinstance(event,dict) or type(event.get('warning')) is not bool or not isinstance(event.get('caution'),dict):
        return '거래소 경보 확인 불가'
    if event['warning'] or any(v is True for v in event['caution'].values()):return '거래소 유의·주의 경보'
    if not ticker:return '현재가 없음'
    stamp=finite(ticker.get('trade_timestamp'),0)/1000
    if not 0<=now-stamp<=300:return '최근 거래 지연'
    if finite(ticker.get('acc_trade_price_24h'),0)<500_000_000:return '24시간 거래대금 5억 미만'
    if finite(ticker.get('trade_price'),0)<=0:return '가격 확인 불가'
    return ''


def score(row,bucket):
    f=row.get(bucket+'_features',row['daily'])
    daily=row['daily'];growth=daily['volume_growth']
    momentum=f['return30'] if bucket=='long' else f['return7']
    target={'long':2,'swing':1.5,'short':.5}[bucket]
    fund=row.get('fundamentals',{})
    onchain=fund.get('change30',fund.get('change7'))
    components={
      'liquidity':row['liquidity_score'],'trend':f['trend'],
      'momentum':clamp(50+momentum*2),'volume_growth':clamp(50+(growth or 0)*.4),
      'stability':clamp(100-abs(f['atr_pct']-target)*20),
      'history':clamp(daily['history']/180*100),
      'onchain':clamp(50+onchain) if onchain is not None else 0,
    }
    # Acute overextension is a penalty, never rewarded as growth potential.
    penalty=max(0,abs(row['day_change'])-10)*1.5
    if f['history']<50:penalty+=10
    result=sum(components[k]*w/100 for k,w in WEIGHTS[bucket].items())-penalty
    return round(clamp(result),2),{k:round(components[k],1) for k in WEIGHTS[bucket]}


class Recommender:
    def __init__(self,root,public=None,external=None):
        self.path=root/'recommendations.json'
        self.public=public or (lambda p,q=None:http('GET',p,q))
        self.external=external or public_json
        self.lock=threading.RLock();self.stop=threading.Event()
        self.refreshing=False;self.progress='첫 전체 종목 조사 대기';self.error=''
        self.next_check=0
        self.result={'version':VERSION,'time':0,'rankings':{'all':[],'long':[],'swing':[],'short':[]},'counts':{}}
        self.fund_cache={};self.fund_time=0;self.bar_cache={}
        if self.path.exists():
            try:
                data=json.loads(self.path.read_text(encoding='utf-8'))
                if data.get('version')==VERSION:self.result=data
            except (OSError,ValueError):self.error='이전 추천 캐시를 읽지 못해 다시 조사합니다.'

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.result | {'refreshing':self.refreshing,'progress':self.progress,'error':self.error,
                'stale':time.time()-self.result.get('time',0)>900,'interval':INTERVAL,'next_check':self.next_check,
                'weights':WEIGHTS,'labels':LABELS})

    def set_progress(self,text):
        with self.lock:self.progress=text

    def bars(self,market,unit):
        now=time.time();key=(market,unit);cached=self.bar_cache.get(key)
        # Reuse data until a new timeframe can close. Discard a cache after one period.
        if cached and now<cached['last_close']+unit*2:
            return cached['features']
        path='/v1/candles/days' if unit==86400 else '/v1/candles/minutes/'+str(unit//60)
        raw=self.public(path,{'market':market,'count':200})
        rows=closed_bars(raw,unit,now);f=features(rows)
        self.bar_cache[key]={'last_close':rows[-1]['t'],'features':f}
        return f

    def fundamentals(self,catalog):
        if time.time()-self.fund_time<INTERVAL:return self.fund_cache
        found={};errors=[]
        try:chains=self.external('/v2/chains')
        except Exception:chains=[];errors.append('체인')
        try:protocols=self.external('/protocols')
        except Exception:protocols=[];errors.append('프로토콜')
        chain_alias={'XRP':'XRP Ledger','AVAX':'Avalanche','POL':'Polygon','ATOM':'CosmosHub','DOT':'Polkadot'}
        for m,meta in catalog.items():
            sym=m[4:];name=meta.get('english_name','')
            chain=[c for c in chains if c.get('tokenSymbol')==sym and
                   norm(c.get('name')) in {norm(name),norm(chain_alias.get(sym,name))}]
            if len(chain)==1:
                c=chain[0]
                try:
                    history=self.external('/v2/historicalChainTvl/'+quote(c['name'],safe=''))
                    history=sorted([h for h in history if finite(h.get('tvl')) is not None and finite(h.get('date')) is not None],key=lambda h:h['date'])
                    end=history[-1]
                    if not 0<=time.time()-end['date']<=3*86400:raise ValueError('온체인 시계열 지연')
                    def change(days):
                        point=min(history,key=lambda h:abs(h['date']-(end['date']-days*86400)))
                        return (end['tvl']/point['tvl']-1)*100 if point['tvl']>0 and abs(point['date']-(end['date']-days*86400))<=2*86400 else None
                    found[m]={'kind':'체인 TVL','name':c['name'],'tvl':end['tvl'],'change7':change(7),
                              'change30':change(30),'data_time':end['date'],'retrieved':time.time(),
                              'source':'https://defillama.com/chain/'+quote(c['name'],safe='')}
                except Exception:pass
            if m not in found:
                matches=[p for p in protocols if p.get('symbol')==sym and norm(p.get('name'))==norm(name)
                         and finite(p.get('tvl'),0)>0]
                if len(matches)==1:
                    p=matches[0]
                    found[m]={'kind':'프로토콜 TVL','name':p['name'],'tvl':p['tvl'],'change7':finite(p.get('change_7d')),
                              'change30':finite(p.get('change_1m')),'data_time':None,'retrieved':time.time(),
                              'source':'https://defillama.com/protocol/'+quote(p['slug'],safe=''),
                              'description':str(p.get('description',''))[:220]}
        self.fund_cache=found
        # A total failure should be retried next scan, not treated as fresh research.
        if not errors:self.fund_time=time.time()
        return found

    def scan(self,pace=True):
        with self.lock:
            if self.refreshing:return
            self.refreshing=True;self.error=''
        try:
            self.set_progress('원화 전체 종목·거래소 경보·거래대금 확인')
            all_meta=self.public('/v1/market/all',{'is_details':'true'})
            catalog={m['market']:m for m in all_meta if m['market'].startswith('KRW-')}
            ticks={t['market']:t for t in self.public('/v1/ticker/all',{'quote_currencies':'KRW'})}
            rejected={};eligible=[];now=time.time()
            for m,meta in catalog.items():
                reason=eligibility(meta,ticks.get(m),now)
                if reason:rejected[reason]=rejected.get(reason,0)+1
                else:eligible.append(m)
            eligible.sort(key=lambda m:ticks[m]['acc_trade_price_24h'])
            self.set_progress('온체인 자금 규모·성장 자료 조사')
            funds=self.fundamentals(catalog)
            rows=[];failed=0
            for i,m in enumerate(eligible):
                if self.stop.is_set():return
                self.set_progress('전체 적격 종목 일봉 비교 '+str(i+1)+' / '+str(len(eligible)))
                try:
                    f=self.bars(m,86400);t=ticks[m]
                    row={'market':m,'name':catalog[m].get('korean_name',m),'price':t['trade_price'],
                         'turnover':t['acc_trade_price_24h'],'volume':t.get('acc_trade_volume_24h'),
                         'day_change':t['signed_change_rate']*100,'daily':f,
                         'liquidity_score':(i+1)/len(eligible)*100,'fundamentals':funds.get(m,{}),
                         'project':PROJECTS.get(m[4:],{}),'time':time.time()}
                    rows.append(row)
                except Exception:failed+=1
                if pace and self.stop.wait(.32):return
            # Every eligible market receives the daily screen. Deeper timeframe and
            # book checks are bounded to the top 20 candidates per profile.
            pools={b:sorted([r for r in rows if b!='long' or
                            (r['daily']['history']>=120 and (r['fundamentals'] or r['project'])
                             and not r['project'].get('exclude_long'))],
                            key=lambda r:score(r,b)[0],reverse=True)[:20] for b in WEIGHTS}
            for bucket,unit in (('swing',14400),('short',900)):
                for i,row in enumerate(pools[bucket]):
                    self.set_progress(('스윙' if bucket=='swing' else '단타')+' 후보 정밀 조사 '+str(i+1)+' / '+str(len(pools[bucket])))
                    try:row[bucket+'_features']=self.bars(row['market'],unit)
                    except Exception:row[bucket+'_unavailable']=True
                    if pace and self.stop.wait(.32):return
            candidates={r['market'] for pool in pools.values() for r in pool}
            books={}
            if candidates:
                for b in self.public('/v1/orderbook',{'markets':','.join(sorted(candidates)),'level':0}):
                    if 0<=time.time()-b.get('timestamp',0)/1000<=10 and b.get('orderbook_units'):
                        u=b['orderbook_units'][0]
                        books[b['market']]=(u['ask_price']/u['bid_price']-1)*100
            rankings={};by_market={}
            for bucket,pool in pools.items():
                usable=[]
                for row in pool:
                    spread=books.get(row['market'])
                    if row.get(bucket+'_unavailable') or spread is None or spread>.3:continue
                    if bucket=='long' and row['daily']['history']<120:continue
                    value,parts=score(row,bucket)
                    r=copy.deepcopy(row);f=r.get(bucket+'_features',r['daily'])
                    status='과열·추격 주의' if abs(r['day_change'])>10 else '상승 추세·진입 확인 대기' if f['trend']>=75 else '약세·전환 대기'
                    r.update(bucket=bucket,score=value,components=parts,spread=spread,status=status,
                        outlook=('추세와 거래대금이 유지되면 추가 관찰 우선' if f['trend']>=75 else '추세 회복 확인 전 관찰 우선'),
                        risks=[x for x in [
                            '실사용 성장 자료 미확인' if not r['fundamentals'] else 'TVL은 예치자금 지표이며 매출·토큰 수익을 뜻하지 않음',
                            '기초 사업·토큰 권리 미검토' if not r['project'] else r['project']['risk'],
                            '거래소 일봉 이력 120개 미만' if r['daily']['history']<120 else '',
                            '일중 급변·추격 위험' if abs(r['day_change'])>10 else '',
                            '호가 차이 0.1% 초과' if spread>.1 else ''] if x])
                    usable.append(r)
                usable.sort(key=lambda r:(-r['score'],r['market']))
                rankings[bucket]=usable[:10]
                for r in usable:
                    if r['market'] not in by_market or r['score']>by_market[r['market']]['score']:
                        by_market[r['market']]=r
            rankings['all']=sorted(by_market.values(),key=lambda r:(-r['score'],r['market']))[:10]
            result={'version':VERSION,'time':time.time(),'started':now,'rankings':rankings,
                    'counts':{'universe':len(catalog),'eligible':len(eligible),'daily_compared':len(rows),
                              'daily_failed':failed,'deep_candidates':len(candidates),'onchain':len(funds),'excluded':rejected}}
            atomic(self.path,result)
            with self.lock:self.result=result;self.progress='전체 조사 완료 · 다음 10분 주기에 갱신'
        except Exception:
            with self.lock:self.error='추천 자료 조회 실패 · 마지막 조사 결과를 확인하세요.'
        finally:
            with self.lock:self.refreshing=False

    def start(self,stop_event):
        self.stop=stop_event
        def loop():
            while not self.stop.is_set():
                started=time.monotonic()
                with self.lock:self.next_check=time.time()+INTERVAL
                self.scan()
                self.stop.wait(max(0,INTERVAL-(time.monotonic()-started)))
        threading.Thread(target=loop,daemon=True).start()
