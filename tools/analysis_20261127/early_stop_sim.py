"""보유 중 '손실 + 봉 연속 하락' 신호에서 손절가 전에 바로 파는 규칙 — 과거 실거래 왕복으로 비교 (2026-11-27 사용자 요청, 분석만).

대상: 손절 3.5% 적용(11-11 09:45) 뒤 매수한 봇 왕복(거래소 체결 v5-). 업비트 5분봉으로 보유 중 매 봉 마감을 다시 보며
규칙이 처음 맞는 봉 종가에 판 것으로 계산(매도 수수료 0.05%+미끄러짐 0.1%), 실제 결과(실현 손익)와 비교한다.
15분·1시간 연속 수는 5분봉을 묶어 계산(마감된 봉만). 결과·캐시는 실행본 data/analysis_20261127(공개 저장소 밖).
"""
import datetime as dt, json, sys, time, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis_20261111'))
import stop035_review as R

OUT = R.DATA / 'analysis_20261127'; CACHE = OUT / 'c5'
UTC = dt.timezone.utc


def candles(m, start, end):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{m}_{start:%m%d%H%M}_{end:%m%d%H%M}.json"
    if f.exists(): return json.loads(f.read_text())
    rows, to = {}, end + dt.timedelta(minutes=10)
    while to > start:
        u = f"https://api.upbit.com/v1/candles/minutes/5?market={m}&count=200&to={to.astimezone(UTC):%Y-%m-%dT%H:%M:%S}"
        for _ in range(3):
            try: d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'x'}), timeout=10)); break
            except Exception: time.sleep(1)
        else: d = []
        if not d: break
        for x in d: rows[x['candle_date_time_utc']] = x
        to = dt.datetime.fromisoformat(d[-1]['candle_date_time_utc']).replace(tzinfo=UTC); time.sleep(.12)
    out = sorted(({'t': k, 'o': x['opening_price'], 'c': x['trade_price'], 'l': x['low_price']} for k, x in rows.items()), key=lambda x: x['t'])
    f.write_text(json.dumps(out)); return out


def streak(cl):
    n = way = 0
    for i in range(len(cl) - 1, 0, -1):
        d = (cl[i] > cl[i - 1]) - (cl[i] < cl[i - 1])
        if d == 0 or (way and d != way): break
        way, n = d, n + 1
    return way * n


def series(bars):
    """각 5분봉 마감 시점의 (시각, 종가, s5, s15, s60)."""
    out = []
    for i, b in enumerate(bars):
        t = dt.datetime.fromisoformat(b['t']).replace(tzinfo=UTC) + dt.timedelta(minutes=5)
        cl5 = [x['c'] for x in bars[max(0, i - 12):i + 1]]
        agg = {}
        for k in (15, 60):
            closes = {}
            for x in bars[:i + 1]:
                xt = dt.datetime.fromisoformat(x['t']).replace(tzinfo=UTC)
                key = xt.replace(minute=(xt.minute // k) * k if k < 60 else 0, second=0)
                if key + dt.timedelta(minutes=k) <= t: closes[key] = x['c']   # 마감된 봉만
            agg[k] = streak([closes[q] for q in sorted(closes)][-13:])
        out.append((t, b['c'], streak(cl5), agg[15], agg[60]))
    return out


RULES = {}
for L in (0.01, 0.015, 0.02, 0.025, 0.03):
    for k in (2, 3, 4):
        RULES[f'손실{L:.1%}·5분{k}연속'] = (L, k, 0, 0)
        RULES[f'손실{L:.1%}·5분{k}연속·15분하락'] = (L, k, 1, 0)
        RULES[f'손실{L:.1%}·5분{k}연속·15분·1시간하락'] = (L, k, 1, 1)


def main():
    trips, _, _ = R.load()
    trips = [t for t in trips if t['open'] >= R.CUT]
    res = {n: {'fired': 0, 'delta': 0., 'false': 0, 'false_cost': 0., 'saved': 0.} for n in RULES}
    rows = []
    for j, t in enumerate(trips):
        entry = t['bf'] / t['bq']; inv = t['cost'] * t['sq'] / t['bq']
        bars = candles(t['m'], t['open'] - dt.timedelta(hours=1, minutes=5), t['close'])
        s = [x for x in series(bars) if t['open'] + dt.timedelta(minutes=5) <= x[0] < t['close'] - dt.timedelta(seconds=30)]
        rows.append({'m': t['m'], 'open': t['open'].isoformat(), 'pnl': t['pnl'], 'n_bars': len(s)})
        for n, (L, k, w15, w60) in RULES.items():
            hit = next((x for x in s if x[1] / entry - 1 <= -L and x[2] <= -k and (not w15 or x[3] <= -1) and (not w60 or x[4] <= -1)), None)
            if not hit: continue
            alt = inv * (hit[1] * (1 - 0.0015) / entry - 1)
            r = res[n]; r['fired'] += 1; d = alt - t['pnl']; r['delta'] += d
            if t['pnl'] > alt and t['pnl'] > 0: r['false'] += 1
            if d < 0: r['false_cost'] += d
            else: r['saved'] += d
        if j % 20 == 0: print(j, len(trips), flush=True)
    base = sum(t['pnl'] for t in trips)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'early_stop_sim_20261127.json').write_text(json.dumps({'base': base, 'n': len(trips), 'res': res, 'trips': rows}, ensure_ascii=False, default=str, indent=1), encoding='utf-8')
    print('trips', len(trips), 'actual pnl', round(base))
    for n, r in sorted(res.items(), key=lambda kv: -kv[1]['delta']):
        print(f"{n:34} 발동{r['fired']:4} 차이{r['delta']:+9,.0f}원  아낀{r['saved']:+8,.0f} 손해{r['false_cost']:+8,.0f}  (발동 뒤 실제로는 이익 끝 {r['false']}건)")


if __name__ == '__main__':
    main()
