"""Public-data forward observer. Features and executable-book cost estimates, NO orders.

This is not a paper-trading engine: snapshots do not imply fills or returns.
The fixed universe rule chooses up to six price/volume-only cautions plus six
unflagged KRW markets by observable 24h turnover, excluding stablecoins.
"""
import argparse
import datetime as dt
import hashlib
import html
import json
import math
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'v5'))
import upbit
import strategy as st

VERSION = 'public-features-v1'
PRICE_VOLUME = {'PRICE_FLUCTUATIONS', 'TRADING_VOLUME_SOARING'}


def positive(x):
    return type(x) in (int, float) and math.isfinite(x) and x > 0


def cohort(detail):
    if detail['investment_warning']:
        return 'excluded_investment_warning'
    if not detail['warning']:
        return 'unflagged'
    if detail['caution_types'] and set(detail['caution_types']) <= PRICE_VOLUME:
        return 'price_volume_only'
    return 'excluded_other_or_unknown_caution'


def bars(raw, now):
    result = []
    for row in raw:
        start = dt.datetime.fromisoformat(row['candle_date_time_utc']).replace(tzinfo=dt.timezone.utc)
        end = start.timestamp() + 300
        if end > now - 20:  # allow exchange publication time, never use the open bar
            continue
        if not all(positive(row[k]) for k in ('opening_price', 'high_price', 'low_price', 'trade_price')):
            continue
        result.append({'t': start.isoformat(), 'end': end, 'o': row['opening_price'],
                       'h': row['high_price'], 'l': row['low_price'], 'c': row['trade_price'],
                       'turnover': row['candle_acc_trade_price']})
    return sorted(result, key=lambda b: b['end'])


def features(raw, now):
    data = bars(raw, now)
    if len(data) < 21:
        return {'available': False, 'reason': 'less_than_21_completed_bars'}
    recent = data[-21:]
    last, prev = recent[-1], recent[-2]
    contiguous = all(b['end'] - a['end'] == 300 for a, b in zip(recent, recent[1:]))
    stale = now - last['end'] > 380
    median = statistics.median(b['turnover'] for b in recent[:-1])
    atr = st.wilder_atr(recent)
    mean = statistics.mean(b['c'] for b in recent[-20:])
    return {'available': True, 'bar_end': last['end'], 'bar_start': last['t'],
            'contiguous': contiguous, 'stale_bar': stale,
            'breakout20': last['c'] > max(b['h'] for b in recent[:-1]) if contiguous and not stale else None,
            'recovery_prev_downbar': (prev['c'] < prev['o'] and last['c'] > prev['h']) if contiguous and not stale else None,
            'turnover_vs_prior20_median': last['turnover'] / median if positive(median) else None,
            'atr14_pct': atr / last['c'] if positive(atr) else None,
            'distance_sma20_pct': last['c'] / mean - 1}


def book_cost(book, budget=50000., fee=.0005):
    """Same-book hypothetical round trip, including size/depth and assumed fees.

    Future exit spread/depth is unknown. This is a cost screen, not a fill.
    """
    units = book['orderbook_units']
    if not units or not positive(budget) or not 0 <= fee < 1:
        raise ValueError('invalid book or cost assumption')
    if any(not positive(u[k]) for u in units for k in ('ask_price', 'bid_price')):
        raise ValueError('invalid book price')
    if units[0]['ask_price'] < units[0]['bid_price']:
        raise ValueError('crossed book')
    left, qty = budget, 0.
    for u in units:
        size = u['ask_size']
        if type(size) not in (int, float) or not math.isfinite(size) or size < 0:
            raise ValueError('invalid ask size')
        spend = min(left, u['ask_price'] * size)
        qty += spend / u['ask_price']; left -= spend
        if left < 1e-7: break
    result = {'book_timestamp_ms': book['timestamp'], 'sample_budget_krw': budget,
              'fee_assumption_per_side': fee,
              'spread_pct': (units[0]['ask_price'] - units[0]['bid_price']) / units[0]['ask_price'],
              'roundtrip_cost_pct_same_book': None, 'depth_sufficient': False}
    if left >= 1e-7: return result
    remaining, proceeds = qty, 0.
    for u in units:
        size = u['bid_size']
        if type(size) not in (int, float) or not math.isfinite(size) or size < 0:
            raise ValueError('invalid bid size')
        sold = min(remaining, size)
        proceeds += sold * u['bid_price']; remaining -= sold
        if remaining < 1e-10: break
    if remaining >= 1e-10: return result
    result.update(depth_sufficient=True, buy_vwap=budget / qty, sell_vwap=proceeds / qty,
                  roundtrip_cost_pct_same_book=1 - proceeds * (1-fee) / (budget * (1+fee)))
    return result


def collect():
    detail = {r['market']: dict(name=r.get('korean_name', ''), **upbit.market_warning_details(r))
              for r in upbit._get('/v1/market/all', {'is_details': 'true'}) if r['market'].startswith('KRW-')}
    ticks = upbit.tickers(detail)
    ordered = sorted((m for m in detail if m in ticks and m not in st.Params().exclude),
                     key=lambda m: -ticks[m].get('acc_trade_price_24h', 0))
    groups = {kind: [m for m in ordered if cohort(detail[m]) == kind][:6]
              for kind in ('price_volume_only', 'unflagged')}
    observations = []
    for kind, markets in groups.items():
        for market in markets:
            row = {'market': market, 'cohort': kind, **detail[market],
                   'turnover24h': ticks[market].get('acc_trade_price_24h')}
            try:
                raw = upbit._get('/v1/candles/minutes/5', {'market': market, 'count': 25})
                row['features'] = features(raw, time.time())
            except Exception as ex:
                row['error'] = type(ex).__name__
            observations.append(row)
            time.sleep(1.05)  # keep additional candle traffic around one request/second
    # Quote requested AFTER feature evaluation; even this does not simulate a fill.
    books = {b['market']: b for b in upbit._get('/v1/orderbook', {'markets': ','.join(r['market'] for r in observations)})} if observations else {}
    now = time.time()
    for row in observations:
        book = books.get(row['market'])
        try:
            row['cost'] = book_cost(book)
            row['cost']['stale_book'] = not -2 <= now - book['timestamp']/1000 <= 15
        except Exception as ex:
            row['book_error'] = type(ex).__name__
        row['observed_epoch'] = now
    return {'version': VERSION, 'observed_at': dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec='seconds'),
            'observed_epoch': now, 'orders_enabled': False, 'sample_selection': groups,
            'warnings': {m: d for m, d in detail.items() if d['warning']}, 'rows': observations}


def report(data):
    esc = lambda x: html.escape(str(x))
    rows = []
    for r in data['rows']:
        f, c = r.get('features', {}), r.get('cost', {})
        spread, cost = c.get('spread_pct'), c.get('roundtrip_cost_pct_same_book')
        fmt = lambda x: f'{x:.2%}' if type(x) in (int, float) and math.isfinite(x) else '확인 불가'
        cost_text = '호가 시각 확인 필요' if c.get('stale_book') else fmt(cost)
        rows.append(f'<tr><td>{esc(r["market"])}</td><td>{esc(r["cohort"])}</td><td>{fmt(spread)}</td>'
                    f'<td>{cost_text}</td><td>{esc(f.get("breakout20"))}</td><td>{esc(f.get("recovery_prev_downbar"))}</td>'
                    f'<td>{esc(f.get("contiguous"))} / {esc(f.get("stale_bar"))}</td></tr>')
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta http-equiv="refresh" content="30"><title>전술 후보 관측</title>
<style>body{{font:14px/1.7 "Malgun Gothic",sans-serif;margin:28px;background:#f3f6fb;color:#233b55}}table{{background:white;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #dde4ee;text-align:left}}</style>
<h1>경보·무경보 전술 후보 관측</h1><p>{esc(data['observed_at'])} · 주문 기능 없음 · 체결·수익 계산 아님</p>
<p>각 집단 거래대금 상위 최대6개를 관측합니다. 비용은 5만원을 같은 호가에서 사고파는 가정, 편도 수수료0.05% 가정입니다.<br>실제 계좌 수수료와 미래 청산 비용은 다를 수 있습니다. True는 관측 특징이며 매수 추천이 아닙니다.</p>
<table><tr><th>종목</th><th>집단</th><th>호가 차이</th><th>호가·수수료 가정 비용</th><th>20봉 돌파</th><th>직전 하락봉 회복</th><th>연속봉 / 오래된 봉</th></tr>{''.join(rows)}</table></html>'''


def atomic(path, text):
    temp = path.with_suffix('.tmp')
    temp.write_text(text, encoding='utf-8')
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'tactical_observer')
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    import msvcrt
    with (args.output / 'observer.lock').open('a+b') as lock:
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        try: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError: return
        statefile = args.output / 'seen.json'
        seen = json.loads(statefile.read_text(encoding='utf-8')) if statefile.exists() else {}
        while not (args.output / 'stop.txt').exists():
            try:
                data = collect()
                with (args.output / 'snapshots.jsonl').open('a', encoding='utf-8') as log:
                    log.write(json.dumps(data, ensure_ascii=False)+'\n')
                with (args.output / 'features.jsonl').open('a', encoding='utf-8') as log:
                    for row in data['rows']:
                        end = row.get('features', {}).get('bar_end')
                        if end and seen.get(row['market']) != end:
                            event = dict(row, version=VERSION)
                            event['id'] = hashlib.sha256(f'{VERSION}:{row["market"]}:{end}'.encode()).hexdigest()[:24]
                            log.write(json.dumps(event, ensure_ascii=False)+'\n')
                            seen[row['market']] = end
                atomic(statefile, json.dumps(seen))
                atomic(args.output / 'latest.json', json.dumps(data, ensure_ascii=False, indent=2))
                atomic(args.output / 'index.html', report(data))
            except Exception as ex:
                with (args.output / 'errors.jsonl').open('a', encoding='utf-8') as log:
                    log.write(json.dumps({'epoch': time.time(), 'error': type(ex).__name__})+'\n')
                if not args.watch: raise
            if not args.watch: break
            for _ in range(60):
                if (args.output / 'stop.txt').exists(): break
                time.sleep(1)


if __name__ == '__main__':
    main()
