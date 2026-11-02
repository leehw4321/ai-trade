"""모의 장부. 전략 배정액 A0=f×E(T0)로 시작하고, 실제 계좌 T0 보유 바구니 H와 비교한다."""
import datetime as dt
import json
from pathlib import Path

from strategy import cost

KST = dt.timezone(dt.timedelta(hours=9))


def now_iso():
    return dt.datetime.now(KST).isoformat(timespec='seconds')


class Ledger:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.file = self.folder / 'ledger.json'
        self.trades_file = self.folder / 'trades.jsonl'
        self.events_file = self.folder / 'events.jsonl'
        self.equity_file = self.folder / 'equity.jsonl'
        self.d = json.loads(self.file.read_text(encoding='utf-8')) if self.file.exists() else None

    @property
    def ready(self):
        return self.d is not None

    def start(self, E, fraction, basket, krw, unpriced, label=''):
        """T0 확정. basket: {종목: 수량} 가격 있는 실제 보유, unpriced: 미평가 자산 목록."""
        A0 = round(E * fraction)
        self.d = {'t0': now_iso(), 'E0': E, 'fraction': fraction, 'A0': A0, 'cash': float(A0),
                  'positions': {}, 'banned': {}, 'last_4h': '', 'last_1h': '',
                  'H': {'krw': krw, 'basket': basket, 'value0': E, 'unpriced': unpriced}}
        self.save()
        self.event(f'{label} 모의 운영 시작: {A0:,}원 배정' if label else
                   f'모의 운영 시작: 실제 평가액 {E:,.0f}원의 {fraction:.0%} = {A0:,}원을 전략에 배정')

    def save(self):
        tmp = self.file.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.d, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(self.file)

    def _append(self, f, row):
        with f.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')

    def event(self, msg):
        self._append(self.events_file, {'time': now_iso(), 'msg': msg})

    def invested(self, prices):
        return sum(p['qty'] * prices.get(m, p['entry']) for m, p in self.d['positions'].items())

    def equity(self, prices):
        return self.d['cash'] + self.invested(prices)

    def h_value(self, prices):
        """T0 보유 바구니 평가. 가격이 하나라도 없으면 None (비교 보류)."""
        H = self.d['H']
        if any(m not in prices for m in H['basket']):
            return None
        return H['krw'] + sum(q * prices[m] for m, q in H['basket'].items())

    def buy(self, market, ask, atr, qty_fn, p):
        """P=ask×(1+c), S=P−k×ATR 고정. qty_fn(P, S) -> 수량."""
        P = ask * (1 + cost(market, p))
        S = P - p.k * atr
        qty = qty_fn(P, S)
        if qty <= 0:
            return None
        spend = qty * P * (1 + p.fee)
        self.d['cash'] -= spend
        self.d['positions'][market] = {'qty': qty, 'entry': P, 'stop': S, 'atr': atr, 'time': now_iso(), 'spend': spend}
        row = {'time': now_iso(), 'side': '매수', 'market': market, 'qty': qty, 'price': P, 'stop': S,
               'amount': round(spend), 'ask': ask}
        self._append(self.trades_file, row)
        self.save()
        return row

    def sell(self, market, bid, reason, p, banned_until=None):
        pos = self.d['positions'].pop(market)
        px = bid * (1 - cost(market, p))
        proceeds = pos['qty'] * px * (1 - p.fee)
        self.d['cash'] += proceeds
        if banned_until:
            self.d['banned'][market] = banned_until
        row = {'time': now_iso(), 'side': '매도', 'market': market, 'qty': pos['qty'], 'price': px,
               'reason': reason, 'amount': round(proceeds), 'pnl': round(proceeds - pos['spend']),
               'pnl_pct': round((proceeds / pos['spend'] - 1) * 100, 2), 'bid': bid, 'entry_time': pos['time']}
        self._append(self.trades_file, row)
        self.save()
        return row

    def snapshot(self, prices):
        h = self.h_value(prices)
        row = {'time': now_iso(), 'P': round(self.equity(prices)), 'H': None if h is None else round(h)}
        self._append(self.equity_file, row)
        return row

    def read_jsonl(self, name, last=50):
        f = self.folder / name
        if not f.exists():
            return []
        return [json.loads(l) for l in f.read_text(encoding='utf-8').splitlines()[-last:] if l.strip()]
