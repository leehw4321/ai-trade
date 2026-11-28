"""'많이 오를 종목'을 미리 가려내는 차트 지표가 있는가 (2026-11-28 사용자: "많이 오른 종목이 아니라 많이 오를 종목을 발굴해야").

읽기 전용, 주문 없음. 자료: c15m(원화 전 종목 15분봉, 10-29~).
매시 정각(11-01~), 그때 24시간 거래대금 10억↑ 종목 전부에서 지표를 계산하고 지표 상위 10% / 하위 10%가
 T1: 다음 4시간 안에 +5% 이상 오른 적 있음(고가 기준)
 T2: 24시간 안에 −3%보다 +3%에 먼저 닿음(지금 봇 같은 손절·익절이면 이기는 거래)
 F : 4시간 뒤 수익률(비용 0.3% 뺌)
을 얼마나 맞히는지 본다. 앞/뒤 절반을 따로 본다. 결과: 코인 트레이딩/reports/indicator_study_*.md
"""
import datetime as dt
import numpy as np
import entry_rule_study as S
import regime_pause_study as P

KST = S.KST


def ema(X, n):
    a = 2 / (n + 1); out = np.full_like(X, np.nan); cur = X[:, 0].copy()
    for t in range(X.shape[1]):
        x = X[:, t]; cur = np.where(np.isnan(cur), x, np.where(np.isnan(x), cur, a * x + (1 - a) * cur)); out[:, t] = cur
    return out


def rsi(C, i, n):
    d = np.diff(C[:, i - n:i + 1], axis=1)
    up = np.nansum(np.clip(d, 0, None), axis=1); dn = np.nansum(np.clip(-d, 0, None), axis=1)
    return 100 - 100 / (1 + up / np.maximum(dn, 1e-12))


def main():
    ms, grid, O, H, L, C, V = S.load()
    n = len(grid); excl = np.array([m in P.EXCL or m in S.STABLE for m in ms])
    macd = ema(C, 48) - ema(C, 104); sig = ema(macd, 36); hist = (macd - sig) / C       # 15분봉으로 만든 1시간 MACD(12,26,9) 근사
    dec = [i for i, g in enumerate(grid) if g.astimezone(KST) >= S.FIRST and g.minute == 45 and i + 97 < n]
    mid = dec[len(dec) // 2]
    IND = {
        '18시간 상승률(지금 봇)': lambda i: C[:, i] / C[:, i - 72] - 1,
        '4시간 상승률': lambda i: C[:, i] / C[:, i - 16] - 1,
        '72시간 상승률': lambda i: C[:, i] / C[:, i - 288] - 1,
        'RSI 15분(14)': lambda i: rsi(C, i, 14),
        'RSI 1시간(14)': lambda i: rsi(C[:, i - 56:i + 1:4], 14, 14),
        'RSI 낮음(과매도, 15분)': lambda i: -rsi(C, i, 14),
        'MACD 히스토그램(1시간 근사)': lambda i: hist[:, i],
        'MACD 막 양전환(직전 4봉 안)': lambda i: np.where((hist[:, i] > 0) & (np.nanmin(hist[:, i - 4:i], axis=1) < 0), hist[:, i], np.nan),
        '볼린저 수축(20시간 폭 좁음)': lambda i: -np.nanstd(C[:, i - 79:i + 1], axis=1) / np.nanmean(C[:, i - 79:i + 1], axis=1),
        '24시간 고점 돌파': lambda i: C[:, i] / np.nanmax(H[:, i - 96:i], axis=1) - 1,
        '72시간 고점 돌파': lambda i: C[:, i] / np.nanmax(H[:, i - 288:i], axis=1) - 1,
        '7일 저점 근처(바닥)': lambda i: -(C[:, i] / np.nanmin(L[:, max(i - 672, 0):i + 1], axis=1) - 1),
        '거래대금 급증(1시간 ÷ 24시간 평균)': lambda i: np.nansum(V[:, i - 3:i + 1], axis=1) / np.maximum(np.nansum(V[:, i - 95:i + 1], axis=1) / 24, 1),
        '거래대금 증가 추세(6시간 ÷ 이전 18시간)': lambda i: np.nansum(V[:, i - 23:i + 1], axis=1) / np.maximum(np.nansum(V[:, i - 95:i - 23], axis=1) / 3, 1),
        '조용한 매집(거래대금 2배↑·4시간 등락 ±2% 안)': lambda i: np.where(np.abs(C[:, i] / C[:, i - 16] - 1) < 0.02,
                                                       np.nansum(V[:, i - 15:i + 1], axis=1) / np.maximum(np.nansum(V[:, i - 95:i + 1], axis=1) / 6, 1), np.nan),
        '5시간 평균선 ÷ 20시간 평균선(골든크로스 정도)': lambda i: np.nanmean(C[:, i - 19:i + 1], axis=1) / np.nanmean(C[:, i - 79:i + 1], axis=1) - 1,
        '20시간 평균선 위 거리': lambda i: C[:, i] / np.nanmean(C[:, i - 79:i + 1], axis=1) - 1,
        '변동성 수축(4시간 ÷ 24시간 변동폭)': lambda i: -(np.nanmax(H[:, i - 15:i + 1], axis=1) / np.nanmin(L[:, i - 15:i + 1], axis=1)) /
                                               (np.nanmax(H[:, i - 95:i + 1], axis=1) / np.nanmin(L[:, i - 95:i + 1], axis=1)),
        '변동성 큼(24시간 15분 수익률 표준편차)': lambda i: np.nanstd(np.diff(np.log(C[:, i - 96:i + 1]), axis=1), axis=1),
    }
    res = {k: {'top': [], 'bot': []} for k in IND}; base = []
    for i in dec:
        v24 = np.nansum(V[:, i - 95:i + 1], axis=1)
        ok = (v24 >= 1e9) & ~excl & ~np.isnan(C[:, i]) & ~np.isnan(C[:, i - 288])
        idx = np.where(ok)[0]
        if len(idx) < 30: continue
        e = C[idx, i]
        t1 = np.nanmax(H[idx, i + 1:i + 17], axis=1) / e - 1 >= 0.05
        hh = H[idx, i + 1:i + 97] >= e[:, None] * 1.03; ll = L[idx, i + 1:i + 97] <= e[:, None] * 0.97
        fh = np.where(hh.any(1), hh.argmax(1), 999); fl = np.where(ll.any(1), ll.argmax(1), 999)
        t2 = fh < fl
        f = C[idx, i + 16] / e - 1 - 0.003
        h2 = i >= mid
        base.append((t1.mean(), t2.mean(), f.mean(), h2))
        for k, fn in IND.items():
            with np.errstate(all='ignore'):
                x = fn(i)[idx]
            good = ~np.isnan(x)
            if good.sum() < 20: continue
            q = max(1, int(good.sum() * 0.1))
            o = np.argsort(np.where(good, x, np.nan))
            o = [j for j in o if good[j]]
            for side, sel in (('top', o[-q:]), ('bot', o[:q])):
                res[k][side].append((t1[sel].mean(), t2[sel].mean(), f[sel].mean(), h2))
    b = np.array([x[:3] for x in base])
    now = dt.datetime.now(KST)
    lines = [f'# 많이 오를 종목을 미리 가려내는 지표 점검 ({now:%Y-%m-%d %H:%M} KST)', '',
             f'- 매시 {len(base)}회, 24시간 거래대금 10억↑ 종목 전부(하루 약 95종목)에서 지표 상위 10% / 하위 10%의 이후 결과',
             f'- 기준선(아무 종목): 4시간 안 +5% 도달 **{b[:, 0].mean():.1%}**, +3%가 −3%보다 먼저 **{b[:, 1].mean():.1%}**, 4시간 뒤 평균 {b[:, 2].mean():+.2%}', '',
             '| 지표(상위 10%) | 4시간 안 +5% (앞/뒤) | +3% 먼저 (앞/뒤) | 4시간 뒤 평균 | 하위 10%의 4시간 안 +5% |', '|---|---|---|---|---|']
    out = []
    for k, r in res.items():
        if not r['top']: continue
        T = np.array([x[:3] for x in r['top']]); Bt = np.array([x[:3] for x in r['bot']])
        a = np.array([x[:3] for x in r['top'] if not x[3]]); c = np.array([x[:3] for x in r['top'] if x[3]])
        out.append((T[:, 1].mean(), f"| {k} | {T[:, 0].mean():.1%} ({a[:, 0].mean():.1%}/{c[:, 0].mean():.1%}) | **{T[:, 1].mean():.1%}** ({a[:, 1].mean():.1%}/{c[:, 1].mean():.1%}) | {T[:, 2].mean():+.2%} | {Bt[:, 0].mean():.1%} |"))
    lines += [l for _, l in sorted(out, key=lambda x: -x[0])]
    lines += ['', '- "+3% 먼저"가 기준선보다 높아야 지금 봇 같은 매매에서 이기는 거래가 늘어난다. 50%를 넘어야 비용 전 본전 이상.',
              '- "4시간 안 +5%"가 높으면서 "+3% 먼저"가 낮은 지표는 크게 출렁이기만 하는 것(오르기도 하지만 먼저 떨어지기도)이다.']
    fo = S.OUT / f'indicator_study_{now:%Y%m%d_%H%M}.md'
    fo.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(fo); print('\n'.join(lines))


if __name__ == '__main__':
    main()
