"""Research-only audit reproducing the engine's sub-1e-8 coin close rule.

AutoTrader.match removes a position when its remaining volume is <1e-8. Its
original validate method retains this dust when replaying fills, which can
leave a small cost-basis mismatch on later re-entry. This module audits the
actual engine rule without changing cash, fills, production code, or state.
Passing this audit must not be reported as passing the original validator.
"""
from collections import defaultdict
import math

from auto_trader import AutoTrader, fresh_state
from investment_profiles import PROFILES
from scalp_universe import valid_code
from scalping_strategy import RULES, VERSION

CLOSE_VOLUME = 1e-8  # Literal production match() threshold, strictly '<'.


def validate_state(state):
    """Raise on discrepancies; return cash and explicitly discarded dust audit.

    Volume totals across different coins have no economic interpretation; dust
    is consequently reported per market. ``cost`` is its aggregate KRW basis.
    This function reads and never mutates the supplied state.
    """
    s = state
    if (not isinstance(s, dict) or set(fresh_state()) - set(s)
            or s['version'] not in (VERSION, 'scalp-v3.0', 'scalp-v3.1') or s['mode'] != 'paper'):
        raise ValueError('Research audit: invalid account state schema')
    if (s['universe_mode'] not in ('all', 'selected') or not isinstance(s['markets'], list)
            or any(not valid_code(m) for m in s['markets']) or len(set(s['markets'])) != len(s['markets'])):
        raise ValueError('Research audit: invalid market universe')

    def finite(value):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('Research audit: invalid nonnegative numeric field')

    for key in ('cash', 'funded', 'day_start', 'peak', 'pause_until'):
        finite(s[key])
    for key in ('review', 'day_stop', 'day_liquidate'):
        if type(s[key]) is not bool:
            raise ValueError('Research audit: invalid risk latch')
    for market, position in s['positions'].items():
        if not valid_code(market) or position.get('bucket', 'short') not in PROFILES:
            raise ValueError('Research audit: invalid position profile/market')
        for key in ('entry', 'volume', 'cost', 'stop', 'r', 'atr', 'since', 'peak', 'trough'):
            finite(position[key])
        if min(position['entry'], position['volume'], position['r'], position['atr']) <= 0:
            raise ValueError('Research audit: invalid position quantity/protection')
        if position.get('strategy', 'pullback') not in ('pullback', 'breakout'):
            raise ValueError('Research audit: invalid position strategy')
        if position.get('strategy') == 'breakout':
            finite(position['breakout_level'])

    orders = {}
    for order in s['orders']:
        if (order['id'] in orders or not valid_code(order['market'])
                or order['side'] not in ('bid', 'ask') or order['status'] not in ('wait', 'done', 'cancel')
                or order['kind'] not in ('entry', 'partial', 'exit')):
            raise ValueError('Research audit: invalid order identity/type')
        orders[order['id']] = order
        for key in ('price', 'volume', 'filled', 'funds', 'fee', 'created'):
            finite(order[key])
        if not 0 <= order['filled'] <= order['volume'] + 1e-10:
            raise ValueError('Research audit: order fill exceeds volume')
        if min(order['price'], order['volume']) <= 0 or type(order['ioc']) is not bool:
            raise ValueError('Research audit: invalid order terms')
        if order['kind'] == 'entry':
            for key in ('atr', 'low', 'cost_rate', 'expires', 'breakout', 'ceiling'):
                finite(order[key])
            if not isinstance(order['signal'], str):
                raise ValueError('Research audit: invalid signal identifier')
            if order.get('strategy') == 'breakout':
                finite(order['stop_price'])
                if not order['ioc'] or not 0 < order['stop_price'] < order['price']:
                    raise ValueError('Research audit: invalid breakout protection')

    order_totals = {key: [0., 0., 0.] for key in orders}
    positions = {}
    dust_volume, dust_cost = defaultdict(float), defaultdict(float)
    dust_events, cash_flows = [], []
    previous_time = -math.inf
    for fill in s['fills']:
        if (fill['order'] not in orders or fill['side'] != orders[fill['order']]['side']
                or fill['market'] != orders[fill['order']]['market']):
            raise ValueError('Research audit: invalid fill identity')
        for key in ('time', 'volume', 'funds', 'fee', 'price'):
            finite(fill[key])
        if fill['time'] < previous_time:
            raise ValueError('Research audit: fills out of chronological order')
        previous_time = fill['time']
        if fill['volume'] <= 0 or fill['price'] <= 0:
            raise ValueError('Research audit: zero fill volume/price')
        if abs(fill['funds'] - fill['volume'] * fill['price']) > max(1e-7, abs(fill['funds']) * 1e-10):
            raise ValueError('Research audit: fill notional is inconsistent')
        if abs(fill['fee'] - fill['funds'] * RULES['fee']) > max(1e-8, abs(fill['fee']) * 1e-10):
            raise ValueError('Research audit: fill fee is inconsistent')
        for index, key in enumerate(('volume', 'funds', 'fee')):
            order_totals[fill['order']][index] += fill[key]
        market = fill['market']
        volume, cost = positions.get(market, (0., 0.))
        if fill['side'] == 'bid':
            positions[market] = volume + fill['volume'], cost + fill['funds'] + fill['fee']
            cash_flows.append(-fill['funds'] - fill['fee'])
        else:
            # Numerical tolerance only; do not treat an actual 1e-8 oversell as dust.
            if fill['volume'] > volume + max(1e-12, math.ulp(volume) * 8):
                raise ValueError(f'Research audit: oversell for {market}')
            remaining = max(0., volume - fill['volume'])
            remaining_cost = cost - cost * fill['volume'] / volume if volume else 0.
            if remaining < CLOSE_VOLUME:
                if remaining > 0:
                    discarded_cost = max(0., remaining_cost)
                    dust_volume[market] += remaining
                    dust_cost[market] += discarded_cost
                    dust_events.append(dict(market=market, time=fill['time'], volume=remaining,
                                            cost_krw=discarded_cost, order=fill['order']))
                positions[market] = 0., 0.
            else:
                positions[market] = remaining, remaining_cost
            cash_flows.append(fill['funds'] - fill['fee'])

    for key, order in orders.items():
        for index, field in enumerate(('filled', 'funds', 'fee')):
            actual, expected = order[field], order_totals[key][index]
            if abs(actual - expected) > max(1e-7, abs(expected) * 1e-9):
                raise ValueError(f'Research audit: order/fill ledger mismatch: {key} {field}')

    max_volume_delta = max_cost_delta = 0.
    for market in set(positions) | set(s['positions']):
        volume, cost = positions.get(market, (0., 0.))
        position = s['positions'].get(market, {'volume': 0., 'cost': 0.})
        volume_delta = abs(volume - position['volume'])
        cost_delta = abs(cost - position['cost'])
        max_volume_delta = max(max_volume_delta, volume_delta)
        max_cost_delta = max(max_cost_delta, cost_delta)
        # Far below one coin subunit, and below the original one-cent tolerance.
        if volume_delta > max(1e-12, math.ulp(volume) * 16) or cost_delta > max(1e-6, abs(cost) * 1e-11):
            raise ValueError(f'Research audit: position ledger mismatch: {market}: {volume_delta} coins/{cost_delta} KRW')

    expected_cash = math.fsum([s['funded'], *cash_flows])
    cash_delta = s['cash'] - expected_cash
    if abs(cash_delta) > max(1e-6, abs(expected_cash) * 1e-12):
        raise ValueError(f'Research audit: cash ledger mismatch: {cash_delta} KRW')
    total_dust_cost = math.fsum(dust_cost.values())
    probe = AutoTrader.__new__(AutoTrader)
    probe.state = s
    original_passed, original_error = True, None
    try:
        AutoTrader.validate(probe)
    except ValueError as error:
        original_passed, original_error = False, str(error)
    return dict(audit='production_close_threshold_replay', passed=True,
                original_validator_passed=original_passed, original_validator_error=original_error,
                dust_threshold_coin=CLOSE_VOLUME, dust_event_count=len(dust_events),
                dust_removed_volume=dict(dust_volume), dust_removed_cost=total_dust_cost,
                dust_removed_cost_krw=total_dust_cost, cost=total_dust_cost,
                removed_dust_cost_krw=total_dust_cost,
                dust_cost_by_market_krw=dict(dust_cost), dust_events=dust_events,
                expected_cash=expected_cash, cash_delta=cash_delta,
                max_position_volume_delta=max_volume_delta, max_position_cost_delta=max_cost_delta,
                maxpositiondelta=dict(volume_coin=max_volume_delta, cost_krw=max_cost_delta),
                note='Research audit only; cash and state were not corrected. Original validator result must be reported separately.')
