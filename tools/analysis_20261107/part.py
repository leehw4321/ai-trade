import statistics as st
from sim import *

def atr_pct(e):
    pre, post = path(e)
    seg = pre[-120:] if len(pre) >= 30 else pre
    if len(seg) < 15: return 0.01
    rs = []
    for i in range(0, len(seg) - 14, 15):
        b = [c for _, c in seg[i:i + 15]]
        rs.append((max(c[1] for c in b) - min(c[2] for c in b)) / b[-1][3])
    return max(0.004, st.mean(rs))

def psim(e, stop=0.045, tr=((0.02, 0.5),), be_after_first=True, trail_pct=None, trail_atr=None, trail_arm=0.0,
         step=False, tmax=720, slip=0.003, lim_slip=0.001, floor_c=0.001):
    """분할매도: tr = ((상승률, 비율), ...) 지정가 분할. 첫 분할 뒤 손절선을 본전으로.
    남은 물량: 고점 대비 trail_pct 또는 trail_atr×ATR 하락 시 시장가 매도. step=True면 분할 때마다 손절선을 직전 분할가로 올림."""
    pre, post = path(e)
    ep = e['price']
    w = trail_pct if trail_pct else (trail_atr * atr_pct(e) if trail_atr else None)
    rem = 1.0; got = 0.0
    stp = ep * (1 - stop); peak = ep
    done = [False] * len(tr)
    floor = ep * (1 + 2 * FEE + floor_c)
    for k, (o, h, l, c) in post[1:tmax + 1]:
        if l <= stp:
            fill = min(stp, o) * (1 - slip)
            got += rem * fill / ep; rem = 0; break
        for i, (t, f) in enumerate(tr):
            if not done[i] and h >= ep * (1 + t) and rem > 0:
                q = min(rem, f)
                got += q * ep * (1 + t) * (1 - lim_slip) / ep; rem -= q; done[i] = True
                if be_after_first: stp = max(stp, floor)
                if step: stp = max(stp, ep * (1 + t) * 0.995 if i > 0 else floor)
        if rem <= 1e-9: break
        peak = max(peak, h)
        if w and peak >= ep * (1 + trail_arm):
            stp = max(stp, peak * (1 - w))
    if rem > 1e-9:
        last = post[min(len(post) - 1, tmax)][1][3]
        got += rem * last * (1 - slip) / ep
    # 수수료: 총 수량에 대해 왕복 (분할이어도 수량 합계 동일)
    return got - 1 - 2 * FEE

def rep(name, fn):
    a = [fn(e) for e in ents if e['t'] < '2026-11-05']; b = [fn(e) for e in ents if e['t'] >= '2026-11-05']
    z = [fn(e) for e in ents if e['t'] >= '2026-11-06T18:00']
    al = a + b
    w = [x for x in al if x > 0]; l = [x for x in al if x <= 0]
    print(f"{name:40s} 전체 {sum(al)*80000:+7.0f}원 ({st.mean(al)*100:+.2f}%) | 전반 {sum(a)*80000:+6.0f} 후반 {sum(b)*80000:+6.0f} | 어제18시~ {sum(z)*80000:+6.0f} | 승률 {len(w)/len(al)*100:.0f}% 평균이익 {st.mean(w)*100:+.2f}% 평균손실 {st.mean(l)*100:+.2f}%")

if __name__ == '__main__':
    print('※ 매수 86건, 건당 8만원 환산, 미끄러짐 시장가 0.3% / 지정가 분할 0.1%')
    rep('0 현행(잠금1.5%+보호3%/30%) 전량', lambda e: sim(e, slip=0.003)[0])
    rep('0b 보호만(잠금 없음) 전량', lambda e: sim(e, slip=0.003, lock=None)[0])
    rep('0c +3% 전량 익절', lambda e: sim(e, slip=0.003, lock=None, arm=None, tp=0.03)[0])
    print('--- 분할 후 나머지는 고점 대비 일정 % 하락 시 매도 ---')
    rep('1 +2%에 1/2, 나머지 고점-2%(+2%부터)', lambda e: psim(e, tr=((0.02, .5),), trail_pct=0.02, trail_arm=0.02))
    rep('2 +2%에 1/2, 나머지 고점-3%(+2%부터)', lambda e: psim(e, tr=((0.02, .5),), trail_pct=0.03, trail_arm=0.02))
    rep('3 +3%에 1/2, 나머지 고점-3%', lambda e: psim(e, tr=((0.03, .5),), trail_pct=0.03, trail_arm=0.03))
    rep('4 +2%·+4% 각 1/3, 나머지 고점-3%', lambda e: psim(e, tr=((0.02, 1/3), (0.04, 1/3)), trail_pct=0.03, trail_arm=0.02))
    rep('5 +1.5%에 1/3, +3%에 1/3, 나머지 고점-3%', lambda e: psim(e, tr=((0.015, 1/3), (0.03, 1/3)), trail_pct=0.03, trail_arm=0.015))
    rep('6 +3%에 1/2, 나머지 4시간 보유', lambda e: psim(e, tr=((0.03, .5),), tmax=240))
    rep('7 +2%에 1/2, 나머지 본전 보호+4시간', lambda e: psim(e, tr=((0.02, .5),), tmax=240))
    print('--- 고점 기준을 변동성(ATR)으로 ---')
    for k in (1.0, 1.5, 2.0, 3.0):
        rep(f'8 +2%에 1/2, 나머지 고점-{k}×ATR', lambda e, k=k: psim(e, tr=((0.02, .5),), trail_atr=k, trail_arm=0.02))
    for k in (2.0, 3.0):
        rep(f'9 +2%·+4% 1/3씩, 나머지 고점-{k}×ATR', lambda e, k=k: psim(e, tr=((0.02, 1/3), (0.04, 1/3)), trail_atr=k, trail_arm=0.02))
    print('--- 계단식 손절 이동(분할 때마다 손절선 상향) ---')
    rep('10 +2%·+4% 1/3씩, 손절선 계단, 나머지 4h', lambda e: psim(e, tr=((0.02, 1/3), (0.04, 1/3)), step=True, tmax=240))
    rep('11 +2%·+4% 1/3씩, 계단+고점-3%', lambda e: psim(e, tr=((0.02, 1/3), (0.04, 1/3)), step=True, trail_pct=0.03, trail_arm=0.02))
    print('--- 분할 없이 고점 기준만(비교) ---')
    for tp_ in (0.02, 0.03):
        rep(f'12 분할 없이 고점-{tp_*100:.0f}% (+{tp_*100:.0f}%부터)', lambda e, tp_=tp_: psim(e, tr=(), be_after_first=False, trail_pct=tp_, trail_arm=tp_))
    rep('13 분할 없이 고점-1.5×ATR (+2%부터)', lambda e: psim(e, tr=(), be_after_first=False, trail_atr=1.5, trail_arm=0.02))
    print('--- 최대 상승(MFE) 분포: 어디서 팔아야 하나 (4h) ---')
    mf = []
    for e in ents:
        pre, post = path(e); seg = post[1:241]
        if seg: mf.append(max(c[1] for _, c in seg) / e['price'] - 1)
    for t in (0.01, 0.015, 0.02, 0.03, 0.04, 0.05, 0.07):
        print(f'  +{t*100:.1f}% 이상 닿은 비율 {sum(x>=t for x in mf)/len(mf)*100:.0f}%')
