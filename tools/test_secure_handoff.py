import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('secure_handoff', Path(__file__).with_name('secure_handoff.py'))
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)

# DER ContentInfo OID id-smime-ct-authEnvelopedData, followed by a test placeholder.
# This is NOT a cryptographic roundtrip; native OpenSSL integration remains host verification.
FAKE_CMS = bytes.fromhex('3016060b2a864886f70d0109100117a007040566616b6521')


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.app = self.root / 'app'; (self.app / 'data/live').mkdir(parents=True)
        self.ledger = b'{\n "positions": {}, "pending_order": null, "risk_pending_orders": {}\n}'
        (self.app / 'data/live/live.json').write_bytes(self.ledger)
        self.protection = b'{"markets": ["KRW-DOGE"]}'
        (self.app / 'data/user_protected_assets.json').write_bytes(self.protection)
        (self.app / 'config.json').write_text(json.dumps({'live': {'user_protection_required': True}}))
        self.cert = self.root / 'recipient.pem'
        self.cert.write_bytes(b'-----BEGIN CERTIFICATE-----\nTEST PUBLIC CERT\n-----END CERTIFICATE-----')
        self.pin = handoff.sha256_file(self.cert)
        self.package = self.root / 'final.zip'
        self.evidence = {'checked_at_epoch': 1000, 'bot_stopped': True, 'shadow_stopped': True,
            'source_transition_locked': True, 'remote_owner': '', 'remote_request': '',
            'remote_checkpoint': handoff.normalized_checkpoint(self.ledger),
            'exchange_wait_count': 0, 'exchange_watch_count': 0,
            'account_read_ok': True, 'account_quantities': {}}
        self.make_package()

    def tearDown(self): self.temp.cleanup()

    def make_package(self, **changes):
        manifest = {'final_export_requested': True, 'live_restore_allowed': True,
            'ledger_changed_during_export': False, 'env_files_included': False,
            'missing_during_export': [], 'ledger_sha256': hashlib.sha256(self.ledger).hexdigest(), **changes}
        with zipfile.ZipFile(self.package, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(manifest))
            archive.writestr(handoff.LEDGER_PATH, self.ledger)
            archive.writestr(handoff.PROTECTION_PATH, self.protection)
            archive.writestr('runtime/config.json', json.dumps({'live': {'user_protection_required': True}}))
            if getattr(self, 'baseline', None) is not None:
                archive.writestr('runtime/pnl_baseline.json', self.baseline)

    def test_raw_file_sha_is_not_normalized_checkpoint(self):
        info = handoff.validate_source(self.package, self.app, self.evidence, now=1001)
        self.assertNotEqual(info['ledger_raw_sha256'], info['ledger_checkpoint'])
        compact = json.dumps(json.loads(self.ledger)).encode()
        self.assertEqual(handoff.normalized_checkpoint(compact), info['ledger_checkpoint'])

    def test_reference_only_package_is_rejected(self):
        self.make_package(live_restore_allowed=False)
        with self.assertRaises(handoff.HandoffError): handoff.inspect_final_zip(self.package)

    def test_unknown_running_or_stale_source_rejected(self):
        for changes in ({'bot_stopped': False}, {'shadow_stopped': None}, {'checked_at_epoch': 900},
                        {'remote_owner': 'someone'}, {'exchange_wait_count': 1},
                        {'exchange_watch_count': None}, {'source_transition_locked': False}):
            with self.subTest(changes=changes), self.assertRaises(handoff.HandoffError):
                handoff.validate_source(self.package, self.app, {**self.evidence, **changes}, now=1001)

    def test_remote_normalized_checkpoint_must_match(self):
        with self.assertRaises(handoff.HandoffError):
            handoff.validate_source(self.package, self.app, {**self.evidence,
                'remote_checkpoint': hashlib.sha256(self.ledger).hexdigest()}, now=1001)

    def test_changed_source_and_protection_rejected(self):
        (self.app / 'data/live/live.json').write_text('{"positions": {}}')
        with self.assertRaises(handoff.HandoffError): handoff.validate_source(self.package, self.app, self.evidence, now=1001)

    def test_changed_runtime_settings_and_unknown_account_rejected(self):
        (self.app / 'config.json').write_text('{}')
        with self.assertRaises(handoff.HandoffError): handoff.validate_source(self.package, self.app, self.evidence, now=1001)
        (self.app / 'config.json').write_text(json.dumps({'live': {'user_protection_required': True}}))
        with self.assertRaises(handoff.HandoffError):
            handoff.validate_source(self.package, self.app, {**self.evidence, 'account_read_ok': False}, now=1001)

    def test_current_source_baseline_cannot_be_omitted_or_changed(self):
        baseline = self.app / 'pnl_baseline.json'; baseline.write_text('{"day":"original"}')
        with self.assertRaises(handoff.HandoffError): handoff.validate_source(self.package, self.app, self.evidence, now=1001)
        self.baseline = baseline.read_bytes(); self.make_package()
        info = handoff.validate_source(self.package, self.app, self.evidence, now=1001)
        self.assertTrue(info['pnl_baseline_present'])
        baseline.write_text('{"day":"changed"}')
        with self.assertRaises(handoff.HandoffError): handoff.validate_source(self.package, self.app, self.evidence, now=1001)

    def test_receiver_unknown_old_baseline_requires_reconciliation(self):
        (self.app / 'pnl_baseline.json').write_text('{"day":"old"}')
        with self.assertRaises(handoff.HandoffError): handoff.validate_receive(self.package, self.app, self.evidence, now=1001)

    def test_transferred_baseline_preserves_source_basis_and_backup(self):
        import time
        self.baseline = b'{"day":"source-existing-basis"}'; self.make_package()
        (self.app / 'pnl_baseline.json').write_text('{"day":"receiver-old-basis"}')
        self.evidence['checked_at_epoch'] = time.time()
        handoff.restore_state(self.package, self.app, lambda: self.evidence, self.root / 'backup', apply=True)
        self.assertEqual((self.app / 'pnl_baseline.json').read_bytes(), self.baseline)
        self.assertIn('receiver-old-basis', (self.root / 'backup/before/pnl_baseline.json').read_text())

    def test_baseline_manifest_mismatch_rejected(self):
        self.baseline = b'{"day":"source"}'
        self.make_package(pnl_baseline_present=True, pnl_baseline_sha256='0' * 64)
        with self.assertRaises(handoff.HandoffError): handoff.inspect_final_zip(self.package)
        (self.app / 'data/live/live.json').write_bytes(self.ledger)
        (self.app / 'data/user_protected_assets.json').write_text('{}')
        with self.assertRaises(handoff.HandoffError): handoff.validate_source(self.package, self.app, self.evidence, now=1001)

    def test_unresolved_risk_orders_rejected(self):
        self.ledger = b'{"risk_pending_orders":{"test":{"time":"now"}}}'
        self.make_package()
        with self.assertRaises(handoff.HandoffError): handoff.inspect_final_zip(self.package)

    def test_doge_protection_is_required(self):
        self.protection = b'{"markets": []}'
        self.make_package()
        with self.assertRaises(handoff.HandoffError): handoff.inspect_final_zip(self.package)

    def test_receiver_dryrun_preserves_old_ledger_and_apply_restores_without_code(self):
        self.evidence.update(account_read_ok=True, account_quantities={})
        self.evidence['checked_at_epoch'] = __import__('time').time()
        with zipfile.ZipFile(self.package, 'a') as archive:
            archive.writestr('runtime/bot.py', 'MUST NOT RESTORE CODE')
        before = (self.app / 'data/live/live.json').read_bytes()
        result = handoff.restore_state(self.package, self.app, lambda: self.evidence, self.root / 'backup')
        self.assertFalse(result['applied'])
        self.assertEqual((self.app / 'data/live/live.json').read_bytes(), before)
        result = handoff.restore_state(self.package, self.app, lambda: self.evidence, self.root / 'backup', apply=True)
        self.assertTrue(result['applied'])
        self.assertFalse((self.app / 'bot.py').exists())
        self.assertFalse((self.app / 'data/session_git/config').exists())
        self.assertFalse((self.app / 'data/handoff-restore-pending.json').exists())

    def test_receiver_account_mismatch_rejects(self):
        self.ledger = b'{"positions":{"KRW-A":{"qty":2}}}'
        self.make_package()
        evidence = {**self.evidence, 'account_read_ok': True, 'account_quantities': {'KRW-A': 1},
                    'remote_checkpoint': handoff.normalized_checkpoint(self.ledger)}
        with self.assertRaises(handoff.HandoffError): handoff.validate_receive(self.package, self.app, evidence, now=1001)

    def test_receiver_extra_user_protections_are_preserved_as_union(self):
        (self.app / 'data/user_protected_assets.json').write_text('{"markets":["KRW-DOGE","KRW-BTC"]}')
        (self.app / 'config.json').write_text('{"live":{"protected_markets":["KRW-ETH"]}}')
        self.evidence['checked_at_epoch'] = __import__('time').time()
        result = handoff.restore_state(self.package, self.app, lambda: self.evidence, self.root / 'backup', apply=True)
        self.assertTrue(result['applied'])
        markets = json.loads((self.app / 'data/user_protected_assets.json').read_bytes())['markets']
        self.assertEqual(markets, ['KRW-BTC', 'KRW-DOGE', 'KRW-ETH'])
        config = json.loads((self.app / 'config.json').read_bytes())
        self.assertEqual(config['live']['protected_markets'], markets)
        self.assertTrue(config['live']['user_protection_required'])

    def test_git_and_auth_internals_are_not_transferable(self):
        for name in ('runtime/data/session_git/config', 'runtime/data/.aws/credentials', 'runtime/data/auth.json',
                     'runtime/data/private_handoff/old.zip', 'runtime/data/archive.zip'):
            self.make_package()
            with zipfile.ZipFile(self.package, 'a') as archive: archive.writestr(name, 'not transferable')
            with self.subTest(name=name), self.assertRaises(handoff.HandoffError): handoff.inspect_final_zip(self.package)

    def test_restore_postcheck_failure_rolls_back_changed_files(self):
        import time
        self.evidence['checked_at_epoch'] = time.time()
        calls = []
        def observe():
            calls.append(True)
            return {**self.evidence, 'remote_owner': 'new-owner' if len(calls) > 2 else ''}
        before = (self.app / 'config.json').read_bytes()
        with self.assertRaises(handoff.HandoffError):
            handoff.restore_state(self.package, self.app, observe, self.root / 'backup', apply=True)
        self.assertEqual((self.app / 'config.json').read_bytes(), before)
        marker = self.app / 'data/handoff-restore-pending.json'
        self.assertEqual(json.loads(marker.read_bytes())['state'], 'rolled_back_requires_verification')
        with self.assertRaises(handoff.HandoffError):
            handoff.validate_receive(self.package, self.app, self.evidence)

    def test_pending_marker_exists_before_first_target_write(self):
        from unittest.mock import patch
        import os, time
        self.evidence['checked_at_epoch'] = time.time()
        original = os.replace
        def replace(source, target):
            self.assertTrue((self.app / 'data/handoff-restore-pending.json').exists())
            return original(source, target)
        with patch.object(handoff.os, 'replace', replace):
            handoff.restore_state(self.package, self.app, lambda: self.evidence, self.root / 'backup', apply=True)

    def test_secret_file_or_path_traversal_is_rejected(self):
        for name in ('runtime/.env', '../escape', 'runtime/private.key'):
            self.make_package()
            with zipfile.ZipFile(self.package, 'a') as archive: archive.writestr(name, 'DO NOT SEND')
            with self.subTest(name=name), self.assertRaises(handoff.HandoffError): handoff.inspect_final_zip(self.package)

    def test_recipient_pin_mismatch_blocks_before_crypto(self):
        with self.assertRaises(handoff.HandoffError):
            handoff.encrypt_package(self.package, self.cert, '0' * 64, self.root / 'sealed.cms', runner=lambda *a, **k: self.fail())

    def fake_encrypt(self, args, **kwargs):
        self.crypto_args = args
        Path(args[args.index('-out') + 1]).write_bytes(FAKE_CMS)
        return types.SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

    def test_cipher_command_is_recipient_oaep_and_authenticated_gcm(self):
        out = self.root / 'sealed.cms'
        receipt = handoff.encrypt_package(self.package, self.cert, self.pin, out, runner=self.fake_encrypt)
        self.assertIn('-aes-256-gcm', self.crypto_args)
        self.assertIn('rsa_padding_mode:oaep', self.crypto_args)
        self.assertIn('rsa_oaep_md:sha256', self.crypto_args)
        self.assertEqual(receipt['ciphertext_sha256'], handoff.sha256_file(out))
        self.assertNotIn('ledger_raw_sha256', receipt)

    def test_failed_crypto_never_falls_back_to_plain_zip(self):
        out = self.root / 'sealed.cms'
        with self.assertRaises(handoff.HandoffError):
            handoff.encrypt_package(self.package, self.cert, self.pin, out,
                runner=lambda *a, **k: types.SimpleNamespace(returncode=1))
        self.assertFalse(out.exists())
        self.assertFalse(out.with_suffix('.cms.partial').exists())

    def test_plaintext_renamed_ciphertext_cannot_be_transmitted(self):
        out = self.root / 'plain.cms'; out.write_bytes(self.package.read_bytes())
        with self.assertRaises(handoff.HandoffError):
            handoff.transmit_encrypted(out, {}, 'https://github.com/example/repo.git', runner=lambda *a, **k: self.fail())

    def test_only_ciphertext_and_public_receipt_are_git_inputs(self):
        out = self.root / 'sealed.cms'
        receipt = handoff.encrypt_package(self.package, self.cert, self.pin, out, runner=self.fake_encrypt)
        seen = []
        def git(args, cwd, **kwargs):
            seen.append(args)
            if args[:2] == ['git', 'add']:
                self.assertEqual(sorted(p.name for p in Path(cwd).iterdir()), ['handoff.cms', 'receipt.json'])
                self.assertEqual((Path(cwd) / 'handoff.cms').read_bytes(), FAKE_CMS)
            return types.SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
        result = handoff.transmit_encrypted(out, receipt, 'https://github.com/example/repo.git', runner=git)
        self.assertTrue(result['pushed'])
        self.assertEqual(seen[1], ['git', 'add', '--', 'handoff.cms', 'receipt.json'])
        self.assertNotIn('--force', seen[-1])

    def test_tampered_ciphertext_and_embedded_credentials_rejected(self):
        out = self.root / 'sealed.cms'
        receipt = handoff.encrypt_package(self.package, self.cert, self.pin, out, runner=self.fake_encrypt)
        out.write_bytes(FAKE_CMS + b'changed')
        with self.assertRaises(handoff.HandoffError): handoff.transport_plan(out, receipt, 'https://github.com/example/repo.git')
        with self.assertRaises(handoff.HandoffError): handoff.transport_plan(out, receipt, 'https://token@github.com/example/repo.git')

    def test_private_fields_cannot_be_added_to_public_receipt(self):
        out = self.root / 'sealed.cms'
        receipt = handoff.encrypt_package(self.package, self.cert, self.pin, out, runner=self.fake_encrypt)
        with self.assertRaises(handoff.HandoffError):
            handoff.transport_plan(out, {**receipt, 'ledger': 'PRIVATE DATA'}, 'https://github.com/example/repo.git')


if __name__ == '__main__': unittest.main()
