"""
Scoring module for comparing probabilistic election forecasts.

Designed for comparing prediction markets against poll-derived probabilities,
but source-agnostic: it scores any set of (probability, outcome) pairs.

Core input is a "forecast frame": one row per (race, source, snapshot date).

    race_id      str    stable id for the contest, e.g. "2024-senate-PA"
    cycle        int    election year, used for clustered resampling
    source       str    e.g. "polymarket", "poll_model"
    days_out     int    days before election day (0 = election day)
    prob         float  forecast probability that `outcome` = 1
    outcome      int    1 if the forecast side won, 0 otherwise

The `outcome` column must be defined consistently across sources for a given
race. Pick one side per race (say, the Democratic candidate) and express every
source's probability as P(that side wins). Getting this wrong is the single
easiest way to produce a confidently backwards result.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Probabilities are clipped before log scoring so a confident miss produces a
# large-but-finite penalty instead of infinity. 1e-4 corresponds to a worst-case
# log score of about 9.2 nats.
LOG_CLIP = 1e-4

REQUIRED_COLUMNS = ["race_id", "cycle", "source", "days_out", "prob", "outcome"]


def validate(df: pd.DataFrame) -> pd.DataFrame:
    """Check a forecast frame and return a cleaned copy."""
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    out = df.loc[:, REQUIRED_COLUMNS].copy()

    if out["prob"].isna().any() or out["outcome"].isna().any():
        raise ValueError("prob and outcome may not contain nulls")
    if not out["prob"].between(0.0, 1.0).all():
        raise ValueError("prob must lie in [0, 1]")
    if not out["outcome"].isin([0, 1]).all():
        raise ValueError("outcome must be 0 or 1")

    dupes = out.duplicated(subset=["race_id", "source", "days_out"])
    if dupes.any():
        raise ValueError(
            f"{dupes.sum()} duplicate (race_id, source, days_out) rows; "
            "collapse to one snapshot per source per day first"
        )

    # A race must have one outcome. Two rows disagreeing means the side
    # convention broke somewhere upstream.
    per_race = out.groupby("race_id")["outcome"].nunique()
    if (per_race > 1).any():
        bad = per_race[per_race > 1].index.tolist()
        raise ValueError(f"inconsistent outcome within races: {bad[:5]}")

    return out


def brier(prob: np.ndarray, outcome: np.ndarray) -> float:
    """Mean squared error of the forecast. Lower is better, 0 is perfect."""
    return float(np.mean((prob - outcome) ** 2))


def log_score(prob: np.ndarray, outcome: np.ndarray) -> float:
    """Mean negative log likelihood in nats. Lower is better.

    Punishes confident errors far harder than Brier does, which is usually
    what you want when the interesting question is overconfidence.
    """
    p = np.clip(prob, LOG_CLIP, 1.0 - LOG_CLIP)
    return float(-np.mean(outcome * np.log(p) + (1 - outcome) * np.log(1 - p)))


def brier_decomposition(
    prob: np.ndarray, outcome: np.ndarray, n_bins: int = 10
) -> dict:
    """Murphy decomposition: Brier = reliability - resolution + uncertainty.

    reliability   how far bucketed forecasts drift from observed rates.
                  Lower is better. This is calibration error.
    resolution    how much forecasts separate outcomes from the base rate.
                  Higher is better. This is discrimination.
    uncertainty   variance of the outcomes themselves. A property of the
                  race set, not the forecaster, so it is not a score.

    This is the decomposition that actually answers "why is one source
    better." A source can win on Brier purely by being better calibrated
    while carrying no more real information.
    """
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(prob, edges[1:-1], right=False), 0, n_bins - 1)

    base_rate = float(np.mean(outcome))
    n = len(prob)

    reliability = 0.0
    resolution = 0.0
    for b in range(n_bins):
        mask = idx == b
        n_b = int(mask.sum())
        if n_b == 0:
            continue
        mean_prob = float(np.mean(prob[mask]))
        obs_rate = float(np.mean(outcome[mask]))
        reliability += n_b * (mean_prob - obs_rate) ** 2
        resolution += n_b * (obs_rate - base_rate) ** 2

    return {
        "reliability": reliability / n,
        "resolution": resolution / n,
        "uncertainty": base_rate * (1.0 - base_rate),
        "base_rate": base_rate,
        "n": n,
    }


def calibration_table(
    df: pd.DataFrame, source: str, n_bins: int = 10
) -> pd.DataFrame:
    """Observed win rate per forecast bucket. Feeds the calibration chart."""
    sub = df[df["source"] == source]
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(
        np.digitize(sub["prob"].to_numpy(), edges[1:-1], right=False),
        0, n_bins - 1,
    )

    rows = []
    for b in range(n_bins):
        mask = idx == b
        if not mask.any():
            continue
        chunk = sub[mask]
        n_b = len(chunk)
        obs = float(chunk["outcome"].mean())
        # Wald interval; wide bins with few races will look noisy, which is
        # honest. Do not hide it by widening the bins.
        se = np.sqrt(max(obs * (1 - obs), 1e-12) / n_b)
        rows.append({
            "bin_low": edges[b],
            "bin_high": edges[b + 1],
            "mean_forecast": float(chunk["prob"].mean()),
            "observed_rate": obs,
            "n": n_b,
            "ci_low": max(0.0, obs - 1.96 * se),
            "ci_high": min(1.0, obs + 1.96 * se),
        })
    return pd.DataFrame(rows)


def score_by_horizon(df: pd.DataFrame, bin_edges: list[int] | None = None) -> pd.DataFrame:
    """Score each source within buckets of days-to-election.

    This is the table that answers "when is each source at its best."
    """
    if bin_edges is None:
        bin_edges = [0, 1, 3, 7, 14, 30, 60, 90, 180, 365]

    labels = [f"{bin_edges[i]}-{bin_edges[i + 1] - 1}d" for i in range(len(bin_edges) - 1)]
    binned = df.assign(
        horizon=pd.cut(
            df["days_out"], bins=bin_edges, labels=labels,
            right=False, include_lowest=True,
        )
    )

    order = {label: i for i, label in enumerate(labels)}

    rows = []
    for (horizon, source), chunk in binned.groupby(["horizon", "source"], observed=True):
        p = chunk["prob"].to_numpy()
        y = chunk["outcome"].to_numpy()
        decomp = brier_decomposition(p, y)
        rows.append({
            "horizon": str(horizon),
            "horizon_rank": order[str(horizon)],
            "days_out_min": bin_edges[order[str(horizon)]],
            "source": source,
            "n": len(chunk),
            "n_races": chunk["race_id"].nunique(),
            "brier": brier(p, y),
            "log_score": log_score(p, y),
            "reliability": decomp["reliability"],
            "resolution": decomp["resolution"],
        })

    return (
        pd.DataFrame(rows)
        .sort_values(["horizon_rank", "source"])
        .reset_index(drop=True)
    )


def paired_comparison(
    df: pd.DataFrame,
    source_a: str,
    source_b: str,
    metric: str = "brier",
    n_boot: int = 2000,
    seed: int = 0,
) -> dict:
    """Compare two sources on races where both made a forecast.

    Uncertainty comes from a bootstrap clustered on election cycle, not on
    individual races. Races within a cycle share a national polling error, so
    treating them as independent understates the true interval by a lot. With
    only a handful of cycles the interval will be wide. That width is the real
    answer: a few cycles of data cannot cleanly separate two decent forecasters.
    """
    if metric not in ("brier", "log_score"):
        raise ValueError("metric must be 'brier' or 'log_score'")
    fn = brier if metric == "brier" else log_score

    a = df[df["source"] == source_a].set_index(["race_id", "days_out"])
    b = df[df["source"] == source_b].set_index(["race_id", "days_out"])
    shared = a.index.intersection(b.index)
    if len(shared) == 0:
        raise ValueError("no overlapping (race_id, days_out) pairs")

    a, b = a.loc[shared], b.loc[shared]

    score_a = fn(a["prob"].to_numpy(), a["outcome"].to_numpy())
    score_b = fn(b["prob"].to_numpy(), b["outcome"].to_numpy())

    cycles = a["cycle"].to_numpy()
    unique_cycles = np.unique(cycles)
    rng = np.random.default_rng(seed)

    diffs = []
    for _ in range(n_boot):
        picked = rng.choice(unique_cycles, size=len(unique_cycles), replace=True)
        mask = np.concatenate([np.flatnonzero(cycles == c) for c in picked])
        d = fn(a["prob"].to_numpy()[mask], a["outcome"].to_numpy()[mask]) - \
            fn(b["prob"].to_numpy()[mask], b["outcome"].to_numpy()[mask])
        diffs.append(d)

    diffs = np.array(diffs)
    lo, hi = np.percentile(diffs, [2.5, 97.5])

    return {
        "metric": metric,
        "source_a": source_a,
        "source_b": source_b,
        f"{metric}_a": score_a,
        f"{metric}_b": score_b,
        "diff": score_a - score_b,
        "ci_low": float(lo),
        "ci_high": float(hi),
        "n_pairs": len(shared),
        "n_races": a.index.get_level_values("race_id").nunique(),
        "n_cycles": len(unique_cycles),
        "significant": bool(lo > 0 or hi < 0),
    }


def build_report(df: pd.DataFrame, sources: tuple[str, str]) -> dict:
    """Assemble everything the frontend needs as one JSON-serializable dict."""
    df = validate(df)
    a, b = sources

    return {
        "horizon_scores": score_by_horizon(df).to_dict(orient="records"),
        "calibration": {
            s: calibration_table(df, s).to_dict(orient="records") for s in sources
        },
        "head_to_head": {
            m: paired_comparison(df, a, b, metric=m) for m in ("brier", "log_score")
        },
        "coverage": {
            "n_races": int(df["race_id"].nunique()),
            "n_cycles": int(df["cycle"].nunique()),
            "cycles": sorted(int(c) for c in df["cycle"].unique()),
        },
    }
