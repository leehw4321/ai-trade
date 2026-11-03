"""D-51 합의: 청산 규칙 4개 비교군 전향 그림자 평가. 주문 기능 없음, 실거래 코드와 분리된 별도 프로세스.

비교군 (매수 판단은 모두 shadow.proposal과 같고 청산만 다르다)
  G0 현행     R = k×ATR(4시간봉). 본전 잠금 1R, 추적 2R, 1/3 익절 2R
  G1 R0       R0 = E−S0 (최초 확정 손절 폭). 본전 잠금 1R0, 추적 2R0, 1/3 익절 2R0
  G2 R0+1R0   G1과 같고 1/3 익절만 1R0 (최초 수량의 1/3, 한 번)
  G3 +재매수  G2 + 1/3 익절 체결 뒤 재매수 권리(별도 lot)

합의 명세: docs/ai-history/debates/D-51/05_Claude_수용_합의.md, 구현 검토 07_ChatGPT_구현검토.md 반영(2판).
- 설정은 시작 때 frozen.json에 고정하고 재시작해도 그 값을 쓴다. 현재 config가 달라도 바꾸지 않는다.
- 같은 판단 시점의 호가·ATR은 한 번 조회해 모든 비교군에 같이 쓴다.
- 판단 시점에 거래대금 상위 종목의 확정 봉이 하나라도 없으면 그 판단은 매매하지 않고 '판단 공백'으로 기록한다.
- 초기 손절 S0는 실시간(30초) 체결가로, 올라간 보호선·익절·재매수는 확정 1분봉을 빠짐없이 순서대로 재생해 판단한다.
- 우선순위: 전량 청산 → 일부 익절 → 재매수 평가. 잔여분의 고점·손절·R0는 일부 익절 뒤에도 보존한다.
- 재매수 권리: 익절 체결 2~3시간 사이, 매수호가 ≤ 익절 평균가×0.97, reentry_block ATR 눌림, 최신 신호 유지 순위 M 이내·
  상대강도 > 0, 신규 매수 제한 전부(경보·과열·위험 기준 금액·종목·총투입·현금), 지출 ≤ 익절 순수령액.
  전량 청산·순환 매도·기한 만료 시 취소.
- 재매수 lot: E=체결가, S0=cap_stop(E, E−k×ATR 4시간봉), R0=E−S0, G2 규칙에서 일부 익절·재매수 없음.
- 장부·거래·묶음 기록은 한 상태 파일에 원자적으로 저장한다(재시작 중복 없음).
- 성과는 진입 묶음 단위, 낙폭은 5분 관측 청산가치(매수호가 기준 비용·수수료 차감) 자산곡선. 시세가 빠진 관측은 낙폭 계산에서 제외하고 표시한다.

python exit_study.py            실행
python exit_study.py --report   보고서만
"""
import argparse
from dataclasses import asdict, replace
import datetime as dt
import html
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import uuid

import strategy as st
import upbit
from shadow import proposal
from bot import boundary, load_params, SETTLE

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'data' / 'exit_study'
STOP = ROOT / 'stop_exit_study.txt'
LOCK = ROOT / 'exit_study.lock'
KST = dt.timezone(dt.timedelta(hours=9))
CAPITAL = 1_900_000
GAP_GRACE = 300   # 판단 봉 마감 뒤 이 초까지 자료를 기다리고, 그래도 없으면 판단 공백
GROUPS = {
    'G0_현행': {'r_mode': 'atr', 'partial_r': 2.0, 'rebuy': False},
    'G1_R0': {'r_mode': 'r0', 'partial_r': 2.0, 'rebuy': False},
    'G2_R0_1R0익절': {'r_mode': 'r0', 'partial_r': 1.0, 'rebuy': False},
    'G3_재매수': {'r_mode': 'r0', 'partial_r': 1.0, 'rebuy': True},
}
REBUY = {'after_h': 2.0, 'until_h': 3.0, 'dip': 0.03}
JUDGE = ('시작 72시간에 결과 공개. 72시간 경과와 비교군별 완결 묶음 20건을 모두 충족하면 본판정, 144시간까지 미충족이면 근거 부족으로 채택 보류. '
         '현행(G0) 대비 묶음별 비용 후 평균 순손익과 계좌 최대 낙폭이 둘 다 나으면 채택, 둘 다 나쁘면 기각, 엇갈리거나 동률이면 한 번만 최대 72시간 연장 후 같은 기준, 그래도 우열이 없으면 채택 보류. '
         '채택은 다음 협의의 기술적 판단이며 실거래 적용 승인이 아니다.')
FILLS = ('매수 최우선 매도호가×(1+c), 매도 최우선 매수호가×(1−c), 수수료 편도 별도. c는 스프레드와 별개로 호가 소진·지연에 따른 불리한 체결 충당분'
         '(BTC·ETH 0.10%, 기타 0.20%)이다.')


def now_iso():
    return dt.datetime.now(KST).isoformat(timespec='seconds')


# ---------- 순수 규칙 ----------
def risk_unit(lot, mode, k):
    """G0은 k×ATR, 나머지는 R0=E−S0(유한한 양수일 때만, 아니면 k×ATR)."""
    if mode == 'r0':
        r0 = lot['E'] - lot['S0']
        if r0 > 0 and r0 == r0 and r0 != float('inf'):
            return r0
    return k * lot['atr']


def protect(lot, close, R, p, market, partial_r):
    """확정 1분봉 종가 평가. 반환: ('exit', 사유) | ('partial', None) | (None, None). 손절가는 내려가지 않는다."""
    E = lot['E']
    lot['peak'] = max(lot.get('peak', E), close)
    peak = lot['peak']
    g = (peak - E) / R if R > 0 else 0
    if g >= p.trail_lock_r:
        lot['stop'] = max(lot['stop'], E * (1 + 2 * p.fee + st.cost(market, p)))
    if g >= p.trail_start_r:
        lot['stop'] = max(lot['stop'], peak - p.trail_m * lot['atr'])
        if p.giveback > 0 and peak > E and (peak - close) / (peak - E) >= p.giveback:
            return 'exit', '수익 반납'
    if close <= lot['stop']:
        return 'exit', '추적 손절' if lot['stop'] > lot['S0'] else '손절'
    if partial_r and not lot.get('part_done') and R > 0 and (close - E) / R >= partial_r:
        return 'partial', None
    return None, None


def rebuy_ok(right, now, ask, r, entry_ok, held_value, equity, invested, cash, p):
    """재매수 권리 평가. 막으면 (사유, 0), 허용이면 (None, 금액)."""
    hours = (now - right['t']) / 3600
    if hours < REBUY['after_h']:
        return '대기 중', 0
    if hours > REBUY['until_h']:
        return '기한 만료', 0
    if ask > right['px'] * (1 - REBUY['dip']):
        return f'매수호가 {ask:,.4g} > 익절가의 97%', 0
    if r is None or r['rank'] > p.M or r['rs'] <= 0:
        return '최신 신호에서 유지 순위 밖 또는 상대강도 ≤ 0', 0
    if r['market'] not in entry_ok:
        return '신규 매수 불가 종목(경보·자료 없음)', 0
    why = st.chase_block(r, p) or st.reentry_block(dict(r, close=ask), {'time': right['t'], 'price': right['px'], 'profit': True},
                                                   now, replace(p, reentry_profit_hours=0))
    if why:
        return why, 0
    risk_room = (p.entry_risk or p.risk) * equity / p.stop_max_pct if p.size_by_risk and p.stop_max_pct > 0 else float('inf')
    amt = min(right['budget'], risk_room, p.coin_cap * equity - held_value, p.total_cap * equity - invested, cash / (1 + p.fee))
    if amt < p.min_order:
        return '한도·현금 부족', 0
    return None, amt


def liquidation(qty, market, price, p):
    return qty * price * (1 - st.cost(market, p)) * (1 - p.fee)


def lots(P):
    return [l for l in (P['main'], P.get('lot')) if l]


# ---------- 장부 (모든 기록을 한 파일에 원자 저장) ----------
class Book:
    def __init__(self, name, cfg, folder):
        self.name, self.cfg, self.dir = name, cfg, folder / name
        self.dir.mkdir(parents=True, exist_ok=True)
        f = self.dir / 'state.json'
        self.d = json.loads(f.read_text(encoding='utf-8')) if f.exists() else \
            {'cash': float(CAPITAL), 'pos': {}, 'last_sells': {}, 'last_decision': 0, 'trades': [], 'bundles': [], 'equity': []}

    def save(self):
        tmp = self.dir / 'state.tmp'
        tmp.write_text(json.dumps(self.d, ensure_ascii=False), encoding='utf-8')
        tmp.replace(self.dir / 'state.json')

    def trade(self, row):
        self.d['trades'].append({'time': now_iso(), **row})

    def holdings(self, prices):
        return {m: sum(l['qty'] for l in lots(P)) * prices[m] for m, P in self.d['pos'].items() if m in prices}

    def open_lot(self, market, qty, E, atr, p, kind):
        S0 = st.cap_stop(E, E - p.k * atr, p)
        spend = qty * E * (1 + p.fee)
        self.d['cash'] -= spend
        return {'qty': qty, 'qty0': qty, 'E': E, 'S0': S0, 'stop': S0, 'atr': atr, 'peak': E, 'time': now_iso(),
                'spend': spend, 'kind': kind}

    def buy(self, market, krw, ask, atr, p):
        E = ask * (1 + st.cost(market, p))
        qty = int(min(krw, self.d['cash']) / (E * (1 + p.fee)) * 1e8) / 1e8
        if qty * E < p.min_order:
            return None
        main = self.open_lot(market, qty, E, atr, p, 'main')
        P = {'bundle': str(uuid.uuid4())[:8], 'opened': now_iso(), 'main': main, 'lot': None, 'right': None,
             'spent': main['spend'], 'got': 0.0, 'last_bar': ''}
        self.d['pos'][market] = P
        self.trade({'side': '매수', 'market': market, 'krw': round(main['spend']), 'E': E, 'S0': main['S0'],
                    'R': risk_unit(main, self.cfg['r_mode'], p.k), 'bundle': P['bundle']})
        return P

    def sell_qty(self, market, lot, qty, bid, p, reason):
        P = self.d['pos'][market]
        px = bid * (1 - st.cost(market, p))
        got = qty * px * (1 - p.fee)
        lot['qty'] -= qty
        self.d['cash'] += got
        P['got'] += got
        self.trade({'side': '매도', 'market': market, 'qty': qty, 'krw': round(got), 'px': px, 'reason': reason,
                    'lot': lot['kind'], 'bundle': P['bundle']})
        return px, got

    def close_all(self, market, bid, p, reason):
        for l in lots(self.d['pos'][market]):
            if l['qty'] > 0:
                self.sell_qty(market, l, l['qty'], bid, p, reason)
        self.finish(market, bid)

    def finish(self, market, last_px):
        P = self.d['pos'].pop(market)
        pnl = P['got'] - P['spent']
        self.d['last_sells'][market] = {'time': time.time(), 'price': last_px, 'profit': pnl > 0}
        self.d['bundles'].append({'time': now_iso(), 'market': market, 'bundle': P['bundle'], 'opened': P['opened'],
                                  'pnl': round(pnl, 2), 'pnl_pct': round(pnl / P['spent'] * 100, 3), 'spent': round(P['spent'])})


# ---------- 운영 ----------
class Study:
    def __init__(self, folder=None):
        self.out = folder or OUT
        self.out.mkdir(parents=True, exist_ok=True)
        frozen = self.out / 'frozen.json'
        if not frozen.exists():
            base, cfg = load_params()
            live = cfg.get('live', {})
            frozen.write_text(json.dumps({
                'start': time.time(), 'start_kst': now_iso(), 'capital': CAPITAL, 'groups': GROUPS, 'rebuy': REBUY,
                'partial_frac': float(live.get('partial_frac', 1 / 3)), 'stop_tf': int(live.get('stop_tf', 240)),
                'params': asdict(replace(base, trail_enabled=True)), 'judge': JUDGE, 'fills': FILLS, 'gap_grace': GAP_GRACE,
                'version': 2}, ensure_ascii=False, indent=1), encoding='utf-8')
        self.frozen = json.loads(frozen.read_text(encoding='utf-8'))
        self.base = st.Params.from_dict(self.frozen['params'])
        self.partial_frac, self.stop_tf = self.frozen['partial_frac'], self.frozen['stop_tf']
        self.groups, self.rebuy = self.frozen['groups'], self.frozen['rebuy']
        self.books = {n: Book(n, c, self.out) for n, c in self.groups.items()}
        self.markets, self.ranked, self.entry_ok, self.atr_cache = {}, [], set(), {}
        self.alive = self.out / 'alive.json'

    def note_gap(self, kind, **info):
        with (self.out / 'gaps.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps({'time': now_iso(), 'kind': kind, **info}, ensure_ascii=False) + '\n')

    def heartbeat(self):
        prev = json.loads(self.alive.read_text(encoding='utf-8'))['t'] if self.alive.exists() else None
        now = time.time()
        if prev and now - prev > 120:
            self.note_gap('감시 중단', since=dt.datetime.fromtimestamp(prev, KST).isoformat(timespec='seconds'), seconds=round(now - prev))
        self.alive.write_text(json.dumps({'t': now}), encoding='utf-8')

    def atr4(self, m):
        t, v = self.atr_cache.get(m, (0, None))
        if time.time() - t > 900:
            v = st.wilder_atr(upbit.completed_candles(m, self.stop_tf, 100))
            self.atr_cache[m] = (time.time(), v)
            time.sleep(0.12)
        return v

    def decide(self, end):
        p = self.base
        self.markets = upbit.krw_markets()
        tick = upbit.tickers(self.markets)
        pool = sorted((m for m in tick if m not in p.exclude), key=lambda m: -tick[m]['acc_trade_price_24h'])
        universe = pool[:p.universe]
        held = {m for b in self.books.values() for m in b.d['pos']}
        bars, missing = {}, []
        for m in dict.fromkeys(universe + sorted(held)):
            b = upbit.completed_candles(m, p.tf, 200)
            if b and dt.datetime.fromisoformat(b[-1]['t']).timestamp() + p.tf * 60 == end:
                bars[m] = b
            elif m in universe:
                missing.append(m)
            time.sleep(0.12)
        if missing:
            if time.time() < end + GAP_GRACE:
                return False  # 잠시 뒤 다시 시도
            self.note_gap('판단 공백', end=end, missing=missing)
            for bk in self.books.values():
                bk.d['last_decision'] = end
                bk.save()
            return True
        uni = set(universe)
        self.ranked = st.rank({m: b for m, b in bars.items() if m in uni}, p)
        rs_out = {m: st.relative_strength(b, p.L) for m, b in bars.items() if m not in uni}
        self.entry_ok = {m for m in universe if m in bars and not self.markets.get(m, {}).get('warning')}
        prices = {m: t['trade_price'] for m, t in tick.items()}
        caps = {'max_order_krw': 1e12, 'max_total_buy_krw': 1e12, 'max_sell_krw_per_plan': 1e12}
        plans = {}
        for name, bk in self.books.items():
            if bk.d['last_decision'] < end:
                text, _ = proposal(bk.holdings(prices), self.ranked, rs_out, self.entry_ok, p, caps, bk.d['cash'], 0,
                                   bk.d['last_sells'])
                plans[name] = (text, parse(text))
        # 같은 판단의 호가·ATR은 한 번만 조회해 모든 비교군에 같이 쓴다
        need = sorted({m for _, orders in plans.values() for _, m, _ in orders})
        books = upbit.orderbooks(need) if need else {}
        atrs = {m: self.atr4(m) for _, orders in plans.values() for side, m, _ in orders if side == 'buy'}
        for name, (text, orders) in plans.items():
            bk = self.books[name]
            for side, m, _ in orders:
                if side == 'sell' and m in bk.d['pos']:
                    bk.close_all(m, books[m]['bid'], p, '순환')
            for side, m, krw in orders:
                if side == 'buy' and m not in bk.d['pos'] and atrs.get(m):
                    bk.buy(m, krw, books[m]['ask'], atrs[m], p)
            bk.d['last_decision'] = end
            bk.save()
        with (self.out / 'decisions.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps({'time': now_iso(), 'end': end, 'universe': universe,
                                'ranked': [(r['market'], r['rank'], round(r['rs'], 5), r['atr']) for r in self.ranked],
                                'entry_ok': sorted(self.entry_ok), 'books': {m: books[m] for m in need},
                                'atr4': atrs, 'plans': {n: t for n, (t, _) in plans.items()}}, ensure_ascii=False) + '\n')
        return True

    def realtime_stops(self):
        """초기 손절 S0만 실시간 체결가로 확인한다."""
        held = sorted({m for b in self.books.values() for m in b.d['pos']})
        if not held:
            return
        live = {m: t['trade_price'] for m, t in upbit.tickers(held).items()}
        hit = sorted({m for b in self.books.values() for m, P in b.d['pos'].items()
                      if m in live and any(l['qty'] > 0 and live[m] <= l['S0'] for l in lots(P))})
        if not hit:
            return
        books = upbit.orderbooks(hit)
        for bk in self.books.values():
            changed = False
            for m in [m for m in hit if m in bk.d['pos']]:
                P = bk.d['pos'][m]
                for l in lots(P):
                    if l['qty'] > 0 and live[m] <= l['S0']:
                        bk.sell_qty(m, l, l['qty'], books[m]['bid'], self.base, '손절')
                        changed = True
                        if l['kind'] == 'main':
                            P['right'] = None
                if all(l['qty'] <= 1e-12 for l in lots(P)):
                    bk.finish(m, books[m]['bid'])
            if changed:
                bk.save()

    def minute_bars(self):
        """확정 1분봉을 빠짐없이 순서대로 재생한다. 봉마다 전량 청산 → 일부 익절 → 재매수 순."""
        p = self.base
        held = sorted({m for b in self.books.values() for m in b.d['pos']})
        if not held:
            return
        bars = {}
        for m in held:
            bars[m] = upbit.completed_candles(m, 1, 200)
            time.sleep(0.1)
        need = [m for m in held if any(m in b.d['pos'] and [x for x in bars[m] if x['t'] > b.d['pos'][m]['last_bar']]
                                       for b in self.books.values())]
        if not need:
            return
        books = upbit.orderbooks(need)  # 매도·재매수에 같은 호가 스냅샷
        rank = {r['market']: r for r in self.ranked}
        prices = {m: books[m]['bid'] for m in need}
        for name, bk in self.books.items():
            g = bk.cfg
            for m in [m for m in need if m in bk.d['pos']]:
                P = bk.d['pos'][m]
                new = [x for x in bars[m] if x['t'] > P['last_bar']]
                if P['last_bar'] and new and len(new) == len(bars[m]):  # 받아온 200개가 모두 새 봉: 그 앞을 놓쳤을 수 있다
                    self.note_gap('1분봉 재생 한계', market=m, book=name, after=P['last_bar'])
                for bar in new:
                    if m not in bk.d['pos']:
                        break
                    self.replay_bar(bk, g, m, P, bar, books[m], rank.get(m), prices)
                    P['last_bar'] = bar['t']
            bk.save()

    def replay_bar(self, bk, g, m, P, bar, book, r, prices):
        p = self.base
        c = bar['c']
        actions = []
        for l in lots(P):
            if l['qty'] <= 0 or not st.bar_after_entry(bar, l['time']):
                continue
            R = risk_unit(l, g['r_mode'], p.k)
            act, why = protect(l, c, R, p, m, g['partial_r'] if l['kind'] == 'main' else 0)
            if act:
                actions.append((l, act, why))
        for l, act, why in [a for a in actions if a[1] == 'exit']:
            bk.sell_qty(m, l, l['qty'], book['bid'], p, why)
            if l['kind'] == 'main':
                P['right'] = None  # 전량 청산 시 재매수 권리 취소
        if all(l['qty'] <= 1e-12 for l in lots(P)):
            bk.finish(m, book['bid'])
            return
        for l, act, _ in [a for a in actions if a[1] == 'partial' and a[0]['qty'] > 0]:
            qty = int(l['qty0'] * self.partial_frac * 1e8) / 1e8
            if qty * book['bid'] < p.min_order or (l['qty'] - qty) * book['bid'] < p.min_order:
                l['part_done'] = True
                continue
            px, got = bk.sell_qty(m, l, qty, book['bid'], p, '부분 익절')
            l['part_done'] = True
            if g['rebuy']:
                P['right'] = {'t': time.time(), 'px': px, 'budget': got}
        if g['rebuy'] and P.get('right') and not P.get('lot'):
            self.try_rebuy(bk, m, P, r, book, prices)

    def try_rebuy(self, bk, m, P, r, book, prices):
        p = self.base
        ask = book['ask']
        px = dict(prices, **{m: ask})
        missing = [x for x in bk.d['pos'] if x not in px]
        if missing:
            px.update({k: t['trade_price'] for k, t in upbit.tickers(missing).items()})
        held = {x: sum(l['qty'] for l in lots(Q)) * px[x] for x, Q in bk.d['pos'].items() if x in px}
        equity = bk.d['cash'] + sum(held.values())
        why, amt = rebuy_ok(P['right'], time.time(), ask, r, self.entry_ok, held.get(m, 0), equity, sum(held.values()),
                            bk.d['cash'], p)
        if why == '기한 만료':
            P['right'] = None
            bk.trade({'side': '재매수 권리 만료', 'market': m, 'bundle': P['bundle']})
            return
        if why:
            return
        atr = self.atr4(m)
        if not atr:
            return
        E = ask * (1 + st.cost(m, p))
        qty = int(amt / (E * (1 + p.fee)) * 1e8) / 1e8
        lot = bk.open_lot(m, qty, E, atr, p, 'rebuy')
        lot['part_done'] = True  # 재매수 lot은 일부 익절·재매수 없음
        P['lot'], P['right'] = lot, None
        P['spent'] += lot['spend']
        bk.trade({'side': '재매수', 'market': m, 'krw': round(lot['spend']), 'E': E, 'S0': lot['S0'], 'bundle': P['bundle']})

    def snapshot(self):
        ms = sorted({m for b in self.books.values() for m in b.d['pos']})
        books = upbit.orderbooks(ms) if ms else {}
        for bk in self.books.values():
            miss = [m for m in bk.d['pos'] if m not in books]
            value = bk.d['cash'] + sum(liquidation(sum(l['qty'] for l in lots(P)), m, books[m]['bid'], self.base)
                                       for m, P in bk.d['pos'].items() if m in books)
            bk.d['equity'].append({'time': now_iso(), 'equity': round(value, 2), 'complete': not miss})
            bk.save()

    def run(self):
        last_stop = last_min = last_snap = 0
        next_report = time.time() + 120
        while not STOP.exists():
            now = time.time()
            try:
                self.heartbeat()
                end = boundary(now - SETTLE, self.base.tf * 60)
                if any(b.d['last_decision'] < end for b in self.books.values()):
                    if not any(b.d['last_decision'] for b in self.books.values()):
                        for b in self.books.values():  # 시작 직후 지난 봉으로 매매하지 않는다
                            b.d['last_decision'] = end
                            b.save()
                    else:
                        self.decide(end)
                if now - last_stop >= self.base.stop_seconds:
                    self.realtime_stops()
                    last_stop = now
                if now - last_min >= 20:
                    self.minute_bars()
                    last_min = now
                if now - last_snap >= 300:
                    self.snapshot()
                    last_snap = now
                if now >= next_report:
                    report(self.out)
                    next_report = now + 3600
            except Exception as ex:
                self.note_gap('오류', error=f'{type(ex).__name__}: {str(ex)[:200]}')
                traceback.print_exc()
                time.sleep(30)
            time.sleep(5)


def parse(text):
    import live
    return live.parse(text.split('실행 주문안:', 1)[-1].strip())


# ---------- 보고 ----------
def summarize(state, capital):
    seen, bundles = set(), []
    for b in state['bundles']:
        if b['bundle'] not in seen:  # 같은 묶음은 한 번만
            seen.add(b['bundle'])
            bundles.append(b)
    peak, mdd, skipped = capital, 0.0, 0
    for e in state['equity']:
        if not e.get('complete', True):
            skipped += 1
            continue
        peak = max(peak, e['equity'])
        mdd = max(mdd, 1 - e['equity'] / peak)
    n = len(bundles)
    return {'n': n, 'avg': sum(b['pnl'] for b in bundles) / n if n else None,
            'avgp': sum(b['pnl_pct'] for b in bundles) / n if n else None,
            'win': sum(b['pnl'] > 0 for b in bundles) / n if n else None,
            'equity': state['equity'][-1]['equity'] if state['equity'] else capital, 'mdd': mdd,
            'open': len(state['pos']), 'skipped': skipped}


def report(out=None):
    out = out or OUT
    f = out / 'frozen.json'
    if not f.exists():
        return ''
    frozen = json.loads(f.read_text(encoding='utf-8'))
    hours = (time.time() - frozen['start']) / 3600
    gaps = len((out / 'gaps.jsonl').read_text(encoding='utf-8').splitlines()) if (out / 'gaps.jsonl').exists() else 0
    lines = ['# D-51 청산 규칙 그림자 평가 (2판)', '',
             f"시작 {frozen['start_kst'][:16]} · 경과 {hours:.1f}시간 · 초기자금 {frozen['capital']:,}원(비교군마다 동일) · 공백·오류 기록 {gaps}건 · 갱신 {now_iso()[:16]}", '',
             '| 비교군 | 완결 묶음 | 평균 순손익(원) | 평균 순손익(%) | 승률 | 청산가치 자산 | 5분 관측 최대 낙폭 | 진행 중 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name in frozen['groups']:
        sf = out / name / 'state.json'
        if not sf.exists():
            continue
        s = summarize(json.loads(sf.read_text(encoding='utf-8')), frozen['capital'])
        lines.append(f"| {name} | {s['n']} | {'-' if s['avg'] is None else format(s['avg'], '+,.0f')} | "
                     f"{'-' if s['avgp'] is None else format(s['avgp'], '+.2f') + '%'} | {'-' if s['win'] is None else format(s['win'], '.0%')} | "
                     f"{s['equity']:,.0f} | {s['mdd'] * 100:.2f}%{' (시세 누락 ' + str(s['skipped']) + '회 제외)' if s['skipped'] else ''} | {s['open']} |")
    stage = '중간 결과 공개 시점 도달' if hours >= 72 else f'72시간 공개까지 {72 - hours:.1f}시간'
    lines += ['', f'상태: {stage}. 최종 기한 144시간.', '', f'체결 가정: {frozen["fills"]}', '',
              f'판정 규칙(사전 고정): {frozen["judge"]}', '', '관측 기간이 짧은 동안의 차이는 우연일 수 있다. 수익성 입증이 아니다.']
    md = '\n'.join(lines) + '\n'
    (out / 'report.md').write_text(md, encoding='utf-8')
    table, text = [], []
    for line in md.splitlines():
        if line.startswith('|---') or line.startswith('# '):
            continue
        if line.startswith('|'):
            cells = [c.strip() for c in line.strip('|').split('|')]
            tag = 'th' if cells[0] == '비교군' else 'td'
            table.append('<tr>' + ''.join(f'<{tag}>{html.escape(c)}</{tag}>' for c in cells) + '</tr>')
        elif line.strip():
            text.append(f'<p>{html.escape(line)}</p>')
    page = ('<!doctype html><html lang=ko><meta charset=utf-8><meta http-equiv=refresh content=120><title>D-51 청산 규칙 평가</title>'
            '<style>body{font:15px/1.6 "Malgun Gothic",sans-serif;margin:24px max(16px,3vw);background:#f4f6f7;color:#17212b}'
            'table{border-collapse:collapse;background:#fff;margin:8px 0}td,th{border:1px solid #dde3e7;padding:6px 10px;text-align:right}'
            'td:first-child,th:first-child{text-align:left}p{max-width:80ch;color:#41505c}</style>'
            f'<h1>D-51 청산 규칙 그림자 평가</h1>{text[0] if text else ""}<table>{"".join(table)}</table>{"".join(text[1:])}</html>')
    (out / 'report.html').write_text(page, encoding='utf-8')
    return md


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--report', action='store_true')
    a = ap.parse_args()
    if a.report:
        print(report())
        sys.exit()
    STOP.unlink(missing_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        pid = LOCK.read_text().strip()
        if pid and pid in subprocess.run(['tasklist', '/FI', f'PID eq {pid}'], capture_output=True, text=True,
                                         encoding='mbcs', errors='replace').stdout:
            sys.exit('이미 실행 중입니다.')
        LOCK.unlink()
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    try:
        Study().run()
    finally:
        LOCK.unlink(missing_ok=True)
