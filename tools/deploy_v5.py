"""안전 배포: 정본(ai-trade/v5) 수정 -> 테스트 -> 봇 정상 종료 -> 백업 -> 동기화 -> 재시작 -> 상태 확인 -> 실패 시 롤백.

  python tools/deploy_v5.py              점검만(봇을 건드리지 않음): 다른 파일, 테스트, 미체결 주문, 승인 상태
  python tools/deploy_v5.py --apply      사용자가 지금 배포를 승인한 것으로 보고 정지->적용->재시작
  python tools/deploy_v5.py --watch      정본이 바뀌면 자동 배포. 단, tools/APPROVED_COMMIT 의 커밋과 HEAD가 같을 때만

안전 규칙
- 프로세스를 강제로 죽이지 않는다. 봇의 정상 종료 파일(stop.txt 등)만 쓰고, 시간 안에 안 끝나면 정지 요청을 취소하고 중단한다.
- 미체결·미확정 주문이 있으면 배포하지 않는다. 테스트가 하나라도 실패하면 배포하지 않는다.
- config.json(실거래 설정)·data/·.env 는 건드리지 않는다. 코드 파일만 바꾼다.
- 재시작 후 상태 화면이 새로 만들어지지 않거나 오류가 보이면 백업으로 되돌리고 다시 시작한다.
- 감시 모드(--watch)는 사용자가 승인한 커밋만 배포한다. 승인은 `tools/APPROVED_COMMIT` 파일에 커밋 해시를 적는 것이다.
  배포를 잠시 막으려면 `tools/deploy_pause.txt` 파일을 만든다.
"""
import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_v5 as sy  # noqa: E402

ROOT = sy.ROOT
SRC = sy.SRC
APP = sy.DST
LAUNCH = ROOT.parent / '코인 트레이딩' / 'launch_v5.py'
STOPS = ('stop.txt', 'stop_shadow.txt', 'account_sync_stop.txt')
LOCKS = ('bot.lock', 'shadow.lock', 'account_sync.lock')
APPROVAL = ROOT / 'tools' / 'APPROVED_COMMIT'
PAUSE = ROOT / 'tools' / 'deploy_pause.txt'
STOP_TIMEOUT = 180
WATCH_SECONDS = 60


def now():
    return dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def log(msg, app=None):
    line = f'[{now()}] {msg}'
    print(line)
    try:
        target = (app or APP) / 'data'
        target.mkdir(exist_ok=True)
        with (target / 'deploy.log').open('a', encoding='utf-8') as f:
            f.write(line + '\n')
    except OSError:
        pass


def pid_alive(pid):
    r = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'], capture_output=True, text=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return f'"{pid}"' in r.stdout


def lock_alive(app, name):
    f = app / name
    if not f.exists():
        return False
    pid = f.read_text(errors='ignore').strip()
    return pid.isdigit() and pid_alive(pid)


def any_alive(app):
    return [n for n in LOCKS if lock_alive(app, n)]


def pending_orders(app):
    """미체결·미확정 주문이 있으면 이유 문자열, 없으면 ''. 장부가 없으면 ''."""
    f = app / 'data' / 'live' / 'live.json'
    if not f.exists():
        return ''
    d = json.loads(f.read_text(encoding='utf-8'))
    if d.get('pending_order'):
        return '미확정 주문 있음(pending_order)'
    if d.get('risk_pending_orders'):
        return '미확정 청산 주문 있음(risk_pending_orders)'
    return ''


def run_tests(src):
    """v5의 모든 test_*.py 모듈을 하나씩 실행한다. 반환: (통과 여부, 실패 목록)."""
    failed = []
    for f in sorted(src.glob('test_*.py')):
        try:
            r = subprocess.run([sys.executable, '-m', 'unittest', f.stem, '-q'], cwd=src, capture_output=True,
                               text=True, timeout=180, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if r.returncode != 0:
                failed.append(f.stem)
        except subprocess.TimeoutExpired:
            failed.append(f.stem + '(시간 초과)')
    return not failed, failed


def backup(app):
    dst = app / '_backup' / dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    dst.mkdir(parents=True)
    for f in list(app.glob('*.py')) + [app / 'README.md', app / 'config.json']:
        if f.exists():
            shutil.copyfile(f, dst / f.name)
    return dst


def restore(app, backup_dir):
    for f in backup_dir.iterdir():
        shutil.copyfile(f, app / f.name)


def patch_config(app, patch):
    """config.json에 중첩 dict를 덮어쓴다(예: {'live': {'trail_enabled': True}}). 호출자가 허용 키를 이미 검사한다."""
    f = app / 'config.json'
    cfg = json.loads(f.read_text(encoding='utf-8-sig'))
    for section, values in patch.items():
        cfg.setdefault(section, {}).update(values)
    f.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')


def request_stop(app):
    for n in STOPS:
        (app / n).write_text('deploy_v5 stop request\n', encoding='utf-8')


def cancel_stop(app):
    for n in STOPS:
        (app / n).unlink(missing_ok=True)


def wait_stopped(app, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not any_alive(app):
            return True
        time.sleep(2)
    return False


def start_bot(launch=None):
    """바탕화면 실행과 같은 경로(launch_v5.py)로 시작한다. 상태 화면이 새로 만들어질 때까지 기다린다."""
    launch = launch or LAUNCH
    r = subprocess.run([sys.executable, str(launch), '--console'], cwd=launch.parent, capture_output=True, text=True,
                       timeout=420, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return r.returncode == 0, (r.stderr or r.stdout)[-400:]


def start_errors(app, since):
    """시작 시각 이후 bot.stderr.log에 오류가 추가됐는지."""
    f = app / 'data' / 'bot.stderr.log'
    if not f.exists() or f.stat().st_mtime < since:
        return ''
    tail = f.read_text(encoding='utf-8', errors='replace')[-1500:]
    return tail if 'Traceback' in tail else ''


def preflight(app=None, src=None, with_tests=True):
    app, src = app or APP, src or SRC
    out = {'diff': sy.differences(), 'alive': any_alive(app), 'pending': pending_orders(app)}
    if with_tests:
        out['tests_ok'], out['tests_failed'] = run_tests(src)
    return out


def deploy(approved_by, app=None, src=None, launch=None, config_patch=None):
    app, src = app or APP, src or SRC
    pre = preflight(app, src)
    log(f'배포 요청({approved_by}): 다른 파일 {pre["diff"]} · 실행 중 {pre["alive"]} · 미확정 주문 {pre["pending"] or "없음"} · 테스트 {"통과" if pre["tests_ok"] else "실패 " + str(pre["tests_failed"])}', app)
    if not pre['diff'] and not config_patch:
        log('적용할 변경 없음', app)
        return 0
    if not pre['tests_ok']:
        log('테스트 실패: 배포하지 않음', app)
        return 1
    if pre['pending']:
        log('미확정 주문: 배포하지 않음. 주문이 정리된 뒤 다시 시도', app)
        return 1
    was_running = bool(pre['alive'])
    bk = backup(app)
    log(f'백업: {bk}', app)
    if was_running:
        request_stop(app)
        log('정상 종료 요청(강제 종료 없음). 최대 %d초 대기' % STOP_TIMEOUT, app)
        if not wait_stopped(app, STOP_TIMEOUT):
            cancel_stop(app)
            log('시간 안에 종료되지 않아 정지 요청을 취소하고 중단함. 봇은 그대로 둠', app)
            return 1
        if pending_orders(app):
            log('종료 후 미확정 주문 발견: 코드는 바꾸지 않고 그대로 재시작 시도', app)
            cancel_stop(app)
            start_bot(launch)
            return 1
    cancel_stop(app)
    started = time.time()
    for name in pre['diff']:
        shutil.copyfile(src / name, app / name)
    if config_patch:
        patch_config(app, config_patch)
        log(f'설정 변경 적용: {config_patch}', app)
    log(f'코드 {len(pre["diff"])}개 적용 (data·.env 그대로' + ('' if config_patch else ', config.json 그대로') + ')', app)
    ok, why = start_bot(launch) if was_running else (True, '')
    err = start_errors(app, started) if was_running else ''
    if was_running and (not ok or err):
        log(f'재시작 실패({why.strip()[:200]} {err[:200]}) -> 백업으로 롤백', app)
        restore(app, bk)
        ok2, why2 = start_bot(launch)
        log('롤백 후 재시작 ' + ('성공' if ok2 else '실패: ' + why2.strip()[:200] + ' — 수동 확인 필요'), app)
        return 2
    log('배포 완료' + ('' if was_running else ' (봇이 꺼져 있어 코드만 적용, 시작은 하지 않음)'), app)
    return 0


def head():
    r = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip()


def v5_clean():
    r = subprocess.run(['git', 'status', '--porcelain', '--', 'v5'], cwd=ROOT, capture_output=True, text=True)
    return not r.stdout.strip()


def approved(head_hash):
    return APPROVAL.exists() and APPROVAL.read_text(encoding='utf-8').strip().split()[:1] == [head_hash]


def watch():
    log('감시 시작: 승인된 커밋만 자동 배포 (tools/APPROVED_COMMIT). 중지: Ctrl+C, 일시 정지: tools/deploy_pause.txt')
    while True:
        try:
            if not PAUSE.exists() and sy.differences() and v5_clean() and approved(head()):
                deploy('감시: 승인 커밋 ' + head()[:7])
        except Exception as e:  # 감시기는 죽지 않는다
            log(f'감시 오류: {type(e).__name__}: {e}')
        time.sleep(WATCH_SECONDS)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--watch', action='store_true')
    ap.add_argument('--patch', help='config.json에 덮어쓸 설정 JSON 파일 경로(예: tools/patches/profit_protection.json). --apply와 함께 사용')
    a = ap.parse_args(argv)
    if not APP.exists():
        print('실행 사본 없음:', APP)
        return 1
    if a.watch:
        watch()
        return 0
    if a.apply:
        patch = json.loads(Path(a.patch).read_text(encoding='utf-8')) if a.patch else None
        return deploy('사용자 직접 실행' + (f' + 설정 {Path(a.patch).name}' if a.patch else ''), config_patch=patch)
    pre = preflight()
    print('다른 파일:', pre['diff'] or '없음')
    print('실행 중인 프로세스:', pre['alive'] or '없음')
    print('미확정 주문:', pre['pending'] or '없음')
    print('테스트:', '통과' if pre['tests_ok'] else '실패 ' + str(pre['tests_failed']))
    print('HEAD', head()[:7], '· 승인 상태:', '승인됨' if approved(head()) else '미승인')
    print('배포하려면: python tools/deploy_v5.py --apply')
    return 0


if __name__ == '__main__':
    sys.exit(main())
