"""봇 화면(HTML)을 크롬으로 연다. 크롬이 없으면 시스템 기본 브라우저를 쓴다.

- `CHROME_PATH` 환경변수로 크롬 위치를 지정할 수 있다.
- `V5_NO_BROWSER=1`이면 아무것도 열지 않는다(배포 자동 재시작이 탭을 계속 늘리지 않게).
"""
import os
import subprocess
import webbrowser
from pathlib import Path

CANDIDATES = (
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
)


def find_chrome():
    paths = [os.environ.get('CHROME_PATH', '')]
    local = os.environ.get('LOCALAPPDATA')
    if local:
        paths.append(str(Path(local) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe'))
    paths += CANDIDATES
    return next((p for p in paths if p and os.path.isfile(p)), None)


def open_page(target):
    """파일 경로 또는 URL을 연다. 반환: 'skipped' | 'chrome' | 'default'."""
    if os.environ.get('V5_NO_BROWSER') == '1':
        return 'skipped'
    text = str(target)
    uri = text if text.startswith(('http://', 'https://', 'file:')) else Path(text).resolve().as_uri()
    chrome = find_chrome()
    if chrome:
        try:
            subprocess.Popen([chrome, uri], close_fds=True)
            return 'chrome'
        except OSError:
            pass
    webbrowser.open(uri)
    return 'default'
