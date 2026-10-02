#!/usr/bin/env python3
"""scorecard.py: score prediction markets against polls once 2026 races resolve.

Run from the repo root:
  python3 analysis/scorecard.py --init-outcomes   # create data/outcomes.csv, all pending
  python3 analysis/scorecard.py --dry-run         # invented outcomes, today as election day
  python3 analysis/scorecard.py                   # the real run, after the cutoff

Rules, as published on the methodology page:
  * A source's forecast for a day is its last reading that day. On election
    day only readings before 6 p.m. Eastern (23:00 UTC) count.
  * The poll probability for a day is the latest poll average as of that day,
    which is what the site showed.
  * A race resolves on the eventual winner of the seat as called by the AP,
    runoffs included. Pending races are listed but not scored.
  * Polymarket and Kalshi are each scored separately against polls, only on
    race-days where both sources have a reading.

Filling in data/outcomes.csv: set status to "called", winner to "dem" or
"rep", and called_date to the AP call date. Leave uncalled races "pending".
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from scoring import build_report, office_of
from to_probability import SIGMA_FINAL, VARIANCE_DOUBLING_DAYS

DATA = Path(__file__).resolve().parent.parent / "data"
SNAPSHOTS = DATA / "snapshots.csv"
POLL_PROBS = DATA / "poll_probabilities.csv"
RACE_META = DATA / "race_meta.json"
OUTCOMES = DATA / "outcomes.csv"
SCORECARD = DATA / "scorecard.json"
DRY_OUT = Path("/tmp/oddsvspolls_scorecard_dryrun.json")

CYCLE = 2026
ELECTION_DATE = date(2026, 11, 3)                   # matches build_races.py
CUTOFF_UTC = pd.Timestamp("2026-11-03T23:00:00Z")   # 6 p.m. EST (DST ends Nov 1)
VENUES = ("polymarket", "kalshi")
PROB_FLOOR = 0.001  # log score is infinite at 0 or 1, so clip to 0.1%..99.9%
REQUIRED = ["race_id", "cycle", "source", "days_out", "prob", "outcome"]
OUTCOME_FIELDS = ["race_id", "winner", "called_date", "status", "note"]


# ------------------------------------------------------------------ inputs

def load_meta() -> dict:
    meta = json.loads(RACE_META.read_text())
    return {rid: m for rid, m in meta.items()
            if rid.startswith(f"{CYCLE}-") and office_of(rid) in ("senate", "governor")}


def load_markets(cutoff: pd.Timestamp) -> pd.DataFrame:
    df = pd.read_csv(SNAPSHOTS, usecols=["fetched_at", "snapshot_date",
                                         "race_id", "venue", "prob"])
    df = df[df["venue"].isin(VENUES)].dropna(subset=["prob"])
    df["fetched_at"] = pd.to_datetime(df["fetched_at"], utc=True, format="ISO8601")
    df = df[df["fetched_at"] <= cutoff]
    # Last reading per race, venue and day. tail(1) keeps whole rows intact.
    df = (df.sort_values("fetched_at")
            .groupby(["race_id", "venue", "snapshot_date"]).tail(1))
    df = df.rename(columns={"venue": "source", "snapshot_date": "day"})
    return df[["race_id", "source", "day", "prob"]]


def load_polls(days: pd.DataFrame, last_day: date) -> pd.DataFrame:
    """Poll probability showing on each race-day: latest as_of_date <= day."""
    p = pd.read_csv(POLL_PROBS, usecols=["race_id", "as_of_date", "prob"])
    p = p.dropna(subset=["prob"]).drop_duplicates(["race_id", "as_of_date"], keep="last")
    p["t"] = pd.to_datetime(p["as_of_date"])
    p = p[p["t"] <= pd.Timestamp(last_day)]
    d = days[["race_id", "day"]].drop_duplicates().copy()
    d["t"] = pd.to_datetime(d["day"])
    m = pd.merge_asof(d.sort_values("t"), p.sort_values("t")[["race_id", "t", "prob"]],
                      on="t", by="race_id", direction="backward")
    m = m.dropna(subset=["prob"])
    m["source"] = "polls"
    return m[["race_id", "source", "day", "prob"]]


def build_frame(cutoff: pd.Timestamp, election_day: date, scope: set) -> pd.DataFrame:
    mk = load_markets(cutoff)
    mk = mk[mk["race_id"].isin(scope)]
    mk = mk[pd.to_datetime(mk["day"]).dt.date <= election_day]
    pl = load_polls(mk, election_day)
    f = pd.concat([mk, pl], ignore_index=True)
    f["days_out"] = (pd.Timestamp(election_day) - pd.to_datetime(f["day"])).dt.days
    f = f[f["days_out"] >= 0].copy()
    f["prob"] = f["prob"].astype(float).clip(PROB_FLOOR, 1 - PROB_FLOOR)
    f["cycle"] = CYCLE
    return f


def read_outcomes(scope: set):
    if not OUTCOMES.exists():
        sys.exit(f"{OUTCOMES} not found. Run with --init-outcomes first.")
    called, pending = {}, set()
    with OUTCOMES.open(newline="", encoding="utf-8") as fh:
        for i, r in enumerate(csv.DictReader(fh), start=2):
            rid = (r.get("race_id") or "").strip()
            status = (r.get("status") or "").strip().lower()
            winner = (r.get("winner") or "").strip().lower()
            if rid not in scope:
                sys.exit(f"outcomes.csv line {i}: unknown race_id {rid!r}")
            if status == "called":
                if winner not in ("dem", "rep"):
                    sys.exit(f"outcomes.csv line {i}: {rid} is called but winner "
                             f"is {winner!r}; use dem or rep")
                called[rid] = 1 if winner == "dem" else 0
            elif status == "pending":
                pending.add(rid)
            else:
                sys.exit(f"outcomes.csv line {i}: status must be called or pending")
    pending |= scope - set(called)
    return called, sorted(pending)


def simulate_outcomes(frame: pd.DataFrame, seed: int = 2026):
    """Dry run only: draw each winner from the average final forecast."""
    rng = np.random.default_rng(seed)
    last = frame["days_out"] == frame.groupby("race_id")["days_out"].transform("min")
    p = frame[last].groupby("race_id")["prob"].mean()
    return {rid: int(rng.random() < prob) for rid, prob in sorted(p.items())}, []


# ----------------------------------------------------------------- scoring

def reports_for(scored: pd.DataFrame) -> dict:
    out = {}
    for venue in VENUES:
        pair = scored[scored["source"].isin(["polls", venue])]
        keys = (pair[pair["source"] == "polls"][["race_id", "days_out"]]
                .merge(pair[pair["source"] == venue][["race_id", "days_out"]]))
        pair = pair.merge(keys, on=["race_id", "days_out"])
        try:
            out[f"{venue}_vs_polls"] = build_report(pair[REQUIRED], ("polls", venue))
        except ValueError as e:
            out[f"{venue}_vs_polls"] = {"error": str(e)}
    return out


def final_forecasts(scored: pd.DataFrame, outcomes: dict, meta: dict) -> list:
    rows = []
    for rid, g in scored.groupby("race_id"):
        row = {"race_id": rid, "label": meta.get(rid, {}).get("label", rid),
               "office": office_of(rid), "outcome": outcomes[rid]}
        for src in ("polls",) + VENUES:
            s = g[g["source"] == src].sort_values("days_out")
            row[src] = None if s.empty else float(s.iloc[0]["prob"])
        rows.append(row)
    return rows


def orientation_check(finals: list) -> None:
    for venue in VENUES:
        pairs = [(r["polls"], r[venue]) for r in finals
                 if r["polls"] is not None and r[venue] is not None]
        if len(pairs) < 5:
            print(f"  orientation: too few races to compare polls with {venue}")
            continue
        c = float(np.corrcoef(*zip(*pairs))[0, 1])
        print(f"  orientation: polls vs {venue} final forecasts correlate at {c:+.2f}")
        if c < 0.3:
            print(f"  WARNING: weak or negative agreement. Check that {venue} "
                  "prices are stated as P(Democrat wins) before publishing.")


def clean(o):
    """Make the report strict-JSON safe: numpy types out, NaN to null."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return float(o) if np.isfinite(o) else None
    return o


# -------------------------------------------------------------------- main

def init_outcomes(scope: set) -> int:
    if OUTCOMES.exists():
        print(f"{OUTCOMES.name} already exists; not overwriting.")
        return 1
    with OUTCOMES.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUTCOME_FIELDS)
        w.writeheader()
        for rid in sorted(scope):
            w.writerow({"race_id": rid, "winner": "", "called_date": "",
                        "status": "pending", "note": ""})
    print(f"wrote {OUTCOMES.name}: {len(scope)} races, all pending")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--init-outcomes", action="store_true",
                   help="create data/outcomes.csv with every race pending")
    g.add_argument("--dry-run", action="store_true",
                   help="treat today as election day with simulated outcomes")
    args = ap.parse_args()

    meta = load_meta()
    scope = set(meta)
    if args.init_outcomes:
        return init_outcomes(scope)

    now = pd.Timestamp.now(tz="UTC")
    if args.dry_run:
        cutoff, election_day, out_path, mode = now, now.date(), DRY_OUT, "dry-run"
    else:
        if now < CUTOFF_UTC:
            print("The cutoff (6 p.m. Eastern, November 3) hasn't passed yet. "
                  "Use --dry-run to test.", file=sys.stderr)
            return 2
        cutoff, election_day, out_path, mode = CUTOFF_UTC, ELECTION_DATE, SCORECARD, "final"

    frame = build_frame(cutoff, election_day, scope)
    if frame.empty:
        sys.exit("No forecast data found for races in scope.")

    if args.dry_run:
        outcomes, pending = simulate_outcomes(frame)
    else:
        outcomes, pending = read_outcomes(scope)

    for rid in sorted(set(outcomes) - set(frame["race_id"])):
        print(f"  warning: {rid} is called but has no forecast data; skipped")
        outcomes.pop(rid)

    if not outcomes:
        print("No called races yet, nothing to score.")
        return 0

    scored = frame[frame["race_id"].isin(outcomes)].copy()
    scored["outcome"] = scored["race_id"].map(outcomes).astype(int)
    finals = final_forecasts(scored, outcomes, meta)

    doc = {
        "mode": mode,
        "generated_at": now.isoformat(),
        "cutoff_utc": cutoff.isoformat(),
        "election_date": election_day.isoformat(),
        "parameters": {"sigma_final": SIGMA_FINAL,
                       "variance_doubling_days": VARIANCE_DOUBLING_DAYS,
                       "prob_clip": [PROB_FLOOR, 1 - PROB_FLOOR]},
        "coverage": {"scored": sorted(outcomes), "pending": pending},
        "final_forecasts": finals,
        "reports": reports_for(scored),
    }
    out_path.write_text(json.dumps(clean(doc), indent=1, allow_nan=False))

    print(f"{mode}: scored {len(outcomes)} races, {len(pending)} pending, "
          f"{len(scored)} race-day rows")
    for key, rep in doc["reports"].items():
        if "error" in rep:
            print(f"  {key}: {rep['error']}")
            continue
        h = rep["head_to_head"]["brier"]
        verdict = "significant" if h["significant"] else "not significant"
        print(f"  {key}: Brier polls {h['brier_a']:.4f} vs {h['source_b']} "
              f"{h['brier_b']:.4f} ({verdict}; {h['n_races']} races, "
              f"{h['n_cycles']} cycle)")
    orientation_check(finals)
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
