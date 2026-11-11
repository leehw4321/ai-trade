"""한 번에 이 PC로 실거래를 가져온다 (매일 회사↔집 이동용).

  python tools/take_over.py          확인창 한 번 뒤 전부 자동
  python tools/take_over.py --check  무엇을 할지만 출력 (변경 없음)

순서: 최신 코드 받기 → 실행본 갱신 → 운용 설정 맞추기 → 다른 PC 봇에 양도 요청(그 봇은 스스로 주문을 멈추고 반납)
      → 장부가 이어지면 그대로, 아니면 현재 계좌 전체를 새 기준으로 인수 → 봇·그림자 시작 → 현황 열기.
실거래 장부는 PC 밖으로 보내지 않는다. 다른 PC에서 넘어올 때는 계좌 인수라 손절가는 현재가 기준으로 다시 잡힌다.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
APP = REPO.parent / '코인 트레이딩' / 'v5-app' / 'v5'
LOG = APP / 'data' / 'takeover.log'
PY = Path(sys.executable)
PYW = PY.with_name('pythonw.exe') if PY.with_name('pythonw.exe').exists() else PY


def say(msg):
    line = f'[{dt.datetime.now():%m-%d %H:%M:%S}] {msg}'
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open('a', encoding='utf-8') as f:
        f.write(line + '\n')


def box(kind, text):
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
    try:
        return getattr(messagebox, kind)('자동거래 v5 · 이 PC에서 시작', text, parent=root)
    finally:
        root.destroy()


def bot_running():
    lock = APP / 'bot.lock'
    if not lock.exists():
        return False
    pid = lock.read_text().strip()
    return bool(pid) and pid in subprocess.run(['tasklist', '/FI', f'PID eq {pid}'], capture_output=True, text=True,
                                               encoding='mbcs', errors='replace',
                                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout


def key_env():
    """pnl.py는 실행 사본 폴더의 .env만 찾는다. 키가 상위 폴더(.env)에 있어도 읽히도록 환경변수로 넘긴다(값은 기록하지 않는다)."""
    env = dict(os.environ)
    try:
        sys.path.insert(0, str(APP))
        import upbit, trade_history
        trade_history.find_keys(upbit)
        env['UPBIT_ACCESS_KEY'], env['UPBIT_SECRET_KEY'] = upbit._keys()
    except Exception:
        pass
    return env


def run(args, cwd, env=None):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace', env=env,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return r.returncode, (r.stdout + r.stderr).strip()


def update_code():
    code, out = run(['git', 'pull', '--ff-only', '--quiet'], REPO)
    say('최신 코드 받음' if code == 0 else f'코드 받기 건너뜀(이 PC 코드로 진행): {out[-200:]}')
    if not APP.exists():
        APP.mkdir(parents=True)
        for f in (REPO / 'v5').iterdir():
            if f.is_file():
                shutil.copy2(f, APP / f.name)
        say('실행본 새로 만듦')
    else:
        code, out = run([str(PY), 'tools/sync_v5.py', '--apply'], REPO)
        say('실행본 갱신' if code == 0 else f'실행본 갱신 실패: {out[-200:]}')
    for name in ('모의운영 시작.bat', '모의운영 중지.bat'):
        if (REPO / 'v5' / name).exists():
            shutil.copy2(REPO / 'v5' / name, APP / name)


AGREED = REPO / 'tools' / 'agreed_config.json'
PC_ONLY = {('live', 'enabled')}   # PC마다 다른 값(실거래 켜짐 여부)과 session 구역은 비교·덮어쓰기 대상이 아니다


class ConfigMismatch(RuntimeError):
    """이 PC 설정이 합의 설정(agreed_config.json)과 달라 실행권 받기를 멈춘다."""


def agreed():
    d = json.loads(AGREED.read_text(encoding='utf-8'))
    return {k: v for k, v in d.items() if not k.startswith('_')}


def config_diff(cfg=None):
    """[(항목, 이 PC 값, 합의 값), ...]. 합의 파일에 있는 항목만 본다."""
    if cfg is None:
        path = APP / 'config.json'
        if not path.exists():
            return []
        cfg = json.loads(path.read_text(encoding='utf-8-sig'))
    out = []
    for sec, vals in agreed().items():
        if isinstance(vals, dict):
            for k, v in vals.items():
                if (sec, k) not in PC_ONLY and (cfg.get(sec) or {}).get(k) != v:
                    out.append((f'{sec}.{k}', (cfg.get(sec) or {}).get(k), v))
        elif cfg.get(sec) != vals:
            out.append((sec, cfg.get(sec), vals))
    return out


def diff_text(diffs, limit=None):
    rows = [f'{k}: 이 PC {a} / 합의 {b}' for k, a, b in diffs]
    return ' · '.join(rows[:limit] + ([f'외 {len(rows) - limit}개'] if limit and len(rows) > limit else []))


def align_config(enabled=None, accept=False):
    """운용 설정을 합의 설정 하나에 맞춘다. 2026-11-10: 예전에는 tools/patches 조각 37개를 이름순으로 전부 덮어써
    철회된 옛 결정(장세 필터 켜기·4종목 등)이 최신 결정을 이겼다. 이제 조각은 읽지 않는다.
    이 PC 설정이 합의와 다르면 accept=True(사용자가 --accept-config로 확인)일 때만 맞추고, 아니면 아무것도 바꾸지 않고 멈춘다."""
    path = APP / 'config.json'
    fresh = not path.exists()
    cfg = json.loads((REPO / 'v5' / 'config.json' if fresh else path).read_text(encoding='utf-8-sig'))
    diffs = config_diff(cfg)
    if diffs and not (accept or fresh):
        raise ConfigMismatch(f'설정이 합의와 달라 멈춤({len(diffs)}개): {diff_text(diffs, 4)}. '
                             '확인 후 python tools/take_over.py --accept-config 로 합의값에 맞추거나 합의 파일을 고치세요')
    if diffs:
        say(f'설정을 합의값으로 맞춤({len(diffs)}개): {diff_text(diffs)}')
    for sec, vals in agreed().items():
        if isinstance(vals, dict):
            for k, v in vals.items():
                if (sec, k) not in PC_ONLY:
                    cfg.setdefault(sec, {})[k] = v
        else:
            cfg[sec] = vals
    if enabled is not None:
        cfg.setdefault('live', {})['enabled'] = enabled
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)
    return cfg


def lease():
    sys.path.insert(0, str(APP))
    from session_guard import GitStore
    store = GitStore(APP / 'data' / 'session_git')
    return store.read()[1]


def local_hash():
    sys.path.insert(0, str(APP))
    from session_guard import SessionGuard
    return SessionGuard(APP).checkpoint_hash()


def request_handover():
    """다른 PC 봇에 양도를 요청하고 반납을 기다린 뒤, 비운 상태로 둔다."""
    sys.path.insert(0, str(APP))
    from session_guard import SessionGuard
    g = SessionGuard(APP, {'live_enabled': False})
    g.acquire(confirm=lambda: True)  # 확인은 이 도구가 먼저 받았다
    g.release()


def relay():
    sys.path.insert(0, str(APP))
    import ledger_relay
    from session_guard import GitStore
    return ledger_relay, ledger_relay.Relay(APP, GitStore(APP / 'data' / 'session_git'), workspace=APP.parents[1], repo=REPO)


def receive_ledger(checkpoint):
    """넘겨준 PC가 반납 때 올린 암호화 장부를 받아 이어 쓴다. 실행권 기록 체크섬과 정확히 같을 때만. 실패하면 False(계좌 새 인수로 진행)."""
    try:
        lr, r = relay()
        bundle = r.fetch(checkpoint)
        keep = lr.install(APP, bundle)
    except Exception as ex:
        say(f'장부 이어받기 불가 → 계좌 새 인수로 진행: {ex}'[:300])
        return False
    say(f'장부 이어받기: {bundle.get("pc")} {bundle.get("time")} 반납분 (체크섬 일치)' + (f' · 이 PC 이전 장부 보관 {keep.name}' if keep else ''))
    return True


def enroll():
    """이 PC를 장부 전달 수신 PC로 등록(한 번). 개인키는 이 PC private_handoff에만, 공개 인증서만 ledger-relay 브랜치에 올린다."""
    lr, r = relay()
    ids = r.identities()
    if not ids:
        sys.path.insert(0, str(REPO / 'tools'))
        import handoff_cli
        name = 'pc-' + ''.join(c for c in os.environ.get('COMPUTERNAME', 'pc') if c.isalnum() or c in '-_')[:30]
        handoff_cli.init_recipient(APP.parents[1] / 'private_handoff', name)
        ids = r.identities()
        say(f'수신 인증서 새로 만듦: private_handoff/identities/{name} (개인키는 이 PC에만)')
    where = r.enroll(ids[0] / 'public-certificate.pem')
    say(f'장부 전달 수신 등록 완료: ledger-relay {where}')


def adopt():
    live = APP / 'data' / 'live'
    if (live / 'live.json').exists():
        keep = APP / 'data' / f"live_이전장부_{dt.datetime.now():%Y%m%d_%H%M%S}"
        live.rename(keep)
        say(f'이 PC의 이전 장부 보관: {keep.name}')
    align_config(enabled=False, accept=True)  # 바로 앞 단계에서 합의 일치를 확인했다. 인수 도구는 꺼진 상태에서만 새 기준을 만든다
    code, out = run([str(PY), 'adopt_account.py', '--user-authorized-all-holdings'], APP)
    if code:
        raise RuntimeError('계좌 인수 실패: ' + out[-300:])
    say('계좌 인수 완료: ' + out.splitlines()[0][:200])


def apply_protection_principle():
    """사용자 보호 원칙(보유 중·직접 거래·5,000원 초과만)대로 보호 목록을 맞춘다. 실패하면 봇을 시작하지 않는다."""
    code, out = run([str(PY), 'pnl.py'], APP, key_env())  # 체결 기록 최신화(읽기 전용)
    if code:
        raise RuntimeError('체결 기록 갱신 실패: ' + out[-300:])
    code, out = run([str(PY), str(REPO / 'tools' / 'protection_principle.py'), '--app', str(APP), '--apply'], REPO, key_env())
    if code:
        raise RuntimeError('보호 원칙 적용 실패: ' + out[-300:])
    say('보호 목록: ' + out.strip().splitlines()[-1])


def start_bot():
    for name in ('stop.txt', 'stop_shadow.txt', '중지.txt'):
        (APP / name).unlink(missing_ok=True)
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    for args in (['bot.py'], ['shadow.py'], ['account_sync.py'], ['exit_study.py'], ['pnl.py', '--loop', '300']):
        if (APP / args[0]).exists():
            subprocess.Popen([str(PYW)] + args, cwd=APP, creationflags=flags, close_fds=True)
    for _ in range(90):
        if bot_running():
            return True
        time.sleep(1)
    return False


def set_grant(state, message):
    """화면 '이 PC에 실행권 받기' 버튼의 진행·결과 표시(봇 화면이 읽는다)."""
    try:
        (APP / 'data').mkdir(parents=True, exist_ok=True)
        (APP / 'data' / 'grant_status.json').write_text(
            json.dumps({'state': state, 'time': dt.datetime.now().isoformat(timespec='seconds'), 'message': message}, ensure_ascii=False),
            encoding='utf-8')
    except OSError:
        pass


def stop_local_bot(wait=90):
    """이 PC에서 대기 중인 봇을 정상 종료한다(강제 종료 없음). 계좌 인수는 봇이 꺼진 상태에서만 한다."""
    (APP / 'stop.txt').write_text('grant button\n', encoding='utf-8')
    for _ in range(wait):
        if not bot_running():
            return
        time.sleep(1)
    (APP / 'stop.txt').unlink(missing_ok=True)
    raise RuntimeError('이 PC 봇이 정상 종료되지 않았습니다(진행 중인 작업이 있을 수 있음). 잠시 뒤 다시 누르세요.')


def shortcut():
    link = Path.home() / 'Desktop' / '자동거래 이 PC에서 시작.lnk'
    if link.exists():
        return
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
          f"$s.TargetPath='{PYW}';$s.Arguments='\"{REPO / 'tools' / 'take_over.py'}\"';"
          f"$s.WorkingDirectory='{REPO}';$s.IconLocation='C:\\Windows\\System32\\shell32.dll,137';$s.Save()")
    subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--button', action='store_true', help='봇 화면의 실행권 버튼에서 호출(확인창 없음, 결과를 화면에 표시)')
    ap.add_argument('--accept-config', action='store_true', help='이 PC 설정이 합의(agreed_config.json)와 달라도 합의값으로 맞추고 진행')
    ap.add_argument('--config-diff', action='store_true', help='이 PC 설정과 합의 설정의 차이만 출력')
    ap.add_argument('--enroll', action='store_true', help='이 PC를 장부 전달 수신 PC로 등록(PC마다 한 번)')
    a = ap.parse_args()
    if a.enroll:
        enroll()
        return
    if a.config_diff:
        d = config_diff()
        print('\n'.join(f'{k}: 이 PC {x} / 합의 {y}' for k, x, y in d) or '차이 없음')
        return
    if a.button and not a.accept_config:
        d = config_diff()
        if d:  # 이 PC 봇을 멈추기도 전에 막는다(설정 차이로 실행권 이동 중단)
            say(f'중단: 설정이 합의와 다름({len(d)}개): {diff_text(d)}')
            set_grant('failed', f'설정이 합의와 달라 멈췄습니다({len(d)}개): {diff_text(d, 3)}'[:160])
            return
    if a.button:
        try:
            set_grant('running', '이 PC 봇을 정상 종료하는 중')
            stop_local_bot()
        except Exception as ex:
            say(f'중단: {type(ex).__name__}: {ex}')
            set_grant('failed', str(ex)[:160])
            for name in ('stop.txt',):
                (APP / name).unlink(missing_ok=True)
            return
    elif bot_running():
        say('이 PC 봇이 이미 실행 중')
        if not a.check:
            os.startfile(APP / 'data' / 'status.html')
        return
    st = lease()
    other = bool(st.get('owner')) and time.time() < st.get('expires', 0) + 30
    same = bool(st.get('checkpoint')) and st.get('checkpoint') == local_hash()
    plan = ['최신 코드 받기·실행본 갱신', '운용 설정이 합의(agreed_config.json)와 같은지 확인(다르면 중단)',
            '다른 PC 봇에 양도 요청' if other else '다른 PC 실행 없음',
            '이 PC 장부 그대로 이어감' if same else '넘겨준 PC 장부 이어받기(암호화 전달·체크섬 일치 시), 안 되면 현재 계좌 전체를 새 기준으로 인수',
            '보호 목록을 사용자 원칙대로 맞춤(보유 중·직접 거래·5,000원 초과만)', '봇·그림자·계좌 조회 시작']
    if a.check:
        print('\n'.join(f'{i}. {x}' for i, x in enumerate(plan, 1)))
        return
    shortcut()
    msg = ('다른 PC에서 실거래 중입니다.\n이 PC로 가져올까요? 그 PC 봇은 주문을 멈춥니다.\n\n' if other else
           '이 PC에서 실거래를 시작할까요?\n\n') + '\n'.join(f'· {x}' for x in plan)
    if not a.button and not box('askyesno', msg):
        say('사용자가 취소')
        return
    try:
        if a.button:
            set_grant('running', '코드·설정 맞추는 중')
        update_code()
        align_config(accept=a.accept_config)   # 다른 PC를 건드리기 전에: 합의와 다르면 여기서 멈춘다
        if other:
            say('양도 요청 · 다른 PC 봇의 반납을 기다리는 중 (최대 4분)')
            if a.button:
                set_grant('running', '다른 PC 봇의 반납을 기다리는 중(최대 4분)')
            request_handover()
            say('다른 PC 봇 반납 확인')
        elif st.get('owner') or st.get('request'):
            say('만료된 실행권 정리 (다른 PC가 응답 없이 꺼진 경우)')
            request_handover()
        if other or st.get('owner') or st.get('request'):
            st = lease()  # 반납한 PC가 방금 기록한 체크섬으로 다시 판단한다
            same = bool(st.get('checkpoint')) and st.get('checkpoint') == local_hash()
        if not same and st.get('checkpoint'):
            if a.button:
                set_grant('running', '넘겨준 PC 장부 받는 중')
            same = receive_ledger(st['checkpoint'])
        if same:
            align_config(enabled=True, accept=True)
        else:
            adopt()
        apply_protection_principle()
        ok = start_bot()
        say('봇 시작' if ok else '봇 시작 확인 실패')
        if a.button:
            set_grant('done' if ok else 'failed', '이 PC에서 실거래를 시작했습니다.' if ok else f'봇이 시작되지 않았습니다. 기록: {LOG.name}')
            return
        os.startfile(APP / 'data' / 'status.html')
        box('showinfo' if ok else 'showwarning',
            '이 PC에서 실거래를 시작했습니다.' if ok else f'봇이 시작되지 않았습니다. 기록: {LOG}')
    except Exception as ex:
        say(f'중단: {type(ex).__name__}: {ex}')
        if a.button:
            set_grant('failed', f'{type(ex).__name__}: {str(ex)[:150]}')
            try:  # 화면(램프·버튼)이 다시 보이도록 봇을 켠다. 계좌 인수 단계에서 실거래는 꺼져 있으므로 이 봇은 거래하지 않는다.
                start_bot()
            except Exception as ex2:
                say(f'봇 다시 켜기 실패: {ex2}')
            return
        box('showerror', f'중단했습니다: {ex}\n\n기록: {LOG}')


if __name__ == '__main__':
    main()
