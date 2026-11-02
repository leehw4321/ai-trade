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
import uuid
import datetime as dt
import math

import strategy as st
import upbit
from paper import now_iso
from session_guard import SessionLost

DEBATE = Path(os.environ.get('AI_DEBATE_DIR', str(Path.home() / 'Desktop' / 'AI토론장'))) / '토론.md'
EVIDENCE = Path(os.environ.get('AI_DEBATE_DIR', str(Path.home() / 'Desktop' / 'AI토론장'))) / '공유_근거자료.md'
ROW = re.compile(r'^\| (\d+) \| [^|]+ \| ([^|]+) \| ([^\n]+) \|\s*$', re.M)
PLAN = re.compile(r'실행 주문안:\s*([^’‘\'"`\n]+)')
SELL = re.compile(r'^매도\s+([A-Z0-9]+)\s+(\d+(?:\.\d+)?)\s*%$')
BUY = re.compile(r'^매수\s+([A-Z0-9]+)\s+([\d,]+)\s*원$')
LIMITS = re.compile(r'실행 한도:\s*([^’‘\'"`\n|]+)')
LIMIT_KEYS = {'1건': 'max_order_krw', '누적': 'max_total_buy_krw', '1회매도': 'max_sell_krw_per_plan', '손실중단': 'loss_stop_krw'}
LIMIT_ITEM = re.compile(r'^(1건|누적|1회매도|손실중단)\s+([\d,]+)\s*원$')
_NO_ASK = object()

DEFAULTS = {'enabled': False, 'auto_proposal': False, 'max_order_krw': 30000, 'max_total_buy_krw': 100000,
            'max_sell_krw_per_plan': 300000, 'loss_stop_krw': 15000, 'max_spread': 0.01, 'stop_tf': 240,
            'trail_enabled': False,  # EXIT-T1 실거래 적용: 사용자 승인 전까지 꺼짐
            'trail_adopted': False,  # 인수 보유분에도 추적 적용(거래소 평균 매수가=E, 15분 ATR로 R 정의). 결정 D-22, 승인 전까지 꺼짐
            'adopted_trail_start_r': 1.0,
            'loss_daily': False,  # True면 손실 집계를 한국시간 하루 단위로 시작하고, 중단은 다음 날 0시에 자동 재개(D-46)
            'loss_stop_pct': 0.0,  # 0이면 loss_stop_krw(금액) 사용. 0보다 크면 '기준 총자산 대비 비율'로 신규 매수 중단(결정 D-37)
            'partial_enabled': False,  # 이익이 partial_r×R 이상이면 partial_frac만큼 현금화(결정 D-34). 승인 전까지 꺼짐
            'partial_r': 2.0, 'partial_frac': 0.3333}


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

    def missing_valuations(self, prices):
        return [m for m in self.d['positions']
                if m not in prices or type(prices[m]) not in (int, float)
                or not math.isfinite(prices[m]) or prices[m] <= 0]

    def equity_total(self, prices=None):
        """계좌 총자산(원화 잔고+잠금+코인 평가). 조회 실패·가격 누락이 있으면 None."""
        try:
            rows = upbit.accounts()
            valid = set(upbit.krw_markets())  # 원화 시장이 없는(상장폐지 등) 자산은 시세 조회에서 제외
            coins = {'KRW-' + r['currency']: float(r['balance']) + float(r['locked']) for r in rows
                     if r['currency'] != 'KRW' and float(r['balance']) + float(r['locked']) > 0
                     and 'KRW-' + r['currency'] in valid}
            krw = sum(float(r['balance']) + float(r['locked']) for r in rows if r['currency'] == 'KRW')
            tick = upbit.tickers(list(coins)) if coins else {}
        except Exception:
            return None
        total = krw
        for m, q in coins.items():
            px = (prices or {}).get(m) or (tick.get(m) or {}).get('trade_price')
            if px: total += q * px  # 시세 없는 먼지 자산은 총자산에서 제외
        return total

    def loss_limit(self, prices=None):
        """신규 매수 중단선(원). loss_stop_pct>0이면 기준 총자산 × 비율(기준은 처음 한 번 저장), 아니면 loss_stop_krw."""
        pct = float(self.cfg.get('loss_stop_pct') or 0)
        if pct <= 0: return float(self.cfg['loss_stop_krw'])
        base = self.d.get('risk_base_equity')
        if not base:
            base = self.equity_total(prices)
            if not base: return float(self.cfg['loss_stop_krw'])  # 총자산을 못 구하면 금액 한도로 대체
            self.d['risk_base_equity'] = base
            self.save()
            self.record({'event': '손실 중단선 기준 총자산 확정', 'base': base, 'pct': pct, 'limit': base * pct})
        return base * pct

    def raw_loss(self, prices):
        return -(self.d['realized'] + self.unrealized(prices) - self.d.get('risk_baseline_unrealized', 0.))

    def resume_buying(self, prices, reason='사용자 지시'):
        """중단 해제. 지금까지의 손실을 기준(offset)으로 옮겨 손실 집계를 지금부터 다시 쌓는다(과거 손실은 장부에 남는다)."""
        if self.missing_valuations(prices):
            return False, '보유분 가격이 없어 손실 기준을 정할 수 없음'
        was = self.d['halted']
        offset = self.raw_loss(prices)
        self.d['risk_loss_offset'] = offset
        self.d['risk_day'] = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime('%Y-%m-%d')
        self.d['halted'] = ''
        self.save()
        self.record({'event': '신규 매수 재개', 'reason': reason, 'loss_offset': offset, 'was_halted': was})
        self.share(f'신규 매수 재개({reason}). 손실 집계 기준을 {offset:,.0f}원으로 옮김. 직전 중단: {was or "없음"}')
        return True, f'재개: 손실 기준 {offset:,.0f}원'

    def check_resume_request(self, prices):
        """data/live/RESUME_BUY 파일이 있으면 수동 재개 요청으로 처리한다(tools/resume_trading.py가 만든다)."""
        f = self.folder / 'RESUME_BUY'
        if not f.exists():
            return None
        try:
            reason = (f.read_text(encoding='utf-8').strip() or '사용자 지시')[:100]
        except Exception:
            reason = '사용자 지시'
        ok, msg = self.resume_buying(prices, '수동 재개: ' + reason)
        if ok:
            f.unlink()
        return msg

    def check_loss(self, prices):
        missing = self.missing_valuations(prices)
        if missing:
            if self.d.get('valuation_incomplete') != missing:
                self.record({'event': '손익 평가 보류', 'markets': missing,
                             'reason': '일부 보유분 가격 누락·무효: 손익을 0으로 간주하지 않음'})
            self.d['valuation_incomplete'] = missing
            return None
        self.d.pop('valuation_incomplete', None)
        if self.cfg.get('loss_daily'):
            today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime('%Y-%m-%d')
            if self.d.get('risk_day') != today:
                was = self.d['halted']
                self.d['risk_day'] = today
                self.d['risk_loss_offset'] = self.raw_loss(prices)
                self.d['halted'] = ''
                self.save()
                self.record({'event': '손실 집계 하루 단위 시작', 'day': today, 'loss_offset': self.d['risk_loss_offset'], 'was_halted': was})
                if was:
                    self.share(f'새 날 시작: 손실 집계를 다시 시작하고 신규 매수를 재개함. 직전 중단: {was}')
        loss = self.raw_loss(prices) - self.d.get('risk_loss_offset', 0.)
        limit = self.loss_limit(prices)
        if loss >= limit and not self.d['halted']:
            pct = float(self.cfg.get('loss_stop_pct') or 0)
            self.d['halted'] = f'{now_iso()} 실거래 누적 손실 {loss:,.0f}원 ≥ 중단선 {limit:,.0f}원' + (f'(기준 총자산의 {pct:.2%})' if pct > 0 else '')
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

    def _pending(self, risk_market=None):
        return (self.d.get('risk_pending_orders', {}).get(risk_market) if risk_market is not None
                else self.d.get('pending_order'))

    def _set_pending(self, pending, risk_market=None):
        if risk_market is None:
            self.d['pending_order'] = pending
        elif pending is None:
            self.d.setdefault('risk_pending_orders', {}).pop(risk_market, None)
        else:
            self.d.setdefault('risk_pending_orders', {})[risk_market] = pending

    def has_uncertain_orders(self):
        return bool(self.d.get('pending_order') or self.d.get('risk_pending_orders'))

    def market_has_uncertain_order(self, market):
        primary = self.d.get('pending_order')
        return bool((primary and primary['market'] == market)
                    or market in self.d.get('risk_pending_orders', {}))

    def _apply_pending(self, order, risk_market=None):
        """Apply cumulative fills once and retain partial/unknown orders durably."""
        pending = self._pending(risk_market)
        if not pending: raise ValueError('정산할 주문 의도 없음')
        if order.get('market', pending['market']) != pending['market']:
            raise ValueError('주문 종목 불일치: 정산 보류')
        if pending.get('uuid') and order.get('uuid', pending['uuid']) != pending['uuid']:
            raise ValueError('주문 식별자 불일치: 정산 보류')
        if order.get('identifier', pending['identifier']) != pending['identifier']:
            raise ValueError('클라이언트 주문 식별자 불일치: 정산 보류')
        if order.get('side', pending['side']) != pending['side']:
            raise ValueError('주문 방향 불일치: 정산 보류')
        volume, funds, fee = self._fill(order)
        if any(not math.isfinite(x) or x < 0 for x in (volume, funds, fee)):
            raise ValueError('체결 수량·금액 무효: 정산 보류')
        terminal = order.get('state') in ('done', 'cancel')
        executed = float(order.get('executed_volume', volume))
        if not math.isfinite(executed) or executed < 0:
            raise ValueError('주문 누적 체결량 무효: 정산 보류')
        if terminal and executed > volume + 1e-8:
            raise ValueError('상세 체결 자료 부족: 주문 대사 필요')
        dv = volume - pending.get('applied_volume', 0.)
        df = funds - pending.get('applied_funds', 0.)
        dfee = fee - pending.get('applied_fee', 0.)
        if min(dv, df, dfee) < -1e-8:
            raise ValueError('주문 누적 체결량 역전: 정산 보류')
        m = pending['market']
        if dv > 1e-12:
            if pending['side'] == 'bid':
                old = self.d['positions'].get(m)
                qty = (old['qty'] if old else 0.) + dv
                cost = (old['cost'] if old else 0.) + df + dfee
                stop = st.cap_stop(df / dv, df / dv - self.p.k * pending['atr'], self.p)
                new = {'qty': qty, 'cost': cost,
                    'stop': max(old['stop'], stop) if old else stop,
                    'time': old['time'] if old else pending['time']}
                if old is None:
                    # EXIT-T1 대상: 봇이 새로 산 포지션만. E·A·초기 손절가는 첫 체결에서 확정한다(안건 26).
                    px = df / dv
                    new.update(entry=px, atr=pending['atr'], stop0=stop,
                               trail=bool(self.cfg.get('trail_enabled')) and stop < px)
                else:
                    # 추가 체결·추가 매수 lot 처리는 미합의(안건 27): 소급 변경하지 않고 EXIT-T1에서 제외한다.
                    for k in ('entry', 'atr', 'stop0', 'last_bar', 'peak'):
                        if k in old: new[k] = old[k]
                    new['trail'] = False
                self.d['positions'][m] = new
                self.d['bought_krw'] += df + dfee
            else:
                pos = self.d['positions'].get(m)
                if not pos and (risk_market is not None or self.d.get('account_adoption')):
                    raise ValueError('위임 장부 없는 매도 체결: 대사 필요')
                if pos:
                    if dv > pos['qty'] + 1e-8:
                        raise ValueError('위임 장부 수량 초과 체결: 대사 필요')
                    fraction = min(1., dv / pos['qty'])
                    basis = pos['cost'] * fraction
                    self.d['realized'] += df - dfee - basis
                    pos['qty'] = max(0., pos['qty'] - dv)
                    pos['cost'] -= basis
                    if pos['qty'] <= 1e-8:
                        # 재진입 규칙(D-38)을 위해 전량 청산 기록을 남긴다
                        self.d.setdefault('last_sell', {})[m] = {'time': time.time(), 'price': df / dv, 'profit': (df - dfee - basis) > 0,
                                                                  'reason': str(pending.get('reason', ''))[:40]}
                        del self.d['positions'][m]
        elif dfee > 0:
            if pending['side'] == 'bid':
                if m in self.d['positions']: self.d['positions'][m]['cost'] += dfee
                self.d['bought_krw'] += dfee
            else:
                self.d['realized'] -= dfee
        pending.update(applied_volume=volume, applied_funds=funds, applied_fee=fee)
        if order.get('uuid'): pending['uuid'] = order['uuid']
        if terminal:
            self._set_pending(None, risk_market)
        self.save()
        if dv > 1e-12 or terminal:
            self.record({'side': '매수' if pending['side'] == 'bid' else '매도', 'market': m,
                'volume': max(0., dv), 'funds': max(0., df), 'fee': max(0., dfee),
                'identifier': pending['identifier'], 'state': order.get('state'), 'reason': pending['reason']})
        return terminal

    def recover_pending(self, risk_market=None):
        pending = self._pending(risk_market)
        if not pending: return True
        order = upbit.get_order(pending['uuid']) if pending.get('uuid') else upbit.get_order_by_identifier(pending['identifier'])
        return self._apply_pending(order, risk_market)

    def recover_all_pending(self):
        """Unrelated market risk exits may continue while an order is unresolved."""
        slots = [None] + list(self.d.get('risk_pending_orders', {}))
        for market in slots:
            if not self._pending(market): continue
            try:
                self.recover_pending(market)
            except Exception as error:
                key = market or 'primary'
                message = type(error).__name__ + ': ' + str(error)[:250]
                alerts = self.d.setdefault('recovery_alerts', {})
                if alerts.get(key) != message:
                    alerts[key] = message
                    try:
                        self.record({'event': '미확정 주문 조회 대기', 'slot': key, 'reason': message})
                    except OSError:
                        pass
        return not self.has_uncertain_orders()

    def _trade(self, side, market, amount, reason, risk=False, entry_context=None, validate_entry=False):
        if not self.cfg['enabled']: raise RuntimeError('실거래 비활성')
        if risk:
            if side != 'ask': raise ValueError('위험 청산은 매도만 허용')
            if self.market_has_uncertain_order(market): raise RuntimeError('동일 종목 미확정 주문: 중복 청산 차단')
        elif self.has_uncertain_orders():
            raise RuntimeError('미확정 주문 대사 중: 중복 주문 차단')
        atr = None
        if side == 'bid':
            if validate_entry:
                why = self.entry_chase_block(market, entry_context)
                if why: raise ValueError(why)
            atr = st.wilder_atr(upbit.completed_candles(market, self.cfg['stop_tf'], 100))
            if atr is None or not math.isfinite(atr) or atr <= 0:
                raise ValueError('손절 ATR 자료 부족·무효: 매수 보류')
            if validate_entry:
                # The ATR lookup can take time or cross a candle boundary. Re-read the
                # ask and source immediately before durable intent and submission.
                book = upbit.orderbooks([market]).get(market, {})
                why = self.entry_chase_block(market, entry_context, book.get('ask'))
                if why: raise ValueError(why)
        pending = {'identifier': 'v5-' + uuid.uuid4().hex, 'side': side, 'market': market,
                   'amount': amount, 'reason': str(reason), 'time': now_iso(), 'atr': atr}
        risk_market = market if risk else None
        self._set_pending(pending, risk_market)
        self.save()  # Persist identifier BEFORE transmission; unknown outcomes are looked up, never replayed.
        try:
            order = (upbit.market_buy(market, amount, identifier=pending['identifier']) if side == 'bid'
                     else upbit.market_sell(market, amount, identifier=pending['identifier']))
        except (upbit.OrderRejected, SessionLost) as error:
            # Rejected submission or failed lease check happened before acceptance; do not create a phantom order.
            self._set_pending(None, risk_market)
            self.save()
            self.record({'event': '주문 전송 차단·거부', 'market': market,
                         'identifier': pending['identifier'], 'reason': str(error)[:300]})
            raise
        if order.get('uuid'):
            pending['uuid'] = order['uuid']; self.save()
        order = self._wait(order)
        if not self._apply_pending(order, risk_market):
            raise RuntimeError('주문 미종결: 체결 반영 후 신규 주문 대기')
        return order

    def poll(self, markets):
        """봇 주문안(위임)과 토론 합의 주문안을 확인하고, 새것이면 한 번 실행한다."""
        if not self.cfg['enabled']:
            return
        if not self.recover_all_pending(): return
        if self.cfg['auto_proposal']:
            self.poll_proposal(markets)
        # An adopted account uses fresh strategy proposals, never replays historical discussion orders.
        if self.d.get('account_adoption'): return
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
        if self.d.get('account_adoption'):
            created = dt.datetime.fromisoformat(prop['time']).timestamp()
            if not self.d['account_adoption']['epoch'] <= created <= time.time(): return
            if time.time() - created > self.cfg.get('proposal_max_age_seconds', 120): return
            # A fresh JSON write does not make an old strategy signal current.
            closed = prop.get('signal_closed_at')
            tf = prop.get('signal_tf_minutes')
            evaluated = prop.get('evaluated_at')
            now = time.time()
            if (type(tf) is not int or tf != self.p.tf or tf <= 0
                    or type(closed) not in (int, float) or not math.isfinite(closed)
                    or type(evaluated) not in (int, float) or not math.isfinite(evaluated)):
                return
            expected = int((now - 20) // (tf * 60)) * tf * 60
            if closed != expected or not closed + 20 <= evaluated <= now:
                return
        text = prop['text'].split('실행 주문안:', 1)[-1].strip()
        key = 'P' + hashlib.sha256(f"{prop['time']}|{text}".encode()).hexdigest()[:15]
        if key in self.d['executed']:
            return
        label = f"봇 주문안 {prop['time'][5:16]}"
        self.d['executed'][key] = {'id': label, 'text': text, 'time': now_iso(), 'result': '처리 중'}
        self.save()
        try:
            result = self.execute((label, text), markets,
                                  entry_context=prop.get('entry_context'), automatic=True)
        except Exception as ex:
            result = f'중단: {type(ex).__name__}: {ex}'
        self.d['executed'][key]['result'] = result
        self.save()
        self.record({'event': '주문안 처리', 'id': label, 'plan': text, 'result': result})
        self.share(f'{label} (사용자 위임으로 자동 실행) 「{text}」 → {result}')

    def execute(self, plan, markets, entry_context=None, automatic=False):
        if self.has_uncertain_orders(): return '미확정 주문 대사 중: 새 주문안 보류'
        orders = parse(plan[1])
        sold_markets = {m for side, m, _ in orders if side == 'sell'}
        bought_markets = {m for side, m, _ in orders if side == 'buy'}
        if sold_markets & bought_markets:
            # Reject before the first account request or order; old generators cannot sell then rebuy.
            return '실행 거부: 동일 주문안 매도·매수 종목 충돌 ' + ', '.join(sorted(sold_markets & bought_markets))
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
            o = self._trade('ask', m, vol, plan[0])
            v, funds, fee = self._fill(o)
            done.append(f'{m[4:]} 매도 {v:g}개 → {funds - fee:,.0f}원 ({o["state"]})')
        self.save()
        for side, m, krw in orders:
            if side != 'buy':
                continue
            # Automatic buys always require source context. Manual plan buys also
            # require it while the chase cap is enabled; absent context is an
            # explicit skip, never an implicit bypass. Disabled-cap manual policy
            # and private low-level recovery/test callers retain their old contract.
            cap = self.p.chase_rs_cap
            strict = (automatic or type(cap) not in (int, float)
                      or not math.isfinite(cap) or cap > 0)
            context = entry_context.get(m) if isinstance(entry_context, dict) else None
            why = (self.buy_block(m, krw, markets, entry_context=context, validate_entry=True)
                   if strict else self.buy_block(m, krw, markets))
            if why:
                done.append(f'{m[4:]} 매수 안 함: {why}')
                continue
            o = (self._trade('bid', m, krw, plan[0], entry_context=context, validate_entry=True)
                 if strict else self._trade('bid', m, krw, plan[0]))
            v, funds, fee = self._fill(o)
            if v <= 0:
                done.append(f'{m[4:]} 매수 미체결 ({o["state"]})')
                continue
            stop = self.d['positions'][m]['stop']
            done.append(f'{m[4:]} 매수 {funds + fee:,.0f}원 · 손절가 {stop:,.4g}')
            self.save()
        return ' / '.join(done) or '실행할 주문 없음'

    def entry_chase_block(self, market, context, ask=_NO_ASK):
        """Validate source identity and enforce the receiver's existing cap; no new threshold."""
        if not isinstance(context, dict) or context.get('market') != market:
            return '매수 신호 출처 누락·종목 불일치: 매수 보류'
        close, anchor, rs = (context.get(k) for k in ('signal_close', 'anchor_close', 'rs'))
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (close, anchor, rs))
                or close <= 0 or anchor <= 0 or rs <= 0):
            return '매수 신호 기준가격·상승률 무효: 매수 보류'
        if not math.isclose(close / anchor - 1, rs, rel_tol=1e-9, abs_tol=1e-12):
            return '매수 신호 가격과 상승률 불일치: 매수 보류'
        closed, tf, evaluated = (context.get(k) for k in ('signal_closed_at', 'signal_tf_minutes', 'evaluated_at'))
        now = time.time()
        if (type(tf) is not int or tf != self.p.tf or tf <= 0
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in (closed, evaluated))):
            return '매수 신호 시각·주기 무효: 매수 보류'
        expected = int((now - 20) // (tf * 60)) * tf * 60
        if closed != expected or not closed + 20 <= evaluated <= now:
            return '매수 신호가 최신 마감 판단과 다름: 매수 보류'
        cap = self.p.chase_rs_cap
        if type(cap) not in (int, float) or not math.isfinite(cap):
            return '과열 상한 설정 무효: 매수 보류'
        if cap > 0 and rs >= cap:
            return '신호 시점부터 기존 과열 상한 이상: 매수 보류'
        if ask is not _NO_ASK:
            if type(ask) not in (int, float) or not math.isfinite(ask) or ask <= 0:
                return '매수 호가 무효: 매수 보류'
            # Price comparison avoids rounding an exact 15% boundary below the cap.
            if cap > 0 and ask >= anchor * (1 + cap):
                return f'실행 호가가 기존 과열 상한 {cap:.1%} 이상: 추격 매수 보류'
        return None

    def buy_block(self, m, krw, markets, entry_context=None, validate_entry=False):
        c = self.cfg
        if self.d['halted']:
            return '손실 중단선 도달로 신규 매수 중단 상태'
        last = self.d.get('last_sell', {}).get(m)
        if last:
            # Re-read the current durable state, not the producer's pre-sale snapshot.
            if type(last.get('time')) not in (int, float) or not math.isfinite(last['time']):
                return '현재 매도 기록 시각 무효: 재진입 보류'
            elapsed = time.time() - last['time']
            if elapsed < 0:
                return '현재 매도 기록이 미래 시각: 재진입 보류'
            hours = self.p.reentry_profit_hours if last.get('profit') else self.p.reentry_loss_hours
            if 0 <= elapsed < min(hours, self.p.reentry_window_hours) * 3600:
                return '현재 매도 장부 기준 재진입 대기: ' + str(m)
        if self.has_uncertain_orders():
            return '미확정 주문 대사 중: 신규 매수 보류'
        if self.d.get('valuation_incomplete'):
            return '일부 보유분 가격 미확인: 신규 매수 보류'
        if m not in markets:
            return '원화 마켓에 없음'
        if markets[m].get('warning'):
            return '업비트 경보 종목'
        if krw < 5000:
            return '5,000원 미만'
        if krw > c['max_order_krw']:
            return f'1건 한도 {c["max_order_krw"]:,}원 초과'
        if self.d['bought_krw'] + krw * (1 + self.p.fee) > c['max_total_buy_krw']:
            return f'누적 매수 한도 {c["max_total_buy_krw"]:,}원 초과 (현재 {self.d["bought_krw"]:,.0f}원)'
        if validate_entry:
            why = self.entry_chase_block(m, entry_context)
            if why: return why
        krw_avail = float(next((r['balance'] for r in upbit.accounts() if r['currency'] == 'KRW'), 0))
        if krw * 1.0005 > krw_avail:
            return f'가용 원화 부족 ({krw_avail:,.0f}원)'
        ob = upbit.orderbooks([m])[m]
        if (ob['ask'] - ob['bid']) / ob['ask'] > c['max_spread']:
            return f'호가 차이 {((ob["ask"] - ob["bid"]) / ob["ask"]):.2%} > {c["max_spread"]:.0%}'
        if validate_entry:
            why = self.entry_chase_block(m, entry_context, ob.get('ask'))
            if why: return why
        if (last and last.get('profit') and self.p.reentry_dip_atr > 0
                and time.time() - last['time'] < self.p.reentry_window_hours * 3600):
            atr = st.wilder_atr(upbit.completed_candles(m, self.p.tf, 100))
            if atr is None or not math.isfinite(atr) or atr <= 0:
                return '재진입 판정 ATR 자료 부족'
            why = st.reentry_block({'close': ob['ask'], 'atr': atr}, last, time.time(), self.p)
            if why: return why
        return ''

    def rebase_adopted_stops(self, prices):
        """인수 보유분의 손절가가 현재가보다 stop_max_pct 넘게 멀면 현재가 기준 상한으로 한 번 올린다(내리지 않음)."""
        if not self.p.stop_max_pct > 0: return
        changed = []
        for m, pos in self.d['positions'].items():
            px = prices.get(m)
            if pos.get('stop_rebased') or type(px) not in (int, float) or not math.isfinite(px) or px <= 0: continue
            new = st.cap_stop(px, pos['stop'], self.p)
            pos['stop_rebased'] = True
            if new > pos['stop']:
                changed.append((m, pos['stop'], new))
                pos['stop'] = new
        if changed:
            for m, old, new in changed:
                self.record({'event': '손절가 상한 적용', 'market': m, 'old_stop': old, 'new_stop': new,
                             'rule': f'손절가는 현재가의 -{self.p.stop_max_pct:.0%}보다 멀 수 없음(결정 D-12)'})
        if changed or self.d['positions']: self.save()

    def stop_limit(self, pos):
        """EXIT-T1 대상은 초기 고정 손절만 실시간 가격으로 보호하고, 상향된 손절가는 확정 1분봉에서 평가한다."""
        if self.cfg.get('trail_enabled') and pos.get('trail'):
            return pos.get('stop0', pos['stop'])
        return pos['stop']

    def adopt_trail_init(self):
        """인수 보유분(entry 없음)에 추적 기준을 한 번 만든다. E=거래소 평균 매수가(cost/qty), A=현재 15분 ATR, 고점=편입 이후 확정 15분봉 고점.
        초기 고정 손절 stop0는 현재 손절가 그대로 쓴다. 진입 시점을 모르므로 편입 시각 이후 봉만 쓰고 R은 3×ATR15로 정의한다(D-22)."""
        done = False
        for m, pos in self.d['positions'].items():
            if 'entry' in pos or pos.get('adopted_trail_failed') or pos['qty'] <= 0: continue
            try:
                bars = upbit.completed_candles(m, 15, 100)
                atr = st.wilder_atr(bars)
            except Exception:
                continue
            entry = pos['cost'] / pos['qty']
            if not atr or not math.isfinite(atr) or atr <= 0 or entry <= 0:
                pos['adopted_trail_failed'] = True
                continue
            after = [b['h'] for b in bars if st.bar_after_entry(b, pos['time'])]
            pos.update(entry=entry, atr=atr, stop0=pos['stop'], trail=True, adopted_trail=True,
                       peak=max([entry] + after))
            self.record({'event': '인수 보유분 추적 기준 생성', 'market': m, 'entry': entry, 'atr15': atr, 'peak': pos['peak'],
                         'rule': '거래소 평균 매수가=E, R=3×ATR15, 1R부터 추적(결정 D-22)'})
            done = True
        return done

    def trail_check(self):
        """EXIT-T1(실거래, 기본 꺼짐). 확정 1분봉 종가로 봉마다 한 번 평가. 봇이 새로 산 포지션만 대상."""
        from dataclasses import replace
        if not self.cfg['enabled'] or not self.cfg.get('trail_enabled'): return
        if not self.recover_all_pending(): return
        p = replace(self.p, trail_enabled=True)
        exits, parts, changed = [], [], False
        if self.cfg.get('trail_adopted') and self.adopt_trail_init(): changed = True
        for m, pos in list(self.d['positions'].items()):
            if not pos.get('trail') or pos['qty'] <= 0 or 'entry' not in pos: continue
            p_pos = replace(p, trail_start_r=float(self.cfg.get('adopted_trail_start_r', 1.0))) if pos.get('adopted_trail') else p
            try: bars = upbit.completed_candles(m, 1, 3)
            except Exception: continue
            bar = bars[-1] if bars else None
            if not bar or pos.get('last_bar') == bar['t'] or not st.bar_after_entry(bar, pos['time']): continue
            pos['last_bar'] = bar['t']
            changed = True
            why = st.ratchet(m, pos, bar['c'], p_pos)
            if why and not self.market_has_uncertain_order(m): exits.append((m, pos, why, bar['c']))
            elif not why and self.cfg.get('partial_enabled') and not pos.get('part_done') and pos['atr'] > 0:
                if (bar['c'] - pos['entry']) / (p_pos.k * pos['atr']) >= float(self.cfg.get('partial_r', 2.0)):
                    parts.append((m, pos, bar['c']))
        if changed: self.save()
        if not exits and not parts: return
        balances = {r['currency']: r for r in upbit.accounts()}
        for m, pos, why, price in exits:
            volume = min(pos['qty'], float(balances.get(m[4:], {}).get('balance', 0)))
            if volume * price < 5000: continue
            try:
                o = self._trade('ask', m, volume, why, risk=True)
                v, funds, fee = self._fill(o)
                self.share(f'{m[4:]} {why} 매도 {funds - fee:,.0f}원 (실현 누적 {self.d["realized"]:+,.0f}원)')
            except SessionLost:
                raise
            except Exception as error:
                self.record({'event': '종목 익절 청산 대기', 'market': m,
                             'reason': type(error).__name__ + ': ' + str(error)[:250]})
        for m, pos, price in parts:
            if self.market_has_uncertain_order(m) or m not in self.d['positions']: continue
            avail = float(balances.get(m[4:], {}).get('balance', 0))
            vol = int(min(pos['qty'], avail) * float(self.cfg.get('partial_frac', 1 / 3)) * 1e8) / 1e8
            if vol * price < 5000 or (pos['qty'] - vol) * price < 5000:
                pos['part_done'] = True  # 너무 작아 나눠 팔 수 없다: 다시 시도하지 않는다
                self.save()
                continue
            try:
                o = self._trade('ask', m, vol, '부분 익절', risk=True)
                v, funds, fee = self._fill(o)
                if m in self.d['positions']: self.d['positions'][m]['part_done'] = True
                self.save()
                self.share(f'{m[4:]} 부분 익절 {funds - fee:,.0f}원 (수익 {(price / pos["entry"] - 1):+.1%} 구간, 실현 누적 {self.d["realized"]:+,.0f}원)')
            except SessionLost:
                raise
            except Exception as error:
                self.record({'event': '부분 익절 대기', 'market': m, 'reason': type(error).__name__ + ': ' + str(error)[:250]})

    def stop_check(self, prices):
        """Manage explicitly delegated positions, retaining partial fills and unresolved orders."""
        if not self.cfg['enabled']: return
        self.recover_all_pending()
        self.rebase_adopted_stops(prices)
        candidates = [(m, pos) for m, pos in list(self.d['positions'].items())
                      if m in prices and type(prices[m]) in (int, float) and math.isfinite(prices[m])
                      and prices[m] > 0 and prices[m] <= self.stop_limit(pos) and pos['qty'] > 0
                      and not self.market_has_uncertain_order(m)]
        if not candidates: return
        balances = {r['currency']: r for r in upbit.accounts()}
        for m, pos in candidates:
            available = float(balances.get(m[4:], {}).get('balance', 0))
            volume = min(pos['qty'], available)
            if volume * prices[m] < 5000: continue
            try:
                o = self._trade('ask', m, volume, '손절', risk=True)
                v, funds, fee = self._fill(o)
                self.share(f'{m[4:]} 손절 매도 {funds - fee:,.0f}원 (실현 누적 {self.d["realized"]:+,.0f}원)')
            except SessionLost:
                raise
            except Exception as error:
                # One market's rejected/uncertain order must not suppress other owned-market exits.
                self.record({'event': '종목 청산 대기', 'market': m,
                             'reason': type(error).__name__ + ': ' + str(error)[:250]})
