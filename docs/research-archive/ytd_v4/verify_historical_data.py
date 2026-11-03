"""Independent public candle data quality evidence (read-only SQLite)."""
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parent
HIST = ROOT / "historical"
UTC = dt.timezone.utc
KST = dt.timezone(dt.timedelta(hours=9))
YTD_START = int(dt.datetime(2026, 1, 1, tzinfo=KST).timestamp())
END = int(dt.datetime(2026, 10, 30, 3, 11, tzinfo=UTC).timestamp())

spec = importlib.util.spec_from_file_location("bulk_downloader", ROOT / "ytd_bulk_download.py")
downloader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(downloader)
db = sqlite3.connect(f"file:{(HIST / 'candles_1m.sqlite').as_posix()}?mode=ro", uri=True)
markets = [row[0] for row in db.execute("SELECT DISTINCT market FROM candles ORDER BY market")]
summary = {"evaluation_start_kst": "2026-01-01T00:00:00+09:00", "evaluation_start_utc": "2025-12-31T15:00:00Z", "end_exclusive_utc": "2026-10-30T03:11:00Z",
           "warmup_start_utc": "2025-12-28T00:00:00Z", "timestamp_unit": "UTC epoch seconds",
           "bar_range_semantics": "Candle starts in [evaluation_start,end). There are 435611 minute slots. A decision-point schedule including both interval endpoints has 435612 points; the first decision can legitimately use the immediately preceding warmup candle. Candle counts and decision-point counts are distinct.",
           "integrity_check": db.execute("PRAGMA integrity_check").fetchone()[0], "markets": {}}
for market in markets:
    times = [row[0] for row in db.execute("SELECT t FROM candles WHERE market=? AND t>=? AND t<? ORDER BY t", (market, YTD_START, END))]
    gaps = [(current, current - previous) for previous, current in zip(times, times[1:]) if current - previous > 60]
    invalid = db.execute("""SELECT count(*) FROM candles WHERE market=? AND
        (t%60 != 0 OR open<=0 OR high<=0 OR low<=0 OR close<=0 OR value<0 OR volume<0
         OR high<low OR high<open OR high<close OR low>open OR low>close)""", (market,)).fetchone()[0]
    summary["markets"][market] = {
        "ytd_candles": len(times), "ytd_expected_minute_slots": (END-YTD_START)//60,
        "ytd_absent_minute_slots": (END-YTD_START)//60-len(times),
        "observed_gap_count": len(gaps), "invalid_ohlcv_or_alignment_rows": invalid,
        "largest_gaps": [{"next_observation_utc": dt.datetime.fromtimestamp(t, UTC).isoformat(), "absent_minutes": (delta//60)-1}
                         for t, delta in sorted(gaps, key=lambda row: row[1], reverse=True)[:10]]}
universe = json.loads((HIST / "bulk_market_inventory.json").read_text(encoding="utf-8"))
current = json.loads((HIST / "current_public_markets.json").read_text(encoding="utf-8"))
bulk_krw = {row["key"].split("/")[1] for row in universe if row["key"].startswith("candle/KRW-")}
current_krw = {row["market"] for row in current if row["market"].startswith("KRW-")}
summary["universe"] = {"historical_directory_krw_markets": len(bulk_krw), "current_krw_markets": len(current_krw),
                       "bulk_not_current": sorted(bulk_krw-current_krw), "current_not_bulk": sorted(current_krw-bulk_krw),
                       "limitation": "Current directory equals current listing; delisted historical universe unavailable. Fixed ten-current-market sample has selection/survivorship limitations."}
gap_url = "https://api.upbit.com/v1/candles/minutes/1?market=KRW-BTC&count=4&to=2026-04-12T22:36:00Z"
gap_rest = json.loads(downloader.get(gap_url))
(HIST / "gap_crosscheck_rest.json").write_text(json.dumps({"url": gap_url, "response": gap_rest}, indent=2), encoding="utf-8")
summary["gap_crosscheck"] = {"url": gap_url, "utc_starts": [row["candle_date_time_utc"] for row in gap_rest],
                             "conclusion": "Public REST agrees with bulk: 2026-04-12T17:00 next observed 22:35. No gap filling performed; cause cannot be identified from candle data alone."}
(HIST / "quality_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"integrity_check": summary["integrity_check"], "markets": {key: {field: value for field, value in row.items() if field != "largest_gaps"} for key,row in summary["markets"].items()}, "universe": summary["universe"]}, ensure_ascii=False, indent=2))
