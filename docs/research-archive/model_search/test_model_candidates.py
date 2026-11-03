"""Behavioral checks for causal signal generation, not profitability assertions."""

import math
import unittest

from model_candidates import CANDIDATES, compute_signals, iter_signals, prepare_features


def candles(n, start=0):
    rows = []
    previous = 100.0
    for i in range(n):
        close = 100.0 + .008 * i + 3 * math.sin(i / 9) + .5 * math.sin(i / 2)
        rows.append({"t": start + i * 300, "o": previous,
                     "h": max(previous, close) + .1,
                     "l": min(previous, close) - .1, "c": close,
                     "v": 500.0 if i % 17 == 0 else 100.0})
        previous = close
    return rows


class CandidateBehavior(unittest.TestCase):
    def test_appending_extreme_future_does_not_change_past_signals(self):
        rows = candles(5000)
        cutoff = 3604  # deliberately inside higher-timeframe buckets
        prefix = prepare_features(rows[:cutoff])
        future = [dict(row) for row in rows]
        for row in future[cutoff:]:
            row.update(o=1_000_000.0, h=2_000_000.0, l=.1,
                       c=1_500_000.0, v=1e10)
        completed = prepare_features(future)
        count = 0
        for candidate in CANDIDATES:
            expected = list(iter_signals(prefix, candidate))
            actual = [s for s in iter_signals(completed, candidate)
                      if s["signal_i"] < cutoff]
            self.assertEqual(expected, actual, candidate["id"])
            count += len(expected)
        self.assertGreater(count, 0)

    def test_breakout_excludes_current_high_and_enters_next_source_open(self):
        rows = []
        for i in range(1000):
            close = 100.0 + i * .1
            rows.append({"t": i * 300, "o": close - .05, "h": close + .02,
                         "l": close - .1, "c": close,
                         "v": 1000.0 if i == 720 else 100.0})
        prepared = prepare_features(rows)
        five = list(iter_signals(prepared, CANDIDATES[0]))
        fifteen = list(iter_signals(prepared, CANDIDATES[2]))
        self.assertIn(720, [s["signal_i"] for s in five])
        signal = next(s for s in fifteen if s["signal_i"] == 722)
        self.assertEqual(signal["entry_i"], 723)
        self.assertEqual(signal["entry_t"], 723 * 300)
        self.assertEqual(signal["signal_closed_at"], signal["entry_t"])

    def test_gap_resets_contiguous_signal_warmup(self):
        rows = candles(1000)
        rows = [row for row in rows if row["t"] != 300 * 300]
        prepared = prepare_features(rows)
        signals = list(iter_signals(prepared, CANDIDATES[0]))
        self.assertFalse(any(301 * 300 <= s["signal_t"] < 500 * 300
                             for s in signals))

    def test_incomplete_higher_timeframe_is_not_emitted(self):
        prepared = prepare_features(candles(3602))
        self.assertEqual(prepared["frames"][900]["source_indices"][-1], 3599)
        self.assertEqual(prepared["frames"][3600]["source_indices"][-1], 3599)

    def test_all_signal_risk_distances_are_known_and_positive(self):
        prepared = prepare_features(candles(5000))
        for candidate in CANDIDATES:
            for signal in iter_signals(prepared, candidate):
                self.assertGreater(signal["stop_distance"], 0)
                self.assertTrue(math.isfinite(signal["stop_distance"]))
                self.assertEqual(signal["signal_closed_at"], signal["entry_t"])
                self.assertGreater(signal["max_hold_bars"], 0)


if __name__ == "__main__":
    unittest.main()
