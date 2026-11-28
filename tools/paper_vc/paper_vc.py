"""두 매수 방식 2주 모의 운용 (2026-11-28 사용자 지시: "2번을 2주간 모의운용하고 매일 보고해줘"). 조회 전용, 주문 없음.

  A 지금 방식    : 거래대금 상위 30 중 18시간 상승률 순위(0~20%, 4시간 −10% 보류), 손절 3.5%·+3% 뒤 30% 반납
  B 변동성 수축  : 같은 범위에서 '최근 4시간 변동폭 ÷ 24시간 변동폭'이 작은 순(급등 뒤 쉬는 종목), 손절 2.5%·+8% 뒤 50% 반납
공통(실거래에 맞춘 시뮬레이션 gap_study S3와 같게): 자리 3개, 보유 종목 재매수 없음, 매시 정각 판단(7위 안에서 매수),
  유지 순위 9위 밖이면 매도(손실이면 4시간 유예), 재매수 대기(이익 청산 4시간·0.5ATR 눌림, 손실 청산 24시간).
  매수 = 판단 봉 종가, 15분봉 저가가 손절가에 닿으면 min(손절가, 시가)에 매도, 이익보호는 15분봉 종가 기준.
  비용 왕복 0.3%(수수료 0.1% + 체결 차이 0.2%). 원화 환산은 1건 10만 원.
판정 기준(미리 고정): 2주·50건 이상에서 비용 뺀 1건 평균 +0.2% 이상이면 소액 실거래 전환을 제안할 수 있다.

  python tools/paper_vc/paper_vc.py             한 번 계산
  python tools/paper_vc/paper_vc.py --loop 60   60초마다 확인(새 15분봉이 마감되면 계산), 매일 09:00 뒤 첫 계산에서 전날 보고서
  python tools/paper_vc/paper_vc.py --report    지금까지 보고서만 다시 쓰기
상태: 코인 트레이딩/v5-app/v5/data/paper_vc/state.json, 보고서: 코인 트레이딩/reports/paper_vc_YYYYMMDD.md
"""
import argparse, datetime as dt, json, os, sys, time, urllib.request
from pathlib import Path
import numpy as np

ROOT = Path(r'C:\Users\User\Desktop\코인 트레이딩')
OUT = ROOT / 'v5-app' / 'v5' / 'data' / 'paper_vc'
REPORTS = ROOT / 'reports'
KST, UTC = dt.timezone(dt.timedelta(hours=9)), dt.timezone.utc
START = dt.datetime(2026, 11, 28, 12, 0, tzinfo=KST)
END = START + dt.timedelta(days=14)
EXCL = {'KRW-USDT', 'KRW-USDC', 'KRW-USDE', 'KRW-USD1', 'KRW-DAI'}   # 봇 제외 목록과 같게
COST, KRW, SLOTS, M, SCAN, NBARS = 0.003, 100000, 3, 9, 7, 120
STRATS = {'A': dict(name='지금 방식(18시간 상승률)', stop=0.035, at=0.03, give=0.30),
          'B': dict(name='변동성 수축', stop=0.025, at=0.08, give=0.50)}


def get(url):
    for k in range(4):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'paper-vc'}), timeout=10))
        except Exception:
            time.sleep(1 + k)
    return None


def universe():
    ms = [x['market'] for x in get('https://api.upbit.com/v1/market/all') or [] if x['market'].startswith('KRW-')]
    tick = []
    for k in range(0, len(ms), 100):
        tick += get('https://api.upbit.com/v1/ticker?markets=' + ','.join(ms[k:k + 100])) or []
        time.sleep(.15)
    pool = sorted((t for t in tick if t['market'] not in EXCL), key=lambda t: -t['acc_trade_price_24h'])
    return [t['market'] for t in pool[:30]], {t['market']: t['trade_price'] for t in tick}


def bars(m, last_start):
    """last_start(UTC, 마감된 마지막 15분봉 시작)까지 NBARS개, 거래 없는 봉은 직전 종가로 채운 배열 (o,h,l,c)."""
    d = get(f'https://api.upbit.com/v1/candles/minutes/15?market={m}&count=200')
    time.sleep(.12)
    if not d: return None
    by = {x['candle_date_time_utc']: x for x in d}
    grid = [last_start - dt.timedelta(minutes=15 * k) for k in range(NBARS - 1, -1, -1)]
    out, prev = [], None
    for g in grid:
        x = by.get(g.strftime('%Y-%m-%dT%H:%M:%S'))
        if x: row = (x['opening_price'], x['high_price'], x['low_price'], x['trade_price']); prev = row[3]
        elif prev is not None: row = (prev, prev, prev, prev)
        else: row = None
        out.append(row)
    first = next((r for r in out if r), None)
    if first is None: return None
    out = [r or (first[0],) * 4 for r in out]
    return np.array(out, float)     # [NBARS, 4]


def score(code, b):
    o, h, l, c = b[:, 0], b[:, 1], b[:, 2], b[:, 3]
    if code == 'A':
        rs, r4 = c[-1] / c[-73] - 1, c[-1] / c[-17] - 1
        return rs, (0 < rs < 0.20 and r4 > -0.10)
    v = -(h[-16:].max() / l[-16:].min()) / (h[-96:].max() / l[-96:].min())
    return v, np.isfinite(v)


def atr_pct(b):
    h, l, c = b[-14:, 1], b[-14:, 2], b[-15:-1, 3]
    return float(np.mean(np.maximum(h - l, np.abs(h - c)))) / b[-1, 3]


def load():
    try: return json.loads((OUT / 'state.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'last_bar': None, 'S': {k: {'held': {}, 'last': {}, 'trades': []} for k in STRATS}}


def save(st):
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / 'state.json.tmp'; tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding='utf-8'); os.replace(tmp, OUT / 'state.json')


def step(st, now=None):
    """새로 마감된 15분봉이 있으면 처리. 반환: 처리한 봉 시작 시각(UTC iso) 또는 None."""
    now = now or dt.datetime.now(UTC)
    last_start = (now - dt.timedelta(minutes=15, seconds=20)).replace(second=0, microsecond=0)
    last_start = last_start.replace(minute=last_start.minute // 15 * 15)
    key = last_start.strftime('%Y-%m-%dT%H:%M:%S')
    if st['last_bar'] == key or now.astimezone(KST) < START: return None
    uni, price = universe()
    if len(uni) < 20: return None
    held_all = {m for s in st['S'].values() for m in s['held']}
    B = {}
    for m in dict.fromkeys(uni + sorted(held_all)):
        b = bars(m, last_start)
        if b is not None: B[m] = b
    hour = last_start.minute == 45                                   # 정시 마감 봉
    for code, cfg in STRATS.items():
        s = st['S'][code]
        # 1) 손절·이익보호(이번 봉)
        for m in list(s['held']):
            h = s['held'][m]
            if m not in B or h['bar'] >= key: continue
            since = max(h['bar'], st['last_bar'] or h['bar'])          # 꺼져 있던 동안의 봉도 차례로 본다
            n_new = int((last_start - dt.datetime.fromisoformat(since).replace(tzinfo=UTC)).total_seconds() // 900)
            for o, hi, lo, c in B[m][-min(max(n_new, 1), NBARS - 1):]:
                e, out, why = h['e'], None, None
                if lo <= e * (1 - cfg['stop']): out, why = min(e * (1 - cfg['stop']), o), '손절'
                else:
                    h['peak'] = float(max(h['peak'], c))
                    if h['peak'] >= e * (1 + cfg['at']) and c <= h['peak'] - cfg['give'] * (h['peak'] - e): out, why = c, '이익보호'
                if out is not None:
                    close(s, m, out, why, key); break
        if not hour: continue
        # 2) 순위 매도
        sc = {m: score(code, b) for m, b in B.items()}
        pool = [m for m in dict.fromkeys(uni + list(s['held'])) if m in sc and np.isfinite(sc[m][0])]
        order = sorted(pool, key=lambda m: -sc[m][0]); rank = {m: k + 1 for k, m in enumerate(order)}
        for m in list(s['held']):
            h = s['held'][m]
            if m not in B: continue
            if rank.get(m, 99) > M:
                h.setdefault('out_since', key)
                n_out = (last_start - dt.datetime.fromisoformat(h['out_since']).replace(tzinfo=UTC)).total_seconds() / 900
                if B[m][-1, 3] >= h['e'] or n_out >= 16: close(s, m, B[m][-1, 3], '순위', key)
            else:
                h.pop('out_since', None)
        # 3) 매수
        free = SLOTS - len(s['held'])
        for m in order[:SCAN]:
            if free <= 0: break
            if m in s['held'] or m not in uni or not sc[m][1]: continue
            c = float(B[m][-1, 3])
            if m in s['last']:
                L = s['last'][m]; hrs = (last_start - dt.datetime.fromisoformat(L['bar']).replace(tzinfo=UTC)).total_seconds() / 3600
                if hrs < 24:
                    if not L['profit']: continue
                    if hrs < 4 or c > L['px'] * (1 - 0.5 * atr_pct(B[m])): continue
            s['held'][m] = {'e': c, 'bar': key, 'peak': c, 'open': now.astimezone(KST).isoformat(timespec='seconds'),
                            'rank': rank[m], 'score': round(float(sc[m][0]), 5), 'ticker': price.get(m)}
            free -= 1
    st['last_bar'] = key
    st['uni'] = uni
    st['marks'] = {m: float(B[m][-1, 3]) for m in held_all | {m for s in st['S'].values() for m in s['held']} if m in B}
    return key


def close(s, m, px, why, key):
    h = s['held'].pop(m); px = float(px)
    r = float(px / h['e'] - 1 - COST)
    s['trades'].append({'m': m, 'open': h['open'], 'close': dt.datetime.now(KST).isoformat(timespec='seconds'), 'e': h['e'], 'x': px, 'ret': r, 'why': why})
    s['last'][m] = {'bar': key, 'px': px, 'profit': bool(px > h['e'])}


def report(st, day=None):
    now = dt.datetime.now(KST)
    day = day or (now - dt.timedelta(days=1)).date()
    lines = [f'# 매수 방식 2주 모의 운용 보고 ({day}, 작성 {now:%m-%d %H:%M})', '',
             f'- 기간 {START:%m-%d %H:%M} ~ {END:%m-%d %H:%M} (경과 {max(0, (now - START).total_seconds()) / 86400:.1f}일). 조회 전용 모의, 실제 주문 없음. 1건 10만 원 환산, 비용 왕복 0.3% 포함',
             '- 판정 기준(미리 고정): 2주·50건 이상, 1건 평균 +0.2% 이상이면 소액 실거래 전환 제안', '']
    for code, cfg in STRATS.items():
        s = st['S'][code]; tr = s['trades']
        d = [t for t in tr if dt.datetime.fromisoformat(t['close']).date() == day]
        lines += [f"## {code}. {cfg['name']} (손절 {cfg['stop']:.1%}·+{cfg['at']:.0%} 뒤 {cfg['give']:.0%} 반납)", '',
                  '| 구분 | 건수 | 1건 평균 | 합계(1건 10만 원) | 승률 | 손절 | 이익보호 | 순위 |', '|---|---|---|---|---|---|---|---|']
        for name, xs in ((f'{day:%m-%d} 하루', d), ('시작 뒤 누적', tr)):
            if xs:
                v = np.array([t['ret'] for t in xs])
                cnt = lambda w: sum(t['why'] == w for t in xs)
                lines.append(f"| {name} | {len(v)} | {v.mean():+.2%} | {v.sum() * KRW:+,.0f}원 | {(v > 0).mean():.0%} | {cnt('손절')} | {cnt('이익보호')} | {cnt('순위')} |")
            else:
                lines.append(f'| {name} | 0 | - | - | - | - | - | - |')
        marks = st.get('marks', {})
        if s['held']:
            lines += ['', '보유 중: ' + ', '.join(f"{m[4:]} {(marks.get(m, h['e']) / h['e'] - 1):+.1%}(매수 {h['open'][5:16]})" for m, h in s['held'].items())]
        lines.append('')
    a, b = st['S']['A']['trades'], st['S']['B']['trades']
    if a and b:
        lines.append(f"- 누적 비교: B − A = 1건 평균 {np.mean([t['ret'] for t in b]) - np.mean([t['ret'] for t in a]):+.2%}p, 합계 {(sum(t['ret'] for t in b) - sum(t['ret'] for t in a)) * KRW:+,.0f}원")
    fl = flow_status()
    if fl: lines.append(f'- 체결 강도·호가 수집(3번): {fl}')
    lines += ['', '주의: 하루·며칠 결과는 장세 영향이 크다. 판정은 2주 뒤 기준으로만 한다.']
    REPORTS.mkdir(exist_ok=True)
    f = REPORTS / f'paper_vc_{day:%Y%m%d}.md'
    f.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return f


def flow_status():
    d = ROOT / 'v5-app' / 'v5' / 'data' / 'flow'
    fs = sorted(d.glob('flow_*.jsonl')) if d.exists() else []
    if not fs: return ''
    n = sum(1 for f in fs for _ in open(f, encoding='utf-8'))
    return f'{len(fs)}일치 파일, {n:,}줄(1분 × 종목), 마지막 {fs[-1].name}'


def single_instance():
    import msvcrt
    OUT.mkdir(parents=True, exist_ok=True)
    f = (OUT / 'paper_vc.lock').open('a+b')
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1); return f
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--loop', type=int, default=0); ap.add_argument('--report', action='store_true')
    a = ap.parse_args()
    st = load()
    if a.report:
        print(report(st, dt.datetime.now(KST).date())); return 0
    if not a.loop:
        print(step(st)); save(st); return 0
    lock = single_instance()
    if not lock: return 0
    done = OUT / 'last_daily.txt'
    while dt.datetime.now(KST) < END + dt.timedelta(hours=2):
        try:
            if step(st): save(st)
            now = dt.datetime.now(KST)
            if now.hour >= 9 and (not done.exists() or done.read_text() != str(now.date())) and now > START + dt.timedelta(hours=21):
                report(st); done.write_text(str(now.date()))
        except Exception as ex:
            with (OUT / 'error.log').open('a', encoding='utf-8') as f: f.write(f'{dt.datetime.now(KST).isoformat()} {type(ex).__name__}: {ex}\n')
        time.sleep(a.loop)
    report(st, dt.datetime.now(KST).date())
    return 0


if __name__ == '__main__':
    sys.exit(main())
