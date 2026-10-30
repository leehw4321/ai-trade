"""Desktop launcher for the local dashboard. Never starts the live trading bot."""
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8766'


def dashboard_ready():
    try:
        with urlopen(URL + '/api/data', timeout=2) as response:
            data = json.load(response)
        if not isinstance(data, dict) or not all(key in data for key in ('cfg', 'positions', 'rec', 'bankroll')):
            raise RuntimeError('CoinBot port is being used by a different application.')
        return True
    except HTTPError:
        raise RuntimeError('CoinBot port returned an unexpected response.') from None
    except (URLError, TimeoutError, ConnectionError):
        return False
    except (ValueError, UnicodeError):
        raise RuntimeError('CoinBot port is being used by a different application.') from None


def start_dashboard():
    python = Path(sys.executable).with_name('python.exe')
    if not python.exists():
        python = Path(sys.executable)
    with (ROOT / 'dashboard.stdout.log').open('ab') as stdout, (ROOT / 'dashboard.stderr.log').open('ab') as stderr:
        process = subprocess.Popen([str(python), str(ROOT / 'dashboard.py')], cwd=str(ROOT),
                                   stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if dashboard_ready():
            return
        if process.poll() is not None:
            raise RuntimeError('The dashboard could not start. See dashboard.stderr.log.')
        time.sleep(.3)
    raise RuntimeError('The dashboard did not respond in time. See dashboard.stderr.log.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Check running dashboard without starting anything')
    parser.add_argument('--no-browser', action='store_true', help='Start dashboard without opening a browser')
    args = parser.parse_args()
    if args.check:
        ready = dashboard_ready()
        print(json.dumps({'dashboard_ready': ready, 'url': URL}))
        return 0 if ready else 1
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, 'Local\\CoinBotDashboardLauncher')
    if not handle:
        raise RuntimeError('Could not create dashboard launcher lock.')
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(handle)
        return 0
    try:
        if not dashboard_ready():
            start_dashboard()
        if not args.no_browser and not webbrowser.open(URL, new=2):
            ctypes.windll.user32.MessageBoxW(None, URL, 'CoinBot dashboard', 0x40)
    finally:
        kernel.CloseHandle(handle)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        ctypes.windll.user32.MessageBoxW(None,
            '코인봇 대시보드를 열 수 없습니다.\nD:\\github\\coin\\dashboard.stderr.log를 확인해 주세요.',
            '코인봇 실행 오류', 0x10)
        raise SystemExit(1)
