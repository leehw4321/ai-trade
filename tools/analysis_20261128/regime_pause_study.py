"""하락장 신규 매수 중단 정밀 점검 (2026-11-28 사용자: "하락장 매수 중단을 정밀하게 분석").

읽기 전용, 주문 없음.
A) 실거래 왕복(stop035_review.load, 11-02~) 각 매수 시각의 장세 값(직전 마감 15분봉 기준, 봇과 같은 방식: 거래대금 상위 30 균등 평균)
   - 지수 24시간 / 6시간 / 4시간 등락, 상승 종목 비율(24시간)
   - 기준별로 "이 값 이하이면 사지 않았다면": 빠지는 거래의 손익, 남는 거래의 손익, 앞/뒤 절반, 부트스트랩 구간
   - 봇 자신의 기록(data/regime_watch.jsonl, 11-06~)과 재계산 값이 맞는지 확인
B) 시뮬레이션(entry_rule_study의 지금 규칙 근사, 매시 상위 3)에 같은 기준 적용
한계: 매수를 쉬면 그 자리에 나중에 다른 종목을 샀을 수 있는데(자리·현금) 이 효과는 넣지 않았다.
"""
import datetime as dt, json, sys
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / 'analysis_20261111'))
import entry_rule_study as S
import stop035_review as R

KST, UTC = S.KST, dt.timezone.utc
EXCL = {'KRW-USDT', 'KRW-USDC', 'KRW-USDE', 'KRW-USD1', 'KRW-DAI'}
RNG = np.random.default_rng(7)


def regime_at(i, ms, C, V):
    """봉 i 마감 시점 장세 값들(봇처럼 24시간 거래대금 상위 30, 스테이블 5종 제외)."""
    v24 = np.nansum(V[:, i - 95:i + 1], axis=1)
    ok = ~np.isnan(C[:, i]) & ~np.isnan(C[:, i - 96]) & np.array([m not in EXCL for m in ms])
    cand = [j for j in np.argsort(-np.where(ok, v24, -1))[:30] if ok[j]]
    r = lambda k: C[cand, i] / C[cand, i - k] - 1
    return {'지수 24시간': float(np.mean(r(96))), '지수 6시간': float(np.mean(r(24))), '지수 4시간': float(np.mean(r(16))),
            '상승 종목 비율 24시간': float(np.mean(r(96) > 0))}


THRESH = {'지수 24시간': [-0.03, -0.02, -0.01, 0.0, 0.01], '지수 6시간': [-0.02, -0.01, 0.0, 0.01],
          '지수 4시간': [-0.015, -0.01, -0.005, 0.0], '상승 종목 비율 24시간': [0.2, 0.3, 0.4, 0.5]}


def boot(x, n=4000):
    if len(x) < 5: return (np.nan, np.nan)
    m = [np.mean(RNG.choice(x, len(x))) for _ in range(n)]
    return np.percentile(m, 2.5), np.percentile(m, 97.5)


def main():
    ms, grid, O, H, L, C, V = S.load()
    t0 = grid[0]; n = len(grid)
    now = dt.datetime.now(KST)
    lines = [f'# 하락장 신규 매수 중단 정밀 점검 ({now:%Y-%m-%d %H:%M} KST)', '']
    # ---- A. 실거래
    trips, _, _ = R.load()
    rows = []
    for t in trips:
        i = int((t['open'].astimezone(UTC) - t0).total_seconds() // 900) - 1
        if i < 100 or i >= n: continue
        rows.append(dict(regime_at(i, ms, C, V), pnl=t['pnl'], ret=t['ret'], open=t['open'], stop=t['stop']))
    # 봇 기록과 비교
    rw = {}
    for line in open(R.DATA / 'regime_watch.jsonl', encoding='utf-8'):
        try: x = json.loads(line); rw[x['at'][:13]] = x['index_24h']
        except (ValueError, KeyError): pass
    pair = []
    for r in rows:
        k = (r['open'].astimezone(KST)).strftime('%Y-%m-%dT%H')
        if k in rw and rw[k] is not None: pair.append((r['지수 24시간'], rw[k]))
    corr = np.corrcoef(*zip(*pair))[0, 1] if len(pair) > 5 else np.nan
    mid = sorted(r['open'] for r in rows)[len(rows) // 2]
    tot = sum(r['pnl'] for r in rows); days = (max(r['open'] for r in rows) - min(r['open'] for r in rows)).total_seconds() / 86400
    lines += ['## A. 실거래 왕복으로 본 효과', '',
              f'- 대상: 실거래 왕복 {len(rows)}건 ({min(r["open"] for r in rows):%m-%d} ~ {max(r["open"] for r in rows):%m-%d}, {days:.1f}일), 손익 합계 **{tot:+,.0f}원**, 1건 평균 {np.mean([r["ret"] for r in rows]):+.2%}',
              f'- 장세 값 재계산 확인: 봇 기록(regime_watch, 같은 시각 정시)과 상관 {corr:.2f} ({len(pair)}건)', '',
              '| 이 값 이하이면 매수 안 함 | 빠지는 거래 | 빠지는 거래 손익 | 그 1건 평균(95% 구간) | 남는 거래 1건 평균 | 바꾼 뒤 손익 합계 | 개선(앞 절반 / 뒤 절반) |',
              '|---|---|---|---|---|---|---|']
    best = []
    for k, ths in THRESH.items():
        for th in ths:
            cut = [r for r in rows if r[k] <= th]; keep = [r for r in rows if r[k] > th]
            if not cut or not keep: continue
            lo, hi = boot(np.array([r['ret'] for r in cut]))
            a = -sum(r['pnl'] for r in cut if r['open'] < mid); b = -sum(r['pnl'] for r in cut if r['open'] >= mid)
            f = (lambda v: f'{v:.0%}') if '비율' in k else (lambda v: f'{v:+.1%}')
            lines.append(f"| {k} ≤ {f(th)} | {len(cut)} ({len(cut) / len(rows):.0%}) | {sum(r['pnl'] for r in cut):+,.0f}원 | {np.mean([r['ret'] for r in cut]):+.2%} ({lo:+.2%}~{hi:+.2%}) | "
                         f"{np.mean([r['ret'] for r in keep]):+.2%} | {sum(r['pnl'] for r in keep):+,.0f}원 | {a:+,.0f} / {b:+,.0f}원 |")
            best.append((-sum(r['pnl'] for r in cut), k, th, a, b))
    lines += ['', '- "개선" = 빠지는 거래 손익의 반대(그 거래를 안 했으면 아낀 돈). 앞·뒤 절반 모두 +여야 우연이 아닐 가능성이 높다.', '']
    # ---- B. 시뮬레이션
    dec = [i for i, g in enumerate(grid) if g.astimezone(KST) >= S.FIRST and g.minute == 45 and i + S.MAXH < n]
    sim = []
    cache = {}
    for i in dec:
        rg = regime_at(i, ms, C, V)
        v24 = np.nansum(V[:, i - 95:i + 1], axis=1)
        okh = ~np.isnan(C[:, i - 288]) & ~np.isnan(C[:, i])
        cand = [j for j in np.argsort(-np.where(okh, v24, -1))[:30] if okh[j]]
        uni = np.zeros(len(ms), bool); uni[cand] = True
        a = np.where(uni, S.scores(i, O, H, L, C, V)['지금 규칙 근사(18시간, 0~20%, 4시간 −10% 보류)'], np.nan)
        for j in [j for j in np.argsort(-np.nan_to_num(a, nan=-np.inf)) if not np.isnan(a[j])][:3]:
            x = S.exit_ret(j, i, O, H, L, C)
            if x is not None: sim.append(dict(rg, ret=x, i=i))
    smid = dec[len(dec) // 2]
    lines += ['## B. 시뮬레이션(지금 규칙 근사, 매시 상위 3, 같은 매도 규칙)', '',
              f'- 대상 {len(sim)}건, 1건 평균 {np.mean([s["ret"] for s in sim]):+.2%}, 합계 {np.sum([s["ret"] for s in sim]) * 100:+.0f}%p', '',
              '| 이 값 이하이면 매수 안 함 | 빠지는 비율 | 빠지는 거래 1건 평균 | 남는 거래 1건 평균 | 합계 개선(%p) | 앞 / 뒤 절반 개선 |', '|---|---|---|---|---|---|']
    for k, ths in THRESH.items():
        for th in ths:
            cut = [s for s in sim if s[k] <= th]; keep = [s for s in sim if s[k] > th]
            if not cut or not keep: continue
            f = (lambda v: f'{v:.0%}') if '비율' in k else (lambda v: f'{v:+.1%}')
            a = -sum(s['ret'] for s in cut if s['i'] < smid) * 100; b = -sum(s['ret'] for s in cut if s['i'] >= smid) * 100
            lines.append(f"| {k} ≤ {f(th)} | {len(cut) / len(sim):.0%} | {np.mean([s['ret'] for s in cut]):+.2%} | {np.mean([s['ret'] for s in keep]):+.2%} | "
                         f"{-sum(s['ret'] for s in cut) * 100:+.0f} | {a:+.0f} / {b:+.0f} |")
    lines += ['', '주의: 실거래 표본은 한 달·한 국면이다. 매수를 쉬면 생기는 빈자리 효과(나중에 다른 종목을 살 수 있음)는 빠져 있다. 여러 기준 중 고른 최고값은 실제보다 좋게 보인다.']
    fo = S.OUT / f'regime_pause_study_{now:%Y%m%d_%H%M}.md'
    fo.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(fo); print('\n'.join(lines))


if __name__ == '__main__':
    main()
