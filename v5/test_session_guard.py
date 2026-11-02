import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import session_guard as sg
import upbit


class Store:
    def __init__(self):
        self.version = ''; self.state = {}; self.read_hook = None

    def read(self):
        if self.read_hook: self.read_hook()
        return self.version, copy.deepcopy(self.state)

    def cas(self, version, state):
        if version != self.version: return False
        self.version += 'x'; self.state = copy.deepcopy(state)
        return True


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.store = Store()

    def tearDown(self):
        self.tmp.cleanup()

    def guard(self, live=False):
        return sg.SessionGuard(self.root, {'live_enabled': live}, store=self.store, clock=lambda: 1000)

    def test_decline_keeps_existing_session(self):
        old = self.guard(); self.assertTrue(old.acquire())
        new = self.guard(); self.assertFalse(new.acquire(confirm=lambda: False))
        old.assert_owner(); self.assertEqual(self.store.state['owner'], old.token)

    def test_handoff_blocks_old_orders_before_new_owner(self):
        old = self.guard(); old.acquire(); new = self.guard()
        def release_on_request():
            if self.store.state.get('request') == new.token:
                self.store.read_hook = None
                with self.assertRaises(sg.SessionLost): old.assert_owner()
                old.release()
        self.store.read_hook = release_on_request
        with patch('session_guard.time.sleep'):
            self.assertTrue(new.acquire(confirm=lambda: True))
        new.assert_owner()
        with self.assertRaises(sg.SessionLost): old.assert_owner()

    def test_missing_checkpoint_blocks_live_and_releases_reservation(self):
        self.store.state = {'owner': '', 'request': '', 'checkpoint': 'different'}
        new = self.guard(live=True)
        with self.assertRaisesRegex(sg.SessionLost, '장부'): new.acquire()
        self.store.state['request'] = new.token
        new.release()
        self.assertEqual(self.store.state['request'], '')

    def test_network_failure_blocks_order_before_http(self):
        owner = self.guard(); owner.acquire()
        with patch.object(self.store, 'read', side_effect=OSError('offline')), \
             patch.object(upbit, 'ORDER_GUARD', owner.assert_owner), \
             patch.object(upbit.urllib.request, 'urlopen') as http:
            with self.assertRaises(sg.SessionLost): upbit.market_buy('KRW-BTC', 5000)
            http.assert_not_called()
        self.assertTrue(owner.lost.is_set())

    def test_paper_session_preserves_unmigrated_live_checkpoint(self):
        self.store.state = {'owner': '', 'request': '', 'checkpoint': 'private-ledger-hash'}
        paper = self.guard(); paper.acquire()
        self.assertEqual(self.store.state['checkpoint'], 'private-ledger-hash')
        paper.release()
        live = self.guard(live=True)
        with self.assertRaisesRegex(sg.SessionLost, '장부'): live.acquire()

    def test_expired_live_session_requires_shutdown_ack(self):
        self.store.state = {'owner': 'old', 'expires': 100, 'request': ''}
        new = self.guard(live=True)
        with self.assertRaisesRegex(sg.SessionLost, '종료 확인'):
            new.acquire(confirm=lambda: True)
        new.release()
        self.assertEqual(self.store.state['request'], '')

    def test_cas_prevents_overwriting_other_owner(self):
        original_cas = self.store.cas
        def competing(version, state):
            original_cas(version, {'owner': 'another'})
            return original_cas(version, state)
        with patch.object(self.store, 'cas', side_effect=competing):
            with self.assertRaises(sg.SessionLost): self.guard().acquire()
        self.assertEqual(self.store.state['owner'], 'another')


class GitStoreTests(unittest.TestCase):
    def test_real_git_compare_and_swap(self):
        import subprocess
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); remote = root / 'remote.git'
            subprocess.run(['git', 'init', '--bare', str(remote)], capture_output=True, check=True)
            a = sg.GitStore(root / 'a.git', str(remote))
            b = sg.GitStore(root / 'b.git', str(remote))
            oid, state = a.read(); self.assertEqual((oid, state), ('', {}))
            self.assertTrue(a.cas('', {'owner': 'a'}))
            self.assertFalse(b.cas('', {'owner': 'b'}))
            oid, state = b.read(); self.assertEqual(state['owner'], 'a')
            self.assertTrue(b.cas(oid, {'owner': 'b'}))
            self.assertEqual(a.read()[1]['owner'], 'b')


if __name__ == '__main__':
    unittest.main()
