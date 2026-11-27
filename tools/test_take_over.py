import io
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import take_over as t

_SAFE = tempfile.TemporaryDirectory()


def setUpModule():
    """모든 테스트에서 실제 운용 폴더(v5-app/v5)를 가린다. 2026-11-27 테스트가 운용 shadow에 정지 파일을 써 세 번 멈췄다."""
    app = Path(_SAFE.name) / 'app'
    (app / 'data').mkdir(parents=True, exist_ok=True)
    for name, val in (('APP', app), ('LOG', app / 'data' / 'takeover.log')):
        p = mock.patch.object(t, name, val); p.start()
        unittest.addModuleCleanup(p.stop)
    unittest.addModuleCleanup(_SAFE.cleanup)


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


class ShowUsesChrome(unittest.TestCase):
    """2026-11-16: .html 기본 연결이 IE/엣지인 PC에서 os.startfile로 열어 크롬이 아닌 창이 떴다."""
    def test_show_goes_through_open_page_not_startfile(self):
        fake = mock.Mock()
        with mock.patch.dict(sys.modules, {'open_page': mock.Mock(open_page=fake)}), \
                mock.patch.object(t.os, 'startfile', create=True) as sf:
            t.show(Path('x/status.html'))
        fake.assert_called_once_with(Path('x/status.html'))
        sf.assert_not_called()

    def test_no_direct_startfile_for_pages(self):
        for name in ('take_over.py', 'open_observer.py'):
            src = (Path(t.__file__).resolve().parent / name).read_text(encoding='utf-8')
            self.assertIn('open_page', src)


class StartBotPassesKeys(unittest.TestCase):
    """2026-11-16: start_bot이 키 환경을 넘기지 않아 봇이 키 없이 실행권을 잡고 주문이 13분간 실패했다."""
    def test_every_process_gets_key_env(self):
        env = {'UPBIT_ACCESS_KEY': 'a', 'UPBIT_SECRET_KEY': 'b'}
        svc = mock.Mock()
        for name in ('bot.py', 'shadow.py', 'account_sync.py', 'exit_study.py', 'trade_history.py', 'pnl.py'):
            (t.APP / name).write_text('', encoding='utf-8')   # 가짜 실행본(실제 운용 폴더 아님)
        with mock.patch.object(t, 'key_env', return_value=env), mock.patch.object(t.subprocess, 'Popen') as popen, \
                mock.patch.object(t, 'bot_running', return_value=True), mock.patch.object(Path, 'unlink'), \
                mock.patch.object(t, 'stop_shadow') as stop, mock.patch.dict(sys.modules, {'pnl_service': svc}):
            self.assertTrue(t.start_bot())
        stop.assert_called_once()
        self.assertTrue(popen.call_args_list)
        for call in popen.call_args_list:
            self.assertIs(call.kwargs.get('env'), env)
        self.assertIs(svc.ensure_running.call_args.kwargs.get('env'), env)
        self.assertNotIn('pnl.py', [c.args[0][1] for c in popen.call_args_list])  # 중복 방지: pnl은 ensure_running으로만


class StartBotRestartsShadow(unittest.TestCase):
    """2026-11-26 21:52~11-27 08:52: 옛 설정으로 남은 shadow 때문에 주문안이 모두 거절됐다. 봇을 켤 때 shadow를 먼저 정상 종료한다.
    실제 운용 폴더를 건드리지 않도록 APP을 임시 폴더로 바꾼다(2026-11-27 테스트가 운용 shadow를 두 번 멈춘 일)."""
    def test_stop_shadow_writes_stop_file_when_running(self):
        with tempfile.TemporaryDirectory() as d:
            app = Path(d)
            states = iter([True, True, False])
            with mock.patch.object(t, 'APP', app), mock.patch.object(t, 'bot_running', side_effect=lambda n='bot.lock': next(states)), \
                    mock.patch.object(t.time, 'sleep'):
                self.assertTrue(t.stop_shadow())
            self.assertTrue((app / 'stop_shadow.txt').exists())

    def test_stop_shadow_noop_when_not_running(self):
        with tempfile.TemporaryDirectory() as d:
            app = Path(d)
            with mock.patch.object(t, 'APP', app), mock.patch.object(t, 'bot_running', return_value=False):
                self.assertTrue(t.stop_shadow())
            self.assertFalse((app / 'stop_shadow.txt').exists())


class HelpersStarted(unittest.TestCase):
    """2026-11-18: 배포 뒤 계좌 동기화·체결 기록 수집이 다시 켜지지 않아 관찰창·계좌 화면이 멈췄다."""
    def test_take_over_starts_trade_history(self):
        env = {}
        with mock.patch.object(t, 'key_env', return_value=env), mock.patch.object(t.subprocess, 'Popen') as popen,                 mock.patch.object(t, 'bot_running', return_value=True), mock.patch.object(Path, 'unlink'),                 mock.patch.object(Path, 'exists', return_value=True):
            t.start_bot()
        scripts = [c.args[0][1] for c in popen.call_args_list]
        self.assertIn('account_sync.py', scripts)
        self.assertIn('trade_history.py', scripts)

    def test_launchers_start_helpers(self):
        here = Path(t.__file__).resolve().parent
        for f in (here / 'launch_v5.py', here.parents[1] / '코인 트레이딩' / 'launch_v5.py'):
            if f.exists():
                s = f.read_text(encoding='utf-8')
                self.assertIn("account_sync.py", s, f)
                self.assertIn("'trade_history.py'", s, f)


class RunLock(unittest.TestCase):
    """2026-11-27 11:51: 실행권 받기가 두 개 동시에 돌아 서로의 양도 요청을 덮어쓰고 둘 다 멈췄다 → 한 번에 하나만."""
    def test_second_takeover_is_ignored_while_first_runs(self):
        first = t.run_lock()
        try:
            self.assertIsNotNone(first)
            self.assertIsNone(t.run_lock())
            said = []
            with mock.patch.object(t, 'say', said.append), \
                    mock.patch.object(t, 'update_code', side_effect=AssertionError('must not run')), \
                    mock.patch.object(sys, 'argv', ['x', '--button']):
                t.main()
            self.assertIn('이미 실행권 받기가 진행 중', said[0])
        finally:
            first.close()
        again = t.run_lock(); self.assertIsNotNone(again); again.close()

if __name__ == '__main__':
    unittest.main()
