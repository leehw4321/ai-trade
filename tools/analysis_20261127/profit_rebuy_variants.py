"""이익보호 매도 뒤 변형 비교(넓은 청산 재매수 NOW_W/A_W/B_W, 절반을 -3.5%까지 버티기 C3/C4). profit_rebuy_sim.py와 같은 자료·비용."""
import sys, json, statistics as stx, datetime as dt
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import profit_rebuy_sim as S
COST=S.COST
def wide(path,i,n,a60):
    """재매수 뒤 넓은 청산: 손절 -3.5%, +3% 넘으면 최고가-3×ATR(1h) 추적(본전 아래로 안 내림), 72h"""
    e=path[i][1]*(1+COST); stop=e*(1-S.STOP); peak=e; armed=False
    end=min(len(path),i+288)
    for j in range(i,end):
        t,o,h,l,c=path[j]
        line=max(stop, (max(e,peak-3*a60) if armed else stop))
        if l<=line: px=min(line,o); break
        peak=max(peak,h); armed=armed or peak>=e*1.03
    else: px=path[end-1][4]
    r=px*(1-COST)/e-1; return n*r,r
def half(path,sp,a,qty,floor):
    peak=sp; end=min(len(path),288); px=None
    for i in range(end):
        t,o,h,l,c=path[i]; line=max(floor,peak-3*a)
        if l<=line: px=min(line,o); break
        peak=max(peak,h)
    if px is None: px=path[end-1][4]
    return qty/2*(px-sp)*(1-COST), px
rs=S.reasons(); ev=[s for s in S.sells() if rs.get(s['id'],'').startswith(('이익보호','계단 보호')) and s['price']>s['avg']]
out={k:[] for k in ('NOW_W','A_W','B_W','C3','C4')}; worst=[]
for s in ev:
    t0=s['time']; bars=S.fetch(s['market'],t0-dt.timedelta(hours=30),t0+dt.timedelta(hours=97))
    cut=int(t0.timestamp())//900*900; prior=[b for b in bars if b[0]<cut]; path=[b for b in bars if b[0]>cut]
    if len(prior)<60 or len(path)<8: continue
    a15=S.atr(prior); a60=S.atr(S.hourly(prior[-(len(prior)//4)*4:])); sp=s['price']; n=sp*s['qty']
    for m in ('NOW','A','B'):
        k=S.rebuy_trigger(m,path,sp,a15)
        if k is not None and k<len(path): out[m+'_W'].append(wide(path,k,n,a60))
    c3=half(path,sp,a60,s['qty'],sp*(1-S.STOP)); out['C3'].append((c3[0],c3[1]/sp-1))
    c4=half(path,sp,a60*1.5,s['qty'],sp*(1-S.STOP)); out['C4'].append((c4[0],c4[1]/sp-1))
    c2=half(path,sp,a60,s['qty'],s['avg']); 
    if c2[1]/sp-1<-0.08: worst.append((t0.astimezone(S.KST).strftime('%m-%d %H:%M'),s['market'],sp,s['avg'],c2[1],[p for p in path[:6]]))
for k,v in out.items():
    r=[x[1] for x in v]; print(f"{k:6} {len(v):3}건 승{sum(x>0 for x in r):3} 합계 {sum(x[0] for x in v):10,.0f}원 평균 {stx.mean(r):+.2%} 중앙 {stx.median(r):+.2%} 최고 {max(r):+.1%} 최악 {min(r):+.1%}")
for w in worst: print(w[:5]); print(w[5])
