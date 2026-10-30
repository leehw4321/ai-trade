import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import paper_selected_model as paper
from analysis_runs.model_search.model_candidates import CANDIDATES


def flat_bar(t):
    return {"t": t, "o": 100., "h": 100., "l": 100., "c": 100., "v": 100., "value": 10000.}


class FakePublic:
    def fetch(self, market, now, *, needed=250, since=None):
        end = int(now)//300*300
        bars = [flat_bar(t) for t in range(end-needed*300, end, 300)]
        return bars if since is None else [b for b in bars if b["t"] >= since-300]


class PaperIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.selection = self.root/"selection.json"
        self.ledger = self.root/"isolated"/"paper.json"
        self.cache = self.root/"isolated"/"bars.sqlite"
        selected = next(dict(c) for c in CANDIDATES if c["id"] == "breakout_1h_48")
        selected.update(name="Display metadata", timeframe="60 minutes")
        self.selection.write_text(json.dumps({"selected_config": selected, "approved_paper": False}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_once_is_read_only_and_never_opens_sqlite(self):
        with patch.object(paper, "atomic_json", side_effect=AssertionError("write")), \
             patch.object(paper.sqlite3, "connect", side_effect=AssertionError("DB opened")):
            result = paper.read_only_status(self.selection, self.ledger, FakePublic(), now=1_000_000)
        self.assertEqual(result["mode"], "read_only_public_signal_status")
        self.assertEqual(result["signals"], [])
        self.assertEqual(len(result["readiness"]), 10)
        self.assertFalse(self.ledger.parent.exists())

    def test_unqualified_watch_refuses_before_creating_account(self):
        with self.assertRaises(ValueError):
            paper.watch(selection_path=self.selection, ledger_path=self.ledger,
                        cache_path=self.cache, client=FakePublic(), max_cycles=1)
        self.assertFalse(self.ledger.parent.exists())

    def test_explicit_experiment_starts_future_then_resumes_same_account(self):
        with patch.object(paper.time, "time", return_value=1_000_000), contextlib.redirect_stdout(io.StringIO()):
            state = paper.watch(experimental=True, selection_path=self.selection, ledger_path=self.ledger,
                                cache_path=self.cache, client=FakePublic(), max_cycles=1)
        self.assertGreater(state["paper_start_t"], 1_000_000)
        self.assertEqual(state["paper_start_t"] % 300, 0)
        self.assertTrue(state["experimental"])
        self.assertEqual(state["summary"]["trade_count"], 0)
        self.assertEqual(state["summary"]["final_equity"], 1_000_000)
        original_start = state["paper_start_t"]
        with patch.object(paper.time, "time", return_value=1_000_900), contextlib.redirect_stdout(io.StringIO()):
            resumed = paper.watch(experimental=True, selection_path=self.selection, ledger_path=self.ledger,
                                  cache_path=self.cache, client=FakePublic(), max_cycles=1)
        self.assertEqual(resumed["paper_start_t"], original_start)
        self.assertEqual(resumed["summary"]["trade_count"], 0)
        self.assertEqual(resumed["summary"]["final_equity"], 1_000_000)
        self.assertTrue(resumed["summary"]["ledger_audit"]["passed"])

    def test_candidate_parameter_changes_are_rejected(self):
        selection = json.loads(self.selection.read_text())
        selection["selected_config"]["stop_atr"] = .01
        self.selection.write_text(json.dumps(selection))
        with self.assertRaises(ValueError):
            paper.load_selection(self.selection)

    def test_closed_candle_revisions_and_forming_bars_are_rejected(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE bars(market TEXT,t INTEGER,o REAL,h REAL,l REAL,c REAL,v REAL,value REAL,PRIMARY KEY(market,t))")
        original = flat_bar(0)
        paper.store_closed_bars(conn, "KRW-BTC", [original], 600)
        changed = dict(original, v=101.)
        with self.assertRaises(ValueError):
            paper.store_closed_bars(conn, "KRW-BTC", [changed], 600)
        with self.assertRaises(ValueError):
            paper.store_closed_bars(conn, "KRW-BTC", [flat_bar(600)], 700)
        self.assertEqual(conn.execute("SELECT v FROM bars").fetchone()[0], 100.)
        conn.close()


if __name__ == "__main__":
    unittest.main()
