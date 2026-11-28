"""업비트 원화 종목(기간 중 거래대금 상위 45 안에 든 적 있는 종목) 15분봉(11-03~현재) 받기 → v5-app/v5/data/analysis_20261128/c15/<종목>.json. 읽기 전용, 주문 없음."""
import datetime as dt, json, time, urllib.request
from pathlib import Path
OUT = Path(r'C:\Users\User\Desktop\코인 트레이딩\v5-app\v5\data\analysis_20261128\c15')
UTC = dt.timezone.utc
START = dt.datetime(2026, 11, 2, 15, 0, tzinfo=UTC)   # 11-03 00:00 KST

def get(u):
    for i in range(5):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'x'}), timeout=10))
        except Exception:
            time.sleep(1 + i)
    return None

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    allm = [x['market'] for x in get('https://api.upbit.com/v1/market/all') if x['market'].startswith('KRW-')]
    # 일봉 거래대금으로 기간 중 하루라도 상위 45위 안에 든 종목만 15분봉을 받는다(거래대금 상위 30 재현에 충분)
    days = {}
    for m in allm:
        for x in get(f'https://api.upbit.com/v1/candles/days?market={m}&count=28') or []:
            days.setdefault(x['candle_date_time_kst'][:10], []).append((x['candle_acc_trade_price'], m))
        time.sleep(.15)
    keep = set()
    for d, rows in days.items():
        keep |= {m for _, m in sorted(rows, reverse=True)[:45]}
    ms = [m for m in allm if m in keep]
    print('대상', len(ms), '/', len(allm), flush=True)
    now = dt.datetime.now(UTC)
    for n, m in enumerate(ms):
        f = OUT / f'{m}.json'
        if f.exists(): continue
        rows, to = {}, now
        while to > START:
            d = get(f'https://api.upbit.com/v1/candles/minutes/15?market={m}&count=200&to={to:%Y-%m-%dT%H:%M:%S}')
            time.sleep(.22)
            if not d: break
            for x in d: rows[x['candle_date_time_utc']] = x
            to = dt.datetime.fromisoformat(d[-1]['candle_date_time_utc']).replace(tzinfo=UTC)
        out = sorted(({'t': k, 'o': x['opening_price'], 'h': x['high_price'], 'l': x['low_price'], 'c': x['trade_price'],
                       'v': x['candle_acc_trade_price']} for k, x in rows.items() if k >= f'{START:%Y-%m-%dT%H:%M:%S}'), key=lambda r: r['t'])
        f.write_text(json.dumps(out))
        print(n, len(ms), m, len(out), flush=True)

if __name__ == '__main__':
    main()
