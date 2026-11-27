"""11-27 집 PC 이익보호 재상승 시뮬레이션의 '제안 3가지'를 같은 자료로 비교 (회사 PC, 분석 전용).
① 현행 유지(재매수: 매도 4h 뒤 0.5ATR 눌림)  ② 눌림 강화 1ATR(+1.5/2/3 민감도)  ③ A_W(재돌파 + 넓은 추적)
참고: 재매수 아예 안 함(0원), A·B·C 원안 재계산.
사유: 실행본 장부 + RELAY_ORDERS(장부 전달로 받아 임시 폴더에 푼 상대 PC orders.jsonl, 저장소 밖). 결과는 SIM_OUT(실행본 data, 공개 저장소 밖).
"""
import sys, os, json, glob, statistics as stx, datetime as dt
sys.path.insert(0, r'C:\Users\User\Desktop\ai-trade\tools\analysis_20261127')
SP = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault('SIM_OUT', r'C:\Users\User\Desktop\코인 트레이딩\v5-app\v5\data\analysis_20261127')
import profit_rebuy_sim as S
from pathlib import Path
S.OUT = Path(os.environ['SIM_OUT']); S.CACHE = S.OUT / 'candles'

def reasons():
    out = {}
    files = [os.environ.get('RELAY_ORDERS', ''), str(S.APP / 'live' / 'orders.jsonl'), *glob.glob(str(S.APP / 'live_이전장부_*' / 'orders.jsonl'))]
    for f in files:
        if not os.path.exists(f): continue
        for line in open(f, encoding='utf-8'):
            try: r = json.loads(line)
            except ValueError: continue
            if r.get('identifier') and r.get('side') == '매도':
                out[r['identifier']] = r.get('reason', '')
    return out

def dip_trigger(path, sp, a, k, wait_bars=16):
    for i in range(min(len(path) - 1, S.WIN_H * 4)):
        if i >= wait_bars and path[i][4] <= sp - k * a:
            return i + 1
    return None

def wide(path, i, n, a60):
    e = path[i][1] * (1 + S.COST); stop = e * (1 - S.STOP); peak = e; armed = False
    end = min(len(path), i + 288)
    for j in range(i, end):
        t, o, h, l, c = path[j]
        line = max(stop, (max(e, peak - 3 * a60) if armed else stop))
        if l <= line: px = min(line, o); break
        peak = max(peak, h); armed = armed or peak >= e * 1.03
    else: px = path[end - 1][4]
    r = px * (1 - S.COST) / e - 1; return n * r, r

rs = reasons()
ev = [s for s in S.sells() if rs.get(s['id'], '').startswith(('이익보호', '계단 보호')) and s['price'] > s['avg']]
res = {}; big = []; n_ev = 0; first = last = None
def add(k, v): res.setdefault(k, []).append(v)
for s in ev:
    t0 = s['time']
    bars = S.fetch(s['market'], t0 - dt.timedelta(hours=30), t0 + dt.timedelta(hours=97))
    cut = int(t0.timestamp()) // 900 * 900
    prior = [b for b in bars if b[0] < cut]; path = [b for b in bars if b[0] > cut]
    if len(prior) < 60 or len(path) < 8: continue
    n_ev += 1; ts = t0.astimezone(S.KST).strftime('%m-%d %H:%M'); first = first or ts; last = ts
    a15 = S.atr(prior); a60 = S.atr(S.hourly(prior[-(len(prior) // 4) * 4:])); sp = s['price']; n = sp * s['qty']
    mx72 = max(b[2] for b in path[:288]) / sp - 1
    row = {'t': ts, 'm': s['market'][4:], 'max72': mx72}
    for k in (0.5, 1.0, 1.5, 2.0, 3.0):
        i = dip_trigger(path, sp, a15, k)
        if i is not None and i < len(path):
            add(f'dip{k:g}', S.manage(path, i, n)[:2]); add(f'dip{k:g}_W', wide(path, i, n, a60))
            row[f'dip{k:g}'] = S.manage(path, i, n)[1]
    for m in ('A', 'B'):
        i = S.rebuy_trigger(m, path, sp, a15)
        if i is not None and i < len(path):
            add(m, S.manage(path, i, n)[:2]); add(m + '_W', wide(path, i, n, a60)); row[m + '_W'] = wide(path, i, n, a60)[1]
    c2 = S.half_hold(path, sp, s['avg'], a60, s['qty']); add('C2', (c2[0], c2[1] / sp - 1))
    if mx72 >= 0.10: big.append(row)

print(f'대상 이익보호 매도 {n_ev}건 ({first} ~ {last})')
print(f"{'방법':10}{'발동':>5}{'이익':>5}{'합계(원)':>11}{'평균':>8}{'중앙':>8}{'최고':>8}")
for k in ['dip0.5', 'dip1', 'dip1.5', 'dip2', 'dip3', 'dip0.5_W', 'dip1_W', 'dip2_W', 'A', 'A_W', 'B', 'B_W', 'C2']:
    v = res.get(k, [])
    if not v: print(f'{k:10}    0'); continue
    r = [x[1] for x in v]
    print(f"{k:10}{len(v):5}{sum(x > 0 for x in r):5}{sum(x[0] for x in v):11,.0f}{stx.mean(r):+8.2%}{stx.median(r):+8.2%}{max(r):+8.1%}")
print(f'\n매도 뒤 72시간 +10% 이상 오른 {len(big)}건 포착(재매수 수익률)')
for b in big:
    print(f"  {b['t']} {b['m']:9} 최고 {b['max72']:+.0%} | " + ' · '.join(f"{k} {b[k]:+.1%}" if k in b else f'{k} -' for k in ('dip0.5', 'dip1', 'A_W')))
json.dump({k: v for k, v in res.items()}, open(os.path.join(os.environ['SIM_OUT'], 'rebuy_proposals_result.json'), 'w'), ensure_ascii=False)
