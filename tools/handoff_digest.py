"""한 장 인계(LATEST.md) 관리: 검증·보관·변경 목록.

긴 날짜별 기록은 그대로 보존하고, 다음 세션이 처음 읽는 것은 LATEST.md 한 장뿐이게 한다.
LATEST.md는 매 세션 끝에 새로 쓰는 요약본이며, 덮어쓰기 전 이전 판은 latest-archive/에 날짜·시각 이름으로 보관한다(기록은 지우지 않는다).

  python tools/handoff_digest.py check              # 구조·길이·비밀정보 검사 (실패하면 종료 코드 1)
  python tools/handoff_digest.py archive            # 현재 LATEST.md를 보관본으로 복사
  python tools/handoff_digest.py changes [--since 커밋]   # '바뀐 것' 작성용 커밋 제목 목록
"""
import argparse
import datetime as dt
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / 'docs' / 'ai-history'
LATEST = HIST / 'LATEST.md'
ARCHIVE = HIST / 'latest-archive'
MAX_LINES = 70
MAX_CHARS = 6000
REQUIRED = ('## 1. 지금 상태', '## 2. 바뀐 것', '## 3. 하지 말 것', '## 4. 다음 할 일', '## 5. 더 읽을 곳')
SECRET = re.compile(r'(?i)(access[_-]?key|secret[_-]?key|api[_-]?key|token)\s*[:=]\s*[A-Za-z0-9_\-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----')


def problems(text):
    out = []
    lines = text.splitlines()
    if len(lines) > MAX_LINES:
        out.append(f'너무 김: {len(lines)}줄 (최대 {MAX_LINES}줄). 한 장으로 줄이고 세부는 링크로 보낸다.')
    if len(text) > MAX_CHARS:
        out.append(f'너무 김: {len(text)}자 (최대 {MAX_CHARS}자).')
    for h in REQUIRED:
        if h not in text:
            out.append(f'필수 절 누락: {h}')
    if SECRET.search(text):
        out.append('비밀정보로 보이는 문자열이 있다.')
    if not re.search(r'작성\s*:\s*\S+.*\d{4}-\d{2}-\d{2}', text):
        out.append('첫머리에 "작성: 누가 · 날짜 시각" 줄이 없다.')
    return out


def check(path=LATEST):
    if not path.exists():
        print(f'없음: {path}')
        return 1
    found = problems(path.read_text(encoding='utf-8'))
    for p in found:
        print('문제:', p)
    if not found:
        n = len(path.read_text(encoding='utf-8').splitlines())
        print(f'LATEST.md 통과 ({n}줄)')
    return 1 if found else 0


def archive(path=LATEST, folder=ARCHIVE, now=None):
    if not path.exists():
        print('보관할 LATEST.md가 없다.')
        return 0
    now = now or dt.datetime.now()
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / f'LATEST_{now:%Y-%m-%d_%H%M%S}.md'
    shutil.copyfile(path, dst)
    print('보관:', dst.relative_to(ROOT) if ROOT in dst.parents else dst)
    return 0


def changes(since=None):
    rng = f'{since}..HEAD' if since else '-30'
    cmd = ['git', 'log', '--no-merges', '--format=%h %ad %s', '--date=format:%m-%d %H:%M'] + ([rng] if since else [rng])
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(r.stdout.strip() or '(변경 없음)')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('check')
    sub.add_parser('archive')
    c = sub.add_parser('changes')
    c.add_argument('--since')
    a = ap.parse_args(argv)
    return {'check': lambda: check(), 'archive': lambda: archive(), 'changes': lambda: changes(a.since)}[a.cmd]()


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
