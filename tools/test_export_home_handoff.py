"""Private transfer invariants. Uses synthetic files, no real account data."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import export_home_handoff as ex


class HandoffExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = self.root / 'app'; (self.app / 'data/live').mkdir(parents=True)
        (self.app / 'config.json').write_text('{"live":{"enabled":true}}')
        (self.app / 'data/live/live.json').write_text('{"positions":{}}')
        (self.app / 'data/.env').write_text('DO_NOT_INCLUDE')
        (self.app / '.env').write_text('DO_NOT_INCLUDE')

    def test_reference_snapshot_excludes_env_and_cannot_claim_live_restore(self):
        with patch.object(ex, 'ROOT', self.root):
            out = ex.export(self.app, self.root / 'out')
        with zipfile.ZipFile(out) as z:
            self.assertFalse(any('.env' in n for n in z.namelist()))
            self.assertFalse(json.loads(z.read('manifest.json'))['live_restore_allowed'])
            self.assertEqual(json.loads(z.read('runtime/data/live/live.json')), {'positions': {}})

    def test_final_export_refuses_running_bot_before_creating_zip(self):
        with patch.object(ex.sync_v5, 'bot_running', return_value=True):
            with self.assertRaises(RuntimeError): ex.export(self.app, self.root / 'out', final=True)
        self.assertFalse((self.root / 'out').exists())

    def test_session_store_and_nested_archives_and_credentials_are_not_exported(self):
        for relative in ('data/session_git/config', 'data/private_handoff/nested.json',
                         'data/hidden.zip', 'data/handoff.cms', 'data/auth.json',
                         'data/.credentials.json', 'data/private-key.pem'):
            file = self.app / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('DO_NOT_TRANSMIT')
        with patch.object(ex, 'ROOT', self.root):
            out = ex.export(self.app, self.root / 'out')
        with zipfile.ZipFile(out) as z:
            self.assertFalse(any('session_git' in n or 'private_handoff' in n
                                 or n.endswith(('.zip', '.cms', '.pem'))
                                 or n.rsplit('/', 1)[-1] in ('auth.json', '.credentials.json')
                                 for n in z.namelist()))

    def test_baseline_presence_and_hash_are_bound_to_the_actual_archived_bytes(self):
        import hashlib
        initial = b'{"start":"synthetic","positions":{}}'
        (self.app / 'pnl_baseline.json').write_bytes(initial)
        with patch.object(ex, 'ROOT', self.root):
            out = ex.export(self.app, self.root / 'out')
        with zipfile.ZipFile(out) as z:
            manifest = json.loads(z.read('manifest.json'))
            self.assertTrue(manifest['pnl_baseline_present'])
            self.assertEqual(manifest['pnl_baseline_sha256'], hashlib.sha256(initial).hexdigest())
            self.assertEqual(z.read('runtime/pnl_baseline.json'), initial)
            self.assertFalse(manifest['pnl_baseline_changed_during_export'])

    def test_uncertain_order_blocks_final_export(self):
        (self.app / 'data/live/live.json').write_text('{"pending_order":{"identifier":"synthetic"}}')
        with patch.object(ex.sync_v5, 'bot_running', return_value=False):
            with self.assertRaises(RuntimeError): ex.export(self.app, self.root / 'out', final=True)
        self.assertFalse((self.root / 'out').exists())


if __name__ == '__main__':
    unittest.main()
