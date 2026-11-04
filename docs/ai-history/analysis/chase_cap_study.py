"""과열 상한(18시간 상승률 15%) 근거 재분석. 공개 시세만 사용, 주문·키 없음.

- 최근 DAYS일, 지금 거래대금 상위 TOP_FETCH 종목의 15분봉(생존 편향: 상장폐지·순위 이탈 종목 누락)
- 판단 시점마다 그 시점 24시간 거래대금으로 상위 30을 재구성(미래 정보 없음), 18시간(72봉) 상승률 순위 상위 5 중 상승률 > 0 = 매수 신호
- 같은 종목은 4시간에 한 번만 센다(겹침 축소). 진입은 신호 다음 봉 시가.
- 결과: 1·4·12·24시간 뒤 종가 수익률(왕복 비용 0.3% 차감), 4시간 안 최대 상승(MFE)·최대 하락(MAE),
  그리고 현행 청산 근사(손절 −4.5%, 고점 +3% 이후 이익 40% 반납 시 매도, 최대 24시간) 결과.
"""
import json
import statistics as S
import sys
import time
from pathlib import Path

sys.path.insert(0, r'C:\Users\User\Desktop\코인 트레이딩\v5-app\v5')
import upbit

DAYS, TOP_FETCH, UNIVERSE, TOPN, L, COST = 14, 80, 30, 5, 72, 0.003
EXCLUDE = {'KRW-USDT', 'KRW-USDC', 'KRW-USDE', 'KRW-USD1', 'KRW-DAI'}
CACHE = Path(__file__).with_name('chase_cache.json')


def fetch(m, n):
    out, to = [], None
    while len(out) < n:
        p = {'market': m, 'count': 200}
        if to:
            p['to'] = to
        r = upbit._get('/v1/candles/minutes/15', p)
        if not r:
            break
        out += r
        to = r[-1]['candle_date_time_utc'] + 'Z'
        time.sleep(0.11)
    return [{'t': x['candle_date_time_utc'], 'o': x['opening_price'], 'h': x['high_price'], 'l': x['low_price'],
             'c': x['trade_price'], 'v': x['candle_acc_trade_price']} for x in reversed(out)]


def load():
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    tick = upbit.tickers(upbit.krw_markets())
    top = [m for m in sorted(tick, key=lambda m: -tick[m]['acc_trade_price_24h']) if m not in EXCLUDE][:TOP_FETCH]
    data = {m: fetch(m, DAYS * 96 + L + 100) for m in top}
    CACHE.write_text(json.dumps(data))
    return data


def exit_current(path, E):
    """현행 근사: 손절 −4.5%, 고점이 +3% 이상이면 고점 이익의 40% 반납 시 매도, 최대 96봉(24시간). 봉 고가·저가 사용."""
    peak = E
    for b in path[:96]:
        if b['l'] <= E * 0.955:
            return 0.955 - 1 - COST
        peak = max(peak, b['h'])
        if peak >= E * 1.03:
            line = E + (peak - E) * 0.6
            if b['l'] <= line:
                return line / E - 1 - COST
    return path[min(95, len(path) - 1)]['c'] / E - 1 - COST if path else None


def main():
    data = load()
    times = sorted({b['t'] for bars in data.values() for b in bars})
    idx = {m: {b['t']: i for i, b in enumerate(bars)} for m, bars in data.items()}
    events, last = [], {}
    for t in times[L + 96:-97]:
        rows = []
        for m, bars in data.items():
            i = idx[m].get(t)
            if i is None or i < L + 96:
                continue
            vol24 = sum(b['v'] for b in bars[i - 95:i + 1])
            rows.append((vol24, m, i))
        uni = sorted(rows, reverse=True)[:UNIVERSE]
        ranked = sorted(((data[m][i]['c'] / data[m][i - L]['c'] - 1, m, i) for _, m, i in uni), reverse=True)[:TOPN]
        for rs, m, i in ranked:
            if rs <= 0:
                continue
            tt = time.mktime(time.strptime(t, '%Y-%m-%dT%H:%M:%S'))
            if tt - last.get(m, -1e18) < 4 * 3600:
                continue
            last[m] = tt
            bars = data[m]
            if i + 97 >= len(bars):
                continue
            E = bars[i + 1]['o']
            fwd = lambda h: bars[i + h * 4]['c'] / E - 1 - COST
            w4 = bars[i + 1:i + 17]
            events.append({'rs': rs, 'r1': fwd(1), 'r4': fwd(4), 'r12': fwd(12), 'r24': fwd(24),
                           'mfe': max(b['h'] for b in w4) / E - 1, 'mae': min(b['l'] for b in w4) / E - 1,
                           'cur': exit_current(bars[i + 1:], E), 'm': m})
    buckets = [(0, .05), (.05, .10), (.10, .15), (.15, .20), (.20, .30), (.30, 9)]
    print(f'기간 {DAYS}일 · 종목 {len(data)} · 신호 {len(events)}건 (같은 종목 4시간 1회) · 왕복 비용 {COST:.1%} 차감\n')
    print(f"{'18h 상승률':>10} {'건수':>4} {'1h':>7} {'4h':>7} {'12h':>7} {'24h':>7} {'현행청산':>8} {'승률(현행)':>9} {'4h MFE':>7} {'4h MAE':>7}")
    for lo, hi in buckets:
        g = [e for e in events if lo <= e['rs'] < hi]
        if not g:
            continue
        avg = lambda k: S.mean(e[k] for e in g) * 100
        win = sum(e['cur'] > 0 for e in g) / len(g) * 100
        print(f"{f'{lo:.0%}~{hi:.0%}' if hi < 9 else f'{lo:.0%}+':>10} {len(g):>4} {avg('r1'):>+6.2f}% {avg('r4'):>+6.2f}% {avg('r12'):>+6.2f}% "
              f"{avg('r24'):>+6.2f}% {avg('cur'):>+7.2f}% {win:>8.0f}% {avg('mfe'):>+6.2f}% {avg('mae'):>+6.2f}%")
    for cap in (.10, .15, .20, .25, .30, 9):
        g = [e for e in events if e['rs'] < cap]
        print(f"상한 {'없음' if cap > 1 else f'{cap:.0%}'}: 매수 {len(g)}건, 현행청산 평균 {S.mean(e['cur'] for e in g) * 100:+.2f}%, "
              f"합계 {sum(e['cur'] for e in g) * 100:+.1f}%p")


if __name__ == '__main__':
    main()
