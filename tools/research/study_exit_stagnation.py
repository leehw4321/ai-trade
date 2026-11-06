import json, datetime as dt, statistics as S
from pathlib import Path
KST = dt.timezone(dt.timedelta(hours=9)); FEE = 0.0005
P = json.load(open(Path(__file__).with_name('exit_study_out.json'), encoding='utf-8'))['positions']
f = lambda t: dt.datetime.fromtimestamp(t, KST).strftime('%m-%d %H:%M')

rows = []
for p in P:
    e = p['entry']; be = e / (1 - FEE)              # 수수료 포함 본전 매도가
    hold = [b for b in p['bars'] if p['open'] - 900 <= b[0] < p['close']]
    after = [b for b in p['bars'] if b[0] >= p['close']]
    if not hold: continue
    hi = [(b[0], b[2]) for b in hold if b[0] + 900 > p['open']]
    mfe_t, mfe = max(hi, key=lambda x: x[1])
    above = [b[0] for b in hold if b[4] >= be]       # 종가가 본전 이상이었던 봉
    r = p['pnl'] / p['invest']
    rows.append({**{k: p[k] for k in ('m', 'open', 'close', 'pnl', 'invest')}, 'r': r, 'e': e, 'be': be, 'hold_h': (p['close'] - p['open']) / 3600,
                 'mfe': mfe / e - 1, 'mfe_h': (mfe_t - p['open']) / 3600, 'last_be_h': ((max(above) + 900 - p['open']) / 3600) if above else 0,
                 'post_hi': (max(b[2] for b in after) / p['exit_px'] - 1) if after else None,
                 'post_24': (after[-1][4] / p['exit_px'] - 1) if after else None, 'hold': hold})

W = [x for x in rows if x['pnl'] > 0]; L = [x for x in rows if x['pnl'] <= 0]
print(f"포지션 {len(rows)} · 이익 {len(W)} {sum(x['pnl'] for x in W):+,.0f}원 · 손실 {len(L)} {sum(x['pnl'] for x in L):+,.0f}원 · 합 {sum(x['pnl'] for x in rows):+,.0f}원")
md = lambda v: S.median(v) if v else float('nan')
for name, g in (('이익', W), ('손실', L)):
    print(f"[{name}] 보유 중앙 {md([x['hold_h'] for x in g]):.1f}h · 최고수익(MFE) 중앙 {md([x['mfe'] for x in g])*100:.2f}% · MFE 도달 중앙 {md([x['mfe_h'] for x in g]):.1f}h · 마지막 본전 위 중앙 {md([x['last_be_h'] for x in g]):.1f}h")
print('\n손실 포지션 상세 (MFE=보유 중 최고 수익, 본전위마지막=진입 뒤 마지막으로 종가가 본전 이상이던 시각, 이후=매도 뒤 24h)')
print(f"{'종목':10}{'진입':12}{'보유h':>6}{'손익':>8}{'수익률':>7}{'MFE':>7}{'MFE시각h':>8}{'본전위마지막h':>12}{'매도뒤최고':>9}{'24h뒤':>7}")
for x in sorted(L, key=lambda x: x['open']):
    print(f"{x['m'][4:]:10}{f(x['open']):12}{x['hold_h']:6.1f}{x['pnl']:8.0f}{x['r']*100:6.2f}%{x['mfe']*100:6.2f}%{x['mfe_h']:8.1f}{x['last_be_h']:12.1f}"
          f"{(x['post_hi'] or 0)*100:8.2f}%{(x['post_24'] or 0)*100:6.2f}%")

# 정체 탈출 규칙: 진입 T시간 뒤까지 MFE가 m 미만이면 본전×(1+k)에 지정가 매도. 실제 매도 전에 고가가 닿으면 체결, 아니면 실제 결과
def sim(T, m, k, rows=rows):
    tot = 0; n = hit = 0; chg = []
    for x in rows:
        b = x['hold']; t0 = x['open'] + T * 3600
        if t0 >= x['close']: tot += x['pnl']; continue
        pre = [y for y in b if y[0] + 900 <= t0]
        mf = max([y[2] for y in pre] or [x['e']]) / x['e'] - 1
        if mf >= m: tot += x['pnl']; continue
        n += 1; L = x['be'] * (1 + k)
        post = [y for y in b if y[0] + 900 > t0]
        # 걸어 두는 순간 이미 가격이 L 이상이면 그 가격 근처에서 즉시 체결로 본다(보수적으로 L)
        if any(y[2] >= L for y in post):
            hit += 1; v = x['invest'] * (L * (1 - FEE) / x['e'] - 1)
        else:
            v = x['pnl']
        chg.append(v - x['pnl']); tot += v
    return tot, n, hit, sum(chg), sum(c for c in chg if c < 0)

base = sum(x['pnl'] for x in rows)
print(f'\n정체 지정가 탈출 시뮬레이션 (실제 합 {base:+,.0f}원). 조건: 진입 T시간까지 최고수익 m 미만 → 본전(수수료 포함)×(1+k) 지정가')
print(f"{'T(h)':>5}{'m':>6}{'k':>7}{'해당':>5}{'체결':>5}{'변화':>9}{'(그중 손해)':>11}")
for T in (2, 4, 6, 8, 12):
    for m in (0.01, 0.02):
        for k in (-0.005, 0.0, 0.003, 0.005, 0.01):
            tot, n, hit, d, neg = sim(T, m, k)
            print(f"{T:5}{m*100:5.0f}%{k*100:6.1f}%{n:5}{hit:5}{d:+9,.0f}{neg:+11,.0f}")

# 시간대 분할 안정성: 앞 절반/뒤 절반
mid = sorted(x['open'] for x in rows)[len(rows) // 2]
A = [x for x in rows if x['open'] < mid]; B = [x for x in rows if x['open'] >= mid]
print('\n앞/뒤 절반 분할(변화액): T,m,k → 앞 | 뒤')
for T, m, k in ((4, .01, 0.0), (6, .01, 0.0), (6, .01, .003), (8, .01, .003), (4, .02, .003), (6, .02, 0.0), (8, .02, 0.0)):
    print(T, m, k, f"{sim(T,m,k,A)[3]:+,.0f} | {sim(T,m,k,B)[3]:+,.0f}")

# 자정 전후 매도
print('\n11-05 21:00~11-06 01:00 매도')
for x in rows:
    if dt.datetime(2026, 11, 5, 21, tzinfo=KST).timestamp() <= x['close'] < dt.datetime(2026, 11, 6, 1, tzinfo=KST).timestamp():
        print(x['m'], f(x['close']), f"{x['pnl']:+.0f}원 {x['r']*100:+.2f}%", f"매도 뒤 최고 {(x['post_hi'] or 0)*100:+.2f}% · 지금(마지막 봉) {(x['post_24'] or 0)*100:+.2f}%")
