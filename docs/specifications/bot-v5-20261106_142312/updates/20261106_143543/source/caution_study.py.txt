"""D-59 합의: 경보(주의) 종목 진입 그림자 비교. 주문 기능 없음, 실거래와 분리된 별도 프로세스.

  C0_경보차단  현행: 투자유의·주의 항목이 하나라도 있으면 신규·재매수 차단
  C1_주의허용  주의 항목이 가격급변·거래량급등뿐인 종목을 아래 조건으로 허용
청산은 두 군 모두 현행 실거래 규칙(exit_study G0와 같음). 장부·청산·재시작·보고는 exit_study를 그대로 쓴다.

C1 주의 종목 매수 조건(합의 원문 debates/D-59/03):
- 투자유의·해외가격차·소액계좌집중·입금량급등·경보 정보 형식 불명은 제외
- 기존 매수 규칙 전부(shadow.proposal) + 그 판단의 호가 차이 ≤ 0.4% + 최근 1시간(15분봉 4개) 종가 상승 > 0
- 금액은 proposal 금액의 1/2(최소 주문 미달이면 증액하지 않고 건너뜀), 주의 종목 동시 1개
- 주의 종목 묶음의 실현손익 + 진행 중 평가손익이 −1만 원 이하가 되면 C1의 주의 종목 신규 진입 중단
- 매수 시 호가 차이를 기록한다(왕복 비용 검토용)

python caution_study.py            실행
python caution_study.py --report   보고서만
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import exit_study as X
import strategy as st
import upbit
from bot import load_params, SETTLE
from shadow import proposal

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'data' / 'caution_study'
STOP = ROOT / 'stop_caution_study.txt'
LOCK = ROOT / 'caution_study.lock'
GROUPS = {
    'C0_경보차단': {'r_mode': 'atr', 'partial_r': 2.0, 'rebuy': False, 'caution': False},
    'C1_주의허용': {'r_mode': 'atr', 'partial_r': 2.0, 'rebuy': False, 'caution': True},
}
ALLOWED = {'PRICE_FLUCTUATIONS', 'TRADING_VOLUME_SOARING'}
MAX_SPREAD = 0.004
PILOT_LOSS = -10000.0
JUDGE = ('D-52·D-54와 같다: 72시간 중간 공개, 각 군 완결 묶음 20건과 자료 유효성을 충족하면 판정, 최대 144시간. '
         'C1이 C0보다 비용 후 묶음 평균 순손익이 높고 계좌 최대 낙폭이 나쁘지 않을 때만 채택 후보. 실거래 반영은 별도 합의.')


def caution_allowed(info):
    """주의 항목이 가격급변·거래량급등뿐이고 투자유의가 아니면 True. 정보가 없거나 형식이 다르면 False."""
    if not isinstance(info, dict) or info.get('investment_warning') or not info.get('warning'):
        return False
    types = info.get('caution_types')
    return isinstance(types, list) and bool(types) and set(types) <= ALLOWED


def rise_1h(bars):
    return len(bars) >= 5 and bars[-1]['c'] > bars[-5]['c']


def pilot_pnl(book, prices):
    """주의 종목 묶음의 실현손익 + 진행 중 평가손익(청산가치)."""
    ids = set(book.d.get('caution_bundles', []))
    realized = sum(b['pnl'] for b in book.d['bundles'] if b['bundle'] in ids)
    open_ = 0.0
    for m, P in book.d['pos'].items():
        if P['bundle'] in ids and m in prices:
            open_ += sum(X.liquidation(l['qty'], m, prices[m], book.p) for l in X.lots(P)) - P['spent'] + P['got']
    return realized + open_


class CautionStudy(X.Study):
    def __init__(self, folder=None):
        out = folder or OUT
        out.mkdir(parents=True, exist_ok=True)
        frozen = out / 'frozen.json'
        if not frozen.exists():
            base, cfg = load_params()
            live = cfg.get('live', {})
            frozen.write_text(json.dumps({
                'start': time.time(), 'start_kst': X.now_iso(), 'capital': X.CAPITAL, 'groups': GROUPS, 'rebuy': X.REBUY,
                'partial_frac': float(live.get('partial_frac', 1 / 3)), 'stop_tf': int(live.get('stop_tf', 240)),
                'params': X.asdict(X.replace(base, trail_enabled=True)), 'judge': JUDGE, 'fills': X.FILLS,
                'gap_grace': X.GAP_GRACE, 'caution': {'allowed': sorted(ALLOWED), 'max_spread': MAX_SPREAD,
                                                      'size': 0.5, 'max_open': 1, 'pilot_loss': PILOT_LOSS},
                'version': 1}, ensure_ascii=False, indent=1), encoding='utf-8')
        super().__init__(out)
        for bk in self.books.values():
            bk.p = self.base

    def decide(self, end):
        p = self.base
        self.markets = upbit.krw_markets()
        tick = upbit.tickers(self.markets)
        pool = sorted((m for m in tick if m not in p.exclude), key=lambda m: -tick[m]['acc_trade_price_24h'])
        universe = pool[:p.universe]
        held = {m for b in self.books.values() for m in b.d['pos']}
        bars, missing = {}, []
        for m in dict.fromkeys(universe + sorted(held)):
            b = upbit.completed_candles(m, p.tf, 200)
            if b and dt.datetime.fromisoformat(b[-1]['t']).timestamp() + p.tf * 60 == end:
                bars[m] = b
            elif m in universe:
                missing.append(m)
            time.sleep(0.12)
        if missing:
            if time.time() < end + X.GAP_GRACE:
                return False
            self.note_gap('판단 공백', end=end, missing=missing)
            for bk in self.books.values():
                bk.d['last_decision'] = end
                bk.save()
            return True
        uni = set(universe)
        self.ranked = st.rank({m: b for m, b in bars.items() if m in uni}, p)
        rs_out = {m: st.relative_strength(b, p.L) for m, b in bars.items() if m not in uni}
        strict = {m for m in universe if m in bars and not self.markets.get(m, {}).get('warning')}
        cautious = {m for m in universe if m in bars and caution_allowed(self.markets.get(m))}
        self.entry_ok = strict
        prices = {m: t['trade_price'] for m, t in tick.items()}
        caps = {'max_order_krw': 1e12, 'max_total_buy_krw': 1e12, 'max_sell_krw_per_plan': 1e12}
        plans = {}
        for name, bk in self.books.items():
            if bk.d['last_decision'] >= end:
                continue
            allow = bk.cfg.get('caution') and not bk.d.get('caution_halt')
            text, _ = proposal(bk.holdings(prices), self.ranked, rs_out, strict | (cautious if allow else set()), p, caps,
                               bk.d['cash'], 0, bk.d['last_sells'])
            plans[name] = (text, X.parse(text))
        need = sorted({m for _, orders in plans.values() for _, m, _ in orders})
        books = upbit.orderbooks(need) if need else {}
        atrs = {m: self.atr4(m) for _, orders in plans.values() for side, m, _ in orders if side == 'buy'}
        notes = []
        for name, (text, orders) in plans.items():
            bk = self.books[name]
            for side, m, _ in orders:
                if side == 'sell' and m in bk.d['pos']:
                    bk.close_all(m, books[m]['bid'], p, '순환')
            for side, m, krw in orders:
                if side != 'buy' or m in bk.d['pos'] or not atrs.get(m):
                    continue
                if m in cautious and m not in strict:
                    ask, bid = books[m]['ask'], books[m]['bid']
                    spread = (ask - bid) / ask if ask else 1.0
                    open_c = sum(1 for x, P in bk.d['pos'].items() if P['bundle'] in bk.d.get('caution_bundles', []))
                    why = ('주의 종목 동시 1개 상한' if open_c >= 1 else f'호가 차이 {spread:.2%} > 0.4%' if spread > MAX_SPREAD
                           else '최근 1시간 상승 아님' if not rise_1h(bars[m]) else '')
                    if why:
                        notes.append(f'{name} {m[4:]} 주의 종목 매수 안 함: {why}')
                        continue
                    P = bk.buy(m, krw / 2, ask, atrs[m], p)
                    if P:
                        bk.d.setdefault('caution_bundles', []).append(P['bundle'])
                        bk.d['trades'][-1].update(caution=True, spread=round(spread, 5),
                                                  types=self.markets[m]['caution_types'])
                    else:
                        notes.append(f'{name} {m[4:]} 주의 종목 매수 안 함: 1/2 금액이 최소 주문 미달')
                    continue
                bk.buy(m, krw, books[m]['ask'], atrs[m], p)
            if bk.cfg.get('caution') and not bk.d.get('caution_halt') and pilot_pnl(bk, prices) <= PILOT_LOSS:
                bk.d['caution_halt'] = X.now_iso()
                notes.append(f'{name} 주의 종목 손익 −1만 원 이하: 주의 종목 신규 진입 중단')
            bk.d['last_decision'] = end
            bk.save()
        with (self.out / 'decisions.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps({'time': X.now_iso(), 'end': end, 'universe': universe, 'cautious': sorted(cautious),
                                'ranked': [(r['market'], r['rank'], round(r['rs'], 5)) for r in self.ranked[:12]],
                                'plans': {n: t for n, (t, _) in plans.items()}, 'notes': notes}, ensure_ascii=False) + '\n')
        self.caution_report(prices)
        return True

    def caution_report(self, prices):
        bk = self.books['C1_주의허용']
        ids = set(bk.d.get('caution_bundles', []))
        done = [b for b in bk.d['bundles'] if b['bundle'] in ids]
        spreads = [t['spread'] for t in bk.d['trades'] if t.get('caution')]
        lines = ['', '## C1 주의 종목 매매만 따로', '',
                 f"매수 {len(spreads)}건 · 완결 {len(done)}건 · 실현 {sum(b['pnl'] for b in done):+,.0f}원 · "
                 f"실현+진행 중 평가 {pilot_pnl(bk, prices):+,.0f}원 · 매수 시 평균 호가 차이 "
                 f"{(sum(spreads) / len(spreads) * 100 if spreads else 0):.2f}% · "
                 f"{'신규 진입 중단 ' + bk.d['caution_halt'][5:16] if bk.d.get('caution_halt') else '진행 중'}"]
        (self.out / 'caution.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def report():
    md = X.report(OUT)
    extra = OUT / 'caution.md'
    if extra.exists():
        md += extra.read_text(encoding='utf-8')
        (OUT / 'report.md').write_text(md, encoding='utf-8')
    return md


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--report', action='store_true')
    a = ap.parse_args()
    if a.report:
        print(report())
        sys.exit()
    X.STOP = STOP  # exit_study의 반복이 이 실험의 중지 파일을 보게 한다
    STOP.unlink(missing_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        pid = LOCK.read_text().strip()
        if pid and pid in subprocess.run(['tasklist', '/FI', f'PID eq {pid}'], capture_output=True, text=True,
                                         encoding='mbcs', errors='replace').stdout:
            sys.exit('이미 실행 중입니다.')
        LOCK.unlink()
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    try:
        CautionStudy().run()
    finally:
        LOCK.unlink(missing_ok=True)
