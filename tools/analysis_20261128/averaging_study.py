"""추세 좋은 종목이 단기로 밀릴 때 일부 물타기(추가 매수)로 평균 단가를 낮춰 버티는 구조 점검 (2026-11-28 사용자 요청, 분석만).

실거래에 맞춘 포트폴리오 시뮬레이션(gap_study S3: 자리 3·순위 매도·재매수 대기, 15분봉)에 물타기를 더한다.
 - 보유 종목이 첫 매수가 대비 −D 에 닿고, 직전 15분봉 마감 기준 '추세 좋음'이면 첫 금액의 f배를 지정가(−D)로 추가 매수(1회)
 - 손절: (keep) 원래 손절가 그대로 / (avg) 평균 단가 기준으로 다시 설정
 - 이익보호(+at 뒤 give 반납)·순위 매도는 평균 단가 기준
결과는 '첫 매수 금액 1단위' 기준 손익(물타기 금액 포함)과, 넣은 돈 대비 수익, 최악의 1건을 본다. 비용 왕복 0.3%.
"""
import datetime as dt, sys, itertools
import numpy as np
import gap_study as G, entry_rule_study as S
sys.path.insert(0, str(G.HERE.parent / 'analysis_20261111')); import stop035_review as R
import regime_pause_study as P

COST = 0.003


def run(ms, grid, O, H, L, C, V, si, ei, score, stop, at, give, D=None, f=0.0, cond=None, mode='keep', M=9, scan=7, slots=3, U=None):
    held, last, trades = {}, {}, []
    for i in range(si, ei):
        for j in list(held):
            h = held[j]
            if i <= h['i0']: continue
            # 물타기(1회): 저가가 −D 에 닿고 직전 봉 마감 기준 추세 좋음
            if D and not h['added'] and L[j, i] <= h['e1'] * (1 - D) and cond(j, i - 1):
                px = min(h['e1'] * (1 - D), O[j, i])
                h['added'] = True; h['u'] += f; h['cost'] += f * px
                if mode == 'avg': h['stop'] = h['cost'] / h['u'] * (1 - stop)
            # 불타기(1회): 고가가 +U 에 닿고 직전 봉 마감 기준 추세 좋음 → 그 가격에 f배 추가, 손절가는 평균 단가 기준과 원래 중 높은 쪽
            if U and not h['added'] and H[j, i] >= h['e1'] * (1 + U) and cond(j, i - 1):
                px = max(h['e1'] * (1 + U), O[j, i])
                h['added'] = True; h['u'] += f; h['cost'] += f * px
                h['stop'] = max(h['stop'], h['cost'] / h['u'] * (1 - stop))
            avg = h['cost'] / h['u']; out = None
            if L[j, i] <= h['stop']: out = min(h['stop'], O[j, i])
            else:
                h['peak'] = max(h['peak'], C[j, i])
                if h['peak'] >= avg * (1 + at) and C[j, i] <= h['peak'] - give * (h['peak'] - avg): out = C[j, i]
            if out is not None:
                fin(trades, last, held, j, i, out)
        if grid[i].minute != 45: continue
        v24 = np.nansum(V[:, i - 95:i + 1], axis=1)
        ok = ~np.isnan(C[:, i - 288]) & ~np.isnan(C[:, i]) & np.array([m not in P.EXCL for m in ms])
        uni = [j for j in np.argsort(-np.where(ok, v24, -1))[:30] if ok[j]]
        with np.errstate(all='ignore'):
            sc = score(i)
        rs, r4 = C[:, i] / C[:, i - 72] - 1, C[:, i] / C[:, i - 16] - 1
        pool = [j for j in dict.fromkeys(uni + list(held)) if np.isfinite(sc[j])]
        order = sorted(pool, key=lambda j: -sc[j]); rank = {j: k + 1 for k, j in enumerate(order)}
        for j in list(held):
            h = held[j]
            if rank.get(j, 99) > M:
                h.setdefault('out', i)
                if C[j, i] >= h['cost'] / h['u'] or i - h['out'] >= 16: fin(trades, last, held, j, i, C[j, i])
            else: h.pop('out', None)
        free = slots - len(held)
        for j in order[:scan]:
            if free <= 0: break
            if j in held or j not in uni: continue
            if score is SC_A and not (0 < rs[j] < .2 and r4[j] > -.1): continue
            if j in last:
                si_, sp, prof = last[j]; hrs = (i - si_) / 4
                if hrs < 24 and (not prof or hrs < 4 or C[j, i] > sp * (1 - 0.5 * G.atr_pct(j, i, H, L, C))): continue
            held[j] = dict(e1=C[j, i], i0=i, u=1.0, cost=C[j, i], peak=C[j, i], stop=C[j, i] * (1 - stop), added=False); free -= 1
    return trades


def fin(trades, last, held, j, i, px):
    h = held.pop(j)
    pnl = h['u'] * px - h['cost'] - COST / 2 * (h['u'] * px + h['cost'])
    trades.append(dict(pnl=pnl / h['e1'], cap=h['u'], i0=h['i0'], added=h['added']))
    last[j] = (i, px, px > h['cost'] / h['u'])


def main():
    global SC_A
    ms, grid, O, H, L, C, V = S.load(); t0 = grid[0]; n = len(grid)
    trips, _, _ = R.load()
    si = int((min(t['open'] for t in trips).astimezone(dt.timezone.utc) - t0).total_seconds() // 900); ei = n - 97; mid = (si + ei) // 2
    SC_A = lambda i: C[:, i] / C[:, i - 72] - 1
    SC_B = lambda i: -(np.nanmax(H[:, i - 15:i + 1], axis=1) / np.nanmin(L[:, i - 15:i + 1], axis=1)) / (np.nanmax(H[:, i - 95:i + 1], axis=1) / np.nanmin(L[:, i - 95:i + 1], axis=1))
    CONDS = {'추세 무관': lambda j, i: True,
             '18시간 +5%↑': lambda j, i: C[j, i] / C[j, i - 72] - 1 >= 0.05,
             '20시간 평균선 위': lambda j, i: C[j, i] > np.nanmean(C[j, i - 79:i + 1]),
             '72시간 상승': lambda j, i: C[j, i] > C[j, i - 288]}
    for sname, score, stop, at, give in (('A 지금 방식', SC_A, 0.035, 0.03, 0.30), ('B 변동성 수축', SC_B, 0.025, 0.08, 0.50)):
        base = run(ms, grid, O, H, L, C, V, si, ei, score, stop, at, give)
        bp = np.array([t['pnl'] for t in base])
        print(f'\n## {sname} (손절 {stop:.1%}·+{at:.0%} 뒤 {give:.0%}) 물타기 없음: {len(bp)}건 합계 {bp.sum() * 100:+.0f}%p, 1건 {bp.mean():+.2%}, 최악 {bp.min():+.2%}, '
              f'앞/뒤 {bp[[t["i0"] < mid for t in base]].sum() * 100:+.0f}/{bp[[t["i0"] >= mid for t in base]].sum() * 100:+.0f}%p')
        print('| 물타기 시점 | 추가 금액 | 추세 조건 | 손절 | 물타기 건 | 합계(첫 금액 기준 %p) | 물타기 없음 대비 | 앞/뒤 대비 | 넣은 돈 대비 1건 | 최악 1건 |')
        print('|---|---|---|---|---|---|---|---|---|---|')
        rows = []
        for D, f, (cn, cf), mode in itertools.product((0.015, 0.025) if stop == 0.035 else (0.01, 0.015), (0.5, 1.0), CONDS.items(), ('keep', 'avg')):
            tr = run(ms, grid, O, H, L, C, V, si, ei, score, stop, at, give, D=D, f=f, cond=cf, mode=mode)
            p = np.array([t['pnl'] for t in tr]); cap = np.array([t['cap'] for t in tr])
            a = p[[t['i0'] < mid for t in tr]].sum() - bp[[t['i0'] < mid for t in base]].sum()
            b = p[[t['i0'] >= mid for t in tr]].sum() - bp[[t['i0'] >= mid for t in base]].sum()
            rows.append((p.sum() - bp.sum(), f"| −{D:.1%} | ×{f:g} | {cn} | {'원래 손절가' if mode == 'keep' else '평균 단가 기준'} | {sum(t['added'] for t in tr)} | {p.sum() * 100:+.0f} | **{(p.sum() - bp.sum()) * 100:+.0f}** | {a * 100:+.0f}/{b * 100:+.0f} | {(p / cap).mean():+.2%} | {p.min():+.2%} |"))
        for _, r in sorted(rows, key=lambda x: -x[0])[:12]: print(r)
        print('(하위 3)')
        for _, r in sorted(rows, key=lambda x: -x[0])[-3:]: print(r)
        print('불타기(오를 때 추가)')
        for Uu, f, (cn, cf) in itertools.product((0.02, 0.03, 0.05), (0.5, 1.0), CONDS.items()):
            tr = run(ms, grid, O, H, L, C, V, si, ei, score, stop, at, give, U=Uu, f=f, cond=cf)
            p = np.array([t['pnl'] for t in tr]); cap = np.array([t['cap'] for t in tr])
            a = p[[t['i0'] < mid for t in tr]].sum() - bp[[t['i0'] < mid for t in base]].sum()
            b = p[[t['i0'] >= mid for t in tr]].sum() - bp[[t['i0'] >= mid for t in base]].sum()
            print(f"| +{Uu:.0%} | ×{f:g} | {cn} | 평균 단가 기준(올리기만) | {sum(t['added'] for t in tr)} | {p.sum() * 100:+.0f} | **{(p.sum() - bp.sum()) * 100:+.0f}** | {a * 100:+.0f}/{b * 100:+.0f} | {(p / cap).mean():+.2%} | {p.min():+.2%} |")


if __name__ == '__main__':
    main()
