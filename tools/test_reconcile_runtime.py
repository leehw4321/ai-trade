"""Synthetic controller transactions and rollback; no network or runtime access."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('staged_runtime_controller', HERE / 'reconcile_runtime.py')
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)


class FakeClient:
    def __init__(self, evidence):
        self.evidence = deepcopy(evidence)
        self.calls = []

    def lease(self):
        self.calls.append('lease')
        return deepcopy(self.evidence['remote_lease'])

    def accounts(self):
        self.calls.append('accounts')
        return deepcopy(self.evidence['account_rows'])

    def closed_orders(self, start, end):
        self.calls.append('closed')
        return list(deepcopy(self.evidence['exchange']['orders']).values())

    def open_orders(self):
        self.calls.append('open')
        return []

    def order_detail(self, uid):
        self.calls.append('detail')
        return deepcopy(self.evidence['exchange']['orders'][uid])


class RuntimeController(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.app, self.evidence = root / 'app', root / 'evidence'
        self.evidence.mkdir()
        self.env = root / 'explicit-env'
        self.env.write_text('NOT_A_REAL_CREDENTIAL=synthetic')
        self.patch = root / 'company-patch.json'
        self.write(self.patch, ctl.PATCH)
        self.cfg = {'strategy': {'stop_max_pct': .045, 'same_old_setting': 99},
                    'live': {'enabled': True, 'loss_stop_pct': .008},
                    'session': {'account_adoption_id': 'old-adoption'}}
        self.source = {'positions': {'KRW-A': {'qty': 10., 'cost': 1000., 'stop': 80.,
                          'time': '1970-01-01T00:00:00+00:00'}},
            'realized': 5., 'bought_krw': 200., 'halted': 'keep latched',
            'risk_baseline_unrealized': -20., 'risk_loss_offset': 30., 'risk_day': '1970-01-01',
            'risk_base_equity': 10000., 'account_adoption': {'epoch': 0., 'history_restored': False},
            'executed': {'old': {'done': True}}, 'last_sell': {}, 'pending_order': None,
            'risk_pending_orders': {}, 'unmanaged_assets': []}
        self.write(self.app / 'config.json', self.cfg)
        self.write(self.app / 'data/live/live.json', self.source)
        self.write(self.app / 'data/user_protected_assets.json', {'markets': ['KRW-DOGE']})
        (self.app / 'data/live/orders.jsonl').write_bytes(b'{"old":"unchanged"}\n')
        self.write(self.evidence / 'source-live.json', self.source)
        self.write(self.evidence / 'applied-local.json', {})
        self.floor = {'stop_floors': {'KRW-BTC': 480.}, 'authorized_external_markets': ['KRW-BTC'],
                      'entry': 500., 'original_company_stop_restored': False}
        self.write(self.evidence / 'verified-stops.json', self.floor)
        orders = [{'uuid': 'synthetic-v5-order', 'identifier': 'v5-synthetic-sale', 'market': 'KRW-A',
                'side': 'ask', 'state': 'cancel', 'executed_volume': '5', 'paid_fee': '.6',
                'created_at': '1970-01-01T00:18:00+00:00', 'trades': [{'uuid': 'synthetic-v5-fill',
                    'created_at': '1970-01-01T00:18:20+00:00', 'volume': '5', 'funds': '600'}]},
            {'uuid': 'synthetic-manual-order', 'market': 'KRW-BTC', 'side': 'bid', 'state': 'cancel',
                'executed_volume': '2', 'paid_fee': '1', 'created_at': '1970-01-01T00:19:00+00:00',
                'trades': [{'uuid': 'synthetic-manual-fill', 'created_at': '1970-01-01T00:19:20+00:00',
                            'volume': '2', 'funds': '1000'}]}]
        self.data = {'source_ledger': self.source,
            'source_ledger_sha256': hashlib.sha256((self.evidence / 'source-live.json').read_bytes()).hexdigest(),
            'source_snapshot_epoch': 1000., 'collected_at': '1970-01-01T00:33:20+00:00',
            'remote_lease': {'owner': '', 'request': '', 'checkpoint': 'f' * 64},
            'account_rows': [{'currency': 'KRW', 'balance': '5000', 'locked': '0'},
                             {'currency': 'A', 'balance': '5', 'locked': '0'},
                             {'currency': 'BTC', 'balance': '2', 'locked': '0'},
                             {'currency': 'DOGE', 'balance': '9', 'locked': '0'}],
            'exchange': {'orders': {o['uuid']: o for o in orders}, 'open_status': 'ok'}}
        self.write(self.evidence / 'evidence.json', self.data)
        self.client = FakeClient(self.data)

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(ctl.canonical(value))

    def execute(self, **kwargs):
        return ctl.execute(self.app, self.env, self.evidence, self.patch,
                           client_factory=lambda *_: self.client, now=lambda: 2000., **kwargs)

    def test_default_offline_dry_run_does_not_construct_client_or_change_app(self):
        before = {p.relative_to(self.app): p.read_bytes() for p in self.app.rglob('*') if p.is_file()}
        report = ctl.execute(self.app, self.env, self.evidence, self.patch,
                             client_factory=lambda *_: self.fail('dry-run constructed private API client'))
        self.assertTrue(report['dry_run'])
        self.assertEqual(report['orders_submitted'], 0)
        self.assertEqual(before, {p.relative_to(self.app): p.read_bytes() for p in self.app.rglob('*') if p.is_file()})

    def test_apply_requires_explicit_second_flag_before_client(self):
        with self.assertRaisesRegex(ctl.ReconciliationBlocked, 'authorization_required'):
            self.execute(apply=True)
        self.assertEqual(self.client.calls, [])

    def test_running_worker_blocks_before_any_lease_call_and_any_write(self):
        lock = self.app / 'bot.lock'
        lock.write_text('12345')
        before = (self.app / 'config.json').read_bytes()
        stopped = lambda app: ctl.require_stopped(app, running=lambda _: True)
        with self.assertRaisesRegex(ctl.ReconciliationBlocked, 'running_or_unknown'):
            self.execute(apply=True, authorized=True, stopped=stopped)
        self.assertEqual(self.client.calls, [])
        self.assertEqual((self.app / 'config.json').read_bytes(), before)
        self.assertFalse((self.app / 'data/reconciliation/APPLYING').exists())

    def test_worker_recheck_under_marker_and_malformed_or_unknown_pid_fail_closed(self):
        plan = ctl.build_plan(self.app, self.evidence, self.patch, 2000.)
        def starts_during_check(app):
            self.assertTrue((self.app / 'data/reconciliation/APPLYING').exists())
            raise ctl.ReconciliationBlocked('bot_started_before_writes')
        with self.assertRaisesRegex(ctl.ReconciliationBlocked, 'bot_started'):
            ctl.apply_plan(self.app, plan, self.client, stopped=starts_during_check)
        self.assertEqual(ctl.read_json(self.app / 'config.json'), self.cfg)
        for text in ('', 'unknown', '-1', '12345'):
            with self.subTest(lock=text):
                (self.app / 'shadow.lock').write_text(text)
                with self.assertRaises(ctl.ReconciliationBlocked):
                    ctl.require_stopped(self.app, running=lambda _: None)
        (self.app / 'shadow.lock').write_text('12345')
        ctl.require_stopped(self.app, running=lambda _: False)

    def test_same_order_identity_cannot_hide_revised_fee_or_actual_fill(self):
        self.client.evidence['exchange']['orders']['synthetic-v5-order']['paid_fee'] = '.7'
        with self.assertRaisesRegex(ctl.ReconciliationBlocked, 'closed_accounting_revised'):
            self.execute(apply=True, authorized=True)
        self.client = FakeClient(self.data)
        original_detail = self.client.order_detail
        def revised_detail(uid):
            value = original_detail(uid)
            value['trades'][0]['created_at'] = '1970-01-01T00:18:21+00:00'
            return value
        self.client.order_detail = revised_detail
        with self.assertRaisesRegex(ctl.ReconciliationBlocked, 'detailed_fills_revised'):
            self.execute(apply=True, authorized=True)
        self.assertFalse((self.app / 'data/reconciliation/APPLYING').exists())

    def test_explicit_dotenv_values_strip_matching_quotes_without_api(self):
        placeholders = {'UPBIT_ACCESS_KEY': '"xxx-access"', 'UPBIT_SECRET_KEY': "'xxx-secret'"}
        self.env.write_text('\n'.join(f'{key}={value}' for key, value in placeholders.items()) + '\n')
        client = ctl.ReadOnlyClient(self.app, self.env, self.cfg)
        self.assertEqual(client.keys['UPBIT_ACCESS_KEY'], 'xxx-access')
        self.assertEqual(client.keys['UPBIT_SECRET_KEY'], 'xxx-secret')

    def test_fresh_changed_account_order_set_and_remote_each_block_before_writes(self):
        for kind in ('account', 'orders', 'remote'):
            with self.subTest(kind=kind):
                self.client = FakeClient(self.data)
                if kind == 'account': self.client.evidence['account_rows'][0]['balance'] = '4999'
                if kind == 'orders': self.client.evidence['exchange']['orders'].pop('synthetic-v5-order')
                if kind == 'remote': self.client.evidence['remote_lease']['owner'] = 'other'
                with self.assertRaises(ctl.ReconciliationBlocked):
                    self.execute(apply=True, authorized=True)
                self.assertFalse((self.app / 'data/reconciliation/APPLYING').exists())
                self.assertEqual(ctl.read_json(self.app / 'data/live/live.json'), self.source)

    def test_success_preserves_basis_journal_limits_and_installs_independent_proof(self):
        journal = (self.app / 'data/live/orders.jsonl').read_bytes()
        result = self.execute(apply=True, authorized=True)
        self.assertTrue(result['applied'])
        cfg = ctl.read_json(self.app / 'config.json')
        target = ctl.read_json(self.app / 'data/live/live.json')
        guard = ctl.peer('reconciliation_guard', self.app)
        self.assertTrue(guard.validate(self.app, cfg, self.data['remote_lease'], 2000.))
        self.assertEqual(guard.preserved_basis(self.source), guard.preserved_basis(target))
        self.assertEqual(cfg['strategy'], self.cfg['strategy'])
        self.assertEqual(cfg['live']['loss_stop_pct'], .008)
        self.assertTrue(cfg['live']['user_protection_required'])
        self.assertEqual(target['halted'], 'keep latched')
        self.assertEqual((self.app / 'data/live/orders.jsonl').read_bytes(), journal)
        self.assertFalse((self.app / 'data/reconciliation/APPLYING').exists())

    def test_installed_validation_failure_rolls_back_byte_exact_and_clears_marker(self):
        plan = ctl.build_plan(self.app, self.evidence, self.patch, 2000.)
        before = {p: p.read_bytes() for p in (self.app / 'config.json', self.app / 'data/live/live.json',
                                             self.app / 'data/live/orders.jsonl')}
        plan['guard'].validate = lambda *_: False
        with self.assertRaisesRegex(ctl.ReconciliationBlocked, 'installed_proof_validation_failed'):
            ctl.apply_plan(self.app, plan, self.client, now=lambda: 2000.)
        for path, raw in before.items(): self.assertEqual(path.read_bytes(), raw)
        self.assertFalse((self.app / 'data/account_reconciliation.json').exists())
        self.assertFalse((self.app / 'data/reconciliation/APPLYING').exists())

    def test_rollback_failure_retains_blocking_marker(self):
        plan = ctl.build_plan(self.app, self.evidence, self.patch, 2000.)
        cfg_path = self.app / 'config.json'
        old = cfg_path.read_bytes()
        plan['guard'].validate = lambda *_: False
        def fail_restore(path, raw):
            if Path(path) == cfg_path and raw == old:
                raise OSError('synthetic rollback failure')
            ctl.atomic_write(path, raw)
        with self.assertRaises(ctl.ReconciliationBlocked):
            ctl.apply_plan(self.app, plan, self.client, now=lambda: 2000., write=fail_restore)
        self.assertTrue((self.app / 'data/reconciliation/APPLYING').exists())

    def test_unreviewed_patch_and_too_loose_or_forged_BTC_stop_rejected(self):
        self.write(self.patch, {'live': {**ctl.PATCH['live'], 'loss_stop_pct': .5}})
        with self.assertRaisesRegex(ctl.ReconciliationBlocked, 'unreviewed_company'):
            self.execute()
        self.write(self.patch, ctl.PATCH)
        self.floor['stop_floors']['KRW-BTC'] = 470.
        self.write(self.evidence / 'verified-stops.json', self.floor)
        with self.assertRaisesRegex(ctl.ReconciliationBlocked, 'existing_cap'):
            self.execute()
        self.floor['entry'] = 450.
        self.write(self.evidence / 'verified-stops.json', self.floor)
        with self.assertRaises(ctl.ReconciliationBlocked):
            self.execute()

    def test_saturated_same_instant_closed_window_fails_closed(self):
        client = object.__new__(ctl.ReadOnlyClient)
        client.get = lambda *_: [{'uuid': 'same-instant'}] * 1000
        with self.assertRaisesRegex(ctl.ReconciliationBlocked, 'saturated_closed_order_window'):
            client.closed_orders(1000., 1001.)


if __name__ == '__main__':
    unittest.main()
