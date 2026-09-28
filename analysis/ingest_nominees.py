#!/usr/bin/env python3
"""
Collect 2028 presidential nominee prices from Kalshi and Polymarket.

Writes one row per (venue, candidate) to nominee_snapshots.csv. Prices are
stored raw; normalization across the field happens at render time so the
raw record stays auditable.

Usage:
    python3 ingest_nominees.py --dry-run
    python3 ingest_nominees.py
"""
import argparse
import csv
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA_BASE = "https://gamma-api.polymarket.com"
UA = "oddsvspolls/1.0 (+https://oddsvspolls.com)"

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "..", "public", "nominee_snapshots.csv")

# One entry per board. rules_must_contain guards against the NH-style
# mistake of pointing at the wrong market: every market's resolution text
# has to mention the right party's presidential nomination.
EVENTS = [
    # Winner board. The guard is inverted from the nominee boards: the
    # rules must mention the presidency and must NOT mention a
    # nomination, so a nominee contract can never land here.
    {
        "event_id": "2028-president",
        "party": "ANY",
        "kalshi_event": "KXPRESPERSON-28",
        "polymarket_slug": "presidential-election-winner-2028",
        "kalshi_rules_must_contain": ["presiden"],
        "kalshi_rules_must_not_contain": ["nominat"],
        "polymarket_rules_must_contain": ["presiden"],
        "polymarket_rules_must_not_contain": ["nominat"],
    },
    {
        "event_id": "2028-dem-nominee",
        "party": "DEM",
        "kalshi_event": "KXPRESNOMD-28",
        "polymarket_slug": "democratic-presidential-nominee-2028",
        "kalshi_rules_must_contain": "presidency for the democratic party",
        "polymarket_rules_must_contain": "nomination of the democratic party",
    },
    {
        "event_id": "2028-rep-nominee",
        "party": "REP",
        "kalshi_event": "KXPRESNOMR-28",
        "polymarket_slug": "republican-presidential-nominee-2028",
        "kalshi_rules_must_contain": "presidency for the republican party",
        "polymarket_rules_must_contain": "nomination of the republican party",
    },
]

# Map venue-specific spellings onto one key. Left side is the normalized
# form of what a venue prints; right side is the canonical key. Add entries
# as check output shows mismatches.
ALIASES = {
    "j d vance": "jd vance",
    "aoc": "alexandria ocasio cortez",
    "donald j trump": "donald trump",
    "donald j trump jr": "donald trump jr",
    "dwayne the rock johnson": "dwayne johnson",
}

FIELDS = [
    "ts_utc", "event_id", "venue", "candidate_key", "candidate_label",
    "venue_id", "yes_bid", "yes_ask", "last", "price", "price_source",
]


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def candidate_key(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    s = re.sub(r"\s+", " ", s).strip()
    return ALIASES.get(s, s)


def rules_ok(rules, ev, venue):
    """Every must_contain phrase has to appear, and no must_not_contain phrase."""
    need = ev[f"{venue}_rules_must_contain"]
    need = [need] if isinstance(need, str) else need
    avoid = ev.get(f"{venue}_rules_must_not_contain", [])
    return all(n in rules for n in need) and not any(a in rules for a in avoid)


def to_float(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pick_price(bid, ask, last):
    """Midpoint when there's a real two-sided book, else last trade."""
    if bid is not None and ask is not None and 0 < bid <= ask and ask - bid <= 0.10:
        return (bid + ask) / 2, "mid"
    if last is not None and last > 0:
        return last, "last"
    return None, None


def fetch_kalshi(ev):
    url = f"{KALSHI_BASE}/events/{ev['kalshi_event']}?with_nested_markets=true"
    data = get_json(url)
    markets = (data.get("event") or {}).get("markets") or data.get("markets") or []
    if not markets:
        raise ValueError(f"no markets under {ev['kalshi_event']}")
    rows = []
    for m in markets:
        if m.get("status") != "active":
            continue
        rules = (m.get("rules_primary") or "").lower()
        if not rules_ok(rules, ev, "kalshi"):
            raise ValueError(f"{m.get('ticker')} rules don't match "
                             f"{ev['party']} nomination: {rules[:120]}")
        label = (m.get("custom_strike") or {}).get("Candidate") or m.get("yes_sub_title")
        bid = to_float(m.get("yes_bid_dollars"))
        ask = to_float(m.get("yes_ask_dollars"))
        last = to_float(m.get("last_price_dollars"))
        price, src = pick_price(bid, ask, last)
        rows.append({
            "venue": "kalshi", "candidate_label": label,
            "candidate_key": candidate_key(label), "venue_id": m.get("ticker"),
            "yes_bid": bid, "yes_ask": ask, "last": last,
            "price": price, "price_source": src,
        })
    return rows


def fetch_polymarket(ev):
    url = f"{GAMMA_BASE}/events?slug={ev['polymarket_slug']}"
    data = get_json(url)
    events = data if isinstance(data, list) else [data]
    if not events or not events[0].get("markets"):
        raise ValueError(f"slug not found: {ev['polymarket_slug']} "
                         "(check with the public-search curl)")
    rows = []
    for m in events[0]["markets"]:
        if m.get("closed") or not m.get("active"):
            continue
        rules = (m.get("description") or "").lower()
        if not rules_ok(rules, ev, "polymarket"):
            raise ValueError(f"{m.get('slug')} rules don't match "
                             f"{ev['party']} nomination: {rules[:120]}")
        label = m.get("groupItemTitle") or m.get("question")
        outcomes = json.loads(m.get("outcomes") or "[]")
        prices = json.loads(m.get("outcomePrices") or "[]")
        yes_idx = outcomes.index("Yes") if "Yes" in outcomes else 0
        quoted = to_float(prices[yes_idx]) if prices else None
        bid = to_float(m.get("bestBid"))
        ask = to_float(m.get("bestAsk"))
        last = to_float(m.get("lastTradePrice"))
        price, src = pick_price(bid, ask, last)
        if price is None and quoted is not None:
            price, src = quoted, "quoted"
        rows.append({
            "venue": "polymarket", "candidate_label": label,
            "candidate_key": candidate_key(label), "venue_id": m.get("slug"),
            "yes_bid": bid, "yes_ask": ask, "last": last,
            "price": price, "price_source": src,
        })
    return rows


def fmt(v):
    return "" if v is None else (f"{v:.4f}" if isinstance(v, float) else v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--min-price", type=float, default=0.005,
                    help="drop candidates below this on every venue")
    args = ap.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_rows, failures = [], 0

    for ev in EVENTS:
        venue_rows = []
        for name, fn in (("kalshi", fetch_kalshi), ("polymarket", fetch_polymarket)):
            try:
                venue_rows += fn(ev)
            except (urllib.error.URLError, ValueError, KeyError) as e:
                failures += 1
                print(f"{ev['event_id']:18} {name:11} FAILED: {e}", file=sys.stderr)

        # Keep a candidate if any venue has them at or above the floor, so a
        # name that's live on one venue still shows up on both.
        best = {}
        for r in venue_rows:
            p = r["price"] or 0
            best[r["candidate_key"]] = max(best.get(r["candidate_key"], 0), p)
        keep = {k for k, p in best.items() if p >= args.min_price}

        kept = [r for r in venue_rows if r["candidate_key"] in keep]
        for r in kept:
            r.update(ts_utc=ts, event_id=ev["event_id"])
        all_rows += kept

        # Name-matching report: anyone above the floor on one venue only.
        venues_by_key = {}
        for r in kept:
            venues_by_key.setdefault(r["candidate_key"], set()).add(r["venue"])
        for k in sorted(keep):
            if len(venues_by_key.get(k, ())) == 1:
                only = next(iter(venues_by_key[k]))
                print(f"{ev['event_id']:18} one venue only ({only}): {k} "
                      f"(add to ALIASES if it's a spelling mismatch)")

        top = sorted(kept, key=lambda r: -(r["price"] or 0))[:12]
        for r in top:
            print(f"{ev['event_id']:18} {r['venue']:11} {fmt(r['price']):>7} "
                  f"{r['price_source'] or '':6} {r['candidate_label']}")
        sums = {}
        for r in venue_rows:
            sums[r["venue"]] = sums.get(r["venue"], 0) + (r["price"] or 0)
        print(f"{ev['event_id']:18} field sums: "
              + ", ".join(f"{v}={s:.3f}" for v, s in sorted(sums.items())))
        print()

    if args.dry_run:
        print(f"dry run, nothing written. {len(all_rows)} rows, {failures} failures")
        return 1 if failures else 0

    if not all_rows:
        print("no rows collected, nothing written", file=sys.stderr)
        return 1

    out = os.path.abspath(args.out)
    new_file = not os.path.exists(out)
    with open(out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        for r in all_rows:
            w.writerow({k: fmt(r.get(k)) for k in FIELDS})
    print(f"wrote {len(all_rows)} rows to {out}, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
