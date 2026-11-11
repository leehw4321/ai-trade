"""장부 전달(ledger_relay) 실제 왕복: 로컬 bare 원격 + 실제 OpenSSL. 네트워크·GitHub 안 씀."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import ledger_relay as lr
import session_guard as sg


def make_identity(root, name):
    folder = root / 'private_handoff' / 'identities' / name
    folder.mkdir(parents=True)
    subprocess.run([lr.openssl(), 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-sha256', '-days', '2',
                    '-subj', '/CN=t-' + name, '-keyout', str(folder / 'private-key.pem'), '-out', str(folder / 'public-certificate.pem')],
                   capture_output=True, check=True)
    return folder


def ledger(app, positions):
    d = app / 'data' / 'live'
    d.mkdir(parents=True, exist_ok=True)
    (d / 'live.json').write_text(json.dumps({'positions': positions, 'realized': 1}, ensure_ascii=False), encoding='utf-8')
    return (d / 'live.json').read_bytes()


class Relay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = Path(self.tmp.name)
        self.remote = r / 'remote.git'
        subprocess.run(['git', 'init', '--bare', '-q', str(self.remote)], check=True)
        self.home = r / 'home' / '코인 트레이딩' / 'v5-app' / 'v5'
        self.work = r / 'work' / '코인 트레이딩' / 'v5-app' / 'v5'
        for a in (self.home, self.work):
            a.mkdir(parents=True)
        self.home_id = make_identity(self.home.parents[1], 'home')
        self.work_id = make_identity(self.work.parents[1], 'work')

    def tearDown(self):
        self.tmp.cleanup()

    def relay(self, app):
        return lr.Relay(app, sg.GitStore(app / 'data' / 'session_git', str(self.remote)))

    def test_round_trip_after_enroll(self):
        self.relay(self.home).enroll(self.home_id / 'public-certificate.pem')
        w = self.relay(self.work)
        w.enroll(self.work_id / 'public-certificate.pem')
        data = ledger(self.home, {'KRW-CAP': {'qty': 1.5, 'stop': 133.17}})
        (self.home / 'data' / 'live' / 'orders.jsonl').write_text('{"time": "2026-11-11T10:00:00", "event": "h"}\n', encoding='utf-8')
        cp = lr.canonical_hash(data.decode())
        meta = self.relay(self.home).publish(data, cp, (self.home / 'data' / 'live' / 'orders.jsonl').read_bytes())
        self.assertEqual(len(meta['recipients']), 2)
        ledger(self.work, {'KRW-OLD': {'qty': 9}})
        (self.work / 'data' / 'live' / 'orders.jsonl').write_text('{"time": "2026-11-09T10:00:00", "event": "w"}\n', encoding='utf-8')
        bundle = w.fetch(cp)
        keep = lr.install(self.work, bundle, stamp='t')
        self.assertEqual((self.work / 'data' / 'live' / 'live.json').read_bytes(), data)
        guard = sg.SessionGuard(self.work, store=sg.GitStore(self.work / 'data' / 'g', str(self.remote)))
        self.assertEqual(guard.checkpoint_hash(), cp)
        orders = (self.work / 'data' / 'live' / 'orders.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual([json.loads(x)['event'] for x in orders], ['w', 'h', '장부 이어받기'])
        self.assertIn('KRW-OLD', (keep / 'live.json').read_text(encoding='utf-8'))
        # 원격에는 평문이 없다
        files = w._remote_tree()[1]
        self.assertNotIn(b'KRW-CAP', b''.join(files.values()))
        self.assertFalse(any(b'PRIVATE KEY' in v for v in files.values()))

    def test_wrong_checkpoint_refused(self):
        self.relay(self.home).enroll(self.home_id / 'public-certificate.pem')
        data = ledger(self.home, {'KRW-A': {'qty': 1}})
        self.relay(self.home).publish(data, lr.canonical_hash(data.decode()))
        with self.assertRaises(lr.RelayError):
            self.relay(self.home).fetch('0' * 64)

    def test_not_enrolled_pc_cannot_read(self):
        self.relay(self.home).enroll(self.home_id / 'public-certificate.pem')
        data = ledger(self.home, {'KRW-A': {'qty': 1}})
        cp = lr.canonical_hash(data.decode())
        self.relay(self.home).publish(data, cp)
        with self.assertRaises(lr.RelayError):
            self.relay(self.work).fetch(cp)

    def test_publish_refuses_mismatched_bytes(self):
        with self.assertRaises(lr.RelayError):
            self.relay(self.home).publish(b'{"a": 1}', 'f' * 64)

    def test_guard_release_publishes_and_records_same_checkpoint(self):
        self.relay(self.home).enroll(self.home_id / 'public-certificate.pem')
        ledger(self.home, {'KRW-A': {'qty': 2}})
        store = sg.GitStore(self.home / 'data' / 'session_git', str(self.remote))
        g = sg.SessionGuard(self.home, {'ledger_relay': True}, store=store)
        self.assertTrue(g.acquire(confirm=lambda: True))
        g.release()
        _, state = store.read()
        self.assertEqual(state['owner'], '')
        self.assertEqual(state['checkpoint'], g.checkpoint_hash())
        self.assertIn('올림', g.relay_note)
        self.assertEqual(self.relay(self.home).fetch(state['checkpoint'])['live_bytes'], (self.home / 'data' / 'live' / 'live.json').read_bytes())

    def test_guard_without_flag_does_not_publish(self):
        ledger(self.home, {'KRW-A': {'qty': 2}})
        store = sg.GitStore(self.home / 'data' / 'session_git', str(self.remote))
        g = sg.SessionGuard(self.home, {}, store=store)
        g.acquire(confirm=lambda: True)
        g.release()
        self.assertEqual(self.relay(self.home)._remote_tree(), ('', {}))

    def test_merge_orders_dedup_sorted(self):
        a = b'{"time": "2"}\n{"time": "1"}\n'
        b = b'{"time": "1"}\n{"time": "3"}\nbroken\n'
        out = lr.merge_orders(a, b).decode().splitlines()
        self.assertEqual(out, ['broken', '{"time": "1"}', '{"time": "2"}', '{"time": "3"}'])


if __name__ == '__main__':
    unittest.main()
