import datetime as dt
from sim import *
from r2 import r2sim
es = sorted(ents, key=lambda e: e['t'])
T = [dt.datetime.fromisoformat(e['t']).replace(tzinfo=None) for e in es]
R = [r2sim(e, T=0.05) for e in es]
W = 80000
print('기준(제동 없음)', len(es), '건', round(sum(R)*W), '원')
def run(N, thr, pause_h, lag_h=4, mode='sum'):
    out = 0; n = 0; until = None
    for i, (t, r) in enumerate(zip(T, R)):
        if until and t < until: continue
        prev = [R[j] for j in range(i) if T[j] <= t - dt.timedelta(hours=lag_h)][-N:]  # 청산이 끝났을 시간만큼 지난 거래만
        if len(prev) == N:
            m = sum(prev) if mode == 'sum' else sum(1 for x in prev if x > 0) / N
            if (mode == 'sum' and m <= thr) or (mode == 'win' and m <= thr):
                until = t + dt.timedelta(hours=pause_h); continue
        out += r; n += 1
    return out, n
print(f"{'규칙':34s} {'합계원':>8s} {'매수건':>6s}")
for N in (5, 8, 10, 15):
    for thr in (-0.03, -0.05, -0.08):
        for ph in (6, 12, 24):
            o, n = run(N, thr, ph)
            print(f'최근{N}건 합 ≤{thr*100:.0f}% → {ph}h 쉼'.ljust(34), f'{o*W:+8.0f} {n:6d}')
for N in (8, 10):
    for thr in (0.25, 0.3):
        for ph in (12, 24):
            o, n = run(N, thr, ph, mode='win')
            print(f'최근{N}건 승률 ≤{thr*100:.0f}% → {ph}h 쉼'.ljust(34), f'{o*W:+8.0f} {n:6d}')
