"""indicator_study 상위 지표를 순위 점수로 바꿔 실거래에 맞춘 포트폴리오 시뮬레이션(gap_study S3)으로 확인. 읽기 전용."""
import datetime as dt, sys
import numpy as np
import gap_study as G, entry_rule_study as S
sys.path.insert(0, str(G.HERE.parent / 'analysis_20261111')); import stop035_review as R
ms, grid, O, H, L, C, V = S.load(); t0 = grid[0]; n = len(grid)
trips, _, _ = R.load()
si = int((min(t['open'] for t in trips).astimezone(dt.timezone.utc) - t0).total_seconds() // 900); ei = n - 97; mid = (si + ei) // 2
SC = {'18시간 상승률(지금)': None,
      '변동성 수축(4시간÷24시간 변동폭 작음)': lambda i: -(np.nanmax(H[:, i - 15:i + 1], axis=1) / np.nanmin(L[:, i - 15:i + 1], axis=1)) / (np.nanmax(H[:, i - 95:i + 1], axis=1) / np.nanmin(L[:, i - 95:i + 1], axis=1)),
      '변동성 큼': lambda i: np.nanstd(np.diff(np.log(C[:, i - 96:i + 1]), axis=1), axis=1),
      '72시간 상승률': lambda i: C[:, i] / C[:, i - 288] - 1}
print('| 순위 점수 | 손절/이익보호 | 건수 | 1건 평균 | 승률 | 앞/뒤 절반 |\n|---|---|---|---|---|---|')
for name, f in SC.items():
    for stop, at, give in ((0.035, 0.03, 0.3), (0.025, 0.08, 0.5)):
        G.STOP, G.AT, G.GIVE = stop, at, give
        tr = G.portfolio(ms, grid, O, H, L, C, V, si, ei, cap24=False, rank_exit=True, reentry=True, score=f)
        v = np.array([x['ret'] for x in tr]); a = [x['ret'] for x in tr if x['i0'] < mid]; b = [x['ret'] for x in tr if x['i0'] >= mid]
        print(f"| {name} | {stop:.1%} / +{at:.0%}·{give:.0%} | {len(v)} | {v.mean():+.2%} | {(v > 0).mean():.0%} | {np.mean(a):+.2%} / {np.mean(b):+.2%} |")
