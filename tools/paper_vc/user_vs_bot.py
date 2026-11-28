"""사용자 직접 주문 vs 봇 자동 거래 승패·패턴 비교 (2026-11-28 사용자 지시: "사용자 주문과 봇 자동거래를 비교하여 승패를 알고 싶고 패턴을 분석"). 조회 전용.

사용자 거래 = 봇 화면 순위표 '직접 매수'(사용자 주문으로 표시·보호, data/live/user_trades.jsonl). 같은 거래 id의 추가 매수·매도를 한 건으로 묶는다.
봇 거래 = 거래소 체결 중 봇 식별자(v5-) 왕복에서 사용자 직접 매수 왕복을 뺀 것.
매수 순간 조건 = 15분마다 남는 data/entry_flags.jsonl(순위·18시간 상승률·1/4시간 변화·고점 대비·장세)에서 매수 시각 직전 기록. 양쪽을 같은 자료로 본다.
판단 주의: 20건 미만이면 우연 차이가 커서 패턴 결론을 내리지 않는다.
"""
import bisect
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(r'C:\Users\User\Desktop\코인 트레이딩')
DATA = ROOT / 'v5-app' / 'v5' / 'data'
KST = dt.timezone(dt.timedelta(hours=9))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis_20261111'))
MIN_N = 20


def _ts(s):
    t = dt.datetime.fromisoformat(str(s))
    return t if t.tzinfo else t.replace(tzinfo=KST)


def user_trips(path=None):
    path = path or DATA / 'live' / 'user_trades.jsonl'
    trips = {}
    try:
        rows = [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]
    except OSError:
        return []
    for r in rows:
        tid = r.get('id')
        if not tid: continue
        t = trips.setdefault(tid, {'id': tid, 'm': r.get('market'), 'open': None, 'cost': 0., 'realized': 0., 'sold_cost': 0.,
                                   'closed': False, 'close': None, 'ctx': {}, 'adds': 0})
        if r['event'] in ('open', 'add'):
            if r['event'] == 'open' or t['open'] is None:
                t['open'] = _ts(r['time']); t['ctx'] = r.get('context') or {}
            else:
                t['adds'] += 1
            t['cost'] += float(r.get('krw') or 0)
        elif r['event'] == 'sell':
            t['realized'] += float(r.get('realized') or 0); t['sold_cost'] += float(r.get('cost_sold') or 0)
            if r.get('closed'): t['closed'], t['close'] = True, _ts(r['time'])
    out = []
    for t in trips.values():
        if t['open'] is None: continue
        t['ret'] = t['realized'] / t['sold_cost'] if t['sold_cost'] else None
        t['pnl'] = t['realized']
        out.append(t)
    return sorted(out, key=lambda t: t['open'])


def bot_trips(since, users):
    import stop035_review as R
    trips, _, _ = R.load()
    out = []
    for t in trips:
        if t['open'] < since: continue
        if any(u['m'] == t['m'] and abs((u['open'] - t['open']).total_seconds()) < 300 for u in users): continue
        out.append({'m': t['m'], 'open': t['open'], 'close': t['close'], 'closed': True, 'ret': t['ret'], 'pnl': t['pnl'],
                    'why': (t.get('why') or ['?'])[-1]})
    return out


class Flags:
    """시장별 15분 기록. 매수 시각 직전(30분 안) 기록을 돌려준다."""
    def __init__(self, path=None):
        self.by = {}
        try:
            for l in open(path or DATA / 'entry_flags.jsonl', encoding='utf-8'):
                try: r = json.loads(l)
                except ValueError: continue
                self.by.setdefault(r['market'], []).append((_ts(r['time']).timestamp(), r))
        except OSError:
            pass
        for v in self.by.values(): v.sort(key=lambda x: x[0])

    def at(self, m, t):
        rows = self.by.get(m) or []
        i = bisect.bisect_right([x[0] for x in rows], t.timestamp()) - 1
        if i < 0 or t.timestamp() - rows[i][0] > 1800: return None
        r = rows[i][1]; f = r.get('f') or {}; k = r.get('mkt') or {}
        return {'rank': r.get('rank'), 'rs': r.get('rs'), 'r1': f.get('r1'), 'r4': f.get('r4'), 'from_hi': f.get('from_hi'),
                'regime': k.get('regime_index')}


BUCKETS = {
    '순위': ('rank', ((1, 3, '1~3위'), (4, 7, '4~7위'), (8, 999, '8위 밖'))),
    '18시간 상승률': ('rs', ((-9, .05, '5% 미만'), (.05, .15, '5~15%'), (.15, 9, '15% 이상'))),
    '직전 1시간': ('r1', ((-99, 0, '하락 중'), (0, 99, '상승 중'))),
    '고점 대비(12시간)': ('from_hi', ((-99, -2, '고점서 2%↓ 아래'), (-2, 99, '고점 근처'))),
    '시간대': ('hour', ((0, 8, '0~8시'), (9, 12, '9~12시'), (13, 18, '13~18시'), (19, 23, '19~23시'))),
}


def stat(ts):
    done = [t for t in ts if t.get('closed') and t.get('ret') is not None]
    if not done: return None
    w = sum(t['pnl'] > 0 for t in done)
    hold = [(t['close'] - t['open']).total_seconds() / 3600 for t in done if t.get('close')]
    return {'n': len(done), 'win': w / len(done), 'avg': sum(t['ret'] for t in done) / len(done),
            'sum': sum(t['pnl'] for t in done), 'hold': sum(hold) / len(hold) if hold else None}


def fmt(s):
    if not s: return '| 0 | - | - | - | - |'
    return f"| {s['n']} | {s['win']:.0%} | {s['avg']:+.2%} | {s['sum']:+,.0f}원 | {'-' if s['hold'] is None else format(s['hold'], '.1f') + '시간'} |"


def lines(now=None):
    try:
        users = user_trips()
        out = ['## 사용자 직접 주문 vs 봇 자동 거래', '']
        if not users:
            return out + ['- 아직 사용자 직접 주문 없음(봇 화면 순위표에서 커서를 올리면 [직접 매수]).', '']
        flags = Flags()
        since = users[0]['open']
        bots = bot_trips(since, users)
        for t in users + bots:
            t['f'] = flags.at(t['m'], t['open']) or {}
            if t in users and t['f'].get('rank') is None and isinstance(t['ctx'].get('rank'), int):
                t['f'].update({k: t['ctx'].get(k) for k in ('rank', 'rs')})
            t['f']['hour'] = t['open'].astimezone(KST).hour
        su, sb = stat(users), stat(bots)
        out += [f"비교 기간: 첫 사용자 주문 {since.astimezone(KST):%m-%d %H:%M}부터 · 끝난 거래만 승패 집계", '',
                '| 구분 | 끝난 건 | 승률 | 1건 평균 | 손익 합계 | 평균 보유 |', '|---|---|---|---|---|---|',
                '| 사용자 직접 주문 ' + fmt(su), '| 봇 자동 거래 ' + fmt(sb), '']
        n_u = su['n'] if su else 0
        if n_u < MIN_N:
            out.append(f'- 사용자 거래 {n_u}건: {MIN_N}건 전에는 우연 차이가 커서 승패·패턴 결론을 내리지 않는다(아래 표는 기록 확인용).')
        out += ['', '### 매수 순간 조건별 (사용자 / 봇)', '', '| 조건 | 구간 | 사용자 건·승률·평균 | 봇 건·승률·평균 |', '|---|---|---|---|']
        for title, (key, bins) in BUCKETS.items():
            for lo, hi, name in bins:
                pick = lambda ts: [t for t in ts if isinstance(t['f'].get(key), (int, float)) and lo <= t['f'][key] < hi + (1 if key in ('rank', 'hour') else 0)]
                a, b = stat(pick(users)), stat(pick(bots))
                cell = lambda s: '-' if not s else f"{s['n']}건 · {s['win']:.0%} · {s['avg']:+.2%}"
                if a or b: out.append(f'| {title} | {name} | {cell(a)} | {cell(b)} |')
        out += ['', '### 사용자 직접 주문 한 건씩', '', '| 매수 | 종목 | 순위 | 18시간 | 1시간 | 고점 대비 | 금액 | 결과 | 보유 |', '|---|---|---|---|---|---|---|---|---|']
        for t in users:
            f = t['f']; p = lambda v, d=1: '-' if not isinstance(v, (int, float)) else f'{v:+.{d}f}%'
            res = '보유 중' if not t['closed'] else f"{t['ret']:+.2%} ({t['pnl']:+,.0f}원)"
            hold = '-' if not t['close'] else f"{(t['close'] - t['open']).total_seconds() / 3600:.1f}시간"
            out.append(f"| {t['open'].astimezone(KST):%m-%d %H:%M} | {t['m'][4:]} | {f.get('rank') or '-'} | {p(f['rs'] * 100) if isinstance(f.get('rs'), (int, float)) else '-'} | "
                       f"{p(f.get('r1'), 2)} | {p(f.get('from_hi'), 1)} | {t['cost']:,.0f}원{' (+추가 ' + str(t['adds']) + ')' if t['adds'] else ''} | {res} | {hold} |")
        return out + ['', '주: 봇 거래도 같은 기간·같은 매수 순간 기록으로 묶었다. 화면 재매수 해제·직접 지정 버튼 거래는 위 강제 활성화 표에 따로 있다.', '']
    except Exception as ex:
        return ['## 사용자 직접 주문 vs 봇 자동 거래', '', f'- 집계 실패: {type(ex).__name__}: {str(ex)[:120]}', '']


if __name__ == '__main__':
    print('\n'.join(lines()))
