"""매수 때 '전체 추세(고점에서 내려오는 중인지)'를 보는 조건 조사 (2026-11-28 사용자: PUFFER처럼 눈으로 봐도 하락 추세인데 왜 사나).

읽기 전용, 주문 없음.
A) 실거래 왕복을 매수 순간의 추세 값 구간별로(고점 대비 하락폭·직전 1·2시간 등락·4시간 평균선 위/아래)
B) 실거래에 맞춘 포트폴리오 시뮬레이션(gap_study S3: 자리 3·순위 매도·재매수 대기)에 매수 조건 하나씩 더하기, 앞/뒤 절반
"""
import datetime as dt, sys
import numpy as np
import gap_study as G, entry_rule_study as S
sys.path.insert(0, str(G.HERE.parent / 'analysis_20261111')); import stop035_review as R

UTC = dt.timezone.utc


def feats(j, i, H, C):
    hi12 = np.nanmax(H[j, i - 47:i + 1]); hi24 = np.nanmax(H[j, i - 95:i + 1])
    return {'12시간 고점 대비': C[j, i] / hi12 - 1, '24시간 고점 대비': C[j, i] / hi24 - 1,
            '직전 1시간': C[j, i] / C[j, i - 4] - 1, '직전 2시간': C[j, i] / C[j, i - 8] - 1,
            '4시간 평균선 대비': C[j, i] / np.nanmean(C[j, i - 15:i + 1]) - 1}


def main():
    ms, grid, O, H, L, C, V = S.load()
    t0 = grid[0]; n = len(grid); pos = {m: j for j, m in enumerate(ms)}
    trips, _, _ = R.load()
    rows = []
    for t in trips:
        j = pos.get(t['m']); i = int((t['open'].astimezone(UTC) - t0).total_seconds() // 900) - 1
        if j is None or i < 100 or i >= n: continue
        rows.append(dict(feats(j, i, H, C), ret=t['ret'], pnl=t['pnl']))
    print(f'## A. 실거래 {len(rows)}건, 매수 순간 추세 값 구간별 (손익 합계 {sum(r["pnl"] for r in rows):+,.0f}원)')
    cuts = {'12시간 고점 대비': [-0.08, -0.05, -0.03, -0.01], '24시간 고점 대비': [-0.10, -0.07, -0.04, -0.02],
            '직전 1시간': [-0.02, -0.005, 0.005, 0.02], '직전 2시간': [-0.03, -0.01, 0.01, 0.03], '4시간 평균선 대비': [-0.02, -0.005, 0.005, 0.02]}
    for k, cs in cuts.items():
        print(f'\n{k}')
        edges = [-9] + cs + [9]
        for a, b in zip(edges, edges[1:]):
            v = [r for r in rows if a < r[k] <= b]
            if v: print(f"  {a:+.1%}~{b:+.1%}: {len(v)}건 평균 {np.mean([r['ret'] for r in v]):+.2%} 합계 {sum(r['pnl'] for r in v):+,.0f}원 승률 {np.mean([r['pnl'] > 0 for r in v]):.0%}")
    si = int((min(t['open'] for t in trips).astimezone(UTC) - t0).total_seconds() // 900); ei = n - 97; mid = (si + ei) // 2
    F = {'(지금 규칙)': None,
         '12시간 고점 대비 −3% 이내만': lambda j, i, O, H, L, C, V: C[j, i] / np.nanmax(H[j, i - 47:i + 1]) - 1 > -0.03,
         '12시간 고점 대비 −5% 이내만': lambda j, i, O, H, L, C, V: C[j, i] / np.nanmax(H[j, i - 47:i + 1]) - 1 > -0.05,
         '24시간 고점 대비 −5% 이내만': lambda j, i, O, H, L, C, V: C[j, i] / np.nanmax(H[j, i - 95:i + 1]) - 1 > -0.05,
         '직전 1시간 상승만': lambda j, i, O, H, L, C, V: C[j, i] > C[j, i - 4],
         '직전 2시간 상승만': lambda j, i, O, H, L, C, V: C[j, i] > C[j, i - 8],
         '4시간 평균선 위만': lambda j, i, O, H, L, C, V: C[j, i] > np.nanmean(C[j, i - 15:i + 1]),
         '4시간 평균선 위 + 직전 1시간 상승': lambda j, i, O, H, L, C, V: C[j, i] > np.nanmean(C[j, i - 15:i + 1]) and C[j, i] > C[j, i - 4],
         '직전 2시간 하락만(눌림 매수)': lambda j, i, O, H, L, C, V: C[j, i] < C[j, i - 8]}
    print('\n## B. 실거래에 맞춘 시뮬레이션(자리 3·순위 매도·재매수 대기)에 매수 조건 추가')
    print('| 매수 조건 | 건수 | 1건 평균 | 승률 | 합계(%p) | 앞/뒤 절반 |\n|---|---|---|---|---|---|')
    for name, f in F.items():
        tr = G.portfolio(ms, grid, O, H, L, C, V, si, ei, cap24=False, rank_exit=True, reentry=True, entry_filter=f)
        v = np.array([x['ret'] for x in tr]); a = [x['ret'] for x in tr if x['i0'] < mid]; b = [x['ret'] for x in tr if x['i0'] >= mid]
        print(f"| {name} | {len(v)} | {v.mean():+.2%} | {(v > 0).mean():.0%} | {v.sum() * 100:+.0f} | {np.mean(a):+.2%} / {np.mean(b):+.2%} |")


if __name__ == '__main__':
    main()
