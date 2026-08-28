"""
Assemble historical polls, market prices and outcomes for the backtest.

    python3 fetch_history.py --polls            # 538 archive -> historical_polls.csv
    python3 fetch_history.py --probe-markets    # what closed markets exist?
    python3 fetch_history.py --markets 2024     # closed markets for a cycle

Standard library only.

WHAT THIS CAN AND CANNOT SETTLE
-------------------------------
The poll side is solid: 538's pollster-ratings archive contains every poll
they analysed with the actual election result already joined, going back to
1998. That is the single most useful public file for this project, and it is
frozen, so it will not move under you.

The market side is thin. Polymarket has broad downballot coverage only from
2024, and Kalshi's election contracts are similarly recent. One cycle is one
cluster: every race in it shares a national polling error, so a bootstrap
that resamples cycles has a single data point and no power to distinguish
two forecasters.

That does not make the exercise pointless. Calibration curves, Brier scores
and a plain count of which source landed closer are all worth showing. It
does mean the honest headline is "here is what happened in 2024", not
"markets beat polls". Build the display around the former.

Data from the FiveThirtyEight archive is CC BY 4.0; attribute it.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DATA = Path(__file__).resolve().parent.parent / "data"
POLLS_OUT = DATA / "historical_polls.csv"
MARKETS_OUT = DATA / "historical_markets.csv"

# 538's repository is archived but still served. raw-polls.csv is the file
# that matters: one row per poll with the actual result already attached.
#
# Its location has moved. The long-cited path now 404s, and the directory
# README says past data lives in year subdirectories, so try candidates in
# order rather than hardcoding one. Run --find-polls when this breaks again;
# an archived repo can still be reorganised.
GH_RAW = "https://raw.githubusercontent.com/fivethirtyeight/data"

RAW_POLLS_CANDIDATES = [
    f"{GH_RAW}/master/pollster-ratings/raw-polls.csv",
    f"{GH_RAW}/master/pollster-ratings/2024/raw-polls.csv",
    f"{GH_RAW}/master/pollster-ratings/2023/raw-polls.csv",
    f"{GH_RAW}/master/pollster-ratings/2022/raw-polls.csv",
    f"{GH_RAW}/main/pollster-ratings/raw-polls.csv",
    f"{GH_RAW}/main/pollster-ratings/2024/raw-polls.csv",
    # Simon Willison mirrors this data and keeps it queryable.
    ("https://fivethirtyeight.datasettes.com/fivethirtyeight/"
     "pollster-ratings~2Fraw-polls.csv?_size=max&_stream=on"),
]

# Fallback: polls 180 to 15 days out for four cycles, in its own directory.
# Structured differently, so it needs its own join to results, but it is a
# live path if raw-polls.csv is gone for good.
STATE_OF_POLLS = [f"{GH_RAW}/master/state-of-the-polls-2024/{y}_polls.csv"
                  for y in (2024, 2020, 2016, 2012)]

RAW_POLLS_URL = RAW_POLLS_CANDIDATES[0]

GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"
GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"

USER_AGENT = "oddsvspolls.com (+https://oddsvspolls.com) python-urllib"
TIMEOUT = 60

POLL_FIELDS = [
    "poll_id", "race_id", "cycle", "office", "state", "pollster",
    "sample_size", "poll_date", "days_out", "election_date",
    "margin_poll", "margin_actual", "error", "dem_won",
]

MARKET_FIELDS = [
    "cycle", "race_id", "slug", "question", "outcome_label",
    "final_price", "volume", "closed", "end_date", "resolved_to",
]


def fetch(url, params=None):
    params = params or {}
    query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    full = f"{url}?{query}" if query else url
    try:
        req = Request(full, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError) as exc:
        code = getattr(exc, "code", "")
        print(f"  fetch failed{f' ({code})' if code else ''}: {exc}",
              file=sys.stderr)
        return None


def fetch_json(url, params=None):
    text = fetch(url, params)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"  bad JSON: {exc}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------
# Polls
# --------------------------------------------------------------------------

def parse_date(value):
    """538 uses M/D/YY in this file. Return an ISO date or None."""
    if not value:
        return None
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def find_polls_url():
    """Try each candidate location and return the first that serves a CSV
    with the columns we need."""
    print("=== looking for raw-polls.csv ===")
    for url in RAW_POLLS_CANDIDATES:
        print(f"  trying {url[:76]}")
        text = fetch(url)
        if not text:
            continue
        first = text.split("\n", 1)[0].lower()
        if "cand1_pct" in first or "cand1_actual" in first:
            print(f"    FOUND, header looks right")
            return url, text
        print(f"    served, but header is unexpected: {first[:90]}")

    print("\n  none worked. Checking the state-of-the-polls files:")
    for url in STATE_OF_POLLS:
        text = fetch(url)
        status = "ok" if text else "missing"
        print(f"    {url.rsplit('/', 1)[-1]:<20} {status}")
        if text:
            print(f"      header: {text.split(chr(10), 1)[0][:110]}")
    return None, None


def load_polls(offices, min_cycle):
    """Download and reshape raw-polls.csv.

    Columns of interest, per 538's codebook: year, race, location, pollster,
    samplesize, polldate, electiondate, cand1_pct, cand2_pct, cand1_actual,
    cand2_actual, where cand1 is the Democrat in partisan races. Column
    names are read from the header rather than assumed by position, since
    the file has been reorganised between methodology revisions.
    """
    url, text = find_polls_url()
    if not text:
        print("\nCould not locate the archive. Run --find-polls for detail.",
              file=sys.stderr)
        return []
    print(f"\nUsing {url}\n")

    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    print(f"  {len(headers)} columns: {headers[:14]}\n")

    def pick(row, *names):
        for n in names:
            if n in row and row[n] not in (None, ""):
                return row[n]
        return None

    rows, skipped = [], 0
    for r in reader:
        try:
            cycle = int(pick(r, "year", "cycle") or 0)
        except ValueError:
            skipped += 1
            continue
        if cycle < min_cycle:
            continue

        race = (pick(r, "type_simple", "race", "type_detail") or "").upper()
        if offices and not any(o in race for o in offices):
            continue

        poll_d = parse_date(pick(r, "polldate", "poll_date"))
        elec_d = parse_date(pick(r, "electiondate", "election_date"))
        if not poll_d or not elec_d:
            skipped += 1
            continue

        try:
            d1 = float(pick(r, "cand1_pct") or "")
            d2 = float(pick(r, "cand2_pct") or "")
            a1 = float(pick(r, "cand1_actual") or "")
            a2 = float(pick(r, "cand2_actual") or "")
        except (TypeError, ValueError):
            skipped += 1
            continue

        margin_poll = d1 - d2
        margin_actual = a1 - a2

        rows.append({
            "poll_id": pick(r, "poll_id", "question_id") or "",
            "race_id": pick(r, "race_id", "race") or "",
            "cycle": cycle,
            "office": race,
            "state": pick(r, "location", "state") or "",
            "pollster": pick(r, "pollster", "pollster_rating_name") or "",
            "sample_size": pick(r, "samplesize", "sample_size") or "",
            "poll_date": poll_d.isoformat(),
            "days_out": (elec_d - poll_d).days,
            "election_date": elec_d.isoformat(),
            "margin_poll": round(margin_poll, 2),
            "margin_actual": round(margin_actual, 2),
            "error": round(margin_poll - margin_actual, 2),
            "dem_won": 1 if margin_actual > 0 else 0,
        })

    print(f"  {len(rows)} polls kept, {skipped} unusable")
    return rows


def summarise_polls(rows):
    """Report the empirical polling error, which is the number that should
    eventually replace the assumed sigma in to_probability.py."""
    if not rows:
        return

    import statistics

    by_cycle = {}
    for r in rows:
        by_cycle.setdefault(r["cycle"], []).append(r)

    print(f"\n{'cycle':<8}{'polls':>7}{'races':>8}{'mean err':>10}"
          f"{'std err':>10}")
    print("-" * 43)
    for cycle in sorted(by_cycle):
        chunk = by_cycle[cycle]
        errs = [r["error"] for r in chunk]
        races = {(r["state"], r["office"]) for r in chunk}
        std = statistics.pstdev(errs) if len(errs) > 1 else 0.0
        print(f"{cycle:<8}{len(chunk):>7}{len(races):>8}"
              f"{statistics.fmean(errs):>+10.2f}{std:>10.2f}")

    late = [r for r in rows if r["days_out"] <= 21]
    if late:
        errs = [r["error"] for r in late]
        std = statistics.pstdev(errs)
        print(f"\nFinal 21 days: {len(late)} polls, "
              f"error std = {std:.2f} points")
        print("This is the empirical counterpart to SIGMA_FINAL in")
        print("to_probability.py, though a per-race average has less error")
        print("than an individual poll, so it is an upper bound, not a")
        print("drop-in replacement.")


# --------------------------------------------------------------------------
# Markets
# --------------------------------------------------------------------------

def probe_market_params():
    """Find which filter parameters this API actually honours.

    Blind paging returns oldest-first and never reaches recent cycles, so a
    working date filter is the difference between a feasible backtest and an
    impossible one. Gamma has ignored parameter names before (tag_slug on
    events), so test rather than trust the docs.
    """
    print("=== which date filters work on closed events? ===")
    trials = [
        {},
        {"end_date_min": "2024-10-01", "end_date_max": "2024-12-31"},
        {"start_date_min": "2024-01-01"},
        {"order": "endDate", "ascending": "false"},
        {"order": "endDate", "ascending": "false",
         "end_date_max": "2025-01-01"},
    ]
    for params in trials:
        page = fetch_json(GAMMA_EVENTS,
                          dict(params, closed="true", limit=100))
        n = len(page) if isinstance(page, list) else 0
        span = ""
        if n:
            ends = sorted(str(e.get("endDate", ""))[:10] for e in page
                          if e.get("endDate"))
            if ends:
                span = f"  ends {ends[0]} to {ends[-1]}"
        print(f"  {params or '(no filter)'} -> {n}{span}")
    print()
    return 0


def probe_markets(date_min=None, date_max=None, max_pages=30):
    """Scan closed events for electoral markets in a date range."""
    print("=== closed electoral events ===")
    if date_min or date_max:
        print(f"    window {date_min or 'any'} to {date_max or 'any'}")

    keywords = ("senate", "house", "governor", "gubernatorial")
    # Words that mark a market as something other than a general-election
    # contest. Primaries and legislative-vote markets are not forecasts of
    # who wins a seat, so they do not belong in a backtest of that question.
    reject = ("nomination", "primary", "pass", "confirm", "convict",
              "impeach", "recall", "speaker", "reconciliation", "bill")

    seen, offset, matched = 0, 0, []
    for _ in range(max_pages):
        params = {"closed": "true", "limit": 100, "offset": offset,
                  "order": "endDate", "ascending": "false"}
        if date_min:
            params["end_date_min"] = date_min
        if date_max:
            params["end_date_max"] = date_max

        page = fetch_json(GAMMA_EVENTS, params)
        if not isinstance(page, list) or not page:
            break
        seen += len(page)
        offset += len(page)

        for ev in page:
            title = str(ev.get("title", "")).lower()
            if not any(k in title for k in keywords):
                continue
            if any(k in title for k in reject):
                continue
            matched.append((str(ev.get("endDate", ""))[:10],
                            ev.get("title"), ev.get("slug"),
                            len(ev.get("markets") or [])))
        if len(page) < 100:
            break

    matched.sort(reverse=True)
    print(f"  scanned {seen} closed events, {len(matched)} general-election\n")
    for end, title, slug, n in matched[:30]:
        print(f"  {end}  {str(title)[:46]:<48} {n} mkt")
        print(f"              {slug}")

    if not matched:
        print("  Nothing in range. If the date filter is being ignored,")
        print("  paging cannot reach recent cycles and market history has")
        print("  to start from your own snapshots.")
    print()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--polls", action="store_true",
                    help="download and reshape the 538 archive")
    ap.add_argument("--probe-markets", action="store_true",
                    help="scan closed events for general-election markets")
    ap.add_argument("--probe-params", action="store_true",
                    help="test which date filters the API honours")
    ap.add_argument("--find-polls", action="store_true",
                    help="locate the 538 archive")
    ap.add_argument("--from-date", help="earliest end date, e.g. 2024-01-01")
    ap.add_argument("--to-date", help="latest end date, e.g. 2024-12-31")
    ap.add_argument("--offices", nargs="*", default=["SEN"],
                    help="race types to keep (default SEN)")
    ap.add_argument("--min-cycle", type=int, default=2000)
    args = ap.parse_args()

    if args.find_polls:
        url, _ = find_polls_url()
        return 0 if url else 1
    if args.probe_params:
        return probe_market_params()
    if args.probe_markets:
        return probe_markets(args.from_date, args.to_date)

    if not args.polls:
        ap.error("pass --polls or --probe-markets")

    rows = load_polls([o.upper() for o in args.offices], args.min_cycle)
    if not rows:
        return 1

    summarise_polls(rows)

    DATA.mkdir(parents=True, exist_ok=True)
    with POLLS_OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=POLL_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {POLLS_OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
