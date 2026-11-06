import statistics as st, datetime as dt
from sim import *
T = lambda s: dt.datetime.fromisoformat(s)
def rep(name, fn, es=None):
    es = es or ents
    a = [fn(e) for e in es if e['t'] < '2026-11-05']; b = [fn(e) for e in es if e['t'] >= '2026-11-05']
    z = [fn(e) for e in es if e['t'] >= '2026-11-06T18:00']
    al = a + b
    print(f"{name:34s} 전체 {sum(al)*80000:+7.0f}원 | 전반 {sum(a)*80000:+6.0f} 후반 {sum(b)*80000:+6.0f} | 어제18시~ {sum(z)*80000:+6.0f}")
print('=== 이익보호: 시작 수익률(arm) × 반납비율(give) , 잠금 없음, 손절4.5%, slip0.3% ===')
for arm in (0.02, 0.03, 0.04, 0.05):
    for give in (0.2, 0.3, 0.4, 0.5):
        rep(f'시작+{arm*100:.0f}% 반납{give*100:.0f}%', lambda e, arm=arm, give=give: sim(e, slip=0.003, lock=None, arm=arm, give=give)[0])
print('현행 설정은 시작+3%·반납30%')

print('\n=== 재매수 제한 검토: 같은 종목을 판 뒤 4시간 안에, 직전 매도가보다 비싸면 매수 안 함 ===')
byM = {}
for e in ents: byM.setdefault(e['market'], []).append(e)
blocked, allowed = [], []
for m, es in byM.items():
    es.sort(key=lambda e: e['t'])
    for a, b in zip(es, es[1:]):
        if 'closed' not in a: continue
        gap = (T(b['t']) - T(a['closed'])).total_seconds() / 60
        last = a['sells'][-1][1]
        if gap <= 240:
            (blocked if b['price'] > last else allowed).append((a, b, gap, b['price'] / last - 1))
print(f'4시간 안 재매수 {len(blocked)+len(allowed)}건: 매도가보다 비싼 {len(blocked)}건(제한 대상), 싼 {len(allowed)}건')
for nm, L in (('매도가보다 비싸게 재매수(제한 대상)', blocked), ('매도가보다 싸게 재매수(허용)', allowed)):
    for rule, fn in (('현행', lambda e: sim(e, slip=0.003)[0]), ('잠금 끔', lambda e: sim(e, slip=0.003, lock=None)[0])):
        rs = [fn(b) for a, b, g, d in L]
        if rs: print(f'  {nm} n={len(rs)} [{rule}] 평균 {st.mean(rs)*100:+.2f}% 합 {sum(rs)*80000:+6.0f}원  이긴 {sum(r>0 for r in rs)}건')
print('상세(제한 대상):')
for a, b, g, d in blocked:
    print(f"  {b['market'][4:]:9s} 직전 {a['actual']*100:+.1f}%청산 {g:4.0f}분 뒤 매도가 대비 {d*100:+.1f}% 비싸게 재매수 → 현행 {sim(b, slip=0.003)[0]*100:+.2f}% / 잠금끔 {sim(b, slip=0.003, lock=None)[0]*100:+.2f}%")
print('상세(허용):')
for a, b, g, d in allowed:
    print(f"  {b['market'][4:]:9s} 직전 {a['actual']*100:+.1f}%청산 {g:4.0f}분 뒤 매도가 대비 {d*100:+.1f}% 싸게 재매수 → 현행 {sim(b, slip=0.003)[0]*100:+.2f}% / 잠금끔 {sim(b, slip=0.003, lock=None)[0]*100:+.2f}%")
# 제한 적용 시 전체
bl = {id(b) for a, b, g, d in blocked}
kept = [e for e in ents if id(e) not in bl]
rep('전체(현행, 제한 없음)', lambda e: sim(e, slip=0.003)[0])
rep('전체(현행, 제한 적용)', lambda e: sim(e, slip=0.003)[0], kept)
rep('전체(잠금끔, 제한 없음)', lambda e: sim(e, slip=0.003, lock=None)[0])
rep('전체(잠금끔, 제한 적용)', lambda e: sim(e, slip=0.003, lock=None)[0], kept)
# 판 가격보다 싼 것만 허용 + 손실/이익 구분 없이 4h
