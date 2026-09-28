#!/usr/bin/env python3
"""
One-time backfill of daily nominee prices from Kalshi and Polymarket,
from market open up to the day before the first live snapshot.

    python3 backfill_nominees.py --dry-run   # fetch and report, write nothing
    python3 backfill_nominees.py             # write, commit and push

Writes public/nominee_backfill.csv with the same columns as
nominee_snapshots.csv. Rerunning replaces the file, so it's safe to repeat.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
from datetime import datetime, timezone

import ingest_nominees as ing

CLOB_BASE = "https://clob.polymarket.com"
HERE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(HERE, "..", "public")
LIVE = os.path.join(PUBLIC, "nominee_snapshots.csv")
OUT = os.path.join(PUBLIC, "nominee_backfill.csv")

HIST_FLOOR = 0.02   # keep a faded candidate if they ever traded at 2%+
PAUSE = 0.15        # be polite to both APIs


def iso_to_ts(s):
    s = s.replace("Z", "+00:00")
    if "." in s:
        head, rest = s.split(".", 1)
        tz = rest[rest.index("+"):] if "+" in rest else ""
        s = head + tz
    return int(datetime.fromisoformat(s).timestamp())


def day_of(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def first_live_day():
    if not os.path.exists(LIVE):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(LIVE) as f:
        days = [r["ts_utc"][:10] for r in csv.DictReader(f) if r.get("ts_utc")]
    return min(days) if days else datetime.now(timezone.utc).strftime("%Y-%m-%d")


def dollars(obj, field):
    """Kalshi candle sub-objects use close_dollars (string) or close (cents)."""
    if not isinstance(obj, dict):
        return None
    v = obj.get(f"{field}_dollars")
    if v not in (None, ""):
        return ing.to_float(v)
    v = obj.get(field)
    if v in (None, ""):
        return None
    return float(v) / 100   # legacy fields are always cents


# ---------------- Kalshi ----------------

def kalshi_markets(ev):
    data = ing.get_json(f"{ing.KALSHI_BASE}/events/{ev['kalshi_event']}"
                        "?with_nested_markets=true")
    return (data.get("event") or {}).get("markets") or data.get("markets") or []


def kalshi_history(ev, m, now):
    series = ev["kalshi_event"].split("-")[0]
    start = iso_to_ts(m.get("open_time") or m.get("created_time"))
    url = (f"{ing.KALSHI_BASE}/series/{series}/markets/{m['ticker']}/candlesticks"
           f"?start_ts={start}&end_ts={now}&period_interval=1440")
    candles = ing.get_json(url).get("candlesticks") or []
    points = {}
    for c in candles:
        ts = c.get("end_period_ts")
        if ts is None:
            continue
        bid = dollars(c.get("yes_bid"), "close")
        ask = dollars(c.get("yes_ask"), "close")
        last = dollars(c.get("price"), "close")
        price, src = ing.pick_price(bid, ask, last)
        if price is None:
            continue
        points[day_of(ts)] = (bid, ask, last, price, src)
    if candles and not points:
        raise ValueError("candles came back but none parsed; first candle:\n"
                         + json.dumps(candles[0], indent=2)[:1500])
    return points


# ---------------- Polymarket ----------------

def poly_markets(ev):
    data = ing.get_json(f"{ing.GAMMA_BASE}/events?slug={ev['polymarket_slug']}")
    events = data if isinstance(data, list) else [data]
    return events[0].get("markets") or [] if events else []


def poly_history(m, now):
    outcomes = json.loads(m.get("outcomes") or "[]")
    tokens = json.loads(m.get("clobTokenIds") or "[]")
    if not tokens:
        return {}
    token = tokens[outcomes.index("Yes") if "Yes" in outcomes else 0]
    try:
        url = f"{CLOB_BASE}/prices-history?market={token}&interval=max&fidelity=1440"
        hist = ing.get_json(url).get("history") or []
        if not hist:
            # interval=max sometimes comes back empty; retry with explicit bounds
            start = iso_to_ts(m.get("startDate") or m.get("createdAt"))
            url = (f"{CLOB_BASE}/prices-history?market={token}"
                   f"&startTs={start}&endTs={now}&fidelity=1440")
            hist = ing.get_json(url).get("history") or []
    except urllib.error.HTTPError as e:
        # A contract that has never traded has no history, and the endpoint
        # answers 400 rather than an empty list.
        if e.code == 400:
            return {}
        raise
    points = {}
    for h in hist:
        p = ing.to_float(h.get("p"))
        if h.get("t") is None or p is None:
            continue
        points[day_of(int(h["t"]))] = (None, None, p, p, "history")
    return points


# Polymarket pre-creates unnamed slots ("Person X", "Another Person") so
# candidates can be added later. They never trade, so skip them up front.
PLACEHOLDER = re.compile(r"^(person [a-z]{1,2}|another person|other)$", re.I)


def is_placeholder(m, label):
    slug = m.get("slug") or ""
    return (PLACEHOLDER.match((label or "").strip()) is not None
            or slug.startswith("will-person-")
            or slug.startswith("will-another-person-"))


# ---------------- main ----------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now = int(time.time())
    cutoff = first_live_day()
    print(f"backfilling up to (not including) {cutoff}\n")

    rows, failures, placeholders = [], 0, 0
    for ev in ing.EVENTS:
        series = []  # (venue, label, key, venue_id, points)

        try:
            for m in kalshi_markets(ev):
                label = (m.get("custom_strike") or {}).get("Candidate") or m.get("yes_sub_title")
                try:
                    pts = kalshi_history(ev, m, now)
                    series.append(("kalshi", label, ing.candidate_key(label), m["ticker"], pts))
                except (urllib.error.URLError, ValueError) as e:
                    failures += 1
                    print(f"  kalshi {m.get('ticker')}: {e}", file=sys.stderr)
                time.sleep(PAUSE)
        except urllib.error.URLError as e:
            failures += 1
            print(f"{ev['event_id']} kalshi event fetch FAILED: {e}", file=sys.stderr)

        try:
            for m in poly_markets(ev):
                label = m.get("groupItemTitle") or m.get("question")
                if is_placeholder(m, label):
                    placeholders += 1
                    continue
                try:
                    pts = poly_history(m, now)
                    series.append(("polymarket", label, ing.candidate_key(label), m.get("slug"), pts))
                except (urllib.error.URLError, ValueError) as e:
                    failures += 1
                    print(f"  polymarket {m.get('slug')}: {e}", file=sys.stderr)
                time.sleep(PAUSE)
        except urllib.error.URLError as e:
            failures += 1
            print(f"{ev['event_id']} polymarket event fetch FAILED: {e}", file=sys.stderr)

        # Keep a candidate if they ever hit the floor on either venue.
        peak = {}
        for venue, label, key, vid, pts in series:
            for day, p in pts.items():
                if day < cutoff:
                    peak[key] = max(peak.get(key, 0), p[3])
        keep = {k for k, p in peak.items() if p >= HIST_FLOOR}

        counts = {}
        for venue, label, key, vid, pts in series:
            if key not in keep:
                continue
            for day, (bid, ask, last, price, src) in sorted(pts.items()):
                if day >= cutoff:
                    continue
                rows.append({
                    "ts_utc": f"{day}T23:59:59Z", "event_id": ev["event_id"],
                    "venue": venue, "candidate_key": key, "candidate_label": label,
                    "venue_id": vid, "yes_bid": bid, "yes_ask": ask, "last": last,
                    "price": price, "price_source": f"backfill_{src}",
                })
                counts[venue] = counts.get(venue, 0) + 1

        days = sorted({r["ts_utc"][:10] for r in rows if r["event_id"] == ev["event_id"]})
        span = f"{days[0]} to {days[-1]}" if days else "no data"
        print(f"{ev['event_id']:18} {len(keep)} candidates, {span}, "
              + ", ".join(f"{v}={n} rows" for v, n in sorted(counts.items())))
        top = sorted(peak.items(), key=lambda kv: -kv[1])[:8]
        print("  peak prices: " + ", ".join(f"{k} {p:.0%}" for k, p in top))
        print()

    print(f"skipped {placeholders} unnamed Polymarket placeholder slots\n")
    if args.dry_run:
        print(f"dry run, nothing written. {len(rows)} rows, {failures} failures")
        return 1 if failures else 0

    if not rows:
        print("no rows collected, nothing written", file=sys.stderr)
        return 1
    if failures:
        print(f"{failures} failures; not writing a partial backfill. Paste the errors.",
              file=sys.stderr)
        return 1

    rows.sort(key=lambda r: (r["event_id"], r["ts_utc"], r["venue"], r["candidate_key"]))
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ing.FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ing.fmt(r.get(k)) for k in ing.FIELDS})
    size = os.path.getsize(OUT) / 1e6
    print(f"wrote {len(rows)} rows ({size:.1f} MB) to {os.path.abspath(OUT)}\n")

    root = os.path.abspath(os.path.join(HERE, ".."))
    rel = os.path.relpath(OUT, root)
    for cmd in (["git", "add", rel],
                ["git", "commit", "-m", "Backfill 2028 nominee price history"],
                ["git", "pull", "--rebase", "--autostash", "origin", "main"],
                ["git", "push", "origin", "main"]):
        print("$ " + " ".join(cmd))
        if subprocess.run(cmd, cwd=root).returncode != 0:
            print("git step failed; the CSV is written, paste the output.", file=sys.stderr)
            return 1
    print("\nBackfill committed and pushed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
