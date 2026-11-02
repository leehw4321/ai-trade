"""Cross-PC trading ownership via an atomic Git ref; no keys/balances are uploaded."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

REF = 'refs/heads/bot-session'
DEFAULT_REMOTE = 'https://github.com/leehw4321/ai-trade.git'


class SessionLost(RuntimeError):
    pass


class GitStore:
    def __init__(self, folder, remote=DEFAULT_REMOTE):
        self.folder = Path(folder)
        self.remote = remote
        self.folder.mkdir(parents=True, exist_ok=True)
        if not (self.folder / 'HEAD').exists():
            self.git('init', '--bare', str(self.folder), outside=True)

    def git(self, *args, input=None, outside=False, check=True):
        env = os.environ.copy()
        env.update(GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never',
                   GIT_AUTHOR_NAME='Bot Session', GIT_AUTHOR_EMAIL='bot@localhost',
                   GIT_COMMITTER_NAME='Bot Session', GIT_COMMITTER_EMAIL='bot@localhost')
        cmd = ['git'] + ([] if outside else ['--git-dir', str(self.folder)]) + list(args)
        result = subprocess.run(cmd, input=input.encode('utf-8') if input is not None else None,
                                capture_output=True, env=env, timeout=25,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        result.stdout = result.stdout.decode('utf-8', errors='replace')
        result.stderr = result.stderr.decode('utf-8', errors='replace')
        if check and result.returncode:
            raise SessionLost('GitHub 연결/인증 실패. 중복 실행 방지를 위해 주문을 중단합니다.')
        return result

    def read(self):
        found = self.git('ls-remote', self.remote, REF).stdout.strip()
        if not found:
            return '', {}
        self.git('fetch', '--quiet', self.remote, REF)
        oid = self.git('rev-parse', 'FETCH_HEAD').stdout.strip()
        state = json.loads(self.git('show', oid + ':session.json').stdout)
        return oid, state

    def cas(self, expected, state):
        # An orphan commit keeps the remote branch history bounded. Only lease metadata is stored.
        blob = self.git('hash-object', '-w', '--stdin', input=json.dumps(state)).stdout.strip()
        tree = self.git('mktree', input=f'100644 blob {blob}\tsession.json\n').stdout.strip()
        commit = self.git('commit-tree', tree, input='Trading session ownership\n').stdout.strip()
        result = self.git('push', '--quiet', f'--force-with-lease={REF}:{expected}',
                          self.remote, f'{commit}:{REF}', check=False)
        if result.returncode:
            # A competing claimant may win. Never overwrite it blindly.
            return False
        return True


def confirm_takeover():
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        return messagebox.askyesno('봇 실행 연결 변경',
            '다른 기계에서 작동 중입니다.\n기존 연결을 끊고 이 연결을 유지할까요?\n\n'
            '기존 봇의 주문 중단을 확인한 뒤 이 PC에서 실행합니다.\n'
            '계좌 조회 화면은 두 PC에서 함께 사용할 수 있습니다.', parent=root)
    finally:
        root.destroy()


def notify(message):
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
    try:
        messagebox.showwarning('봇 실행 상태', str(message), parent=root)
    finally:
        root.destroy()


class SessionGuard:
    def __init__(self, root, cfg=None, store=None, clock=time.time):
        self.root = Path(root)
        self.cfg = cfg or {}
        self.store = store or GitStore(self.root / 'data' / 'session_git', self.cfg.get('remote', DEFAULT_REMOTE))
        self.clock = clock
        self.token = str(uuid.uuid4())
        self.closed = threading.Event()
        self.lost = threading.Event()
        self.mutex = threading.RLock()
        self.reason = ''
        self.acquired = False
        self.thread = None
        self.required_checkpoint = ''

    def checkpoint_hash(self):
        file = self.root / 'data' / 'live' / 'live.json'
        if not file.exists(): return ''
        state = json.loads(file.read_text(encoding='utf-8'))
        return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def record(self, **values):
        state = {'owner': self.token, 'updated': self.clock(), 'expires': self.clock() + 180,
                 'request': '', 'checkpoint': self.required_checkpoint or self.checkpoint_hash()}
        state.update(values)
        return state

    def acquire(self, confirm=confirm_takeover, wait_seconds=240):
        oid, state = self.store.read()
        if state.get('owner') and not confirm():
            return False
        if state.get('owner'):
            wanted = dict(state, request=self.token)
            if not self.store.cas(oid, wanted):
                raise SessionLost('다른 PC가 연결 상태를 바꿨습니다. 다시 실행해 주세요.')
            deadline = self.clock() + wait_seconds
            while self.clock() < deadline:
                oid, state = self.store.read()
                if state.get('request') not in {'', self.token}:
                    raise SessionLost('다른 연결 요청이 우선 처리 중입니다.')
                if not state.get('owner'):
                    break
                if self.clock() > state.get('expires', 0) + 30:
                    if self.cfg.get('live_enabled'):
                        raise SessionLost('기존 PC의 종료 확인이 없습니다. 실거래 연결은 넘기지 않습니다. 기존 봇을 정상 종료한 뒤 다시 실행해 주세요.')
                    break
                time.sleep(2)
            else:
                raise SessionLost('기존 봇의 종료 확인을 기다리다 중단했습니다. 기존 PC 상태를 확인해 주세요.')
        elif state.get('request') not in {'', None, self.token}:
            raise SessionLost('다른 PC로 연결을 넘기는 중입니다. 잠시 뒤 다시 실행해 주세요.')
        # Moving control is not moving the live ledger. Require its exact checkpoint before live trading.
        expected = state.get('checkpoint', '')
        self.required_checkpoint = expected if expected and self.checkpoint_hash() != expected else ''
        if self.cfg.get('live_enabled') and self.required_checkpoint:
            raise SessionLost('기존 PC의 실거래 장부와 다릅니다. 기존 PC의 data 폴더를 비공개로 옮긴 뒤 다시 실행해 주세요.')
        if not self.store.cas(oid, self.record()):
            raise SessionLost('다른 PC가 먼저 연결했습니다. 중복 실행하지 않습니다.')
        self.acquired = True
        return True

    def assert_owner(self):
        with self.mutex:
            if not self.acquired or self.lost.is_set():
                raise SessionLost(self.reason or '이 PC에 주문 실행 권한이 없습니다.')
            try:
                _, state = self.store.read()
                if (state.get('owner') != self.token or state.get('request')
                        or self.clock() >= state.get('expires', 0)):
                    raise SessionLost('다른 PC로 연결이 변경되어 이 PC의 주문을 중단합니다.')
            except Exception as ex:
                self.reason = str(ex); self.lost.set()
                raise SessionLost(self.reason) from ex

    def start(self):
        def watch():
            last_renew = self.clock()
            while not self.closed.wait(10):
                try:
                    with self.mutex:
                        self.assert_owner()
                        if self.clock() - last_renew >= 60:
                            oid, state = self.store.read()
                            if state.get('owner') != self.token or state.get('request'):
                                raise SessionLost('실행 연결 양도 요청을 받았습니다.')
                            if not self.store.cas(oid, self.record()):
                                raise SessionLost('실행 연결 갱신에 실패했습니다.')
                            last_renew = self.clock()
                except Exception as ex:
                    self.reason = str(ex); self.lost.set()
                    return
        self.thread = threading.Thread(target=watch, daemon=True)
        self.thread.start()

    def release(self):
        self.closed.set()
        if self.thread:
            self.thread.join(timeout=80)
        with self.mutex:
            try:
                oid, state = self.store.read()
                if self.acquired and state.get('owner') == self.token:
                    self.store.cas(oid, self.record(owner='', expires=0,
                        request=state.get('request', ''),
                        checkpoint=self.required_checkpoint or self.checkpoint_hash()))
                elif state.get('request') == self.token:
                    # Failed/cancelled handoff must not reserve a connection forever.
                    self.store.cas(oid, dict(state, request=''))
            except Exception:
                pass  # Retain the expiring lease if the network is unavailable.
            self.acquired = False
