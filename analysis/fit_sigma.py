"""
Measure the polling error that to_probability.py currently assumes.

    python3 fit_sigma.py                  # decomposition and fitted sigma
    python3 fit_sigma.py --min-cycle 2010 # recent cycles only

Standard library only. Reads data/historical_polls.csv from fetch_history.py.

WHY A SINGLE STANDARD DEVIATION IS THE WRONG NUMBER TO COPY
-----------------------------------------------------------
The raw spread of individual poll errors is about 6.5 points. It is tempting
to use that as sigma, but it answers a different question. We do not forecast
from one poll; we forecast from an average of several. Some of that 6.5 is
noise that averaging removes, and some is error that averaging cannot touch.

Three levels, separated here:

  cycle    every race in a year misses the same direction together. 2020
           polls overstated Democrats by about 5 points nationally. No
           amount of polling within that year would have caught it, and it
           is why races in a cycle are not independent observations.

  race     a particular contest is misjudged in a way its own polls agree
           on: a bad likely-voter screen, a late local swing.

  poll     one survey differs from its neighbours. This is the only level
           averaging reduces.

For a per-race poll average, the relevant sigma combines cycle and race
error and excludes most of the poll level. That is the number reported here.

The fitted sigma at the end takes a different route to the same question:
pick the value that would have scored best, by log score, on these races.
That is the version to publish, because it gives polls their strongest fair
showing rather than a figure chosen to flatter a conclusion.

LIMITATION: this archive contains only polls within 21 days of the election,
so it measures election-eve error and cannot estimate how error grows at
longer horizons. The horizon term in to_probability.py stays an assumption.
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


def load():
    if not POLLS_IN.exists():
        print(f"{POLLS_IN} not found; run fetch_history.py --polls first",
              file=sys.stderr)
        return []
    with POLLS_IN.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        try:
            out.append({
                "cycle": int(r["cycle"]),
                "race_key": (r["cycle"], r["state"], r["office"],
                             r.get("race_id", "")),
                "margin_poll": float(r["margin_poll"]),
                "margin_actual": float(r["margin_actual"]),
                "error": float(r["error"]),
                "dem_won": int(r["dem_won"]),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def normal_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def race_averages(rows):
    """Collapse polls to one average per race, which is what the live site
    actually forecasts from."""
    by_race = defaultdict(list)
    for r in rows:
        by_race[r["race_key"]].append(r)

    races = []
    for key, chunk in by_race.items():
        races.append({
            "cycle": key[0],
            "n_polls": len(chunk),
            "margin_poll": statistics.fmean(r["margin_poll"] for r in chunk),
            "margin_actual": chunk[0]["margin_actual"],
            "dem_won": chunk[0]["dem_won"],
        })
        races[-1]["error"] = races[-1]["margin_poll"] - races[-1]["margin_actual"]
    return races


def decompose(rows, races):
    print("=== error decomposition ===\n")

    poll_errs = [r["error"] for r in rows]
    print(f"individual polls        n={len(poll_errs):<6} "
          f"std={statistics.pstdev(poll_errs):.2f}")

    race_errs = [r["error"] for r in races]
    print(f"per-race averages       n={len(race_errs):<6} "
          f"std={statistics.pstdev(race_errs):.2f}   <-- what we forecast from")

    by_cycle = defaultdict(list)
    for r in races:
        by_cycle[r["cycle"]].append(r["error"])
    cycle_means = [statistics.fmean(v) for v in by_cycle.values()]
    cycle_std = statistics.pstdev(cycle_means)
    print(f"cycle-level bias        n={len(cycle_means):<6} "
          f"std={cycle_std:.2f}   <-- irreducible, shared by every race")

    # Race error net of its cycle's mean: what is left once the national
    # miss is removed.
    within = []
    for cycle, errs in by_cycle.items():
        m = statistics.fmean(errs)
        within.extend(e - m for e in errs)
    within_std = statistics.pstdev(within)
    print(f"race error within cycle n={len(within):<6} std={within_std:.2f}")

    print(f"\ncheck: sqrt({cycle_std:.2f}^2 + {within_std:.2f}^2) = "
          f"{math.sqrt(cycle_std**2 + within_std**2):.2f} "
          f"vs observed {statistics.pstdev(race_errs):.2f}")

    print("\nAveraging more polls shrinks nothing below the cycle-level")
    print("figure. That floor is the honest limit on how confident any")
    print("poll-derived probability can be.")
    return statistics.pstdev(race_errs)


def log_score(races, sigma):
    total = 0.0
    for r in races:
        p = normal_cdf(r["margin_poll"] / sigma)
        p = min(max(p, LOG_CLIP), 1 - LOG_CLIP)
        total += -(math.log(p) if r["dem_won"] else math.log(1 - p))
    return total / len(races)


def brier(races, sigma):
    return statistics.fmean(
        (normal_cdf(r["margin_poll"] / sigma) - r["dem_won"]) ** 2
        for r in races)


def fit(races, max_margin=None, label=""):
    """Pick the sigma with the best log score.

    max_margin restricts the fit to competitive races, and that restriction
    matters more than anything else in this file.

    Fitting on every race lets uncompetitive seats dominate. A contest
    decided by 30 points is forecast correctly at any plausible sigma, so
    the optimiser trims sigma for a negligible gain across dozens of safe
    seats, and the handful of close races that should discipline it are
    outvoted. The result is a sigma far below the measured error, which
    would make the site most overconfident precisely where readers care.

    Fitting on races inside a competitive margin puts the weight where the
    probability actually carries information.
    """
    if max_margin is not None:
        races = [r for r in races if abs(r["margin_poll"]) <= max_margin]
    print(f"\n=== fitted sigma{label} ===")
    print(f"    {len(races)} races" +
          (f", margin within {max_margin} points\n" if max_margin else "\n"))
    if len(races) < 20:
        print("    too few races to fit; widen the margin threshold")
        return None
    print(f"{'sigma':>7}{'log score':>12}{'brier':>10}{'calls right':>13}")
    print("-" * 42)

    best, best_score = None, float("inf")
    for i in range(10, 201):
        sigma = i / 10.0
        ls = log_score(races, sigma)
        if ls < best_score:
            best, best_score = sigma, ls

    for sigma in (3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, best):
        ls = log_score(races, sigma)
        b = brier(races, sigma)
        right = statistics.fmean(
            1.0 if (normal_cdf(r["margin_poll"] / sigma) >= 0.5) == bool(r["dem_won"])
            else 0.0 for r in races)
        mark = "  <-- best" if sigma == best else ""
        print(f"{sigma:>7.1f}{ls:>12.4f}{b:>10.4f}{right * 100:>12.1f}%{mark}")

    print(f"\nBest log score at sigma = {best:.1f}")
    return best


def by_cycle_table(races):
    print("\n=== per cycle ===\n")
    by_cycle = defaultdict(list)
    for r in races:
        by_cycle[r["cycle"]].append(r)
    print(f"{'cycle':<8}{'races':>7}{'mean err':>11}{'std':>8}")
    print("-" * 34)
    for cycle in sorted(by_cycle):
        chunk = by_cycle[cycle]
        errs = [r["error"] for r in chunk]
        print(f"{cycle:<8}{len(chunk):>7}{statistics.fmean(errs):>+11.2f}"
              f"{statistics.pstdev(errs) if len(errs) > 1 else 0:>8.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-cycle", type=int, default=0)
    ap.add_argument("--max-margin", type=float, default=10.0,
                    help="fit only on races polling within this margin")
    args = ap.parse_args()

    rows = load()
    if not rows:
        return 1
    if args.min_cycle:
        rows = [r for r in rows if r["cycle"] >= args.min_cycle]

    races = race_averages(rows)
    print(f"{len(rows)} polls across {len(races)} races\n")

    observed = decompose(rows, races)

    all_fit = fit(races, None, ", all races")
    comp_fit = fit(races, args.max_margin,
                   f", competitive only")

    by_cycle_table(races)

    n_comp = len([r for r in races if abs(r["margin_poll"]) <= args.max_margin])
    print(f"\n{'=' * 58}")
    print(f"measured error of race averages   {observed:.2f}")
    print(f"fitted on all {len(races)} races          "
          f"{all_fit:.1f}   <-- do not use")
    print(f"fitted on {n_comp} competitive races   "
          f"{comp_fit:.1f}" if comp_fit else "")
    print()
    print("The all-races fit is dragged down by safe seats, which score")
    print("well at any sigma. Prefer the competitive fit, and sanity-check")
    print("it against the measured spread: they should be in the same")
    print("neighbourhood. If they are far apart, something is wrong.")
    if comp_fit:
        print(f"\nSet SIGMA_FINAL in to_probability.py to {comp_fit:.1f}")
        print("and say on the site that it was fitted to competitive Senate")
        print("races from the 538 archive, naming the period.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
