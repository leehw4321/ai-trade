#!/usr/bin/env python3
"""Local web dashboard for upbit_bot.py. Serves http://127.0.0.1:8765 (this PC only)."""
import json
import logging
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import analog
import market
import upbit_bot as bot

bot.load_env()
PORT = int(os.environ.get('COINBOT_DASHBOARD_PORT', '8766'))
SELL = re.compile(r"^(\S+ \S+?),\d+ INFO SELL (\S+) \((.+?)\) vol=([\d.]+) net=([+-]\d+) KRW \| today ([+-]\d+) total ([+-]\d+)")
BUY = re.compile(r"^(\S+ \S+?),\d+ INFO BUY (\S+) score=([\d.]+) 15m=([+-][\d.]+)% 1h=([+-][\d.]+)% vol=([\d.]+)x atr=([\d.]+)% spread=([\d.]+)% size=(\d+)")
BOUGHT = re.compile(r"^(\S+ \S+?),\d+ INFO Bought (\S+) vol=([\d.]+) avg=([\d.]+) cost=(\d+)")


REC_LOCK = threading.Lock()
SCANNING = False
ACCOUNT_LOCK = threading.Lock()
ACCOUNT_CACHE = {"expires": 0, "data": None}


def account_data():
    """Cached account read only. Never return credentials or raw API errors."""
    with ACCOUNT_LOCK:
        if time.monotonic() < ACCOUNT_CACHE["expires"]:
            return ACCOUNT_CACHE["data"]
        try:
            rows = bot.Upbit().request("GET", "/v1/accounts")
            if not isinstance(rows, list):
                raise ValueError("unexpected account response")
            krw = next((row for row in rows if row.get("currency") == "KRW"), {})
            holdings = [{"currency": str(row.get("currency", "")),
                         "available": bot.fnum(row.get("balance")),
                         "locked": bot.fnum(row.get("locked"))}
                        for row in rows if row.get("currency") != "KRW"
                        and bot.fnum(row.get("balance")) + bot.fnum(row.get("locked")) > 0]
            result = {"connected": True, "checked_at": time.time(),
                      "krw_available": bot.fnum(krw.get("balance")),
                      "krw_locked": bot.fnum(krw.get("locked")), "holdings": holdings}
        except Exception as exc:
            code = next((code for code in ("no_authorization_ip", "invalid_access_key", "out_of_scope",
                                          "expired_access_key", "jwt_verification") if code in str(exc)), "")
            messages = {"no_authorization_ip": "업비트에 등록한 공인 IP를 확인하세요.",
                        "invalid_access_key": "API 키를 확인하세요.", "out_of_scope": "자산조회 권한을 확인하세요.",
                        "expired_access_key": "API 키가 만료되었습니다.",
                        "jwt_verification": "API 키 인증을 확인하세요."}
            result = {"connected": False, "error": messages.get(code, "계좌에 연결할 수 없습니다. 키 설정과 인터넷을 확인하세요.")}
        ACCOUNT_CACHE.update(expires=time.monotonic() + 30, data=result)
        return result


def run_scan():
    """Manual scan (button); normally the bot writes scan_result.json itself every minute."""
    global SCANNING
    with REC_LOCK:
        if SCANNING:
            return
        SCANNING = True
    try:
        diag = []
        picks = bot.scan(bot.build_cfg(), set(bot.read_state().get("positions") or {}), diag)
        bot.write_scan(diag, picks)
    except Exception as exc:
        logging.warning("Manual scan failed: %s", exc)
    finally:
        SCANNING = False


def recommend():
    rec = {"time": 0, "picks": [], "near": []}
    try:
        rec.update(json.loads(bot.SCAN_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    rec["running"], rec["error"] = SCANNING, ""
    return rec

def save_settings(body):
    saved = {}
    if bot.SETTINGS.exists():
        try:
            saved = json.loads(bot.SETTINGS.read_text(encoding="utf-8"))
        except Exception:
            saved = {}
    for key, (lo, hi) in bot.FIELDS.items():
        if key in body:
            value = bot.fnum(body[key], None)
            if value is None or not lo <= value <= hi:
                raise ValueError(f"{key}: {lo}~{hi} 범위의 숫자여야 합니다")
            saved[key] = value
    if "exclude" in body:
        saved["exclude"] = [m.strip().upper() for m in str(body["exclude"]).replace("\n", ",").split(",") if m.strip()]
    tmp = bot.SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(bot.SETTINGS)


def read_log():
    if not bot.LOG.exists():
        return [], [], [], []
    lines = bot.LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    sells, buys, bought = [], [], []
    for ln in lines:
        if m := SELL.match(ln):
            sells.append({"time": m[1], "market": m[2], "reason": m[3], "net": int(m[5]), "total": int(m[7])})
        elif m := BUY.match(ln):
            buys.append({"time": m[1], "market": m[2], "score": float(m[3]), "r15": float(m[4]),
                         "r1h": float(m[5]), "vol": float(m[6]), "atr": float(m[7]),
                         "spread": float(m[8]), "size": int(m[9])})
        elif m := BOUGHT.match(ln):
            bought.append({"time": m[1], "market": m[2], "avg": float(m[4]), "cost": int(m[5])})
    return sells, buys, bought, lines[-60:]


def event_top():
    """Event-window monitoring snapshot (written by the bot every ~3s); None when stale."""
    try:
        snap = json.loads(bot.EVENT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    return snap if time.time() - snap.get("time", 0) < 60 else None


def queued():
    """Answers already clicked but not yet applied by the bot loop."""
    try:
        return json.loads(bot.DECISION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def data():
    cfg = bot.build_cfg()
    state = bot.read_state()
    sells, buys, bought, tail = read_log()
    positions = []
    for market, pos in (state.get("positions") or {}).items():
        try:
            px = bot.price(market)
        except Exception:
            px = 0
        entry = bot.fnum(pos.get("entry_price"))
        cost = bot.fnum(pos.get("cost"))
        value = px * bot.fnum(pos.get("volume"))
        positions.append({
            "market": market, "entry": entry, "price": px, "cost": cost, "value": value,
            "pnl_pct": (px / entry - 1) * 100 if entry else 0, "pnl": value - cost,
            "age_min": (time.time() - bot.fnum(pos.get("entry_time"))) / 60,
            "stop": entry * (1 + cfg["be_lock"]) if (pos.get("part_done") or bot.fnum(pos.get("peak_price")) >= entry * (1 + cfg["part_on"]))
                    else entry * (1 - cfg["stop"]), "take": entry * (1 + cfg["take"]),
            "peak_pct": (bot.fnum(pos.get("peak_price")) / entry - 1) * 100 if entry else 0,
            "event": bool(pos.get("event"))})
    pending = [{"market": m, "pnl_pct": bot.fnum(p.get("pnl")) * 100, "peak_pct": bot.fnum(p.get("peak_pnl")) * 100,
                "price": bot.fnum(p.get("price")), "age": bot.fnum(p.get("age")), "why": p.get("why", "")}
               for m, p in (state.get("pending") or {}).items() if m in (state.get("positions") or {})]
    wins = [s for s in sells if s["net"] > 0]
    cfg["exclude"] = sorted(cfg["exclude"])
    return {"now": time.strftime("%Y-%m-%d %H:%M:%S"), "cfg": cfg,
            "realized": bot.fnum(state.get("realized")), "day_pnl": bot.fnum(state.get("day_pnl")),
            "positions": positions, "pending": pending, "event_day": state.get("event_day", ""),
            "queued": queued(), "sells": sells[::-1][:200], "buys": buys[::-1][:50],
            "wins": len(wins), "trades": len(sells),
            "bankroll": cfg["budget"] + bot.fnum(state.get("realized")), "log": tail,
            "rec": recommend(), "event": event_top()}


PAGE = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>업비트 단타 봇</title>
<style>
:root{--bg:#0f1218;--card:#181d27;--line:#262d3a;--tx:#e6e9ef;--mu:#8b94a7;--up:#ef5350;--dn:#4a90ff}
@media(prefers-color-scheme:light){:root{--bg:#f4f6fa;--card:#fff;--line:#dde2ea;--tx:#1b2230;--mu:#667085}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 system-ui,"Malgun Gothic",sans-serif}
.w{max-width:1100px;margin:0 auto;padding:16px}h1{font-size:20px;margin:0 0 4px}.mu{color:var(--mu)}
.g{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin:14px 0}
.c{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.c b{display:block;font-size:20px;margin-top:2px}.up{color:var(--up)}.dn{color:var(--dn)}
h2{font-size:15px;margin:22px 0 8px}table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:7px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}th{color:var(--mu);font-weight:500;font-size:12px}
th:first-child,td:first-child{text-align:left}.sc{overflow-x:auto}
.s{display:grid;gap:8px;grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}.s .c{font-size:13px}
pre{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px;overflow:auto;max-height:260px;font-size:12px;margin:0}
.bt{background:var(--card);color:var(--tx);border:1px solid var(--line);border-radius:8px;padding:6px 12px;font:inherit;cursor:pointer}
.bt.pri{background:#2f6fed;border-color:#2f6fed;color:#fff}.bt:disabled{opacity:.5;cursor:default}
fieldset{border:1px solid var(--line);border-radius:10px;background:var(--card);margin:0;padding:8px 14px 12px}legend{font-weight:600;padding:0 6px}
label{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:8px;font-size:13px}
label small{color:var(--mu);display:block}select{background:var(--bg);color:var(--tx);border:1px solid var(--line);border-radius:6px;padding:5px 7px;font:inherit}input,textarea{width:110px;background:var(--bg);color:var(--tx);border:1px solid var(--line);border-radius:6px;padding:5px 7px;font:inherit;text-align:right}
textarea{width:100%;text-align:left;margin-top:6px;height:54px}input.bad{border-color:var(--dn)}.ok{color:#2e9e5b}.er{color:var(--up)}
.sg{display:grid;gap:12px;grid-template-columns:minmax(150px,200px) 1fr 1fr;margin:8px 0}@media(max-width:800px){.sg{grid-template-columns:1fr}}
.lab{font-size:30px;font-weight:800;margin:4px 0}.lab.up{color:var(--up)}.lab.dn{color:var(--dn)}
.dots{display:flex;gap:4px;margin-top:6px}.dots i{flex:1;height:8px;border-radius:4px;background:var(--line)}
.dv{display:grid;grid-template-columns:118px 1fr 58px;align-items:center;gap:8px;margin:7px 0;font-size:12px}
.dv .tr{position:relative;height:14px;background:var(--bg);border-radius:3px}.dv .tr:before{content:"";position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--mu)}
.dv .tr i{position:absolute;top:0;bottom:0}.dv b{text-align:right;font-size:12px;margin:0}
.st{display:flex;height:16px;border-radius:4px;overflow:hidden;margin-top:4px}.st i{display:block;height:100%}
.chip{display:inline-block;font-size:12px;padding:2px 10px;border-radius:12px;border:1px solid var(--line);color:var(--mu);margin:0 6px 6px 0}.chip.g1{color:var(--up);border-color:var(--up)}.chip.b1{color:var(--dn);border-color:var(--dn)}
.ck{display:grid;grid-template-columns:112px 1fr;gap:1px 8px;font-size:12px;margin-top:6px}.ck span:nth-child(2){text-align:right}.ck .bar{grid-column:1/-1;margin-top:0;height:4px}
#toast{position:fixed;top:12px;right:12px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 14px;display:none;z-index:9}
.tg2{font-size:11px;color:var(--mu);margin-left:4px}.bar{height:6px;background:var(--line);border-radius:3px;margin-top:6px;overflow:hidden}.bar i{display:block;height:100%}
</style></head><body><div class="w">
<div class="mu"><a href="/market" style="color:inherit">시황 리포트 →</a> · <a href="/morning-analysis" style="color:inherit">오늘 오전 시뮬레이션 분석 →</a> · <a href="/bot-review" style="color:inherit">단타봇 종합 진단 →</a></div>
<h1>업비트 단타 봇 <span class="mu" id="now"></span></h1>
<div class="mu">5분·15분 모멘텀 돌파 · 최대 <span id="mp"></span>종목 · 5초마다 자동 갱신</div>
<h2>업비트 계좌 <span id="accountstatus" class="mu" style="font-weight:400">연결 확인 중…</span></h2>
<div class="g" id="accountsum"></div>
<div class="sc"><table id="accountholdings"></table></div>
<div class="mu" style="margin-top:6px">계좌 정보는 30초마다 조회합니다. 이 화면을 여는 것으로 자동매매가 시작되지는 않습니다.</div>
<h2>봇 운용 현황</h2>
<div class="g" id="sum"></div>
<h2>시황 시그널 <span class="mu" id="sigt" style="font-weight:400"></span></h2>
<div class="sg"><div class="c"><span class="mu">현재 국면</span><div class="lab" id="siglab">-</div><div class="dots" id="sigdots"></div><div class="mu" id="sigsub" style="margin-top:6px;font-size:12px"></div></div>
<div class="c" id="sigbars"></div><div class="c"><span class="mu">BTC 최근 25시간 (1시간봉 등락)</span><div id="sighr"></div></div></div>
<h2>과거 유사 국면 <span class="mu" id="ant" style="font-weight:400"></span></h2>
<div class="c" id="anhead"><span class="mu">계산 중…</span></div>
<div style="margin-top:8px" id="ankw"></div>
<div class="sc"><table id="antbl"></table></div>
<div class="mu" id="annote" style="font-size:12px;margin-top:6px"></div>
<div id="evbox"></div>
<h2>보유 포지션</h2><div class="sc"><table id="pos"></table></div>
<div id="toast"></div>
<div id="evmon"></div>
<h2>모니터링 상위 10 <span class="mu" id="montime" style="font-weight:400"></span></h2>
<div class="mu" style="margin-bottom:6px">조건을 가장 많이 충족한 순서입니다. 막대는 각 조건의 기준치까지 도달 정도(꽉 차면 충족)이며, 매수 버튼은 시장가로 1종목 몫(시드÷최대 보유 종목 수)을 즉시 주문합니다.</div>
<div class="s" id="mon"></div>
<h2>종목 추천 <span class="mu" id="rectime" style="font-weight:400"></span> <button class="bt" id="scanbtn">지금 스캔</button></h2>
<div class="mu" style="margin-bottom:6px">현재 설정 기준으로 진입 조건을 모두 통과한 종목입니다. 아래 표는 조건이 3개 이하로 부족한 근접 후보입니다. 봇이 1분마다 스캔한 최신 결과입니다.</div>
<div class="sc"><table id="recs"></table></div><div class="sc" style="margin-top:8px"><table id="near"></table></div>
<h2>전략 설정 <span class="mu" style="font-weight:400">저장하면 봇이 최대 15초 안에 적용합니다 (재시작 불필요)</span></h2>
<form id="form" class="s" autocomplete="off"></form>
<div style="margin-top:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
<button class="bt pri" id="save" type="button">저장</button><button class="bt" id="reload" type="button">되돌리기</button>
<button class="bt" id="defaults" type="button">기본값으로 복원</button><span id="msg" class="mu"></span></div>
<h2>봇 로그</h2><pre id="log"></pre>
<h2>매매 내역 (청산)</h2><div class="sc"><table id="sells"></table></div>
<h2>진입 기록 (선정 근거)</h2><div class="sc"><table id="buys"></table></div>
</div><script>
const $=id=>document.getElementById(id),n=(v,d=0)=>Number(v).toLocaleString('ko-KR',{maximumFractionDigits:d,minimumFractionDigits:d});
const cl=v=>v>0?'up':v<0?'dn':'',sg=(v,d=0)=>(v>0?'+':'')+n(v,d);
const tbl=(h,rows)=>'<tr>'+h.map(x=>`<th>${x}</th>`).join('')+'</tr>'+(rows.length?rows.join(''):`<tr><td colspan="${h.length}" class="mu">없음</td></tr>`);
let CFG=null,formBuilt=false,dirty=false;
const SECTIONS=[
['진입 조건',[['mode','진입 방식','',1,'돌파=고점 넘은 뒤 진입, 선행=돌파 직전 징후에 진입'],['vol_ratio','거래량 배수 ≥','배',1,'직전 20봉 중앙값 대비'],['min_atr','최소 변동성(ATR) ≥','%',100,'5분봉 평균 고저폭'],['max_1h','1시간 상승률 ≤','%',100,'과열 종목 제외'],['max_spread','스프레드 ≤','%',100,'호가 간격'],['early_gap','선행: 고점까지 거리 ≤','%',100,'최근 고점 아래 이 범위 안'],['early_vol','선행: 거래량 증가 ≥','배',1,'직전 2봉 평균 / 평소'],['early_ob','선행: 매수/매도 호가잔량 ≥','배',1,'매수세 우위 확인']]],
['종목 추천 풀',[['watch','스캔 종목 수','개',1,'거래대금 상위 N개'],['turnover','최소 24h 거래대금','억원',1e8,''],['exclude','제외 종목 (쉼표로 구분, 예: KRW-BTC,KRW-ETH)','',1,'']]],
['청산 조건',[['stop','손절','%',100,'진입가 대비 하락'],['take','익절','%',100,'진입가 대비 상승'],['part_on','분할 익절 시작 수익','%',100,'이 수익에 닿으면 일부를 팔고 남은 물량은 본전 보호'],['part_ratio','분할 익절 비율','%',100,'0이면 분할 안 함(본전 보호만)'],['be_lock','본전 보호선 (매수가 +)','%',100,'분할 익절 뒤 이 수익 아래로 내려오면 청산 (수수료 보전)'],['reentry','손절 후 반등 재진입 (1=켬, 0=끔)','',1,'손절·본전청산 뒤 진입가를 회복하며 오르면 1회 다시 매수'],['reentry_minutes','재진입 감시 시간','분',1,''],['reentry_reclaim','진입가 회복 폭 ≥','%',100,'진입가 +이 값 이상으로 올라야 재진입'],['reentry_max','하루 최대 재진입','회',1,''],['trail_on','추적손절 시작 수익','%',100,''],['trail_gap','추적손절 간격','%',100,'고점 대비 하락'],['max_minutes','최대 보유 시간 (자동 청산)','분',1,'이 시간이 지나면 질문 없이 자동으로 정리 (호가 지정가 후 시장가)']]],
['주문 방식 (이원화)',[['surge_vol','거래량 폭등 기준 ≥','배',1,'이 이상이면 시장가로 즉시 매수 (그 종목의 매도도 시장가)'],['fast_move','급등·급락 판정: 30초 변동 ≥','%',100,'1초봉 기준. 이 이상 움직이면 시장가 (10초 변동은 절반 기준)'],['fast_accel','급등·급락 판정: 거래대금 가속 ≥','배',1,'최근 10초 / 직전 20초 평균'],['stop_wait','조용한 종목의 손절 호가 대기','초',1,'매도1호가로 먼저 시도, 이 시간 안에 안 팔리면 시장가'],['limit_wait','호가 주문 대기 시간','초',1,'평소: 매수1호가로 매수, 익절·매도 응답은 매도1호가로. 시간 안에 안 사지면 취소, 안 팔리면 시장가로 정리 (0=항상 시장가)']]],
['급등 상시 감시 (하루 종일)',[['surge_mode','사용 (1=켬, 0=끔)','',1,'09시 이벤트와 같은 1초봉·1분봉 조건으로 하루 종일 폭등 종목을 시장가 매수'],['surge_max','하루 최대 매수','회',1,''],['cooldown_win','수익·추적손절 후 재매수 대기','분',1,'손절 후에는 아래 "청산 후 재진입 금지" 시간 적용']]],
['보조 규칙 (무반응 정리 · 추격 필터)',[['dead_on','무반응 정리 사용 (1=켬, 0=끔)','',1,'사자마자 안 오르는 종목을 일찍 정리 (수동 매수는 제외)'],['dead_min','무반응 판정 시간','분',1,'이 시간 안에'],['dead_gain','최소 도달 수익','%',100,'최고 수익이 이만큼도 안 되면 청산'],['chase_mode','추격 필터 (0=끔, 1=그림자·기록만, 2=적용)','',1,'신호 후 가격이 이미 달아났으면 매수 건너뜀. 1이면 막지 않고 기록만'],['chase_gap','추격 판정 폭 (직전 1분봉 종가 대비)','%',100,'이보다 비싸면 추격으로 판단']]],
['09시 이벤트 (미친말 올라타기)',[['event','이벤트 사용 (1=켬, 0=끔)','',1,'08:58 감시 → 09:00 매수'],['ev_count','매수 종목 수','개',1,'상승·거래량 상위 N개'],['ev_ratio','시드머니 사용 비율','%',100,'N종목에 균등 분산'],['ev_window','매수 구간','분',1,'09:00부터 N분 동안만 진입'],['ev_rise','09시 시가 대비 상승 ≥','%',100,'1분봉: 추세 확인'],['ev_max_rise','시가 대비 상승 ≤','%',100,'너무 오른 종목 추격 방지'],['ev_vol','1초봉 거래대금 가속 ≥','배',1,'최근 10초 / 직전 20초 평균'],['ev_fade','1초봉 고점 꺾임 질문','%',100,'30초 고점 대비 하락 시 매도/보유 질문'],['ev_rot','갈아타기 사용 (1=켬, 0=끔)','',1,'더 강한 종목이 나오면 약한 보유 종목을 정리'],['ev_rot_margin','갈아탈 점수 차 ≥','점',1,'수수료·스프레드를 넘길 만큼 클 때만'],['ev_rot_max','구간 내 최대 갈아타기','회',1,''],['ev_rot_gain','이 수익률 이상이면 유지','%',100,'수익 중인 종목은 건드리지 않음']]],
['이벤트 진입 보호',[['ev_rebuy','같은 종목 이벤트 재매수 (1=켬, 0=끔)','',1,'기본 끔. 일반 재진입 설정과 별개이며, 켜도 종목당 1회·순위 3위 이내만 허용'],['ev_rebuy_wait','이벤트 재매수 최소 대기','분',1,'매도 후 대기. 일반 청산 후 재진입 금지보다 짧게 설정해도 더 긴 시간을 적용'],['ev_recheck','매수 직전 조건 재검사 (1=켬, 0=끔)','',1,'09시 이벤트·상시 급등 자동매수에서 상승세·거래대금·호가를 다시 확인'],['ev_signal_age','매수 후보 신호 최대 경과 시간','초',1,'이보다 오래된 후보는 주문하지 않고 다음 스캔에서 다시 판단']]],
['리스크 관리',[['budget','시드머니','원',1,'수익·손실이 반영되어 변동'],['max_pos','최대 동시 보유','종목',1,'종목당 시드÷N'],['daily_loss','일일 손실 한도','원',1,'도달 시 그날 신규 진입 중단'],['btc_drop','BTC 급락 시 진입 중단','%',100,'5분 기준 하락폭'],['cooldown','청산 후 재진입 금지','분',1,'']]]];
function buildForm(){$('form').innerHTML=SECTIONS.map(([t,fs])=>`<fieldset><legend>${t}</legend>`+fs.map(([k,l,u,m,h])=>k=='exclude'?
`<label style="display:block">${l}<textarea id="f_${k}"></textarea></label>`:
`<label><span>${l}${h?`<small>${h}</small>`:''}</span><span>${k=='mode'?'<select id="f_mode"><option value="0">돌파만</option><option value="1">선행만</option><option value="2">둘 다</option></select>':`<input id="f_${k}" type="number" step="any">`} ${u}</span></label>`).join('')+'</fieldset>').join('');
$('form').oninput=e=>{dirty=true;e.target.classList.remove('bad');$('msg').textContent=''};formBuilt=true}
function fillForm(c){if(!formBuilt)buildForm();SECTIONS.forEach(([t,fs])=>fs.forEach(([k,l,u,m])=>{const el=$('f_'+k);
el.value=k=='exclude'?c[k].join(', '):+(c[k]*(m==1e8?1/1e8:m)).toFixed(6)}));dirty=false}
async function post(url,body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});return r.json()}
$('save').onclick=async()=>{const body={};SECTIONS.forEach(([t,fs])=>fs.forEach(([k,l,u,m])=>{const v=$('f_'+k).value;
body[k]=k=='exclude'?v:(v===''?NaN:(m==1e8?v*1e8:v/m))}));
const res=await post('/api/settings',body);if(res.error){$('msg').className='er';$('msg').textContent=res.error;return}
$('msg').className='ok';$('msg').textContent='저장됨 — 봇이 15초 안에 적용합니다';dirty=false;load()};
$('reload').onclick=()=>{fillForm(CFG);$('msg').textContent=''};
$('defaults').onclick=async()=>{if(!confirm('모든 설정을 기본값(.env)으로 되돌립니다. 계속할까요?'))return;await post('/api/settings/reset',{});await load();fillForm(CFG);$('msg').className='ok';$('msg').textContent='기본값으로 복원됨'};
let LAST=null;
function toast(t,bad){const e=$('toast');e.textContent=t;e.className=bad?'er':'ok';e.style.display='block';clearTimeout(toast.t);toast.t=setTimeout(()=>e.style.display='none',4000)}
async function trade(m,s){const sz=LAST?Math.min(LAST.bankroll/LAST.cfg.max_pos,1e12):0;
if(!confirm(s=='sell'?m+' 전량을 시장가로 매도합니다. 계속할까요?':m+' 을(를) 시장가로 약 '+n(sz)+'원 매수합니다. 계속할까요?'))return;
const r=await post('/api/trade',{market:m,side:s});if(r.error)toast(r.error,true);else toast((s=='sell'?'매도':'매수')+' 접수됨 — 봇이 몇 초 안에 실행합니다');load()}
async function decide(m,a){if(a=='sell'&&!confirm(m+' 을(를) 시장가로 매도합니다. 계속할까요?'))return;const r=await post('/api/decide',{market:m,action:a});if(r.error)alert(r.error);load()}
$('scanbtn').onclick=async()=>{$('scanbtn').disabled=true;await post('/api/scan',{});load()};async function load(){try{const d=await (await fetch('/api/data')).json();
$('now').textContent=d.now;$('mp').textContent=d.cfg.max_pos;
const wr=d.trades?d.wins/d.trades*100:0;
const items=[['운용자금',n(d.bankroll)+'원',''],['누적 손익',sg(d.realized)+'원',cl(d.realized)],['오늘 손익',sg(d.day_pnl)+'원',cl(d.day_pnl)],
['거래 횟수',d.trades+'회',''],['승률',d.trades?n(wr,0)+'%':'-',''],['일일 손실 한도',n(d.cfg.daily_loss)+'원','']];
$('sum').innerHTML=items.map(([a,b,c])=>`<div class="c"><span class="mu">${a}</span><b class="${c}">${b}</b></div>`).join('');
$('evbox').innerHTML=d.pending.length?`<h2 class="er">⚠ 매도/보유 결정 필요 (${d.pending.length})</h2><div class="s">`+d.pending.map(p=>{const q=d.queued[p.market];return `<div class="c" style="border-color:var(--up)"><b>${p.market}</b><div class="mu">${esc2(p.why)}${p.age?' · 보유 '+n(p.age,0)+'분':''}</div><div>현재 <span class="${cl(p.pnl_pct)}">${sg(p.pnl_pct,2)}%</span> · 최고 <span class="${cl(p.peak_pct)}">${sg(p.peak_pct,2)}%</span></div><div style="margin-top:8px;display:flex;gap:8px;align-items:center">`+(q?`<span class="ok">${q=='sell'?'매도':'보유'} 접수됨 — 곧 반영</span>`:`<button class="bt pri" onclick="decide('${p.market}','sell')">매도</button><button class="bt" onclick="decide('${p.market}','hold')">보유 (계속, 다시 묻기)</button>`)+`</div></div>`}).join('')+'</div>':(d.event_day==d.now.slice(0,10)?'<div class="mu" style="margin-top:10px">오늘 09시 이벤트 실행됨 · 결정 대기 종목 없음</div>':'');
LAST=d;
const ckCard=(x,tag,buy)=>{const cs=x.checks||[],ok=cs.filter(c=>c.ok).length;return `<div class="c"><div style="display:flex;justify-content:space-between;align-items:center"><b style="font-size:16px;margin:0">${x.market}</b><span class="mu">${tag}</span></div>${x.cooldown?`<div class="er" style="font-size:12px">방금 거래한 종목 · 자동매수 ${n(x.cooldown,0)}분 후 (직접 매수는 가능)</div>`:''}<div class="mu">현재가 ${n(x.price,2)} · 점수 ${n(x.score,1)}${x.surge===undefined?'':' · 주문 '+(x.surge?'시장가(거래량 폭등)':'호가 지정가')}</div><div class="mu" style="margin-top:4px">조건 ${ok}/${cs.length} 충족${x.failed&&x.failed.length?'':' — 진입 신호'}</div><div class="bar"><i style="width:${cs.length?ok/cs.length*100:0}%;background:var(--up)"></i></div>`+cs.map(c=>`<div class="ck"><span class="${c.ok?'ok':'mu'}">${c.ok?'✓':'✗'} ${esc2(c.name)}</span><span class="mu">${esc2(c.text)}</span><div class="bar"><i style="width:${c.prog*100}%;background:${c.ok?'var(--up)':'var(--mu)'}"></i></div></div>`).join('')+(buy?`<div style="margin-top:8px"><button class="bt pri" onclick="trade('${x.market}','buy')">매수</button></div>`:'')+'</div>'};
const top=d.rec.top||[];$('montime').textContent=top.length?'(스캔 '+new Date(d.rec.time*1000).toLocaleTimeString('ko-KR')+' · 이미 보유한 종목 제외)':'';
$('mon').innerHTML=top.length?top.map(x=>ckCard(x,x.kind=='early'?'선행':'돌파',true)).join(''):'<div class="mu">스캔 결과를 기다리는 중…</div>';
$('evmon').innerHTML=d.event&&d.event.top.length?`<h2>09시 이벤트 감시 <span class="mu" style="font-weight:400">(1초봉·1분봉, 3초마다 갱신)</span></h2><div class="s">`+d.event.top.map(x=>ckCard(x,'이벤트 +'+n(x.rise*100,1)+'%',true)).join('')+'</div>':'';
$('pos').innerHTML=tbl(['종목','진입가','현재가','수익률','손익','보유(분)','손절가','익절가','고점대비','주문'],d.positions.map(p=>
`<tr><td>${p.market}${p.event?' <span class="chip g1" style="margin:0">이벤트</span>':''}</td><td>${n(p.entry,2)}</td><td>${n(p.price,2)}</td><td class="${cl(p.pnl_pct)}">${sg(p.pnl_pct,2)}%</td><td class="${cl(p.pnl)}">${sg(p.pnl)}</td><td>${n(p.age_min,0)}/${d.cfg.max_minutes}</td><td>${n(p.stop,2)}</td><td>${n(p.take,2)}</td><td>${sg(p.peak_pct,2)}%</td><td><button class="bt" onclick="trade('${p.market}','sell')">매도</button></td></tr>`));
$('sells').innerHTML=tbl(['시간','종목','사유','손익(원)','누적(원)'],d.sells.map(s=>
`<tr><td>${s.time}</td><td>${s.market}</td><td>${s.reason}</td><td class="${cl(s.net)}">${sg(s.net)}</td><td class="${cl(s.total)}">${sg(s.total)}</td></tr>`));
$('buys').innerHTML=tbl(['시간','종목','점수','15분','1시간','거래량배수','ATR','스프레드','금액'],d.buys.map(b=>
`<tr><td>${b.time}</td><td>${b.market}</td><td>${b.score}</td><td class="${cl(b.r15)}">${sg(b.r15,2)}%</td><td class="${cl(b.r1h)}">${sg(b.r1h,2)}%</td><td>${b.vol}x</td><td>${b.atr}%</td><td>${b.spread}%</td><td>${n(b.size)}</td></tr>`));
CFG=d.cfg;if(!formBuilt||!dirty&&!document.activeElement.closest('#form'))fillForm(CFG);
const r=d.rec,tf=ts=>ts?new Date(ts*1000).toLocaleTimeString('ko-KR'):'-';
$('rectime').textContent=r.running?'(스캔 중…)':'(마지막 스캔 '+tf(r.time)+')'+(r.error?' 오류: '+r.error:'');
$('scanbtn').disabled=r.running;
const rrow=x=>`<td>${x.market}</td><td>${x.kind=='early'?'선행':'돌파'}</td><td>${n(x.price,2)}</td><td>${n(x.score,2)}</td><td class="${cl(x.r15)}">${sg(x.r15*100,2)}%</td><td class="${cl(x.r1h)}">${sg(x.r1h*100,2)}%</td><td>${n(x.volume,1)}x</td><td>${n(x.atr*100,2)}%</td><td>${x.spread==null?'-':n(x.spread*100,3)+'%'}</td>`;
const rh=['종목','방식','현재가','점수','15분','1시간','거래량','ATR','스프레드'];
$('recs').innerHTML=tbl(rh,r.picks.map(x=>'<tr>'+rrow(x)+'</tr>'));
$('near').innerHTML=tbl(rh.concat(['부족한 조건']),r.near.map(x=>'<tr>'+rrow(x)+`<td class="er">${x.failed.join(', ')}</td></tr>`));const lg=$('log'),bt=lg.scrollTop+lg.clientHeight>=lg.scrollHeight-20;lg.textContent=d.log.join('\n');if(bt)lg.scrollTop=lg.scrollHeight;
}catch(e){$('now').textContent='연결 끊김'}}
load();setInterval(load,5000);
async function loadSignal(){try{const s=await (await fetch('/api/signal')).json();if(s.error)return;
const up=s.points>=4,dn=s.points<=2;$('siglab').textContent=s.label;$('siglab').className='lab '+(up?'up':dn?'dn':'');
$('sigdots').innerHTML=Array.from({length:6},(_,i)=>`<i style="${i<s.points?`background:${up?'var(--up)':dn?'var(--dn)':'var(--mu)'}`:''}"></i>`).join('');
$('sigsub').textContent=`상승 신호 ${s.points}/6 (BTC 4개 지표 + 평균등락 + 상승종목>50%)`;
const bp=s.breadth.up/s.breadth.total*100;
$('sigbars').innerHTML=s.metrics.map(m=>{const w=Math.min(Math.abs(m.value)/m.scale,1)*50;return `<div class="dv"><span>${m.name}</span><div class="tr"><i style="${m.value>=0?`left:50%;`:`right:50%;`}width:${w}%;background:${m.value>=0?'var(--up)':'var(--dn)'}"></i></div><b class="${cl(m.value)}">${sg(m.value,2)}%</b></div>`}).join('')
+`<div class="dv"><span>상승/하락 종목</span><div class="st"><i style="width:${bp}%;background:var(--up)"></i><i style="width:${100-bp}%;background:var(--dn)"></i></div><b>${n(bp,0)}%↑</b></div><div class="mu" style="font-size:11px">빨강=상승 · 파랑=하락 · 중앙선=0% · 막대 길이는 지표별 기준폭 대비</div>`;
const h=s.hourly,mx=Math.max(...h.map(x=>Math.abs(x.v)),.3),W=250,H=84,bw=W/h.length;
$('sighr').innerHTML=`<svg viewBox="0 0 ${W} ${H+14}" width="100%" role="img" aria-label="BTC 시간별 등락 막대그래프"><line x1="0" x2="${W}" y1="${H/2}" y2="${H/2}" stroke="var(--line)"/>`+h.map((x,i)=>{const bh=Math.abs(x.v)/mx*(H/2-2);return `<rect x="${i*bw+1}" width="${bw-2}" y="${x.v>=0?H/2-bh:H/2}" height="${Math.max(bh,1)}" fill="${x.v>=0?'var(--up)':'var(--dn)'}"><title>${x.t} ${sg(x.v,2)}%</title></rect>`}).join('')+`<text x="0" y="${H+11}" font-size="9" fill="var(--mu)">25시간 전</text><text x="${W}" y="${H+11}" font-size="9" text-anchor="end" fill="var(--mu)">지금(${h[h.length-1].t}~)</text></svg>`;
$('sigt').textContent='· '+new Date().toLocaleTimeString('ko-KR',{hour12:false})+' 기준';
}catch(e){}}
async function loadAnalog(){try{const a=await (await fetch('/api/analog')).json();
if(a.loading){setTimeout(loadAnalog,5000);return}if(a.error){$('anhead').textContent='계산 실패: '+a.error;return}
const s=a.summary,c=a.cur;$('ant').textContent='· 과거 데이터 ~'+a.hist_end+' (일봉)';
$('anhead').innerHTML=`<div>지금: 상승종목 <b style="display:inline;font-size:14px">${n(c.breadth*100,0)}%</b> · BTC 오늘 ${sg(c.btc1*100,1)}% · 7일 ${sg(c.btc7*100,1)}% → 비슷했던 <b style="display:inline;font-size:14px">${s.n}개 시점</b></div>
<div class="mu" style="margin-top:4px">이후 3일: BTC 평균 <span class="${cl(s.btc3_mean)}">${sg(s.btc3_mean*100,1)}%</span> (상승 ${n(s.btc3_up*100,0)}%) · 알트 평균 <span class="${cl(s.alt3_mean)}">${sg(s.alt3_mean*100,1)}%</span> (상승 종목 ${n(s.alt3_up*100,0)}%의 시점)${a.winners?` · 상위 20% 종목은 1일 ${sg(a.winners.f1*100,1)}%, 3일 ${sg(a.winners.f3*100,1)}% (컷 ${sg(a.winners.cut*100,1)}%)`:''}</div>
${a.timing?`<div class="mu" style="margin-top:4px">상위 종목의 고점 시점: 1일차 ${n(a.timing[0]*100,0)}% · 2일차 ${n(a.timing[1]*100,0)}% · <b style="display:inline;font-size:13px">3일차 ${n(a.timing[2]*100,0)}%</b></div>`:''}`;
$('ankw').innerHTML=a.keywords.map(k=>`<span class="chip ${k.lift>1?'g1':'b1'}" title="상위 종목 ${n(k.win*100,0)}% vs 전체 ${n(k.all*100,0)}%">#${esc2(k.word)} ${k.lift>1?'↑':'↓'}${n(k.lift,2)}배</span>`).join('')||'<span class="mu">뚜렷한 공통 특징 없음</span>';
$('antbl').innerHTML=tbl(['날짜','유사도','BTC 1일','BTC 3일','알트 평균 3일','3일 최고 종목 (3일 수익률 · 특징)'],a.rows.map(r=>`<tr><td>${r.date}</td><td>${n(r.sim,0)}</td><td class="${cl(r.btc1)}">${sg(r.btc1*100,1)}%</td><td class="${cl(r.btc3)}">${sg(r.btc3*100,1)}%</td><td class="${cl(r.alt3)}">${sg(r.alt3*100,1)}%</td><td style="text-align:left;white-space:normal">${r.tops.map(t=>`<b style="display:inline;font-size:12px">${esc2(t.m)}</b> <span class="${cl(t.f3)}">${sg(t.f3*100,0)}%</span><span class="tg2">${t.tags.map(esc2).join('·')}</span>`).join(' &nbsp; ')}</td></tr>`));
$('annote').textContent=`참고용: 표본 ${s.n}개 시점·종목 ${s.pool}건으로 작아 우연일 수 있습니다. 현재 상장 종목만 포함(상폐 제외)되어 실제보다 좋게 나올 수 있고, 같은 패턴이 반복된다는 보장은 없습니다. 키워드 배수는 상위 20% 종목의 비율 ÷ 전체 비율입니다.`;
}catch(e){}}
const esc2=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function loadAccount(){try{
const a=await (await fetch('/api/account')).json();
if(!a.connected){$('accountstatus').textContent=a.error||'연결 확인 실패';$('accountstatus').className='er';$('accountsum').replaceChildren();$('accountholdings').replaceChildren();return}
$('accountstatus').textContent='연결됨 · '+new Date(a.checked_at*1000).toLocaleTimeString('ko-KR');$('accountstatus').className='ok';
$('accountsum').innerHTML=[['주문 가능 원화',n(a.krw_available,2)+'원'],['주문에 묶인 원화',n(a.krw_locked,2)+'원'],['계좌 보유 코인',a.holdings.length+'종목']].map(([label,value])=>`<div class="c"><span class="mu">${label}</span><b>${value}</b></div>`).join('');
$('accountholdings').innerHTML=tbl(['코인','거래 가능 수량','주문에 묶인 수량'],a.holdings.map(h=>`<tr><td>${esc2(h.currency)}</td><td>${n(h.available,8)}</td><td>${n(h.locked,8)}</td></tr>`));
}catch(e){$('accountstatus').textContent='계좌 조회 연결 끊김';$('accountstatus').className='er';$('accountsum').replaceChildren();$('accountholdings').replaceChildren()}}
loadAccount();setInterval(loadAccount,30000);
loadSignal();setInterval(loadSignal,30000);loadAnalog();setInterval(loadAnalog,600000);
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path in ("/morning-analysis", "/bot-review"):
                filename = "overall_assessment.html" if self.path == "/bot-review" else "morning_report.html"
                report = bot.ROOT / "analysis_runs" / "2026-10-26_morning" / filename
                if not report.exists():
                    return self.reply({"error": "분석 보고서가 아직 없습니다."}, 404)
                body, ctype = report.read_bytes(), "text/html"
            elif self.path == "/api/account":
                if self.headers.get("Host", "") not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
                    return self.reply({"error": "forbidden"}, 403)
                body, ctype = json.dumps(account_data(), ensure_ascii=False).encode(), "application/json"
            elif self.path.startswith("/api/data"):
                body, ctype = json.dumps(data(), ensure_ascii=False).encode(), "application/json"
            elif self.path.startswith("/api/signal"):
                body, ctype = json.dumps(market.signal(), ensure_ascii=False).encode(), "application/json"
            elif self.path.startswith("/api/analog"):
                body, ctype = json.dumps(analog.payload(), ensure_ascii=False).encode(), "application/json"
            elif self.path.startswith("/api/market"):
                body, ctype = json.dumps(market.payload(), ensure_ascii=False).encode(), "application/json"
            elif self.path.startswith("/market"):
                body, ctype = market.page().encode(), "text/html"
            else:
                body, ctype = PAGE.encode(), "text/html"
        except Exception as exc:
            body, ctype = json.dumps({"error": str(exc)}).encode(), "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # Only same-origin JSON from this dashboard: blocks other sites from changing settings.
        local = self.headers.get("Host", "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")
        origin = self.headers.get("Origin")
        same = origin in (None, f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")
        if not (local and same and self.headers.get("Content-Type", "").startswith("application/json")):
            return self.reply({"error": "forbidden"}, 403)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/settings":
                save_settings(body)
            elif self.path == "/api/settings/reset":
                bot.SETTINGS.unlink(missing_ok=True)
            elif self.path == "/api/decide":
                market, action = str(body.get("market", "")), body.get("action")
                if action not in ("sell", "hold") or market not in (bot.read_state().get("pending") or {}):
                    raise ValueError("대기 중인 결정이 아닙니다")
                bot.record_decisions({market: action})
            elif self.path == "/api/trade":
                market, side = str(body.get("market", "")), body.get("side")
                if side not in ("buy", "sell") or not re.fullmatch(r"KRW-[A-Z0-9]{1,10}", market):
                    raise ValueError("잘못된 주문입니다")
                if side == "sell" and market not in (bot.read_state().get("positions") or {}):
                    raise ValueError("보유 중인 종목이 아닙니다")
                bot.queue_manual(market, side)
            elif self.path == "/api/scan":
                threading.Thread(target=run_scan, daemon=True).start()
            else:
                return self.reply({"error": "not found"}, 404)
            self.reply({"ok": True})
        except ValueError as exc:
            self.reply({"error": str(exc)})
        except Exception as exc:
            self.reply({"error": f"처리 실패: {exc}"}, 500)

    def reply(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"Dashboard: http://127.0.0.1:{PORT}")
    market.start()
    analog.start()
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
