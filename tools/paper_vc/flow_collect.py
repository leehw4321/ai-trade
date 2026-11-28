"""체결 강도·호가 잔량 기록 수집 (2026-11-28 사용자 지시 "3번도": 차트 밖 방향 단서를 2주 모아 예측력 확인). 조회 전용, 주문 없음.

1분마다 거래대금 상위 30 종목(10분마다 갱신)에 대해
  - 체결: 업비트 최근 체결(ticks)에서 직전 수집 이후 새 체결만 모아 매수 체결액(ask_bid=BID)·매도 체결액(ASK)·건수
          (한 번에 200건이라 다 못 받으면 truncated=True)
  - 호가: 매수·매도 총잔량(원), 1~5호가 잔량(원), 스프레드
를 한 줄씩 남긴다 → 코인 트레이딩/v5-app/v5/data/flow/flow_YYYYMMDD.jsonl (KST 날짜)
2주 뒤(12-12) indicator_study와 같은 방식으로 '체결 강도·호가 불균형 → 이후 상승 방향' 예측력을 본다.

  python tools/paper_vc/flow_collect.py --loop 60
"""
import argparse, datetime as dt, json, sys, time, urllib.request
from pathlib import Path

ROOT = Path(r'C:\Users\User\Desktop\코인 트레이딩')
OUT = ROOT / 'v5-app' / 'v5' / 'data' / 'flow'
KST = dt.timezone(dt.timedelta(hours=9))
END = dt.datetime(2026, 12, 12, 14, 0, tzinfo=KST)
EXCL = {'KRW-USDT', 'KRW-USDC', 'KRW-USDE', 'KRW-USD1', 'KRW-DAI'}


def get(url):
    for k in range(3):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'flow-collect'}), timeout=10))
        except Exception:
            time.sleep(1 + k)
    return None


def top30():
    ms = [x['market'] for x in get('https://api.upbit.com/v1/market/all') or [] if x['market'].startswith('KRW-')]
    tick = []
    for k in range(0, len(ms), 100):
        tick += get('https://api.upbit.com/v1/ticker?markets=' + ','.join(ms[k:k + 100])) or []
        time.sleep(.15)
    return [t['market'] for t in sorted((t for t in tick if t['market'] not in EXCL), key=lambda t: -t['acc_trade_price_24h'])[:30]]


def once(ms, seen):
    now = dt.datetime.now(KST)
    books = {b['market']: b for b in (get('https://api.upbit.com/v1/orderbook?markets=' + ','.join(ms)) or [])}
    rows = []
    for m in ms:
        ticks = get(f'https://api.upbit.com/v1/trades/ticks?market={m}&count=200') or []
        time.sleep(.13)
        last = seen.get(m)
        new = [t for t in ticks if last is None or t['sequential_id'] > last]
        if ticks: seen[m] = max(t['sequential_id'] for t in ticks)
        buy = sum(t['trade_price'] * t['trade_volume'] for t in new if t['ask_bid'] == 'BID')
        sell = sum(t['trade_price'] * t['trade_volume'] for t in new if t['ask_bid'] == 'ASK')
        b = books.get(m) or {}
        u = b.get('orderbook_units') or []
        row = {'t': now.isoformat(timespec='seconds'), 'm': m, 'buy': round(buy), 'sell': round(sell), 'n': len(new),
               'truncated': last is not None and len(new) >= len(ticks) and len(ticks) >= 200, 'first': last is None,
               'price': ticks[0]['trade_price'] if ticks else None}
        if u:
            row.update(bid_all=round(sum(x['bid_price'] * x['bid_size'] for x in u)), ask_all=round(sum(x['ask_price'] * x['ask_size'] for x in u)),
                       bid5=round(sum(x['bid_price'] * x['bid_size'] for x in u[:5])), ask5=round(sum(x['ask_price'] * x['ask_size'] for x in u[:5])),
                       spread=round(u[0]['ask_price'] / u[0]['bid_price'] - 1, 6))
        rows.append(row)
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / f'flow_{now:%Y%m%d}.jsonl').open('a', encoding='utf-8') as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + '\n')
    return len(rows)


def single_instance():
    import msvcrt
    OUT.mkdir(parents=True, exist_ok=True)
    f = (OUT / 'flow.lock').open('a+b')
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1); return f
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--loop', type=int, default=0)
    a = ap.parse_args()
    seen = {}
    if not a.loop:
        print(once(top30(), seen)); return 0
    lock = single_instance()
    if not lock: return 0
    ms, refreshed = [], 0
    while dt.datetime.now(KST) < END:
        t0 = time.time()
        try:
            if time.time() - refreshed > 600:
                ms = top30() or ms; refreshed = time.time()
            if ms: once(ms, seen)
        except Exception as ex:
            with (OUT / 'error.log').open('a', encoding='utf-8') as f: f.write(f'{dt.datetime.now(KST).isoformat()} {type(ex).__name__}: {ex}\n')
        time.sleep(max(5, a.loop - (time.time() - t0)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
