"""하락장에서 매수를 쉬는 필터 비교 (읽기 전용, Claude 단독 탐색 2026-11-12).

자료: research_data/candles15_long (업비트 15분봉 61종목, 2024-11 ~ 2026-11, 현재 상장 종목만 → 생존 편향).
봇 규칙 단순 재현: 매시 18시간(72봉) 상승률 순위, 0 < 상승률 < 15%, 7위 안에서 빈자리(최대 3) 채움,
  손절 −3.5%, 이익보호(+3% 도달 뒤 이익 30% 반납), 유지 9위 밖이면 정시 매도, 손실 매도 뒤 24시간 재매수 금지.
  체결은 판단 봉 종가, 편도 비용 COST(수수료+미끄러짐). 같은 봉에서 손절을 먼저 본다(보수).
필터는 매수 시점의 장세로 '그 매수를 했는가'만 가른다(필터별로 자리 경쟁을 다시 돌리지 않음 → 근사).
비교는 '필터가 켜진 때 매수의 평균'과 '꺼진 때 매수의 평균'. 평균이 음수인 전략은 아무 필터나 거래 수를 줄이면 합계가 좋아지므로
  합계보다 평균 차이(선별력)를 본다.
"""
import json
import sys
from pathlib import Path

import numpy as np

D = Path(r'C:\Users\User\Desktop\research_data\candles15_long')
COST = float(sys.argv[1]) if len(sys.argv) > 1 else 0.002
L, SCAN, KEEP, SLOTS, CAP, STOP, ARM, GIVE, BAN = 72, 7, 9, 3, 0.15, 0.035, 0.03, 0.3, 96

files = sorted(D.glob('KRW-*.jsonl'))
names = [f.stem for f in files]
raw = {}
for f in files:
    rows = [json.loads(x) for x in f.read_text(encoding='utf-8').splitlines() if x.strip()]
    raw[f.stem] = {r['t']: (r['o'], r['h'], r['l'], r['c'], r.get('val') or 0) for r in rows}
ts = sorted(set().union(*[set(v) for v in raw.values()]))
T, N = len(ts), len(names)
O = np.full((T, N), np.nan); H = O.copy(); Lo = O.copy(); C = O.copy(); V = np.zeros((T, N))
idx = {t: i for i, t in enumerate(ts)}
for j, m in enumerate(names):
    for t, (o, h, l, c, v) in raw[m].items():
        i = idx[t]; O[i, j], H[i, j], Lo[i, j], C[i, j], V[i, j] = o, h, l, c, v
del raw
# 결측은 직전 종가로 채워 순위·지수 계산(거래는 실제 봉이 있을 때만)
Cf = C.copy()
for i in range(1, T):
    m = np.isnan(Cf[i]); Cf[i, m] = Cf[i - 1, m]
bj = names.index('KRW-BTC')


def ret(arr, k):
    out = np.full_like(arr, np.nan)
    out[k:] = arr[k:] / arr[:-k] - 1
    return out


R72 = ret(Cf, L)
btc = Cf[:, bj]
day = 96
feat = {
    'BTC 50일선 위': btc > np.array([np.nanmean(btc[max(0, i - 50 * day):i + 1]) if i >= 50 * day else np.nan for i in range(T)]),
}
# 이동평균은 누적합으로 빠르게
def sma(x, n):
    cs = np.nancumsum(np.nan_to_num(x)); out = np.full_like(x, np.nan)
    out[n - 1:] = (cs[n - 1:] - np.concatenate([[0], cs[:-n]])) / n
    return out
feat = {}
feat['BTC 20일선 위'] = btc > sma(btc, 20 * day)
feat['BTC 50일선 위'] = btc > sma(btc, 50 * day)
feat['BTC 7일 상승'] = ret(btc[:, None], 7 * day)[:, 0] > 0
feat['BTC 24시간 상승'] = ret(btc[:, None], day)[:, 0] > 0
ew24 = np.nanmean(ret(Cf, day), axis=1); ew7d = np.nanmean(ret(Cf, 7 * day), axis=1)
feat['균등지수 24시간 상승'] = ew24 > 0
feat['균등지수 7일 상승'] = ew7d > 0
above20 = np.nanmean(Cf > np.column_stack([sma(Cf[:, j], 20 * day) for j in range(N)]), axis=1)
feat['20일선 위 종목 50% 이상'] = above20 >= 0.5

trades = []
pos = {}           # j -> dict(entry, peak, i0)
ban = {}
for i in range(L + 1, T):
    # 15분마다 손절·이익보호
    for j in list(pos):
        if np.isnan(Lo[i, j]): continue
        p = pos[j]
        if Lo[i, j] <= p['e'] * (1 - STOP):
            px = min(p['e'] * (1 - STOP), O[i, j]); why = '손절'
        else:
            p['pk'] = max(p['pk'], H[i, j])
            if p['pk'] >= p['e'] * (1 + ARM) and C[i, j] <= p['e'] + (p['pk'] - p['e']) * (1 - GIVE):
                px = C[i, j]; why = '이익보호'
            else:
                continue
        r = px / p['e'] * (1 - COST) / (1 + COST) - 1
        trades.append((p['i0'], names[j], r, why, i)); pos.pop(j)
        if r < 0: ban[j] = i + BAN
    if not ts[i].endswith(':45:00'):   # 정시 판단 = :45 봉 마감
        continue
    rs = R72[i]
    order = [j for j in np.argsort(-np.nan_to_num(rs, nan=-9)) if not np.isnan(rs[j]) and not np.isnan(C[i, j])]
    rank = {j: k + 1 for k, j in enumerate(order)}
    for j in list(pos):
        if rank.get(j, 99) > KEEP or rs[j] < 0:
            p = pos.pop(j); r = C[i, j] / p['e'] * (1 - COST) / (1 + COST) - 1 if not np.isnan(C[i, j]) else 0
            trades.append((p['i0'], names[j], r, '순위', i))
            if r < 0: ban[j] = i + BAN
    for j in order[:SCAN]:
        if len(pos) >= SLOTS: break
        if j in pos or ban.get(j, 0) > i or not (0 < rs[j] < CAP): continue
        pos[j] = {'e': C[i, j], 'pk': C[i, j], 'i0': i}

tr = np.array([t[2] for t in trades]); ent = np.array([t[0] for t in trades])
yrs = np.array([ts[i][:4] for i in ent])
print(f'편도 비용 {COST:.2%} · 기간 {ts[0][:10]} ~ {ts[-1][:10]} · 거래 {len(tr)} · 평균 {tr.mean():+.3%} · 승률 {(tr > 0).mean():.0%}')
bh = np.nanmean(Cf[-1] / Cf[L] - 1); fb = btc[~np.isnan(btc)]; print(f'같은 기간 61종목 균등 보유 {bh:+.1%}, BTC {fb[-1] / fb[0] - 1:+.1%}')
for y in sorted(set(yrs)):
    m = yrs == y; print(f'  {y}: {m.sum()}건 평균 {tr[m].mean():+.3%} 승률 {(tr[m] > 0).mean():.0%}')
# 자기 성과 필터: 직전 10건(매수 시점에 이미 끝난 거래) 합이 음수면 쉼
# 자기 성과 필터: 매수 시점에 이미 끝난 직전 10건(필터 없는 전체 거래 기준 = 그림자 장부로 구현 가능)의 합이 음수면 쉼
import bisect
cl = sorted((t[4], t[2]) for t in trades); cl_t = [c[0] for c in cl]; cum = np.cumsum([0] + [c[1] for c in cl])
def own_ok(i0, n=10):
    k = bisect.bisect_left(cl_t, i0)      # i0보다 먼저 끝난 거래 수(같은 봉 제외)
    return True if k < n else (cum[k] - cum[k - n]) > 0
own = np.array([own_ok(t[0]) for t in trades])
own20 = np.array([own_ok(t[0], 20) for t in trades])
feat_tr = {name: np.array([bool(f[i]) if not np.isnan(f[i]) else True for i in ent]) for name, f in
           ((n, v.astype(float)) for n, v in feat.items())}
feat_tr['직전 10건 합 플러스(자기 성과)'] = own
feat_tr['직전 20건 합 플러스(자기 성과)'] = own20
print('\n필터 | 켜진 때 매수(거래함) | 꺼진 때 매수(쉼) | 평균 차이 | 거래 비율')
for n, on in feat_tr.items():
    a, b = tr[on], tr[~on]
    print(f'{n:22} | {len(a):5}건 평균 {a.mean():+.3%} 승 {(a > 0).mean():.0%} | {len(b):5}건 평균 {b.mean() if len(b) else 0:+.3%} 승 {(b > 0).mean() if len(b) else 0:.0%} | {a.mean() - (b.mean() if len(b) else 0):+.3%}p | {on.mean():.0%}')
    for y in sorted(set(yrs)):
        m = yrs == y
        if (on & m).sum() and (~on & m).sum():
            print(f'    {y}: 켜짐 {(on & m).sum()}건 {tr[on & m].mean():+.3%} / 꺼짐 {(~on & m).sum()}건 {tr[~on & m].mean():+.3%}')
