#!/usr/bin/env python3
"""Past-analog finder: days whose market state resembles now, and what happened over the next 1-3 days.
Uses daily candles in history_daily.json (refreshed every 6h). Reference only; not used for orders."""
import json
import statistics as st
import threading
import time
from datetime import datetime, timezone

import upbit_bot as bot

HIST = bot.ROOT / "history_daily.json"
MINV = 5e9  # 50억원/day to count as tradable
SKIP = {"KRW-USDT", "KRW-USDC"}
K = 8  # analog days to show
SPACING = 4  # min days between analogs (avoid one episode counted repeatedly)
REFRESH = 6 * 3600
LOCK = threading.Lock()
CACHE = {"built": None, "payload": None, "time": 0, "building": False}

TRAITS = {
    "낙폭과대 (30일 고점 대비 -30%↓)": lambda f: f["dd30"] <= -.30,
    "7일 급락 (-10%↓)": lambda f: f["r7"] <= -.10,
    "BTC 대비 약세 (7일 -5%p↓)": lambda f: f["rel7"] <= -.05,
    "RSI 과매도 (35↓)": lambda f: f["rsi"] < 35,
    "거래량 급증 (평소 2배↑)": lambda f: f["volx"] >= 2,
    "당일 양봉": lambda f: f["r1"] > 0,
    "고변동 (일 변동폭 10%↑)": lambda f: f["range"] >= .10,
    "종가가 당일 상단 (위치 0.5↑)": lambda f: f["pos"] >= .5,
}


def utc_today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def build():
    raw = json.loads(HIST.read_text(encoding="utf-8"))
    today = utc_today()
    series = {m: {r["d"]: r for r in rows if r["d"] < today}
              for m, rows in raw.items() if rows and m not in SKIP}
    dates = sorted(series["KRW-BTC"])
    A = {m: {k: [s[d][k] if d in s else None for d in dates] for k in "hlcv"} for m, s in series.items()}
    n = len(dates)
    btc = A["KRW-BTC"]["c"]
    vec = {}
    for i in range(31, n):
        live = [m for m in A if A[m]["c"][i] and A[m]["c"][i - 1] and (A[m]["v"][i] or 0) >= MINV]
        if len(live) < 20 or not btc[i] or not btc[i - 1] or not btc[i - 7]:
            continue
        vec[i] = (sum(A[m]["c"][i] > A[m]["c"][i - 1] for m in live) / len(live),
                  btc[i] / btc[i - 1] - 1, btc[i] / btc[i - 7] - 1,
                  btc[i] / (sum(btc[i - 9:i + 1]) / 10) - 1)
    cols = list(zip(*vec.values()))
    sd = [st.pstdev(c) or 1 for c in cols]
    return {"dates": dates, "A": A, "n": n, "vec": vec, "sd": sd}


def feat(H, m, i):
    A = H["A"][m]
    c, h, l, v = A["c"], A["h"], A["l"], A["v"]
    if any(x is None for x in (c[i], c[i - 1], c[i - 3], c[i - 7], c[i - 30], h[i], l[i])):
        return None
    if any(c[j] is None for j in range(i - 14, i + 1)) or not v[i]:
        return None
    vs = [x for x in v[i - 20:i] if x]
    if len(vs) < 10:
        return None
    gains = [max(c[j] - c[j - 1], 0) for j in range(i - 13, i + 1)]
    losses = [max(c[j - 1] - c[j], 0) for j in range(i - 13, i + 1)]
    rsi = 100 - 100 / (1 + (sum(gains) / 14) / (sum(losses) / 14 or 1e-9))
    hh = max(x for x in h[i - 30:i + 1] if x)
    rng = h[i] - l[i]
    b = H["A"]["KRW-BTC"]["c"]
    return {"r1": c[i] / c[i - 1] - 1, "r7": c[i] / c[i - 7] - 1, "dd30": c[i] / hh - 1,
            "volx": v[i] / st.median(vs), "rel7": (c[i] / c[i - 7] - 1) - (b[i] / b[i - 7] - 1),
            "pos": (c[i] - l[i]) / rng if rng else .5, "rsi": rsi, "range": rng / c[i]}


def current(H):
    tickers = bot.public("/v1/ticker/all", {"quote_currencies": "KRW"})
    live = [t for t in tickers if bot.fnum(t["acc_trade_price_24h"]) >= MINV and t["market"] not in SKIP]
    btc = next(t for t in tickers if t["market"] == "KRW-BTC")
    c = H["A"]["KRW-BTC"]["c"]
    px = bot.fnum(btc["trade_price"])
    # history ends yesterday (UTC); "today" is index n, so 7 days ago is n-7
    return (sum(bot.fnum(t["signed_change_rate"]) > 0 for t in live) / len(live),
            bot.fnum(btc["signed_change_rate"]), px / c[H["n"] - 7] - 1,
            px / ((sum(c[H["n"] - 9:H["n"]]) + px) / 10) - 1)


def fwd(H, m, i, k):
    c = H["A"][m]["c"]
    return c[i + k] / c[i] - 1 if c[i] and c[i + k] else None


def compute(H, cur):
    W = (1.5, .6, 1.2, 1.0)
    cand = [(sum(w * ((a - b) / s) ** 2 for w, a, b, s in zip(W, v, cur, H["sd"])) ** .5, i)
            for i, v in H["vec"].items() if i <= H["n"] - 4]
    picked = []
    for dist, i in sorted(cand):
        if all(abs(i - j) >= SPACING for _, j in picked):
            picked.append((dist, i))
        if len(picked) == K:
            break
    rows, pool = [], []
    for dist, i in sorted(picked, key=lambda x: -x[1]):
        coins = []
        for m in H["A"]:
            if m == "KRW-BTC" or not H["A"][m]["c"][i] or (H["A"][m]["v"][i] or 0) < MINV:
                continue
            f1, f3 = fwd(H, m, i, 1), fwd(H, m, i, 3)
            f = feat(H, m, i)
            hs = [H["A"][m]["h"][i + k] for k in (1, 2, 3)]
            if f1 is None or f3 is None or f is None or any(x is None for x in hs):
                continue
            f.update(m=m, f1=f1, f3=f3, best=max(range(3), key=lambda k: hs[k]) + 1)
            coins.append(f)
        pool += coins
        top = sorted(coins, key=lambda r: -r["f3"])[:3]
        rows.append({"date": H["dates"][i], "sim": max(0, 100 - dist * 25),
                     "btc1": fwd(H, "KRW-BTC", i, 1), "btc3": fwd(H, "KRW-BTC", i, 3),
                     "alt3": st.mean(r["f3"] for r in coins) if coins else None,
                     "up5": sum(r["f3"] >= .05 for r in coins) / len(coins) if coins else None,
                     "tops": [{"m": r["m"][4:], "f1": r["f1"], "f3": r["f3"],
                               "tags": [k.split(" (")[0] for k, fn in TRAITS.items() if fn(r)][:3]} for r in top]})
    keywords, timing, win_med = [], None, None
    if len(pool) >= 100:
        pool.sort(key=lambda r: -r["f3"])
        q = len(pool) // 5
        win = pool[:q]
        win_med = {"f1": st.median(r["f1"] for r in win), "f3": st.median(r["f3"] for r in win), "cut": win[-1]["f3"]}
        for name, fn in TRAITS.items():
            a, b = sum(fn(r) for r in win) / len(win), sum(fn(r) for r in pool) / len(pool)
            if b and (a / b >= 1.15 or a / b <= .85):
                keywords.append({"word": name, "win": a, "all": b, "lift": a / b})
        keywords.sort(key=lambda k: -abs(k["lift"] - 1))
        timing = [sum(r["best"] == d for r in win) / len(win) for d in (1, 2, 3)]
    alts = [r["alt3"] for r in rows if r["alt3"] is not None]
    b3 = [r["btc3"] for r in rows if r["btc3"] is not None]
    return {"cur": {"breadth": cur[0], "btc1": cur[1], "btc7": cur[2], "btcma": cur[3]}, "rows": rows,
            "summary": {"n": len(rows), "btc3_mean": st.mean(b3) if b3 else None,
                        "btc3_up": sum(x > 0 for x in b3) / len(b3) if b3 else None,
                        "alt3_mean": st.mean(alts) if alts else None,
                        "alt3_up": sum(x > 0 for x in alts) / len(alts) if alts else None,
                        "pool": len(pool)},
            "keywords": keywords, "timing": timing, "winners": win_med, "hist_end": H["dates"][-1]}


def payload():
    with LOCK:
        if CACHE["payload"] and time.time() - CACHE["time"] < 600:
            return CACHE["payload"]
        if CACHE["building"]:
            return CACHE["payload"] or {"loading": True}
        CACHE["building"] = True
    try:
        if CACHE["built"] is None:
            CACHE["built"] = build()
        out = compute(CACHE["built"], current(CACHE["built"]))
        with LOCK:
            CACHE.update(payload=out, time=time.time())
        return out
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        with LOCK:
            CACHE["building"] = False


def refresh_history():
    raw = json.loads(HIST.read_text(encoding="utf-8"))
    for m in list(raw):
        try:
            rows = bot.public("/v1/candles/days", {"market": m, "count": 8})
            time.sleep(0.25)
        except Exception:
            continue
        have = {r["d"]: r for r in raw[m]}
        for r in rows:
            have[r["candle_date_time_utc"][:10]] = {
                "d": r["candle_date_time_utc"][:10], "o": r["opening_price"], "h": r["high_price"],
                "l": r["low_price"], "c": r["trade_price"], "v": r["candle_acc_trade_price"]}
        raw[m] = [have[d] for d in sorted(have)]
    tmp = HIST.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw), encoding="utf-8")
    tmp.replace(HIST)
    with LOCK:
        CACHE.update(built=None, payload=None)


def loop():
    while True:
        try:
            if time.time() - HIST.stat().st_mtime > REFRESH:
                refresh_history()
        except Exception as exc:
            print("history refresh failed:", exc)
        time.sleep(1800)


def start():
    threading.Thread(target=loop, daemon=True).start()
