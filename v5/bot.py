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

import manual_orders
import position_popup
import protection_panel
import observations
import strategy as st
import rotation_policy as rotation
import chase_override as co
import math
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


def ranking_rows(ranking, held, warned, p, esc=html.escape, promoted=(), hot=(), can_promote=False, stuck=None):
    """순위표 행. 바탕색: 보유=하늘색, 매수 불가 경보=붉은 파스텔, 투자 대상(매수 후보)=연두, 관찰=연노랑. '구분' 칸에도 적는다.
    매수 후보 = 현재 매수 규칙(상위 entry_top 안, 상승률 0 초과 chase_rs_cap 미만, 보유·매수 불가 경보 아님). 관찰 = 5위(유지 순위) 안 정상 종목.
    warned: {종목: 경보 정보} (집합이면 모두 매수 불가로 본다). 사용자 지시(2026-11-04): 경보 종목 중 매수 가능하면 '매수 후보'와
    경보 이유를, 불가하면 '매수 불가(경보 이유)'를 적는다."""
    out, want, scan, picked = [], p.entry_want or p.entry_top, p.entry_scan or p.entry_top, 0
    info = warned if isinstance(warned, dict) else {m: {'trade_blocked': True, 'warning_reasons': []} for m in warned}
    for r in ranking:
        m, rs, rank = r['market'], r['rs'], r['rank']
        w = info.get(m)
        reasons = '·'.join((w or {}).get('warning_reasons') or []) or '경보'
        note = f" · 경보: {reasons}(매수 가능)" if w and not w.get('trade_blocked', True) else ''
        if w and w.get('trade_blocked', True):
            cls, tag = 'rk-alert', ('보유 · ' if m in held else '') + f'매수 불가({reasons})'
        elif m in held:
            s = (stuck or {}).get(m)
            cls, tag = 'rk-held', '보유' + (f" · 묶임({s.get('state', '')})" if s else '') + (' · 과열 보유: 순위 매도 제외(손절·이익보호로만 매도)' if m in hot else '')
        elif rank <= scan and m in promoted and p.chase_rs_cap <= rs < co.ext_cap(p):
            cls, tag, picked = 'rk-target', f'직접 지정 매수 후보(상한 {co.EXT_CAP:.0%})', picked + 1
        elif rank <= scan and rs >= p.chase_rs_cap > 0:
            cls, tag = 'rk-hot', '과열(매수 보류)'
        elif rank <= scan and rs > 0 and picked < want:
            cls, tag, picked = 'rk-target', '투자 대상(매수 후보)', picked + 1
        elif rank <= p.M and rs > 0:
            cls, tag = 'rk-watch', '관찰'
        else:
            cls, tag = '', ''
        tag = (tag + note) if tag else note.lstrip(' ·')
        btn = ''
        if can_promote and m not in held and rank <= scan and p.chase_rs_cap > 0 and p.chase_rs_cap <= rs < co.ext_cap(p):
            act, label = ('unpromote', '지정 취소') if m in promoted else ('promote', '매수 대상으로')
            btn = f" <button class=pr-btn data-act={act} data-m='{esc(m)}'>{label}</button>"
        tip = f" title='오른쪽 버튼으로 사용자 권한으로 매수 대상 후보에 올릴 수 있습니다(상한 {co.EXT_CAP:.0%}, 24시간)'" if btn else ''
        out.append(f"<tr class='{cls}'{tip}><td class=n>{rank}</td><td>{esc(m[4:])}</td>"
                   f"<td class='n {'up' if rs >= 0 else 'dn'}'>{rs * 100:+.2f}%</td><td>{esc(tag)}{btn}</td></tr>")
    return ''.join(out)


def stuck_html(stuck, esc=html.escape):
    """묶인 보유: 예전 규칙이면 순위로 팔았을 종목. 순위 매도 없이 손절·이익보호로만 판다(사용자 지시 2026-11-05)."""
    if not stuck:
        return ''
    items = []
    for m, s in sorted(stuck.items(), key=lambda x: (x[1].get('pnl_pct') is None, x[1].get('pnl_pct') or 0)):
        rank = f"{s['rank']}위" if s.get('rank') else '상위 밖'
        pnl = f" {s['pnl_pct'] * 100:+.2f}%" if isinstance(s.get('pnl_pct'), (int, float)) else ''
        items.append(f"<b>{esc(m[4:])}</b> {esc(s.get('state', ''))}{pnl} · {rank}")
    return ("<div class=sub style='margin-top:8px;padding:6px 8px;border-left:3px solid var(--up)'>묶인 종목(순위 매도 안 함, 손절·이익보호로만 매도): "
            + ' / '.join(items) + '</div>')


def fmt_price(x):
    """가격 표시: 지수 표기(1.047e+08) 없이 자릿수에 맞춰 쓴다."""
    if not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0:
        return '-'
    if x >= 1000:
        return f'{x:,.0f}'
    if x >= 1:
        return f'{x:,.2f}'.rstrip('0').rstrip('.')
    return f'{x:.4g}'


def holdings_table(rows, protected, stuck, positions, sell_line, esc=html.escape, min_value=5000):
    """보유 종목 한 표(실제 계좌 평가·원가 + 봇 장부 매도선 + 보호·묶임 구분). 5,000원 미만 자투리는 개수만 적는다.
    rows: pnl 요약의 unrealized_rows. sell_line(m) -> (이름, 가격) 또는 None."""
    out, dust = [], 0
    for r in sorted(rows, key=lambda r: -(r.get('value') or 0)):
        m = 'KRW-' + r['coin']
        if (r.get('value') or 0) < min_value:
            dust += 1
            continue
        if m in protected:
            kind, line = "<span class='tag tag-prot'>사용자 보호</span>", '<span class=muted>봇 매도 안 함</span>'
        else:
            s = stuck.get(m)
            kind = (f"<span class='tag tag-stuck'>묶임 · {esc(s.get('state', ''))}</span>" if s else "<span class='tag tag-bot'>봇</span>")
            sl = sell_line(m)
            line = f"{esc(sl[0])} {fmt_price(sl[1])}" if sl else '-'
        u = r.get('unrealized')
        cost = r.get('cost') or 0
        pnl = (f"<span class={'up' if u >= 0 else 'dn'}>{u:+,.0f}원 <small>({u / cost * 100:+.2f}%)</small></span>"
               if isinstance(u, (int, float)) and cost else '<span class=warn>미확정</span>')
        qty = r.get('qty') or 0
        buy = cost / qty if qty and cost else None                       # 매수가 = 원가 ÷ 수량(수수료 포함 평균)
        now_px = r.get('price') or (r['value'] / qty if qty else None)   # 현재가
        attrs = position_popup.row_attrs(m) if m in positions else ''
        out.append(f"<tr{attrs}><td><b>{esc(r['coin'])}</b></td><td>{kind}</td><td class=n>{r['value']:,.0f}</td>"
                   f"<td class=n>{cost:,.0f}</td><td class=n>{pnl}</td><td class=n>{fmt_price(buy)}</td><td class=n><b>{fmt_price(now_px)}</b></td>"
                   f"<td class=n>{line}</td></tr>")
    body = ''.join(out) or '<tr><td colspan=8 class=muted>보유 없음</td></tr>'
    note = f"<div class=sub>5,000원 미만 자투리 {dust}개는 숨김(팔 수 없는 잔량)</div>" if dust else ''
    return (f"<table><tr><th>코인</th><th>구분</th><th class=n>평가액</th><th class=n>원가</th><th class=n>평가손익</th><th class=n>매수가</th><th class=n>현재가</th><th class=n>매도선</th></tr>"
            f"{body}</table>{note}")


SHOW_EVENTS = ('주문안 처리', '이익보호 시작', '본전 잠금')


def useful_events(rows, limit=8):
    """실거래 기록 중 사람이 볼 것만: 체결(매수·매도 금액), 주문안 결과, 이익보호·본전 잠금 시작. 내부 기준 기록('0원' 줄)은 뺀다."""
    out = []
    for o in rows:
        m = (o.get('market') or '')[4:]
        if o.get('side') and m and o.get('funds'):
            why = str(o.get('reason') or '')
            text = f"{o['side']} {m} {o['funds']:,.0f}원" + ('' if not why or why.startswith('봇 주문안') else f' · {why}')
        elif o.get('event') in SHOW_EVENTS:
            text = (f'{m} ' if m else '') + o['event'] + (f": {o['result']}" if o.get('result') else '')
        else:
            continue
        out.append((o.get('time', ''), text))
        if len(out) >= limit:
            break
    return out


NOISE = ('봇 종료', '봇 실행 시작', '요청 서버 시작')


def promote_script(api):
    """순위표 '매수 대상으로'/'지정 취소' 버튼. 서버가 없으면 버튼이 나오지 않는다."""
    if not api:
        return ''
    return ("<div id=pr-res class=sub></div><script>(function(){var A=" + json.dumps({'port': api['port'], 'token': api['token']}) + ";"
            "document.querySelectorAll('.pr-btn').forEach(function(b){b.addEventListener('click',function(){var m=b.dataset.m,a=b.dataset.act;"
            "if(!confirm(a=='promote'?m.slice(4)+'을(를) 24시간 동안 매수 대상으로 올립니다(과열 상한 20%).\\n진행할까요?':m.slice(4)+' 직접 지정을 취소할까요?'))return;"
            "b.disabled=true;var r=document.getElementById('pr-res');r.textContent='⏳ 요청 중…';"
            "fetch('http://127.0.0.1:'+A.port+'/'+a,{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify({token:A.token,market:m,confirm:true})})"
            ".then(function(x){return x.json()}).then(function(d){r.textContent=(d.ok?'✔ ':'✘ ')+d.message;if(!d.ok)b.disabled=false})"
            ".catch(function(){r.textContent='✘ 봇에 연결하지 못했습니다.';b.disabled=false})})})})()</script>")


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
        entry_ok = {m for m in universe if m in bars and not self.markets.get(m, {}).get('trade_blocked', self.markets.get(m, {}).get('warning'))}
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
        if rotation.enabled(self.p):
            floor = d.setdefault('rotation_first_slot', rotation.slot(time.time()))
            prior = max(floor, d.get('rotation_last_slot', floor))
            d['ranking'] = ranked[:15]
            d['ranking_time'] = utc(end).astimezone(KST).isoformat(timespec='minutes')
            if end % rotation.HOUR or end <= prior:
                d['last_4h'] = end
                lg.save()
                return
            d['rotation_last_slot'] = end
            d['last_4h'] = end
            lg.save()  # claim before any paper fill; crash skips this slot
        held = list(d['positions'])
        now = time.time()
        d['banned'] = {m: t for m, t in d['banned'].items() if t > now}
        sells, buys, unknown = st.plan(ranked, held, set(d['banned']), entry_ok, self.p)
        if rotation.enabled(self.p):
            index = st.market_index({m:b for m,b in bars.items() if m in uni}, self.p.regime_bars, self.p.regime_min_markets) if self.p.regime_filter else None
            expired = self.p.regime_filter and self.p.regime_expires and now >= self.p.regime_expires
            if self.p.halt_new_buys or expired or (self.p.regime_filter and (type(index) not in (int,float) or not math.isfinite(index) or index <= 0)):
                buys = []
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

    def can_act(self):
        """이 PC가 실행권을 가졌나(캐시된 판단). 실제 주문 직전에는 session_guard가 GitHub에서 다시 확인한다."""
        g = getattr(self, 'session_guard', None)
        return g is None or bool(g.acquired)

    GRANT_FILE = DATA / 'grant_status.json'

    def grant_status(self):
        try:
            return json.loads(self.GRANT_FILE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def request_grant(self):
        """화면 '이 PC에 실행권 받기' 버튼: 이전 도구(take_over)를 별도 프로세스로 시작한다. 이 봇은 도구가 멈추고 다시 켠다."""
        g = getattr(self, 'session_guard', None)
        if g is not None and g.acquired:
            return True, '이 PC에 이미 실행권이 있습니다.'
        tool = ROOT.parents[2] / 'ai-trade' / 'tools' / 'take_over.py'
        if not tool.exists():
            return False, '실행권 이전 도구(take_over.py)를 찾지 못했습니다.'
        pyw = Path(sys.executable).with_name('pythonw.exe')
        exe = str(pyw if pyw.exists() else sys.executable)
        self.GRANT_FILE.write_text(json.dumps({'state': 'running', 'time': now_iso(), 'message': '실행권 이전을 시작했습니다.'}, ensure_ascii=False), encoding='utf-8')
        subprocess.Popen([exe, str(tool), '--button'], cwd=str(tool.parents[1]),
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
        return True, '실행권 이전을 시작했습니다. 다른 PC 봇이 멈춘 뒤 이 PC 봇이 다시 시작됩니다(1~4분). 화면이 바뀌면 램프를 확인하세요.'

    def ownership_html(self):
        """실행권 램프와 '이 PC에 실행권 받기' 버튼. 램프: 초록=이 PC, 빨강=다른 PC/없음, 노랑=확인 불가."""
        g = getattr(self, 'session_guard', None)
        if g is None:
            return ''
        e = html.escape
        snap = g.snapshot()
        st = snap['status']
        if snap['owner'] and st == 'unverified':
            color, text = '#d9a400', f"확인 불가 · GitHub 응답 없음 {snap['blind_seconds'] // 60}분째 (이 PC가 마지막 실행권자: 매수 보류, 손절 매도는 허용)"
        elif snap['owner']:
            color, text = '#2e9e5b', f"이 PC({e(snap['pc'])})에 있음" + (f" · {snap['checked_ago']}초 전 확인" if snap['checked_ago'] is not None else '')
        elif st == 'other':
            color, text = '#d64545', f"다른 PC({e(snap['other_pc'] or '?')})에 있음 · 이 PC는 주문하지 않고 지켜보기만 합니다"
        elif st == 'free':
            color, text = '#d64545', '아무도 갖고 있지 않음 · 이 PC는 주문하지 않습니다'
        else:
            color, text = '#d9a400', '확인 중' + (f" · {e(snap['reason'])}" if snap['reason'] else '')
        gs = self.grant_status()
        result = ''
        if gs.get('state') == 'running':
            result = f"<span class=muted>⏳ 이전 진행 중 ({e(str(gs.get('time', ''))[5:16].replace('T', ' '))})</span>"
        elif gs.get('state') == 'done':
            result = f"<b class=up>✔ 넘어옴</b> <span class=muted>{e(str(gs.get('time', ''))[5:16].replace('T', ' '))} · {e(str(gs.get('message', '')))}</span>"
        elif gs.get('state') == 'failed':
            result = f"<b class=dn>✘ 넘어오지 않음</b> <span class=muted>{e(str(gs.get('time', ''))[5:16].replace('T', ' '))} · {e(str(gs.get('message', '')))}</span>"
        srv = getattr(self, 'manual_server', None)
        act = json.dumps({'port': srv.port, 'token': srv.token}) if srv else 'null'
        disabled = ' disabled' if snap['owner'] else ''
        label = '이 PC가 실행권 보유 중' if snap['owner'] else '이 PC에 실행권 받기'
        return (f"<div id=own-box style='display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:8px 0;border-bottom:1px solid var(--line)'>"
                f"<span style='display:inline-block;width:16px;height:16px;border-radius:50%;background:{color}'></span>"
                f"<b>실행권</b><span>{text}</span>"
                f"<button id=own-btn type=button{disabled}>{label}</button><span id=own-res>{result}</span></div>"
                "<script>(function(){var A=" + act.replace('<', '\\u003c') + ",b=document.getElementById('own-btn'),r=document.getElementById('own-res');"
                "if(!b)return;if(!A){b.disabled=true;b.title='봇 제어 연결을 기다리는 중';return}"
                "b.addEventListener('click',function(){if(!confirm('다른 PC 봇은 주문을 멈추고, 이 PC가 실행권을 받습니다.\\n이 PC 봇은 잠시 재시작되고 계좌 전체를 새 기준으로 인수합니다(손절선은 다시 계산).\\n진행할까요?'))return;"
                "b.disabled=true;r.textContent='⏳ 요청 중…';"
                "fetch('http://127.0.0.1:'+A.port+'/grant',{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify({token:A.token,confirm:true})})"
                ".then(function(x){return x.json()}).then(function(d){r.textContent=(d.ok?'⏳ ':'✘ ')+d.message;if(!d.ok)b.disabled=false})"
                ".catch(function(){r.textContent='✘ 봇에 연결하지 못했습니다.';b.disabled=false})})})();</script>")

    def pnl_summary(self):
        f = DATA / 'pnl' / 'summary.json'
        try:
            return json.loads(f.read_text(encoding='utf-8')) if f.exists() else None
        except (OSError, ValueError):
            return None

    def holding_rows(self, s):
        """보유 종목 표의 행. 수량·평가액은 5초마다 갱신되는 실제 계좌 조회(account_live.json)를 기준으로 한다.
        원가는 수량이 일치하는 것 중 손익 집계(pnl, 5분 주기) → 봇 장부 → 업비트 평균 매입가 순서로 고른다.
        계좌 조회가 없거나 2분 넘게 멈췄으면 손익 집계 행을 그대로 쓴다."""
        pnl_rows = (s or {}).get('unrealized_rows') or []
        f = DATA / 'account_live.json'
        try:
            acct = json.loads(f.read_text(encoding='utf-8')) if f.exists() else None
        except (OSError, ValueError):
            acct = None
        if not acct or time.time() - float(acct.get('epoch') or 0) > 120:
            return pnl_rows
        by_pnl = {r['coin']: r for r in pnl_rows}
        same = lambda a, b: a and b and abs(a - b) <= max(1e-8, abs(a) * 1e-6)
        rows = []
        for x in acct.get('assets', []):
            coin, qty, value = x.get('currency'), x.get('quantity') or 0, x.get('value')
            if coin == 'KRW' or not qty or value is None:
                continue
            pos = self.live.d['positions'].get('KRW-' + coin)
            r = by_pnl.get(coin)
            if r and same(r.get('qty'), qty) and isinstance(r.get('cost'), (int, float)):
                cost = r['cost']
            elif pos and same(pos.get('qty'), qty):
                cost = pos['cost']
            else:
                cost = qty * (x.get('avg') or 0) or None
            rows.append({'coin': coin, 'qty': qty, 'value': value, 'cost': cost, 'price': x.get('price'),
                         'unrealized': (value - cost) if cost else None})
        return rows

    def sell_line(self, m):
        """봇 장부 종목의 지금 매도선: (이름, 가격). 손절·이익보호·본전 중 실제로 먼저 걸리는 선."""
        pos = self.live.d['positions'].get(m)
        if not pos or not pos.get('qty'):
            return None
        line = self.live.stop_limit(pos)
        g = pos.get('profit_guard_stop') if self.live.cfg.get('profit_guard_enabled') else None
        if isinstance(g, (int, float)) and line == g:
            entry = pos['cost'] / pos['qty'] if pos.get('cost') else g
            return ('본전' if g < entry * 1.01 else '이익보호', g)
        return ('손절', line)

    def live_block(self, prices):
        e, L, c = html.escape, self.live.d, self.live.cfg
        if not c['enabled']:
            return f"<section><h2 class=lg>실거래 (꺼짐)</h2>{self.ownership_html()}<div class=sub>이 PC의 실거래 설정이 꺼져 있습니다. 실행권을 받으면 설정을 맞추고 계좌를 인수해 시작합니다.</div></section>" if getattr(self, 'session_guard', None) else ''
        loss = self.live.raw_loss(prices) - L.get('risk_loss_offset', 0.)
        limit = self.live.loss_limit(prices)
        protected = self.live.protected_markets()
        s = self.pnl_summary() or {}
        urows = self.holding_rows(s)
        acct = DATA / 'account_live.json'
        try:
            total = json.loads(acct.read_text(encoding='utf-8')).get('priced_total') if acct.exists() else None
        except (OSError, ValueError):
            total = None
        total = total or self.live.equity_total(prices) or 0
        prot_value = sum(r.get('value') or 0 for r in urows if 'KRW-' + r['coin'] in protected)
        bot_u = sum(r['unrealized'] for r in urows if 'KRW-' + r['coin'] not in protected and isinstance(r.get('unrealized'), (int, float)))
        per = s.get('periods') or {}
        won = lambda v: f"<span class={'up' if v > 0 else 'dn' if v < 0 else 'muted'}>{v:+,.0f}원</span>"
        tile = lambda title, big, small='': f"<div class=tile><h2>{title}</h2><b>{big}</b><span class=muted>{small}</span></div>"
        today, cum = per.get('오늘', {}), per.get('누적', {})
        tiles = (tile('총자산', f'{total:,.0f}원', f'보호 종목 {prot_value:,.0f}원 포함')
                 + tile('봇 운용 자금', f'{total - prot_value:,.0f}원', '현금 + 봇 보유')
                 + tile('오늘 실현손익', won(today.get('realized', 0)), f"매도 {today.get('sell_orders', 0)}건 · 이익 {today.get('wins', 0)} / 손실 {today.get('losses', 0)}")
                 + tile('누적 실현손익', won(cum.get('realized', 0)), f"{e(str(s.get('baseline_start', ''))[5:10])}부터 · 매도 {cum.get('sell_orders', 0)}건")
                 + tile('봇 보유 평가손익', won(bot_u), f"보호 종목 포함 {sum(r['unrealized'] for r in urows if isinstance(r.get('unrealized'), (int, float))):+,.0f}원"))
        chips = []
        if protected:
            chips.append(f"<span class='tag tag-prot'>사용자 보호 {e(', '.join(sorted(m[4:] for m in protected)))}</span> 봇이 사고팔지 않음")
        chips.append(f"손실 중단선 여유 <b>{max(limit - loss, 0):,.0f}원</b> <span class=muted>(중단선 {limit:,.0f}원 · 지금 손익 {-loss:+,.0f}원)</span>")
        halt = f"<div class=warn>신규 매수 중단: {e(L['halted'])} · 재개: python tools/resume_trading.py</div>" if L['halted'] else ''
        cw = L.get('concurrent_warn') or {}
        if cw.get('active'): halt += f"<div class=warn><b>동시 보유 경보: {int(cw.get('count', 0))}개</b> ({e(', '.join(m[4:] for m in cw.get('markets', [])))}) · 알림만, 주문 제한 없음</div>"
        orders = []
        f = DATA / 'live' / 'orders.jsonl'
        if f.exists():
            orders = [json.loads(x) for x in f.read_text(encoding='utf-8').splitlines()[-200:] if x.strip()][::-1]
        ol = ''.join(f"<li><span class=t>{e(t[5:16].replace('T', ' '))}</span>{e(x)}</li>" for t, x in useful_events(orders))             or '<li class=muted>새 체결 없음 · 다음 정시 판단 대기</li>'
        recent = ''.join(
            f"<li><span class=t>{e(r['time'][5:16].replace('T', ' '))}</span>{e(r['coin'])} "
            f"{won(r['realized']) if r['realized'] is not None else '<span class=warn>미확정</span>'}</li>"
            for r in (s.get('recent') or [])[:8]) or '<li class=muted>매도 체결 없음</li>'
        table = holdings_table(urows, protected, co.read_stuck(DATA), L['positions'], self.sell_line)
        return f"""<section><h2 class=lg>실거래 (봇 자동 실행)</h2>{self.ownership_html()}{halt}
<div class=chips>{' · '.join(chips)}</div>
<div class=tiles>{tiles}</div>
<h3>보유 종목 <span class=sub>(행을 누르면 상세 · 매도선 = 손절·이익보호·본전 중 지금 먼저 걸리는 선)</span></h3>{table}
<div class=cols><div><h3>최근 실현손익 (매도 체결)</h3><ul>{recent}</ul></div><div><h3>실거래 기록</h3><ul>{ol}</ul></div></div>
{position_popup.data_script(L['positions'], prices, L.get('last_sell'), action=self.manual_action())}{position_popup.MODAL}</section>"""

    def real_block(self, prices):
        if (DATA / 'account_live.html').exists():
            return ('<details><summary class=lg>업비트 계좌 상세 (조회 전용) — 눌러서 열기</summary><section>'
                    '<iframe title="실제 업비트 실시간 계좌" src="account_live.html" style="width:100%;height:620px;border:0"></iframe></section></details>')
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
            f"<td class='n {'up' if t.get('pnl', 0) >= 0 else 'dn'}'>{(format(t['pnl'], '+,') + '원') if 'pnl' in t else ''}</td>"
            f"<td class=n>{(format(t['pnl_pct'], '+.2f') + '%') if 'pnl_pct' in t else ''}</td></tr>"
            for t in trades) or '<tr><td colspan=7 class=muted>아직 거래 없음</td></tr>'
        return f"""<section><h2 class=lg>{e(title)}</h2>
<div class=tiles>
<div class=tile><h2>전략 순자산 (모의)</h2><b>{P:,.0f}원</b><span class="{'up' if pr >= 0 else 'dn'}">{pr*100:+.2f}%</span> <span class=muted>배정 {d['A0']:,}원</span></div>
<div class=tile><h2>전략 − 계좌 그대로 보유</h2>{(f'<b class="{"up" if pr >= hr else "dn"}">{(pr-hr)*100:+.2f}%p</b><span class=muted>보유(H) {hr*100:+.2f}%</span>') if hr is not None else ('<b>비교 보류</b><span class=warn>시세 누락: ' + e(', '.join(m[4:] for m in self.price_missing)) + '</span>')}</div>
<div class=tile><h2>현금 · 투입</h2><b>{d['cash']:,.0f}원</b><span class=muted>투입 {lg.invested(prices):,.0f}원 · 최대 {self.p.total_cap:.0%}</span></div>
<div class=tile><h2>매수 신호 · 최소주문 미달</h2><b>{sig} · {skip}</b><span class=muted>건너뜀 비율 {(skip/sig*100 if sig else 0):.0f}%</span></div>
</div>
<div class=cols><div><h3>보유 종목</h3><table><tr><th>종목</th><th class=n>평가액</th><th class=n>진입가</th><th class=n>현재가</th><th class=n>손절가</th><th class=n>손익</th></tr>{pos_rows}</table></div>
<div><h3>최근 모의거래 (실제 주문 아님)</h3><table><tr><th>시각</th><th>구분</th><th>종목</th><th class=n>금액</th><th>사유</th><th class=n>손익</th><th class=n>손익률</th></tr>{tr_rows}</table></div></div></section>"""

    def held_markets(self, prices):
        """평가 5,000원 이상 보유 종목(실제 계좌 + 봇 관리 장부)."""
        held = {m for m, c in ((self.real or {}).get('coins') or {}).items() if c['qty'] * prices.get(m, 0) >= 5000}
        held |= {m for m, pos in self.live.d['positions'].items() if pos['qty'] * prices.get(m, 0) >= 5000}
        return held

    def pnl_block(self):
        """실현손익 관리(D-56). pnl.py가 거래소 체결로 계산한 data/pnl/summary.json을 보여 준다. 접기·펼치기 상태는 브라우저에 기억한다."""
        e, f = html.escape, DATA / 'pnl' / 'summary.json'
        head = '<details id=pnlBox><summary class=lg>실현손익 상세 (기간별 · 출처별) — 눌러서 열기</summary>'
        tail = ('</details><script>(function(){var d=document.getElementById("pnlBox");'
                'try{if(localStorage.getItem("pnlOpen")==="1")d.open=true;}catch(x){}'
                'd.addEventListener("toggle",function(){try{localStorage.setItem("pnlOpen",d.open?"1":"0")}catch(x){}});})();</script>')
        if not f.exists():
            return head + '<section><p class=muted>아직 계산 전입니다. pnl.py가 거래소 체결을 받아 5분마다 계산합니다.</p></section>' + tail
        s = json.loads(f.read_text(encoding='utf-8'))
        won = lambda v: f"<span class={'up' if v > 0 else 'dn' if v < 0 else 'muted'}>{v:+,.0f}원</span>"
        # 같은 숫자인 기간은 하나로 합친다(예: 이번 주 = 이번 달 = 누적)
        groups = []
        for k, v in s['periods'].items():
            key = (round(v['realized']), v['sell_orders'])
            if groups and groups[-1][0] == key:
                groups[-1][1].append(k)
            else:
                groups.append((key, [k], v))
        tiles = ''.join(
            f"<div class=tile><h2>{' = '.join(e(x) for x in ks)} 실현손익</h2>"
            f"<b>{won(v['realized'])}</b><span class=muted>확정 {v.get('confirmed', v['realized']):+,.0f} · 추정 {v.get('estimated', 0):+,.0f}"
            f"{' · <span class=warn>미확정 ' + str(v['unconfirmed_count']) + '건</span>' if v['unconfirmed_count'] else ''}<br>"
            f"매도 {v['sell_orders']}건 · 이익 {v['wins']} / 손실 {v['losses']} · 수수료 {v['fees']:,.0f}원</span></div>"
            for _, ks, v in groups)
        src = s['periods'].get('누적', {}).get('by_source', {})
        src_txt = ' · '.join(f'{e(k)} {v:+,.0f}원' for k, v in src.items()) or '없음'
        warn = ''
        if s['mismatch']:
            warn += f"<div class=warn>체결로 재구성한 수량과 실제 잔고가 다릅니다: {e(', '.join(s['mismatch']))} (입출금·에어드롭 등 주문이 아닌 변동)</div>"
        if s.get('open_status') not in (None, 'ok'):
            warn += f"<div class=warn>미체결 주문 조회 {e(s['open_status'])}: 부분 체결분이 빠졌을 수 있습니다.</div>"
        synced = s.get('synced_at')
        if not synced or (dt.datetime.now(KST) - dt.datetime.fromisoformat(synced)).total_seconds() > 900:
            warn += f"<div class=warn>실현손익 동기화가 15분 넘게 멈춰 있습니다(마지막 {e((synced or '-')[5:16].replace('T', ' '))}). pnl.py 실행을 확인하세요.</div>"
        err = DATA / 'pnl' / 'error.json'
        if err.exists() and err.stat().st_mtime > f.stat().st_mtime:
            warn += f"<div class=warn>최근 동기화 실패, 마지막 성공 결과입니다: {e(json.loads(err.read_text(encoding='utf-8'))['error'])}</div>"
        return head + f"""<section>{warn}<div class=tiles>{tiles}</div>
<div class=sub>누적 출처별: {src_txt}. 기준: 거래소 체결, 이동평균 원가(기초 이후 매수는 수수료 포함), 한국시간 매도 체결 시각, 기초 {e(s['baseline_start'][:16].replace('T', ' '))}부터.
확정 = 체결 기록만으로 원가가 정해진 손익 · 추정 = 기초 보유분의 평균 매입가(수수료 포함 여부·입출금 미확인)가 원가에 섞인 손익 · 미확정 = 원가 불명·잔고 불일치·체결 상세 불완전.
마지막 동기화 {e((synced or '-')[5:16].replace('T', ' '))} · 관리용 손익이며 세무상 손익이 아닙니다.</div></section>""" + tail

    def write_status(self, prices, next_4h):
        d, e, p = self.ledger.d, html.escape, self.p
        if not self.markets:    # 재시작 직후 첫 판단 전에는 비어 있다
            try:
                self.markets = upbit.krw_markets()
            except Exception:
                pass
        warned = {m: info for m, info in (self.markets or {}).items() if info.get('warning')}
        promote_api = self.manual_action() if self.live.cfg.get('enabled') and self.can_act() else None
        rk_rows = ranking_rows(d.get('ranking', []), self.held_markets(prices), warned, p, promoted=co.promoted(DATA), hot=co.hot_held(DATA),
                               can_promote=bool(promote_api), stuck=co.read_stuck(DATA)) or '<tr><td colspan=4 class=muted>첫 판단 전</td></tr>'
        rk_rows_script = promote_script(promote_api) + stuck_html(co.read_stuck(DATA))
        events = sorted([dict(x, who=n) for n, lg in self.ledgers.items() for x in lg.read_jsonl('events.jsonl', 40)
                         if not any(w in x.get('msg', '') for w in NOISE)], key=lambda x: x['time'])[-20:][::-1]
        ev = ''.join(f"<li><span class=t>{e(x['time'][5:16].replace('T', ' '))} {e(x['who'])}</span>{e(x['msg'])}</li>" for x in events)             or '<li class=muted>기록 없음</li>'
        now = time.time()
        slot = int(now // 3600) * 3600 + (0 if now % 3600 < 60 else 3600)
        next_buy = dt.datetime.fromtimestamp(slot + 60, KST).strftime('%H:%M')
        # 실제 거래·계좌(위)와 모의(가상) 장부(아래, 접어 둠)를 구분한다. 모의 장부는 실제 거래가 아니며 옛 규칙으로 20:45에 진입한 뒤 거의 멈춰 있다.
        paper = self.block(f"모의 장부 P10 · 계좌의 {d['fraction']:.0%} 가상 배정 ({d['A0']:,}원)", self.ledgers['P10'], prices) + \
            self.block(f"모의 장부 ALL · 계좌 평가액만큼 가상 현금 ({self.ledgers['ALL'].d['A0']:,}원, 실제 계좌 인수 아님)", self.ledgers['ALL'], prices)
        protection_top=protection_panel.render(self.live,((self.real or {}).get('coins') or {}),self.protection_action())
        srv_o = getattr(self, 'manual_server', None)
        try:
            obs_panel = observations.render(DATA / 'observations.json', {'port': srv_o.port, 'token': srv_o.token} if srv_o else None)
        except Exception as ex:  # 관찰 창 오류가 화면·거래를 막지 않는다
            obs_panel = f'<section><b>관찰·그림자 관리 창 오류: {html.escape(type(ex).__name__)}</b></section>'
        ranking = (f"<section><h2 class=lg>상대강도 순위 <span class=sub>({e(d.get('ranking_time', '-')[5:16].replace('T', ' '))} · 최근 {p.L * p.tf // 60}시간 수익률)</span></h2>"
                   f"<table><tr><th class=n>#</th><th>종목</th><th class=n>{p.L * p.tf // 60}시간</th><th>구분</th></tr>{rk_rows}</table>{rk_rows_script}"
                   f"<div class=sub style='margin-top:8px'><span class='chip chip-held'>보유</span> <span class='chip chip-target'>매수 후보</span> "
                   f"<span class='chip chip-watch'>관찰</span> <span class='chip chip-hot'>과열(매수 보류 · 20% 미만은 커서를 올려 직접 지정)</span> "
                   f"<span class='chip chip-alert'>매수 불가 경보</span> · {p.entry_scan or p.entry_top}위 안에서 상승률 0~{p.chase_rs_cap:.0%}</div></section>")
        srv_r = getattr(self, 'manual_server', None)
        refresh_btn = ("<button id=rf-btn style='font-size:13px;padding:4px 12px;margin-left:10px;cursor:pointer'>지금 새로고침</button>"
                       "<span id=rf-res class=sub></span><script>(function(){var A=" + json.dumps({'port': srv_r.port, 'token': srv_r.token}) + ","
                       "b=document.getElementById('rf-btn'),r=document.getElementById('rf-res');b.onclick=function(){b.disabled=true;r.textContent=' ⏳ 봇이 화면을 다시 만드는 중…';"
                       "fetch('http://127.0.0.1:'+A.port+'/refresh',{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify({token:A.token,confirm:true})})"
                       ".then(function(){setTimeout(function(){location.reload()},4000)}).catch(function(){location.reload()})}})()</script>") if srv_r else ''
        fold = lambda title, body: f'<details><summary class=lg>{title} — 눌러서 열기</summary>{body}</details>'
        blocks = self.live_block(prices) + ranking + protection_top + obs_panel + self.pnl_block() + self.real_block(prices) +             fold('운영 기록 (봇 판단·모의 장부)', f'<section><ul>{ev}</ul></section>') +             fold('모의(가상) 장부 — 실제 거래 아님 · 전략 실험용', paper)
        page = f"""<!doctype html><html lang=ko><meta charset=utf-8><meta http-equiv=refresh content=30>
<meta name=viewport content="width=device-width,initial-scale=1"><title>자동거래 v5</title>
<script>try{{if(localStorage.getItem('hideDust')!=='0')document.documentElement.classList.add('hd');}}catch(e){{document.documentElement.classList.add('hd');}}</script><style>
html.hd .dust{{display:none}}
details>summary{{cursor:pointer;color:var(--muted);padding:8px 2px}}details[open]>summary{{margin-bottom:8px}}
tr.rk-held td{{background:#dff0fa}} tr.rk-alert td{{background:#fbe0e0}} tr.rk-target td{{background:#e0f3dc}} tr.rk-watch td{{background:#fff5d1}} tr.rk-hot td{{background:#ffe6cc}} .pr-btn{{visibility:hidden;margin-left:6px;font-size:12px;cursor:pointer}} tr:hover .pr-btn,.pr-btn:focus,.pr-btn:disabled{{visibility:visible}} @media (hover:none){{.pr-btn{{visibility:visible}}}} .chip-hot{{background:#ffe6cc;color:#17212b}} .chip{{padding:1px 8px;border-radius:4px}} .chip-held{{background:#dff0fa;color:#17212b}} .chip-alert{{background:#fbe0e0;color:#17212b}} .chip-target{{background:#e0f3dc;color:#17212b}} .chip-watch{{background:#fff5d1;color:#17212b}}
@media (prefers-color-scheme:dark){{tr.rk-held td{{background:#1d3647}} tr.rk-alert td{{background:#4a2a2d}} tr.rk-target td{{background:#22402a}} tr.rk-watch td{{background:#47401f}} tr.rk-hot td{{background:#4d3620}} .chip-hot{{background:#4d3620;color:#e3e8eb}} .chip-held{{background:#1d3647;color:#e3e8eb}} .chip-alert{{background:#4a2a2d;color:#e3e8eb}} .chip-target{{background:#22402a;color:#e3e8eb}} .chip-watch{{background:#47401f;color:#e3e8eb}}}}
.tag{{font-size:12px;padding:2px 8px;border-radius:10px;white-space:nowrap}} .tag-prot{{background:#fbe0e0;color:#7a1f1c}} .tag-bot{{background:#e0f3dc;color:#1d4d22}} .tag-stuck{{background:#fff0c2;color:#5c4300}}
.chips{{font-size:13.5px;margin:6px 0 10px}} header h1{{margin:0}} small{{font-size:12px}}
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
<header><h1>자동거래 v5 {refresh_btn}</h1><div class=sub>갱신 {e(now_iso()[11:19])} (자동 30초) · 다음 매수 판단 {next_buy} (매시 1분) · 손절·이익보호는 3초마다 실시간</div>
{f'<div class=warn>최근 오류: {e(self.error)}</div>' if self.error else ''}</header>
{blocks}
<div class=sub>지금 규칙: 거래대금 상위 {p.universe} · 상대강도 {p.L * p.tf // 60}시간 · {p.entry_scan or p.entry_top}위 안에서 사고 봇 보유 최대 {p.slots}종목 · 과열 상한 {p.chase_rs_cap:.0%}(직접 지정·이익 청산 뒤 재진입 {co.EXT_CAP:.0%}) ·
손절 최대 {p.stop_max_pct:.1%} · 이익보호 +{float(self.live.cfg.get('profit_guard_arm_pct') or 0):.0%}부터 {float(self.live.cfg.get('profit_guard_giveback') or 0):.0%} 반납 시 매도 ·
본전 잠금 {('+' + format(float(self.live.cfg['breakeven_lock_pct']), '.1%')) if self.live.cfg.get('breakeven_lock_pct') else '꺼짐'} · 순위 매도 {'켜짐' if getattr(p, 'rank_exit', True) else '꺼짐(손절·이익보호로만 매도)'} ·
묶임(손실 중) 부분 교체 {'켜짐' if getattr(p, 'swap_stuck_loss', False) else '꺼짐'} · 장세 필터 {'켜짐' if p.regime_filter else '꺼짐'}</div>
</main></html>"""
        # Browsers/antivirus may briefly hold the destination on Windows.
        # Keep the last good page; a display write must not suspend protection.
        temporary = DATA / f'status.{os.getpid()}.tmp'
        try:
            temporary.write_text(page, encoding='utf-8')
            for attempt in range(5):
                try:
                    os.replace(temporary, DATA / 'status.html')
                    break
                except OSError:
                    if attempt == 4: raise
                    time.sleep(.2)
        except OSError as ex:
            self.log('현황 화면 저장 대기: ' + type(ex).__name__)
        finally:
            try: temporary.unlink(missing_ok=True)
            except OSError: pass

    # ---------- 반복 ----------
    def run(self):
        subprocess.Popen([sys.executable, str(ROOT / 'account_sync.py')],
                         cwd=str(ROOT), creationflags=subprocess.CREATE_NO_WINDOW)
        if not all(lg.ready for lg in self.ledgers.values()):
            self.init_t0()
        self.align_timeframe(time.time())
        self.log('봇 실행 시작 (실거래 활성 · 기존 보유분 위임 관리 포함)' if self.live.cfg['enabled'] else '봇 실행 시작 (모의 운영)')
        last_snap = last_stop = last_live = last_display = last_trail = last_manual_scan = 0
        # 사용자 지시(2026-11-04): 손절 기준을 넘으면 인식 즉시 매도. 실거래 중이면 3초마다 실시간 가격 확인.
        stop_iv = int(self.live.cfg.get('stop_check_seconds', 3)) if self.live.cfg['enabled'] else self.p.stop_seconds
        self.manual_server = None
        if True:
            kinds={'grant', 'obs', 'refresh'}
            if self.live.cfg.get('manual_ui'): kinds.update(('buy','liquidate','promote','unpromote'))
            if self.live.cfg.get('protection_ui'): kinds.update(('protect','unprotect'))
            srv = manual_orders.ManualOrderServer(self.live.cfg.get('manual_port', manual_orders.DEFAULT_PORT),allowed_kinds=kinds)
            started=srv.start()
            if not started:
                srv=manual_orders.ManualOrderServer(0,allowed_kinds=kinds)
                started=srv.start()
            if started:
                self.manual_server = srv
                self.log(f'화면 강제 청산 요청 서버 시작 (127.0.0.1:{srv.port}, 토큰 인증)')
            else:
                self.log(f'화면 강제 청산 서버를 열지 못함(포트 {srv.port} 사용 중): 청산 버튼 비활성')
        if self.live.cfg['enabled'] and self.can_act():
            try: self.live.restore_continuity()
            except Exception as ex: self.log(f'연속성 복원 오류(무시하고 계속): {type(ex).__name__}')
        while not (STOP.exists() or STOP_ASCII.exists()):
            guard = getattr(self, 'session_guard', None)
            if guard and guard.status != getattr(self, '_last_lamp', None):  # 실행권 램프가 바뀌면 화면을 바로 새로 쓴다
                if getattr(self, '_last_lamp', None) is not None:
                    self.log(f'실행권 상태 변경: {self._last_lamp} → {guard.status}' + (f' ({guard.reason})' if guard.reason else ''))
                self._last_lamp = guard.status
                last_display = 0
            now = time.time()
            try:
                if self.live.cfg.get('auto_manual_protection') and now-last_manual_scan>=15 and self.can_act():
                    try: self.live.scan_manual_orders()
                    except Exception as ex: self.log('직접 주문 보호 확인 대기: '+type(ex).__name__)
                    last_manual_scan=now
                self.handle_manual_requests()
                if getattr(self,'_protection_ui_dirty',False):
                    last_display=0; self._protection_ui_dirty=False
                if getattr(self, '_refresh_now', False):  # 화면의 새로고침 버튼
                    last_display = 0; self._refresh_now = False
                TF = self.p.tf * 60
                end4 = boundary(now - SETTLE, TF)
                held = list({m for lg in self.ledgers.values() for m in lg.d['positions']} | set(self.live.d['positions']))
                if held and now - last_stop >= stop_iv:
                    live = {m: t['trade_price'] for m, t in upbit.tickers(held).items()}
                    if self.can_act():  # 실행권이 없는 PC는 실거래 손절·판단을 하지 않는다(주문은 어차피 막힌다)
                        self.live.stop_check(live)  # 실시간 손절·이익보호를 1분봉 순차 조회(trail_check)보다 먼저
                        if now - last_trail >= self.p.stop_seconds:
                            self.live.trail_check()
                            last_trail = now
                        self.live.check_resume_request(live)
                        self.live.check_loss(live)
                        try: self.live.check_concurrent(live)
                        except Exception as ex: self.log('동시 보유 확인 오류(무시): '+type(ex).__name__)
                    for lg in self.ledgers.values():
                        self.stop_check(lg, live)
                    last_stop = now
                if now - last_display >= 30:
                    self.refresh_display(end4 + TF + SETTLE)
                    last_display = now
                if now - last_live >= 30:
                    if self.can_act():
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
            time.sleep(2)
        if self.manual_server:
            self.manual_server.stop()
        self.log('중지.txt 확인 · 봇 종료')

    def manual_action(self):
        """화면 청산 버튼용 접속 정보. 서버가 꺼져 있으면 None이라 버튼이 나오지 않는다."""
        srv = getattr(self, 'manual_server', None)
        cap = min(self.live.cfg.get('manual_buy_max_krw', 100000), self.live.cfg['max_order_krw'])
        return {'port': srv.port, 'token': srv.token, 'buy_max': cap} if srv and self.live.cfg.get('manual_ui') else None

    def protection_action(self):
        srv=getattr(self,'manual_server',None)
        return {'port':srv.port,'token':srv.token} if srv and self.live.cfg.get('protection_ui') else None

    def handle_manual_requests(self):
        """화면에서 확인된 강제 청산 요청을 메인 루프에서 실행한다. 서버 스레드는 주문을 내지 않는다."""
        srv = getattr(self, 'manual_server', None)
        if not srv:
            return
        for req in srv.drain():
            try:
                if req.kind == 'obs':
                    ok, msg = observations.apply(DATA / 'observations.json', req.market, req.krw)
                    req.finish(ok, msg)
                    continue
                if req.kind == 'refresh':  # 화면 즉시 새로고침(주문과 무관)
                    self._refresh_now = True
                    req.finish(True, '화면을 새로 만드는 중입니다.')
                    continue
                if req.kind == 'grant':
                    ok, msg = self.request_grant()
                    req.finish(ok, msg)
                    self.log(f'실행권 받기 요청: {"시작" if ok else "보류"} · {msg}')
                    continue
                if not self.can_act():
                    req.finish(False, '이 PC에는 실행권이 없어 주문·보호 변경을 할 수 없습니다. 먼저 실행권을 받으세요.')
                    continue
                if req.kind == 'promote':
                    row = next((r for r in self.ledger.d.get('ranking', []) if r.get('market') == req.market), None)
                    scan = self.p.entry_scan or self.p.entry_top
                    if row is None or row.get('rank', 99) > scan:
                        ok, msg = False, f'{req.market[4:]}: 지금 {scan}위 안에 없어 지정할 수 없습니다.'
                    else:
                        ok, msg = co.promote(DATA, req.market, row['rs'], self.p)
                    label = '과열 종목 직접 지정'
                elif req.kind == 'unpromote':
                    ok, msg = co.unpromote(DATA, req.market)
                    label = '과열 종목 직접 지정 취소'
                elif req.kind in ('protect','unprotect'):
                    ok,msg=self.live.set_user_protection(req.market,req.kind=='protect',getattr(req,'revision',None))
                    label='사용자 보호 선택'; self._protection_ui_dirty=True
                elif req.kind == 'buy':
                    ok, msg = self.live.add_to_position(req.market, req.krw, self.markets or upbit.krw_markets())
                    label = f'수동 추가 매수 {req.krw:,}원'
                    if self.live.cfg.get('auto_manual_protection'):
                        self.live.protection_policy.decide(req.market,True)
                        self._protection_ui_dirty=True
                elif req.kind=='liquidate':
                    prices = {m: t['trade_price'] for m, t in upbit.tickers([req.market]).items()}
                    ok, msg = self.live.liquidate_position(req.market, prices)
                    label = '수동 강제 청산'
                    if self.live.cfg.get('auto_manual_protection'):
                        self.live.protection_policy.decide(req.market,True)
                        self._protection_ui_dirty=True
                else:
                    req.finish(False,'허용되지 않은 요청'); continue
                req.finish(ok, msg)
                self.log(f'{label} {req.market}: {"성공" if ok else "보류"} · {msg}')
            except SessionLost as ex:
                req.finish(False, f'실행 권한 없음: {ex}')
                raise
            except Exception as ex:
                req.finish(False, f'오류: {type(ex).__name__}: {str(ex)[:120]}')
                self.log(f'수동 강제 청산 오류 {req.market}: {ex}')

    def check(self):
        universe, tick = self.candidates()
        end = boundary(time.time() - SETTLE, self.p.tf * 60)
        bars = self.fetch_bars(universe, end)
        ranked = st.rank(bars, self.p)
        entry_ok = {m for m in universe if m in bars and not self.markets.get(m, {}).get('trade_blocked', self.markets.get(m, {}).get('warning'))}
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
            try:
                got = guard.acquire(confirm=lambda: False) if options['live_enabled'] else False  # 시작할 때 남의 실행권을 빼앗지 않는다(버튼으로만 받는다)
                if not options['live_enabled']:
                    guard.reason = '이 PC의 실거래 설정이 꺼져 있어 실행권을 받지 않습니다.'
            except SessionLost as ex:
                got, guard.reason = False, str(ex)
            if not got and not guard.reason:
                guard.reason = '다른 PC가 실행권을 갖고 있어 이 PC는 지켜보기만 합니다.'
            upbit.ORDER_GUARD = guard.assert_owner
            guard.check_now()
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
