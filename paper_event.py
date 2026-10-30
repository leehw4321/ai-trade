"""Virtual (paper) trading for the 09:00 morning scenario. Public API only: this script never places orders.

Runs three portfolios side by side on live data, from 08:49 to 10:00 local time, so they can be compared:
  S1  fixed: buy the top 4 by 10-minute momentum at 09:00, hold, sell at 10:00 (no stop)
  S4  auto : enter when a coin confirms strength after 09:00 (entry window to 09:30), stop -2%, trail 1% after +3%
  S6  auto : same as S4 with the entry window open until 10:00
Fills use the real best ask (buy) / best bid (sell) at the moment of decision, plus the 0.05% fee.

  python paper_event.py                 # tomorrow's (or today's, if before 10:00) 09:00 session
  python paper_event.py --report 2026-10-26
  python paper_event.py --test-minutes 6 --loose   # smoke test that starts in 2 minutes
"""
import argparse
import datetime
import json
import logging
import time
from pathlib import Path

import upbit_bot as bot

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "paper_event"
FEE, SLOT, MAXPOS = 0.0005, 250_000, 3
POLL_SECONDS = 5


class Port:
    def __init__(self, name, mode, win_end=0, stop=None, trail=None):
        self.name, self.mode, self.win_end, self.stop, self.trail = name, mode, win_end, stop, trail
        self.pos, self.trades = {}, []


def fill_range(got, lo, hi):
    """bars for minute indexes lo..hi; a minute with no trades repeats the previous close with zero turnover."""
    out, prev = [], None
    for i in range(lo, hi + 1):
        b = got.get(i)
        if b is None:
            out.append(None if prev is None else [prev, prev, prev, prev, 0.0])
        else:
            out.append(b)
            prev = b[3]
    return out


class Session:
    def __init__(self, t0, minutes, loose):
        self.t0, self.t_exit = t0, t0 + minutes * 60
        self.minutes, self.loose = minutes, loose
        win4 = min(30, minutes - 1)
        win6 = minutes - 1
        self.ports = [Port("S1 고정진입", "fixed"),
                      Port("S4 자율진입(~09:30)", "auto", win4, 0.02, (0.03, 0.01)),
                      Port("S6 자율진입(~10:00)", "auto", win6, 0.02, (0.03, 0.01))]
        self.universe, self.last_poll = [], 0.0
        day = datetime.datetime.fromtimestamp(t0).strftime("%Y-%m-%d")
        OUT.mkdir(exist_ok=True)
        self.events = OUT / f"{day}_events.jsonl"
        self.report_file = OUT / f"{day}_report.json"

    # ---- plumbing
    def ev(self, kind, **kw):
        with self.events.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(kind=kind, t=time.strftime("%H:%M:%S"), **kw), ensure_ascii=False) + "\n")

    def series(self, market, count):
        rows = bot.public("/v1/candles/minutes/1", {"market": market, "count": count})
        time.sleep(0.11)
        now, got = time.time(), {}
        for r in rows:
            st = bot.utc_ts(r["candle_date_time_utc"])
            if st + 60 > now - 1:
                continue  # still forming
            got[int(round((st - self.t0) / 60))] = [bot.fnum(r["opening_price"]), bot.fnum(r["high_price"]),
                                                    bot.fnum(r["low_price"]), bot.fnum(r["trade_price"]),
                                                    bot.fnum(r["candle_acc_trade_price"])]
        return got

    def quotes(self, markets):
        out = {}
        for i in range(0, len(markets), 20):
            for r in bot.public("/v1/orderbook", {"markets": ",".join(markets[i:i + 20])}):
                u = r["orderbook_units"][0]
                out[r["market"]] = (bot.fnum(u["bid_price"]), bot.fnum(u["ask_price"]))
        return out

    # ---- universe (08:49:15): traded in at least 8 of the last 10 closed minutes, >= 100M KRW turnover
    def build_universe(self):
        uni = []
        for n, m in enumerate(sorted(bot.allowed_markets())):
            try:
                got = self.series(m, 14)
            except Exception as exc:
                logging.warning("universe fetch failed %s: %s", m, exc)
                continue
            w = [got[i] for i in range(-11, -1) if i in got]
            if len(w) >= 8 and sum(b[4] for b in w) >= 1e8:
                uni.append(m)
            if n % 15 == 0:
                self.tick()
        self.universe = uni
        logging.info("universe: %d markets", len(uni))
        self.ev("universe", n=len(uni))

    # ---- positions: poll bid, stop / trail / time exit
    def tick(self):
        now = time.time()
        if now - self.last_poll < POLL_SECONDS:
            return
        self.last_poll = now
        held = sorted({m for p in self.ports for m in p.pos})
        if not held:
            return
        try:
            q = self.quotes(held)
        except Exception as exc:
            logging.warning("quote poll failed: %s", exc)
            return
        for p in self.ports:
            for m in list(p.pos):
                if m not in q or not q[m][0]:
                    continue
                bid = q[m][0]
                pos = p.pos[m]
                pos["peak"] = max(pos["peak"], bid)
                why = None
                if now >= self.t_exit:
                    why = "time"
                elif p.stop and bid <= pos["entry"] * (1 - p.stop):
                    why = "stop"
                elif p.trail and pos["peak"] >= pos["entry"] * (1 + p.trail[0]) and bid <= pos["peak"] * (1 - p.trail[1]):
                    why = "trail"
                if why:
                    self.close(p, m, bid, why)

    def close(self, p, m, bid, why):
        pos = p.pos.pop(m)
        net = SLOT * ((bid * (1 - FEE)) / (pos["entry"] * (1 + FEE)) - 1)
        row = {"market": m, "in": pos["t"], "out": time.strftime("%H:%M:%S"), "entry": pos["entry"], "exit": bid,
               "gross_pct": round((bid / pos["entry"] - 1) * 100, 2), "net_krw": round(net), "why": why,
               "peak_pct": round((pos["peak"] / pos["entry"] - 1) * 100, 2), "spread_in_pct": pos["spread"]}
        p.trades.append(row)
        logging.info("[%s] SELL %s %s gross %+.2f%% net %+d", p.name, m, why, row["gross_pct"], round(net))
        self.ev("sell", port=p.name, **row)
        self.write_report()

    def open_pos(self, p, m, ask, bid, score, extra):
        spread = round((ask - bid) / ask * 100, 3) if ask else None
        p.pos[m] = {"entry": ask, "peak": bid or ask, "t": time.strftime("%H:%M:%S"), "spread": spread}
        try:
            sec = bot.sec_stats(m)  # 1-second view, recorded only (not used as a filter) for later evaluation
        except Exception:
            sec = None
        logging.info("[%s] BUY %s ask=%s spread=%s%% score=%.2f", p.name, m, ask, spread, score)
        self.ev("buy", port=p.name, market=m, ask=ask, bid=bid, spread_pct=spread, score=round(score, 2), sec=sec, **extra)

    # ---- decisions at each minute boundary k (candles idx <= k-1 are closed)
    def decide(self, k):
        want_fixed = k == 0 and time.time() <= self.t0 + 90
        want_auto = any(p.mode == "auto" and k <= p.win_end and len(p.pos) < MAXPOS for p in self.ports) and k >= 2
        if not (want_fixed or want_auto):
            return
        data = {}
        for n, m in enumerate(self.universe):
            try:
                data[m] = self.series(m, min(200, k + 14))
            except Exception as exc:
                logging.warning("fetch failed %s: %s", m, exc)
            if n % 12 == 0:
                self.tick()
        if want_fixed:
            self.enter_fixed(data)
        for p in self.ports:
            if p.mode == "auto" and 2 <= k <= p.win_end and len(p.pos) < MAXPOS:
                self.enter_auto(p, data, k)

    def enter_fixed(self, data):
        p = self.ports[0]
        rows = []
        for m, got in data.items():
            w = fill_range(got, -10, -1)
            if any(b is None for b in w):
                continue
            r = w[-1][3] / w[0][0] - 1
            if not 0 < r <= 0.30:
                continue
            recent = sum(b[4] for b in w[-3:]) / 3
            prev = sum(b[4] for b in w[:-3]) / 7
            acc = recent / max(prev, 1_000_000)
            rows.append((r * 100 + 0.5 * min(acc, 10), m, r, acc))
        rows.sort(reverse=True)
        top = rows[:4]
        self.ev("rank_0900", top=[(m, round(r * 100, 2), round(a, 1)) for s, m, r, a in top], n_candidates=len(rows))
        if not top:
            return
        q = self.quotes([m for s, m, r, a in top])
        for s, m, r, a in top:
            if m in q and q[m][1]:
                self.open_pos(p, m, q[m][1], q[m][0], s, {"rise10m_pct": round(r * 100, 2), "accel": round(a, 1)})

    def enter_auto(self, p, data, k):
        rmin, amin, brk = (0.001, 0.0, False) if self.loose else (0.015, 1.5, True)
        cands = []
        for m, got in data.items():
            if m in p.pos:
                continue
            full = fill_range(got, -10, k - 1)
            if any(b is None for b in full[10 + k - 6:10 + k]):
                continue
            w = full[10:]
            if len(w) < 2 or w[0] is None:
                continue
            last = w[-1]
            rise = last[3] / w[0][0] - 1
            if not rmin <= rise <= 0.15 or last[3] < last[0]:
                continue
            recent = sum(b[4] for b in w[-3:]) / 3
            prev = sum(b[4] for b in full[10 + k - 8:10 + k - 3]) / 5
            acc = recent / max(prev, 1_000_000)
            if acc < amin:
                continue
            if brk and last[3] < max(b[1] for b in w[:-1]):
                continue
            cands.append((rise * 100 + 0.5 * min(acc, 10), m, rise, acc))
        cands.sort(reverse=True)
        need = MAXPOS - len(p.pos)
        pick = cands[:need]
        if cands:
            self.ev("signal", port=p.name, k=k, top=[(m, round(r * 100, 2), round(a, 1)) for s, m, r, a in cands[:5]])
        if not pick:
            return
        q = self.quotes([m for s, m, r, a in pick])
        for s, m, r, a in pick:
            if m in q and q[m][1]:
                self.open_pos(p, m, q[m][1], q[m][0], s, {"rise_pct": round(r * 100, 2), "accel": round(a, 1), "k": k})

    # ---- report
    def summarize(self):
        out = {}
        for p in self.ports:
            t = p.trades
            out[p.name] = {"trades": len(t), "wins": sum(1 for x in t if x["net_krw"] > 0),
                           "net_krw": sum(x["net_krw"] for x in t), "open": list(p.pos), "list": t}
        return out

    def write_report(self):
        self.report_file.write_text(json.dumps(self.summarize(), ensure_ascii=False, indent=1), encoding="utf-8")

    def run(self):
        logging.info("session: t0=%s exit=%s", time.strftime("%H:%M:%S", time.localtime(self.t0)),
                     time.strftime("%H:%M:%S", time.localtime(self.t_exit)))
        while time.time() < self.t0 - 45:
            time.sleep(1)
        self.build_universe()
        last_k = -1
        while time.time() < self.t_exit + 8:
            self.tick()
            now = time.time()
            k = int((now - self.t0) // 60)
            if 0 <= k < self.minutes and k > last_k and now - (self.t0 + k * 60) >= 2:
                last_k = k
                try:
                    self.decide(k)
                except Exception:
                    logging.exception("decide(%d) failed", k)
            time.sleep(1)
        for p in self.ports:
            for m in list(p.pos):
                try:
                    bid = self.quotes([m])[m][0]
                except Exception:
                    bid = p.pos[m]["peak"]
                self.close(p, m, bid, "time")
        self.write_report()
        print_report(self.summarize())


def print_report(rep):
    print("\n===== 가상거래 결과 (25만원/종목, 수수료 0.05%, 실제 호가 체결) =====")
    for name, r in rep.items():
        n = r["trades"]
        print(f"{name}: 거래 {n}건, 승 {r['wins']}, 순손익 {r['net_krw']:+,}원"
              + (f", 미청산 {r['open']}" if r["open"] else ""))
        for t in r["list"]:
            print(f"   {t['market']:<11} {t['in']}→{t['out']} 진입 {t['entry']:g} 청산 {t['exit']:g} "
                  f"{t['gross_pct']:+.2f}% 순 {t['net_krw']:+,}원 ({t['why']}, 최고 {t['peak_pct']:+.2f}%, 스프레드 {t['spread_in_pct']}%)")


def main():
    ap = argparse.ArgumentParser(description="09:00 paper trading (no orders)")
    ap.add_argument("--date", help="YYYY-MM-DD (default: next 09:00)")
    ap.add_argument("--report", metavar="DATE", help="print the saved report of DATE and exit")
    ap.add_argument("--test-minutes", type=int, help="smoke test: session starts in ~2 min and lasts N minutes")
    ap.add_argument("--loose", action="store_true", help="test only: relax entry filters so trades occur")
    args = ap.parse_args()
    if args.report:
        print_report(json.loads((OUT / f"{args.report}_report.json").read_text(encoding="utf-8")))
        return 0
    OUT.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(OUT / "paper.log", encoding="utf-8")])
    if args.test_minutes:
        t0 = (int(time.time() // 60) + 3) * 60
        minutes = args.test_minutes
    else:
        if args.date:
            day = datetime.date.fromisoformat(args.date)
        else:
            now = datetime.datetime.now()
            day = now.date() if now.time() < datetime.time(9, 55) else now.date() + datetime.timedelta(days=1)
        t0 = datetime.datetime.combine(day, datetime.time(9, 0)).timestamp()
        minutes = 60
    if time.time() > t0 + minutes * 60:
        raise SystemExit("That session is already over.")
    Session(t0, minutes, args.loose).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
