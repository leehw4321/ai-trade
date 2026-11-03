"""Persist user-selected assets excluded from bot orders. No exchange access."""
import argparse
import datetime as dt
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'v5'))
from asset_protection import Policy, effective, normalize as normalize_protected_market


def update(app, action, symbols):
    file = app / 'data/user_protected_assets.json'
    cfgfile=app/'config.json'
    cfg=json.loads(cfgfile.read_text(encoding='utf-8-sig')) if cfgfile.exists() else {}
    store=Policy(file,cfg.get('live',{}).get('protected_markets',[]),required=False)
    if action not in ('protect','unprotect','list'): raise ValueError('unknown action')
    if action!='list':
        for symbol in symbols:
            market=normalize_protected_market(symbol)
            store.decide(market,action=='protect',origin='cli',expected=store.revision(market))
    return sorted(effective(store.read(),store.configured))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('action', choices=['protect', 'unprotect', 'list'])
    parser.add_argument('symbols', nargs='*')
    args = parser.parse_args()
    print('사용자 보호 종목:', ', '.join(update(args.app, args.action, args.symbols)) or '없음')
