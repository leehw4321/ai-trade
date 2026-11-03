import io
import sys
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import take_over as t


def plan(lease, local):
    out = io.StringIO()
    with mock.patch.object(t, 'bot_running', return_value=False), mock.patch.object(t, 'lease', return_value=lease), \
            mock.patch.object(t, 'local_hash', return_value=local), mock.patch.object(sys, 'argv', ['x', '--check']), \
            redirect_stdout(out):
        t.main()
    return out.getvalue()


class Plan(unittest.TestCase):
    def test_other_pc_active_means_handover_then_adoption(self):
        s = plan({'owner': 'a', 'expires': time.time() + 100, 'checkpoint': 'h-other'}, 'h-mine')
        self.assertIn('양도 요청', s)
        self.assertIn('새 기준으로 인수', s)

    def test_same_pc_restart_keeps_ledger(self):
        s = plan({'owner': '', 'checkpoint': 'h'}, 'h')
        self.assertIn('다른 PC 실행 없음', s)
        self.assertIn('그대로 이어감', s)

    def test_expired_owner_is_not_treated_as_active(self):
        s = plan({'owner': 'a', 'expires': time.time() - 100, 'checkpoint': 'x'}, 'y')
        self.assertIn('다른 PC 실행 없음', s)


class Config(unittest.TestCase):
    def test_patches_applied_and_enable_flag(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as d:
            app = Path(d)
            (app / 'config.json').write_text(json.dumps({'live': {'enabled': True, 'max_order_krw': 30000}, 'strategy': {}}), encoding='utf-8')
            with mock.patch.object(t, 'APP', app):
                cfg = t.align_config(enabled=False)
            self.assertFalse(cfg['live']['enabled'])
            self.assertEqual(cfg['live']['max_order_krw'], 1_000_000_000)
            self.assertEqual(cfg['strategy']['stop_max_pct'], 0.045)


if __name__ == '__main__':
    unittest.main()
