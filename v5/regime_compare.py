"""매수 필터 6시간 전환(2026-11-06 사용자 지시) 평가: '24시간 필터였다면 안 샀을 매수'만 골라 실제 손익을 모은다(표시 전용).

regime_watch.jsonl(정시마다 24h·6h 지수)과 거래소 체결 캐시(data/pnl/orders.json)를 맞춘다. 매수 체결이 들어간 정시 판단의
24h 지수가 0 이하이면 '6시간 전환으로 추가된 매수'다. 포지션은 보유가 5,000원 미만에서 매수로 열리고 매도로 5,000원 미만이
되면 닫힌다(수수료 포함, 평균 원가). 빈자리·현금 때문에 24시간 필터라면 다른 종목을 샀을 수 있는 효과는 계산하지 않는다.
"""
import datetime as dt
import json
from pathlib import Path

HOUR = 3600
SWITCH = '2026-11-06T12:17+09:00'   # 6시간 필터 적용 시각. 13:00 기록에는 filter_hours가 빠져 있어(기록 결함, 13:xx 수정) 이 시각 이후는 6으로 본다.


def _t(s):
    return dt.datetime.fromisoformat(s).timestamp()


def hours(watch_path):
    out = {}
    try:
        for line in Path(watch_path).read_text(encoding='utf-8').splitlines():
            r = json.loads(line)
            if r.get('filter_hours') is None:
                r['filter_hours'] = 6 if _t(r['at']) >= _t(SWITCH) else 24
            out[int(_t(r['at']) // HOUR * HOUR)] = r
    except (OSError, ValueError, KeyError):
        pass
    return out


def positions(orders_path, protected=lambda m: False):
    d = json.loads(Path(orders_path).read_text(encoding='utf-8'))
    orders = d['orders']; orders = list(orders.values()) if isinstance(orders, dict) else orders
    fills = []
    for o in orders:
        if not o.get('trades') or protected(o['market']): continue
        fee = float(o.get('paid_fee') or 0); tot = sum(float(x['funds']) for x in o['trades']) or 1
        for x in o['trades']:
            f = float(x['funds'])
            fills.append((_t(x['created_at']), o['market'], o['side'], float(x['volume']), f, fee * f / tot))
    fills.sort()
    pos, done = {}, []
    for t, m, side, vol, funds, fee in fills:
        px = funds / vol; p = pos.get(m)
        if side == 'bid':
            if p is None or p['qty'] * px < 5000:
                p = pos[m] = {'m': m, 'open': t, 'qty': (p or {}).get('qty', 0.), 'cost': (p or {}).get('cost', 0.), 'pnl': 0.}
            p['qty'] += vol; p['cost'] += funds + fee
        elif p and p['qty'] > 0:
            q = min(vol, p['qty']); avg = p['cost'] / p['qty']
            p['pnl'] += (funds - fee) * q / vol - avg * q; p['cost'] -= avg * q; p['qty'] -= q
            if p['qty'] * px < 5000:
                p['close'] = t; done.append(p); del pos[m]
    return done, list(pos.values())


def extra_buys(watch_path, orders_path, protected=lambda m: False):
    """(닫힌 추가 매수 목록, 보유 중 추가 매수 목록)."""
    H = hours(watch_path)
    if not H: return [], []
    first = min(H)
    def extra(p):
        r = H.get(int(p['open'] // HOUR * HOUR))
        return p['open'] >= first and r is not None and (r.get('filter_hours') or 24) < 24 \
            and isinstance(r.get('index_24h'), (int, float)) and r['index_24h'] <= 0
    done, still = positions(orders_path, protected)
    return [p for p in done if extra(p)], [p for p in still if extra(p)]


def render(watch_path, orders_path, protected=lambda m: False):
    try:
        done, still = extra_buys(watch_path, orders_path, protected)
    except (OSError, ValueError, KeyError):
        return ''
    H = hours(watch_path)
    blocked24 = sum(1 for r in H.values() if (r.get('filter_hours') or 24) < 24 and isinstance(r.get('index_24h'), (int, float)) and r['index_24h'] <= 0)
    if not any((r.get('filter_hours') or 24) < 24 for r in H.values()):
        return ''
    win = sum(p['pnl'] > 0 for p in done)
    return (f"<div class=sub>매수 필터 6시간 시험(24시간이었다면 안 샀을 매수): 24h 지수 ≤ 0인 판단 {blocked24}시간 · "
            f"그때 산 포지션 끝남 {len(done)}건(이익 {win}) 실현 손익 <b>{sum(p['pnl'] for p in done):+,.0f}원</b> · 보유 중 {len(still)}건</div>")
