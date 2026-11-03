"""Staged private handoff transport. Does not trade, stop bots, change leases or restore.

Native OpenSSL CMS performs recipient encryption and authenticated encryption.
Only an authenticated-enveloped CMS blob and public transport receipt may be
pushed. Plaintext ZIPs, manifests, keys, and runtime files are never Git inputs.
The host must supply fresh read-only source evidence before and after sealing.
"""
import datetime as dt
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
import zipfile
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'v5'))
import asset_protection

DEFAULT_OPENSSL = Path('C:/Program Files/Git/usr/bin/openssl.exe')
AUTH_ENVELOPED_DATA_OID = '1.2.840.113549.1.9.16.1.23'
LEDGER_PATH = 'runtime/data/live/live.json'
PROTECTION_PATH = 'runtime/data/user_protected_assets.json'


class HandoffError(RuntimeError):
    pass


def protected_names(values):
    if not isinstance(values, list): raise HandoffError('Protected market list is malformed')
    result = set()
    for value in values:
        if not isinstance(value, str): raise HandoffError('Protected market is malformed')
        value = value.strip().upper()
        if not value.startswith('KRW-'): value = 'KRW-' + value
        if not re.fullmatch(r'KRW-[A-Z0-9]+', value): raise HandoffError('Protected market is malformed')
        result.add(value)
    return result


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def normalized_checkpoint(ledger_bytes):
    """Exactly SessionGuard's JSON checkpoint; deliberately NOT raw file SHA256."""
    ledger = json.loads(ledger_bytes.decode('utf-8-sig'))
    return hashlib.sha256(json.dumps(ledger, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def inspect_final_zip(package, max_uncompressed_bytes=2 * 1024 ** 3):
    with zipfile.ZipFile(package) as archive:
        members = archive.infolist()
        names = [item.filename for item in members]
        if len(names) != len(set(names)) or len(names) > 100000:
            raise HandoffError('Duplicate or excessive archive members')
        if sum(item.file_size for item in members) > max_uncompressed_bytes:
            raise HandoffError('Archive exceeds configured decompression size limit')
        for item in members:
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in item.filename or ':' in item.filename
                    or stat.S_ISLNK(item.external_attr >> 16)):
                raise HandoffError('Unsafe archive member path')
            lower = path.name.lower()
            if (lower.endswith(('.zip', '.cms')) or 'private_handoff' in {part.lower() for part in path.parts}
                    or item.filename in {'runtime/data/handoff-restore-pending.json', 'runtime/data/reconciliation/APPLYING'}):
                raise HandoffError('Nested handoff archive or incomplete restore marker in source package')
            if ('session_git' in {part.lower() for part in path.parts}
                    or any(part.lower() in {'.git', '.ssh', '.aws', '.codex', '.claude'} for part in path.parts)):
                raise HandoffError('Git/auth internals are not valid transferable state')
            if (lower.startswith('.env') or lower in {'auth.json', '.credentials.json', 'credentials.json',
                    '.git-credentials', '.netrc', '_netrc', 'credentials', 'id_rsa', 'id_ed25519'}
                    or path.suffix.lower() in {'.key', '.pem', '.pfx', '.p12', '.keystore', '.jks'}):
                raise HandoffError('Credential/key file in source archive')
        if 'manifest.json' not in names or LEDGER_PATH not in names or 'runtime/config.json' not in names:
            raise HandoffError('Required final handoff files missing')
        manifest = json.loads(archive.read('manifest.json'))
        if (manifest.get('final_export_requested') is not True
                or manifest.get('live_restore_allowed') is not True
                or manifest.get('ledger_changed_during_export') is not False
                or manifest.get('env_files_included') is not False
                or manifest.get('missing_during_export')):
            raise HandoffError('Reference-only or unstable package cannot be transmitted as final state')
        ledger = archive.read(LEDGER_PATH)
        raw_sha = hashlib.sha256(ledger).hexdigest()
        if manifest.get('ledger_sha256') != raw_sha:
            raise HandoffError('Archive raw ledger SHA mismatch')
        state = json.loads(ledger)
        if state.get('pending_order') or state.get('risk_pending_orders'):
            raise HandoffError('Archive has unresolved local orders')
        config_bytes = archive.read('runtime/config.json')
        config = json.loads(config_bytes)
        if config.get('live', {}).get('user_protection_required') is not True or PROTECTION_PATH not in names:
            raise HandoffError('Mandatory user protection is missing')
        protection = archive.read(PROTECTION_PATH)
        protection_data = json.loads(protection)
        try:
            protected = asset_protection.effective(protection_data,config.get('live', {}).get('protected_markets', []))
            released = asset_protection.explicit_release(protection_data,'KRW-DOGE')
        except (ValueError,TypeError):
            raise HandoffError('User protection policy malformed') from None
        if 'KRW-DOGE' not in protected and not released:
            raise HandoffError('DOGE whole-asset protection must survive handoff')
        baseline_present = 'runtime/pnl_baseline.json' in names
        baseline_sha = hashlib.sha256(archive.read('runtime/pnl_baseline.json')).hexdigest() if baseline_present else None
        if ('pnl_baseline_present' in manifest and manifest['pnl_baseline_present'] is not baseline_present
                or 'pnl_baseline_sha256' in manifest and manifest['pnl_baseline_sha256'] != baseline_sha):
            raise HandoffError('Pnl baseline presence/hash differs from manifest')
        if archive.testzip() is not None:
            raise HandoffError('Archive CRC verification failed')
    return {'ledger_raw_sha256': raw_sha, 'ledger_checkpoint': normalized_checkpoint(ledger),
            'protection_sha256': hashlib.sha256(protection).hexdigest(),
            'runtime_config_sha256': hashlib.sha256(config_bytes).hexdigest(),
            'pnl_baseline_present': baseline_present, 'pnl_baseline_sha256': baseline_sha,
            'file_count': len(names), 'final': True}


def validate_source(package, app, evidence, now=None, evidence_max_age=30):
    """Validate host-provided fresh observations; this function makes no API calls.

    Evidence required: checked_at_epoch, bot_stopped, shadow_stopped,
    remote_owner, remote_request, remote_checkpoint, exchange_wait_count,
    exchange_watch_count, source_transition_locked. Unknown is never success.
    The host must hold its nontrading PC-handoff lock throughout both checks.
    """
    now = time.time() if now is None else now
    checked = evidence.get('checked_at_epoch')
    if not isinstance(checked, (int, float)) or not 0 <= now - checked <= evidence_max_age:
        raise HandoffError('Source observations missing or stale')
    for flag in ('bot_stopped', 'shadow_stopped', 'source_transition_locked'):
        if evidence.get(flag) is not True:
            raise HandoffError('Source shutdown/transition ownership not confirmed')
    for name in ('remote_owner', 'remote_request'):
        if name not in evidence or evidence[name] not in ('', None):
            raise HandoffError('Remote owner/handoff still active or unknown')
    for name in ('exchange_wait_count', 'exchange_watch_count'):
        if type(evidence.get(name)) is not int or evidence[name] != 0:
            raise HandoffError('Exchange open/reserved order reconciliation incomplete')
    app = Path(app)
    if (app / 'data/handoff-restore-pending.json').exists() or (app / 'data/reconciliation/APPLYING').exists():
        raise HandoffError('Incomplete local restore/reconciliation blocks source export')
    before = (app / 'data/live/live.json').read_bytes()
    state = json.loads(before)
    if state.get('pending_order') or state.get('risk_pending_orders'):
        raise HandoffError('Current source has unresolved local orders')
    package_info = inspect_final_zip(package)
    if package_info['ledger_raw_sha256'] != hashlib.sha256(before).hexdigest():
        raise HandoffError('Package is older than current source ledger')
    checkpoint = normalized_checkpoint(before)
    if not evidence.get('remote_checkpoint') or evidence['remote_checkpoint'] != checkpoint:
        raise HandoffError('Normalized ledger checkpoint differs from remote lease')
    protection = (app / 'data/user_protected_assets.json').read_bytes()
    if hashlib.sha256(protection).hexdigest() != package_info['protection_sha256']:
        raise HandoffError('User protection changed after packaging')
    if sha256_file(app / 'config.json') != package_info['runtime_config_sha256']:
        raise HandoffError('Runtime settings changed after packaging')
    baseline = app / 'pnl_baseline.json'
    if baseline.exists() != package_info['pnl_baseline_present']:
        raise HandoffError('Source pnl baseline presence changed or missing from package')
    if baseline.exists() and sha256_file(baseline) != package_info['pnl_baseline_sha256']:
        raise HandoffError('Source pnl baseline changed after packaging')
    if evidence.get('account_read_ok') is not True or not isinstance(evidence.get('account_quantities'), dict):
        raise HandoffError('Fresh source account quantities are unknown')
    for market, position in state.get('positions', {}).items():
        quantities = evidence['account_quantities']
        if market not in quantities or abs(Decimal(str(position['qty'])) - Decimal(str(quantities[market]))) > Decimal('0.00000001'):
            raise HandoffError('Source managed quantity differs from fresh account')
    if (app / 'data/live/live.json').read_bytes() != before:
        raise HandoffError('Source ledger changed during preflight')
    if (sha256_file(app / 'config.json') != package_info['runtime_config_sha256']
            or sha256_file(app / 'data/user_protected_assets.json') != package_info['protection_sha256']
            or baseline.exists() != package_info['pnl_baseline_present']
            or baseline.exists() and sha256_file(baseline) != package_info['pnl_baseline_sha256']):
        raise HandoffError('Source settings/protection/pnl baseline changed during preflight')
    return package_info


def safe_env():
    return {key: value for key, value in os.environ.items()
            if not re.search(r'API_KEY|ACCESS_KEY|SECRET|TOKEN|PASSWORD', key, re.I)}


def validate_receive(package, app, evidence, now=None, evidence_max_age=30, transaction_id=None):
    """Receiver is stopped; incoming checkpoint and live account must still match."""
    now = time.time() if now is None else now
    pending_marker = Path(app) / 'data/handoff-restore-pending.json'
    if pending_marker.exists():
        journal = json.loads(pending_marker.read_bytes())
        if transaction_id is None or journal.get('id') != transaction_id:
            raise HandoffError('Previous restore transaction requires explicit recovery')
    if (Path(app) / 'data/reconciliation/APPLYING').exists():
        raise HandoffError('Concurrent account reconciliation is applying')
    checked = evidence.get('checked_at_epoch')
    if not isinstance(checked, (int, float)) or not 0 <= now - checked <= evidence_max_age:
        raise HandoffError('Receiver observations missing or stale')
    if any(evidence.get(key) is not True for key in ('bot_stopped', 'shadow_stopped', 'source_transition_locked', 'account_read_ok')):
        raise HandoffError('Receiver must be stopped/locked with confirmed account read')
    if any(key not in evidence or evidence[key] not in ('', None) for key in ('remote_owner', 'remote_request')):
        raise HandoffError('Remote execution owner is active or unknown')
    if any(type(evidence.get(key)) is not int or evidence[key] != 0 for key in ('exchange_wait_count', 'exchange_watch_count')):
        raise HandoffError('Open orders require reconciliation, not automatic cancellation')
    info = inspect_final_zip(package)
    if not info['pnl_baseline_present'] and (Path(app) / 'pnl_baseline.json').exists():
        raise HandoffError('Source baseline is absent but receiver has one; explicit reconciliation required')
    if evidence.get('remote_checkpoint') != info['ledger_checkpoint']:
        raise HandoffError('Incoming normalized checkpoint is not the released source checkpoint')
    current = Path(app) / 'data/live/live.json'
    if current.exists():
        state = json.loads(current.read_bytes())
        if state.get('pending_order') or state.get('risk_pending_orders'):
            raise HandoffError('Receiver has unresolved old orders')
    quantities = evidence.get('account_quantities')
    if not isinstance(quantities, dict): raise HandoffError('Current account quantities are unknown')
    with zipfile.ZipFile(package) as archive:
        incoming = json.loads(archive.read(LEDGER_PATH))
        protection_union(app, archive)  # Validate all lists without discarding receiver protections.
    for market, position in incoming.get('positions', {}).items():
        if market not in quantities or abs(Decimal(str(position['qty'])) - Decimal(str(quantities[market]))) > Decimal('0.00000001'):
            raise HandoffError('Incoming managed quantity differs from fresh account')
    return info


def approved_restore_path(name):
    """Transfer state only; source code, Git internals, locks and identities are never restored."""
    if name in {'runtime/config.json', 'runtime/pnl_baseline.json'}: return True
    path = PurePosixPath(name)
    if not name.startswith('runtime/data/') or 'session_git' in path.parts: return False
    return path.suffix.lower() in {'.json', '.jsonl', '.log', '.csv', '.tsv', '.txt', '.html', '.md'} and not path.name.endswith(('.pid', '.lock', '.tmp'))


def protection_union(app, archive):
    config = json.loads(archive.read('runtime/config.json'))
    protection = json.loads(archive.read(PROTECTION_PATH))
    local={}; saved={'markets':[]}
    local_config = Path(app) / 'config.json'
    if local_config.exists():
        local = json.loads(local_config.read_bytes())
    local_protection = Path(app) / 'data/user_protected_assets.json'
    if local_protection.exists():
        saved=json.loads(local_protection.read_bytes())
    try:
        protection=asset_protection.merge(saved,protection,local.get('live',{}).get('protected_markets',[]),
                                          config.get('live',{}).get('protected_markets',[]))
    except (ValueError,TypeError): raise HandoffError('User protection merge failed') from None
    union=asset_protection.effective(protection)
    config.setdefault('live', {}).update(user_protection_required=True, protected_markets=sorted(union))
    protection.update(policy='user_managed_no_bot_orders',handoff_merge_policy='latest_explicit_choice_or_protective_union')
    return config, protection


def restore_state(package, app, observe_receiver, backup_directory, apply=False):
    """Dry-run by default. No order, cancellation, lease mutation or bot launch."""
    app, backup_directory = Path(app).resolve(), Path(backup_directory).resolve()
    info = validate_receive(package, app, observe_receiver())
    with zipfile.ZipFile(package) as archive:
        selected = [name for name in archive.namelist() if approved_restore_path(name)]
        config, protection = protection_union(app, archive)
        overrides = {'runtime/config.json': json.dumps(config, ensure_ascii=False, indent=2).encode(),
                     PROTECTION_PATH: json.dumps(protection, ensure_ascii=False, indent=2).encode()}
        for name in selected:
            destination = (app / name.removeprefix('runtime/')).resolve()
            if not destination.is_relative_to(app): raise HandoffError('Destination junction/symlink escapes runtime')
        if not apply: return {'applied': False, 'validated': True, 'files_to_restore': len(selected), **info}
        if backup_directory.exists(): raise HandoffError('Backup directory already exists')
        backup_directory.mkdir(parents=True)
        staged = backup_directory / 'incoming'
        staged.mkdir()
        existed = []
        for name in selected:
            relative = name.removeprefix('runtime/')
            destination = app / relative
            if destination.exists():
                saved = backup_directory / 'before' / relative
                saved.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(destination, saved)
                existed.append(relative)
            incoming = staged / relative
            incoming.parent.mkdir(parents=True, exist_ok=True); incoming.write_bytes(overrides.get(name, archive.read(name)))
        validate_receive(package, app, observe_receiver())
        transaction_id = uuid.uuid4().hex
        journal_path = app / 'data/handoff-restore-pending.json'
        journal = {'id': transaction_id, 'state': 'applying', 'backup_directory': str(backup_directory),
            'package_sha256': sha256_file(package),
            'expected_files': {name.removeprefix('runtime/'): sha256_file(staged / name.removeprefix('runtime/')) for name in selected},
            'old_files': {relative: sha256_file(backup_directory / 'before' / relative) for relative in existed}}
        with journal_path.open('x', encoding='utf-8') as stream:
            json.dump(journal, stream, ensure_ascii=False, indent=2)
        installed = []
        try:
            for name in selected:
                relative = name.removeprefix('runtime/')
                destination = app / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged / relative, destination); installed.append(relative)
            validate_receive(package, app, observe_receiver(), transaction_id=transaction_id)
            expected = {LEDGER_PATH: info['ledger_raw_sha256'],
                        **{name: hashlib.sha256(value).hexdigest() for name, value in overrides.items()}}
            if info['pnl_baseline_present']:
                expected['runtime/pnl_baseline.json'] = info['pnl_baseline_sha256']
            for name, digest in expected.items():
                if sha256_file(app / name.removeprefix('runtime/')) != digest:
                    raise HandoffError('Post-restore state/settings/protection verification failed')
            journal_path.unlink()  # Only after file hashes AND fresh ownership/account postcheck.
        except BaseException:
            try:
                for relative in reversed(installed):
                    destination = app / relative
                    if relative in existed: shutil.copyfile(backup_directory / 'before' / relative, destination)
                    else: destination.unlink(missing_ok=True)
                journal['state'] = 'rolled_back_requires_verification'
            except BaseException:
                journal['state'] = 'rollback_failed_do_not_start'
                raise
            finally:
                journal_path.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding='utf-8')
            raise
    return {'applied': True, 'validated': True, 'files_restored': len(selected),
            'recipient_execution_started': False, **info}


def run_command(args, cwd=None, runner=subprocess.run, timeout=120):
    result = runner([str(arg) for arg in args], cwd=cwd, env={**safe_env(),
        'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never'}, capture_output=True,
        timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise HandoffError('External handoff command failed; no fallback to plaintext')
    return result


def certificate_pin(certificate, expected):
    certificate = Path(certificate)
    raw = certificate.read_bytes()
    if b'BEGIN CERTIFICATE' not in raw or b'PRIVATE KEY' in raw:
        raise HandoffError('Recipient certificate must contain public certificate only')
    digest = hashlib.sha256(raw).hexdigest()
    if not re.fullmatch(r'[a-fA-F0-9]{64}', str(expected)) or digest != expected.lower():
        raise HandoffError('Recipient certificate does not match trusted out-of-band pin')
    return digest


def _der_length(data, offset):
    first = data[offset]; offset += 1
    if first < 128: return first, offset
    size = first & 127
    if size == 0 or size > 8 or offset + size > len(data): raise HandoffError('Invalid CMS DER length')
    return int.from_bytes(data[offset:offset + size], 'big'), offset + size


def validate_encrypted_container(path):
    """Reject renamed ZIP/plaintext; require CMS authenticated-enveloped content type."""
    with Path(path).open('rb') as stream: prefix = stream.read(256)
    try:
        if prefix[0] != 0x30: raise HandoffError('Not a CMS DER sequence')
        _, offset = _der_length(prefix, 1)
        if prefix[offset] != 0x06: raise HandoffError('CMS content type missing')
        length, offset = _der_length(prefix, offset + 1)
        oid = prefix[offset:offset + length]
        if len(oid) != length: raise HandoffError('Incomplete CMS content type')
        parts, value = [oid[0] // 40, oid[0] % 40], 0
        for byte in oid[1:]:
            value = (value << 7) | (byte & 127)
            if not byte & 128: parts.append(value); value = 0
        if '.'.join(map(str, parts)) != AUTH_ENVELOPED_DATA_OID:
            raise HandoffError('CMS container is not authenticated-enveloped data')
    except (IndexError, ValueError) as error:
        raise HandoffError('Malformed CMS envelope') from error
    return True


def encrypt_package(package, certificate, trusted_pin, destination, openssl=DEFAULT_OPENSSL, runner=subprocess.run):
    pin = certificate_pin(certificate, trusted_pin)
    destination = Path(destination)
    if destination.exists(): raise HandoffError('Refuse to overwrite existing sealed package')
    source_hash = sha256_file(package)
    temporary = destination.with_suffix(destination.suffix + '.partial')
    try:
        run_command([openssl, 'cms', '-encrypt', '-binary', '-aes-256-gcm', '-in', package,
            '-out', temporary, '-outform', 'DER', '-recip', certificate,
            '-keyopt', 'rsa_padding_mode:oaep', '-keyopt', 'rsa_oaep_md:sha256',
            '-keyopt', 'rsa_mgf1_md:sha256'], runner=runner, timeout=600)
        validate_encrypted_container(temporary)
        if sha256_file(package) != source_hash: raise HandoffError('Package changed while sealing')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {'format': 'CMS-AuthEnvelopedData', 'cipher': 'AES-256-GCM',
            'key_transport': 'RSA-OAEP-SHA256', 'recipient_certificate_sha256': pin,
            'ciphertext_sha256': sha256_file(destination), 'version': 1}


def decrypt_package(envelope, certificate, trusted_pin, private_key, destination,
                    openssl=DEFAULT_OPENSSL, runner=subprocess.run):
    certificate_pin(certificate, trusted_pin)
    validate_encrypted_container(envelope)
    destination = Path(destination)
    if destination.exists(): raise HandoffError('Refuse to overwrite restored package')
    temporary = destination.with_suffix(destination.suffix + '.partial')
    try:
        # OpenSSL verifies GCM authentication before successful return; partial output stays private.
        run_command([openssl, 'cms', '-decrypt', '-binary', '-inform', 'DER', '-in', envelope,
                     '-recip', certificate, '-inkey', private_key, '-out', temporary],
                    runner=runner, timeout=600)
        inspect_final_zip(temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def transport_plan(envelope, receipt, remote):
    if not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?', str(remote)):
        raise HandoffError('Explicit GitHub destination required; no embedded credentials or arbitrary endpoints')
    validate_encrypted_container(envelope)
    public_keys = {'format', 'cipher', 'key_transport', 'recipient_certificate_sha256', 'ciphertext_sha256', 'version'}
    if set(receipt) != public_keys or receipt.get('version') != 1:
        raise HandoffError('Only the public transport receipt fields may be published')
    digest = sha256_file(envelope)
    if receipt.get('ciphertext_sha256') != digest or receipt.get('format') != 'CMS-AuthEnvelopedData':
        raise HandoffError('Ciphertext receipt mismatch')
    if receipt.get('cipher') != 'AES-256-GCM' or receipt.get('key_transport') != 'RSA-OAEP-SHA256':
        raise HandoffError('Unapproved encryption parameters')
    pin = receipt.get('recipient_certificate_sha256', '')
    if not re.fullmatch(r'[a-f0-9]{64}', pin): raise HandoffError('Recipient pin missing')
    return {'remote': remote, 'branch': 'private-handoff/' + pin[:12] + '/' + digest,
            'files': ['handoff.cms', 'receipt.json'], 'receipt': dict(receipt)}


def transmit_encrypted(envelope, receipt, remote, runner=subprocess.run):
    """Called by host only after final preflight; never stages source repo/plaintext."""
    plan = transport_plan(envelope, receipt, remote)
    with tempfile.TemporaryDirectory(prefix='sealed-handoff-') as folder:
        folder = Path(folder)
        shutil.copyfile(envelope, folder / 'handoff.cms')
        (folder / 'receipt.json').write_text(json.dumps(receipt, sort_keys=True), encoding='utf-8')
        if sha256_file(folder / 'handoff.cms') != receipt['ciphertext_sha256']:
            raise HandoffError('Ciphertext changed during staging')
        commands = [['git', 'init', '--quiet'], ['git', 'add', '--', 'handoff.cms', 'receipt.json'],
            ['git', '-c', 'user.name=Private Handoff', '-c', 'user.email=handoff@localhost',
             '-c', 'commit.gpgsign=false', 'commit', '--quiet', '-m', 'Encrypted private handoff'],
            ['git', 'push', '--porcelain', remote, 'HEAD:refs/heads/' + plan['branch']]]
        for command in commands: run_command(command, cwd=folder, runner=runner)
    return {'branch': plan['branch'], 'ciphertext_sha256': receipt['ciphertext_sha256'], 'pushed': True}


def seal_and_transmit(package, app, certificate, trusted_pin, envelope, remote,
                      observe_source, openssl=DEFAULT_OPENSSL, runner=subprocess.run):
    """Host integration API. observe_source must query fresh lease/exchange/process state.

    No process shutdown, lease release, trading toggle or recipient startup occurs
    here. Delivery success does not mean receipt/restore/startup was acknowledged.
    """
    validate_source(package, app, observe_source())
    receipt = encrypt_package(package, certificate, trusted_pin, envelope, openssl, runner)
    validate_source(package, app, observe_source())
    result = transmit_encrypted(envelope, receipt, remote, runner)
    result['recipient_restore_confirmed'] = False
    return result
