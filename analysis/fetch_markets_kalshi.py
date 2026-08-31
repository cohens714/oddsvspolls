"""
Probe Kalshi for settled 2024 election markets and their price history.

    python3 fetch_markets_kalshi.py --statuses        # which status values work
    python3 fetch_markets_kalshi.py --find-series 24  # series for a cycle
    python3 fetch_markets_kalshi.py --markets SENATE  # markets in a series
    python3 fetch_markets_kalshi.py --candles TICKER  # does history work?

Standard library only, no key required for public market data.

WHY THIS IS WORTH A LOOK AFTER POLYMARKET FAILED
------------------------------------------------
Polymarket's CLOB returns nothing for settled markets, so its 2024 prices
are unreachable. Kalshi documents a candlesticks endpoint, which is a
different mechanism, and it was a CFTC-regulated exchange running election
contracts through 2024. If candlesticks serve settled markets, a completed
cycle becomes available and the site can compare the two sources against
real outcomes before November rather than after.

Everything here probes rather than assumes. Kalshi's parameter names have
already surprised us once: status=active returns a 400, and the SENATE
series in the Politics catalogue holds no current markets.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from pathlib import Path
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent

BASE = "https://api.elections.kalshi.com/trade-api/v2"
USER_AGENT = "oddsvspolls.com (+https://oddsvspolls.com) python-urllib"
TIMEOUT = 30
DELAY = 0.3

_last = [0.0]


def get(path, params=None, quiet=False):
    wait = DELAY - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()

    params = params or {}
    query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    url = f"{BASE}{path}" + (f"?{query}" if query else "")
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT,
                                    "Accept": "application/json"})
        with urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        if not quiet:
            code = getattr(exc, "code", "")
            print(f"    failed{f' ({code})' if code else ''}: {exc}")
        return None


def statuses():
    """Find which status values the markets endpoint accepts.

    We know 'active' is rejected. Settled markets are what a backtest needs,
    so establishing the right word is the first blocker.
    """
    print("=== status values on /markets ===")
    for status in ("open", "closed", "settled", "finalized", "determined",
                   "unopened", "inactive", None):
        payload = get("/markets", {"status": status, "limit": 5}, quiet=True)
        if payload is None:
            print(f"  {str(status):<12} rejected")
            continue
        markets = payload.get("markets") or []
        sample = markets[0].get("status") if markets else ""
        print(f"  {str(status):<12} {len(markets)} returned"
              f"{f'  (status field reads {sample!r})' if sample else ''}")
    print()
    return 0


# Election series turned out not to live under Politics, so search every
# category rather than assuming which one holds them.
CATEGORIES = [None, "Politics", "Elections", "Election", "World",
              "Economics", "Financials"]


def find_series(needle):
    """List series whose ticker or title matches, across categories.

    The per-state series seen in event scans (SENATEGA, SENATEAL) are
    absent from Politics, so the category taxonomy does not group election
    contracts where you would expect. Searching everything is cheap and
    avoids another round of wrong guesses.
    """
    low = needle.lower()
    seen, hits = set(), []

    for cat in CATEGORIES:
        payload = get("/series", {"category": cat}, quiet=True)
        if not isinstance(payload, dict):
            continue
        series = payload.get("series") or []
        fresh = 0
        for s in series:
            tick = str(s.get("ticker", ""))
            if tick in seen:
                continue
            seen.add(tick)
            fresh += 1
            if low in f"{tick}{s.get('title', '')}".lower():
                hits.append((tick, str(s.get("title", "")), cat or "(all)"))
        print(f"  category={str(cat):<12} {len(series):>5} series, "
              f"{fresh:>5} new")

    print(f"\n=== {len(hits)} of {len(seen)} series match {needle!r} ===\n")
    for tick, title, cat in sorted(hits)[:70]:
        print(f"  {tick:<24} {title[:48]:<50} [{cat}]")
    print()
    return 0


def markets_in(series_ticker, status=None):
    """Every market in a series, across statuses if none is given."""
    print(f"=== markets in {series_ticker} ===")
    found = 0
    for st in ([status] if status else ["settled", "closed", "open", None]):
        payload = get("/markets", {"series_ticker": series_ticker,
                                   "status": st, "limit": 200}, quiet=True)
        if not isinstance(payload, dict):
            continue
        markets = payload.get("markets") or []
        if not markets:
            print(f"  status={st or 'any':<10} none")
            continue
        print(f"  status={st or 'any':<10} {len(markets)} markets")
        for m in markets[:12]:
            print(f"    {str(m.get('ticker')):<28} "
                  f"{str(m.get('title'))[:36]:<38}")
            print(f"      YES means {m.get('yes_sub_title') or '?'}   "
                  f"result={m.get('result') or '?'}   "
                  f"close={str(m.get('close_time'))[:10]}")
        found += len(markets)
        if status:
            break
    if not found:
        print("  nothing found; try --find-series to confirm the ticker")
    print()
    return 0


# State names as they appear in Kalshi series titles, keyed by the race_id
# suffix used everywhere else in this project.
STATE_NAMES = {
    "AK": "alaska", "AZ": "arizona", "CA": "california", "FL": "florida",
    "GA": "georgia", "IA": "iowa", "IL": "illinois", "KS": "kansas",
    "ME": "maine", "MI": "michigan", "MN": "minnesota", "NC": "north carolina",
    "NE": "nebraska", "NH": "new hampshire", "NM": "new mexico",
    "NV": "nevada", "NY": "new york", "OH": "ohio", "PA": "pennsylvania",
    "TX": "texas", "WI": "wisconsin",
}


def write_config(mapping):
    """Write the tickers into races.json rather than have them retyped.

    Eleven tickers copied by hand is eleven chances to put a Georgia ticker
    on the Michigan row, and nothing downstream would notice: the price
    would be plausible and the race would simply be wrong.
    """
    path = HERE / "races.json"
    cfg = json.loads(path.read_text())
    changed = 0
    for race in cfg.get("races", []):
        ticker = mapping.get(race["race_id"])
        if ticker and race.get("kalshi_ticker") != ticker:
            race["kalshi_ticker"] = ticker
            race["kalshi_yes_means"] = "DEM"
            changed += 1
    path.write_text(json.dumps(cfg, indent=2) + "\n")
    print(f"updated {path.name}: {changed} races now have Kalshi tickers")
    return changed


CYCLE_SUFFIX = "26"


def cycle_matches(ticker, close_time):
    """True if this market belongs to the 2026 cycle.

    Checks the ticker's cycle segment first, and falls back to the close
    time for tickers that do not carry one. A 2026 race settles once the
    winner is sworn in, so close dates land in late 2026 or 2027.
    """
    parts = str(ticker).upper().split("-")
    for part in parts[1:]:
        # Segments look like 26, 28, 26NOV03, 26AUG18.
        if len(part) >= 2 and part[:2].isdigit():
            return part[:2] == CYCLE_SUFFIX

    close = str(close_time or "")[:4]
    return close in ("2026", "2027") if close.isdigit() else True


def resolves_on_general(ticker):
    """True only if the market settles on winning the seat.

    Title matching is not enough and has now failed twice on the same
    contract. KXSENATENHD is titled "New Hampshire Democratic Senate
    nominee": it names the state, the office and the right candidate, and
    reads as a race market. Its rules say it resolves on winning the
    nomination.

    rules_primary states the resolution condition in plain language and is
    the only field that distinguishes these reliably. One extra request per
    candidate market is worth it to avoid silently collecting a primary as
    though it were a general.
    """
    if not ticker:
        return False
    payload = get(f"/markets/{ticker}", quiet=True)
    m = (payload or {}).get("market") or {}
    rules = str(m.get("rules_primary", "")).lower()
    if not rules:
        return True          # nothing to judge on; fall back to the title

    # Reject only on known-bad wording. An earlier version also required a
    # whitelist phrase ("sworn in", "elected") and rejected fifteen valid
    # races whose rules simply worded it differently. The asymmetry matters:
    # a missed market costs one race's data, while a wrongly accepted one
    # silently publishes a primary as though it were a general election.
    disqualifying = ("nomination", "nominee", "primary", "endorse",
                     "convention", "caucus", "lieutenant")
    return not any(w in rules for w in disqualifying)


def map_races(write=False):
    """Find the Kalshi ticker for each race's Democratic candidate.

    Series naming is not consistent (KXIASENATE, KXAKSENATE,
    KXMESENATEPERSON), so this searches by state name rather than
    constructing a ticker, then matches the candidate on yes_sub_title.

    Matching on the nominee's name is what keeps primary and hypothetical
    markets out. A series can hold markets for half a dozen candidates who
    never made the ballot, and their tickers look identical in structure.
    """
    try:
        from fetch_polls_votehub import RACES
    except ImportError:
        print("could not import RACES from fetch_polls_votehub.py")
        return 1

    payload = get("/series", {}, quiet=True)
    series = payload.get("series") or [] if isinstance(payload, dict) else []
    print(f"scanning {len(series)} series\n")

    results = {}
    for race_id, (subject, dem, rep, poll_type) in sorted(RACES.items()):
        code = race_id.split("-")[-1]
        state = STATE_NAMES.get(code)
        office = "governor" if poll_type == "governor" else "senate"
        if not state or not dem:
            print(f"  {race_id:<22} skipped (no state or nominee configured)")
            continue

        # Candidate series: title mentions the state and the word senate,
        # and is not a primary, combo or margin market.
        cands = []
        for s in series:
            title = str(s.get("title", "")).lower()
            tick = str(s.get("ticker", ""))
            if state not in title or office not in title:
                continue
            # Reject anything that is not "who wins this seat". Alaska
            # matched an endorsement market on the first pass: its title
            # named the state, the chamber and the candidate, and looked
            # identical in structure to a real race market.
            # "lieutenant" and "lt gov" matter because "governor" is a
            # substring of both: Georgia matched KXLTGOVGA, the Lieutenant
            # Governor series, on the office check.
            if any(w in title for w in (
                    "primary", "combo", "margin", "advancer", "nomination",
                    "nominee", "round", "matchup", "outright", "endorse",
                    "approval", "poll", "concede", "recount", "turnout",
                    "call", "resign", "retire", "run for", "announce",
                    "lieutenant", "lt gov", "lt. gov")):
                continue
            # Same trap in the ticker, which the title check cannot see.
            if "LTGOV" in tick.upper():
                continue
            cands.append((tick, s.get("title")))

        if not cands:
            print(f"  {race_id:<22} no series found for {state}")
            continue

        surname = dem.split()[-1].lower()
        hit = None
        for tick, title in cands:
            resp = get("/markets", {"series_ticker": tick, "limit": 100},
                       quiet=True)
            for m in (resp or {}).get("markets") or []:
                sub = str(m.get("yes_sub_title", "")).lower()
                mticker = str(m.get("ticker", ""))

                # Most series name the candidate in yes_sub_title, but some
                # name the party instead: New Hampshire's SENATENH-26-D
                # reads "Democratic party" where Georgia's reads "Jon
                # Ossoff". Accept either, and for the party form require the
                # ticker's own -D suffix so the Republican market with its
                # mirror-image wording cannot match.
                by_name = surname in sub
                by_party = (("democrat" in sub or "democratic" in sub)
                            and mticker.upper().endswith("-D"))
                if not (by_name or by_party):
                    continue

                # A series can hold several cycles at once: SENATENH carries
                # -26- and -28- side by side, and the party wording is
                # identical in both. Without this the 2028 contract is as
                # good a match as the 2026 one, and which you get depends on
                # response ordering.
                if not cycle_matches(mticker, m.get("close_time")):
                    continue
                # A market for a race two months out must still be trading.
                # Anything already finalised is a different question that
                # happens to mention the same candidate.
                if str(m.get("status")) in ("finalized", "determined"):
                    continue
                if not resolves_on_general(m.get("ticker")):
                    continue
                hit = (m.get("ticker"), m.get("yes_sub_title"), tick,
                       m.get("status"))
                break
            if hit:
                break

        if hit:
            ticker, yes_means, series_tick, status = hit
            results[race_id] = ticker
            print(f"  {race_id:<22} {ticker}")
            print(f"      series {series_tick}   YES={yes_means}   "
                  f"status={status}")
        else:
            names = ", ".join(t for t, _ in cands[:3])
            print(f"  {race_id:<22} no market for {dem!r} in {names}")

    print(f"\n{len(results)} of {len(RACES)} races mapped")
    print("\nCheck every line above before writing. A market that names the")
    print("right state and candidate is not necessarily a market on who")
    print("wins the seat.\n")

    if write and results:
        write_config(results)
    elif results:
        print("Rerun with --write to put these into races.json.\n")
    return 0


def scan_settled(needle, limit=60):
    """Find series matching a term that actually contain settled markets.

    This is the question that matters. A series full of 2026 and 2028
    contracts is no use for a backtest; only finalised markets have an
    outcome to score against. Rather than reading 300 series names and
    guessing which cycle each belongs to, ask each one whether it holds
    anything settled.
    """
    low = needle.lower()
    payload = get("/series", {}, quiet=True)
    series = payload.get("series") or [] if isinstance(payload, dict) else []
    hits = [s for s in series
            if low in f"{s.get('ticker','')}{s.get('title','')}".lower()]
    print(f"{len(hits)} series match {needle!r}; checking the first {limit} "
          f"for settled markets\n")

    found = []
    for s in sorted(hits, key=lambda x: str(x.get("ticker")))[:limit]:
        tick = str(s.get("ticker"))
        resp = get("/markets", {"series_ticker": tick, "status": "settled",
                                "limit": 20}, quiet=True)
        markets = (resp or {}).get("markets") or []
        if not markets:
            continue
        closes = sorted(str(m.get("close_time"))[:10] for m in markets
                        if m.get("close_time"))
        span = f"{closes[0]} to {closes[-1]}" if closes else "?"
        found.append((tick, s.get("title"), len(markets), span))
        print(f"  {tick:<24} {len(markets):>3} settled   {span}")
        print(f"      {str(s.get('title'))[:60]}")
        for m in markets[:3]:
            print(f"      {str(m.get('ticker')):<30} "
                  f"result={m.get('result') or '?':<6} "
                  f"YES={str(m.get('yes_sub_title'))[:22]}")

    if not found:
        print("  none of these series contain settled markets")
    else:
        print(f"\n{len(found)} series have settled markets. Pick a ticker "
              f"above and run --candles on it.")
    print()
    return 0


def candles(ticker, series_ticker=None):
    """Try to retrieve historical prices for one market.

    This is the whole question. Polymarket refuses history on settled
    markets; if Kalshi serves it, 2024 is recoverable.

    The candlestick route has lived at more than one path, so try the
    documented shapes rather than betting on one.
    """
    print(f"=== price history for {ticker} ===")

    if not series_ticker:
        # The series ticker is the part before the first dash.
        series_ticker = ticker.split("-")[0]
        print(f"  guessing series {series_ticker} from the ticker\n")

    end = int(datetime.now(timezone.utc).timestamp())
    start = end - 3 * 365 * 24 * 3600      # three years back

    attempts = [
        (f"/series/{series_ticker}/markets/{ticker}/candlesticks",
         {"start_ts": start, "end_ts": end, "period_interval": 1440}),
        (f"/series/{series_ticker}/markets/{ticker}/candlesticks",
         {"start_ts": start, "end_ts": end, "period_interval": 60}),
        (f"/markets/{ticker}/candlesticks",
         {"start_ts": start, "end_ts": end, "period_interval": 1440}),
        (f"/markets/trades", {"ticker": ticker, "limit": 100}),
    ]

    for path, params in attempts:
        print(f"  GET {path}")
        payload = get(path, params, quiet=True)
        if payload is None:
            print("    rejected\n")
            continue

        for key in ("candlesticks", "trades", "history"):
            items = payload.get(key)
            if items:
                print(f"    {len(items)} {key} returned")
                print(f"    first: {json.dumps(items[0])[:200]}")
                if len(items) > 1:
                    print(f"    last:  {json.dumps(items[-1])[:200]}")
                print("\n  WORKS. 2024 prices are recoverable through this"
                      " route.\n")
                return 0
        print(f"    empty; keys were {list(payload)[:6]}\n")

    print("  No history from any route. If this is a settled market, Kalshi")
    print("  is another dead end and the comparison waits for your own")
    print("  2026 snapshots.\n")
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--statuses", action="store_true")
    ap.add_argument("--find-series", metavar="TEXT")
    ap.add_argument("--markets", metavar="SERIES")
    ap.add_argument("--status", help="restrict --markets to one status")
    ap.add_argument("--map-races", action="store_true",
                    help="find Kalshi tickers for the configured races")
    ap.add_argument("--write", action="store_true",
                    help="with --map-races, update races.json")
    ap.add_argument("--scan-settled", metavar="TEXT",
                    help="find series with settled markets, e.g. senate")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--candles", metavar="TICKER")
    ap.add_argument("--series", help="series ticker for --candles")
    args = ap.parse_args()

    if args.statuses:
        return statuses()
    if args.find_series:
        return find_series(args.find_series)
    if args.map_races:
        return map_races(args.write)
    if args.scan_settled:
        return scan_settled(args.scan_settled, args.limit)
    if args.markets:
        return markets_in(args.markets, args.status)
    if args.candles:
        return candles(args.candles, args.series)

    ap.error("pass --statuses, --find-series, --scan-settled, --markets "
             "or --candles")


if __name__ == "__main__":
    sys.exit(main())
