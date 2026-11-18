"""손절 상한 3.5% → 3.0% 가정 분석 (2026-11-18 사용자 요청: "거래와 손절 그리고 지금 가상거래 상황을 모두 조사해서
손절라인을 3.0으로 올릴때 어떤 차이가 있는지 분석"). 읽기 전용, 주문 없음.

  python tools/analysis_20261118/stop030_whatif.py [--cache 폴더]  → 코인 트레이딩/reports/stop030_whatif_YYYYMMDD_HHMM.md

대상
  1) 실거래 왕복(거래소 체결 exchange_orders.jsonl, v5- 주문) 11-02~현재
  2) 모의 장부: P10(실제 규모) + 격자 12개 + EXIT 3개 (data/P10, data/shadow/*/trades.jsonl)
  3) 매수 쉬기 중 가상 매수(data/buy_pause.json virtual)
방법(같은 규칙으로 3.0%와 3.5%를 각각 다시 계산해 둘을 비교한다)
  - 업비트 1분봉 저가로, 매수 다음 분부터 실제 첫 매도까지 '진입가×(1−X)'를 처음 건드린 시점을 찾는다.
    매도한 그 분의 봉은 실제 매도가가 선 아래였을 때만 인정(같은 분 안의 순서를 모르므로).
  - 건드렸으면 그 선에서 실측 미끄러짐(실거래 손절 매도가 vs 손절선 중앙값)만큼 낮게 판 것으로 본다. 아니면 실제 결과 그대로.
  - 매수 때 손절가(ATR 기준)가 이미 선보다 가까우면 상한이 영향을 주지 않으므로 그대로 둔다.
    실거래는 매수 손절가 기록이 없어 같은 종목·1시간 안의 P10 모의 매수 손절가로 추정한다(없으면 상한이 결정한 것으로 본다).
  - 1회 매수 금액은 그대로(entry_risk를 손절폭에 비례해 함께 내리는 지금 방식이면 금액이 같다).
한계: 손절 뒤 빈 자리에 다른 종목을 사는 효과·재매수 금지·손익 집계 중단선 영향은 넣지 않았다.
"""
import argparse
import bisect
import datetime as dt
import glob
import json
import statistics as S
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(r'C:\Users\User\Desktop\코인 트레이딩')
APP = ROOT / 'v5-app' / 'v5'
DATA = APP / 'data'
OUT = ROOT / 'reports'
KST = dt.timezone(dt.timedelta(hours=9))
CUT = dt.datetime(2026, 11, 11, 9, 45, tzinfo=KST)
FEE = 0.0005
CAPS = (0.035, 0.030)


# ---------- 자료 ----------
def live_trips():
    reason = {}
    for f in glob.glob(str(DATA / 'live*' / 'orders.jsonl')):
        for line in open(f, encoding='utf-8'):
            try: r = json.loads(line)
            except ValueError: continue
            if r.get('identifier') and r.get('reason'): reason[r['identifier']] = r['reason']
    fills = []
    for line in open(DATA / 'exchange_orders.jsonl', encoding='utf-8'):
        r = json.loads(line)
        if not str(r.get('identifier') or '').startswith('v5-') or float(r.get('executed_volume') or 0) <= 0: continue
        fills.append({'m': r['market'], 'side': r['side'], 't': dt.datetime.fromisoformat(r['created_at']),
                      'v': float(r['executed_volume']), 'f': float(r['executed_funds']), 'fee': float(r['paid_fee']), 'id': r['identifier']})
    fills.sort(key=lambda x: x['t'])
    trips, cur = [], {}
    for e in fills:
        t = cur.get(e['m'])
        if e['side'] == 'bid':
            if t is None:
                t = cur[e['m']] = {'m': e['m'], 'open': e['t'], 'qty': 0., 'bq': 0., 'bf': 0., 'cost': 0., 'sq': 0., 'proc': 0., 'why': []}
            t['qty'] += e['v']; t['bq'] += e['v']; t['bf'] += e['f']; t['cost'] += e['f'] + e['fee']
        else:
            if t is None: continue
            t.setdefault('first_sell', e['t']); t.setdefault('first_px', e['f'] / e['v'])
            t['qty'] -= e['v']; t['sq'] += e['v']; t['proc'] += e['f'] - e['fee']; t['close'] = e['t']
            t['why'].append(reason.get(e['id'], '?'))
            if t['qty'] <= 1e-8 or t['qty'] * (t['bf'] / t['bq']) < 5000:
                trips.append(t); cur.pop(e['m'])
    out = []
    for t in trips:
        inv = t['cost'] * t['sq'] / t['bq']
        px = t['bf'] / t['bq']
        why = '/'.join(dict.fromkeys(t['why']))
        out.append({'src': 'live', 'm': t['m'], 't_in': t['open'], 't_out': t['first_sell'], 'px_in': px,
                    'px_out': t['first_px'], 'ret': (t['proc'] - inv) / inv, 'krw': inv, 'why': why,
                    'stop_kind': '손절' in why, 'stop0': None, 'cap': 0.035 if t['open'] >= CUT else 0.045})
    return out, cur


def paper_trades(folder, name):
    f = folder / 'trades.jsonl'
    if not f.exists(): return []
    buys, out = {}, []
    for line in open(f, encoding='utf-8'):
        try: r = json.loads(line)
        except ValueError: continue
        if r.get('side') == '매수':
            buys[(r['market'], r['time'])] = r
        elif r.get('side') == '매도':
            b = buys.get((r['market'], r.get('entry_time')))
            if not b: continue
            out.append({'src': name, 'm': r['market'], 't_in': dt.datetime.fromisoformat(b['time']),
                        't_out': dt.datetime.fromisoformat(r['time']), 'px_in': b['price'], 'px_out': r['price'],
                        'ret': r['pnl'] / b['amount'] if b.get('amount') else r['pnl_pct'] / 100, 'krw': b.get('amount') or 0,
                        'why': r.get('reason', ''), 'stop_kind': r.get('reason') == '손절', 'stop0': b.get('stop'), 'cap': None})
    return out


def virtual_trades():
    try: d = json.loads((DATA / 'buy_pause.json').read_text(encoding='utf-8'))
    except (OSError, ValueError): return []
    out = []
    for v in d.get('virtual', []):
        if 'out_t' not in v: continue
        out.append({'src': 'virtual', 'm': v['m'], 't_in': dt.datetime.fromtimestamp(v['t'], KST),
                    't_out': dt.datetime.fromtimestamp(v['out_t'], KST), 'px_in': v['px'], 'px_out': v['out_px'],
                    'ret': v['ret'], 'krw': v['krw'], 'why': v.get('why', ''), 'stop_kind': v.get('why') == '손절',
                    'stop0': None, 'cap': 0.036})
    return out


# ---------- 1분봉 ----------
class Candles:
    def __init__(self, cache):
        self.cache = Path(cache); self.cache.mkdir(parents=True, exist_ok=True)
        self.calls = 0

    def _get(self, market, to):
        q = urllib.parse.urlencode({'market': market, 'to': to.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'count': 200})
        for i in range(6):
            try:
                time.sleep(0.15); self.calls += 1
                with urllib.request.urlopen(urllib.request.Request('https://api.upbit.com/v1/candles/minutes/1?' + q,
                                                                   headers={'Accept': 'application/json'}), timeout=15) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code == 429: time.sleep(1 + i); continue
                raise
            except urllib.error.URLError:
                time.sleep(2 + i)
        raise RuntimeError(f'{market} 1분봉 조회 실패')

    def load(self, market, intervals):
        """intervals: [(start, end)] KST → {분 시작(epoch): (저가, 종가)}"""
        f = self.cache / f'{market}.json'
        have = json.loads(f.read_text()) if f.exists() else {}
        have = {int(k): tuple(v) for k, v in have.items()}
        now = time.time()
        for a, b in intervals:
            lo, hi = int(a.timestamp()) // 60 * 60, min(int(b.timestamp()), int(now) - 60) // 60 * 60
            if hi < lo: continue
            # 캐시에 빈칸이 많으면(거래 없는 분은 봉이 없을 수 있음) 끝에서부터 받는다
            need = [m for m in range(lo, hi + 60, 60) if m not in have]
            if len(need) <= max(3, (hi - lo) // 60 // 50): continue
            to = dt.datetime.fromtimestamp(hi + 60, KST)
            while True:
                rows = self._get(market, to)
                if not rows: break
                for r in rows:
                    k = int(dt.datetime.fromisoformat(r['candle_date_time_kst']).replace(tzinfo=KST).timestamp())
                    have[k] = (r['low_price'], r['trade_price'])
                first = min(int(dt.datetime.fromisoformat(r['candle_date_time_kst']).replace(tzinfo=KST).timestamp()) for r in rows)
                if first <= lo or len(rows) < 200: break
                to = dt.datetime.fromtimestamp(first, KST)
        f.write_text(json.dumps(have))
        keys = sorted(have)
        return keys, have


def merge(iv):
    iv = sorted(iv)
    out = []
    for a, b in iv:
        if out and a <= out[-1][1] + dt.timedelta(minutes=30): out[-1][1] = max(out[-1][1], b)
        else: out.append([a, b])
    return out


# ---------- 가정 계산 ----------
def touch(tr, keys, bars, line):
    """매수 다음 분 ~ 첫 매도 분에서 저가가 line 이하가 된 첫 분(epoch) 또는 None."""
    s = int(tr['t_in'].timestamp()) // 60 * 60 + 60
    e = int(tr['t_out'].timestamp()) // 60 * 60
    i = bisect.bisect_left(keys, s)
    while i < len(keys) and keys[i] <= e:
        k = keys[i]
        if bars[k][0] <= line:
            if k < e or tr['px_out'] <= line: return k
        i += 1
    return None


def whatif(tr, keys, bars, cap, slip):
    # 손절가 = max(매수 때 ATR 손절, 상한선). 두 상한 모두 같은 규칙·같은 미끄러짐으로 계산해 장부 자체 체결 가정 차이를 없앤다.
    line = max(tr['px_in'] * (1 - cap), tr['stop0'] or 0)
    k = touch(tr, keys, bars, line)
    if k is None: return tr['ret'], False, None
    px = line * (1 - slip)
    return px * (1 - FEE) / (tr['px_in'] * (1 + FEE)) - 1, True, k


def p10_stop(p10_buys, tr):
    best = None
    for b in p10_buys.get(tr['m'], []):
        d = abs((b[0] - tr['t_in']).total_seconds())
        if d <= 3600 and (best is None or d < best[0]): best = (d, b[1] / b[2])
    return None if best is None else tr['px_in'] * best[1]   # P10 손절 비율을 실거래 진입가에 적용


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', default=str(Path(__file__).resolve().parent / '_candles'))
    a = ap.parse_args()
    live, open_live = live_trips()
    p10_buys = {}
    for line in open(DATA / 'P10' / 'trades.jsonl', encoding='utf-8'):
        r = json.loads(line)
        if r.get('side') == '매수' and r.get('stop'):
            p10_buys.setdefault(r['market'], []).append((dt.datetime.fromisoformat(r['time']), r['stop'], r['price']))
    for t in live: t['stop0'] = p10_stop(p10_buys, t)

    # 실측 미끄러짐: 적용 후 실거래 손절(첫 매도가 vs 진입가×0.965)
    sl = [max(0.0, 1 - t['px_out'] / (t['px_in'] * (1 - 0.035))) for t in live if t['t_in'] >= CUT and t['why'].startswith('손절')]
    slip = S.mean(sl) if sl else 0.003      # 평균(갭 하락 포함, 중앙값보다 보수적)

    groups = {'실거래(적용 전 4.5%)': [t for t in live if t['t_in'] < CUT], '실거래(적용 후 3.5%)': [t for t in live if t['t_in'] >= CUT],
              '매수 쉬기 가상 매수': virtual_trades()}
    papers = {'P10(실제 규모 모의)': paper_trades(DATA / 'P10', 'P10')}
    for d in sorted((DATA / 'shadow').iterdir()):
        if d.is_dir(): papers[d.name] = paper_trades(d, d.name)

    allt = [t for g in list(groups.values()) + list(papers.values()) for t in g]
    per = {}
    for t in allt: per.setdefault(t['m'], []).append((t['t_in'], t['t_out']))
    cd = Candles(a.cache)
    bars = {}
    for i, (m, iv) in enumerate(sorted(per.items())):
        bars[m] = cd.load(m, merge(iv))
        if i % 20 == 0: print(f'1분봉 {i + 1}/{len(per)} 종목 · 호출 {cd.calls}', flush=True)

    def run(ts):
        rows = []
        for t in ts:
            keys, b = bars[t['m']]
            r35, h35, _ = whatif(t, keys, b, 0.035, slip)
            r30, h30, k30 = whatif(t, keys, b, 0.030, slip)
            rows.append({**t, 'r35': r35, 'r30': r30, 'h35': h35, 'h30': h30, 'k30': k30})
        return rows

    def agg(rows):
        n = len(rows)
        if not n: return None
        d = [r['r30'] - r['r35'] for r in rows]
        krw = [r['krw'] * (r['r30'] - r['r35']) for r in rows]
        saved = [x for x, r in zip(d, rows) if x > 1e-9]
        lost = [x for x, r in zip(d, rows) if x < -1e-9]
        # 3.5에서는 살아남았는데 3.0에서 손절된 거래(=휩쏘 희생)의 3.5 결과
        victims = [r for r in rows if r['h30'] and not r['h35']]
        return {'n': n, 'avg35': S.mean(r['r35'] for r in rows), 'avg30': S.mean(r['r30'] for r in rows),
                'stop35': sum(r['h35'] for r in rows), 'stop30': sum(r['h30'] for r in rows),
                'win35': sum(r['r35'] > 0 for r in rows) / n, 'win30': sum(r['r30'] > 0 for r in rows) / n,
                'saved': len(saved), 'saved_avg': S.mean(saved) if saved else 0, 'lost': len(lost), 'lost_avg': S.mean(lost) if lost else 0,
                'krw': sum(krw), 'victims': victims, 'victim_win': sum(v['r35'] > 0 for v in victims),
                'victim_avg35': S.mean(v['r35'] for v in victims) if victims else 0}

    res = {k: run(v) for k, v in {**groups, **papers}.items()}
    now = dt.datetime.now(KST)
    L = [f'# 손절 상한 3.0% 가정 분석 ({now:%Y-%m-%d %H:%M} KST, 단독 작성)', '',
         f'- 실측 손절 미끄러짐(11-11 이후 실거래 손절 {len(sl)}건, 손절선 대비 매도가) 중앙값 {S.median(sl) if sl else 0:.2%}, 평균 {slip:.2%} → 가정 매도가 = 선 × (1−{slip:.2%})',
         f'- 1분봉 {sum(len(v[0]) for v in bars.values()):,}개 ({len(bars)}종목, API {cd.calls}회)',
         '- 같은 1분봉 규칙으로 3.5%와 3.0%를 각각 계산해 비교(실거래 3.5% 기간은 계산값이 실제와 거의 같아야 함 → 아래 검산)', '',
         '| 묶음 | 거래 | 평균 수익률 3.5% → 3.0% | 손절 건수 3.5 → 3.0 | 승률 3.5 → 3.0 | 손실 줄어든 거래(평균) | 손해 본 거래(평균) | 손익 차이(원) | 3.0에서만 손절된 거래 중 3.5였으면 이익 |',
         '|---|---|---|---|---|---|---|---|---|']
    summ = {}
    for k, rows in res.items():
        g = agg(rows)
        if not g: continue
        summ[k] = g
        L.append(f"| {k} | {g['n']} | {g['avg35']:+.2%} → {g['avg30']:+.2%} | {g['stop35']} → {g['stop30']} | {g['win35']:.0%} → {g['win30']:.0%} | "
                 f"{g['saved']}건 ({g['saved_avg']:+.2%}) | {g['lost']}건 ({g['lost_avg']:+.2%}) | {g['krw']:+,.0f} | "
                 f"{g['victim_win']}/{len(g['victims'])} (3.5 결과 평균 {g['victim_avg35']:+.2%}) |")
    # 모의 15개 합계
    pk = [k for k in summ if k not in groups]
    if pk:
        tot = sum(summ[k]['krw'] for k in pk); n = sum(summ[k]['n'] for k in pk)
        better = sum(summ[k]['avg30'] > summ[k]['avg35'] for k in pk)
        L += ['', f'- 모의 장부 {len(pk)}개 합계: 거래 {n}건, 손익 차이 {tot:+,.0f}원 · 3.0%가 나은 장부 {better}/{len(pk)}개']

    # 검산
    live35 = res['실거래(적용 후 3.5%)']
    if live35:
        L += ['', '## 검산 (실거래 3.5% 기간: 계산 3.5% vs 실제)', '',
              f"- 실제 평균 {S.mean(r['ret'] for r in live35):+.2%} · 계산 3.5% 평균 {S.mean(r['r35'] for r in live35):+.2%} · 실제 손절 {sum(r['stop_kind'] for r in live35)}건 / 계산 {sum(r['h35'] for r in live35)}건 (계산은 실제보다 일찍 건드린 경우만 바꿈)"]

    # 실거래 상세
    for k in ('실거래(적용 후 3.5%)', '실거래(적용 전 4.5%)', '매수 쉬기 가상 매수'):
        rows = [r for r in res[k] if abs(r['r30'] - r['r35']) > 1e-9]
        L += ['', f'## {k}: 3.0%였다면 결과가 달라진 거래 {len(rows)}건', '',
              '| 매수 | 종목 | 실제 | 3.5% 계산 | 3.0% 계산 | 차이(원) | 실제 매도 사유 | 3.0% 선 닿은 시각 |', '|---|---|---|---|---|---|---|---|']
        for r in sorted(rows, key=lambda r: r['t_in']):
            kt = dt.datetime.fromtimestamp(r['k30'], KST).strftime('%m-%d %H:%M') if r['k30'] else ''
            L.append(f"| {r['t_in'].astimezone(KST):%m-%d %H:%M} | {r['m'][4:]} | {r['ret']:+.2%} | {r['r35']:+.2%} | {r['r30']:+.2%} | "
                     f"{r['krw'] * (r['r30'] - r['r35']):+,.0f} | {r['why'][:24]} | {kt} |")

    # 현재 보유(실거래 미청산)
    L += ['', '## 지금 실거래 보유분(체결 기록 기준)', '']
    if open_live:
        for m, t in open_live.items():
            px = t['bf'] / t['bq']
            keys, b = cd.load(m, [(t['open'], now)])
            lows = [b[k][0] for k in keys if k > t['open'].timestamp()]
            last = b[keys[-1]][1] if keys else None
            if lows:
                L.append(f"- {m[4:]}: 매수 {t['open'].astimezone(KST):%m-%d %H:%M} · 진입 {px:,.6g} · 최저 {min(lows) / px - 1:+.2%} · 지금 {last / px - 1:+.2%}"
                         f" → 3.0%였다면 {'이미 손절' if min(lows) <= px * 0.97 else '유지'}")
    else:
        L.append('- 없음')
    L += ['', '## 한계', '',
          '- 손절 뒤 빈 자리에 다른 종목을 사는 효과, 재매수 금지, 하루 손실 중단선은 넣지 않았다.',
          '- 1분봉 저가 기준이라 실제 체결(초 단위)보다 조금 더 자주 닿은 것으로 잡힐 수 있다(3.0% 쪽에 불리).',
          '- 기간이 16일, 장세 하나에 가깝다. 입증이 아니라 방향 확인용이다.']
    OUT.mkdir(exist_ok=True)
    f = OUT / f'stop030_whatif_{now:%Y%m%d_%H%M}.md'
    f.write_text('\n'.join(L) + '\n', encoding='utf-8')
    print(f)
    print('\n'.join(L))


if __name__ == '__main__':
    main()
