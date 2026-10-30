"""Focused causal-input and actual account reconciliation checks for OHLC proxy."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from allocation_settings import DEFAULTS
from ytd_proxy_sim import CandleStore, read_signals, run_scenario


class ProxyTests(unittest.TestCase):
    def test_future_signal_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'signals.jsonl'
            path.write_text(json.dumps(dict(market='KRW-BTC', time=60,
                                           signal=dict(ok=True, closed_at=120, id='0'))), encoding='utf-8')
            with self.assertRaises(ValueError):
                read_signals([path], 0, 180, ['KRW-BTC'])

    def test_touch_fill_and_exit_reconcile_fees(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'candles.sqlite'
            stamp = 1767225600  # minute aligned, UTC Jan 1 2026
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE candles (market TEXT,t INTEGER,open REAL,high REAL,low REAL,close REAL,value REAL,volume REAL,PRIMARY KEY(market,t))')
                db.executemany('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)', [
                    ('KRW-BTC', stamp, 100000, 100200, 99500, 100100, 1e8, 1000),
                    ('KRW-BTC', stamp + 60, 100100, 100200, 98000, 98500, 1e8, 1000)])
            db.close()
            store = CandleStore(path)
            sig = dict(ok=True, closed_at=stamp, time=stamp, id=str(stamp - 60),
                       close=100000, atr=700, ema20=99900, breakout=98000, low=99500)
            result = run_scenario(store, [dict(market='KRW-BTC', time=stamp, signal=sig)], stamp,
                                  stamp + 120, ['KRW-BTC'], .0004, ('l', 'h', 'c'), 1e6, DEFAULTS)
            store.close()
            self.assertEqual(result['entry_fill_count'], 1)
            self.assertEqual(result['closed_trade_count'], 1)
            self.assertGreater(result['fees'], 0)
            self.assertEqual(result['accounting_validation'], 'AutoTrader.validate passed')
            self.assertAlmostEqual(result['cash'] - 1e6, result['trades'][0]['net'], places=6)
            self.assertFalse(result['open_positions'])
            self.assertEqual(result['signal_count'], result['signals_reserved'] + result['signals_rejected'])

    def test_context_does_not_read_current_candle(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'candles.sqlite'
            stamp = 1767225600
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE candles (market TEXT,t INTEGER,open REAL,high REAL,low REAL,close REAL,value REAL,volume REAL,PRIMARY KEY(market,t))')
                rows = [('KRW-BTC', stamp + i * 60, 100000, 100100, 99900, 100000 + i * .01, 1e8, 1000)
                        for i in range(3101)]
                db.executemany('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)', rows)
            db.close()
            now = stamp + 3100 * 60
            store = CandleStore(path)
            before = store.context('KRW-BTC', now)
            store.close()
            self.assertIsNotNone(before)
            with sqlite3.connect(path) as db:
                db.execute('UPDATE candles SET high=1e10,low=1,close=1e9 WHERE t=?', (now,))
            db.close()
            store = CandleStore(path)
            after = store.context('KRW-BTC', now)
            store.close()
            self.assertEqual(before, after)
            self.assertEqual(after['closed_at'], now)


if __name__ == '__main__':
    unittest.main()
