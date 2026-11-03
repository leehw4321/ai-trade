"""한 번에 이 PC로 실거래를 가져온다 (매일 회사↔집 이동용).

  python tools/take_over.py          확인창 한 번 뒤 전부 자동
  python tools/take_over.py --check  무엇을 할지만 출력 (변경 없음)

순서: 최신 코드 받기 → 실행본 갱신 → 운용 설정 맞추기 → 다른 PC 봇에 양도 요청(그 봇은 스스로 주문을 멈추고 반납)
      → 장부가 이어지면 그대로, 아니면 현재 계좌 전체를 새 기준으로 인수 → 봇·그림자 시작 → 현황 열기.
실거래 장부는 PC 밖으로 보내지 않는다. 다른 PC에서 넘어올 때는 계좌 인수라 손절가는 현재가 기준으로 다시 잡힌다.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
APP = REPO.parent / '코인 트레이딩' / 'v5-app' / 'v5'
LOG = APP / 'data' / 'takeover.log'
PY = Path(sys.executable)
PYW = PY.with_name('pythonw.exe') if PY.with_name('pythonw.exe').exists() else PY


def say(msg):
    line = f'[{dt.datetime.now():%m-%d %H:%M:%S}] {msg}'
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open('a', encoding='utf-8') as f:
        f.write(line + '\n')


def box(kind, text):
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
    try:
        return getattr(messagebox, kind)('자동거래 v5 · 이 PC에서 시작', text, parent=root)
    finally:
        root.destroy()


def bot_running():
    lock = APP / 'bot.lock'
    if not lock.exists():
        return False
    pid = lock.read_text().strip()
    return bool(pid) and pid in subprocess.run(['tasklist', '/FI', f'PID eq {pid}'], capture_output=True, text=True,
                                               encoding='mbcs', errors='replace').stdout


def run(args, cwd):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    return r.returncode, (r.stdout + r.stderr).strip()


def update_code():
    code, out = run(['git', 'pull', '--ff-only', '--quiet'], REPO)
    say('최신 코드 받음' if code == 0 else f'코드 받기 건너뜀(이 PC 코드로 진행): {out[-200:]}')
    if not APP.exists():
        APP.mkdir(parents=True)
        for f in (REPO / 'v5').iterdir():
            if f.is_file():
                shutil.copy2(f, APP / f.name)
        say('실행본 새로 만듦')
    else:
        code, out = run([str(PY), 'tools/sync_v5.py', '--apply'], REPO)
        say('실행본 갱신' if code == 0 else f'실행본 갱신 실패: {out[-200:]}')
    for name in ('모의운영 시작.bat', '모의운영 중지.bat'):
        if (REPO / 'v5' / name).exists():
            shutil.copy2(REPO / 'v5' / name, APP / name)


def align_config(enabled=None):
    """두 PC가 같은 규칙을 쓰도록 저장소 기본값 위에 합의 조각(tools/patches)을 덮는다."""
    path = APP / 'config.json'
    cfg = json.loads(path.read_text(encoding='utf-8')) if path.exists() else \
        json.loads((REPO / 'v5' / 'config.json').read_text(encoding='utf-8'))
    for f in sorted((REPO / 'tools' / 'patches').glob('*.json')):
        for sec, vals in json.loads(f.read_text(encoding='utf-8')).items():
            cfg.setdefault(sec, {}).update(vals)
    if enabled is not None:
        cfg.setdefault('live', {})['enabled'] = enabled
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)
    return cfg


def lease():
    sys.path.insert(0, str(APP))
    from session_guard import GitStore
    store = GitStore(APP / 'data' / 'session_git')
    return store.read()[1]


def local_hash():
    sys.path.insert(0, str(APP))
    from session_guard import SessionGuard
    return SessionGuard(APP).checkpoint_hash()


def request_handover():
    """다른 PC 봇에 양도를 요청하고 반납을 기다린 뒤, 비운 상태로 둔다."""
    sys.path.insert(0, str(APP))
    from session_guard import SessionGuard
    g = SessionGuard(APP, {'live_enabled': False})
    g.acquire(confirm=lambda: True)  # 확인은 이 도구가 먼저 받았다
    g.release()


def adopt():
    live = APP / 'data' / 'live'
    if (live / 'live.json').exists():
        keep = APP / 'data' / f"live_이전장부_{dt.datetime.now():%Y%m%d_%H%M%S}"
        live.rename(keep)
        say(f'이 PC의 이전 장부 보관: {keep.name}')
    align_config(enabled=False)  # 인수 도구는 꺼진 상태에서만 새 기준을 만든다
    code, out = run([str(PY), 'adopt_account.py', '--user-authorized-all-holdings'], APP)
    if code:
        raise RuntimeError('계좌 인수 실패: ' + out[-300:])
    say('계좌 인수 완료: ' + out.splitlines()[0][:200])


def start_bot():
    for name in ('stop.txt', 'stop_shadow.txt', '중지.txt'):
        (APP / name).unlink(missing_ok=True)
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    for args in (['bot.py'], ['shadow.py'], ['account_sync.py'], ['exit_study.py'], ['pnl.py', '--loop', '300']):
        if (APP / args[0]).exists():
            subprocess.Popen([str(PYW)] + args, cwd=APP, creationflags=flags, close_fds=True)
    for _ in range(90):
        if bot_running():
            return True
        time.sleep(1)
    return False


def shortcut():
    link = Path.home() / 'Desktop' / '자동거래 이 PC에서 시작.lnk'
    if link.exists():
        return
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
          f"$s.TargetPath='{PYW}';$s.Arguments='\"{REPO / 'tools' / 'take_over.py'}\"';"
          f"$s.WorkingDirectory='{REPO}';$s.IconLocation='C:\\Windows\\System32\\shell32.dll,137';$s.Save()")
    subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args()
    if bot_running():
        say('이 PC 봇이 이미 실행 중')
        if not a.check:
            os.startfile(APP / 'data' / 'status.html')
        return
    st = lease()
    other = bool(st.get('owner')) and time.time() < st.get('expires', 0) + 30
    same = bool(st.get('checkpoint')) and st.get('checkpoint') == local_hash()
    plan = ['최신 코드 받기·실행본 갱신', '운용 설정 맞추기',
            '다른 PC 봇에 양도 요청' if other else '다른 PC 실행 없음',
            '이 PC 장부 그대로 이어감' if same else '현재 계좌 전체를 새 기준으로 인수', '봇·그림자·계좌 조회 시작']
    if a.check:
        print('\n'.join(f'{i}. {x}' for i, x in enumerate(plan, 1)))
        return
    shortcut()
    msg = ('다른 PC에서 실거래 중입니다.\n이 PC로 가져올까요? 그 PC 봇은 주문을 멈춥니다.\n\n' if other else
           '이 PC에서 실거래를 시작할까요?\n\n') + '\n'.join(f'· {x}' for x in plan)
    if not box('askyesno', msg):
        say('사용자가 취소')
        return
    try:
        update_code()
        align_config()
        if other:
            say('양도 요청 · 다른 PC 봇의 반납을 기다리는 중 (최대 4분)')
            request_handover()
            say('다른 PC 봇 반납 확인')
        elif st.get('owner') or st.get('request'):
            say('만료된 실행권 정리 (다른 PC가 응답 없이 꺼진 경우)')
            request_handover()
        if same:
            align_config(enabled=True)
        else:
            adopt()
        ok = start_bot()
        say('봇 시작' if ok else '봇 시작 확인 실패')
        os.startfile(APP / 'data' / 'status.html')
        box('showinfo' if ok else 'showwarning',
            '이 PC에서 실거래를 시작했습니다.' if ok else f'봇이 시작되지 않았습니다. 기록: {LOG}')
    except Exception as ex:
        say(f'중단: {type(ex).__name__}: {ex}')
        box('showerror', f'중단했습니다: {ex}\n\n기록: {LOG}')


if __name__ == '__main__':
    main()
