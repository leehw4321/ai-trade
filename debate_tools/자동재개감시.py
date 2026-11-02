"""Watch the debate broker from outside and restart it after usage-limit stops.

토론진행기.py is not modified. This only reads its log/lock files and relaunches
`토론진행기.py --continuous-run` when the last run ended for a retryable reason.
"""
import datetime as dt
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
BROKER = ROOT / '토론진행기.py'
LOG = ROOT / '진행기록.log'
LOCK = ROOT / '진행중.lock'
STOP = ROOT / '중지.txt'
STATUS = ROOT / '진행상태.txt'
OUT = ROOT / '자동진행기록'
MYLOG = ROOT / '자동재개기록.log'
KST = dt.timezone(dt.timedelta(hours=9))

LIMIT_WAIT = 30 * 60      # 사용량 한도: 30분마다 재시도 (충전 시각을 몰라도 5시간 안에 재개)
ERROR_WAIT = 5 * 60       # 그 밖의 오류: 5분 뒤 재시도
MAX_ERRORS = 3            # 사용량 외 오류가 연속 3번이면 재시도를 멈춘다
LIMIT_WORDS = re.compile(r'usage limit|rate limit|rate_limit|usage_limit|hit your limit|limit reached|'
                         r'too many requests|\b429\b|resets? at|사용량 한도|사용 한도', re.I)
USER_STOP = ('사용자가 중지', '중지 요청', '중지.txt')
DONE = ('완료: 사용자 검토', '완료: 1차합의안')


def now():
    return dt.datetime.now(KST)


def note(msg, status=True):
    line = f'[{now():%Y-%m-%d %H:%M:%S}] {msg}'
    with MYLOG.open('a', encoding='utf-8') as f:
        f.write(line + '\n')
    if status and not LOCK.exists():
        STATUS.write_text(f'{now():%Y-%m-%d %H:%M:%S}\n{msg}', encoding='utf-8')


def last_event():
    if not LOG.exists():
        return ''
    lines = [l for l in LOG.read_text(encoding='utf-8', errors='replace').splitlines() if l.strip()]
    return lines[-1] if lines else ''


def limit_hit(since):
    """Look for usage-limit wording in the output of the last (failed) AI call only."""
    if not OUT.exists():
        return False
    calls = sorted(OUT.glob('*.stdout'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not calls or calls[0].stat().st_mtime < since:
        return False
    stem = calls[0].name[:-len('.stdout')]
    for p in (OUT / (stem + '.stdout'), OUT / (stem + '.stderr')):
        if p.exists() and LIMIT_WORDS.search(p.read_bytes()[-20000:].decode('utf-8', errors='replace')):
            return True
    return False


def launch():
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('CLAUDECODE', 'CLAUDE_CODE'))}
    exe = Path(sys.executable)
    pythonw = exe.with_name('pythonw.exe') if exe.with_name('pythonw.exe').exists() else exe
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen([str(pythonw), str(BROKER), '--continuous-run'], cwd=ROOT, env=env,
                     creationflags=flags, close_fds=True)


def main():
    note('자동 재개 감시 시작. 사용량 한도로 멈추면 30분마다 다시 시도합니다.', status=False)
    handled = last_event() if not LOCK.exists() else ''
    retry_at = None
    errors = 0
    stopped_at = time.time()
    while True:
        time.sleep(10)
        if LOCK.exists():
            retry_at = None
            stopped_at = time.time()
            if '기록 완료' in last_event():
                errors = 0
            continue
        if STOP.exists():
            retry_at = None
            continue
        event = last_event()
        if retry_at is None:
            if event == handled or '중단:' not in event:
                if any(s in event for s in DONE):
                    handled = event
                continue
            handled = event
            if any(s in event for s in USER_STOP):
                continue
            if limit_hit(stopped_at - 900):
                errors = 0
                retry_at = time.time() + LIMIT_WAIT
                note(f'사용량 한도로 멈췄습니다. {dt.datetime.fromtimestamp(retry_at, KST):%H:%M}에 다시 시도합니다.')
            else:
                errors += 1
                if errors > MAX_ERRORS:
                    note(f'사용량이 아닌 오류가 {MAX_ERRORS}번 연속 발생해 자동 재개를 멈춥니다: {event}')
                    continue
                retry_at = time.time() + ERROR_WAIT
                note(f'오류로 멈췄습니다({errors}/{MAX_ERRORS}). {dt.datetime.fromtimestamp(retry_at, KST):%H:%M}에 다시 시도합니다: {event}')
        elif time.time() >= retry_at:
            retry_at = None
            note('토론을 다시 시작합니다.', status=False)
            launch()
            for _ in range(60):
                if LOCK.exists() or last_event() != handled:
                    break
                time.sleep(1)


if __name__ == '__main__':
    lock = ROOT / '자동재개감시.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        pid = lock.read_text().strip()
        alive = subprocess.run(['tasklist', '/FI', f'PID eq {pid}'], capture_output=True, text=True).stdout
        if pid and pid in alive:
            sys.exit(0)
        lock.write_text(str(os.getpid()))
    try:
        main()
    finally:
        lock.unlink(missing_ok=True)
