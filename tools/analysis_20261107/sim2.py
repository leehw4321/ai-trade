import json, datetime as dt, statistics as st
from sim import *

T = lambda s: dt.datetime.fromisoformat(s)
# ---- 1) 매수 자체의 우위: 매수 뒤 N분 종가 수익률, 최대 상승/하락
def fwd(e, mins):
    pre, post = path(e)
    if len(post) <= mins: return None
    return post[mins][1][3] / e['price'] - 1
print('=== 매수 뒤 가격 흐름 (비용 전, 진입 86건) ===')
for mins in (15, 30, 60, 120, 240, 480):
    xs = [x for x in (fwd(e, mins) for e in ents) if x is not None]
    print(f'{mins:4d}분 뒤: 평균 {st.mean(xs)*100:+.2f}%  중앙값 {st.median(xs)*100:+.2f}%  오른 비율 {sum(x>0 for x in xs)/len(xs)*100:.0f}%  (n={len(xs)})')
mfe = []; mae = []
for e in ents:
    pre, post = path(e)
    seg = post[1:241]
    if not seg: continue
    mfe.append(max(c[1] for _, c in seg) / e['price'] - 1)
    mae.append(min(c[2] for _, c in seg) / e['price'] - 1)
print(f'4시간 안 최대 상승(MFE) 중앙값 {st.median(mfe)*100:+.2f}% / 최대 하락(MAE) 중앙값 {st.median(mae)*100:+.2f}%')
print(f'  MFE>=1.5%: {sum(x>=0.015 for x in mfe)}건, >=3%: {sum(x>=0.03 for x in mfe)}건, >=5%: {sum(x>=0.05 for x in mfe)}건 / 86')
print(f'  MAE<=-3%: {sum(x<=-0.03 for x in mae)}건, <=-4.5%: {sum(x<=-0.045 for x in mae)}건')
# 비교 기준: 같은 종목을 아무 때나 샀다면 (매수 시각을 +1시간, +2시간 어긋나게 한 가상 진입)
print('=== 비교: 같은 종목을 1~3시간 전에 샀다면(무작위 기준) 4시간 뒤 평균 ===')
for sh in (-60, -120, -180):
    xs = []
    for e in ents:
        pre, post = path(e)
        allc = pre + post
        idx = len(pre) + sh   # pre 길이 120, 사실상 sh분 전
        if idx < 0 or idx + 240 >= len(allc): continue
        xs.append(allc[idx + 240][1][3] / allc[idx][1][3] - 1)
    if xs: print(f'  진입 {-sh}분 전: 4시간 뒤 평균 {st.mean(xs)*100:+.2f}% (n={len(xs)})')

# ---- 2) 매수 직전 상태: 직전 2시간 고점 대비 위치 / 직전 1시간 상승률
print('=== 매수 시점 위치와 이후 4시간 성과 ===')
buckets = {}
for e in ents:
    pre, post = path(e)
    if len(pre) < 60 or len(post) < 241: continue
    hi = max(c[1] for _, c in pre[-120:]); p = e['price']
    run = p / pre[-60][1][3] - 1       # 직전 1시간 상승률
    f4 = post[240][1][3] / p - 1
    b = ('직전2h고점의 95%미만' if p / hi < 0.95 else ('95~99%' if p / hi < 0.99 else '99%이상(고점부근)'))
    buckets.setdefault(b, []).append(f4)
    b2 = '직전1h 상승 +2%이상' if run >= 0.02 else ('직전1h -0.5~+2%' if run > -0.005 else '직전1h 하락')
    buckets.setdefault(b2, []).append(f4)
for k, v in sorted(buckets.items()):
    print(f'  {k:22s} n={len(v):3d} 4h뒤 평균 {st.mean(v)*100:+.2f}% 오른비율 {sum(x>0 for x in v)/len(v)*100:.0f}%')

# ---- 3) 팔고 나서 다시 사는 경향
print('=== 매도 뒤 같은 종목 재매수 ===')
sells = E['sells']
bysell = {}
for s in sells: bysell.setdefault(s['market'], []).append(s)
rec = []
for e in ents:
    prev = [s for s in bysell.get(e['market'], []) if T(s['t']) < T(e['t'])]
    if not prev: e['prev'] = None; continue
    s = prev[-1]
    gap = (T(e['t']) - T(s['t'])).total_seconds() / 60
    e['prev'] = dict(gap=gap, price=s['price'], reason=s['reason'], rebuy_vs_sell=e['price'] / s['price'] - 1)
def cls(e):
    p = e['prev']
    if not p or p['gap'] > 24 * 60: return '재진입 아님(24h 넘음)'
    loss = p['reason'] == '손절' or (p['rebuy_vs_sell'] is not None and False)
    return '손절 뒤 재진입' if p['reason'] == '손절' else ('이익보호 뒤 재진입' if p['reason'] == '이익보호' else '기타 매도 뒤')
res = {}
for e in ents:
    k = cls(e)
    r = sim(e, slip=0.003)[0]
    f4 = fwd(e, 240)
    res.setdefault(k, []).append((r, f4, e))
for k, v in res.items():
    rs = [x[0] for x in v]; fs = [x[1] for x in v if x[1] is not None]
    print(f'  {k:22s} n={len(v):3d} 현행규칙 평균 {st.mean(rs)*100:+.2f}% 합계 {sum(rs)*80000:+7.0f}원 | 4h뒤 평균 {st.mean(fs)*100 if fs else 0:+.2f}%')
print('--- 재진입(24h 안) 상세: 간격·매도가 대비 재매수가 ---')
for k in ('손절 뒤 재진입', '이익보호 뒤 재진입', '기타 매도 뒤'):
    for r, f4, e in res.get(k, []):
        p = e['prev']
        print(f"  {k[:6]} {e['market'][4:]:9s} 매도 {p['reason'] or '?':6s} 간격 {p['gap']:5.0f}분 재매수가가 매도가 대비 {p['rebuy_vs_sell']*100:+.1f}% → 결과(현행) {r*100:+.2f}%")
# 손절/손실 직후 (60분 안) 재매수 vs 아닌 것
quick = [r for k in res for r, f4, e in res[k] if e['prev'] and e['prev']['gap'] <= 60]
print(f'60분 안 재매수 {len(quick)}건 평균 {st.mean(quick)*100 if quick else 0:+.2f}%')

# ---- 4) 후반/전반 안정성 (보정 slip 0.3%)
print('=== 안정성: 전반(11-02~04) vs 후반(11-05~07) , slip 0.3% ===')
h1 = [e for e in ents if e['t'] < '2026-11-05']; h2 = [e for e in ents if e['t'] >= '2026-11-05']
for nm, fn in (('현행', lambda e: sim(e, slip=0.003)),
               ('잠금·보호 없음', lambda e: sim(e, slip=0.003, lock=None, arm=None)),
               ('보호만(잠금 없음)', lambda e: sim(e, slip=0.003, lock=None)),
               ('잠금 +2.5%', lambda e: sim(e, slip=0.003, lock=0.025)),
               ('손절 3%', lambda e: sim(e, slip=0.003, stop=0.03)),
               ('손절 6%', lambda e: sim(e, slip=0.003, stop=0.06)),
               ('고정익절 +3%', lambda e: sim(e, slip=0.003, lock=None, arm=None, tp=0.03)),
               ('고정익절 +2%', lambda e: sim(e, slip=0.003, lock=None, arm=None, tp=0.02)),
               ('시간 4h', lambda e: sim(e, slip=0.003, lock=None, arm=None, tmax=240))):
    a = [fn(e)[0] for e in h1]; b = [fn(e)[0] for e in h2]
    print(f'  {nm:16s} 전반(n={len(a)}) {st.mean(a)*100:+.2f}% 합 {sum(a)*80000:+6.0f}원 | 후반(n={len(b)}) {st.mean(b)*100:+.2f}% 합 {sum(b)*80000:+6.0f}원')
# ---- 5) 오늘(어제 18시 이후) 9~11건만
print('=== 어제 18시 이후만 (사용자가 본 구간) slip 0.3% ===')
rec_ = [e for e in ents if e['t'] >= '2026-11-06T18:00']
print('n=', len(rec_))
for nm, fn in (('현행', lambda e: sim(e, slip=0.003)),
               ('A 잠금 +2.5%', lambda e: sim(e, slip=0.003, lock=0.025)),
               ('A2 잠금2.5·보호+4%', lambda e: sim(e, slip=0.003, lock=0.025, arm=0.04)),
               ('B 손절3%', lambda e: sim(e, slip=0.003, stop=0.03)),
               ('B 손절6%', lambda e: sim(e, slip=0.003, stop=0.06)),
               ('잠금·보호 없음', lambda e: sim(e, slip=0.003, lock=None, arm=None)),
               ('보호만', lambda e: sim(e, slip=0.003, lock=None)),
               ('고정익절 +3%', lambda e: sim(e, slip=0.003, lock=None, arm=None, tp=0.03))):
    a = [fn(e) for e in rec_]
    print(f'  {nm:18s} 합 {sum(x[0] for x in a)*80000:+7.0f}원  ' + ' '.join(f"{e['market'][4:8]}{x[0]*100:+.1f}" for e, x in zip(rec_, a)))
