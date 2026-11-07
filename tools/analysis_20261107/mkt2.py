import json, statistics as st, datetime as dt, os
from sim import *
from r2 import r2sim
S = os.path.dirname(os.path.abspath(__file__))
M = json.load(open(S + r'\mkt.json'))
allh = sorted(set(h for v in M.values() for h in v))
idx = {}; lvl = 100.0; br = {}
for a, b in zip(allh, allh[1:]):
    rs = [M[m][b] / M[m][a] - 1 for m in M if m != 'KRW-BTC' and a in M[m] and b in M[m]]
    if len(rs) < 20: continue
    lvl *= 1 + st.mean(rs); idx[b] = lvl; br[b] = sum(r > 0 for r in rs) / len(rs)
hs = sorted(idx)
print('지수 구간', hs[0], '~', hs[-1], len(hs))
def ixat(t):   # 진입 시각 직전 정시 지수
    k = t.replace(minute=0, second=0, microsecond=0).strftime('%Y-%m-%dT%H:%M:%S')
    return k if k in idx else None
def back(k, n):
    i = hs.index(k); return idx[hs[i]] / idx[hs[i - n]] - 1 if i - n >= 0 else None
def fwdI(k, n):
    i = hs.index(k); return idx[hs[i + n]] / idx[hs[i]] - 1 if i + n < len(hs) else None
rows = []
for e in ents:
    t = dt.datetime.fromisoformat(e['t']).replace(tzinfo=None)
    k = ixat(t)
    r = sim(e, slip=0.003, lock=None)[0]
    cur = r2sim(e, T=0.05)
    pre, post = path(e)
    f4 = post[240][1][3] / e['price'] - 1 if len(post) > 240 else None
    rows.append(dict(e=e, t=t, day=e['t'][:10], r=r, k=k, ix6=back(k, 6) if k and k in hs else None, ixf4=fwdI(k, 4) if k and k in hs else None, f4=f4,
                     act=e.get('actual')))
print('=== 날짜별 성과 (현행 규칙 시뮬: 잠금 끔 / 실제 기록) ===')
for d in sorted(set(x['day'] for x in rows)):
    xs = [x for x in rows if x['day'] == d]
    rs = [x['r'] for x in xs]; ac = [x['act'] for x in xs if x['act'] is not None]
    f4 = [x['f4'] for x in xs if x['f4'] is not None]
    ixf = [x['ixf4'] for x in xs if x['ixf4'] is not None]
    print(f"{d} 진입 {len(xs):2d}건 | 시뮬 승률 {sum(r>0 for r in rs)/len(rs)*100:3.0f}% 합 {sum(rs)*80000:+6.0f}원 | 실제 승률 {sum(a>0 for a in ac)/max(1,len(ac))*100:3.0f}% 합 {sum(ac)*80000:+6.0f}원 | 매수 4h뒤 평균 {st.mean(f4)*100 if f4 else float('nan'):+.2f}% (같은 시각 시장 {st.mean(ixf)*100 if ixf else float('nan'):+.2f}%)")
print('=== 매수 시점 시장 상태별 (직전 6시간 균등지수 변화) ===')
for nm, f in (('시장 6h 하락(<-0.3%)', lambda v: v < -0.003), ('시장 6h 횡보(-0.3~+0.3%)', lambda v: -0.003 <= v <= 0.003), ('시장 6h 상승(>+0.3%)', lambda v: v > 0.003)):
    xs = [x for x in rows if x['ix6'] is not None and f(x['ix6'])]
    if not xs: continue
    rs = [x['r'] for x in xs]; f4 = [x['f4'] for x in xs if x['f4'] is not None]
    print(f"{nm:26s} n={len(xs):2d} 승률 {sum(r>0 for r in rs)/len(rs)*100:3.0f}% 합 {sum(rs)*80000:+6.0f}원 평균 {st.mean(rs)*100:+.2f}% | 매수 4h뒤 평균 {st.mean(f4)*100 if f4 else 0:+.2f}%")
print('=== 알파: 매수 4시간 뒤 수익률 - 같은 시각 시장 4시간 수익률 ===')
al = [(x['f4'] - x['ixf4']) for x in rows if x['f4'] is not None and x['ixf4'] is not None]
f4s = [x['f4'] for x in rows if x['f4'] is not None and x['ixf4'] is not None]; ixs = [x['ixf4'] for x in rows if x['f4'] is not None and x['ixf4'] is not None]
print(f'n={len(al)} 매수 평균 {st.mean(f4s)*100:+.2f}% / 시장 평균 {st.mean(ixs)*100:+.2f}% / 알파 평균 {st.mean(al)*100:+.2f}% (중앙값 {st.median(al)*100:+.2f}%) 알파>0 비율 {sum(a>0 for a in al)/len(al)*100:.0f}%')
# 시장과 개별 종목 결과의 상관
import math
def corr(a, b):
    ma, mb = st.mean(a), st.mean(b);
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
print(f'매수 4h 수익률과 시장 4h 수익률 상관계수 {corr(f4s, ixs):+.2f}')
print('=== 오늘(11-07) 거래별 ===')
for x in rows:
    if x['day'] >= '2026-11-06' and x['e']['t'] >= '2026-11-06T18':
        e = x['e']
        print(f"{e['market'][4:]:9s} {e['t'][5:16]} 시장6h전 {x['ix6']*100 if x['ix6'] is not None else float('nan'):+.2f}% 시장4h후 {x['ixf4']*100 if x['ixf4'] is not None else float('nan'):+.2f}% | 종목4h후 {x['f4']*100 if x['f4'] is not None else float('nan'):+.2f}% | 결과(시뮬) {x['r']*100:+.2f}% 실제 {('%+.2f%%' % (x['act']*100)) if x['act'] is not None else '보유중'}")
