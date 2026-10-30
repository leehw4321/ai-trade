#!/usr/bin/env python3
"""Market report for the dashboard: Upbit prices + news headlines, refreshed every 20 minutes.
Qualitative analysis (analysis.json) is written separately by Claude's scheduled search."""
import json
import threading
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from urllib.request import Request, urlopen

import upbit_bot as bot

REFRESH = 1200
ANALYSIS = bot.ROOT / "analysis.json"
MAJORS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE", "KRW-ADA", "KRW-SUI", "KRW-LINK"]
QUERIES = [("비트코인", "ko"), ("가상자산 규제", "ko"), ("미국 금리 연준 증시", "ko"),
           ("이더리움 ETF", "ko"), ("bitcoin", "en"), ("crypto SEC regulation", "en"),
           ("crypto hack exploit", "en"), ("Fed rate CPI", "en")]
GOOD = ["급등", "상승", "신고가", "승인", "etf", "매수", "유입", "인하", "반등", "rally", "surge", "approve",
        "inflow", "record", "bull", "cut"]
BAD = ["급락", "하락", "폭락", "해킹", "규제", "청산", "소송", "금지", "인상", "유출", "crash", "plunge", "hack",
       "exploit", "ban", "lawsuit", "liquidat", "outflow", "bear", "hike", "sell-off", "selloff"]
EVENTS = ["fomc", "cpi", "연준", "금리", "etf", "sec", "해킹", "hack", "상장", "상폐", "상장폐지", "고용", "관세"]

KEYWORDS = {"ETF": ["etf"], "연준·금리": ["fed", "fomc", "연준", "금리", "rate cut", "rate hike"],
            "청산": ["liquidat", "청산"], "규제·법안": ["sec ", "regulat", "규제", "법안", "clarity", "클래리티"],
            "해킹": ["hack", "exploit", "해킹"], "스테이블코인": ["stablecoin", "스테이블"],
            "고래": ["whale", "고래"], "증시·유가": ["oil", "유가", "stocks", "증시", "nasdaq", "나스닥"],
            "옵션만기": ["expiry", "만기"], "XRP": ["xrp", "리플"], "이더리움": ["ethereum", "이더리움"],
            "솔라나": ["solana", "솔라나"], "도지코인": ["doge", "도지"], "상장": ["listing", "상장"]}

CACHE = {"time": 0, "data": None, "running": False}
LOCK = threading.Lock()


def get(url, timeout=10):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 market-report/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def news():
    seen, items = set(), []
    for q, lang in QUERIES:
        loc = "hl=ko&gl=KR&ceid=KR:ko" if lang == "ko" else "hl=en-US&gl=US&ceid=US:en"
        try:
            root = ET.fromstring(get(f"https://news.google.com/rss/search?q={quote(q)}+when:1d&{loc}"))
        except Exception as exc:
            print("news fetch failed:", q, exc)
            continue
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            key = title.lower()[:60]
            link = it.findtext("link") or ""
            if not title or key in seen or not link.startswith("http"):
                continue
            seen.add(key)
            try:
                ts = parsedate_to_datetime(it.findtext("pubDate")).timestamp()
            except Exception:
                ts = 0
            low = title.lower()
            items.append({"title": title, "link": link, "ts": ts, "lang": lang,
                          "source": (it.findtext("source") or "").strip(),
                          "good": sum(w in low for w in GOOD), "bad": sum(w in low for w in BAD),
                          "events": [w for w in EVENTS if w in low]})
    items.sort(key=lambda x: x["ts"], reverse=True)
    for it in items:
        it["tone"] = "호재" if it["good"] > it["bad"] else "악재" if it["bad"] > it["good"] else "중립"
    return items[:40]


def keywords(items):
    out = []
    for word, keys in KEYWORDS.items():
        hit = [it for it in items if any(k in it["title"].lower() for k in keys)]
        if hit:
            net = sum(it["good"] - it["bad"] for it in hit)
            out.append({"word": word, "count": len(hit), "tone": "호재" if net > 0 else "악재" if net < 0 else "중립"})
    return sorted(out, key=lambda k: -k["count"])[:10]


def pct(a, b):
    return (a / b - 1) * 100 if b else 0


def coin(t):
    market = t["market"]
    row = {"market": market, "price": bot.fnum(t["trade_price"]), "d24": bot.fnum(t["signed_change_rate"]) * 100,
           "turnover": bot.fnum(t["acc_trade_price_24h"])}
    try:
        bars = bot.public("/v1/candles/minutes/15", {"market": market, "count": 20})
        time.sleep(0.11)
        row["h1"] = pct(row["price"], bot.fnum(bars[4]["trade_price"]))
        row["h4"] = pct(row["price"], bot.fnum(bars[16]["trade_price"]))
    except Exception:
        row["h1"] = row["h4"] = None
    return row


def trend(c):
    score = sum(1 if (c.get(k) or 0) > 0 else -1 for k in ("h1", "h4", "d24"))
    return "상승 우위" if score >= 2 else "하락 우위" if score <= -2 else "혼조"


def collect():
    tickers = bot.public("/v1/ticker/all", {"quote_currencies": "KRW"})
    by = {t["market"]: t for t in tickers}
    liquid = [t for t in tickers if bot.fnum(t["acc_trade_price_24h"]) >= 1e9]
    up = sum(bot.fnum(t["signed_change_rate"]) > 0 for t in liquid)
    chg = sorted(liquid, key=lambda t: bot.fnum(t["signed_change_rate"]))
    big = [t for t in chg if bot.fnum(t["acc_trade_price_24h"]) >= 5e9]
    majors = [coin(by[m]) for m in MAJORS if m in by]
    btc = next((c for c in majors if c["market"] == "KRW-BTC"), {})
    week = None
    try:
        days = bot.public("/v1/candles/days", {"market": "KRW-BTC", "count": 8})
        week = {"d7": pct(btc["price"], bot.fnum(days[7]["trade_price"])),
                "high": max(bot.fnum(d["high_price"]) for d in days[:7]),
                "low": min(bot.fnum(d["low_price"]) for d in days[:7])}
    except Exception:
        pass
    fng = None
    try:
        j = json.loads(get("https://api.alternative.me/fng/?limit=1"))["data"][0]
        fng = {"value": int(j["value"]), "label": j["value_classification"]}
    except Exception:
        pass
    mover = lambda t: {"market": t["market"], "d24": bot.fnum(t["signed_change_rate"]) * 100,
                       "price": bot.fnum(t["trade_price"]), "turnover": bot.fnum(t["acc_trade_price_24h"])}
    return {"time": time.time(), "majors": majors, "btc_trend": trend(btc) if btc else "-", "week": week, "fng": fng,
            "breadth": {"up": up, "total": len(liquid), "avg": sum(bot.fnum(t["signed_change_rate"]) for t in liquid)
                        / max(len(liquid), 1) * 100},
            "gainers": [mover(t) for t in reversed(big[-5:])], "losers": [mover(t) for t in big[:5]],
            "news": (items := news()), "keywords": keywords(items)}


def refresh():
    with LOCK:
        if CACHE["running"]:
            return
        CACHE["running"] = True
    try:
        data = collect()
        with LOCK:
            CACHE.update(time=time.time(), data=data)
    except Exception as exc:
        print("market refresh failed:", exc)
    finally:
        with LOCK:
            CACHE["running"] = False


def loop():
    while True:
        refresh()
        time.sleep(REFRESH)


def payload():
    try:
        analysis = json.loads(ANALYSIS.read_text(encoding="utf-8"))
    except Exception:
        analysis = None
    with LOCK:
        return {"data": CACHE["data"], "running": CACHE["running"], "analysis": analysis,
                "refresh_sec": REFRESH, "now": time.time()}


def start():
    threading.Thread(target=loop, daemon=True).start()


SIG = {"time": 0, "data": None}


def signal():
    """Current regime from live data: BTC returns, breadth, hourly BTC bars. Cached 45s."""
    if SIG["data"] and time.time() - SIG["time"] < 45:
        return SIG["data"]
    tickers = bot.public("/v1/ticker/all", {"quote_currencies": "KRW"})
    live = [t for t in tickers if bot.fnum(t["acc_trade_price_24h"]) >= 1e9]
    up = sum(bot.fnum(t["signed_change_rate"]) > 0 for t in live)
    avg = sum(bot.fnum(t["signed_change_rate"]) for t in live) / max(len(live), 1) * 100
    btc = next(t for t in tickers if t["market"] == "KRW-BTC")
    px = bot.fnum(btc["trade_price"])
    q = bot.public("/v1/candles/minutes/15", {"market": "KRW-BTC", "count": 20})
    time.sleep(0.11)
    hrs = bot.public("/v1/candles/minutes/60", {"market": "KRW-BTC", "count": 25})
    time.sleep(0.11)
    days = bot.public("/v1/candles/days", {"market": "KRW-BTC", "count": 8})
    d24 = bot.fnum(btc["signed_change_rate"]) * 100
    metrics = [("BTC 1시간", pct(px, bot.fnum(q[4]["trade_price"])), 2), ("BTC 4시간", pct(px, bot.fnum(q[16]["trade_price"])), 3),
               ("BTC 오늘", d24, 5), ("BTC 7일", pct(px, bot.fnum(days[7]["trade_price"])), 10),
               ("전체 평균 등락", avg, 4)]
    points = sum(v > 0 for _, v, _ in metrics) + (up / max(len(live), 1) > .5)
    label = ["하락장", "하락장", "약세", "중립", "강세", "상승장", "상승장"][points]
    hourly = [{"v": pct(bot.fnum(h["trade_price"]), bot.fnum(h["opening_price"])), "t": h["candle_date_time_kst"][11:16]}
              for h in reversed(hrs)]
    SIG.update(time=time.time(), data={"label": label, "points": points, "max": 6,
               "metrics": [{"name": n, "value": v, "scale": s} for n, v, s in metrics],
               "breadth": {"up": up, "total": len(live)}, "hourly": hourly})
    return SIG["data"]


PAGE_FILE = bot.ROOT / "market.html"


def page():
    return PAGE_FILE.read_text(encoding="utf-8")
