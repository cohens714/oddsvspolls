# oddsvspolls.com

Tracks prediction market probabilities against poll-derived probabilities for
US elections, and scores both against actual outcomes.

## Layout

    src/                  React frontend (Vite)
    analysis/             Python scoring and ingestion
      scoring.py          Brier, log score, calibration, clustered bootstrap
      test_scoring.py     Synthetic-data checks, run before trusting results
    data/                 Daily snapshots, committed to git as the audit trail
    .github/workflows/    Scheduled snapshot job

## Local development

    npm install
    npm run dev

    cd analysis && python3 test_scoring.py

## Licence

Code is MIT (`LICENSE`). Data is CC BY-NC 4.0 (`data/LICENSE`): share and
adapt with attribution for non-commercial purposes; commercial use needs
permission. Individual prices and poll results are facts and not
copyrightable; the licence covers the compilation and the derived figures.

## Data sources

Polling from VoteHub, CC BY 4.0. Historical polling error from
FiveThirtyEight's pollster-ratings archive, CC BY 4.0. Market prices from
public Polymarket and Kalshi endpoints.

## Methodology

Scoring uses Brier and log scores with a bootstrap clustered on election
cycle rather than individual race, because races within a cycle share a
national polling error. See analysis/scoring.py for details.
