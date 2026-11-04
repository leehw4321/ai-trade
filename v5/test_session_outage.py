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

    def test_unrecognised_push_failure_is_unverified_not_lost(self):
        # 2026-11-04 07:31: an 8 s push failure with an unrecognised message used to end the bot as "lost".
        with mock.patch('session_guard.subprocess.run', side_effect=[completed(0), completed(0), completed(0),
                                                                    completed(1, 'error: something unexpected')]):
            with self.assertRaises(sg.SessionUnverified):
                self.store.cas('abc', {'owner': 't'})
        with mock.patch('session_guard.subprocess.run', side_effect=[completed(0), completed(0), completed(0),
                                                                    completed(1, 'error: RPC failed; HTTP 502')]):
            with self.assertRaises(sg.SessionUnverified):
                self.store.cas('abc', {'owner': 't'})

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

    def test_background_check_interval_leaves_room_for_retries_before_expiry(self):
        # lease 180 s, renewed every 60-90 s (one check interval of slack), margin 20 s: the worst case leaves
        # 70 s, i.e. two more background checks. Scheduling and request time can cost more, so the count is
        # not guaranteed; the orders below never depend on it.
        grace = 180 - sg.LEASE_MARGIN - (60 + sg.CHECK_SECONDS)
        self.assertGreaterEqual(grace // sg.CHECK_SECONDS, 2)
        self.assertEqual(sg.CHECK_SECONDS, 30)

    def test_no_order_passes_during_an_outage_and_the_session_ends_at_the_margin(self):
        self.store.down = True
        passed, lost_at = [], None
        for t in range(1010, 1230, 10):  # also covers checks that arrive late
            self.now[0] = t
            try:
                self.guard.assert_owner()
                passed.append(t)
            except sg.SessionLost:
                pass
            if lost_at is None and self.guard.lost.is_set():
                lost_at = t
        self.assertEqual(passed, [])  # no order is ever allowed while ownership cannot be read
        self.assertEqual(lost_at, 1160)  # lease_until 1180 - margin 20: first moment it is no longer trusted
        self.guard.lost.clear()
        self.store.down = False
        self.now[0] = 1170  # network back, but the session was already given up: still no orders until re-acquired
        self.guard.lost.set()
        with self.assertRaises(sg.SessionLost):
            self.guard.assert_owner()

    def test_renewal_extends_trust_so_the_margin_moves_with_it(self):
        self.now[0] = 1090
        oid, _ = self.store.read()
        renewed = self.guard.record()
        self.assertTrue(self.store.cas(oid, renewed))
        self.guard.lease_until = renewed['expires']  # what the watcher does after a successful renewal
        self.store.down = True
        self.now[0] = 1170  # would have been lost under the old lease, still trusted under the renewed one
        with self.assertRaises(sg.SessionUnverified):
            self.guard.assert_owner()
        self.assertFalse(self.guard.lost.is_set())
        self.now[0] = 1090 + 180 - sg.LEASE_MARGIN
        with self.assertRaises(sg.SessionUnverified):
            self.guard.assert_owner()
        self.assertTrue(self.guard.lost.is_set())

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
