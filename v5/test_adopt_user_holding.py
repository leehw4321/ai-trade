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

    def today(self, px):
        return self.w.raw_loss({'KRW-TFUEL': px}) - self.w.d.get('risk_loss_offset', 0.)

    def test_adopt_keeps_user_protection_then_separate_handover_keeps_loss_flat(self):
        before = self.today(11.0)
        ok, msg = self.run_adopt()
        self.assertTrue(ok, msg); self.assertIn('보호 유지', msg)
        pos = self.w.d['positions']['KRW-TFUEL']
        self.assertEqual(pos['qty'], 1000.0); self.assertAlmostEqual(pos['cost'], 10000.0)
        self.assertTrue(pos['inherited'] and pos['stop_rebased'])
        self.assertLess(pos['stop'], 11.0); self.assertGreaterEqual(pos['stop'], 11.0 * (1 - self.w.p.stop_max_pct) - 1e-9)
        # 사용자 지시: 장부에 올려도 거래 주도권은 사용자. 보호 유지 → 봇 주문 금지
        self.assertIn('KRW-TFUEL', self.w.protected_markets())
        with self.assertRaises(live.ProtectedAssetError): self.w.assert_unprotected('KRW-TFUEL')
        self.assertAlmostEqual(self.today(11.0), before, places=6)
        # 매수·매도 권한은 별도 보호 해제로만. 넘기는 순간 손실 집계는 그대로, 이후 변동만 쌓인다.
        rev = self.w.protection_policy.revision('KRW-TFUEL')
        with patch.object(self.w, 'scan_manual_orders'), \
             patch.object(live.upbit, 'tickers', return_value={'KRW-TFUEL': {'trade_price': 11.0}}):
            ok, msg = self.w.set_user_protection('KRW-TFUEL', False, rev)
        self.assertTrue(ok); self.assertNotIn('KRW-TFUEL', self.w.protected_markets())
        self.assertAlmostEqual(self.today(11.0), before, places=6)
        self.assertAlmostEqual(self.today(10.0) - before, 1000 * 1.0 * (1 - 0.0005), places=6)

    def test_refuses_locked_existing_dust_and_stale_revision(self):
        ok, msg = self.run_adopt(acct(locked='5')); self.assertFalse(ok); self.assertIn('미체결', msg)
        ok, msg = self.run_adopt(acct(balance='100')); self.assertFalse(ok); self.assertIn('5,000', msg)
        self.assertNotIn('KRW-TFUEL', self.w.d['positions'])
        ok, msg = self.run_adopt(revision=self.w.protection_policy.revision('KRW-TFUEL') + 5)
        self.assertFalse(ok); self.assertIn('새로고침', msg)
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


class PopupTests(unittest.TestCase):
    def test_outside_holding_gets_adopt_data_and_ledger_wins(self):
        import json, re, position_popup
        out = {'KRW-TFUEL': position_popup.outside_detail('KRW-TFUEL', {'qty': 1000, 'cost': 9930, 'price': 9.85, 'value': 9850}, True, 3),
               'KRW-BTC': position_popup.outside_detail('KRW-BTC', {'qty': 1, 'cost': 1, 'price': 1, 'value': 1}, True, 1)}
        tag = position_popup.data_script({'KRW-BTC': {'qty': 1, 'cost': 1, 'stop': 0.9, 'time': '2026-11-28T00:00:00'}}, {'KRW-BTC': 1.0},
                                         action={'port': 1, 'token': 't'}, outside=out)
        d = json.loads(re.search(r'id="posdata">(.*?)</script>', tag).group(1))
        self.assertTrue(d['KRW-TFUEL']['outside']); self.assertEqual(d['KRW-TFUEL']['revision'], 3)
        self.assertTrue(d['KRW-TFUEL']['sellable']); self.assertNotIn('outside', d['KRW-BTC'])
        self.assertIn('pm-adopt-btn', position_popup.MODAL); self.assertIn("'/adopt'", position_popup.MODAL)


class ScriptQuoteTests(unittest.TestCase):
    """2026-11-28 사고 재발 방지: 확인 문구의 줄바꿈이 실제 개행으로 들어가 화면 스크립트 전체가 멈췄다."""
    def test_no_single_quoted_string_spans_lines(self):
        import re, position_popup, protection_panel
        for name, js in (('panel', protection_panel.SCRIPT),
                         ('popup', re.search(r'<script>(.*)</script>', position_popup.MODAL, re.S).group(1))):
            for i, line in enumerate(js.split('\n'), 1):
                self.assertEqual(len(re.findall(r"(?<!\\)'", line)) % 2, 0, f'{name} {i}행 따옴표가 줄을 넘김: {line[:80]}')


if __name__ == '__main__':
    unittest.main()
