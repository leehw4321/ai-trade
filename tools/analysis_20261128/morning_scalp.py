"""아침 급등 단타 전략 후보 시험 (2026-11-28 사용자: "단타 전략으로 추천할 만한 전략을 만들어 제안").

읽기 전용, 주문 없음. 자료: fetch1m.py 1분봉(매일 08:00~10:30, 거래대금 10억↑ 전 종목, 약 30일).
설정은 앞 절반 날짜에서만 고르고 뒤 절반 날짜로 확인한다(여러 조합 중 최고값 착시 방지).

전략
 S1 돌파 단타: 09:00 뒤 w분 등락 ≥ g, 그 w분 거래대금 ≥ 평소 vr배, 마지막 1분 양봉 → 다음 1분 시가에 시장가 매수.
    익절 지정가 +T, 손절 −S, 최대 N분.
 S2 급등 뒤 눌림 매수: 09:00~09:20 사이 09:00 대비 ≥ X 오르고(그때까지 거래대금 ≥ 평소 10배) → 그 뒤 고점에서
    (고점−09:00가격)×R 만큼 내려온 가격에 지정가 매수(30분 안 체결 안 되면 취소). 익절 +T, 손절 −S, 늦어도 10:30.
 체결: 시장가 매수·손절은 0.2% 불리, 지정가는 그 값. 같은 1분봉에 익절·손절이 다 닿으면 손절로 본다. 수수료 왕복 0.1%.
결과: 코인 트레이딩/reports/morning_scalp_YYYYMMDD_HHMM.md
"""
import datetime as dt, itertools
import numpy as np
import morning_1m as Q

FEE, SLIP = 0.001, 0.002


def exit_path(b, k0, e, T, S, last):
    """k0분부터 보유. 익절 e(1+T) 지정가, 손절 e(1−S), last분 종가에 정리. 반환 수익률(수수료·체결 차이 포함)."""
    tp, sl = e * (1 + T), e * (1 - S)
    for q in range(k0, min(last + 1, 150)):
        if b['l'][q] <= sl: return min(sl, b['o'][q]) * (1 - SLIP) / e - 1 - FEE
        if b['h'][q] > tp: return T - FEE
    return b['c'][min(last, 149)] * (1 - SLIP) / e - 1 - FEE


_TRIG = {}


def triggers(days, norm, w, g, vr, until):
    key = (id(days), w, g, vr, until)
    if key not in _TRIG:
        out = []
        for day, rows in days.items():
            for m, b in rows.items():
                nv = norm[(day, m)]
                if not nv == nv: continue
                for k in range(60 + w - 1, 60 + until):
                    if b['c'][k] / b['o'][k - w + 1] - 1 >= g and b['v'][k - w + 1:k + 1].sum() / w / nv >= vr and b['c'][k] > b['o'][k]:
                        if k + 1 < 149: out.append((day, b, k))
                        break
        _TRIG[key] = out
    return _TRIG[key]


def s1(days, norm, info, w, g, vr, until, T, S, N):
    out = []
    for day, b, k in triggers(days, norm, w, g, vr, until):
        e = b['o'][k + 1] * (1 + SLIP)
        out.append((day, exit_path(b, k + 1, e, T, S, k + 1 + N)))
    return out


def s2(days, norm, info, X, R, T, S):
    out = []
    for day, rows in days.items():
        p9 = info[day][0]
        for m, b in rows.items():
            nv = norm[(day, m)]
            if not nv == nv: continue
            base = p9[m]; hi = base; armed = None
            for k in range(60, 81):                      # 09:00~09:20 급등 확인
                hi = max(hi, b['h'][k])
                if hi / base - 1 >= X and b['v'][60:k + 1].sum() / (k - 59) / nv >= 10:
                    armed = k; break
            if armed is None: continue
            filled = None
            for k in range(armed + 1, min(armed + 31, 149)):
                lim = hi - R * (hi - base)
                if b['l'][k] <= lim:
                    filled = (k, min(lim, b['o'][k])); break
                hi = max(hi, b['h'][k])
            if not filled: continue
            k, e = filled
            # 체결 봉 안에서 손절가까지 닿았으면 손절로 본다
            if b['l'][k] <= e * (1 - S):
                out.append((day, -S * (1 + 0) - SLIP - FEE)); continue
            out.append((day, exit_path(b, k + 1, e, T, S, 149)))
    return out


def stats(res, days_list):
    if not res: return None
    v = np.array([x for _, x in res])
    return dict(n=len(v), per_day=len(v) / len(days_list), mean=v.mean(), win=(v > 0).mean(), sum=v.sum())


def main():
    days = Q.load_days()
    ms, grid, O, H, L, C, V = Q.M.load()
    pos = {m: j for j, m in enumerate(ms)}; t0 = grid[0]
    norm, info = {}, {}
    for day, rows in days.items():
        nine = dt.datetime(day.year, day.month, day.day, tzinfo=Q.UTC)
        i9 = int((nine - t0).total_seconds() // 900)
        for m in rows:
            j = pos.get(m)
            v24 = np.nansum(V[j, i9 - 96:i9]) if j is not None else np.nan
            norm[(day, m)] = max(v24 / 1440, 1) if v24 == v24 else np.nan
        p9 = {m: b['c'][59] for m, b in rows.items()}
        info[day] = (p9,)
    ds = sorted(days); mid = ds[len(ds) // 2]
    A = {d: days[d] for d in ds if d < mid}; B = {d: days[d] for d in ds if d >= mid}
    now = dt.datetime.now(Q.KST)
    lines = [f'# 아침 급등 단타 전략 후보 시험 ({now:%Y-%m-%d %H:%M} KST)', '',
             f'- 자료: {ds[0]} ~ {ds[-1]} ({len(ds)}일) 08:00~10:30 1분봉, 거래대금 10억↑ 전 종목(하루 약 95종목)',
             f'- **설정 고르기 = 앞 절반 {len(A)}일({ds[0]:%m-%d}~), 확인 = 뒤 절반 {len(B)}일({mid:%m-%d}~)**. 뒤 절반 결과가 실제 기대에 가깝다.',
             '- 체결: 시장가 매수·손절 0.2% 불리, 익절 지정가, 같은 1분봉에 익절·손절 다 닿으면 손절, 수수료 왕복 0.1%', '']
    for name, fn, grid_ in (
        ('S1 돌파 단타', s1, [dict(w=w, g=g, vr=vr, until=u, T=T, S=S, N=N) for w, g, vr, u, T, S, N in
                          itertools.product((1, 3, 5), (0.02, 0.03, 0.05), (10, 20), (10, 30), (0.015, 0.02, 0.03, 0.05), (0.01, 0.015, 0.02, 0.03), (5, 15, 30))]),
        ('S2 급등 뒤 눌림 매수', s2, [dict(X=X, R=R, T=T, S=S) for X, R, T, S in
                                itertools.product((0.05, 0.08, 0.12), (0.3, 0.4, 0.5, 0.6), (0.015, 0.02, 0.03, 0.05), (0.015, 0.02, 0.03, 0.05))])):
        res = []
        for p in grid_:
            a = stats(fn(A, norm, info, **p), list(A)); b = stats(fn(B, norm, info, **p), list(B))
            if a and b and a['n'] >= 15: res.append((p, a, b))
        res.sort(key=lambda x: -x[1]['mean'])
        pos_b = np.mean([b['mean'] > 0 for _, _, b in res]) if res else 0
        lines += [f'## {name}', '', f'- 조합 {len(res)}개(앞 절반 15건 이상). 뒤 절반에서 + 인 조합 비율 {pos_b:.0%}, 뒤 절반 전체 조합 평균 {np.mean([b["mean"] for _, _, b in res]):+.2%}', '',
                  '| 앞 절반 순위 | 설정 | 앞: 건/일 · 평균 · 승률 | **뒤: 건/일 · 평균 · 승률** |', '|---|---|---|---|']
        for r, (p, a, b) in enumerate(res[:10], 1):
            ps = ' '.join(f'{k}={v:g}' for k, v in p.items())
            lines.append(f"| {r} | {ps} | {a['per_day']:.1f} · {a['mean']:+.2%} · {a['win']:.0%} | **{b['per_day']:.1f} · {b['mean']:+.2%} · {b['win']:.0%}** |")
        # 뒤 절반에서도 좋은 것(참고, 둘 다 + 인 조합 중 합산 평균 상위)
        both = sorted([x for x in res if x[1]['mean'] > 0 and x[2]['mean'] > 0], key=lambda x: -(x[1]['mean'] * x[1]['n'] + x[2]['mean'] * x[2]['n']) / (x[1]['n'] + x[2]['n']))
        lines += ['', f'- 앞·뒤 모두 + 인 조합 {len(both)}개. 상위 5:', '']
        for p, a, b in both[:5]:
            lines.append(f"  - {' '.join(f'{k}={v:g}' for k, v in p.items())}: 앞 {a['n']}건 {a['mean']:+.2%} / 뒤 {b['n']}건 {b['mean']:+.2%}")
        lines.append('')
    lines += ['주의: 1분봉으로는 봉 안의 가격 순서와 호가 공백을 알 수 없다. 실제 급등 중 시장가 체결은 0.2%보다 나쁠 수 있다. 아침 시간대만 본 결과다. 입증이 아니다.']
    fo = Q.M.OUT / f'morning_scalp_{now:%Y%m%d_%H%M}.md'
    fo.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(fo); print('\n'.join(lines))


if __name__ == '__main__':
    main()
