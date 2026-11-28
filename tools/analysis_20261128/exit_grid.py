"""실거래에 맞춘 포트폴리오 시뮬레이션(gap_study S3: 자리 3·순위 매도·재매수 대기)으로 매도 설정 비교. 읽기 전용."""
import datetime as dt, sys, itertools
import numpy as np
import gap_study as G, entry_rule_study as S
sys.path.insert(0, str(G.HERE.parent / 'analysis_20261111')); import stop035_review as R
ms, grid, O, H, L, C, V = S.load()
trips, _, _ = R.load(); t0 = grid[0]
si = int((min(t['open'] for t in trips).astimezone(dt.timezone.utc) - t0).total_seconds() // 900); ei = len(grid) - 97
mid = (si + ei) // 2
rows = []
for stop, at, give in itertools.product((0.025, 0.035, 0.05, 0.07), (0.03, 0.05, 0.08, 9.0), (0.3, 0.5)):
    if at == 9.0 and give == 0.5: continue
    G.STOP, G.AT, G.GIVE = stop, at, give
    tr = G.portfolio(ms, grid, O, H, L, C, V, si, ei, cap24=False, rank_exit=True, reentry=True)
    v = np.array([x['ret'] for x in tr]); a = [x['ret'] for x in tr if x['i0'] < mid]; b = [x['ret'] for x in tr if x['i0'] >= mid]
    rows.append((v.mean(), f"| 손절 {stop:.1%} | {'없음(순위 매도만)' if at > 1 else f'+{at:.0%} 뒤 {give:.0%} 반납'} | {len(v)} | {v.mean():+.2%} | {(v > 0).mean():.0%} | {v.sum() * 100:+.0f}%p | {np.mean(a):+.2%} / {np.mean(b):+.2%} |"))
rows.sort(key=lambda r: -r[0])
print('| 손절 | 이익보호 | 건수 | 1건 평균 | 승률 | 합계 | 앞/뒤 절반 |\n|---|---|---|---|---|---|---|')
print('\n'.join(r for _, r in rows))
