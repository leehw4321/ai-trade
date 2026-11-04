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
        self.guard = sg.SessionGuard(Path(self.tmp.name), {'pc_id': 'home'}, store=self.store, clock=lambda: self.now[0])
        self.assertTrue(self.guard.acquire())

    def tearDown(self):
        self.tmp.cleanup()

    def test_short_outage_blocks_buy_but_keeps_the_session(self):
        self.store.down = True
        self.now[0] = 1060
        with self.assertRaises(sg.SessionUnverified):
            self.guard.assert_owner('bid')
        self.assertFalse(self.guard.lost.is_set())
        self.assertEqual(self.guard.status, 'unverified')
        self.store.down = False
        self.guard.assert_owner('bid')  # recovers without a restart
        self.assertEqual(self.guard.status, 'owner')

    def test_long_outage_never_ends_the_bot_buys_stay_blocked_sells_allowed_until_the_cap(self):
        self.store.down = True
        buys, sells, ended = [], [], False
        for t in range(1010, 1000 + sg.SELL_BLIND_MAX + 600, 600):
            self.now[0] = t
            try:
                self.guard.assert_owner('bid'); buys.append(t)
            except sg.SessionLost:
                pass
            try:
                self.guard.assert_owner('ask'); sells.append(t)
            except sg.SessionLost:
                pass
            ended = ended or self.guard.lost.is_set()
        self.assertEqual(buys, [])           # a buy can never pass while ownership cannot be read
        self.assertFalse(ended)              # the bot is never shut down by an outage
        self.assertTrue(sells and max(sells) < 1000 + sg.SELL_BLIND_MAX)   # stop-loss sells pass, but not forever
        self.store.down = False
        self.now[0] += 1
        self.guard.assert_owner('bid')       # network back and the record is still ours: trading resumes by itself

    def test_expired_record_that_is_still_ours_is_renewed_on_the_next_check(self):
        self.now[0] = 1000 + 400            # renewals lapsed during an outage; nobody took the record
        before = self.store.state['expires']
        self.guard.assert_owner('bid')
        self.assertGreater(self.store.state['expires'], before)

    def test_background_check_interval_is_unchanged(self):
        self.assertEqual(sg.CHECK_SECONDS, 30)

    def test_real_ownership_change_stops_orders_but_not_the_bot(self):
        self.store.state['owner'] = 'someone-else'
        self.store.state['pc'] = 'OFFICE'
        with self.assertRaises(sg.NotOwner):
            self.guard.assert_owner('bid')
        self.assertFalse(self.guard.acquired)
        self.assertFalse(self.guard.lost.is_set())
        self.assertEqual((self.guard.status, self.guard.other_pc), ('other', 'OFFICE'))
        with self.assertRaises(sg.NotOwner):
            self.guard.assert_owner('ask')   # a sell is not allowed either once another PC owns it

    def test_watcher_survives_a_blip_and_renews_afterwards(self):
        guard = self.guard
        guard.closed = mock.Mock()
        steps = iter([(1070, True), (1075, True), (1090, False)])

        def wait(_):
            nxt = next(steps, None)
            if nxt is None:
                return True
            self.now[0], self.store.down = nxt
            return False
        guard.closed.wait.side_effect = wait
        guard.start()
        guard.thread.join(5)
        self.assertFalse(guard.lost.is_set())
        self.assertTrue(guard.acquired)
        self.assertGreater(self.store.state['expires'], 1180)   # renewed once the network came back


if __name__ == '__main__':
    unittest.main()
