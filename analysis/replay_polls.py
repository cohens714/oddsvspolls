"""
Recompute poll averages as they would have stood on past dates.

    python3 replay_polls.py --dry-run
    python3 replay_polls.py                 # -> data/poll_history.csv

Standard library only. Reads data/raw_polls.csv, writes a weekly series of
poll averages and probabilities for every race.

WHY REPLAY RATHER THAN LOG
--------------------------
poll_averages.csv only grows from the day the collector first ran, so it
holds a couple of days. But raw_polls.csv carries every poll with its field
dates, and the averaging rule is a pure function of the polls available on a
given date. So the whole back series can be reconstructed.

The one thing that must not slip: on any given date only polls whose field
work had FINISHED by then may be included. Including a poll published later
would let the chart show the polling average reacting to news before the
news happened, and the resulting comparison against market prices would be
meaningless in a way that is very hard to spot afterwards.

The averaging parameters are imported from the live collector rather than
copied, so the replayed history and the live figures cannot drift apart.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
RAW_IN = DATA / "raw_polls.csv"
OUT = DATA / "poll_history.csv"

sys.path.insert(0, str(HERE))
from fetch_polls_votehub import (WINDOW_DAYS, weight, average,  # noqa: E402
                                 ELECTION_DATE)
from to_probability import to_probability  # noqa: E402

STEP_DAYS = 7

FIELDS = [
    "race_id", "as_of_date", "days_out", "margin", "prob",
    "sigma", "n_polls", "effective_n", "n_partisan",
]


def load_raw():
    if not RAW_IN.exists():
        print(f"{RAW_IN} not found; run fetch_polls_votehub.py first",
              file=sys.stderr)
        return {}
    by_race = {}
    with RAW_IN.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                float(r["margin"]), float(r["dem_pct"]), float(r["rep_pct"])
                date.fromisoformat(r["end_date"])
            except (KeyError, TypeError, ValueError):
                continue
            by_race.setdefault(r["race_id"], []).append({
                "end_date": r["end_date"],
                "margin": float(r["margin"]),
                "dem_pct": float(r["dem_pct"]),
                "rep_pct": float(r["rep_pct"]),
                "sample_size": r.get("sample_size", ""),
                "partisan": r.get("partisan", ""),
            })
    return by_race


def replay(polls, step=STEP_DAYS):
    """Weekly averages from the first poll to today.

    Starts one window after the earliest poll so the first point is
    computed from a full window rather than a single survey.
    """
    if not polls:
        return []

    dates = sorted(date.fromisoformat(p["end_date"]) for p in polls)
    start = dates[0] + timedelta(days=WINDOW_DAYS)
    today = datetime.utcnow().date()
    if start > today:
        start = dates[0]

    out, cursor = [], start
    while cursor <= today:
        # Only polls already finished on this date. Anything later has not
        # happened yet from the perspective of this point on the chart.
        visible = [p for p in polls
                   if date.fromisoformat(p["end_date"]) <= cursor]
        avg = average(visible, cursor, drop_partisan=False)
        if avg:
            days_out = (ELECTION_DATE - cursor).days
            prob, sigma, *_ = to_probability(
                avg["margin"], days_out, avg["effective_n"])
            out.append({
                "as_of_date": cursor.isoformat(),
                "days_out": days_out,
                "margin": avg["margin"],
                "prob": round(prob, 4),
                "sigma": round(sigma, 2),
                "n_polls": avg["n_polls"],
                "effective_n": avg["effective_n"],
                "n_partisan": avg["n_partisan"],
            })
        cursor += timedelta(days=step)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--step", type=int, default=STEP_DAYS)
    args = ap.parse_args()

    by_race = load_raw()
    if not by_race:
        return 1

    rows = []
    for race_id, polls in sorted(by_race.items()):
        if race_id == "2026-generic-ballot":
            continue          # a national margin, not a race probability
        series = replay(polls, args.step)
        for point in series:
            rows.append(dict(point, race_id=race_id))
        if series:
            first, last = series[0], series[-1]
            print(f"  {race_id:<24} {len(series):>3} points  "
                  f"{first['as_of_date']} ({first['prob']*100:.0f}%) -> "
                  f"{last['as_of_date']} ({last['prob']*100:.0f}%)")
        else:
            print(f"  {race_id:<24} no points")

    print(f"\n{len(rows)} rows across "
          f"{len({r['race_id'] for r in rows})} races")

    if args.dry_run:
        print("dry run, nothing written")
        return 0

    DATA.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {OUT.name}")
    print("\nRegenerate this after every poll fetch; it is derived from")
    print("raw_polls.csv rather than accumulated, so it is safe to rerun.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
