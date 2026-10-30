import unittest
from research_v4 import adjust_plan, trend_assessment

class ResearchV4Tests(unittest.TestCase):
    def test_shrink_and_no_upscale(self):
        p = dict(amount=100000, atr=1, stop=98, cost_rate=.002)
        self.assertEqual(adjust_plan(p, 100)['amount'], 50000)
        self.assertEqual(p['amount'], 100000)
        self.assertEqual(adjust_plan(p | {'atr': .1}, 100)['amount'], 100000)
        for values in ({'cost_rate': .02}, {'amount': 5000}, {'atr': float('nan')}, {'stop': 101}):
            with self.assertRaises(ValueError):
                adjust_plan(p | values, 100)

    def test_closed_trend(self):
        rows = [dict(t=i*86400, c=100+i, h=101+i, l=99+i, v=10) for i in range(200)]
        self.assertTrue(trend_assessment(rows, 1440, 200*86400)['trend_ok'])
        with self.assertRaises(ValueError):
            trend_assessment(rows, 1440, 199*86400)
        rows[-1]['c'] = rows[-1]['l'] = 50
        self.assertFalse(trend_assessment(rows, 1440, 200*86400)['trend_ok'])
