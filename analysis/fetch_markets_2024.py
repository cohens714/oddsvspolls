"""
Pull 2024 Senate market prices and outcomes for the backtest.

    python3 fetch_markets_2024.py --inspect pennsylvania   # what is returned?
    python3 fetch_markets_2024.py --probe-history          # does price history work?
    python3 fetch_markets_2024.py --dry-run
    python3 fetch_markets_2024.py                          # -> historical_markets.csv

Standard library only.

THE THING THAT MAKES THIS NON-OBVIOUS
-------------------------------------
A closed market's outcomePrices are the resolution, 1 and 0. They tell you
who won, not what the market thought beforehand. Scoring a forecaster on its
resolution would give it a perfect record, which is obviously wrong and not
obviously wrong from the data alone.

The forecast has to come from the CLOB price history, sampled at a chosen
number of days before the election. So each race needs two things from two
endpoints: the outcome from Gamma, and the price path from CLOB, joined on
the market's token id.

Prices are sampled at several horizons because "were markets better" has a
different answer a week out than six months out, and a single snapshot would
hide that.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DATA = Path(__file__).resolve().parent.parent / "data"
OUT = DATA / "historical_markets.csv"

GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"
CLOB_HISTORY = "https://clob.polymarket.com/prices-history"

USER_AGENT = "oddsvspolls.com (+https://oddsvspolls.com) python-urllib"
TIMEOUT = 45
DELAY = 0.4                     # between requests; ~40 states is not a raid

ELECTION_2024 = date(2024, 11, 5)

# Horizons to sample, in days before election day.
HORIZONS = [1, 3, 7, 14, 30, 60, 90, 180]

# 2024 Senate races, as Polymarket slugs. Derived from the closed-event
# scan; verify with --inspect before trusting any one of them.
STATES = [
    "arizona", "california", "connecticut", "delaware", "florida", "hawaii",
    "indiana", "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada", "new-jersey",
    "new-mexico", "new-york", "north-dakota", "ohio", "pennsylvania",
    "rhode-island", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west-virginia", "wisconsin", "wyoming",
]

FIELDS = [
    "cycle", "race_id", "state", "slug", "question",
    "days_out", "sample_date", "prob", "outcome", "volume", "token_id",
]

_last = [0.0]


def get_json(url, params=None):
    wait = DELAY - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()

    params = params or {}
    query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    full = f"{url}?{query}" if query else url
    try:
        req = Request(full, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        code = getattr(exc, "code", "")
        print(f"    failed{f' ({code})' if code else ''}: {exc}",
              file=sys.stderr)
        return None


def maybe_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def get_event(state):
    payload = get_json(GAMMA_EVENTS,
                       {"slug": f"{state}-us-senate-election-winner"})
    if isinstance(payload, list) and payload:
        return payload[0]
    return None


def democratic_market(event):
    """The market asking whether the Democrat wins.

    Matched on the question text, never position. 2024 events carry three
    markets (Democrat, Republican, and usually a third party), and their
    order is not guaranteed.
    """
    for m in event.get("markets", []) or []:
        q = str(m.get("question", "")).lower()
        if "democrat" in q:
            return m
    return None


def resolution(market):
    """1 if the Democrat won, 0 if not, None if unresolved.

    On a settled market outcomePrices collapse to 1 and 0, which is the
    result rather than a forecast.
    """
    outcomes = maybe_json(market.get("outcomes")) or []
    prices = maybe_json(market.get("outcomePrices")) or []
    if len(outcomes) != len(prices):
        return None
    for label, price in zip(outcomes, prices):
        if str(label).strip().lower() in ("yes", "democrat", "democrats"):
            try:
                p = float(price)
            except (TypeError, ValueError):
                return None
            if p > 0.99:
                return 1
            if p < 0.01:
                return 0
            return None       # not actually settled
    return None


def yes_token(market):
    ids = maybe_json(market.get("clobTokenIds")) or []
    outcomes = maybe_json(market.get("outcomes")) or []
    if not ids:
        return None
    for label, tid in zip(outcomes, ids):
        if str(label).strip().lower() == "yes":
            return tid
    return ids[0]


def price_history(token_id):
    """Full price path for a token, as [(timestamp, price)]."""
    payload = get_json(CLOB_HISTORY, {"market": token_id, "interval": "max",
                                      "fidelity": 60})
    if not isinstance(payload, dict):
        return []
    out = []
    for point in payload.get("history") or []:
        try:
            out.append((int(point["t"]), float(point["p"])))
        except (KeyError, TypeError, ValueError):
            continue
    out.sort()
    return out


def price_at(history, target: date):
    """Last price at or before a date. Returns None when the market had not
    started trading yet, rather than reaching forward to the first price it
    can find, which would leak information from after the target."""
    cutoff = int(datetime.combine(target, datetime.min.time()).timestamp())
    prior = [p for t, p in history if t <= cutoff]
    return prior[-1] if prior else None


# --------------------------------------------------------------------------

def inspect(state):
    event = get_event(state)
    if not event:
        print("no event returned")
        return 1
    print(f"title: {event.get('title')}")
    print(f"end:   {event.get('endDate')}\n")
    for m in event.get("markets", []) or []:
        print(f"  question:  {m.get('question')}")
        print(f"  outcomes:  {maybe_json(m.get('outcomes'))}")
        print(f"  prices:    {maybe_json(m.get('outcomePrices'))}")
        print(f"  tokenIds:  {str(maybe_json(m.get('clobTokenIds')))[:90]}")
        print(f"  volume:    {m.get('volumeNum') or m.get('volume')}")
        print(f"  closed:    {m.get('closed')}\n")

    dem = democratic_market(event)
    if dem:
        print(f"picked Democratic market: {dem.get('question')}")
        print(f"resolution: {resolution(dem)}")
        print(f"yes token:  {str(yes_token(dem))[:60]}")
    return 0


def probe_history(state="pennsylvania"):
    """Confirm the price history endpoint still serves settled markets."""
    event = get_event(state)
    dem = democratic_market(event) if event else None
    if not dem:
        print("could not find a market to probe")
        return 1

    token = yes_token(dem)
    print(f"token {str(token)[:50]}")
    hist = price_history(token)
    print(f"{len(hist)} price points")
    if not hist:
        print("\nNo history returned. Settled markets may not be served by")
        print("the CLOB endpoint, in which case the 2024 backtest needs a")
        print("third-party archive of historical prices.")
        return 1

    first = datetime.fromtimestamp(hist[0][0]).date()
    last = datetime.fromtimestamp(hist[-1][0]).date()
    print(f"spans {first} to {last}\n")
    for d in HORIZONS:
        target = ELECTION_2024 - timedelta(days=d)
        p = price_at(hist, target)
        print(f"  {d:>4}d out ({target}): "
              f"{f'{p:.3f}' if p is not None else 'no price yet'}")
    print(f"\n  resolution: {resolution(dem)}")
    return 0


def collect(states, dry_run):
    rows, missing = [], []

    for state in states:
        event = get_event(state)
        if not event:
            missing.append((state, "no event"))
            continue
        dem = democratic_market(event)
        if not dem:
            missing.append((state, "no Democratic market"))
            continue

        outcome = resolution(dem)
        if outcome is None:
            missing.append((state, "unresolved"))
            continue

        token = yes_token(dem)
        hist = price_history(token) if token else []
        if not hist:
            missing.append((state, "no price history"))
            continue

        got = 0
        for d in HORIZONS:
            target = ELECTION_2024 - timedelta(days=d)
            p = price_at(hist, target)
            if p is None:
                continue
            got += 1
            rows.append({
                "cycle": 2024,
                "race_id": f"2024-senate-{state}",
                "state": state,
                "slug": event.get("slug", ""),
                "question": dem.get("question", ""),
                "days_out": d,
                "sample_date": target.isoformat(),
                "prob": round(p, 4),
                "outcome": outcome,
                "volume": dem.get("volumeNum") or dem.get("volume") or "",
                "token_id": token,
            })
        print(f"  {state:<16} outcome={outcome}  {got}/{len(HORIZONS)} horizons")

    print(f"\n{len(rows)} observations across "
          f"{len({r['race_id'] for r in rows})} races")
    if missing:
        print(f"{len(missing)} skipped:")
        for state, why in missing:
            print(f"  {state:<16} {why}")

    if dry_run or not rows:
        print("\nnothing written")
        return 0 if rows else 1

    DATA.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {OUT.name}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inspect", metavar="STATE")
    ap.add_argument("--probe-history", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--states", nargs="*")
    args = ap.parse_args()

    if args.inspect:
        return inspect(args.inspect)
    if args.probe_history:
        return probe_history()
    return collect(args.states or STATES, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
