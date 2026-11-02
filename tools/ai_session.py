"""AI 작업 이력 도구. 새 PC·새 세션에서 맥락을 한 번에 불러오고, 끝낼 때 기록을 Git에 보관한다.

  python tools/ai_session.py start            최신 코드 받기 + 인계 요약 출력
  python tools/ai_session.py end -m "요약"    비밀정보 검사 -> 토론 스냅샷 저장 -> 커밋 -> 푸시
  python tools/ai_session.py end --no-push    커밋까지만
"""
import argparse
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / 'docs' / 'ai-history'
KST = dt.timezone(dt.timedelta(hours=9))
SECRET = [
    re.compile(r'UPBIT_(?:ACCESS|SECRET)_KEY\s*=\s*(?!\s*$)(?!your|xxx|<|\.\.\.)\S{12,}', re.I),
    re.compile(r'(?:access|secret)[_ -]?key["\']?\s*[:=]\s*["\']?[A-Za-z0-9]{24,}', re.I),
    re.compile(r'Bearer\s+[A-Za-z0-9._\-]{20,}'),
    re.compile(r'\b(?:sk|ghp|gho|github_pat)_[A-Za-z0-9_]{20,}'),
]
TRACKED = ['HANDOFF.md', 'CLAUDE.md', 'AGENTS.md', 'docs', 'tools', 'debate_tools', 'v5', '.gitignore']


def git(*a, check=False):
    r = subprocess.run(['git', *a], cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if check and r.returncode:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return r


def today():
    return dt.datetime.now(KST).date().isoformat()


def find_secrets(text):
    return [p.pattern[:40] for p in SECRET if p.search(text)]


def scan(paths):
    bad = []
    for base in paths:
        p = ROOT / base
        files = [p] if p.is_file() else [f for f in p.rglob('*') if f.is_file()] if p.exists() else []
        for f in files:
            if f.suffix.lower() not in ('.md', '.py', '.txt', '.json', '.bat') or f.name in ('ai_session.py', 'test_ai_session.py'):
                continue
            try:
                hits = find_secrets(f.read_text(encoding='utf-8', errors='ignore'))
            except OSError:
                continue
            if hits:
                bad.append((str(f.relative_to(ROOT)), hits))
    return bad


def running():
    if os.name != 'nt':
        return []
    r = subprocess.run(['powershell', '-NoProfile', '-Command',
                        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ForEach-Object { $_.ProcessId.ToString() + ' ' + $_.CommandLine }"],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    return [l.strip() for l in r.stdout.splitlines() if re.search(r'토론진행기|bot\.py|shadow\.py|account_sync|run\.py|launch_', l)]


def start(_):
    print(f'== PC {os.environ.get("COMPUTERNAME", "?")} / {dt.datetime.now(KST):%Y-%m-%d %H:%M} KST ==')
    r = git('pull', '--ff-only')
    print('[git pull]', (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr).strip() else 'ok')
    dirty = git('status', '--short').stdout.strip()
    print('[미커밋 변경]', '없음' if not dirty else '\n' + dirty)
    h = ROOT / 'HANDOFF.md'
    print('\n===== HANDOFF.md =====\n' + (h.read_text(encoding='utf-8') if h.exists() else '(없음: 인계 문서를 먼저 만드세요)'))
    logs = sorted(HIST.glob('20??-??-??.md'))
    if logs:
        lines = logs[-1].read_text(encoding='utf-8').splitlines()
        print(f'\n===== 최근 기록 {logs[-1].name} (끝 40줄) =====\n' + '\n'.join(lines[-40:]))
    procs = running()
    print('\n[실행 중인 관련 프로세스]', '없음' if not procs else '\n  ' + '\n  '.join(procs))
    print('\n다음: HANDOFF.md의 "미해결"을 확인하고 작업 후 `python tools/ai_session.py end -m "요약"`')


def snapshot_debate():
    src = ROOT / 'debate_tools' / '토론.md'
    if not src.exists():
        return None
    dst = HIST / 'debates' / f'{today()}_토론.md'
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return dst


def end(a):
    log = HIST / f'{today()}.md'
    if not log.exists():
        print(f'경고: 오늘 기록 {log.relative_to(ROOT)} 이 없습니다. 먼저 작성하세요.')
        return 1
    bad = scan(TRACKED)
    if bad:
        print('비밀정보로 보이는 내용이 있어 중단합니다:')
        for f, h in bad:
            print(' ', f, h)
        return 1
    snap = snapshot_debate()
    if snap:
        print('토론 스냅샷:', snap.relative_to(ROOT))
    git('add', '--', *[p for p in TRACKED if (ROOT / p).exists()], check=True)
    if not git('diff', '--cached', '--name-only').stdout.strip():
        print('커밋할 변경이 없습니다.')
        return 0
    msg = a.m or f'AI 이력 갱신 {today()}'
    git('commit', '-m', msg + '\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>', check=True)
    print('커밋:', git('log', '-1', '--format=%h %s').stdout.strip())
    if a.no_push:
        return 0
    r = git('push', 'origin', 'HEAD')
    print('푸시 성공' if r.returncode == 0 else '푸시 실패: ' + r.stderr.strip()[-300:])
    return r.returncode


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('start').set_defaults(fn=start)
    e = sub.add_parser('end')
    e.add_argument('-m')
    e.add_argument('--no-push', action='store_true')
    e.set_defaults(fn=end)
    a = ap.parse_args(argv)
    return a.fn(a) or 0


if __name__ == '__main__':
    sys.exit(main())
