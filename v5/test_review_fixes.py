"""ChatGPT 검토 4건에 대한 회귀 테스트 (네트워크 없음)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bot
import strategy as st
from paper import Ledger


def bars(closes):
    return [{'t': str(i), 'o': c, 'h': c * 1.01, 'l': c * 0.99, 'c': c} for i, c in enumerate(closes)]


class PriceMissing(unittest.TestCase):
    def test_h_value_is_withheld_when_a_price_is_missing(self):
        with tempfile.TemporaryDirectory() as d:
            lg = Ledger(Path(d))
            lg.start(10010, 1.0, {'KRW-BTC': 1.0}, 10, [])
            self.assertEqual(lg.h_value({'KRW-BTC': 10000}), 10010)
            self.assertIsNone(lg.h_value({}))
            self.assertIsNone(lg.snapshot({})['H'])


class Retry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.b = bot.Bot.__new__(bot.Bot)
        self.b.p = st.Params(L=2, tf=15)
        self.b.cfg = {}
        self.b.ledgers = {k: Ledger(root / k) for k in ('P10', 'ALL')}
        for lg in self.b.ledgers.values():
            lg.start(1000, 1.0, {}, 1000, [])
            lg.d['last_4h'] = 100
        self.b.ledger = self.b.ledgers['P10']
        self.b.real, self.b.rs_all, self.b.markets = {}, {}, {}

    def tearDown(self):
        self.tmp.cleanup()

    def test_failed_ledger_is_retried_alone(self):
        calls = []

        def apply(name, lg, *a):
            calls.append(name)
            if name == 'ALL' and calls.count('ALL') == 1:
                raise ValueError('일시 오류')
            lg.d['last_4h'] = 200

        with mock.patch.object(self.b, 'candidates', return_value=([], {})), \
                mock.patch.object(self.b, 'fetch_bars', return_value={}), \
                mock.patch.object(self.b, 'apply', side_effect=apply), \
                mock.patch.object(self.b, 'log'):
            with self.assertRaises(RuntimeError):
                self.b.rebalance(200)
            self.b.rebalance(200)
        self.assertEqual(calls, ['P10', 'ALL', 'ALL'])

    def test_holding_outside_universe_does_not_take_rank(self):
        data = {'KRW-IN': bars([100, 101, 102] + [102] * 15), 'KRW-OUT': bars([100, 150, 300] + [300] * 15)}
        seen = {}

        def apply(name, lg, ranked, entry_ok, end, b, uni):
            seen['ranked'] = [r['market'] for r in ranked]
            lg.d['last_4h'] = end

        with mock.patch.object(self.b, 'candidates', return_value=(['KRW-IN'], {})), \
                mock.patch.object(self.b, 'fetch_bars', return_value=data), \
                mock.patch.object(self.b, 'apply', side_effect=apply), \
                mock.patch.object(self.b, 'log'):
            self.b.ledgers['ALL'].d['positions']['KRW-OUT'] = {'qty': 1, 'entry': 1, 'stop': 0.5, 'spend': 1}
            self.b.rebalance(200)
        self.assertEqual(seen['ranked'], ['KRW-IN'])
        self.assertIsNone(self.b.rs_all['KRW-OUT'][0])


class PartialInit(unittest.TestCase):
    def test_existing_ledger_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            b = bot.Bot.__new__(bot.Bot)
            b.cfg, b.ledgers = {}, {k: Ledger(root / k) for k in ('P10', 'ALL')}
            b.ledger = b.ledgers['P10']
            b.ledgers['P10'].start(500, 0.1, {}, 500, [])
            b.ledgers['P10'].d['cash'] = 12.0
            rows = [{'currency': 'KRW', 'balance': '1000', 'locked': '0', 'avg_buy_price': '0'}]
            with mock.patch('upbit.accounts', return_value=rows), mock.patch('upbit.krw_markets', return_value={}), \
                    mock.patch.object(b, 'log'):
                b.init_t0()
            self.assertEqual(b.ledgers['P10'].d['cash'], 12.0)
            self.assertTrue(b.ledgers['ALL'].ready)


if __name__ == '__main__':
    unittest.main()
