"""실현손익 관리 (D-56 합의). 거래소 체결을 원천으로 수수료 포함 이동평균 원가의 관리용 실현손익을 재구성한다.

주문을 내지 않는다(GET만). 봇 장부(live.json)와 무관하므로 PC 이동·계좌 인수·수동 주문에도 누적이 이어진다.
결과는 data/pnl/summary.json에 쓰고, bot.py 현황 화면의 '실현손익' 구역이 읽는다.

  python pnl.py            한 번 동기화·계산
  python pnl.py --loop 300 5분마다 (별도 프로세스)

- 기초: pnl_baseline.json (모든 PC 공통. 기초 시각의 수량·평균 매입가)
- 기간: 한국시간 오늘·이번 주(월요일)·이번 달·기초 이후 누적. 매도 체결 시각으로 나눈다.
- 원가: 매수 체결대금+수수료를 원가에 더하고, 매도 시 실현손익 = 체결대금 − 매도수수료 − 이동평균 배분원가.
- 원가 불명(평균가 0인 기초 보유·주문이 아닌 입고)이나 수량 부족 매도는 확정 합계에 넣지 않고 미확정으로 분리한다.
- 관리용 손익이며 세무상 손익이 아니다.
"""
import argparse
import datetime as dt
from decimal import Decimal as D
import hashlib
import hmac
import json
from pathlib import Path
import sys
import time
import urllib.parse
import urllib.request
import uuid as uuidlib

import upbit

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'data' / 'pnl'
BASELINE = ROOT / 'pnl_baseline.json'
KST = dt.timezone(dt.timedelta(hours=9))
VERSION = 'moving-average-fee-in-cost-v1'
EPS = D('1e-8')
WINDOW = dt.timedelta(days=7)


def kst(s):
    return dt.datetime.fromisoformat(s).astimezone(KST)


def now():
    return dt.datetime.now(KST)


# ---------- 수집 (읽기 전용) ----------
def _get(path, params):
    q = urllib.parse.urlencode(params)
    access, secret = upbit._keys()
    payload = {'access_key': access, 'nonce': str(uuidlib.uuid4()),
               'query_hash': hashlib.sha512(urllib.parse.unquote(q).encode()).hexdigest(), 'query_hash_alg': 'SHA512'}
    b64 = upbit._b64
    h, p = b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode()), b64(json.dumps(payload).encode())
    sig = b64(hmac.new(secret.encode(), f'{h}.{p}'.encode(), hashlib.sha256).digest())
    req = urllib.request.Request(upbit.BASE + path + '?' + q, headers={'Authorization': f'Bearer {h}.{p}.{sig}'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(1 + attempt)
                continue
            raise


def closed_between(start, end):
    """[start, end) 종료 주문 전부. 1000건이 차면 가장 이른 생성 시각으로 끝을 당겨 이어 받는다."""
    out, hi = {}, end
    while True:
        rows = _get('/v1/orders/closed', [('states[]', 'done'), ('states[]', 'cancel'), ('start_time', start.isoformat()),
                                          ('end_time', hi.isoformat()), ('limit', 1000), ('order_by', 'desc')])
        for o in rows:
            out[o['uuid']] = o
        if len(rows) < 1000:
            return list(out.values())
        earliest = min(kst(o['created_at']) for o in rows)
        if earliest >= hi:
            raise RuntimeError('종료 주문 페이지를 더 나눌 수 없음')
        hi = earliest + dt.timedelta(seconds=1)  # 경계 중복은 uuid로 제거
        time.sleep(0.15)


class Store:
    def __init__(self, folder=OUT):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        f = self.dir / 'orders.json'
        self.d = json.loads(f.read_text(encoding='utf-8')) if f.exists() else {'covered_until': None, 'orders': {}}

    def save(self):
        tmp = self.dir / 'orders.tmp'
        tmp.write_text(json.dumps(self.d, ensure_ascii=False), encoding='utf-8')
        tmp.replace(self.dir / 'orders.json')

    def sync(self, start):
        """기초 시각부터(이미 받은 구간은 1시간 겹쳐서) 지금까지 7일 창으로 받는다. 체결 있는 주문은 체결 내역을 받는다."""
        t = kst(self.d['covered_until']) - dt.timedelta(hours=1) if self.d['covered_until'] else start
        end = now()
        while t < end:
            hi = min(t + WINDOW, end)
            for o in closed_between(t, hi):
                if float(o.get('executed_volume') or 0) <= 0:
                    continue
                old = self.d['orders'].get(o['uuid'])
                if old and old.get('state') == o['state'] and old.get('executed_volume') == o['executed_volume']:
                    continue
                detail = _get('/v1/order', [('uuid', o['uuid'])])
                self.d['orders'][o['uuid']] = {k: detail.get(k) for k in (
                    'uuid', 'market', 'side', 'state', 'created_at', 'executed_volume', 'paid_fee', 'identifier', 'trades')}
                time.sleep(0.12)
            t = hi
        self.d['covered_until'] = end.isoformat(timespec='seconds')
        self.d['synced_at'] = end.isoformat(timespec='seconds')
        self.save()
        try:
            opened = _get('/v1/orders/open', [('state', 'wait'), ('limit', 100)])
            self.d['open_partial'] = sum(1 for o in opened if float(o.get('executed_volume') or 0) > 0)
        except Exception:
            self.d['open_partial'] = None
        self.save()


# ---------- 계산 (순수) ----------
def fills(orders):
    """주문 → 체결 이벤트. 주문 수수료는 체결대금 비율로 나눈다."""
    ev = []
    for o in orders.values():
        trades = o.get('trades') or []
        total = sum(D(str(t['funds'])) for t in trades)
        fee = D(str(o.get('paid_fee') or 0))
        for t in trades:
            funds = D(str(t['funds']))
            ev.append({'time': kst(t['created_at']), 'market': o['market'], 'coin': o['market'].split('-', 1)[1],
                       'side': o['side'], 'vol': D(str(t['volume'])), 'funds': funds,
                       'fee': fee * funds / total if total > 0 else D(0), 'order': o['uuid'],
                       'source': 'V5 식별 주문' if (o.get('identifier') or '').startswith('v5-') else '출처 불명(수동 등)'})
    return sorted(ev, key=lambda e: (e['time'], e['order']))


def rebuild(baseline, events):
    """이동평균 원가로 재구성. 반환: (종목 상태, 매도 체결 결과 목록)."""
    pos = {}
    for coin, h in baseline['holdings'].items():
        q, a = D(str(h['qty'])), D(str(h['avg']))
        pos[coin] = {'qty': q, 'cost': q * a, 'known': a > 0}
    sells = []
    for e in events:
        p = pos.setdefault(e['coin'], {'qty': D(0), 'cost': D(0), 'known': True})
        if e['side'] == 'bid':
            p['qty'] += e['vol']
            p['cost'] += e['funds'] + e['fee']
            continue
        if not p['known'] or p['qty'] + EPS < e['vol']:
            sells.append(dict(e, realized=None, why='원가 불명' if not p['known'] else '수량 부족'))
            p['qty'] = max(D(0), p['qty'] - e['vol'])
            if p['qty'] <= EPS:
                p.update(qty=D(0), cost=D(0), known=True)
            continue
        alloc = p['cost'] * e['vol'] / p['qty']
        sells.append(dict(e, realized=e['funds'] - e['fee'] - alloc))
        p['qty'] -= e['vol']
        p['cost'] -= alloc
        if p['qty'] <= EPS:
            p.update(qty=D(0), cost=D(0))
    return pos, sells


def period_starts(t):
    day = t.replace(hour=0, minute=0, second=0, microsecond=0)
    return {'오늘': day, '이번 주': day - dt.timedelta(days=day.weekday()), '이번 달': day.replace(day=1)}


def aggregate(sells, events, starts):
    out = {}
    for name, start in starts.items():
        ss = [s for s in sells if s['time'] >= start]
        orders = {}
        for s in ss:
            if s['realized'] is not None:
                orders.setdefault(s['order'], [s['source'], D(0)])[1] += s['realized']
        conf = sum((v for _, v in orders.values()), D(0))
        by_src = {}
        for src, v in orders.values():
            by_src[src] = by_src.get(src, D(0)) + v
        out[name] = {'realized': float(conf), 'unconfirmed_count': sum(1 for s in ss if s['realized'] is None),
                     'fees': float(sum((e['fee'] for e in events if e['time'] >= start), D(0))),
                     'sell_orders': len({s['order'] for s in ss}), 'wins': sum(1 for _, v in orders.values() if v > 0),
                     'losses': sum(1 for _, v in orders.values() if v < 0), 'zero': sum(1 for _, v in orders.values() if v == 0),
                     'by_source': {k: float(v) for k, v in by_src.items()}}
    return out


def unrealized(pos, accounts, prices):
    rows, total, mismatch = [], D(0), []
    actual = {r['currency']: D(str(r['balance'])) + D(str(r['locked'])) for r in accounts}
    for coin in sorted(set(pos) | {c for c in actual if c != 'KRW'}):
        p = pos.get(coin, {'qty': D(0), 'cost': D(0), 'known': True})
        a = actual.get(coin, D(0))
        if abs(a - p['qty']) > max(EPS, a * D('0.000001')):
            mismatch.append(coin)
        price = prices.get('KRW-' + coin)
        if p['qty'] <= EPS or price is None:
            continue
        value = p['qty'] * D(str(price))
        u = value - p['cost'] if p['known'] else None
        if value < 5000 and u is not None and abs(u) < 100:
            continue  # 소액 먼지
        rows.append({'coin': coin, 'qty': float(p['qty']), 'value': float(value), 'cost': float(p['cost']),
                     'unrealized': None if u is None else float(u), 'mismatch': coin in mismatch})
        if u is not None:
            total += u
    return rows, float(total), mismatch


def compute(store, baseline, accounts, prices, at=None):
    at = at or now()
    ev = [e for e in fills(store.d['orders']) if e['time'] >= kst(baseline['start'])]
    pos, sells = rebuild(baseline, ev)
    starts = period_starts(at)
    starts['누적'] = kst(baseline['start'])
    rows, utotal, mismatch = unrealized(pos, accounts, prices)
    return {'version': VERSION, 'computed_at': at.isoformat(timespec='seconds'), 'baseline_start': baseline['start'],
            'baseline_source': baseline['source'], 'synced_at': store.d.get('synced_at'), 'covered_until': store.d.get('covered_until'),
            'open_partial': store.d.get('open_partial'), 'periods': aggregate(sells, ev, starts),
            'unrealized_rows': rows, 'unrealized_total': utotal, 'mismatch': mismatch,
            'recent': [{'time': s['time'].isoformat(timespec='minutes'), 'coin': s['coin'], 'source': s['source'],
                        'realized': None if s['realized'] is None else float(s['realized']), 'why': s.get('why', '')}
                       for s in sells[-15:]][::-1]}


def run_once(folder=OUT):
    baseline = json.loads(BASELINE.read_text(encoding='utf-8'))
    store = Store(folder)
    store.sync(kst(baseline['start']))
    accounts = upbit.accounts()
    listed = upbit.krw_markets()
    markets = ['KRW-' + r['currency'] for r in accounts if r['currency'] != 'KRW' and 'KRW-' + r['currency'] in listed]
    prices = {m: t['trade_price'] for m, t in upbit.tickers(markets).items()} if markets else {}
    summary = compute(store, baseline, accounts, prices)
    tmp = Path(folder) / 'summary.tmp'
    tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(Path(folder) / 'summary.json')
    return summary


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--loop', type=int, default=0)
    a = ap.parse_args()
    while True:
        try:
            s = run_once()
            if not a.loop:
                for k, v in s['periods'].items():
                    print(f"{k}: 실현 {v['realized']:+,.0f}원 · 매도 {v['sell_orders']}건 · 수수료 {v['fees']:,.0f}원 · 미확정 {v['unconfirmed_count']}")
                print(f"평가손익 {s['unrealized_total']:+,.0f}원 · 불일치 {s['mismatch'] or '없음'}")
        except Exception as ex:
            err = OUT / 'error.json'
            OUT.mkdir(parents=True, exist_ok=True)
            err.write_text(json.dumps({'time': now().isoformat(timespec='seconds'), 'error': f'{type(ex).__name__}: {ex}'},
                                      ensure_ascii=False), encoding='utf-8')
            if not a.loop:
                raise
        if not a.loop:
            break
        time.sleep(a.loop)
