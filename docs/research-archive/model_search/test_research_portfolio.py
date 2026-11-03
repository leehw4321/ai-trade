"""Behavioral checks for the historical execution/accounting assumptions."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest

from research_portfolio import PortfolioConfig, audit_result, cost_config, simulate


def bar(t, o=100., h=None, l=None, c=None):
    return {"t": t, "o": o, "h": o if h is None else h,
            "l": o if l is None else l, "c": o if c is None else c, "v": 1000.}


def signal(t=300, distance=5., target=2., hold=10):
    return {"signal_t": t-300, "signal_closed_at": t, "entry_t": t,
            "stop_distance": distance, "target_r": target,
            "max_hold_bars": hold, "score": 1.}


ZERO = PortfolioConfig(fee_rate=0., adverse_slippage=0., half_spread=0.,
                       daily_entry_halt=.9, daily_liquidation=.95, peak_drawdown_halt=.99)


class ExecutionTests(unittest.TestCase):
    def test_entry_uses_next_open_not_signal_close(self):
        rows = {"A": [bar(0, 100), bar(300, 110), bar(600, 111)]}
        result = simulate(rows, {"A": [signal(distance=10)]}, 0, 900, ZERO)
        trade = result["trades"][0]
        self.assertEqual(trade["entry_t"], 300)
        self.assertEqual(trade["entry_reference"], 110)
        self.assertEqual(trade["entry_fill"], 110)

    def test_missing_immediate_next_bar_does_not_fill_later(self):
        rows = {"A": [bar(0), bar(600)]}
        result = simulate(rows, {"A": [signal()]}, 0, 900, ZERO)
        self.assertEqual(result["summary"]["trade_count"], 0)
        self.assertEqual(result["summary"]["entry_rejections"]["next_bar_missing"], 1)

    def test_future_signal_rejected(self):
        s = signal()
        s["signal_closed_at"] = 301
        result = simulate({"A": [bar(0), bar(300)]}, {"A": [s]}, 0, 600, ZERO)
        self.assertEqual(result["summary"]["trade_count"], 0)
        self.assertEqual(result["summary"]["entry_rejections"]["noncausal_or_noncontiguous_signal"], 1)

    def test_ambiguous_entry_bar_is_stop_first(self):
        rows = {"A": [bar(0), bar(300, h=120, l=90)]}
        trade = simulate(rows, {"A": [signal()]}, 0, 600, ZERO)["trades"][0]
        self.assertEqual(trade["reason"], "stop_intrabar_ambiguous")
        self.assertEqual(trade["exit_reference"], 95)
        self.assertLess(trade["net_pnl"], 0)

    def test_stop_gap_exits_at_open_not_stale_stop(self):
        rows = {"A": [bar(0), bar(300), bar(600, o=80)]}
        trade = simulate(rows, {"A": [signal()]}, 0, 900, ZERO)["trades"][0]
        self.assertEqual(trade["reason"], "stop_open_gap")
        self.assertEqual(trade["exit_reference"], 80)

    def test_existing_position_ambiguous_bar_is_also_stop_first(self):
        rows = {"A": [bar(0), bar(300), bar(600, h=120, l=90)]}
        trade = simulate(rows, {"A": [signal()]}, 0, 900, ZERO)["trades"][0]
        self.assertEqual(trade["reason"], "stop_intrabar_ambiguous")
        self.assertEqual(trade["exit_reference"], 95)

    def test_time_exit_is_next_open_after_completed_hold(self):
        rows = {"A": [bar(0), bar(300, c=101, h=101), bar(600, o=102)]}
        trade = simulate(rows, {"A": [signal(distance=20, hold=1)]}, 0, 900, ZERO)["trades"][0]
        self.assertEqual(trade["reason"], "time_next_open")
        self.assertEqual(trade["exit_t"], 600)
        self.assertEqual(trade["exit_reference"], 102)

    def test_all_orders_respect_cash_position_exposure_and_risk(self):
        rows = {f"A{i}": [bar(0), bar(300), bar(600)] for i in range(10)}
        sigs = {m: [signal(distance=.01)] for m in rows}
        cfg = PortfolioConfig()
        result = simulate(rows, sigs, 0, 900, cfg)
        self.assertGreater(len(result["entry_audit"]), 0)
        for order in result["entry_audit"]:
            e = order["equity_before"]
            self.assertGreaterEqual(order["cash_after"], -1e-7)
            self.assertLessEqual(order["debit"], e*cfg.max_position_fraction+1e-7)
            self.assertLessEqual(order["exposure_plus_debit"], e*cfg.max_exposure_fraction+1e-7)
            self.assertLessEqual(order["planned_risk"], e*cfg.risk_per_trade+1e-7)
            self.assertLessEqual(order["aggregate_planned_risk"], e*cfg.max_open_risk+1e-7)
            self.assertLessEqual(order["slots"], cfg.max_positions)
        # Flat price must lose both fees and adverse execution cost.
        self.assertLess(result["summary"]["net_profit"], 0)
        self.assertAlmostEqual(sum(t["net_pnl"] for t in result["trades"]),
                               result["summary"]["net_profit"], places=6)

    def test_slot_limit_with_wide_stop(self):
        rows = {f"A{i}": [bar(0), bar(300), bar(600)] for i in range(10)}
        sigs = {m: [signal(distance=20)] for m in rows}
        result = simulate(rows, sigs, 0, 900, ZERO)
        self.assertEqual(result["summary"]["max_observed_slots"], 4)
        self.assertEqual(result["summary"]["entry_rejections"]["position_slots"], 6)

    def test_close_account_loss_queues_liquidation_for_next_open(self):
        cfg = PortfolioConfig(fee_rate=0., adverse_slippage=0., half_spread=0.,
                              daily_entry_halt=.0005, daily_liquidation=.001,
                              peak_drawdown_halt=.9)
        rows = {"A": [bar(0), bar(300, h=100, l=89, c=90), bar(600, o=85)]}
        result = simulate(rows, {"A": [signal(distance=20)]}, 0, 900, cfg)
        trade = result["trades"][0]
        self.assertEqual(trade["reason"], "daily_risk_next_open")
        self.assertEqual(trade["exit_t"], 600)
        self.assertEqual(trade["exit_reference"], 85)

    def test_minimum_order_rejection(self):
        cfg = PortfolioConfig(initial_cash=1000)
        result = simulate({"A": [bar(0), bar(300)]}, {"A": [signal()]}, 0, 600, cfg)
        self.assertEqual(result["summary"]["trade_count"], 0)
        self.assertEqual(result["summary"]["entry_rejections"]["below_minimum_or_no_budget"], 1)

    def test_independent_audit_rejects_ledger_tampering(self):
        original = simulate({"A": [bar(0), bar(300), bar(600)]},
                            {"A": [signal()]}, 0, 900, PortfolioConfig())
        self.assertTrue(original["summary"]["ledger_audit"]["passed"])
        for field in ("qty", "entry_fee", "exit_fill", "net_pnl"):
            tampered = copy.deepcopy(original)
            tampered["trades"][0][field] += 1.
            with self.assertRaises(ValueError, msg=field):
                audit_result(tampered)
        tampered = copy.deepcopy(original)
        tampered["summary"]["final_equity"] += 1.
        with self.assertRaises(ValueError):
            audit_result(tampered)

    def test_stress_doubles_price_cost_and_keeps_fee(self):
        base, stress = cost_config("base"), cost_config("stress")
        self.assertEqual(base.fee_rate, stress.fee_rate)
        self.assertAlmostEqual(stress.price_cost, base.price_cost*2)
        rows = {"A": [bar(0), bar(300), bar(600)]}
        signal_map = {"A": [signal(distance=20)]}
        normal = simulate(rows, signal_map, 0, 900, base)
        adverse = simulate(rows, signal_map, 0, 900, stress)
        self.assertLess(adverse["summary"]["net_profit"], normal["summary"]["net_profit"])

    def test_peak_drawdown_halt_remains_after_price_recovery(self):
        cfg = PortfolioConfig(fee_rate=0., adverse_slippage=0., half_spread=0.,
                              daily_entry_halt=.9, daily_liquidation=.95,
                              peak_drawdown_halt=.001)
        rows = {"A": [bar(0), bar(300, h=100, l=90, c=90),
                      bar(600, o=110), bar(900, o=120)],
                "B": [bar(0), bar(300), bar(600), bar(900)]}
        result = simulate(rows, {"A": [signal(distance=20)],
                                 "B": [signal(t=600), signal(t=900)]}, 0, 1200, cfg)
        self.assertTrue(result["summary"]["peak_halted"])
        self.assertEqual(result["summary"]["entry_rejections"]["risk_halt"], 2)
        self.assertEqual(result["summary"]["trade_count"], 1)

    def test_paper_replay_keeps_position_open_across_poll_boundaries(self):
        rows = {"A": [bar(0), bar(300, h=101, c=101),
                      bar(600, o=102, h=105, l=101, c=104),
                      bar(900, o=105, h=115, l=104, c=110)]}
        sigs = {"A": [signal()]}
        first = simulate(rows, sigs, 0, 600, ZERO, terminal_flatten=False)
        self.assertEqual(first["summary"]["trade_count"], 0)
        self.assertEqual(len(first["open_positions"]), 1)
        self.assertTrue(first["summary"]["ledger_audit"]["passed"])
        self.assertNotEqual(first["cash"], first["summary"]["final_equity"])
        middle = simulate(rows, sigs, 0, 900, ZERO, terminal_flatten=False)
        self.assertEqual(middle["open_positions"]["A"]["entry_t"], 300)
        self.assertEqual(middle["summary"]["trade_count"], 0)
        final = simulate(rows, sigs, 0, 1200, ZERO, terminal_flatten=False)
        self.assertEqual(final["open_positions"], {})
        self.assertEqual(final["summary"]["trade_count"], 1)
        self.assertEqual(final["trades"][0]["reason"], "target_intrabar")
        self.assertEqual(len(final["entry_audit"]), 1)
        tampered = copy.deepcopy(first)
        tampered["open_positions"]["A"]["qty"] += 1
        with self.assertRaises(ValueError):
            audit_result(tampered)

    def test_default_research_output_matches_frozen_pretest_engine(self):
        frozen = Path(__file__).with_name("frozen_sources")/"research_portfolio.py"
        if not frozen.exists():
            self.skipTest("pre-test frozen source unavailable")
        spec = importlib.util.spec_from_file_location("frozen_research_engine_regression", frozen)
        old = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = old
        spec.loader.exec_module(old)
        rows = {"A": [bar(0), bar(300), bar(600, o=80), bar(900, o=81)],
                "B": [bar(0), bar(300), bar(600, h=120, l=90), bar(900)],
                "C": [bar(0), bar(300), bar(600), bar(900)]}
        sigs = {m: [signal()] for m in rows}
        self.assertEqual(simulate(rows, sigs, 0, 1200), old.simulate(rows, sigs, 0, 1200))


if __name__ == "__main__":
    unittest.main()
