"""PC를 옮길 때 쓰는 폴더 단위 올리기/받기. 병합을 하지 않는다(받기는 깃 최신 상태로 덮어쓴다).

  python tools/handoff_sync.py push    지금 폴더 상태를 모두 올린다(.gitignore 대상 제외). 상대 PC가 먼저 바뀌었으면 멈춘다.
  python tools/handoff_sync.py pull    깃 최신 상태로 덮어쓴다. 이 PC에 올리지 않은 변경이 있으면 멈춘다.
  python tools/handoff_sync.py status  두 저장소의 올림/받음 상태만 본다.
  옵션 --force                         멈추는 경우에도 진행(push: 상대 변경을 무시하고 덮어씀, pull: 이 PC의 미올림 변경을 버림)

대상 저장소: ai-trade(이 도구가 있는 곳)와 같은 바탕화면의 '코인 트레이딩'(있을 때). 거래 장부·키·data/·연구 데이터는 깃 밖이라 옮기지 않는다.
올리기 전에 비밀·장부 파일이 섞였는지, 너무 큰 파일이 있는지 검사하고 있으면 올리지 않는다.
"""
import datetime as dt
import re
import socket
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPOS = [HERE, HERE.parent / '코인 트레이딩']
FORBIDDEN = re.compile(r'(^|/)(\.env($|\.(?!example))|data/|[^/]*\.lock$|settings\.json$|[^/]*\.pem$|[^/]*\.key$|live\.json$|account_[^/]*\.json$|private_handoff/|v5-app/)')
MAX_MB = 20


def git(repo, *args, check=True):
    r = subprocess.run(['git', '-c', 'core.quotepath=off', '-C', str(repo), *args], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)} 실패: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def state(repo):
    """(브랜치, 올리지 않은 커밋 수, 받지 않은 커밋 수, 변경 파일 목록). fetch는 호출 쪽이 한다."""
    branch = git(repo, 'rev-parse', '--abbrev-ref', 'HEAD')
    ahead, behind = (int(x) for x in git(repo, 'rev-list', '--left-right', '--count', f'HEAD...origin/{branch}').split())
    dirty = [l for l in git(repo, 'status', '--porcelain').splitlines() if l.strip()]
    return branch, ahead, behind, dirty


def bad_paths(repo, paths):
    """올리면 안 되는 이름이거나 너무 큰 파일."""
    out = []
    for p in paths:
        p = p.strip('"')
        f = Path(repo) / p
        if FORBIDDEN.search(p.replace('\\', '/')):
            out.append(f'{p} (비밀·장부·실행 파일 이름)')
        elif f.is_file() and f.stat().st_size > MAX_MB * 1024 * 1024:
            out.append(f'{p} ({f.stat().st_size / 1e6:.0f}MB 초과)')
    return out


def push(repo, force=False):
    git(repo, 'fetch', '-q')
    branch, ahead, behind, dirty = state(repo)
    if behind and not force:
        return False, f'{repo.name}: 상대가 먼저 올린 변경 {behind}건이 있어 멈춤. 먼저 받기(pull)를 하거나 --force로 덮어쓰세요.'
    git(repo, 'add', '-A')
    staged = git(repo, 'diff', '--cached', '--name-only').splitlines()
    bad = bad_paths(repo, staged)
    if bad:
        git(repo, 'reset', '-q')
        return False, f'{repo.name}: 올리면 안 되는 파일이 섞여 있어 멈춤 → ' + ' / '.join(bad[:6])
    if staged:
        git(repo, 'commit', '-q', '-m', f'동기화 {socket.gethostname()} {dt.datetime.now():%m-%d %H:%M}\n\n'
            'Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>')
    if behind and force:
        git(repo, 'push', '-q', '--force-with-lease', 'origin', branch)
    elif staged or ahead:
        git(repo, 'push', '-q', 'origin', branch)
    else:
        return True, f'{repo.name}: 올릴 변경 없음(이미 최신)'
    return True, f'{repo.name}: 올림 · 파일 {len(staged)}개 변경 · 커밋 {git(repo, "rev-parse", "--short", "HEAD")}'


def pull(repo, force=False):
    git(repo, 'fetch', '-q')
    branch, ahead, behind, dirty = state(repo)
    if (dirty or ahead) and not force:
        what = ([f'올리지 않은 변경 {len(dirty)}개'] if dirty else []) + ([f'올리지 않은 커밋 {ahead}건'] if ahead else [])
        return False, f'{repo.name}: {" · ".join(what)}가 있어 멈춤. 먼저 올리기(push)를 하거나 --force로 버리세요.'
    if not behind and not (force and (dirty or ahead)):
        return True, f'{repo.name}: 이미 최신'
    git(repo, 'reset', '--hard', f'origin/{branch}')
    return True, f'{repo.name}: 받음(덮어쓰기) · 새 커밋 {behind}건 · 커밋 {git(repo, "rev-parse", "--short", "HEAD")}'


def status(repo, force=False):
    git(repo, 'fetch', '-q')
    branch, ahead, behind, dirty = state(repo)
    return True, f'{repo.name}: 올리지 않은 변경 {len(dirty)}개 · 올리지 않은 커밋 {ahead} · 받지 않은 커밋 {behind}'


def main(argv):
    if len(argv) < 2 or argv[1] not in ('push', 'pull', 'status'):
        print(__doc__)
        return 2
    fn, force, ok_all = {'push': push, 'pull': pull, 'status': status}[argv[1]], '--force' in argv, True
    for repo in REPOS:
        if not (repo / '.git').exists():
            print(f'{repo.name}: 저장소 없음(건너뜀)')
            continue
        try:
            ok, msg = fn(repo, force)
        except RuntimeError as e:
            ok, msg = False, f'{repo.name}: {e}'
        ok_all &= ok
        print(('[OK] ' if ok else '[멈춤] ') + msg)
    if argv[1] == 'pull' and ok_all:
        print('\n받은 뒤: 봇 코드가 바뀌었으면 python tools/deploy_v5.py 로 점검하세요(자동 배포는 하지 않습니다).')
    return 0 if ok_all else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
