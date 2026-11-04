"""OPT-IN real OpenSSL fake-data roundtrip. No network, API, ledger, lease, or bot calls."""
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

import handoff_cli
import secure_handoff as sh


def selftest():
    version = sh.run_command([sh.DEFAULT_OPENSSL, 'version']).stdout.decode('utf-8', errors='replace').strip()
    with tempfile.TemporaryDirectory(prefix='fake-crypto-') as folder:
        root = Path(folder)
        identity = handoff_cli.init_recipient(root / 'private_handoff', 'selftest')
        cert = Path(identity['public_certificate']); key = cert.with_name('private-key.pem')
        package = root / 'fake-final.zip'
        ledger = b'{"positions":{},"pending_order":null,"risk_pending_orders":{}}'
        with zipfile.ZipFile(package, 'w') as archive:
            archive.writestr(sh.LEDGER_PATH, ledger)
            archive.writestr(sh.PROTECTION_PATH, '{"markets":["KRW-DOGE"]}')
            archive.writestr('runtime/config.json', '{"live":{"user_protection_required":true}}')
            archive.writestr('manifest.json', json.dumps({'final_export_requested': True,
                'live_restore_allowed': True, 'ledger_changed_during_export': False,
                'env_files_included': False, 'missing_during_export': [],
                'pnl_baseline_present': False, 'pnl_baseline_sha256': None,
                'ledger_sha256': hashlib.sha256(ledger).hexdigest()}))
        sealed, restored = root / 'fake.cms', root / 'restored.zip'
        receipt = sh.encrypt_package(package, cert, identity['public_certificate_sha256'], sealed)
        sh.decrypt_package(sealed, cert, identity['public_certificate_sha256'], key, restored)
        if sh.sha256_file(package) != sh.sha256_file(restored): raise AssertionError('Roundtrip mismatch')
        raw = bytearray(sealed.read_bytes()); raw[-20] ^= 1
        tampered = root / 'tampered.cms'; tampered.write_bytes(raw)
        rejected = False
        try: sh.decrypt_package(tampered, cert, identity['public_certificate_sha256'], key, root / 'bad.zip')
        except sh.HandoffError: rejected = True
        if not rejected or (root / 'bad.zip').exists(): raise AssertionError('Tampered output was accepted')
        return {'openssl_version': version, 'fake_data_only': True, 'roundtrip': True,
                'tamper_rejected': True, 'cipher': receipt['cipher'],
                'key_transport': receipt['key_transport'], 'network_calls': 0}


if __name__ == '__main__':
    print(json.dumps(selftest(), ensure_ascii=False))
