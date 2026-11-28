"""매수 기준 점검 (2026-11-28 사용자: "상대 강도 순위로 매수하는 기준을 다시 점검하고 다른 기준을 추천해줘, 수익률이 상당히 낮다").

읽기 전용, 주문 없음. 업비트 15분봉(fetch15.py가 받은 원화 전 종목)으로 11-06~현재 매시 정각에
 1) 그때 거래대금 상위 30 종목을 다시 만들고
 2) 여러 매수 점수(지금 쓰는 18시간 상승률 포함)로 순위를 매겨
 3) 각 점수 상위 3 종목을 샀다면 결과를 같은 매도 규칙(손절 3.5%·+3% 뒤 이익 30% 반납·최대 24시간, 왕복 비용 0.3%)으로 계산한다.
또 점수와 이후 12시간 수익률의 순위 상관(IC)을 본다. 실거래 왕복(stop035_review.load)도 매수 당시 18시간 상승률 구간별로 나눈다.

한계: 15분봉 근사(봉 안 순서는 손절 우선), 순위 매도·부분 익절·재매수 대기·5분봉 보류는 생략, 매시 판단이 서로 겹친다(독립 표본 아님).
결과는 코인 트레이딩/reports/entry_rule_study_YYYYMMDD_HHMM.md.
"""
import datetime as dt, json, sys
from pathlib import Path
import numpy as np

ROOT = Path(r'C:\Users\User\Desktop\코인 트레이딩')
C15 = ROOT / 'v5-app' / 'v5' / 'data' / 'analysis_20261128' / 'c15m'   # fetch15m.py(원화 전 종목 한 달)
OUT = ROOT / 'reports'
KST = dt.timezone(dt.timedelta(hours=9))
STABLE = {'KRW-USDT', 'KRW-USDC', 'KRW-USDE', 'KRW-USD1', 'KRW-DAI', 'KRW-USDS', 'KRW-PYUSD'}
COST = 0.003
STOP, GUARD_AT, GIVEBACK, MAXH = 0.035, 0.03, 0.30, 96   # 96봉 = 24시간
FIRST = dt.datetime(2026, 11, 1, 0, 0, tzinfo=KST)   # 10-29부터 받은 자료에서 72시간 거슬러 볼 수 있는 첫날


def load():
    files = sorted(C15.glob('KRW-*.json'))
    data = {f.stem: json.loads(f.read_text()) for f in files}
    data = {m: b for m, b in data.items() if len(b) > 300 and m not in STABLE}
    t0 = min(b[0]['t'] for b in data.values())
    start = dt.datetime.fromisoformat(t0).replace(tzinfo=dt.timezone.utc)
    end = max(dt.datetime.fromisoformat(b[-1]['t']).replace(tzinfo=dt.timezone.utc) for b in data.values())
    n = int((end - start).total_seconds() // 900) + 1
    grid = [start + dt.timedelta(minutes=15 * i) for i in range(n)]
    idx = {g.strftime('%Y-%m-%dT%H:%M:%S'): i for i, g in enumerate(grid)}
    ms = sorted(data)
    O, H, L, Cl, V = (np.full((len(ms), n), np.nan) for _ in range(5))
    for j, m in enumerate(ms):
        for b in data[m]:
            i = idx.get(b['t'])
            if i is None: continue
            O[j, i], H[j, i], L[j, i], Cl[j, i], V[j, i] = b['o'], b['h'], b['l'], b['c'], b['v']
        # 거래 없는 봉: 직전 종가로 채움(시가·고가·저가 = 직전 종가, 거래대금 0)
        last = np.nan
        for i in range(n):
            if np.isnan(Cl[j, i]):
                if not np.isnan(last):
                    O[j, i] = H[j, i] = L[j, i] = Cl[j, i] = last; V[j, i] = 0
            else:
                last = Cl[j, i]
    return ms, grid, O, H, L, Cl, V


def ret(C, i, k):
    return C[:, i] / C[:, i - k] - 1


def scores(i, O, H, L, C, V):
    """봉 i 마감 시점 각 종목 점수(클수록 매수). nan = 대상 아님."""
    s = {}
    for name, k in (('상승률 4시간', 16), ('상승률 6시간', 24), ('상승률 12시간', 48), ('상승률 18시간(지금)', 72),
                    ('상승률 24시간', 96), ('상승률 48시간', 192), ('상승률 72시간', 288)):
        s[name] = ret(C, i, k)
    r72, r16, r8, r4 = ret(C, i, 72), ret(C, i, 16), ret(C, i, 8), ret(C, i, 4)
    lr = np.diff(np.log(C[:, i - 72:i + 1]), axis=1)
    sd = np.nanstd(lr, axis=1)
    s['상승률÷변동성 18시간'] = np.where(sd > 0, r72 / (sd * np.sqrt(72)), np.nan)
    y = np.log(C[:, i - 72:i + 1]); x = np.arange(73) - 36
    slope = (y * x).sum(1) / (x * x).sum()
    fit = y.mean(1, keepdims=True) + slope[:, None] * x
    r2 = 1 - ((y - fit) ** 2).sum(1) / np.maximum(((y - y.mean(1, keepdims=True)) ** 2).sum(1), 1e-12)
    s['고른 상승(기울기×R²) 18시간'] = slope * 96 * r2
    s['18시간 강세 + 최근 2시간 눌림'] = np.where((r8 < 0) & (r72 > 0), r72, np.nan)
    s['18시간 강세 + 최근 1시간 제외'] = C[:, i - 4] / C[:, i - 76] - 1
    s['4시간 최약(되돌림)'] = -r16
    vr = V[:, i - 15:i + 1].sum(1) / np.maximum(V[:, i - 95:i + 1].sum(1) / 6, 1)
    s['거래대금 급증 + 4시간 상승'] = np.where(r16 > 0, vr, np.nan)
    hi = np.nanmax(H[:, i - 95:i + 1], axis=1)
    s['24시간 고점 근접(돌파)'] = C[:, i] / hi
    def rk(a): return np.argsort(np.argsort(np.nan_to_num(a, nan=-9)))
    s['4·12·24시간 순위 평균'] = (rk(ret(C, i, 16)) + rk(ret(C, i, 48)) + rk(ret(C, i, 96))) / 3.0
    s['낮은 변동성'] = -sd
    r4h = ret(C, i, 16)
    s['지금 규칙 근사(18시간, 0~20%, 4시간 −10% 보류)'] = np.where((r72 > 0) & (r72 < 0.20) & (r4h > -0.10), r72, np.nan)
    s['지금 규칙 + 18시간 5% 이상만'] = np.where((r72 >= 0.05) & (r72 < 0.20) & (r4h > -0.10), r72, np.nan)
    s['지금 규칙 + 18시간 10% 이상만'] = np.where((r72 >= 0.10) & (r72 < 0.20) & (r4h > -0.10), r72, np.nan)
    s['18시간 3~12% + 4시간 −3~+3%'] = np.where((r72 > 0.03) & (r72 < 0.12) & (np.abs(r4h) < 0.03), r72, np.nan)
    return s


def exit_ret(j, i, O, H, L, C):
    """i 봉 종가 매수 → 손절 3.5%·이익보호·24시간."""
    e = C[j, i]; stop = e * (1 - STOP); peak = e
    n = C.shape[1]
    for k in range(i + 1, min(i + 1 + MAXH, n)):
        if L[j, k] <= stop:
            return min(stop, O[j, k]) / e - 1 - COST
        peak = max(peak, C[j, k])
        if peak >= e * (1 + GUARD_AT) and C[j, k] <= peak - GIVEBACK * (peak - e):
            return C[j, k] / e - 1 - COST
    if i + MAXH >= n: return None
    return C[j, min(i + MAXH, n - 1)] / e - 1 - COST


def spearman(a, b):
    ok = ~np.isnan(a) & ~np.isnan(b)
    if ok.sum() < 8: return np.nan
    ra = np.argsort(np.argsort(a[ok])); rb = np.argsort(np.argsort(b[ok]))
    return np.corrcoef(ra, rb)[0, 1]


def main():
    ms, grid, O, H, L, C, V = load()
    n = len(grid)
    dec = [i for i, g in enumerate(grid) if g.astimezone(KST) >= FIRST and g.minute == 45 and i + MAXH < n]   # 15분봉 시작 :45 = 정시 마감
    res, ics, bench, cache, regs = {}, {}, [], {}, {}
    rows_by_bucket = {}
    for i in dec:
        v24 = V[:, i - 95:i + 1].sum(1)
        okhist = ~np.isnan(C[:, i - 288]) & ~np.isnan(C[:, i])
        cand = [j for j in np.argsort(-np.where(okhist, v24, -1))[:30] if okhist[j]]
        uni = np.zeros(len(ms), bool); uni[cand] = True
        f12 = C[:, i + 48] / C[:, i] - 1
        sc = scores(i, O, H, L, C, V)
        def ex(j):
            if (j, i) not in cache: cache[(j, i)] = exit_ret(j, i, O, H, L, C)
            return cache[(j, i)]
        b = [ex(j) for j in cand]; b = [x for x in b if x is not None]
        bench.append((i, float(np.mean(b))))
        up = float(np.nanmean(ret(C, i, 96)[cand])) > 0   # 상위 30 평균 24시간 등락
        regs[i] = up
        r72 = ret(C, i, 72)
        for j in cand:
            x = ex(j)
            if x is None: continue
            v = r72[j]
            key = ('0% 이하' if v <= 0 else '0~5%' if v < .05 else '5~10%' if v < .10 else '10~15%' if v < .15 else '15~20%' if v < .20 else '20% 이상')
            rows_by_bucket.setdefault(key, []).append(x)
        for name, a in sc.items():
            a = np.where(uni, a, np.nan)
            ics.setdefault(name, []).append(spearman(a, np.where(uni, f12, np.nan)))
            if name.startswith('상승률'):   # 지금처럼 상승 종목만 산다
                a = np.where(a > 0, a, np.nan)
            top = [j for j in np.argsort(-np.nan_to_num(a, nan=-np.inf)) if not np.isnan(a[j])][:3]
            for j in top:
                x = ex(j)
                if x is not None: res.setdefault(name, []).append((i, x))
    mid = dec[len(dec) // 2]
    bm = np.mean([x for _, x in bench])
    now = dt.datetime.now(KST)
    lines = [f'# 매수 기준 점검: 상대강도(18시간 상승률) vs 다른 기준 ({now:%Y-%m-%d %H:%M} KST)', '',
             f'- 기간 {grid[dec[0]].astimezone(KST):%m-%d %H시} ~ {grid[dec[-1]].astimezone(KST):%m-%d %H시} 매시 {len(dec)}회 판단, 매번 거래대금 상위 30 종목 중 점수 상위 3 매수 가정',
             f'- 매도는 모두 같게: 손절 −3.5% · +3% 뒤 이익 30% 반납 · 최대 24시간, 왕복 비용 {COST:.1%}',
             f'- 기준선(상위 30 종목 아무거나 산 평균): 1건 평균 **{bm:+.2%}**',
             f'- 상승장 = 그 시각 상위 30 종목 24시간 평균 등락 > 0: {sum(regs.values())}/{len(regs)}회', '',
             '## 1. 기준별 결과 (1건 평균, 높은 순)', '',
             '| 매수 기준 | 건수 | 1건 평균 | 기준선 대비 | 승률 | 손절 비율 | 앞 절반 | 뒤 절반 | 상승장 | 하락장 | 12시간 순위 상관(IC) |', '|---|---|---|---|---|---|---|---|---|---|---|']
    out = []
    for name, xs in res.items():
        v = np.array([x for _, x in xs])
        a = np.array([x for i, x in xs if i < mid]); b = np.array([x for i, x in xs if i >= mid])
        ic = np.nanmean(ics[name])
        u = np.array([x for i, x in xs if regs[i]]); d = np.array([x for i, x in xs if not regs[i]])
        out.append((v.mean(), f'| {name} | {len(v)} | {v.mean():+.2%} | {v.mean() - bm:+.2%} | {(v > 0).mean():.0%} | {(v <= -STOP - COST + 1e-9).mean():.0%} | '
                               f'{a.mean() if len(a) else 0:+.2%} | {b.mean() if len(b) else 0:+.2%} | {u.mean() if len(u) else 0:+.2%} | {d.mean() if len(d) else 0:+.2%} | {ic:+.3f} |'))
    lines += [r for _, r in sorted(out, reverse=True)]
    lines += ['', 'IC: 판단 때마다 상위 30 종목의 점수 순위와 이후 12시간 수익률 순위의 상관 평균. + 클수록 점수가 높은 종목이 더 올랐다는 뜻(0.05면 약하지만 의미 있는 편).', '',
              '## 2. 지금 기준의 18시간 상승률 구간별 (상위 30 종목 모두, 같은 매도 규칙)', '', '| 18시간 상승률 | 건수 | 1건 평균 | 승률 |', '|---|---|---|---|']
    for k in ('0% 이하', '0~5%', '5~10%', '10~15%', '15~20%', '20% 이상'):
        v = np.array(rows_by_bucket.get(k, []))
        if len(v): lines.append(f'| {k} | {len(v)} | {v.mean():+.2%} | {(v > 0).mean():.0%} |')
    lines += ['', live_section(ms, grid, C)]
    lines += ['', '주의: 3주 남짓 한 장세(대체로 하락·횡보) 자료다. 매시 판단이 겹쳐 건수만큼 독립적이지 않다. 입증이 아니다.']
    OUT.mkdir(exist_ok=True)
    f = OUT / f'entry_rule_study_{now:%Y%m%d_%H%M}.md'
    f.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f); print('\n'.join(lines))


def live_section(ms, grid, C):
    """실거래 왕복을 매수 직전 마감 15분봉 기준 18시간 상승률 구간별로."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis_20261111'))
    try:
        import stop035_review as R
        trips, _, _ = R.load()
    except Exception as ex:
        return f'(실거래 왕복 불러오기 실패: {type(ex).__name__})'
    pos = {m: j for j, m in enumerate(ms)}
    t0 = grid[0]
    rows = {}
    for t in trips:
        j = pos.get(t['m'])
        if j is None: continue
        i = int((t['open'].astimezone(dt.timezone.utc) - t0).total_seconds() // 900) - 1
        if i < 72 or i >= C.shape[1]: continue
        r = C[j, i] / C[j, i - 72] - 1
        r4 = C[j, i] / C[j, i - 16] - 1
        k = '0~5%' if r < .05 else '5~10%' if r < .10 else '10~15%' if r < .15 else '15% 이상'
        rows.setdefault(k, []).append((t['pnl'], t['ret'], r4))
    lines = ['## 3. 실거래 왕복을 매수 당시 18시간 상승률 구간별로', '', '| 18시간 상승률 | 건수 | 손익 합계 | 1건 평균 수익률 | 승률 |', '|---|---|---|---|---|']
    for k in ('0~5%', '5~10%', '10~15%', '15% 이상'):
        v = rows.get(k, [])
        if v: lines.append(f'| {k} | {len(v)} | {sum(x[0] for x in v):+,.0f}원 | {np.mean([x[1] for x in v]):+.2%} | {np.mean([x[0] > 0 for x in v]):.0%} |')
    return '\n'.join(lines)


if __name__ == '__main__':
    main()
