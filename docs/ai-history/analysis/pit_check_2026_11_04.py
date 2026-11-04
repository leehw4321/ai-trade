import json
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

sys.path.insert(0, r'C:\Users\User\Desktop\ai-trade\tools\research')
import pit_universe as pit

t0 = time.time()
data = pit.load_dir(r'C:\Users\User\Desktop\research_data\candles15')
print('markets', len(data), 'load sec', round(time.time() - t0))
firsts = {m: min(b) for m, b in data.items()}
lasts = {m: max(b) for m, b in data.items()}
allmin, allmax = min(firsts.values()), max(lasts.values())
fmt = lambda e: datetime.utcfromtimestamp(e).strftime('%Y-%m-%d %H:%M')
print('range', fmt(allmin), '->', fmt(allmax), 'days', round((allmax - allmin) / 86400, 1))
counts = sorted(len(b) for b in data.values())
print('bars per market min/median/max', counts[0], counts[len(counts) // 2], counts[-1])
full = sum(1 for m, b in data.items() if firsts[m] <= allmin + 2 * 86400)
print('markets with data from the start window:', full, '| listed later:', len(data) - full)
stale = [m for m, l in lasts.items() if l < allmax - 3600]
print('markets whose last bar is >1h before the end (stopped trading / delisted-like):', len(stale), stale[:8])
# 거래 공백(결측) 비율: 각 종목의 [첫 봉, 마지막 봉] 구간 대비 존재 비율
gaps = []
for m, b in data.items():
    span = (lasts[m] - firsts[m]) // pit.BAR + 1
    gaps.append((len(b) / span, m))
gaps.sort()
print('lowest completeness (ratio of bars present in own span):', [(round(r, 2), m) for r, m in gaps[:5]])
print('median completeness', round(gaps[len(gaps) // 2][0], 3))
# 시점별 유니버스 크기와 안정성
for label, T in (('start+30d', allmin + 30 * 86400), ('mid', (allmin + allmax) // 2), ('end', allmax + pit.BAR)):
    T = T // pit.BAR * pit.BAR
    u = pit.universe_at(data, T, top_n=30)
    elig = pit.universe_at(data, T, top_n=10_000)
    print(label, fmt(T), 'eligible markets', len(elig), 'top3', [m[4:] for m, _ in u[:3]])
# 최근 시점 순위 vs 거래소 24h 거래대금 순위(현재 ticker)
T = (allmax + pit.BAR) // pit.BAR * pit.BAR
mine = [m for m, _ in pit.universe_at(data, T, top_n=30)]
with urllib.request.urlopen('https://api.upbit.com/v1/ticker/all?quote_currencies=KRW', timeout=15) as r:
    tick = json.loads(r.read())
exch = [t['market'] for t in sorted((t for t in tick if t['market'] not in pit.EXCLUDE), key=lambda t: -t['acc_trade_price_24h'])[:30]]
print('overlap of reconstructed top30 and exchange top30 at the latest bar:', len(set(mine) & set(exch)), '/30')
print('only mine:', sorted(set(mine) - set(exch))[:8], '| only exchange:', sorted(set(exch) - set(mine))[:8])
# 결정성: 같은 T에서 두 번 계산해 동일
print('deterministic:', pit.universe_at(data, T, 30) == pit.universe_at(data, T, 30))
# 미래 정보 누출 점검: T 이후 값을 바꿔도 결과가 변하지 않는다
import copy
alt = {m: dict(b) for m, b in list(data.items())[:60]}
for m in alt:
    for s in list(alt[m]):
        if s >= T - pit.BAR + pit.BAR:  # 마감이 T 이후인 봉
            o = alt[m][s]; alt[m][s] = (o[0], o[1], o[2], o[3], o[4] * 1000)
sub = {m: data[m] for m in alt}
print('future-bar perturbation leaves ranking unchanged:', pit.universe_at(sub, T - 10 * pit.BAR, 30) == pit.universe_at(alt, T - 10 * pit.BAR, 30))
