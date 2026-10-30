"""Offline count of the current v4 pullback candle signals, without live changes.

This is a candle-condition census, not an execution backtest. It deliberately
does not fill missing candles. Current universe membership, historic warnings,
the scanner rotation, order books, and tick-based breakout signals cannot be
recovered from OHLCV. Input bars must be ascending and have t,h,l,c,v and
preferably value (KRW traded notional). CLI output records these limitations.
"""
import argparse
import bisect
from collections import Counter
import datetime as dt
import json
import math
from pathlib import Path
import sqlite3
import time

from scalping_strategy import evaluate_intraday

KST = dt.timezone(dt.timedelta(hours=9))
WINDOWS = (("15", 900, 200, 200), ("5", 300, 300, 200), ("1", 60, 200, 60))
CHECK_PRICE = "1분 상승 확인 필요조건(직전 고가 돌파)"
CHECK_VOLUME = "1분 거래량 1.3배"
CHECK_VALUE = "최근 거래대금"
CHECK_CHASE = "급등 추격 차단"
CHECK_BREAKOUT = "5분 거래량 돌파 존재(눌림 필요조건)"


def iso(epoch):
    return dt.datetime.fromtimestamp(epoch, KST).isoformat()


def timestamp(value):
    if isinstance(value, (int, float)):
        return float(value)
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.timestamp()


def load_data(path):
    """Accept {market, 1:[...],5:[...],15:[...]} and candles/timeframes wrapper."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    bars = data.get("candles", data.get("timeframes", data))
    result = {}
    aliases = {"1": ("1", "one", "1m"), "5": ("5", "five", "5m"),
               "15": ("15", "quarter", "15m")}
    for unit, choices in aliases.items():
        rows = next((bars[key] for key in choices if key in bars), [])
        if not isinstance(rows, list):
            raise ValueError(f"{unit}m bars must be a list")
        if any(rows[i]["t"] > rows[i + 1]["t"] for i in range(len(rows) - 1)):
            raise ValueError(f"{unit}m input must be ascending; input was not reordered")
        result[unit] = rows
    return data.get("market", Path(path).stem), result


def load_sqlite(path, market):
    """Read downloader's 1m table; aggregate traded minutes without gap fill."""
    with sqlite3.connect(path) as db:
        raw = db.execute(
            "SELECT t,open,high,low,close,value,volume FROM candles WHERE market=? ORDER BY t",
            (market,)).fetchall()
    one = [dict(t=row[0], o=row[1], h=row[2], l=row[3], c=row[4], value=row[5], v=row[6]) for row in raw]
    result = {"1": one}
    for unit in (5, 15):
        seconds, grouped = unit * 60, []
        for bar in one:
            stamp = math.floor(bar["t"] / seconds) * seconds
            if not grouped or grouped[-1]["t"] != stamp:
                grouped.append(dict(t=stamp, o=bar["o"], h=bar["h"], l=bar["l"],
                                    c=bar["c"], value=bar["value"], v=bar["v"]))
            else:
                current = grouped[-1]
                current.update(h=max(current["h"], bar["h"]), l=min(current["l"], bar["l"]),
                               c=bar["c"], value=current["value"] + bar["value"], v=current["v"] + bar["v"])
        result[str(unit)] = grouped
    return market, result


class WindowIndex:
    """O(1) exact validate_bars result after an O(log N) closed-bar lookup."""
    def __init__(self, rows, seconds, window, minimum):
        self.rows, self.seconds, self.window, self.minimum = rows, seconds, window, minimum
        self.times = [row["t"] for row in rows]
        self.bad, self.gaps = [0], [0]
        for i, row in enumerate(rows):
            try:
                valid = (all(isinstance(row[key], (int, float)) and math.isfinite(row[key])
                             for key in ("t", "h", "l", "c", "v"))
                         and 0 < row["l"] <= row["c"] <= row["h"]
                         and row["v"] >= 0 and row["t"] % seconds == 0)
            except (KeyError, TypeError, ValueError):
                valid = False
            self.bad.append(self.bad[-1] + int(not valid))
            self.gaps.append(self.gaps[-1] + int(i > 0 and row["t"] - rows[i - 1]["t"] != seconds))

    def at(self, now):
        right = bisect.bisect_right(self.times, now - self.seconds)
        left = max(0, right - self.window)
        if right - left < self.minimum:
            return left, right, "insufficient_bars"
        if not 0 <= now - self.times[right - 1] - self.seconds < self.seconds:
            return left, right, "stale_or_unclosed_bar"
        if self.bad[right] != self.bad[left]:
            return left, right, "invalid_price_or_time"
        # gaps[i+1] includes the pair ending at row i; omit pair crossing left.
        if self.gaps[right] != self.gaps[left + 1]:
            return left, right, "gap_duplicate_or_reverse"
        return left, right, ""


def cheap_checks(one, oi, five, fi):
    """Necessary conditions only. Rejections cannot suppress a true signal."""
    latest, previous = one[oi - 1], one[oi - 2]
    last5, previous5 = five[fi - 1], five[fi - 2]
    average = sum(bar["v"] for bar in one[oi - 21:oi - 1]) / 20
    breakout = False
    for lag in (1, 2, 3):
        pos = fi - 1 - lag
        before = five[pos - 20:pos]
        mean = sum(bar["v"] for bar in before) / 20
        if (mean > 0 and five[pos]["c"] > max(bar["h"] for bar in before)
                and five[pos]["v"] >= mean * 1.5):
            breakout = True
            break
    return {
        CHECK_PRICE: latest["c"] > previous["h"],
        CHECK_VOLUME: average > 0 and latest["v"] >= average * 1.3,
        CHECK_VALUE: (latest.get("value", latest["c"] * latest["v"]) >= 5_000_000
                      and last5.get("value", last5["c"] * last5["v"]) >= 30_000_000),
        CHECK_CHASE: (abs(latest["c"] / previous["c"] - 1) <= .015
                      and abs(last5["c"] / previous5["c"] - 1) <= .04),
        CHECK_BREAKOUT: breakout,
    }


def census(market, bars, start, end, progress=None):
    """Count each market-minute once, at the first minute after candle close.

    Inputs end strictly at or before now. Higher frames are selected by close
    time, never by open time. Warmup may precede start and is not itself counted.
    A gap invalidates the same complete rolling window as production does.
    """
    started = time.perf_counter()
    indexes = {unit: WindowIndex(bars[unit], sec, window, minimum)
               for unit, sec, window, minimum in WINDOWS}
    attempts = valid = survived = 0
    data_failures, cheap_failures, reference_failures, monthly = Counter(), Counter(), Counter(), Counter()
    cheap_funnel = Counter()
    signals, sampled_reference = [], []
    fallback_signals = 0
    first = int(math.ceil(start / 60) * 60)
    last = int(math.floor(end / 60) * 60)
    for now in range(first, last + 1, 60):
        attempts += 1
        windows = {unit: indexes[unit].at(now) for unit, *_ in WINDOWS}
        errors = [(unit, windows[unit][2]) for unit, *_ in WINDOWS if windows[unit][2]]
        if errors:
            # Each invalid minute has one primary reason plus independent reasons.
            data_failures[f"primary_{errors[0][0]}m_{errors[0][1]}"] += 1
            for unit, error in errors:
                data_failures[f"any_{unit}m_{error}"] += 1
            continue
        valid += 1
        oi, fi = windows["1"][1], windows["5"][1]
        checks = cheap_checks(bars["1"], oi, bars["5"], fi)
        passes = True
        for key, value in checks.items():
            if not value:
                cheap_failures[key] += 1
            passes = passes and value
            if passes:
                cheap_funnel[key] += 1
        if not all(checks.values()):
            continue
        survived += 1
        selected = {unit: bars[unit][windows[unit][0]:windows[unit][1]] for unit, *_ in WINDOWS}
        signal = evaluate_intraday(selected["15"], selected["5"], selected["1"], now)
        for key, value in signal.get("checks", {}).items():
            if not value:
                reference_failures[key] += 1
        if not signal.get("checks"):
            reference_failures[signal.get("reason", "unknown")] += 1
        if len(sampled_reference) < 5:
            sampled_reference.append({"closed_at": now, "ok": signal.get("ok"), "reason": signal.get("reason")})
        if signal.get("ok"):
            record = {key: signal[key] for key in ("id", "closed_at", "close", "atr", "ema20", "adx", "breakout", "low", "vwap", "vwap_method")}
            record.update(market=market, closed_at_kst=iso(now))
            record["signal"] = signal.copy()
            signals.append(record)
            monthly[iso(now)[:7]] += 1
            fallback_signals += int(signal.get("vwap_method") != "거래대금/수량")
        if progress and survived % 1000 == 0:
            progress({"market": market, "checked_through": iso(now), "reference_evaluations": survived,
                      "signals": len(signals), "elapsed_seconds": round(time.perf_counter() - started, 1)})
    coverage = {unit: {"rows": len(rows), "first_open_kst": iso(rows[0]["t"]) if rows else None,
                       "last_close_kst": iso(rows[-1]["t"] + int(unit) * 60) if rows else None,
                       "notional_rows": sum("value" in bar for bar in rows)} for unit, rows in bars.items()}
    return {"market": market, "status": "evaluated" if valid else "no_evaluable_minutes",
            "requested_start_kst": iso(start), "requested_end_kst": iso(end), "coverage": coverage,
            "minute_slots": attempts, "valid_candle_minutes": valid, "data_invalid_minutes": attempts - valid,
            "cheap_necessary_conditions_survived": survived, "exact_reference_evaluations": survived,
            "candle_signal_count": len(signals) if valid else None,
            "signals_with_approximate_vwap": fallback_signals,
            "monthly_signal_count": dict(sorted(monthly.items())), "data_failures": dict(data_failures),
            "cheap_condition_independent_failures_among_valid_minutes": dict(cheap_failures),
            "cheap_condition_cumulative_survivors_in_listed_order": dict(cheap_funnel),
            "reference_condition_failures_among_cheap_survivors_only": dict(reference_failures),
            "sampled_reference_evaluations": sampled_reference, "signals": signals,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "execution_trades": None, "expected_return": None,
            "limitations": [
                "Counts current pullback candle conditions; a signal is not an order or fill.",
                "Historical universe warnings, ranking rotation and scanner delivery latency are not reproduced.",
                "No historical order books: spread/depth, 30-second passive fill and repricing are unverified.",
                "Tick-based early breakout route cannot be reconstructed from candles and is excluded.",
                "A zero count is valid only within the reported evaluable data coverage; missing coverage is not zero trades.",
                "No expected future return is inferred from this historical signal census."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--market", help="Read SQLite candles table for this market instead of JSON")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--signals-output", type=Path, help="Optional exact signal JSONL events")
    parser.add_argument("--start", default="2026-01-01T00:00:00+09:00")
    parser.add_argument("--end", required=True, help="Inclusive last completed minute boundary, with timezone")
    args = parser.parse_args()
    market, bars = load_sqlite(args.input, args.market) if args.market else load_data(args.input)
    result = census(market, bars, timestamp(args.start), timestamp(args.end),
                    progress=lambda message: print(json.dumps(message, ensure_ascii=False), flush=True))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.signals_output:
        args.signals_output.parent.mkdir(parents=True, exist_ok=True)
        with args.signals_output.open("w", encoding="utf-8") as stream:
            for record in result["signals"]:
                stream.write(json.dumps({"market": market, "time": record["closed_at"],
                                         "signal": record["signal"]}, ensure_ascii=False) + "\n")
    print(json.dumps({key: result[key] for key in ("market", "status", "valid_candle_minutes", "candle_signal_count", "elapsed_seconds")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
