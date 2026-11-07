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
import rotation_policy as rotation
import chase_override as co
import upbit
import asset_protection
import continuity
import manual_protection
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


DUST_REASON = '자투리 정리'
DUST_BUY_KRW = 5500   # 최소 주문 5,000원 + 수수료·가격 변동 여유(판 금액도 5,000원 이상이어야 한다)

class ProtectedAssetError(RuntimeError):
    """User-managed assets must never reach automated order submission."""


def normalize_protected_market(value):
    if not isinstance(value, str): raise ValueError('종목 코드는 문자열이어야 합니다')
    value = value.strip().upper()
    value = value if value.startswith('KRW-') else 'KRW-' + value
    if not re.fullmatch(r'KRW-[A-Z0-9]{1,20}', value): raise ValueError('원화 종목 코드 무효')
    return value

DEFAULTS = {'enabled': False, 'auto_proposal': False, 'max_order_krw': 30000, 'max_total_buy_krw': 100000,
            'max_sell_krw_per_plan': 300000, 'loss_stop_krw': 15000, 'max_spread': 0.01, 'stop_tf': 240,
            'trail_enabled': False,  # EXIT-T1 실거래 적용: 사용자 승인 전까지 꺼짐
            'trail_adopted': False,  # 인수 보유분에도 추적 적용(거래소 평균 매수가=E, 15분 ATR로 R 정의). 결정 D-22, 승인 전까지 꺼짐
            'adopted_trail_start_r': 1.0,
            'loss_daily': False,  # True면 손실 집계를 한국시간 하루 단위로 시작하고, 중단은 다음 날 0시에 자동 재개(D-46)
            'loss_stop_pct': 0.0,  # 0이면 loss_stop_krw(금액) 사용. 0보다 크면 '기준 총자산 대비 비율'로 신규 매수 중단(결정 D-37)
            'partial_enabled': False,  # 이익이 partial_r×R 이상이면 partial_frac만큼 현금화(결정 D-34). 승인 전까지 꺼짐
            'partial_r': 2.0, 'partial_frac': 0.3333,
            'partial_pct': 0.0,  # 0보다 크면 R 대신 평단 대비 이 수익률(예: 0.05)에 도달했을 때 partial_frac만큼 현금화(사용자 지시 2026-11-07)
            'manual_ui': False,  # True면 화면의 '강제 청산' 버튼을 받는 127.0.0.1 전용 서버를 연다. 사용자가 설정으로 켜야만 동작
            'manual_port': 8765,
            'manual_buy_max_krw': 100000,  # 화면 '추가 매수' 1회 상한(원). 1건 한도(max_order_krw)보다 클 수 없다
            'profit_guard_enabled': False,  # 도입 후 관측 고점 기준 이익 일부를 실시간 보호선으로 보존. 꺼져 있으면 기존 동작 그대로
            'profit_guard_arm_pct': 0.02, 'profit_guard_giveback': 0.4,
            'protected_markets': [], 'auto_manual_protection': False,
            'protection_ui': False,  # 사용자 직접 주문 기본 보호/화면은 명시 활성 시 적용
            'restore_continuity': False,  # True면 시작 때 거래소 체결 이력에서 봇의 재진입 기록·평단 기반 손절 기준을 복원(계좌 인수 후 상실 방지)
            'r_from_15m': False,  # True면 새 진입의 R(1R=k×ATR)을 설계대로 15분 ATR로 정의(False면 손절용 4시간 ATR)
            'concurrent_warn_at': 6}  # 봇이 관리하는 동시 보유(보호 종목·5천원 미만 제외)가 이 수 이상이면 알림만 낸다(주문 제한 없음, 0이면 끔)


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
        self._rotation_meta = None
        if rotation.enabled(self.p):
            self.d.setdefault('rotation_state', {'floor_slot': rotation.slot(time.time()),
                'last_slot': -1, 'net_realized': 0., 'paused': ''})
            self.save()
            rotation.append_audit(self.folder / 'rotation_audit.jsonl',
                {'event': 'restart', 'policy_hash': rotation.policy_hash(self.p),
                 'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                 'state': dict(self.d['rotation_state'])})

    def save(self):
        tmp = self.file.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.d, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(self.file)

    @property
    def protection_file(self):
        return self.folder.parent / 'user_protected_assets.json'

    def protected_markets(self):
        """Reload at each decision/submission; malformed state blocks submissions."""
        try:
            return asset_protection.effective(self.protection_policy.read(),self.protection_policy.configured)
        except (OSError, ValueError, KeyError, TypeError) as ex:
            raise ProtectedAssetError('사용자 보호 목록 확인 실패: 주문 보류 (' + type(ex).__name__ + ')') from None

    def is_protected(self, market):
        return normalize_protected_market(market) in self.protected_markets()

    @property
    def protection_policy(self):
        configured=self.cfg.get('protected_markets',[])
        if not isinstance(configured,list): raise asset_protection.ProtectionError('설정 보호 목록 형식 오류')
        if not hasattr(self,'_protection_policy'):
            self._protection_policy=asset_protection.Policy(self.protection_file,configured,
                required=bool(self.cfg.get('user_protection_required')))
        self._protection_policy.configured=configured
        self._protection_policy.required=bool(self.cfg.get('user_protection_required'))
        return self._protection_policy

    @property
    def manual_detector(self):
        if not hasattr(self,'_manual_detector'):
            self._manual_detector=manual_protection.Detector(self.protection_policy,self.folder)
        return self._manual_detector

    def scan_manual_orders(self, market=None, risk=False, amount=None, allow_protected=False):
        if not self.cfg.get('auto_manual_protection'): return amount
        try:
            return self.manual_detector.scan(self.d,market,risk,amount,allow_protected)
        except (asset_protection.ProtectionError,OSError,ValueError,TypeError) as error:
            raise ProtectedAssetError(str(error)) from None

    def set_user_protection(self, market, protect, revision=None):
        if not protect: self.scan_manual_orders(market,allow_protected=True)
        result=self.protection_policy.decide(market,protect,expected=revision)
        self.record({'event':'사용자 보호 선택','market':result['market'],'protected':protect})
        return True, ('보호 유지: 봇 주문·손절·익절 제외' if protect else '보호 해제: 다음 봇 판단부터 관리 허용')

    def assert_unprotected(self, market):
        if self.is_protected(market):
            raise ProtectedAssetError(market + ' 사용자 보호 종목: 봇 주문 금지')

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
    def loss_excluded(self):
        """손실 집계에서 뺄 사용자 보호 종목. 목록을 못 읽으면 마지막으로 읽은 목록을 쓴다(없으면 빈 집합)."""
        try:
            self._loss_prot = frozenset(self.protected_markets())
        except (ProtectedAssetError, OSError, ValueError, KeyError, TypeError, asset_protection.ProtectionError):
            pass
        return getattr(self, '_loss_prot', frozenset())

    def unrealized(self, prices, exclude_protected=True):
        skip = self.loss_excluded() if exclude_protected else ()  # 봇이 거래하지 못하는 보호 종목의 시세 변동은 손실 중단 판단에 넣지 않는다
        return sum(p['qty'] * prices[m] * (1 - 0.0005) - p['cost'] for m, p in self.d['positions'].items()
                   if m in prices and normalize_protected_market(m) not in skip)

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

    def raw_loss(self, prices, exclude_protected=True):
        return -(self.d['realized'] + self.unrealized(prices, exclude_protected) - self.d.get('risk_baseline_unrealized', 0.))

    def migrate_loss_scope(self, prices):
        """보호 종목 제외 방식으로 처음 바뀔 때, 오늘 손실 값이 갑자기 달라지지 않게 기준(offset)을 맞춘다."""
        if self.d.get('loss_scope') == 'bot_only':
            return
        old_today = self.raw_loss(prices, False) - self.d.get('risk_loss_offset', 0.)
        self.d['risk_loss_offset'] = self.raw_loss(prices) - old_today
        self.d['loss_scope'] = 'bot_only'
        self.save()
        self.record({'event': '손실 집계에서 사용자 보호 종목 제외', 'today_loss': old_today, 'loss_offset': self.d['risk_loss_offset']})

    def resume_buying(self, prices, reason='사용자 지시'):
        """중단 해제. 지금까지의 손실을 기준(offset)으로 옮겨 손실 집계를 지금부터 다시 쌓는다(과거 손실은 장부에 남는다)."""
        if self.missing_valuations(prices):
            return False, '보유분 가격이 없어 손실 기준을 정할 수 없음'
        self.migrate_loss_scope(prices)
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

    def check_concurrent(self, prices):
        """동시 보유 알림(주문을 막지 않는다): 관리 종목(사용자 보호·5천원 미만 제외)이 concurrent_warn_at 이상이면 경보를 켜고 기록한다."""
        warn = self.cfg.get('concurrent_warn_at', 6)
        if type(warn) is not int or warn <= 0:
            return 0
        try:
            protected = self.protected_markets()
        except (ProtectedAssetError, OSError, ValueError):
            return 0
        held = sorted(m for m, p in self.d['positions'].items()
                      if normalize_protected_market(m) not in protected and p.get('qty', 0) * prices.get(m, 0) >= 5000)
        state = self.d.get('concurrent_warn') or {}
        if len(held) >= warn and not state.get('active'):
            self.d['concurrent_warn'] = {'active': True, 'count': len(held), 'markets': held, 'since': now_iso()}
            self.record({'event': '동시 보유 경보', 'count': len(held), 'markets': held, 'warn_at': warn})
            self.save()
        elif len(held) < warn and state.get('active'):
            self.d['concurrent_warn'] = {'active': False, 'count': len(held), 'cleared': now_iso(), 'peak': state.get('count')}
            self.record({'event': '동시 보유 경보 해제', 'count': len(held)})
            self.save()
        elif state.get('active') and len(held) > state.get('count', 0):
            state.update({'count': len(held), 'markets': held})
            self.d['concurrent_warn'] = state
            self.save()
        return len(held)

    def check_loss(self, prices):
        missing = self.missing_valuations(prices)
        if missing:
            if self.d.get('valuation_incomplete') != missing:
                self.record({'event': '손익 평가 보류', 'markets': missing,
                             'reason': '일부 보유분 가격 누락·무효: 손익을 0으로 간주하지 않음'})
            self.d['valuation_incomplete'] = missing
            return None
        self.d.pop('valuation_incomplete', None)
        self.migrate_loss_scope(prices)
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
        if isinstance(risk_market, tuple):   # ('rest', 종목): 걸어 둔 지정가 매수(종목별로 여러 개)
            return self.d.get('resting_orders', {}).get(risk_market[1])
        return (self.d.get('risk_pending_orders', {}).get(risk_market) if risk_market is not None
                else self.d.get('pending_order'))

    def _set_pending(self, pending, risk_market=None):
        if isinstance(risk_market, tuple):
            if pending is None: self.d.setdefault('resting_orders', {}).pop(risk_market[1], None)
            else: self.d.setdefault('resting_orders', {})[risk_market[1]] = pending
        elif risk_market is None:
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
                    or market in self.d.get('risk_pending_orders', {})
                    or market in self.d.get('resting_orders', {}))

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
        realized_before = self.d.get('realized', 0.)
        basis = None
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
                # 손절 뒤 남은 자투리(최소 주문 5,000원 미만) 위에 산 것은 새 포지션이다(2026-11-06). 옛 손절가·시각을 이어받으면
                # 옛 손절가가 현재가보다 높은 종목(BIRB 138 > 134)은 사자마자 팔린다. 같은 주문의 추가 체결은 time이 같아 이어 붙인다.
                fresh = old is None or (old['qty'] * (df / dv) < 5000 and old.get('time') != pending['time'])
                new = {'qty': qty, 'cost': cost,
                    'stop': stop if fresh else max(old['stop'], stop),
                    'time': pending['time'] if fresh else old['time']}
                if fresh:
                    # EXIT-T1 대상: 봇이 새로 산 포지션만. E·A·초기 손절가는 첫 체결에서 확정한다(안건 26).
                    px = df / dv
                    new.update(entry=px, atr=pending['atr'], stop0=stop,
                               trail=bool(self.cfg.get('trail_enabled')) and stop < px)
                    if pending.get('atr_r'): new['atr_r'] = pending['atr_r']
                    if co.reentry_ok(self.d.get('last_sell', {}).get(m)): new['reentry'] = True  # 이익 청산 뒤 재진입 1회를 쓴 물량
                    try: co.consume(self.folder.parent, m)  # 직접 지정은 1회 권한: 첫 체결로 쓴 것으로 본다
                    except OSError: pass
                else:
                    # 추가 체결·추가 매수 lot 처리는 미합의(안건 27): 소급 변경하지 않고 EXIT-T1에서 제외한다.
                    for k in ('entry', 'atr', 'atr_r', 'stop0', 'last_bar', 'peak', 'profit_guard_v', 'profit_guard_peak', 'profit_guard_stop', 'stop0_rebased', 'stop_rebased', 'bot_basis', 'reentry'):
                        if k in old: new[k] = old[k]
                    new['trail'] = False
                if rotation.enabled(self.p) and old is None:
                    new['rotation_initial_risk'] = {'per_unit': max(0., df / dv - stop),
                        'entry_price': df / dv, 'stop': stop, 'basis': 'first_fill_price_minus_initial_stop_excludes_costs'}
                self.d['positions'][m] = new
                self.d['bought_krw'] += df + dfee
            else:
                pos = self.d['positions'].get(m)
                if not pos and (isinstance(risk_market, str) or self.d.get('account_adoption')):
                    raise ValueError('위임 장부 없는 매도 체결: 대사 필요')
                if pos:
                    if dv > pos['qty'] + 1e-8:
                        raise ValueError('위임 장부 수량 초과 체결: 대사 필요')
                    fraction = min(1., dv / pos['qty'])
                    basis = pos['cost'] * fraction
                    self.d['realized'] += df - dfee - basis
                    pos['qty'] = max(0., pos['qty'] - dv)
                    pos['cost'] -= basis
                    if pos['qty'] <= 1e-8 and pending.get('reason') == DUST_REASON:
                        del self.d['positions'][m]   # 자투리 정리는 투자 매도가 아니다: 직전 매도 기록(재진입 규칙)을 바꾸지 않는다
                    elif pos['qty'] <= 1e-8:
                        # 재진입 규칙(D-38)을 위해 전량 청산 기록을 남긴다
                        self.d.setdefault('last_sell', {})[m] = {'time': time.time(), 'price': df / dv, 'profit': (df - dfee - basis) > 0,
                                                                  'reason': str(pending.get('reason', ''))[:40],
                                                                  'from_reentry': bool(pos.get('reentry'))}
                        del self.d['positions'][m]
        elif dfee > 0:
            if pending['side'] == 'bid':
                if m in self.d['positions']: self.d['positions'][m]['cost'] += dfee
                self.d['bought_krw'] += dfee
            else:
                self.d['realized'] -= dfee
        delta_pnl = self.d.get('realized', 0.) - realized_before
        if rotation.enabled(self.p) and pending.get('rotation_evidence', {}).get('decision'):
            state = self.d['rotation_state']
            state['net_realized'] += delta_pnl
            budget = rotation.saved_loss_budget(self.cfg, self.d)
            if budget is None:
                state['paused'] = '저장된 순환 손실 기준 무효: 정규 순환 보류'
            elif state['net_realized'] <= -budget:
                state['paused'] = '순환 시험 손실이 기존 손실 한도 도달: 정규 순환 보류, 위험 청산 유지'
        pending.update(applied_volume=volume, applied_funds=funds, applied_fee=fee)
        if order.get('uuid'): pending['uuid'] = order['uuid']
        if terminal:
            self._set_pending(None, risk_market)
        self.save()
        if dv > 1e-12 or terminal:
            self.record({'side': '매수' if pending['side'] == 'bid' else '매도', 'market': m,
                'volume': max(0., dv), 'funds': max(0., df), 'fee': max(0., dfee),
                'identifier': pending['identifier'], 'state': order.get('state'), 'reason': pending['reason']})
        if rotation.enabled(self.p) and (dv > 1e-12 or dfee > 0):
            audit = pending.get('rotation_evidence', {})
            fill_times = [t.get('created_at') for t in order.get('trades', []) if isinstance(t, dict) and t.get('created_at')]
            rotation.append_audit(self.folder / 'rotation_audit.jsonl',
                {'event': 'fill', 'market': m, 'side': pending['side'],
                 'volume': dv, 'funds': df, 'fee': dfee, 'realized_delta': delta_pnl,
                 'average_fill': df / dv if dv > 0 else None,
                 'observed_basis': basis,
                 'net_return_pct': delta_pnl / basis * 100 if basis and basis > 0 else None,
                 'r_multiple': (delta_pnl / (dv * audit.get('position_at_submit', {}).get('rotation_initial_risk', {}).get('per_unit', 0)))
                    if dv > 0 and audit.get('position_at_submit', {}).get('rotation_initial_risk', {}).get('per_unit', 0) > 0 else None,
                 'exchange_fill_times_if_provided': fill_times,
                 'exchange_order_created_at': order.get('created_at'), 'analysis': audit})
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
        self.assert_unprotected(market)
        if not self.cfg['enabled']: raise RuntimeError('실거래 비활성')
        if risk:
            if side != 'ask': raise ValueError('위험 청산은 매도만 허용')
            if self.market_has_uncertain_order(market): raise RuntimeError('동일 종목 미확정 주문: 중복 청산 차단')
        elif self.has_uncertain_orders():
            raise RuntimeError('미확정 주문 대사 중: 중복 주문 차단')
        atr = None
        atr_r = None
        if side == 'bid':
            if validate_entry:
                why = self.entry_chase_block(market, entry_context)
                if why: raise ValueError(why)
            atr = st.wilder_atr(upbit.completed_candles(market, self.cfg['stop_tf'], 100))
            if atr is None or not math.isfinite(atr) or atr <= 0:
                raise ValueError('손절 ATR 자료 부족·무효: 매수 보류')
            if self.cfg.get('r_from_15m'):
                try: atr_r = st.wilder_atr(upbit.completed_candles(market, 15, 100))
                except Exception: atr_r = None
                if atr_r is None or not math.isfinite(atr_r) or atr_r <= 0: atr_r = None  # 자료가 없으면 기존 정의로 되돌아간다
        checked=self.scan_manual_orders(market,risk,amount)
        if checked is not None: amount=checked
        if amount<=0: raise ProtectedAssetError('현재 가용 봇 보유 수량 없음: 주문 보류')
        if side=='bid' and validate_entry:
            # Provenance and ATR lookups precede the final executable quote check.
            book = upbit.orderbooks([market]).get(market, {})
            why = self.entry_chase_block(market, entry_context, book.get('ask'))
            if why: raise ValueError(why)
        self.assert_unprotected(market)  # ATR/quote requests may have taken time.
        pending = {'identifier': 'v5-' + uuid.uuid4().hex, 'side': side, 'market': market,
                   'amount': amount, 'reason': str(reason), 'time': now_iso(), 'atr': atr, 'atr_r': atr_r}
        risk_market = market if risk else None
        if rotation.enabled(self.p):
            meta = getattr(self, '_rotation_meta', None)
            evidence = rotation.trade_evidence(self.d['positions'].get(market), reason, time.time(), meta)
            if meta:
                evidence['intent_id'] = rotation.intent_id(meta['slot'], market, side, meta['policy_hash'])
            pending['rotation_evidence'] = evidence
        self._set_pending(pending, risk_market)
        self.save()  # Persist identifier BEFORE transmission; unknown outcomes are looked up, never replayed.
        try:
            self.assert_unprotected(market)  # Do not transmit a newly protected asset.
            order = (upbit.market_buy(market, amount, identifier=pending['identifier']) if side == 'bid'
                     else upbit.market_sell(market, amount, identifier=pending['identifier']))
        except (upbit.OrderRejected, SessionLost, ProtectedAssetError) as error:
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
        if self.d.get('account_adoption') or rotation.enabled(self.p): return
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
        meta = None
        if rotation.enabled(self.p):
            state = self.d['rotation_state']
            if state.get('paused') or self.has_uncertain_orders(): return
            try:
                meta = rotation.validate(prop, self.p, time.time(),
                    max(state['floor_slot'], state.get('last_slot', -1)))
            except ValueError as error:
                rotation.append_audit(self.folder / 'rotation_audit.jsonl',
                    {'event': 'proposal_rejected', 'reason': str(error)})
                return
            state['last_slot'] = meta['slot']
            state['last_decision'] = meta['decision_id']
            state['claim_status'] = 'claimed_at_most_once_crash_may_skip'
            self.save()  # critical durable claim; diagnostics are optional
            meta = {**meta, 'markets': prop.get('rotation_markets', {})}
        text = prop['text'].split('실행 주문안:', 1)[-1].strip()
        key = ('R' + meta['decision_id']) if meta else 'P' + hashlib.sha256(f"{prop['time']}|{text}".encode()).hexdigest()[:15]
        if key in self.d['executed']:
            return
        label = f"봇 주문안 {prop['time'][5:16]}"
        self.d['executed'][key] = {'id': label, 'text': text, 'time': now_iso(), 'result': '처리 중'}
        self.save()
        self._rotation_meta = meta
        try:
            result = self.execute((label, text), markets,
                                  entry_context=prop.get('entry_context'), automatic=True,
                                  market_index=prop.get('market_index'))
        except Exception as ex:
            result = f'중단: {type(ex).__name__}: {ex}'
        self._rotation_meta = None
        if meta:
            self.d['rotation_state']['claim_status'] = 'processed'
            rotation.append_audit(self.folder / 'rotation_audit.jsonl',
                {'event': 'decision_processed', 'decision': meta, 'result': result})
        self.d['executed'][key]['result'] = result
        self.save()
        self.record({'event': '주문안 처리', 'id': label, 'plan': text, 'result': result})
        self.share(f'{label} (사용자 위임으로 자동 실행) 「{text}」 → {result}')

    def execute(self, plan, markets, entry_context=None, automatic=False, market_index=None):
        if self.has_uncertain_orders(): return '미확정 주문 대사 중: 새 주문안 보류'
        orders = parse(plan[1])
        protected = self.protected_markets()
        protected_orders = [m for _, m, _ in orders if m in protected]
        orders = [(side, m, amount) for side, m, amount in orders if m not in protected]
        regime_blocked = []
        if automatic and getattr(self.p, 'halt_new_buys', False):
            regime_blocked = [m for side, m, _ in orders if side == 'buy']
            orders = [o for o in orders if o[0] != 'buy']
            if regime_blocked:
                self.record({'event': '신규 매수 임시 중단으로 차단', 'markets': regime_blocked})
        elif automatic and getattr(self.p, 'regime_filter', False):
            # 수신 쪽에서도 장세 필터를 강제한다: 오래된 생성기나 필터 정보 누락 주문안이 신규 매수를 만들지 못하게 한다. 매도는 그대로.
            expires = getattr(self.p, 'regime_expires', 0.0)
            expired = bool(expires) and time.time() >= expires
            if expired or not (type(market_index) in (int, float) and math.isfinite(market_index) and market_index > 0):
                regime_blocked = [m for side, m, _ in orders if side == 'buy']
                orders = [o for o in orders if o[0] != 'buy']
                if regime_blocked:
                    self.record({'event': '장세 필터 매수 차단', 'markets': regime_blocked, 'market_index': market_index})
        sold_markets = {m for side, m, _ in orders if side == 'sell'}
        bought_markets = {m for side, m, _ in orders if side == 'buy'}
        if sold_markets & bought_markets:
            # Reject before the first account request or order; old generators cannot sell then rebuy.
            return '실행 거부: 동일 주문안 매도·매수 종목 충돌 ' + ', '.join(sorted(sold_markets & bought_markets))
        if not orders:
            if regime_blocked:
                return '장세 필터: 신규 매수 차단 ' + ', '.join(sorted(set(regime_blocked)))
            return ('사용자 보호 종목 주문 제외: ' + ', '.join(sorted(set(protected_orders)))) if protected_orders else '주문 없음 (매도 없음·매수 없음)'
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
            # 전량(100%)은 잔고 그대로 판다. int(x*1e8)/1e8은 이진 소수 오차로 950.45500505를 ...504로 깎아 1e-08개 자투리를
            # 남겼다(2026-11-06 확인: CAP·BIRB·PIEVERSE·LSK·TRUST·WLD). 일부 매도 뒤 남는 양이 최소 주문 미만이면 전량으로 바꾼다.
            vol = avail if pct >= 100 else float(upbit.vol8(avail * pct / 100))
            if (avail - vol) * tick[m]['trade_price'] < 5000: vol = avail
            if vol * tick[m]['trade_price'] < 5000:
                done.append(f'{m[4:]} 매도 건너뜀(5,000원 미만)')
                continue
            rested = self._rest_sell(m, vol, plan[0]) if self.cfg.get('resting_sell_enabled') else None
            if rested:
                done.append(rested)
                continue
            o = self._trade('ask', m, vol, plan[0])
            v, funds, fee = self._fill(o)
            done.append(f'{m[4:]} 매도 {v:g}개 → {funds - fee:,.0f}원 ({o["state"]})')
        self.save()
        rest_ms = self.resting_candidates([m for side, m, _ in orders if side == 'buy'])
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
            if any(p.get('side') == 'ask' for p in self.d.get('resting_orders', {}).values()):
                try: cash = float({r['currency']: r for r in upbit.accounts()}.get('KRW', {}).get('balance', 0))
                except Exception: cash = 0.0
                if cash < krw * 1.01:
                    done.append(f'{m[4:]} 매수 건너뜀: 지정가 매도 대기 중이라 현금 부족({cash:,.0f}원)')
                    continue
            if m in rest_ms and strict:
                done.append(self._rest_buy(m, krw, plan[0], context))
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

    def resting_candidates(self, markets):
        """호가 1칸이 큰 매수 후보 전부. 2026-11-06 사용자 지시·study_limit_entry: 1칸 ≥ 0.3%면 매수 1호가 지정가로 15분 기다리고,
        안 사지면 시장가(가격이 1칸 넘게 오르지 않았을 때만). 180일 1,841건에서 시장가 대비 +0.08~0.33%p/진입.
        지정가는 종목마다 따로 걸어 둔다(resting_orders): 다른 매수·손절을 막지 않는다."""
        if not self.cfg.get('resting_buy_enabled') or not markets: return set()
        try: book = upbit.orderbooks(markets)
        except Exception: return set()
        out = set()
        for m in markets:
            ask = (book.get(m) or {}).get('ask')
            if type(ask) in (int, float) and ask > 0 and upbit.tick_size(ask) / ask >= self.cfg.get('resting_min_tick', 0.003):
                out.add(m)
        return out

    def _rest_buy(self, market, krw, reason, context):
        """매도 1호가 − 1칸에 post_only 지정가를 걸고 바로 돌아온다. 이후는 manage_resting이 3초마다 관리한다."""
        self.assert_unprotected(market)
        if not self.cfg['enabled']: raise RuntimeError('실거래 비활성')
        if self.market_has_uncertain_order(market): return f'{market[4:]} 매수 안 함: 같은 종목 주문 진행 중'
        why = self.entry_chase_block(market, context)
        if why: return f'{market[4:]} 매수 안 함: {why}'
        atr = st.wilder_atr(upbit.completed_candles(market, self.cfg['stop_tf'], 100))
        if atr is None or not math.isfinite(atr) or atr <= 0: return f'{market[4:]} 매수 안 함: 손절 ATR 자료 부족'
        atr_r = None
        if self.cfg.get('r_from_15m'):
            try: atr_r = st.wilder_atr(upbit.completed_candles(market, 15, 100))
            except Exception: atr_r = None
            if atr_r is None or not math.isfinite(atr_r) or atr_r <= 0: atr_r = None
        checked = self.scan_manual_orders(market, False, krw)
        if checked is not None: krw = checked
        ask = upbit.orderbooks([market]).get(market, {}).get('ask')
        why = self.entry_chase_block(market, context, ask)
        if why: return f'{market[4:]} 매수 안 함: {why}'
        tk = upbit.tick_size(ask)
        price = float(upbit.price_str(ask - tk))
        volume = int(krw / (1 + self.p.fee) / price * 1e8) / 1e8
        if volume * price < 5000: return f'{market[4:]} 매수 안 함: 금액 부족'
        wait = float(self.cfg.get('resting_wait_seconds', 900))
        pending = {'identifier': 'v5-' + uuid.uuid4().hex, 'side': 'bid', 'market': market, 'amount': krw,
                   'reason': str(reason), 'time': now_iso(), 'atr': atr, 'atr_r': atr_r,
                   'resting': {'price': price, 'ref_ask': ask, 'tick': tk, 'krw': krw, 'deadline': time.time() + wait,
                               'placed': time.time(), 'anchor': (context or {}).get('anchor_close')}}
        slot = ('rest', market)
        self._set_pending(pending, slot)
        self.save()
        try:
            self.assert_unprotected(market)
            order = upbit.limit_buy_post_only(market, price, volume, identifier=pending['identifier'])
        except (upbit.OrderRejected, SessionLost, ProtectedAssetError) as error:
            self._set_pending(None, slot); self.save()
            self.record({'event': '주문 전송 차단·거부', 'market': market, 'identifier': pending['identifier'], 'reason': str(error)[:300]})
            raise
        if order.get('uuid'):
            pending['uuid'] = order['uuid']; self.save()
        self.record({'event': '지정가 매수 대기', 'market': market, 'price': price, 'ref_ask': ask, 'krw': krw, 'wait_s': wait})
        return f'{market[4:]} 지정가 매수 대기 {price:,.8g}원(매도호가 {ask:,.8g}) · 최대 {wait / 60:.0f}분, 안 사지면 시장가'

    def _rest_sell(self, market, volume, reason):
        """순위 이탈 등 주문안 매도를 지정가 우선으로(사용자 지시 2026-11-07): 호가 1칸이 큰 종목은 매도 1호가에 post_only 지정가를 걸고
        resting_sell_wait_seconds(기본 20분) 안에 안 팔리면 시장가. 급락 중이면 지정가 없이 바로 시장가(None을 돌려줘 호출자가 시장가로 판다).
        이후는 manage_resting이 3초마다 관리한다(손절선 근접·하락 중·기한 만료면 취소 후 남은 수량 시장가). 손절·이익보호는 이 경로를 쓰지 않는다."""
        try:
            ask = upbit.orderbooks([market]).get(market, {}).get('ask')
            if type(ask) not in (int, float) or not math.isfinite(ask) or ask <= 0: return None
            tk = upbit.tick_size(ask)
            if tk / ask < self.cfg.get('resting_min_tick', 0.003): return None
            bars = upbit.completed_candles(market, 1, 20)[-15:]
            crash = float(self.cfg.get('resting_sell_crash_pct', 0.015))
            if bars and ask <= max(b['h'] for b in bars) * (1 - crash): return None   # 급락 중: 기다리지 않는다
        except SessionLost: raise
        except Exception: return None
        return self._place_rest_sell(market, volume, reason, ask, tk)

    def _place_rest_sell(self, market, volume, reason, ask, tk, manual=False):
        """매도 1호가(ask)에 post_only 지정가 매도를 걸고 resting_orders에 기록한다. 거절·중복·최소금액 미달이면 None.
        manual=True(사용자 수동 지정가 매도): 기한이 지나면 취소만 하고 시장가로 바꾸지 않는다(손절선 근접만 안전을 위해 시장가)."""
        if self.market_has_uncertain_order(market): return None
        self.assert_unprotected(market)
        checked = self.scan_manual_orders(market, False, volume)
        if checked is not None: volume = checked
        volume = float(upbit.vol8(volume))
        if volume * ask < 5000: return None
        wait = float(self.cfg.get('resting_sell_wait_seconds', 1200))
        pending = {'identifier': 'v5-' + uuid.uuid4().hex, 'side': 'ask', 'market': market, 'amount': volume,
                   'reason': str(reason), 'time': now_iso(), 'atr': None, 'atr_r': None,
                   'resting': {'price': ask, 'tick': tk, 'volume': volume, 'deadline': time.time() + wait, 'placed': time.time(),
                               'manual': bool(manual)}}
        slot = ('rest', market)
        self._set_pending(pending, slot)
        self.save()
        try:
            self.assert_unprotected(market)
            order = upbit.limit_sell_post_only(market, ask, volume, identifier=pending['identifier'])
        except (upbit.OrderRejected, SessionLost, ProtectedAssetError) as error:
            self._set_pending(None, slot); self.save()
            self.record({'event': '주문 전송 차단·거부', 'market': market, 'identifier': pending['identifier'], 'reason': str(error)[:300]})
            if isinstance(error, upbit.OrderRejected): return None   # post_only가 거절되면(이미 체결 가격) 시장가로 판다
            raise
        if order.get('uuid'):
            pending['uuid'] = order['uuid']; self.save()
        self.record({'event': '지정가 매도 대기', 'market': market, 'price': ask, 'volume': volume, 'wait_s': wait, 'reason': str(reason)})
        if manual: return f'{market[4:]} 지정가 매도 대기 {ask:,.8g}원 · {volume:g}개 · 최대 {wait / 60:.0f}분, 안 팔리면 취소(시장가 전환 없음)'
        return f'{market[4:]} 지정가 매도 대기 {ask:,.8g}원 · 최대 {wait / 60:.0f}분, 안 팔리면 시장가'

    def _manage_rest_sell(self, m, prices):
        """걸어 둔 지정가 매도 관리. 체결분은 즉시 장부에 반영. 손절선 근접(+0.5%)·지정가보다 resting_sell_drop_pct 하락·기한 만료 →
        취소하고 남은 수량을 시장가로 판다(위험 청산 경로). 보호 지정이면 취소만 한다."""
        slot = ('rest', m)
        pending = self._pending(slot)
        rest = pending['resting']
        order = upbit.get_order(pending['uuid'])
        why = None
        if order.get('state') not in ('done', 'cancel'):
            px = prices.get(m)
            if px is None:
                try: px = upbit.tickers([m])[m]['trade_price']
                except Exception: px = None
            live_px = type(px) in (int, float) and math.isfinite(px) and px > 0
            pos = self.d['positions'].get(m)
            if self.is_protected(m): why = '사용자 보호 지정'
            elif live_px and pos and px <= self.stop_limit(pos) * 1.005: why = '손절선 근접'
            elif live_px and not rest.get('manual') and px <= rest['price'] * (1 - float(self.cfg.get('resting_sell_drop_pct', 0.005))): why = '가격 하락 중'
            elif time.time() >= rest['deadline']: why = '대기 시간 만료'
            if not why:
                self._apply_pending(order, slot)
                return
            upbit.cancel_order(pending['uuid'])
            order = self._wait(order)
        _, f0, fee0 = self._fill(order)
        if not self._apply_pending(order, slot): return
        pos = self.d['positions'].get(m)
        msg = f'{m[4:]} 지정가 매도 {f0 - fee0:,.0f}원 체결'
        manual_expired = bool(rest.get('manual')) and why == '대기 시간 만료'
        if manual_expired: msg += ' · 수동 지정가 기한 만료로 취소(시장가 전환 없음, 남은 수량은 보유 그대로)'
        if pos and pos.get('qty', 0) > 0 and why != '사용자 보호 지정' and not manual_expired and self.cfg['enabled'] and not self.is_protected(m):
            try:
                bal = float({r['currency']: r for r in upbit.accounts()}.get(m[4:], {}).get('balance', 0))
                vol = min(pos['qty'], bal)
                px = prices.get(m) or 0
                if vol > 0 and (px <= 0 or vol * px >= 5000):
                    o = self._trade('ask', m, vol, pending['reason'] + f' (지정가 미체결분 시장가: {why or "종료"})', risk=True)
                    v, f1, fee1 = self._fill(o)
                    msg += f' + 시장가 {f1 - fee1:,.0f}원({why})'
            except SessionLost: raise
            except Exception as ex: msg += f' · 시장가 전환 실패 {type(ex).__name__}: {str(ex)[:80]}'
        self.record({'event': '지정가 매도 종료', 'market': m, 'filled_krw': f0, 'note': msg})
        self.share(msg)

    def resting_info(self):
        """화면 표시용: {종목: {price, ref_ask, krw, deadline, ...}} 걸어 둔 지정가 매수."""
        return {m: dict(p['resting'], market=m) for m, p in self.d.get('resting_orders', {}).items() if p.get('side') != 'ask'}

    def request_resting_now(self, market):
        """화면의 '즉시 시장가' 버튼: 다음 관리 주기에 지정가를 취소하고 남은 금액을 바로 시장가로 산다(가격 조건 없이)."""
        p = self.d.get('resting_orders', {}).get(market)
        if not p: return False, '걸어 둔 지정가 매수가 없음'
        p['resting']['deadline'] = 0; p['resting']['force'] = True; self.save()
        return True, f'{market[4:]} 지정가 취소 후 즉시 시장가 매수 요청'

    def manage_resting(self, prices, still_ok=None):
        """걸어 둔 지정가 매수 관리(3초마다, 손절 감시보다 먼저). 종목마다 따로 처리하고 한 종목 오류가 다른 종목을 막지 않는다.
        still_ok(종목, resting) → 매수 조건이 무너졌으면 사유 문자열(봇의 새 15분봉 판단: 순위·상승률·과열·장세 필터·경보)."""
        errors = []
        for m in list(self.d.get('resting_orders', {})):
            try: self._manage_one_resting(m, prices, still_ok)
            except SessionLost: raise
            except Exception as ex: errors.append(f'{m[4:]} {type(ex).__name__}: {str(ex)[:80]}')
        if errors: raise RuntimeError(' / '.join(errors))

    def resting_broken(self, m, rest, px, still_ok=None):
        """대기 중 매수 조건이 무너졌는지(2026-11-06 사용자 지시). 사유 문자열 또는 None. 무너지면 취소하고 남은 금액은 사지 않는다."""
        if self.is_protected(m): return '사용자 보호 지정'
        if self.d.get('halted'): return '손실 중단선 도달'
        pos = self.d['positions'].get(m)
        live_px = type(px) in (int, float) and math.isfinite(px) and px > 0
        if pos and live_px and px <= self.stop_limit(pos) * 1.005: return '손절선 근접'
        anchor = rest.get('anchor')
        if live_px and type(anchor) in (int, float) and anchor > 0 and px <= anchor: return '18시간 상승률 0 이하'
        return still_ok(m, rest) if still_ok else None

    def _manage_one_resting(self, m, prices, still_ok=None):
        """체결분은 즉시 장부에 반영(_apply_pending). 기한 만료 → 취소 후 남은 금액은 매도호가가 기준가+1칸 이하일 때만 시장가.
        보호 지정·손절선 근접 → 취소하고 사지 않는다."""
        slot = ('rest', m)
        pending = self._pending(slot)
        if not pending or not pending.get('uuid'): return
        if pending.get('side') == 'ask': return self._manage_rest_sell(m, prices)
        rest = pending['resting']
        order = upbit.get_order(pending['uuid'])
        if order.get('state') not in ('done', 'cancel'):
            px = prices.get(m)
            if px is None:
                try: px = upbit.tickers([m])[m]['trade_price']
                except Exception: px = None
            broken = self.resting_broken(m, rest, px, still_ok)
            if not broken and time.time() < rest['deadline']:
                self._apply_pending(order, slot)    # 일부 체결분을 바로 장부에 넣어 손절 관리를 시작한다
                return
            if broken: rest['no_fallback'] = broken; self.save()
            upbit.cancel_order(pending['uuid'])
            order = self._wait(order)
        _, f0, fee0 = self._fill(order)
        if not self._apply_pending(order, slot):
            return
        left = rest['krw'] - f0 - fee0
        msg = f'{m[4:]} 지정가 매수 {f0:,.0f}원 체결'
        if left >= 5000 and not rest.get('no_fallback') and self.cfg['enabled'] and not self.is_protected(m):
            ask = upbit.orderbooks([m]).get(m, {}).get('ask')
            if rest.get('force') or (type(ask) in (int, float) and ask <= rest['ref_ask'] + rest['tick'] * 1.0001):
                try:
                    v, f1, fee1 = self._fill(self._trade('bid', m, int(left), pending['reason'] + ' (지정가 미체결분 시장가)'))
                    msg += f' + 시장가 {f1 + fee1:,.0f}원'
                except SessionLost: raise
                except Exception as ex: msg += f' · 시장가 전환 실패 {type(ex).__name__}: {str(ex)[:80]}'
            else:
                msg += f' · 남은 {left:,.0f}원은 가격이 1칸 넘게 올라(매도호가 {ask}) 사지 않음'
        elif rest.get('no_fallback'):
            why = rest['no_fallback'] if isinstance(rest['no_fallback'], str) else '조건 붕괴'
            msg += f' · 매수 조건 붕괴({why})로 취소, 남은 금액 사지 않음'
        self.record({'event': '지정가 매수 종료', 'market': m, 'filled_krw': f0, 'left_krw': max(0., left), 'note': msg})
        self.share(msg)

    def entry_chase_block(self, market, context, ask=_NO_ASK):
        """Validate source identity and enforce the receiver's existing cap; no new threshold."""
        if rotation.enabled(self.p):
            if (not isinstance(context, dict) or type(context.get('rank')) is not int
                    or not 1 <= context['rank'] <= self.p.M
                    or context.get('policy_hash') != rotation.policy_hash(self.p)):
                return '신규 매수 순위·유지 범위·정책 불일치: 매수 보류'
            last = self.d.get('last_sell', {}).get(market)
            why = None if co.reentry_ok(last) else rotation.fresh_reentry(last, self.p, context.get('signal_closed_at'))
            if why: return why
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
        # 사용자 직접 지정·이익 청산 뒤 재진입은 상한 20%(chase_override). 생성기 표시를 믿지 않고 이 장부·파일로 다시 판단한다.
        cap = co.cap_for(market, self.p, co.promoted(self.folder.parent), self.d.get('last_sell', {}).get(market))
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
        if self.is_protected(m): return '사용자 보호 종목: 자동·추가 매수 금지'
        c = self.cfg
        if self.d['halted']:
            return '손실 중단선 도달로 신규 매수 중단 상태'
        last = self.d.get('last_sell', {}).get(m)
        again = co.reentry_ok(last)  # 이익 청산 뒤 재진입 1회: 대기·눌림 조건 없이 허용(사용자 지시 2026-11-05)
        if last and not again:
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
        if markets[m].get('trade_blocked', markets[m].get('warning')):
            return '업비트 경보 종목'  # 거래량 급등·입금량 급증·가격 급등락 경보만 있으면 제한하지 않는다(사용자 지시 2026-11-04)
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
        if (last and not again and last.get('profit') and self.p.reentry_dip_atr > 0
                and time.time() - last['time'] < self.p.reentry_window_hours * 3600):
            reentry_bars = upbit.completed_candles(m, self.p.tf, 100)
            atr = st.wilder_atr(reentry_bars)
            if atr is None or not math.isfinite(atr) or atr <= 0:
                return '재진입 판정 ATR 자료 부족'
            reentry_row = {'close': ob['ask'], 'atr': atr}
            if rotation.enabled(self.p) and reentry_bars:
                reentry_row['bar'] = reentry_bars[-1]['t']
            why = st.reentry_block(reentry_row, last, time.time(), self.p)
            if why: return why
        return ''

    def restore_continuity(self, rows=None):
        """시작할 때 한 번: 거래소 체결 이력에서 봇(v5-)의 마지막 매도(재진입 기록)와, 인수 보유의 원래 평단(손절 기준)을 복원한다.
        이력 조회가 실패하거나 확인이 부족하면 아무것도 바꾸지 않는다. 사용자 보호·주문 경로는 건드리지 않는다."""
        if not (self.cfg.get('restore_continuity') and self.cfg.get('enabled')): return {}
        if rows is None:
            try: rows = manual_protection.HistoryClient().orders(None)
            except Exception as ex:
                self.record({'event': '연속성 복원 보류', 'reason': type(ex).__name__ + ': ' + str(ex)[:120]})
                return {}
        replay = continuity.replay_bot_orders(rows)
        now = time.time()
        done = {'last_sell': [], 'bot_basis': []}
        last = self.d.setdefault('last_sell', {})
        pending = self.d.get('pending_order') or {}
        for m, info in replay.items():
            if self.d['positions'].get(m): continue  # 보유 중인 종목은 아직 청산 기록이 없다
            if pending.get('market') == m or self.market_has_uncertain_order(m): continue  # 진행·미확정 주문이 있는 종목은 건드리지 않는다
            rec = continuity.restorable_last_sell(info, last.get(m), now)
            if rec:
                last[m] = rec
                done['last_sell'].append(m)
        for m, pos in self.d['positions'].items():
            if pos.get('inherited') and not pos.get('stop_rebased') and not pos.get('bot_basis') and not self.is_protected(m):
                basis = continuity.bot_basis(replay.get(m), pos.get('qty'))
                if basis:
                    pos['bot_basis'] = basis
                    done['bot_basis'].append(m)
        if done['last_sell'] or done['bot_basis']:
            self.record({'event': '연속성 복원', 'last_sell': done['last_sell'], 'bot_basis': done['bot_basis']})
            self.save()
        return done

    def rebase_adopted_stops(self, prices):
        """인수 보유분의 손절가가 현재가보다 stop_max_pct 넘게 멀면 현재가 기준 상한으로 한 번 올린다(내리지 않음).
        실시간 보호 기준인 stop0에도 같은 상한을 한 번 적용한다(stop0_rebased)."""
        if not self.p.stop_max_pct > 0: return
        changed, changed0 = [], []
        for m, pos in self.d['positions'].items():
            px = prices.get(m)
            if self.is_protected(m): continue
            if type(px) not in (int, float) or not math.isfinite(px) or px <= 0: continue
            # 거래소 이력으로 봇이 산 물량임이 입증된 보유(bot_basis)는 인수 시점 현재가가 아니라 원래 평단 기준으로 상한을 적용한다.
            basis = pos.get('bot_basis')
            if type(basis) in (int, float) and math.isfinite(basis) and basis > 0: px = basis
            if not pos.get('stop_rebased'):
                new = st.cap_stop(px, pos['stop'], self.p)
                pos['stop_rebased'] = True
                if new > pos['stop']:
                    changed.append((m, pos['stop'], new))
                    pos['stop'] = new
            if 'stop0' in pos and not pos.get('stop0_rebased'):
                new = st.cap_stop(px, pos['stop0'], self.p)
                pos['stop0_rebased'] = True
                if new > pos['stop0']:
                    changed0.append((m, pos['stop0'], new))
                    pos['stop0'] = new
        if changed:
            for m, old, new in changed:
                self.record({'event': '손절가 상한 적용', 'market': m, 'old_stop': old, 'new_stop': new,
                             'rule': f'손절가는 현재가의 -{self.p.stop_max_pct:.0%}보다 멀 수 없음(결정 D-12)'})
        for m, old, new in changed0:
            self.record({'event': '실시간 손절가 상한 적용', 'market': m, 'old_stop0': old, 'new_stop0': new,
                         'rule': f'실시간 보호 손절(stop0)도 현재가의 -{self.p.stop_max_pct:.0%}보다 멀 수 없음(결정 D-12)'})
        if changed or changed0 or self.d['positions']: self.save()

    def stop_limit(self, pos):
        """실시간 가격으로 평가할 매도선. 사용자 지시(2026-11-04) "손절 기준을 넘어서면 인식 즉시 손절 실행"에 따라
        추적으로 올라간 손절가도 1분봉 마감을 기다리지 않고 실시간 가격으로 평가한다. 이익 보호선도 함께 본다."""
        base = max(pos['stop'], pos.get('stop0', pos['stop']))
        g = pos.get('profit_guard_stop') if self.cfg.get('profit_guard_enabled') else None
        return max(base, g) if type(g) in (int, float) and math.isfinite(g) else base

    def update_profit_guard(self, prices):
        """도입 후 관측한 고점이 평단 대비 arm_pct 이상이면 고점 이익의 (1-giveback)를 보존하는 보호선을 올린다(내리지 않음).
        수수료 바닥은 전략 수수료 추정치이며 실제 체결가를 보장하지 않는다."""
        if self.cfg.get('profit_guard_enabled') is not True: return
        a = self.cfg.get('profit_guard_arm_pct', 0.02)
        g = self.cfg.get('profit_guard_giveback', 0.4)
        if isinstance(a, bool) or isinstance(g, bool) or isinstance(self.p.fee, bool): return
        try: arm, give, fee = float(a), float(g), float(self.p.fee)
        except (TypeError, ValueError, OverflowError, AttributeError): return
        if not (math.isfinite(arm) and arm > 0 and math.isfinite(give) and 0 < give < 1
                and math.isfinite(fee) and fee >= 0): return
        ok = lambda x: type(x) in (int, float) and math.isfinite(x) and x > 0
        changed = False
        for m, pos in self.d['positions'].items():
            px, entry, q = prices.get(m), pos.get('entry'), pos.get('qty')
            if self.is_protected(m): continue
            if ok(q) and ok(pos.get('cost')): entry = pos['cost'] / q
            if not (ok(px) and ok(entry) and ok(q)): continue
            if q * px < 5000: continue  # 팔 수 없는 자투리(최소 주문 미만)는 보호선을 두지 않는다
            try: c = float(st.cost(m, self.p))
            except (TypeError, ValueError, OverflowError): continue
            floor = 1 + 2 * fee + c
            if not (math.isfinite(c) and c >= 0 and math.isfinite(floor)): continue
            old_peak = pos.get('profit_guard_peak') if pos.get('profit_guard_v') == 1 else None
            if old_peak is None:
                pos['profit_guard_v'] = 1
                changed = True
            peak = max(old_peak if ok(old_peak) else 0, entry, px)
            if peak != old_peak:
                pos['profit_guard_peak'] = peak
                changed = True
            if peak < entry * (1 + arm):
                # 본전 잠금(사용자 지시 2026-11-05 '라'): 고점이 +lock 이상이면 보호선을 본전(수수료·비용 포함)으로 올린다.
                lock = self.cfg.get('breakeven_lock_pct')
                if type(lock) in (int, float) and math.isfinite(lock) and 0 < lock < arm and peak >= entry * (1 + lock):
                    old = pos.get('profit_guard_stop')
                    old = old if ok(old) else 0
                    if entry * floor > old:
                        self.record({'event': '본전 잠금', 'market': m, 'entry': entry, 'peak': peak, 'guard_stop': entry * floor})
                        pos['profit_guard_stop'] = entry * floor
                        changed = True
                continue
            old = pos.get('profit_guard_stop')
            old = old if ok(old) else 0
            new = max(old, entry * floor, entry + (peak - entry) * (1 - give))
            if new > old:
                if not old:
                    self.record({'event': '이익보호 시작', 'market': m, 'entry': entry, 'peak': peak, 'guard_stop': new})
                pos['profit_guard_stop'] = new
                changed = True
        if changed: self.save()

    def adopt_trail_init(self):
        """인수 보유분(entry 없음)에 추적 기준을 한 번 만든다. E=거래소 평균 매수가(cost/qty), A=현재 15분 ATR, 고점=편입 이후 확정 15분봉 고점.
        초기 고정 손절 stop0는 현재 손절가 그대로 쓴다. 진입 시점을 모르므로 편입 시각 이후 봉만 쓰고 R은 3×ATR15로 정의한다(D-22)."""
        done = False
        for m, pos in self.d['positions'].items():
            if self.is_protected(m): continue
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
            if self.is_protected(m): continue
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
            elif not why and self.cfg.get('partial_enabled') and not pos.get('part_done'):
                pct = self.cfg.get('partial_pct')
                if type(pct) in (int, float) and math.isfinite(pct) and pct > 0:
                    hit = pos['entry'] > 0 and bar['c'] / pos['entry'] - 1 >= pct
                else:
                    a = pos.get('atr_r') or pos['atr']
                    hit = a > 0 and (bar['c'] - pos['entry']) / (p_pos.k * a) >= float(self.cfg.get('partial_r', 2.0))
                if hit: parts.append((m, pos, bar['c']))
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
            vol = float(upbit.vol8(min(pos['qty'], avail) * float(self.cfg.get('partial_frac', 1 / 3))))
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

    def liquidate_position(self, market, prices, reason='수동 강제 청산', pct=100, ord_type='market'):
        """사용자가 화면에서 확인한 종목 한 개를 전량 시장가 매도한다. (성공 여부, 안내문)을 돌려준다.

        봇이 관리하는 장부 종목만 허용하고 손절과 같은 위험 청산 경로(_trade risk=True)를 쓴다.
        그래서 주문 식별자 선기록·실행권 검사·미확정 주문 대사·장부 반영이 손절과 동일하다.
        """
        if self.is_protected(market): return False, '사용자 보호 종목: 해제 전에는 봇 화면 주문도 금지'
        if not self.cfg['enabled']: return False, '실거래가 꺼져 있음'
        pos = self.d['positions'].get(market)
        if not pos or not pos.get('qty', 0) > 0: return False, '봇이 관리하는 보유 종목이 아님'
        price = prices.get(market)
        if type(price) not in (int, float) or not math.isfinite(price) or price <= 0:
            return False, '현재가를 확인할 수 없어 청산 보류'
        if self.market_has_uncertain_order(market): return False, '미확정 주문 대사 중: 청산 보류'
        balances = {r['currency']: r for r in upbit.accounts()}
        volume = min(pos['qty'], float(balances.get(market[4:], {}).get('balance', 0)))
        if volume * price < 5000:
            return False, f'평가액 {volume * price:,.0f}원: 최소 주문금액 5,000원 미만이라 청산할 수 없음'
        if pct != 100 or ord_type != 'market':
            if pct not in (25, 50, 75, 100) or ord_type not in ('market', 'limit'): return False, '매도 비율·주문 방식이 올바르지 않음'
            if pct < 100:
                part = float(upbit.vol8(volume * pct / 100))
                if part * price < 5000: return False, f'{pct}%는 평가액 {part * price:,.0f}원으로 최소 주문금액 5,000원 미만이라 팔 수 없음(더 큰 비율을 고르세요)'
                if (volume - part) * price < 5000: part = volume   # 남는 양이 최소 주문 미만이면 전량(자투리 방지)
                volume = part
            reason = f'수동 매도 {pct}%' + (' 지정가' if ord_type == 'limit' else '')
        self.record({'event': '수동 청산 요청', 'market': market, 'reason': reason, 'pct': pct, 'ord': ord_type})
        if ord_type == 'limit':
            try:
                book = upbit.orderbooks([market]).get(market, {})
                ask = book.get('ask')
                if type(ask) not in (int, float) or not math.isfinite(ask) or ask <= 0: return False, '매도 1호가를 확인할 수 없어 보류'
                msg = self._place_rest_sell(market, volume, reason, ask, upbit.tick_size(ask), manual=True)
            except SessionLost:
                raise
            except Exception as error:
                self.record({'event': '수동 청산 실패', 'market': market, 'reason': type(error).__name__ + ': ' + str(error)[:250]})
                return False, f'지정가 매도 실패: {type(error).__name__}: {str(error)[:120]}'
            if not msg: return False, '지정가 매도를 걸 수 없음(이미 진행 중인 주문·거절·최소 금액). 주문 기록을 확인하세요.'
            self.share(msg + ' (수동 지정가 매도)')
            return True, msg
        try:
            o = self._trade('ask', market, volume, reason, risk=True)
        except SessionLost:
            raise
        except Exception as error:
            self.record({'event': '수동 청산 실패', 'market': market, 'reason': type(error).__name__ + ': ' + str(error)[:250]})
            return False, f'청산 실패: {type(error).__name__}: {str(error)[:120]}'
        v, funds, fee = self._fill(o)
        if v <= 0:
            return False, f'매도 미체결 ({o.get("state")}): 보유는 그대로입니다'
        left = self.d['positions'].get(market, {}).get('qty', 0)
        msg = f'{market[4:]} 청산 {v:,.8g}개 → {funds - fee:,.0f}원' + (f' (일부 체결, 남은 수량 {left:,.8g})' if left > 0 else '')
        self.share(f'{msg} (수동 강제 청산, 실현 누적 {self.d["realized"]:+,.0f}원)')
        return True, msg

    def stagnation_sell(self, market, w, price):
        """정체 탈출(2026-11-06 사용자 승인): 매수 T시간 뒤 최고수익 1% 미만·본전 아래였던 종목이 본전 지정가 위로 되돌아오면 판다.
        거래소에 지정가를 걸지 않고 봇이 3초마다 감시해 닿는 순간 위험 청산 경로(_trade risk=True)로 시장가 매도한다.
        (걸어 둔 매도 주문이 잔고를 묶어 손절·순환 매도를 막는 일을 피하려는 선택.) 성공하면 True, 보류·실패면 False."""
        if not self.cfg.get('stagnation_exit_enabled') or not self.cfg['enabled'] or self.is_protected(market): return False
        pos = self.d['positions'].get(market)
        if not pos or not pos.get('qty', 0) > 0 or pos.get('time') != w.get('opened'): return False
        if type(price) not in (int, float) or not math.isfinite(price) or price <= 0: return False
        if self.market_has_uncertain_order(market): return False
        try:
            balances = {r['currency']: r for r in upbit.accounts()}
            volume = min(pos['qty'], float(balances.get(market[4:], {}).get('balance', 0)))
            if volume * price < 5000: return False
            reason = f"정체 탈출 {w.get('T')}시간"
            self.record({'event': '정체 탈출 매도 요청', 'market': market, 'limit': w.get('limit'), 'price': price, 'entry': w.get('entry')})
            o = self._trade('ask', market, volume, reason, risk=True)
            v, funds, fee = self._fill(o)
            if v <= 0: return False
            self.share(f'{market[4:]} {reason} 매도 {funds - fee:,.0f}원 (실현 누적 {self.d["realized"]:+,.0f}원)')
            return True
        except SessionLost:
            raise
        except Exception as error:
            self.record({'event': '정체 탈출 매도 실패', 'market': market, 'reason': type(error).__name__ + ': ' + str(error)[:250]})
            return False

    def sweep_dust(self, market, markets, krw=DUST_BUY_KRW):
        """팔 수 없는 자투리(평가 5,000원 미만)를 없앤다(사용자 지시 2026-11-06). krw만큼 사서 자투리와 합친 뒤 잔고 전부를 판다.
        비용은 수수료 2회와 호가 차이(종목당 수~수십 원). 손절과 같은 주문 경로(_trade)를 쓰고, 보호 종목·장부 밖 잔고·잠긴 수량은 하지 않는다."""
        if self.is_protected(market): return False, '사용자 보호 종목'
        if not self.cfg['enabled']: return False, '실거래가 꺼져 있음'
        if self.d['halted']: return False, '손실 중단 상태'
        if self.has_uncertain_orders(): return False, '미확정 주문 대사 중'
        if market not in markets: return False, '원화 마켓에 없음'
        if market not in self.d['positions']: return False, '봇 장부에 없는 잔고'
        def balance():
            r = {x['currency']: x for x in upbit.accounts()}.get(market[4:], {})
            return float(r.get('balance', 0)), float(r.get('locked', 0))
        have, locked = balance()
        px = upbit.tickers([market])[market]['trade_price']
        if locked > 0: return False, '잠긴 수량 있음(미체결 주문)'
        if have <= 0: return False, '잔고 없음'
        if have * px >= 5000: return False, f'자투리 아님(평가 {have * px:,.0f}원)'
        self.record({'event': '자투리 정리 요청', 'market': market, 'qty': have, 'krw': krw})
        v, f1, fee1 = self._fill(self._trade('bid', market, krw, DUST_REASON))
        if v <= 0: return False, '매수 미체결'
        have, _ = balance()
        v2, f2, fee2 = self._fill(self._trade('ask', market, have, DUST_REASON, risk=True))
        left, _ = balance()
        cost = (f1 + fee1) - (f2 - fee2)
        msg = f'{market[4:]} 자투리 정리: 비용 {cost:,.0f}원, 남은 수량 {left:.8f}'
        self.record({'event': '자투리 정리', 'market': market, 'cost': cost, 'left': left})
        return left == 0, msg

    def add_to_position(self, market, krw, markets, reason='수동 추가 매수'):
        """사용자가 화면에서 확인한 금액만큼 이미 관리 중인 종목을 시장가로 더 산다. (성공 여부, 안내문)을 돌려준다.

        자동 매수와 같은 검사(buy_block: 손실 중단·미확정 주문·한도·가용 원화·호가 차이)를 모두 통과해야 한다.
        기존 규칙상 추가 매수한 종목은 추적 손절·이익 보호 대상에서 빠진다(_apply_pending, 안건 27 미합의).
        """
        if self.is_protected(market): return False, '사용자 보호 종목: 해제 전에는 봇 화면 주문도 금지'
        if not self.cfg['enabled']: return False, '실거래가 꺼져 있음'
        if market not in self.d['positions']: return False, '봇이 관리하는 보유 종목이 아님: 새 종목 매수는 지원하지 않음'
        cap = min(self.cfg.get('manual_buy_max_krw', 100000), self.cfg['max_order_krw'])
        if type(krw) is not int or krw < 5000: return False, '매수 금액은 5,000원 이상의 정수여야 함'
        if krw > cap: return False, f'1회 추가 매수 상한 {cap:,}원 초과'
        why = self.buy_block(market, krw, markets)
        if why: return False, f'매수 보류: {why}'
        self.record({'event': '수동 추가 매수 요청', 'market': market, 'krw': krw})
        try:
            o = self._trade('bid', market, krw, reason)
        except SessionLost:
            raise
        except Exception as error:
            self.record({'event': '수동 추가 매수 실패', 'market': market, 'reason': type(error).__name__ + ': ' + str(error)[:250]})
            return False, f'매수 실패: {type(error).__name__}: {str(error)[:120]}'
        v, funds, fee = self._fill(o)
        if v <= 0:
            return False, f'매수 미체결 ({o.get("state")}): 보유는 그대로입니다'
        pos = self.d['positions'][market]
        msg = f'{market[4:]} 추가 매수 {funds + fee:,.0f}원 · 손절가 {pos["stop"]:,.4g} · 보유 평가 매수금액 {pos["cost"]:,.0f}원'
        self.share(f'{msg} (수동 추가 매수)')
        return True, msg

    def stop_check(self, prices):
        """Manage explicitly delegated positions, retaining partial fills and unresolved orders."""
        if not self.cfg['enabled']: return
        self.recover_all_pending()
        self.rebase_adopted_stops(prices)
        self.update_profit_guard(prices)
        if rotation.enabled(self.p):
            for m, pos in self.d['positions'].items():
                if not self.is_protected(m):
                    rotation.observe(pos, prices.get(m), time.time())
            # Optional observations persist with the next normal ledger save.
            # Do not add a disk write in front of urgent protective orders.
        candidates = [(m, pos) for m, pos in list(self.d['positions'].items())
                      if not self.is_protected(m) and m in prices and type(prices[m]) in (int, float) and math.isfinite(prices[m])
                      and prices[m] > 0 and prices[m] <= self.stop_limit(pos) and pos['qty'] > 0
                      and not self.market_has_uncertain_order(m)]
        if not candidates: return
        balances = {r['currency']: r for r in upbit.accounts()}
        for m, pos in candidates:
            available = float(balances.get(m[4:], {}).get('balance', 0))
            volume = min(pos['qty'], available)
            if volume * prices[m] < 5000: continue
            g = pos.get('profit_guard_stop') if self.cfg.get('profit_guard_enabled') else None
            why = '이익보호' if g is not None and self.stop_limit(pos) == g else '손절'
            try:
                o = self._trade('ask', m, volume, why, risk=True)
                v, funds, fee = self._fill(o)
                self.share(f'{m[4:]} {why} 매도 {funds - fee:,.0f}원 (실현 누적 {self.d["realized"]:+,.0f}원)')
            except SessionLost:
                raise
            except Exception as error:
                # One market's rejected/uncertain order must not suppress other owned-market exits.
                self.record({'event': '종목 청산 대기', 'market': m,
                             'reason': type(error).__name__ + ': ' + str(error)[:250]})
