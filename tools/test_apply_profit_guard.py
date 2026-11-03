"""Deployment safety gates; every process/file deployment action is mocked."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import apply_profit_guard as ag


class DeploymentGates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / 'src'; self.src.mkdir()
        self.app = self.root / 'app'; self.app.mkdir()
        for name in ('live.py', 'bot.py'):
            (self.src / name).write_text('new')
            (self.app / name).write_text('old')
        (self.app / 'config.json').write_text('{}')
        self.manifest = {'approved_sources': {n: ag.digest(self.src / n) for n in ('live.py', 'bot.py')},
                         'app_config_sha256': ag.digest(self.app / 'config.json'), 'config_patch': {},
                         'app': str(self.app), 'app_old_sources': {n: ag.digest(self.app / n) for n in ('live.py', 'bot.py')},
                         'approved_tests': {}, 'config_patch_sha256': ag.object_digest({})}

    def test_changed_source_never_stops_bot(self):
        (self.src / 'live.py').write_text('unreviewed')
        with patch.object(ag.sync, 'SRC', self.src), patch.object(ag, 'stop') as stop:
            with self.assertRaises(ValueError): ag.apply(self.app, self.manifest)
        stop.assert_not_called()

    def test_changed_config_never_stops_bot(self):
        (self.app / 'config.json').write_text('{"changed":true}')
        with patch.object(ag.sync, 'SRC', self.src), patch.object(ag, 'stop') as stop:
            with self.assertRaises(ValueError): ag.apply(self.app, self.manifest)
        stop.assert_not_called()

    def test_pending_order_blocks_before_shutdown(self):
        with patch.object(ag.sync, 'SRC', self.src), patch.object(ag.deploy, 'lock_alive', return_value=True), \
             patch.object(ag, 'pending', return_value='uncertain'), patch.object(ag, 'stop') as stop:
            with self.assertRaises(RuntimeError): ag.apply(self.app, self.manifest)
        stop.assert_not_called()

    def test_partial_shutdown_attempts_original_restart_without_copy(self):
        with patch.object(ag.sync, 'SRC', self.src), patch.object(ag.deploy, 'lock_alive', return_value=True), \
             patch.object(ag, 'pending', return_value=''), patch.object(ag.deploy, 'backup'), \
             patch.object(ag, 'stop', return_value=False), patch.object(ag, 'start', return_value=True) as start:
            with self.assertRaises(RuntimeError): ag.apply(self.app, self.manifest)
        start.assert_called_once_with(self.app)
        self.assertEqual((self.app / 'live.py').read_text(), 'old')


if __name__ == '__main__':
    unittest.main()
