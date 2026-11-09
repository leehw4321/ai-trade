"""11-08~11-09 KST 왕복 거래 재구성 + 보유 중 최고/최저, 청산 후 4시간 움직임(공개 5분봉)."""
import json, sys, time, datetime as dt, urllib.request
from decimal import Decimal as D
from pathlib import Path

APP = Path(r'C:\Users\User\Desktop\코인 트레이딩\v5-app\v5')
sys.path.insert(0, str(APP))
import pnl  # noqa

KST = dt.timezone(dt.timedelta(hours=9))
START = dt.datetime(2026, 11, 8, tzinfo=KST)
store = json.loads((APP / 'data/pnl/orders.json').read_text(encoding='utf-8'))
orders = store['orders']
otype = {u: (o.get('ord_type') or '?') for u, o in orders.items()}
ev = pnl.fills(orders)
ev = [e for e in ev if e['source'] == 'V5 식별 주문' and e['coin'] not in ('BTC', 'DOGE', 'XRP')]

trips, cur = [], {}
for e in ev:
    t = cur.get(e['coin'])
    if e['side'] == 'bid':
        if t is None:
            t = cur[e['coin']] = {'coin': e['coin'], 'open': e['time'], 'qty': D(0), 'cost': D(0), 'proceeds': D(0),
                                  'buys': set(), 'sells': set(), 'close': None, 'sold_qty': D(0)}
        t['qty'] += e['vol']; t['cost'] += e['funds'] + e['fee']; t['buys'].add(e['order'])
    else:
        if t is None:
            continue
        t['qty'] -= e['vol']; t['sold_qty'] += e['vol']; t['proceeds'] += e['funds'] - e['fee']; t['sells'].add(e['order'])
        t['close'] = e['time']
        if t['qty'] * 1 <= D('1e-8') or t['qty'] * (t['cost'] / (t['qty'] + t['sold_qty'])) < 5000 and t['qty'] >= 0:
            trips.append(t); cur.pop(e['coin'])

def candles(market, end, n=200, unit=5):
    url = f'https://api.upbit.com/v1/candles/minutes/{unit}?market={market}&count={n}&to={end.astimezone(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}'
    for _ in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'analysis'}), timeout=10) as r:
                time.sleep(0.15)
                return json.loads(r.read())
        except Exception:
            time.sleep(1)
    return []

rows = []
for t in trips:
    if t['close'] < START:
        continue
    bought = t['qty'] + t['sold_qty']
    cost_left = t['cost'] * (t['qty'] / bought) if bought else 0
    pnl_krw = t['proceeds'] - (t['cost'] - cost_left)
    invest = t['cost'] - cost_left
    entry = float(t['cost'] / bought)
    exitp = float(t['proceeds'] / t['sold_qty'])
    m = 'KRW-' + t['coin']
    cs = candles(m, t['close'] + dt.timedelta(hours=4, minutes=5), 200)
    hold = [c for c in cs if t['open'] - dt.timedelta(minutes=5) <= dt.datetime.fromisoformat(c['candle_date_time_kst']).replace(tzinfo=KST) <= t['close']]
    after = [c for c in cs if t['close'] < dt.datetime.fromisoformat(c['candle_date_time_kst']).replace(tzinfo=KST) <= t['close'] + dt.timedelta(hours=4)]
    hi = max((c['high_price'] for c in hold), default=None); lo = min((c['low_price'] for c in hold), default=None)
    ahi = max((c['high_price'] for c in after), default=None); alo = min((c['low_price'] for c in after), default=None)
    alast = after[0]['trade_price'] if after else None  # 최신이 앞
    pre = candles(m, t['open'], 12, 60)  # 진입 전 12시간 1시간봉
    pre12 = (entry / pre[-1]['opening_price'] - 1) * 100 if pre else None
    rows.append(dict(coin=t['coin'], open=t['open'].strftime('%m-%d %H:%M'), close=t['close'].strftime('%m-%d %H:%M'),
                     hold_min=round((t['close'] - t['open']).total_seconds() / 60), invest=round(float(invest)),
                     pnl=round(float(pnl_krw)), pct=round(float(pnl_krw / invest) * 100, 2) if invest else None,
                     mfe=round((hi / entry - 1) * 100, 2) if hi else None, mae=round((lo / entry - 1) * 100, 2) if lo else None,
                     after_hi=round((ahi / exitp - 1) * 100, 2) if ahi else None, after_lo=round((alo / exitp - 1) * 100, 2) if alo else None,
                     after_4h=round((alast / exitp - 1) * 100, 2) if alast else None, pre12h=round(pre12, 2) if pre12 is not None else None,
                     nbuy=len(t['buys']), nsell=len(t['sells']),
                     sell_types='/'.join(sorted({otype[u] for u in t['sells']})), buy_types='/'.join(sorted({otype[u] for u in t['buys']}))))
print(json.dumps(rows, ensure_ascii=False, indent=0))
print('OPEN', {k: v['open'].strftime('%m-%d %H:%M') for k, v in cur.items()})
