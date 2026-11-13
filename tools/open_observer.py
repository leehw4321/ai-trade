"""바탕화면 「자동거래 관찰창」: 실행본의 관찰창(owner_watch.py, 조회 전용)을 켜고(이미 켜져 있으면 그대로) 화면을 연다."""
import os
import subprocess
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[1].parent / '코인 트레이딩' / 'v5-app' / 'v5'
PY = Path(sys.executable)
PYW = PY.with_name('pythonw.exe') if PY.with_name('pythonw.exe').exists() else PY


def main():
    out = APP / 'data' / 'observe.html'
    subprocess.Popen([str(PYW), 'owner_watch.py'], cwd=APP, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for _ in range(30):   # 첫 화면이 써질 때까지 최대 15초
        if out.exists() and time.time() - out.stat().st_mtime < 20: break
        time.sleep(0.5)
    os.startfile(str(out))


if __name__ == '__main__':
    main()
