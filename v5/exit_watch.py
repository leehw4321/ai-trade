"""정체 탈출 기록 모드(주문 없음). 2026-11-06 제안서 안 B를 실시간 가격으로 '했다면 어땠을지'만 남긴다.

규칙(사전 고정): 매수 T시간(2h 1차, 4h 비교) 뒤, 그때까지 최고가(profit_guard_peak)가 평단 +1% 미만이고 현재가가 수수료 포함 본전
아래면 '본전 지정가(공식 호가 올림)'를 건 것으로 친다(유효 2시간). 그 뒤 실시간 체결가가 지정가보다 한 호가 이상 위로 거래되면
'가상 체결', 그 전에 봇이 실제로 팔면 '실제 매도 먼저', 기한이 지나면 그때 근거(18h 상승률 ≤ 0 또는 순위 15위 밖)를 보고
'2단계(매수 1호가 매도)' 또는 '취소·보유'로 끝낸다. 가상 체결은 실제 자기 주문 체결의 증명이 아니다(대기열·수량 미반영).

파일: data/exit_watch.json(진행 상태), data/exit_watch.jsonl(사건 기록, 추가만).
"""
import json
import math
import os
import time

FEE = 0.0005
TIMES = (2, 4)
MFE_MAX = 0.01
HOLD = 2 * 3600
RANK_OUT = 15
TABLE = [(2_000_000, 1000), (1_000_000, 1000), (500_000, 500), (100_000, 100), (50_000, 50), (10_000, 10), (5_000, 5),
         (1_000, 1), (100, 1), (10, .1), (1, .01), (.1, .001), (.01, .0001), (.001, .00001), (.0001, .000001), (.00001, .0000001)]


def tick(px):
    """업비트 원화 마켓 호가 단위(공식 표, 2025-07-31 기준)."""
    for lo, t in TABLE:
        if px >= lo: return t
    return 1e-8


def ceil_tick(px):
    t = tick(px)
    return round(math.ceil(px / t - 1e-9) * t, 10)


def _ts(iso):
    import datetime as dt
    try: return dt.datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError): return None


def _ok(x):
    return type(x) in (int, float) and math.isfinite(x) and x > 0


class Watch:
    def __init__(self, data_dir):
        self.state_path = data_dir / 'exit_watch.json'
        self.log_path = data_dir / 'exit_watch.jsonl'
        try: self.s = json.loads(self.state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError): self.s = {}

    def _event(self, row):
        with open(self.log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')

    def _save(self):
        tmp = self.state_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.s, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, self.state_path)

    def update(self, positions, prices, rs_all, protected=(), now=None):
        """3초 감시마다 부른다. positions = 실거래 장부 positions, rs_all = {종목: (순위, 18h 상승률)}."""
        now = now or time.time(); changed = False
        live_keys = set()                 # 아직 보유 중인 포지션(가격을 못 받은 순간도 보유로 본다)
        for m, p in positions.items():
            q0, px0 = p.get('qty'), prices.get(m)
            if not _ok(px0) or (_ok(q0) and q0 * px0 >= 5000):
                live_keys.update(f'{m}|{p.get("time")}|{T}' for T in TIMES)
        for m, p in positions.items():
            if m in protected: continue
            q, cost, opened, px = p.get('qty'), p.get('cost'), _ts(p.get('time')), prices.get(m)
            if not (_ok(q) and _ok(cost) and opened and _ok(px)) or q * px < 5000: continue
            e = cost / q; be = e / (1 - FEE)
            for T in TIMES:
                key = f'{m}|{p.get("time")}|{T}'
                w = self.s.get(key)
                if w is None:
                    if now - opened < T * 3600: continue
                    peak = p.get('profit_guard_peak') if _ok(p.get('profit_guard_peak')) else max(e, px)
                    weak = peak / e - 1 < MFE_MAX and px < be
                    w = self.s[key] = {'m': m, 'T': T, 'opened': p.get('time'), 'at': now, 'entry': e, 'be': be, 'peak': peak,
                                       'px': px, 'cost': cost, 'qty': q, 'weak': weak, 'state': 'armed' if weak else 'pass'}
                    if weak:
                        w['limit'] = ceil_tick(be)
                    self._event({'event': '정체 판정', **w}); changed = True
                    continue
                if w['state'] != 'armed': continue
                w['last_px'] = px
                if px >= w['limit'] + tick(w['limit']):
                    w.update(state='filled', fill_at=now, pnl=w['qty'] * w['limit'] * (1 - FEE) - w['cost'])
                    self._event({'event': '가상 체결', 'key': key, 'px': px, 'limit': w['limit'], 'pnl': w['pnl'], 'at': now}); changed = True
                elif now - w['at'] >= HOLD and rs_all:      # 순위 자료가 아직 없으면(재시작 직후) 판단을 미룬다
                    rank, rs = (rs_all.get(m) or (None, None))
                    gone = (rs is not None and rs <= 0) or rank is None or rank > RANK_OUT
                    bid = px - tick(px)
                    w.update(state='stage2' if gone else 'cancel', end_at=now, rank=rank, rs=rs,
                             pnl=(w['qty'] * bid * (1 - FEE) - w['cost']) if gone else None)
                    self._event({'event': '2단계 매도(가상)' if gone else '기한 만료·근거 유지(취소)', 'key': key, 'px': px,
                                 'rank': rank, 'rs': rs, 'pnl': w['pnl'], 'at': now}); changed = True
        for key, w in self.s.items():     # 진행 중이던 판정의 포지션이 사라짐 = 봇이 실제로 먼저 팔았다
            if w.get('state') == 'armed' and key not in live_keys:
                w.update(state='sold_first', end_at=now)
                self._event({'event': '실제 매도 먼저', 'key': key, 'last_px': w.get('last_px'), 'at': now}); changed = True
        if changed: self._save()

    def summary(self):
        v = list(self.s.values())
        out = {}
        for T in TIMES:
            g = [w for w in v if w.get('T') == T]
            armed = [w for w in g if w.get('weak')]
            out[T] = {'checked': len(g), 'armed': len(armed), 'filled': sum(w['state'] == 'filled' for w in armed),
                      'stage2': sum(w['state'] == 'stage2' for w in armed), 'sold_first': sum(w['state'] == 'sold_first' for w in armed),
                      'cancel': sum(w['state'] == 'cancel' for w in armed), 'open': sum(w['state'] == 'armed' for w in armed)}
        first = min((w['at'] for w in v), default=None)
        return out, first


def render(watch, brake_log, esc=None):
    """화면 한 줄 요약."""
    import datetime as dt
    s, first = watch.summary()
    if first is None:
        since = '아직 판정 없음'
    else:
        since = dt.datetime.fromtimestamp(first).strftime('%m-%d %H:%M') + '부터'
    try:
        rows = [json.loads(x) for x in brake_log.read_text(encoding='utf-8').splitlines() if x.strip()]
    except (OSError, ValueError):
        rows = []
    b = sum(1 for r in rows if r.get('brake_4h'))
    a = s[2]; c = s[4]
    return (f"<div class=sub style='margin-top:8px'>기록 모드(주문 없음) · {since} · 정체 탈출 2시간: 판정 {a['checked']} · 발동 {a['armed']} "
            f"(가상 체결 {a['filled']} · 실제 매도 먼저 {a['sold_first']} · 2단계 {a['stage2']} · 취소 {a['cancel']} · 진행 {a['open']}) · "
            f"4시간 비교: 발동 {c['armed']}(가상 체결 {c['filled']}) · 단기 브레이크 발동 {b}/{len(rows)}시간 · "
            f"판정 기준: 최소 2주 그리고 발동 30건</div>")
