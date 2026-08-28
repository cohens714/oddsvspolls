"""
Find real Polymarket slugs and Kalshi tickers to paste into races.json.

    python3 discover.py --scan senate        # find which series/tags hold it
    python3 discover.py senate               # search both venues
    python3 discover.py senate --pages 40    # dig deeper if not found
    python3 discover.py --debug senate       # print every URL

Standard library only.

Findings that shaped this, verified against the live API rather than docs:

  Polymarket caps limit at 100 regardless of what you ask, and neither
  tag_slug nor tag filters the events endpoint. Only offset pagination works,
  so we page and filter client-side.

  Kalshi's SENATE series returns no markets under any status, so it is almost
  certainly the settled 2024 series. Its markets endpoint is dominated by
  parlay contracts, so we page the events endpoint instead, which is far
  smaller, and filter on title.

Both loops track identifiers they have already seen. If a venue silently
ignores our pagination parameter and keeps returning page one, the scan
reports that rather than looping pointlessly.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"
KALSHI_EVENTS = "https://api.elections.kalshi.com/trade-api/v2/events"

USER_AGENT = "oddsvspolls.com data collector (+https://oddsvspolls.com)"
TIMEOUT = 30
PAGE = 100
DEBUG = False


def try_json(url, params=None, label=""):
    params = params or {}
    query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    full = f"{url}?{query}" if query else url
    if DEBUG:
        print(f"    GET {full}")
    try:
        req = Request(full, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        code = getattr(exc, "code", "")
        print(f"  [{label}] failed{f' ({code})' if code else ''}: {exc}")
        return None


def maybe_json(value):
    """Gamma returns arrays as JSON-encoded strings. Handle both."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


# --------------------------------------------------------------------------
# Iterators: yield every event, stopping if pagination stalls
# --------------------------------------------------------------------------

def iter_polymarket_events(max_pages):
    seen, offset = set(), 0
    for page_no in range(max_pages):
        page = try_json(GAMMA_EVENTS, {
            "active": "true", "closed": "false", "limit": PAGE, "offset": offset,
        }, "pm")
        if not isinstance(page, list) or not page:
            return
        fresh = [e for e in page if e.get("id") not in seen]
        if not fresh:
            print(f"  [pm] pagination stalled at page {page_no + 1}, "
                  f"offset returned only records already seen")
            return
        for e in fresh:
            seen.add(e.get("id"))
            yield e
        offset += len(page)
        if len(page) < PAGE:
            return


def iter_kalshi_events(max_pages, status=None):
    seen, cursor = set(), None
    for page_no in range(max_pages):
        params = {"limit": 200, "cursor": cursor, "with_nested_markets": "true"}
        if status:
            params["status"] = status
        payload = try_json(KALSHI_EVENTS, params, "kalshi")
        if not isinstance(payload, dict):
            return
        events = payload.get("events") or []
        if not events:
            return
        fresh = [e for e in events if e.get("event_ticker") not in seen]
        if not fresh:
            print(f"  [kalshi] pagination stalled at page {page_no + 1}")
            return
        for e in fresh:
            seen.add(e.get("event_ticker"))
            yield e
        cursor = payload.get("cursor")
        if not cursor:
            return


# --------------------------------------------------------------------------
# Scan: what series and tags exist that match a term
# --------------------------------------------------------------------------

def scan(term, max_pages):
    """Enumerate matching events and report the series behind them.

    Use this first. It tells you which Kalshi series ticker actually holds
    the current contracts, which the Politics catalog does not, since that
    still lists settled series from prior cycles.
    """
    needle = term.lower()

    print("=== Kalshi: series behind events matching your term ===")
    series = Counter()
    total, shown = 0, 0
    for ev in iter_kalshi_events(max_pages):
        total += 1
        blob = f"{ev.get('title', '')} {ev.get('event_ticker', '')}".lower()
        if needle in blob:
            series[ev.get("series_ticker") or "(none)"] += 1
            if shown < 15:
                shown += 1
                print(f"  {str(ev.get('event_ticker')):<28} "
                      f"series={str(ev.get('series_ticker')):<16} "
                      f"{str(ev.get('title'))[:44]}")
    print(f"\n  scanned {total} events")
    if series:
        print("  series ticker counts:")
        for tick, n in series.most_common(15):
            print(f"    {tick:<24} {n}")
    else:
        print(f"  no events matching {needle!r}; try --pages higher")

    print("\n=== Polymarket: events matching your term ===")
    total, hits = 0, 0
    for ev in iter_polymarket_events(max_pages):
        total += 1
        if needle in str(ev.get("title", "")).lower():
            hits += 1
            if hits <= 15:
                print(f"  slug={str(ev.get('slug')):<52} "
                      f"{str(ev.get('title'))[:40]}")
    print(f"\n  scanned {total} events, {hits} match")
    print()


# --------------------------------------------------------------------------
# Search: show the identifiers and prices you need for races.json
# --------------------------------------------------------------------------

def search_polymarket(term, max_pages):
    print("=== Polymarket ===")
    hits, total = 0, 0
    for ev in iter_polymarket_events(max_pages):
        total += 1
        for m in ev.get("markets", []) or []:
            blob = (f"{m.get('question', '')} {m.get('slug', '')} "
                    f"{ev.get('title', '')}").lower()
            if term.lower() not in blob:
                continue
            hits += 1
            outcomes = maybe_json(m.get("outcomes")) or []
            prices = maybe_json(m.get("outcomePrices")) or []
            print(f"\n  slug:     {m.get('slug')}")
            print(f"  event:    {ev.get('title')}")
            print(f"  question: {m.get('question')}")
            for label, price in zip(outcomes, prices):
                try:
                    print(f"    outcome: {str(label)!r:<26} {float(price):.3f}")
                except (TypeError, ValueError):
                    print(f"    outcome: {str(label)!r:<26} {price}")
            if hits >= 20:
                print(f"\n  ...stopping at 20 (scanned {total} events)")
                return
    print(f"\n  scanned {total} events, {hits} match")
    print()


def search_kalshi(term, max_pages, series=None):
    print("=== Kalshi ===")
    hits, total = 0, 0
    for ev in iter_kalshi_events(max_pages):
        total += 1
        if series and ev.get("series_ticker") != series:
            continue
        ev_blob = f"{ev.get('title', '')} {ev.get('event_ticker', '')}".lower()
        for m in ev.get("markets", []) or []:
            blob = (f"{ev_blob} {m.get('title', '')} {m.get('ticker', '')} "
                    f"{m.get('yes_sub_title', '')}").lower()
            if term and term.lower() not in blob:
                continue
            hits += 1
            print(f"\n  ticker:    {m.get('ticker')}")
            print(f"  event:     {ev.get('title')} [{ev.get('series_ticker')}]")
            print(f"  YES means: {m.get('yes_sub_title') or m.get('title')}")
            print(f"  status:    {m.get('status')}")
            print(f"  price:     bid {m.get('yes_bid')} / ask {m.get('yes_ask')}"
                  f", last {m.get('last_price')}")
            if hits >= 20:
                print(f"\n  ...stopping at 20 (scanned {total} events)")
                return
    print(f"\n  scanned {total} events, {hits} match")
    print()


def inspect_polymarket(slug):
    """Fetch one event by slug and print every market with its outcomes.

    This is the payoff step: the outcome labels printed here are what go into
    polymarket_outcome in races.json, and they must match character for
    character.
    """
    print(f"=== Polymarket event: {slug} ===")
    page = try_json(GAMMA_EVENTS, {"slug": slug}, "pm")
    if not isinstance(page, list) or not page:
        print("  no event returned for that slug\n")
        return
    for ev in page:
        print(f"  title: {ev.get('title')}")
        print(f"  end:   {ev.get('endDate')}")
        for m in ev.get("markets", []) or []:
            outcomes = maybe_json(m.get("outcomes")) or []
            prices = maybe_json(m.get("outcomePrices")) or []
            print(f"\n    market slug: {m.get('slug')}")
            print(f"    question:    {m.get('question')}")
            print(f"    closed:      {m.get('closed')}")
            for label, price in zip(outcomes, prices):
                try:
                    print(f"      outcome: {str(label)!r:<24} {float(price):.3f}")
                except (TypeError, ValueError):
                    print(f"      outcome: {str(label)!r:<24} {price}")
    print()


def inspect_kalshi(series_ticker):
    """List every event and market in one Kalshi series.

    yes_sub_title is the field that tells you what YES resolves to, which is
    what kalshi_yes_means records in races.json.
    """
    print(f"=== Kalshi series: {series_ticker} ===")
    payload = try_json(KALSHI_EVENTS, {
        "series_ticker": series_ticker, "limit": 200,
        "with_nested_markets": "true",
    }, "kalshi")
    events = payload.get("events") if isinstance(payload, dict) else None
    if not events:
        print("  no events returned for that series\n")
        return
    for ev in events:
        print(f"\n  event: {ev.get('event_ticker')}  {ev.get('title')}")
        for m in ev.get("markets", []) or []:
            print(f"    ticker:    {m.get('ticker')}")
            print(f"    YES means: {m.get('yes_sub_title') or m.get('title')}")
            print(f"    status:    {m.get('status')}   "
                  f"bid {m.get('yes_bid')} / ask {m.get('yes_ask')}")
    print()


def main():
    global DEBUG
    ap = argparse.ArgumentParser()
    ap.add_argument("term", nargs="?", help="substring to search for")
    ap.add_argument("--scan", metavar="TERM",
                    help="find which series and tags hold this term")
    ap.add_argument("--event", metavar="SLUG",
                    help="inspect one Polymarket event by slug")
    ap.add_argument("--kseries", metavar="TICKER",
                    help="inspect one Kalshi series by ticker")
    ap.add_argument("--series", help="restrict Kalshi search to one series")
    ap.add_argument("--pages", type=int, default=25, help="max pages per venue")
    ap.add_argument("--venue", choices=["polymarket", "kalshi", "both"],
                    default="both")
    ap.add_argument("--debug", action="store_true", help="print every URL")
    args = ap.parse_args()

    DEBUG = args.debug

    if args.event:
        inspect_polymarket(args.event)
        return 0
    if args.kseries:
        inspect_kalshi(args.kseries)
        return 0
    if args.scan:
        scan(args.scan, args.pages)
        return 0
    if args.term is None:
        ap.error('provide a search term (use "" for all), --scan, '
                 '--event or --kseries')

    if args.venue in ("polymarket", "both"):
        search_polymarket(args.term, args.pages)
    if args.venue in ("kalshi", "both"):
        search_kalshi(args.term, args.pages, args.series)
    return 0


if __name__ == "__main__":
    sys.exit(main())
