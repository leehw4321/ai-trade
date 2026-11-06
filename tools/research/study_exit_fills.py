"""실제 체결(업비트 주문 캐시)로 포지션을 재구성하고, 공개 15분봉(거래대금 포함)을 붙인다(읽기 전용).

2판(ChatGPT 2차 검산 반영): 개별 체결 시각 사용 · 매도 수량을 장부 수량으로 자르면 대금·수수료도 같은 비율로 자름 ·
5,000원 미만으로 닫힐 때 남은 잔량·원가를 다음 포지션으로 이월하고 기록 · 15분봉 거래대금 저장.

  python tools/research/study_exit_fills.py [--out 경로]
"""
import argparse, datetime as dt, json, sys, time, urllib.request
from pathlib import Path

SRC = Path(r'C:\Users\User\Desktop\코인 트레이딩\v5-app\v5\data\pnl\orders.json')
PROTECTED = {'KRW-BTC', 'KRW-XRP', 'KRW-DOGE'}
KST = dt.timezone(dt.timedelta(hours=9))


def t(s): return dt.datetime.fromisoformat(s).timestamp()


def load_fills(src):
    d = json.load(open(src, encoding='utf-8'))
    orders = d['orders']; orders = list(orders.values()) if isinstance(orders, dict) else orders
    out = []
    for o in orders:
        if o['market'] in PROTECTED or not o.get('trades'): continue
        tot = sum(float(x['funds']) for x in o['trades']) or 1
        fee = float(o.get('paid_fee') or 0)
        for x in o['trades']:                       # 체결 하나하나를 그 체결 시각으로. 수수료는 금액 비례 배분
            f = float(x['funds'])
            out.append({'m': o['market'], 'side': o['side'], 't': t(x['created_at']), 'vol': float(x['volume']),
                        'funds': f, 'fee': fee * f / tot})
    return sorted(out, key=lambda x: x['t'])


def positions(fills):
    pos, done, carry = {}, [], {}
    for f in fills:
        px = f['funds'] / f['vol']; m = f['m']; p = pos.get(m)
        if f['side'] == 'bid':
            if p is None:
                c = carry.pop(m, {'qty': 0., 'cost': 0.})
                p = pos[m] = {'m': m, 'open': f['t'], 'qty': c['qty'], 'cost': c['cost'], 'carried_in': c['cost'],
                              'buys': [], 'sells': [], 'proceeds': 0., 'sold_cost': 0.}
            p['qty'] += f['vol']; p['cost'] += f['funds'] + f['fee']; p['buys'].append({'t': f['t'], 'px': px, 'krw': f['funds']})
        else:
            if p is None or p['qty'] <= 0: continue
            q = min(f['vol'], p['qty']); share = q / f['vol']; avg = p['cost'] / p['qty']
            p['sold_cost'] += avg * q; p['cost'] -= avg * q; p['qty'] -= q
            p['proceeds'] += (f['funds'] - f['fee']) * share
            p['sells'].append({'t': f['t'], 'px': px, 'q': q, 'cut': share < 1})
            if p['qty'] * px < 5000:
                p['close'] = f['t']; p['exit_px'] = px
                p['carried_out'] = p['cost']
                if p['qty'] > 0: carry[m] = {'qty': p['qty'], 'cost': p['cost']}
                done.append(p); del pos[m]
    return done, pos


def candles(m, start, end):
    out = {}; to = end
    while to > start:
        u = (f'https://api.upbit.com/v1/candles/minutes/15?market={m}&count=200&to='
             + dt.datetime.fromtimestamp(to, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
        rows = []
        for _ in range(3):
            try:
                rows = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'x'}), timeout=15).read()); break
            except Exception: time.sleep(1)
        time.sleep(0.12)
        if not rows: break
        for r in rows:
            k = dt.datetime.fromisoformat(r['candle_date_time_utc']).replace(tzinfo=dt.timezone.utc).timestamp()
            out[k] = (r['opening_price'], r['high_price'], r['low_price'], r['trade_price'], r['candle_acc_trade_price'])
        if min(out) >= to: break
        to = min(out)
    return [[k, *v] for k, v in sorted(out.items()) if start <= k < end]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--out', type=Path, default=Path(__file__).with_name('exit_study_out.json'))
    a = ap.parse_args()
    done, still = positions(load_fills(SRC))
    now = time.time(); res = []
    for p in done:
        bars = candles(p['m'], p['open'] - 3600, min(p['close'] + 24 * 3600 + 900, now))
        res.append({k: p[k] for k in ('m', 'open', 'close', 'exit_px', 'proceeds', 'sold_cost', 'carried_in', 'carried_out', 'buys', 'sells')}
                   | {'pnl': p['proceeds'] - p['sold_cost'], 'bars': bars})
        print(p['m'], dt.datetime.fromtimestamp(p['open'], KST).strftime('%m-%d %H:%M'), round(p['proceeds'] - p['sold_cost']), len(bars), file=sys.stderr)
    a.out.write_text(json.dumps({'made': now, 'positions': res, 'open': [{'m': p['m'], 'open': p['open'], 'qty': p['qty']} for p in still.values()]}),
                     encoding='utf-8')
    print('closed', len(res), 'open', len(still), '->', a.out)


if __name__ == '__main__':
    main()
