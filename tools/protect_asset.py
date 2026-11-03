"""Persist user-selected assets excluded from bot orders. No exchange access."""
import argparse
import datetime as dt
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'v5'))
from live import normalize_protected_market


def update(app, action, symbols):
    file = app / 'data/user_protected_assets.json'
    file.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(file.read_text(encoding='utf-8')) if file.exists() else {'markets': []}
    if not isinstance(data, dict) or not isinstance(data.get('markets'), list):
        raise ValueError('보호 목록 형식 무효: 기존 보호를 덮어쓰지 않습니다')
    markets = {normalize_protected_market(x) for x in data['markets']}
    selected = {normalize_protected_market(x) for x in symbols}
    if action == 'protect': markets |= selected
    elif action == 'unprotect': markets -= selected
    elif action != 'list': raise ValueError('unknown action')
    if action != 'list':
        data.update(markets=sorted(markets), updated_at=dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec='seconds'),
                    policy='user_managed_no_bot_orders')
        temp = file.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(file)
    return sorted(markets)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('action', choices=['protect', 'unprotect', 'list'])
    parser.add_argument('symbols', nargs='*')
    args = parser.parse_args()
    print('사용자 보호 종목:', ', '.join(update(args.app, args.action, args.symbols)) or '없음')
