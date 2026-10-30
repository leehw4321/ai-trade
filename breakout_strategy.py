"""Experimental paper-only breakout route; bounded live trade aggregates."""
import math
import statistics
from collections import deque
from adaptive_model import indicators, ema
from scalping_strategy import validate_bars, RULES, walk_book

BREAKOUT = {'volume_multiple': 2., 'buy_ratio': .60, 'hold_seconds': 5.,
            'trade_age': 2., 'warm_seconds': 60., 'max_gap': 5.,
            'max_chase_atr': .5, 'max_distance': .03, 'max_spread': .0035,
            'max_spread_ticks': 2, 'failure_seconds': 60., 'min_turnover': 5_000_000}


def prepare_breakout(one, now):
    validate_bars(one, 60, 60, now)
    f = indicators(one)
    last = one[-30:]
    values = [b.get('value', (b['h']+b['l']+b['c'])/3*b['v']) for b in last]
    if not all(math.isfinite(v) and v >= 0 for v in values):
        raise ValueError('돌파 거래대금 형식 오류')
    volume = sum(b['v'] for b in last)
    baseline = statistics.median(values[-20:])
    if volume <= 0 or baseline <= 0 or f['atr'] <= 0:
        raise ValueError('돌파 기준 거래량·ATR 부족')
    level = max(b['h'] for b in one[-20:])
    return {'closed_at': one[-1]['t']+60, 'level': level, 'atr': f['atr'],
            'ema20': f['ema20'], 'rising': f['ema20'] > ema([b['c'] for b in one[:-1]], 20),
            'vwap': sum(values)/volume, 'baseline': baseline,
            'stop_price': max(level-.5*f['atr'], min(b['l'] for b in one[-5:])-.2*f['atr'])}


class TradeWindow:
    """At most 61 second buckets, with bounded ID deduplication per symbol."""
    def __init__(self):
        self.seconds = deque(maxlen=61)
        self.ids = deque(maxlen=4096)
        self.seen = set()
        self.first = None
        self.last = 0.
        self.price = 0.
        self.above_since = None
        self.above_level = None
        self.last_sequence = None

    def ingest(self, data, now):
        try:
            stamp = float(data['trade_timestamp'])/1000
            price, volume = float(data['trade_price']), float(data['trade_volume'])
            sequence = data['sequential_id']
            if (type(sequence) is not int or data.get('stream_type') != 'REALTIME'
                    or data.get('ask_bid') not in ('BID', 'ASK')
                    or not all(math.isfinite(v) for v in (stamp, price, volume, price*volume))
                    or min(price, volume) <= 0 or not 0 <= now-stamp <= BREAKOUT['trade_age']
                    or stamp < self.last or sequence in self.seen):
                return False
        except (ValueError, TypeError, KeyError, OverflowError):
            return False
        if self.last and stamp-self.last > BREAKOUT['max_gap']:
            self.seconds.clear()
            self.first = None
            self.above_since = None
        if self.first is None:
            self.first = stamp
        self.last, self.price = stamp, price
        if len(self.ids) == self.ids.maxlen:
            self.seen.discard(self.ids[0])
        self.ids.append(sequence)
        self.seen.add(sequence)
        second = int(stamp)
        if not self.seconds or self.seconds[-1]['second'] != second:
            self.seconds.append({'second': second, 'value': 0., 'buy': 0.})
        self.seconds[-1]['value'] += price*volume
        if data['ask_bid'] == 'BID':
            self.seconds[-1]['buy'] += price*volume
        # A below-level print resets confirmation even between monitor steps.
        if self.above_level is not None and price <= self.above_level:
            self.above_since = None
        return True

    def signal(self, context, now):
        out = {'ok': False, 'strategy': 'breakout', 'time': now, 'checks': {}}
        if not context or not 0 <= now-context['closed_at'] < 75:
            out['reason'] = '돌파 기준 봉 준비/지연'
            return out
        level = context['level']
        if self.above_level != level:
            self.above_level, self.above_since = level, None
        fresh = 0 <= now-self.last <= BREAKOUT['trade_age']
        if not fresh or self.price <= level:
            self.above_since = None
        elif self.above_since is None:
            self.above_since = now
        # Exclude the partially overlapping oldest second: never overcount 60s.
        recent = [b for b in self.seconds if b['second'] >= math.ceil(now-60)]
        flow = [b for b in recent if b['second'] >= math.ceil(now-10)]
        notional = sum(b['value'] for b in recent)
        flow_value = sum(b['value'] for b in flow)
        buy_ratio = sum(b['buy'] for b in flow)/flow_value if flow_value else 0.
        multiple = notional/context['baseline']
        held = 0 if self.above_since is None else now-self.above_since
        checks = {'실시간 체결 60초 준비': self.first is not None and now-self.first >= 60 and fresh,
                  '최근 60초 거래대금 2배': multiple >= BREAKOUT['volume_multiple'] and notional >= BREAKOUT['min_turnover'],
                  '직전 20분 고점 돌파·5초 유지': self.price > level and held >= BREAKOUT['hold_seconds'],
                  '최근 10초 매수 체결대금 60%': buy_ratio >= BREAKOUT['buy_ratio'],
                  '1분 EMA20 상승·30분 VWAP 위': context['rising'] and self.price > context['vwap'] and self.price > context['ema20'],
                  '돌파선 0.5 ATR 이내': 0 < self.price-level <= BREAKOUT['max_chase_atr']*context['atr']}
        # One immutable breakout identity per completed baseline bar and level.
        out.update(checks=checks, ok=all(checks.values()), id=f"breakout:{int(context['closed_at'])}:{level}",
                   closed_at=now, baseline_closed_at=context['closed_at'], close=self.price,
                   atr=context['atr'], ema20=context['ema20'], vwap=context['vwap'],
                   breakout=level, low=context['stop_price'], stop_price=context['stop_price'],
                   volume_ratio=multiple, buy_ratio=buy_ratio, turnover_60s=notional,
                   held_seconds=held, trade_time=self.last, vwap_method='완료 1분봉 최근 30분 거래대금/수량')
        out['reason'] = '돌파 초기 진입 조건 충족' if out['ok'] else ' / '.join(k for k, v in checks.items() if not v)
        return out


def breakout_plan(signal, quote, equity, cash, exposure, open_risk, now, tick_size, rules=None):
    RULES = rules or globals()['RULES']
    if (not signal.get('ok') or not 0 <= now-signal['time'] <= 2
            or not 0 <= now-signal['trade_time'] <= 2 or not 0 <= now-quote['time'] <= 2):
        raise ValueError('돌파 신호/체결/호가 지연')
    top = quote['units'][0]
    ask, bid = top['ask_price'], top['bid_price']
    spread = (ask-bid)/((ask+bid)/2)
    if (tick_size <= 0 or spread < 0 or spread > BREAKOUT['max_spread']
            or ask-bid > tick_size*BREAKOUT['max_spread_ticks']+1e-9):
        raise ValueError('돌파 호가 간격: 2틱 또는 0.35% 초과')
    ceiling = signal['breakout']+BREAKOUT['max_chase_atr']*signal['atr']
    # Reserve adverse simulated execution; the actual IOC limit is rounded down.
    price = ask*(1+RULES['impact'])
    if bid <= signal['breakout'] or price > ceiling:
        raise ValueError('돌파선 이탈/추격 가격 초과')
    stop = signal['stop_price']
    distance = (price-stop)/price
    if not RULES['min_distance'] <= distance <= BREAKOUT['max_distance']:
        raise ValueError('돌파 구조 손절 거리 0.4~3% 이탈')
    cost = 2*RULES['fee']+spread+2*RULES['impact']
    risk_cash = max(0, min(equity*RULES['risk'], equity*RULES['max_open_risk']-open_risk))*.99
    amount = max(0, min(risk_cash/(distance+cost), equity*RULES['max_position'],
                        equity*RULES['max_total']-exposure, cash/(1+RULES['fee'])))
    if amount < RULES['minimum']:
        raise ValueError('돌파 위험 예산/운용자금 부족')
    volume = amount/price
    buy, sell = walk_book(quote['units'], 'ask', volume), walk_book(quote['units'], 'bid', volume)
    cost = 2*RULES['fee']+(buy-sell)/buy+2*RULES['impact']
    if cost > distance*.25:
        raise ValueError('돌파 예상 왕복 비용이 손절 거리의 25% 초과')
    amount = min(amount, risk_cash/(distance+cost))
    for side, limit in (('ask', min(ceiling, ask*1.001)), ('bid', bid*.999)):
        depth = sum(u[side+'_price']*u[side+'_size'] for u in quote['units']
                    if (u[side+'_price'] <= limit if side == 'ask' else u[side+'_price'] >= limit))
        if depth < amount*5:
            raise ValueError('돌파 진입/청산 5배 호가 잔량 부족')
    return {'amount': amount, 'price': price, 'stop': stop, 'stop_price': stop,
            'atr': signal['atr'], 'low': signal['low'], 'cost_rate': cost,
            'signal': signal['id'], 'expires': now+2, 'breakout': signal['breakout'],
            'ceiling': ceiling, 'strategy': 'breakout', 'ioc': True}
