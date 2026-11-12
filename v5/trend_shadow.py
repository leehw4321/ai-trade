"""주요 코인 추세 추종 모의 장부(사용자 지시 2026-11-12 '진행'). 주문과 무관, 가상 자금으로 기록·표시만.

규칙: BTC·ETH 각각 가상 50만 원. 업비트 일봉(09:00 KST 시작)이 새로 마감될 때마다 종가가 50일 단순이동평균보다 높으면 보유, 아니면 현금.
  바뀔 때만 그 시점 현재가로 사고팖, 편도 비용 0.1%(수수료+미끄러짐 가정).
비교: 같은 시점에 반반 사서 그대로 보유한 경우, 같은 기간 실거래 봇의 실현 손익.
근거: 2024-12~2026-11 재현에서 BTC 50일선 +9.6%(보유 −26.5%), ETH +90.7%(보유 −38.2%). 한 구간·변수 민감 → 입증 아님.
파일: data/trend_shadow.json (봇만 쓴다)
"""
import datetime as dt
import json
import os
import time
from pathlib import Path

MARKETS = ('KRW-BTC', 'KRW-ETH')
N, SLEEVE, COST = 50, 500000.0, 0.001
FILE = 'trend_shadow.json'
KST = dt.timezone(dt.timedelta(hours=9))


def _load(d):
    try:
        return json.loads((Path(d) / FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _save(d, s):
    p = Path(d) / FILE
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(s, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, p)


def signal(bars):
    """완성 일봉(시간순)으로 (마지막 봉 시작 시각, 종가 > N일선 여부, 종가, 이동평균). 모자라면 None."""
    if len(bars) < N:
        return None
    closes = [float(b['c']) for b in bars[-N:]]
    sma = sum(closes) / N
    return bars[-1]['t'], closes[-1] > sma, closes[-1], sma


def update(data_dir, daily, price, now=None):
    """daily(m) → 완성 일봉 목록, price(m) → 현재가. 새 일봉이 마감됐을 때만 일봉을 읽는다."""
    now = now or time.time()
    s = _load(data_dir)
    if s is None:
        s = {'since': now, 'start_px': {}, 'sleeves': {m: {'cash': SLEEVE, 'qty': 0.0, 'held': False} for m in MARKETS},
             'last_day': None, 'log': [], 'sig': {}}
    today = dt.datetime.fromtimestamp(now, KST)
    day_key = (today - dt.timedelta(hours=9)).strftime('%Y-%m-%d')   # 09:00 KST에 바뀌는 업비트 일봉 날짜
    changed = False
    for m in MARKETS:
        if m not in s['start_px']:
            s['start_px'][m] = price(m); changed = True
    if s['last_day'] != day_key:
        for m in MARKETS:
            sig = signal(daily(m))
            if sig is None:
                continue
            t, up, close, sma = sig
            s['sig'][m] = {'day': t[:10], 'close': close, 'sma': sma, 'up': up}
            sl = s['sleeves'][m]
            if up != sl['held']:
                px = price(m)
                if up:
                    sl['qty'] = sl['cash'] * (1 - COST) / px; sl['cash'] = 0.0
                else:
                    sl['cash'] = sl['qty'] * px * (1 - COST); sl['qty'] = 0.0
                sl['held'] = up
                s['log'].append({'at': now, 'm': m, 'side': '매수' if up else '매도', 'px': px, 'close': close, 'sma': sma})
        s['last_day'] = day_key
        changed = True
    if changed:
        _save(data_dir, s)
    return s


def render(data_dir, prices, bot_realized=None, now=None):
    s = _load(data_dir)
    if not s:
        return ''
    eq = sum(sl['cash'] + sl['qty'] * prices.get(m, 0) for m, sl in s['sleeves'].items())
    cap = SLEEVE * len(MARKETS)
    hold = sum(SLEEVE / s['start_px'][m] * prices.get(m, 0) for m in MARKETS if s['start_px'].get(m))
    parts = []
    for m in MARKETS:
        g = s['sig'].get(m) or {}; sl = s['sleeves'][m]
        gap = (g['close'] / g['sma'] - 1) if g.get('sma') else None
        parts.append(f"{m[4:]} {'보유' if sl['held'] else '현금'}" + (f"(50일선 대비 {gap:+.1%})" if gap is not None else ''))
    trades = len(s['log'])
    bot = f" │ 같은 기간 실거래 봇 실현 {bot_realized:+,.0f}원" if bot_realized is not None else ''
    col = '#1a7f37' if eq > cap else '#b42318' if eq < cap else 'inherit'
    return ("<div style='margin:6px 0;padding:8px 12px;border:2px solid #4338ca;border-radius:8px;font-size:15px'>"
            f"<b>🧪 주요 코인 추세 모의</b>(BTC·ETH 50일선, 가상 100만 원, 주문 무영향) · "
            f"{dt.datetime.fromtimestamp(s['since'], KST):%m-%d %H:%M}부터 · {' / '.join(parts)}<br>"
            f"모의 평가 <b style='color:{col}'>{eq:,.0f}원 ({eq / cap - 1:+.2%})</b> │ 그대로 보유였다면 {hold:,.0f}원 ({hold / cap - 1:+.2%}){bot}"
            f"<div class=sub style='margin-top:3px'>일봉(09시 마감) 종가가 50일 평균보다 높으면 보유, 낮으면 현금 · 매매 {trades}회 · 편도 비용 0.1% 가정 · "
            "2주 뒤 실거래 봇과 비교해 자금 배분 결정</div></div>")
