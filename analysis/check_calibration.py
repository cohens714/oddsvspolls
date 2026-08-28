"""
Check calibration of candidate sigmas against historical Senate races.

    python3 check_calibration.py                # compare 3.2 vs 6.5
    python3 check_calibration.py --sigmas 3 5 7

Standard library only. Reads data/historical_polls.csv.

WHY THIS EXISTS
---------------
fit_sigma.py reports two numbers that disagree: the measured spread of
race-average error is about 6.5, but the sigma that scores best is about
3.2. Both cannot be describing the same thing, and picking whichever is
convenient is how a project like this quietly becomes wrong.

Log score treats every race as an independent observation. It is not.
Polling error is shared within a cycle: in 2020 the average Senate poll
overstated Democrats by nearly 7 points, in every race at once. A sigma
tuned on independent races will look excellent in a typical year and fail
catastrophically in a bad one, because the failures arrive together rather
than cancelling out.

So this reports three things per candidate sigma:

  calibration    do 80% forecasts win 80% of the time? The direct test of
                 whether a probability means what it says.

  per cycle      score in each individual year. A sigma that is superb on
                 average and disastrous in 2016 and 2020 is not a good
                 sigma for forecasting an election that has not happened.

  worst cycle    the number to publish alongside the average, because a
                 forecast is judged in the year it is wrong.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
POLLS_IN = DATA / "historical_polls.csv"
LOG_CLIP = 1e-6


def normal_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def load_races():
    if not POLLS_IN.exists():
        print(f"{POLLS_IN} not found; run fetch_history.py --polls",
              file=sys.stderr)
        return []
    with POLLS_IN.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    by_race = defaultdict(list)
    for r in rows:
        try:
            key = (r["cycle"], r["state"], r["office"], r.get("race_id", ""))
            by_race[key].append({
                "cycle": int(r["cycle"]),
                "margin_poll": float(r["margin_poll"]),
                "margin_actual": float(r["margin_actual"]),
                "dem_won": int(r["dem_won"]),
            })
        except (KeyError, TypeError, ValueError):
            continue

    races = []
    for chunk in by_race.values():
        races.append({
            "cycle": chunk[0]["cycle"],
            "margin_poll": statistics.fmean(c["margin_poll"] for c in chunk),
            "dem_won": chunk[0]["dem_won"],
            "n_polls": len(chunk),
        })
    return races


def probs(races, sigma):
    return [(normal_cdf(r["margin_poll"] / sigma), r["dem_won"], r["cycle"])
            for r in races]


def log_score(pairs):
    total = 0.0
    for p, y, _ in pairs:
        p = min(max(p, LOG_CLIP), 1 - LOG_CLIP)
        total += -(math.log(p) if y else math.log(1 - p))
    return total / len(pairs)


def brier(pairs):
    return statistics.fmean((p - y) ** 2 for p, y, _ in pairs)


def calibration(pairs):
    """Observed win rate per confidence bucket.

    Folded onto the favourite so a 5% forecast and a 95% forecast land in
    the same bucket: both are 95% confident, just about different sides.
    """
    buckets = [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9),
               (0.9, 0.97), (0.97, 1.01)]
    out = []
    for lo, hi in buckets:
        hits = []
        for p, y, _ in pairs:
            conf = max(p, 1 - p)
            if lo <= conf < hi:
                # Did the favourite win?
                hits.append(1 if (y == 1) == (p >= 0.5) else 0)
        if hits:
            out.append((lo, hi, len(hits), statistics.fmean(hits)))
    return out


def report(races, sigma):
    pairs = probs(races, sigma)
    print(f"\n{'=' * 58}")
    print(f"sigma = {sigma}")
    print(f"  overall   log {log_score(pairs):.4f}   brier {brier(pairs):.4f}")

    print(f"\n  {'confidence':<14}{'races':>7}{'won':>9}{'expected':>11}"
          f"{'gap':>8}")
    for lo, hi, n, rate in calibration(pairs):
        mid = (lo + hi) / 2
        gap = rate - mid
        flag = "  <-- overconfident" if gap < -0.06 else ""
        print(f"  {lo:.0%}-{hi:.0%}{'':<7}{n:>7}{rate:>8.0%}"
              f"{mid:>11.0%}{gap:>+8.0%}{flag}")

    by_cycle = defaultdict(list)
    for pair in pairs:
        by_cycle[pair[2]].append(pair)

    scores = {c: log_score(v) for c, v in by_cycle.items() if len(v) >= 10}
    worst = max(scores, key=scores.get)
    best = min(scores, key=scores.get)
    print(f"\n  best cycle  {best}  log {scores[best]:.4f}")
    print(f"  worst cycle {worst}  log {scores[worst]:.4f}"
          f"   <-- how it fails when polls miss together")
    return {"sigma": sigma, "log": log_score(pairs), "brier": brier(pairs),
            "worst": scores[worst], "worst_cycle": worst,
            "cal": calibration(pairs)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sigmas", nargs="*", type=float,
                    default=[3.2, 4.5, 6.5])
    args = ap.parse_args()

    races = load_races()
    if not races:
        return 1
    print(f"{len(races)} races across "
          f"{len({r['cycle'] for r in races})} cycles")

    results = [report(races, s) for s in args.sigmas]

    print(f"\n{'=' * 58}")
    print(f"{'sigma':>7}{'log':>10}{'worst cycle':>14}{'max overconf':>15}")
    print("-" * 47)
    for r in results:
        worst_gap = min((rate - (lo + hi) / 2 for lo, hi, n, rate in r["cal"]),
                        default=0.0)
        print(f"{r['sigma']:>7.1f}{r['log']:>10.4f}"
              f"{r['worst']:>9.4f} ({r['worst_cycle']})"
              f"{worst_gap:>+14.0%}")

    print("\nPick on the calibration table and the worst cycle, not the")
    print("overall log score. A sigma whose 90% forecasts win 90% of the")
    print("time is honest; one that wins 75% of the time is claiming more")
    print("than it knows, however well it scores on average.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
