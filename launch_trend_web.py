"""Open V3 dashboard; never enable entries or restart a trading server automatically."""
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen
from urllib.error import URLError
import webbrowser

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8768'


def ready():
    try:
        with urlopen(URL + '/api/health', timeout=2) as r:
            data = json.load(r)
        if data.get('app') != 'auto-trading-v4':
            raise RuntimeError('8768 주소를 다른 앱이 사용 중입니다.')
        if not data.get('auto_v3') or not data.get('all_krw'):
            raise RuntimeError('구버전 서버가 실행 중입니다. 보유분·미체결 관리 상태를 확인하고 서버를 정상 종료한 뒤 다시 실행하세요. 브라우저 새로고침만으로 서버 코드가 갱신되지는 않습니다.')
        return True
    except (URLError, TimeoutError, ConnectionError):
        return False


def main():
    if not ready():
        python = Path(sys.executable).with_name('python.exe')
        with (ROOT/'trend_web.stdout.log').open('ab') as out, (ROOT/'trend_web.stderr.log').open('ab') as err:
            p = subprocess.Popen([str(python), str(ROOT/'trend_web.py')], cwd=ROOT, stdin=subprocess.DEVNULL,
                                 stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic()+25
        while not ready():
            if p.poll() is not None:
                raise RuntimeError('새 봇 서버를 시작하지 못했습니다. 기존 트렌드 봇 창을 닫고 다시 여세요. trend_web.stderr.log를 확인하세요.')
            if time.monotonic()>deadline:
                raise RuntimeError('서버 시작 대기 시간이 초과됐습니다.')
            time.sleep(.3)
    webbrowser.open(URL, new=2)


if __name__=='__main__':
    try:
        main()
    except Exception as e:
        ctypes.windll.user32.MessageBoxW(None,str(e),'트렌드 봇 실행 확인',0x10)
