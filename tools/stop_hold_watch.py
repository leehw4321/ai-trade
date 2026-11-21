"""손절 버팀·재매수 규칙 관찰기(2026-11-21 사용자 지시 '스터디를 기록해놓고 관찰해'). 읽기 전용: 주문·장부·설정을 바꾸지 않는다.

배경: docs/ai-history/2026-11-21-stoploss-after-study.md. 180일 검증(study_stop_hold_rule.py)은 불통과였다. 이 관찰기는 앞으로 생기는
실제 손절마다 같은 규칙을 '했다면'으로 계산해 실거래 자료로 다시 확인한다(검증 4단계).

- 자료: 실행본 data/pnl/orders.json(pnl.py가 5분마다 거래소에서 받는 계좌 전체 체결, v5- 주문만) → 종목별 왕복.
- 손절 = 왕복 순손익(수수료 0.1% 포함) ≤ −1.5%.
- 조건 ① = 손절 체결 봉 시작 전에 마감된 15분봉으로 ATR14/종가 ≥ 1.5% 그리고 24시간 수익 ≥ +3%.
- 규칙(study_stop_hold_rule.py와 같은 값):
  S1 손절 대신 −10% 강제선·매수가 +5% 익절, 손절 순간부터 72h / S2 −10% 강제선·이익보호(+3% 시작·30% 반납) 유지, 72h /
  S3 −8% 강제선·+3% 익절, 24h / R1 손절 뒤 24h 안 15분 종가 ≥ 매도가×1.02면 다음 봉 시가 재매수(손절 3.5%·이익보호·72h) /
  R2 24h 안 종가 ≥ 처음 매수가면 재매수. 재매수 1회 비용 0.4%(수수료+미끄러짐 근사).
- 차이 = 규칙 결과 − 실제 손절 결과(%p, 매수 금액 대비). 결과 창이 다 지나야 확정(최대 96h), 그 전은 '진행'.
- 판정 기준(사전 고정): 관찰 시작(2026-11-21) 뒤 ① 손절 30건 이상에서 S1~S3 중 평균 차이 > 0 이고 개선 건이 절반 이상인 규칙이 있으면 '재검토 가치', 아니면 '적용 안 함 유지'.
- 출력: 실행본 data/stop_hold_watch/{rows.json, summary.md, rows.csv}. 공개 시세만 호출(키 없음).
  python tools/stop_hold_watch.py            한 번 계산
  python tools/stop_hold_watch.py --watch    1시간마다(정지: data/stop_hold_watch/stop.txt)
"""
import argparse, datetime as dt, json, os, sys, time, urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / '코인 트레이딩' / 'v5-app' / 'v5'
KST = dt.timezone(dt.timedelta(hours=9))
B, H = 900, 3600
FEE = 0.001
STOP_RET = -0.015
ATR_MIN, R24_MIN = 0.015, 0.03
STOP, GUARD = 0.035, (0.03, 0.30)
RE_COST = 0.004
WATCH_START = dt.datetime(2026, 11, 21, tzinfo=KST).timestamp()
NEED = 30
RULES = {'S1': ('hold', .10, .05, 72, None), 'S2': ('hold', .10, None, 72, GUARD), 'S3': ('hold', .08, .03, 24, None),
         'R1': ('re', 'sell'), 'R2': ('re', 'entry')}
WINDOW_H = 96


def trips(orders):
    fills = []
    for o in orders:
        if not str(o.get('identifier') or '').startswith('v5-'):
            continue
        for tr in o.get('trades') or []:
            fills.append((dt.datetime.fromisoformat(tr['created_at']).timestamp(), o['market'], o['side'],
                          float(tr['volume']), float(tr['funds'])))
    fills.sort()
    cur, out = {}, []
    for t, m, side, v, f in fills:
        x = cur.get(m)
        if side == 'bid':
            if x is None:
                x = cur[m] = {'m': m, 'open': t, 'q': 0., 'bf': 0., 'bq': 0., 'sf': 0., 'sq': 0.}
            x['q'] += v; x['bf'] += f; x['bq'] += v
        elif x is not None and x['q'] > 0:
            take = min(v, x['q'])
            x['q'] -= take; x['sf'] += f * take / v; x['sq'] += take; x['close'] = t
            if x['q'] <= 1e-8 or x['q'] * (f / v) < 5000:
                cur.pop(m)
                bp, sp = x['bf'] / x['bq'], x['sf'] / x['sq']
                out.append({'m': m, 'open': x['open'], 'close': x['close'], 'buy': bp, 'sell': sp, 'ret': sp / bp - 1 - FEE})
    return out


def fetch(m, start, end, get=None):
    """15분봉 {시작 epoch: (o,h,l,c)} [start, end)."""
    get = get or _get
    out, to = {}, end
    while to > start:
        u = dt.datetime.fromtimestamp(to, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        rows = get(f'https://api.upbit.com/v1/candles/minutes/15?market={m}&count=200&to={u}')
        if not rows:
            break
        for r in rows:
            t = dt.datetime.fromisoformat(r['candle_date_time_kst']).replace(tzinfo=KST).timestamp()
            out[t] = (r['opening_price'], r['high_price'], r['low_price'], r['trade_price'])
        to = min(out)
        if len(rows) < 200:
            break
        time.sleep(0.12)
    return {k: v for k, v in out.items() if start <= k < end}


def _get(url):
    for i in range(5):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(1 + i)
    raise RuntimeError('시세 조회 실패')


def cond1(bars, s):
    past = [bars.get(s - k * B) for k in range(1, 16)]
    if any(x is None for x in past):
        return None
    tr = [max(b[1], p[3]) - min(b[2], p[3]) for b, p in zip(past[:14], past[1:15])]
    c = past[0][3]
    old = next((bars[s - k * B] for k in range(97, 101) if s - k * B in bars), None)
    if old is None:
        return None
    atr, r24 = sum(tr) / 14 / c, c / old[3] - 1
    return {'hit': atr >= ATR_MIN and r24 >= R24_MIN, 'atr': atr, 'r24': r24}


def run_exit(bars, s, end, entry, stop, guard, tp, peak=None):
    peak = entry if peak is None else peak
    last = None
    while s < end:
        b = bars.get(s)
        if b is not None:
            last = b
            if b[2] <= stop:
                return min(stop, b[0]) / entry - 1, 'stop'
            if guard and peak >= entry * (1 + guard[0]):
                gs = entry + (peak - entry) * (1 - guard[1])
                if b[2] <= gs:
                    return min(gs, b[0]) / entry - 1, 'guard'
            if tp and b[1] >= entry * (1 + tp):
                return max(entry * (1 + tp), b[0]) / entry - 1, 'tp'
            peak = max(peak, b[1])
        s += B
    return (last[3] / entry - 1, 'time') if last else (None, 'none')


def evaluate(t, bars):
    """손절 왕복 t의 규칙별 (차이, 사유)."""
    s = int(t['close'] // B * B)
    hold = [b for k, b in bars.items() if t['open'] <= k < s]
    peak0 = max([t['buy']] + [b[1] for b in hold])
    res = {}
    for name, spec in RULES.items():
        if spec[0] == 'hold':
            _, wide, tp, hrs, g = spec
            r, why = run_exit(bars, s, s + hrs * H, t['buy'], t['buy'] * (1 - wide), g, tp, peak=peak0)
            res[name] = (None if r is None else (r - FEE) - t['ret'], why)
        else:
            trig = t['sell'] * 1.02 if spec[1] == 'sell' else t['buy']
            k, extra, why = s + B, 0.0, '재매수 없음'
            while k < s + 24 * H:
                b = bars.get(k)
                if b is not None and b[3] >= trig:
                    nb = next((k + j * B for j in range(1, 5) if k + j * B in bars), None)
                    if nb:
                        e2 = bars[nb][0]
                        r, w = run_exit(bars, nb, nb + 72 * H, e2, e2 * (1 - STOP), GUARD, None)
                        if r is not None:
                            extra, why = r - RE_COST, '재매수-' + w
                    break
                k += B
            res[name] = (extra, why)
    return res


def update(app=APP, now=None, get=None):
    now = now or time.time()
    out_dir = app / 'data' / 'stop_hold_watch'
    out_dir.mkdir(parents=True, exist_ok=True)
    f = out_dir / 'rows.json'
    rows = json.loads(f.read_text(encoding='utf-8')) if f.exists() else {}
    orders = json.loads((app / 'data' / 'pnl' / 'orders.json').read_text(encoding='utf-8'))['orders'].values()
    for t in trips(orders):
        if t['ret'] > STOP_RET:
            continue
        key = f"{t['m']}|{int(t['close'])}"
        if rows.get(key, {}).get('done'):
            continue
        end = t['close'] + WINDOW_H * H
        bars = fetch(t['m'], t['open'] - 26 * H, min(now, end) + B, get)
        c = cond1(bars, int(t['close'] // B * B))
        ev = evaluate(t, bars)
        rows[key] = {**t, 'forward': t['close'] >= WATCH_START, 'cond': c, 'rules': ev, 'done': now >= end + B}
    tmp = f.with_suffix('.tmp'); tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8'); os.replace(tmp, f)
    (out_dir / 'summary.md').write_text(summary(rows), encoding='utf-8')
    write_csv(out_dir, rows)
    return rows


def summary(rows):
    def fmt(t): return dt.datetime.fromtimestamp(t, KST).strftime('%m-%d %H:%M')
    L = [f'# 손절 버팀·재매수 관찰 ({dt.datetime.now(KST):%Y-%m-%d %H:%M} 갱신)', '',
         '조건 ① = 손절 순간 15분 ATR ≥ 1.5% & 24h 수익 ≥ +3%. 차이 = 규칙대로 했다면 − 실제 손절(%p). 확정 = 결과 창(최대 96h) 지남.',
         f'판정 기준(사전 고정): 관찰 시작 11-21 이후 ① 확정 {NEED}건 이상에서 평균 차이 > 0 이고 개선 건 절반 이상인 버팀 규칙이 있으면 재검토 가치.', '']
    for title, sel in (('관찰 시작 뒤(앞으로의 실거래)', True), ('참고: 시작 전 실거래(가설을 만든 자료)', False)):
        part = [r for r in rows.values() if r['forward'] == sel]
        hit = [r for r in part if r['cond'] and r['cond']['hit']]
        done = [r for r in hit if r['done']]
        L.append(f'## {title}: 손절 {len(part)}건 · ① {len(hit)}건 (확정 {len(done)})')
        for name in RULES:
            for lab, grp in (('①', [r for r in part if r['cond'] and r['cond']['hit'] and r['done']]),
                             ('①아님', [r for r in part if r['cond'] and not r['cond']['hit'] and r['done']])):
                d = [r['rules'][name][0] for r in grp if r['rules'][name][0] is not None]
                if d:
                    L.append(f'- {name} {lab}: {len(d)}건 평균 {sum(d)/len(d)*100:+.2f}%p · 합 {sum(d)*100:+.1f}%p · 개선 {sum(x > 0 for x in d)}건')
        if sel:
            ok = [n for n in ('S1', 'S2', 'S3') if len(done) >= NEED and
                  (lambda d: d and sum(d) / len(d) > 0 and sum(x > 0 for x in d) * 2 >= len(d))(
                      [r['rules'][n][0] for r in done if r['rules'][n][0] is not None])]
            L.append(f"- 판정: {'재검토 가치 ' + ','.join(ok) if ok else ('적용 안 함 유지' if len(done) >= NEED else f'자료 모으는 중({len(done)}/{NEED})')}")
        L.append('')
    L.append('## 최근 손절 20건')
    for r in sorted(rows.values(), key=lambda r: -r['close'])[:20]:
        c = r['cond']
        L.append(f"- {fmt(r['close'])} {r['m'][4:]} {r['ret']*100:+.2f}% · " + ('① ' if c and c['hit'] else '') +
                 (f"ATR {c['atr']*100:.1f}% 24h {c['r24']*100:+.1f}%" if c else '자료 부족') + ' · ' +
                 ' '.join(f"{n} {v[0]*100:+.1f}" if v[0] is not None else f'{n} -' for n, v in r['rules'].items()) +
                 ('' if r['done'] else ' (진행)'))
    return '\n'.join(L) + '\n'


def write_csv(out_dir, rows):
    head = '매도 시각,코인,실제 손익률,조건1,ATR,24h수익,' + ','.join(f'{n} 차이' for n in RULES) + ',확정,관찰 시작 뒤'
    lines = [head]
    for r in sorted(rows.values(), key=lambda r: r['close']):
        c = r['cond'] or {}
        lines.append(','.join(str(x) for x in (
            dt.datetime.fromtimestamp(r['close'], KST).strftime('%Y-%m-%d %H:%M'), r['m'][4:], f"{r['ret']:.4f}",
            'Y' if c.get('hit') else ('N' if c else '?'), f"{c.get('atr', 0):.4f}", f"{c.get('r24', 0):.4f}",
            *[('' if v[0] is None else f'{v[0]:.4f}') for v in r['rules'].values()], 'Y' if r['done'] else 'N', 'Y' if r['forward'] else 'N')))
    try:
        (out_dir / 'rows.csv').write_text('\n'.join(lines) + '\n', encoding='utf-8-sig')
    except OSError:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument('--watch', action='store_true'); ap.add_argument('--app', default=str(APP))
    a = ap.parse_args(argv); app = Path(a.app)
    stop = app / 'data' / 'stop_hold_watch' / 'stop.txt'
    while True:
        try:
            rows = update(app)
            print(f'[{dt.datetime.now(KST):%H:%M}] 손절 {len(rows)}건 갱신', flush=True)
        except Exception as ex:
            print(f'[{dt.datetime.now(KST):%H:%M}] 오류 {type(ex).__name__}: {ex}', flush=True)
        if not a.watch:
            return 0
        for _ in range(360):
            if stop.exists():
                stop.unlink(missing_ok=True); return 0
            time.sleep(10)


if __name__ == '__main__':
    sys.exit(main())
