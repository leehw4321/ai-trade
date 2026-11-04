import json
from pathlib import Path
import subprocess
import tempfile
import traceback
import unittest
from unittest.mock import patch

from session_guard import GitStore, SessionLost


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GitStore.__new__(GitStore)
        self.store.folder = Path(self.temp.name) / 'session_git'
        self.store.remote = 'https://example.test/repo'
        self.store.folder.mkdir()
        self.log = self.store.folder.parent / 'session_diagnostics.jsonl'

    def record(self):
        return json.loads(self.log.read_text(encoding='utf-8').strip())

    def test_auth_failure_records_only_fixed_metadata(self):
        raw = b'Authentication failed for https://user:private-password@example.test/repo'
        result = subprocess.CompletedProcess([], 128, b'', raw)
        with patch('session_guard.subprocess.run', return_value=result):
            with self.assertRaisesRegex(SessionLost, 'authentication_or_permission'):
                self.store.git('fetch', 'https://private.example/token', input='private-input')
        row = self.record()
        self.assertEqual(set(row), {'epoch', 'operation', 'category', 'duration_ms', 'returncode'})
        self.assertEqual((row['operation'], row['category'], row['returncode']),
                         ('fetch', 'authentication_or_permission', 128))
        text = self.log.read_text(encoding='utf-8')
        for forbidden in ('private-password', 'private-input', 'private.example', 'user:', 'stderr'):
            self.assertNotIn(forbidden, text)

    def test_failed_cas_command_still_returns_nonzero_without_raising(self):
        result = subprocess.CompletedProcess([], 1, b'', b'! [rejected] HEAD (stale info)')
        with patch('session_guard.subprocess.run', return_value=result):
            self.assertEqual(self.store.git('push', '--force-with-lease=private', check=False).returncode, 1)
        self.assertEqual(self.record()['category'], 'reference_conflict')

    def test_timeout_is_recorded_and_still_blocks(self):
        with patch('session_guard.subprocess.run', side_effect=subprocess.TimeoutExpired('private-command', 25)):
            with self.assertRaisesRegex(SessionLost, 'timeout'): self.store.git('ls-remote')
        self.assertEqual(self.record()['category'], 'timeout')
        self.assertNotIn('private-command', self.log.read_text(encoding='utf-8'))

    def test_timeout_and_oserror_do_not_expose_original_exception_chain(self):
        errors = [subprocess.TimeoutExpired(['git', 'https://user:private-token@example.test'], 25),
                  PermissionError(13, 'denied', 'private-token')]
        for error in errors:
            with self.subTest(error=type(error).__name__), \
                 patch('session_guard.subprocess.run', side_effect=error):
                try:
                    self.store.git('fetch')
                except SessionLost as caught:
                    self.assertIsNone(caught.__cause__)
                    self.assertTrue(caught.__suppress_context__)
                    self.assertNotIn('private-token', ''.join(traceback.format_exception(caught)))
                else:
                    self.fail('failed operation must be blocked')

    def test_log_write_failure_preserves_cas_false(self):
        result = [subprocess.CompletedProcess([], 0, b'blob', b''),
                  subprocess.CompletedProcess([], 0, b'tree', b''),
                  subprocess.CompletedProcess([], 0, b'commit', b''),
                  subprocess.CompletedProcess([], 1, b'', b'stale info')]
        with patch('session_guard.subprocess.run', side_effect=result), \
             patch.object(Path, 'open', side_effect=PermissionError('denied')):
            self.assertFalse(self.store.cas('old', {'owner': 'test'}))

    def test_missing_git_is_recorded_and_blocks(self):
        with patch('session_guard.subprocess.run', side_effect=FileNotFoundError('private-path')):
            with self.assertRaisesRegex(SessionLost, 'git_process_unavailable'): self.store.git('show')
        self.assertEqual(self.record()['category'], 'git_process_unavailable')

    def test_network_failure_is_classified(self):
        make = lambda *a, **k: subprocess.CompletedProcess([], 128, b'', b'Could not resolve host: private.example')
        with patch('session_guard.subprocess.run', side_effect=make), patch.object(self.store, 'sleep'):
            with self.assertRaises(SessionLost): self.store.git('fetch')
        lines = self.log.read_text(encoding='utf-8').strip().splitlines()
        self.assertEqual(len(lines), 3)  # the first try plus two quick retries are each recorded
        self.assertEqual(json.loads(lines[-1])['category'], 'network_or_tls')

    def test_success_does_not_create_error_log(self):
        with patch('session_guard.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'ok', b'')):
            self.assertEqual(self.store.git('show').stdout, 'ok')
        self.assertFalse(self.log.exists())

    def test_log_write_error_does_not_allow_failed_command(self):
        result = subprocess.CompletedProcess([], 128, b'', b'unknown failure')
        with patch('session_guard.subprocess.run', return_value=result), \
             patch.object(Path, 'open', side_effect=PermissionError('private-path')):
            with self.assertRaises(SessionLost): self.store.git('fetch')


if __name__ == '__main__':
    unittest.main()
