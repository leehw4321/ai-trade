"""봇 고정 PC용 런처: 실행본의 봇·그림자·청산 평가·실현손익 계산을 켜고 현황 화면이 갱신될 때까지 기다린다.

집 PC의 코인 트레이딩/launch_v5.py가 없는 PC에서 deploy_v5.py가 재시작에 쓴다(take_over가 없을 때만 복사).
  python launch_v5.py --console
종료 코드 0 = 봇이 실행 중이고 현황 화면이 새로 쓰였다.
"""
import subprocess
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parent / 'v5-app' / 'v5'
PYW = Path(sys.executable).with_name('pythonw.exe')
PYW = PYW if PYW.exists() else Path(sys.executable)


def alive(lock):
    f = APP / lock
    if not f.exists():
        return False
    pid = f.read_text().strip()
    out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}'], capture_output=True, text=True, encoding='mbcs', errors='replace').stdout
    return bool(pid) and pid in out


def main():
    status = APP / 'data' / 'status.html'
    before = status.stat().st_mtime if status.exists() else 0
    for name in ('stop.txt', 'stop_shadow.txt', 'stop_exit_study.txt', '중지.txt'):
        (APP / name).unlink(missing_ok=True)
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    for args, lock in ((['bot.py'], 'bot.lock'), (['shadow.py'], 'shadow.lock'), (['exit_study.py'], 'exit_study.lock')):
        if (APP / args[0]).exists() and not alive(lock):
            subprocess.Popen([str(PYW)] + args, cwd=APP, creationflags=flags, close_fds=True)
    running = subprocess.run(['powershell', '-NoProfile', '-Command',
                              "@(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*pnl.py --loop*' }).Count"],
                             capture_output=True, text=True).stdout.strip()
    if running in ('', '0') and (APP / 'pnl.py').exists():
        subprocess.Popen([str(PYW), 'pnl.py', '--loop', '300'], cwd=APP, creationflags=flags, close_fds=True)
    for _ in range(180):
        if alive('bot.lock') and status.exists() and status.stat().st_mtime > before:
            return 0
        time.sleep(2)
    print('봇 시작 또는 현황 갱신을 확인하지 못함', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
