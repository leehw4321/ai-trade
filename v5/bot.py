"""자동거래 v5 1판: C안 모의 운영. 실제 주문 기능 없음.

python bot.py          모의 운영 (판단 봉 마감마다 순환 판단, 30초마다 실시간 손절 확인)
python bot.py --check  지금 시세로 순위·계획만 출력 (장부 변경 없음)
"""
import argparse
import datetime as dt
import html
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

import strategy as st
import upbit
from session_guard import SessionGuard, SessionLost, notify
from live import Live
from paper import Ledger, KST, now_iso

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
STOP = ROOT / '중지.txt'
STOP_ASCII = ROOT / 'stop.txt'  # 바탕화면 중지 파일(.bat)은 한글 파일 이름을 쓰지 못해 이 이름을 쓴다
LOCK = ROOT / 'bot.lock'
H4, H1 = 4 * 3600, 3600
SETTLE = 20  # 봉 마감 후 업비트 반영을 기다리는 초


def load_params():
    f = ROOT / 'config.json'
    cfg = json.loads(f.read_text(encoding='utf-8')) if f.exists() else {}
    return st.Params.from_dict(cfg.get('strategy', {})), cfg


def boundary(ts, period):
    return int(ts // period * period)


def utc(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc)


def ranking_rows(ranking, held, warned, p, esc=html.escape):
    """순위표 행. 바탕색: 보유=하늘색, 경보=붉은 파스텔, 투자 대상(매수 후보)=연두, 관찰=연노랑. '구분' 칸에도 적는다.
    매수 후보 = 현재 매수 규칙(상위 entry_top 안, 상승률 0 초과 chase_rs_cap 미만, 보유·경보 아님). 관찰 = 5위(유지 순위) 안 정상 종목."""
    out, want, scan, picked = [], p.entry_want or p.entry_top, p.entry_scan or p.entry_top, 0
    for r in ranking:
        m, rs, rank = r['market'], r['rs'], r['rank']
        if m in warned:
            cls, tag = 'rk-alert', '보유·경보' if m in held else '경보'
        elif m in held:
            cls, tag = 'rk-held', '보유'
        elif rank <= scan and rs >= p.chase_rs_cap > 0:
            cls, tag = '', '과열'
        elif rank <= scan and rs > 0 and picked < want:
            cls, tag, picked = 'rk-target', '투자 대상(매수 후보)', picked + 1
        elif rank <= p.M and rs > 0:
            cls, tag = 'rk-watch', '관찰'
        else:
            cls, tag = '', ''
        out.append(f"<tr class='{cls}'><td class=n>{rank}</td><td>{esc(m[4:])}</td>"
                   f"<td class='n {'up' if rs >= 0 else 'dn'}'>{rs * 100:+.2f}%</td><td>{tag}</td></tr>")
    return ''.join(out)


class Bot:
    def __init__(self):
        self.p, self.cfg = load_params()
        # P10: 첫 실거래 단계 규모(계좌 10%), ALL: 사용자 지시로 계좌 전체를 시드로 한 장부
        self.ledgers = {'P10': Ledger(DATA / 'P10'), 'ALL': Ledger(DATA / 'ALL')}
        self.ledger = self.ledgers['P10']
        self.ranking = []
        self.markets = {}
        self.error = ''
        self.price_cache = {}    # 종목: (가격, 조회 시각). 누락 시 마지막 관측값을 쓰되 표시한다
        self.price_missing = []
        self.real = {}           # 실제 업비트 계좌 (조회 전용)
        self.real_error = ''
        self.rs_all = {}         # 종목: (순위 또는 None, 상대강도)
        self.live = Live(DATA / 'live', self.cfg.get('live', {}), self.p)

    def log(self, msg):
        self.ledger.event(msg)
        print(f'[{now_iso()}] {msg}', flush=True)

    def align_timeframe(self, now):
        """Preserve holdings on a timeframe change and wait for the next new close."""
        if type(self.p.tf) is not int or self.p.tf not in {1, 3, 5, 10, 15, 30, 60, 240}:
            raise ValueError('지원하지 않는 판단 분봉입니다.')
        if type(self.p.stop_seconds) is not int or self.p.stop_seconds <= 0:
            raise ValueError('손절 확인 간격은 양의 정수 초여야 합니다.')
        for lg in self.ledgers.values():
            if lg.d.get('timeframe_minutes') == self.p.tf:
                continue
            lg.d['last_4h'] = boundary(now - SETTLE, self.p.tf * 60)
            lg.d['timeframe_minutes'] = self.p.tf
            lg.event(f'판단 주기 {self.p.tf}분 적용: 기존 수량·손절가 보존, 지난 신호 소급 처리 없음')
            lg.save()

    # ---------- T0 ----------
    def init_t0(self):
        rows = upbit.accounts()
        krw = sum(float(r['balance']) + float(r['locked']) for r in rows if r['currency'] == 'KRW')
        listed = upbit.krw_markets()
        coins = {f"KRW-{r['currency']}": float(r['balance']) + float(r['locked']) for r in rows if r['currency'] != 'KRW'}
        priced = [m for m in coins if m in listed]
        tick = upbit.tickers(priced) if priced else {}
        basket = {m: q for m, q in coins.items() if m in tick}
        unpriced = sorted(m for m in coins if m not in tick)
        E = krw + sum(q * tick[m]['trade_price'] for m, q in basket.items())
        f = self.cfg.get('allocation_fraction', 0.10)
        if not self.ledgers['P10'].ready:
            self.ledgers['P10'].start(E, f, basket, krw, unpriced)
        if not self.ledgers['ALL'].ready:
            self.ledgers['ALL'].start(E, 1.0, basket, krw, unpriced,
                                      label='단독 전략 실험(계좌 평가액만큼 가상 현금, 실제 계좌 인수 아님)')
        if unpriced:
            self.log('평가 가격이 없는 보유자산은 E에서 제외: ' + ', '.join(unpriced))

    # ---------- 판단 ----------
    def candidates(self):
        self.markets = upbit.krw_markets()
        tick = upbit.tickers(self.markets)
        pool = [m for m in tick if m not in self.p.exclude]
        pool.sort(key=lambda m: -tick[m]['acc_trade_price_24h'])
        return pool[:self.p.universe], tick

    def fetch_bars(self, markets, end):
        out = {}
        for m in markets:
            bars = upbit.completed_candles(m, self.p.tf, 200)
            # 방금 마감된 봉이 있어야 판단에 쓴다 (준비 자료 부족이면 제외)
            if bars and dt.datetime.fromisoformat(bars[-1]['t']).timestamp() + self.p.tf * 60 == end:
                out[m] = bars
            time.sleep(0.12)
        return out

    def rebalance(self, end):
        universe, tick = self.candidates()
        held = [m for lg in self.ledgers.values() for m in lg.d['positions']]
        real = list(self.real.get('coins', {}))
        bars = self.fetch_bars(list(dict.fromkeys(universe + held + real)), end)
        uni = set(universe)
        # 신규 순위는 거래대금 대상 안에서만 계산한다. 대상 밖 보유는 순위를 차지하지 않는다
        ranked = st.rank({m: b for m, b in bars.items() if m in uni}, self.p)
        self.rs_all = {r['market']: (r['rank'], r['rs']) for r in ranked}
        for m in bars:
            if m not in uni:
                self.rs_all[m] = (None, st.relative_strength(bars[m], self.p.L))
        entry_ok = {m for m in universe if m in bars and not self.markets.get(m, {}).get('warning')}
        failed = []
        for name, lg in self.ledgers.items():
            if (lg.d['last_4h'] or 0) >= end:
                continue  # 이 마감은 이미 처리했다
            try:
                self.apply(name, lg, ranked, entry_ok, end, bars, uni)
            except Exception as ex:
                failed.append(f'{name}: {type(ex).__name__}: {ex}')
        top = ', '.join(f"{r['market'][4:]}{r['rs']:+.1%}" for r in ranked[:5])
        self.log(f'{self.p.tf}분 판단 완료 · 상위: {top}')
        if failed:
            raise RuntimeError('장부 처리 실패, 다음 주기에 그 장부만 재시도: ' + '; '.join(failed))

    def apply(self, name, lg, ranked, entry_ok, end, bars, uni):
        d = lg.d
        held = list(d['positions'])
        now = time.time()
        d['banned'] = {m: t for m, t in d['banned'].items() if t > now}
        sells, buys, unknown = st.plan(ranked, held, set(d['banned']), entry_ok, self.p)
        d['ranking'] = ranked[:15]
        d['ranking_time'] = utc(end).astimezone(KST).isoformat(timespec='minutes')
        log = lambda msg: (lg.event(msg), print(f'[{now_iso()}] {name} {msg}', flush=True))
        for m in unknown:
            why = '거래대금 상위 대상 밖' if m in bars and m not in uni else '마감 봉 자료 없음'
            log(f'{m}: {why} · 순위 판단 보류하고 유지 (누락·이탈 처리 17번 미합의)')
        books = upbit.orderbooks([m for m, _ in sells] + buys) if sells or buys else {}
        for m, reason in sells:
            r = lg.sell(m, books[m]['bid'], reason, self.p)
            log(f"{m} {reason} 매도 {r['amount']:,}원 (손익 {r['pnl']:+,}원, {r['pnl_pct']:+.2f}%)")
        atr = {r['market']: r['atr'] for r in ranked}
        for m in buys:
            prices = self.prices(lg)
            eq, inv = lg.equity(prices), lg.invested(prices)
            qty_fn = lambda P, S: st.size(eq, inv, d['cash'], P, S, self.p)
            r = lg.buy(m, books[m]['ask'], atr[m], qty_fn, self.p)
            if r:
                log(f"{m} 매수 {r['amount']:,}원 · 손절가 {r['stop']:,.4g}")
            else:
                d['skipped'] = d.get('skipped', 0) + 1
                log(f'{m} 매수 건너뜀: 계산된 주문이 최소주문 5,000원 미달')
        d['signals'] = d.get('signals', 0) + len(buys)
        d['last_4h'] = end
        lg.save()

    def stop_check(self, lg, live):
        """실시간 손절: 현재 체결가가 손절가 이하이면 최우선 매수호가로 매도 (사용자 의견으로 봉 마감 대기 폐지)."""
        d = lg.d
        hit = [m for m, pos in d['positions'].items() if m in live and live[m] <= pos['stop']]
        if not hit:
            return
        books = upbit.orderbooks(hit)
        for m in hit:
            r = lg.sell(m, books[m]['bid'], '손절', self.p, banned_until=time.time() + self.p.reentry_hours * 3600)
            lg.event(f"{m} 손절 매도 {r['amount']:,}원 (손익 {r['pnl']:+,}원) · 24시간 재진입 금지")

    def prices(self, lg=None):
        """최신 체결가. 응답에 빠진 종목은 마지막 관측값을 쓰고 price_missing에 기록한다."""
        ms = list(dict.fromkeys([m for x in self.ledgers.values() for m in x.d['positions']]
                                + list(self.ledger.d['H']['basket']) + list(self.real.get('coins', {}))
                                + list(self.live.d['positions'])))
        fresh = upbit.tickers(ms) if ms else {}
        now = time.time()
        for m, t in fresh.items():
            self.price_cache[m] = (t['trade_price'], now)
        self.price_missing = [m for m in ms if m not in fresh]
        return {m: v[0] for m, v in self.price_cache.items()}

    def refresh_real(self):
        """실제 업비트 계좌 조회 (주문 없음)."""
        rows = upbit.accounts()
        listed = self.markets or upbit.krw_markets()
        coins, unpriced = {}, []
        for r in rows:
            if r['currency'] == 'KRW':
                continue
            m = 'KRW-' + r['currency']
            q = float(r['balance']) + float(r['locked'])
            if m in listed:
                coins[m] = {'qty': q, 'avg': float(r['avg_buy_price']), 'locked': float(r['locked'])}
            else:
                unpriced.append(r['currency'])
        krw = sum(float(r['balance']) + float(r['locked']) for r in rows if r['currency'] == 'KRW')
        self.real = {'time': now_iso(), 'krw': krw, 'coins': coins, 'unpriced': unpriced}
        self.real_error = ''

    def refresh_display(self, next_close):
        """Refresh exchange balances independently of strategy/ledger snapshots."""
        try:
            self.refresh_real()
        except Exception as ex:
            self.real_error = f'{type(ex).__name__}: {ex}'
            self.log('실제 계좌 조회 실패 (최종 성공 자료 유지): ' + self.real_error)
        self.write_status(self.prices(), next_close)

    def live_block(self, prices):
        e, L, c = html.escape, self.live.d, self.live.cfg
        if not c['enabled']:
            return ''
        loss = self.live.raw_loss(prices) - L.get('risk_loss_offset', 0.)
        rows = ''.join(
            f"<tr{' class=dust' if p['qty'] * prices.get(m, 0) < 5000 else ''}><td>{e(m[4:])}</td><td class=n>{p['qty']:,.8g}</td><td class=n>{p['cost']:,.0f}</td>"
            f"<td class=n>{p['qty'] * prices.get(m, 0):,.0f}</td><td class=n>{p['stop']:,.4g}</td></tr>"
            for m, p in L['positions'].items()) or '<tr><td colspan=5 class=muted>위임된 실거래 보유 없음</td></tr>'
        orders = []
        f = DATA / 'live' / 'orders.jsonl'
        if f.exists():
            orders = [json.loads(x) for x in f.read_text(encoding='utf-8').splitlines()[-8:] if x.strip()][::-1]
        ol = ''.join(f"<li><span class=t>{e(o['time'][5:16].replace('T', ' '))}</span>"
                     f"{e(o.get('result') or o.get('reason') or (o.get('side', '') + ' ' + o.get('market', '')[4:] + ' ' + format(o.get('funds', 0), ',.0f') + '원'))}</li>"
                     for o in orders) or '<li class=muted>아직 새 체결 없음 · 현재 전략의 새 주문안을 대기 중</li>'
        halt = f"<div class=warn>신규 매수 중단: {e(L['halted'])}</div>" if L['halted'] else ''
        adoption = '<div class=sub>기존 보유분 포함 계좌 인수 운용 · 과거 장부 복원이 아닌 새 기준 · 손익 한도는 인수 후 변동 기준 · 손절가는 인수 시 재산정</div>' if L.get('account_adoption') else ''
        return f"""<section><h2 class=lg>실거래 (봇 자동 실행)</h2>{halt}{adoption}
<div class=sub>한도: 1건 {c['max_order_krw']:,}원 · 누적 매수 {L['bought_krw']:,.0f} / {c['max_total_buy_krw']:,}원 · 1회 매도 {c['max_sell_krw_per_plan']:,}원</div>
<div class=sub><b>손실 중단선</b>: 누적 손실 <b class="{'dn' if loss > 0 else 'up'}">{max(loss, 0):,.0f}원</b> / 중단선 {self.live.loss_limit(prices):,.0f}원{(' (총자산의 ' + format(float(c.get('loss_stop_pct') or 0), '.2%') + ')') if float(c.get('loss_stop_pct') or 0) > 0 else ''} · 남은 여유 {max(self.live.loss_limit(prices) - loss, 0):,.0f}원 · 현재 손익 {-loss:+,.0f}원{' · 재개: python tools/resume_trading.py' if L['halted'] else ''}</div>
<div class=cols><div><h3>봇 실거래 보유 <label class=sub style="font-weight:400"><input type=checkbox id=hideDust2 onchange="document.documentElement.classList.toggle('hd',this.checked);try{{localStorage.setItem('hideDust',this.checked?'1':'0')}}catch(e){{}}"> 소액(5,000원 미만) 숨기기</label></h3><table><tr><th>코인</th><th class=n>수량</th><th class=n>매수금액</th><th class=n>평가액</th><th class=n>손절가</th></tr>{rows}</table></div>
<div><h3>실거래 기록</h3><ul>{ol}</ul></div></div><script>document.getElementById('hideDust2').checked=document.documentElement.classList.contains('hd')</script></section>"""

    def real_block(self, prices):
        if (DATA / 'account_live.html').exists():
            return '<section><iframe title="실제 업비트 실시간 계좌" src="account_live.html" style="width:100%;height:620px;border:0"></iframe></section>'
        e, R = html.escape, self.real
        if not R:
            return '<section><h2 class=lg>실제 업비트 계좌</h2><p class=muted>계좌 조회 전입니다.</p></section>'
        vals = {m: c['qty'] * prices[m] for m, c in R['coins'].items() if m in prices}
        total = R['krw'] + sum(vals.values())
        rows = []
        for m, c in sorted(R['coins'].items(), key=lambda x: -vals.get(x[0], 0)):
            px = prices.get(m)
            v = vals.get(m, 0)
            pnl = (px / c['avg'] - 1) * 100 if px and c['avg'] else None
            rank, rs = self.rs_all.get(m, (None, None))
            rank_txt = f'{rank}위' if rank else ('상위 30 밖' if m in self.rs_all else '-')
            rows.append(
                f"<tr><td>{e(m[4:])}</td><td class=n>{c['qty']:,.8g}</td><td class=n>{c['avg']:,.4g}</td>"
                f"<td class=n>{(format(px, ',.4g') if px else '-')}</td><td class=n>{v:,.0f}</td>"
                f"<td class=n>{(v / total * 100 if total else 0):.1f}%</td>"
                f"<td class='n {'up' if (pnl or 0) >= 0 else 'dn'}'>{(format(pnl, '+.2f') + '%') if pnl is not None else '-'}</td>"
                f"<td class=n>{rank_txt}</td><td class='n {'up' if (rs or 0) >= 0 else 'dn'}'>{(format(rs * 100, '+.2f') + '%') if rs is not None else '-'}</td></tr>")
        rows.append(f"<tr><td>원화</td><td colspan=3></td><td class=n>{R['krw']:,.0f}</td>"
                    f"<td class=n>{(R['krw'] / total * 100 if total else 0):.1f}%</td><td colspan=3></td></tr>")
        extra = f" · 시세 없는 잔고: {e(', '.join(R['unpriced']))}" if R['unpriced'] else ''
        p = self.p
        warning = f'<div class=warn>계좌 갱신 실패 · 마지막 성공 자료입니다: {e(self.real_error)}</div>' if self.real_error else ''
        return f"""<section><h2 class=lg>실제 업비트 계좌 · 총 {total:,.0f}원</h2>{warning}
<div class=sub>조회 전용 · {e(R['time'][5:16].replace('T', ' '))}{extra} · 순위와 상대강도는 마지막 판단 기준(거래대금 상위 {p.universe}, 최근 {p.L * p.tf // 60}시간)</div>
<table><tr><th>코인</th><th class=n>수량</th><th class=n>평균단가</th><th class=n>현재가</th><th class=n>평가액</th><th class=n>비중</th><th class=n>매입 대비</th><th class=n>봇 순위</th><th class=n>상대강도</th></tr>{''.join(rows)}</table></section>"""

    # ---------- 현황 ----------
    def block(self, title, lg, prices):
        d, e = lg.d, html.escape
        P, H = lg.equity(prices), lg.h_value(prices)
        pr = P / d['A0'] - 1
        hr = None if H is None or self.price_missing else H / d['H']['value0'] - 1
        sig, skip = d.get('signals', 0), d.get('skipped', 0)
        pos_rows = ''.join(
            f"<tr><td>{e(m[4:])}</td><td class=n>{p['qty']*prices.get(m, p['entry']):,.0f}</td>"
            f"<td class=n>{p['entry']:,.4g}</td><td class=n>{prices.get(m, 0):,.4g}</td><td class=n>{p['stop']:,.4g}</td>"
            f"<td class='n {'up' if prices.get(m, 0) >= p['entry'] else 'dn'}'>{(prices.get(m, p['entry'])/p['entry']-1)*100:+.2f}%</td></tr>"
            for m, p in d['positions'].items()) or '<tr><td colspan=6 class=muted>보유 종목 없음</td></tr>'
        trades = lg.read_jsonl('trades.jsonl', 10)[::-1]
        tr_rows = ''.join(
            f"<tr><td>{e(t['time'][5:16].replace('T', ' '))}</td><td>{e(t['side'])}</td><td>{e(t['market'][4:])}</td>"
            f"<td class=n>{t['amount']:,}</td><td>{e(t.get('reason', ''))}</td>"
            f"<td class='n {'up' if t.get('pnl', 0) >= 0 else 'dn'}'>{(format(t['pnl'], '+,') + '원') if 'pnl' in t else ''}</td></tr>"
            for t in trades) or '<tr><td colspan=6 class=muted>아직 거래 없음</td></tr>'
        return f"""<section><h2 class=lg>{e(title)}</h2>
<div class=tiles>
<div class=tile><h2>전략 순자산 (모의)</h2><b>{P:,.0f}원</b><span class="{'up' if pr >= 0 else 'dn'}">{pr*100:+.2f}%</span> <span class=muted>배정 {d['A0']:,}원</span></div>
<div class=tile><h2>전략 − 계좌 그대로 보유</h2>{(f'<b class="{"up" if pr >= hr else "dn"}">{(pr-hr)*100:+.2f}%p</b><span class=muted>보유(H) {hr*100:+.2f}%</span>') if hr is not None else ('<b>비교 보류</b><span class=warn>시세 누락: ' + e(', '.join(m[4:] for m in self.price_missing)) + '</span>')}</div>
<div class=tile><h2>현금 · 투입</h2><b>{d['cash']:,.0f}원</b><span class=muted>투입 {lg.invested(prices):,.0f}원 · 최대 {self.p.total_cap:.0%}</span></div>
<div class=tile><h2>매수 신호 · 최소주문 미달</h2><b>{sig} · {skip}</b><span class=muted>건너뜀 비율 {(skip/sig*100 if sig else 0):.0f}%</span></div>
</div>
<div class=cols><div><h3>보유 종목</h3><table><tr><th>종목</th><th class=n>평가액</th><th class=n>진입가</th><th class=n>현재가</th><th class=n>손절가</th><th class=n>손익</th></tr>{pos_rows}</table></div>
<div><h3>최근 거래</h3><table><tr><th>시각</th><th>구분</th><th>종목</th><th class=n>금액</th><th>사유</th><th class=n>손익</th></tr>{tr_rows}</table></div></div></section>"""

    def held_markets(self, prices):
        """평가 5,000원 이상 보유 종목(실제 계좌 + 봇 관리 장부)."""
        held = {m for m, c in ((self.real or {}).get('coins') or {}).items() if c['qty'] * prices.get(m, 0) >= 5000}
        held |= {m for m, pos in self.live.d['positions'].items() if pos['qty'] * prices.get(m, 0) >= 5000}
        return held

    def write_status(self, prices, next_4h):
        d, e, p = self.ledger.d, html.escape, self.p
        if not self.markets:    # 재시작 직후 첫 판단 전에는 비어 있다
            try:
                self.markets = upbit.krw_markets()
            except Exception:
                pass
        warned = {m for m, info in (self.markets or {}).items() if info.get('warning')}
        rk_rows = ranking_rows(d.get('ranking', []), self.held_markets(prices), warned, p) or '<tr><td colspan=4 class=muted>첫 판단 전</td></tr>'
        events = sorted([dict(x, who=n) for n, lg in self.ledgers.items() for x in lg.read_jsonl('events.jsonl', 12)],
                        key=lambda x: x['time'])[-14:][::-1]
        ev = ''.join(f"<li><span class=t>{e(x['time'][5:16].replace('T', ' '))} {e(x['who'])}</span>{e(x['msg'])}</li>" for x in events)
        # 실제 거래·계좌(위)와 모의(가상) 장부(아래, 접어 둠)를 구분한다. 모의 장부는 실제 거래가 아니며 옛 규칙으로 20:45에 진입한 뒤 거의 멈춰 있다.
        paper = self.block(f"모의 장부 P10 · 계좌의 {d['fraction']:.0%} 가상 배정 ({d['A0']:,}원)", self.ledgers['P10'], prices) + \
            self.block(f"모의 장부 ALL · 계좌 평가액만큼 가상 현금 ({self.ledgers['ALL'].d['A0']:,}원, 실제 계좌 인수 아님)", self.ledgers['ALL'], prices)
        blocks = self.real_block(prices) + self.live_block(prices) + \
            '<details><summary class=lg>모의(가상) 장부 — 실제 거래가 아닙니다 · 전략 실험용 (눌러서 펼치기)</summary>' + paper + '</details>'
        page = f"""<!doctype html><html lang=ko><meta charset=utf-8><meta http-equiv=refresh content=30>
<meta name=viewport content="width=device-width,initial-scale=1"><title>자동거래 v5</title>
<script>try{{if(localStorage.getItem('hideDust')!=='0')document.documentElement.classList.add('hd');}}catch(e){{document.documentElement.classList.add('hd');}}</script><style>
html.hd .dust{{display:none}}
details>summary{{cursor:pointer;color:var(--muted);padding:8px 2px}}details[open]>summary{{margin-bottom:8px}}
tr.rk-held td{{background:#dff0fa}} tr.rk-alert td{{background:#fbe0e0}} tr.rk-target td{{background:#e0f3dc}} tr.rk-watch td{{background:#fff5d1}} .chip{{padding:1px 8px;border-radius:4px}} .chip-held{{background:#dff0fa;color:#17212b}} .chip-alert{{background:#fbe0e0;color:#17212b}} .chip-target{{background:#e0f3dc;color:#17212b}} .chip-watch{{background:#fff5d1;color:#17212b}}
@media (prefers-color-scheme:dark){{tr.rk-held td{{background:#1d3647}} tr.rk-alert td{{background:#4a2a2d}} tr.rk-target td{{background:#22402a}} tr.rk-watch td{{background:#47401f}} .chip-held{{background:#1d3647;color:#e3e8eb}} .chip-alert{{background:#4a2a2d;color:#e3e8eb}} .chip-target{{background:#22402a;color:#e3e8eb}} .chip-watch{{background:#47401f;color:#e3e8eb}}}}
:root{{--bg:#f4f6f7;--card:#fff;--ink:#17212b;--muted:#5d6b77;--line:#dde3e7;--acc:#0f6e5c;--up:#c8322f;--dn:#2457b3}}
@media (prefers-color-scheme:dark){{:root{{--bg:#11171c;--card:#182128;--ink:#e3e8eb;--muted:#9aa6af;--line:#2c3740;--acc:#4fc2a6;--up:#ff6b66;--dn:#6e9bff}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 "Malgun Gothic",sans-serif;padding:24px max(16px,3vw)}}
main{{max-width:1180px;margin:0 auto;display:flex;flex-direction:column;gap:18px}}
h1{{margin:0;font-size:22px}} h2{{margin:0 0 6px;font-size:14px;color:var(--muted);font-weight:600}} h2.lg{{font-size:16px;color:var(--acc)}} h3{{font-size:14px;margin:12px 0 4px}}
.sub{{color:var(--muted);font-size:13px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}}
.tile{{border:1px solid var(--line);padding:12px 14px}} .tile b{{display:block;font-size:21px;font-variant-numeric:tabular-nums}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:18px}} .grid{{display:grid;grid-template-columns:1fr 2fr;gap:18px}}
@media(max-width:820px){{.cols,.grid{{grid-template-columns:1fr}}}}
section{{background:var(--card);border:1px solid var(--line);padding:14px 16px;overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:14px}} td,th{{padding:5px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}
th{{color:var(--muted);font-weight:500;font-size:12.5px}} .n{{text-align:right;font-variant-numeric:tabular-nums}}
.up{{color:var(--up)}} .dn{{color:var(--dn)}} .muted{{color:var(--muted)}} .warn{{color:var(--up)}}
ul{{margin:0;padding:0;list-style:none;font-size:13.5px}} li{{padding:4px 0;border-bottom:1px solid var(--line)}} .t{{color:var(--muted);margin-right:10px;font-variant-numeric:tabular-nums}}
</style><main>
<div><h1>자동거래 v5 · C안</h1>
<div class=sub>모의 장부 + 합의 주문안 실거래 · 갱신 {e(now_iso()[5:16].replace('T', ' '))} · 다음 판단 {e(utc(next_4h).astimezone(KST).strftime('%m-%d %H:%M'))} · 시작 {e(d['t0'][5:16].replace('T', ' '))} · 계좌 T0 평가액 {d['H']['value0']:,.0f}원</div>
{f'<div class=warn>최근 오류: {e(self.error)}</div>' if self.error else ''}</div>
{blocks}
<div class=grid>
<section><h2>상대강도 순위 ({e(d.get('ranking_time', '-'))})</h2><table><tr><th class=n>#</th><th>종목</th><th class=n>{p.L}봉 수익률</th><th>구분</th></tr>{rk_rows}</table><div class=sub style="margin-top:8px"><span class='chip chip-held'>보유</span> <span class='chip chip-alert'>경보 종목(진입 불가)</span> <span class='chip chip-target'>투자 대상(매수 후보)</span> <span class='chip chip-watch'>관찰</span> · 매수 후보는 상위 {p.entry_top}위 안·상승률 0~{p.chase_rs_cap:.0%}·보유/경보 아님</div></section>
<section><h2>운영 기록</h2><ul>{ev}</ul></section>
</div>
<div class=sub>규칙: 거래대금 상위 {p.universe} · 판단 {p.tf}분봉 · 상대강도 {p.L}봉({p.L*p.tf//60}시간) · 실시간 손절 {p.stop_seconds}초 · 유지 {p.M}위 · 신규 상위 {p.entry_top}위 · 최대 {p.slots}종목 · 손절 {p.k:g}×ATR14 · 거래당 위험 {p.risk:.1%} · 편도 비용 BTC·ETH {p.cost_major:.2%} / 기타 {p.cost_other:.2%} + 수수료 {p.fee:.2%} · 경보 종목 신규 진입 제외. L·M·k는 임시값.</div>
</main></html>"""
        (DATA / 'status.html').write_text(page, encoding='utf-8')

    # ---------- 반복 ----------
    def run(self):
        subprocess.Popen([sys.executable, str(ROOT / 'account_sync.py')],
                         cwd=str(ROOT), creationflags=subprocess.CREATE_NO_WINDOW)
        if not all(lg.ready for lg in self.ledgers.values()):
            self.init_t0()
        self.align_timeframe(time.time())
        self.log('봇 실행 시작 (실거래 활성 · 기존 보유분 위임 관리 포함)' if self.live.cfg['enabled'] else '봇 실행 시작 (모의 운영)')
        last_snap = last_stop = last_live = last_display = 0
        while not (STOP.exists() or STOP_ASCII.exists()):
            guard = getattr(self, 'session_guard', None)
            if guard and guard.lost.is_set():
                self.log('실행 연결 종료: ' + guard.reason)
                break
            now = time.time()
            try:
                TF = self.p.tf * 60
                end4 = boundary(now - SETTLE, TF)
                held = list({m for lg in self.ledgers.values() for m in lg.d['positions']} | set(self.live.d['positions']))
                if held and now - last_stop >= self.p.stop_seconds:
                    live = {m: t['trade_price'] for m, t in upbit.tickers(held).items()}
                    self.live.trail_check()
                    self.live.stop_check(live)
                    self.live.check_resume_request(live)
                    self.live.check_loss(live)
                    for lg in self.ledgers.values():
                        self.stop_check(lg, live)
                    last_stop = now
                if now - last_display >= 30:
                    self.refresh_display(end4 + TF + SETTLE)
                    last_display = now
                if now - last_live >= 30:
                    self.live.poll(self.markets or upbit.krw_markets())
                    last_live = now
                    self.refresh_display(end4 + TF + SETTLE)
                    last_display = now
                for lg in self.ledgers.values():
                    if not lg.d['last_4h']:  # 새 장부는 이미 지난 봉으로 매매하지 않는다
                        lg.d['last_4h'] = end4
                        lg.save()
                if any((lg.d['last_4h'] or 0) < end4 for lg in self.ledgers.values()):
                    self.rebalance(end4)
                if now - last_snap >= 300:
                    try:
                        self.refresh_real()
                    except Exception as ex:
                        self.log(f'실제 계좌 조회 실패 (표시만 영향): {ex}')
                    prices = self.prices()
                    for lg in self.ledgers.values():
                        lg.snapshot(prices)
                    self.write_status(prices, end4 + TF + SETTLE)
                    last_snap = now
                self.error = ''
            except Exception as ex:
                self.error = f'{type(ex).__name__}: {ex}'
                self.log('오류 (다음 주기에 재시도): ' + self.error)
                traceback.print_exc()
                last_snap = 0 if now - last_snap >= 300 else last_snap
                time.sleep(30)
            time.sleep(10)
        self.log('중지.txt 확인 · 봇 종료')

    def check(self):
        universe, tick = self.candidates()
        end = boundary(time.time() - SETTLE, self.p.tf * 60)
        bars = self.fetch_bars(universe, end)
        ranked = st.rank(bars, self.p)
        entry_ok = {m for m in universe if m in bars and not self.markets.get(m, {}).get('warning')}
        held = list(self.ledger.d['positions']) if self.ledger.ready else []
        sells, buys, unknown = st.plan(ranked, held, set(), entry_ok, self.p)
        print(f'기준 봉 마감: {utc(end).astimezone(KST):%m-%d %H:%M} · 자료 확보 {len(bars)}/{len(universe)}')
        for r in ranked[:10]:
            flag = '' if r['market'] in entry_ok else ' (진입 불가: 경보)'
            print(f"{r['rank']:>2}. {r['market']:<12} {r['rs']*100:+7.2f}%  ATR {r['atr']/r['close']*100:.2f}%{flag}")
        print('매도:', sells or '없음', '/ 매수 후보:', buys or '없음', '/ 보류:', unknown or '없음')


def single_instance():
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


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args()
    if a.check:
        Bot().check()
    else:
        single_instance()
        guard = None
        try:
            _, cfg = load_params()
            options = dict(cfg.get('session', {}), live_enabled=cfg.get('live', {}).get('enabled', False))
            guard = SessionGuard(ROOT, options)
            if not guard.acquire():
                sys.exit(0)
            upbit.ORDER_GUARD = guard.assert_owner
            guard.start()
            STOP.unlink(missing_ok=True)
            STOP_ASCII.unlink(missing_ok=True)
            runner = Bot()
            runner.session_guard = guard
            runner.run()
        except Exception as ex:
            notify(ex)
        finally:
            if guard:
                guard.release()
            LOCK.unlink(missing_ok=True)
