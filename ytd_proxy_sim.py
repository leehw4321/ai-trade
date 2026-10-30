"""Offline, conditional OHLC execution proxy for current v4 pullback signals.

This is deliberately NOT a reconstruction of historic live trading or a return
forecast. It uses the actual AutoTrader account/exit/risk engine, but missing
historic order books and tick ordering are replaced by documented assumptions.
No network, credentials, production state, or production code is modified.
"""
import argparse
from collections import Counter, defaultdict
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import time

from adaptive_model import indicators
from allocation_settings import DEFAULTS, validate as validate_allocation
from auto_trader import AutoTrader
from holding_settings import HoldingSettings
from scalping_strategy import RULES, validate_bars

KST = dt.timezone(dt.timedelta(hours=9))
FIXED_MARKETS = ['KRW-' + x for x in ('BTC', 'ETH', 'XRP', 'SOL', 'DOGE', 'SUI', 'ADA', 'HBAR', 'LINK', 'AVAX')]


def iso(stamp):
    return dt.datetime.fromtimestamp(stamp, KST).isoformat()


def epoch(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.replace(tzinfo=KST).timestamp() if parsed.tzinfo is None else parsed.timestamp()


class NoSaveTrader(AutoTrader):
    def save(self):
        self.dirty = False
        self.last_save = time.monotonic()

    def validate(self):
        try:
            super().validate()
            self.ledger_audit = None
        except ValueError as error:
            # Do not change cash or silently waive the production discrepancy.
            # Replay exactly the production close threshold in an independent audit.
            from ytd_ledger_audit import validate_state
            self.ledger_audit = validate_state(self.state)
            self.ledger_audit.update(original_validator_passed=False, original_error=str(error))


def book(mid, spread):
    # Deep synthetic book. Historical depth/queue priority are UNKNOWN.
    return [dict(bid_price=mid * (1 - spread / 2), ask_price=mid * (1 + spread / 2),
                 bid_size=1e12, ask_size=1e12)]


class CandleStore:
    def __init__(self, path):
        self.db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
        self.context_cache = {}

    def close(self):
        self.db.close()

    def minute(self, market, stamp):
        row = self.db.execute('SELECT t,open,high,low,close,value,volume FROM candles WHERE market=? AND t=?',
                              (market, stamp)).fetchone()
        return None if row is None else dict(zip(('t', 'o', 'h', 'l', 'c', 'value', 'v'), row))

    def context(self, market, now):
        """Only candles CLOSED at now. Never use current minute H/L/C here."""
        key = market, now
        if key in self.context_cache:
            return self.context_cache[key]
        rows = self.db.execute('SELECT t,open,high,low,close,value,volume FROM candles '
                               'WHERE market=? AND t<? ORDER BY t DESC LIMIT 4000', (market, now)).fetchall()
        one = [dict(zip(('t', 'o', 'h', 'l', 'c', 'value', 'v'), row)) for row in reversed(rows)]
        frames = {1: one[-200:]}
        for unit, count in ((5, 300), (15, 200)):
            sec, grouped = unit * 60, []
            for bar in one:
                stamp = math.floor(bar['t'] / sec) * sec
                if stamp + sec > now:
                    continue
                if not grouped or grouped[-1]['t'] != stamp:
                    grouped.append(dict(t=stamp, h=bar['h'], l=bar['l'], c=bar['c'], v=bar['v']))
                else:
                    current = grouped[-1]
                    current.update(h=max(current['h'], bar['h']), l=min(current['l'], bar['l']),
                                   c=bar['c'], v=current['v'] + bar['v'])
            frames[unit] = grouped[-count:]
        try:
            for unit, minimum in ((15, 200), (5, 200), (1, 60)):
                validate_bars(frames[unit], unit * 60, minimum, now)
            # evaluate_intraday sets these fields after all validation and before
            # VWAP/entry checks; retain previous context on invalid input, as live.
            five, one_f = indicators(frames[5]), indicators(frames[1])
            result = dict(atr=five['atr'], close=one_f['close'], ema20=one_f['ema20'],
                          closed_at=frames[1][-1]['t'] + 60, bucket='short', unit_seconds=60)
        except (ValueError, KeyError, ZeroDivisionError, OverflowError):
            result = None
        # Cache across four scenarios; only visited active minutes are stored.
        self.context_cache[key] = result
        return result


def read_signals(paths, start, end, markets):
    rows = []
    for path in paths:
        with Path(path).open(encoding='utf-8-sig') as stream:
            for line in stream:
                if not line.strip():
                    continue
                row = json.loads(line)
                signal = row['signal']
                stamp = float(row.get('time', row.get('closed_at', signal['closed_at'])))
                if row['market'] not in markets or not start <= stamp < end:
                    continue
                if stamp % 60 or signal['closed_at'] > stamp or signal.get('time', stamp) > stamp:
                    raise ValueError('Signal is unaligned or uses information from the future')
                if not signal.get('ok') or signal.get('strategy') == 'breakout':
                    raise ValueError('Input must contain only valid pullback signals')
                rows.append(dict(market=row['market'], time=int(stamp), signal=signal))
    rows.sort(key=lambda r: (r['time'], r['market']))
    unique = {(r['market'], r['time'], r['signal']['id']) for r in rows}
    if len(unique) != len(rows):
        raise ValueError('Duplicate signal rows')
    return rows


def source_hashes():
    root = Path(__file__).parent
    files = ['ytd_proxy_sim.py', 'auto_trader.py', 'scalping_strategy.py', 'research_v4.py',
             'investment_profiles.py', 'allocation_settings.py', 'holding_settings.py',
             'adaptive_model.py', 'portfolio.py']
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}


def run_scenario(store, signals, start, end, markets, spread, path_order, budget, allocation, holding=None):
    by_time = defaultdict(list)
    for row in signals:
        by_time[row['time']].append(row)
    signal_times = sorted(by_time)
    rejected, accepted, signal_outcomes = Counter(), Counter(), []
    ticks, trace = 0, []
    with tempfile.TemporaryDirectory(prefix='v4_ytd_offline_') as directory:
        a = NoSaveTrader(Path(directory), public=lambda *args: (_ for _ in ()).throw(RuntimeError('offline only')))
        a.allocation.value = allocation.copy()
        if holding is not None:
            h = HoldingSettings(Path(directory))
            h.value = HoldingSettings.validate(holding)
            a.profiles = h.profiles
        a.state.update(funded=budget, cash=budget, markets=markets, universe_mode='selected',
                       peak=budget, day_start=budget)
        a.running = True
        high, max_dd, last_value = budget, 0., budget
        monthly_last, monthly_first = {}, {}
        review_first = None

        def sample(stamp):
            nonlocal high, max_dd, last_value, review_first
            value = a.equity()
            high = max(high, value)
            max_dd = max(max_dd, 1 - value / high)
            last_value = value
            month = iso(stamp)[:7]
            monthly_first.setdefault(month, trace[-1]['equity'] if trace else budget)
            monthly_last[month] = value
            trace.append(dict(time=stamp, equity=value, cash=a.state['cash'], positions=len(a.state['positions'])))
            if a.state['review'] and review_first is None:
                review_first = stamp

        def feed(prices, stamp):
            for market, price in prices.items():
                a.ingest(market, book(price, spread), stamp, 'synthetic_ohlc', stamp)
            a.step(stamp)

        sample(start)
        now = start
        signal_index = 0
        while now < end:
            while signal_index < len(signal_times) and signal_times[signal_index] < now:
                signal_index += 1
            if not a.state['positions'] and not a.active():
                if signal_index >= len(signal_times):
                    break
                now = signal_times[signal_index]
                if now >= end:
                    break
            current = by_time.get(now, [])
            a.signals = {row['market']: row['signal'] for row in current}
            relevant = set(a.protected_markets()) | set(a.signals)
            bars = {market: store.minute(market, now) for market in relevant}
            for market in relevant:
                context = store.context(market, now)
                if context:
                    a.contexts[market] = context
            # Signal context is exact and already causal at this minute boundary.
            for row in current:
                sig = row['signal']
                a.contexts[row['market']] = {key: sig[key] for key in ('atr', 'close', 'ema20', 'closed_at')} | {
                    'bucket': 'short', 'unit_seconds': 60}
            before_orders = len(a.state['orders'])
            feed({m: b['o'] for m, b in bars.items() if b}, now)
            sample(now)
            created = a.state['orders'][before_orders:]
            for row in current:
                market = row['market']
                orders = [o for o in created if o['market'] == market and o['side'] == 'bid']
                if orders:
                    outcome = 'entry_order_reserved'
                    accepted[outcome] += 1
                elif not bars.get(market):
                    outcome = 'no_next_traded_minute_candle'
                    rejected[outcome] += 1
                elif a.entry_blocks.get(market, {}).get('time') == now:
                    outcome = a.entry_blocks[market]['reason']
                    rejected[outcome] += 1
                elif a.state['review']:
                    outcome = 'permanent_5pct_peak_review_lock'
                    rejected[outcome] += 1
                elif a.state['day_stop']:
                    outcome = 'daily_1_5pct_halt'
                    rejected[outcome] += 1
                elif now < a.state['pause_until']:
                    outcome = 'three_consecutive_loss_pause'
                    rejected[outcome] += 1
                elif market in a.state['positions'] or a.active(market, 'bid'):
                    outcome = 'already_held_or_reserved'
                    rejected[outcome] += 1
                elif now < a.state['cooldown'].get(market, 0):
                    outcome = 'market_cooldown'
                    rejected[outcome] += 1
                elif len(set(a.state['positions']) | {o['market'] for o in a.active(side='bid')}) >= allocation['max_positions']:
                    outcome = 'position_slots_full'
                    rejected[outcome] += 1
                else:
                    outcome = 'other_engine_precondition'
                    rejected[outcome] += 1
                signal_outcomes.append(dict(time=now, market=market, outcome=outcome))
            # Optimistic LIMIT TOUCH approximation: infer from this minute's low
            # that a passive order was touched/fillable within its 30-second life.
            # Inject touch at +1s to preserve the production 3-second reprice rule.
            # This timing/queue assumption is UNKNOWN and NOT causally observable
            # from 1m data. We never present these fills as actual historic fills.
            prices = {m: b['o'] for m, b in bars.items() if b}
            for order in list(a.active(side='bid')):
                bar = bars.get(order['market'])
                if not bar:
                    continue
                target_mid = order['price'] / (1 + RULES['impact']) / (1 + spread / 2) * (1 - 1e-12)
                if bar['l'] <= target_mid <= bar['h']:
                    prices[order['market']] = target_mid
            if prices and now + 1 < end:
                feed(prices, now + 1)
                sample(now + 1)
            for offset, field in zip((20, 40, 59), path_order):
                if now + offset >= end:
                    break
                feed({m: b[field] for m, b in bars.items() if b}, now + offset)
                sample(now + offset)
            ticks += 1
            now += 60
        # Remaining positions retain their last executable synthetic mark; the
        # current strategy is NOT overwritten with a forced final liquidation.
        a.validate()  # actual account/fill reconciliation and money conservation
        sample(end)
        state = a.state
        bid_orders = [o for o in state['orders'] if o['side'] == 'bid']
        bid_fills = [f for f in state['fills'] if f['side'] == 'bid']
        closed = state['closed']
        gains = sum(max(0, trade['net']) for trade in closed)
        losses = -sum(min(0, trade['net']) for trade in closed)
        # Continuous calendar monthly returns, including months with no trades.
        months = []
        cursor = dt.datetime.fromtimestamp(start, KST).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        previous = budget
        while cursor.timestamp() < end:
            month = cursor.strftime('%Y-%m')
            final = monthly_last.get(month, previous)
            months.append(dict(month=month, start_equity=previous, end_equity=final,
                               return_pct=(final / previous - 1) * 100))
            previous = final
            cursor = cursor.replace(year=cursor.year + 1, month=1) if cursor.month == 12 else cursor.replace(month=cursor.month + 1)
        return dict(scenario=f"spread_{spread * 100:.3f}pct_" + ''.join(path_order).upper(), spread_ratio=spread,
                    intrabar_path='O-' + '-'.join(x.upper() for x in path_order),
                    initial_budget=budget, final_equity=last_value, return_pct=(last_value / budget - 1) * 100,
                    pnl=last_value - budget, realized_pnl=state['cash'] + sum(p['cost'] for p in state['positions'].values()) - budget,
                    max_drawdown_pct=max_dd * 100, signal_count=len(signals),
                    signals_reserved=sum(accepted.values()), signals_rejected=sum(rejected.values()),
                    signal_rejection_reasons=dict(rejected), signal_outcomes=signal_outcomes,
                    entry_order_count=len(bid_orders), entry_fill_count=len(bid_fills),
                    distinct_filled_entry_orders=len({f['order'] for f in bid_fills}),
                    closed_trade_count=len(closed), wins=sum(t['net'] > 0 for t in closed),
                    win_rate_pct=100 * sum(t['net'] > 0 for t in closed) / len(closed) if closed else None,
                    profit_factor=gains / losses if losses else None,
                    fees=sum(f['fee'] for f in state['fills']), cash=state['cash'], open_positions=state['positions'],
                    permanent_review_lock=state['review'], first_review_lock_time=review_first,
                    review_lock_kst=iso(review_first) if review_first else None,
                    monthly=months, trades=closed, orders=state['orders'], fills=state['fills'], equity=trace,
                    processed_active_minutes=ticks,
                    original_validator_passed=not bool(a.ledger_audit), ledger_audit=a.ledger_audit,
                    accounting_validation='research dust audit passed; original validator failed' if a.ledger_audit else 'AutoTrader.validate passed')


ASSUMPTIONS = [
    'Conditional OHLC execution proxy, not historic account performance and not an expected-return forecast.',
    'Current fixed 10-market subset; not all 299 markets, historic scanner rotation, listing universe or warnings.',
    'Only exact current pullback candle signals; tick-dependent breakout path excluded because historic ticks are unavailable.',
    'Next minute open is synthetic mid; fixed positive spread 0.04% base / 0.0999% stress, deep book assumed; historic depth and queue unknown.',
    'Actual engine fee 0.05% and adverse impact 0.05% per side retained. Spread also affects executable prices.',
    'Optimistic entry touch fill: minute low reaching executable limit is assumed filled at +1s, within 30s expiry and before 3s reprice; timing and queue unverified.',
    'Buy fill still must satisfy production breakout, chase, minimum order and post-fill risk guards.',
    'Intrabar exits use two assumed paths O-L-H-C or O-H-L-C at 0,20,40,59 seconds; the entry touch time is independent of those exit paths.',
    'Actual 1.5% daily halt, 2% daily liquidation, 5% peak review lock, 3-loss pause, cooldown and account sizing remain active.',
    'No nonexistent 1m candles are filled; contexts use only prior completed bars; missing quotes can delay simulated exits.',
    'Remaining positions are marked to last synthetic executable bid less fee/impact, not forcibly liquidated at the end.',
    'The stress/path spread is assumption sensitivity, not a confidence interval or a forecast range.'
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--signals', required=True, nargs='+', type=Path, help='One or more JSONL signal files (PowerShell wildcard expansion is not required)')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--start', default='2026-01-01T00:00:00+09:00')
    parser.add_argument('--end', required=True, help='Exclusive endpoint with timezone')
    parser.add_argument('--budget', type=float, default=1_000_000)
    parser.add_argument('--markets', nargs='+', default=FIXED_MARKETS)
    parser.add_argument('--allocation', type=Path, help='Optional read-only frozen allocation JSON; default is current code DEFAULTS')
    parser.add_argument('--holding', type=Path, help='Optional read-only frozen holding_settings JSON')
    parser.add_argument('--base-only', action='store_true', help='Run just base spread O-L-H-C for quick smoke run')
    args = parser.parse_args()
    start, end = int(epoch(args.start)), int(epoch(args.end))
    if end <= start or start % 60 or end % 60 or not math.isfinite(args.budget) or args.budget <= 0:
        raise ValueError('Invalid aligned time range or budget')
    paths = []
    for path in args.signals:
        paths.extend(sorted(path.parent.glob(path.name)))
    if not paths:
        raise ValueError('No signal files matched')
    allocation = validate_allocation(json.loads(args.allocation.read_text(encoding='utf-8-sig'))) if args.allocation else DEFAULTS.copy()
    holding = json.loads(args.holding.read_text(encoding='utf-8-sig')) if args.holding else None
    signals = read_signals(paths, start, end, args.markets)
    store = CandleStore(args.db)
    scenarios = []
    choices = [(.0004, ('l', 'h', 'c'))] if args.base_only else [(spread, path) for spread in (.0004, .000999) for path in (('l', 'h', 'c'), ('h', 'l', 'c'))]
    for spread, path in choices:
        print(json.dumps(dict(stage='scenario_start', spread=spread, path=path, signals=len(signals))), flush=True)
        result = run_scenario(store, signals, start, end, args.markets, spread, path, args.budget, allocation, holding)
        scenarios.append(result)
        print(json.dumps({key: result[key] for key in ('scenario', 'signal_count', 'signals_reserved', 'entry_fill_count', 'closed_trade_count', 'return_pct', 'signal_rejection_reasons')}, ensure_ascii=False), flush=True)
    store.close()
    report = dict(kind='conditional_ohlc_pullback_proxy', created_kst=dt.datetime.now(KST).isoformat(),
                  start_kst=iso(start), end_exclusive_kst=iso(end), markets=args.markets,
                  signal_files=[str(p.resolve()) for p in paths], database=str(args.db.resolve()),
                  budget=args.budget, allocation=allocation, holding=holding or dict(max_minutes=45, progress_minutes=15),
                  assumptions=ASSUMPTIONS, source_sha256=source_hashes(), scenarios=scenarios,
                  historical_live_performance=False, expected_future_return_estimated=False, real_orders_submitted=0)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(output=str(args.output.resolve()), scenarios=len(scenarios))), flush=True)


if __name__ == '__main__':
    main()
