"""하락 흐름 조기 손절 (2026-11-27 사용자 지시: "손실 1% 이상 + 5분 2연속·15분·1시간 모두 하락 이 조건으로 수정해서 적용하고
2주간 비교 모니터링해서 리포트").

규칙: 봇이 산 종목(사용자 보호·계좌 인수분 제외)이 마감된 5분봉 종가 기준 매수가 대비 −1% 이하이고,
      마감된 봉 기준 5분 2연속 하락 · 15분 1연속 이상 하락 · 1시간 1연속 이상 하락이면 손절가를 기다리지 않고 시장가로 판다.
근거: tools/analysis_20261127/early_stop_sim.py, 11-11~11-27 왕복 175건 재계산에서 실제 대비 +15,380원(앞뒤 절반 모두 +).
      45개 조합 중 최고값이라 실제 효과는 더 작을 수 있다 → 발동 기록을 남겨 2주 뒤 비교한다.

판단은 종목별로 새 5분봉이 마감됐을 때 한 번만 한다(같은 봉으로 반복 매도 시도 없음).
발동·매도 결과는 data/momentum_exit.jsonl에 남기고, 보고서(tools/analysis_20261127/momentum_exit_report.py)가
'안 팔았으면 어땠을지'(원래 손절가·이익보호로 끝났을 결과)를 업비트 봉으로 다시 계산한다.
"""
import datetime as dt
import json
import time
from pathlib import Path

import bar_streak

DEFAULTS = {'momentum_exit_enabled': False, 'momentum_exit_loss_pct': 0.01,
            'momentum_exit_m5': 2, 'momentum_exit_m15': 1, 'momentum_exit_m60': 1}
REASON = '하락 흐름 조기 손절'


def conf(cfg):
    return {k: cfg.get(k, v) for k, v in DEFAULTS.items()}


def signal(entry, price, s5, s15, s60, c):
    """규칙에 맞으면 사유 문자열, 아니면 None. 연속 수를 모르면(None) 팔지 않는다."""
    if not (isinstance(entry, (int, float)) and entry > 0 and isinstance(price, (int, float)) and price > 0):
        return None
    if not all(isinstance(x, int) for x in (s5, s15, s60)):
        return None
    loss = price / entry - 1
    if loss > -float(c['momentum_exit_loss_pct']):
        return None
    if s5 > -int(c['momentum_exit_m5']) or s15 > -int(c['momentum_exit_m15']) or s60 > -int(c['momentum_exit_m60']):
        return None
    return f'{REASON}(매수가 대비 {loss:+.2%}, 5분 {-s5}연속·15분 {-s15}연속·1시간 {-s60}연속 하락)'


class Watch:
    def __init__(self, data_dir, fetch=None, clock=time.time):
        self.path = Path(data_dir) / 'momentum_exit.jsonl'
        if fetch is None:
            import upbit
            fetch = upbit.completed_candles
        self.fetch, self.clock = fetch, clock
        self.seen = {}   # 종목 → 마지막으로 판단한 5분봉 시작 시각

    def log(self, row):
        try:
            with self.path.open('a', encoding='utf-8') as f:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
        except OSError:
            pass

    def check(self, positions, cfg, is_protected, sell):
        """positions: 장부 positions. sell(market, why, price) → (성공 여부, 체결 정보 dict|None).
        반환: 이번에 발동한 종목 목록."""
        c = conf(cfg)
        if not c['momentum_exit_enabled']:
            return []
        fired = []
        for m, pos in list(positions.items()):
            if is_protected(m) or pos.get('inherited') or not pos.get('qty', 0) > 0:
                continue
            entry = pos.get('entry')
            if not isinstance(entry, (int, float)) or entry <= 0:
                continue
            try:
                b5 = self.fetch(m, 5, count=12)
            except Exception:
                continue
            if len(b5) < 3:
                continue
            last = b5[-1]
            if self.seen.get(m) == last['t']:
                continue
            bar_end = dt.datetime.fromisoformat(last['t']) + dt.timedelta(minutes=5)
            opened = _ts(pos.get('time'))
            if opened is None or bar_end.timestamp() <= opened + 300:   # 매수 뒤 처음 마감된 봉부터
                self.seen[m] = last['t']
                continue
            self.seen[m] = last['t']
            s5 = bar_streak.streak([b['c'] for b in b5])
            if s5 > -int(c['momentum_exit_m5']) or last['c'] / entry - 1 > -float(c['momentum_exit_loss_pct']):
                continue   # 대부분 여기서 끝: 15분·1시간 봉은 필요할 때만 받는다
            try:
                s15 = bar_streak.streak([b['c'] for b in self.fetch(m, 15, count=12)])
                s60 = bar_streak.streak([b['c'] for b in self.fetch(m, 60, count=12)])
            except Exception:
                continue
            why = signal(entry, last['c'], s5, s15, s60, c)
            if not why:
                continue
            row = {'time': dt.datetime.fromtimestamp(self.clock(), dt.timezone(dt.timedelta(hours=9))).isoformat(timespec='seconds'),
                   'event': 'fire', 'market': m, 'opened': pos.get('time'), 'entry': entry, 'cost': pos.get('cost'), 'qty': pos.get('qty'),
                   'stop': pos.get('stop'), 'bar': last['t'], 'bar_close': last['c'], 's5': s5, 's15': s15, 's60': s60, 'why': why}
            ok, fill = sell(m, why, last['c'])
            row.update(sold=bool(ok), fill=fill)
            self.log(row)
            if ok:
                fired.append(m)
        return fired


def _ts(v):
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return dt.datetime.fromisoformat(str(v)).timestamp()
    except ValueError:
        return None
