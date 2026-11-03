"""Create LOCAL private handoff ZIP. Never uploads it or copies .env files.

Normal export is reference-only while trading can continue. --final requires
local bot/shadow shutdown, no uncertain orders and a stable live ledger. The
receiving PC must still pass the existing ownership/checkpoint checks.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile

import sync_v5

ROOT = Path(__file__).resolve().parent.parent
KST = dt.timezone(dt.timedelta(hours=9))


def include(path):
    return (path.is_file() and not path.is_symlink()
            and not path.name.lower().startswith('.env')
            and path.suffix.lower() not in {'.key', '.pem', '.pyc', '.lnk', '.zip', '.cms', '.pfx', '.p12'}
            and path.name.lower() not in {'auth.json', '.credentials.json'}
            and '__pycache__' not in path.parts and '.git' not in path.parts
            and '.venv' not in path.parts and 'node_modules' not in path.parts
            and 'session_git' not in path.parts and 'private_handoff' not in path.parts
            and not path.name.endswith(('.lock', '.tmp', '.partial')))


def export(app, destination, final=False, with_history=False):
    app = app.resolve(strict=True)
    if not (app / 'config.json').is_file():
        raise ValueError('runtime config missing')
    ledger = app / 'data/live/live.json'
    initial = ledger.read_bytes()
    config_initial = (app / 'config.json').read_bytes()
    baseline_file = app / 'pnl_baseline.json'
    baseline_initial = baseline_file.read_bytes() if baseline_file.exists() else None
    state = json.loads(initial)
    if final and sync_v5.bot_running():
        raise RuntimeError('final export requires normal shutdown of bot and shadow')
    if final and (state.get('pending_order') or state.get('risk_pending_orders')):
        raise RuntimeError('uncertain orders block final export')
    destination.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(KST).strftime('%Y%m%d_%H%M%S_%f')
    target = destination / ('집작업_비공개인계_' + stamp + '.zip')
    temp = target.with_suffix('.partial')
    sources = []
    for file in app.glob('*'):
        if file.name in ('config.json', 'pnl_baseline.json'):
            continue
        if file.name == 'config.json' or file.name == 'pnl_baseline.json' or file.suffix in {'.py', '.md'}:
            if include(file): sources.append((file, 'runtime/' + file.name))
    for file in (app / 'data').rglob('*'):
        if include(file) and file != ledger:
            sources.append((file, 'runtime/data/' + file.relative_to(app / 'data').as_posix()))
    # Legacy observer data is archive-only; the collector was removed on 2026-11-04.
    for name in ('tactical_observer', 'warning_policy'):
        folder = ROOT / 'data' / name
        for file in folder.rglob('*') if folder.exists() else []:
            if include(file): sources.append((file, 'observations/' + name + '/' + file.relative_to(folder).as_posix()))
    if with_history:
        folders = [(ROOT / 'analysis_runs', 'history/analysis_runs'),
                   (ROOT / 'trend_data', 'history/trend_data'),
                   (ROOT / 'document_work', 'history/document_work'),
                   (Path.home() / 'Desktop' / 'AI토론장', 'history/AI토론장')]
        for folder, prefix in folders:
            for file in folder.rglob('*') if folder.exists() else []:
                if include(file): sources.append((file, prefix + '/' + file.relative_to(folder).as_posix()))
    manifest = {'created_kst': dt.datetime.now(KST).isoformat(timespec='seconds'),
                'source_pc': os.environ.get('COMPUTERNAME', ''), 'source_app': str(app),
                'final_export_requested': final, 'live_restore_allowed': False,
                'env_files_included': False,
                'ledger_sha256': hashlib.sha256(initial).hexdigest(),
                'config_sha256': hashlib.sha256(config_initial).hexdigest(),
                'pnl_baseline_present': baseline_initial is not None,
                'pnl_baseline_sha256': hashlib.sha256(baseline_initial).hexdigest() if baseline_initial is not None else None,
                'files': [], 'missing_during_export': []}
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True)
    manifest['source_git_head'] = head.stdout.strip()
    try:
        with zipfile.ZipFile(temp, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as bundle:
            bundle.writestr('runtime/data/live/live.json', initial)
            bundle.writestr('runtime/config.json', config_initial)
            if baseline_initial is not None:
                bundle.writestr('runtime/pnl_baseline.json', baseline_initial)
            for i, (file, name) in enumerate(sources):
                try: bundle.write(file, name)
                except FileNotFoundError:
                    if final: raise
                    manifest['missing_during_export'].append(name)
                    continue
                manifest['files'].append({'path': name, 'size': bundle.getinfo(name).file_size})
                if i and i % 250 == 0: print(f'Archived {i}/{len(sources)} files', flush=True)
            changed = ledger.read_bytes() != initial
            config_changed = (app / 'config.json').read_bytes() != config_initial
            baseline_current = baseline_file.read_bytes() if baseline_file.exists() else None
            baseline_changed = baseline_current != baseline_initial
            manifest['ledger_changed_during_export'] = changed
            manifest['config_changed_during_export'] = config_changed
            manifest['pnl_baseline_changed_during_export'] = baseline_changed
            if final and (changed or config_changed or baseline_changed or sync_v5.bot_running()):
                raise RuntimeError('runtime restarted or ledger changed during final export')
            manifest['live_restore_allowed'] = final
            bundle.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            bundle.writestr('먼저읽기_README.txt',
                '이 ZIP은 비공개 계좌·주문 자료를 포함합니다. 공개 GitHub에 올리지 마세요.\n'
                '기본 내보내기는 참고용 시점 스냅샷이며 현재 거래 장부로 곧바로 복원하면 안 됩니다.\n'
                '--final 내보내기라도 복원 후 실행권/체크포인트 확인이 필요합니다.\n'
                '.env는 포함하지 않습니다. 집 PC의 인증 설정을 사용하세요.\n'
                '계산기/관측기/토론 진행기의 실행 상태나 메모리는 옮겨지지 않습니다.\n')
        temp.replace(target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    print('Private export:', target, flush=True)
    print('Live restore allowed:', manifest['live_restore_allowed'], flush=True)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--destination', type=Path, default=ROOT / 'data/private_handoff')
    parser.add_argument('--final', action='store_true')
    parser.add_argument('--with-history', action='store_true')
    args = parser.parse_args()
    export(args.app, args.destination, args.final, args.with_history)
