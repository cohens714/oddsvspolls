"""
Find races the two-party framing cannot describe.

    python3 check_two_party.py

Standard library only. Reads races.json, checks each configured market
against its siblings on Polymarket.

WHY THIS EXISTS
---------------
Nebraska passed every check this project has: a valid market, an active
contract, a plausible price, matching tickers across two venues. It
displayed as "Republican >99%" because the site reads the Democratic
market and flips it.

The market's actual view was Republicans 69%, an independent 29.5%,
Democrats 0.2%. So the page said the Republican was certain while the
market thought there was nearly a one-in-three chance they lose.

Nothing caught it because every check assumes the two named parties are
the only outcomes. When they are not, P(Democrat) carries no information
about P(Republican), and flipping one to get the other is simply wrong.

THE TEST
--------
On a genuine two-way race the Democratic and Republican prices sum to
about 1, allowing a little for spread and for negligible third options.
A sum well below 1 means real money is on somebody else, and the race
should either be dropped or represented differently.

This is purely structural: it needs no knowledge of who is running, which
is what makes it worth automating.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
RACES_JSON = HERE / "races.json"
GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"

# Below this, the two parties do not account for the outcome and something
# else has real support. Set loosely: spreads and dust options mean a clean
# two-way race sums to roughly 0.98-1.02, so 0.90 flags only real cases.
TWO_PARTY_FLOOR = 0.90

# A third option above this is worth naming even when the pair still sums
# close to 1, since it may be growing.
THIRD_NOTICE = 0.05

DELAY = 0.3
_last = [0.0]


def get(url, params):
    wait = DELAY - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    try:
        req = Request(f"{url}?{query}",
                      headers={"User-Agent": "oddsvspolls.com structure check"})
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return None


def maybe_json(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return v


def yes_price(market):
    outcomes = maybe_json(market.get("outcomes")) or []
    prices = maybe_json(market.get("outcomePrices")) or []
    for label, p in zip(outcomes, prices):
        if str(label).strip().lower() == "yes":
            try:
                return float(p)
            except (TypeError, ValueError):
                return None
    return None


def event_for(slug):
    """The event a market belongs to, so its siblings can be read."""
    payload = get(GAMMA_MARKETS, {"slug": slug})
    if not isinstance(payload, list) or not payload:
        return None
    market = payload[0]

    # Gamma returns the parent event inline on some responses.
    events = market.get("events") or []
    if events and events[0].get("slug"):
        return get(GAMMA_EVENTS, {"slug": events[0]["slug"]})

    # Otherwise derive it: market slugs end with the race, events name it.
    for suffix in ("-election-winner", "-winner-2026"):
        for word in ("senate", "governor"):
            if f"-{word}-race-in-2026" in slug:
                state = slug.split(f"-{word}-race-in-2026")[0]
                state = state.split("-win-the-")[-1]
                ev = get(GAMMA_EVENTS,
                         {"slug": f"{state}-{word}{suffix}"})
                if isinstance(ev, list) and ev:
                    return ev
    return None


def main():
    cfg = json.loads(RACES_JSON.read_text())
    races = cfg["races"]
    print(f"checking {len(races)} races for third-candidate support\n")

    flagged, noticed, unchecked = [], [], []

    for race in races:
        race_id = race["race_id"]
        slug = race.get("polymarket_slug")
        if not slug:
            unchecked.append((race_id, "no Polymarket slug"))
            continue

        ev = event_for(slug)
        if not isinstance(ev, list) or not ev:
            unchecked.append((race_id, "event not found"))
            continue

        # Keep every priced option, including near-zero ones. An earlier
        # version dropped anything under 0.01 as dust, which discarded
        # Nebraska's Democrat at 0.002 and so lost the very asymmetry the
        # check exists to find: with the Democratic market filtered out the
        # party lookup failed, the code fell back to summing the top two
        # options, and the Republican plus the independent summed to 0.985,
        # reporting a three-way race as a healthy two-way one.
        options = []
        for m in ev[0].get("markets", []) or []:
            p = yes_price(m)
            if p is None:
                continue
            q = str(m.get("question", ""))
            options.append((q, p))

        if not options:
            unchecked.append((race_id, "no priced options"))
            continue

        dem = next((p for q, p in options if "democrat" in q.lower()), None)
        rep = next((p for q, p in options if "republican" in q.lower()), None)

        # Candidate-listed races have no party questions; sum everything
        # priced and look for a third with real support instead.
        if dem is None or rep is None:
            # No party questions, so this race is listed per candidate.
            # Summing the top two is the closest equivalent: if the leading
            # pair does not account for the outcome, someone else does.
            ranked = sorted(options, key=lambda o: -o[1])
            top_two = sum(p for _, p in ranked[:2])
            third = ranked[2][1] if len(ranked) > 2 else 0.0
            label = "candidate-listed"
        else:
            top_two = dem + rep
            third = max((p for q, p in options
                         if "democrat" not in q.lower()
                         and "republican" not in q.lower()), default=0.0)
            label = "party-listed"

        detail = (f"D {dem:.3f} R {rep:.3f}" if dem is not None
                  and rep is not None else label)

        if top_two < TWO_PARTY_FLOOR:
            flagged.append((race_id, label, top_two, third, options))
            print(f"  FLAG  {race_id:<22} {top_two:.3f}   {detail}")
            for q, p in sorted(options, key=lambda o: -o[1])[:4]:
                if p >= 0.005:
                    print(f"          {p:.3f}  {q[:62]}")
        elif third >= THIRD_NOTICE:
            noticed.append((race_id, third))
            print(f"  note  {race_id:<22} {top_two:.3f}   "
                  f"third option at {third:.3f}")
        else:
            print(f"  ok    {race_id:<22} {top_two:.3f}   {detail}")

    print(f"\n{len(flagged)} flagged, {len(noticed)} worth watching, "
          f"{len(unchecked)} unchecked")

    if flagged:
        print("\nFlagged races cannot be described by a two-party "
              "probability.\nThe site reads P(Democrat) and flips it to get "
              "P(Republican), which\nis wrong when a third candidate holds "
              "real support. Remove them from\nraces.json and from the state "
              "lists in build_races.py.")
        for race_id, *_ in flagged:
            print(f"  {race_id}")

    if unchecked:
        print("\nCould not check:")
        for race_id, why in unchecked:
            print(f"  {race_id:<22} {why}")

    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
