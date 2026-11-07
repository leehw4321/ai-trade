import json, statistics as st, datetime as dt, os
from sim import *
from r2 import r2sim
S = os.path.dirname(os.path.abspath(__file__))
M = json.load(open(S + r'\mkt.json'))
allh = sorted(set(h for v in M.values() for h in v))
idx = {}; br = {}; lvl = 100.0
for a, b in zip(allh, allh[1:]):
    rs = [M[m][b] / M[m][a] - 1 for m in M if m != 'KRW-BTC' and a in M[m] and b in M[m]]
    if len(rs) < 20: continue
    lvl *= 1 + st.mean(rs); idx[b] = lvl; br[b] = sum(r > 0 for r in rs) / len(rs)
hs = sorted(idx); pos_ = {h: i for i, h in enumerate(hs)}
T = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=None)
def mk(e):
    k = T(e['t']).replace(minute=0, second=0, microsecond=0).strftime('%Y-%m-%dT%H:%M:%S')
    return k if k in pos_ else None
def chg(k, n):
    i = pos_[k]; return idx[k] / idx[hs[i - n]] - 1 if i - n >= 0 else None
def brd(k, n=6):
    i = pos_[k]; return st.mean(br[hs[j]] for j in range(max(0, i - n + 1), i + 1))
base = lambda e: r2sim(e, T=0.05)
for e in ents:
    e['r'] = base(e)
    pre, post = path(e); p = e['price']
    lo2 = min(c[2] for _, c in pre[-120:]) if len(pre) >= 30 else None
    hi2 = max(c[1] for _, c in pre[-120:]) if len(pre) >= 30 else None
    e['up_from_low'] = p / lo2 - 1 if lo2 else None
    e['from_hi'] = p / hi2 - 1 if hi2 else None
    k = mk(e)
    e['ix6'] = chg(k, 6) if k else None; e['ix24'] = chg(k, 24) if k else None; e['br6'] = brd(k) if k else None
    e['half'] = 'A' if e['t'] < '2026-11-05' else 'B'
def show(name, keep, note=''):
    kept = [e for e in ents if keep(e)]; cut = [e for e in ents if not keep(e)]
    f = lambda L: (sum(e['r'] for e in L) * 80000, sum(e['r'] > 0 for e in L), len(L))
    k = f(kept); c = f(cut)
    ka = sum(e['r'] for e in kept if e['half'] == 'A') * 80000; kb = sum(e['r'] for e in kept if e['half'] == 'B') * 80000
    print(f"{name:34s} 매수 {k[2]:2d}건 합 {k[0]:+7.0f}원 (전반 {ka:+6.0f} 후반 {kb:+6.0f}) | 걸러진 {c[2]:2d}건 합 {c[0]:+6.0f}원 이긴 {c[1]}건 {note}")
tot = sum(e['r'] for e in ents) * 80000
print(f'기준선(현재 적용 규칙, 필터 없음): 88건 합 {tot:+.0f}원 (전반 {sum(e["r"] for e in ents if e["half"]=="A")*80000:+.0f} 후반 {sum(e["r"] for e in ents if e["half"]=="B")*80000:+.0f})\n')

print('=== 시험 1) 급등 직후 매수 제한: 직전 2시간 저점 대비 +X% 이상 올랐으면 매수 안 함 ===')
for x in (0.03, 0.04, 0.05, 0.06, 0.08):
    show(f'저점 대비 +{x*100:.0f}% 이상 제외', lambda e, x=x: e['up_from_low'] is None or e['up_from_low'] < x)
print('  [보조] 직전 2시간 고점 99% 이상(고점 부근) 제외')
show('고점 99%이상 제외', lambda e: e['from_hi'] is None or e['from_hi'] < -0.01)
print('  [보조] 직전 2시간 고점 대비 -8% 이하(꺾인 종목) 제외')
show('고점대비 -8%이하 제외', lambda e: e['from_hi'] is None or e['from_hi'] > -0.08)
print('  걸러지는 급등 매수(+5%) 상세:')
for e in ents:
    if e['up_from_low'] is not None and e['up_from_low'] >= 0.05:
        print(f"    {e['market'][4:]:9s} {e['t'][5:16]} 저점대비 +{e['up_from_low']*100:.1f}% → 결과 {e['r']*100:+.2f}%")

print('\n=== 시험 2) 교체 매도(손실 종목 팔고 새 종목 매수) ===')
E = json.load(open(S + r'\entries.json', encoding='utf-8'))
sells = E['sells']
swap = []
for e in ents:
    t = T(e['t'])
    for s in sells:
        if s['market'] != e['market'] and abs((T(s['t']) - t).total_seconds()) <= 15 and (s.get('reason', '').startswith('봇 주문안') or not s.get('reason')):
            # 손절/이익보호 사유는 제외(같은 시각 우연)
            swap.append((e, s)); break
print(f'같은 시각(15초 안)에 다른 종목을 판 매수: {len(swap)}건')
sw = {id(e) for e, s in swap}
for e, s in swap:
    ent_old = [x for x in ents if x['market'] == s['market'] and T(x['t']) < T(s['t']) and ('closed' not in x or abs((T(x['closed']) - T(s['t'])).total_seconds()) < 60)]
    oldp = ent_old[-1]['price'] if ent_old else None
    # 팔린 종목을 계속 보유했다면 4시간 뒤
    key = None
    old_f = None
    if ent_old:
        o = ent_old[-1]; pre, post = path(o)
        tt = T(s['t']).replace(second=0, microsecond=0)
        c = C[o['key']]; ks = sorted(c)
        after = [c[k][3] for k in ks if T(k) >= tt]
        old_f = after[min(240, len(after) - 1)] / s['price'] - 1 if len(after) > 5 else None
    pre, post = path(e)
    new_f = post[240][1][3] / e['price'] - 1 if len(post) > 240 else None
    print(f"  {s['market'][4:]:9s} 매도 {s['t'][5:16]} (평단 대비 {((s['price']/oldp-1)*100) if oldp else float('nan'):+.1f}%) → {e['market'][4:]:9s} 매수 | 판 종목 이후 4h {('%+.1f%%' % (old_f*100)) if old_f is not None else '-'} / 산 종목 4h {('%+.1f%%' % (new_f*100)) if new_f is not None else '-'} | 산 종목 결과(현행) {e['r']*100:+.2f}%")
show('교체 매수 전부 제외', lambda e: id(e) not in sw)
oldr = [e['r'] for e, s in swap]
print(f'  교체로 산 종목: {len(oldr)}건 평균 {st.mean(oldr)*100 if oldr else 0:+.2f}% 합 {sum(oldr)*80000:+.0f}원 / 그 외 매수 평균 {st.mean([e["r"] for e in ents if id(e) not in sw])*100:+.2f}%')

print('\n=== 시험 3) 횡보·약세장 신규 매수 축소(시장 지표로 매수 보류) ===')
cands = [
 ('시장 6h 하락(<0) 제외', lambda e: e['ix6'] is None or e['ix6'] >= 0),
 ('시장 6h < -0.3% 제외', lambda e: e['ix6'] is None or e['ix6'] >= -0.003),
 ('시장 24h 하락(<0) 제외', lambda e: e['ix24'] is None or e['ix24'] >= 0),
 ('시장 24h < -1% 제외', lambda e: e['ix24'] is None or e['ix24'] >= -0.01),
 ('상승 종목비율 6h평균 <45% 제외', lambda e: e['br6'] is None or e['br6'] >= 0.45),
 ('상승 종목비율 6h평균 <50% 제외', lambda e: e['br6'] is None or e['br6'] >= 0.50),
 ('시장 6h 상승 >+0.3%일 때만', lambda e: e['ix6'] is None or e['ix6'] > 0.003),
]
for nm, f in cands: show(nm, f)
print('  (참고) 이 표본에서 시장 지표가 계산된 진입:', sum(e['ix6'] is not None for e in ents), '/ 88  -- 지수는 10-21~ 시간봉, 40종목 균등')
