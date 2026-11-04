"""Preserved-basis exchange reconciliation controller; dry-run unless twice authorized.

Never starts a bot, submits an order, changes a remote lease, or resets risk basis.
Private inputs/artifacts stay local. The only network adapter methods are GETs and
a read of the Git session ref. Run with explicit --app, --env and --evidence-dir.
"""
import argparse
from copy import deepcopy
import datetime as dt
from decimal import Decimal
import hashlib
import hmac
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
import base64

HERE = Path(__file__).resolve().parent
PATCH = {'live': {'profit_guard_enabled': True, 'profit_guard_arm_pct': .02,
                  'profit_guard_giveback': .4}}
REMOTE = 'https://github.com/leehw4321/ai-trade.git'


class ReconciliationBlocked(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise ReconciliationBlocked(reason)


def peer(name, app):
    paths = (HERE / (name + '.py'), Path(app) / (name + '.py'),
             HERE.parent / 'v5' / (name + '.py'))
    path = next((p for p in paths if p.is_file()), None)
    require(path is not None, 'reviewed_helper_missing_' + name)
    spec = importlib.util.spec_from_file_location('_controller_' + name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path):
    def invalid(_):
        raise ReconciliationBlocked('nonfinite_json')
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), parse_constant=invalid)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def epoch(value):
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value).timestamp()
    require(type(value) in (int, float) and math.isfinite(value), 'invalid_epoch')
    return value


def account_signature(rows):
    """Exact inventory/cash/cost-basis comparison, independent of row order/format."""
    require(isinstance(rows, list), 'accounts_not_list')
    out = {}
    for row in rows:
        currency = row.get('currency')
        require(isinstance(currency, str) and re.fullmatch(r'[A-Z0-9]+', currency)
                and currency not in out, 'invalid_or_duplicate_currency')
        values = []
        for key in ('balance', 'locked', 'avg_buy_price'):
            value = Decimal(str(row.get(key, '0')))
            require(value.is_finite() and value >= 0, 'invalid_account_value')
            values.append(value)
        out[currency] = tuple(values)
    return out


def quantities(rows):
    sig = account_signature(rows)
    return {'KRW-' + c: float(v[0] + v[1]) for c, v in sig.items() if c != 'KRW'}


def filled_ids(rows):
    require(isinstance(rows, list), 'closed_orders_not_list')
    found = set()
    for row in rows:
        require(row.get('state') in ('done', 'cancel'), 'nonterminal_order_in_closed_set')
        volume = Decimal(str(row.get('executed_volume', '0')))
        require(volume.is_finite() and volume >= 0, 'invalid_executed_volume')
        if volume:
            uid = row.get('uuid')
            require(isinstance(uid, str) and uid, 'missing_closed_order_identity')
            require(uid not in found, 'duplicate_filled_order')
            found.add(uid)
    return found


def idle(lease):
    require(isinstance(lease, dict) and lease.get('owner') in (None, '')
            and lease.get('request') in (None, '')
            and isinstance(lease.get('checkpoint'), str) and lease['checkpoint'],
            'remote_not_idle')


def windows_pid_running(pid):
    """False only for a definitely exited/nonexistent PID; access denial is unknown."""
    require(os.name == 'nt', 'cannot_verify_windows_process_state')
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        require(ctypes.get_last_error() == 87, 'process_liveness_unknown')
        return False
    try:
        code = wintypes.DWORD()
        require(bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))), 'process_liveness_unknown')
        return code.value == 259  # STILL_ACTIVE, including a conservatively reused PID.
    finally:
        kernel.CloseHandle(handle)


def require_stopped(app, running=windows_pid_running):
    """Both cooperating workers hold their root PID locks for their whole lifetime."""
    for name in ('bot.lock', 'shadow.lock'):
        path = Path(app) / name
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding='utf-8-sig').strip()
            require(re.fullmatch(r'[1-9][0-9]{0,9}', text) is not None, 'malformed_worker_pid_lock')
            pid = int(text)
            require(pid <= 0xffffffff, 'malformed_worker_pid_lock')
            require(running(pid) is False, 'bot_or_shadow_running_or_unknown')
        except ReconciliationBlocked:
            raise
        except Exception:
            raise ReconciliationBlocked('worker_pid_lock_unreadable_or_unknown') from None


def order_signature(order, detailed=False):
    require(isinstance(order, dict) and order.get('side') in ('bid', 'ask')
            and order.get('state') in ('done', 'cancel')
            and isinstance(order.get('market'), str), 'invalid_order_signature')
    def amount(value):
        value = Decimal(str(value))
        require(value.is_finite() and value >= 0, 'invalid_order_amount')
        return value
    def timestamp(value):
        require(isinstance(value, str), 'invalid_fill_timestamp')
        parsed = dt.datetime.fromisoformat(value)
        require(parsed.tzinfo is not None, 'ambiguous_fill_timestamp')
        return parsed.timestamp()
    result = (order['market'], order['side'], order['state'],
              amount(order.get('executed_volume')), amount(order.get('paid_fee')),
              timestamp(order.get('created_at')))
    if detailed:
        require(isinstance(order.get('trades'), list), 'missing_order_trades')
        trades = []
        for trade in order['trades']:
            require(isinstance(trade, dict), 'invalid_order_trade')
            trades.append((str(trade.get('uuid') or ''), timestamp(trade.get('created_at')),
                           amount(trade.get('volume')), amount(trade.get('funds'))))
        result += (str(order.get('identifier') or ''), tuple(sorted(trades)))
    return result


def fresh_check(client, evidence, now):
    lease = client.lease()
    idle(lease)
    require(lease['checkpoint'] == evidence['remote_lease']['checkpoint'], 'remote_checkpoint_changed')
    expected = account_signature(evidence['account_rows'])
    rows = client.accounts()
    require(account_signature(rows) == expected, 'account_changed_since_evidence')
    require(client.open_orders() == [], 'open_orders_exist')
    start = epoch(evidence['source_ledger']['account_adoption']['epoch'])
    latest = client.closed_orders(start, now)
    stored = list(evidence['exchange']['orders'].values())
    require(filled_ids(latest) == filled_ids(stored), 'closed_fills_changed_since_evidence')
    latest_by_id = {row['uuid']: row for row in latest if row['uuid'] in filled_ids(stored)}
    for old in stored:
        require(order_signature(latest_by_id[old['uuid']]) == order_signature(old),
                'closed_accounting_revised_since_evidence')
        current = client.order_detail(old['uuid'])
        require(current.get('uuid') == old['uuid']
                and order_signature(current, True) == order_signature(old, True),
                'detailed_fills_revised_since_evidence')
    # A second observation brackets closed-order collection and checks cash too.
    require(client.open_orders() == [], 'open_orders_appeared')
    again = client.accounts()
    require(account_signature(again) == expected, 'account_changed_during_collection')
    final = client.lease()
    idle(final)
    require(final['checkpoint'] == lease['checkpoint'], 'remote_checkpoint_changed')
    return again, final


def build_plan(app, evidence_dir, company_profit_guard, checked, account_rows=None, lease=None,
               proof_id=None):
    app, evidence_dir = Path(app).resolve(), Path(evidence_dir).resolve()
    cfg = read_json(app / 'config.json')
    source_raw = (evidence_dir / 'source-live.json').read_bytes()
    source = read_json(evidence_dir / 'source-live.json')
    evidence = read_json(evidence_dir / 'evidence.json')
    require(canonical(source) == canonical(evidence['source_ledger']), 'source_evidence_disagrees')
    source_epoch = epoch(evidence['source_snapshot_epoch'])
    require(digest(source_raw) == evidence['source_ledger_sha256'], 'source_raw_hash_disagrees')
    # The bridge is permitted only over the exact old home ledger that was reviewed.
    require(canonical(read_json(app / 'data/live/live.json')) == canonical(source),
            'local_source_changed')
    require(source.get('pending_order') is None and not source.get('risk_pending_orders'),
            'source_has_pending_order')
    require(read_json(company_profit_guard) == PATCH, 'unreviewed_company_profit_guard_patch')
    protection = read_json(app / 'data/user_protected_assets.json')
    require(isinstance(protection, dict) and 'KRW-DOGE' in protection.get('markets', []),
            'DOGE_protection_missing')
    floor_doc = read_json(evidence_dir / 'verified-stops.json')
    require(floor_doc.get('authorized_external_markets') == ['KRW-BTC']
            and set(floor_doc.get('stop_floors', {})) == {'KRW-BTC'}, 'external_scope_not_explicit_BTC')
    maximum = cfg.get('strategy', {}).get('stop_max_pct')
    require(type(maximum) in (int, float) and math.isfinite(maximum) and 0 < maximum < 1,
            'invalid_configured_stop_cap')
    floor = float(floor_doc['stop_floors']['KRW-BTC'])
    entry = float(floor_doc['entry'])
    require(math.isfinite(floor) and math.isfinite(entry) and entry > 0
            and entry * (1 - maximum) <= floor <= entry, 'BTC_stop_violates_existing_cap')
    require(floor_doc.get('original_company_stop_restored') is False,
            'cannot_claim_company_stop_restored')
    orders = list(evidence['exchange']['orders'].values())
    require(evidence['exchange'].get('open_status') == 'ok', 'evidence_open_status_not_ok')
    filled_ids(orders)
    applied = read_json(evidence_dir / 'applied-local.json')
    rows = evidence['account_rows'] if account_rows is None else account_rows
    lease = evidence['remote_lease'] if lease is None else lease
    idle(lease)
    replay = peer('reconcile', app)
    guard = peer('reconciliation_guard', app)
    target, report = replay.reconcile(source, orders, applied, source_epoch, quantities(rows),
        {'KRW-DOGE'}, floor_doc['stop_floors'], authorized_external_markets={'KRW-BTC'})
    require(report.get('accounting_replay_complete') is True and report.get('unresolved') == []
            and report.get('quantity_mismatches') == [], 'replay_incomplete')
    btc = target.get('positions', {}).get('KRW-BTC')
    require(isinstance(btc, dict) and math.isfinite(float(btc.get('entry', 0)))
            and float(btc['entry']) > 0
            and math.isclose(entry, float(btc['entry']), rel_tol=1e-9, abs_tol=1e-6)
            and floor >= float(btc['entry']) * (1 - maximum), 'BTC_stop_entry_not_verified_fill')
    proof_id = proof_id or ('exchange-' + dt.datetime.fromtimestamp(checked, dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                            + '-' + digest(canonical(source))[:12])
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', proof_id) is not None, 'unsafe_proof_id')
    updated = deepcopy(cfg)
    updated.setdefault('live', {}).update(PATCH['live'])
    updated['live']['user_protection_required'] = True
    updated.setdefault('session', {}).update(account_reconciliation_id=proof_id,
        account_reconciliation_source_checkpoint=digest(canonical(source)),
        account_reconciliation_source_epoch=source_epoch)
    account = {'source_snapshot_epoch': source_epoch, 'collected_epoch': checked, 'accounts': rows}
    values = {'source_snapshot': source, 'orders': orders, 'account': account, 'applied_local': applied,
              'preserved_basis': guard.preserved_basis(source), 'authorized_external': ['KRW-BTC'],
              'verified_stop_floors': floor_doc}
    artifacts = {name: source_raw if name == 'source_snapshot' else canonical(value)
                 for name, value in values.items()}
    proof = {'id': proof_id, 'previous_checkpoint': lease['checkpoint'],
        'source_checkpoint_sha256': digest(canonical(source)), 'source_snapshot_epoch': source_epoch,
        'target_ledger_sha256': digest(canonical(target)), 'checked_epoch': checked,
        'no_open_orders': True, 'stable_account': True, 'replay_complete': True,
        'protected_DOGE': True, 'unresolved': [], 'authorized_external_markets': ['KRW-BTC'],
        'preserved_basis_sha256': digest(canonical(values['preserved_basis'])),
        'files': {name: 'reconciliation/' + proof_id + '/' + filename
                  for name, filename in guard.ARTIFACTS.items()}}
    for name, raw in artifacts.items():
        if name != 'preserved_basis': proof[name + '_sha256'] = digest(raw)
    return {'config': updated, 'target': target, 'proof': proof, 'artifacts': artifacts,
            'report': report, 'guard': guard, 'lease': lease, 'evidence': evidence,
            'source_config_bytes': (app / 'config.json').read_bytes(),
            'source_ledger_bytes': (app / 'data/live/live.json').read_bytes(),
            'protection_bytes': (app / 'data/user_protected_assets.json').read_bytes()}


def atomic_write(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.reconcile-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()


def install_artifacts(app, plan, write=atomic_write):
    data = Path(app) / 'data'
    folder = data / 'reconciliation' / plan['proof']['id']
    for name, filename in plan['guard'].ARTIFACTS.items():
        write(folder / filename, plan['artifacts'][name])
    write(data / 'account_reconciliation.json', canonical(plan['proof']))


def verify_in_temp(plan):
    with tempfile.TemporaryDirectory(prefix='reconcile-offline-') as temporary:
        app = Path(temporary)
        atomic_write(app / 'data/live/live.json', canonical(plan['target']))
        atomic_write(app / 'data/user_protected_assets.json', plan['protection_bytes'])
        install_artifacts(app, plan)
        require(plan['guard'].validate(app, plan['config'], plan['lease'],
                                      plan['proof']['checked_epoch']), 'offline_proof_validation_failed')


def apply_plan(app, plan, client, now=time.time, write=atomic_write, stopped=require_stopped):
    """Write a reversible, marker-protected transaction; never acquire a lease."""
    app = Path(app).resolve()
    marker = app / 'data/reconciliation/APPLYING'
    marker.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive marker blocks concurrent controller invocations and cooperating launches.
    fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, canonical({'id': plan['proof']['id']}))
    os.close(fd)
    paths = (app / 'config.json', app / 'data/live/live.json',
             app / 'data/live/orders.jsonl', app / 'data/account_reconciliation.json')
    old = {}
    success = False
    try:
        require((app / 'config.json').read_bytes() == plan['source_config_bytes']
                and (app / 'data/live/live.json').read_bytes() == plan['source_ledger_bytes']
                and (app / 'data/user_protected_assets.json').read_bytes() == plan['protection_bytes'],
                'local_state_changed_before_transaction')
        stopped(app)  # Recheck after acquiring APPLYING, immediately before any state/backup writes.
        folder = marker.parent / plan['proof']['id']
        require(not folder.exists(), 'reconciliation_id_already_exists')
        for path in paths:
            old[path] = path.read_bytes() if path.exists() else None
        # Durable backups precede all modified active state; journals are never appended.
        for index, (path, raw) in enumerate(old.items()):
            if raw is not None: write(folder / 'backup' / (str(index) + '-' + path.name), raw)
        install_artifacts(app, plan, write)
        write(app / 'config.json', canonical(plan['config']))
        write(app / 'data/live/live.json', canonical(plan['target']))
        lease = client.lease()
        idle(lease)
        require(lease['checkpoint'] == plan['lease']['checkpoint'], 'remote_changed_during_transaction')
        require(plan['guard'].validate(app, read_json(app / 'config.json'), lease, now()),
                'installed_proof_validation_failed')
        require((app / 'data/live/orders.jsonl').read_bytes() == old[app / 'data/live/orders.jsonl']
                if old[app / 'data/live/orders.jsonl'] is not None
                else not (app / 'data/live/orders.jsonl').exists(), 'journal_changed_during_transaction')
        success = True
    except Exception:
        rollback_ok = True
        for path, raw in old.items():
            try:
                if raw is None:
                    if path.exists(): path.unlink()
                else: write(path, raw)
            except Exception:
                rollback_ok = False
        rollback_ok = rollback_ok and all((path.read_bytes() == raw if raw is not None else not path.exists())
                                          for path, raw in old.items())
        if rollback_ok: marker.unlink()
        # Failure to restore leaves APPLYING in place: the launcher must stay blocked.
        raise
    finally:
        if success: marker.unlink()


class ReadOnlyClient:
    """Explicit GET-only adapter. Credentials are read only when apply is invoked."""
    def __init__(self, app, env, cfg):
        self.app, self.cfg = Path(app), cfg
        require(Path(env).is_file(), 'explicit_env_missing')
        self.keys = {}
        for line in Path(env).read_text(encoding='utf-8-sig').splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                if key.strip() in ('UPBIT_ACCESS_KEY', 'UPBIT_SECRET_KEY'):
                    self.keys[key.strip()] = value.strip().strip('"').strip("'")
        require(all(self.keys.get(k) for k in ('UPBIT_ACCESS_KEY', 'UPBIT_SECRET_KEY')), 'explicit_env_missing_keys')

    def get(self, path, params=()):
        query = urllib.parse.urlencode(params)
        payload = {'access_key': self.keys['UPBIT_ACCESS_KEY'], 'nonce': str(uuid.uuid4())}
        if query:
            payload.update(query_hash=hashlib.sha512(urllib.parse.unquote(query).encode()).hexdigest(),
                           query_hash_alg='SHA512')
        b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b'=').decode()
        head = b64(canonical({'alg': 'HS256', 'typ': 'JWT'}))
        body = b64(canonical(payload))
        sig = b64(hmac.new(self.keys['UPBIT_SECRET_KEY'].encode(), (head + '.' + body).encode(), hashlib.sha256).digest())
        request = urllib.request.Request('https://api.upbit.com' + path + ('?' + query if query else ''),
            headers={'Authorization': 'Bearer ' + head + '.' + body + '.' + sig}, method='GET')
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)

    def accounts(self):
        return self.get('/v1/accounts')

    def order_detail(self, order_uuid):
        result = self.get('/v1/order', [('uuid', order_uuid)])
        time.sleep(.06)  # Stay below the shared 30/s order-query group even on fast connections.
        return result

    def open_orders(self):
        # Any open order blocks apply, so one row in either state is sufficient.
        out = []
        for state in ('wait', 'watch'):
            rows = self.get('/v1/orders/open', [('state', state), ('limit', 1)])
            require(isinstance(rows, list), 'invalid_open_orders_response')
            out.extend(rows)
        return out

    def closed_orders(self, start, end):
        out = {}
        utc = lambda value: dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()
        def window(lo, hi):
            rows = self.get('/v1/orders/closed', [('states[]', 'done'), ('states[]', 'cancel'),
                ('start_time', utc(lo)), ('end_time', utc(hi)), ('limit', 1000), ('order_by', 'desc')])
            require(isinstance(rows, list), 'invalid_closed_orders_response')
            require(len(rows) <= 1000, 'invalid_closed_orders_count')
            if len(rows) == 1000:
                require(hi - lo > 1, 'saturated_closed_order_window')
                middle = (lo + hi) / 2
                require(lo < middle < hi, 'closed_window_cannot_progress')
                window(lo, middle)
                window(middle, hi)
            else:
                for row in rows:
                    require(isinstance(row, dict) and row.get('uuid'), 'invalid_closed_order_row')
                    out[row['uuid']] = row
        lo = start
        while lo < end:
            hi = min(end, lo + 7 * 86400)
            window(lo, hi)
            lo = hi
        return list(out.values())

    def lease(self):
        # Fetch only in a temporary bare repository; never CAS/push/write app Git cache.
        remote = self.cfg.get('session', {}).get('remote', REMOTE)
        env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never')
        with tempfile.TemporaryDirectory(prefix='reconcile-lease-read-') as folder:
            def run(*args):
                completed = subprocess.run(['git', *args], cwd=folder, env=env, capture_output=True,
                    timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                require(completed.returncode == 0, 'remote_lease_read_failed')
                return completed.stdout
            run('init', '--bare', '.')
            run('fetch', '--quiet', remote, 'refs/heads/bot-session')
            return json.loads(run('show', 'FETCH_HEAD:session.json'))


def execute(app, env, evidence_dir, company_profit_guard, apply=False, authorized=False,
            client_factory=ReadOnlyClient, now=time.time, stopped=require_stopped):
    require(not apply or authorized, 'explicit_exchange_reconciliation_authorization_required')
    app = Path(app).resolve()
    require(app.is_dir() and Path(env).is_file(), 'explicit_app_or_env_missing')
    evidence = read_json(Path(evidence_dir) / 'evidence.json')
    clock = now() if apply else epoch(evidence['collected_at'])
    rows, lease = None, None
    client = None
    if apply:
        stopped(app)  # Must precede even private read-only network collection.
        client = client_factory(app, Path(env).resolve(), read_json(app / 'config.json'))
        rows, lease = fresh_check(client, evidence, clock)
        clock = now()
    plan = build_plan(app, evidence_dir, company_profit_guard, clock, rows, lease)
    verify_in_temp(plan)
    if apply: apply_plan(app, plan, client, now, stopped=stopped)
    report = plan['report']
    return {'applied': apply, 'dry_run': not apply, 'orders_submitted': 0, 'remote_lease_modified': False,
            'accounting_replay_complete': report['accounting_replay_complete'],
            'new_bot_order_count': report.get('new_bot_order_count'),
            'new_external_order_count': report.get('new_external_order_count'),
            'realized_delta': report.get('realized_delta'), 'bought_delta': report.get('bought_delta'),
            'managed_market_count': report.get('managed_market_count'),
            'company_internal_peak_trailing_history_restored': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('app', 'env', 'evidence-dir', 'company-profit-guard'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--user-authorized-exchange-reconciliation', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = execute(args.app, args.env, args.evidence_dir, args.company_profit_guard,
                         args.apply, args.user_authorized_exchange_reconciliation)
    except Exception as error:
        # API responses/arguments can contain account/order details: only fixed error codes escape.
        code = str(error) if isinstance(error, ReconciliationBlocked) else type(error).__name__
        print(json.dumps({'applied': False, 'blocked': code, 'orders_submitted': 0}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
