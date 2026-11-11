import tempfile
import unittest
from pathlib import Path

import lease_check as L


class Judge(unittest.TestCase):
    def test_this_pc_owner_ok(self):
        lv, msg = L.judge({'owner': 'x:1', 'pc': 'HOME', 'expires': 200}, True, True, 'h', pc='HOME', now=100)
        self.assertEqual(lv, 'ok'); self.assertIn('이 PC', msg)

    def test_other_pc_owner_is_normal_watching(self):
        lv, msg = L.judge({'owner': 'y:1', 'pc': 'WORK', 'expires': 200}, True, True, 'h', pc='HOME', now=100)
        self.assertEqual(lv, 'ok'); self.assertIn('WORK', msg)

    def test_free_lease_with_running_live_bot_is_bad(self):
        """2026-11-11 사고 재현: 봇은 켜져 있고 실거래 켜짐인데 실행권 비어 있음 + 체크섬 불일치."""
        lv, msg = L.judge({'owner': '', 'pc': 'HOME', 'expires': 0, 'checkpoint': 'old'}, True, True, 'new', pc='HOME', now=100)
        self.assertEqual(lv, 'bad'); self.assertIn('체크섬', msg); self.assertIn('주문', msg)

    def test_expired_owner_counts_as_free(self):
        lv, _ = L.judge({'owner': 'x:1', 'pc': 'HOME', 'expires': 50}, True, True, 'h', pc='HOME', now=100)
        self.assertEqual(lv, 'bad')

    def test_free_and_bot_off_is_warn(self):
        lv, msg = L.judge({'owner': '', 'pc': 'HOME', 'checkpoint': 'h'}, True, False, 'h', pc='HOME', now=100)
        self.assertEqual(lv, 'warn'); self.assertNotIn('체크섬', msg)

    def test_report_without_session_guard_does_not_touch_network(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(L.report(Path(d), True, True)[0], 'warn')


if __name__ == '__main__':
    unittest.main()
