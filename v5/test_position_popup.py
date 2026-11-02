import json
import re
import unittest

import position_popup as pp

POS = {'qty': 100.0, 'cost': 10000.0, 'stop': 90.0, 'time': '2026-11-03T08:00:00+09:00',
       'peak': 120.0, 'atr': 3.5, 'trail': True, 'inherited': False}


class PositionPopupTest(unittest.TestCase):
    def test_detail_values(self):
        d = pp.detail('KRW-ABC', POS, 110.0, now=1793660000)
        self.assertEqual(d['name'], 'ABC')
        self.assertAlmostEqual(d['value'], 11000.0)
        self.assertAlmostEqual(d['pnl'], 1000.0)
        self.assertAlmostEqual(d['pnl_pct'], 10.0)
        self.assertAlmostEqual(d['avg'], 100.0)
        self.assertAlmostEqual(d['stop_gap_pct'], (110 / 90 - 1) * 100)
        self.assertTrue(d['trail'])
        self.assertFalse(d['inherited'])

    def test_missing_price_is_not_faked_as_zero(self):
        for price in (None, 0, -1, float('nan'), 'x'):
            d = pp.detail('KRW-ABC', POS, price)
            self.assertIsNone(d['price'])
            self.assertIsNone(d['value'])
            self.assertIsNone(d['pnl'])
            self.assertIsNone(d['stop_gap_pct'])
            self.assertEqual(d['cost'], 10000.0)

    def test_bad_position_fields_do_not_raise(self):
        d = pp.detail('KRW-ABC', {'qty': 'x', 'cost': None, 'time': 'not a date'}, 5.0)
        self.assertIsNone(d['qty'])
        self.assertIsNone(d['held_hours'])
        self.assertIsNone(d['avg'])

    def test_last_sell(self):
        d = pp.detail('KRW-ABC', POS, 110.0, {'time': 1000.0, 'price': 99.0, 'profit': False, 'reason': '손절'}, now=1600.0)
        self.assertAlmostEqual(d['last_sell']['ago_min'], 10.0)
        self.assertFalse(d['last_sell']['profit'])
        self.assertIsNone(pp.detail('KRW-ABC', POS, 1.0, {'time': 'x'})['last_sell'])

    def test_data_script_cannot_break_out_of_the_tag(self):
        pos = dict(POS, time='</script><script>alert(1)</script>')
        tag = pp.data_script({'KRW-A</script>': pos}, {})
        inner = re.fullmatch(r'<script type="application/json" id="posdata">(.*)</script>', tag, re.S).group(1)
        self.assertNotIn('<', inner)
        self.assertNotIn('>', inner)
        self.assertIn('KRW-A</script>', json.loads(inner))

    def test_row_attrs_escape_and_dust_class(self):
        self.assertIn('class="clk"', pp.row_attrs('KRW-A'))
        self.assertIn('class="clk dust"', pp.row_attrs('KRW-A', True))
        self.assertNotIn('"x"', pp.row_attrs('KRW-A"x"'))

    def test_modal_is_read_only(self):
        self.assertNotIn('fetch(', pp.MODAL)
        self.assertNotIn('XMLHttpRequest', pp.MODAL)
        self.assertNotIn('<form', pp.MODAL)
        self.assertNotIn('innerHTML', pp.MODAL)  # 값은 textContent로만 넣어 주입을 막는다
        self.assertIn('조회 전용', pp.MODAL)


if __name__ == '__main__':
    unittest.main()
