"""ai-trade/v5 (정본)의 코드를 실행 사본(코인 트레이딩/v5-app/v5)에 맞춘다. 설정·장부·키는 건드리지 않는다.

  python tools/sync_v5.py            무엇이 다른지만 출력
  python tools/sync_v5.py --apply    봇이 꺼져 있을 때만 복사 (봇 실행 중이면 거부)
규칙: 코드는 ai-trade에서만 고친다. 실행 사본은 이 도구로만 갱신한다.
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'v5'
DST = ROOT.parent / '코인 트레이딩' / 'v5-app' / 'v5'
SKIP = {'config.json'}


def digest(f):
    return hashlib.sha256(f.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def differences():
    out = []
    for f in sorted(SRC.glob('*.py')) + [SRC / 'README.md']:
        if f.name in SKIP or not f.exists():
            continue
        g = DST / f.name
        if not g.exists() or digest(f) != digest(g):
            out.append(f.name)
    return out


def bot_running():
    import re
    ps = ("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          "Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'python.exe','pythonw.exe' } "
          "| ForEach-Object { 'P:' + $_.CommandLine }; 'END'")
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        raise RuntimeError('프로세스 조회 실패') from None
    if r.returncode != 0:
        raise RuntimeError('프로세스 조회 실패')
    lines = (r.stdout or '').lstrip('\ufeff').splitlines()
    if not lines or lines[-1].strip() != 'END':
        raise RuntimeError('프로세스 조회 출력 이상')
    pat = re.compile(r'(?i)(?:^|[\s"\'\\/])(?:bot|shadow)\.py(?=["\s]|$)')
    for line in lines[:-1]:
        if not line.startswith('P:'):
            raise RuntimeError('프로세스 조회 출력 이상')
        cmd = line[2:]
        if not cmd.strip():
            raise RuntimeError('프로세스 명령줄 조회 불명')
        if pat.search(cmd):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    if not DST.exists():
        print('실행 사본 없음:', DST)
        return 1
    diff = differences()
    print('다른 파일:', diff or '없음')
    if not a.apply or not diff:
        return 0
    if bot_running():
        print('봇이 실행 중입니다. 정지 후 다시 실행하세요 (python 코인 트레이딩/launch_v5.py --stop).')
        return 1
    for name in diff:
        shutil.copyfile(SRC / name, DST / name)
    print('복사 완료:', len(diff), '개 (config.json·data·.env는 그대로)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
