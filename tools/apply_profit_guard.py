"""Narrow reviewed deployment: bot/live only, normal stop, sync helper, rollback.

Requires a local manifest of approved SHA256s and an explicit app path. No keys
or ledgers are copied. Existing independent account sync remains running.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import deploy_v5 as deploy
import sync_v5 as sync


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def object_digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def atomic_copy(source, target):
    temp = target.with_suffix(target.suffix + '.guard-deploy.tmp')
    shutil.copyfile(source, temp)
    os.replace(temp, target)


def pending(app):
    return deploy.pending_orders(app)


def stopped(app):
    return not any(deploy.lock_alive(app, n) for n in ('bot.lock', 'shadow.lock'))


def stop(app, timeout=90):
    for n in ('stop.txt', 'stop_shadow.txt'):
        (app / n).write_text('reviewed profit protection deployment', encoding='utf-8')
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if stopped(app):
            return True
        time.sleep(1)
    return False


def clear(app):
    for n in ('stop.txt', 'stop_shadow.txt'):
        (app / n).unlink(missing_ok=True)


def start(app):
    clear(app)
    started = time.time()
    processes = []
    for name in ('bot', 'shadow'):
        if deploy.lock_alive(app, name + '.lock'):
            continue
        with (app / 'data' / (name + '.guard-deploy.stdout.log')).open('ab') as out, \
             (app / 'data' / (name + '.guard-deploy.stderr.log')).open('wb') as err:
            processes.append(subprocess.Popen(
                [str(Path(sys.executable).with_name('pythonw.exe')), str(app / (name + '.py'))],
                cwd=app, stdout=out, stderr=err,
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
                creationflags=subprocess.CREATE_NO_WINDOW))
    end = time.monotonic() + 100
    while time.monotonic() < end:
        if any(p.poll() is not None for p in processes):
            return False
        page = app / 'data' / 'status.html'
        if (page.exists() and page.stat().st_mtime >= started
                and all(deploy.lock_alive(app, n) for n in ('bot.lock', 'shadow.lock'))):
            time.sleep(5)
            return (all(p.poll() is None for p in processes)
                    and not any((app / 'data' / (n + '.guard-deploy.stderr.log')).stat().st_size
                                for n in ('bot', 'shadow')))
        time.sleep(1)
    return False


def apply(app, manifest):
    app = app.resolve(strict=True)
    src = sync.SRC
    # Verify before any stop, and again after normal shutdown.
    def verify():
        if str(app) != manifest['app']:
            raise ValueError('wrong target app')
        if object_digest(manifest['config_patch']) != manifest['config_patch_sha256']:
            raise ValueError('config patch changed')
        for name in ('live.py', 'bot.py'):
            if digest(src / name) != manifest['approved_sources'][name]:
                raise ValueError('source changed: ' + name)
            if digest(app / name) != manifest['app_old_sources'][name]:
                raise ValueError('app source changed: ' + name)
        for rel, sha in manifest['approved_tests'].items():
            if digest(sync.ROOT / rel) != sha:
                raise ValueError('test changed: ' + rel)
        if digest(app / 'config.json') != manifest['app_config_sha256']:
            raise ValueError('app config changed')
    verify()
    if not all(deploy.lock_alive(app, n) for n in ('bot.lock', 'shadow.lock')):
        raise RuntimeError('expected running bot and shadow required')
    if pending(app):
        raise RuntimeError('pending order; deployment held')
    backup = deploy.backup(app)
    print('Backup created; requesting normal shutdown', flush=True)
    if not stop(app):
        restored = start(app)  # Restore components that stopped before the other timed out.
        raise RuntimeError('normal shutdown timeout; no files changed; restart_ok=' + str(restored))
    changed = False
    try:
        verify()
        if pending(app):
            raise RuntimeError('pending order after shutdown')
        stage = backup / 'approved_stage'
        stage.mkdir()
        for name in ('live.py', 'bot.py'):
            shutil.copyfile(src / name, stage / name)
        sync.SRC, sync.DST = stage, app
        changed = True
        argv = sys.argv
        try:
            sys.argv = ['sync_v5.py', '--apply']
            if sync.main() != 0:
                raise RuntimeError('sync refused')
        finally:
            sys.argv = argv
        deploy.patch_config(app, manifest['config_patch'])
        for name in ('live.py', 'bot.py'):
            if digest(app / name) != manifest['approved_sources'][name]:
                raise RuntimeError('deployed source mismatch: ' + name)
        if digest(app / 'config.json') != manifest['expected_config_sha256']:
            raise RuntimeError('deployed config mismatch')
        print('Reviewed files/config applied; restarting', flush=True)
        if not start(app):
            raise RuntimeError('new process health check failed')
        print('DEPLOY_OK', flush=True)
    except BaseException as original:
        try:
            if not stop(app):
                raise RuntimeError('cannot stop new processes; rollback not written')
            if changed:
                for name in ('live.py', 'bot.py', 'config.json'):
                    atomic_copy(backup / name, app / name)
                    if digest(backup / name) != digest(app / name):
                        raise RuntimeError('rollback hash mismatch: ' + name)
            if not start(app):
                raise RuntimeError('rollback restart failed')
            print('ROLLED_BACK', flush=True)
        except BaseException as rollback_error:
            raise RuntimeError('rollback failed: ' + type(rollback_error).__name__) from original
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    apply(args.app, json.loads(args.manifest.read_text(encoding='utf-8')))
