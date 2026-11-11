"""실거래 실행권(GitHub 세션 기록)과 이 PC 장부를 대조해 한 줄로 알려 준다. 읽기 전용(실행권을 받거나 바꾸지 않는다).

2026-11-11 사고: 봇을 멈춘 사이 live.json을 고쳐 장부 체크섬이 실행권 기록과 달라졌고, 재시작한 봇이 실행권을 받지 못한 채
'중단 사유 없음'으로 보이며 4시간 넘게 주문 없이 대기했다(손절도 못 함). 상태 확인·배포 점검에서 이 상태를 바로 드러낸다.

  python tools/lease_check.py [--app <실행 폴더>]
"""
import argparse
import hashlib
import json
import socket
import sys
import time
from pathlib import Path

DEFAULT_APP = Path(__file__).resolve().parents[1].parent / '코인 트레이딩' / 'v5-app' / 'v5'


def ledger_hash(app):
    f = Path(app) / 'data' / 'live' / 'live.json'
    if not f.exists():
        return ''
    state = json.loads(f.read_text(encoding='utf-8'))
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_lease(app):
    app = Path(app)
    sys.path.insert(0, str(app))
    try:
        from session_guard import GitStore
        return GitStore(app / 'data' / 'session_git').read()[1]
    finally:
        sys.path.remove(str(app))


def judge(state, live_enabled, bot_running, local_hash, pc=None, now=None):
    """(심각도, 문장). 심각도: ok | warn | bad."""
    pc = pc or socket.gethostname()
    now = time.time() if now is None else now
    owner, holder = state.get('owner') or '', state.get('pc') or '?'
    alive = bool(owner) and now < state.get('expires', 0)
    if alive and holder == pc:
        return 'ok', f'실행권: 이 PC({pc}) 보유 · 만료까지 {int(state["expires"] - now)}초'
    if alive:
        return 'ok', f'실행권: 다른 PC({holder}) 보유' + (' · 이 PC 봇은 지켜보기만 함' if bot_running else '')
    msg = '실행권: 아무 PC도 갖고 있지 않음' + (f'(마지막 {holder})' if holder != '?' else '')
    cp = state.get('checkpoint') or ''
    if cp and local_hash and cp != local_hash:
        msg += ' · 이 PC 장부 체크섬이 실행권 기록과 다름 → 봇을 다시 켜도 실행권을 받지 못함'
    if live_enabled and bot_running:
        return 'bad', msg + ' · 경고: 이 PC 봇은 켜져 있지만 주문(손절 포함)을 하지 못함'
    return 'warn', msg


def report(app, live_enabled, bot_running):
    if not (Path(app) / 'session_guard.py').exists():
        return 'warn', '실행권 확인 불가(session_guard.py 없음)'
    try:
        state = read_lease(app)
    except Exception as ex:
        return 'warn', f'실행권 확인 불가({type(ex).__name__})'
    return judge(state, live_enabled, bot_running, ledger_hash(app))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', default=str(DEFAULT_APP))
    a = ap.parse_args()
    cfg = json.loads((Path(a.app) / 'config.json').read_text(encoding='utf-8-sig'))
    lock = Path(a.app) / 'bot.lock'
    level, msg = report(a.app, cfg.get('live', {}).get('enabled', False), lock.exists())
    print(('[문제] ' if level == 'bad' else '') + msg)
    return 1 if level == 'bad' else 0


if __name__ == '__main__':
    sys.exit(main())
