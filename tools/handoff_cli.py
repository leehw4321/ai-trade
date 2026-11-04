"""Staged CLI: recipient enrollment, final-state sealing/publish, receive/dry-run/apply.

Publish requires --publish. Receive defaults to validated local ZIP only; --apply
restores state while stopped, never starts a trader. All identities remain under
the explicitly selected private root. No API billing or plaintext Git transport.
"""
import argparse
import contextlib
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from decimal import Decimal

import secure_handoff as sh


def restrict_identity(path, runner=subprocess.run):
    path = Path(path)
    if os.name != 'nt':
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    result = sh.run_command(['whoami', '/user', '/fo', 'csv', '/nh'], runner=runner)
    sid = re.search(rb'S-1-5-\d+(?:-\d+)+', result.stdout)
    if not sid: raise sh.HandoffError('Cannot determine local identity SID')
    permissions = '(OI)(CI)F' if path.is_dir() else 'F'
    sh.run_command(['icacls', path, '/inheritance:r', '/grant:r', '*' + sid[0].decode() + ':' + permissions,
                    '/grant:r', '*S-1-5-18:' + permissions], runner=runner)


def init_recipient(private_root, name, openssl=sh.DEFAULT_OPENSSL, runner=subprocess.run):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', name): raise sh.HandoffError('Invalid recipient name')
    root = Path(private_root).resolve()
    folder = root / 'identities' / name
    if folder.exists(): raise sh.HandoffError('Recipient already enrolled; refuse to replace private key')
    folder.mkdir(parents=True)
    restrict_identity(folder, runner)
    key, cert = folder / 'private-key.pem', folder / 'public-certificate.pem'
    try:
        sh.run_command([openssl, 'req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-sha256',
            '-days', '730', '-subj', '/CN=coinbot-handoff-' + name, '-keyout', key, '-out', cert],
            runner=runner, timeout=120)
        restrict_identity(key, runner)
        if b'PRIVATE KEY' in cert.read_bytes(): raise sh.HandoffError('Public certificate unexpectedly contains private material')
    except BaseException:
        key.unlink(missing_ok=True)
        raise
    return {'recipient': name, 'public_certificate': str(cert),
            'public_certificate_sha256': sh.sha256_file(cert), 'private_key_stays_on_recipient': True}


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alive_from_lock(path):
    if not path.exists(): return False
    text = path.read_text().strip()
    if not text.isdigit() or not int(text): raise sh.HandoffError('Process lock cannot be verified')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, int(text))
    if not handle:
        if ctypes.get_last_error() == 5: raise sh.HandoffError('Process visibility denied')
        return False
    try: return kernel.WaitForSingleObject(handle, 0) == 258
    finally: kernel.CloseHandle(handle)


def require_stopped(app, checker=None):
    """Read-only preflight; must precede any transition marker, download or seal."""
    checker = checker or _alive_from_lock
    for name in ('bot.lock', 'shadow.lock'):
        if checker(Path(app) / name):
            raise sh.HandoffError(name + ' process is running; stop it normally before handoff')


@contextlib.contextmanager
def transition_lock(app, retain_on_success=False, keep_on_error=True):
    path = Path(app) / 'data/handoff-transition.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    success = False
    try:
        os.write(fd, str(os.getpid()).encode()); os.close(fd)
        yield
        success = True
    finally:
        # Crash/exception marker is deliberately persistent. SessionGuard must refuse startup.
        # keep_on_error=False is only for operations that never change active files (receive dry-run).
        if (success and not retain_on_success) or (not success and not keep_on_error):
            path.unlink(missing_ok=True)


def observer(app, credentials_file=None):
    """Trusted runtime modules, read-only API methods and lease fetch; no CAS/order call."""
    app = Path(app).resolve(strict=True)
    api = _load(app / 'upbit.py', '_handoff_readonly_upbit')
    guard = _load(app / 'session_guard.py', '_handoff_readonly_session_guard')
    if credentials_file: api.ENV_FILES.append(Path(credentials_file))
    def observe():
        started = time.time()
        config = json.loads((app / 'config.json').read_text(encoding='utf-8'))
        counts = {}
        for state in ('wait', 'watch'):
            count, page = 0, 1
            while True:
                params = {'state': state, 'limit': 100, 'page': page}
                rows = api._get('/v1/orders/open', params, headers=api._auth(params))
                count += len(rows)
                if len(rows) < 100: break
                page += 1
            counts[state] = count
        account = api.accounts()
        quantities = {'KRW-' + row['currency']: str(Decimal(row['balance']) + Decimal(row['locked']))
                      for row in account if row['currency'] != 'KRW'}
        # Lease is read LAST so source ownership cannot be reported from a pre-API snapshot.
        _, lease = guard.GitStore(app / 'data/session_git', config.get('session', {}).get('remote', guard.DEFAULT_REMOTE)).read()
        if not 0 <= time.time() - started <= 30:
            raise sh.HandoffError('Read-only observations exceeded the 30-second freshness bound')
        return {'checked_at_epoch': started, 'bot_stopped': not _alive_from_lock(app / 'bot.lock'),
                'shadow_stopped': not _alive_from_lock(app / 'shadow.lock'),
                'source_transition_locked': (app / 'data/handoff-transition.lock').read_text().strip() == str(os.getpid()),
                'remote_owner': lease.get('owner'), 'remote_request': lease.get('request'),
                'remote_checkpoint': lease.get('checkpoint'), 'exchange_wait_count': counts['wait'],
                'exchange_watch_count': counts['watch'], 'account_read_ok': True,
                'account_quantities': quantities}
    return observe


def request_graceful_stop(app, timeout=180, checker=None, clock=None, sleep=None):
    """Only a user-authorized publish calls this; never kills or changes a lease."""
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 180:
        raise sh.HandoffError('Graceful stop timeout must be between 0 and 180 seconds')
    app = Path(app)
    checker, clock, sleep = checker or _alive_from_lock, clock or time.monotonic, sleep or time.sleep
    def pending_clear():
        state = json.loads((app / 'data/live/live.json').read_bytes())
        if state.get('pending_order') or state.get('risk_pending_orders'):
            raise sh.HandoffError('Uncertain local orders block handoff shutdown; reconcile without cancellation')
    pending_clear()  # Do not request any shutdown if acceptance/fill status is unknown.
    for name in ('stop.txt', 'stop_shadow.txt'):
        file = app / name
        if not file.exists(): file.write_text('user-authorized final handoff graceful stop', encoding='utf-8')
    started = clock()
    while True:
        elapsed = clock() - started
        if elapsed > timeout:
            raise sh.HandoffError('Graceful shutdown timed out; stop requests retained, no force kill/restart')
        if not any(checker(app / name) for name in ('bot.lock', 'shadow.lock')):
            pending_clear()
            return {'source_stopped': True, 'graceful_stop_requested': True, 'waited_seconds': elapsed}
        if elapsed >= timeout:
            raise sh.HandoffError('Graceful shutdown timed out; stop requests retained, no force kill/restart')
        sleep(min(1., timeout - elapsed))


def publish_final(args, root):
    if not args.publish:
        return {'publish': False, 'stop_source_executed': False,
                'requires': ['source normal shutdown', 'fresh final ZIP', 'trusted recipient certificate pin',
                             'released matching lease', 'no uncertain or exchange orders'],
                'package_validated': bool(args.package and sh.inspect_final_zip(args.package))}
    stopped = None
    if getattr(args, 'stop_source', False):
        # Stop first: installing the Guard's transition marker while trading would interrupt it.
        stopped = request_graceful_stop(args.app, getattr(args, 'stop_timeout', 180))
    # Without --stop-source a running worker refuses here, before any marker, export or seal.
    require_stopped(args.app)
    with transition_lock(args.app):
        if not args.package:
            if not args.export_tool: raise sh.HandoffError('Existing final ZIP or explicit approved exporter required')
            output = root / 'outgoing' / str(time.time_ns()); output.mkdir(parents=True)
            sh.run_command([sys.executable, args.export_tool, '--app', args.app,
                            '--destination', output, '--final'], timeout=600)
            packages = list(output.glob('*.zip'))
            if len(packages) != 1: raise sh.HandoffError('Final exporter did not produce exactly one package')
            args.package = packages[0]
        envelope = root / 'sealed' / (str(time.time_ns()) + '.cms'); envelope.parent.mkdir(exist_ok=True)
        result = sh.seal_and_transmit(args.package, args.app, args.recipient_cert, args.recipient_pin,
            envelope, args.remote, observer(args.app, args.credentials_file), args.openssl)
        if stopped is not None: result.update(stopped)
        return result


def download_encrypted(remote, branch, destination, runner=subprocess.run):
    if not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?', remote):
        raise sh.HandoffError('Explicit credential-free GitHub destination required')
    if not re.fullmatch(r'private-handoff/[a-f0-9]{12}/[a-f0-9]{64}', branch):
        raise sh.HandoffError('Only a dedicated encrypted handoff branch is allowed')
    destination = Path(destination)
    if destination.exists(): raise sh.HandoffError('Incoming destination already exists')
    destination.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='receive-sealed-') as folder:
        sh.run_command(['git', 'init', '--quiet'], cwd=folder, runner=runner)
        sh.run_command(['git', 'fetch', '--quiet', '--depth=1', remote, 'refs/heads/' + branch], cwd=folder, runner=runner)
        receipt_result = sh.run_command(['git', 'show', 'FETCH_HEAD:receipt.json'], cwd=folder, runner=runner)
        if len(receipt_result.stdout) > 8192: raise sh.HandoffError('Public receipt too large')
        receipt = json.loads(receipt_result.stdout)
        path = destination / 'handoff.cms'
        # Stream ciphertext to disk instead of loading a potentially large archive into memory.
        with path.open('xb') as output:
            result = runner(['git', 'show', 'FETCH_HEAD:handoff.cms'], cwd=folder,
                env={**sh.safe_env(), 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never'},
                stdout=output, stderr=subprocess.PIPE, timeout=600,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode: raise sh.HandoffError('Encrypted blob download failed')
        plan = sh.transport_plan(path, receipt, remote)
        if plan['branch'] != branch: raise sh.HandoffError('Branch does not match ciphertext receipt')
        (destination / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    return path, receipt


def receive_final(args, root):
    identities = (root / 'identities').resolve()
    if not args.private_key.resolve().is_relative_to(identities):
        raise sh.HandoffError('Private key must remain inside the selected private_handoff/identities')
    # Refuse before download or marker: the Guard would otherwise stop a running receiver.
    require_stopped(args.app)
    incoming = root / 'incoming' / str(time.time_ns())
    envelope, receipt = download_encrypted(args.remote, args.branch, incoming)
    if receipt['recipient_certificate_sha256'] != args.recipient_pin.lower():
        raise sh.HandoffError('Transport recipient differs from trusted local identity')
    package = incoming / 'final.zip'
    sh.decrypt_package(envelope, args.recipient_cert, args.recipient_pin, args.private_key, package, args.openssl)
    # Dry-run never changes active files, so its own marker is removed even on validation failure.
    with transition_lock(args.app, keep_on_error=bool(args.apply)):
        return sh.restore_state(package, args.app, observer(args.app, args.credentials_file),
                                root / 'restore-backups' / str(time.time_ns()), args.apply)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--private-root', type=Path, required=True,
                        help='Local private_handoff directory; never a tracked repository directory')
    parser.add_argument('--openssl', type=Path, default=sh.DEFAULT_OPENSSL)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init-recipient'); init.add_argument('--name', required=True)
    publish = commands.add_parser('publish')
    for command in (publish,):
        command.add_argument('--app', type=Path, required=True)
        command.add_argument('--package', type=Path)
        command.add_argument('--export-tool', type=Path)
        command.add_argument('--recipient-cert', type=Path, required=True)
        command.add_argument('--recipient-pin', required=True)
        command.add_argument('--remote', required=True)
        command.add_argument('--credentials-file', type=Path)
        command.add_argument('--publish', action='store_true')
        command.add_argument('--stop-source', action='store_true', help='Only with --publish: request normal shutdown before final export')
        command.add_argument('--stop-timeout', type=float, default=180, help='Normal shutdown wait, maximum 180 seconds')
    receive = commands.add_parser('receive')
    for name in ('remote', 'branch', 'recipient-pin'):
        receive.add_argument('--' + name, required=True)
    for name in ('app', 'recipient-cert', 'private-key'):
        receive.add_argument('--' + name, type=Path, required=True)
    receive.add_argument('--credentials-file', type=Path)
    receive.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = args.private_root.resolve()
    if root.name != 'private_handoff': raise sh.HandoffError('Select an explicitly private_handoff root')
    root.mkdir(parents=True, exist_ok=True)
    if args.command == 'init-recipient':
        result = init_recipient(root, args.name, args.openssl)
    elif args.command == 'publish':
        result = publish_final(args, root)
    else:
        result = receive_final(args, root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__': main()
