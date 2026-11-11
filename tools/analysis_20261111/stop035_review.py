"""손절 3.5% 적용(2026-11-11 09:45 KST) 전후 비교 리포트. 사용자 요청: 1~2주 뒤 리포트.

  python tools/analysis_20261111/stop035_review.py   → 코인 트레이딩/reports/stop035_review_YYYYMMDD_HHMM.md

비교: 적용 전(11-02 ~ 11-11 09:45, 손절 상한 4.5%) vs 적용 후(같은 시각 이후 매수한 거래, 3.5%).
거래소 체결(exchange_orders.jsonl)의 봇 주문(v5-)만 매수→전량 매도 왕복으로 묶는다. 매도 사유는 실거래 장부 orders.jsonl.
함께 보는 것: 바닥가 손절·위험 청산 소요(11-11 08:12 배포 기능). 읽기 전용, 주문 없음.
"""
import datetime as dt
import glob
import json
import statistics as S
from pathlib import Path

APP = Path(r'C:\Users\User\Desktop\코인 트레이딩\v5-app\v5')
DATA = APP / 'data'
OUT = Path(r'C:\Users\User\Desktop\코인 트레이딩\reports')
KST = dt.timezone(dt.timedelta(hours=9))
CUT = dt.datetime(2026, 11, 11, 9, 45, tzinfo=KST)        # 3.5% 배포
FLOOR_ON = dt.datetime(2026, 11, 11, 8, 12, tzinfo=KST)   # 바닥가 손절 등 배포


def load():
    reason, events = {}, []
    for f in glob.glob(str(DATA / 'live*' / 'orders.jsonl')):
        for line in open(f, encoding='utf-8'):
            try: r = json.loads(line)
            except ValueError: continue
            if r.get('identifier') and r.get('reason') and r.get('side') in ('매수', '매도'):
                reason[r['identifier']] = r['reason']
            if r.get('event'): events.append(r)
    fills = []
    for line in open(DATA / 'exchange_orders.jsonl', encoding='utf-8'):
        r = json.loads(line)
        if not str(r.get('identifier') or '').startswith('v5-') or float(r.get('executed_volume') or 0) <= 0: continue
        fills.append({'m': r['market'], 'side': r['side'], 't': dt.datetime.fromisoformat(r['created_at']), 'v': float(r['executed_volume']),
                      'f': float(r['executed_funds']), 'fee': float(r['paid_fee']), 'id': r['identifier']})
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
            t['qty'] -= e['v']; t['sq'] += e['v']; t['proc'] += e['f'] - e['fee']; t['close'] = e['t']
            t['why'].append(reason.get(e['id'], '?'))
            if t['qty'] <= 1e-8 or t['qty'] * (t['bf'] / t['bq']) < 5000:
                trips.append(t); cur.pop(e['m'])
    for t in trips:
        inv = t['cost'] * t['sq'] / t['bq']
        t['pnl'] = t['proc'] - inv; t['ret'] = t['pnl'] / inv
        t['days'] = (t['close'] - t['open']).total_seconds() / 86400
        t['stop'] = any('손절' in w for w in t['why'])
    return trips, cur, events


def summary(ts, days):
    n = len(ts)
    if not n: return None
    st = [t for t in ts if t['stop']]
    win = [t for t in ts if t['pnl'] > 0]
    return {'n': n, 'days': days, 'per_day': n / days if days else 0, 'pnl': sum(t['pnl'] for t in ts),
            'pnl_day': sum(t['pnl'] for t in ts) / days if days else 0, 'win': len(win) / n,
            'stops': len(st), 'stop_rate': len(st) / n, 'stop_avg': S.mean(t['pnl'] for t in st) if st else 0,
            'stop_ret': S.mean(t['ret'] for t in st) if st else 0, 'win_avg': S.mean(t['pnl'] for t in win) if win else 0,
            'avg': sum(t['pnl'] for t in ts) / n}


def main():
    trips, open_, events = load()
    now = dt.datetime.now(KST)
    first = min(t['open'] for t in trips)
    before = [t for t in trips if t['open'] < CUT]
    after = [t for t in trips if t['open'] >= CUT]
    b = summary(before, (CUT - first).total_seconds() / 86400)
    a = summary(after, (now - CUT).total_seconds() / 86400)
    floor = [e for e in events if e.get('event') == '바닥가 손절 주문']
    floor_end = [e for e in events if e.get('event') == '지정가 매도 종료' and '바닥가' in str(e.get('note', ''))]
    timing = [e for e in events if e.get('event') == '위험 청산 소요']
    thin = sum(1 for line in open(DATA / 'bot.stdout.log', encoding='utf-8', errors='replace') if '매수 호가 얇음' in line)

    L = [f'# 손절 3.5% 적용 전후 리포트 ({now:%Y-%m-%d %H:%M} KST)', '',
         f'- 적용: 2026-11-11 09:45 (`stop_max_pct` 0.045→0.035, `entry_risk` 0.00184→0.00143, 기존 CAP·CYBER·DOS 손절가도 −3.5%로)',
         f'- 적용 후 경과 {a["days"] if a else (now - CUT).total_seconds() / 86400:.1f}일 · 아직 보유 중: {", ".join(m[4:] for m in open_) or "없음"}', '',
         '| | 적용 전 (4.5%) | 적용 후 (3.5%) |', '|---|---|---|']
    rows = [('기간(일)', 'days', '{:.1f}'), ('끝난 거래', 'n', '{}'), ('하루 거래 수', 'per_day', '{:.1f}'),
            ('손익 합계(원)', 'pnl', '{:+,.0f}'), ('하루 평균 손익(원)', 'pnl_day', '{:+,.0f}'), ('거래당 평균(원)', 'avg', '{:+,.0f}'),
            ('승률', 'win', '{:.0%}'), ('손절 건수', 'stops', '{}'), ('손절 비율', 'stop_rate', '{:.0%}'),
            ('손절 1건 평균(원)', 'stop_avg', '{:+,.0f}'), ('손절 1건 평균 수익률', 'stop_ret', '{:+.2%}'), ('이긴 거래 평균(원)', 'win_avg', '{:+,.0f}')]
    for name, k, fmt in rows:
        L.append(f'| {name} | {fmt.format(b[k]) if b else "-"} | {fmt.format(a[k]) if a else "-"} |')
    L += ['', '## 적용 후 거래', '', '| 매수 | 종목 | 손익(원) | 수익률 | 매도 사유 |', '|---|---|---|---|---|']
    for t in after:
        L.append(f"| {t['open'].astimezone(KST):%m-%d %H:%M} | {t['m'][4:]} | {t['pnl']:+,.0f} | {t['ret']:+.2%} | {'/'.join(dict.fromkeys(t['why']))[:40]} |")
    L += ['', '## 함께 배포한 기능(11-11 08:12~)', '',
          f'- 바닥가 손절 주문 {len(floor)}건 · 종료 기록 {len(floor_end)}건 · 매수 호가 얇음 보류 로그 {thin}줄']
    for e in floor_end: L.append(f"  - {e.get('time', '')[5:16]} {e.get('note', '')}")
    if timing:
        tot = [e['ms'].get('합계') for e in timing if isinstance(e.get('ms'), dict) and e['ms'].get('합계') is not None]
        chk = [e['ms'].get('직접주문확인') for e in timing if isinstance(e.get('ms'), dict) and e['ms'].get('직접주문확인') is not None]
        L.append(f'- 위험 청산 소요 {len(timing)}건 · 판정→접수 합계 중앙값 {S.median(tot):,.0f}ms(최대 {max(tot):,.0f}) · 직접 주문 확인 중앙값 {S.median(chk):,.0f}ms'
                 if tot and chk else f'- 위험 청산 소요 {len(timing)}건')
    else:
        L.append('- 위험 청산 소요 기록 없음(손절이 없었음)')
    L += ['', '주의: 적용 후 표본이 작으면 장세 차이가 결과를 좌우한다. 적용 전 기간도 9일뿐이다. 개선 입증으로 쓰지 않는다.']
    OUT.mkdir(exist_ok=True)
    f = OUT / f'stop035_review_{now:%Y%m%d_%H%M}.md'
    f.write_text('\n'.join(L) + '\n', encoding='utf-8')
    print(f)
    print('\n'.join(L))


if __name__ == '__main__':
    main()
