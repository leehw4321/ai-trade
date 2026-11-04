"""Causal, pooled-capital spot simulator for the frozen model research.

No private exchange API, production settings, or orders are used. Prices are
historical 5-minute OHLC proxies. Orders fill at the next observed contiguous
bar open, with explicit adverse execution cost and fees. This is not order-book
replay. All timestamps are UTC candle OPEN seconds; reporting days are KST.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import datetime as dt
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

BAR_SECONDS = 300
KST = dt.timezone(dt.timedelta(hours=9))


@dataclass(frozen=True)
class PortfolioConfig:
    initial_cash: float = 1_000_000.0
    risk_per_trade: float = 0.0025
    max_open_risk: float = 0.01
    max_position_fraction: float = 0.25
    max_exposure_fraction: float = 0.50
    max_positions: int = 4
    min_order: float = 5000.0
    fee_rate: float = 0.0005
    adverse_slippage: float = 0.0005
    half_spread: float = 0.0002
    daily_entry_halt: float = 0.015
    daily_liquidation: float = 0.02
    peak_drawdown_halt: float = 0.05

    @property
    def price_cost(self) -> float:
        return self.adverse_slippage + self.half_spread


def cost_config(name: str = "base", **overrides: Any) -> PortfolioConfig:
    """Stress doubles price impact/spread; exchange fee is unchanged."""
    if name not in ("base", "stress"):
        raise ValueError("cost must be base or stress")
    fields = {"adverse_slippage": .001, "half_spread": .0004} if name == "stress" else {}
    fields.update(overrides)
    return PortfolioConfig(**fields)


def iso(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, KST).isoformat()


def parse_time(value: str) -> int:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return int(parsed.timestamp())


def load_5m(db_path: str | Path, *, end: int | None = None,
            start: int | None = None, markets: list[str] | None = None):
    """Aggregate observed 1m candles without fabricating absent intervals.

    A 5m bucket uses the first/last actual trade candle as open/close. Missing
    minutes inside an otherwise observed 5m bucket are legitimate no-trade
    intervals or unknown missing data; no distinction is asserted. Completely
    absent 5m buckets stay absent, and signal continuity checks reject them.
    A partial final 5m bucket is excluded (end is an exclusive minute boundary).
    """
    path = Path(db_path).resolve()
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        selected = markets or [r[0] for r in conn.execute("SELECT DISTINCT market FROM candles ORDER BY market")]
        out, quality = {}, {}
        for market in selected:
            sql = "SELECT t,open,high,low,close,volume,value FROM candles WHERE market=?"
            args: list[Any] = [market]
            if start is not None:
                sql += " AND t>=?"
                args.append(start)
            if end is not None:
                sql += " AND t<?"
                args.append(end)
            sql += " ORDER BY t"
            bars, current, seen, minute_count, invalid = [], None, 0, 0, 0
            for t, o, h, l, c, v, value in conn.execute(sql, args):
                if not (t % 60 == 0 and min(o, h, l, c) > 0 and
                        h >= max(o, l, c) and l <= min(o, h, c) and v >= 0 and value >= 0):
                    invalid += 1
                    continue
                minute_count += 1
                bucket = t - t % BAR_SECONDS
                if end is not None and bucket + BAR_SECONDS > end:
                    continue
                if current is None or bucket != current["t"]:
                    if current is not None:
                        current["observed_minutes"] = seen
                        bars.append(current)
                    current = {"t": bucket, "o": o, "h": h, "l": l,
                               "c": c, "v": v, "value": value}
                    seen = 1
                else:
                    current["h"] = max(current["h"], h)
                    current["l"] = min(current["l"], l)
                    current["c"] = c
                    current["v"] += v
                    current["value"] += value
                    seen += 1
            if current is not None:
                current["observed_minutes"] = seen
                bars.append(current)
            gaps = sum(max(0, (b["t"]-a["t"]) // BAR_SECONDS-1) for a, b in zip(bars, bars[1:]))
            quality[market] = {"observed_1m": minute_count, "invalid_1m_rejected": invalid,
                               "bars_5m": len(bars), "absent_5m_between_observations": gaps,
                               "partial_observed_minutes_buckets": sum(b["observed_minutes"] < 5 for b in bars),
                               "first_5m": iso(bars[0]["t"]) if bars else None,
                               "last_5m": iso(bars[-1]["t"]) if bars else None}
            out[market] = bars
        return out, quality
    finally:
        conn.close()


def audit_result(result: dict, config: PortfolioConfig | None = None) -> dict:
    """Independently reconstruct terminal cash and validate each order's caps.

    Reconstruct using quantities, executed prices, and separately stated fees
    rather than reported P&L. Open virtual positions, when present, remain open.
    This never changes cash/trades and raises on an inconsistent ledger.
    """
    summary, trades, orders = result["summary"], result["trades"], result["entry_audit"]
    cfg = config or PortfolioConfig(**summary["config"])
    tolerance = max(1e-6, cfg.initial_cash*1e-10)
    reconstructed = cfg.initial_cash
    fees, net_pnl = 0., 0.
    failures = []
    opened = result.get("open_positions", {})
    if len(orders) != len(trades)+len(opened):
        failures.append("entry order / terminal trade count mismatch")
    for trade in trades:
        q, entry, exit_ = trade["qty"], trade["entry_fill"], trade["exit_fill"]
        if min(q, entry, exit_) <= 0:
            failures.append("nonpositive quantity or execution price")
        entry_fee, exit_fee = q*entry*cfg.fee_rate, q*exit_*cfg.fee_rate
        if abs(entry_fee-trade["entry_fee"]) > tolerance or abs(exit_fee-trade["exit_fee"]) > tolerance:
            failures.append("fee does not match executed notional")
        debit, credit = q*entry+entry_fee, q*exit_-exit_fee
        reconstructed += credit-debit
        fees += entry_fee+exit_fee
        net_pnl += trade["net_pnl"]
        if abs(credit-debit-trade["net_pnl"]) > tolerance:
            failures.append("trade P&L does not match independent cash flow")
        if abs(entry-trade["entry_reference"]*(1+cfg.price_cost)) > tolerance:
            failures.append("entry adverse price cost mismatch")
        if abs(exit_-trade["exit_reference"]*(1-cfg.price_cost)) > tolerance:
            failures.append("exit adverse price cost mismatch")
        if trade["entry_t"] != trade["signal_t"]+BAR_SECONDS:
            failures.append("entry did not wait until the next bar")
    open_marked_value, open_pnl = 0., 0.
    for p in opened.values():
        q, entry, mark = p["qty"], p["entry_fill"], p["mark_price"]
        entry_fee = q*entry*cfg.fee_rate
        debit = q*entry+entry_fee
        if min(q, entry, mark) <= 0 or abs(entry_fee-p["entry_fee"]) > tolerance:
            failures.append("invalid open position or open entry fee")
        if abs(debit-p["cash_debit"]) > tolerance:
            failures.append("open position entry debit mismatch")
        reconstructed -= debit
        fees += entry_fee
        open_marked_value += q*mark
        open_pnl += q*mark-debit
    for order in orders:
        e = order["equity_before"]
        limits = ((order["debit"], e*cfg.max_position_fraction, "position size"),
                  (order["exposure_plus_debit"], e*cfg.max_exposure_fraction, "total exposure"),
                  (order["planned_risk"], e*cfg.risk_per_trade, "single trade risk"),
                  (order["aggregate_planned_risk"], e*cfg.max_open_risk, "aggregate risk"))
        for actual, cap, name in limits:
            if actual > cap+tolerance:
                failures.append(f"{name} exceeded")
        if order["cash_after"] < -tolerance or order["slots"] > cfg.max_positions:
            failures.append("negative cash or exceeded position slots")
    expected_cash = result.get("cash", summary["final_equity"])
    cash_error = reconstructed-expected_cash
    if abs(cash_error) > tolerance:
        failures.append("terminal cash does not match reconstructed cash flows")
    if abs(reconstructed+open_marked_value-summary["final_equity"]) > tolerance:
        failures.append("marked equity does not match cash plus open positions")
    if abs(net_pnl+open_pnl-summary["net_profit"]) > tolerance or abs(fees-summary["fees_paid"]) > tolerance:
        failures.append("summary P&L or fees mismatch")
    if failures:
        raise ValueError("Research ledger audit failed: "+"; ".join(sorted(set(failures))))
    return {"passed": True, "terminal_positions": len(opened), "trade_count": len(trades),
            "cash_reconstruction_error": cash_error, "reconstructed_final_cash": reconstructed,
            "fee_reconstruction_error": fees-summary["fees_paid"], "tolerance_krw": tolerance,
            "method": "independent executed-quantity cash-flow reconstruction and entry constraint audit"}


def simulate(rows_by_market: dict[str, list[dict]], signals_by_market: dict[str, list[dict]],
             start: int, end: int, config: PortfolioConfig | None = None,
             *, candidate_id: str = "custom", include_equity: bool = True,
             terminal_flatten: bool = True) -> dict:
    """Run one independent period with common cash, risk, and position limits.

    Signals must be finalized at entry_t and entry_t == signal_t + 300.
    Entry ranks use only the supplied prior-bar score, then market name.
    Stops/targets are checked on the entry candle too; ambiguous candles stop
    first. Opening gaps execute at the adverse open, never the stale stop.
    Time exits occur at the first open after max_hold_bars completed intervals.
    Account-close risk liquidation is queued for the next available bar open.
    Research defaults to last-available-close terminal liquidation. A separate
    paper observation can set terminal_flatten=False so a polling boundary never
    manufactures a close/re-entry. Its entire observed history is replayed again.
    """
    cfg = config or PortfolioConfig()
    if not start < end or cfg.initial_cash <= 0:
        raise ValueError("positive cash and a nonempty time interval required")
    if not (0 < cfg.risk_per_trade <= cfg.max_open_risk and
            0 < cfg.max_position_fraction <= cfg.max_exposure_fraction <= 1 and
            cfg.max_positions >= 1 and 0 <= cfg.price_cost < .1 and 0 <= cfg.fee_rate < .1):
        raise ValueError("invalid portfolio constraints")
    events = defaultdict(dict)
    for market, rows in rows_by_market.items():
        previous = None
        for bar in rows:
            t = int(bar["t"])
            if previous is not None and t <= previous:
                raise ValueError(f"unordered/duplicate bars: {market}")
            previous = t
            if start <= t < end and t + BAR_SECONDS <= end:
                events[t][market] = bar
    signals = defaultdict(list)
    rejections = Counter()
    raw_signal_count = 0
    for market, items in signals_by_market.items():
        for item in items:
            t = int(item["entry_t"])
            if not start <= t < end:
                continue
            raw_signal_count += 1
            if (int(item["signal_t"]) + BAR_SECONDS != t or
                int(item.get("signal_closed_at", t)) > t):
                rejections["noncausal_or_noncontiguous_signal"] += 1
                continue
            if t not in events or market not in events[t]:
                rejections["next_bar_missing"] += 1
                continue
            signals[t].append((market, item))

    cash, positions, last_marks = cfg.initial_cash, {}, {}
    trades, equity_curve, halt_events, entry_audit = [], [], [], []
    peak, max_dd = cash, 0.0
    day_start_equity, current_day = cash, None
    daily_halted, permanent_halted = False, False
    pending_liquidation = False
    peak_halt_t = None
    max_observed_exposure, max_observed_slots, max_planned_risk = 0.0, 0, 0.0
    total_fees, total_execution_cost = 0.0, 0.0

    def equity():
        return cash + sum(p["qty"] * last_marks.get(m, p["entry_fill"]) for m, p in positions.items())

    def close(market, reference, t, reason):
        nonlocal cash, total_fees, total_execution_cost
        p = positions.pop(market)
        fill = reference * (1-cfg.price_cost)
        proceeds = p["qty"] * fill
        fee = proceeds * cfg.fee_rate
        cash += proceeds - fee
        total_fees += fee
        execution_cost = p["qty"] * reference * cfg.price_cost
        total_execution_cost += execution_cost
        pnl = proceeds - fee - p["cash_debit"]
        trades.append({"market": market, "candidate_id": candidate_id,
                       "signal_t": p["signal_t"], "entry_t": p["entry_t"], "exit_t": t,
                       "entry_kst": iso(p["entry_t"]), "exit_kst": iso(t),
                       "entry_reference": p["entry_reference"], "entry_fill": p["entry_fill"],
                       "exit_reference": reference, "exit_fill": fill, "qty": p["qty"],
                       "entry_fee": p["entry_fee"], "exit_fee": fee,
                       "fees": p["entry_fee"] + fee,
                       "execution_cost": p["execution_cost"] + execution_cost,
                       "entry_cost": p["cash_debit"], "net_pnl": pnl,
                       "net_return_pct": pnl / p["cash_debit"] * 100,
                       "planned_risk": p["planned_risk"], "stop": p["stop"],
                       "target": p["target"], "reason": reason})

    def mark_guard(t, stage):
        nonlocal daily_halted, permanent_halted, pending_liquidation, peak, max_dd, peak_halt_t
        e = equity()
        peak = max(peak, e)
        dd = 0 if peak <= 0 else 1-e/peak
        max_dd = max(max_dd, dd)
        daily_loss = 0 if day_start_equity <= 0 else 1-e/day_start_equity
        if daily_loss >= cfg.daily_entry_halt and not daily_halted:
            daily_halted = True
            halt_events.append({"t": t, "kst": iso(t), "reason": "daily_entry_halt", "stage": stage,
                                "equity": e, "loss_fraction": daily_loss})
        if daily_loss >= cfg.daily_liquidation and positions:
            pending_liquidation = True
        if dd >= cfg.peak_drawdown_halt and not permanent_halted:
            permanent_halted = True
            peak_halt_t = t
            halt_events.append({"t": t, "kst": iso(t), "reason": "permanent_peak_drawdown_halt",
                                "stage": stage, "equity": e, "loss_fraction": dd})

    ordered_times = sorted(events)
    for t in ordered_times:
        bars = events[t]
        for m, b in bars.items():
            last_marks[m] = b["o"]
        day = dt.datetime.fromtimestamp(t, KST).date().isoformat()
        if day != current_day:
            current_day, day_start_equity, daily_halted = day, equity(), False

        # Existing orders and queued account liquidation execute before entries.
        for m in list(positions):
            if m not in bars:
                continue
            p, b = positions[m], bars[m]
            if pending_liquidation:
                close(m, b["o"], t, "daily_risk_next_open")
            elif b["o"] <= p["stop"]:
                close(m, b["o"], t, "stop_open_gap")
            elif p["target"] is not None and b["o"] >= p["target"]:
                # Taking the known target rather than gap improvement is pessimistic.
                close(m, p["target"], t, "target_open_gap_capped")
            elif t >= p["expires_t"]:
                close(m, b["o"], t, "time_next_open")
        if pending_liquidation and not positions:
            pending_liquidation = False
        mark_guard(t, "open")
        if pending_liquidation:
            # The opening marks themselves are now known, so current-open
            # liquidation can execute without observing the candle close.
            for m in list(positions):
                if m in bars:
                    close(m, bars[m]["o"], t, "daily_risk_open")
            if not positions:
                pending_liquidation = False

        ranked = sorted(signals.get(t, []), key=lambda x: (-float(x[1].get("score", 0)), x[0]))
        for m, signal in ranked:
            if permanent_halted or daily_halted or pending_liquidation:
                rejections["risk_halt"] += 1
                continue
            if m in positions:
                rejections["already_held"] += 1
                continue
            if len(positions) >= cfg.max_positions:
                rejections["position_slots"] += 1
                continue
            b = bars[m]
            distance = float(signal["stop_distance"])
            reference, e = b["o"], equity()
            fill = reference * (1+cfg.price_cost)
            stop = fill-distance
            if not math.isfinite(distance) or distance <= 0 or stop <= 0 or e <= 0:
                rejections["invalid_stop_or_equity"] += 1
                continue
            # Risk includes both fees and adverse stop execution, not just ATR.
            risk_unit = fill * (1+cfg.fee_rate) - stop*(1-cfg.price_cost)*(1-cfg.fee_rate)
            open_risk = sum(p["planned_risk"] for p in positions.values())
            risk_budget = min(e*cfg.risk_per_trade, max(0, e*cfg.max_open_risk-open_risk))
            exposure = sum(p["qty"]*last_marks[m2] for m2,p in positions.items())
            debit_per_unit = fill * (1+cfg.fee_rate)
            qty = min(risk_budget/risk_unit, e*cfg.max_position_fraction/debit_per_unit,
                      max(0, e*cfg.max_exposure_fraction-exposure)/debit_per_unit,
                      cash/debit_per_unit)
            notional = qty * fill
            if notional + 1e-8 < cfg.min_order:
                rejections["below_minimum_or_no_budget"] += 1
                continue
            entry_fee = notional*cfg.fee_rate
            debit = notional+entry_fee
            cash -= debit
            total_fees += entry_fee
            execution_cost = qty*reference*cfg.price_cost
            total_execution_cost += execution_cost
            target_r = signal.get("target_r", signal.get("take_profit_multiple"))
            target = fill+distance*float(target_r) if target_r is not None and float(target_r) > 0 else None
            risk = qty*risk_unit
            positions[m] = {"qty": qty, "entry_t": t, "signal_t": int(signal["signal_t"]),
                            "entry_reference": reference, "entry_fill": fill,
                            "cash_debit": debit, "entry_fee": entry_fee,
                            "execution_cost": execution_cost, "stop": stop,
                            "target": target, "planned_risk": risk,
                            "expires_t": t+max(1, int(signal["max_hold_bars"]))*BAR_SECONDS}
            # Audit requested entry constraints against pre-order equity; the
            # mark immediately after paying costs may make percentages drift.
            entry_audit.append({"t": t, "market": m, "equity_before": e, "debit": debit,
                                "planned_risk": risk, "aggregate_planned_risk": open_risk+risk,
                                "exposure_plus_debit": exposure+debit,
                                "slots": len(positions), "cash_after": cash})
            max_observed_slots = max(max_observed_slots, len(positions))
            max_observed_exposure = max(max_observed_exposure, (exposure+debit)/e)
            max_planned_risk = max(max_planned_risk, (open_risk+risk)/e)

        for m in list(positions):
            if m not in bars:
                continue
            p, b = positions[m], bars[m]
            stop_hit = b["l"] <= p["stop"]
            target_hit = p["target"] is not None and b["h"] >= p["target"]
            if stop_hit:
                reference = min(b["o"], p["stop"])
                close(m, reference, t, "stop_intrabar_ambiguous" if target_hit else "stop_intrabar")
            elif target_hit:
                close(m, p["target"], t, "target_intrabar")
        for m, b in bars.items():
            last_marks[m] = b["c"]
        mark_guard(t+BAR_SECONDS, "close")
        equity_curve.append({"t": t+BAR_SECONDS, "equity": equity(), "cash": cash,
                             "positions": len(positions), "daily_halted": daily_halted,
                             "permanent_halted": permanent_halted})

    # Forced terminal flatten is a fixed evaluation convention, not a signal.
    if terminal_flatten:
        for m in list(positions):
            last_t = max(t for t in ordered_times if m in events[t])
            close(m, events[last_t][m]["c"], last_t+BAR_SECONDS, "research_window_end_close")
    if ordered_times and terminal_flatten:
        mark_guard(ordered_times[-1]+BAR_SECONDS, "terminal_flatten")
        equity_curve[-1].update(equity=cash, cash=cash, positions=0)

    final_equity = equity()

    wins = [x["net_pnl"] for x in trades if x["net_pnl"] > 0]
    losses = [x["net_pnl"] for x in trades if x["net_pnl"] < 0]
    monthly = {}
    previous_equity = cfg.initial_cash
    for point in equity_curve:
        # A candle close at midnight belongs to the preceding candle month.
        month = dt.datetime.fromtimestamp(point["t"]-1, KST).strftime("%Y-%m")
        if month not in monthly:
            monthly[month] = {"start_equity": previous_equity, "end_equity": point["equity"]}
        monthly[month]["end_equity"] = point["equity"]
        previous_equity = point["equity"]
    for item in monthly.values():
        item["net_return_pct"] = (item["end_equity"]/item["start_equity"]-1)*100
    summary = {"candidate_id": candidate_id, "start_kst": iso(start), "end_exclusive_kst": iso(end),
               "initial_cash": cfg.initial_cash, "final_equity": final_equity,
               "net_profit": final_equity-cfg.initial_cash,
               "net_return_pct": (final_equity/cfg.initial_cash-1)*100,
               "max_drawdown_pct": max_dd*100, "trade_count": len(trades),
               "raw_signal_count": raw_signal_count,
               "win_rate_pct": len(wins)/len(trades)*100 if trades else None,
               "profit_factor": sum(wins)/-sum(losses) if losses else None,
               "profit_factor_undefined_reason": "no losing closed trades" if not losses else None,
               "average_trade_pnl": ((cash-cfg.initial_cash)/len(trades) if terminal_flatten
                                      else sum(x["net_pnl"] for x in trades)/len(trades)) if trades else None,
               "average_trade_return_pct": sum(x["net_return_pct"] for x in trades)/len(trades) if trades else None,
               "fees_paid": total_fees, "adverse_execution_cost": total_execution_cost,
               "entry_rejections": dict(rejections),
               "exit_reasons": dict(Counter(x["reason"] for x in trades)),
               "peak_halted": permanent_halted, "peak_halt_t": peak_halt_t,
               "max_entry_exposure_fraction": max_observed_exposure,
               "max_entry_aggregate_risk_fraction": max_planned_risk,
               "max_observed_slots": max_observed_slots,
               "monthly": monthly, "config": asdict(cfg), "calendar_days": (end-start)/86400,
               "limitations": ["OHLC market-fill proxy, not historical order-book replay",
                               "bar-close drawdown and daily guards can miss intrabar account losses",
                               "last available quotes mark missing-market bars; stale quotes possible",
                               "fixed sample of ten currently listed markets has selection/survivorship bias",
                               "risk stops may overrun on gaps; final close flatten is a research convention"]}
    result = {"summary": summary, "trades": trades, "halt_events": halt_events,
              "entry_audit": entry_audit, "equity_curve": equity_curve if include_equity else []}
    if not terminal_flatten:
        result.update(cash=cash,
                      open_positions={m: {**p, "mark_price": last_marks.get(m, p["entry_fill"])}
                                      for m,p in positions.items()},
                      paper_state={"pending_liquidation": pending_liquidation,
                                   "daily_halted": daily_halted, "permanent_halted": permanent_halted,
                                   "day_start_equity": day_start_equity, "current_kst_day": current_day,
                                   "peak_equity": peak})
        summary["terminal_flatten"] = False
        summary["unrealized_mark_pnl"] = sum(p["qty"]*last_marks.get(m,p["entry_fill"])-p["cash_debit"]
                                             for m,p in positions.items())
        summary["open_position_count"] = len(positions)
    summary["ledger_audit"] = audit_result(result, cfg)
    return result


def buy_hold_baseline(rows_by_market, start, end, config=None):
    """Equal-weight 50%-invested, 50%-cash ten-market passive comparator."""
    cfg = config or PortfolioConfig()
    selected = {m: [b for b in rows if start <= b["t"] and b["t"]+BAR_SECONDS <= end]
                for m, rows in rows_by_market.items()}
    selected = {m: b for m,b in selected.items() if b}
    if not selected:
        return {"net_return_pct": 0., "final_equity": cfg.initial_cash, "markets": {}}
    budget = cfg.initial_cash*cfg.max_exposure_fraction/len(selected)
    cash, detail = cfg.initial_cash, {}
    for m, bars in selected.items():
        entry = bars[0]["o"]*(1+cfg.price_cost)
        qty = budget/(entry*(1+cfg.fee_rate))
        proceeds = qty*bars[-1]["c"]*(1-cfg.price_cost)*(1-cfg.fee_rate)
        cash += proceeds-budget
        detail[m] = {"allocated": budget, "net_profit": proceeds-budget,
                     "entry_t": bars[0]["t"], "exit_t": bars[-1]["t"]+BAR_SECONDS}
    return {"label": "50% equal-weight buy-and-hold, 50% cash, no risk stops",
            "final_equity": cash, "net_return_pct": (cash/cfg.initial_cash-1)*100,
            "markets": detail}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(__file__).parents[1]/"ytd_v4/historical/candles_1m.sqlite")
    parser.add_argument("--start", required=True, help="inclusive KST/ISO datetime")
    parser.add_argument("--end", required=True, help="exclusive KST/ISO datetime")
    parser.add_argument("--candidate", action="append", help="frozen candidate ID; may repeat")
    parser.add_argument("--cost", choices=["base", "stress"], default="base")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    from model_candidates import CANDIDATES, prepare_features, iter_signals
    start, end = parse_time(args.start), parse_time(args.end)
    candidates = [c for c in CANDIDATES if not args.candidate or c["id"] in args.candidate]
    if args.candidate and len(candidates) != len(set(args.candidate)):
        raise SystemExit("unknown candidate ID")
    rows, quality = load_5m(args.db, end=end)
    prepared = {m: prepare_features(bars) for m,bars in rows.items()}
    cfg = cost_config(args.cost)
    results = []
    for candidate in candidates:
        signal_map = {m: list(iter_signals(p, candidate)) for m,p in prepared.items()}
        result = simulate(rows, signal_map, start, end, cfg, candidate_id=candidate["id"])
        if args.summary_only:
            result = {"summary": result["summary"], "halt_events": result["halt_events"]}
        results.append(result)
        print(json.dumps(result["summary"], ensure_ascii=True), flush=True)
    payload = {"schema": 1, "cost": args.cost, "quality": quality,
               "baseline": buy_hold_baseline(rows, start, end, cfg), "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
