"""Pure management-ledger replay. No I/O, API, lease mutation, adoption, or baseline reset."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math

PRESERVED_FIELDS = ('account_adoption', 'risk_baseline_unrealized', 'risk_base_equity',
                    'risk_loss_offset', 'risk_day', 'halted', 'limits', 'limits_id')
EPS = Decimal('0.00000001')


class ReconciliationError(ValueError):
    pass


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def preserved_basis(state):
    present = sorted(key for key in PRESERVED_FIELDS if key in state)
    return {**{key: deepcopy(state[key]) for key in present}, '__present_keys__': present}


def number(value):
    if isinstance(value, bool): raise ReconciliationError('Boolean numeric input')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ReconciliationError('Invalid decimal input') from None
    if not result.is_finite() or result < 0:
        raise ReconciliationError('Negative/nonfinite numeric input')
    return result


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None: raise ValueError
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        raise ReconciliationError('Missing or ambiguous fill timestamp') from None


def totals(value=None):
    value = value or {}
    return {key: number(value.get(key, 0)) for key in ('volume', 'funds', 'fee')}


def reconcile(base_state, detailed_orders, accounted_by_identifier, snapshot_epoch,
              current_quantities, protected_markets=(), verified_stop_floors=None,
              authorized_external_markets=()):
    """Return provisional candidate/report; caller separately validates ownership and coverage.

    accounted_by_identifier MUST be sums from the SAME source snapshot's local fill journal.
    Order details are terminal cumulative fills. New inventory is never invented from balances.
    Unknown transient protection is allowed only if that new position closes in this replay.
    """
    if type(snapshot_epoch) not in (int, float) or not math.isfinite(snapshot_epoch):
        raise ReconciliationError('Invalid source snapshot epoch')
    if base_state.get('pending_order') or base_state.get('risk_pending_orders'):
        raise ReconciliationError('Source has unresolved orders; reconcile those explicitly first')
    state = deepcopy(base_state)
    basis = preserved_basis(base_state)
    protected = set(protected_markets)
    authorized_external = set(authorized_external_markets)
    if protected & authorized_external:
        raise ReconciliationError('User-protected market cannot be externally adopted')
    stop_floors = verified_stop_floors or {}
    prior = state.get('reconciliation_replay', {})
    pointers = deepcopy(accounted_by_identifier)
    pointers.update(deepcopy(prior.get('accounted_totals', {})))
    digests = deepcopy(prior.get('order_digests', {}))
    jobs, seen, events = [], {}, []
    unresolved = []

    for order in detailed_orders:
        identifier = order.get('identifier') or ''
        market, side = order.get('market'), order.get('side')
        trades = order.get('trades')
        if not isinstance(trades, list): raise ReconciliationError('Missing detailed fills')
        if order.get('state') not in ('done', 'cancel'):
            raise ReconciliationError('Nonterminal order in recovery evidence')
        if order.get('complete') is False:
            raise ReconciliationError('Incomplete detailed order evidence')
        fills = []
        for trade in trades:
            if trade.get('market', market) != market or trade.get('side', side) != side:
                raise ReconciliationError('Fill identity mismatch')
            volume, funds = number(trade.get('volume')), number(trade.get('funds'))
            if volume <= 0 or funds <= 0: raise ReconciliationError('Nonpositive fill')
            fills.append((timestamp(trade.get('created_at')), volume, funds))
        fills.sort(key=lambda row: row[0])
        total = {'volume': sum((v for _, v, _ in fills), Decimal(0)),
                 'funds': sum((f for _, _, f in fills), Decimal(0)), 'fee': number(order.get('paid_fee', 0))}
        if abs(number(order.get('executed_volume', 0)) - total['volume']) > EPS:
            raise ReconciliationError('Cumulative and detailed fill quantities differ')
        if not fills:
            if total['fee'] > 0: raise ReconciliationError('Fee without fills')
            continue
        external = not identifier.startswith('v5-')
        if external and market not in authorized_external:
            if fills[-1][0] > snapshot_epoch and market not in protected:
                unresolved.append({'market': market, 'reason': 'unattributed post-snapshot fills'})
            continue
        if external:
            if not isinstance(order.get('uuid'), str) or not order['uuid']:
                raise ReconciliationError('Authorized external fill has no stable exchange identity')
            identifier = 'external:' + hashlib.sha256(order['uuid'].encode()).hexdigest()
        if not isinstance(market, str) or not market.startswith('KRW-') or side not in ('bid', 'ask'):
            raise ReconciliationError('Invalid bot order identity')
        payload = {'market': market, 'side': side, 'state': order['state'],
                   'fills': [(t, str(v), str(f)) for t, v, f in fills], 'fee': str(total['fee'])}
        digest = canonical_sha256(payload)
        if identifier in seen:
            if seen[identifier] != digest: raise ReconciliationError('Conflicting duplicate order evidence')
            continue
        seen[identifier] = digest
        if identifier in digests and digests[identifier] != digest:
            raise ReconciliationError('Previously replayed order revised; rebuild from original snapshot')
        applied = totals(pointers.get(identifier))
        delta = {key: total[key] - applied[key] for key in total}
        if any(value < -EPS for value in delta.values()):
            raise ReconciliationError('Evidence regresses source accounted totals')
        if all(abs(value) <= EPS for value in delta.values()):
            continue
        if delta['volume'] <= 0 or delta['funds'] <= 0:
            raise ReconciliationError('Fee-only or inconsistent adjustment needs explicit original-path replay')
        # Validate that the source accounted exactly a prefix, not an arbitrary proportional guess.
        cut, q, f = 0, Decimal(0), Decimal(0)
        if applied['volume'] > EPS or applied['funds'] > EPS:
            for i, (_, volume, funds) in enumerate(fills, 1):
                q += volume; f += funds
                if abs(q - applied['volume']) <= EPS and abs(f - applied['funds']) <= EPS:
                    cut = i; break
            if not cut: raise ReconciliationError('Source accounted fills are not a proven order prefix')
        remaining = fills[cut:]
        if not remaining or remaining[0][0] <= snapshot_epoch:
            raise ReconciliationError('Unaccounted fills do not follow source snapshot')
        if market in protected:
            unresolved.append({'market': market, 'reason': 'bot fills on user-protected market require separate review'})
            continue
        jobs.append({'identifier': identifier, 'market': market, 'side': side, 'delta': delta,
                     'start': remaining[0][0], 'end': remaining[-1][0], 'total': total,
                     'digest': digest, 'external': external})

    # Aggregating each order is valid only where same-market order intervals do not overlap.
    # The broker's serial order policy normally ensures this; recovery must prove, not assume it.
    by_market = {}
    for job in sorted(jobs, key=lambda j: (j['start'], j['end'])):
        previous = by_market.get(job['market'])
        if previous and job['start'] <= previous['end']:
            raise ReconciliationError('Overlapping same-market orders need fill-event replay')
        by_market[job['market']] = job

    realized = Decimal(str(state.get('realized', 0)))
    bought = number(state.get('bought_krw', 0))
    if not realized.is_finite(): raise ReconciliationError('Invalid source realized PnL')
    realized_before, bought_before = realized, bought
    external_cost = number(state.get('external_acquisition_cost', 0))
    external_before = external_cost
    for job in sorted(jobs, key=lambda j: (j['start'], j['end'])):
        market, side = job['market'], job['side']
        volume, funds, fee = (job['delta'][key] for key in ('volume', 'funds', 'fee'))
        position = state['positions'].get(market)
        if side == 'bid':
            if position:
                position['qty'] = float(number(position['qty']) + volume)
                position['cost'] = float(number(position['cost']) + funds + fee)
                # Keep all known stronger protective fields; do not invent unseen company peaks.
                position['protection_exact_after_replay'] = False
            else:
                position = {'qty': float(volume), 'cost': float(funds + fee),
                            'time': datetime.fromtimestamp(job['start'], timezone(timedelta(hours=9))).isoformat(),
                            'entry': float(funds / volume), 'protection_exact_after_replay': False}
                if market in stop_floors:
                    floor = number(stop_floors[market])
                    if floor <= 0: raise ReconciliationError('Invalid verified stop floor')
                    position['stop'] = float(floor)
                    position['stop_source'] = 'explicit_verified_recovery_floor'
                if job['external']:
                    position['acquisition_source'] = 'explicitly_authorized_external_exchange_fill'
                state['positions'][market] = position
            if job['external']:
                external_cost += funds + fee
            else:
                bought += funds + fee
        else:
            if not position: raise ReconciliationError('Sale lacks source/replayed inventory')
            owned, cost = number(position['qty']), number(position['cost'])
            if volume > owned + EPS: raise ReconciliationError('Sale exceeds owned quantity')
            fraction = min(Decimal(1), volume / owned)
            basis_cost = cost * fraction
            pnl = funds - fee - basis_cost
            realized += pnl
            remaining = max(Decimal(0), owned - volume)
            if remaining <= EPS:
                last = state.setdefault('last_sell', {}).get(market)
                if not last or job['end'] > last.get('time', 0):
                    state['last_sell'][market] = {'time': job['end'], 'price': float(funds / volume),
                        'profit': pnl > 0, 'reason': 'verified_exchange_reconciliation',
                        'time_source': 'actual_last_fill_not_recovery_wall_clock'}
                del state['positions'][market]
            else:
                position['qty'], position['cost'] = float(remaining), float(cost - basis_cost)
        pointers[job['identifier']] = {key: str(value) for key, value in job['total'].items()}
        digests[job['identifier']] = job['digest']
        events.append({'market': market, 'side': side, 'volume': str(volume),
                       'funds': str(funds), 'fee': str(fee), 'actual_fill_epoch': job['end'],
                       'source': 'authorized_external' if job['external'] else 'bot-v5'})
    if jobs:
        state['realized'], state['bought_krw'] = float(realized), float(bought)
        if external_cost != external_before:
            state['external_acquisition_cost'] = float(external_cost)
        state['reconciliation_replay'] = {'source_snapshot_sha256': prior.get('source_snapshot_sha256', canonical_sha256(base_state)),
            'snapshot_epoch': prior.get('snapshot_epoch', snapshot_epoch),
            'accounted_totals': pointers, 'order_digests': digests,
            'authorized_external_markets': sorted(authorized_external),
            'company_internal_protection_history_restored': False}
    for market, position in state['positions'].items():
        if market in protected: continue
        floor = stop_floors.get(market)
        if floor is not None:
            floor = number(floor)
            if floor <= 0: raise ReconciliationError('Invalid verified stop floor')
            old = number(position.get('stop', 0))
            position['stop'] = float(max(old, floor))
        stop = position.get('stop')
        if type(stop) not in (int, float) or not math.isfinite(stop) or stop <= 0:
            unresolved.append({'market': market, 'reason': 'open position has no verified positive stop floor'})
    mismatches = []
    for market, position in state['positions'].items():
        if market in protected: continue
        observed = number(current_quantities.get(market, 0))
        if abs(number(position['qty']) - observed) > EPS:
            mismatches.append({'market': market, 'reason': 'managed quantity differs from current account'})
    unmanaged = {'KRW-' + row['currency'] for row in state.get('unmanaged_assets', [])}
    for market, quantity in current_quantities.items():
        if number(quantity) > EPS and market not in protected | unmanaged | set(state['positions']):
            mismatches.append({'market': market, 'reason': 'current unprotected inventory absent from replay'})
    if preserved_basis(state) != basis:
        raise ReconciliationError('Historical risk/baseline fields changed')
    report = {'accounting_replay_complete': not unresolved and not mismatches,
              'new_order_count': len(jobs), 'new_fill_management_events': events,
              'new_bot_order_count': sum(not j['external'] for j in jobs),
              'new_external_order_count': sum(j['external'] for j in jobs),
              'realized_delta': float(realized - realized_before), 'bought_delta': float(bought - bought_before),
              'external_acquisition_cost': float(external_cost - external_before),
              'managed_market_count': len(state['positions']), 'quantity_mismatches': mismatches,
              'unresolved': unresolved, 'preserved_basis_sha256': canonical_sha256(basis),
              'target_ledger_sha256': canonical_sha256(state),
              'company_internal_peak_trailing_history_restored': False,
              'orders_submitted': 0, 'lease_modified': False}
    return state, report
