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
    """2026-11-10: 실행권 받기가 patches 조각을 이름순으로 덮어써 사용자 결정 7개가 뒤집힌 사고 재발 방지."""

    def setUp(self):
        import json, tempfile
        self.json = json
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.app, self.agreed = d / 'app', d / 'agreed.json'
        self.app.mkdir()
        self.agreed.write_text(json.dumps({'_note': 'x', 'allocation_fraction': 0.1,
                                           'strategy': {'regime_filter': False, 'slots': 3}, 'live': {'breakeven_lock_pct': 0}}), encoding='utf-8')
        for obj, name, val in ((t, 'APP', self.app), (t, 'AGREED', self.agreed), (t, 'say', lambda m: None)):
            p = mock.patch.object(obj, name, val); p.start(); self.addCleanup(p.stop)

    def write(self, cfg):
        (self.app / 'config.json').write_text(self.json.dumps(cfg), encoding='utf-8')

    def read(self):
        return self.json.loads((self.app / 'config.json').read_text(encoding='utf-8'))

    def test_same_as_agreed_only_sets_enable_flag_and_keeps_session(self):
        self.write({'allocation_fraction': 0.1, 'strategy': {'regime_filter': False, 'slots': 3, 'tf': 15},
                    'live': {'enabled': True, 'breakeven_lock_pct': 0}, 'session': {'account_adoption_id': 'a'}})
        cfg = t.align_config(enabled=False)
        self.assertFalse(cfg['live']['enabled'])
        self.assertEqual(cfg['session'], {'account_adoption_id': 'a'})
        self.assertEqual(cfg['strategy']['tf'], 15)

    def test_patches_folder_is_no_longer_applied(self):
        self.write({'allocation_fraction': 0.1, 'strategy': {'regime_filter': False, 'slots': 3}, 'live': {'breakeven_lock_pct': 0}})
        cfg = t.align_config()
        self.assertFalse(cfg['strategy']['regime_filter'])   # regime_keep_no_expiry.json(켜기)이 이기지 않는다
        self.assertEqual(cfg['strategy']['slots'], 3)          # slots_4.json이 이기지 않는다

    def test_mismatch_stops_without_touching_config(self):
        before = {'allocation_fraction': 0.1, 'strategy': {'regime_filter': True, 'slots': 4}, 'live': {'breakeven_lock_pct': 0}}
        self.write(before)
        with self.assertRaises(t.ConfigMismatch) as e:
            t.align_config()
        self.assertIn('strategy.regime_filter', str(e.exception))
        self.assertEqual(self.read(), before)
        self.assertEqual([k for k, _, _ in t.config_diff()], ['strategy.regime_filter', 'strategy.slots'])

    def test_accept_applies_agreed_values(self):
        self.write({'allocation_fraction': 0.1, 'strategy': {'regime_filter': True, 'slots': 4}, 'live': {'enabled': True}})
        cfg = t.align_config(accept=True)
        self.assertEqual((cfg['strategy']['regime_filter'], cfg['strategy']['slots'], cfg['live']['breakeven_lock_pct']), (False, 3, 0))
        self.assertTrue(cfg['live']['enabled'])

    def test_button_refuses_before_stopping_local_bot(self):
        self.write({'allocation_fraction': 0.1, 'strategy': {'regime_filter': True, 'slots': 3}, 'live': {'breakeven_lock_pct': 0}})
        grants = []
        with mock.patch.object(t, 'stop_local_bot', side_effect=AssertionError('must not stop')),                 mock.patch.object(t, 'set_grant', lambda *a: grants.append(a)), mock.patch.object(sys, 'argv', ['x', '--button']):
            t.main()
        self.assertEqual(grants[-1][0], 'failed')
        self.assertIn('regime_filter', grants[-1][1])


class RealAgreedFile(unittest.TestCase):
    def test_repo_agreed_file_matches_user_choices_20261110(self):
        a = t.agreed()
        s, l = a['strategy'], a['live']
        self.assertEqual((s['regime_filter'], s['slots'], s['entry_want'], s['bot_capital_divisor'], s['swap_stuck_loss']), (False, 3, 3, 0, False))
        self.assertEqual((l['breakeven_lock_pct'], l['stagnation_exit_enabled']), (0, False))
        self.assertNotIn('enabled', l)


if __name__ == '__main__':
    unittest.main()
