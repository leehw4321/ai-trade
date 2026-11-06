import statistics as st, datetime as dt
from sim import *
T = lambda s: dt.datetime.fromisoformat(s)
byM = {}
for e in ents: byM.setdefault(e['market'], []).append(e)
rows = []
for m, es in byM.items():
    es.sort(key=lambda e: e['t'])
    for a, b in zip(es, es[1:]):
        if 'closed' not in a: continue
        gap = (T(b['t']) - T(a['closed'])).total_seconds() / 60
        rows.append((a, b, gap))
def fwd(e, mins):
    pre, post = path(e)
    return post[mins][1][3] / e['price'] - 1 if len(post) > mins else None
def f4(e):
    x = fwd(e, 240); return x
print('같은 종목 재매수 총', len(rows), '건 (전체 진입 86건 중 첫 진입 제외)')
for lo, hi, nm in ((0, 60, '60분 안'), (60, 240, '1~4시간'), (240, 1440, '4~24시간'), (1440, 1e9, '24시간 넘음')):
    for kind in ('손실 청산 뒤', '이익 청산 뒤'):
        sel = [(a, b, g) for a, b, g in rows if lo <= g < hi and ((a['actual'] < 0) == (kind[0] == '손'))]
        if not sel: continue
        rs = [sim(b, slip=0.003)[0] for a, b, g in sel]
        fs = [x for x in (f4(b) for a, b, g in sel) if x is not None]
        px = [b['price'] / a['sold_funds'] * a['sold_vol'] / a['sold_vol'] for a, b, g in sel]
        rb = [b['price'] / (a['sold_funds'] / a['sold_vol']) - 1 for a, b, g in sel]
        print(f'{nm:8s} {kind} n={len(sel):2d} | 재매수가가 직전 매도가 대비 평균 {st.mean(rb)*100:+.1f}% | 재진입 거래 현행규칙 평균 {st.mean(rs)*100:+.2f}% 합 {sum(rs)*80000:+6.0f}원 | 4h뒤 평균 {st.mean(fs)*100 if fs else 0:+.2f}%')
print('--- 첫 진입(재진입 아님) vs 재진입 전체 ---')
first = [e for e in ents if all(e is not b for a, b, g in rows)]
re_ = [b for a, b, g in rows]
for nm, es in (('첫 진입', first), ('재진입(24h 이내)', [b for a, b, g in rows if g < 1440]), ('재진입(전체)', re_)):
    rs = [sim(e, slip=0.003)[0] for e in es]
    print(f'  {nm:14s} n={len(es):2d} 평균 {st.mean(rs)*100:+.2f}% 합 {sum(rs)*80000:+6.0f}원')
print('--- 손실 직후 4시간 안 재매수 상세 ---')
for a, b, g in rows:
    if a['actual'] < 0 and g < 240:
        print(f"  {a['market'][4:]:9s} 직전 {a['actual']*100:+.1f}% 청산 {a['closed'][5:16]} → {g:4.0f}분 뒤 재매수 {b['t'][5:16]} → 결과 {sim(b, slip=0.003)[0]*100:+.2f}%")
print('--- 이익 청산 뒤 4시간 안 재매수 상세 ---')
for a, b, g in rows:
    if a['actual'] >= 0 and g < 240:
        print(f"  {a['market'][4:]:9s} 직전 {a['actual']*100:+.1f}% 청산 {a['closed'][5:16]} → {g:4.0f}분 뒤 재매수 {b['t'][5:16]} → 결과 {sim(b, slip=0.003)[0]*100:+.2f}%")
