import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import deploy_v5 as dp
import sync_v5 as sy


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.src, self.app = root / 'src', root / 'app'
        for d in (self.src, self.app):
            d.mkdir()
        (self.src / 'bot.py').write_text('NEW = 1\n', encoding='utf-8')
        (self.src / 'test_ok.py').write_text('import unittest\nclass T(unittest.TestCase):\n    def test(self):\n        self.assertTrue(True)\n', encoding='utf-8')
        (self.app / 'bot.py').write_text('OLD = 1\n', encoding='utf-8')
        (self.app / 'config.json').write_text('{"live": {"enabled": true}}', encoding='utf-8')
        for p in (mock.patch.object(sy, 'SRC', self.src), mock.patch.object(sy, 'DST', self.app),
                  mock.patch.object(dp, 'SRC', self.src), mock.patch.object(dp, 'APP', self.app),
                  mock.patch.object(dp, 'STOP_TIMEOUT', 0.2), mock.patch.object(dp.time, 'sleep', lambda s: None)):
            p.start()
            self.addCleanup(p.stop)

    def code(self, f='bot.py'):
        return (self.app / f).read_text(encoding='utf-8')


class Parts(Fixture):
    def test_pending_orders_detected(self):
        live = self.app / 'data' / 'live'
        live.mkdir(parents=True)
        (live / 'live.json').write_text(json.dumps({'pending_order': None, 'risk_pending_orders': {}}), encoding='utf-8')
        self.assertEqual(dp.pending_orders(self.app), '')
        (live / 'live.json').write_text(json.dumps({'pending_order': {'id': 1}}), encoding='utf-8')
        self.assertIn('미확정', dp.pending_orders(self.app))
        (live / 'live.json').write_text(json.dumps({'risk_pending_orders': {'KRW-X': {}}}), encoding='utf-8')
        self.assertIn('청산', dp.pending_orders(self.app))

    def test_backup_and_restore(self):
        bk = dp.backup(self.app)
        (self.app / 'bot.py').write_text('BROKEN\n', encoding='utf-8')
        dp.restore(self.app, bk)
        self.assertEqual(self.code(), 'OLD = 1\n')

    def test_approval_requires_exact_head(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(dp, 'APPROVAL', Path(t) / 'A'):
            self.assertFalse(dp.approved('abc'))
            (Path(t) / 'A').write_text('abc  # 승인\n', encoding='utf-8')
            self.assertTrue(dp.approved('abc'))
            self.assertFalse(dp.approved('abd'))


class Deploy(Fixture):
    def test_stopped_bot_gets_code_only_and_config_untouched(self):
        self.assertEqual(dp.deploy('t', self.app, self.src), 0)
        self.assertEqual(self.code(), 'NEW = 1\n')
        self.assertIn('enabled', self.code('config.json'))

    def test_pending_order_blocks_deploy(self):
        with mock.patch.object(dp, 'pending_orders', return_value='미확정 주문'):
            self.assertEqual(dp.deploy('t', self.app, self.src), 1)
        self.assertEqual(self.code(), 'OLD = 1\n')

    def test_failing_tests_block_deploy(self):
        (self.src / 'test_bad.py').write_text('import unittest\nclass T(unittest.TestCase):\n    def test(self):\n        self.fail("x")\n', encoding='utf-8')
        self.assertEqual(dp.deploy('t', self.app, self.src), 1)
        self.assertEqual(self.code(), 'OLD = 1\n')

    def test_graceful_stop_then_apply_then_start(self):
        states = iter([['bot.lock'], ['bot.lock'], []])
        calls = []
        with mock.patch.object(dp, 'any_alive', lambda app: next(states, [])), \
                mock.patch.object(dp, 'start_bot', lambda launch=None: calls.append('start') or (True, '')):
            self.assertEqual(dp.deploy('t', self.app, self.src), 0)
        self.assertEqual(calls, ['start'])
        self.assertEqual(self.code(), 'NEW = 1\n')
        self.assertFalse((self.app / 'stop.txt').exists())          # 재시작 전에 정지 요청 파일 제거

    def test_stop_timeout_aborts_without_killing_and_cancels_request(self):
        with mock.patch.object(dp, 'any_alive', lambda app: ['bot.lock']):
            self.assertEqual(dp.deploy('t', self.app, self.src), 1)
        self.assertEqual(self.code(), 'OLD = 1\n')
        self.assertFalse((self.app / 'stop.txt').exists())

    def test_failed_start_rolls_back(self):
        states = iter([['bot.lock'], []])
        results = iter([(False, 'boom'), (True, '')])
        with mock.patch.object(dp, 'any_alive', lambda app: next(states, [])), \
                mock.patch.object(dp, 'start_bot', lambda launch=None: next(results)):
            self.assertEqual(dp.deploy('t', self.app, self.src), 2)
        self.assertEqual(self.code(), 'OLD = 1\n')                  # 백업으로 복원


class AgreedSync(unittest.TestCase):
    def test_patch_updates_agreed_file_without_pc_values(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'agreed.json'
            f.write_text(json.dumps({'_note': 'n', 'strategy': {'slots': 3}, 'live': {'x': 0}}), encoding='utf-8')
            dp.sync_agreed({'strategy': {'slots': 2}, 'live': {'enabled': True, 'x': 1}, 'session': {'id': 'z'}}, f)
            got = json.loads(f.read_text(encoding='utf-8'))
            self.assertEqual(got, {'_note': 'n', 'strategy': {'slots': 2}, 'live': {'x': 1}})


if __name__ == '__main__':
    unittest.main()


class RemoteApproval(unittest.TestCase):
    """봇 고정 PC 운영: 다른 PC에서 승인 파일을 커밋해도 v5 내용이 같으면 승인."""

    def test_same_v5_tree_counts_as_approved_and_patches_once(self):
        import json
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / 'tools' / 'patches').mkdir(parents=True)
            (root / 'tools' / 'patches' / 'p.json').write_text(json.dumps({'live': {'x': 1}}), encoding='utf-8')
            app = root / 'app'
            (app / 'data').mkdir(parents=True)
            (root / 'A').write_text('c1\ntools/patches/p.json\n', encoding='utf-8')
            trees = {'c1': 'T', 'c2': 'T', 'c3': 'U'}
            with mock.patch.object(dp, 'APPROVAL', root / 'A'), mock.patch.object(dp, 'ROOT', root), \
                    mock.patch.object(dp, 'v5_tree', side_effect=lambda r: trees.get(r)):
                self.assertTrue(dp.approved('c2'))        # 승인 파일만 바뀐 커밋
                self.assertFalse(dp.approved('c3'))       # v5 코드가 더 바뀐 커밋
                commit, patch = dp.pending_patches(app)
                self.assertEqual((commit, patch), ('c1', {'live': {'x': 1}}))
                (app / 'data' / 'deploy_applied.txt').write_text('c1', encoding='utf-8')
                self.assertEqual(dp.pending_patches(app), ('c1', None))
