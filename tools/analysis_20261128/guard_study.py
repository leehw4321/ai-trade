"""이익보호가 순간 흔들림에 팔리는 문제(2026-11-28 ELF: +3.1% 고점 뒤 26초 만에 순간 71.4원 매도, 직후 74원↑) 대응안 비교. 분석만.

대상: 손절 3.5% 적용(11-11 09:45) 뒤 봇 매수 왕복. 실제 매수 시각·가격부터 1분봉 24시간으로 다시 계산:
 손절 −3.5%(1분 저가), 이익보호 변형, 24시간 뒤 종가. 순위 매도·부분 익절·조기 손절은 넣지 않았다(모든 변형에 같게 빠짐).
 지금 방식 근사: 고점 = 1분 고가, 매도 = 1분 저가가 매도선 아래(순간 가격) → ELF 같은 순간 하락에 팔림.
비용 왕복 0.3%. 결과: 1건 평균, 합계(1건 8.2만 원 환산), 앞/뒤 절반, 이익보호로 판 뒤 24시간 안 더 오른 폭.
"""
import datetime as dt, json, sys, time, urllib.request
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis_20261111'))
import stop035_review as R

UTC = dt.timezone.utc
CACHE = R.DATA / 'analysis_20261128' / 'm1trip'
COST, STOP, KRW = 0.003, 0.035, 82000


def m1(m, start):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{m}_{start:%m%d%H%M}.json"
    if f.exists(): return json.loads(f.read_text())
    end = min(start + dt.timedelta(hours=24, minutes=1), dt.datetime.now(UTC) - dt.timedelta(minutes=1))
    rows, to = {}, end
    while to > start:
        u = f"https://api.upbit.com/v1/candles/minutes/1?market={m}&count=200&to={to.astimezone(UTC):%Y-%m-%dT%H:%M:%S}"
        d = None
        for k in range(4):
            try: d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'x'}), timeout=10)); break
            except Exception: time.sleep(1 + k)
        time.sleep(.13)
        if not d: break
        for x in d: rows[x['candle_date_time_utc']] = x
        to = dt.datetime.fromisoformat(d[-1]['candle_date_time_utc']).replace(tzinfo=UTC)
    s = f"{start.astimezone(UTC):%Y-%m-%dT%H:%M:%S}"
    out = sorted(({'t': k, 'o': x['opening_price'], 'h': x['high_price'], 'l': x['low_price'], 'c': x['trade_price']} for k, x in rows.items() if k > s), key=lambda r: r['t'])
    if end >= start + dt.timedelta(hours=24): f.write_text(json.dumps(out))
    return out


def sim(bars, e, rule):
    """rule: dict(arm, give, trig='tick'|'c1'|'c5', atr_k, floor=(arm2, keep), half)"""
    if not bars: return None, None, None
    o = np.array([b['o'] for b in bars]); h = np.array([b['h'] for b in bars]); l = np.array([b['l'] for b in bars]); c = np.array([b['c'] for b in bars])
    tr = np.maximum(h - l, np.abs(h - np.r_[c[0], c[:-1]]))
    stop = e * (1 - STOP); peak = e; armed = False; sold = []   # (비율, 가격, 이유)
    left = 1.0
    peak2 = None
    for k in range(len(c)):
        if l[k] <= stop:
            sold.append((left, min(stop, o[k]), '손절')); left = 0; break
        # 매도선(직전 봉까지의 고점 기준)
        if armed:
            band = rule['give'] * (peak - e)
            if rule.get('atr_k'):
                atr = tr[max(0, k - 15):k].mean() if k > 0 else tr[0]
                band = max(band, rule['atr_k'] * atr)
            if rule.get('floor') and peak < e * (1 + rule['floor'][0]):
                line = e * (1 + rule['floor'][1])
            else:
                line = peak - band
            trig = rule.get('trig', 'tick')
            px = l[k] if trig == 'tick' else c[k]
            if trig == 'c5' and (k + 1) % 5: px = np.inf
            if px <= line:
                fill = min(line, o[k]) if trig == 'tick' else c[k]
                if rule.get('half') and left == 1.0:
                    sold.append((0.5, fill, '이익보호 절반')); left = 0.5
                    peak2 = c[k]; rule = dict(rule, give=rule['half'], floor=None, atr_k=rule.get('atr_k'))
                    peak = max(peak, c[k])
                else:
                    sold.append((left, fill, '이익보호')); left = 0; break
        peak = max(peak, h[k])
        if not armed and peak >= e * (1 + rule['arm']): armed = True
    if left > 0: sold.append((left, c[-1], '24시간'))
    ret = sum(w * (p / e - 1) for w, p, _ in sold) - COST
    last_guard = next((k for k, (w, p, r) in enumerate(sold) if '이익보호' in r), None)
    return ret, sold[-1][2], (h.max() / e - 1)


RULES = {
    '지금 방식(+3% 켜짐·30% 반납·순간 가격)': dict(arm=0.03, give=0.30),
    '반납 50%': dict(arm=0.03, give=0.50),
    '1분 종가 확인': dict(arm=0.03, give=0.30, trig='c1'),
    '5분 종가 확인': dict(arm=0.03, give=0.30, trig='c5'),
    '반납 폭 ≥ 1분 ATR×2': dict(arm=0.03, give=0.30, atr_k=2),
    '반납 폭 ≥ 1분 ATR×3': dict(arm=0.03, give=0.30, atr_k=3),
    '반납 폭 ≥ 1분 ATR×3 + 1분 종가': dict(arm=0.03, give=0.30, atr_k=3, trig='c1'),
    '+5%에서 켜짐': dict(arm=0.05, give=0.30),
    '2단계(+6% 전엔 +1% 지킴)': dict(arm=0.03, give=0.30, floor=(0.06, 0.01)),
    '2단계(+6% 전엔 +1% 지킴) + 1분 종가': dict(arm=0.03, give=0.30, floor=(0.06, 0.01), trig='c1'),
    '절반 매도 후 나머지 반납 60%': dict(arm=0.03, give=0.30, half=0.60),
    '절반 매도 후 나머지 반납 60% + 1분 종가': dict(arm=0.03, give=0.30, half=0.60, trig='c1'),
}


def main():
    trips, _, _ = R.load()
    trips = [t for t in trips if t['open'] >= R.CUT]
    data = []
    for k, t in enumerate(trips):
        data.append((t, m1(t['m'], t['open'].astimezone(UTC).replace(second=0, microsecond=0))))
        if k % 20 == 0: print('받는 중', k, len(trips), flush=True)
    mid = sorted(t['open'] for t, _ in data)[len(data) // 2]
    print(f"\n대상 {len(data)}건 (실제 손익 {sum(t['pnl'] for t, _ in data):+,.0f}원)")
    print('| 이익보호 방식 | 1건 평균 | 합계(1건 8.2만 원) | 앞/뒤 절반 | 승률 | 이익보호로 끝 | 그중 판 뒤 24시간 안 +3%p 더 오른 건 |')
    print('|---|---|---|---|---|---|---|')
    base = None
    for name, rule in RULES.items():
        rs = []
        for t, bars in data:
            e = t['bf'] / t['bq']
            b = [x for x in bars if dt.datetime.fromisoformat(x['t']).replace(tzinfo=UTC) >= t['open'].astimezone(UTC).replace(second=0, microsecond=0)]
            r, why, mx = sim(b, e, dict(rule))
            if r is not None: rs.append((t['open'], r, why, mx))
        v = np.array([x[1] for x in rs])
        a = sum(x[1] for x in rs if x[0] < mid); b2 = sum(x[1] for x in rs if x[0] >= mid)
        g = [x for x in rs if '이익보호' in x[2]]
        miss = sum(1 for x in g if x[3] - (x[1] + COST) > 0.03)
        if base is None: base = (v.sum(), a, b2)
        print(f"| {name} | {v.mean():+.2%} | {v.sum() * KRW:+,.0f}원 ({(v.sum() - base[0]) * KRW:+,.0f}) | {(a - base[1]) * KRW:+,.0f} / {(b2 - base[2]) * KRW:+,.0f} | {(v > 0).mean():.0%} | {len(g)} | {miss} |")


if __name__ == '__main__':
    main()
