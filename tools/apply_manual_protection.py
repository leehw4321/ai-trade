"""Hash-gated manual protection deployment; normal stop, sync, failed start stays stopped."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import sys
import time
import urllib.request

import apply_profit_guard as safe
import deploy_v5 as deploy
import sync_v5 as sync

NAMES={'asset_protection.py','manual_protection.py','protection_panel.py','manual_orders.py',
       'bot.py','live.py','test_auto_protection.py'}
PATCH={'auto_manual_protection':True,'protection_ui':True,'user_protection_required':True}


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def wait_for_panel(app,started,timeout=45):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            page=(app/'data/status.html').read_text(encoding='utf-8')
            match=re.search(r'<script type="application/json" id="ap-data">(.*?)</script>',page,re.S)
            if match:
                payload=json.loads(match[1]);health=payload.get('health',{})
                if payload.get('action') and health.get('checked_at',0)>=started and not health.get('error'):return payload
        except (OSError,ValueError):pass
        time.sleep(1)
    raise RuntimeError('First global protection scan or panel action failed')


def apply(app,manifest):
    app=Path(app).resolve(strict=True)
    def verify():
        if str(app)!=manifest['app'] or set(manifest['sources'])!=NAMES: raise ValueError('Unexpected target/sources')
        for name,sha in manifest['sources'].items():
            if digest(sync.SRC/name)!=sha: raise ValueError('Source changed: '+name)
            if digest(app/name)!=manifest['old_sources'][name]: raise ValueError('Runtime source changed: '+name)
        if digest(app/'config.json')!=manifest['config_sha256']: raise ValueError('Config changed')
        if not (app.parent.parent/'launch_v5.py').exists(): raise ValueError('Launcher missing')
        cfg=json.loads((app/'config.json').read_text(encoding='utf-8-sig'))['live']
        if not cfg.get('enabled') or not cfg.get('user_protection_required'): raise ValueError('Unexpected live policy')
    verify()
    if deploy.pending_orders(app): raise RuntimeError('Unresolved order; no deployment')
    if not safe.stop(app): raise RuntimeError('Normal stop incomplete; no code change, stop request retained')
    changed=False; backup=None
    try:
        verify()
        if deploy.pending_orders(app): raise RuntimeError('Unresolved order after shutdown')
        backup=deploy.backup(app)
        before=json.loads((app/'data/live/live.json').read_text(encoding='utf-8'))
        stage=backup/'approved_stage'; stage.mkdir()
        for name in NAMES: shutil.copyfile(sync.SRC/name,stage/name)
        previous=(sync.SRC,sync.DST); argv=sys.argv
        try:
            sync.SRC,sync.DST=stage,app; sys.argv=['sync_v5.py','--apply']; changed=True
            if sync.main()!=0: raise RuntimeError('Sync rejected')
        finally: sync.SRC,sync.DST=previous; sys.argv=argv
        cfg=json.loads((app/'config.json').read_text(encoding='utf-8-sig'))
        cfg['live'].update(PATCH)
        target=app/'config.json'; temp=target.with_suffix('.protection.tmp')
        temp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8'); temp.replace(target)
        for name,sha in manifest['sources'].items():
            if digest(app/name)!=sha: raise RuntimeError('Deployed hash mismatch')
        after=json.loads((app/'data/live/live.json').read_text(encoding='utf-8'))
        if before!=after: raise RuntimeError('Ledger changed during stopped deployment')
        safe.clear(app)
        started=time.time()
        ok,_=deploy.start_bot(app.parent.parent/'launch_v5.py')
        if not ok: raise RuntimeError('Launcher failed')
        payload=wait_for_panel(app,started)
        if not all(payload['rows'].get(m,{}).get('protected') is True for m in ('KRW-BTC','KRW-DOGE')):
            raise RuntimeError('BTC/DOGE protection missing')
        sys.path.insert(0,str(app))
        from asset_protection import validate
        validate(json.loads((app/'data/user_protected_assets.json').read_text(encoding='utf-8')))
        with urllib.request.urlopen('http://127.0.0.1:'+str(payload['action']['port'])+'/ping',timeout=3) as response:
            if response.status!=200: raise RuntimeError('Protection ping failed')
        current=json.loads((app/'data/live/live.json').read_text(encoding='utf-8'))
        for key in ('risk_base_krw','risk_loss_offset','risk_day','baseline_unrealized'):
            if current.get(key)!=before.get(key): raise RuntimeError('Risk basis changed: '+key)
        print('MANUAL_PROTECTION_DEPLOY_OK',flush=True)
    except BaseException:
        # Stop the NEW process before writing any rollback. Old version is never revived.
        if not safe.stop(app): raise RuntimeError('New process did not stop; rollback not written')
        if changed and backup:
            for name in NAMES|{'config.json'}:
                if (backup/name).exists(): safe.atomic_copy(backup/name,app/name)
                elif name in NAMES: (app/name).unlink(missing_ok=True)
        print('PROTECTION_DEPLOY_FAILED_STOPPED',flush=True)
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--app',required=True,type=Path)
    parser.add_argument('--manifest',required=True,type=Path); args=parser.parse_args()
    apply(args.app,json.loads(args.manifest.read_text(encoding='utf-8')))
