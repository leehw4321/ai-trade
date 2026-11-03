"""업비트 공개 API 15분봉 장기 수집기 (읽기 전용·이어받기 가능).

개인 키·계좌·주문을 전혀 쓰지 않는다. 시점별 거래대금 순위를 재구성하는 검증(pit_universe.py)의 원자료를 만든다.
종목마다 한 파일(JSON Lines)이며 한 줄이 한 봉이다: {"t": 봉 시작 KST, "o","h","l","c","v","val"}.
거래가 없던 봉은 업비트가 돌려주지 않으므로 결측으로 남는다(채우지 않는다).
상장폐지 종목은 마켓 목록에 없어 수집할 수 없다 → 생존 편향이 남는다. 결과 해석에 반드시 밝힌다.

사용: python collect_candles.py --out <폴더> [--days 180] [--markets KRW-BTC,KRW-ETH]
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = 'https://api.upbit.com/v1'
KST = timezone(timedelta(hours=9))
PAUSE = 0.12  # 공개 시세 호출 간격(초). 업비트 한도(초당 10회)보다 넉넉하게


def get(url, retries=6):
    delay = 1.0
    for _ in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(delay); delay = min(delay * 2, 30); continue
            if e.code == 404:
                return None
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(delay); delay = min(delay * 2, 30)
    raise RuntimeError('조회 실패: ' + url.split('?')[0])


def list_markets():
    rows = get(f'{API}/market/all?is_details=false') or []
    return sorted(r['market'] for r in rows if r['market'].startswith('KRW-'))


def load_existing(path):
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            row = json.loads(line)
            out[row['t']] = row
        except (ValueError, KeyError):
            continue  # 쓰는 도중 끊긴 마지막 줄은 버리고 다시 받는다
    return out


def save(path, rows):
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        for t in sorted(rows):
            f.write(json.dumps(rows[t], ensure_ascii=False, separators=(',', ':')) + '\n')
    tmp.replace(path)


FMT = '%Y-%m-%dT%H:%M:%S'


def collect_market(market, out_dir, since):
    path = out_dir / f'{market}.jsonl'
    rows = load_existing(path)
    since_kst = since.astimezone(KST).strftime(FMT)

    def sweep(upper, stop_at=None):
        """upper(UTC)부터 과거로 200개씩. 이미 가진 봉(stop_at)이나 since에 닿으면 멈춘다."""
        to = upper
        while True:
            batch = get(f"{API}/candles/minutes/15?market={market}&count=200&to={to.strftime('%Y-%m-%dT%H:%M:%SZ')}")
            time.sleep(PAUSE)
            if not batch:
                return
            for c in batch:
                t = c['candle_date_time_kst']
                rows[t] = {'t': t, 'o': c['opening_price'], 'h': c['high_price'], 'l': c['low_price'], 'c': c['trade_price'],
                           'v': c['candle_acc_trade_volume'], 'val': c['candle_acc_trade_price']}
            earliest = batch[-1]['candle_date_time_kst']
            to = datetime.fromisoformat(batch[-1]['candle_date_time_utc']).replace(tzinfo=timezone.utc)
            if len(batch) < 200 or earliest <= since_kst or (stop_at is not None and earliest <= stop_at):
                return

    newest = max(rows) if rows else None
    oldest = min(rows) if rows else None
    sweep(datetime.now(timezone.utc), stop_at=newest)  # 최신 쪽 이어받기(처음이면 since까지)
    if oldest is not None and oldest > since_kst:  # 과거 쪽 확장
        sweep(datetime.fromisoformat(oldest).replace(tzinfo=KST).astimezone(timezone.utc))
    save(path, rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--days', type=int, default=180)
    ap.add_argument('--markets', default='')
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    since = datetime.now(timezone.utc) - timedelta(days=a.days)
    markets = [m for m in a.markets.split(',') if m] or list_markets()
    print(f'{len(markets)}개 종목, {a.days}일, 출력 {out}', flush=True)
    done = 0
    for m in markets:
        try:
            n = collect_market(m, out, since)
        except Exception as e:  # 한 종목의 실패가 전체를 멈추지 않게 한다
            print(f'{m} 실패: {type(e).__name__}: {str(e)[:80]}', flush=True)
            continue
        done += 1
        print(f'[{done}/{len(markets)}] {m} {n}봉', flush=True)
    print('완료', flush=True)


if __name__ == '__main__':
    sys.exit(main())
