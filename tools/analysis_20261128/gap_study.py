"""시뮬레이션과 실거래 차이 원인 분석 (2026-11-28 사용자: "2 원인분석" — 좋은 장에서 시뮬 +0.86% vs 실거래 −0.42%).

읽기 전용, 주문 없음. 자료: c15m 15분봉, 실거래 왕복(stop035_review.load).
다리 놓기(한 단계씩 실거래에 가깝게):
 S0 매시 상위 3을 겹쳐 셈(지난 보고서 방식), 손절 3.5%·이익보호 3%/30%·24시간
 S1 자리 3개 포트폴리오(보유 중 재매수 없음), 같은 매도
 S2 S1 + 24시간 제한 없음 + 순위 매도(유지 순위 9 밖, 손실이면 4시간 유예)
 S3 S2 + 재매수 대기(이익 청산 4시간·0.5ATR 눌림, 손실 청산 24시간)
 L1 실거래가 실제로 산 종목·시각, 신호 가격(직전 마감 15분봉 종가)에 사고 S0 매도 규칙
 L2 L1과 같되 실제 체결 가격으로 산 경우(매수 체결 차이)
 L3 실제 결과
그리고 실거래 매도 사유별 손익, 시뮬(S3)과 실거래의 종목 겹침.
결과: 코인 트레이딩/reports/gap_study_YYYYMMDD_HHMM.md
"""
import datetime as dt, sys
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / 'analysis_20261111'))
import entry_rule_study as S
import regime_pause_study as P
import stop035_review as R

KST, UTC = S.KST, dt.timezone.utc
COST, STOP, AT, GIVE = 0.003, 0.035, 0.03, 0.30


def atr_pct(j, i, H, L, C):
    tr = np.maximum(H[j, i - 13:i + 1] - L[j, i - 13:i + 1], np.abs(H[j, i - 13:i + 1] - C[j, i - 14:i]))
    return np.nanmean(tr) / C[j, i]


def portfolio(ms, grid, O, H, L, C, V, start_i, end_i, cap24=True, rank_exit=False, reentry=False, slots=3, M=9, scan=7, entry_filter=None):
    held = {}      # j → dict(e, i0, peak, out_since)
    last = {}      # j → (sell i, price, profit)
    trades = []
    good = {}      # 판단 시각 → 좋은 장 여부(오른 종목 비율 > 60%)
    for i in range(start_i, end_i):
        # 15분마다 손절·이익보호·24시간
        for j in list(held):
            h = held[j]
            if i <= h['i0']: continue
            e = h['e']; out = None
            if L[j, i] <= e * (1 - STOP): out = min(e * (1 - STOP), O[j, i])
            else:
                h['peak'] = max(h['peak'], C[j, i])
                if h['peak'] >= e * (1 + AT) and C[j, i] <= h['peak'] - GIVE * (h['peak'] - e): out = C[j, i]
                elif cap24 and i - h['i0'] >= 96: out = C[j, i]
            if out is not None:
                trades.append(dict(ret=out / e - 1 - COST, i0=h['i0'], j=j, good=h['good'], why='손절' if out < e * (1 - STOP) + 1e-12 else '기타'))
                last[j] = (i, out, out > e); del held[j]
        if grid[i].minute != 45: continue        # 정시 판단
        v24 = np.nansum(V[:, i - 95:i + 1], axis=1)
        ok = ~np.isnan(C[:, i - 288]) & ~np.isnan(C[:, i]) & np.array([m not in P.EXCL for m in ms])
        uni = [j for j in np.argsort(-np.where(ok, v24, -1))[:30] if ok[j]]
        rs = C[:, i] / C[:, i - 72] - 1; r4 = C[:, i] / C[:, i - 16] - 1
        pool = list(dict.fromkeys(uni + list(held)))
        order = sorted(pool, key=lambda j: -np.nan_to_num(rs[j], nan=-9))
        rank = {j: k + 1 for k, j in enumerate(order)}
        r24 = C[uni, i] / C[uni, i - 96] - 1
        g = bool(np.mean(r24 > 0) > 0.6)
        if rank_exit:
            for j in list(held):
                h = held[j]
                if rank.get(j, 99) > M:
                    h.setdefault('out_since', i)
                    loss = C[j, i] < h['e']
                    if not loss or i - h['out_since'] >= 16:
                        trades.append(dict(ret=C[j, i] / h['e'] - 1 - COST, i0=h['i0'], j=j, good=h['good'], why='순위'))
                        last[j] = (i, C[j, i], C[j, i] > h['e']); del held[j]
                else:
                    h.pop('out_since', None)
        free = slots - len(held)
        for j in order[:scan]:
            if free <= 0: break
            if j in held or j not in uni: continue
            if not (0 < rs[j] < 0.20 and r4[j] > -0.10): continue
            if entry_filter is not None and not entry_filter(j, i, O, H, L, C, V): continue   # 추가 매수 조건 시험용(2026-11-28)
            if reentry and j in last:
                si, sp, prof = last[j]
                hrs = (i - si) / 4
                if hrs < 24:
                    if prof and (hrs < 4 or C[j, i] > sp * (1 - 0.5 * atr_pct(j, i, H, L, C))): continue
                    if not prof: continue
            held[j] = dict(e=C[j, i], i0=i, peak=C[j, i], good=g); free -= 1
    return trades


def exit_from(j, i, e, O, H, L, C):
    """i 봉 이후 손절 3.5%·이익보호·24시간(S0 매도)."""
    peak = e; n = C.shape[1]
    for k in range(i + 1, min(i + 97, n)):
        if L[j, k] <= e * (1 - STOP): return min(e * (1 - STOP), O[j, k]) / e - 1 - COST
        peak = max(peak, C[j, k])
        if peak >= e * (1 + AT) and C[j, k] <= peak - GIVE * (peak - e): return C[j, k] / e - 1 - COST
    return C[j, min(i + 96, n - 1)] / e - 1 - COST


def main():
    ms, grid, O, H, L, C, V = S.load()
    t0 = grid[0]; n = len(grid); pos = {m: j for j, m in enumerate(ms)}
    trips, _, _ = R.load()
    first = min(t['open'] for t in trips)
    si = int((first.astimezone(UTC) - t0).total_seconds() // 900); ei = n - 97
    now = dt.datetime.now(KST)

    def summ(xs, key='ret'):
        v = np.array([x[key] for x in xs]); return (len(v), v.mean() if len(v) else np.nan)

    lines = [f'# 시뮬레이션 vs 실거래 차이 원인 ({now:%Y-%m-%d %H:%M} KST)', '',
             f'- 기간 {grid[si].astimezone(KST):%m-%d %H시} ~ {grid[ei].astimezone(KST):%m-%d %H시}. "좋은 장" = 매수 시점 상위 30 중 24시간 오른 종목 > 60%',
             '- 비용 왕복 0.3%(수수료 0.1% + 체결 차이 0.2% 가정)를 시뮬레이션에 넣었다', '',
             '## 1. 시뮬레이션을 한 단계씩 실거래에 가깝게', '', '| 단계 | 건수 | 1건 평균 | 좋은 장 | 나쁜 장 | 하루 건수 |', '|---|---|---|---|---|---|']
    days = (ei - si) / 96
    # S0
    s0 = []
    for i in range(si, ei):
        if grid[i].minute != 45: continue
        v24 = np.nansum(V[:, i - 95:i + 1], axis=1)
        ok = ~np.isnan(C[:, i - 288]) & ~np.isnan(C[:, i]) & np.array([m not in P.EXCL for m in ms])
        uni = [j for j in np.argsort(-np.where(ok, v24, -1))[:30] if ok[j]]
        g = bool(np.mean(C[uni, i] / C[uni, i - 96] - 1 > 0) > 0.6)
        rs = C[:, i] / C[:, i - 72] - 1; r4 = C[:, i] / C[:, i - 16] - 1
        cand = [j for j in sorted(uni, key=lambda j: -rs[j]) if 0 < rs[j] < .2 and r4[j] > -.1][:3]
        for j in cand: s0.append(dict(ret=exit_from(j, i, C[j, i], O, H, L, C), good=g))
    steps = [('S0 매시 상위 3 겹쳐 셈(지난 보고서 방식)', s0),
             ('S1 자리 3개·보유 중 재매수 없음', portfolio(ms, grid, O, H, L, C, V, si, ei)),
             ('S2 + 24시간 제한 없음 + 순위 매도(9위 밖, 손실 4시간 유예)', portfolio(ms, grid, O, H, L, C, V, si, ei, cap24=False, rank_exit=True)),
             ('S3 + 재매수 대기(이익 4시간·눌림, 손실 24시간)', portfolio(ms, grid, O, H, L, C, V, si, ei, cap24=False, rank_exit=True, reentry=True))]
    for name, xs in steps:
        a = summ(xs); gd = summ([x for x in xs if x['good']]); bd = summ([x for x in xs if not x['good']])
        lines.append(f'| {name} | {a[0]} | {a[1]:+.2%} | {gd[1]:+.2%} ({gd[0]}) | {bd[1]:+.2%} ({bd[0]}) | {a[0] / days:.1f} |')
    s3 = steps[-1][1]
    why = {}
    for x in s3: why.setdefault(x['why'], []).append(x['ret'])
    lines += ['', 'S3 매도 사유별: ' + ', '.join(f'{k} {len(v)}건 {np.mean(v):+.2%}' for k, v in why.items()), '']
    # 실거래
    L1, L2, L3, slip, reasons = [], [], [], [], {}
    for t in trips:
        j = pos.get(t['m'])
        if j is None: continue
        i = int((t['open'].astimezone(UTC) - t0).total_seconds() // 900) - 1
        if i < 300 or i + 97 >= n: continue
        uni_r = C[:, i] / C[:, i - 96] - 1
        v24 = np.nansum(V[:, i - 95:i + 1], axis=1)
        ok = ~np.isnan(C[:, i]) & ~np.isnan(C[:, i - 96]) & np.array([m not in P.EXCL for m in ms])
        uni = [k for k in np.argsort(-np.where(ok, v24, -1))[:30] if ok[k]]
        g = bool(np.mean(uni_r[uni] > 0) > 0.6)
        sig = C[j, i]; fill = t['bf'] / t['bq']
        slip.append(fill / sig - 1)
        L1.append(dict(ret=exit_from(j, i, sig, O, H, L, C), good=g))
        L2.append(dict(ret=exit_from(j, i, fill, O, H, L, C) + 0.001, good=g))   # 실제 체결가로 산 경우: 가정한 매수 쪽 체결 차이 0.1%는 되돌린다
        L3.append(dict(ret=t['ret'], good=g, pnl=t['pnl']))
        w = t['why'][0] if t['why'] else '?'
        k = ('손절' if '손절' in w and '조기' not in w else '하락 흐름 조기 손절' if '조기' in w else '이익보호' if '이익' in w else
             '추적 손절' if '추적' in w else '부분 익절' if '부분' in w else '순위 매도' if ('순위' in w or '순환' in w) else '손실 유예 본전' if '유예' in w else w[:12])
        reasons.setdefault(k, []).append((t['ret'], t['pnl'], g))
    lines += ['## 2. 실거래 322건을 같은 방식으로', '', '| 단계 | 건수 | 1건 평균 | 좋은 장 | 나쁜 장 |', '|---|---|---|---|---|']
    for name, xs in (('L1 실거래가 산 종목·시각, 신호 가격에 사고 S0 매도', L1), ('L2 같되 실제 체결 가격에 산 경우', L2), ('L3 실제 결과(실제 매도 규칙)', L3)):
        a = summ(xs); gd = summ([x for x in xs if x['good']]); bd = summ([x for x in xs if not x['good']])
        lines.append(f'| {name} | {a[0]} | {a[1]:+.2%} | {gd[1]:+.2%} ({gd[0]}) | {bd[1]:+.2%} ({bd[0]}) |')
    sl = np.array(slip)
    lines += ['', f'- 매수 체결 차이(실제 평균 매수가 ÷ 직전 마감 15분봉 종가 − 1): 평균 {sl.mean():+.2%}, 중앙값 {np.median(sl):+.2%}, +1% 넘게 비싸게 산 비율 {np.mean(sl > .01):.0%}', '',
              '### 실거래 첫 매도 사유별', '', '| 사유 | 건수 | 1건 평균 | 손익 합계 | 좋은 장 건수 |', '|---|---|---|---|---|']
    for k, v in sorted(reasons.items(), key=lambda kv: sum(x[1] for x in kv[1])):
        lines.append(f'| {k} | {len(v)} | {np.mean([x[0] for x in v]):+.2%} | {sum(x[1] for x in v):+,.0f}원 | {sum(x[2] for x in v)} |')
    lines += ['', '주의: 실거래 매도 규칙(추적 손절·부분 익절·단계 손절·지정가 매도 등)은 시뮬레이션에 다 넣지 못했다. 손절 폭은 11-11 전 4.5%였지만 시뮬레이션은 3.5%로 통일했다.']
    fo = S.OUT / f'gap_study_{now:%Y%m%d_%H%M}.md'
    fo.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(fo); print('\n'.join(lines))


if __name__ == '__main__':
    main()
