"""실거래 실행: 두 AI가 합의한 '실행 주문안'만 하드 한도 안에서 실행한다.

사용자 지시(2026-11-02): "합의되면 승인 절차 없이 시작해", "봇 거래로 인한 손실이 많으면 중지하고 다시 검토시킬게",
"보유 현금으로 실제 투자를 시작하고 상황에 따라 보유 종목을 일부 혹은 전량 매도해서라도 투자를 전개해 봐. 권한을 위임한다".
- auto_proposal이 켜져 있으면 shadow.py가 1시간마다 계산한 봇 주문안(data/proposal.json)도 실행한다.
- 토론.md 안건 표에서 상태가 '합의'이고 내용에 `실행 주문안: ...`이 있는 가장 최근 칸만 읽는다.
- 같은 주문안은 한 번만 실행한다. 조건부(<...>)나 형식이 틀린 주문안은 실행하지 않고 기록한다.
- 한도(config.json live)를 넘거나 누적 손실이 중단선에 닿으면 신규 매수를 하지 않는다.
- 사용자 지시 "한도도 둘이 의논해 자율적으로 정하고, 가능성 있는 모델이 보이면 확대해 전체를 운영하라":
  안건 표의 합의된 `실행 한도:` 칸을 적용한다. 한 번에 각 값은 최대 2배까지만 오르고 계좌 총평가액을 넘지 않는다.
  낮추는 변경은 바로 적용한다. 손실 중단 상태는 사용자만 해제한다.
"""
import hashlib
import os
import json
from pathlib import Path
import re
import time

import strategy as st
import upbit
from paper import now_iso

DEBATE = Path(os.environ.get('AI_DEBATE_DIR', str(Path.home() / 'Desktop' / 'AI토론장'))) / '토론.md'
EVIDENCE = Path(os.environ.get('AI_DEBATE_DIR', str(Path.home() / 'Desktop' / 'AI토론장'))) / '공유_근거자료.md'
ROW = re.compile(r'^\| (\d+) \| [^|]+ \| ([^|]+) \| ([^\n]+) \|\s*$', re.M)
PLAN = re.compile(r'실행 주문안:\s*([^’‘\'"`\n]+)')
SELL = re.compile(r'^매도\s+([A-Z0-9]+)\s+(\d+(?:\.\d+)?)\s*%$')
BUY = re.compile(r'^매수\s+([A-Z0-9]+)\s+([\d,]+)\s*원$')
LIMITS = re.compile(r'실행 한도:\s*([^’‘\'"`\n|]+)')
LIMIT_KEYS = {'1건': 'max_order_krw', '누적': 'max_total_buy_krw', '1회매도': 'max_sell_krw_per_plan', '손실중단': 'loss_stop_krw'}
LIMIT_ITEM = re.compile(r'^(1건|누적|1회매도|손실중단)\s+([\d,]+)\s*원$')

DEFAULTS = {'enabled': False, 'auto_proposal': False, 'max_order_krw': 30000, 'max_total_buy_krw': 100000,
            'max_sell_krw_per_plan': 300000, 'loss_stop_krw': 15000, 'max_spread': 0.01, 'stop_tf': 240}


def parse(text):
    """'매도 ONG 100%; 매수 BTC 20000원' -> [('sell','KRW-ONG',100.0), ('buy','KRW-BTC',20000)]. 형식 오류는 ValueError."""
    orders = []
    for part in [x.strip().rstrip('.') for x in text.split(';')]:
        if not part or part in ('매도 없음', '매수 없음', '없음'):
            continue
        if '<' in part or '>' in part:
            raise ValueError(f'조건부 항목은 실행하지 않음: {part}')
        if m := SELL.match(part):
            pct = float(m[2])
            if not 0 < pct <= 100:
                raise ValueError(f'매도 비율 오류: {part}')
            orders.append(('sell', 'KRW-' + m[1], pct))
        elif m := BUY.match(part):
            orders.append(('buy', 'KRW-' + m[1], int(m[2].replace(',', ''))))
        else:
            raise ValueError(f'형식 오류: {part}')
    return orders


def latest_plan(doc):
    """합의 상태인 칸 중 번호가 가장 큰 실행 주문안 (번호, 원문)."""
    best = None
    for m in ROW.finditer(doc):
        if m[2].strip() != '합의':
            continue
        p = PLAN.search(m[3])
        if p and (best is None or int(m[1]) > best[0]):
            best = (int(m[1]), p[1].strip())
    return best


def parse_limits(text):
    """'1건 60000원; 누적 300000원' -> {'max_order_krw': 60000, 'max_total_buy_krw': 300000}"""
    out = {}
    for part in [x.strip().rstrip('.') for x in text.split(';') if x.strip()]:
        m = LIMIT_ITEM.match(part)
        if not m:
            raise ValueError(f'한도 형식 오류: {part}')
        out[LIMIT_KEYS[m[1]]] = int(m[2].replace(',', ''))
    return out


def latest_limits(doc):
    best = None
    for m in ROW.finditer(doc):
        if m[2].strip() != '합의':
            continue
        p = LIMITS.search(m[3])
        if p and (best is None or int(m[1]) > best[0]):
            best = (int(m[1]), p[1].strip())
    return best


def throttle(current, wanted, ceiling):
    """올리는 값은 현재의 2배와 계좌 총평가액 중 작은 값까지. 내리는 값은 그대로."""
    out = {}
    for k, v in wanted.items():
        cur = current[k]
        out[k] = v if v <= cur else int(min(v, cur * 2, ceiling))
    return out


class Live:
    def __init__(self, folder, cfg, params):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.file = self.folder / 'live.json'
        self.cfg = {**DEFAULTS, **cfg}
        self.p = params
        self.d = json.loads(self.file.read_text(encoding='utf-8')) if self.file.exists() else \
            {'executed': {}, 'positions': {}, 'bought_krw': 0, 'realized': 0, 'halted': ''}
        self.cfg.update(self.d.get('limits', {}))
        self.doc_mtime = 0

    def save(self):
        tmp = self.file.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.d, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(self.file)

    def record(self, row):
        row = {'time': now_iso(), **row}
        with (self.folder / 'orders.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
        return row

    def share(self, msg):
        """토론 AI가 다음 회차에 읽도록 근거자료에 체결 사실을 남긴다."""
        try:
            with EVIDENCE.open('a', encoding='utf-8') as f:
                f.write(f'\n- 실거래 기록 {now_iso()[5:16]} (v5 봇 자동 기록): {msg}\n')
        except OSError:
            pass

    # ---------- 손실 ----------
    def unrealized(self, prices):
        return sum(p['qty'] * prices[m] * (1 - 0.0005) - p['cost'] for m, p in self.d['positions'].items() if m in prices)

    def check_loss(self, prices):
        loss = -(self.d['realized'] + self.unrealized(prices))
        if loss >= self.cfg['loss_stop_krw'] and not self.d['halted']:
            self.d['halted'] = f'{now_iso()} 실거래 누적 손실 {loss:,.0f}원 ≥ 중단선 {self.cfg["loss_stop_krw"]:,}원'
            self.save()
            self.record({'event': '신규 매수 중단', 'reason': self.d['halted']})
            self.share(f'손실 중단선 도달로 신규 매수를 중단함. {self.d["halted"]}. 기존 실거래 보유의 손절 감시는 계속. 사용자 검토 필요.')
        return loss

    # ---------- 주문 ----------
    def _wait(self, order):
        for _ in range(20):
            o = upbit.get_order(order['uuid'])
            if o['state'] in ('done', 'cancel'):
                return o
            time.sleep(0.5)
        return upbit.get_order(order['uuid'])

    @staticmethod
    def _fill(o):
        trades = o.get('trades') or []
        vol = sum(float(t['volume']) for t in trades)
        funds = sum(float(t['funds']) for t in trades)
        return vol, funds, float(o.get('paid_fee') or 0)

    def poll(self, markets):
        """봇 주문안(위임)과 토론 합의 주문안을 확인하고, 새것이면 한 번 실행한다."""
        if not self.cfg['enabled']:
            return
        if self.cfg['auto_proposal']:
            self.poll_proposal(markets)
        if not DEBATE.exists():
            return
        mtime = DEBATE.stat().st_mtime
        if mtime == self.doc_mtime:
            return
        self.doc_mtime = mtime
        doc = DEBATE.read_text(encoding='utf-8')
        try:
            self.apply_limits(doc)
        except Exception as ex:
            self.record({'event': '한도 변경 실패', 'reason': f'{type(ex).__name__}: {ex}'})
        plan = latest_plan(doc)
        if not plan:
            return
        key = hashlib.sha256(f'{plan[0]}|{plan[1]}'.encode()).hexdigest()[:16]
        if key in self.d['executed']:
            return
        self.d['executed'][key] = {'id': plan[0], 'text': plan[1], 'time': now_iso(), 'result': '처리 중'}
        self.save()
        try:
            result = self.execute(plan, markets)
        except Exception as ex:
            result = f'중단: {type(ex).__name__}: {ex}'
        self.d['executed'][key]['result'] = result
        self.save()
        self.record({'event': '주문안 처리', 'id': plan[0], 'plan': plan[1], 'result': result})
        self.share(f'안건 {plan[0]} 「실행 주문안: {plan[1]}」 → {result}')

    def account_value(self):
        rows = upbit.accounts()
        coins = ['KRW-' + r['currency'] for r in rows if r['currency'] != 'KRW']
        listed = upbit.krw_markets()
        tick = upbit.tickers([m for m in coins if m in listed]) if coins else {}
        return sum(float(r['balance']) + float(r['locked']) for r in rows if r['currency'] == 'KRW') + sum(
            (float(r['balance']) + float(r['locked'])) * tick['KRW-' + r['currency']]['trade_price']
            for r in rows if 'KRW-' + r['currency'] in tick)

    def apply_limits(self, doc):
        found = latest_limits(doc)
        if not found or found[0] == self.d.get('limits_id'):
            return
        wanted = parse_limits(found[1])
        applied = throttle(self.cfg, wanted, self.account_value())
        self.cfg.update(applied)
        self.d['limits'] = {**self.d.get('limits', {}), **applied}
        self.d['limits_id'] = found[0]
        self.save()
        text = ', '.join(f'{k}={v:,}' for k, v in applied.items())
        capped = [k for k in wanted if applied[k] != wanted[k]]
        self.record({'event': '한도 변경', 'id': found[0], 'applied': applied, 'wanted': wanted})
        self.share(f'안건 {found[0]} 합의 한도 적용: {text}' + (f' (2배·계좌 총액 상한으로 조정: {", ".join(capped)})' if capped else ''))

    def poll_proposal(self, markets):
        f = self.folder.parent / 'proposal.json'
        if not f.exists():
            return
        prop = json.loads(f.read_text(encoding='utf-8'))
        text = prop['text'].split('실행 주문안:', 1)[-1].strip()
        key = 'P' + hashlib.sha256(f"{prop['time']}|{text}".encode()).hexdigest()[:15]
        if key in self.d['executed']:
            return
        label = f"봇 주문안 {prop['time'][5:16]}"
        self.d['executed'][key] = {'id': label, 'text': text, 'time': now_iso(), 'result': '처리 중'}
        self.save()
        try:
            result = self.execute((label, text), markets)
        except Exception as ex:
            result = f'중단: {type(ex).__name__}: {ex}'
        self.d['executed'][key]['result'] = result
        self.save()
        self.record({'event': '주문안 처리', 'id': label, 'plan': text, 'result': result})
        self.share(f'{label} (사용자 위임으로 자동 실행) 「{text}」 → {result}')

    def execute(self, plan, markets):
        orders = parse(plan[1])
        if not orders:
            return '주문 없음 (매도 없음·매수 없음)'
        acc = {r['currency']: r for r in upbit.accounts()}
        tick = upbit.tickers([m for _, m, _ in orders])
        done = []
        sell_value = sum(float(acc.get(m[4:], {}).get('balance', 0)) * pct / 100 * tick[m]['trade_price']
                         for side, m, pct in orders if side == 'sell' and m in tick)
        if sell_value > self.cfg['max_sell_krw_per_plan']:
            return f'실행 안 함: 매도 예상 {sell_value:,.0f}원이 1회 한도 {self.cfg["max_sell_krw_per_plan"]:,}원 초과'
        for side, m, pct in orders:
            if side != 'sell':
                continue
            avail = float(acc.get(m[4:], {}).get('balance', 0))
            vol = int(avail * pct / 100 * 1e8) / 1e8
            if vol * tick[m]['trade_price'] < 5000:
                done.append(f'{m[4:]} 매도 건너뜀(5,000원 미만)')
                continue
            o = self._wait(upbit.market_sell(m, vol))
            v, funds, fee = self._fill(o)
            if m in self.d['positions']:  # 봇이 산 물량이면 실현 손익 반영
                pos = self.d['positions'][m]
                share = min(1.0, v / pos['qty'])
                self.d['realized'] += funds - fee - pos['cost'] * share
                pos['qty'] -= v
                pos['cost'] *= (1 - share)
                if pos['qty'] <= 1e-8:
                    del self.d['positions'][m]
            self.record({'side': '매도', 'market': m, 'volume': v, 'funds': funds, 'fee': fee, 'state': o['state'], 'plan': plan[0]})
            done.append(f'{m[4:]} 매도 {v:g}개 → {funds - fee:,.0f}원 ({o["state"]})')
        self.save()
        for side, m, krw in orders:
            if side != 'buy':
                continue
            why = self.buy_block(m, krw, markets)
            if why:
                done.append(f'{m[4:]} 매수 안 함: {why}')
                continue
            o = self._wait(upbit.market_buy(m, krw))
            v, funds, fee = self._fill(o)
            if v <= 0:
                done.append(f'{m[4:]} 매수 미체결 ({o["state"]})')
                continue
            entry = funds / v
            bars = upbit.completed_candles(m, self.cfg['stop_tf'], 100)
            atr = st.wilder_atr(bars) or entry * 0.05
            stop = entry - self.p.k * atr
            pos = self.d['positions'].setdefault(m, {'qty': 0.0, 'cost': 0.0, 'stop': stop, 'time': now_iso()})
            pos['qty'] += v
            pos['cost'] += funds + fee
            pos['stop'] = max(pos['stop'], stop) if pos['qty'] > v else stop
            self.d['bought_krw'] += funds + fee
            self.record({'side': '매수', 'market': m, 'volume': v, 'funds': funds, 'fee': fee, 'stop': stop, 'state': o['state'], 'plan': plan[0]})
            done.append(f'{m[4:]} 매수 {funds + fee:,.0f}원 · 손절가 {stop:,.4g}')
            self.save()
        return ' / '.join(done) or '실행할 주문 없음'

    def buy_block(self, m, krw, markets):
        c = self.cfg
        if self.d['halted']:
            return '손실 중단선 도달로 신규 매수 중단 상태'
        if m not in markets:
            return '원화 마켓에 없음'
        if markets[m].get('warning'):
            return '업비트 경보 종목'
        if krw < 5000:
            return '5,000원 미만'
        if krw > c['max_order_krw']:
            return f'1건 한도 {c["max_order_krw"]:,}원 초과'
        if self.d['bought_krw'] + krw > c['max_total_buy_krw']:
            return f'누적 매수 한도 {c["max_total_buy_krw"]:,}원 초과 (현재 {self.d["bought_krw"]:,.0f}원)'
        krw_avail = float(next((r['balance'] for r in upbit.accounts() if r['currency'] == 'KRW'), 0))
        if krw * 1.0005 > krw_avail:
            return f'가용 원화 부족 ({krw_avail:,.0f}원)'
        ob = upbit.orderbooks([m])[m]
        if (ob['ask'] - ob['bid']) / ob['ask'] > c['max_spread']:
            return f'호가 차이 {((ob["ask"] - ob["bid"]) / ob["ask"]):.2%} > {c["max_spread"]:.0%}'
        return ''

    def stop_check(self, prices):
        """실거래로 산 물량만 손절한다. 기존 보유분에는 손대지 않는다."""
        for m, pos in list(self.d['positions'].items()):
            if m in prices and prices[m] <= pos['stop'] and pos['qty'] > 0:
                o = self._wait(upbit.market_sell(m, pos['qty']))
                v, funds, fee = self._fill(o)
                self.d['realized'] += funds - fee - pos['cost']
                del self.d['positions'][m]
                self.save()
                row = self.record({'side': '매도', 'market': m, 'volume': v, 'funds': funds, 'fee': fee, 'reason': '손절', 'state': o['state']})
                self.share(f'{m[4:]} 손절 매도 {funds - fee:,.0f}원 (실현 누적 {self.d["realized"]:+,.0f}원)')
