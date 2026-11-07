import statistics as st
from sim import *
from part import atr_pct

def r2sim(e, T=None, partial=True, trail=True, giveback=True, stop=0.045, arm=0.03, give=0.30, slip=0.003, lim_slip=0.001, ret_info=False):
    """T=2R 기준 수익률(None이면 종목별 6×ATR15). 이익보호(+3%/30%)는 항상 켜짐(잠금 없음)."""
    pre, post = path(e)
    ep = e['price']; A = atr_pct(e)
    t2 = T if T is not None else 6 * A
    floor = ep * (1 + 2 * FEE + 0.001)
    hard = ep * (1 - stop); guard = 0.0; peak = ep
    rem = 1.0; got = 0.0; part_done = False; hit2r = False
    for k, (o, h, l, c) in post[1:721]:
        lvl = max(hard, guard)
        if l <= lvl:
            got += rem * min(lvl, o) * (1 - slip) / ep; rem = 0; break
        peak = max(peak, h)
        if peak >= ep * (1 + arm):
            guard = max(guard, floor, ep + (peak - ep) * (1 - give))
        if peak >= ep * (1 + t2):
            hit2r = True
            if partial and not part_done and h >= ep * (1 + t2):
                q = rem / 3 if rem == 1.0 else rem * 0.5   # 1/3 (첫 분할이므로 rem=1)
                q = 1 / 3
                got += q * (1 + t2) * (1 - lim_slip); rem -= q; part_done = True
            if trail:
                guard = max(guard, peak * (1 - 2 * A))          # 추적 손절: 고점 - 2×ATR15
            if giveback:
                guard = max(guard, ep + (peak - ep) * 0.5)      # 수익 반납 50%
    if rem > 1e-9:
        last = post[min(len(post) - 1, 720)][1][3]
        got += rem * last * (1 - slip) / ep
    r = got - 1 - 2 * FEE
    return (r, hit2r) if ret_info else r

def rep(name, fn):
    a = [fn(e) for e in ents if e['t'] < '2026-11-05']; b = [fn(e) for e in ents if e['t'] >= '2026-11-05']
    z = [fn(e) for e in ents if e['t'] >= '2026-11-06T18:00']; al = a + b
    print(f"{name:42s} 전체 {sum(al)*80000:+7.0f}원 | 전반 {sum(a)*80000:+6.0f} 후반 {sum(b)*80000:+6.0f} | 어제18시~ {sum(z)*80000:+6.0f}")

if __name__ == '__main__':
    As = [6 * atr_pct(e) for e in ents]
    print(f'현재 2R 기준(6×ATR15) 분포: 중앙값 {st.median(As)*100:.1f}%  최소 {min(As)*100:.1f}%  최대 {max(As)*100:.1f}%')
    reach = lambda T: sum(1 for e in ents if r2sim(e, T=T, ret_info=True)[1])
    for T in (0.04, 0.05, 0.06, 0.08):
        print(f'  4h·12h 안에 +{T*100:.0f}% 닿은 진입: {reach(T)}/86')
    print(f'  현재 기준(종목별) 닿은 진입: {sum(1 for e in ents if r2sim(e, ret_info=True)[1])}/86')
    print('--- 전체 규칙 (이익보호+3%/30%, 잠금 없음, 손절4.5%) + 2R 기준 바꾸기 ---')
    rep('기준선: 2R 규칙 없음(이익보호만)', lambda e: sim(e, slip=0.003, lock=None)[0])
    rep('현재 2R(종목별 6×ATR15) 1/3+추적+반납', lambda e: r2sim(e))
    for T in (0.04, 0.05, 0.06, 0.08):
        rep(f'2R={T*100:.0f}% 1/3+추적+반납', lambda e, T=T: r2sim(e, T=T))
    print('--- 5%로 낮출 때 구성요소별 ---')
    rep('2R=5% 1/3 분할만', lambda e: r2sim(e, T=0.05, trail=False, giveback=False))
    rep('2R=5% ATR추적만', lambda e: r2sim(e, T=0.05, partial=False, giveback=False))
    rep('2R=5% 반납50%만', lambda e: r2sim(e, T=0.05, partial=False, trail=False))
    rep('2R=5% 추적+반납(분할 없음)', lambda e: r2sim(e, T=0.05, partial=False))
    print('--- 5%로 낮추면 영향받는 거래 ---')
    n = 0
    for e in ents:
        base = sim(e, slip=0.003, lock=None)[0]; r, hit = r2sim(e, T=0.05, ret_info=True)
        cur = r2sim(e)
        if hit and abs(r - cur) > 1e-6:
            n += 1
            print(f"  {e['market'][4:]:9s} {e['t'][5:16]} 이익보호만 {base*100:+.2f}%  현재2R {cur*100:+.2f}%  2R=5% {r*100:+.2f}%")
    print('영향받은 거래', n)
