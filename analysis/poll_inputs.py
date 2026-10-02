"""
Publish the polls behind each race's current polling average.

    python3 poll_inputs.py                          # -> data/poll_inputs.json
    python3 poll_inputs.py --race 2026-senate-SC    # also print one race

Standard library only. Reads raw_polls.csv and poll_averages.csv and writes,
for every race, the polls inside the current averaging window along with each
poll's share of the total weight, so a reader can see why a number is what
it is.

The window, weights and average are imported from the live collector rather
than reimplemented, and each race's average is recomputed from the listed
polls and checked against the published figure. A race whose list would not
reproduce the number on the site is left out, because a table that does not
add up is worse than no table at all.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
RAW_IN = DATA / "raw_polls.csv"
AVG_IN = DATA / "poll_averages.csv"
OUT = DATA / "poll_inputs.json"

sys.path.insert(0, str(HERE))
from fetch_polls_votehub import (WINDOW_DAYS, HALF_LIFE_DAYS,  # noqa: E402
                                 weight, average)

TOLERANCE = 0.05          # points; averages are stored rounded to 0.01
SKIP = {"2026-generic-ballot"}


def load_raw():
    """Same validity rule as replay_polls.load_raw, plus display fields."""
    by_race = {}
    with RAW_IN.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                margin = float(r["margin"])
                dem = float(r["dem_pct"])
                rep = float(r["rep_pct"])
                date.fromisoformat(r["end_date"])
            except (KeyError, TypeError, ValueError):
                continue
            by_race.setdefault(r["race_id"], []).append({
                "pollster": r.get("pollster", ""),
                "sponsors": r.get("sponsors", ""),
                "start_date": r.get("start_date", ""),
                "end_date": r["end_date"],
                "sample_size": r.get("sample_size", ""),
                "population": r.get("population", ""),
                "dem_pct": dem,
                "rep_pct": rep,
                "margin": margin,
                "partisan": r.get("partisan", ""),
                "url": r.get("url", ""),
            })
    return by_race


def load_latest_averages():
    """The most recent published average for each race."""
    latest = {}
    with AVG_IN.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rid, d = r.get("race_id"), r.get("as_of_date")
            if not rid or not d:
                continue
            if rid not in latest or d >= latest[rid]["as_of_date"]:
                latest[rid] = r
    return latest


def _sample(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def inputs_for(polls, as_of: date, drop_partisan: bool):
    """The polls average() uses on as_of, with weight shares.

    The window test mirrors average(). If average() ever changes, the
    recompute check in main() catches the drift and the race is left out.
    """
    cutoff = as_of - timedelta(days=WINDOW_DAYS)
    live = [p for p in polls
            if cutoff <= date.fromisoformat(p["end_date"]) <= as_of]
    if drop_partisan:
        live = [p for p in live if not p["partisan"]]
    weights = [weight(p, as_of) for p in live]
    total = sum(weights)
    if total <= 0:
        return []
    rows = []
    for p, w in zip(live, weights):
        rows.append({
            "pollster": p["pollster"],
            "sponsors": p["sponsors"],
            "start_date": p["start_date"],
            "end_date": p["end_date"],
            "sample_size": _sample(p["sample_size"]),
            "population": p["population"],
            "dem_pct": p["dem_pct"],
            "rep_pct": p["rep_pct"],
            "margin": p["margin"],
            "partisan": p["partisan"],
            "url": p["url"],
            "weight_share": round(w / total, 4),
        })
    rows.sort(key=lambda r: (r["end_date"], r["weight_share"]), reverse=True)
    return rows


def show(race_id, entry):
    print(f"\n{race_id}  average {entry['margin']:+.2f} as of {entry['as_of']}")
    for p in entry["polls"]:
        tag = f" [{p['partisan']}]" if p["partisan"] else ""
        print(f"  {p['end_date']}  {p['pollster'][:30]:<30}{tag:<6} "
              f"{p['dem_pct']:>5.1f}-{p['rep_pct']:<5.1f} "
              f"{p['margin']:+6.1f}  {p['weight_share'] * 100:5.1f}% of weight")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--race", help="print the inputs for one race")
    args = ap.parse_args()

    for path in (RAW_IN, AVG_IN):
        if not path.exists():
            print(f"{path} not found; run fetch_polls_votehub.py first",
                  file=sys.stderr)
            return 1

    by_race = load_raw()
    latest = load_latest_averages()

    races, left_out = {}, []
    for rid, row in sorted(latest.items()):
        if rid in SKIP:
            continue
        try:
            published = float(row["margin"])
            as_of = date.fromisoformat(row["as_of_date"])
        except (KeyError, TypeError, ValueError):
            continue
        drop = str(row.get("excluded_partisan", "")).strip().lower() == "true"
        polls = by_race.get(rid, [])
        rows = inputs_for(polls, as_of, drop)
        check = average(polls, as_of, drop) if polls else None

        if (not rows or not check or check["n_polls"] != len(rows)
                or abs(check["margin"] - published) > TOLERANCE):
            got = f"{check['margin']:+.2f}" if check else "nothing"
            print(f"  WARNING {rid}: listed polls give {got}, published "
                  f"average is {published:+.2f}; left out")
            left_out.append(rid)
            continue

        races[rid] = {"as_of": as_of.isoformat(), "margin": published,
                      "n_polls": len(rows), "polls": rows}
        n_part = sum(1 for p in rows if p["partisan"])
        print(f"  {rid:<24} {published:+6.2f}  {len(rows):>2} polls"
              f"{f'  ({n_part} partisan)' if n_part else ''}  verified")

    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_days": WINDOW_DAYS,
        "half_life_days": HALF_LIFE_DAYS,
        "races": races,
        "left_out": left_out,
    }
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"\nwrote {OUT.name}: {len(races)} races verified, "
          f"{len(left_out)} left out")

    if args.race:
        if args.race in races:
            show(args.race, races[args.race])
        else:
            print(f"\n{args.race} not in the output", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
