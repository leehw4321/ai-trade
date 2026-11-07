import numpy as np, pandas as pd, os, sys, math
S = os.path.dirname(os.path.abspath(__file__))
Z = np.load(S + r'\c15.npz', allow_pickle=True)
times = Z['times']; N = len(times)
coins = [k for k in Z.files if k != 'times']
O = np.stack([Z[m][:, 0] for m in coins], 1); H = np.stack([Z[m][:, 1] for m in coins], 1)
L = np.stack([Z[m][:, 2] for m in coins], 1); C = np.stack([Z[m][:, 3] for m in coins], 1); V = np.stack([Z[m][:, 4] for m in coins], 1)
tsi = pd.to_datetime(times)
# ---- 시장 균등지수 (15분 수익률 평균, 20종목 이상일 때)
ret = C / np.roll(C, 1, 0) - 1; ret[0] = np.nan
cnt = np.sum(~np.isnan(ret), 1)
mr = np.where(cnt >= 20, np.nanmean(np.where(np.isnan(ret), np.nan, np.clip(ret, -0.5, 0.5)), 1), np.nan)
lvl = np.nancumprod(1 + np.nan_to_num(mr, nan=0.0))
idx = pd.Series(lvl, index=tsi)
valid = ~np.isnan(mr)
def trailing(n):
    s = idx / idx.shift(n) - 1; s[~valid] = np.nan; return s.values
R3 = trailing(288)   # 3일
R1 = trailing(96)    # 24시간
fwd24 = (idx.shift(-96) / idx - 1).values
UPT = 0.03
reg = np.full(N, 'NA', dtype=object)
reg[R3 > UPT] = '상승'; reg[(R3 <= UPT) & (R3 >= -UPT)] = '횡보'; reg[R3 < -UPT] = '하락'
reg[np.isnan(R3)] = 'NA'

COST_IN = 0.0015; FEE = 0.0005; SLIP_MKT = 0.0025
def feats():
    df = {}
    c = pd.DataFrame(C, index=tsi, columns=coins); h = pd.DataFrame(H, index=tsi, columns=coins); l = pd.DataFrame(L, index=tsi, columns=coins)
    sma = c.rolling(96, min_periods=80).mean(); sd = c.rolling(96, min_periods=80).std()
    df['z'] = ((c - sma) / sd).values; df['sma'] = sma.values
    hi = h.rolling(192, min_periods=160).max(); lo = l.rolling(192, min_periods=160).min()
    df['rg_pos'] = ((c - lo) / (hi - lo)).values; df['rg_w'] = (hi / lo - 1).values; df['rg_lo'] = lo.values; df['rg_hi'] = hi.values
    df['r18'] = (c / c.shift(72) - 1).values
    d = c.diff(); up = d.clip(lower=0).rolling(56).mean(); dn = (-d.clip(upper=0)).rolling(56).mean()
    df['rsi'] = (100 - 100 / (1 + up / dn)).values
    return df
F = feats()

def exit_sim(ci, i0, p_in, tp=None, stop=0.05, stop_abs=None, guard=None, tmax=48):
    """i0=진입 봉(시가 체결). 반환 (청산봉, 순수익률). 같은 봉에서 손절과 익절이 같이 닿으면 손절 우선."""
    peak = p_in; gstop = 0.0
    hard = stop_abs if stop_abs else p_in * (1 - stop)
    for j in range(i0, min(i0 + tmax, N - 1) + 1):
        o, h, l, c = O[j, ci], H[j, ci], L[j, ci], C[j, ci]
        if np.isnan(h) or np.isnan(l): continue
        lvl = max(hard, gstop)
        if l <= lvl:
            fill = min(lvl, o) * (1 - SLIP_MKT)
            return j, fill * (1 - FEE) / (p_in * (1 + COST_IN)) - 1 + 0  # fee 포함
        if tp and h >= tp:
            return j, tp * (1 - FEE) / (p_in * (1 + COST_IN)) - 1
        peak = max(peak, h)
        if guard:
            arm, give = guard
            if peak >= p_in * (1 + arm): gstop = max(gstop, p_in * 1.003, p_in + (peak - p_in) * (1 - give))
        if j == min(i0 + tmax, N - 1):
            return j, c * (1 - SLIP_MKT) * (1 - FEE) / (p_in * (1 + COST_IN)) - 1
    return i0, 0.0

def run(name, cand_fn, exit_kw_fn, slots=5, start=None, end=None):
    """cand_fn(i) -> [(priority, ci)] 신호(봉 i 마감). 진입은 i+1 시가. exit_kw_fn(i, ci, p_in) -> kwargs"""
    open_until = {}   # ci -> exit bar
    trades = []
    for i in range(300, N - 2):
        if reg[i] == 'NA': continue
        # 슬롯 정리
        for ci in [c for c, ex in open_until.items() if ex <= i]: del open_until[ci]
        if len(open_until) >= slots: continue
        cands = cand_fn(i)
        if not cands: continue
        cands.sort()
        for pr, ci in cands:
            if len(open_until) >= slots: break
            if ci in open_until: continue
            p_in = O[i + 1, ci]
            if np.isnan(p_in) or p_in <= 0: continue
            kw = exit_kw_fn(i, ci, p_in)
            j, r = exit_sim(ci, i + 1, p_in, **kw)
            open_until[ci] = j + 1
            trades.append((i + 1, j, ci, r, reg[i]))
    return trades

def summarize(name, trades, split='2026-03-01'):
    t = pd.DataFrame(trades, columns=['i', 'j', 'ci', 'r', 'reg'])
    if t.empty: print(name, '거래 없음'); return t
    t['time'] = tsi[t['i'].values]; t['hold_h'] = (t['j'] - t['i'] + 1) * 0.25
    return t

def line(tag, t):
    if len(t) == 0: return f'{tag}: 거래 없음'
    r = t['r']; w = r[r > 0]; l = r[r <= 0]
    # 일 단위 군집 t값
    d = t.groupby(t['time'].dt.date)['r'].sum(); tv = d.mean() / (d.std() / math.sqrt(len(d))) if len(d) > 2 and d.std() > 0 else float('nan')
    return f"{tag}: n={len(t):4d} 평균 {r.mean()*100:+.2f}% 승률 {len(w)/len(r)*100:3.0f}% 이익{w.mean()*100 if len(w) else 0:+.2f}%/손실{l.mean()*100 if len(l) else 0:+.2f}% 보유 {t['hold_h'].mean():.1f}h 일군집t {tv:+.1f}"
