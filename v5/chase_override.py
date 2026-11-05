"""과열 상한 예외 (사용자 지시 2026-11-05: 오른 종목을 끝까지 들고 가기).

1. 사용자 직접 지정: 18시간 상승률이 과열 상한(15%) 이상 EXT_CAP(20%) 미만인 종목을 화면에서 '매수 대상'으로 올리면
   PROMOTE_HOURS 동안 그 종목의 상한을 EXT_CAP으로 본다.
2. 이익 청산 뒤 재진입: 이익으로 전량 매도한 종목은 REENTRY_HOURS 안에 한 번, 상한 EXT_CAP으로 다시 살 수 있다.
   재진입 대기·눌림 조건(reentry_profit_hours, reentry_dip_atr)은 이 한 번에는 적용하지 않는다. 재진입으로 산 물량을 판 뒤에는 다시 주지 않는다.
3. 과열 보유: 보유 중 18시간 상승률이 과열 상한 이상이 된 종목은 순위 매도(유지 순위 밖·상대강도 음수·거래대금 상위 밖)를 하지 않고
   손절·이익보호로만 판다. 매도 뒤에는 표시를 지운다.

파일: data/chase_promoted.json(봇 화면이 쓴다), data/hot_held.json(주문안 생성기가 쓴다). 쓰는 쪽이 하나씩이라 서로 덮지 않는다.
읽기 실패·형식 오류는 '예외 없음'으로 본다(기존 15% 규칙으로 돌아간다).
"""
import json
import math
import time
from pathlib import Path

EXT_CAP = 0.20
PROMOTE_HOURS = 24
REENTRY_HOURS = 24
PROMOTED = 'chase_promoted.json'
HOT_HELD = 'hot_held.json'


def _read(path):
    try:
        doc = json.loads(Path(path).read_text(encoding='utf-8'))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, doc):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(path)


def _finite(x):
    return type(x) in (int, float) and math.isfinite(x)


def ext_cap(p):
    """예외 종목에 쓰는 상한. 과열 상한이 꺼져 있으면(0) 예외도 없다."""
    cap = p.chase_rs_cap
    return max(cap, EXT_CAP) if _finite(cap) and cap > 0 else cap


def promoted(data_dir, now=None):
    """지금 유효한 직접 지정 종목 {종목: 만료 시각}."""
    rows = _read(Path(data_dir) / PROMOTED).get('markets', {})
    if not rows:
        return {}
    now = time.time() if now is None else now
    return {m: v['until'] for m, v in rows.items() if isinstance(v, dict) and _finite(v.get('until')) and v['until'] > now}


def promote(data_dir, market, rs, p, now=None):
    """(성공 여부, 안내문). 상승률이 과열 상한 이상·EXT_CAP 미만일 때만 올린다."""
    now = time.time() if now is None else now
    cap = p.chase_rs_cap
    if not (_finite(rs) and _finite(cap) and cap > 0 and cap <= rs < ext_cap(p)):
        return False, f'상승률 {rs:+.1%}: 직접 지정은 {cap:.0%} 이상 {EXT_CAP:.0%} 미만일 때만 가능합니다.'
    path = Path(data_dir) / PROMOTED
    doc = _read(path)
    rows = {m: v for m, v in doc.get('markets', {}).items() if isinstance(v, dict) and _finite(v.get('until')) and v['until'] > now}
    rows[market] = {'at': now, 'until': now + PROMOTE_HOURS * 3600, 'rs': rs}
    _write(path, {'markets': rows})
    return True, f'{market[4:]}: {PROMOTE_HOURS}시간 동안 매수 대상(상한 {EXT_CAP:.0%}). 다음 정시 판단부터 적용됩니다.'


def unpromote(data_dir, market):
    path = Path(data_dir) / PROMOTED
    doc = _read(path)
    rows = doc.get('markets', {})
    if market not in rows:
        return False, f'{market[4:]}: 직접 지정 상태가 아닙니다.'
    rows.pop(market)
    _write(path, {'markets': rows})
    return True, f'{market[4:]}: 직접 지정을 취소했습니다.'


def reentry_ok(last, now=None):
    """이익 청산 뒤 한 번 허용되는 재진입인가."""
    if not (isinstance(last, dict) and last.get('profit') is True and not last.get('from_reentry') and _finite(last.get('time'))):
        return False
    now = time.time() if now is None else now
    return 0 <= now - last['time'] < REENTRY_HOURS * 3600


def cap_for(market, p, promoted_markets, last, now=None):
    """이 종목에 적용할 과열 상한."""
    if market in promoted_markets or reentry_ok(last, now):
        return ext_cap(p)
    return p.chase_rs_cap


def hot_held(data_dir):
    rows = _read(Path(data_dir) / HOT_HELD).get('markets', [])
    return {m for m in rows if isinstance(m, str)} if isinstance(rows, list) else set()


def update_hot_held(data_dir, held, rs_of, p):
    """보유 중 과열 상한 이상이 된 종목을 표시하고, 더 이상 보유하지 않는 종목은 지운다. 반환: 표시된 종목 집합."""
    cap = p.chase_rs_cap
    old = hot_held(data_dir)
    new = {m for m in old if m in held}
    if _finite(cap) and cap > 0:
        new |= {m for m in held if _finite(rs_of.get(m)) and rs_of[m] >= cap}
    if new != old:
        _write(Path(data_dir) / HOT_HELD, {'markets': sorted(new)})
    return new
