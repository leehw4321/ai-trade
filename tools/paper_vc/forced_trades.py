"""사용자 강제 활성화 거래 누적 승률 (2026-11-28 사용자 지시: 강제 활성화로 거래된 것만 모아 매일 보고서에 누적 승률). 조회 전용.

강제 활성화 = 봇 화면 버튼
  - '과열 종목 직접 지정'(1회 매수 권한): 매수 후보 기록(missed_ledger.jsonl)에서 promoted=True 이고 outcome='매수'인 회차의 매수만
  - '재매수 금지 해제'(1회): 해제 뒤 24시간 안 그 종목의 첫 봇 매수(그 사이 '재매수 해제 취소'가 없을 때)
  - '지정가 → 즉시 시장가', '수동 추가 매수', '수동 매도/강제 청산': 해당 종목·시각 기록
자료: 이 PC의 봇 로그(data/bot.stdout.log)와 거래소 체결 기록. 다른 PC에서 봇이 돌던 동안 누른 버튼은 빠질 수 있다.
"""
import datetime as dt, json, re, sys
from pathlib import Path

ROOT = Path(r'C:\Users\User\Desktop\코인 트레이딩')
DATA = ROOT / 'v5-app' / 'v5' / 'data'
KST = dt.timezone(dt.timedelta(hours=9))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis_20261111'))

PAT = re.compile(r'^\[(?P<t>[^\]]+)\] (?P<kind>과열 종목 직접 지정 취소|과열 종목 직접 지정|재매수 해제 취소|재매수 금지 해제|지정가 → 즉시 시장가|수동 추가 매수[^ ]* ?[^ ]*|수동 강제 청산|수동 매도[^K]*) (?P<m>KRW-[A-Z0-9]+): 성공')


def actions():
    """봇 로그 + P10 이벤트 기록(봇 재시작 사이 로그가 빠진 때도 남아 있음)을 합쳐 중복 제거."""
    out = set()
    try:
        for line in open(DATA / 'bot.stdout.log', encoding='utf-8', errors='replace'):
            g = PAT.match(line)
            if g: out.add((dt.datetime.fromisoformat(g['t']), g['kind'].strip(), g['m']))
    except OSError:
        pass
    try:
        for line in open(DATA / 'P10' / 'events.jsonl', encoding='utf-8', errors='replace'):
            try: r = json.loads(line)
            except ValueError: continue
            g = PAT.match(f"[{r.get('time', '')}] {r.get('msg', '')}")
            if g: out.add((dt.datetime.fromisoformat(g['t']), g['kind'].strip(), g['m']))
    except OSError:
        pass
    return sorted(out)


def collect():
    import stop035_review as R
    trips, cur, _ = R.load()
    acts = actions()
    promo = set()
    try:
        for l in open(DATA / 'missed_ledger.jsonl', encoding='utf-8'):
            r = json.loads(l)
            if r.get('promoted') and r.get('outcome') == '매수':
                promo.add((r['market'], int(r['slot'])))
    except OSError:
        pass
    rows = []
    allt = [(t, False) for t in trips] + [(t, True) for t in cur.values()]
    for ta, kind, m in acts:
        if '취소' in kind: continue
        if kind == '과열 종목 직접 지정':
            cand = [(t, o) for t, o in allt if t['m'] == m and ta < t['open'] <= ta + dt.timedelta(days=3)
                    and any((m, s) in promo for s in range(int(t['open'].timestamp()) // 900 * 900 - 1800, int(t['open'].timestamp()) + 1, 900))]
            if not cand:   # 다른 버튼(재매수 해제)과 함께 눌러 바로 산 경우: 10분 안 첫 매수를 같은 거래로 본다
                cand = [(t, o) for t, o in allt if t['m'] == m and ta < t['open'] <= ta + dt.timedelta(minutes=10)]
            cancel = [a for a, k, mm in acts if mm == m and k == '과열 종목 직접 지정 취소' and a > ta]
            if not cand and cancel and cancel[0] - ta < dt.timedelta(hours=1):
                rows.append(dict(at=ta, kind=kind, m=m, done='취소')); continue
        elif kind == '재매수 금지 해제':
            cancel = min([a for a, k, mm in acts if mm == m and k == '재매수 해제 취소' and a > ta] or [ta + dt.timedelta(days=1)])
            cand = [(t, o) for t, o in allt if t['m'] == m and ta < t['open'] <= min(cancel, ta + dt.timedelta(days=1))]
        else:
            cand = [(t, o) for t, o in allt if t['m'] == m and t['open'] <= ta + dt.timedelta(minutes=5) and (o or t.get('close', ta) >= ta - dt.timedelta(minutes=5))]
        if not cand:
            rows.append(dict(at=ta, kind=kind, m=m, done=None)); continue
        t, open_ = min(cand, key=lambda x: x[0]['open'])
        rows.append(dict(at=ta, kind=kind, m=m, open=t['open'], done=not open_, ret=t.get('ret'), pnl=t.get('pnl'),
                         entry=t['bf'] / t['bq'], why=(t.get('why') or ['보유 중'])[0]))
    return rows


def lines():
    try:
        rows = collect()
    except Exception as ex:
        return [f'- 강제 활성화 거래 집계 실패: {type(ex).__name__}']
    # 한 거래에 버튼 여러 개(예: 재매수 해제 + 직접 지정)면 1건으로 합친다
    merged = {}
    for r in rows:
        k = (r['m'], r['open']) if r.get('open') else (r['m'], r['at'], r['kind'])
        if k in merged: merged[k]['kind'] += ' + ' + r['kind']
        else: merged[k] = dict(r)
    rows = sorted(merged.values(), key=lambda r: r['at'])
    done = [r for r in rows if r['done']]
    done = [r for r in rows if r['done'] is True]
    held = [r for r in rows if r['done'] is False]
    none = [r for r in rows if r['done'] is None]
    out = ['## 사용자 강제 활성화 거래 누적', '']
    if done:
        w = sum(r['pnl'] > 0 for r in done)
        out.append(f"- 끝난 거래 {len(done)}건: 승률 **{w / len(done):.0%}** ({w}승 {len(done) - w}패), 손익 합계 **{sum(r['pnl'] for r in done):+,.0f}원**, 1건 평균 {sum(r['ret'] for r in done) / len(done):+.2%}")
    else:
        out.append('- 끝난 거래 없음')
    if held: out.append('- 보유 중: ' + ', '.join(f"{r['m'][4:]}({r['kind']}, 매수 {r['open'].astimezone(KST):%m-%d %H:%M})" for r in held))
    if none: out.append(f'- 조작했지만 그 조건으로 매수 안 됨: {len(none)}건')
    out += ['', '| 조작 시각 | 조작 | 종목 | 매수 | 결과 |', '|---|---|---|---|---|']
    for r in rows:
        res = '바로 취소' if r['done'] == '취소' else '매수 안 됨' if r['done'] is None else ('보유 중' if not r['done'] else f"{r['ret']:+.2%} ({r['pnl']:+,.0f}원, {r['why']})")
        buy = f"{r['open'].astimezone(KST):%m-%d %H:%M}" if r.get('open') else '-'
        out.append(f"| {r['at']:%m-%d %H:%M} | {r['kind']} | {r['m'][4:]} | {buy} | {res} |")
    out += ['', '주의: 이 PC 봇 로그 기준(다른 PC에서 누른 버튼은 빠질 수 있음).', '']
    return out


if __name__ == '__main__':
    print('\n'.join(lines()))
