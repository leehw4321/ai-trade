import sys
from collections import defaultdict

sys.path.insert(0, r'C:\Users\User\Desktop\ai-trade\tools\research')
import pit_universe as pit
import study_reversal as sr

data = pit.load_dir(sr.DATA_DIR, cutoff=pit.to_epoch(sr.CUTOFF_KST))
t_start, dev_end, lock_end = sr.grid(data)
events = sr.build_events(data, t_start, lock_end)
by_t = defaultdict(list)
for e in events:
    by_t[e['T']].append(e['fwd'] + sr.COST)  # 비용 전 총수익
cum, path = 1.0, []
for T in sorted(by_t):
    cum *= 1 + sum(by_t[T]) / len(by_t[T])
    path.append((T, cum))
print('상위30 균등 지수(4시간 리밸런싱, 비용 전) 누적: 시작 1.00 → 개발 끝', round(next(c for T, c in path if T >= dev_end), 3), '→ 끝', round(path[-1][1], 3))
for m in ('KRW-BTC', 'KRW-ETH', 'KRW-XRP'):
    b = data[m]
    ks = sorted(k for k in b if t_start - pit.BAR <= k)
    print(m, '구간 시작→끝 가격 변화', f'{(b[ks[-1]][3] / b[ks[0]][3] - 1) * 100:+.1f}%', '(시작', pit.from_epoch_kst(ks[0]), ')')
n_up = sum(1 for T, rows in by_t.items() if sum(rows) / len(rows) > 0)
print('4시간 균등 평균 총수익이 양수인 시점 비율:', f'{n_up / len(by_t):.1%}', '시점 수', len(by_t))
