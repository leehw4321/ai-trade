"""놓친 이유 장부(2026-11-05, ChatGPT 제안 · Claude 구현). 주문 판단에는 쓰지 않는 기록 전용.

매시 주문안을 만들 때 순위 상위 종목마다 '매수 신호가 있었는지'와 '걸린 차단 사유 전부'를 data/missed_ledger.jsonl에 한 줄씩 남긴다.
주문안 사유에는 처음 걸린 사유 하나만 남아서, BEAM처럼 놓친 사례를 나중에 추측으로 설명하게 되는 문제를 막는다.
이후 가격 변화(1·4·24시간)는 tools/missed_report.py가 공개 시세로 채워 사유별로 '놓친 수익'과 '피한 손실'을 함께 집계한다.
기록 실패는 주문안 생성을 막지 않는다(호출하는 쪽에서 예외를 삼킨다).
"""
import json
import re
import time
from pathlib import Path

import chase_override as co
import strategy as st

TOP = 15
FILE = 'missed_ledger.jsonl'


def blocks_for(r, base, held, protected, promoted, last, entry_ok, regime_closed, scan, now):
    """이 종목에 걸린 차단 사유 전부(순서 무관). 빈 목록이면 규칙상 매수 후보."""
    m, out = r['market'], []
    if m in protected:
        out.append('사용자 보호')
    if m in held:
        out.append('이미 보유')
    if r['rank'] > scan:
        out.append(f'순위 {scan}위 밖')
    if r['rs'] <= 0:
        out.append('상승률 0 이하')
    if m not in entry_ok:
        out.append('매수 불가 경보')
    if regime_closed:
        out.append('장세 필터·매수 중단')
    cap = co.cap_for(m, base, promoted, last, now)
    if cap > 0 and r['rs'] >= cap:
        out.append(f'과열(상한 {cap:.0%})')
    if st.drop_block(r, base):
        out.append('급락 중(2시간)')
    if last and not co.reentry_ok(last, now) and st.reentry_block(r, last, now, base):
        out.append('재진입 대기')
    return out


def record(data_dir, signal_closed_at, ranked, base, holdings, protected, promoted, last_sells, entry_ok, reasons, text, now=None):
    now = time.time() if now is None else now
    held = {m for m, v in holdings.items() if v >= 5000}
    scan = base.entry_scan or base.entry_top
    regime_closed = any(('장세 필터' in x and '통과' not in x) or '신규 매수 임시 중단' in x for x in reasons)
    bought = set('KRW-' + x for x in re.findall(r'매수 ([A-Z0-9]+) \d+원', text))
    rows = []
    for r in ranked[:TOP]:
        m = r['market']
        b = blocks_for(r, base, held, protected, promoted or {}, (last_sells or {}).get(m), entry_ok, regime_closed, scan, now)
        if m in bought:
            outcome = '매수'
        elif not b:
            outcome = '후보였으나 미매수(자리·현금·후보 수)'
        else:
            outcome = '차단'
        rows.append({'slot': signal_closed_at, 'time': now, 'market': m, 'rank': r['rank'], 'rs': r['rs'],
                     'close': r.get('close'), 'signal': r['rank'] <= scan and r['rs'] > 0,
                     'blocks': b, 'outcome': outcome, 'promoted': m in (promoted or {})})
    with (Path(data_dir) / FILE).open('a', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    return rows
