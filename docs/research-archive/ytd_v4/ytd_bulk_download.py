"""Public Upbit historical candle downloads; no credentials or trading calls.

Hosts are from Upbit's public production JavaScript environment. ZIPs and their
official SHA-256 checksums are preserved. UTC candle starts are normalized to
epoch seconds in SQLite. Missing no-trade minutes are retained as gaps.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import ssl
import time
import urllib.parse
import urllib.request
import zipfile

API_HOST = "https://crix-data-api.upbit.com"
DOWNLOAD_HOST = "https://crix-data.upbit.com"
PUBLIC_REST = "https://api.upbit.com/v1"
UTC = dt.timezone.utc


def tls_context():
    context = ssl.create_default_context()
    # Python in this Windows environment omits the user/enterprise root store.
    # Import trusted Windows roots while retaining certificate verification.
    if hasattr(ssl, "enum_certificates"):
        for store_name in ("ROOT", "CA"):
            for cert, encoding, _trust in ssl.enum_certificates(store_name):
                if encoding == "x509_asn":
                    try:
                        context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(cert))
                    except ssl.SSLError:
                        pass
    return context


TLS = tls_context()


def get(url, attempts=5):
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "public-research-ytd-v4/1.0"})
            with urllib.request.urlopen(request, context=TLS, timeout=60) as response:
                return response.read()
        except Exception:
            if attempt + 1 == attempts:
                raise
            time.sleep(min(8, 0.7 * (2 ** attempt)))


def listing(prefix):
    return json.loads(get(f"{API_HOST}/api/v1/market-data/listing?" + urllib.parse.urlencode({"prefix": prefix})))


def parse_time(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def epoch(value):
    return int(parse_time(value).timestamp())


def archive_plan(market, start, end):
    monthly, daily = [], []
    for year in range(start.year, end.year + 1):
        monthly.extend(listing(f"candle/{market}/monthly/1m/{year}"))
        daily.extend(listing(f"candle/{market}/daily/1m/{year}"))
    chosen = []
    covered_months = set()
    for item in monthly:
        key = item["key"]
        if not key.endswith(".zip"):
            continue
        stamp = key.rsplit("_", 1)[-1][:-4]
        month_start = dt.datetime.strptime(stamp, "%Y%m").replace(tzinfo=UTC)
        month_end = (month_start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        if month_end > start and month_start < end and month_end <= end:
            chosen.append(item)
            covered_months.add(stamp)
    for item in daily:
        key = item["key"]
        if not key.endswith(".zip"):
            continue
        stamp = key.rsplit("_", 1)[-1][:-4]
        day_start = dt.datetime.strptime(stamp, "%Y%m%d").replace(tzinfo=UTC)
        if day_start + dt.timedelta(days=1) > start and day_start < end and stamp[:6] not in covered_months:
            chosen.append(item)
    return sorted(chosen, key=lambda item: item["key"].rsplit("_", 1)[-1])


def download(item, archive_dir):
    key = item["key"]
    target = archive_dir / Path(key).name
    checksum_target = target.with_name(target.name + ".checksum")
    if not checksum_target.exists():
        checksum_target.write_bytes(get(f"{DOWNLOAD_HOST}/{key}.checksum"))
    expected = checksum_target.read_text().strip().split()[0].lower()
    if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
        payload = get(f"{DOWNLOAD_HOST}/{key}")
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected:
            raise ValueError(f"Checksum mismatch {key}: {actual} != {expected}")
        target.write_bytes(payload)
    return target, {"key": key, "size": target.stat().st_size, "sha256": expected}


def normalize_archive(target, market, start_s, end_s):
    with zipfile.ZipFile(target) as archive:
        for name in archive.namelist():
            if not name.endswith(".csv"):
                continue
            with archive.open(name) as raw:
                for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                    t = epoch(row["date_time_utc"])
                    if start_s <= t < end_s:
                        yield (market, t, float(row["open"]), float(row["high"]),
                               float(row["low"]), float(row["close"]),
                               float(row["acc_trade_price"]), float(row["acc_trade_volume"]), "bulk")


def rest_tail(market, start_s, end_s, raw_dir):
    cursor = end_s
    rows = {}
    page = 0
    while cursor > start_s:
        to = dt.datetime.fromtimestamp(cursor, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = f"{PUBLIC_REST}/candles/minutes/1?" + urllib.parse.urlencode({"market": market, "count": 200, "to": to})
        payload = get(url)
        (raw_dir / f"{market}_tail_{page:04d}.json").write_bytes(payload)
        batch = json.loads(payload)
        if not batch:
            break
        oldest = cursor
        for item in batch:
            t = epoch(item["candle_date_time_utc"])
            oldest = min(oldest, t)
            if start_s <= t < end_s:
                rows[t] = (market, t, item["opening_price"], item["high_price"], item["low_price"],
                           item["trade_price"], item["candle_acc_trade_price"], item["candle_acc_trade_volume"], "rest")
        if oldest >= cursor:
            break
        cursor = oldest
        page += 1
        time.sleep(0.15)
    return sorted(rows.values(), key=lambda row: row[1])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", nargs="+", default=["KRW-BTC", "KRW-ETH"])
    parser.add_argument("--start", default="2025-12-28T00:00:00Z")
    parser.add_argument("--end", default="2026-10-30T03:11:00Z")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent / "historical")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    start, end = parse_time(args.start), parse_time(args.end)
    start_s, end_s = int(start.timestamp()), int(end.timestamp())
    args.root.mkdir(parents=True, exist_ok=True)
    archive_dir = args.root / "archives"
    raw_dir = args.root / "rest_tail"
    for directory in (archive_dir, raw_dir):
        directory.mkdir(exist_ok=True)
    universe = listing("candle")
    (args.root / "bulk_market_inventory.json").write_text(json.dumps(universe, ensure_ascii=False, indent=2), encoding="utf-8")
    current = json.loads(get(f"{PUBLIC_REST}/market/all?is_details=false"))
    (args.root / "current_public_markets.json").write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    con = sqlite3.connect(args.root / "candles_1m.sqlite")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS candles (market TEXT, t INTEGER, open REAL,
        high REAL, low REAL, close REAL, value REAL, volume REAL, source TEXT,
        PRIMARY KEY (market,t)) WITHOUT ROWID""")
    manifest_path = args.root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest.update({"start_utc": start.isoformat(), "end_exclusive_utc": end.isoformat(),
                     "source_api_host": API_HOST, "source_download_host": DOWNLOAD_HOST,
                     "timestamp_unit": "seconds UTC candle start", "schema": "market,t,open,high,low,close,value,volume,source",
                     "sources": "Upbit public historical ZIP CSV + public REST for unpublished tail"})
    manifest.setdefault("markets", {})
    insert_sql = "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?,?)"
    for market in args.markets:
        started = time.monotonic()
        plan = archive_plan(market, start, end)
        print(json.dumps({"event": "plan", "market": market, "archives": len(plan), "bytes": sum(item["size"] for item in plan)}, ensure_ascii=False), flush=True)
        files = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            tasks = [pool.submit(download, item, archive_dir) for item in plan]
            for future in concurrent.futures.as_completed(tasks):
                target, entry = future.result()
                rows = list(normalize_archive(target, market, start_s, end_s))
                con.executemany(insert_sql, rows)
                con.commit()
                files.append(entry)
        last = con.execute("SELECT max(t) FROM candles WHERE market=? AND source='bulk'", (market,)).fetchone()[0]
        tail_start = max(start_s, (last + 60) if last else start_s)
        tail = rest_tail(market, tail_start, end_s, raw_dir)
        con.executemany(insert_sql, tail)
        con.commit()
        count, minimum, maximum = con.execute("SELECT count(*), min(t), max(t) FROM candles WHERE market=? AND t>=? AND t<?", (market, start_s, end_s)).fetchone()
        dupes = 0  # primary key removes duplicate overlaps; originals remain preserved.
        summary = {"rows": count, "min_t": minimum, "max_t": maximum,
                   "min_utc": dt.datetime.fromtimestamp(minimum, UTC).isoformat() if minimum else None,
                   "max_utc": dt.datetime.fromtimestamp(maximum, UTC).isoformat() if maximum else None,
                   "missing_minutes_in_full_range": ((end_s-start_s)//60)-count,
                   "tail_rest_rows": len(tail), "files": sorted(files, key=lambda item: item["key"]),
                   "seconds": round(time.monotonic()-started, 2)}
        manifest["markets"][market] = summary
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"event": "complete", "market": market, **{key:value for key,value in summary.items() if key != "files"}}, ensure_ascii=False), flush=True)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    print(json.dumps({"event": "done", "sqlite": str(args.root / "candles_1m.sqlite")}), flush=True)


if __name__ == "__main__":
    main()
