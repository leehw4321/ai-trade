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

    def test_uncertain_order_blocks_final_export(self):
        (self.app / 'data/live/live.json').write_text('{"pending_order":{"identifier":"synthetic"}}')
        with patch.object(ex.sync_v5, 'bot_running', return_value=False):
            with self.assertRaises(RuntimeError): ex.export(self.app, self.root / 'out', final=True)
        self.assertFalse((self.root / 'out').exists())


if __name__ == '__main__':
    unittest.main()
