"""손실·이익 거래의 매수 직전 하락폭 분포 (읽기 전용). 실거래 v5- 왕복 전체, 업비트 60분봉."""
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
            c=x['cost']*x['sq']/x['bq'];x['pnl']=x['sf']-c;x['pct']=x['pnl']/c;x['entry']=x['cost']/x['bq'];trips.append(x);cur.pop(m)
def get(m,to,n):
    u=f'https://api.upbit.com/v1/candles/minutes/60?market={m}&count={n}&to={to:%Y-%m-%dT%H:%M:%S}%2B09:00'
    for k in range(5):
        try: time.sleep(0.13);return json.load(urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'research-client'}),timeout=10))[::-1]
        except Exception: time.sleep(1+k)
rows=[]
for x in trips:
    t0=x['open'].astimezone(K)
    b=get(x['m'],t0.replace(minute=0,second=0,microsecond=0),25)
    if not b or len(b)<25: continue
    px=b[-1]['trade_price']
    rows.append({'m':x['m'][4:],'t':t0.strftime('%m-%d %H:%M'),'pct':x['pct'],'pnl':x['pnl'],
        'r1':px/b[-1]['opening_price']-1,'r2':px/b[-2]['opening_price']-1,'r4':px/b[-4]['opening_price']-1,
        'r24':px/b[0]['opening_price']-1,'dd':px/max(c['high_price'] for c in b)-1,
        'dd4':px/max(c['high_price'] for c in b[-4:])-1})
json.dump(rows,open('drop4h_rows.json','w'),ensure_ascii=False)
print(len(trips),'왕복 중 분봉 확인',len(rows),'건 · 기간',min(r['t'] for r in rows),'~',max(r['t'] for r in rows))
