"""사용자 직접 보유를 봇 장부로 넘기기(2026-11-28) 검사. 주문을 내지 않는지, 손실 기준·보호 해제·거절 경로."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from asset_protection import effective
import live
import manual_orders
import protection_panel


def acct(balance='1000', locked='0', avg='10'):
    return [{'currency': 'KRW', 'balance': '1000', 'locked': '0', 'avg_buy_price': '0'},
            {'currency': 'TFUEL', 'balance': balance, 'locked': locked, 'avg_buy_price': avg}]


class AdoptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'data').mkdir()
        (root / 'data/user_protected_assets.json').write_text('{"markets":[]}')
        self.w = live.Live(root / 'data/live', {'enabled': True, 'protection_ui': True}, live.st.Params())
        self.w.protection_policy.observe([{'uuid': 'u1', 'identifier': None, 'market': 'KRW-TFUEL', 'side': 'bid', 'state': 'done',
                                           'executed_volume': '1000', 'paid_fee': '0', 'created_at': '1970-01-01T00:00:00+00:00'}])
        self.assertIn('KRW-TFUEL', self.w.protected_markets())

    def run_adopt(self, rows=None, price=11.0, revision=None):
        rev = self.w.protection_policy.revision('KRW-TFUEL') if revision is None else revision
        with patch.object(self.w, 'scan_manual_orders'), \
             patch.object(live.upbit, 'accounts', return_value=rows or acct()), \
             patch.object(live.upbit, 'tickers', return_value={'KRW-TFUEL': {'trade_price': price}}), \
             patch.object(live.upbit, 'completed_candles', return_value=[]), \
             patch.object(live.st, 'wilder_atr', return_value=0.2), \
             patch.object(live.upbit, 'market_sell', side_effect=AssertionError('no order')), \
             patch.object(live.upbit, 'market_buy', side_effect=AssertionError('no order')):
            return self.w.adopt_user_holding('KRW-TFUEL', rev)

    def test_adopts_without_order_and_keeps_loss_flat(self):
        before = self.w.raw_loss({'KRW-TFUEL': 11.0})
        ok, msg = self.run_adopt()
        self.assertTrue(ok, msg)
        pos = self.w.d['positions']['KRW-TFUEL']
        self.assertEqual(pos['qty'], 1000.0); self.assertAlmostEqual(pos['cost'], 10000.0)
        self.assertTrue(pos['inherited'] and pos['stop_rebased'])
        self.assertLess(pos['stop'], 11.0); self.assertGreaterEqual(pos['stop'], 11.0 * (1 - self.w.p.stop_max_pct) - 1e-9)
        self.assertNotIn('KRW-TFUEL', self.w.protected_markets())
        self.assertAlmostEqual(self.w.raw_loss({'KRW-TFUEL': 11.0}), before, places=6)
        self.assertAlmostEqual(self.w.raw_loss({'KRW-TFUEL': 10.0}) - before, 1000 * 1.0 * (1 - 0.0005), places=6)

    def test_refuses_locked_existing_dust_and_stale_revision(self):
        ok, msg = self.run_adopt(acct(locked='5')); self.assertFalse(ok); self.assertIn('미체결', msg)
        ok, msg = self.run_adopt(acct(balance='100')); self.assertFalse(ok); self.assertIn('5,000', msg)
        self.assertNotIn('KRW-TFUEL', self.w.d['positions'])
        with self.assertRaises(Exception):
            self.run_adopt(revision=self.w.protection_policy.revision('KRW-TFUEL') + 5)
        self.assertNotIn('KRW-TFUEL', self.w.d['positions'])
        self.assertIn('KRW-TFUEL', self.w.protected_markets())
        ok, _ = self.run_adopt(); self.assertTrue(ok)
        ok, msg = self.run_adopt(); self.assertFalse(ok); self.assertIn('이미', msg)

    def test_endpoint_and_panel(self):
        srv = manual_orders.ManualOrderServer(8765, token='synthetic', allowed_kinds={'adopt'})
        code, _ = srv.check('127.0.0.1:8765', 'null', b'{"token":"synthetic","market":"KRW-TFUEL","confirm":true}', 'adopt')
        self.assertEqual(code, 400)
        code, val = srv.check('127.0.0.1:8765', 'null', b'{"token":"synthetic","market":"KRW-TFUEL","confirm":true,"revision":1}', 'adopt')
        self.assertEqual((code, val[0]), (200, 'KRW-TFUEL'))
        html = protection_panel.render(self.w, {'KRW-TFUEL': {}}, {'port': 1, 'token': 't'})
        self.assertIn('data-choice="adopt"', html)
        self.run_adopt()
        html = protection_panel.render(self.w, {'KRW-TFUEL': {}}, {'port': 1, 'token': 't'})
        self.assertNotIn('data-choice="adopt"', html)


if __name__ == '__main__':
    unittest.main()
