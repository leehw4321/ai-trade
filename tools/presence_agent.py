"""지금 사용 중인 PC로 실거래 실행권을 자동으로 옮기는 상주 감시기 (각 PC에서 하나씩 돈다).

  python tools/presence_agent.py            감시 시작(보통 시작 프로그램으로 자동 실행)
  python tools/presence_agent.py --install  Windows 시작 프로그램에 등록하고 지금 시작
  python tools/presence_agent.py --uninstall
  python tools/presence_agent.py --once     지금 무엇을 할지 한 번만 판단해 출력(변경 없음)

규칙: 이 PC에서 키보드·마우스 입력이 30초쯤 이어지면 사용 중으로 보고, 실행권이 다른 PC에 있거나 비어 있으면
`take_over.py --button`(확인창 없음)으로 가져온다. 반대쪽 봇은 양도 요청을 받으면 스스로 주문을 멈추고 반납한다(session_guard).
핑퐁 방지: 시도 사이 10분, 실패하면 5분→15분→1시간으로 늦춘다. 다른 PC가 방금 가져갔다면 10분이 지나야 다시 가져온다.
멈추려면 tools/presence_pause.txt 파일을 만든다(감시만 쉬고 실행권은 그대로).
"""
import argparse
import ctypes
import datetime as dt
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import take_over as T

POLL = 10            # 입력 확인 주기(초)
ACTIVE_IDLE = 60     # 마지막 입력이 이보다 최근이면 '사용 중'
ACTIVE_STREAK = 3    # 연속 몇 번 사용 중이어야 하는지(약 30초)
COOLDOWN = 600       # 자동 인수 시도 사이 최소 간격, 그리고 다른 PC의 인수 직후 대기
OWN_GRACE = 300      # 이 PC가 소유자였다가 비어 보이면(배포·재시작 중 정상 반납) 이만큼 기다린 뒤에야 다시 가져온다
BACKOFF = (300, 900, 3600)
RUN_TIMEOUT = 900
PAUSE = T.REPO / 'tools' / 'presence_pause.txt'
STATE = T.APP / 'data' / 'presence_state.json'
LOGFILE = T.APP / 'data' / 'presence.log'
LOCK = T.APP / 'data' / 'presence.lock'
STARTUP = Path(os.environ.get('APPDATA', str(Path.home()))) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'Startup' / '자동거래 실행권 자동 이동.lnk'


def log(msg):
    line = f'[{dt.datetime.now():%m-%d %H:%M:%S}] {msg}'
    print(line, flush=True)
    try:
        LOGFILE.parent.mkdir(parents=True, exist_ok=True)
        with LOGFILE.open('a', encoding='utf-8') as f:
            f.write(line + '\n')
    except OSError:
        pass


def idle_seconds():
    """마지막 키보드·마우스 입력 뒤 지난 초. 알 수 없으면 아주 큰 값(=사용 중 아님)."""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.UINT), ('dwTime', wintypes.DWORD)]
    try:
        info = LASTINPUTINFO(); info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 1e9
        return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0
    except Exception:
        return 1e9


def decide(now, idle, streak, lease, mine, bot_up, last_attempt, fails, owner_change_at, paused, bot_change_at=0):
    """(행동, 이유). 행동: pause | idle | wait | ok | cooldown | settling | take"""
    if paused:
        return 'pause', '일시정지 파일이 있음'
    if idle >= ACTIVE_IDLE:
        return 'idle', '사용 중 아님'
    if streak < ACTIVE_STREAK:
        return 'wait', '사용 시작을 확인하는 중'
    live = bool(lease.get('owner')) and now < lease.get('expires', 0) + 30
    if live and mine and bot_up:
        return 'ok', '이 PC가 이미 실행권을 가짐'
    if (not live or (mine and not bot_up)) and now - bot_change_at < OWN_GRACE:
        return 'grace', '이 PC 봇이 방금 켜지거나 꺼졌음(배포·재시작 중일 수 있어 기다림)'
    wait = max(COOLDOWN, BACKOFF[min(fails, len(BACKOFF)) - 1] if fails else 0)
    if now - last_attempt < wait:
        return 'cooldown', f'직전 시도 뒤 {int(wait)}초 대기 중'
    if live and not mine and now - owner_change_at < COOLDOWN:
        return 'settling', '다른 PC가 방금 가져가 잠시 기다림'
    if live and mine:
        return 'take', '이 PC가 소유자로 기록돼 있으나 봇이 꺼져 있어 다시 시작'
    return 'take', ('다른 PC에서 가져옴' if live else '실행권이 비어 있어 가져옴')


def load_state():
    try:
        return json.loads(STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_state(st):
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st), encoding='utf-8')
    except OSError:
        pass


def my_pc():
    sys.path.insert(0, str(T.APP))
    from session_guard import pc_identity
    return pc_identity(T.APP)


def grant_state():
    try:
        return json.loads((T.APP / 'data' / 'grant_status.json').read_text(encoding='utf-8')).get('state')
    except (OSError, ValueError):
        return None


def run_take_over():
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        r = subprocess.run([str(T.PY), str(T.REPO / 'tools' / 'take_over.py'), '--button'], cwd=T.REPO,
                           timeout=RUN_TIMEOUT, creationflags=flags, capture_output=True)
        return r.returncode == 0 and grant_state() == 'done'
    except subprocess.TimeoutExpired:
        return False


def single_instance():
    try:
        pid = int(LOCK.read_text().strip())
        if pid != os.getpid() and str(pid) in subprocess.run(['tasklist', '/FI', f'PID eq {pid}'], capture_output=True,
                                                             text=True, encoding='mbcs', errors='replace',
                                                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout:
            return False
    except (OSError, ValueError):
        pass
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(str(os.getpid()))
    return True


def once(streak=ACTIVE_STREAK):
    st = load_state(); now = time.time()
    lease = T.lease(); pc = my_pc()
    mine = isinstance(lease.get('owner'), str) and lease['owner'].startswith(pc + ':')
    return decide(now, idle_seconds(), streak, lease, mine, T.bot_running(), st.get('last_attempt', 0),
                  st.get('fails', 0), st.get('owner_change_at', 0), PAUSE.exists(), st.get('bot_change_at', 0))


def hide_child_windows():
    """감시기가 띄우는 tasklist·git 같은 하위 프로그램의 검은 창이 번쩍이지 않게 한다(이 프로세스 안에서만)."""
    orig = subprocess.Popen.__init__
    def init(self, *a, **kw):
        kw['creationflags'] = kw.get('creationflags', 0) | getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        orig(self, *a, **kw)
    subprocess.Popen.__init__ = init


LEASE_EVERY = 60  # 사용 중이어도 GitHub 실행권 확인은 이 간격으로만(조용하고 가볍게)


def watch():
    hide_child_windows()
    if not single_instance():
        print('이미 실행 중')
        return
    log('실행권 자동 이동 감시 시작')
    streak = 0
    prev_up = T.bot_running()
    last_check = 0
    while True:
        try:
            streak = streak + 1 if idle_seconds() < ACTIVE_IDLE else 0
            up = T.bot_running()  # 봇이 켜지거나 꺼진 시각을 기록(사용 여부와 무관, 네트워크 없음): 배포·재시작을 알아보는 데 쓴다
            if up != prev_up:
                st0 = load_state(); st0['bot_change_at'] = time.time(); save_state(st0)
                prev_up = up
            if streak >= ACTIVE_STREAK and not PAUSE.exists() and time.time() - last_check >= LEASE_EVERY:
                last_check = time.time()
                st = load_state(); now = time.time()
                lease = T.lease(); pc = my_pc()
                owner = lease.get('owner') or ''
                owner_pc = owner.split(':')[0] if isinstance(owner, str) else ''
                if st.get('owner_seen') is not None and owner_pc != st['owner_seen']:
                    st['owner_change_at'] = now   # 어느 PC의 소유인지가 바뀐 시각(봇 재시작은 같은 PC라 바뀐 게 아니다)
                st['owner_seen'] = owner_pc
                mine = owner_pc == pc
                action, why = decide(now, idle_seconds(), streak, lease, mine, T.bot_running(), st.get('last_attempt', 0),
                                     st.get('fails', 0), st.get('owner_change_at', 0), False, st.get('bot_change_at', 0))
                if action == 'take':
                    log(f'사용 중 확인 · {why} · 인수 시작')
                    st['last_attempt'] = now; save_state(st)
                    ok = run_take_over()
                    st['fails'] = 0 if ok else st.get('fails', 0) + 1
                    st['owner_change_at'] = time.time() if ok else st.get('owner_change_at', 0)
                    log('인수 성공' if ok else f'인수 실패 · 다음 시도는 {max(COOLDOWN, BACKOFF[min(st["fails"], len(BACKOFF)) - 1])}초 뒤 (기록: takeover.log)')
                save_state(st)
        except Exception as ex:  # 감시기는 어떤 오류로도 죽지 않는다
            log(f'감시 오류 {type(ex).__name__}: {str(ex)[:150]}')
            time.sleep(30)
        time.sleep(POLL)


def install():
    pyw = T.PYW
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{STARTUP}');"
          f"$s.TargetPath='{pyw}';$s.Arguments='\"{Path(__file__).resolve()}\"';"
          f"$s.WorkingDirectory='{T.REPO}';$s.WindowStyle=7;$s.Save()")
    r = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if r.returncode or not STARTUP.exists():
        print('시작 프로그램 등록 실패:', r.stderr[-200:]); return 1
    subprocess.Popen([str(pyw), str(Path(__file__).resolve())], cwd=T.REPO,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
    print('등록했습니다:', STARTUP)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--install', action='store_true'); ap.add_argument('--uninstall', action='store_true')
    ap.add_argument('--once', action='store_true')
    a = ap.parse_args()
    if a.install:
        sys.exit(install())
    if a.uninstall:
        STARTUP.unlink(missing_ok=True); print('시작 프로그램 등록을 지웠습니다(실행 중인 감시기는 로그아웃·재부팅 때 사라집니다).'); return
    if a.once:
        print(once()); return
    watch()


if __name__ == '__main__':
    main()
