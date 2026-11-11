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
                        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
                        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
                        "ForEach-Object { $_.ProcessId.ToString() + ' ' + $_.CommandLine }"],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode:
        return ['조회 실패: 실행 여부를 확인할 수 없음']
    return [l.strip() for l in r.stdout.splitlines() if re.search(r'토론진행기|bot\.py|shadow\.py|account_sync|run\.py|launch_', l)]


def v5_dirs():
    cands = [ROOT / 'v5', ROOT.parent / '코인 트레이딩' / 'v5-app' / 'v5',
             ROOT.parent / '자동거래 v5', Path.home() / 'Desktop' / '코인 트레이딩' / 'v5-app' / 'v5']
    if os.environ.get('V5_APP_DIR'):
        cands.append(Path(os.environ['V5_APP_DIR']))
    found, seen = [], set()
    for c in cands:
        key = os.path.normcase(str(c.resolve()))
        if key not in seen and (c / 'config.json').exists():
            found.append(c); seen.add(key)
    return found


def status(_):
    """지금 이 PC의 실제 운용 상태. 상태를 말하기 전에 항상 이 결과로 확인한다. 금액·키는 출력하지 않는다."""
    import json
    print(f'== 운용 상태 {dt.datetime.now(KST):%Y-%m-%d %H:%M:%S} KST / PC {os.environ.get("COMPUTERNAME", "?")} ==')
    for d in v5_dirs():
        cfg = json.loads((d / 'config.json').read_text(encoding='utf-8'))
        live = cfg.get('live', {})
        print(f'\n[{d}]')
        print(f'  실거래 live.enabled={live.get("enabled")} · auto_proposal={live.get("auto_proposal")} · 한도 1건 {live.get("max_order_krw")} / 누적 {live.get("max_total_buy_krw")} / 1회 매도 {live.get("max_sell_krw_per_plan")} / 손실 중단 {live.get("loss_stop_krw")}')
        lj = d / 'data' / 'live' / 'live.json'
        if lj.exists():
            x = json.loads(lj.read_text(encoding='utf-8'))
            print(f'  실거래 장부: 관리 종목 {len(x.get("positions", {}))}개 · 체결 주문 {len(x.get("executed") or {})}건 · 중단 사유 {x.get("halted") or "없음"} · 갱신 {dt.datetime.fromtimestamp(lj.stat().st_mtime, KST):%H:%M:%S}')
        else:
            print('  실거래 장부(data/live/live.json) 없음')
        ar = d / 'data' / 'adoption-result.json'
        if ar.exists():
            a = json.loads(ar.read_text(encoding='utf-8'))
            print(f'  계좌 인수: {a.get("adopted_at")} · 범위 {a.get("scope")} · 이력 복원 {a.get("past_history_restored")}')
        try:  # 2026-11-11: 봇이 켜져 있어도 실행권이 없으면 주문(손절 포함)을 못 한다. 반드시 함께 보여 준다.
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import lease_check
            level, msg = lease_check.report(d, live.get('enabled', False), (d / 'bot.lock').exists())
            print('  ' + ('[문제] ' if level == 'bad' else '') + msg)
        except Exception as ex:
            print(f'  실행권 확인 불가({type(ex).__name__})')
        for f in ('bot.stdout.log',):
            lf = d / 'data' / f
            if lf.exists():
                print('  봇 최근 로그:', lf.read_text(encoding='utf-8', errors='replace').strip().splitlines()[-1][:160])
    dirs = v5_dirs()
    if len(dirs) >= 2:
        import hashlib
        h = lambda f: hashlib.sha256(f.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
        for app in dirs[1:]:
            source = {f.name: f for f in dirs[0].glob('*.py')}
            target = {f.name: f for f in app.glob('*.py')}
            diff = [n for n in sorted(source.keys() & target.keys()) if h(source[n]) != h(target[n])]
            missing = sorted(source.keys() - target.keys())
            extra = sorted(target.keys() - source.keys())
            print(f'\n[코드 어긋남: {app}]', '없음(정본과 동일)' if not diff and not missing and not extra
                  else f'다름 {diff} / 실행 후보에 없음 {missing} / 실행 후보에만 있음 {extra} -> 배포 전 확인')
    else:
        print(f'\n[코드 대조 불가] 설정이 있는 후보 {len(dirs)}개. 실행 사본이 없거나 경로를 찾지 못했습니다. V5_APP_DIR을 확인하세요.')
    procs = running()
    print('\n[실행 중인 프로세스]', '없음 (봇 꺼짐)' if not procs else '\n  ' + '\n  '.join(procs))
    print('\n주의: 같은 PC의 다른 AI 세션이 설정을 바꿀 수 있다. 상태를 단정하기 전에 이 명령을 다시 실행한다.')


LATEST = HIST / 'LATEST.md'


def start(a):
    print(f'== PC {os.environ.get("COMPUTERNAME", "?")} / {dt.datetime.now(KST):%Y-%m-%d %H:%M} KST ==')
    r = git('pull', '--ff-only')
    print('[git pull]', (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr).strip() else 'ok')
    dirty = git('status', '--short').stdout.strip()
    print('[미커밋 변경]', '없음' if not dirty else '\n' + dirty)
    if LATEST.exists() and not getattr(a, 'full', False):
        # 한 장 요약만 보여 주고 긴 문서는 목록(크기)만 알린다. 전부 보려면 `start --full`.
        print('\n===== LATEST.md (한 장 인계, 가장 먼저 읽는다) =====\n' + LATEST.read_text(encoding='utf-8'))
        print('\n===== 더 읽을 곳(필요할 때만; 지금은 읽지 않아도 된다) =====')
        for rel in ('HANDOFF.md', 'docs/ai-history/집에서_이어서_2026-11-03.md', 'docs/ai-history/permissions.md',
                    'docs/ai-history/decision-log.md', 'docs/ai-history/2026-11-03-claude-verification.md'):
            f = ROOT / rel
            if f.exists():
                print(f'  {rel} ({len(f.read_text(encoding="utf-8", errors="replace").splitlines())}줄)')
        procs = running()
        print('\n[실행 중인 관련 프로세스]', '없음' if not procs else '\n  ' + '\n  '.join(procs))
        print('\n상태를 말하기 전에 `python tools/ai_session.py status`. 끝낼 때: LATEST.md 갱신 → '
              '`python tools/handoff_digest.py check` → `python tools/ai_session.py end -m "요약"`')
        return
    home = HIST / '집에서_이어서_2026-11-03.md'
    if home.exists():
        print('\n===== 최신 집 PC 인계 (역사 기록보다 우선) =====\n' + home.read_text(encoding='utf-8'))
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
    # Authorship/review evidence belongs in the handoff; never fabricate a co-author.
    git('commit', '-m', msg, check=True)
    print('커밋:', git('log', '-1', '--format=%h %s').stdout.strip())
    if a.no_push:
        return 0
    r = git('push', 'origin', 'HEAD')
    print('푸시 성공' if r.returncode == 0 else '푸시 실패: ' + r.stderr.strip()[-300:])
    return r.returncode


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('start')
    s.add_argument('--full', action='store_true', help='LATEST.md 대신 긴 인계 문서 전체를 출력')
    s.set_defaults(fn=start)
    sub.add_parser('status').set_defaults(fn=status)
    e = sub.add_parser('end')
    e.add_argument('-m')
    e.add_argument('--no-push', action='store_true')
    e.set_defaults(fn=end)
    a = ap.parse_args(argv)
    return a.fn(a) or 0


if __name__ == '__main__':
    # Windows' legacy console encoding must not abort an otherwise read-only status check.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
