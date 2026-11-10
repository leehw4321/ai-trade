"""계단 보호선(2026-11-10). 처음엔 기록 모드로 만들었고, 같은 날 사용자 지시 '2+5를 적용해서 실거래'로 실거래 매도를 붙였다.

규칙(사전 고정, 과거 124건 모의에서 고른 'ATR 1배 + 10분 유지'):
- 계단 간격 = 매수 직전 15분봉 ATR(14봉 평균 변동폭 ÷ 가격) × 1.0, 최소 1%. (방안 2)
- 1분 종가가 매수 평단 대비 k계단 이상인 상태가 10분 연속 이어지면 k계단을 인정한다. 순간 급등은 인정하지 않는다. (방안 5)
- 인정된 계단이 1 이상이면 보호선 = 본전(수수료 포함) + (계단 - 1) × 간격. 3초 가격이 보호선 이하가 되면 판다
  (live.step_stop_enabled가 켜져 있을 때 live_sell 경로로 실제 매도, 꺼져 있으면 '가상 매도' 기록만).
- 기존 이익보호·손절·순위 매도는 그대로 함께 작동한다.

이전 조건과 비교: 계단으로 판 종목은 그 뒤에도 가격을 따라가며 '이전 규칙이었다면'의 결과를 추정한다.
  이전 규칙 = 이익보호(설정 arm·giveback) + 그때 손절가, 최대 6시간(넘으면 그때 가격). 순위 교체 매도는 반영하지 못한다.
계단에 걸리지 않은 포지션은 이전 조건과 결과가 같다(차이 0)로 센다.

파일: data/step_watch.json(진행 상태), data/step_watch.jsonl(사건 기록, 추가만).
"""
import json
import math
import os
import time

FEE = 0.0005
ATR_MULT = 1.0
MIN_STEP = 0.01
HOLD_MIN = 10
CF_HOURS = 6


def _ok(x):
    return type(x) in (int, float) and math.isfinite(x) and x > 0


def _ts(iso):
    import datetime as dt
    try: return dt.datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError): return None


def atr_pct(bars, opened=None):
    """15분봉(시간순, t/h/l/c)으로 매수 직전 ATR ÷ 가격. 매수 시각 이후 봉은 쓰지 않는다. 자료가 부족하면 None."""
    if opened:
        bars = [b for b in bars if (_ts(b.get('t')) or 0) + 900 <= opened]
    bars = bars[-15:]
    trs = []
    for p, c in zip(bars, bars[1:]):
        try: trs.append(max(c['h'], p['c']) - min(c['l'], p['c']))
        except (KeyError, TypeError): return None
    if len(trs) < 5 or not _ok(bars[-1].get('c')):
        return None
    return sum(trs) / len(trs) / bars[-1]['c']


class Watch:
    def __init__(self, data_dir):
        self.state_path = data_dir / 'step_watch.json'
        self.log_path = data_dir / 'step_watch.jsonl'
        try: self.s = json.loads(self.state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError): self.s = {}
        self._fail = {}

    def _event(self, row):
        with open(self.log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')

    def _save(self):
        tmp = self.state_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.s, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, self.state_path)

    def cf_markets(self):
        """계단으로 판 뒤 '이전 규칙이었다면'을 따라가는 중인 종목(보유가 아니라 따로 시세를 받아야 한다)."""
        return sorted({w['m'] for w in self.s.values() if w.get('state') == 'sold' and not w.get('cf_done')})

    def update(self, positions, prices, atr_bars=None, now=None, live_sell=None, guard=None):
        """3초 감시마다 부른다. positions = 실거래 장부(보호 종목 제외), prices = {종목: 현재가}(cf_markets 시세 포함).
        atr_bars(종목) → 15분봉 목록: 새 포지션을 처음 볼 때 한 번만 부른다(실패하면 5분 뒤 다시).
        live_sell(종목, w, 가격) → bool: 있으면 보호선에 닿을 때 실제로 판다. guard = {'arm', 'give'}: 이전 규칙 추정용 이익보호 설정."""
        now = now or time.time(); changed = False; seen = set()
        minute = int(now // 60)
        guard = guard or {'arm': 0.03, 'give': 0.3}
        for m, p in positions.items():
            q, cost, opened, px = p.get('qty'), p.get('cost'), _ts(p.get('time')), prices.get(m)
            key = f'{m}|{p.get("time")}'
            if not (_ok(q) and _ok(cost) and opened):
                continue
            if _ok(px) and q * px < 5000:
                continue
            seen.add(key)
            if not _ok(px):
                continue
            w = self.s.get(key)
            if w is None:
                if atr_bars is None or now - self._fail.get(key, 0) < 300:
                    continue
                try:
                    bars = atr_bars(m)
                    a = atr_pct(bars, opened)
                    if a is None:   # 오래전 매수라 매수 전 봉이 없으면 최근 봉으로 대신한다('late'로 표시)
                        a = atr_pct(bars)
                except Exception:
                    a = None
                if a is None:
                    self._fail[key] = now   # 조회 실패: 5분 뒤 다시(3초마다 조회하지 않는다)
                    continue
                e = cost / q
                w = self.s[key] = {'m': m, 'opened': p.get('time'), 'at': now, 'entry': e, 'be': e * (1 + 2 * FEE),
                                   'qty': q, 'cost': cost, 'atr': a, 'step': max(MIN_STEP, ATR_MULT * a), 'level': 0, 'run': 0,
                                   'min': minute, 'last': px, 'peak': px, 'state': 'open', 'late': now - opened > 1800,
                                   'live': live_sell is not None}
                self._event({'event': '계단 추적 시작', 'key': key, **{k: w[k] for k in ('entry', 'atr', 'step', 'late', 'live')}, 'at': now})
                changed = True
                continue
            if w['state'] not in ('open', 'hit'):
                continue
            w['qty'], w['cost'] = q, cost          # 부분 매도·추가 매수 반영
            w['hard'] = p.get('stop'); w['gstop'] = p.get('profit_guard_stop'); w['gpeak'] = p.get('profit_guard_peak')
            if minute != w['min']:          # 1분이 넘어감: 직전 분의 마지막 가격을 그 분의 종가로 본다
                close = w['last']
                k = int((close / w['entry'] - 1) // w['step'])
                if k > w['level']:
                    w['run'] += 1
                    if w['run'] >= HOLD_MIN:
                        w['level'], w['run'] = k, 0
                        w['stop'] = w['be'] * (1 + (k - 1) * w['step']) if k >= 1 else None
                        self._event({'event': '계단 인정', 'key': key, 'level': k, 'stop': w.get('stop'), 'close': close, 'at': now})
                else:
                    w['run'] = 0
                w['min'] = minute; changed = True
            w['last'] = px; w['peak'] = max(w['peak'], px)
            if w.get('stop') and px <= w['stop']:
                if live_sell is not None:
                    if live_sell(m, w, px):
                        funds, fee = w.get('fill_funds'), w.get('fill_fee')
                        pnl = (funds - fee - w['cost'] * min(1, w.get('fill_vol', w['qty']) / w['qty'])) if _ok(funds) else w['qty'] * px * (1 - FEE) - w['cost']
                        w.update(state='sold', sold_at=now, sold_px=px, actual_pnl=pnl, cf_peak=max(w['peak'], w.get('gpeak') or 0),
                                 cf_line=max(w.get('hard') or 0, w.get('gstop') or 0), cf_until=now + CF_HOURS * 3600)
                        self._event({'event': '계단 보호 실제 매도', 'key': key, 'level': w['level'], 'stop': w['stop'], 'px': px, 'pnl': pnl, 'at': now})
                        changed = True
                    elif now - w.get('fail_at', 0) >= 60:
                        w['fail_at'] = now
                        self._event({'event': '계단 보호 매도 보류', 'key': key, 'px': px, 'at': now}); changed = True
                elif w['state'] == 'open':
                    pnl = w['qty'] * px * (1 - FEE) - w['cost']
                    w.update(state='hit', hit_at=now, hit_px=px, hit_pnl=pnl)
                    self._event({'event': '가상 매도', 'key': key, 'level': w['level'], 'stop': w['stop'], 'px': px, 'pnl': pnl, 'at': now})
                    changed = True
        for key, w in self.s.items():
            if w.get('state') in ('open', 'hit') and key not in seen:     # 장부에서 사라짐 = 다른 규칙으로 실제로 팔렸다
                actual = w['qty'] * w['last'] * (1 - FEE) - w['cost']
                w.update(state='closed', hit=w['state'] == 'hit', end_at=now, actual_pnl=actual, cf_pnl=actual, cf_done=True)
                self._event({'event': '실제 매도 확인', 'key': key, 'hit': w['hit'], 'last_px': w['last'], 'actual_pnl': actual,
                             'hit_pnl': w.get('hit_pnl'), 'at': now})
                changed = True
            elif w.get('state') == 'sold' and not w.get('cf_done'):          # 계단으로 판 뒤: 이전 규칙이었다면
                px = prices.get(w['m'])
                if not _ok(px):
                    continue
                e = w['entry']; w['cf_peak'] = max(w['cf_peak'], px)
                if w['cf_peak'] >= e * (1 + guard['arm']):
                    w['cf_line'] = max(w['cf_line'], e * (1 + 2 * FEE), e + (w['cf_peak'] - e) * (1 - guard['give']))
                if px <= w['cf_line'] or now >= w['cf_until']:
                    w.update(cf_done=True, cf_px=px, cf_pnl=w['qty'] * px * (1 - FEE) - w['cost'], cf_why='보호선' if px <= w['cf_line'] else '6시간')
                    self._event({'event': '이전 규칙 추정 끝', 'key': key, 'px': px, 'cf_pnl': w['cf_pnl'], 'actual_pnl': w['actual_pnl'],
                                 'why': w['cf_why'], 'at': now})
                    changed = True
        if changed: self._save()

    def summary(self):
        v = list(self.s.values())
        done = [w for w in v if w.get('cf_done') and w.get('live')]
        n = len(done)
        a = sum(w['actual_pnl'] for w in done); b = sum(w['cf_pnl'] for w in done)
        return {'tracked': len(v), 'stepped': sum(1 for w in v if w.get('level', 0) >= 1),
                'sold': sum(1 for w in v if w.get('state') == 'sold'), 'pending': sum(1 for w in v if w.get('state') == 'sold' and not w.get('cf_done')),
                'hits': sum(1 for w in v if w.get('hit') or w.get('state') == 'hit'),
                'n': n, 'win_now': sum(w['actual_pnl'] > 0 for w in done), 'win_old': sum(w['cf_pnl'] > 0 for w in done),
                'pnl_now': a, 'pnl_old': b, 'first': min((w['at'] for w in v if w.get('live')), default=None)}


def render(watch, live=False):
    import datetime as dt
    s = watch.summary()
    if not live:
        since = dt.datetime.fromtimestamp(min(w['at'] for w in watch.s.values())).strftime('%m-%d %H:%M') + '부터' if watch.s else '아직 기록 없음'
        return (f"<div class=sub>계단 보호선 기록 모드(주문 없음, ATR×{ATR_MULT:g}·{HOLD_MIN}분 유지) · {since} · "
                f"추적 {s['tracked']} · 계단 인정 {s['stepped']} · 가상 매도 {s['hits']}</div>")
    since = dt.datetime.fromtimestamp(s['first']).strftime('%m-%d %H:%M') if s['first'] else '방금'
    if s['n']:
        d = s['pnl_now'] - s['pnl_old']
        col = '#1a7f37' if d > 0 else '#b42318' if d < 0 else 'inherit'
        cmp_ = (f"끝난 {s['n']}건 · 승률 <b>{s['win_now'] / s['n']:.0%}</b> (이전 조건 {s['win_old'] / s['n']:.0%}) · "
                f"손익 <b>{s['pnl_now']:+,.0f}원</b> (이전 조건 {s['pnl_old']:+,.0f}원) → "
                f"<b style='color:{col};font-size:17px'>효과 {d:+,.0f}원</b>")
    else:
        cmp_ = '아직 끝난 거래 없음 · 효과 계산 대기'
    return (f"<div style='margin:6px 0;padding:8px 12px;border:2px solid #2563eb;border-radius:8px;font-size:15px'>"
            f"<b>🧪 2+5 실험 중</b> (계단 보호선 실거래: 간격 ATR×{ATR_MULT:g}·고점 {HOLD_MIN}분 유지 시 계단 인정) · {since}부터 · {cmp_}"
            f"<div class=sub style='margin-top:3px'>계단 매도 {s['sold']}건" + (f"(이전 조건 추정 진행 중 {s['pending']})" if s['pending'] else '')
            + f" · 계단 인정 {s['stepped']} / 추적 {s['tracked']} · 이전 조건 = 이익보호+손절만 가정, 계단으로 판 뒤 최대 {CF_HOURS}시간 추적(순위 교체 매도는 미반영)"
            " · 효과가 없으면 설정 live.step_stop_enabled=false 로 원래대로</div></div>")
