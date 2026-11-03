import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import apply_manual_protection as deploy


class ManualDeployTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.app=self.root/'work/v5-app/v5';self.app.mkdir(parents=True)
        self.source=self.root/'source';self.source.mkdir()
        (self.app.parent.parent/'launch_v5.py').write_text('launcher')
        for name in deploy.NAMES:
            (self.source/name).write_text('approved');(self.app/name).write_text('old')
        (self.app/'config.json').write_text(json.dumps({'live':{'enabled':True,'user_protection_required':True}}))
        self.manifest={'app':str(self.app),'sources':{n:deploy.digest(self.source/n) for n in deploy.NAMES},
            'old_sources':{n:deploy.digest(self.app/n) for n in deploy.NAMES},
            'config_sha256':deploy.digest(self.app/'config.json')}

    def test_unreviewed_source_cannot_stop_running_bot(self):
        (self.source/'live.py').write_text('changed')
        with patch.object(deploy.sync,'SRC',self.source),patch.object(deploy.safe,'stop') as stop:
            with self.assertRaises(ValueError):deploy.apply(self.app,self.manifest)
        stop.assert_not_called()

    def test_pending_order_never_stops_bot(self):
        with patch.object(deploy.sync,'SRC',self.source),patch.object(deploy.deploy,'pending_orders',return_value='pending'), \
                patch.object(deploy.safe,'stop') as stop:
            with self.assertRaises(RuntimeError):deploy.apply(self.app,self.manifest)
        stop.assert_not_called()

    def test_missing_launcher_does_not_stop_bot(self):
        (self.app.parent.parent/'launch_v5.py').unlink()
        with patch.object(deploy.sync,'SRC',self.source),patch.object(deploy.safe,'stop') as stop:
            with self.assertRaises(ValueError):deploy.apply(self.app,self.manifest)
        stop.assert_not_called()

    def test_failed_start_stops_new_process_before_rollback(self):
        (self.app/'data/live').mkdir(parents=True)
        (self.app/'data/live/live.json').write_text('{"positions":{}}')
        stop_calls=[]
        def stop(app):
            stop_calls.append((app/'live.py').read_text());return True
        def sync():
            for name in deploy.NAMES: (self.app/name).write_bytes((deploy.sync.SRC/name).read_bytes())
            return 0
        with patch.object(deploy.sync,'SRC',self.source),patch.object(deploy.deploy,'pending_orders',return_value=''), \
                patch.object(deploy.safe,'stop',side_effect=stop),patch.object(deploy.sync,'main',side_effect=sync), \
                patch.object(deploy.deploy,'start_bot',return_value=(False,'failed')) as start:
            with self.assertRaises(RuntimeError):deploy.apply(self.app,self.manifest)
        self.assertEqual(stop_calls,['old','approved'])
        self.assertEqual((self.app/'live.py').read_text(),'old');self.assertEqual(start.call_count,1)

    def test_panel_health_retries_transient_read_and_parse_errors(self):
        payload={'action':{'port':8765},'health':{'checked_at':200,'error':''}}
        page='<script type="application/json" id="ap-data">'+json.dumps(payload)+'</script>'
        broken='<script type="application/json" id="ap-data">{broken}</script>'
        with patch.object(Path,'read_text',side_effect=[OSError('sharing violation'),broken,page]), \
                patch.object(deploy.time,'sleep'):
            self.assertEqual(deploy.wait_for_panel(self.app,100,1),payload)


if __name__=='__main__':unittest.main()
