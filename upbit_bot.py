#!/usr/bin/env python3
"""Upbit KRW intraday momentum bot (5m/15m, multi-position). No live orders unless --live and MAX_BUDGET_KRW are set."""
import argparse
import base64
import datetime
import hashlib
import hmac
import json
import logging
import os
import statistics
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, unquote
from urllib.request import Request, urlopen

BASE = "https://api.upbit.com"
ROOT = Path(__file__).resolve().parent
STATE = ROOT / "bot_state.json"
LOG = ROOT / "bot.log"
MIN_ORDER = 5000
PAPER_DAY = ROOT / "paper_day.json"


def paper_day_active():
    """True on dates listed in paper_day.json ({"dates": ["2026-10-26"]}): virtual-trading day, no real buys."""
    try:
        dates = json.loads(PAPER_DAY.read_text(encoding="utf-8")).get("dates", [])
    except (OSError, ValueError, AttributeError):
        return False
    return datetime.date.today().isoformat() in dates


def load_env():
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip()
                if len(v) > 1 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                os.environ.setdefault(k.strip(), v)


def number(name, default):
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        raise SystemExit(f"{name} must be numeric")


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Upbit:
    def __init__(self):
        self.access = os.getenv("UPBIT_ACCESS_KEY", "").strip()
        self.secret = os.getenv("UPBIT_SECRET_KEY", "").strip()
        if not self.access or not self.secret:
            raise RuntimeError(".env needs UPBIT_ACCESS_KEY and UPBIT_SECRET_KEY")

    def token(self, query=""):
        payload = {"access_key": self.access, "nonce": str(uuid.uuid4())}
        if query:
            payload["query_hash"] = hashlib.sha512(query.encode()).hexdigest()
            payload["query_hash_alg"] = "SHA512"
        head = b64(json.dumps({"alg": "HS512", "typ": "JWT"}, separators=(",", ":")).encode())
        body = b64(json.dumps(payload, separators=(",", ":")).encode())
        msg = f"{head}.{body}".encode()
        sig = hmac.new(self.secret.encode(), msg, hashlib.sha512).digest()
        return f"{head}.{body}.{b64(sig)}"

    def request(self, method, path, params=None):
        params = params or {}
        if method == "POST" and path == "/v1/orders" and params.get("side") == "bid" and paper_day_active():
            raise RuntimeError("paper day: real buy orders are blocked (paper_day.json)")
        encoded = urlencode(params, doseq=True)
        url = BASE + path + (("?" + encoded) if method in ("GET", "DELETE") and encoded else "")
        headers = {"Accept": "application/json", "User-Agent": "local-upbit-momentum-bot/2.0"}
        data = None
        if method == "POST":
            data = json.dumps(params, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        headers["Authorization"] = "Bearer " + self.token(unquote(encoded))
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as exc:
            msg = exc.read().decode(errors="replace")
            raise RuntimeError(f"Upbit HTTP {exc.code}: {msg[:400]}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"Upbit connection failed: {exc}") from exc


def public(path, params=None):
    query = urlencode(params or {}, doseq=True)
    req = Request(BASE + path + (("?" + query) if query else ""),
                  headers={"Accept": "application/json", "User-Agent": "local-upbit-momentum-bot/2.0"})
    for attempt in range(4):
        try:
            with urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as exc:
            if exc.code != 429 or attempt == 3:
                raise
            time.sleep(1 + attempt)


def fnum(value, default=0.0):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def ema(values, period):
    alpha = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def candles(market, unit, count):
    rows = public(f"/v1/candles/minutes/{unit}", {"market": market, "count": count})
    time.sleep(0.11)
    return rows[1:]  # newest first, still-forming candle dropped


def btc_falling(limit):
    try:
        bars = candles("KRW-BTC", 5, 5)
        return price("KRW-BTC") / fnum(bars[1]["trade_price"]) - 1 <= -limit
    except Exception as exc:
        logging.warning("BTC filter failed: %s", exc)
        return False


def book(market):
    """(spread, bid/ask depth ratio) from the order book."""
    ob = public("/v1/orderbook", {"markets": market})[0]
    top = ob["orderbook_units"][0]
    ask, bid = fnum(top["ask_price"]), fnum(top["bid_price"])
    asks = fnum(ob.get("total_ask_size"))
    return ((ask - bid) / ask if ask else 1), (fnum(ob.get("total_bid_size")) / asks if asks else 0)


def chk(name, ok, prog, text):
    """One entry condition for the dashboard: pass/fail plus 0..1 progress toward its threshold."""
    return {"name": name, "ok": bool(ok), "prog": max(0.0, min(1.0, prog)), "text": text}


def scan(cfg, skip=(), diag=None, tick=None):
    """Entry candidates (breakout and/or early-entry per cfg['mode']).
    If diag is a list, every examined market is appended with its failed conditions."""
    markets = public("/v1/market/all", {"is_details": "true"})
    allowed = {m["market"] for m in markets
               if m.get("market", "").startswith("KRW-")
               and m.get("market_warning", "NONE") == "NONE"
               and not (m.get("market_event") or {}).get("warning", False)}
    tickers = public("/v1/ticker/all", {"quote_currencies": "KRW"})
    liquid = [t for t in tickers if t.get("market") in allowed
              and t["market"] not in cfg["exclude"]
              and fnum(t.get("acc_trade_price_24h")) >= cfg["turnover"]]
    liquid.sort(key=lambda t: fnum(t.get("acc_trade_price_24h")), reverse=True)
    mode = int(cfg["mode"])
    picks = []
    for ticker in liquid[:int(cfg["watch"])]:
        market = ticker["market"]
        if tick:
            tick()  # keep watching open positions while this long scan runs
        if market in skip:
            continue
        try:
            bars = candles(market, 5, 40)
            if len(bars) < 30:
                continue
            closes = [fnum(b["trade_price"]) for b in bars]
            last = closes[0]
            r15 = last / closes[3] - 1
            r1h = last / closes[11] - 1
            series = list(reversed(closes[:30]))
            e9, e21 = ema(series, 9), ema(series, 21)
            hi = max(fnum(b["high_price"]) for b in bars[1:21])
            vols = [fnum(b["candle_acc_trade_volume"]) for b in bars]
            med = statistics.median(vols[1:21])
            vol = vols[0] / med if med else 0
            vol2 = (vols[0] + vols[1]) / 2 / med if med else 0
            atr = statistics.mean((fnum(b["high_price"]) - fnum(b["low_price"])) / fnum(b["trade_price"])
                                  for b in bars[:20])
            brk, early = [], []
            if not r15 > 0:
                brk.append("15분 하락")
            if not 0 < r1h <= cfg["max_1h"]:
                brk.append("1시간 상승률 범위")
            if e9 <= e21:
                brk.append("5분 EMA 역배열")
            if last <= hi:
                brk.append("고점 미돌파")
            if vol < cfg["vol_ratio"]:
                brk.append("거래량 부족")
            if atr < cfg["min_atr"]:
                brk.append("변동성 부족")
            if not (last > hi or 0 < (hi - last) / hi <= cfg["early_gap"]):
                early.append("고점과 거리 멂")
            if last <= e21:
                early.append("5분 EMA21 아래")
            if not -0.02 <= r1h <= cfg["max_1h"]:
                early.append("1시간 등락 범위")
            if vol2 < cfg["early_vol"]:
                early.append("거래량 증가 부족")
            if atr < cfg["min_atr"]:
                early.append("변동성 부족")
            if last < fnum(bars[0]["opening_price"]):
                early.append("음봉")
            options = ([("breakout", brk)] if mode in (0, 2) else []) + ([("early", early)] if mode in (1, 2) else [])
            kind, failed = min(options, key=lambda kv: len(kv[1]))
            gap = (hi - last) / hi if hi else 1
            atr_c = chk("변동성", atr >= cfg["min_atr"], atr / cfg["min_atr"] if cfg["min_atr"] else 1,
                        f"{atr*100:.2f}%/{cfg['min_atr']*100:.2f}%")
            if kind == "breakout":
                ema_ok = e9 > e21
                checks = [
                    chk("15분 상승", r15 > 0, 1 if r15 > 0 else 0, f"{r15*100:+.2f}%"),
                    chk("1시간 범위", 0 < r1h <= cfg["max_1h"], 1 if 0 < r1h <= cfg["max_1h"] else 0, f"{r1h*100:+.1f}%"),
                    chk("EMA 정배열", ema_ok, 1 if ema_ok else 1 - (e21 - e9) / e21 / 0.005, "정배열" if ema_ok else "역배열"),
                    chk("고점 돌파", last > hi, 1 - max(gap, 0) / 0.02, f"고점까지 {max(gap, 0)*100:.2f}%"),
                    chk("거래량", vol >= cfg["vol_ratio"], vol / cfg["vol_ratio"], f"{vol:.1f}x/{cfg['vol_ratio']:.1f}x"), atr_c]
            else:
                near_hi = last > hi or 0 < gap <= cfg["early_gap"]
                checks = [
                    chk("고점 근접", near_hi, 1 if near_hi else 1 - (gap - cfg["early_gap"]) / 0.02, f"고점까지 {max(gap, 0)*100:.2f}%"),
                    chk("EMA21 위", last > e21, 1 if last > e21 else 1 - (e21 - last) / e21 / 0.005,
                        f"{(last / e21 - 1)*100:+.2f}%"),
                    chk("1시간 범위", -0.02 <= r1h <= cfg["max_1h"], 1 if -0.02 <= r1h <= cfg["max_1h"] else 0, f"{r1h*100:+.1f}%"),
                    chk("거래량 증가", vol2 >= cfg["early_vol"], vol2 / cfg["early_vol"], f"{vol2:.1f}x/{cfg['early_vol']:.1f}x"),
                    atr_c,
                    chk("양봉", last >= fnum(bars[0]["opening_price"]), 1 if last >= fnum(bars[0]["opening_price"]) else 0, "")]
            sp = ratio = None
            if not failed or (diag is not None and len(failed) <= 2):
                b15 = candles(market, 15, 30)
                c15 = [fnum(b["trade_price"]) for b in b15]
                s15 = list(reversed(c15[:26]))
                if len(c15) < 26:
                    failed.append("15분 데이터 부족")
                elif kind == "breakout" and (c15[0] <= c15[3] or ema(s15, 9) <= ema(s15, 21)):
                    failed.append("15분 추세 불충족")
                elif kind == "early" and c15[0] <= ema(s15, 21):
                    failed.append("15분 EMA21 아래")
                sp, ratio = book(market)
                if sp > cfg["max_spread"]:
                    failed.append("스프레드 과대")
                if kind == "early" and ratio < cfg["early_ob"]:
                    failed.append("매수 호가 우위 부족")
                checks.append(chk("15분 추세", not any(f.startswith("15분") for f in failed),
                                  0 if any(f.startswith("15분") for f in failed) else 1, ""))
                checks.append(chk("스프레드", sp <= cfg["max_spread"], cfg["max_spread"] / sp if sp else 1,
                                  f"{sp*100:.3f}%/{cfg['max_spread']*100:.2f}%"))
                if kind == "early":
                    checks.append(chk("매수호가 우위", ratio >= cfg["early_ob"], ratio / cfg["early_ob"],
                                      f"{ratio:.1f}x/{cfg['early_ob']:.1f}x"))
        except Exception as exc:
            logging.warning("Scan failed for %s: %s", market, exc)
            continue
        score = r15 * 100 * 2 + r1h * 100 + min(vol, 6) * 0.8 + atr * 100 + min(ratio or 0, 3) * 0.5
        row = {"market": market, "score": score, "price": last, "r15": r15, "kind": kind,
               "r1h": r1h, "volume": vol, "atr": atr, "spread": sp, "checks": checks,
               "surge": vol >= cfg["surge_vol"] or cfg["limit_wait"] <= 0}
        if diag is not None:
            diag.append(dict(row, failed=failed))
        if not failed:
            picks.append(row)
    return sorted(picks, key=lambda x: x["score"], reverse=True)

SCAN_FILE = ROOT / "scan_result.json"


def write_scan(diag, picks):
    """Latest scan snapshot for the dashboard (avoids a second client hammering the API)."""
    near = sorted((d for d in diag if d["failed"] and len(d["failed"]) <= 3),
                  key=lambda d: (len(d["failed"]), -d["score"]))[:10]
    top = sorted(diag, key=lambda d: (len(d["failed"]), -d["score"]))[:10]
    tmp = SCAN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"time": time.time(), "picks": picks[:10], "near": near, "top": top}), encoding="utf-8")
    tmp.replace(SCAN_FILE)


EVENT_FILE = ROOT / "event_scan.json"


def write_event_scan(diag):
    top = sorted(diag, key=lambda d: (len(d["failed"]), -d["score"]))[:10]
    tmp = EVENT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"time": time.time(), "top": top}), encoding="utf-8")
    tmp.replace(EVENT_FILE)


def read_state():
    if not STATE.exists():
        return {}
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("Cannot read bot_state.json; inspect it before continuing.") from exc
    if "market" in state:  # legacy single-position format
        state = {"positions": {state["market"]: state}} if state.get("market") else {}
    return state


def save_state(value):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE)


def balances(api):
    return {row["currency"]: row for row in api.request("GET", "/v1/accounts")}


def order_status(api, uid):
    order = {}
    for _ in range(8):
        order = api.request("GET", "/v1/order", {"uuid": uid})
        if order.get("state") in ("done", "cancel"):
            break
        time.sleep(0.8)
    return order


def fills(order):
    """(volume, funds, fee) of an order from its trades."""
    trades = order.get("trades") or []
    vol = sum(fnum(t.get("volume")) for t in trades) or fnum(order.get("executed_volume"))
    funds = sum(fnum(t.get("funds")) for t in trades)
    fee = fnum(order.get("paid_fee"))
    return vol, funds, fee


def price(market):
    rows = public("/v1/ticker", {"markets": market})
    return fnum(rows[0].get("trade_price")) if rows else 0


def today():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def best_quotes(market):
    top = public("/v1/orderbook", {"markets": market})[0]["orderbook_units"][0]
    return fnum(top["bid_price"]), fnum(top["ask_price"])


def passive_order(api, market, side, wait, budget=0.0, volume=0.0):
    """Limit orders on the passive side (buy at the best bid, sell at the best ask) for up to `wait` seconds,
    re-pegging whenever the quote moves. Anything unfilled at the deadline is cancelled.
    side 'bid' spends up to `budget` KRW; side 'ask' sells `volume`. Returns (volume, funds, fee) filled."""
    deadline = time.time() + wait
    got_vol = got_funds = got_fee = 0.0
    while time.time() < deadline:
        bid, ask = best_quotes(market)
        px = bid if side == "bid" else ask
        if side == "bid":
            left = budget - got_funds - got_fee
            if left < MIN_ORDER:
                break
            vol = left / (px * 1.0005) * 0.999  # room for the 0.05% fee
        else:
            vol = volume - got_vol
            if vol * px < MIN_ORDER:
                break
        uid = api.request("POST", "/v1/orders", {
            "market": market, "side": side, "volume": f"{vol:.8f}",
            "price": f"{px:.8f}".rstrip("0").rstrip("."), "ord_type": "limit"})["uuid"]
        order = {}
        while time.time() < deadline:
            time.sleep(1)
            order = api.request("GET", "/v1/order", {"uuid": uid})
            if order.get("state") in ("done", "cancel"):
                break
            nbid, nask = best_quotes(market)
            if (nbid if side == "bid" else nask) != px:
                break  # quote moved away: re-peg
        if order.get("state") != "done":
            try:
                api.request("DELETE", "/v1/order", {"uuid": uid})
            except RuntimeError as exc:
                logging.warning("Cancel %s failed (maybe just filled): %s", uid, exc)
            order = order_status(api, uid)
        v, f, fee = fills(order)
        if v and not f:
            f = v * px
        got_vol, got_funds, got_fee = got_vol + v, got_funds + f, got_fee + fee
        if order.get("state") == "done":
            break
    return got_vol, got_funds, got_fee


def sell(api, state, market, pos, held, reason, px, wait=0, fraction=1.0):
    """wait>0: try the best ask with limit orders first; whatever is left is sold at market (exit is guaranteed).
    fraction<1 sells only that share of the position (partial take profit); the rest stays tracked."""
    total = min(held, fnum(pos.get("volume"), held))
    size0 = fnum(pos.get("cost0")) or fnum(pos.get("cost"))
    volume = total * fraction
    vol = funds = fee = 0.0
    if wait > 0:
        vol, funds, fee = passive_order(api, market, "ask", wait, volume=volume)
    left = volume - vol
    if left > 1e-8 and (wait <= 0 or left * px >= MIN_ORDER):
        result = api.request("POST", "/v1/orders", {
            "market": market, "side": "ask",
            "volume": f"{left:.8f}".rstrip("0").rstrip("."), "ord_type": "market"})
        v2, f2, fee2 = fills(order_status(api, result["uuid"]))
        vol, funds, fee = vol + v2, funds + f2, fee + fee2
    cost = fnum(pos.get("cost")) * (vol / total if total else 1)
    pnl = funds - fee - cost
    state["realized"] = fnum(state.get("realized")) + pnl
    state["day_pnl"] = fnum(state.get("day_pnl")) + pnl
    state.setdefault("cooldown", {})[market] = time.time()
    state.setdefault("cooldown_why", {})[market] = reason
    logging.info("SELL %s (%s) vol=%.8f net=%+.0f KRW | today %+.0f total %+.0f",
                 market, reason, vol, pnl, state["day_pnl"], state["realized"])
    pos["net"] = fnum(pos.get("net")) + pnl
    if total - vol > 1e-8 and (total - vol) * px >= MIN_ORDER:
        pos["volume"] = total - vol
        pos["cost"] = fnum(pos.get("cost")) - cost
    else:
        state["positions"].pop(market, None)
        if "would_skip" in pos:
            record_shadow(market, pos, reason)
        if reason in REENTRY_REASONS and not pos.get("reentry") and not pos.get("part_done"):
            state.setdefault("watch", {})[market] = {"entry": fnum(pos.get("entry_price")), "t": time.time(), "size": size0}


SHADOW_FILE = ROOT / "shadow_trades.jsonl"


def chase_gap(market):
    """How far the live price is above the last completed 1m close (fraction); None when it cannot be read."""
    try:
        last_close = fnum(candles(market, 1, 3)[0]["trade_price"])  # newest first, forming candle dropped
        return price(market) / last_close - 1 if last_close else None
    except Exception as exc:
        logging.warning("Chase gap check failed for %s: %s", market, exc)
        return None


def record_shadow(market, pos, reason):
    """One line per closed auto trade: what the chase filter would have done and what the trade really earned."""
    row = {"day": today(), "t": fnum(pos.get("entry_time")), "market": market, "gap": pos.get("gap"),
           "would_skip": pos.get("would_skip"), "net": round(fnum(pos.get("net"))), "reason": reason}
    with SHADOW_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def shadow_report():
    """Compare trades the chase filter would have skipped with the ones it would have allowed."""
    if not SHADOW_FILE.exists():
        print("아직 기록이 없습니다 (그림자 모드는 자동 진입이 청산될 때마다 기록합니다).")
        return 0
    rows = [json.loads(line) for line in SHADOW_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    days = sorted({r["day"] for r in rows})
    print(f"기록 {len(rows)}건 | 관찰 {len(days)}일째 ({days[0]} ~ {days[-1]}) / 목표 5일")
    for label, flag in (("필터가 건너뛰었을 매수", True), ("필터를 통과한 매수", False)):
        grp = [r for r in rows if r["would_skip"] is flag]
        if grp:
            wins = sum(1 for r in grp if r["net"] > 0)
            print(f"  {label:22s} {len(grp):3d}건  승 {wins}건  합계 {sum(r['net'] for r in grp):+8d}원  평균 {sum(r['net'] for r in grp)/len(grp):+7.0f}원")
        else:
            print(f"  {label:22s}   0건")
    skip_sum = sum(r["net"] for r in rows if r["would_skip"])
    print(f"=> 필터를 실제로 켰다면 {-skip_sum:+d}원 차이 (건너뛴 매수의 손익을 빼면 됩니다)")
    return 0


REENTRY_REASONS = ("stop loss", "event stop loss", "break-even stop", "event break-even stop")
DECISION_FILE = ROOT / "event_decision.json"
EVENT_START, EVENT_BUY = 8 * 60 + 58, 9 * 60  # minutes since midnight (local time)
allowed_cache = {"time": 0, "markets": set()}


def allowed_markets():
    if time.time() - allowed_cache["time"] > 600:
        rows = public("/v1/market/all", {"is_details": "true"})
        allowed_cache.update(time=time.time(), markets={
            m["market"] for m in rows if m.get("market", "").startswith("KRW-")
            and m.get("market_warning", "NONE") == "NONE"
            and not (m.get("market_event") or {}).get("warning", False)})
    return allowed_cache["markets"]


def utc_ts(text):
    return datetime.datetime.fromisoformat(text).replace(tzinfo=datetime.timezone.utc).timestamp()


def sec_stats(market):
    """1-second candle view: recent returns, trading continuity, turnover acceleration and fade from the 30s high.
    Upbit omits seconds with no trades, so None means nothing traded in the last 8s (not 'running')."""
    rows = public("/v1/candles/seconds", {"market": market, "count": 60})
    time.sleep(0.11)
    now = time.time()
    bars = [(utc_ts(r["candle_date_time_utc"]), fnum(r["opening_price"]), fnum(r["high_price"]),
             fnum(r["trade_price"]), fnum(r["candle_acc_trade_price"])) for r in rows]  # newest first
    if not bars or now - bars[0][0] > 8:
        return None

    def px_at(age):
        for bar in bars:
            if bar[0] <= now - age:
                return bar[3]
        return bars[-1][1]

    last = bars[0][3]
    t10 = sum(b[4] for b in bars if b[0] > now - 10)
    prev = sum(b[4] for b in bars if now - 30 < b[0] <= now - 10) / 2
    hi30 = max([b[2] for b in bars if b[0] > now - 30] or [last])
    return {"last": last, "checked_at": now, "r10": last / px_at(10) - 1, "r30": last / px_at(30) - 1,
            "active": sum(1 for b in bars if b[0] > now - 10), "t10": t10,
            # floor on the prior turnover: a dead prior 20s must not turn any print into a "1000000x" surge
            "accel": t10 / max(prev, 1_000_000), "fade": last / hi30 - 1 if hi30 else 0}


def cooling_left(state, cfg, market):
    """Minutes left before auto-buying `market` again: long after a stop loss, short after any other exit."""
    sold = state.get("cooldown", {}).get(market)
    if sold is None:
        return 0
    lost = state.get("cooldown_why", {}).get(market, "stop loss") in ("stop loss", "event stop loss")
    return max(0.0, (cfg["cooldown"] if lost else cfg["cooldown_win"]) - (time.time() - sold) / 60)


def event_rebuy_block(cfg, state, ev, market, rank, now=None):
    """Separate event rebuy policy: off by default, then one rebuy with an explicit wait."""
    if market not in ev.get("bought", []):
        return None
    if not cfg.get("ev_rebuy", 0):
        return "event rebuy disabled"
    if market in ev.get("rebought", []) or rank >= 3:
        return "event rebuy rank/count limit"
    sold_at = fnum(state.get("cooldown", {}).get(market))
    if not sold_at:
        return "event rebuy sale time unavailable"
    now = time.time() if now is None else now
    wait_minutes = max(cfg.get("ev_rebuy_wait", cfg["cooldown"]), cfg["cooldown"])
    if now - sold_at < wait_minutes * 60:
        return "event rebuy cooldown"
    return None


def recheck_event_entry(cfg, pick):
    """Validate a ranked candidate again immediately before submitting any auto event buy.

    Returns a reason to skip; skips are not order failures and do not blacklist a market.
    """
    market = pick["market"]
    started = time.time()
    age_limit = cfg.get("ev_signal_age", 8)
    signal_time = fnum(pick.get("signal_time"))
    if not signal_time or not 0 <= started - signal_time <= age_limit:
        return "event signal expired"
    if not cfg.get("ev_recheck", 1):
        return None
    try:
        ticker = public("/v1/ticker", {"markets": market})[0]
        mins = public("/v1/candles/minutes/1", {"market": market, "count": 1})
        spread, _ = book(market)
        sec = sec_stats(market)
        if not sec:
            return "event no recent trades"
        checked = fnum(sec.get("checked_at"))
        now = time.time()
        if not checked or not 0 <= now - checked <= age_limit or now - started > age_limit:
            return "event recheck expired"
        opening = fnum(ticker.get("opening_price"))
        last = fnum(sec.get("last"))
        if not opening or not last:
            return "event price unavailable"
        rise = last / opening - 1
        if not cfg["ev_rise"] <= rise <= cfg["ev_max_rise"]:
            return "event rise changed"
        if fnum(ticker.get("acc_trade_price")) < cfg["ev_open_krw"] or fnum(ticker.get("acc_trade_price_24h")) < cfg["turnover"] / 5:
            return "event turnover changed"
        if sec["r10"] < 0 or sec["r30"] < .003:
            return "event momentum reversed"
        if sec["active"] < 3 or sec["t10"] < cfg["ev_min_krw"] or sec["accel"] < cfg["ev_vol"]:
            return "event trading strength faded"
        if sec["fade"] <= -cfg["ev_fade"]:
            return "event price faded from peak"
        if not mins or last < fnum(mins[0].get("opening_price")):
            return "event minute turned red"
        if spread > cfg["max_spread"] * 2:
            return "event spread widened"
        pick.update(signal_time=checked, price=last, rise=rise, spread=spread,
                    r10=sec["r10"], r30=sec["r30"], accel=sec["accel"],
                    score=rise * 100 + sec["r30"] * 200 + min(sec["accel"], 10) * .5)
    except Exception:
        logging.warning("Event recheck unavailable for %s; skipping candidate", market)
        return "event recheck unavailable"
    return None


def guarded_event_buy(api, cfg, state, pick, per, tag):
    """True=confirmed order, False=order attempt failed, None=filtered with no order."""
    if paper_day_active():
        logging.info("EVENT ENTRY BLOCK %s: paper day", pick["market"])
        return None
    reason = recheck_event_entry(cfg, pick)
    if reason:
        logging.info("EVENT ENTRY BLOCK %s: %s", pick["market"], reason)
        return None
    logging.info("EVENT ENTRY PASS %s age=%.2fs rise=%+.2f%% r10=%+.2f%% r30=%+.2f%% spread=%.3f%%",
                 pick["market"], time.time() - fnum(pick.get("signal_time")),
                 pick["rise"] * 100, pick["r10"] * 100, pick["r30"] * 100,
                 fnum(pick.get("spread")) * 100)
    return event_buy(api, state, pick["market"], per, tag)


def event_scan(cfg, diag=None, limit=10):
    """Stage 1 (one ticker call): rise since the 09:00 open and turnover since open.
    Stage 2 (1s + 1m candles): is it still accelerating right now, with continuous trading?"""
    allowed = allowed_markets()
    tickers = public("/v1/ticker/all", {"quote_currencies": "KRW"})
    cands = []
    for t in tickers:
        market = t.get("market")
        opening = fnum(t.get("opening_price"))
        if market not in allowed or market in cfg["exclude"] or not opening:
            continue
        rise = fnum(t.get("trade_price")) / opening - 1
        if (cfg["ev_rise"] <= rise <= cfg["ev_max_rise"] and fnum(t.get("acc_trade_price")) >= cfg["ev_open_krw"]
                and fnum(t.get("acc_trade_price_24h")) >= cfg["turnover"] / 5):
            cands.append((market, rise, fnum(t.get("acc_trade_price"))))
    cands.sort(key=lambda c: c[1], reverse=True)
    picks = []
    for market, rise, turn in cands[:limit]:
        failed = []
        try:
            sec = sec_stats(market)
            if sec is None:
                failed.append("최근 8초 체결 없음")
                sec = {"r10": 0, "r30": 0, "active": 0, "t10": 0, "accel": 0, "fade": 0, "last": 0}
            else:
                if sec["r10"] < 0:
                    failed.append("10초 하락")
                if sec["r30"] < 0.003:
                    failed.append("30초 상승 부족")
                if sec["active"] < 3:
                    failed.append("체결 단속적(가짜 급등)")
                if sec["t10"] < cfg["ev_min_krw"]:
                    failed.append("10초 거래대금 부족")
                if sec["accel"] < cfg["ev_vol"]:
                    failed.append("거래대금 가속 부족")
                if sec["fade"] <= -cfg["ev_fade"]:
                    failed.append("30초 고점서 꺾임")
            mins = public("/v1/candles/minutes/1", {"market": market, "count": 5})
            time.sleep(0.11)
            cur = mins[0]
            if fnum(cur["trade_price"]) < fnum(cur["opening_price"]):
                failed.append("1분봉 음봉")
            r3m = None
            if len(mins) >= 4 and utc_ts(mins[0]["candle_date_time_utc"]) - utc_ts(mins[3]["candle_date_time_utc"]) <= 240:
                r3m = fnum(mins[0]["trade_price"]) / fnum(mins[3]["trade_price"]) - 1
            sp = None
            if not failed:
                sp, _ = book(market)
                if sp > cfg["max_spread"] * 2:
                    failed.append("스프레드 과대")
        except Exception as exc:
            logging.warning("Event scan failed for %s: %s", market, exc)
            continue
        checks = [
            chk("시가 대비", True, 1, f"{rise*100:+.1f}%"),
            chk("10초 상승", sec["r10"] >= 0, 1 if sec["r10"] >= 0 else 0, f"{sec['r10']*100:+.2f}%"),
            chk("30초 상승", sec["r30"] >= 0.003, sec["r30"] / 0.003, f"{sec['r30']*100:+.2f}%/+0.30%"),
            chk("연속 체결", sec["active"] >= 3, sec["active"] / 3, f"{sec['active']}/10초"),
            chk("10초 거래대금", sec["t10"] >= cfg["ev_min_krw"], sec["t10"] / cfg["ev_min_krw"],
                f"{sec['t10']/1e6:.1f}/{cfg['ev_min_krw']/1e6:.0f}백만"),
            chk("거래대금 가속", sec["accel"] >= cfg["ev_vol"], sec["accel"] / cfg["ev_vol"],
                f"{sec['accel']:.1f}x/{cfg['ev_vol']:.1f}x"),
            chk("고점 유지", sec["fade"] > -cfg["ev_fade"], 1 if sec["fade"] > -cfg["ev_fade"] else 0,
                f"{sec['fade']*100:+.2f}%"),
            chk("1분봉 양봉", "1분봉 음봉" not in failed, 0 if "1분봉 음봉" in failed else 1, "")]
        row = {"market": market, "rise": rise, "r3m": r3m, "r10": sec["r10"], "r30": sec["r30"],
               "active": sec["active"], "accel": sec["accel"], "spread": sp, "price": sec["last"],
               "score": rise * 100 + sec["r30"] * 100 * 2 + min(sec["accel"], 10) * 0.5, "checks": checks,
               "surge": True, "signal_time": fnum(sec.get("checked_at"))}
        if diag is not None:
            diag.append(dict(row, failed=failed))
        if not failed:
            picks.append(row)
    return sorted(picks, key=lambda x: x["score"], reverse=True)


def event_buy(api, state, market, per, tag, event=True):
    logging.info("EVENT BUY %s %s size=%.0f", market, tag, per)
    try:
        result = api.request("POST", "/v1/orders", {
            "market": market, "side": "bid", "price": f"{per * 0.998:.0f}", "ord_type": "price"})
        vol, funds, fee = fills(order_status(api, result["uuid"]))
    except Exception:
        logging.exception("EVENT buy failed for %s", market)
        return False
    if not vol or not funds:
        logging.error("Could not confirm event buy fill; order UUID %s", result["uuid"])
        return False
    avg = funds / vol
    state["positions"][market] = {
        "volume": vol, "entry_price": avg, "peak_price": avg, "cost": funds + fee,
        "entry_time": time.time(), "order_uuid": result["uuid"], "event": event}
    save_state(state)
    return True


event_log = {"time": 0}


def reentry_cycle(api, cfg, state):
    """After a stop-out, buy the coin back ONCE if it climbs back over the entry price with rising 1s momentum.
    Most stopped coins keep falling and never come back; the ones that do are the fast V-rebounds."""
    watch = state.get("watch")
    if not watch or not cfg["reentry"]:
        return
    rd = state.setdefault("reentry_day", {})
    if rd.get("day") != today():
        rd.clear()
        rd.update(day=today(), n=0)
    now = time.time()
    for market in [m for m, w in watch.items() if now - w["t"] > cfg["reentry_minutes"] * 60 or m in state["positions"]]:
        watch.pop(market, None)
    if (not watch or rd["n"] >= cfg["reentry_max"] or len(state["positions"]) >= int(cfg["max_pos"])
            or fnum(state.get("day_pnl")) <= -cfg["daily_loss"]):
        return
    for market, w in list(watch.items()):
        if now - w["t"] < 20 or price(market) < w["entry"] * (1 + cfg["reentry_reclaim"]):
            continue
        sec = sec_stats(market)
        if not sec or sec["r10"] < 0 or sec["r30"] <= 0 or sec["active"] < 3:
            continue
        bankroll = cfg["budget"] + fnum(state.get("realized"))
        krw = fnum((balances(api).get("KRW") or {}).get("balance"))
        size = min(w["size"], krw * 0.995, bankroll - sum(fnum(p.get("cost")) for p in state["positions"].values()))
        if size < MIN_ORDER:
            continue
        watch.pop(market)
        rd["n"] += 1
        tag = "REENTRY reclaimed %.4g (%+.2f%% 30s)" % (w["entry"], sec["r30"] * 100)
        if event_buy(api, state, market, size, tag, event=False):
            state["positions"][market]["reentry"] = True
        save_state(state)
        return  # one re-entry per cycle


def surge_cycle(api, cfg, state):
    """All-day version of the 09:00 chase: buy the best coin that is surging right now (1s + 1m confirmation).
    Uses the same market-order entry and event exit rules (stop loss only, then ask at the peak)."""
    if not cfg["surge_mode"]:
        return
    sd = state.setdefault("surge", {})
    if sd.get("day") != today():
        sd.clear()
        sd.update(day=today(), n=0)
    if fnum(state.get("day_pnl")) <= -cfg["daily_loss"] or sd["n"] >= cfg["surge_max"]:
        return
    if sum(1 for p in state["positions"].values() if p.get("event")) >= int(cfg["ev_count"]):
        return
    diag = []
    picks = event_scan(cfg, diag, limit=20)
    write_event_scan(diag)
    if time.time() - event_log["time"] >= 30:
        event_log["time"] = time.time()
        near = sorted(diag, key=lambda d: (len(d["failed"]), -d["score"]))[:3]
        logging.info("SURGE NEAR %s", " | ".join(
            f'{d["market"]}({d["rise"]*100:+.1f}%)[{",".join(d["failed"]) or "통과"}]' for d in near) or "none")
    for pick in picks:
        market = pick["market"]
        if market in state["positions"] or cooling_left(state, cfg, market) > 0:
            continue
        krw = fnum((balances(api).get("KRW") or {}).get("balance"))
        bankroll = cfg["budget"] + fnum(state.get("realized"))
        per = min(bankroll, krw) * cfg["ev_ratio"] * 0.995 / int(cfg["ev_count"])
        if per < MIN_ORDER:
            logging.info("SURGE skipped: %.0f KRW per coin is below the minimum.", per)
            return
        tag = "SURGE since-open=%+.2f%% 30s=%+.2f%% 10s=%+.2f%% accel=%.1fx score=%.1f" % (
            pick["rise"] * 100, pick["r30"] * 100, pick["r10"] * 100, pick["accel"], pick["score"])
        if guarded_event_buy(api, cfg, state, pick, per, tag) is True:
            sd["n"] += 1
        save_state(state)
        return  # one buy per cycle


def event_cycle(api, cfg, state):
    """Daily 09:00 'crazy horse' event: 09:00-09:0N chase surging coins, first-come up to ev_count.
    Returns True while the event window is active (normal entries are paused)."""
    if not cfg["event"] or state.get("event_day") == today():
        return False
    now = datetime.datetime.now()
    t = now.hour * 60 + now.minute + now.second / 60
    end = EVENT_BUY + cfg["ev_window"]
    if t >= end:
        if (state.get("event") or {}).get("day") == today():
            state["event_day"] = today()
        return False
    if t < EVENT_START:
        return False
    if t < EVENT_BUY:
        return True  # cash stays free for the event
    ev = state.setdefault("event", {})
    if ev.get("day") != today():
        ev.clear()
        ev.update(day=today(), bought=[], failed=[], per=0)
    if fnum(state.get("day_pnl")) <= -cfg["daily_loss"]:
        logging.warning("EVENT skipped: daily loss limit hit.")
        state["event_day"] = today()
        return True
    if not ev["per"]:
        krw = fnum((balances(api).get("KRW") or {}).get("balance"))
        bankroll = cfg["budget"] + fnum(state.get("realized"))  # seed at event start, incl. realized P&L
        ev["per"] = min(bankroll, krw) * cfg["ev_ratio"] * 0.995 / int(cfg["ev_count"])
        if ev["per"] < MIN_ORDER:
            logging.warning("EVENT skipped: %.0f KRW per coin is below the %d minimum.", ev["per"], MIN_ORDER)
            state["event_day"] = today()
            return True
        logging.info("EVENT start: %.0f KRW per coin x %d", ev["per"], cfg["ev_count"])
    ev.setdefault("sold", [])
    ev.setdefault("rotations", 0)
    rotated_this_cycle = False
    ev.setdefault("rebought", [])
    diag = []
    picks = event_scan(cfg, diag)
    write_event_scan(diag)
    if time.time() - event_log["time"] >= 15:  # keep a trace of why nothing was bought
        event_log["time"] = time.time()
        near = sorted(diag, key=lambda d: (len(d["failed"]), -d["score"]))[:3]
        logging.info("EVENT NEAR %s", " | ".join(
            f'{d["market"]}({d["rise"]*100:+.1f}%)[{",".join(d["failed"]) or "통과"}]' for d in near) or "none")
    for rank, pick in enumerate(picks):  # event_scan is sorted best-first
        market = pick["market"]
        if market in state["positions"] or market in ev["failed"]:
            continue
        reason = event_rebuy_block(cfg, state, ev, market, rank)
        if reason:
            logging.info("EVENT ENTRY BLOCK %s: %s", market, reason)
            continue
        # Rebuy is separate from normal re-entry and must pass its explicit policy above.
        rebuy = market in ev["bought"] and market not in ev["rebought"] and rank < 3
        if market in ev["bought"] and not rebuy:
            continue
        active = sum(1 for p in state["positions"].values() if p.get("event"))
        tag = "since-open=%+.2f%% 30s=%+.2f%% 10s=%+.2f%% accel=%.1fx score=%.1f" % (
            pick["rise"] * 100, pick["r30"] * 100, pick["r10"] * 100, pick["accel"], pick["score"])
        if rebuy:
            tag = "REBUY(rank %d) " % (rank + 1) + tag
        if active < int(cfg["ev_count"]):
            if len(ev["bought"]) >= int(cfg["ev_count"] + cfg["ev_rot_max"]):
                break  # total buys capped so stop-out refills cannot churn forever
            result = guarded_event_buy(api, cfg, state, pick, ev["per"], tag)
            if result is True:
                ev["bought"].append(market)
                if rebuy:
                    ev["rebought"].append(market)
            elif result is False:
                ev["failed"].append(market)
            save_state(state)
        elif cfg["ev_rot"] and ev["rotations"] < cfg["ev_rot_max"] and not rotated_this_cycle:
            # Do not sell a holding to rotate into a candidate already known to be invalid.
            reason = recheck_event_entry(cfg, pick)
            if reason:
                logging.info("EVENT ROTATION BLOCK %s: %s", market, reason)
                continue
            rotated_this_cycle = True  # one swap per cycle keeps API calls and churn bounded
            if event_rotate(api, cfg, state, ev, pick):
                result = guarded_event_buy(api, cfg, state, pick, ev["per"], "ROTATE " + tag)
                if result is True:
                    ev["bought"].append(market)
                    if rebuy:
                        ev["rebought"].append(market)
                elif result is False:
                    ev["failed"].append(market)
                save_state(state)
    return True


def event_rotate(api, cfg, state, ev, pick):
    """Sell the weakest event holding if the newcomer scores clearly higher; True if a slot was freed."""
    weakest = None
    for market, pos in state["positions"].items():
        if not pos.get("event") or time.time() - fnum(pos["entry_time"]) < cfg["ev_hold"]:
            continue
        tick = public("/v1/ticker", {"markets": market})[0]
        px, opening = fnum(tick["trade_price"]), fnum(tick["opening_price"])
        if not px or not opening or px / fnum(pos["entry_price"]) - 1 >= cfg["ev_rot_gain"]:
            continue  # winners are handled by the sell/hold question, not rotated away
        sec = sec_stats(market) or {"r30": 0, "accel": 0}
        score = (px / opening - 1) * 100 + sec["r30"] * 200 + min(sec["accel"], 10) * 0.5
        if weakest is None or score < weakest[0]:
            weakest = (score, market, pos, px)
    if not weakest or pick["score"] - weakest[0] < cfg["ev_rot_margin"]:
        return False
    score, market, pos, px = weakest
    held = fnum((balances(api).get(market.split("-", 1)[1]) or {}).get("balance"))
    if held <= 0:
        return False
    logging.warning("EVENT ROTATE: sell %s (score %.1f) -> buy %s (score %.1f)", market, score, pick["market"], pick["score"])
    sell(api, state, market, pos, held, "event rotate", px)
    state.get("pending", {}).pop(market, None)
    ev["sold"].append(market)
    ev["rotations"] += 1
    save_state(state)
    return market not in state["positions"]


def take_decisions():
    """User answers written by --decide: {market: 'sell'|'hold'}. File is consumed once read."""
    if not DECISION_FILE.exists():
        return {}
    try:
        data = json.loads(DECISION_FILE.read_text(encoding="utf-8"))
        DECISION_FILE.unlink()
        return data
    except Exception:
        logging.warning("event_decision.json unreadable; ignored")
        return {}


def record_decisions(answers):
    """Queue user answers ({market: 'sell'|'hold'}) for the bot loop (used by --decide and the dashboard)."""
    try:
        current = json.loads(DECISION_FILE.read_text(encoding="utf-8")) if DECISION_FILE.exists() else {}
    except Exception:
        current = {}
    current.update(answers)
    tmp = DECISION_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(current), encoding="utf-8")
    tmp.replace(DECISION_FILE)


MANUAL_FILE = ROOT / "manual_orders.json"


def queue_manual(market, side):
    """Dashboard buy/sell button -> queued for the bot loop, which owns all orders and bot_state.json."""
    try:
        current = json.loads(MANUAL_FILE.read_text(encoding="utf-8")) if MANUAL_FILE.exists() else []
    except Exception:
        current = []
    current.append({"market": market, "side": side, "time": time.time()})
    tmp = MANUAL_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(current), encoding="utf-8")
    tmp.replace(MANUAL_FILE)


def process_manual(api, cfg, state):
    if not MANUAL_FILE.exists():
        return
    proc = MANUAL_FILE.with_suffix(".proc")
    try:
        MANUAL_FILE.replace(proc)
        orders = json.loads(proc.read_text(encoding="utf-8"))
        proc.unlink()
    except Exception:
        logging.warning("manual_orders.json unreadable; ignored")
        return
    for order in orders:
        market, side = order.get("market"), order.get("side")
        if time.time() - fnum(order.get("time")) > 120:
            logging.warning("MANUAL %s %s skipped: older than 2 minutes", side, market)
            continue
        try:
            if side == "sell":
                pos = state["positions"].get(market)
                held = fnum((balances(api).get(market.split("-", 1)[1]) or {}).get("balance"))
                if not pos or held <= 0:
                    logging.warning("MANUAL SELL %s skipped: not held", market)
                    continue
                sell(api, state, market, pos, held, "manual sell", price(market))
                state.get("pending", {}).pop(market, None)
            elif side == "buy":
                if market in state["positions"]:
                    logging.warning("MANUAL BUY %s skipped: already held", market)
                    continue
                bankroll = cfg["budget"] + fnum(state.get("realized"))
                free = bankroll - sum(fnum(p.get("cost")) for p in state["positions"].values())
                krw = fnum((balances(api).get("KRW") or {}).get("balance"))
                size = min(bankroll / cfg["max_pos"], free, krw * 0.995)
                if size < MIN_ORDER:
                    logging.warning("MANUAL BUY %s skipped: size %.0f KRW below minimum", market, size)
                    continue
                if event_buy(api, state, market, size, "MANUAL", event=False):
                    state["positions"][market]["manual"] = True
                    logging.info("MANUAL BUY %s done", market)
        except Exception:
            logging.exception("Manual %s %s failed", side, market)
        save_state(state)


def decide():
    """Interactive answers for pending event positions."""
    pending = read_state().get("pending") or {}
    if not pending:
        print("No pending decisions.")
        return 0
    answers = {}
    for market, p in pending.items():
        print(f'{market}: 현재 {p["pnl"]*100:+.2f}% / 최고 {p["peak_pnl"]*100:+.2f}% ({p.get("why", "")})')
        choice = input("  [s]매도  [h]보유  [엔터]나중에 > ").strip().lower()
        if choice in ("s", "h"):
            answers[market] = "sell" if choice == "s" else "hold"
    if answers:
        record_decisions(answers)
        print("Saved; the bot applies it within ~15 seconds.")
    return 0


def recent_high(market, since):
    """Highest 1-second print since `since` (epoch). Catches spikes between two position checks; 0 on failure."""
    try:
        rows = public("/v1/candles/seconds", {"market": market, "count": min(120, max(5, int(time.time() - since) + 3))})
        time.sleep(0.11)
        return max([fnum(r["high_price"]) for r in rows if utc_ts(r["candle_date_time_utc"]) >= since - 1] or [0])
    except Exception as exc:
        logging.warning("Peak check failed for %s: %s", market, exc)
        return 0


def partial_take(api, cfg, state, market, pos, held, px):
    """At +part_on sell part_ratio of the position and arm the break-even stop for the rest."""
    total = min(held, fnum(pos.get("volume"), held))
    sold, rest = total * cfg["part_ratio"] * px, total * (1 - cfg["part_ratio"]) * px
    if cfg["part_ratio"] <= 0 or min(sold, rest) < MIN_ORDER * 1.1:
        pos["part_done"] = True  # too small to split: only the break-even stop applies
        return
    pos.setdefault("cost0", fnum(pos.get("cost")))
    logging.info("PARTIAL take profit %s: selling %.0f%% at %+.2f%%", market, cfg["part_ratio"] * 100,
                 (px / fnum(pos["entry_price"]) - 1) * 100)
    sell(api, state, market, pos, held, "partial take profit", px,
         wait=exit_wait(cfg, market, "take profit"), fraction=cfg["part_ratio"])
    pos["part_done"] = True


def is_fast(cfg, sec):
    """Fast tape (surge or crash) right now, judged from the 1-second candles."""
    return bool(sec) and (abs(sec["r30"]) >= cfg["fast_move"] or abs(sec["r10"]) >= cfg["fast_move"] / 2
                          or (sec["accel"] >= cfg["fast_accel"] and sec["t10"] >= cfg["ev_min_krw"]))


def exit_wait(cfg, market, reason):
    """Seconds to work a limit order at the best ask before falling back to market (0 = market now).
    A fast tape (surge/crash) always gets a market order; a quiet, thin coin is worked patiently."""
    if cfg["limit_wait"] <= 0:
        return 0
    try:
        if is_fast(cfg, sec_stats(market)):
            return 0
    except Exception as exc:
        logging.warning("Exit regime check failed for %s: %s", market, exc)
        return 0
    urgent = reason in ("stop loss", "trailing stop", "event stop loss", "break-even stop", "event break-even stop",
                        "no progress")
    return min(cfg["limit_wait"], cfg["stop_wait"]) if urgent else cfg["limit_wait"]


def manage(api, cfg, state):
    wallet = balances(api)
    take_decisions()  # the sell/hold question flow is retired: just clear any stale answers
    for market, pos in list(state["positions"].items()):
        coin = market.split("-", 1)[1]
        held = fnum((wallet.get(coin) or {}).get("balance"))
        if held <= 0:
            logging.warning("Tracked coin missing; dropping %s", market)
            state["positions"].pop(market)
            continue
        px = price(market)
        # the true high since the last check (1s candles), not only the price at check time
        pos["peak_price"] = max(fnum(pos.get("peak_price")), px,
                                recent_high(market, fnum(pos.get("checked")) or fnum(pos["entry_time"])))
        pos["checked"] = time.time()
        entry = fnum(pos["entry_price"])
        peak = max(fnum(pos.get("peak_price")), px)
        pnl = px / entry - 1 if entry else 0
        age = (time.time() - fnum(pos["entry_time"])) / 60
        reason = None
        be = bool(pos.get("part_done")) or peak >= entry * (1 + cfg["part_on"])  # break-even stop armed
        if be and px <= entry * (1 + cfg["be_lock"]):
            reason = "break-even stop"
        elif pnl <= -cfg["stop"]:
            reason = "stop loss"
        elif pnl >= cfg["take"]:
            reason = "take profit"
        elif peak >= entry * (1 + cfg["trail_on"]) and px <= peak * (1 - cfg["trail_gap"]):
            reason = "trailing stop"
        elif (cfg["dead_on"] and not pos.get("manual") and not pos.get("part_done")
              and age >= cfg["dead_min"] and peak < entry * (1 + cfg["dead_gain"])):
            reason = "no progress"  # never got going: bleeds into the stop loss otherwise
        elif age >= cfg["max_minutes"]:
            reason = "time limit"
        pos["peak_price"] = peak
        if not reason and not pos.get("part_done") and px >= entry * (1 + cfg["part_on"]):
            partial_take(api, cfg, state, market, pos, held, px)
            if market not in state["positions"]:
                continue
        if reason:
            # quiet coin -> work the best ask (short for stops); surge/crash tape -> market order
            sell(api, state, market, pos, held, reason, px, wait=exit_wait(cfg, market, reason))
        elif time.time() - fnum(pos.get("logged")) >= 15:
            pos["logged"] = time.time()
            logging.info("%s held %.0fm; PnL %+.2f%% (peak %+.2f%%)", market, age,
                         pnl * 100, (peak / entry - 1) * 100)
    return wallet


def enter(api, cfg, state):
    if state.get("day") != today():
        state.update(day=today(), day_pnl=0.0)
    if fnum(state.get("day_pnl")) <= -cfg["daily_loss"]:
        logging.warning("Daily loss limit hit (%+.0f KRW); no new entries today.", state["day_pnl"])
        return
    slots = int(cfg["max_pos"]) - len(state["positions"])
    if slots <= 0:
        return
    bankroll = cfg["budget"] + fnum(state.get("realized"))
    free = bankroll - sum(fnum(p.get("cost")) for p in state["positions"].values())
    per_slot = bankroll / cfg["max_pos"]
    budget = min(per_slot, free)
    wallet = balances(api)
    krw = fnum((wallet.get("KRW") or {}).get("balance"))
    budget = min(budget, krw * 0.995)
    if budget < MIN_ORDER:
        logging.info("Budget %.0f KRW below minimum.", budget)
        return
    if btc_falling(cfg["btc_drop"]):
        logging.info("BTC falling; skip entries.")
        return
    now = time.time()
    # recently sold coins stay candidates (shown on the dashboard); the cooldown only delays the auto-buy
    cooling = {m: left for m in state.get("cooldown", {}) if (left := cooling_left(state, cfg, m)) > 0}
    diag = []
    last_tick = [time.time()]

    def tick():
        if state["positions"] and time.time() - last_tick[0] >= 3:
            last_tick[0] = time.time()
            try:
                manage(api, cfg, state)
                save_state(state)
            except Exception:
                logging.exception("Position check during scan failed")

    picks = scan(cfg, set(state["positions"]), diag, tick)
    for row in diag + picks:
        if row["market"] in cooling:
            row["cooldown"] = cooling[row["market"]]
    write_scan(diag, picks)
    near = sorted(diag, key=lambda d: (len(d["failed"]), -d["score"]))[:3]
    logging.info("NEAR %s", " | ".join(f'{d["market"]}[{",".join(d["failed"]) or "통과"}]' for d in near))
    if not picks:
        logging.info("No entry signal.")
        return
    waiting = [p["market"] for p in picks if p["market"] in cooling]
    if waiting:
        logging.info("Signal but cooling down (auto-buy waits): %s", ", ".join(waiting))
    picks = [p for p in picks if p["market"] not in cooling]
    for pick in picks[:slots]:
        budget = min(per_slot, bankroll - sum(fnum(p.get("cost")) for p in state["positions"].values()),
                     krw * 0.995)
        if budget < MIN_ORDER:
            return
        logging.info("BUY %s score=%.2f 15m=%+.2f%% 1h=%+.2f%% vol=%.1fx atr=%.2f%% spread=%.3f%% size=%.0f kind=%s",
                     pick["market"], pick["score"], pick["r15"] * 100, pick["r1h"] * 100,
                     pick["volume"], pick["atr"] * 100, pick["spread"] * 100, budget, pick["kind"])
        # chase filter: has the price already run away from the last completed 1m close? (mode 1 = log only)
        gap = chase_gap(pick["market"]) if cfg["chase_mode"] else None
        would_skip = gap is not None and gap > cfg["chase_gap"]
        if would_skip:
            logging.info("CHASE FILTER %s: live price %+.2f%% above the last 1m close -> %s", pick["market"], gap * 100,
                         "SKIPPED" if cfg["chase_mode"] >= 2 else "shadow only, still buying")
            if cfg["chase_mode"] >= 2:
                continue
        # two order styles: volume surge -> market order now; otherwise passive limit at the best bid
        surge = pick["volume"] >= cfg["surge_vol"] or cfg["limit_wait"] <= 0
        if not surge:  # volume alone is stale by a minute: confirm the live tape from the 1s candles
            try:
                surge = is_fast(cfg, sec_stats(pick["market"]))
            except Exception as exc:
                logging.warning("Entry regime check failed for %s: %s", pick["market"], exc)
        uid = ""
        if surge:
            result = api.request("POST", "/v1/orders", {
                "market": pick["market"], "side": "bid", "price": f"{budget * 0.998:.0f}", "ord_type": "price"})
            uid = result["uuid"]
            vol, funds, fee = fills(order_status(api, uid))
        else:
            vol, funds, fee = passive_order(api, pick["market"], "bid", cfg["limit_wait"], budget=budget * 0.998)
            if not vol or funds < MIN_ORDER:
                logging.info("Passive buy of %s not filled in %.0fs (filled %.0f KRW); skipped",
                             pick["market"], cfg["limit_wait"], funds)
                continue
        if not vol or not funds:
            logging.error("Could not confirm buy fill; order UUID %s", uid)
            continue
        avg = funds / vol
        krw -= funds + fee
        state["positions"][pick["market"]] = {
            "volume": vol, "entry_price": avg, "peak_price": avg, "cost": funds + fee,
            "entry_time": time.time(), "order_uuid": uid, "exec": "market" if surge else "passive"}
        if gap is not None:
            state["positions"][pick["market"]].update(gap=gap, would_skip=would_skip)
        save_state(state)
        logging.info("Bought %s vol=%.8f avg=%.8f cost=%.0f exec=%s", pick["market"], vol, avg, funds + fee,
                     "market" if surge else "passive")


def env_cfg():
    return {"budget": number("MAX_BUDGET_KRW", 0),
           "max_pos": number("MAX_POSITIONS", 3),
           "watch": int(number("WATCH_COUNT", 40)),
           "turnover": number("MIN_TURNOVER_24H_KRW", 1_000_000_000),
           "stop": number("STOP_LOSS_PCT", .018),
           "take": number("TAKE_PROFIT_PCT", .035),
           "trail_on": number("TRAIL_ACTIVATE_PCT", .015),
           "trail_gap": number("TRAIL_GAP_PCT", .01),
           "max_minutes": number("MAX_HOLD_MINUTES", 60),
           "vol_ratio": number("MIN_VOL_RATIO", 2.0),
           "min_atr": number("MIN_ATR_PCT", .0025),
           "max_1h": number("MAX_1H_RETURN", .15),
           "max_spread": number("MAX_SPREAD_PCT", .0015),
           "btc_drop": number("BTC_DROP_PCT", .01),
           "daily_loss": number("DAILY_LOSS_KRW", 150_000),
           "cooldown": number("COOLDOWN_MINUTES", 15),
           "mode": number("ENTRY_MODE", 2), "early_gap": number("EARLY_GAP_PCT", .005),
           "early_vol": number("EARLY_VOL_RATIO", 1.3), "early_ob": number("EARLY_BOOK_RATIO", 1.2),
           "event": number("EVENT_ENABLED", 1), "ev_count": int(number("EVENT_COUNT", 4)),
           "ev_ratio": number("EVENT_CASH_RATIO", .5), "ev_vol": number("EVENT_VOL_RATIO", 2.0),
           "ev_min": number("EVENT_ASK_MIN_GAIN", .03), "ev_gap": number("EVENT_PEAK_PULLBACK", .015),
           "ev_max_minutes": number("EVENT_MAX_MINUTES", 120),
           "ev_rise": number("EVENT_MIN_RISE", .015), "ev_max_rise": number("EVENT_MAX_RISE", .2),
           "ev_fade": number("EVENT_FADE_PCT", .008), "ev_window": number("EVENT_WINDOW_MINUTES", 5),
           "ev_rebuy": number("EVENT_REBUY_ENABLED", 0),
           "ev_rebuy_wait": number("EVENT_REBUY_WAIT_MINUTES", 15),
           "ev_recheck": number("EVENT_ENTRY_RECHECK", 1),
           "ev_signal_age": number("EVENT_SIGNAL_MAX_AGE_SECONDS", 8),
           "ev_rot": number("EVENT_ROTATE", 1), "ev_rot_margin": number("EVENT_ROTATE_MARGIN", 3.0),
           "ev_rot_max": int(number("EVENT_ROTATE_MAX", 3)), "ev_rot_gain": number("EVENT_ROTATE_MAX_GAIN", .01),
           "ev_hold": number("EVENT_MIN_HOLD_SECONDS", 40),
           "surge_mode": number("SURGE_MODE", 1), "surge_max": number("SURGE_MAX_PER_DAY", 8),
           "cooldown_win": number("COOLDOWN_AFTER_PROFIT_MINUTES", 1),
           "part_on": number("PARTIAL_TP_PCT", .04), "part_ratio": number("PARTIAL_TP_RATIO", .7),
           "be_lock": number("BREAKEVEN_LOCK_PCT", .006),
           "reentry": number("REENTRY", 1), "reentry_minutes": number("REENTRY_MINUTES", 30),
           "reentry_reclaim": number("REENTRY_RECLAIM_PCT", .002), "reentry_max": int(number("REENTRY_MAX_PER_DAY", 6)),
           "dead_on": number("DEAD_EXIT", 1), "dead_min": number("DEAD_EXIT_MINUTES", 5),
           "dead_gain": number("DEAD_EXIT_GAIN_PCT", .008),
           "chase_mode": int(number("CHASE_FILTER_MODE", 1)), "chase_gap": number("CHASE_GAP_PCT", .0013),
           "fast_move": number("EXEC_FAST_MOVE_PCT", .01), "fast_accel": number("EXEC_FAST_ACCEL", 3.0),
           "stop_wait": number("EXEC_STOP_WAIT_SECONDS", 10),
           "surge_vol": number("SURGE_VOL_RATIO", 4.0), "limit_wait": number("LIMIT_WAIT_SECONDS", 30),
           "ev_min_krw": number("EVENT_MIN_10S_KRW", 3_000_000), "ev_open_krw": number("EVENT_MIN_OPEN_KRW", 30_000_000),
           "exclude": {"KRW-BTC", "KRW-ETH"} if os.getenv("EXCLUDE_MAJORS", "1") == "1" else set()}


SETTINGS = ROOT / "settings.json"
# editable from the dashboard: key -> (min, max)
FIELDS = {"budget": (5000, 1e9), "max_pos": (1, 10), "watch": (5, 100), "turnover": (0, 1e12),
          "stop": (.002, .3), "take": (.003, 1), "trail_on": (.002, .5), "trail_gap": (.001, .3),
          "max_minutes": (1, 1440), "vol_ratio": (1, 20), "min_atr": (0, .2), "max_1h": (.01, 1),
          "max_spread": (.0005, .05), "btc_drop": (.002, .2), "daily_loss": (0, 1e9), "cooldown": (0, 600),
          "mode": (0, 2), "early_gap": (.001, .03), "early_vol": (.5, 10), "early_ob": (.5, 10),
          "event": (0, 1), "ev_count": (1, 10), "ev_ratio": (.05, 1), "ev_vol": (1, 50),
          "ev_min": (.005, 1), "ev_gap": (.002, .3), "ev_max_minutes": (5, 1440),
          "ev_rise": (.002, .5), "ev_max_rise": (.02, 1), "ev_fade": (.002, .1), "ev_window": (1, 120),
          "ev_rebuy": (0, 1), "ev_rebuy_wait": (0, 600), "ev_recheck": (0, 1), "ev_signal_age": (1, 60),
          "ev_rot": (0, 1), "ev_rot_margin": (.5, 30), "ev_rot_max": (0, 10), "ev_rot_gain": (0, .2),
          "surge_vol": (1.5, 50), "limit_wait": (0, 120),
          "surge_mode": (0, 1), "surge_max": (1, 50), "cooldown_win": (0, 600),
          "fast_move": (.002, .1), "fast_accel": (1.5, 50), "stop_wait": (0, 120),
          "part_on": (.005, .5), "part_ratio": (0, .9), "be_lock": (0, .05),
          "reentry": (0, 1), "reentry_minutes": (1, 240), "reentry_reclaim": (0, .05), "reentry_max": (0, 50),
          "dead_on": (0, 1), "dead_min": (1, 60), "dead_gain": (.001, .05), "chase_mode": (0, 2), "chase_gap": (.0002, .02)}


def build_cfg():
    """.env defaults overridden by settings.json (written by the dashboard, re-read every cycle)."""
    cfg = env_cfg()
    try:
        saved = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
    except Exception:
        logging.warning("settings.json unreadable; using .env defaults")
        return cfg
    for key, (lo, hi) in FIELDS.items():
        value = fnum(saved.get(key), None) if key in saved else None
        if value is not None and lo <= value <= hi:
            cfg[key] = int(value) if key in ("watch", "ev_count", "ev_rot_max", "reentry_max", "chase_mode") else value
    if isinstance(saved.get("exclude"), list):
        cfg["exclude"] = {m.strip().upper() for m in saved["exclude"] if str(m).strip()}
    return cfg


def nap(seconds):
    """Sleep, but wake early when a dashboard click (manual order / decision) is waiting."""
    for _ in range(int(seconds)):
        if MANUAL_FILE.exists() or DECISION_FILE.exists():
            return
        time.sleep(1)


def main():
    load_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(LOG, encoding="utf-8")])
    parser = argparse.ArgumentParser(description="Upbit KRW intraday momentum bot")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan", action="store_true", help="Public market scan only; no orders")
    group.add_argument("--event-scan", action="store_true", help="Show current 09:00 event candidates; no orders")
    group.add_argument("--decide", action="store_true", help="Answer pending event sell/hold questions")
    group.add_argument("--shadow-report", action="store_true", help="Summarize the chase-filter shadow trades")
    group.add_argument("--live", action="store_true", help="Submit real Upbit orders")
    parser.add_argument("--once", action="store_true", help="One live cycle, then exit")
    args = parser.parse_args()
    cfg = build_cfg()
    if args.decide:
        return decide()
    if args.shadow_report:
        return shadow_report()
    if args.event_scan:
        diag = []
        picks = event_scan(cfg, diag)
        for d in diag:
            r3m = "  -  " if d["r3m"] is None else f'{d["r3m"]*100:+.2f}%'
            print(f'{"BUY ✔" if not d["failed"] else "skip "} {d["market"]:12s} 시가대비={d["rise"]*100:+.2f}% 3분={r3m} '
                  f'10초={d["r10"]*100:+.2f}% 30초={d["r30"]*100:+.2f}% 체결초={d["active"]}/10 가속={d["accel"]:.1f}x '
                  + ("" if not d["failed"] else "| " + ", ".join(d["failed"])))
        if not diag:
            print("시가 대비 상승 조건을 넘은 종목이 없습니다.")
        return 0
    if args.scan:
        picks = scan(cfg)
        for p in picks[:10]:
            print(f'{p["market"]:12s} score={p["score"]:.2f} 15m={p["r15"]*100:+.2f}% 1h={p["r1h"]*100:+.2f}% '
                  f'vol={p["volume"]:.1f}x atr={p["atr"]*100:.2f}% spread={p["spread"]*100:.3f}%')
        if not picks:
            print("No entry signal.")
        return 0
    if cfg["budget"] < MIN_ORDER:
        raise SystemExit("Live orders blocked: set MAX_BUDGET_KRW to at least 5000 in .env.")
    api = Upbit()
    logging.warning("LIVE TRADING: seed %.0f KRW, max %d positions, daily loss limit %.0f KRW",
                    cfg["budget"], cfg["max_pos"], cfg["daily_loss"])
    state = read_state()
    state.setdefault("positions", {})
    last_scan = 0
    while True:
        try:
            cfg = build_cfg()
            if paper_day_active():
                # virtual-trading day: no new buys of any kind; still manage and sell what is already held
                if not state.get("paper_noted") == today():
                    state["paper_noted"] = today()
                    logging.warning("PAPER DAY: real buying is off today (see paper_event.py); open positions still managed.")
                if state["positions"]:
                    manage(api, cfg, state)
                save_state(state)
                if args.once:
                    return 0
                nap(2 if state["positions"] else 30)
                continue
            process_manual(api, cfg, state)
            if state["positions"]:
                manage(api, cfg, state)
                save_state(state)
            if event_cycle(api, cfg, state):
                save_state(state)
                if args.once:
                    return 0
                nap(3)
                continue
            surge_cycle(api, cfg, state)
            reentry_cycle(api, cfg, state)
            if time.time() - last_scan >= number("SCAN_INTERVAL_SECONDS", 60):
                last_scan = time.time()
                enter(api, cfg, state)
                save_state(state)
        except KeyboardInterrupt:
            logging.warning("Stopped by user.")
            return 0
        except Exception:
            logging.exception("Cycle failed; retrying next interval.")
        if args.once:
            return 0
        # positions open -> check every ~2s (a spike can pass the stop in seconds); idle -> relax
        nap(2 if state["positions"] else 5 if cfg["surge_mode"] else 15)


if __name__ == "__main__":
    raise SystemExit(main())
