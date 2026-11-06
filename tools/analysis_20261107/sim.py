import json, os, datetime as dt, statistics as st, itertools
S = os.path.dirname(os.path.abspath(__file__))
E = json.load(open(S + r'\entries.json', encoding='utf-8'))
C = json.load(open(S + r'\c1m.json'))
ents = E['entries']
FEE = 0.0005
def path(e):
    c = C[e['key']]
    t0 = dt.datetime.fromisoformat(e['t']).replace(second=0, microsecond=0, tzinfo=None)
    ks = sorted(c)
    post = [(k, c[k]) for k in ks if dt.datetime.fromisoformat(k) >= t0]
    pre = [(k, c[k]) for k in ks if dt.datetime.fromisoformat(k) < t0]
    return pre, post

def sim(e, stop=0.045, lock=0.015, arm=0.03, give=0.30, tp=None, trail=None, trail_arm=None, tmax=720, slip=0.005, fill_gap=True):
    """진입가 e['price'] 기준. 반환: 순수익률(비용 후), 사유, 보유분"""
    pre, post = path(e)
    ep = e['price']
    hard = ep * (1 - stop)
    guard = 0.0
    peak = ep
    floor = ep * (1 + 2 * FEE + 0.001)
    n = 0
    for k, (o, h, l, c) in post[1:tmax + 1]:   # 진입 분은 건너뜀(체결 시점이 분 중간)
        n += 1
        lvl = max(hard, guard)
        if l <= lvl:
            fill = min(lvl, o) if fill_gap else lvl
            return fill * (1 - slip) / ep - 1 - 2 * FEE, ('손절' if lvl == hard and guard < hard else '이익보호'), n
        if tp and h >= ep * (1 + tp):
            return ep * (1 + tp) * (1 - slip * 0.4) / ep - 1 - 2 * FEE, '익절', n   # 지정가 익절은 미끄러짐 작게
        peak = max(peak, h)
        if lock is not None and peak >= ep * (1 + lock) and (arm is None or peak < ep * (1 + arm)):
            guard = max(guard, floor)
        if arm is not None and give is not None and peak >= ep * (1 + arm):
            guard = max(guard, floor, ep + (peak - ep) * (1 - give))
        if trail is not None and peak >= ep * (1 + (trail_arm or 0)):
            guard = max(guard, peak * (1 - trail))
    last = post[min(len(post) - 1, tmax)][1][3]
    return last * (1 - slip) / ep - 1 - 2 * FEE, '기간끝', n

def stats(name, fn, es=None):
    es = es if es is not None else ents
    r = [fn(e) for e in es]
    xs = [x[0] for x in r]
    w = [x for x in xs if x > 0]; l = [x for x in xs if x <= 0]
    tot = sum(xs)
    why = {}
    for x in r: why[x[1]] = why.get(x[1], 0) + 1
    # 한 건당 8만원 고정 → 원 단위
    print(f"{name:34s} 합계 {tot*80000:+8.0f}원 평균 {st.mean(xs)*100:+.2f}% 승률 {len(w)/len(xs)*100:4.0f}% 평균이익 {st.mean(w)*100 if w else 0:+.2f}% 평균손실 {st.mean(l)*100 if l else 0:+.2f}%  {why}")
    return xs

if __name__ == '__main__':
    done = [e for e in ents if 'actual' in e]
    print('진입', len(ents), '청산', len(done))
    ac = [e['actual'] for e in done]
    print(f"[실제] 합계 {sum(ac)*80000:+.0f}원 평균 {st.mean(ac)*100:+.2f}% 승률 {sum(a>0 for a in ac)/len(ac)*100:.0f}% (기록 기준, 8만원 환산)")
    print('--- 현재 규칙 재현 (손절4.5% · 본전잠금+1.5% · 이익보호+3%/반납30%) ---')
    for slip in (0.003, 0.005):
        stats(f'재현 slip{slip}', lambda e: sim(e, slip=slip))
    # 재현 정확도: 같은 종목들 실제 대비
    print('--- 규칙 바꿔보기 (미끄러짐 0.5%) ---')
    stats('V0 현행', lambda e: sim(e))
    stats('V1 잠금·이익보호 없음(손절만)', lambda e: sim(e, lock=None, arm=None))
    stats('V2 잠금 없음, 이익보호만', lambda e: sim(e, lock=None))
    stats('A  잠금 +2.5%', lambda e: sim(e, lock=0.025))
    stats('A2 잠금 +2.5%, 이익보호 +4%', lambda e: sim(e, lock=0.025, arm=0.04))
    for s in (0.03, 0.035, 0.06, 0.08):
        stats(f'B  손절 {s*100:.1f}%', lambda e, s=s: sim(e, stop=s))
    stats('B2 손절3% + 잠금2.5%', lambda e: sim(e, stop=0.03, lock=0.025))
    print('--- 오른 뒤 매도 기준 후보 ---')
    for tp in (0.02, 0.03, 0.05):
        stats(f'고정익절 +{tp*100:.0f}% (손절4.5)', lambda e, tp=tp: sim(e, lock=None, arm=None, tp=tp))
    for tr, ta in ((0.015, 0.02), (0.02, 0.03), (0.03, 0.03), (0.03, 0.05), (0.04, 0.05)):
        stats(f'추적 고점-{tr*100:.1f}% (+{ta*100:.0f}%부터)', lambda e, tr=tr, ta=ta: sim(e, lock=None, arm=None, trail=tr, trail_arm=ta))
    for tm in (60, 120, 240, 360):
        stats(f'시간만 {tm//60}h 후 매도(손절4.5)', lambda e, tm=tm: sim(e, lock=None, arm=None, tmax=tm))
