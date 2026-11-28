"""아침 1분봉(매일 08:00~10:30 KST) 받기 → v5-app/v5/data/analysis_20261128/m1/<YYYYMMDD>/<종목>.json.
대상: 그날 09:00 직전 24시간 거래대금 10억 이상 종목 전부(c15m 15분봉으로 판단). 급등 종목만이 아니라 안 오른 종목도 받아 헛신호를 센다.
읽기 전용, 주문 없음. 봇 조회와 겹쳐 429가 나지 않게 천천히 받는다."""
import datetime as dt, json, time, urllib.request
from pathlib import Path
BASE = Path(r'C:\Users\User\Desktop\코인 트레이딩\v5-app\v5\data\analysis_20261128')
KST = dt.timezone(dt.timedelta(hours=9)); UTC = dt.timezone.utc
EXCLUDE = {'KRW-USDT', 'KRW-USDC', 'KRW-USDE', 'KRW-USD1', 'KRW-DAI'}

def get(u):
    for i in range(5):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'x'}), timeout=10))
        except Exception:
            time.sleep(1 + i)
    return None

def targets():
    out = {}
    for f in sorted((BASE / 'c15m').glob('KRW-*.json')):
        if f.stem in EXCLUDE: continue
        bars = json.loads(f.read_text())
        by = {b['t']: b for b in bars}
        days = sorted({(dt.datetime.fromisoformat(b['t']).replace(tzinfo=UTC).astimezone(KST)).date() for b in bars})
        for d in days:
            nine = dt.datetime(d.year, d.month, d.day, 9, tzinfo=KST).astimezone(UTC)
            v = sum(by.get((nine - dt.timedelta(minutes=15 * k)).strftime('%Y-%m-%dT%H:%M:%S'), {}).get('v', 0) for k in range(1, 97))
            if v >= 1e9 and d >= dt.date(2026, 10, 30):
                out.setdefault(d, []).append(f.stem)
    return out

def main():
    tg = targets()
    print('대상', sum(len(v) for v in tg.values()), '건', flush=True)
    for d, ms in sorted(tg.items()):
        od = BASE / 'm1' / f'{d:%Y%m%d}'; od.mkdir(parents=True, exist_ok=True)
        to = dt.datetime(d.year, d.month, d.day, 10, 30, tzinfo=KST).astimezone(UTC)
        if to > dt.datetime.now(UTC): continue
        for m in ms:
            f = od / f'{m}.json'
            if f.exists(): continue
            x = get(f'https://api.upbit.com/v1/candles/minutes/1?market={m}&count=150&to={to:%Y-%m-%dT%H:%M:%S}')
            time.sleep(.13)
            if x is None: continue
            rows = sorted(({'t': r['candle_date_time_utc'], 'o': r['opening_price'], 'h': r['high_price'], 'l': r['low_price'],
                            'c': r['trade_price'], 'v': r['candle_acc_trade_price']} for r in x), key=lambda r: r['t'])
            f.write_text(json.dumps(rows))
        print(d, len(ms), flush=True)

if __name__ == '__main__':
    main()
