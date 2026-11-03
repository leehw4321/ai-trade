import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import types
import os

sys.path.insert(0, str(Path(__file__).resolve().parent))
spec = importlib.util.spec_from_file_location('handoff_cli_test_target', Path(__file__).with_name('handoff_cli.py'))
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class TransitionTests(unittest.TestCase):
    def source(self, root, pending=None):
        import json
        app = Path(root) / 'app'; (app / 'data/live').mkdir(parents=True)
        (app / 'data/live/live.json').write_text(json.dumps({'pending_order': pending, 'risk_pending_orders': {}}))
        return app

    def test_pending_order_refuses_stop_before_writing_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            app = self.source(folder, pending={'uncertain': True})
            with self.assertRaises(cli.sh.HandoffError):
                cli.request_graceful_stop(app, checker=lambda p: self.fail('must not query after pending failure'))
            self.assertFalse((app / 'stop.txt').exists())
            self.assertFalse((app / 'stop_shadow.txt').exists())

    def test_stop_timeout_keeps_requests_without_force_kill(self):
        with tempfile.TemporaryDirectory() as folder:
            app = self.source(folder)
            ticks = iter([0, 0, 181])
            with patch.object(cli.subprocess, 'run', side_effect=AssertionError('no external kill or lease command')):
                with self.assertRaises(cli.sh.HandoffError):
                    cli.request_graceful_stop(app, checker=lambda p: True, clock=lambda: next(ticks), sleep=lambda s: None)
            self.assertTrue((app / 'stop.txt').exists())
            self.assertTrue((app / 'stop_shadow.txt').exists())

    def test_publish_stops_source_before_transition_and_sealing(self):
        import contextlib
        with tempfile.TemporaryDirectory() as folder:
            app = self.source(folder); root = Path(folder) / 'private_handoff'; root.mkdir()
            args = types.SimpleNamespace(publish=True, stop_source=True, stop_timeout=180, app=app,
                package=Path(folder) / 'final.zip', recipient_cert='public.pem', recipient_pin='pin',
                remote='https://github.com/example/repo.git', credentials_file=None, openssl='openssl')
            calls = []
            original = cli.request_graceful_stop
            def stop(*a, **k):
                calls.append('stop')
                return original(app, checker=lambda p: False)
            @contextlib.contextmanager
            def lock(path):
                calls.append('lock'); yield
            def seal(*a, **k): calls.append('seal'); return {'pushed': True}
            with patch.object(cli, 'request_graceful_stop', side_effect=stop), patch.object(cli, 'transition_lock', lock), \
                    patch.object(cli, 'observer', return_value=lambda: {}), patch.object(cli.sh, 'seal_and_transmit', side_effect=seal):
                result = cli.publish_final(args, root)
            self.assertEqual(calls, ['stop', 'lock', 'seal'])
            self.assertTrue(result['source_stopped'])
            self.assertTrue((app / 'stop.txt').exists())

    def test_dryrun_stop_flag_does_not_request_shutdown(self):
        args = types.SimpleNamespace(publish=False, stop_source=True, package=None)
        with patch.object(cli, 'request_graceful_stop', side_effect=AssertionError('dryrun must not stop')):
            result = cli.publish_final(args, Path('unused'))
        self.assertFalse(result['publish'])
        self.assertFalse(result['stop_source_executed'])

    def test_observer_reads_remote_lease_last_and_uses_start_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder); (app / 'data').mkdir(); (app / 'config.json').write_text('{"session":{}}')
            (app / 'data/handoff-transition.lock').write_text(str(os.getpid()))
            calls = []
            api = types.SimpleNamespace(ENV_FILES=[], _auth=lambda params: {},
                _get=lambda *args, **kwargs: calls.append('orders') or [],
                accounts=lambda: calls.append('account') or [{'currency': 'A', 'balance': '0.10000001', 'locked': '0.20000002'}])
            guard = types.SimpleNamespace(DEFAULT_REMOTE='https://github.com/example/repo.git',
                GitStore=lambda *args: types.SimpleNamespace(read=lambda: (calls.append('lease') or '', {'checkpoint': 'checkpoint'})))
            with patch.object(cli, '_load', side_effect=[api, guard]), patch.object(cli, '_alive_from_lock', return_value=False), \
                    patch.object(cli.time, 'time', side_effect=[1000, 1005]):
                result = cli.observer(app)()
            self.assertEqual(calls[-1], 'lease')
            self.assertEqual(result['checked_at_epoch'], 1000)
            self.assertEqual(result['account_quantities']['KRW-A'], '0.30000003')

    def test_observer_rejects_slow_readonly_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder); (app / 'config.json').write_text('{"session":{}}')
            api = types.SimpleNamespace(ENV_FILES=[], _auth=lambda params: {}, _get=lambda *a, **k: [], accounts=lambda: [])
            guard = types.SimpleNamespace(DEFAULT_REMOTE='remote', GitStore=lambda *a: types.SimpleNamespace(read=lambda: ('', {})))
            with patch.object(cli, '_load', side_effect=[api, guard]), patch.object(cli.time, 'time', side_effect=[1000, 1031]):
                with self.assertRaises(cli.sh.HandoffError): cli.observer(app)()

    def test_successful_verified_host_operation_clears_transition_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder)
            with cli.transition_lock(app):
                self.assertTrue((app / 'data/handoff-transition.lock').exists())
            self.assertFalse((app / 'data/handoff-transition.lock').exists())

    def test_failure_keeps_marker_for_guarded_manual_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder)
            with self.assertRaises(RuntimeError):
                with cli.transition_lock(app):
                    raise RuntimeError('do not start after partial transfer')
            self.assertTrue((app / 'data/handoff-transition.lock').exists())
            with self.assertRaises(FileExistsError):
                with cli.transition_lock(app): pass

    def test_recipient_names_cannot_escape_identity_root(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(cli.sh.HandoffError):
                cli.init_recipient(Path(folder), '../other')


if __name__ == '__main__': unittest.main()
