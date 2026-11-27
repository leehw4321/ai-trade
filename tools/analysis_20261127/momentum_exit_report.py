"""하락 흐름 조기 손절(momentum_exit) 2주 비교 리포트 (2026-11-27 사용자 지시: "2주간 비교 모니터링해서 리포트").

  python tools/analysis_20261127/momentum_exit_report.py   → 코인 트레이딩/reports/momentum_exit_report_YYYYMMDD_HHMM.md

1) 발동별 '안 팔았으면': 발동 봉 마감부터 업비트 5분봉으로 원래 규칙을 근사한다 — 원래 손절가(발동 때 장부 stop)에 닿으면 손절,
   +3% 도달 뒤 이익의 30% 반납이면 이익보호, 최대 72시간. 실제 조기 매도 체결과 비교(매도 비용 0.15% 반영).
2) 적용 전후 전체 성적: 같은 기간 길이로 하루 손익·손절 비율(stop035_review의 왕복 계산 재사용).
읽기 전용, 주문 없음. 표본이 작아 입증이 아니다.
"""
import datetime as dt, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis_20261111'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stop035_review as R
import early_stop_sim as E

START = dt.datetime(2026, 11, 27, 19, 30, tzinfo=R.KST)    # 실거래 적용(배포) 시각 — 배포 뒤 정정
LOG = R.DATA / 'momentum_exit.jsonl'
UTC = dt.timezone.utc


def counterfactual(f, now):
    """발동 뒤 원래 규칙으로 들고 있었으면 판 가격과 사유."""
    t0 = dt.datetime.fromisoformat(f['bar']) + dt.timedelta(minutes=5)
    end = min(t0 + dt.timedelta(hours=72), now)
    bars = [b for b in E.candles(f['market'], t0, end) if dt.datetime.fromisoformat(b['t']).replace(tzinfo=UTC) >= t0.astimezone(UTC)]
    entry, stop, peak = f['entry'], f.get('stop') or f['entry'] * (1 - 0.035), f['entry']
    for b in bars:
        if b['l'] <= stop:
            return min(stop, b['o']), '원래 손절가'
        peak = max(peak, b['c'])
        if peak >= entry * 1.03 and b['c'] <= peak - 0.3 * (peak - entry):
            return b['c'], '이익보호'
    if end < t0 + dt.timedelta(hours=72):
        return (bars[-1]['c'] if bars else None), '진행 중(현재가)'
    return (bars[-1]['c'] if bars else None), '72시간 보유 끝'


def main():
    now = dt.datetime.now(R.KST)
    fires = [json.loads(l) for l in LOG.open(encoding='utf-8')] if LOG.exists() else []
    sold = [f for f in fires if f.get('sold')]
    lines = [f'# 하락 흐름 조기 손절 비교 리포트 ({now:%Y-%m-%d %H:%M} KST)', '',
             f'- 규칙: 손실 1% 이상 + 5분 2연속·15분·1시간 하락이면 시장가 매도. 적용 {START:%m-%d %H:%M}, 경과 {(now - START).total_seconds() / 86400:.1f}일',
             f'- 발동 {len(fires)}회, 실제 매도 {len(sold)}건', '',
             '## 1. 발동별: 조기 매도 vs 안 팔았으면(원래 손절가·이익보호, 최대 72시간)', '',
             '| 발동 | 종목 | 매수가 대비 | 조기 매도 손익 | 안 팔았으면 | 차이(조기−원래) | 원래 결과 |', '|---|---|---|---|---|---|---|']
    tot = better = 0
    for f in sold:
        fill = f.get('fill') or {}
        vol, avg = fill.get('vol') or f.get('qty') or 0, fill.get('avg') or f['bar_close']
        cf, why = counterfactual(f, now)
        if cf is None: continue
        early = vol * (avg - f['entry']) - (fill.get('fee') or vol * avg * 0.0005)
        held = vol * (cf * (1 - 0.0015) - f['entry'])
        d = early - held; tot += d; better += d > 0
        lines.append(f"| {f['time'][5:16]} | {f['market'][4:]} | {f['bar_close'] / f['entry'] - 1:+.2%} | {early:+,.0f} | {held:+,.0f} | **{d:+,.0f}** | {why} |")
    lines += ['', f'합계 차이 **{tot:+,.0f}원** (조기 매도가 나았던 건 {better}/{len(sold)})', '']
    trips, _, _ = R.load()
    span = now - START
    pre = [t for t in trips if START - span <= t['open'] < START]
    post = [t for t in trips if t['open'] >= START]
    lines += ['## 2. 적용 전후 같은 길이 기간 전체 성적', '', '| | 기간 | 거래 | 손익 합계 | 하루 평균 | 승률 | 손절 비율 |', '|---|---|---|---|---|---|---|']
    for name, ts, a in (('적용 전', pre, START - span), ('적용 후', post, START)):
        days = span.total_seconds() / 86400 or 1
        n = len(ts); pnl = sum(t['pnl'] for t in ts)
        lines.append(f"| {name} | {a:%m-%d}~ | {n} | {pnl:+,.0f} | {pnl / days:+,.0f} | {(sum(t['pnl'] > 0 for t in ts) / n if n else 0):.0%} | {(sum(t['stop'] for t in ts) / n if n else 0):.0%} |")
    lines += ['', '주의: 안 팔았으면 계산은 5분봉 근사(봉 안 순서는 손절 우선), 부분 익절·순위 매도 생략. 전후 비교는 장세 차이가 크다. 입증이 아니다.']
    out = R.OUT / f'momentum_exit_report_{now:%Y%m%d_%H%M}.md'
    out.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(out); print('\n'.join(lines[:12]))


if __name__ == '__main__':
    main()
