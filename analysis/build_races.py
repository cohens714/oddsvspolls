"""
Build races.json entries by discovering Polymarket slugs per state.

    python3 build_races.py                    # print config for default states
    python3 build_races.py --write            # overwrite races.json
    python3 build_races.py --states GA MI NC  # just these

Standard library only.

Polymarket names state events '<state>-senate-election-winner' and puts a
separate binary market inside for each party. This finds the Democratic
market by matching the question text, never by position, and reports states
where no market exists yet so you know what is missing rather than silently
shipping a short list.

Rerun this when new races list. It preserves nothing, so any hand edits to
races.json will be lost with --write. Check the diff before committing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"
USER_AGENT = "oddsvspolls.com data collector (+https://oddsvspolls.com)"
TIMEOUT = 30
OUT = Path(__file__).resolve().parent / "races.json"

ELECTION_DATE = "2026-11-03"
CYCLE = 2026

# Competitive 2026 Senate seats. Toss-ups and lean races carry the
# information; safe seats sit at 0.99 all cycle and tell you nothing about
# whether markets or polls are better calibrated. Add safe seats only if you
# specifically want to measure overconfidence at the tails.
DEFAULT_STATES = {
    "GA": "georgia",
    "MI": "michigan",
    "NC": "north-carolina",
    "ME": "maine",
    "OH": "ohio",
    "TX": "texas",
    "IA": "iowa",
    "NH": "new-hampshire",
    "MN": "minnesota",
    "AK": "alaska",
    "NE": "nebraska",
    "KS": "kansas",
}

# Control markets, which live under their own event slugs.
CONTROL = [
    {
        "race_id": "2026-senate-control",
        "office": "senate-control",
        "state": None,
        "event_slug": "which-party-will-win-the-senate-in-2026",
        "match": "democratic party control the senate",
    },
    {
        "race_id": "2026-house-control",
        "office": "house-control",
        "state": None,
        "event_slug": "which-party-will-win-the-house-in-2026",
        "match": "democratic party control the house",
    },
]


def get_json(url, params=None):
    params = params or {}
    query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    full = f"{url}?{query}" if query else url
    try:
        req = Request(full, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"    request failed: {exc}", file=sys.stderr)
        return None


def find_market(event_slug: str, needle: str):
    """Return (market_slug, price) for the market whose question contains
    needle. Returns None if the event or market does not exist."""
    payload = get_json(GAMMA_EVENTS, {"slug": event_slug})
    if not isinstance(payload, list) or not payload:
        return None

    for ev in payload:
        for m in ev.get("markets", []) or []:
            question = str(m.get("question", "")).lower()
            if needle not in question:
                continue
            if m.get("closed"):
                continue
            prices = m.get("outcomePrices")
            if isinstance(prices, str):
                try:
                    prices = json.loads(prices)
                except json.JSONDecodeError:
                    prices = None
            # Placeholder markets ("Will Person A win...") carry no prices.
            if not prices:
                continue
            return m.get("slug"), float(prices[0])
    return None


def build(states: dict) -> tuple[list, list]:
    found, missing = [], []

    for spec in CONTROL:
        print(f"  {spec['race_id']:<22}", end=" ")
        hit = find_market(spec["event_slug"], spec["match"])
        if not hit:
            print("not found")
            missing.append(spec["race_id"])
            continue
        slug, price = hit
        print(f"{price:.3f}")
        found.append({
            "race_id": spec["race_id"],
            "office": spec["office"],
            "state": spec["state"],
            "yes_side": "DEM",
            "polymarket_slug": slug,
            "polymarket_outcome": "Yes",
            "kalshi_ticker": None,
            "kalshi_yes_means": "DEM",
        })

    for code, name in states.items():
        race_id = f"{CYCLE}-senate-{code}"
        print(f"  {race_id:<22}", end=" ")
        hit = find_market(f"{name}-senate-election-winner",
                          f"democrats win the {name.replace('-', ' ')} senate")
        if not hit:
            print("not found")
            missing.append(race_id)
            continue
        slug, price = hit
        print(f"{price:.3f}")
        found.append({
            "race_id": race_id,
            "office": "senate",
            "state": code,
            "yes_side": "DEM",
            "polymarket_slug": slug,
            "polymarket_outcome": "Yes",
            "kalshi_ticker": None,
            "kalshi_yes_means": "DEM",
        })

    return found, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="overwrite races.json")
    ap.add_argument("--states", nargs="*", help="state codes, e.g. GA MI NC")
    args = ap.parse_args()

    states = DEFAULT_STATES
    if args.states:
        unknown = [s for s in args.states if s.upper() not in DEFAULT_STATES]
        if unknown:
            ap.error(f"unknown state codes: {unknown}. Add them to "
                     f"DEFAULT_STATES with their Polymarket slug name.")
        states = {s.upper(): DEFAULT_STATES[s.upper()] for s in args.states}

    print(f"Discovering {len(states)} states plus {len(CONTROL)} control markets\n")
    found, missing = build(states)

    print(f"\n{len(found)} found, {len(missing)} missing")
    if missing:
        print("  missing: " + ", ".join(missing))
        print("  (not every race has a listed market yet; rerun later)")

    config = {
        "_comment": [
            "Generated by build_races.py. Rerun that script to refresh, but",
            "note --write discards hand edits, including any kalshi_ticker",
            "values you have filled in. Check the diff before committing.",
            "",
            "Polymarket uses separate binary markets per party, so",
            "polymarket_outcome is always 'Yes' and the slug names the party.",
            "yes_side is the outcome every venue's probability must refer to.",
        ],
        "cycle": CYCLE,
        "election_date": ELECTION_DATE,
        "races": found,
    }

    if not args.write:
        print("\n--- races.json (not written, pass --write) ---")
        print(json.dumps(config, indent=2)[:1200] + "\n...")
        return 0

    if not found:
        print("\nnothing found, refusing to overwrite races.json", file=sys.stderr)
        return 1

    OUT.write_text(json.dumps(config, indent=2) + "\n")
    print(f"\nwrote {OUT.name} with {len(found)} races")
    return 0


if __name__ == "__main__":
    sys.exit(main())
