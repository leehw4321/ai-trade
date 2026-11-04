"""사용자 보호 원칙을 거래소 체결 기록으로 판정해 보호 목록을 맞춘다. 주문을 내지 않는다(GET만).

사용자 원칙(2026-11-04): "모든 보호 종목은 보유 종목 중에서 내가 직접 거래한 종목, 소액 제외이다."
  보호 = 현재 보유 + 사용자가 직접 거래 + 그 직접 보유분 평가액 5,000원 초과. 봇이 산 종목·봇 잔량·자투리·미보유는 보호하지 않는다.

직접 보유 수량 = min(현재 잔고, 기초 보유 수량 + 기초 이후 직접 주문(식별자가 v5-가 아닌 주문)의 순매수 수량)
  기초 보유(v5/pnl_baseline.json, 봇 운용 전 보유)는 사용자가 직접 산 것으로 본다.
  체결 기록은 pnl.py가 거래소에서 받아 둔 data/pnl/orders.json을 쓴다(없으면 먼저 pnl.py를 실행).

  python tools/protection_principle.py --app <실행폴더>          판정만 출력
  python tools/protection_principle.py --app <실행폴더> --apply  보호 목록을 판정대로 맞춤
"""
import argparse
from decimal import Decimal as D
import json
from pathlib import Path
import sys

MIN_VALUE = D(5000)


def direct_quantities(baseline, orders):
    """종목별 직접 보유 수량(기초 + 직접 주문 순매수)."""
    qty = {'KRW-' + c: D(str(h['qty'])) for c, h in baseline['holdings'].items()}
    for o in orders.values():
        if (o.get('identifier') or '').startswith('v5-'):
            continue
        v = D(str(o.get('executed_volume') or 0))
        if v <= 0:
            continue
        m = o['market']
        qty[m] = qty.get(m, D(0)) + (v if o['side'] == 'bid' else -v)
    return qty


def judge(baseline, orders, balances, prices):
    """반환: (보호할 종목 집합, 종목별 판단 근거 목록)."""
    direct = direct_quantities(baseline, orders)
    keep, rows = set(), []
    for m in sorted(set(balances) | set(direct)):
        held = balances.get(m, D(0))
        if held <= 0:
            continue
        price = prices.get(m)
        if price is None:
            rows.append((m, '시세 없음(거래지원 없음 등): 보호 안 함'))
            continue
        mine = min(held, max(direct.get(m, D(0)), D(0)))
        value, mine_value = held * D(str(price)), mine * D(str(price))
        if mine_value > MIN_VALUE:
            keep.add(m)
            rows.append((m, f'보호: 직접 보유 {mine_value:,.0f}원 / 전체 {value:,.0f}원'))
        elif value <= MIN_VALUE:
            rows.append((m, f'보호 안 함: 소액 {value:,.0f}원'))
        else:
            rows.append((m, f'보호 안 함: 봇 보유(직접 보유 {mine_value:,.0f}원) / 전체 {value:,.0f}원'))
    return keep, rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', type=Path, required=True)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args(argv)
    sys.path.insert(0, str(a.app))
    import upbit
    from asset_protection import Policy, effective
    baseline = json.loads((a.app / 'pnl_baseline.json').read_text(encoding='utf-8'))
    of = a.app / 'data' / 'pnl' / 'orders.json'
    if not of.exists():
        sys.exit('체결 기록이 없습니다. 먼저 pnl.py를 실행하세요.')
    orders = json.loads(of.read_text(encoding='utf-8'))['orders']
    rows = upbit.accounts()
    balances = {'KRW-' + r['currency']: D(str(r['balance'])) + D(str(r['locked'])) for r in rows if r['currency'] != 'KRW'}
    listed = upbit.krw_markets()
    prices = {m: t['trade_price'] for m, t in upbit.tickers([m for m in balances if m in listed]).items()}
    keep, why = judge(baseline, orders, balances, prices)
    for m, w in why:
        print(f'{m:12} {w}')
    cfg = json.loads((a.app / 'config.json').read_text(encoding='utf-8-sig'))
    policy = Policy(a.app / 'data' / 'user_protected_assets.json', cfg.get('live', {}).get('protected_markets', []), required=False)
    current = set(effective(policy.read(), policy.configured))
    print('현재 보호:', sorted(current), '→ 원칙 판정:', sorted(keep))
    if not a.apply:
        return keep
    for m in sorted(current - keep):
        policy.decide(m, False, origin='cli', expected=policy.revision(m))
    for m in sorted(keep - current):
        policy.decide(m, True, origin='cli', expected=policy.revision(m))
    after = set(effective(policy.read(), policy.configured))
    print('적용 후 보호:', sorted(after))
    if after != keep:
        sys.exit('설정 고정 보호 목록(config protected_markets) 때문에 판정과 다릅니다. 확인하세요.')
    return keep


if __name__ == '__main__':
    main()
