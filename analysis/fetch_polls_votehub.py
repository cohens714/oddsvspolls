"""
Collect Senate race and generic ballot polls from the VoteHub API.

    python3 fetch_polls_votehub.py --probe          # what subjects exist
    python3 fetch_polls_votehub.py --sample GA      # raw JSON for one race
    python3 fetch_polls_votehub.py --dry-run        # fetch, show, write nothing
    python3 fetch_polls_votehub.py                  # write raw + averages
    python3 fetch_polls_votehub.py --no-partisan    # exclude sponsored polls

Standard library only, no API key.

VoteHub's data is CC BY 4.0. Attribution is required, and it belongs on the
site where readers see it, not only in this docstring.

Notes from the live API, which differs from its documentation:
  - /polls returns a bare JSON array, not {"polls": [...]}.
  - Race polls label answers by CANDIDATE NAME ("Jon Ossoff"), not by party,
    so a name-to-party map is required. Generic ballot uses "Dem"/"Rep".
  - Each poll carries a `partisan` field of DEM, REP or null, and a separate
    `internal` boolean.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API = "https://api.votehub.com"
USER_AGENT = "oddsvspolls.com (+https://oddsvspolls.com) python-urllib"
TIMEOUT = 30

DATA = Path(__file__).resolve().parent.parent / "data"
RAW_OUT = DATA / "raw_polls.csv"
AVG_OUT = DATA / "poll_averages.csv"

ELECTION_DATE = date(2026, 11, 3)

# Averaging parameters. Kept simple on purpose: a method that fits in two
# sentences on the site is worth more than a better one nobody can check.
WINDOW_DAYS = 45          # wider than a presidential year; Senate polling is thin
HALF_LIFE_DAYS = 14
SAMPLE_CAP = 1500

# race_id -> (VoteHub subject, Democratic candidate, Republican candidate)
#
# The candidate names are load-bearing: answers are labelled by name, so a
# wrong or missing name means the poll is skipped rather than mis-assigned.
# Verify each against --sample output before trusting a race, and update
# when a nominee changes.
# race_id -> (VoteHub subject, Democratic nominee, Republican nominee, poll_type)
#
# The candidate names are load-bearing. Answers are labelled by name, so a
# wrong name means the poll is skipped, and a name belonging to someone no
# longer running means polls of a race that will never happen get averaged
# in as though they were real.
#
# Do NOT pick the two most-polled names. Maine is the standing example:
# Platner had 20 polls to Jackson's 3, but Platner won the June primary and
# withdrew in July, and Jackson replaced him. The most recent field date is
# the reliable signal, not the count.
#
# Governor races share every mechanism with Senate races and differ only in
# poll_type, so they live in the same table. Leave a race's candidates as
# None until both nominees are settled: it will skip itself rather than
# collect a matchup that never happens. Fill them in with
#     python3 fetch_polls_votehub.py --labels
RACES = {
    # --- Senate ---
    "2026-senate-GA": ("2026 Georgia", "Jon Ossoff", "Mike Collins", "us-senator"),
    "2026-senate-MI": ("2026 Michigan", "Abdul El-Sayed", "Mike Rogers", "us-senator"),
    "2026-senate-NC": ("2026 North Carolina", "Roy Cooper", "Michael Whatley", "us-senator"),
    "2026-senate-ME": ("2026 Maine", "Troy Jackson", "Susan Collins", "us-senator"),
    "2026-senate-OH": ("2026 Ohio", "Sherrod Brown", "Jon Husted", "us-senator"),
    "2026-senate-TX": ("2026 Texas", "James Talarico", "Ken Paxton", "us-senator"),
    "2026-senate-IA": ("2026 Iowa", "Josh Turek", "Ashley Hinson", "us-senator"),
    "2026-senate-NH": ("2026 New Hampshire", "Chris Pappas", "John Sununu", "us-senator"),
    "2026-senate-MN": ("2026 Minnesota", "Peggy Flanagan", "Michele Tafoya", "us-senator"),
    "2026-senate-AK": ("2026 Alaska", "Mary Peltola", "Dan Sullivan", "us-senator"),
    "2026-senate-KS": ("2026 Kansas", "Adam Hamilton", "Roger Marshall", "us-senator"),

    # --- Governor ---
    #
    # Worth collecting for a reason beyond volume: sigma was fitted on
    # Senate races, and gubernatorial polling has its own error profile. If
    # the calibration holds across both offices that is a much stronger
    # claim than Senate alone, and if it does not, that is worth knowing
    # before November rather than after.
    #
    # Candidates are unset until confirmed. Run --labels to see who is
    # actually being polled in each.
    # Unambiguous: both candidates lead their race's polling by a wide
    # margin and share the most recent field date.
    "2026-gov-AZ": ("2026 Arizona", "Katie Hobbs", "Andy Biggs", "governor"),
    "2026-gov-PA": ("2026 Pennsylvania", "Josh Shapiro", "Stacy Garrity", "governor"),
    "2026-gov-MI": ("2026 Michigan", "Jocelyn Benson", "John James", "governor"),
    "2026-gov-OH": ("2026 Ohio", "Amy Acton", "Vivek Ramaswamy", "governor"),
    "2026-gov-TX": ("2026 Texas", "Gina Hinojosa", "Greg Abbott", "governor"),
    "2026-gov-FL": ("2026 Florida", "David Jolly", "Byron Donalds", "governor"),
    "2026-gov-NY": ("2026 New York", "Kathy Hochul", "Bruce Blakeman", "governor"),
    "2026-gov-NV": ("2026 Nevada", "Aaron Ford", "Joe Lombardo", "governor"),
    "2026-gov-CA": ("2026 California", "Xavier Becerra", "Steve Hilton", "governor"),
    "2026-gov-NM": ("2026 New Mexico", "Deb Haaland", "Greg Hull", "governor"),
    "2026-gov-IL": ("2026 Illinois", "JB Pritzker", "Darren Bailey", "governor"),

    # Worth re-checking. Each was chosen on recency, but the margin over
    # the next candidate is narrow enough that a withdrawal or a late
    # primary result could have been missed. The weekly drift check will
    # flag any of these if polls stop matching.
    #
    # GA: Rick Jackson leads Republican mentions and shares the latest
    #     field date, but Burt Jones was the better-known contender and
    #     stopped appearing after June.
    "2026-gov-GA": ("2026 Georgia", "Keisha Lance Bottoms", "Rick Jackson", "governor"),
    # WI: Crowley has 9 mentions to Barnes's 7 and a later date, which is a
    #     thin basis. Verify before trusting Wisconsin figures.
    "2026-gov-WI": ("2026 Wisconsin", "David Crowley", "Tom Tiffany", "governor"),
    # ME: Rick Bennett polls at 3 alongside these two and may be running as
    #     an independent, in which case a two-way margin overstates the
    #     Democrat. "Robert Charles" in the data is the same person as
    #     Bobby Charles.
    "2026-gov-ME": ("2026 Maine", "Hannah Pingree", "Bobby Charles", "governor"),
    # NH: appears in the data as both "Cinde" and "Cindy" Warmington. The
    #     matcher works on surname, so either spelling matches.
    "2026-gov-NH": ("2026 New Hampshire", "Cinde Warmington", "Kelly Ayotte", "governor"),
    # MN: Klobuchar leading a governor poll is surprising for a sitting
    #     senator. If she is not actually running this is a hypothetical
    #     matchup and the race should be dropped rather than collected.
"2026-gov-MN": ("2026 Minnesota", "Amy Klobuchar", "Lisa Demuth", "governor"),
}

GENERIC = ("2026-generic-ballot", "2026", None, None)

RAW_FIELDS = [
    "poll_id", "race_id", "subject", "poll_type", "pollster", "sponsors",
    "start_date", "end_date", "sample_size", "population",
    "dem_name", "rep_name", "dem_pct", "rep_pct", "margin",
    "internal", "partisan", "url", "fetched_at", "first_seen",
]


def load_first_seen():
    """When each poll was first observed in the API.

    A poll's field dates are not when it became public. A survey fielded
    1-3 August might be published on the 5th, and a market can only react
    to publication. VoteHub carries no publication timestamp, so the first
    time we see a poll is the closest available proxy.

    This file is rewritten in full on every run, so without carrying these
    values forward they are lost each time. And unlike everything else here
    they cannot be recovered later: a poll observed for the first time next
    month gets next month's timestamp regardless of when it actually
    appeared. Which makes this the one field worth starting today.
    """
    seen = {}
    if not RAW_OUT.exists():
        return seen
    try:
        with RAW_OUT.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                pid = row.get("poll_id")
                first = row.get("first_seen") or row.get("fetched_at")
                if pid and first:
                    # Keep the earliest if a poll somehow appears twice.
                    if pid not in seen or first < seen[pid]:
                        seen[pid] = first
    except (OSError, csv.Error):
        pass
    return seen

AVG_FIELDS = [
    "computed_at", "race_id", "as_of_date", "days_out",
    "margin", "dem_pct", "rep_pct", "n_polls", "effective_n",
    "n_partisan", "partisan_lean", "excluded_partisan",
    "window_days", "half_life_days",
]


def get_json(path: str, params: dict | None = None):
    params = params or {}
    # Subjects contain spaces ("2026 Georgia"), so escape every value.
    query = "&".join(f"{k}={quote(str(v))}"
                     for k, v in params.items() if v is not None)
    url = f"{API}{path}" + (f"?{query}" if query else "")
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT,
                                    "Accept": "application/json"})
        with urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        code = getattr(exc, "code", "")
        print(f"  request failed{f' ({code})' if code else ''}: {exc}",
              file=sys.stderr)
        return None


def poll_list(payload):
    """The API returns a bare array; the docs show {'polls': [...]}.
    Accept either so a future fix on their side does not break this."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("polls") or []
    return []


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def parse_answers(answers, dem_name=None, rep_name=None):
    """Return (dem_pct, rep_pct) or (None, None).

    Two shapes exist. Generic ballot labels answers 'Dem' and 'Rep'. Race
    polls label them by candidate name, which is why dem_name and rep_name
    must be supplied per race.

    Matching is by label, never by position, and an unmatched poll is
    rejected rather than guessed at: a poll of a matchup against a different
    candidate is not a poll of this race, and silently averaging it in is
    exactly the error the Wikipedia scraper had to guard against.
    """
    dem = rep = None
    for a in answers or []:
        choice = str(a.get("choice", "")).strip()
        low = choice.lower()
        try:
            pct = float(a.get("pct"))
        except (TypeError, ValueError):
            continue

        if dem_name and rep_name:
            if dem is None and dem_name.lower() in low:
                dem = pct
            elif rep is None and rep_name.lower() in low:
                rep = pct
        else:
            if dem is None and (low.startswith("dem") or low == "d"):
                dem = pct
            elif rep is None and (low.startswith(("rep", "gop")) or low == "r"):
                rep = pct

    return dem, rep


def fetch_race(race_id, subject, dem_name, rep_name, poll_type, from_date,
               first_seen=None):
    payload = get_json("/polls", {"poll_type": poll_type, "subject": subject,
                                  "from_date": from_date})
    polls = poll_list(payload)
    fetched = datetime.utcnow().isoformat(timespec="seconds")
    first_seen = first_seen or {}

    rows, unmatched = [], 0
    for p in polls:
        dem, rep = parse_answers(p.get("answers"), dem_name, rep_name)
        end = p.get("end_date")
        if dem is None or rep is None or not end:
            unmatched += 1
            continue
        rows.append({
            "poll_id": p.get("id", ""),
            "race_id": race_id,
            "subject": p.get("subject", ""),
            "poll_type": p.get("poll_type", ""),
            "pollster": p.get("pollster", ""),
            "sponsors": "|".join(p.get("sponsors") or []),
            "start_date": p.get("start_date") or end,
            "end_date": end,
            "sample_size": p.get("sample_size") or "",
            "population": p.get("population") or "",
            "dem_name": dem_name or "Dem",
            "rep_name": rep_name or "Rep",
            "dem_pct": dem,
            "rep_pct": rep,
            "margin": round(dem - rep, 2),
            "internal": p.get("internal", ""),
            "partisan": p.get("partisan") or "",
            "url": p.get("url", ""),
            "fetched_at": fetched,
            # Preserved across runs; only a genuinely new poll gets now.
            "first_seen": first_seen.get(p.get("id", ""), fetched),
        })

    return rows, unmatched, len(polls)


# --------------------------------------------------------------------------
# Averaging
# --------------------------------------------------------------------------

def weight(row, as_of: date) -> float:
    """Recency decay times sqrt(sample size), capped.

    sqrt(n) is how standard error actually scales. The cap stops a single
    20,000-person online panel from drowning out five good 800-person live
    caller polls.

    No pollster quality adjustment. Rating pollsters is its own project, and
    an unweighted average is defensible and explainable to a sceptic. House
    effects can be added later; a black-box weight cannot be un-explained.
    """
    end = date.fromisoformat(row["end_date"])
    recency = 0.5 ** (max(0, (as_of - end).days) / HALF_LIFE_DAYS)
    try:
        n = float(row["sample_size"])
    except (TypeError, ValueError):
        n = 600.0
    return recency * math.sqrt(min(n, SAMPLE_CAP))


def average(rows, as_of: date, drop_partisan=False):
    """Trailing weighted mean. Returns None if the window is empty rather
    than quietly reaching further back for something to average."""
    cutoff = as_of - timedelta(days=WINDOW_DAYS)
    live = [r for r in rows
            if cutoff <= date.fromisoformat(r["end_date"]) <= as_of]

    partisan = [r for r in live if r["partisan"]]
    lean = ""
    if partisan:
        # Mean margin of partisan polls minus mean margin of the rest. A
        # large value means the sponsored polls are pulling the average, and
        # a reader deserves to see that rather than a bare poll count.
        others = [r for r in live if not r["partisan"]]
        if others:
            lean = round(
                sum(r["margin"] for r in partisan) / len(partisan)
                - sum(r["margin"] for r in others) / len(others), 2)

    if drop_partisan:
        live = [r for r in live if not r["partisan"]]

    if not live:
        return None

    weights = [weight(r, as_of) for r in live]
    total = sum(weights)
    if total <= 0:
        return None

    return {
        "margin": round(sum(r["margin"] * w for r, w in zip(live, weights)) / total, 2),
        "dem_pct": round(sum(r["dem_pct"] * w for r, w in zip(live, weights)) / total, 2),
        "rep_pct": round(sum(r["rep_pct"] * w for r, w in zip(live, weights)) / total, 2),
        "n_polls": len(live),
        # Kish effective sample size: how many equally-weighted polls this is
        # worth. Drops toward 1 when a single poll dominates.
        "effective_n": round((total ** 2) / sum(w * w for w in weights), 2),
        "n_partisan": len(partisan),
        "partisan_lean": lean,
    }


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------

def probe():
    print("=== poll types ===")
    print(f"  {get_json('/poll-types')}\n")

    subjects = get_json("/subjects")
    for ptype in ("us-senator", "governor"):
        hits = [s for s in (subjects or [])
                if ptype in (s.get("poll_types") or [])]
        print(f"=== {len(hits)} subject(s) with {ptype} polls ===")
        for s in sorted(hits, key=lambda x: str(x.get("subject"))):
            print(f"  {s.get('subject')}")
        print()
    return 0


def sample(code):
    """Accepts a race_id (2026-gov-GA) or a bare state code (GA)."""
    entry = RACES.get(code) or RACES.get(f"2026-senate-{code.upper()}")
    subject = entry[0] if entry else code
    poll_type = entry[3] if entry else "us-senator"
    payload = get_json("/polls", {"poll_type": poll_type, "subject": subject})
    polls = poll_list(payload)
    print(f"{len(polls)} poll(s) for {subject!r}\n")
    for p in polls[:3]:
        print(json.dumps(p, indent=2))
        print()
    if polls:
        names = {a.get("choice") for p in polls for a in p.get("answers") or []}
        print(f"distinct answer labels: {sorted(names)}")
    return 0


def labels():
    """Print the candidates appearing in each race's polls, most-polled first.

    Answer labels mix the real matchup with hypothetical ones, so the two
    names to configure are almost always the two most frequent. Verify the
    pairing rather than assuming: an unopposed-primary candidate can out-
    appear an eventual nominee early in a cycle.
    """
    for race_id, (subject, dem, rep, poll_type) in RACES.items():
        payload = get_json("/polls", {"poll_type": poll_type,
                                      "subject": subject})
        polls = poll_list(payload)
        counts, recent = {}, {}
        for p in polls:
            end = p.get("end_date") or ""
            for a in p.get("answers") or []:
                name = a.get("choice")
                if not name:
                    continue
                counts[name] = counts.get(name, 0) + 1
                if end > recent.get(name, ""):
                    recent[name] = end

        configured = f"  [configured: {dem} vs {rep}]" if dem and rep else ""
        print(f"\n{race_id}  {subject} / {poll_type}  "
              f"({len(polls)} polls){configured}")
        if not counts:
            print("    no polls returned")
            continue
        for name, n in sorted(counts.items(), key=lambda kv: -kv[1])[:8]:
            print(f"    {n:>3} polls  last {recent.get(name, '?')}  {name}")

    print("\nPut the two nominees into RACES at the top of this file.")
    return 0


def write_csv(path, fields, rows, append=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    header_needed = not append or not path.exists()
    with path.open("a" if append else "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if header_needed:
            w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--sample", metavar="STATE", help="e.g. --sample GA")
    ap.add_argument("--labels", action="store_true",
                    help="list candidates per race, to fill in RACES")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-partisan", action="store_true",
                    help="exclude sponsored polls from the average")
    ap.add_argument("--lookback", type=int, default=400)
    args = ap.parse_args()

    if args.probe:
        return probe()
    if args.sample:
        return sample(args.sample)
    if args.labels:
        return labels()

    today = datetime.utcnow().date()
    from_date = (today - timedelta(days=args.lookback)).isoformat()

    first_seen = load_first_seen()
    if first_seen:
        print(f"carrying forward first-seen times for "
              f"{len(first_seen):,} known polls\n")

    targets = [(rid, subj, d, r, ptype)
               for rid, (subj, d, r, ptype) in RACES.items()]
    targets.append((GENERIC[0], GENERIC[1], None, None, "generic-ballot"))

    all_rows, avg_rows = [], []
    print(f"Fetching polls since {from_date}\n")

    for race_id, subject, dem_name, rep_name, poll_type in targets:
        if poll_type != "generic-ballot" and not (dem_name and rep_name):
            print(f"  {race_id:<24} skipped, candidates not configured")
            continue

        rows, unmatched, total = fetch_race(
            race_id, subject, dem_name, rep_name, poll_type, from_date,
            first_seen)
        all_rows.extend(rows)

        avg = average(rows, today, args.no_partisan)
        if not avg:
            print(f"  {race_id:<24} {len(rows)}/{total} parsed, "
                  f"no polls in {WINDOW_DAYS}d window")
            continue

        side = "D" if avg["margin"] >= 0 else "R"
        flag = ""
        if avg["n_partisan"] and avg["partisan_lean"] != "":
            flag = (f"  [{avg['n_partisan']} partisan, "
                    f"lean {avg['partisan_lean']:+.1f}]")
        print(f"  {race_id:<24} {side}+{abs(avg['margin']):<5.1f} "
              f"({avg['n_polls']} polls, eff {avg['effective_n']:.1f})"
              f"{'  ' + str(unmatched) + ' unmatched' if unmatched else ''}"
              f"{flag}")

        avg_rows.append(dict(
            avg,
            computed_at=datetime.utcnow().isoformat(timespec="seconds"),
            race_id=race_id,
            as_of_date=today.isoformat(),
            days_out=(ELECTION_DATE - today).days,
            excluded_partisan=bool(args.no_partisan),
            window_days=WINDOW_DAYS,
            half_life_days=HALF_LIFE_DAYS,
        ))

    fresh = sum(1 for r in all_rows if r["first_seen"] == r["fetched_at"])
    print(f"\n{len(all_rows)} polls, {len(avg_rows)} averages, "
          f"{fresh} newly seen")

    if args.dry_run:
        print("dry run, nothing written")
        return 0
    if not all_rows:
        print("nothing to write", file=sys.stderr)
        return 1

    write_csv(RAW_OUT, RAW_FIELDS, all_rows)
    write_csv(AVG_OUT, AVG_FIELDS, avg_rows, append=True)
    print(f"wrote {RAW_OUT.name}, appended {AVG_OUT.name}")
    print("\nThese are margins, not probabilities. The conversion needs a"
          "\nhistorical polling-error distribution, which is the next step.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
