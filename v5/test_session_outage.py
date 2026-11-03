import copy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import session_guard as sg


class Store:
    def __init__(self):
        self.version = ''
        self.state = {}
        self.down = False

    def read(self):
        if self.down:
            raise sg.SessionUnverified('GitHub 실행권 확인 실패(network_or_tls).')
        return self.version, copy.deepcopy(self.state)

    def cas(self, version, state):
        if self.down:
            raise sg.SessionUnverified('GitHub 실행권 확인 실패(network_or_tls).')
        if version != self.version:
            return False
        self.version += 'x'
        self.state = copy.deepcopy(state)
        return True


def completed(code, err=''):
    return subprocess.CompletedProcess([], code, b'', err.encode())


class GitRetry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sleeps = []
        self.store = sg.GitStore(Path(self.tmp.name) / 'g', sleep=self.sleeps.append)
        self.net = 'fatal: unable to access: Could not resolve host: github.com'

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_retries_quick_network_errors_then_succeeds(self):
        with mock.patch('session_guard.subprocess.run', side_effect=[completed(128, self.net), completed(128, self.net), completed(0)]) as run:
            self.assertEqual(self.store.git('ls-remote', 'x').returncode, 0)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(self.sleeps, [1, 3])

    def test_read_gives_up_as_unverified_not_as_ownership_change(self):
        with mock.patch('session_guard.subprocess.run', side_effect=lambda *a, **k: completed(128, self.net)) as run:
            with self.assertRaises(sg.SessionUnverified):
                self.store.git('ls-remote', 'x')
        self.assertEqual(run.call_count, 3)

    def test_auth_and_conflict_errors_are_not_retried_or_softened(self):
        with mock.patch('session_guard.subprocess.run', side_effect=lambda *a, **k: completed(128, 'fatal: Authentication failed')) as run:
            with self.assertRaises(sg.SessionLost) as cm:
                self.store.git('ls-remote', 'x')
        self.assertNotIsInstance(cm.exception, sg.SessionUnverified)
        self.assertEqual(run.call_count, 1)

    def test_push_is_never_retried_and_only_network_failure_is_unverified(self):
        with mock.patch('session_guard.subprocess.run', side_effect=[completed(0, ''), completed(0, ''), completed(0, ''),
                                                                    completed(1, self.net)]) as run:
            with self.assertRaises(sg.SessionUnverified):
                self.store.cas('abc', {'owner': 't'})
        self.assertEqual(run.call_count, 4)
        with mock.patch('session_guard.subprocess.run', side_effect=[completed(0), completed(0), completed(0),
                                                                    completed(1, '! [rejected] (stale info)')]):
            self.assertFalse(self.store.cas('abc', {'owner': 't'}))

    def test_timeout_is_unverified(self):
        with mock.patch('session_guard.subprocess.run', side_effect=subprocess.TimeoutExpired('git', 25)):
            with self.assertRaises(sg.SessionUnverified):
                self.store.git('ls-remote', 'x')


class OutageTolerance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = [1000.0]
        self.store = Store()
        self.guard = sg.SessionGuard(Path(self.tmp.name), {}, store=self.store, clock=lambda: self.now[0])
        self.assertTrue(self.guard.acquire())  # lease_until = 1180

    def tearDown(self):
        self.tmp.cleanup()

    def test_short_outage_blocks_the_order_but_keeps_the_session(self):
        self.store.down = True
        self.now[0] = 1060
        with self.assertRaises(sg.SessionUnverified):
            self.guard.assert_owner()
        self.assertFalse(self.guard.lost.is_set())
        self.store.down = False
        self.guard.assert_owner()  # recovers without a restart

    def test_outage_beyond_the_lease_still_fails_closed(self):
        self.store.down = True
        self.now[0] = 1180 - sg.LEASE_MARGIN
        with self.assertRaises(sg.SessionUnverified):
            self.guard.assert_owner()
        self.assertTrue(self.guard.lost.is_set())
        self.store.down = False
        with self.assertRaises(sg.SessionLost):
            self.guard.assert_owner()

    def test_real_ownership_change_is_lost_immediately(self):
        self.store.state['owner'] = 'someone-else'
        with self.assertRaises(sg.SessionLost):
            self.guard.assert_owner()
        self.assertTrue(self.guard.lost.is_set())

    def test_watcher_survives_a_blip_and_renews_afterwards(self):
        waits = iter([False, False, False, True])
        guard = self.guard
        guard.closed = mock.Mock()
        guard.closed.wait.side_effect = lambda t: next(waits)
        steps = [(1000, True), (1070, True), (1080, False)]  # (clock, store down?)
        it = iter(steps)

        def tick(_):
            now, down = next(it, (1090, False))
            self.now[0] = now
            self.store.down = down
            return False
        guard.closed.wait.side_effect = None
        calls = {'n': 0}

        def wait(_):
            calls['n'] += 1
            if calls['n'] > 4:
                return True
            return tick(_)
        guard.closed.wait.side_effect = wait
        guard.start()
        guard.thread.join(5)
        self.assertFalse(guard.lost.is_set())
        self.assertGreater(guard.lease_until, 1180)  # renewed once the network came back


if __name__ == '__main__':
    unittest.main()
