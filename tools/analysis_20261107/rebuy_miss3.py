import datetime as dt, statistics as st
from sim import *
from part import atr_pct
T=lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=None)
G={'눌림 없음(규칙이 막음)':[], '눌림 있음(규칙 통과)':[]}
for e in ents:
    if e.get('closed') is None or e.get('actual') is None or e['actual']<0: continue
    c=C[e['key']]; ks=sorted(c); tc=T(e['closed']).replace(second=0,microsecond=0)
    post=[k for k in ks if T(k)>tc]
    if len(post)<300: continue
    sp=e['sold_funds']/e['sold_vol']; A=atr_pct(e)
    k0=post[240]; o=c[k0][0]
    # 4h 시점 현재가가 매도가-1ATR 이하면 통과, 아니면 막힘(그 뒤 눌림이 오면 그때 통과이므로 '구간 중 눌림' 기준도 따로)
    cur_ok = o<=sp*(1-A)
    ever = any(c[k][2]<=sp*(1-A) for k in post[240:])
    fake=dict(e); fake['t']=k0+'+09:00'; fake['price']=o
    r=sim(fake,slip=0.003)[0]
    G['눌림 있음(규칙 통과)' if ever else '눌림 없음(규칙이 막음)'].append(r)
for g,v in G.items():
    print(f'{g}: {len(v)}건 평균 {st.mean(v)*100:+.2f}% 합 {sum(v)*80000:+.0f}원 승률 {sum(x>0 for x in v)/len(v)*100:.0f}%')
allv=sum(G.values(),[]); print(f'4시간 뒤 무조건 재매수(규칙 없음): {len(allv)}건 평균 {st.mean(allv)*100:+.2f}% 합 {sum(allv)*80000:+.0f}원')
