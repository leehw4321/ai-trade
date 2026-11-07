import json, datetime as dt, statistics as st
from sim import *
from part import atr_pct
T=lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=None)
rows=[]
for e in ents:
    if e.get('closed') is None or e.get('actual') is None or e['actual']<0: continue
    c=C[e['key']]; ks=sorted(c); tc=T(e['closed']).replace(second=0,microsecond=0)
    post=[(k,c[k]) for k in ks if T(k)>tc]
    sp=e['sold_funds']/e['sold_vol']
    A=atr_pct(e)
    rows.append((e,post,sp,A,tc))
print('이익 청산', len(rows),'건')
cov=[len(p)/60 for _,p,_,_,_ in rows]
print('청산 뒤 캔들 확보 시간(h): 중앙값 %.1f, 4h이상 %d건, 12h이상 %d건'%(st.median(cov),sum(x>=4 for x in cov),sum(x>=12 for x in cov)))
# 재진입 실제 기록과 새 규칙 판정
byM={}
for e in ents: byM.setdefault(e['market'],[]).append(e)
print('\n[A] 실제로 이익 청산 뒤 재매수했던 건을 새 규칙(4h금지+1ATR 눌림, 24h 후 해제)에 대보면')
tot_blocked=0;tot_pass=0;nb=0;np_=0
for m,es in byM.items():
    es.sort(key=lambda e:e['t'])
    for a,b in zip(es,es[1:]):
        if a.get('closed') is None or a['actual']<0: continue
        gap=(T(b['t'])-T(a['closed'])).total_seconds()/3600
        if gap>=24: continue
        sp=a['sold_funds']/a['sold_vol']; A=atr_pct(a)
        dip=(sp-b['price'])/sp
        ok = gap>=4 and dip>=A
        r=sim(b,slip=0.003)[0]*80000
        if ok: tot_pass+=r;np_+=1
        else: tot_blocked+=r;nb+=1
print(f'  막혔을 재매수 {nb}건 실제 결과 합 {tot_blocked:+.0f}원 / 통과했을 재매수 {np_}건 합 {tot_pass:+.0f}원')
print('\n[B] 청산 뒤 가격 흐름 (캔들 4h 이상 확보된 건만)')
n=0;rise=0;dipfirst=0;neither=0;ups=[];dips=[]
for e,post,sp,A,tc in rows:
    if len(post)<240: continue
    n+=1
    w=post[:1440]
    hi=max(v[1] for k,v in w); lo=min(v[2] for k,v in w)
    up=hi/sp-1; dn=1-lo/sp
    # 1ATR 눌림이 4시간 후 구간(240분~)에서 나왔는지
    hit=None
    for i,(k,v) in enumerate(w):
        if i>=240 and v[2]<=sp*(1-A): hit=i;break
    ups.append(up);dips.append(dn)
    tag='눌림 옴' if hit is not None else '눌림 없음'
    if hit is None and up>=A: rise+=1
    print(f"  {e['market'][4:]:9s} 청산 {e['closed'][5:16]} 실제 {e['actual']*100:+.1f}% | 이후 최대상승 {up*100:+5.1f}% 최대하락 {dn*100:4.1f}% ATR {A*100:.1f}% | 4h후 {tag}{'' if hit is None else f' ({hit/60:.1f}h)'} | 확보 {len(w)/60:.0f}h")
print(f'  표본 {n}건 / 눌림 없이 1ATR 이상 더 오른 건 {rise}건 / 최대상승 중앙값 {st.median(ups)*100:+.1f}%')
