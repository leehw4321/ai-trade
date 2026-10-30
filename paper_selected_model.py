"""Isolated public-data paper observation for the frozen research candidate.

--once only reads public candles and prints current signal/status. It never
changes a ledger. --watch explicitly starts a separate virtual account at the
NEXT five-minute boundary. Completed 5m candles are replayed deterministically;
fills are delayed OHLC reconstructions, not live execution or fill guarantees.

No credentials, production account, broker, or private endpoint are imported.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

from analysis_runs.model_search.model_candidates import CANDIDATES, iter_signals, prepare_features
from analysis_runs.model_search.research_portfolio import PortfolioConfig, cost_config, iso, simulate

ROOT = Path(__file__).resolve().parent
SELECTION = ROOT / "analysis_runs/model_search/selection.json"
LEDGER = ROOT / "trend_data/research_model_paper.json"
BARS = ROOT / "trend_data/research_model_paper_bars.sqlite"
MARKETS = ("KRW-ADA", "KRW-AVAX", "KRW-BTC", "KRW-DOGE", "KRW-ETH",
           "KRW-HBAR", "KRW-LINK", "KRW-SOL", "KRW-SUI", "KRW-XRP")
UTC = dt.timezone.utc
BAR_SECONDS = 300
MODE = "isolated_delayed_ohlc_paper"


def load_selection(path=SELECTION):
    selection = json.loads(Path(path).read_text(encoding="utf-8"))
    selected = selection.get("selected_config")
    if not isinstance(selected, dict):
        raise ValueError("선택된 연구 모델이 없습니다. selection.json의 selected_config를 확인하세요.")
    ident = selected.get("id")
    allowed = {c["id"]: c for c in CANDIDATES}
    if (ident not in allowed or set(selected)-set(allowed[ident])-{"name", "timeframe"} or
        {k: selected.get(k) for k in allowed[ident]} != allowed[ident]):
        raise ValueError("선택 모델이 고정된 연구 후보와 다릅니다. 임의 설정은 실행하지 않습니다.")
    selected = allowed[ident]
    digest = hashlib.sha256(json.dumps({"candidate": selected, "config": cost_config().__dict__,
                                       "markets": MARKETS}, sort_keys=True).encode()).hexdigest()
    return selection, dict(selected), digest


class PublicCandles:
    """Only Upbit's unauthenticated 5m candle endpoint is reachable here."""
    def __init__(self, timeout=10., retries=4, min_interval=.16):
        self.timeout, self.retries, self.min_interval = timeout, retries, min_interval
        self._last_request = 0.

    def _get(self, market, before):
        if market not in MARKETS:
            raise ValueError("market is outside frozen research universe")
        params = {"market": market, "count": 200,
                  "to": dt.datetime.fromtimestamp(before, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
        url = "https://api.upbit.com/v1/candles/minutes/5?"+urllib.parse.urlencode(params)
        last_error = None
        for attempt in range(self.retries+1):
            pause = self.min_interval-(time.monotonic()-self._last_request)
            if pause > 0:
                time.sleep(pause)
            self._last_request = time.monotonic()
            req = urllib.request.Request(url, headers={"User-Agent": "AutoTradeV4-IsolatedPaper/1.0",
                                                       "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, list):
                    raise ValueError("public candle endpoint returned non-list payload")
                return payload
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = error
                if isinstance(error, urllib.error.HTTPError) and error.code not in (429, 500, 502, 503, 504):
                    raise
                if attempt == self.retries:
                    break
                time.sleep(min(8., .5*2**attempt))
        raise RuntimeError(f"공개 캔들 조회 실패: {market}: {last_error}")

    def fetch(self, market, now, *, needed=250, since=None):
        """Return completed observed candles; no zero-volume/gap fabrication."""
        rows, before = {}, int(now)
        for _ in range(500):
            payload = self._get(market, before)
            if not payload:
                break
            observed_times = []
            for raw in payload:
                t = int(dt.datetime.fromisoformat(raw["candle_date_time_utc"]).replace(tzinfo=UTC).timestamp())
                observed_times.append(t)
                if t+BAR_SECONDS > now:
                    continue
                b = {"t": t, "o": float(raw["opening_price"]), "h": float(raw["high_price"]),
                     "l": float(raw["low_price"]), "c": float(raw["trade_price"]),
                     "v": float(raw["candle_acc_trade_volume"]),
                     "value": float(raw["candle_acc_trade_price"])}
                validate_bar(b)
                rows[t] = b
            oldest = min(observed_times)
            if since is not None and oldest <= since:
                break
            if since is None and len(rows) >= needed:
                break
            if oldest >= before:
                raise ValueError("public candle pagination did not move backwards")
            before = oldest
        else:
            raise RuntimeError("재시작 공백이 너무 커 공개 캔들 복구 한도를 초과했습니다. 기존 기록은 유지됩니다.")
        ordered = [rows[t] for t in sorted(rows)]
        return ordered if since is not None else ordered[-needed:]


def validate_bar(bar):
    t = bar["t"]
    numbers = [bar[k] for k in ("o", "h", "l", "c", "v", "value")]
    if (t % BAR_SECONDS or not all(math.isfinite(v) for v in numbers) or
        min(numbers[:4]) <= 0 or min(numbers[4:]) < 0 or
        bar["h"] < max(bar["o"], bar["l"], bar["c"]) or
        bar["l"] > min(bar["o"], bar["h"], bar["c"])):
        raise ValueError("invalid/alignment-broken public candle")


def needed_source_bars(candidate):
    # More than 200 complete native bars, including partial boundary allowance.
    return 250*max(1, candidate["timeframe_s"]//BAR_SECONDS)


def read_only_status(selection_path=SELECTION, ledger_path=LEDGER, client=None, now=None):
    """No mkdir, cache DB, ledger write, or simulated order is performed."""
    selection, candidate, digest = load_selection(selection_path)
    now = int(time.time()) if now is None else int(now)
    public = client or PublicCandles()
    signals, readiness = [], {}
    needed = needed_source_bars(candidate)
    expected_entry = now-now % BAR_SECONDS
    for market in MARKETS:
        rows = public.fetch(market, now, needed=needed)
        prepared = prepare_features(rows)
        current = [s for s in iter_signals(prepared, candidate) if s["entry_t"] == expected_entry]
        signals.extend({"market": market, **s} for s in current)
        frame = prepared["frames"][candidate["timeframe_s"]]
        continuity = int(frame["contiguous"][-1]) if frame["rows"] else 0
        readiness[market] = {"bars": len(rows), "latest_completed_bar_kst": iso(rows[-1]["t"]) if rows else None,
                             "contiguous_native_bars": continuity, "ready": continuity >= 200}
    ledger = None
    if Path(ledger_path).exists():
        old = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
        ledger = {k: old.get(k) for k in ("paper_start_kst", "updated_kst", "candidate_id", "summary", "open_positions")}
    return {"mode": "read_only_public_signal_status", "candidate_id": candidate["id"],
            "approved_paper": selection.get("approved_paper") is True,
            "selection_digest": digest, "asof_kst": iso(now), "signals": signals,
            "readiness": readiness, "existing_isolated_ledger": ledger,
            "notice": "현재 완료 봉의 신호 조회만 수행했습니다. 가상·실제 주문과 원장 변경은 없습니다."}


def open_cache(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS bars(market TEXT,t INTEGER,o REAL,h REAL,l REAL,c REAL,v REAL,value REAL,PRIMARY KEY(market,t)) WITHOUT ROWID")
    return conn


def store_closed_bars(conn, market, rows, now):
    """Keep first observed closed candle immutable, reject silent revisions."""
    added = 0
    for bar in rows:
        validate_bar(bar)
        if bar["t"]+BAR_SECONDS > now:
            raise ValueError("forming candle cannot be persisted")
        values = tuple(bar[k] for k in ("t", "o", "h", "l", "c", "v", "value"))
        old = conn.execute("SELECT t,o,h,l,c,v,value FROM bars WHERE market=? AND t=?", (market, bar["t"])).fetchone()
        if old is not None:
            if any(not math.isclose(a,b, rel_tol=1e-10, abs_tol=1e-8) for a,b in zip(old, values)):
                raise ValueError(f"확정 캔들 변경 감지: {market} {iso(bar['t'])}. 원장을 자동으로 고쳐 쓰지 않았습니다.")
            continue
        conn.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?,?)", (market, *values))
        added += 1
    return added


def cached_rows(conn):
    out = {m: [] for m in MARKETS}
    for market, t, o, h, l, c, v, value in conn.execute("SELECT market,t,o,h,l,c,v,value FROM bars ORDER BY market,t"):
        out[market].append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": v, "value": value})
    return out


def atomic_json(path, payload):
    path = Path(path)
    temp = path.with_suffix(path.suffix+".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


@contextmanager
def exclusive_watch(ledger_path):
    """Use an OS file lock so a crash releases ownership automatically."""
    lock_path = Path(ledger_path).with_suffix(".lock")
    handle = lock_path.open("a+b")
    handle.seek(0)
    if not handle.read(1):
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        raise RuntimeError("별도 모의 관찰기가 이미 실행 중입니다.") from error
    try:
        yield
    finally:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def replay_snapshot(rows, candidate, start, end):
    signals = {m: list(iter_signals(prepare_features(b), candidate)) for m,b in rows.items()}
    if end <= start:
        return {"summary": {"candidate_id": candidate["id"], "initial_cash": 1_000_000.,
                            "final_equity": 1_000_000., "net_profit": 0., "trade_count": 0,
                            "net_return_pct": 0., "status": "waiting_for_first_completed_paper_bar"},
                "trades": [], "open_positions": {}, "halt_events": [], "entry_audit": [], "equity_curve": []}
    return simulate(rows, signals, start, end, cost_config(), candidate_id=candidate["id"],
                    terminal_flatten=False)


def watch(*, experimental=False, selection_path=SELECTION, ledger_path=LEDGER,
          cache_path=BARS, interval=15., client=None, max_cycles=None):
    selection, candidate, digest = load_selection(selection_path)
    if selection.get("approved_paper") is not True and not experimental:
        raise ValueError("이 모델은 검증 기준을 통과하지 못했습니다. 별도 연구용 모의 관찰은 --watch --experimental로만 시작할 수 있습니다.")
    ledger_path, cache_path = Path(ledger_path), Path(cache_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    public = client or PublicCandles()
    with exclusive_watch(ledger_path):
        if ledger_path.exists():
            state = json.loads(ledger_path.read_text(encoding="utf-8"))
            if (state.get("mode") != MODE or state.get("selection_digest") != digest or
                state.get("candidate_id") != candidate["id"] or
                Path(state.get("cache_path", "")).resolve() != cache_path.resolve()):
                raise ValueError("기존 모의 원장과 선택 모델/캐시가 다릅니다. 기록을 덮어쓰지 않았습니다.")
            if not cache_path.exists() and state.get("replayed_end_t") is not None:
                raise ValueError("기존 모의 원장의 캔들 캐시가 없습니다. 원장을 다시 구성할 수 없습니다.")
        else:
            if cache_path.exists():
                raise ValueError("원장 없는 기존 캐시가 있습니다. 이전 기록을 자동으로 재사용하거나 덮어쓰지 않았습니다.")
            now = int(time.time())
            start = (now//BAR_SECONDS+1)*BAR_SECONDS
            state = {"schema": 1, "mode": MODE, "candidate_id": candidate["id"],
                     "candidate": candidate, "selection_digest": digest,
                     "approved_paper": selection.get("approved_paper") is True,
                     "experimental": selection.get("approved_paper") is not True,
                     "created_kst": iso(now), "paper_start_t": start, "paper_start_kst": iso(start),
                     "cache_path": str(cache_path.resolve()), "config": cost_config().__dict__,
                     "notice": "실거래와 별개인 지연 OHLC 모의 관찰. 다음 5분봉 시가 체결을 봉 마감 뒤 재구성하며 실제 체결을 보장하지 않습니다."}
            # Save fixed future start before network collection. A first-fetch
            # failure can resume this same start without inventing a new balance.
            atomic_json(ledger_path, state)
        conn = open_cache(cache_path)
        try:
            cycles, previous_end = 0, state.get("replayed_end_t", 0)
            while True:
                now = int(time.time())
                end = now-now % BAR_SECONDS
                try:
                    counts = {}
                    with conn:
                        for market in MARKETS:
                            last = conn.execute("SELECT max(t) FROM bars WHERE market=?", (market,)).fetchone()[0]
                            new_rows = public.fetch(market, now, needed=needed_source_bars(candidate), since=last)
                            counts[market] = store_closed_bars(conn, market, new_rows, now)
                    if end > previous_end or any(counts.values()) or "summary" not in state:
                        result = replay_snapshot(cached_rows(conn), candidate, state["paper_start_t"], end)
                        state.update({"updated_kst": iso(now), "replayed_end_t": end,
                                      "replayed_end_kst": iso(end), "summary": result["summary"],
                                      "trades": result["trades"], "open_positions": result["open_positions"],
                                      "halt_events": result["halt_events"],
                                      "cache_counts": dict(conn.execute("SELECT market,count(*) FROM bars GROUP BY market")),
                                      "last_error": None})
                        atomic_json(ledger_path, state)
                        previous_end = end
                        print(json.dumps({"updated_kst": state["updated_kst"], "candidate_id": candidate["id"],
                                          "summary": state["summary"], "open_position_count": len(result["open_positions"])}, ensure_ascii=False), flush=True)
                except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
                    state.update(updated_kst=iso(now), last_error=str(error))
                    atomic_json(ledger_path, state)
                    print(json.dumps({"status": "retrying_without_new_fills", "error": str(error)}, ensure_ascii=False), flush=True)
                cycles += 1
                if max_cycles is not None and cycles >= max_cycles:
                    return state
                time.sleep(max(5., min(60., interval)))
        finally:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--once", action="store_true", help="public signal/status only; no ledger writes")
    modes.add_argument("--watch", action="store_true", help="start/resume separate delayed-OHLC paper ledger")
    parser.add_argument("--experimental", action="store_true", help="explicit paper-only opt-in for an unqualified research model")
    parser.add_argument("--interval", type=float, default=15., help="watch polling seconds, clamped to 5..60")
    args = parser.parse_args()
    try:
        if args.once:
            print(json.dumps(read_only_status(), ensure_ascii=False, indent=2))
        else:
            watch(experimental=args.experimental, interval=args.interval)
    except KeyboardInterrupt:
        print("별도 모의 관찰을 종료했습니다. 실제 계좌에는 주문하지 않았습니다.")
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        parser.exit(1, str(error)+"\n")


if __name__ == "__main__":
    main()
