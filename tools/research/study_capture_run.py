"""탐색 연구(2026-11-05 사용자 요청): 18시간 30~40% 급등을 더 많이 먹을 수 있는가 — 후보 3가지를 한 번에 비교한다.

  아이디어1 보호선 넓히기: +3~10% 구간은 30% 반납 대신 '고점 − k×ATR(15분, 14봉)'(본전 아래로는 안 내려감), +10% 이상은 기존 30% 반납과 둘 중 높은 선.
  아이디어2 이익 청산 뒤 재진입: 현재 정책이 이익보호로 이익 청산한 종목을 24시간 안 정시에 7위 안·상승률>0이면 과열 상한 없이 한 번 더 산다.
  아이디어3 급등 초기 매수: 15분 거래대금이 직전 24시간 평균의 M배 이상이고 그 봉이 +3% 이상 양봉이면 그 봉 종가에 산다(종목당 24시간 1회).

180일 공개 15분봉(생존 편향: 상장폐지 종목 없음). 사전 등록이 아닌 탐색이며 여러 변형(10개: 보호선 3·순위 이탈 3·재진입 1·급등 3)을 봤으므로 Bonferroni 구간으로 보고한다.
결과를 보고 값을 바꿔 재실행하면 그 사실을 기록한다.
  python study_capture_run.py
"""
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pit_universe as pit
import study_regime_exit as rx

B = pit.BAR
HOUR = 3600
DATA_DIR = Path(r'C:\Users\User\Desktop\research_data\candles15')
OUT = DATA_DIR.parent / 'study_capture_run' / 'result.json'
CUTOFF_KST = '2026-11-05T11:30:00'
STEP = 4 * HOUR                 # 현재 정책 진입 판단 간격(기존 연구와 같음, 겹침 축소)
HOURS = 72                      # 모든 정책의 최대 보유(자료 끝 처리를 같게 하려고 168h 대신 72h)
COST = 0.0085                   # 실측 왕복 비용
TESTS = 10
LEVEL = 1 - 0.05 / TESTS / 2
BOOT = 2000
SEED = 20261105
BLOCK_DAYS = 7
CASES = ('KRW-EDGE', 'KRW-CAP', 'KRW-ELSA', 'KRW-BEAM', 'KRW-DRV')

P0 = dict(stop=0.045, guard=(0.03, 0.30), atr_k=None, rank=9)
IDEA1 = {'A1 ATR×2': dict(P0, atr_k=2.0), 'A1 ATR×3': dict(P0, atr_k=3.0), 'A1 반납50%': dict(P0, guard=(0.03, 0.50))}
# 사용자 추가 요청: 순위 이탈 즉시 매도(R0=P0) vs 기다렸다 손절·이익보호로만 매도
RANKX = {'R1 순위매도 없음': dict(P0, rank=None), 'R2 손실일 때만 순위매도': dict(P0, rank_loss_only=True),
         'R1+ATR×3': dict(P0, rank=None, atr_k=3.0)}
ORDERS = Path(r'C:\Users\User\Desktop\코인 트레이딩\v5-app\v5\data\pnl\orders.json')
BREAK_EXIT = dict(stop=0.045, guard=(0.03, 0.30), atr_k=None, rank=None)
BREAK_M = (3, 5, 10)


def atr(bars, T, n=14):
    trs, prev = [], None
    for s in range(T - (n + 1) * B, T, B):
        b = bars.get(s)
        if b is None:
            continue
        if prev is not None:
            trs.append(max(b[1] - b[2], abs(b[1] - prev), abs(b[2] - prev)))
        prev = b[3]
    return sum(trs) / len(trs) if len(trs) >= n // 2 else None


def simulate(bars, market, T, entry, pol, state, hours=HOURS):
    """T에 entry로 진입 → (총수익, 보유 시간, 사유, 보유 중 고점). 한 봉 안에서는 손절 → 보호선 순으로 불리하게 먼저 본다."""
    stop = entry * (1 - pol['stop'])
    arm, give = pol['guard']
    a = atr(bars, T) if pol['atr_k'] else None
    peak = entry
    end = T + hours * HOUR
    s = T
    last = None
    while s < end:
        b = bars.get(s)
        if b is not None:
            last = b
            if b[2] <= stop:
                return min(stop, b[0]) / entry - 1, (s - T) / HOUR, 'stop', peak
            if peak >= entry * (1 + arm):
                gs = entry + (peak - entry) * (1 - give)
                if a is not None and peak < entry * 1.10:
                    gs = max(peak - pol['atr_k'] * a, entry)
                elif a is not None:
                    gs = max(gs, peak - pol['atr_k'] * a)
                if b[2] <= gs:
                    return min(gs, b[0]) / entry - 1, (s - T) / HOUR, 'guard', peak
            peak = max(peak, b[1])
        nxt = s + B
        if pol['rank'] is not None and nxt % HOUR == 0:
            snap = state.get(nxt)
            if snap is not None and b is not None and not (pol.get('rank_loss_only') and b[3] >= entry):
                info = snap[0].get(market)
                if info is None or info[1] > pol['rank'] or info[0] < 0:
                    return b[3] / entry - 1, (nxt - T) / HOUR, 'rank', peak
        s = nxt
    return (last[3] / entry - 1, hours, 'time', peak) if last else None


def ci(rows):
    """rows: [(블록, 값)] → 평균의 블록 부트스트랩 LEVEL 구간."""
    g = defaultdict(list)
    for k, d in rows:
        g[k].append(d)
    ids = sorted(g)
    rng = random.Random(SEED)
    ds = []
    for _ in range(BOOT):
        s = c = 0.0
        for _ in ids:
            v = g[rng.choice(ids)]
            s += sum(v)
            c += len(v)
        if c:
            ds.append(s / c)
    ds.sort()
    return ds[int((1 - LEVEL) / 2 * len(ds))], ds[int((1 + LEVEL) / 2 * len(ds)) - 1]


def summary(rows, periods):
    """rows: [(블록, 구간, 값)] → 평균·구간·구간별 평균."""
    v = [x for _, _, x in rows]
    lo, hi = ci([(k, x) for k, _, x in rows])
    per = [sum(x for _, p, x in rows if p == q) / max(1, sum(1 for _, p, _ in rows if p == q)) for q in periods]
    return {'n': len(v), 'mean': sum(v) / len(v) if v else 0.0, 'ci': [lo, hi], 'per_period': per,
            'win': sum(1 for x in v if x > 0) / len(v) if v else 0.0}


def show(name, r, unit='%'):
    print(f"  {name:14} n={r['n']:5} 평균 {r['mean']*100:+.3f}{unit} [{r['ci'][0]*100:+.3f}, {r['ci'][1]*100:+.3f}] "
          f"구간별 {[round(x*100, 2) for x in r['per_period']]} 승률 {r['win']:.0%}")


def main():
    cut = pit.to_epoch(CUTOFF_KST)
    data = {m: b for m, b in pit.load_dir(DATA_DIR, cutoff=cut).items() if b}
    allmin = min(min(b) for b in data.values())
    t_start = math.ceil((allmin + 120 * B) / STEP) * STEP
    last_entry = cut - HOURS * HOUR - 2 * HOUR
    span = (last_entry - t_start) / 3
    periods = ('P1', 'P2', 'P3')

    def period(T):
        return periods[min(2, int((T - t_start) // span))]

    def block(T):
        return int((T - t_start) // (BLOCK_DAYS * 86400))
    print(f'진입 구간 {pit.from_epoch_kst(t_start)} ~ {pit.from_epoch_kst(last_entry)} · 종목 {len(data)}개 · 비용 {COST:.2%} · 구간 {1 - (1 - LEVEL) * 2:.4f}', flush=True)
    state = rx.hourly_state(data, t_start - HOUR, cut)
    res = {'cutoff': CUTOFF_KST, 'cost': COST, 'level': LEVEL, 'idea1': {}, 'idea2': {}, 'idea3': {}}

    # 현재 정책 진입(4시간마다 7위 안·0<상승률<15%에서 3종목)
    entries = []
    T = t_start
    while T <= last_entry:
        snap = state.get(T)
        if snap and snap[1] >= rx.MIN_UNIVERSE:
            for m, rs in rx.candidates(snap[0], rx.SCAN):
                c = data[m].get(T - B)
                if not c:
                    continue
                out = {'P0 현재': simulate(data[m], m, T, c[3], P0, state)}
                for n, pol in {**IDEA1, **RANKX}.items():
                    out[n] = simulate(data[m], m, T, c[3], pol, state)
                if all(out.values()):
                    entries.append({'T': T, 'm': m, 'entry': c[3], 'o': out})
        T += STEP
    print('\n[기준] 현재 정책 진입', len(entries))
    base = summary([(block(e['T']), period(e['T']), e['o']['P0 현재'][0] - COST) for e in entries], periods)
    show('P0 현재 순수익', base)
    res['p0'] = base

    print('\n[아이디어1] 보호선 넓히기: 같은 진입에서 정책별 순수익과 P0 대비 차이')
    for n in IDEA1:
        net = summary([(block(e['T']), period(e['T']), e['o'][n][0] - COST) for e in entries], periods)
        diff = summary([(block(e['T']), period(e['T']), e['o'][n][0] - e['o']['P0 현재'][0]) for e in entries], periods)
        hold = sum(e['o'][n][1] for e in entries) / len(entries)
        big = [e for e in entries if e['o'][n][3] >= e['entry'] * 1.15]
        cap = sum(e['o'][n][0] for e in big) / len(big) if big else 0.0
        cap0 = sum(e['o']['P0 현재'][0] for e in big) / len(big) if big else 0.0
        show(n + ' 순수익', net)
        show('  └ P0 대비', diff, '%p')
        print(f"    평균 보유 {hold:.1f}h · 보유 중 +15% 이상 올랐던 {len(big)}건의 평균 총수익 {cap*100:+.2f}% (P0 {cap0*100:+.2f}%)")
        res['idea1'][n] = {'net': net, 'diff': diff, 'hold_h': hold, 'big_n': len(big), 'big_gross': cap, 'big_gross_p0': cap0}

    print('\n[순위 이탈 비교] 같은 진입에서 R0(지금: 순위 이탈 즉시 매도) 대비')
    res['rankx'] = {}
    for n in RANKX:
        net = summary([(block(e['T']), period(e['T']), e['o'][n][0] - COST) for e in entries], periods)
        diff = summary([(block(e['T']), period(e['T']), e['o'][n][0] - e['o']['P0 현재'][0]) for e in entries], periods)
        hold = sum(e['o'][n][1] for e in entries) / len(entries)
        why = defaultdict(int)
        for e in entries:
            why[e['o'][n][2]] += 1
        losers = [e for e in entries if e['o']['P0 현재'][2] == 'rank' and e['o']['P0 현재'][0] < 0]
        lose_after = sum(e['o'][n][0] for e in losers) / len(losers) if losers else 0.0
        lose_before = sum(e['o']['P0 현재'][0] for e in losers) / len(losers) if losers else 0.0
        show(n + ' 순수익', net)
        show('  └ R0 대비', diff, '%p')
        print(f"    평균 보유 {hold:.1f}h (R0 {sum(e['o']['P0 현재'][1] for e in entries) / len(entries):.1f}h) · 청산 사유 {dict(why)}")
        print(f"    R0에서 손실로 순위 매도된 {len(losers)}건: R0 평균 {lose_before*100:+.2f}% → {n} {lose_after*100:+.2f}%")
        res['rankx'][n] = {'net': net, 'diff': diff, 'hold_h': hold, 'why': dict(why), 'rank_losers': [len(losers), lose_before, lose_after]}

    print('\n[실제 거래 사례] 봇의 실제 매수(11-02~)를 같은 가격·시각에서 정책별로 재생 (실제 결과와 나란히)')
    cases = []
    if ORDERS.exists():
        rows = sorted((r for r in json.loads(ORDERS.read_text(encoding='utf-8'))['orders'].values()
                       if (r.get('identifier') or '').startswith('v5-') and r.get('trades')), key=lambda r: r['created_at'])
        open_buy = {}
        for r in rows:
            v = sum(float(t['volume']) for t in r['trades'])
            f = sum(float(t['funds']) for t in r['trades'])
            if v <= 0:
                continue
            ts = int(pit.datetime.fromisoformat(r['created_at']).timestamp())
            if r['side'] == 'bid':
                open_buy[r['market']] = (ts, f / v)
            elif r['market'] in open_buy:
                bt, bp = open_buy.pop(r['market'])
                cases.append((r['market'], bt, bp, (f / v) / bp - 1, (ts - bt) / HOUR))
        for m, bp in open_buy.items():
            cases.append((m, bp[0], bp[1], None, None))
    pol_all = {'R0 지금': P0, **RANKX}
    table = []
    for m, bt, bp, actual, held in cases:
        if m not in data:
            continue
        T = (bt // B + 1) * B
        outs = {n: simulate(data[m], m, T, bp, pol, state, hours=HOURS) for n, pol in pol_all.items()}
        if not all(outs.values()):
            continue
        table.append({'m': m, 'T': bt, 'actual': actual, 'held': held, **{n: o[0] for n, o in outs.items()}, **{n + '_why': o[2] for n, o in outs.items()}})
        a = '보유 중' if actual is None else f'{actual*100:+.2f}%({held:.1f}h)'
        print(f"  {pit.from_epoch_kst(bt)[5:16]} {m[4:]:7} 실제 {a:15} " + ' | '.join(f"{n.split()[0]} {o[0]*100:+.2f}%({o[2]})" for n, o in outs.items()))
    if table:
        print('  평균 총수익: ' + ' | '.join(f"{n.split()[0]} {sum(t[n] for t in table)/len(table)*100:+.2f}%" for n in pol_all)
              + f" · 실제 청산분 평균 {sum(t['actual'] for t in table if t['actual'] is not None)/max(1, sum(t['actual'] is not None for t in table))*100:+.2f}% (n={len(table)}, 비용 전)")
    res['cases'] = table

    print('\n[아이디어2] P0 이익보호 청산 뒤 24시간 안 재진입(과열 상한 없음) — 재진입 거래만의 순수익')
    for label, need_break in (('재진입(고점 돌파)', True), ('재진입(조건 없음)', False)):
        rows = []
        for e in entries:
            r, h, why, peak = e['o']['P0 현재']
            if why != 'guard' or r <= 0:
                continue
            x = e['T'] + int(h * HOUR)
            S = (x // HOUR + 1) * HOUR
            while S <= x + 24 * HOUR and S <= cut - HOURS * HOUR:
                snap = state.get(S)
                c = data[e['m']].get(S - B)
                info = snap[0].get(e['m']) if snap else None
                if c and info and info[1] <= rx.SCAN and info[0] > 0 and (not need_break or c[3] > peak):
                    out = simulate(data[e['m']], e['m'], S, c[3], P0, state)
                    if out:
                        rows.append((block(S), period(S), out[0] - COST))
                    break
                S += HOUR
        if rows:
            r = summary(rows, periods)
            show(label, r)
            res['idea2'][label] = r

    print('\n[아이디어3] 급등 초기 매수(거래대금 M배 + 15분 +3% 양봉, 그 봉 종가 진입, 손절 4.5%·보호 3%/30%·최대 72h)')
    for M in BREAK_M:
        rows, cases = [], []
        for m, bars in data.items():
            if m in pit.EXCLUDE:
                continue
            nxt_ok = 0
            for s in sorted(bars):
                T = s + B
                if T < t_start or T > last_entry or T < nxt_ok:
                    continue
                b = bars[s]
                if b[3] / b[0] - 1 < 0.03:
                    continue
                hist = [bars[k][4] for k in range(s - 96 * B, s, B) if k in bars]
                if len(hist) < 90 or b[4] < M * (sum(hist) / len(hist)):
                    continue
                snap = state.get(T // HOUR * HOUR)
                if not snap or m not in snap[0]:
                    continue                       # 거래대금 상위 30 밖은 제외(봇 유니버스와 같게)
                out = simulate(bars, m, T, b[3], BREAK_EXIT, state)
                if out:
                    rows.append((block(T), period(T), out[0] - COST))
                    if m in CASES:
                        cases.append((pit.from_epoch_kst(T)[5:16], m[4:], out[0], out[2], out[1]))
                nxt_ok = T + 24 * HOUR
        r = summary(rows, periods)
        show(f'M={M}', r)
        res['idea3'][f'M{M}'] = r
        for c in cases[-6:]:
            print(f'      사례 {c[0]} {c[1]:5} 총수익 {c[2]*100:+.2f}% ({c[3]}, {c[4]:.1f}h)')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    print('저장:', OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
