import datetime as dt
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import bot
import strategy


class TimeframeChanges(unittest.TestCase):
    def test_default_and_config_are_shorter(self):
        self.assertEqual(strategy.Params().tf, 15)
        p, _ = bot.load_params()
        self.assertEqual((p.tf, p.stop_seconds), (15, 30))

    def test_timeframe_change_preserves_position_and_waits_for_new_close(self):
        b = bot.Bot.__new__(bot.Bot)
        b.p = strategy.Params(tf=15)
        pos = {'KRW-BTC': {'qty': 2, 'stop': 95}}
        lg = SimpleNamespace(d={'positions': pos, 'cash': 123, 'last_4h': 0}, event=Mock(), save=Mock())
        b.ledgers = {'test': lg}
        b.align_timeframe(10000)
        self.assertEqual(lg.d['last_4h'], bot.boundary(10000 - bot.SETTLE, 900))
        self.assertIs(lg.d['positions'], pos)
        self.assertEqual((lg.d['cash'], pos['KRW-BTC']['stop']), (123, 95))
        lg.d['last_4h'] = 9000
        b.align_timeframe(12000)
        self.assertEqual(lg.d['last_4h'], 9000)  # Same timeframe restart must not hide missed boundaries.

    def test_signal_fetch_uses_fifteen_minute_close(self):
        b = bot.Bot.__new__(bot.Bot)
        b.p = strategy.Params(tf=15)
        end = 18000
        bars = [{'t': bot.utc(end - 900).isoformat(), 'c': 100}]
        with patch('bot.upbit.completed_candles', return_value=bars) as fetch, patch('bot.time.sleep'):
            self.assertEqual(b.fetch_bars(['KRW-BTC'], end), {'KRW-BTC': bars})
            fetch.assert_called_once_with('KRW-BTC', 15, 200)

    def test_stop_does_not_wait_for_candle_close(self):
        b = bot.Bot.__new__(bot.Bot)
        b.p = strategy.Params(tf=15)
        lg = SimpleNamespace(d={'positions': {'KRW-BTC': {'stop': 90}}},
                             sell=Mock(return_value={'amount': 80, 'pnl': -10, 'pnl_pct': -10}), event=Mock())
        with patch('bot.upbit.orderbooks', return_value={'KRW-BTC': {'bid': 80}}), patch('bot.upbit.completed_candles') as candles:
            b.stop_check(lg, {'KRW-BTC': 81})
            self.assertEqual(lg.sell.call_count, 1)
            candles.assert_not_called()

    def test_unsupported_timeframe_is_rejected(self):
        b = bot.Bot.__new__(bot.Bot)
        b.p = strategy.Params(tf=7)
        b.ledgers = {}
        with self.assertRaises(ValueError): b.align_timeframe(1000)


if __name__ == '__main__': unittest.main()
