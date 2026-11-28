"""업비트 원화 전 종목 15분봉 한 달치(10-29 00:00 KST~현재) → v5-app/v5/data/analysis_20261128/c15m/<종목>.json.
9시 급등 점검(morning_movers.py)과 매수 기준 점검(entry_rule_study.py)에 쓴다. 읽기 전용, 주문 없음.
봇 시세 조회와 겹쳐 429가 나지 않게 초당 4회 남짓으로 천천히 받는다. 이미 받은 종목은 건너뛴다."""
import datetime as dt, json, time, urllib.request
from pathlib import Path
OUT = Path(r'C:\Users\User\Desktop\코인 트레이딩\v5-app\v5\data\analysis_20261128\c15m')
UTC = dt.timezone.utc
START = dt.datetime(2026, 10, 28, 15, 0, tzinfo=UTC)   # 10-29 00:00 KST

def get(u):
    for i in range(5):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'x'}), timeout=10))
        except Exception:
            time.sleep(1 + i)
    return None

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ms = [x['market'] for x in get('https://api.upbit.com/v1/market/all') if x['market'].startswith('KRW-')]
    now = dt.datetime.now(UTC).replace(second=0, microsecond=0)
    for n, m in enumerate(ms):
        f = OUT / f'{m}.json'
        if f.exists(): continue
        rows, to = {}, now
        while to > START:
            d = get(f'https://api.upbit.com/v1/candles/minutes/15?market={m}&count=200&to={to:%Y-%m-%dT%H:%M:%S}')
            time.sleep(.12)
            if not d: break
            for x in d: rows[x['candle_date_time_utc']] = x
            to = dt.datetime.fromisoformat(d[-1]['candle_date_time_utc']).replace(tzinfo=UTC)
        out = sorted(({'t': k, 'o': x['opening_price'], 'h': x['high_price'], 'l': x['low_price'], 'c': x['trade_price'],
                       'v': x['candle_acc_trade_price']} for k, x in rows.items() if k >= f'{START:%Y-%m-%dT%H:%M:%S}'), key=lambda r: r['t'])
        tmp = f.with_suffix('.tmp'); tmp.write_text(json.dumps(out)); tmp.replace(f)
        print(n, len(ms), m, len(out), flush=True)

if __name__ == '__main__':
    main()
