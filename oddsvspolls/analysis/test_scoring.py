"""Synthetic-data checks for scoring.py.

Generates two fake sources with known properties and verifies the module
recovers them. Run this before trusting any result on real data.

Source "market_like": well calibrated, sharpens as election day approaches.
Source "poll_like":   slightly overconfident far out, similar near the end.
"""

import numpy as np
import pandas as pd

from scoring import (
    brier, log_score, brier_decomposition, validate,
    score_by_horizon, calibration_table, paired_comparison, build_report,
)

RNG = np.random.default_rng(42)
HORIZONS = [0, 2, 5, 10, 21, 45, 90, 180]


def make_data(n_races_per_cycle=35, cycles=(2016, 2018, 2020, 2022, 2024)):
    rows = []
    for cycle in cycles:
        # Each cycle has a shared national error. This is the correlation that
        # makes naive per-race confidence intervals wrong.
        national_shift = RNG.normal(0, 0.03)

        for i in range(n_races_per_cycle):
            race_id = f"{cycle}-race-{i:03d}"
            true_p = np.clip(RNG.beta(2, 2) + national_shift, 0.02, 0.98)
            outcome = int(RNG.random() < true_p)

            for d in HORIZONS:
                # Information accrues as the election nears: noise shrinks.
                noise = 0.18 * (d / 180) ** 0.5 + 0.02

                m = np.clip(true_p + RNG.normal(0, noise), 0.01, 0.99)
                rows.append(dict(race_id=race_id, cycle=cycle, source="market_like",
                                 days_out=d, prob=m, outcome=outcome))

                # Overconfidence: push away from 0.5, worse at long horizons.
                p = np.clip(true_p + RNG.normal(0, noise), 0.01, 0.99)
                stretch = 1.0 + 0.45 * (d / 180)
                p = np.clip(0.5 + (p - 0.5) * stretch, 0.01, 0.99)
                rows.append(dict(race_id=race_id, cycle=cycle, source="poll_like",
                                 days_out=d, prob=p, outcome=outcome))

    return pd.DataFrame(rows)


def main():
    df = make_data()
    df = validate(df)
    print(f"rows={len(df)}  races={df['race_id'].nunique()}  cycles={df['cycle'].nunique()}\n")

    # Sanity: a perfect forecaster scores 0, a coin flip scores 0.25.
    y = df["outcome"].to_numpy()
    assert abs(brier(y.astype(float), y)) < 1e-12
    assert abs(brier(np.full(len(y), 0.5), y) - 0.25) < 1e-12
    assert log_score(np.full(len(y), 0.5), y) > 0.69
    print("sanity checks passed\n")

    # Murphy identity: Brier == reliability - resolution + uncertainty.
    p = df.loc[df["source"] == "market_like", "prob"].to_numpy()
    o = df.loc[df["source"] == "market_like", "outcome"].to_numpy()
    d = brier_decomposition(p, o)
    lhs = brier(p, o)
    rhs = d["reliability"] - d["resolution"] + d["uncertainty"]
    print(f"Murphy identity: brier={lhs:.5f} decomposed={rhs:.5f} gap={abs(lhs-rhs):.2e}")
    assert abs(lhs - rhs) < 5e-3, "decomposition drifted; check binning"
    print()

    print("=== Score by horizon ===")
    h = score_by_horizon(df)
    print(h.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print()

    print("=== Calibration: poll_like (should sag off the diagonal) ===")
    print(calibration_table(df, "poll_like").to_string(
        index=False, float_format=lambda x: f"{x:.3f}"))
    print()

    print("=== Head to head, all horizons pooled ===")
    for metric in ("brier", "log_score"):
        r = paired_comparison(df, "poll_like", "market_like", metric=metric)
        print(f"{metric}: poll={r[metric + '_a']:.4f} market={r[metric + '_b']:.4f} "
              f"diff={r['diff']:+.4f} 95% CI [{r['ci_low']:+.4f}, {r['ci_high']:+.4f}] "
              f"significant={r['significant']}  (clusters={r['n_cycles']})")
    print()

    print("=== Head to head, near vs far from election day ===")
    for label, sel in [("<= 5 days", df["days_out"] <= 5), ("> 45 days", df["days_out"] > 45)]:
        r = paired_comparison(df[sel], "poll_like", "market_like")
        print(f"{label:>10}: diff={r['diff']:+.4f} "
              f"CI [{r['ci_low']:+.4f}, {r['ci_high']:+.4f}] significant={r['significant']}")
    print()

    report = build_report(df, ("poll_like", "market_like"))
    print(f"build_report ok: keys={list(report)} "
          f"horizon_rows={len(report['horizon_scores'])}")


if __name__ == "__main__":
    main()
