"""매수 시점 모의 실험(2026-11-19 사용자 지시 "모의실험에 넣고 매일 보고해줘"). 읽기 전용, 주문 없음.

질문: 매시 정각 신호에 바로 사는 것 vs 되밀림을 기다렸다 사는 것, 어느 쪽이 나은가.
신호: P10(실제 규모 모의) 장부의 매수(= 매시 주문안이 고른 종목·시각·가격). 실행권이 어느 PC에 있든 이 PC 모의 장부에 계속 쌓인다.
매수 방식(가지):
  즉시      정각 신호 가격(P10 매수가)에 산다(지금 실거래 방식)
  되밀림0.5_30  신호 때 매도호가 −0.5% 지정가, 30분 안에 1분봉 저가가 그 아래로 내려오면 체결. 안 오면 건너뜀
  되밀림1.0_30  −1.0%, 30분
  되밀림0.5_60  −0.5%, 60분
  15분후    15분 기다렸다 그때 1분봉 종가 + P10과 같은 미끄러짐으로 산다
매도(모든 가지 같음, 체결 다음 분부터 1분봉): 손절 −3.5%(선 아래면 min(선, 시가)에서 실측 미끄러짐 0.27% 더) → 이익보호(+3% 닿은 뒤 최고 수익의 30% 반납) → 6시간 종가.
수수료 왕복 0.1%. 1건 6만 원으로 원화 환산.

  python tools/entry_timing/entry_timing.py            한 번 계산 + 오늘 보고서
  python tools/entry_timing/entry_timing.py --loop 1800 30분마다 계산, 매일 09:00 이후 첫 계산에서 전날 보고서
출력: 코인 트레이딩/v5-app/v5/data/entry_timing/results.json, 코인 트레이딩/reports/entry_timing_YYYYMMDD.md
"""
import argparse
import datetime as dt
import json
import statistics as S
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(r'C:\Users\User\Desktop\코인 트레이딩')
DATA = ROOT / 'v5-app' / 'v5' / 'data'
OUT = DATA / 'entry_timing'
REPORTS = ROOT / 'reports'
KST = dt.timezone(dt.timedelta(hours=9))
START = dt.datetime(2026, 11, 19, 0, 0, tzinfo=KST)     # 실험 시작(이전은 과거 재계산)
STOP, ARM, GIVE, HOLD_MIN, SLIP, FEE2, KRW = 0.035, 0.03, 0.3, 360, 0.0027, 0.001, 60000
ARMS = [('즉시', None, 0), ('되밀림0.5_30', 0.005, 30), ('되밀림1.0_30', 0.010, 30), ('되밀림0.5_60', 0.005, 60), ('15분후', 'wait', 15)]
WINDOW = 60 + HOLD_MIN + 2      # 분


def signals():
    out, seen = [], set()
    for line in open(DATA / 'P10' / 'trades.jsonl', encoding='utf-8'):
        try: r = json.loads(line)
        except ValueError: continue
        if r.get('side') != '매수': continue
        t = dt.datetime.fromisoformat(r['time'])
        k = (r['market'], t.strftime('%Y%m%d%H'))
        if k in seen: continue
        seen.add(k)
        px = float(r['price'])
        ask = float(r.get('ask') or px)
        out.append({'m': r['market'], 't': t, 'px': px, 'ask': ask, 'slip': max(0.0, px / ask - 1)})
    return out


def fetch(market, to):
    q = urllib.parse.urlencode({'market': market, 'to': to.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'count': 200})
    for i in range(6):
        try:
            time.sleep(0.2)
            with urllib.request.urlopen('https://api.upbit.com/v1/candles/minutes/1?' + q, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code != 429: raise
            time.sleep(1 + i)
        except urllib.error.URLError:
            time.sleep(2 + i)
    raise RuntimeError(f'{market} 1분봉 조회 실패')


def bars(sig):
    """신호 다음 분부터 WINDOW분의 1분봉 [(epoch, o, h, l, c)]. 창이 다 지나야 저장(그 전엔 None)."""
    s0 = int(sig['t'].timestamp()) // 60 * 60 + 60
    end = s0 + WINDOW * 60
    if time.time() < end + 120: return None
    f = OUT / 'candles' / f"{sig['m']}_{s0}.json"
    if f.exists(): return [tuple(x) for x in json.loads(f.read_text())]
    have, to = {}, dt.datetime.fromtimestamp(end, KST)
    while True:
        rows = fetch(sig['m'], to)
        if not rows: break
        for r in rows:
            k = int(dt.datetime.fromisoformat(r['candle_date_time_kst']).replace(tzinfo=KST).timestamp())
            if s0 <= k < end: have[k] = (k, r['opening_price'], r['high_price'], r['low_price'], r['trade_price'])
        first = min(int(dt.datetime.fromisoformat(r['candle_date_time_kst']).replace(tzinfo=KST).timestamp()) for r in rows)
        if first <= s0 or len(rows) < 200: break
        to = dt.datetime.fromtimestamp(first, KST)
    # 거래 없는 분은 봉이 없다 → 앞 종가로 채운다
    out, last = [], None
    for k in range(s0, end, 60):
        if k in have: last = have[k]; out.append(have[k])
        elif last: out.append((k, last[4], last[4], last[4], last[4]))
    if len(out) < WINDOW * 0.9: return []
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out))
    return out


def exit_from(entry, xs):
    stop, peak = entry * (1 - STOP), entry
    for b in xs[:HOLD_MIN]:
        _, o, h, l, c = b
        if l <= stop:
            return min(stop, o) * (1 - SLIP) / entry - 1 - FEE2, '손절'
        peak = max(peak, h)
        if peak >= entry * (1 + ARM) and c <= entry + (peak - entry) * (1 - GIVE):
            return c / entry - 1 - FEE2, '이익보호'
    return xs[min(HOLD_MIN, len(xs)) - 1][4] / entry - 1 - FEE2, '6시간'


def arm_result(sig, xs, kind, wait):
    if kind is None:
        return sig['px'], exit_from(sig['px'], xs)
    if kind == 'wait':
        e = xs[wait - 1][4] * (1 + sig['slip'])   # 시장가 매수: P10과 같은 미끄러짐
        return e, exit_from(e, xs[wait:])
    limit = sig['ask'] * (1 - kind)                # 지정가: 신호 때 매도호가 기준(미끄러짐 없음)
    for i, b in enumerate(xs[:wait]):
        if b[3] < limit:
            return limit, exit_from(limit, xs[i + 1:])
    return None, None


def compute():
    rows = []
    for sig in signals():
        xs = bars(sig)
        if not xs: continue
        r = {'m': sig['m'], 't': sig['t'].isoformat(), 'px': sig['px'], 'arms': {}}
        for name, kind, wait in ARMS:
            e, res = arm_result(sig, xs, kind, wait)
            r['arms'][name] = None if e is None else {'entry': e, 'ret': res[0], 'why': res[1]}
        rows.append(r)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'results.json').write_text(json.dumps({'updated': dt.datetime.now(KST).isoformat(timespec='seconds'), 'rows': rows},
                                                 ensure_ascii=False), encoding='utf-8')
    return rows


def table(rows, title):
    L = [f'### {title} (신호 {len(rows)}건)', '']
    if not rows: return L + ['- 아직 결과가 난 신호 없음(신호 뒤 7시간이 지나야 결과가 확정됨)', '']
    L += ['| 매수 방식 | 체결 | 평균 수익률 | 합계(1건 6만 원) | 승률 | 손절 | 진입가 차이(즉시 대비) | 놓친 신호의 즉시 매수 결과 |',
          '|---|---|---|---|---|---|---|---|']
    for name, _, _ in ARMS:
        got = [r for r in rows if r['arms'][name]]
        miss = [r for r in rows if not r['arms'][name]]
        if not got:
            L.append(f'| {name} | 0 | - | - | - | - | - | - |'); continue
        rets = [r['arms'][name]['ret'] for r in got]
        imp = [r['arms'][name]['entry'] / r['px'] - 1 for r in got]
        mret = [r['arms']['즉시']['ret'] for r in miss]
        L.append(f"| {name} | {len(got)}/{len(rows)} | {S.mean(rets):+.2%} | {sum(rets) * KRW:+,.0f}원 | {sum(x > 0 for x in rets) / len(rets):.0%} | "
                 f"{sum(r['arms'][name]['why'] == '손절' for r in got)} | {S.mean(imp):+.2%} | "
                 f"{(f'{len(miss)}건 평균 {S.mean(mret):+.2%}' if miss else '-')} |")
    return L + ['']


def report(rows, day):
    """day(KST 날짜) 하루 보고서 + 실험 시작 뒤 누적 + 과거 재계산."""
    def d(r): return dt.datetime.fromisoformat(r['t']).astimezone(KST)
    today = [r for r in rows if d(r).date() == day]
    fwd = [r for r in rows if d(r) >= START]
    back = [r for r in rows if d(r) < START]
    L = [f'# 매수 시점 모의 실험 보고 ({day:%Y-%m-%d}, 작성 {dt.datetime.now(KST):%m-%d %H:%M}, 단독)', '',
         '- 신호: P10 모의 장부의 매시 매수. 매도 규칙은 모든 방식이 같음(손절 3.5%·이익보호 +3%/30% 반납·6시간). 1분봉 모의, 실제 체결 아님.',
         '- 되밀림 방식은 지정가가 안 닿으면 사지 않는다 → "놓친 신호" 칸이 그 대가.', '']
    L += table(today, f'{day:%m-%d} 하루')
    L += table(fwd, f'실험 시작({START:%m-%d}) 뒤 누적')
    L += table(back, '과거 재계산(11-02~11-18, 참고)')
    L += ['주의: 기간이 짧으면 장세 영향이 크다. 2주 이상 같은 방향이 나와야 실거래 변경을 검토한다.']
    REPORTS.mkdir(exist_ok=True)
    f = REPORTS / f'entry_timing_{day:%Y%m%d}.md'
    f.write_text('\n'.join(L) + '\n', encoding='utf-8')
    return f


def single_instance():
    import msvcrt
    OUT.mkdir(parents=True, exist_ok=True)
    f = (OUT / 'entry_timing.lock').open('a+b')
    f.seek(0); f.write(b'0'); f.flush(); f.seek(0)
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return None
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--loop', type=int, default=0)
    a = ap.parse_args()
    if not a.loop:
        rows = compute()
        print(report(rows, dt.datetime.now(KST).date()))
        return 0
    lock = single_instance()
    if not lock: return 0
    done = OUT / 'last_daily.txt'
    while not (OUT / 'stop.txt').exists():
        try:
            rows = compute()
            now = dt.datetime.now(KST)
            if now.hour >= 9:
                y = (now - dt.timedelta(days=1)).date()
                if (done.read_text().strip() if done.exists() else '') != str(y):
                    report(rows, y); done.write_text(str(y))
            report(rows, now.date())       # 오늘 진행분(계속 덮어씀)
        except Exception as e:
            with (OUT / 'error.log').open('a', encoding='utf-8') as log:
                log.write(f'{dt.datetime.now(KST):%m-%d %H:%M} {type(e).__name__}: {e}\n')
        time.sleep(a.loop)
    return 0


if __name__ == '__main__':
    sys.exit(main())
