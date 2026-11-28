"""오를 때 +3%(또는 +5%) 선을 새 매수가로 보고 계단식으로 올리는 손절(2026-11-28 사용자 제안) 비교. 분석만.
guard_study와 같은 대상·1분봉. 기준선 = 매수가, 1분 고가가 기준선×(1+step)에 닿을 때마다 기준선을 그 값으로 올림(여러 칸 가능).
손절선 = max(원래 손절가, 기준선×(1−d)), 1분 저가가 닿으면 매도. 이익보호(지금 30% 반납)는 함께/없이 둘 다 본다.
"""
import datetime as dt
import numpy as np
import guard_study as G

UTC = dt.timezone.utc


def ladder(bars, e, step, d, guard=None):
    if not bars: return None, None
    o = np.array([b['o'] for b in bars]); h = np.array([b['h'] for b in bars]); l = np.array([b['l'] for b in bars]); c = np.array([b['c'] for b in bars])
    base = e; stop0 = e * (1 - G.STOP); peak = e; armed = False
    for k in range(len(c)):
        line = max(stop0, base * (1 - d)) if base > e else stop0
        if guard and armed: line = max(line, peak - guard[1] * (peak - e))
        if l[k] <= line:
            why = '손절' if line == stop0 else ('계단' if base > e and line == max(stop0, base * (1 - d)) else '이익보호')
            return min(line, o[k]) / e - 1 - G.COST, why
        peak = max(peak, h[k])
        while h[k] >= base * (1 + step): base *= (1 + step)
        if guard and not armed and peak >= e * (1 + guard[0]): armed = True
    return c[-1] / e - 1 - G.COST, '24시간'


def main():
    trips, _, _ = G.R.load()
    trips = [t for t in trips if t['open'] >= G.R.CUT]
    data = []
    for t in trips:
        st = t['open'].astimezone(UTC).replace(second=0, microsecond=0)
        data.append((t, [x for x in G.m1(t['m'], st) if dt.datetime.fromisoformat(x['t']).replace(tzinfo=UTC) >= st]))
    mid = sorted(t['open'] for t, _ in data)[len(data) // 2]

    def run(fn):
        rs = [(t['open'],) + fn(b, t['bf'] / t['bq']) for t, b in data]
        rs = [x for x in rs if x[1] is not None]
        v = np.array([x[1] for x in rs])
        return v, sum(x[1] for x in rs if x[0] < mid), sum(x[1] for x in rs if x[0] >= mid), rs
    bv, ba, bb, _ = run(lambda b, e: G.sim(b, e, dict(arm=0.03, give=0.30))[:2])
    fv, fa, fb, _ = run(lambda b, e: G.sim(b, e, dict(arm=0.05, give=0.30))[:2])
    K = G.KRW
    print(f'대상 {len(bv)}건. 지금 방식(+3% 이익보호) 합계 {bv.sum() * K:+,.0f}원, 1건 {bv.mean():+.2%}, 승률 {(bv > 0).mean():.0%}')
    print(f'참고 +5% 이익보호: 지금 대비 {(fv.sum() - bv.sum()) * K:+,.0f}원 (앞 {(fa - ba) * K:+,.0f} / 뒤 {(fb - bb) * K:+,.0f})\n')
    print('| 계단 | 새 기준선 아래 손절 거리 | 이익보호 | 지금 대비 | 앞 / 뒤 절반 | 1건 평균 | 승률 | 계단 손절로 끝 |')
    print('|---|---|---|---|---|---|---|---|')
    rows = []
    for step in (0.03, 0.05):
        for d in (0.035, 0.02, 0.01, 0.0):
            for gname, g in (('없음', None), ('+3% 30%', (0.03, 0.30)), ('+5% 30%', (0.05, 0.30))):
                v, a, b, rs = run(lambda bb_, e, step=step, d=d, g=g: ladder(bb_, e, step, d, g))
                rows.append(((v.sum() - bv.sum()) * K, f"| +{step:.0%} | {d:.1%} | {gname} | **{(v.sum() - bv.sum()) * K:+,.0f}원** | {(a - ba) * K:+,.0f} / {(b - bb) * K:+,.0f} | {v.mean():+.2%} | {(v > 0).mean():.0%} | {sum(x[2] == '계단' for x in rs)} |"))
    for _, r in sorted(rows, key=lambda x: -x[0]): print(r)


if __name__ == '__main__':
    main()
