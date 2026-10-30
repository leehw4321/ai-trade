import unittest
from unittest.mock import patch

import morning_analysis as h
import paper_event as p
from replay_morning import Replay, Tape


class ReplayChecks(unittest.TestCase):
    def setUp(self):
        self.config = h.bot.env_cfg()
        self.config.update(stop=.02, trail_on=.03, trail_gap=.01)

    def test_missing_historical_snapshot_is_explicit(self):
        with patch('replay_morning.CFG', None), self.assertRaises(FileNotFoundError):
            Replay({}, [], None)

    def test_no_current_or_future_second_used(self):
        tape = Tape.__new__(Tape)
        tape.times = {'KRW-X': [h.T0-1, h.T0, h.T0+1]}
        tape.rows = {'KRW-X': [dict(trade_price=v, high_price=v) for v in (100, 110, 999)]}
        self.assertEqual(tape.quote('KRW-X', h.T0), 100)
        self.assertEqual(tape.quote('KRW-X', h.T0+1), 110)
        self.assertEqual(tape.high('KRW-X', h.T0-1, h.T0+1), 110)

    def test_order_mutations_are_blocked(self):
        api = h.ReadOnlyAccount.__new__(h.ReadOnlyAccount)
        for method, path in [('POST','/v1/orders'), ('DELETE','/v1/order'), ('GET','/v1/accounts')]:
            with self.assertRaisesRegex(RuntimeError, 'Read-only'):
                api.request(method,path)

    def test_partial_exit_accounting(self):
        data={'KRW-X':{i:[100,100,100,100,1] for i in range(-10,0)}}
        replay=Replay(data,['KRW-X'],None,config=self.config)
        port=p.Port('test','auto')
        port.used, port.diagnostics=set(),[]
        replay.now=h.T0+2
        replay.open_pos(port,'KRW-X',100,100,1,{})
        replay.now+=10
        replay.close_part(port,'KRW-X',110,'partial',.7)
        self.assertAlmostEqual(port.pos['KRW-X']['remaining'],.3)
        replay.now+=10
        replay.close_part(port,'KRW-X',120,'final',.3)
        expected=round(250000*((.7*110+.3*120)*.9995/(100*1.0005)-1))
        self.assertEqual(port.trades[0]['net_krw'],expected)
        self.assertFalse(port.pos)

    def test_current_bot_uses_trade_price_for_stop_then_bid_for_fill(self):
        class FakeTape:
            price=100
            def quote(self,market,now):
                return self.price
            def high(self,market,start,now):
                return 100
        tape=FakeTape()
        replay=Replay({'KRW-X':{i:[100,100,100,100,1] for i in range(-10,0)}},['KRW-X'],tape,spread=.006,config=self.config)
        port=p.Port('current','event')
        port.used,port.diagnostics=set(),[]
        replay.ports=[port]
        replay.now=h.T0+2
        replay.open_pos(port,'KRW-X',102,99.7,1,{})
        replay.now=h.T0+6
        replay.exits()
        self.assertIn('KRW-X',port.pos)  # Last trade is above -2%; bid being lower cannot trigger the stop.
        tape.price=99.8
        replay.now=h.T0+9
        replay.exits()
        self.assertFalse(port.pos)
        self.assertAlmostEqual(port.trades[0]['exit'],99.8*.997)


if __name__ == '__main__':
    unittest.main()
