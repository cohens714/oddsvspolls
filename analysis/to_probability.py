"""
Convert poll margins into win probabilities.

    python3 to_probability.py                 # rebuild poll_probabilities.csv
    python3 to_probability.py --sensitivity   # how much does sigma matter?

Standard library only. Reads data/poll_averages.csv, writes
data/poll_probabilities.csv. Pure function of its input: safe to rerun.

THE HONEST VERSION OF WHAT THIS DOES
------------------------------------
A margin is not a probability. Turning "D+7.8" into "84% likely to win"
requires assuming how wrong polls typically are, and that assumption does
most of the work. Pick a small error and polls look confident and sharp;
pick a large one and they look cautious. Whoever picks the number picks the
answer, which is why it is stated here in the open rather than buried.

The model is deliberately the simplest defensible one:

    P(Democrat wins) = Phi(margin / sigma)

where Phi is the standard normal CDF and sigma is the standard deviation of
the eventual error in the poll margin. Two things drive sigma.

1. Systematic error, which does not shrink no matter how many polls you
   have. It is the shared miss: a turnout model that is wrong for everyone,
   a late swing, a demographic that will not answer the phone. It grows the
   further out you are, because there is more time for the race to move.

2. Sampling error, which does shrink with more polling. A race with one
   poll deserves less confidence than a race with nine, and the effective
   sample size already computed in the averages captures exactly that.

These combine in quadrature, since they are independent sources of error.

SIGMA_FINAL is the provisional number. It is set from published estimates of
Senate polling error, and it should be replaced with a value fitted to
historical races once the backtest exists. Until then this file, and the
site, should say plainly that it is an assumption rather than a measurement.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from datetime import date, datetime
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
AVG_IN = DATA / "poll_averages.csv"
PROB_OUT = DATA / "poll_probabilities.csv"

# --------------------------------------------------------------------------
# The assumption. Change this and every probability on the site changes.
# --------------------------------------------------------------------------

# Standard deviation of the final polling error in a Senate race margin,
# in percentage points.
#
# FITTED, not borrowed. Chosen by running candidate values against 379
# Senate races from the 538 pollster-ratings archive, 2000 to 2022, and
# picking the best calibrated rather than the best scoring. See
# check_calibration.py to reproduce.
#
# Why not the two numbers that look more obvious:
#
#   6.5  is the measured standard deviation of race-average error, but it
#        is inflated by a few enormous misses. Used as sigma it is
#        systematically underconfident: its 70-80% forecasts won 97% of
#        the time. It also scored worst in 2020, so the vagueness bought
#        no protection in the year it would have mattered.
#
#   3.2  scores best on log score across all races, but is overconfident
#        where it counts: its 70-80% forecasts won 62% of the time. Log
#        score treats races as independent, and they are not, so it never
#        sees the risk of every race missing the same way at once.
#
# 4.5 is the best calibrated of the three, largest bucket miss 2 points,
# and had the best worst-cycle score. Revisit after 2026 resolves.
SIGMA_FINAL = 4.5

# Variance doubles this many days out. Captures that a poll in August tells
# you less about November than a poll in late October does.
#
# STILL AN ASSUMPTION. The 538 archive contains only polls within 21 days
# of the election, so it can measure election-eve error but says nothing
# about how error grows at longer horizons. Nothing in this repository
# constrains this number yet.
VARIANCE_DOUBLING_DAYS = 120

# Typical sample size of a single state poll, used to turn effective_n
# (a count of polls) into an effective number of respondents.
TYPICAL_POLL_N = 800

FIELDS = [
    "computed_at", "race_id", "as_of_date", "days_out",
    "margin", "prob", "sigma", "sigma_systematic", "sigma_sampling",
    "n_polls", "effective_n", "n_partisan", "partisan_lean",
    "excluded_partisan", "sigma_final_assumed",
]


def normal_cdf(z: float) -> float:
    """Standard normal CDF via erf. No scipy dependency."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def systematic_sigma(days_out: float) -> float:
    """Shared error that more polling cannot reduce, growing with horizon."""
    days_out = max(0.0, days_out)
    return SIGMA_FINAL * math.sqrt(1.0 + days_out / VARIANCE_DOUBLING_DAYS)


def sampling_sigma(effective_n: float) -> float:
    """Error from limited polling, which does shrink as polls accumulate.

    The standard error of a margin (a difference of two shares) is about
    2 * sqrt(p(1-p)/n); at p = 0.5 that is 1/sqrt(n), in proportion terms.
    Multiplied by 100 for percentage points.
    """
    if not effective_n or effective_n <= 0:
        return 0.0
    n = effective_n * TYPICAL_POLL_N
    return 100.0 / math.sqrt(n)


def to_probability(margin: float, days_out: float, effective_n: float):
    """Return (prob, sigma, sigma_sys, sigma_samp)."""
    sys_s = systematic_sigma(days_out)
    samp_s = sampling_sigma(effective_n)
    sigma = math.sqrt(sys_s ** 2 + samp_s ** 2)
    return normal_cdf(margin / sigma), sigma, sys_s, samp_s


# --------------------------------------------------------------------------

def read_averages():
    if not AVG_IN.exists():
        print(f"{AVG_IN} not found; run fetch_polls_votehub.py first",
              file=sys.stderr)
        return []
    with AVG_IN.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def convert(rows):
    out = []
    for r in rows:
        try:
            margin = float(r["margin"])
            days_out = float(r["days_out"])
            eff = float(r["effective_n"] or 0)
        except (KeyError, TypeError, ValueError):
            continue

        # The generic ballot is a national vote-share margin, not a race
        # margin. Converting it to P(Democrats win the House) needs a
        # seats-votes model, which this is not, so it is passed through
        # without a probability rather than given a fabricated one.
        if r["race_id"] == "2026-generic-ballot":
            continue

        prob, sigma, sys_s, samp_s = to_probability(margin, days_out, eff)
        out.append({
            "computed_at": datetime.utcnow().isoformat(timespec="seconds"),
            "race_id": r["race_id"],
            "as_of_date": r.get("as_of_date", ""),
            "days_out": int(days_out),
            "margin": margin,
            "prob": round(prob, 4),
            "sigma": round(sigma, 2),
            "sigma_systematic": round(sys_s, 2),
            "sigma_sampling": round(samp_s, 2),
            "n_polls": r.get("n_polls", ""),
            "effective_n": r.get("effective_n", ""),
            "n_partisan": r.get("n_partisan", ""),
            "partisan_lean": r.get("partisan_lean", ""),
            "excluded_partisan": r.get("excluded_partisan", ""),
            "sigma_final_assumed": SIGMA_FINAL,
        })
    return out


def sensitivity(rows):
    """Show how the headline probabilities move with sigma.

    Run this before defending any number on the site. If a race swings 20
    points across a plausible sigma range, the probability is an artefact of
    the assumption rather than a finding about the race.
    """
    global SIGMA_FINAL
    original = SIGMA_FINAL
    candidates = [4.0, 5.0, 6.0, 7.0, 8.0]

    latest = {}
    for r in rows:
        if r["race_id"] == "2026-generic-ballot":
            continue
        latest[r["race_id"]] = r

    print(f"{'race':<22} {'margin':>7}  " +
          "  ".join(f"s={s:<4.0f}" for s in candidates))
    print("-" * 68)

    for race_id, r in sorted(latest.items()):
        try:
            margin = float(r["margin"])
            days_out = float(r["days_out"])
            eff = float(r["effective_n"] or 0)
        except (TypeError, ValueError):
            continue
        cells = []
        for s in candidates:
            SIGMA_FINAL = s
            p, *_ = to_probability(margin, days_out, eff)
            cells.append(f"{p * 100:5.1f}%")
        SIGMA_FINAL = original
        print(f"{race_id:<22} {margin:>+7.1f}  " + "  ".join(cells))

    print("\nA race whose probability swings widely across this range is")
    print("reporting your sigma assumption, not the polling. Say so on the")
    print("site rather than letting a reader assume otherwise.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sensitivity", action="store_true",
                    help="show probabilities across a range of sigma")
    args = ap.parse_args()

    rows = read_averages()
    if not rows:
        return 1

    if args.sensitivity:
        return sensitivity(rows)

    out = convert(rows)
    if not out:
        print("nothing to convert", file=sys.stderr)
        return 1

    PROB_OUT.parent.mkdir(parents=True, exist_ok=True)
    with PROB_OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(out)

    latest = {}
    for r in out:
        latest[r["race_id"]] = r
    for race_id, r in sorted(latest.items()):
        print(f"  {race_id:<22} {r['margin']:>+5.1f} -> "
              f"{r['prob'] * 100:5.1f}%   sigma {r['sigma']:.1f} "
              f"({r['sigma_systematic']:.1f} systematic, "
              f"{r['sigma_sampling']:.1f} sampling)")

    print(f"\nwrote {PROB_OUT.name} ({len(out)} rows, "
          f"sigma_final = {SIGMA_FINAL})")
    print("Run --sensitivity before trusting any of these.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
