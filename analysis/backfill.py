"""
Backfill market price history into data/snapshots.csv.

    python3 backfill.py --probe              # do the history endpoints work?
    python3 backfill.py --dry-run            # fetch and report, write nothing
    python3 backfill.py                      # append backfilled rows

Standard library only.

WHY THIS MATTERS MORE THAN IT SOUNDS
------------------------------------
The live collector started in August 2026 and samples four times a day. But
these markets have been trading since late 2025, and both venues appear to
retain the price path from inception. If that history is reachable, the
2026 comparison covers ten months rather than ten weeks, and the horizon
question this project exists to answer ("when is each source better?")
becomes answerable instead of aspirational.

Polymarket's CLOB refuses history for SETTLED markets, which is what killed
the 2024 backtest. Open markets were never tested. Kalshi's candlesticks
work on settled markets, so they should work on open ones too.

WHAT IT WRITES, AND WHY IT IS MARKED
------------------------------------
Backfilled rows go into the same snapshots.csv as live ones, with venue
suffixed '-backfill'. They are not equivalent to live observations: they
come from the venue's own aggregation rather than a request we made and
timestamped, and the venue could revise or lose them. Keeping them
distinguishable means an analysis can include or exclude them deliberately,
and a reader can see which is which.

Safe to rerun. It skips any race and venue that already has backfilled
rows, so adding races later and running it again imports only the new ones.
To redo a race, delete its backfilled rows from snapshots.csv first.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
SNAPSHOT_FILE = DATA / "snapshots.csv"
CONFIG = HERE / "races.json"

GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
CLOB_HISTORY = "https://clob.polymarket.com/prices-history"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

USER_AGENT = "oddsvspolls.com (+https://oddsvspolls.com) python-urllib"
TIMEOUT = 45
DELAY = 0.4

FIELDS = [
    "fetched_at", "snapshot_date", "race_id", "cycle", "venue", "yes_side",
    "prob", "days_out", "raw_price", "inverted", "volume", "liquidity",
    "spread", "note",
]

_last = [0.0]


def get_json(url, params=None, quiet=False):
    wait = DELAY - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()

    params = params or {}
    query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    full = f"{url}?{query}" if query else url
    try:
        req = Request(full, headers={"User-Agent": USER_AGENT,
                                     "Accept": "application/json"})
        with urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        if not quiet:
            code = getattr(exc, "code", "")
            print(f"    failed{f' ({code})' if code else ''}: {exc}")
        return None


def maybe_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def load_config():
    cfg = json.loads(CONFIG.read_text())
    return cfg, date.fromisoformat(cfg["election_date"])


# --------------------------------------------------------------------------
# Polymarket
# --------------------------------------------------------------------------

def poly_token(slug):
    """The Yes token id for a market slug."""
    payload = get_json(GAMMA_MARKETS, {"slug": slug}, quiet=True)
    if not isinstance(payload, list) or not payload:
        return None
    market = payload[0]
    ids = maybe_json(market.get("clobTokenIds")) or []
    outcomes = maybe_json(market.get("outcomes")) or []
    for label, tid in zip(outcomes, ids):
        if str(label).strip().lower() == "yes":
            return tid
    return ids[0] if ids else None


def poly_history(token_id):
    """Daily price points for a token, as [(datetime, price)]."""
    payload = get_json(CLOB_HISTORY,
                       {"market": token_id, "interval": "max", "fidelity": 1440},
                       quiet=True)
    if not isinstance(payload, dict):
        return []
    out = []
    for point in payload.get("history") or []:
        try:
            out.append((datetime.fromtimestamp(int(point["t"]), timezone.utc),
                        float(point["p"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out)


# --------------------------------------------------------------------------
# Kalshi
# --------------------------------------------------------------------------

def kalshi_history(ticker, series_ticker=None, days=400):
    """Daily candlesticks for a market, as [(datetime, price)].

    Uses the mean price over each period rather than the close, since a
    thin market's close can be a single stale trade while the mean reflects
    where it actually traded that day.
    """
    series_ticker = series_ticker or ticker.split("-")[0]
    end = int(datetime.now(timezone.utc).timestamp())
    start = end - days * 24 * 3600

    payload = get_json(
        f"{KALSHI}/series/{series_ticker}/markets/{ticker}/candlesticks",
        {"start_ts": start, "end_ts": end, "period_interval": 1440},
        quiet=True)
    if not isinstance(payload, dict):
        return []

    # The ticker is in the path here rather than a query parameter, so it
    # cannot be silently ignored the way the list endpoint's filter was.
    # Still worth confirming the response names the market we asked for.
    got = payload.get("ticker")
    if got and got != ticker:
        print(f"    ticker mismatch: asked {ticker}, got {got}")
        return []

    out = []
    for c in payload.get("candlesticks") or []:
        try:
            ts = int(c["end_period_ts"])
            price = c.get("price") or {}
            raw = price.get("mean_dollars") or price.get("close_dollars")
            if raw is None:
                continue
            p = float(raw)
        except (KeyError, TypeError, ValueError):
            continue
        if 0.0 <= p <= 1.0:
            out.append((datetime.fromtimestamp(ts, timezone.utc), p))
    return sorted(out)


# --------------------------------------------------------------------------

def probe():
    """Test both history endpoints against one live market each."""
    cfg, _ = load_config()
    races = cfg["races"]

    print("=== Polymarket, open market ===")
    slug = next((r["polymarket_slug"] for r in races
                 if r.get("polymarket_slug")), None)
    if not slug:
        print("  no slug configured")
    else:
        print(f"  {slug[:70]}")
        token = poly_token(slug)
        if not token:
            print("  no token id returned")
        else:
            hist = poly_history(token)
            print(f"  {len(hist)} price points")
            if hist:
                print(f"  spans {hist[0][0].date()} to {hist[-1][0].date()}")
                print(f"  first {hist[0][1]:.3f}, last {hist[-1][1]:.3f}")
                print("\n  WORKS on open markets. Backfill is available,")
                print("  which extends the series back to market inception.")
            else:
                print("\n  Empty. The CLOB refuses history for open markets")
                print("  too, so Polymarket history starts from your own")
                print("  snapshots.")

    print("\n=== Kalshi ===")
    ticker = next((r["kalshi_ticker"] for r in races
                   if r.get("kalshi_ticker")), None)
    if not ticker:
        print("  no ticker configured yet; run")
        print("  python3 fetch_markets_kalshi.py --map-races")
    else:
        print(f"  {ticker}")
        hist = kalshi_history(ticker)
        print(f"  {len(hist)} candles")
        if hist:
            print(f"  spans {hist[0][0].date()} to {hist[-1][0].date()}")
            print(f"  first {hist[0][1]:.3f}, last {hist[-1][1]:.3f}")
    print()
    return 0


def existing_backfill():
    """Which (race, venue) pairs already hold backfilled rows.

    The old guard refused to run at all if any backfill existed, which was
    right when every race was added at once and wrong the moment races were
    added later: the governor races could never be backfilled because the
    Senate ones already had been.

    Skipping per race keeps the protection that matters. This appends
    without deduplicating, so re-importing a race would double its history,
    and duplicates are invisible in a chart while quietly doubling those
    days' weight in any average.
    """
    seen = set()
    if not SNAPSHOT_FILE.exists():
        return seen
    with SNAPSHOT_FILE.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            venue = row.get("venue") or ""
            if "backfill" in venue:
                seen.add((row.get("race_id"), venue.replace("-backfill", "")))
    return seen


def backfill(dry_run):
    cfg, election_day = load_config()
    cycle = cfg["cycle"]
    rows = []

    already = existing_backfill()
    if already:
        races_done = sorted({r for r, _ in already})
        print(f"{len(already)} race/venue pairs already backfilled "
              f"across {len(races_done)} races; skipping those\n")

    for race in cfg["races"]:
        race_id = race["race_id"]
        yes_side = race["yes_side"]

        if race.get("polymarket_slug") and \
                (race_id, "polymarket") not in already:
            token = poly_token(race["polymarket_slug"])
            hist = poly_history(token) if token else []
            for when, price in hist:
                rows.append({
                    "fetched_at": when.isoformat(timespec="seconds"),
                    "snapshot_date": when.date().isoformat(),
                    "race_id": race_id, "cycle": cycle,
                    "venue": "polymarket-backfill", "yes_side": yes_side,
                    "prob": round(price, 6),
                    "days_out": (election_day - when.date()).days,
                    "raw_price": round(price, 6), "inverted": False,
                    "volume": "", "liquidity": "", "spread": "", "note": "",
                })
            print(f"  {race_id:<24} polymarket {len(hist):>4} points")

        if race.get("kalshi_ticker") and (race_id, "kalshi") not in already:
            hist = kalshi_history(race["kalshi_ticker"])
            flip = race.get("kalshi_yes_means") != yes_side
            for when, price in hist:
                p = 1.0 - price if flip else price
                rows.append({
                    "fetched_at": when.isoformat(timespec="seconds"),
                    "snapshot_date": when.date().isoformat(),
                    "race_id": race_id, "cycle": cycle,
                    "venue": "kalshi-backfill", "yes_side": yes_side,
                    "prob": round(p, 6),
                    "days_out": (election_day - when.date()).days,
                    "raw_price": round(price, 6), "inverted": flip,
                    "volume": "", "liquidity": "", "spread": "", "note": "",
                })
            print(f"  {race_id:<24} kalshi     {len(hist):>4} points")

    if not rows:
        print("\nnothing new to backfill; every configured race and venue "
              "already has history")
        return 0

    dates = sorted(r["snapshot_date"] for r in rows)
    print(f"\n{len(rows)} rows spanning {dates[0]} to {dates[-1]}")

    if dry_run:
        print("dry run, nothing written")
        return 0

    if not SNAPSHOT_FILE.exists():
        print(f"{SNAPSHOT_FILE} missing", file=sys.stderr)
        return 1

    with SNAPSHOT_FILE.open("a", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=FIELDS).writerows(rows)
    print(f"appended to {SNAPSHOT_FILE.name}")
    print("\nBackfilled rows carry venue '<venue>-backfill' so they can be")
    print("separated from live observations in any analysis.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.probe:
        return probe()
    return backfill(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
