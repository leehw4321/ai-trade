import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_v5


def done(out='', rc=0, err='err'):
    return subprocess.CompletedProcess([], rc, out, err)


class BotRunningTest(unittest.TestCase):
    def call(self, **kw):
        with mock.patch.object(sync_v5.subprocess, 'run', **kw):
            return sync_v5.bot_running()

    def test_pythonw_relative_path_true(self):
        self.assertTrue(self.call(return_value=done('P:"C:\\Py\\pythonw.exe" bot.py\nEND\n')))

    def test_empty_result_false(self):
        self.assertFalse(self.call(return_value=done('END\n')))

    def test_rc_failure_raises(self):
        with self.assertRaises(RuntimeError):
            self.call(return_value=done('', rc=1))

    def test_timeout_raises(self):
        with self.assertRaises(RuntimeError):
            self.call(side_effect=subprocess.TimeoutExpired('powershell', 15))

    def test_apply_blocked_when_running(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d, 'src'), Path(d, 'dst')
            src.mkdir()
            dst.mkdir()
            (src / 'a.py').write_text('1')
            (dst / 'a.py').write_text('2')
            with mock.patch.object(sync_v5, 'SRC', src), mock.patch.object(sync_v5, 'DST', dst), \
                    mock.patch.object(sync_v5, 'bot_running', return_value=True), \
                    mock.patch.object(sync_v5.shutil, 'copyfile') as cp, \
                    mock.patch.object(sys, 'argv', ['sync_v5.py', '--apply']):
                self.assertEqual(sync_v5.main(), 1)
            cp.assert_not_called()
            self.assertEqual((dst / 'a.py').read_text(), '2')

    def test_blank_commandline_raises(self):
        for out in ('P:\nEND\n', 'P:   \nEND\n', 'P:"C:\\Py\\python.exe" x.py\nP:\nEND\n'):
            with self.subTest(out=out), self.assertRaises(RuntimeError):
                self.call(return_value=done(out))

    def test_malformed_output_raises(self):
        for out in ('', 'P:x bot.py\n', 'junk\nEND\n', 'P:x.py\n\nEND\n'):
            with self.subTest(out=out), self.assertRaises(RuntimeError):
                self.call(return_value=done(out))

    def test_errors_do_not_leak(self):
        s = 'SECRET=hunter2'
        cases = [dict(return_value=done('', rc=1, err=s)),
                 dict(return_value=done('junk ' + s + '\nEND\n')),
                 dict(side_effect=subprocess.TimeoutExpired(['powershell', s], 15, output=s, stderr=s)),
                 dict(side_effect=OSError(s))]
        for i, kw in enumerate(cases):
            with self.subTest(i=i):
                with self.assertRaises(RuntimeError) as cm:
                    self.call(**kw)
                e = cm.exception
                self.assertNotIn('hunter2', str(e) + repr(e) + repr(e.args))
                self.assertIsNone(e.__cause__)
                self.assertTrue(e.__suppress_context__ or e.__context__ is None)

    def test_errors_replace_passed(self):
        with mock.patch.object(sync_v5.subprocess, 'run', return_value=done('END\n')) as run:
            sync_v5.bot_running()
        self.assertEqual(run.call_args.kwargs.get('errors'), 'replace')


if __name__ == '__main__':
    unittest.main()
