"""아침 급등 1분봉 점검 (2026-11-28 사용자: "급등주는 15분봉만으로 알 수 있나? 1분·5분봉을 참고해야 하지 않나").

읽기 전용, 주문 없음. 자료: fetch1m.py(매일 08:00~10:30 1분봉, 그날 09:00 직전 24시간 거래대금 10억 이상 전 종목)와
fetch15m.py(10:30 이후 보유 결과 계산용 15분봉).

1) 급등 상위 10은 언제 오르나: 9~10시 상승분의 절반을 몇 분 만에 냈나
2) 08:00~08:59 1분봉 신호: 급등 종목 vs 나머지
3) 1분·5분 따라 사기 규칙(오른 폭·거래대금 배수·시간 제한)마다 신호 수, 급등 적중, 헛신호, 결과
   매수 = 신호가 뜬 다음 1분봉 시가 × (1 + 0.2% 추가 체결 차이). 비용 왕복 0.3% 별도.
   매도 세 가지: 10:00 / 1분 추적(고점 대비 −3%, 늦어도 10:30) / 지금 봇 규칙(손절 3.5%·+3% 뒤 30% 반납·24시간, 15분봉 근사)
결과: 코인 트레이딩/reports/morning_1m_YYYYMMDD_HHMM.md
"""
import datetime as dt, json
from pathlib import Path
import numpy as np
import morning_movers as M

BASE = M.DATA / 'analysis_20261128'
KST, UTC = M.KST, M.UTC
COST, SLIP = 0.003, 0.002


def load_days():
    out = {}
    for d in sorted((BASE / 'm1').glob('2026*')):
        day = dt.date(int(d.name[:4]), int(d.name[4:6]), int(d.name[6:]))
        rows = {}
        for f in d.glob('KRW-*.json'):
            b = json.loads(f.read_text())
            if len(b) < 60: continue
            nine = dt.datetime(day.year, day.month, day.day, 0, tzinfo=UTC)   # 09:00 KST
            t = [dt.datetime.fromisoformat(x['t']).replace(tzinfo=UTC) for x in b]
            # 08:00~10:29 1분 격자로 채움(거래 없는 분 = 직전 종가, 거래대금 0)
            grid = [nine + dt.timedelta(minutes=k) for k in range(-60, 90)]
            by = dict(zip(t, b)); last = None; arr = []
            for g in grid:
                x = by.get(g)
                if x is None:
                    if last is None: arr.append(None); continue
                    x = {'o': last, 'h': last, 'l': last, 'c': last, 'v': 0}
                last = x['c']; arr.append(x)
            if arr[59] is None: continue
            first = next(x for x in arr if x is not None)
            arr = [x or {'o': first['o'], 'h': first['o'], 'l': first['o'], 'c': first['o'], 'v': 0} for x in arr]
            rows[f.stem] = {k: np.array([x[k] for x in arr], float) for k in 'ohlcv'}
        if rows: out[day] = rows
    return out


def main():
    days = load_days()
    ms, grid, O, H, L, C, V = M.load()
    pos = {m: j for j, m in enumerate(ms)}
    t0 = grid[0]

    def i15(ts):   # ts(UTC)가 들어 있는 15분봉 번호
        return int((ts - t0).total_seconds() // 900)

    def bot_exit(m, ts, e):
        """ts에 e로 산 뒤 지금 봇 매도 규칙(15분봉 근사)."""
        j = pos.get(m)
        if j is None: return None
        i = i15(ts); n = C.shape[1]; peak = e
        if i + 97 >= n: return None
        for k in range(i + 1, i + 97):
            if L[j, k] <= e * (1 - .035): return min(e * (1 - .035), O[j, k]) / e - 1 - COST
            peak = max(peak, C[j, k])
            if peak >= e * 1.03 and C[j, k] <= peak - .3 * (peak - e): return C[j, k] / e - 1 - COST
        return C[j, i + 96] / e - 1 - COST

    norm = {}   # (day, m) → 평소 1분 거래대금(그 전 24시간 평균)
    info = {}
    for day, rows in days.items():
        nine = dt.datetime(day.year, day.month, day.day, tzinfo=UTC)
        i9 = i15(nine)
        for m, b in rows.items():
            j = pos.get(m)
            v24 = np.nansum(V[j, i9 - 96:i9]) if j is not None else np.nan
            norm[(day, m)] = max(v24 / 1440, 1) if v24 == v24 else np.nan
        p9 = {m: b['c'][59] for m, b in rows.items()}
        r = {m: rows[m]['c'][119] / p9[m] - 1 for m in rows}
        top = set(sorted(r, key=lambda m: -r[m])[:10])
        info[day] = (p9, r, top)

    now = dt.datetime.now(KST)
    out = [f'# 아침 급등 1분봉 점검 ({now:%Y-%m-%d %H:%M} KST)', '',
           f'- 기간 {min(days)} ~ {max(days)} ({len(days)}일), 매일 09:00 직전 24시간 거래대금 10억 이상 종목 전부(하루 평균 {np.mean([len(v) for v in days.values()]):.0f}종목)의 08:00~10:30 1분봉',
           '- 급등 상위 10 = 이 종목들 중 그날 9~10시 상승 상위 10(거래대금 작은 종목은 빠져 15분봉 보고서와 조금 다르다)', '']
    # 1. 언제 오르나
    half, firstmin, peakmin = [], [], []
    for day, rows in days.items():
        p9, r, top = info[day]
        for m in top:
            c = rows[m]['c'][60:120] / p9[m] - 1
            g = r[m]
            if g <= 0: continue
            half.append(int(np.argmax(c >= g / 2)) + 1)
            firstmin.append(c[0]); peakmin.append(int(np.argmax(rows[m]['h'][60:120])) + 1)
    out += ['## 1. 급등 상위 10은 언제 오르나', '',
            f'- 9~10시 상승분의 **절반에 도달한 시각**: 중앙값 09:{np.median(half) - 1:02.0f}대, 5분 안 {np.mean(np.array(half) <= 5):.0%}, 15분 안 {np.mean(np.array(half) <= 15):.0%}',
            f'- 첫 1분(09:00~09:01) 등락 중앙값 {np.median(firstmin):+.2%}, 9~10시 최고가 시각 중앙값 09:{np.median(peakmin) - 1:02.0f}대', '']
    # 2. 08시대 신호
    feats = {'08:00~08:59 등락': lambda b: b['c'][59] / b['o'][0] - 1,
             '08:30~08:59 등락': lambda b: b['c'][59] / b['o'][30] - 1,
             '08:50~08:59 등락': lambda b: b['c'][59] / b['o'][50] - 1,
             '08시대 거래대금 ÷ 평소': None, '08:50~08:59 거래대금 ÷ 평소': None}
    out += ['## 2. 9시 전(08:00~08:59) 1분봉 신호: 급등 상위 10 vs 나머지 (중앙값)', '', '| 신호 | 급등 상위 10 | 나머지 |', '|---|---|---|']
    for k, fn in feats.items():
        a, b_ = [], []
        for day, rows in days.items():
            top = info[day][2]
            for m, b in rows.items():
                nv = norm[(day, m)]
                if k == '08시대 거래대금 ÷ 평소': v = b['v'][:60].sum() / 60 / nv
                elif k == '08:50~08:59 거래대금 ÷ 평소': v = b['v'][50:60].sum() / 10 / nv
                else: v = fn(b)
                if v == v and np.isfinite(v): (a if m in top else b_).append(v)
        f = (lambda x: f'{x:.2f}배') if '÷' in k else (lambda x: f'{x:+.2%}')
        out.append(f'| {k} | {f(np.median(a))} | {f(np.median(b_))} |')
    # 3. 따라 사기 규칙
    def run(g, win, vr, until):
        res = []
        for day, rows in days.items():
            p9, r, top = info[day]
            nine = dt.datetime(day.year, day.month, day.day, tzinfo=UTC)
            for m, b in rows.items():
                nv = norm[(day, m)]
                if not nv == nv: continue
                for k in range(60 + win - 1, 60 + until):          # k: 신호 판단 분(그 분 종가 마감 뒤)
                    ch = b['c'][k] / b['o'][k - win + 1] - 1
                    vol = b['v'][k - win + 1:k + 1].sum() / win / nv
                    if ch >= g and vol >= vr and b['c'][k] > b['o'][k]:
                        if k + 1 >= 150: break
                        e = b['o'][k + 1] * (1 + SLIP)
                        r10 = b['c'][119] / e - 1 - COST if k + 1 < 120 else np.nan
                        pk, tr = e, None
                        for q in range(k + 1, 150):
                            pk = max(pk, b['h'][q])
                            if b['l'][q] <= pk * 0.97: tr = pk * 0.97 / e - 1 - COST; break
                        if tr is None: tr = b['c'][149] / e - 1 - COST
                        be = bot_exit(m, nine + dt.timedelta(minutes=k + 1 - 60), e)
                        res.append({'day': day, 'top': m in top, 'r10': r10, 'tr': tr, 'bot': be, 'late': e / p9[m] - 1, 'min': k + 1 - 60})
                        break
        return res
    out += ['', '## 3. 1분·5분봉 따라 사기 규칙 (종목당 하루 첫 신호만)', '',
            '매수 = 신호 다음 1분 시가 + 0.2%(급등 중 체결 불리), 비용 왕복 0.3% 별도. 헛신호 = 그날 9~10시 상위 10에 못 든 신호.', '',
            '| 규칙 | 신호/일 | 급등 적중 | 매수가(09:00 대비) | 10시 매도 | 1분 추적 −3% | 1분 추적 앞/뒤 절반 | 봇 규칙 24시간(승률) |', '|---|---|---|---|---|---|---|---|']
    table = []
    for win, label in ((1, '1분'), (3, '3분'), (5, '5분')):
        for g in (0.01, 0.02, 0.03, 0.05):
            for vr in (5, 10, 20):
                for until in (10, 60):
                    res = run(g, win, vr, until)
                    if len(res) < 15: continue
                    r10 = np.array([x['r10'] for x in res if x['r10'] == x['r10']]); tr = np.array([x['tr'] for x in res])
                    bo = np.array([x['bot'] for x in res if x['bot'] is not None])
                    mid = sorted(days)[len(days) // 2]
                    h1 = np.mean([x['tr'] for x in res if x['day'] < mid] or [np.nan]); h2 = np.mean([x['tr'] for x in res if x['day'] >= mid] or [np.nan])
                    table.append((tr.mean(), f"| {label} +{g:.0%}↑ · 거래대금 {vr}배↑ · 09:{until - 1:02d}까지 | {len(res) / len(days):.1f} | {np.mean([x['top'] for x in res]):.0%} | "
                                             f"{np.median([x['late'] for x in res]):+.1%} | {r10.mean():+.2%} ({(r10 > 0).mean():.0%}) | {tr.mean():+.2%} ({(tr > 0).mean():.0%}) | {h1:+.2%} / {h2:+.2%} | {bo.mean():+.2%} ({(bo > 0).mean():.0%}) |"))
    table.sort(key=lambda x: -x[0])
    out += [r for _, r in table[:15]] + ['', f'(조합 {len(table)}개 중 1분 추적 결과 상위 15. 하위 5:)', ''] + [r for _, r in table[-5:]]
    out += ['', '주의: 1분봉 체결 근사(봉 안 순서 모름, 급등 중 호가 공백은 0.2%로만 반영). 약 30일·여러 조합 중 고른 최고값은 실제보다 좋게 보인다. 입증이 아니다.']
    fo = M.OUT / f'morning_1m_{now:%Y%m%d_%H%M}.md'
    fo.write_text('\n'.join(out) + '\n', encoding='utf-8')
    print(fo); print('\n'.join(out))


if __name__ == '__main__':
    main()
