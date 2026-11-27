"""이익보호 매도 뒤 재상승 포착 방법 과거 자료 비교 (2026-11-27, 사용자 요청 "과거 자료로 시뮬레이션을 통해 결과 비교").

분석 전용. 주문·설정·실행 중 봇과 무관. 공개 15분봉만 조회(계좌 키 사용 안 함).

대상: V5 실거래 매도 중 사유가 '이익보호' 또는 '계단 보호'인 매도(장부 orders.jsonl에서 사유 확인된 것).
비교(모든 방법에 같은 청산 규칙·비용, 순위 조건은 재현 못 해 모두 생략):
  NOW  현행 규칙: 매도 4시간 뒤~24시간 안, 15분 종가 <= 매도가 - 0.5ATR 이면 다음 봉 시가 매수
  A    재돌파: 매도 뒤 24시간 안, 종가 > max(매도가, 매도 뒤 최고가) + 0.3ATR 이면 다음 봉 시가 매수(대기 없음)
  B    눌림 후 반등: 매도가 - 1ATR 아래로 눌린 뒤, 종가 >= 매도 뒤 최저가 + 1.5ATR 이면 다음 봉 시가 매수(24시간 안)
  C1/C2 절반 남기기: 이익보호 때 절반만 판 것으로 보고, 나머지 절반을 추적 손절로 보유
        C1 = 최고가 - 3×ATR(15분), C2 = 최고가 - 3×ATR(1시간). 손절선은 평균 매수가(본전) 아래로 내리지 않음. 최대 72시간.
재매수 포지션 청산(실거래 설정 근사): 손절 -3.5%, 이익보호 +3% 도달 뒤 이익의 30% 반납 시 매도, 최대 72시간.
  같은 봉에서 손절·이익보호가 겹치면 손절 우선(보수적). 1/3 부분 익절·순위 매도는 생략.
비용: 매수·매도 각각 수수료 0.05% + 미끄러짐 0.1%.
ATR: 매도 직전까지 완료된 14개 봉 단순 평균 TR.
"""
import datetime as dt
import glob
import json
import os
import statistics as stx
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

APP = Path(r'C:\Users\User\Desktop\코인 트레이딩\v5-app\v5\data')
OUT = Path(os.environ.get('SIM_OUT', str(APP / 'analysis_20261127')))   # 거래별 결과·시세 캐시는 실행본 data(공개 저장소 밖)
CACHE = Path(os.environ.get('SIM_CACHE', str(OUT / 'candles')))
KST = dt.timezone(dt.timedelta(hours=9))
UTC = dt.timezone.utc
COST = 0.0015          # 한쪽 수수료+미끄러짐
STOP, ARM, GIVE = 0.035, 0.03, 0.30
WIN_H, HOLD_H = 24, 72
BAR = 900


def reasons():
    out = {}
    for f in [APP / 'live' / 'orders.jsonl', *glob.glob(str(APP / 'live_이전장부_*' / 'orders.jsonl'))]:
        for line in open(f, encoding='utf-8'):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get('identifier') and r.get('side') == '매도':
                out[r['identifier']] = r.get('reason', '')
    return out


def sells():
    """거래소 주문 기록을 평균단가법으로 재생해 V5 매도마다 (시각, 종목, 가격, 수량, 평균매수가, 식별자)."""
    orders = json.load(open(APP / 'pnl' / 'orders.json', encoding='utf-8'))['orders']
    ev = []
    for r in orders.values():
        tr = r.get('trades') or []
        if not tr:
            continue
        q = sum(float(x['volume']) for x in tr); f = sum(float(x['funds']) for x in tr)
        ev.append((min(x['created_at'] for x in tr), r['market'], r['side'], q, f, float(r.get('paid_fee') or 0), str(r.get('identifier') or '')))
    ev.sort()
    pos, out = {}, []
    for t, m, side, q, f, fee, ident in ev:
        p = pos.setdefault(m, [0., 0.])
        if side == 'bid':
            p[0] += q; p[1] += f + fee
        else:
            avg = p[1] / p[0] if p[0] > 1e-12 else None
            if ident.startswith('v5-') and avg:
                out.append({'time': dt.datetime.fromisoformat(t), 'market': m, 'price': f / q, 'qty': q, 'avg': avg, 'id': ident,
                            'net': f - fee})
            if p[0] > 1e-12:
                take = min(q, p[0]); p[1] -= p[1] * take / p[0]; p[0] -= take
    return out


def fetch(market, start, end):
    """[start, end) 15분봉. 캐시 사용. 각 봉: (epoch 시작, o, h, l, c)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f'{market}.json'
    have = {int(k): v for k, v in json.load(open(f)).items()} if f.exists() else {}
    need_to = end
    s_ep, e_ep = int(start.timestamp()) // BAR * BAR, int(end.timestamp()) // BAR * BAR
    if not all(t in have for t in range(s_ep, min(e_ep, int(time.time()) // BAR * BAR - BAR), BAR)):
        to = end
        while to.timestamp() > s_ep:
            q = urllib.parse.urlencode({'market': market, 'count': 200, 'to': to.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%S')})
            for attempt in range(5):
                try:
                    with urllib.request.urlopen(urllib.request.Request('https://api.upbit.com/v1/candles/minutes/15?' + q,
                                                                       headers={'Accept': 'application/json'}), timeout=10) as r:
                        rows = json.load(r)
                    break
                except Exception:
                    time.sleep(1 + attempt)
            else:
                raise RuntimeError(f'candles {market}')
            time.sleep(0.12)
            if not rows:
                break
            for r in rows:
                t = int(dt.datetime.fromisoformat(r['candle_date_time_utc']).replace(tzinfo=UTC).timestamp())
                have[t] = [r['opening_price'], r['high_price'], r['low_price'], r['trade_price']]
            to = dt.datetime.fromtimestamp(min(int(dt.datetime.fromisoformat(r['candle_date_time_utc']).replace(tzinfo=UTC).timestamp()) for r in rows), UTC)
        json.dump({str(k): v for k, v in have.items()}, open(f, 'w'))
    now_done = int(time.time()) // BAR * BAR - BAR
    bars, last = [], None
    for t in range(s_ep, min(e_ep, now_done + BAR), BAR):
        v = have.get(t)
        if v is None:   # 거래 없는 봉: 직전 종가로 채움
            if last is None:
                continue
            v = [last, last, last, last]
        bars.append((t, *v)); last = v[3]
    return bars


def atr(bars, n=14):
    trs = [max(h - l, abs(h - bars[i - 1][4]), abs(l - bars[i - 1][4])) for i, (_, o, h, l, c) in enumerate(bars) if i > 0]
    trs = trs[-n:]
    return sum(trs) / len(trs) if trs else None


def hourly(bars):
    out = []
    for i in range(0, len(bars) - 3, 4):
        g = bars[i:i + 4]
        out.append((g[0][0], g[0][1], max(x[2] for x in g), min(x[3] for x in g), g[-1][4]))
    return out


def manage(path, entry_i, notional):
    """path[entry_i] 시가에 매수 후 실거래 근사 규칙으로 청산. (순손익, 수익률, 청산사유, 보유시간h, 열림여부)"""
    e = path[entry_i][1] * (1 + COST)
    stop = e * (1 - STOP); peak = 0.
    end = min(len(path), entry_i + HOLD_H * 4)
    for j in range(entry_i, end):
        t, o, h, l, c = path[j]
        if l <= stop:
            px = min(stop, o); why = '손절'
            break
        if peak >= ARM:
            line = e * (1 + peak * (1 - GIVE))
            if l <= line:
                px = min(line, o); why = '이익보호'
                break
        peak = max(peak, h / e - 1)
    else:
        px = path[end - 1][4]; why = '기한' if end == entry_i + HOLD_H * 4 else '진행중'
        j = end - 1
    ret = px * (1 - COST) / e - 1
    return notional * ret, ret, why, (j - entry_i + 1) / 4


def rebuy_trigger(method, path, sp, a):
    """path: 매도 다음 봉부터. 매수할 봉 인덱스(그 봉 시가) 또는 None."""
    hi, lo, dipped = sp, sp, False
    for i in range(min(len(path) - 1, WIN_H * 4)):
        t, o, h, l, c = path[i]
        if method == 'NOW':
            if i >= 16 and c <= sp - 0.5 * a:   # 4시간 = 16봉 뒤부터
                return i + 1
        elif method == 'A':
            if c > max(sp, hi) + 0.3 * a:
                return i + 1
        elif method == 'B':
            lo = min(lo, l)
            if lo <= sp - 1.0 * a:
                dipped = True
            if dipped and c >= lo + 1.5 * a:
                return i + 1
        hi = max(hi, h)
    return None


def half_hold(path, sp, avg, a, qty):
    """C: 나머지 절반 추적 보유. 매도가 대비 추가 손익(원)·청산가."""
    peak = sp; px = None
    end = min(len(path), HOLD_H * 4)
    for i in range(end):
        t, o, h, l, c = path[i]
        line = max(avg, peak - 3 * a)
        if l <= line:
            px = min(line, o); break
        peak = max(peak, h)
    if px is None:
        px = path[end - 1][4] if end else sp
    half = qty / 2
    return half * (px * (1 - COST) - sp * (1 - COST)), px


def main():
    rs = reasons()
    ev = [s for s in sells() if rs.get(s['id'], '').startswith(('이익보호', '계단 보호'))]
    ev = [s for s in ev if s['price'] > s['avg']]  # 실제로 이익인 매도만
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for s in ev:
        t0 = s['time']
        bars = fetch(s['market'], t0 - dt.timedelta(hours=30), t0 + dt.timedelta(hours=WIN_H + HOLD_H + 1))
        cut = int(t0.timestamp()) // BAR * BAR      # 매도가 일어난 봉 시작
        prior = [b for b in bars if b[0] < cut]
        path = [b for b in bars if b[0] > cut]       # 매도 다음 봉부터
        if len(prior) < 60 or len(path) < 8:
            continue
        a15 = atr(prior); a60 = atr(hourly(prior[-(len(prior) // 4) * 4:]))
        sp, n = s['price'], s['price'] * s['qty']
        fut24 = [b for b in path[:96]]; fut72 = path[:288]
        r = {'time': t0.astimezone(KST).strftime('%m-%d %H:%M'), 'market': s['market'][4:], 'sell': sp, 'avg': s['avg'],
             'gain': sp / s['avg'] - 1, 'notional': n, 'atr15_pct': a15 / sp, 'atr60_pct': a60 / sp,
             'max24': max(b[2] for b in fut24) / sp - 1, 'max72': max(b[2] for b in fut72) / sp - 1,
             'hours_after': len(path) / 4}
        for m in ('NOW', 'A', 'B'):
            k = rebuy_trigger(m, path, sp, a15)
            r[m] = None if k is None or k >= len(path) else (*manage(path, k, n), k / 4)
        r['C1'] = half_hold(path, sp, s['avg'], a15, s['qty'])
        r['C2'] = half_hold(path, sp, s['avg'], a60, s['qty'])
        rows.append(r)
    json.dump(rows, open(OUT / 'profit_rebuy_sim_20261127.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    report(rows)


def report(rows):
    n = len(rows)
    print(f'대상 이익 매도 {n}건 ({rows[0]["time"]} ~ {rows[-1]["time"]})')
    g = [r['gain'] for r in rows]
    print(f'실제 매도 이익률 중앙 {stx.median(g):+.2%}, 평균 {stx.mean(g):+.2%}')
    for key in ('max24', 'max72'):
        v = [r[key] for r in rows]
        print(f'매도 뒤 {key[3:]}시간 최고가(매도가 대비): 중앙 {stx.median(v):+.1%} · +5%↑ {sum(x >= .05 for x in v)} · +10%↑ {sum(x >= .10 for x in v)} · +20%↑ {sum(x >= .20 for x in v)} · +30%↑ {sum(x >= .30 for x in v)}')
    print()
    print('방법   발동  승  패  합계손익(원)  평균수익률  중앙   최고   최악   손절/이익보호/기한/진행')
    for m in ('NOW', 'A', 'B'):
        hit = [r[m] for r in rows if r[m]]
        if not hit:
            print(f'{m:5}  0'); continue
        rets = [h[1] for h in hit]; why = [h[2] for h in hit]
        print(f'{m:5} {len(hit):4} {sum(x > 0 for x in rets):3} {sum(x <= 0 for x in rets):3} {sum(h[0] for h in hit):12,.0f}  {stx.mean(rets):+8.2%} {stx.median(rets):+6.2%} {max(rets):+6.1%} {min(rets):+6.1%}   '
              f"{why.count('손절')}/{why.count('이익보호')}/{why.count('기한')}/{why.count('진행중')}")
    for m in ('C1', 'C2'):
        v = [r[m][0] for r in rows]; pct = [r[m][1] / r['sell'] - 1 for r in rows]
        print(f'{m:5} {n:4} {sum(x > 0 for x in v):3} {sum(x <= 0 for x in v):3} {sum(v):12,.0f}  절반 청산가 매도가 대비 평균 {stx.mean(pct):+.2%} 중앙 {stx.median(pct):+.2%} 최고 {max(pct):+.1%} 최악 {min(pct):+.1%}')
    big = [r for r in rows if r['max72'] >= .10]
    print(f'\n매도 뒤 72시간 안 +10% 이상 오른 {len(big)}건에서 방법별 포착')
    for r in big:
        cells = []
        for m in ('NOW', 'A', 'B'):
            cells.append(f"{m} {r[m][1]:+.1%}" if r[m] else f'{m} -')
        cells.append(f"C2 절반 {r['C2'][1] / r['sell'] - 1:+.1%}")
        print(f"  {r['time']} {r['market']:8} 매도이익 {r['gain']:+.1%} 최고 {r['max72']:+.1%} | " + ' · '.join(cells))


if __name__ == '__main__':
    if '--report' in sys.argv:
        report(json.load(open(OUT / 'profit_rebuy_sim_20261127.json', encoding='utf-8')))
    else:
        main()
