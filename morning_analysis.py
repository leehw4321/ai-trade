"""Read-only historical data collection and replay. No order submission endpoints."""
import argparse
import bisect
import csv
import datetime as dt
import hashlib
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import upbit_bot as bot
import paper_event as paper

KST = dt.timezone(dt.timedelta(hours=9))
DAY = '2026-10-26'
ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'analysis_runs' / (DAY + '_morning')
OUT.mkdir(parents=True, exist_ok=True)
START = dt.datetime.fromisoformat(DAY + 'T08:50:00+09:00').timestamp()
T0 = START + 600
END = T0 + 3600
LAST_REQUEST = 0


def stamp(t):
    return dt.datetime.fromtimestamp(t, KST).isoformat()


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def public(path, params):
    global LAST_REQUEST
    for attempt in range(6):
        time.sleep(max(0, .22 - (time.monotonic() - LAST_REQUEST)))
        LAST_REQUEST = time.monotonic()
        req = Request('https://api.upbit.com' + path + '?' + urlencode(params),
                      headers={'Accept': 'application/json', 'User-Agent': 'local-readonly-replay/1.0'})
        try:
            with urlopen(req, timeout=20) as r:
                return json.load(r)
        except HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504):
                raise RuntimeError('Public HTTP ' + str(e.code)) from None
            time.sleep(min(2 ** attempt, 15))
        except (URLError, TimeoutError):
            if attempt == 5:
                raise RuntimeError('Public network timeout') from None
            time.sleep(2 ** attempt)
    raise RuntimeError('Public API retries exhausted')


class ReadOnlyAccount(bot.Upbit):
    def request(self, method, path, params=None):
        if method != 'GET' or path not in ('/v1/orders/closed', '/v1/orders/open', '/v1/order'):
            raise RuntimeError('Read-only analysis endpoint guard')
        return super().request(method, path, params)


def collect_account():
    bot.load_env()
    api = ReadOnlyAccount()
    midnight = START - (8 * 3600 + 50 * 60)
    cursor = min(time.time(), START + (15 * 3600 + 10 * 60))
    orders, seen = [], set()
    try:
        while True:
            batch = api.request('GET', '/v1/orders/closed', {
                'start_time': int((midnight - 6*86400) * 1000), 'end_time': int(cursor * 1000),
                'limit': 1000, 'order_by': 'desc'})
            if not isinstance(batch, list):
                raise RuntimeError('unexpected order response')
            for order in batch:
                uid = order.get('uuid')
                if uid not in seen:
                    seen.add(uid)
                    orders.append(order)
            if len(batch) < 1000:
                break
            new_cursor = min(dt.datetime.fromisoformat(o['created_at']).timestamp() for o in batch)
            if new_cursor >= cursor:
                raise RuntimeError('pagination boundary ambiguous')
            cursor = new_cursor
        closed_count = len(orders)
        open_count = 0
        for state in ('wait', 'watch'):
            page = 1
            while True:
                batch = api.request('GET', '/v1/orders/open', {'state':state,'page':page,'limit':100})
                if not isinstance(batch,list):
                    raise RuntimeError('unexpected open-order response')
                for order in batch:
                    if order.get('uuid') not in seen:
                        seen.add(order.get('uuid'))
                        orders.append(order)
                        open_count += 1
                if len(batch)<100:
                    break
                page += 1
        fills = []
        for o in orders:
            if bot.fnum(o.get('executed_volume')) <= 0:
                continue
            detail = api.request('GET', '/v1/order', {'uuid': o['uuid']})
            trades = detail.get('trades', [])
            funds = sum(bot.fnum(t.get('funds')) for t in trades)
            for t in trades:
                f = bot.fnum(t.get('funds'))
                fills.append({'market': o['market'], 'side': o['side'], 'time': t.get('created_at', o['created_at']),
                              'price': bot.fnum(t.get('price')), 'volume': bot.fnum(t.get('volume')),
                              'funds': f, 'fee': bot.fnum(detail.get('paid_fee')) * f / funds if funds else 0})
            time.sleep(.12)
        fills = [r for r in fills if midnight <= dt.datetime.fromisoformat(r['time']).timestamp() <= time.time()]
        fills.sort(key=lambda r: r['time'])
        window = [r for r in fills if START <= dt.datetime.fromisoformat(r['time']).timestamp() < END]
        result = {'status': 'ok', 'closed_orders_lookback':closed_count, 'open_orders_checked':open_count,
                  'orders_today':sum(dt.datetime.fromisoformat(o['created_at']).timestamp()>=midnight for o in orders), 'fills_today': len(fills),
                  'fills_0850_1000': len(window), 'fills': fills,
                  'query_start':stamp(midnight-6*86400),'query_end':stamp(time.time()),
                  'warning': 'Order origin (manual/other machine/bot) cannot be identified from exchange records alone.'}
    except Exception as e:
        detail = str(e)
        code = next((c for c in ('out_of_scope', 'no_authorization_ip', 'invalid_access_key') if c in detail), 'query_failed')
        result = {'status': 'unavailable', 'error_code': code}
    dump(OUT / 'actual_account.json', result)
    print('ACCOUNT', result.get('status'), 'window_fills', result.get('fills_0850_1000'), flush=True)


def collect_minutes():
    market_file = OUT / 'markets.json'
    if market_file.exists():
        markets = json.loads(market_file.read_text(encoding='utf-8'))
    else:
        markets = public('/v1/market/all', {'is_details': 'true'})
        dump(market_file, markets)
    allowed = [m['market'] for m in markets if m.get('market', '').startswith('KRW-')
               and m.get('market_warning', 'NONE') == 'NONE'
               and not (m.get('market_event') or {}).get('warning', False)]
    dest = OUT / 'minutes'
    dest.mkdir(exist_ok=True)
    errors = []
    for i, market in enumerate(sorted(allowed)):
        path = dest / (market + '.json')
        if not path.exists():
            try:
                rows = public('/v1/candles/minutes/1', {'market': market, 'count': 200, 'to': stamp(END + 60)})
                dump(path, rows)
            except Exception as e:
                errors.append({'market': market, 'error': type(e).__name__})
        if i % 25 == 0 or i == len(allowed) - 1:
            print('MINUTES', i + 1, '/', len(allowed), flush=True)
    dump(OUT / 'collection.json', {'date': DAY, 'start': stamp(START), 'end': stamp(END),
                                 'collected_at': stamp(time.time()), 'market_count': len(allowed), 'errors': errors,
                                 'selection_caveat': 'Current market warning/listing status; historical snapshot not available.'})


def load_minutes():
    data = {}
    for path in (OUT / 'minutes').glob('*.json'):
        got = {}
        for r in json.loads(path.read_text(encoding='utf-8')):
            t = bot.utc_ts(r['candle_date_time_utc'])
            got[int(round((t - T0) / 60))] = [bot.fnum(r[k]) for k in
                ('opening_price', 'high_price', 'low_price', 'trade_price', 'candle_acc_trade_price')]
        data[path.stem] = got
    return data


def universe(data, cutoff=-10):
    # At 08:50, only 08:40..08:49 fully closed candles are visible.
    return sorted(m for m, got in data.items()
                  if len(w := [got[i] for i in range(cutoff - 10, cutoff) if i in got]) >= 8
                  and sum(b[4] for b in w) >= 1e8)


class SignalProbe(paper.Session):
    """Reuse original signal methods, capturing ALL eligible candidates before portfolio limits."""
    def __init__(self):
        self.loose = False
        self.ports = [paper.Port('S1', 'fixed')]
        self.candidates = set()
        self.signals = []

    def ev(self, kind, **kw):
        if kind == 'signal':
            self.signals.append(kw)

    def quotes(self, markets):
        self.candidates.update(markets)
        return {}

    def open_pos(self, *args):
        raise AssertionError('probe must not open positions')


def candidates(data, uni):
    probe = SignalProbe()
    sub = {m: data[m] for m in uni}
    probe.enter_fixed({m: {i: b for i, b in got.items() if i < 0} for m, got in sub.items()})
    # Probe each market separately so all ranks are covered, including later replacements.
    for k in range(2, 60):
        for m in uni:
            probe.enter_auto(paper.Port('probe', 'auto'), {m: {i: b for i, b in data[m].items() if i < k}}, k)
    return sorted(probe.candidates)


def collect_seconds(markets):
    dest = OUT / 'seconds'
    dest.mkdir(exist_ok=True)
    for j, market in enumerate(markets):
        path = dest / (market + '.json')
        if path.exists():
            continue
        cursor = END + 1
        got = {}
        calls = 0
        while cursor > T0 - 60:
            rows = public('/v1/candles/seconds', {'market': market, 'count': 200, 'to': stamp(cursor)})
            calls += 1
            if not rows:
                break
            earliest = min(bot.utc_ts(r['candle_date_time_utc']) for r in rows)
            if earliest >= cursor:
                raise RuntimeError('candle pagination did not advance')
            for r in rows:
                t = bot.utc_ts(r['candle_date_time_utc'])
                if T0 - 60 <= t <= END:
                    got[t] = r
            cursor = earliest
        dump(path, [got[t] for t in sorted(got)])
        print('SECONDS', j + 1, '/', len(markets), market, 'bars', len(got), 'calls', calls, flush=True)


def collect_event_data():
    markets = json.loads((OUT / 'event_candidate_markets.json').read_text(encoding='utf-8'))
    collect_seconds(markets)
    totals = {}
    for market in markets:
        hours = public('/v1/candles/minutes/60', {'market': market, 'count': 24, 'to': stamp(T0)})
        prior = public('/v1/candles/minutes/1', {'market': market, 'count': 10, 'to': stamp(T0 - 86400 + 600)})
        totals[market] = {
            'turnover_24h_at_0900': sum(float(r['candle_acc_trade_price']) for r in hours
                                      if T0 - 86400 <= bot.utc_ts(r['candle_date_time_utc']) < T0),
            'prior_day_first10m': sum(float(r['candle_acc_trade_price']) for r in prior
                                      if T0 - 86400 <= bot.utc_ts(r['candle_date_time_utc']) < T0 - 86400 + 600)}
    dump(OUT / 'event_prior_turnover.json', totals)
    print('EVENT_DATA_COMPLETE', len(markets), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('stage', choices=['collect', 'seconds', 'event'])
    args = ap.parse_args()
    if args.stage == 'collect':
        collect_account()
        collect_minutes()
        data = load_minutes()
        uni = universe(data)
        uni_legacy = universe(data, -1)
        needed = sorted(set(candidates(data, uni)) | set(candidates(data, uni_legacy)))
        dump(OUT / 'universe.json', {'at_0850': uni, 'legacy_0859': uni_legacy, 'seconds_needed': needed})
        dump(OUT / 'settings_snapshot.json', json.loads((ROOT / 'settings.json').read_text(encoding='utf-8')))
        print('UNIVERSE_0850', len(uni), 'LEGACY_0859', len(uni_legacy), 'SECONDS_NEEDED', len(needed), flush=True)
    elif args.stage == 'seconds':
        meta = json.loads((OUT / 'universe.json').read_text(encoding='utf-8'))
        collect_seconds(meta['seconds_needed'])
    elif args.stage == 'event':
        collect_event_data()


if __name__ == '__main__':
    main()
