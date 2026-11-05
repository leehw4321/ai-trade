"""그림자 실험: C안 변형 12개를 같은 시세로 동시에 모의 운영하고, 1시간마다 성과를 분석해 토론에 공유한다.

주문 기능 없음. 실거래(bot.py의 live)와 완전히 분리된 별도 프로세스다.
python shadow.py            실행
python shadow.py --report   지금 성과 보고서만 생성
"""
import argparse
from dataclasses import replace
import datetime as dt
import html
import itertools
import json
import math
import os
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback

import strategy as st
import rotation_policy as rotation
import chase_override as co
import missed_ledger
import upbit
from bot import boundary, load_params, SETTLE, KST, utc
from paper import Ledger, now_iso

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
SHADOW = DATA / 'shadow'
STOP = ROOT / 'stop_shadow.txt'
LOCK = ROOT / 'shadow.lock'
EVIDENCE = Path(os.environ.get('AI_DEBATE_DIR', str(Path.home() / 'Desktop' / 'AI토론장'))) / '공유_근거자료.md'
MARK_A, MARK_B = '<!-- v5 성과 분석 시작 -->', '<!-- v5 성과 분석 끝 -->'
GRID = {'L': (32, 72, 168), 'k': (2.0, 3.0), 'M': (5, 8)}  # 15분봉 기준 8·18·42시간


# 고정 시간 슬롯 주문안(2026-11-04 합의 B): 재시작은 새 판단을 만들지 않고 다음 정규 슬롯(매시 정각 뒤)을 기다린다.
SLOT_SECONDS = 3600
SLOT_DELAY = 90        # 슬롯 시작 뒤 이 시간이 지나 마감 봉 판단이 끝난 다음에 만든다
SLOT_MAX_LATE = 600    # 이 시간이 지난 슬롯은 소급해서 만들지 않는다(사전 정한 허용 지연)
SLOT_FILE = DATA / 'plan_slot.json'


def current_slot(now):
    return int(now // SLOT_SECONDS) * SLOT_SECONDS


def slot_due(now, last_slot):
    """지금 만들어야 할 슬롯 시작 시각. 이미 처리했거나 시작 전·허용 지연 밖이면 None."""
    slot = current_slot(now)
    if slot > last_slot and SLOT_DELAY <= now - slot <= SLOT_MAX_LATE:
        return slot
    return None


def load_last_slot(now, path=None):
    """영속 기록된 마지막 슬롯. 파일이 없거나 손상·비정각·미래 값이면 현재 슬롯을 처리한 것으로 간주한다(중복 주문안보다 한 슬롯 건너뛰는 쪽이 안전).

    가동 시작에는 항상 현재 슬롯도 처리한 것으로 본다: 재시작이 허용 지연창 안에 있어도 새 판단을 만들지 않고 다음 정규 슬롯을 기다린다."""
    path = path or SLOT_FILE
    now_slot = current_slot(now)
    try:
        value = json.loads(path.read_text(encoding='utf-8'))['slot']
        if type(value) in (int, float) and math.isfinite(value) and value % SLOT_SECONDS == 0 and 0 <= value <= now_slot:
            return max(int(value), now_slot)
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        pass
    return now_slot


def plan_tick(now, last_slot, ready, claim, make):
    """한 번의 반복에서 할 일. (새 last_slot, 상태). 신호가 아직 최신이 아니면 기록하지 않고 허용 지연 안에서 다시 시도한다."""
    slot = slot_due(now, last_slot)
    if slot is None:
        return last_slot, None
    if not ready():
        return last_slot, 'waiting'
    claim(slot)  # 먼저 기록: 이후 어떤 장애가 나도 이 슬롯은 한 번만
    try:
        make()
    except Exception:
        traceback.print_exc()
        return slot, 'failed'
    return slot, 'made'


def claim_slot(slot, path=None):
    """주문안을 만들기 전에 슬롯을 먼저 기록한다. 기록 뒤 장애가 나도 같은 슬롯의 주문안이 두 번 만들어지지 않는다."""
    path = path or SLOT_FILE
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps({'slot': int(slot), 'claimed_at': now_iso()}), encoding='utf-8')
    tmp.replace(path)


def proposal(holdings, ranked, rs_out, entry_ok, base, caps, krw, bought, last_sells=None, market_index=None, rotation_state=None, rotation_slot=None,
             promoted=None, hot=None, protected=None, costs=None, stuck_out=None):
    """C안 규칙을 실제 계좌에 그대로 적용한 주문안. 기존 보유도 신규 후보와 같은 기준으로 평가한다(사용자 지시).

    holdings: {종목: 평가액}. 반환: (주문안 문구, 사유 목록)
    promoted: 사용자 직접 지정 종목, hot: 과열 보유 종목(순위 매도 제외). chase_override 참고.
    """
    pos = {r['market']: r for r in ranked}
    reasons, sells, sold = [], [], 0.0
    cands = []
    promoted, hot, protected = promoted or {}, hot or set(), protected or set()
    for m, v in holdings.items():
        if v < 5000:
            continue
        if m in protected:
            reasons.append(f'{m[4:]} 사용자 보호: 봇 매매 대상 아님 (투입 한도 계산에서도 제외)')
            continue
        if m in hot or not getattr(base, 'rank_exit', True):
            kind = '과열 보유' if m in hot else '보유'
            r = pos.get(m)
            rs = r['rs'] if r else rs_out.get(m)
            if r is None or r['rank'] > base.M or (rs is not None and rs < 0):
                # 묶임: 예전 규칙이면 순위로 팔았을 보유. 손익은 봇 장부 원가 기준(없으면 미확인).
                cost = (costs or {}).get(m)
                state = '손익 미확인' if not cost else ('이익 중' if v > cost else '손실 중')
                kind = (kind + ' · ' if m in hot else '') + f'묶임({state})'
                if stuck_out is not None:
                    stuck_out[m] = {'state': state, 'rank': r['rank'] if r else None, 'rs': rs,
                                    'pnl_pct': (v / cost - 1) if cost else None, 'value': v}
            reasons.append(f'{m[4:]} 유지: {kind} — 순위 매도 안 함, 손절·이익보호로만 매도 (사용자 지시)')
            continue
        r = pos.get(m)
        rs = r['rs'] if r else rs_out.get(m)
        if r is None:
            if rs is None:
                reasons.append(f'{m[4:]} 자료 누락: 순위 매도 판단 보류')
                continue
            why = f'거래대금 상위 {base.universe} 밖' + (f'(상대강도 {rs:+.1%})' if rs is not None else '')
        elif r['rank'] > base.M:
            if rotation.enabled(base) and rs is not None and rs >= 0:
                counts = rotation_state if isinstance(rotation_state, dict) else {}
                if not rotation.rank_exit(counts, m, r['rank'], rotation_slot, base.M, base.rotation_confirmations):
                    reasons.append(f'{m[4:]} 순위 경계 확인 대기 (손절·이익보호 별도)')
                    continue
            why = f'순위 {r["rank"]}위 > 유지 {base.M}위 (상대강도 {rs:+.1%})'
        elif rs is not None and rs < 0:
            why = f'상대강도 {rs:+.1%} < 0'
        else:
            if rotation.enabled(base) and isinstance(rotation_state, dict): rotation_state.pop(m, None)
            reasons.append(f'{m[4:]} 유지: 순위 {r["rank"]}위, 상대강도 {rs:+.1%}')
            continue
        cands.append((rs if rs is not None else -1.0, m, v, why))
    for rs, m, v, why in sorted(cands):  # 약한 것부터, 1회 매도 한도 안에서
        if sold + v > caps['max_sell_krw_per_plan']:
            reasons.append(f'{m[4:]} 매도 보류: 1회 매도 한도 {caps["max_sell_krw_per_plan"]:,}원 초과분 ({why})')
            continue
        sells.append(m)
        sold += v
        reasons.append(f'{m[4:]} 매도 100% (약 {v:,.0f}원): {why}')
    cash = (krw + sold * 0.997) * 0.98
    room = max(0.0, caps['max_total_buy_krw'] - bought)
    # Eligibility is based on the inventory at plan creation, not the inventory after
    # hypothetical sales. A queued sale must never make the same coin a fresh buy.
    originally_held = set(holdings)
    sell_queued = set(sells)
    picks = []
    want, scan = base.entry_want or base.entry_top, base.entry_scan or base.entry_top
    if not getattr(base, 'rank_exit', True):
        # 순위 매도를 끈 뒤에는 봇 보유 종목 수 상한(slots)을 지킨다(사용자 지시 2026-11-05: 최대 4종목). 보호 종목·5,000원 미만 자투리는 세지 않는다.
        bot_held = {m: v for m, v in holdings.items() if v >= 5000 and m not in protected and m not in sell_queued}
        count = len(bot_held)
        if not getattr(base, 'swap_stuck_loss', False):   # 부분 교체가 켜져 있으면 자리는 아래에서 금액 기준으로 센다
            want = max(0, min(want, base.slots - count))
        stuck_n = sum(1 for _ in (stuck_out or {}))
        reasons.append(f'봇 보유 {count}/{base.slots}종목' + (f' (묶임 {stuck_n})' if stuck_n else '') + f' → 신규 매수 최대 {want}종목')
    if rotation.enabled(base): scan = min(scan, base.M)
    regime_closed = False
    if getattr(base, 'halt_new_buys', False):
        regime_closed = True
        reasons.append('신규 매수 임시 중단: 비용 후 우위가 검증되기 전에는 새로 사지 않는다 (보유 관리·매도·손절은 그대로)')
    elif getattr(base, 'regime_filter', False):
        expires = getattr(base, 'regime_expires', 0.0)
        if expires and time.time() >= expires:
            regime_closed = True
            reasons.append('장세 필터 임시 적용 기간 만료 → 신규 매수 전부 보류 (재검토·재결정 전에는 재개하지 않음)')
        elif type(market_index) not in (int, float) or not math.isfinite(market_index):
            regime_closed = True
            reasons.append('장세 필터: 시장 지수 자료 부족·비정상 → 신규 매수 보류')
        elif market_index <= 0:
            regime_closed = True
            reasons.append(f'장세 필터: 시장 균등지수 {base.regime_bars * base.tf // 60}시간 {market_index:+.2%} ≤ 0 → 신규 매수 보류 (보유 관리·매도는 그대로)')
        else:
            reasons.append(f'장세 필터 통과: 시장 균등지수 {market_index:+.2%} > 0')
    for r in ([] if regime_closed else ranked[:scan]):
        if rotation.enabled(base) and (type(r.get('rank')) is not int or not 1 <= r['rank'] <= base.M):
            continue
        if len(picks) >= want:
            break
        if r['market'] in originally_held or r['market'] in sell_queued:
            if r['market'] in sell_queued:
                reasons.append(f'{r["market"][4:]} 동일 주문안 매도 예정: 재매수 금지')
            continue
        if r['market'] in entry_ok and r['rs'] > 0:
            last = (last_sells or {}).get(r['market'])
            again = co.reentry_ok(last)
            why = (st.chase_block(r, base, co.cap_for(r['market'], base, promoted, last))
                   or (st.reentry_block(r, last, time.time(), base) if last and not again else None))
            if why:
                reasons.append(f'{r["market"][4:]} 매수 보류: {why}')
            else:
                if r['rs'] >= base.chase_rs_cap > 0:
                    reasons.append(f'{r["market"][4:]} 과열 예외 매수 후보: ' + ('사용자 직접 지정' if r['market'] in promoted else '이익 청산 뒤 재진입 1회'))
                picks.append(r)
    equity = sum(holdings.values()) + krw
    partial = {}
    if not getattr(base, 'rank_exit', True) and getattr(base, 'swap_stuck_loss', False) and picks:
        # 부분 매도 교체(사용자 지시 2026-11-05 '다'): 자리는 금액(1칸 = 1회 매수 금액)으로 센다. 매수 후보가 있는데 자리·현금이 모자라면
        # 묶임(손실 중) 중 상승률이 가장 약한 한 종목을 모자란 만큼만 판다(남는 양이 1만 원 미만이면 전량). 묶임(이익 중)·보호 종목은 팔지 않는다.
        unit = ((base.entry_risk or base.risk) * equity / base.stop_max_pct if base.size_by_risk and base.stop_max_pct > 0
                else caps['max_order_krw'])
        used = sum(min(1.0, v / unit) for v in bot_held.values())
        short = max(max(0.0, 1 - (base.slots - used)) * unit, unit - cash)
        losers = sorted(((s['rs'] if isinstance(s.get('rs'), (int, float)) else -1.0), m) for m, s in (stuck_out or {}).items()
                        if s.get('state') == '손실 중' and m in bot_held)
        if short > 0 and losers:
            k = losers[0][1]
            v = bot_held[k]
            need = short * 1.03
            pct = 100 if v - need < 10000 else min(100, math.ceil(need / v * 100))
            partial[k] = pct
            cash += v * pct / 100 * 0.997 * 0.98
            used -= min(1.0, v / unit) - min(1.0, v * (1 - pct / 100) / unit)
            reasons.append(f'{k[4:]} 부분 교체 매도 {pct}% (약 {v * pct / 100:,.0f}원): 묶임(손실 중) 중 가장 약함, 새 매수 자리·현금 확보')
        n = max(0, int(base.slots - used + 1e-9))
        if len(picks) > n:
            reasons.append(f'봇 자리(금액 기준) {used:.2f}/{base.slots}칸 → 매수 후보 {len(picks)}개 중 {n}개만')
            picks = picks[:n]
        if not picks and partial:
            reasons.append('교체 매도 취소: 자리가 나지 않아 살 수 없음')
            cash -= sum(bot_held[k] * p / 100 * 0.997 * 0.98 for k, p in partial.items())
            partial = {}
    buys = []
    # 전략 설계의 비율 한도: 종목당 coin_cap, 총투입 total_cap (계좌 평가액 기준). 고정 금액 한도를 풀어도 이 한도는 유지한다(D-29).
    # 사용자 보호 종목은 봇이 투입한 돈이 아니다. 예전에는 보호 종목이 순위 매도 목록에 올라 우연히 빠졌으나 순위 매도를 끈 뒤(2026-11-05) 명시적으로 뺀다.
    invested = (sum(v for m, v in holdings.items() if m not in sells and m not in protected)
                - sum(holdings[k] * p / 100 for k, p in partial.items()))
    total_room = max(0.0, base.total_cap * equity - invested)
    for i, r in enumerate(picks):
        coin_room = max(0.0, base.coin_cap * equity - holdings.get(r['market'], 0.0))
        risk_room = (base.entry_risk or base.risk) * equity / base.stop_max_pct if base.size_by_risk and base.stop_max_pct > 0 else float('inf')
        amt = int(min(caps['max_order_krw'], room, cash / (len(picks) - i), coin_room, total_room, risk_room) // 1000 * 1000)
        if amt < 5000:
            reasons.append(f'{r["market"][4:]} 매수 불가: 금액 부족 (누적 한도 잔여 {room:,.0f}원, 종목 한도 잔여 {coin_room:,.0f}원, 총투입 한도 잔여 {total_room:,.0f}원)')
            continue
        buys.append((r['market'], amt))
        cash -= amt
        room -= amt
        total_room -= amt
        reasons.append(f'{r["market"][4:]} 매수 {amt:,}원: 순위 {r["rank"]}위, 상대강도 {r["rs"]:+.1%}')
    parts = ([f'매도 {m[4:]} 100%' for m in sells] + [f'매도 {m[4:]} {p}%' for m, p in partial.items()]) or ['매도 없음']
    parts += [f'매수 {m[4:]} {a}원' for m, a in buys] or ['매수 없음']
    return '실행 주문안: ' + '; '.join(parts), reasons


bar_after_entry = st.bar_after_entry


def variants(base):
    out = {}
    for L, k, M in itertools.product(GRID['L'], GRID['k'], GRID['M']):
        out[f'L{L}_k{k:g}_M{M}'] = replace(base, L=L, k=k, M=M)
    # EXIT-T1 비교군 (토론 안건 26 합의). 비교 기준 '기존'은 위 격자의 기본 변형(trail 꺼짐)이다.
    inf = float('inf')
    out['EXIT_T1'] = replace(base, trail_enabled=True, trail_start_r=inf, giveback=0.0)
    out['EXIT_T1T2'] = replace(base, trail_enabled=True, giveback=0.0)
    out['EXIT_T1T2T3'] = replace(base, trail_enabled=True)
    return out


# ---------- 성과 분석 ----------
def metrics(folder, A0):
    def rows(name):
        f = folder / name
        return [json.loads(x) for x in f.read_text(encoding='utf-8').splitlines() if x.strip()] if f.exists() else []
    trades, eq = rows('trades.jsonl'), rows('equity.jsonl')
    closed = [t for t in trades if t['side'] == '매도']
    wins = [t['pnl'] for t in closed if t['pnl'] > 0]
    losses = [-t['pnl'] for t in closed if t['pnl'] <= 0]
    peak, mdd = A0, 0.0
    for r in eq:
        peak = max(peak, r['P'])
        mdd = max(mdd, 1 - r['P'] / peak)
    last = eq[-1] if eq else {'P': A0, 'H': None}
    h_ret = None
    if eq and eq[0].get('H') and last.get('H'):
        h_ret = last['H'] / eq[0]['H'] - 1
    return {'ret': last['P'] / A0 - 1, 'h_ret': h_ret, 'buys': sum(t['side'] == '매수' for t in trades),
            'closed': len(closed), 'win': len(wins) / len(closed) if closed else None,
            'pf': (sum(wins) / sum(losses)) if losses else (None if not wins else float('inf')),
            'avg': sum(t['pnl_pct'] for t in closed) / len(closed) if closed else None,
            'mdd': mdd, 'stops': sum(t.get('reason') == '손절' for t in closed), 'since': eq[0]['time'] if eq else ''}


def fmt(v, kind):
    if v is None:
        return '-'
    if kind == '%':
        return f'{v * 100:+.2f}%'
    if kind == 'pf':
        return '∞' if v == float('inf') else f'{v:.2f}'
    return str(v)


def report(base, prop=None):
    rows = []
    for name, folder in [('ALL(운영 기본)', DATA / 'ALL'), ('P10(실제 규모)', DATA / 'P10')] + \
            [(n, SHADOW / n) for n in variants(base)]:
        f = folder / 'ledger.json'
        if not f.exists():
            continue
        A0 = json.loads(f.read_text(encoding='utf-8'))['A0']
        rows.append((name, metrics(folder, A0)))
    rows.sort(key=lambda r: -r[1]['ret'])
    head = '| 모델 | 수익률 | 보유(H) 대비 | 매수 | 청산 | 승률 | 손익비(PF) | 평균 손익 | 손절 | 최대낙폭 |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|'
    lines = [f"| {n} | {fmt(m['ret'], '%')} | {fmt(None if m['h_ret'] is None else m['ret'] - m['h_ret'], '%')} | {m['buys']} | {m['closed']} | "
             f"{fmt(m['win'], '%') if m['win'] is not None else '-'} | {fmt(m['pf'], 'pf')} | "
             f"{('%+.2f%%' % m['avg']) if m['avg'] is not None else '-'} | {m['stops']} | {m['mdd'] * 100:.2f}% |" for n, m in rows]
    since = min((m['since'] for _, m in rows if m['since']), default='')
    md = (f'### v5 실운영 성과 (자동 분석 {now_iso()[5:16]}, 관측 시작 {since[5:16]})\n\n'
          '같은 실제 시세로 동시에 돌린 모의 장부들이다. 비용(호가±c, 수수료) 포함. 관측 기간이 짧으면 우연의 영향이 크다. '
          '변형 이름: L=상대강도 15분봉 수(32=8시간, 72=18시간, 168=42시간), k=손절 ATR 배수, M=유지 순위.\n\n'
          + head + '\n' + '\n'.join(lines) + '\n')
    if prop:
        md += (f'\n### 봇 계산 주문안 ({prop["time"][5:16]}, C안 운영 기본값을 실제 계좌에 적용)\n\n'
               '기존 보유도 신규 후보와 같은 기준으로 평가했다(사용자 지시). 두 AI가 아래 문구를 그대로 수용해 합의하면 봇이 실행한다. '
               '수정하려면 근거를 대고 고친 문구로 합의한다.\n\n'
               f'`{prop["text"]}`\n\n' + '\n'.join(f'- {x}' for x in prop['reasons']) + '\n')
    (DATA / 'report.md').write_text(md, encoding='utf-8')
    page = ('<!doctype html><html lang=ko><meta charset=utf-8><meta http-equiv=refresh content=60><title>v5 성과 분석</title>'
            '<style>body{font:15px/1.6 "Malgun Gothic",sans-serif;margin:24px max(16px,3vw);background:#f4f6f7;color:#17212b}'
            'table{border-collapse:collapse;background:#fff}td,th{border:1px solid #dde3e7;padding:5px 9px;text-align:right}'
            'td:first-child,th:first-child{text-align:left}p{max-width:70ch;color:#5d6b77}</style>'
            + '<h1>v5 모델 비교</h1>' + ''.join(_md_to_html(md)) + '</html>')
    (DATA / 'report.html').write_text(page, encoding='utf-8')
    share(md)
    return md


def _md_to_html(md):
    for line in md.splitlines():
        if line.startswith('### '):
            yield f'<h2>{html.escape(line[4:])}</h2>'
        elif line.startswith('|---'):
            continue
        elif line.startswith('|'):
            cells = [c.strip() for c in line.strip('|').split('|')]
            tag = 'th' if cells[0] == '모델' else 'td'
            yield '<tr>' + ''.join(f'<{tag}>{html.escape(c)}</{tag}>' for c in cells) + '</tr>'
        elif line.strip():
            yield f'<p>{html.escape(line)}</p>'
    yield ''


def share(md):
    """근거자료의 성과 구역만 최신 내용으로 바꾼다(파일이 계속 커지지 않게)."""
    try:
        text = EVIDENCE.read_text(encoding='utf-8')
        block = f'{MARK_A}\n{md}{MARK_B}'
        if MARK_A in text and MARK_B in text:
            text = re.sub(re.escape(MARK_A) + r'.*?' + re.escape(MARK_B), lambda _: block, text, flags=re.S)
        else:
            text += '\n\n' + block + '\n'
        EVIDENCE.write_text(text, encoding='utf-8')
    except OSError:
        pass


# ---------- 운영 ----------
class Shadow:
    def __init__(self):
        self.base, self.cfg = load_params()
        self.vars = variants(self.base)
        self.ledgers = {n: Ledger(SHADOW / n) for n in self.vars}
        self.markets = {}
        self.prop = None
        self.base_ranked, self.rs_out, self.entry_ok = [], {}, set()
        self.signal_closed_at = self.signal_tf_minutes = self.evaluated_at = None
        self.rotation_counts = {}

    def proposal_signal(self, now=None):
        """Return provenance only for the current completed signal, never a relabelled cache."""
        now = time.time() if now is None else now
        tf = self.base.tf
        if type(tf) is not int or tf <= 0 or self.signal_tf_minutes != tf:
            raise ValueError('주문안 신호 주기 불일치: 새 판단 필요')
        closed, evaluated = self.signal_closed_at, self.evaluated_at
        if any(type(value) not in (int, float) or not math.isfinite(value)
               for value in (now, closed, evaluated)):
            raise ValueError('주문안 신호 출처 누락 또는 비정상 시각')
        expected = boundary(now - SETTLE, tf * 60)
        if closed != expected or not closed + SETTLE <= evaluated <= now:
            raise ValueError('주문안 신호가 최신 마감 판단과 다름: 오래된 순위 사용 금지')
        if not self.base_ranked:
            raise ValueError('주문안 순위 자료 없음')
        for row in self.base_ranked:
            try:
                bar_close = dt.datetime.fromisoformat(row['bar']).timestamp() + tf * 60
            except (KeyError, TypeError, ValueError, OverflowError) as error:
                raise ValueError('주문안 순위 봉 시각 오류') from error
            if bar_close != closed:
                raise ValueError('주문안 순위와 신호 마감 시각 불일치')
        return {'signal_closed_at': closed, 'signal_tf_minutes': tf, 'evaluated_at': evaluated}

    def protected_markets(self):
        """사용자 보호 종목(봇 매매 대상 아님). 읽기 실패면 빈 집합: 보호 종목이 투입액에 남아 매수 여력이 줄어드는 보수적인 쪽이다.
        실제 주문의 보호 강제는 live가 따로 한다."""
        import asset_protection
        try:
            policy = asset_protection.Policy(DATA / 'user_protected_assets.json', self.cfg.get('live', {}).get('protected_markets', []), required=False)
            return asset_protection.effective(policy.read(), policy.configured)
        except Exception:
            return set()

    def make_proposal(self):
        self.proposal_signal()  # Refuse stale cached ranks before any private account lookup.
        if rotation.enabled(self.base):
            if self.cfg.get('live', {}).get('slot_plans') is not True:
                raise ValueError('새 순환 정책은 고정 슬롯 경로만 허용')
            rotation.envelope(self.base, self.signal_closed_at, time.time())
        rows = upbit.accounts()
        krw = sum(float(r['balance']) for r in rows if r['currency'] == 'KRW')
        coins = {'KRW-' + r['currency']: float(r['balance']) for r in rows
                 if r['currency'] != 'KRW' and 'KRW-' + r['currency'] in self.markets}
        tick = upbit.tickers(coins) if coins else {}
        holdings = {m: q * tick[m]['trade_price'] for m, q in coins.items() if m in tick}
        caps = {'max_order_krw': 30000, 'max_total_buy_krw': 100000, 'max_sell_krw_per_plan': 300000, **self.cfg.get('live', {})}
        lf = DATA / 'live' / 'live.json'
        state = json.loads(lf.read_text(encoding='utf-8')) if lf.exists() else {}
        bought = state.get('bought_krw', 0)
        caps.update(state.get('limits', {}))
        rs_of = {**self.rs_out, **{r['market']: r['rs'] for r in self.base_ranked}}
        hot = co.update_hot_held(DATA, {m for m, v in holdings.items() if v >= 5000}, rs_of, self.base)
        co.reset_cooled(DATA, rs_of, self.base)  # 과열이 풀린 직접 지정은 지운다(다시 과열되면 원래 15% 규칙)
        promoted, protected = co.promoted(DATA), self.protected_markets()
        text, reasons = proposal(holdings, self.base_ranked, self.rs_out, self.entry_ok, self.base, caps, krw, bought, state.get('last_sell'),
                                 market_index=getattr(self, 'market_index', None),
                                 rotation_state=getattr(self, 'rotation_counts', None),
                                 rotation_slot=self.signal_closed_at,
                                 promoted=promoted, hot=hot, protected=protected,
                                 costs={m: p.get('cost') for m, p in state.get('positions', {}).items() if isinstance(p, dict)},
                                 stuck_out=(stuck := {}))
        co.write_stuck(DATA, stuck)
        # Account/quote lookups may cross a candle boundary. Recheck the actual source before writing.
        provenance = self.proposal_signal()
        try:  # 놓친 이유 장부: 기록 전용, 실패해도 주문안은 그대로 만든다
            missed_ledger.record(DATA, self.signal_closed_at, self.base_ranked, self.base, holdings, protected, promoted,
                                 state.get('last_sell'), self.entry_ok, reasons, text, now=self.evaluated_at)
        except Exception as ex:
            print(f'[{now_iso()}] 놓친 이유 장부 기록 실패: {type(ex).__name__}: {ex}', flush=True)
        # Public signal prices, not account inventory: the receiver can enforce the SAME
        # chase cap against a later executable ask instead of trusting a frozen return.
        entry_context = {}
        for row in self.base_ranked:
            close, rs = row.get('close'), row.get('rs')
            if (type(close) not in (int, float) or not math.isfinite(close) or close <= 0
                    or type(rs) not in (int, float) or not math.isfinite(rs) or rs <= -1):
                continue
            anchor = close / (1 + rs)
            if not math.isfinite(anchor) or anchor <= 0: continue
            entry_context[row['market']] = {'market': row['market'], 'signal_close': close,
                'anchor_close': anchor, 'rs': rs, **provenance}
            if rotation.enabled(self.base):
                entry_context[row['market']].update(rank=row['rank'], policy_hash=rotation.policy_hash(self.base))
        self.prop = {'time': now_iso(), 'text': text, 'reasons': reasons,
                     'entry_context': entry_context, 'market_index': getattr(self, 'market_index', None), **provenance}
        if rotation.enabled(self.base):
            self.prop['rotation'] = rotation.envelope(self.base, self.signal_closed_at, time.time())
            self.prop['rotation_markets'] = {r['market']: {k:r[k] for k in ('rank', 'rs', 'close', 'atr', 'bar')} for r in self.base_ranked}
            rotation.append_audit(SHADOW / 'rotation_audit.jsonl',
                {'event': 'decision_created', 'rotation': self.prop['rotation'],
                 'markets': self.prop['rotation_markets'], 'reasons': reasons, 'plan': text})
        tmp = DATA / 'proposal.tmp'
        tmp.write_text(json.dumps(self.prop, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(DATA / 'proposal.json')

    def init(self):
        src = json.loads((DATA / 'ALL' / 'ledger.json').read_text(encoding='utf-8'))
        for n, lg in self.ledgers.items():
            if not lg.ready:
                H = src['H']
                lg.start(src['E0'], 1.0, H['basket'], H['krw'], H['unpriced'], label=f'그림자 {n}')
                lg.d['last_4h'] = boundary(time.time() - SETTLE, self.base.tf * 60)
                lg.save()

    def decide(self, end):
        # A failed refresh must not leave the previous ranks eligible for a newly stamped order.
        self.signal_closed_at = self.signal_tf_minutes = self.evaluated_at = None
        self.prop = None
        self.markets = upbit.krw_markets()
        tick = upbit.tickers(self.markets)
        pool = sorted((m for m in tick if m not in self.base.exclude), key=lambda m: -tick[m]['acc_trade_price_24h'])
        universe = pool[:self.base.universe]
        held = {m for lg in self.ledgers.values() for m in lg.d['positions']}
        try:
            held |= {'KRW-' + r['currency'] for r in upbit.accounts() if 'KRW-' + r['currency'] in self.markets}
        except Exception:
            pass
        bars = {}
        for m in dict.fromkeys(universe + sorted(held)):
            b = upbit.completed_candles(m, self.base.tf, 200)
            if b and dt.datetime.fromisoformat(b[-1]['t']).timestamp() + self.base.tf * 60 == end:
                bars[m] = b
            time.sleep(0.12)
        uni = set(universe)
        entry_ok = {m for m in universe if m in bars and not self.markets.get(m, {}).get('trade_blocked', self.markets.get(m, {}).get('warning'))}
        self.base_ranked = st.rank({m: b for m, b in bars.items() if m in uni}, self.base)
        self.market_index = st.market_index({m: b for m, b in bars.items() if m in uni}, self.base.regime_bars,
                                            self.base.regime_min_markets)
        self.rs_out = {m: st.relative_strength(b, self.base.L) for m, b in bars.items() if m not in uni}
        self.entry_ok = entry_ok
        need_books = set()
        plans = {}
        for n, lg in self.ledgers.items():
            if (lg.d['last_4h'] or 0) >= end:
                continue
            p = self.vars[n]
            ranked = st.rank({m: b for m, b in bars.items() if m in uni}, p)
            lg.d['banned'] = {m: t for m, t in lg.d['banned'].items() if t > time.time()}
            sells, buys, _ = st.plan(ranked, list(lg.d['positions']), set(lg.d['banned']), entry_ok, p)
            plans[n] = (p, ranked, sells, buys)
            need_books |= {m for m, _ in sells} | set(buys)
        books = upbit.orderbooks(sorted(need_books)) if need_books else {}
        prices = {m: t['trade_price'] for m, t in tick.items()}
        for n, (p, ranked, sells, buys) in plans.items():
            lg = self.ledgers[n]
            for m, reason in sells:
                lg.sell(m, books[m]['bid'], reason, p)
            atr = {r['market']: r['atr'] for r in ranked}
            for m in buys:
                eq, inv = lg.equity(prices), lg.invested(prices)
                if not lg.buy(m, books[m]['ask'], atr[m], lambda P, S: st.size(eq, inv, lg.d['cash'], P, S, p), p):
                    lg.d['skipped'] = lg.d.get('skipped', 0) + 1
            lg.d['signals'] = lg.d.get('signals', 0) + len(buys)
            lg.d['last_4h'] = end
            lg.save()
        self.signal_closed_at = end
        self.signal_tf_minutes = self.base.tf
        self.evaluated_at = time.time()

    def stops(self):
        held = sorted({m for lg in self.ledgers.values() for m in lg.d['positions']})
        if not held:
            return
        live = {m: t['trade_price'] for m, t in upbit.tickers(held).items()}
        # EXIT-T1: 확정 1분봉 종가로만 평가하고 봉마다 한 번만 반영한다. 진입 이후 마감된 봉만 쓴다.
        closes = {}
        for n, lg in self.ledgers.items():
            if self.vars[n].trail_enabled:
                for m in lg.d['positions']:
                    if m not in closes:
                        try:
                            bars = upbit.completed_candles(m, 1, 3)
                        except Exception:
                            bars = []
                        closes[m] = bars[-1] if bars else None
        reasons = {}
        for n, lg in self.ledgers.items():
            p = self.vars[n]
            for m, pos in lg.d['positions'].items():
                if m not in live:
                    continue
                why = []
                if p.trail_enabled:
                    bar = closes.get(m)
                    if bar and pos.get('last_bar') != bar['t'] and bar_after_entry(bar, pos['time']):
                        pos['last_bar'] = bar['t']
                        r = st.ratchet(m, pos, bar['c'], p)
                        if r:
                            why.append(r)
                    if live[m] <= pos.get('stop0', pos['stop']) and '손절' not in why:
                        why.append('손절')  # 초기 고정 손절은 기존처럼 실시간 가격으로 보호
                elif live[m] <= pos['stop']:
                    why.append('손절')
                if why:
                    reasons[(n, m)] = '+'.join(why)
            if p.trail_enabled:
                lg.save()
        hit = sorted({m for _, m in reasons})
        if not hit:
            return
        books = upbit.orderbooks(hit)
        for (n, m), why in reasons.items():
            lg = self.ledgers[n]
            if m in lg.d['positions']:  # 종목·비교군당 청산 요청은 한 번, 사유는 모두 기록
                lg.sell(m, books[m]['bid'], why, self.vars[n], banned_until=time.time() + self.base.reentry_hours * 3600)

    def snapshot(self):
        ms = sorted({m for lg in self.ledgers.values() for m in list(lg.d['positions']) + list(lg.d['H']['basket'])})
        prices = {m: t['trade_price'] for m, t in upbit.tickers(ms).items()} if ms else {}
        for lg in self.ledgers.values():
            lg.snapshot(prices)

    def run(self):
        if rotation.enabled(self.base) and self.cfg.get('live', {}).get('slot_plans') is not True:
            raise ValueError('순환 정책/고정 슬롯 설정 불일치: 기존 경로로 전환하지 않음')
        self.init()
        if self.cfg.get('live', {}).get('enabled') and self.cfg.get('live', {}).get('auto_proposal'):
            # Recompute current ranks on restart; ledgers already processed at this close are skipped.
            self.decide(boundary(time.time() - SETTLE, self.base.tf * 60))
        last_stop = last_snap = 0
        next_report = time.time() + 60
        slot_mode = bool(self.cfg.get('live', {}).get('slot_plans'))
        last_slot = load_last_slot(time.time()) if slot_mode else None
        while not STOP.exists():
            now = time.time()
            try:
                end = boundary(now - SETTLE, self.base.tf * 60)
                if any((lg.d['last_4h'] or 0) < end for lg in self.ledgers.values()):
                    self.decide(end)
                if now - last_stop >= self.base.stop_seconds:
                    self.stops()
                    last_stop = now
                if now - last_snap >= 300:
                    self.snapshot()
                    last_snap = now
                if slot_mode:
                    def ready():
                        try:
                            self.proposal_signal(now)  # 마감 봉 순위가 최신일 때만(판단 완료 전에는 기록하지 않고 기다린다)
                            return bool(self.base_ranked)
                        except ValueError:
                            return False
                    last_slot, status = plan_tick(now, last_slot, ready, claim_slot, self.make_proposal)
                    if status in ('made', 'failed'):
                        rotation.append_audit(SHADOW / 'rotation_audit.jsonl',
                            {'event': 'slot_attempt', 'slot': last_slot, 'status': status,
                             'crash_policy': 'at_most_once_skipped_claim_not_replayed'})
                    if now >= next_report:
                        report(self.base, self.prop)
                        next_report = now + 3600
                    if now < last_slot - SLOT_SECONDS:
                        print(f'시계 역행 의심: 현재 {now:.0f} < 기록 슬롯 {last_slot}. 새 주문안은 만들지 않는다.', flush=True)
                elif now >= next_report and self.base_ranked:
                    try:
                        self.make_proposal()
                    except Exception:
                        traceback.print_exc()
                    report(self.base, self.prop)
                    next_report = now + 3600
            except Exception:
                traceback.print_exc()
                time.sleep(30)
            time.sleep(10)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--report', action='store_true')
    a = ap.parse_args()
    if a.report:
        print(report(load_params()[0]))
        sys.exit()
    STOP.unlink(missing_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        pid = LOCK.read_text().strip()
        if pid and pid in subprocess.run(['tasklist', '/FI', f'PID eq {pid}'], capture_output=True, text=True).stdout:
            sys.exit('이미 실행 중입니다.')
        LOCK.unlink()
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    try:
        Shadow().run()
    finally:
        LOCK.unlink(missing_ok=True)
