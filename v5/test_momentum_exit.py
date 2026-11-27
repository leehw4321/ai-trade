import datetime as dt
import tempfile
import unittest
from pathlib import Path

import momentum_exit as me

C = dict(me.DEFAULTS, momentum_exit_enabled=True)


def bars(closes, unit, end):
    """end: 마지막 봉 마감 시각(UTC)."""
    n = len(closes)
    return [{'t': (end - dt.timedelta(minutes=unit * (n - i))).isoformat(), 'o': c, 'h': c, 'l': c, 'c': c} for i, c in enumerate(closes)]


class Signal(unittest.TestCase):
    def test_all_falling_and_loss_one_percent(self):
        self.assertIn('조기 손절', me.signal(100, 98.9, -2, -1, -1, C))

    def test_needs_every_timeframe_falling(self):
        self.assertIsNone(me.signal(100, 98, -1, -1, -1, C))
        self.assertIsNone(me.signal(100, 98, -3, 0, -1, C))
        self.assertIsNone(me.signal(100, 98, -3, -1, 1, C))
        self.assertIsNone(me.signal(100, 98, -3, -1, None, C))

    def test_small_loss_or_gain_does_not_sell(self):
        self.assertIsNone(me.signal(100, 99.5, -4, -3, -2, C))
        self.assertIsNone(me.signal(100, 101, -4, -3, -2, C))


class WatchCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.end = dt.datetime(2026, 11, 28, 1, 0, tzinfo=dt.timezone.utc)
        self.data = {5: [100, 99.5, 99, 98.5], 15: [100, 99], 60: [101, 100]}
        self.calls = []
        fetch = lambda m, u, count=12: (self.calls.append(u), bars(self.data[u], u, self.end))[1]
        self.w = me.Watch(self.tmp.name, fetch=fetch, clock=lambda: self.end.timestamp())
        self.pos = {'KRW-X': {'qty': 10, 'entry': 100, 'cost': 1000, 'stop': 96.5,
                              'time': (self.end - dt.timedelta(hours=2)).isoformat()}}
        self.sold = []

    def sell(self, m, why, px):
        self.sold.append((m, px)); return True, {'vol': 10, 'funds': 985, 'fee': 0.5, 'avg': 98.5}

    def test_fires_once_per_bar_and_logs(self):
        self.assertEqual(self.w.check(self.pos, C, lambda m: False, self.sell), ['KRW-X'])
        self.assertEqual(self.sold, [('KRW-X', 98.5)])
        self.w.check(self.pos, C, lambda m: False, self.sell)          # 같은 봉: 다시 안 함
        self.assertEqual(len(self.sold), 1)
        log = (Path(self.tmp.name) / 'momentum_exit.jsonl').read_text(encoding='utf-8')
        self.assertIn('"sold": true', log)

    def test_disabled_protected_inherited_skip(self):
        self.assertEqual(self.w.check(self.pos, dict(C, momentum_exit_enabled=False), lambda m: False, self.sell), [])
        self.assertEqual(self.w.check(self.pos, C, lambda m: True, self.sell), [])
        self.pos['KRW-X']['inherited'] = True
        self.assertEqual(self.w.check(self.pos, C, lambda m: False, self.sell), [])
        self.assertEqual(self.sold, [])

    def test_longer_frames_fetched_only_when_5m_qualifies(self):
        self.data[5] = [100, 99, 99.5, 99.4]   # 1연속 하락
        self.w.check(self.pos, C, lambda m: False, self.sell)
        self.assertEqual(self.calls, [5])
        self.assertEqual(self.sold, [])

    def test_bar_before_entry_is_ignored(self):
        self.pos['KRW-X']['time'] = (self.end - dt.timedelta(minutes=4)).isoformat()
        self.assertEqual(self.w.check(self.pos, C, lambda m: False, self.sell), [])


class Wiring(unittest.TestCase):
    def test_bot_and_live_use_rule(self):
        here = Path(__file__).parent
        self.assertIn('momentum_exit.Watch(DATA)', (here / 'bot.py').read_text(encoding='utf-8'))
        src = (here / 'live.py').read_text(encoding='utf-8')
        self.assertIn('def momentum_sell', src)
        self.assertIn("risk=True", src[src.index('def momentum_sell'):src.index('def sweep_dust')])


if __name__ == '__main__':
    unittest.main()
