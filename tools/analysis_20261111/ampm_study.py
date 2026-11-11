import json,urllib.request,datetime as d,time,statistics as st
K=d.timezone(d.timedelta(hours=9))
D=r'C:\Users\User\Desktop\코인 트레이딩\v5-app\v5\data'
fills=[]
for l in open(D+r'\exchange_orders.jsonl',encoding='utf-8'):
    r=json.loads(l)
    if not str(r.get('identifier') or '').startswith('v5-') or float(r.get('executed_volume') or 0)<=0: continue
    fills.append((d.datetime.fromisoformat(r['created_at']),r['market'],r['side'],float(r['executed_volume']),float(r['executed_funds']),float(r['paid_fee'])))
fills.sort();cur={};trips=[]
for t,m,s,v,f,fee in fills:
    x=cur.get(m)
    if s=='bid':
        if x is None: x=cur[m]={'m':m,'open':t,'q':0,'bq':0,'cost':0,'sq':0,'sf':0}
        x['q']+=v;x['bq']+=v;x['cost']+=f+fee
    else:
        if x is None: continue
        x['q']-=v;x['sq']+=v;x['sf']+=f-fee;x['close']=t
        if x['q']<=1e-8 or x['q']*(x['cost']/x['bq'])<5000:
            c=x['cost']*x['sq']/x['bq'];x['pct']=(x['sf']-c)/c;trips.append(x);cur.pop(m)
reg={}
for l in open(D+r'\regime_watch.jsonl',encoding='utf-8'):
    r=json.loads(l);reg[r['at'][:13]]=r
def get(m,to,n):
    u=f'https://api.upbit.com/v1/candles/minutes/60?market={m}&count={n}&to={to:%Y-%m-%dT%H:%M:%S}%2B09:00'
    for k in range(5):
        try: time.sleep(0.13);return json.load(urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'research-client'}),timeout=10))[::-1]
        except Exception: time.sleep(1+k)
rows=[]
for x in trips:
    t0=x['open'].astimezone(K);h=t0.strftime('%Y-%m-%dT%H')
    g=reg.get(h)
    if not g: continue
    b=get(x['m'],t0.replace(minute=0,second=0,microsecond=0),25)
    if not b or len(b)<25: continue
    px=b[-1]['trade_price'];hi=max(c['high_price'] for c in b)
    rows.append({'m':x['m'],'t':h,'hr':t0.hour,'pct':x['pct'],'up':g['up']/g['total'],'i6':g.get('index_6h'),'i4':g['index_4h'],'i24':g['index_24h'],
                 'r4':px/b[-4]['opening_price']-1,'r24':px/b[0]['opening_price']-1,'dd':px/hi-1})
json.dump(rows,open(__import__('tempfile').gettempdir()+'/ampm_rows.json','w'),default=str)
print(len(rows),'건, 평균',f"{st.mean(r['pct'] for r in rows):+.2%}")
def grp(name,f):
    a=[r['pct'] for r in rows if f(r)];b=[r['pct'] for r in rows if not f(r)]
    s=lambda z:f"{len(z):3}건 평균{st.mean(z):+.2%} 승{sum(v>0 for v in z)/len(z):.0%} 합{sum(z)*70000:+,.0f}" if z else '0'
    print(f'{name:28} 예: {s(a)} | 아니오: {s(b)}')
grp('상승종목 비율 ≤30%',lambda r:r['up']<=0.30)
grp('상승종목 비율 ≤35%',lambda r:r['up']<=0.35)
grp('시장 6h 지수 <0',lambda r:r['i6'] is not None and r['i6']<0)
grp("시장 4h 지수 <0",lambda r:(r["i4"] or 0)<0)
grp('시장 24h 지수 <0',lambda r:r['i24']<0)
grp('종목 24h 고점대비 ≤-12%',lambda r:r['dd']<=-0.12)
grp('종목 직전4h ≤-5%',lambda r:r['r4']<=-0.05)
grp('종목 직전24h ≥+10%',lambda r:r['r24']>=0.10)
grp('KST 0~9시 매수',lambda r:r['hr']<9)
grp('KST 9~15시 매수',lambda r:9<=r['hr']<15)
grp('KST 15~24시 매수',lambda r:r['hr']>=15)
grp('6h<0 그리고 상승비율≤30%',lambda r:r['i6'] is not None and r['i6']<0 and r['up']<=0.30)
