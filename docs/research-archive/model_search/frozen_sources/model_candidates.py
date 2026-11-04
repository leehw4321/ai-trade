"""Frozen, causal long-only spot research candidates.

This module creates signals only. It never places orders or reads bot settings.
Input bars are *completed* OHLCV candles labelled by UTC opening time in epoch
seconds. Indicators see the signal candle and earlier candles only. The portfolio
simulator must enter at the NEXT source candle's open, apply costs, size positions,
and resolve stop/target collisions pessimistically.

No parameter search is performed here. The 16 candidate specifications were fixed
before observing their train/validation/test returns. The published research run
uses observed five-minute candles only and synthesizes no missing candles.
Higher timeframes require all three/twelve observed source candles per bucket.
"""

from array import array
from collections import deque
import json
import math
from pathlib import Path


def _candidate(ident, family, timeframe_s, stop_atr, target_r, hold_hours,
               *, lookback=None, reclaim_ema=None, volume_ratio=None):
    return {
        "id": ident, "family": family, "timeframe_s": timeframe_s,
        "stop_atr": stop_atr, "target_r": target_r,
        "max_hold_s": int(hold_hours * 3600), "lookback": lookback,
        "reclaim_ema": reclaim_ema, "volume_ratio": volume_ratio,
    }


# Keep this list small: every additional candidate is another selection attempt.
CANDIDATES = [
    _candidate("breakout_5m_20", "breakout", 300, 1.5, 2.0, 2,
               lookback=20, volume_ratio=1.2),
    _candidate("breakout_5m_48", "breakout", 300, 2.0, 2.5, 6,
               lookback=48, volume_ratio=1.2),
    _candidate("breakout_15m_20", "breakout", 900, 1.5, 2.0, 6,
               lookback=20, volume_ratio=1.2),
    _candidate("breakout_15m_48", "breakout", 900, 2.0, 2.5, 12,
               lookback=48, volume_ratio=1.2),
    _candidate("pullback_5m_ema20", "pullback", 300, 1.5, 2.0, 2,
               reclaim_ema=20),
    _candidate("pullback_5m_ema50", "pullback", 300, 2.0, 2.0, 4,
               reclaim_ema=50),
    _candidate("pullback_15m_ema20", "pullback", 900, 1.5, 2.0, 8,
               reclaim_ema=20),
    _candidate("pullback_15m_ema50", "pullback", 900, 2.0, 2.0, 12,
               reclaim_ema=50),
    _candidate("revert_5m_bbands", "bb_recovery", 300, 1.5, 1.5, 1),
    _candidate("revert_15m_bbands", "bb_recovery", 900, 1.5, 1.5, 4),
    _candidate("revert_5m_rsi", "rsi_recovery", 300, 1.5, 1.5, 1),
    _candidate("revert_15m_rsi", "rsi_recovery", 900, 1.5, 1.5, 4),
    _candidate("breakout_1h_24", "breakout", 3600, 2.0, 3.0, 48,
               lookback=24, volume_ratio=1.0),
    _candidate("breakout_1h_48", "breakout", 3600, 2.5, 3.0, 72,
               lookback=48, volume_ratio=1.0),
    _candidate("pullback_1h_ema20", "pullback", 3600, 2.0, 2.5, 48,
               reclaim_ema=20),
    _candidate("revert_1h_rsi", "rsi_recovery", 3600, 2.0, 1.5, 24),
]

RULES = {
    "indicator_warmup_bars": 200,
    "ema_seed": "first observed close; require 200 contiguous bars",
    "atr_rsi": "Wilder 14, seeded by first 14 true ranges / close changes",
    "bbands": "20 completed closes, 2 population standard deviations",
    "breakout": (
        "close above preceding N highs; close>EMA200; EMA20>EMA50; "
        "EMA50 above value 3 bars earlier; current volume >= configured multiple "
        "of preceding 20 volumes"
    ),
    "pullback": (
        "prior close<=prior EMA20/50, current close>current EMA20/50; "
        "EMA50>EMA200 and EMA50 rising over 3 bars; RSI14 in [45,70]"
    ),
    "bb_recovery": (
        "prior close below prior lower band, current close recovers current lower "
        "band; RSI14<=45; EMA200 nonfalling over 3 bars OR close>=0.97*EMA200"
    ),
    "rsi_recovery": (
        "RSI14 crosses from below 30 to >=30, prior 3 RSI minimum<=25; "
        "EMA200 nonfalling over 3 bars OR close>=0.97*EMA200"
    ),
    "entry": "next 5-minute open, no current-close fill",
    "exit": "fixed ATR stop / fixed R target / time exit, whichever occurs first",
    "signal_volume": "current completed candle volume must be positive",
    "optimization": "none inside candidate definitions; fixed candidate selection only",
}


def freeze_candidates(path=None):
    """Write reviewable specs. Refuse silently replacing a different freeze."""
    path = Path(path) if path else Path(__file__).with_name("candidates.json")
    payload = {"schema": 1, "candidates": CANDIDATES, "shared_rules": RULES}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != payload:
            raise ValueError("Candidate freeze differs; use a new research run")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def _ema(values, period):
    output = array("d")
    alpha = 2.0 / (period + 1.0)
    current = values[0]
    for value in values:
        current += alpha * (value - current)
        output.append(current)
    return output


def _wilder(values, period=14):
    output = array("d", [math.nan]) * len(values)
    if len(values) < period:
        return output
    average = sum(values[:period]) / period
    output[period - 1] = average
    for i in range(period, len(values)):
        average += (values[i] - average) / period
        output[i] = average
    return output


def _prior_high(values, period):
    """Max of [i-period, i), explicitly excluding the signal candle."""
    output = array("d", [math.nan]) * len(values)
    queue = deque()
    for i, value in enumerate(values):
        while queue and queue[0] < i - period:
            queue.popleft()
        if i >= period:
            output[i] = values[queue[0]]
        while queue and values[queue[-1]] <= value:
            queue.pop()
        queue.append(i)
    return output


def _features(rows, source_indices, seconds):
    n = len(rows)
    closes = array("d", (float(b["c"]) for b in rows))
    volumes = array("d", (float(b["v"]) for b in rows))
    highs = array("d", (float(b["h"]) for b in rows))
    lows = array("d", (float(b["l"]) for b in rows))
    if not n:
        return {"rows": rows, "source_indices": source_indices}
    ema20, ema50, ema200 = (_ema(closes, p) for p in (20, 50, 200))
    tr = array("d", [highs[0] - lows[0]])
    gains, losses = array("d", [0.0]), array("d", [0.0])
    contiguous = array("I", [1])
    for i in range(1, n):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i-1]),
                      abs(lows[i] - closes[i-1])))
        change = closes[i] - closes[i-1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
        contiguous.append(contiguous[-1] + 1 if rows[i]["t"] - rows[i-1]["t"] == seconds else 1)
    atr = _wilder(tr)
    # RSI is seeded with changes 1..14, not an artificial change at bar zero.
    avg_gain, avg_loss = _wilder(gains[1:]), _wilder(losses[1:])
    rsi = array("d", [math.nan])
    for gain, loss in zip(avg_gain, avg_loss):
        rsi.append(50.0 if gain == loss == 0 else
                   100.0 if loss == 0 else 100.0 - 100.0 / (1.0 + gain / loss))
    lower = array("d", [math.nan]) * n
    vol_mean = array("d", [math.nan]) * n
    # Sum close offsets rather than huge raw KRW prices to improve variance stability.
    anchor = closes[0]
    running, running_sq, running_v = 0.0, 0.0, 0.0
    for i, close in enumerate(closes):
        if i >= 20:
            vol_mean[i] = running_v / 20.0  # before adding current volume
        shifted = close - anchor
        running += shifted
        running_sq += shifted * shifted
        running_v += volumes[i]
        if i >= 20:
            old = closes[i-20] - anchor
            running -= old
            running_sq -= old * old
            running_v -= volumes[i-20]
        if i >= 19:
            mean = running / 20.0
            variance = max(0.0, running_sq / 20.0 - mean * mean)
            lower[i] = anchor + mean - 2.0 * math.sqrt(variance)
    return {
        "rows": rows, "source_indices": source_indices, "close": closes,
        "volume": volumes, "ema20": ema20, "ema50": ema50, "ema200": ema200,
        "atr": atr, "rsi": rsi, "bb_lower": lower, "prior_volume20": vol_mean,
        "prior_high": {period: _prior_high(highs, period) for period in (20, 24, 48)},
        "contiguous": contiguous,
    }


def _aggregate(rows, target_seconds, source_seconds):
    factor = target_seconds // source_seconds
    result, indices, bucket = [], [], []
    start = None

    def finish():
        if (len(bucket) == factor and bucket[0][1]["t"] == start and
                bucket[-1][1]["t"] == start + target_seconds - source_seconds and
                all(b[1]["t"] - a[1]["t"] == source_seconds
                    for a, b in zip(bucket, bucket[1:]))):
            result.append({
                "t": start, "o": bucket[0][1]["o"],
                "h": max(item[1]["h"] for item in bucket),
                "l": min(item[1]["l"] for item in bucket),
                "c": bucket[-1][1]["c"],
                "v": sum(item[1]["v"] for item in bucket),
            })
            indices.append(bucket[-1][0])

    for i, row in enumerate(rows):
        current = int(row["t"]) // target_seconds * target_seconds
        if start is not None and current != start:
            finish()
            bucket = []
        start = current
        bucket.append((i, row))
    if bucket:
        finish()
    return result, indices


def prepare_features(rows, source_seconds=300):
    """Precompute each timeframe once; O(n), standard-library only.

    Returns source rows plus three independent completed-timeframe feature sets.
    Gaps are allowed, but signals require 200 contiguous timeframe candles since
    the last gap. No missing values or zero-volume intervals are synthesized here.
    The research run passes observed source candles only.
    """
    if source_seconds != 300:
        raise ValueError("Research portfolio source must be five-minute candles")
    previous_t = None
    for row in rows:
        t = row["t"]
        if (int(t) != t or int(t) % source_seconds or
                (previous_t is not None and t <= previous_t)):
            raise ValueError("Bars must have aligned, strictly increasing opening timestamps")
        if (not all(math.isfinite(float(row[k])) for k in ("o", "h", "l", "c", "v")) or
                not 0 < row["l"] <= min(row["o"], row["c"]) <=
                max(row["o"], row["c"]) <= row["h"] or row["v"] < 0):
            raise ValueError("Invalid OHLCV candle")
        previous_t = t
    frames = {300: _features(rows, list(range(len(rows))), 300)}
    for seconds in (900, 3600):
        aggregated, indices = _aggregate(rows, seconds, source_seconds)
        frames[seconds] = _features(aggregated, indices, seconds)
    return {"source_rows": rows, "source_seconds": source_seconds, "frames": frames}


def iter_signals(prepared, candidate):
    """Yield sparse signal dicts; signal_i is a source 5m candle index.

    stop_distance is an absolute price distance fixed from known signal ATR.
    Entry fill/fees/slippage are deliberately the portfolio simulator's concern.
    A final-candle signal can be yielded; a simulator without its next open must
    count it as unexecutable rather than filling at the signal candle close.
    """
    frame = prepared["frames"][candidate["timeframe_s"]]
    rows = frame["rows"]
    if len(rows) < 200:
        return
    c, v, atr, rsi = (frame[key] for key in ("close", "volume", "atr", "rsi"))
    e20, e50, e200 = (frame[key] for key in ("ema20", "ema50", "ema200"))
    family = candidate["family"]
    for i in range(199, len(rows)):
        if frame["contiguous"][i] < 200 or v[i] <= 0 or not atr[i] > 0:
            continue
        if family == "breakout":
            mean_volume = frame["prior_volume20"][i]
            ok = (c[i] > frame["prior_high"][candidate["lookback"]][i] and
                  c[i] > e200[i] and e20[i] > e50[i] and e50[i] > e50[i-3] and
                  mean_volume > 0 and v[i] >= mean_volume * candidate["volume_ratio"])
            score = c[i] / frame["prior_high"][candidate["lookback"]][i] - 1.0
        elif family == "pullback":
            reclaim = frame["ema" + str(candidate["reclaim_ema"]) ]
            ok = (c[i-1] <= reclaim[i-1] and c[i] > reclaim[i] and
                  e50[i] > e200[i] and e50[i] > e50[i-3] and 45 <= rsi[i] <= 70)
            score = (c[i] - reclaim[i]) / atr[i]
        elif family == "bb_recovery":
            lower = frame["bb_lower"]
            ok = (c[i-1] < lower[i-1] and c[i] >= lower[i] and rsi[i] <= 45 and
                  (e200[i] >= e200[i-3] or c[i] >= .97 * e200[i]))
            score = 45.0 - rsi[i]
        elif family == "rsi_recovery":
            ok = (rsi[i-1] < 30 <= rsi[i] and min(rsi[i-3:i]) <= 25 and
                  (e200[i] >= e200[i-3] or c[i] >= .97 * e200[i]))
            score = rsi[i] - rsi[i-1]
        else:
            raise ValueError("Unknown candidate family: " + family)
        if not ok:
            continue
        source_i = frame["source_indices"][i]
        source_t = prepared["source_rows"][source_i]["t"]
        yield {
            "config_id": candidate["id"], "family": family,
            "signal_i": source_i, "entry_i": source_i + 1,
            "signal_t": int(source_t),
            "entry_t": int(source_t + prepared["source_seconds"]),
            "signal_closed_at": int(rows[i]["t"] + candidate["timeframe_s"]),
            "atr": atr[i], "stop_distance": candidate["stop_atr"] * atr[i],
            "target_r": candidate["target_r"],
            "take_profit_multiple": candidate["target_r"],
            "max_hold_bars": candidate["max_hold_s"] // prepared["source_seconds"],
            "score": score,
        }


def compute_signals(rows, candidate, prepared=None):
    """Compatibility wrapper for callers researching one candidate at a time."""
    prepared = prepared if prepared is not None else prepare_features(rows)
    return list(iter_signals(prepared, candidate))


if __name__ == "__main__":
    print(freeze_candidates())
