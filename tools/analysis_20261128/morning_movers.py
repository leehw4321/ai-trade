"""아침 9시 급등 점검 (2026-11-28 사용자: "매일 아침 9시부터 1시간 동안 많이 오른 종목을 우리는 전혀 잡지 못한다.
오르기 전 어떤 시그널이 있는지 1달 자료로 점검하고 현재 봇과 차이를 보고해줘").

읽기 전용, 주문 없음. 자료: fetch15m.py가 받은 원화 전 종목 15분봉(10-29~).
업비트 '전일 대비'는 매일 09:00(KST) 기준이라, 10시 화면의 등락률 = 09:00→10:00 상승률이다.

1) 매일 09:00→10:00 상승 상위 종목
2) 09:00 직전(08:59까지 자료) 신호: 급등 종목과 나머지의 차이, 신호 구간별 9~10시 평균
3) 09:00에 신호로 미리 사기 / 09:15에 첫 15분 급등을 보고 따라 사기 → 10시·12시·24시간(손절·이익보호) 결과
   같은 '첫 15분 급등 따라 사기'를 다른 시각에도 해서 9시가 특별한지 본다
4) 봇과 차이: 급등 종목이 09:00에 봇 후보(거래대금 상위 30·18시간 상승률 순위)였는지, 봇이 실제로 그날 샀는지
5) 급등 뒤(10시 이후) 흐름

결과: 코인 트레이딩/reports/morning_movers_YYYYMMDD_HHMM.md
"""
import datetime as dt, json, sys
from pathlib import Path
import numpy as np

ROOT = Path(r'C:\Users\User\Desktop\코인 트레이딩')
DATA = ROOT / 'v5-app' / 'v5' / 'data'
C15 = DATA / 'analysis_20261128' / 'c15m'
OUT = ROOT / 'reports'
KST = dt.timezone(dt.timedelta(hours=9))
UTC = dt.timezone.utc
EXCLUDE = {'KRW-USDT', 'KRW-USDC', 'KRW-USDE', 'KRW-USD1', 'KRW-DAI'}   # 봇 제외 목록과 같게
COST = 0.003
TOP = 10


def load():
    data = {f.stem: json.loads(f.read_text()) for f in sorted(C15.glob('KRW-*.json'))}
    data = {m: b for m, b in data.items() if len(b) > 100}
    start = min(dt.datetime.fromisoformat(b[0]['t']) for b in data.values()).replace(tzinfo=UTC)
    end = max(dt.datetime.fromisoformat(b[-1]['t']) for b in data.values()).replace(tzinfo=UTC)
    n = int((end - start).total_seconds() // 900) + 1
    grid = [start + dt.timedelta(minutes=15 * i) for i in range(n)]
    idx = {g.strftime('%Y-%m-%dT%H:%M:%S'): i for i, g in enumerate(grid)}
    ms = sorted(data)
    O, H, L, C, V = (np.full((len(ms), n), np.nan) for _ in range(5))
    for j, m in enumerate(ms):
        for b in data[m]:
            i = idx.get(b['t'])
            if i is not None:
                O[j, i], H[j, i], L[j, i], C[j, i], V[j, i] = b['o'], b['h'], b['l'], b['c'], b['v']
        last = np.nan
        for i in range(n):
            if np.isnan(C[j, i]):
                if not np.isnan(last):
                    O[j, i] = H[j, i] = L[j, i] = C[j, i] = last; V[j, i] = 0
            else:
                last = C[j, i]
    return ms, grid, O, H, L, C, V


def live_exit(j, i, O, H, L, C, stop=0.035, at=0.03, give=0.30, maxbars=96):
    """i 봉 종가 매수 → 손절 3.5%·+3% 뒤 이익 30% 반납·최대 24시간(지금 봇 매도 규칙 근사)."""
    e = C[j, i]; peak = e; n = C.shape[1]
    if not e > 0 or i + maxbars >= n: return None
    for k in range(i + 1, i + 1 + maxbars):
        if L[j, k] <= e * (1 - stop): return min(e * (1 - stop), O[j, k]) / e - 1 - COST
        peak = max(peak, C[j, k])
        if peak >= e * (1 + at) and C[j, k] <= peak - give * (peak - e): return C[j, k] / e - 1 - COST
    return C[j, i + maxbars] / e - 1 - COST


def bot_buys():
    """봇 실제 매수 체결(종목, 시각)."""
    out = []
    try:
        for line in open(DATA / 'exchange_orders.jsonl', encoding='utf-8'):
            r = json.loads(line)
            if str(r.get('identifier') or '').startswith('v5-') and r.get('side') == 'bid' and float(r.get('executed_volume') or 0) > 0:
                out.append((r['market'], dt.datetime.fromisoformat(r['created_at']).astimezone(KST), float(r['executed_funds']) / float(r['executed_volume'])))
    except OSError:
        pass
    return out


def pct(x): return f'{x:+.2%}' if x == x else '-'


def main():
    ms, grid, O, H, L, C, V = load()
    n = len(grid)
    nine = [i for i, g in enumerate(grid) if g.astimezone(KST).hour == 9 and g.minute == 0 and i >= 96 * 3 and i + 3 < n]
    vol24 = lambda i: np.nansum(V[:, i - 96:i], axis=1)     # 09:00 직전 24시간 거래대금
    rows, days = [], []
    buys = bot_buys()
    for i9 in nine:
        b = i9 - 1                                          # 08:45 봉(09:00 마감)
        base = C[:, b]
        r910 = C[:, i9 + 3] / base - 1
        ok = (base > 0) & ~np.isnan(r910) & ~np.isnan(C[:, i9 - 96])
        v24 = vol24(i9)
        rank_v = np.full(len(ms), 999)
        order = [j for j in np.argsort(-np.where(ok, v24, -1)) if ok[j] and ms[j] not in EXCLUDE]
        for r, j in enumerate(order, 1): rank_v[j] = r
        uni = rank_v <= 30
        rs18 = base / C[:, b - 72] - 1
        rs_rank = np.full(len(ms), 999)
        for r, j in enumerate([j for j in np.argsort(-np.nan_to_num(np.where(uni, rs18, np.nan), nan=-9)) if uni[j]], 1): rs_rank[j] = r
        hr_avg = v24 / 24
        f = {
            '직전 1시간 등락': base / C[:, b - 4] - 1,
            '밤사이(00~09시) 등락': base / C[:, b - 36] - 1,
            '직전 24시간 등락': base / C[:, b - 96] - 1,
            '18시간 상승률(봇 점수)': rs18,
            '직전 72시간 등락': base / C[:, max(b - 288, 0)] - 1,
            '7일 고점 대비': base / np.nanmax(H[:, max(b - 672, 0):b + 1], axis=1) - 1,
            '직전 1시간 거래대금 ÷ 24시간 시간평균': np.nansum(V[:, b - 3:b + 1], axis=1) / np.maximum(hr_avg, 1),
            '24시간 거래대금 ÷ 그 전 24시간': v24 / np.maximum(np.nansum(V[:, i9 - 192:i9 - 96], axis=1), 1),
            '24시간 거래대금(억)': v24 / 1e8,
            '24시간 변동성(15분 수익률 표준편차)': np.nanstd(np.diff(np.log(C[:, b - 96:b + 1]), axis=1), axis=1),
            '첫 15분(09:00~09:15) 등락': C[:, i9] / base - 1,
            '첫 15분 거래대금 ÷ 24시간 15분평균': V[:, i9] / np.maximum(v24 / 96, 1),
        }
        top = [j for j in np.argsort(-np.where(ok & ~np.isin(ms, list(EXCLUDE)), r910, -9)) if ok[j]][:TOP]
        day = grid[i9].astimezone(KST).date()
        for j in range(len(ms)):
            if not ok[j] or ms[j] in EXCLUDE: continue
            rows.append({'day': day, 'i9': i9, 'j': j, 'm': ms[j], 'r910': r910[j], 'top': j in top, 'vrank': rank_v[j], 'rsrank': rs_rank[j],
                         **{k: v[j] for k, v in f.items()},
                         'r_peak': np.nanmax(H[j, i9:i9 + 4]) / base[j] - 1,
                         'after_10_12': C[j, i9 + 11] / C[j, i9 + 3] - 1 if i9 + 11 < n else np.nan,
                         'after_10_24h': C[j, i9 + 99] / C[j, i9 + 3] - 1 if i9 + 99 < n else np.nan})
        days.append((day, i9, top))
    R = rows
    now = dt.datetime.now(KST)
    L_ = [f'# 아침 9시 급등 종목 점검 ({now:%Y-%m-%d %H:%M} KST)', '',
          f'- 기간 {days[0][0]} ~ {days[-1][0]} ({len(days)}일), 업비트 원화 전 종목 15분봉. 9시 등락 = 09:00 가격 → 10:00 가격(업비트 \'전일 대비\'와 같은 기준)',
          f'- 급등 종목 = 그날 9~10시 상승 상위 {TOP}. 비용(수수료+체결 차이) 왕복 {COST:.1%} 반영', '']
    # 1. 규모
    tops = [r for r in R if r['top']]
    L_ += ['## 1. 9시 급등은 매일 있나', '', '| 날짜 | 1위 | 2위 | 3위 | +5% 이상 종목 수 | 1~10위 중 봇 후보(거래대금 상위 30) |', '|---|---|---|---|---|---|']
    for day, i9, top in days:
        dr = sorted([r for r in R if r['day'] == day], key=lambda r: -r['r910'])
        t3 = ' | '.join(f"{r['m'][4:]} {r['r910']:+.1%}" for r in dr[:3])
        L_.append(f"| {day:%m-%d} | {t3} | {sum(r['r910'] >= .05 for r in dr)} | {sum(r['vrank'] <= 30 for r in dr[:TOP])}/{TOP} |")
    L_ += ['', f"- 급등 상위 {TOP}의 9~10시 평균 {np.mean([r['r910'] for r in tops]):+.2%}, 한때 최고 평균 {np.mean([r['r_peak'] for r in tops]):+.2%}",
           f"- 같은 시간 전체 종목 평균 {np.mean([r['r910'] for r in R]):+.2%}", '']
    # 2. 신호
    feats = [k for k in R[0] if k not in ('day', 'i9', 'j', 'm', 'r910', 'top', 'vrank', 'rsrank', 'r_peak', 'after_10_12', 'after_10_24h')]
    L_ += ['## 2. 오르기 전(09:00까지) 신호: 급등 종목 vs 나머지', '',
           '구간: 그날 전체 종목을 신호 값으로 5등분(1=가장 낮음, 5=가장 높음). "급등 비율" = 그 구간 종목 중 그날 급등 상위 10에 든 비율(전체 평균은 ' +
           f'{np.mean([r["top"] for r in R]):.1%}).', '',
           '| 신호 | 급등 종목 중앙값 | 나머지 중앙값 | 구간1 급등 비율 | 구간3 | 구간5 | 구간5의 9~10시 평균 | 구간1의 9~10시 평균 |', '|---|---|---|---|---|---|---|---|']
    def q5(k):
        out = {q: [] for q in range(5)}
        for day, _, _ in days:
            dr = [r for r in R if r['day'] == day and r[k] == r[k] and np.isfinite(r[k])]
            dr.sort(key=lambda r: r[k])
            for t, r in enumerate(dr): out[min(4, t * 5 // len(dr))].append(r)
        return out
    sig = []
    for k in feats:
        q = q5(k)
        hit = [np.mean([r['top'] for r in q[x]]) if q[x] else np.nan for x in range(5)]
        a = [r[k] for r in tops if np.isfinite(r[k])]; b = [r[k] for r in R if not r['top'] and np.isfinite(r[k])]
        fmt = (lambda v: f'{v:.2f}') if ('÷' in k or '억' in k or '변동성' in k) else pct
        L_.append(f"| {k} | {fmt(np.median(a))} | {fmt(np.median(b))} | {hit[0]:.1%} | {hit[2]:.1%} | {hit[4]:.1%} | "
                  f"{pct(np.mean([r['r910'] for r in q[4]]))} | {pct(np.mean([r['r910'] for r in q[0]]))} |")
        sig.append((max(hit[4], hit[0]) / max(np.mean([r['top'] for r in R]), 1e-9), k))
    L_ += ['', '- "첫 15분" 두 줄은 09:00 이전이 아니라 09:15에야 알 수 있는 신호다(참고용 비교).', '']
    # 3. 사는 방법별 결과
    def strat(name, pick, when):
        """pick(day rows) → 산 rows. when: 0 = 09:00 가격(08:45 봉 종가)에 매수, 1 = 09:15 가격(09:00 봉 종가)에 매수."""
        res = []
        for day, i9, _ in days:
            dr = [r for r in R if r['day'] == day]
            for r in pick(dr):
                i = i9 - 1 + when
                to10 = C[r['j'], i9 + 3] / C[r['j'], i] - 1 - COST
                ex = live_exit(r['j'], i, O, H, L, C)
                res.append((to10, ex, r['top'], r['vrank'] <= 30))
        if not res: return f'| {name} | 0 | - | - | - | - |'
        a = np.array([x[0] for x in res]); e = np.array([x[1] for x in res if x[1] is not None])
        return (f'| {name} | {len(res)} ({len(res) / len(days):.1f}/일) | {a.mean():+.2%} | {(a > 0).mean():.0%} | '
                f'{e.mean() if len(e) else np.nan:+.2%} ({(e > 0).mean() if len(e) else 0:.0%}) | {np.mean([x[2] for x in res]):.0%} |')
    liquid = lambda r: r['24시간 거래대금(억)'] >= 10
    L_ += ['## 3. 이렇게 샀다면 (1건 평균, 비용 0.3% 반영)', '',
           '"보유 24시간" = 지금 봇 매도 규칙 근사(손절 −3.5%·+3% 뒤 이익 30% 반납·최대 24시간). 거래대금 10억 미만 종목은 사기 어려워 뺐다.', '',
           '| 방법 | 건수 | 10시에 팔면 | 10시 승률 | 봇 규칙으로 보유(승률) | 그날 급등 상위10 적중 |', '|---|---|---|---|---|---|']
    pre = [('직전 1시간 거래대금 ÷ 24시간 시간평균', 5), ('밤사이(00~09시) 등락', 5), ('직전 24시간 등락', 5), ('18시간 상승률(봇 점수)', 5),
           ('24시간 거래대금 ÷ 그 전 24시간', 5), ('7일 고점 대비', 5)]
    L_.append(strat('09:00 · 거래대금 10억↑ 전 종목(기준선)', lambda dr: [r for r in dr if liquid(r)], 0))
    for k, nn in pre:
        L_.append(strat(f'09:00 · {k} 상위 {nn}', lambda dr, k=k, nn=nn: sorted([r for r in dr if liquid(r) and np.isfinite(r[k])], key=lambda r: -r[k])[:nn], 0))
    L_.append(strat('09:00 · 봇 방식(상위30 중 18시간 상승률 0~20% 상위 3)',
                    lambda dr: sorted([r for r in dr if r['vrank'] <= 30 and 0 < r['18시간 상승률(봇 점수)'] < .2], key=lambda r: -r['18시간 상승률(봇 점수)'])[:3], 0))
    for g, vr in ((0.02, 3), (0.03, 3), (0.03, 5), (0.05, 5)):
        L_.append(strat(f'09:15 · 첫 15분 +{g:.0%}↑ & 거래대금 {vr}배↑ 따라 사기',
                        lambda dr, g=g, vr=vr: [r for r in dr if liquid(r) and r['첫 15분(09:00~09:15) 등락'] >= g and r['첫 15분 거래대금 ÷ 24시간 15분평균'] >= vr], 1))
    L_ += ['', '- "09:00 · …" 줄은 매수 = 09:00 가격(08:45 봉 종가), "09:15" 줄은 매수 = 09:15 가격(첫 15분이 지난 뒤).', '']
    # 3b. 9시가 특별한가: 첫 15분 급등 따라 사기를 다른 시각에도
    L_ += ['### 같은 "첫 15분 +3%↑·거래대금 3배↑ 따라 사기"를 시각별로 (매수 = 그 시각+15분, 1시간 뒤 팔기)', '', '| 시각 | 건수/일 | 1시간 뒤 평균 | 봇 규칙 보유 평균 |', '|---|---|---|---|']
    for hh in (0, 3, 6, 8, 9, 10, 11, 12, 15, 18, 21):
        res = []
        for i, g in enumerate(grid):
            gk = g.astimezone(KST)
            if gk.hour != hh or g.minute != 0 or i < 97 or i + 4 >= n: continue
            v = np.nansum(V[:, i - 96:i], axis=1)
            e1 = C[:, i] / C[:, i - 1] - 1; vr = V[:, i] / np.maximum(v / 96, 1)
            for j in np.where((e1 >= .03) & (vr >= 3) & (v >= 1e9))[0]:
                if ms[j] in EXCLUDE: continue
                ex = live_exit(j, i, O, H, L, C)
                res.append((C[j, i + 4] / C[j, i] - 1 - COST, ex))
        nd = len(days)
        if res:
            e = [x[1] for x in res if x[1] is not None]
            L_.append(f'| {hh:02d}:00 | {len(res) / nd:.1f} | {np.mean([x[0] for x in res]):+.2%} | {np.mean(e) if e else np.nan:+.2%} |')
    # 4. 봇과 차이
    bb = {}
    for m, t, px in buys: bb.setdefault((m, (t - dt.timedelta(hours=9)).date()), []).append((t, px))   # 09:00~다음 날 09:00을 그날로
    L_ += ['', '## 4. 지금 봇과 차이 (그날 급등 상위 10 기준)', '']
    inuni = np.mean([r['vrank'] <= 30 for r in tops]); cand = np.mean([r['rsrank'] <= 7 and 0 < r['18시간 상승률(봇 점수)'] < .2 for r in tops])
    bought = [(r, bb[(r['m'], r['day'])]) for r in tops if (r['m'], r['day']) in bb]
    L_ += [f"- 09:00에 봇 후보 범위(거래대금 상위 30)에 있던 비율: **{inuni:.0%}**",
           f"- 09:00에 봇 매수 순위(18시간 상승률 상위 7, 0~20%) 안이던 비율: **{cand:.0%}**",
           f"- 그날(09:00~다음 날 09:00) 봇이 실제로 산 급등 종목: {len(bought)}/{len(tops)}건 (9~10시 +5% 이상만 아래에 표시)"]
    for r, bl in [x for x in bought if x[0]['r910'] >= .05]:
        t, px = bl[0]
        L_.append(f"  - {r['day']:%m-%d} {r['m'][4:]}: 9~10시 {r['r910']:+.1%}, 봇 매수 {t:%H:%M} (09:00 가격 대비 {px / C[r['j'], r['i9'] - 1] - 1:+.1%})")
    L_ += ['', '## 5. 급등 뒤 흐름 (10시에 따라 샀다면)', '',
           f"- 급등 상위 10의 10시→12시 평균 {pct(np.nanmean([r['after_10_12'] for r in tops]))} (오른 비율 {np.nanmean([r['after_10_12'] > 0 for r in tops if r['after_10_12'] == r['after_10_12']]):.0%})",
           f"- 10시→다음 날 10시 평균 {pct(np.nanmean([r['after_10_24h'] for r in tops]))} (오른 비율 {np.nanmean([r['after_10_24h'] > 0 for r in tops if r['after_10_24h'] == r['after_10_24h']]):.0%})", '',
           '주의: 한 달(약 30일) 자료, 9~10시 상위 10은 사후에 고른 것이라 신호 표는 "있었다/없었다"만 보여 준다. 실제 매수 규칙 효과는 3번 표로 판단한다. 입증이 아니다.']
    OUT.mkdir(exist_ok=True)
    fo = OUT / f'morning_movers_{now:%Y%m%d_%H%M}.md'
    fo.write_text('\n'.join(L_) + '\n', encoding='utf-8')
    print(fo); print('\n'.join(L_))


if __name__ == '__main__':
    main()
