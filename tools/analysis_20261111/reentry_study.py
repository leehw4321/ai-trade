"""손실 청산 뒤 재매수 금지 시간 스터디 (읽기 전용).
자료: 거래소 체결(exchange_orders.jsonl, v5- 봇 주문 왕복), 봇이 '재진입 대기'만으로 막은 후보(missed_ledger.jsonl: 시각·순위·종가), 업비트 1분봉.
가정: 막힌 후보를 그 판단 시각 종가로 샀다고 본다. 매도는 단순화: 손절 −3.5% / 이익보호(+3% 도달 뒤 이익 30% 반납) / H시간 뒤 종가.
보정: 같은 단순 매도 규칙을 실제 진입에 적용해 실제 손익과 비교한다. 수수료 편도 0.05%, 금액은 1건 7만 원 기준."""
import json, sys, time, datetime as dt, urllib.request, statistics as stt
from collections import defaultdict

DATA = r'C:\Users\User\Desktop\코인 트레이딩\v5-app\v5\data'
OUT = r'C:\Users\User\AppData\Local\Temp\claude\d18e1f21b93d24cd\fb13a6af-beec-45f2-a04a-2c89cd4d7e8e\scratchpad'
KST = dt.timezone(dt.timedelta(hours=9))
FEE, STOP, ARM, GIVE, SIZE = 0.0005, 0.035, 0.03, 0.3, 70000
WAITS = (0, 1, 2, 4, 6, 8, 12, 24)
HOLD = (6, 12)
NOW = time.time()

fills = []
for line in open(DATA + r'\exchange_orders.jsonl', encoding='utf-8'):
    r = json.loads(line)
    if not str(r.get('identifier') or '').startswith('v5-') or float(r.get('executed_volume') or 0) <= 0: continue
    fills.append({'m': r['market'], 'side': r['side'], 't': dt.datetime.fromisoformat(r['created_at']).timestamp(),
                  'v': float(r['executed_volume']), 'f': float(r['executed_funds']), 'fee': float(r['paid_fee'])})
fills.sort(key=lambda x: x['t'])
trips, cur = [], {}
for e in fills:
    t = cur.get(e['m'])
    if e['side'] == 'bid':
        if t is None: t = cur[e['m']] = {'m': e['m'], 'open': e['t'], 'qty': 0., 'bq': 0., 'bf': 0., 'cost': 0., 'sq': 0., 'sf': 0.}
        t['qty'] += e['v']; t['bq'] += e['v']; t['bf'] += e['f']; t['cost'] += e['f'] + e['fee']
    else:
        if t is None: continue
        t['qty'] -= e['v']; t['sq'] += e['v']; t['sf'] += e['f'] - e['fee']; t['close'] = e['t']
        if t['qty'] <= 1e-8 or t['qty'] * (t['bf'] / t['bq']) < 5000:
            t['pnl'] = t['sf'] - t['cost'] * (t['sq'] / t['bq']); t['pct'] = t['pnl'] / (t['cost'] * (t['sq'] / t['bq']))
            t['entry'] = t['bf'] / t['bq']
            trips.append(t); cur.pop(e['m'])

missed = defaultdict(list)
slots_seen = set()
for line in open(DATA + r'\missed_ledger.jsonl', encoding='utf-8'):
    r = json.loads(line)
    slots_seen.add(r['slot'])
    if r.get('signal') and r.get('blocks') == ['재진입 대기']:
        missed[r['market']].append(r)

cache = {}


def candles(m, start, end):
    """[(epoch, o, h, l, c)] 1분봉, start~end(epoch)."""
    key = (m, int(start // 3600), int(end // 3600))
    if key in cache: return cache[key]
    out, to = [], min(end, NOW)
    while True:
        url = f'https://api.upbit.com/v1/candles/minutes/1?market={m}&count=200&to={dt.datetime.fromtimestamp(to, dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}'
        for k in range(6):
            try: r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'research-client'}), timeout=10)); break
            except Exception: time.sleep(1 + k)
        else: raise RuntimeError('fetch')
        time.sleep(0.12)
        if not r: break
        for c in r:
            ts = dt.datetime.fromisoformat(c['candle_date_time_utc']).replace(tzinfo=dt.timezone.utc).timestamp()
            if ts >= start: out.append((ts, c['opening_price'], c['high_price'], c['low_price'], c['trade_price']))
        last = dt.datetime.fromisoformat(r[-1]['candle_date_time_utc']).replace(tzinfo=dt.timezone.utc).timestamp()
        if last <= start or len(r) < 200: break
        to = last
    out = sorted(set(out)); cache[key] = out
    return out


def sim(m, t0, px, hours):
    """단순 매도 규칙. 반환 (수익률, 사유, 보유시간h) 또는 None(자료 부족)."""
    end = t0 + hours * 3600
    if end > NOW - 120: return None
    cs = [c for c in candles(m, t0 - 60, end + 60) if c[0] > t0 - 60 and c[0] < end]
    if len(cs) < hours * 30: return None
    stop, peak = px * (1 - STOP), px
    for ts, o, h, l, c in cs:
        if l <= stop:                       # 같은 봉에서 고가·저가 순서는 모름 → 손절 먼저(보수)
            return min(stop, o) / px - 1 - 2 * FEE, '손절', (ts - t0) / 3600
        peak = max(peak, h)
        if peak >= px * (1 + ARM):
            g = px + (peak - px) * (1 - GIVE)
            if c <= g:
                return c / px - 1 - 2 * FEE, '이익보호', (ts - t0) / 3600
    return cs[-1][4] / px - 1 - 2 * FEE, '시간', hours


# ---- 1) 보정: 실제 진입에 같은 단순 규칙 ----
cal = []
for t in trips:
    for H in HOLD:
        r = sim(t['m'], t['open'], t['entry'], H)
        if r: cal.append((H, t['pct'], r[0]))
print('== 보정: 실제 진입에 단순 매도 규칙 ==')
for H in HOLD:
    xs = [(a, b) for h, a, b in cal if h == H]
    if xs:
        print(f'{H}h 상한: {len(xs)}건 · 실제 평균 {stt.mean(a for a,_ in xs):+.2%} / 단순 규칙 평균 {stt.mean(b for _,b in xs):+.2%}'
              f' · 7만원 기준 실제 {sum(a for a,_ in xs)*SIZE:+,.0f} / 규칙 {sum(b for _,b in xs)*SIZE:+,.0f}')

# ---- 2) 손실 청산 뒤 재진입 대기별 ----
first_slot, last_slot = min(slots_seen), max(slots_seen)
loss = [t for t in trips if t['pnl'] < 0 and first_slot <= t['close'] and t['close'] + 24 * 3600 <= last_slot + 3600]
cover = []
for t in loss:
    n = sum(1 for s in slots_seen if t['close'] <= s <= t['close'] + 24 * 3600)
    if n >= 18: cover.append(t)
print(f'\n손실 청산 {sum(1 for t in trips if t["pnl"]<0)}건 중 이후 24시간 판단 기록이 충분한 것 {len(cover)}건')

rows = []
for t in cover:
    cands = sorted((r for r in missed[t['m']] if t['close'] < r['time'] <= t['close'] + 24 * 3600), key=lambda r: r['time'])
    row = {'m': t['m'][4:], 'exit': dt.datetime.fromtimestamp(t['close'], KST).strftime('%m-%d %H:%M'), 'loss_pct': t['pct'],
           'blocked_slots': len(cands), 'first_h': (cands[0]['time'] - t['close']) / 3600 if cands else None}
    for W in WAITS:
        c = next((r for r in cands if r['time'] >= t['close'] + W * 3600), None)
        for H in HOLD:
            row[(W, H)] = None if c is None or W >= 24 else (sim(t['m'], c['time'], c['close'], H), (c['time'] - t['close']) / 3600, c['rank'])
    rows.append(row)

json.dump([{k if isinstance(k, str) else f'{k[0]}_{k[1]}': v for k, v in r.items()} for r in rows],
          open(OUT + r'\reentry_rows.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=0, default=str)
print(f'그중 24시간 안 "재진입 대기"로만 막힌 적 있는 것 {sum(1 for r in rows if r["blocked_slots"])}건 (막힌 판단 합계 {sum(r["blocked_slots"] for r in rows)}회)')
for r in rows:
    if r['blocked_slots']:
        print(f'  {r["m"]:9} {r["exit"]} 손실 {r["loss_pct"]:+.1%} · 막힘 {r["blocked_slots"]}회 · 첫 막힘 {r["first_h"]:.1f}h 뒤')

print('\n== 대기 시간별: 막힌 후보를 대기 끝난 뒤 첫 판단에 샀다면 ==')
for H in HOLD:
    print(f'-- 보유 상한 {H}h --')
    for W in WAITS[:-1]:
        res = [r[(W, H)] for r in rows if r[(W, H)] and r[(W, H)][0]]
        if not res: print(f'  대기 {W:>2}h: 0건'); continue
        p = [x[0][0] for x in res]
        why = defaultdict(int)
        for x in res: why[x[0][1]] += 1
        print(f'  대기 {W:>2}h: {len(res)}건 · 합계 {sum(p)*SIZE:+,.0f}원 · 평균 {stt.mean(p):+.2%} · 이익 {sum(1 for v in p if v>0)}/{len(p)} · '
              f'최악 {min(p):+.1%} · 사유 {dict(why)}')
