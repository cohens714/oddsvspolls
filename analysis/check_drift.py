"""
Detect drift between the configuration and reality.

    python3 check_drift.py             # human-readable report
    python3 check_drift.py --markdown  # for a GitHub issue

Standard library only. Exits 1 when anything needs attention, so a workflow
can act on it.

WHY THIS REPORTS RATHER THAN FIXES
----------------------------------
It is tempting to have a job rewrite nominee names automatically. Do not.
Maine is the standing counter-example: Graham Platner had 20 polls to Troy
Jackson's 3, so any "take the most-polled candidates" rule picks the man who
withdrew from the race, and every Maine figure on the site becomes wrong in
a way that looks entirely plausible.

Config that edits itself while nobody is watching turns a visible failure
into a silent one. So this only ever tells you what changed, and a person
decides.

WHAT IT LOOKS FOR

  unmatched polls    a race where most polls fail to match the configured
                     candidates. The strongest single signal that a nominee
                     changed: Maine showed 31 unmatched before anyone
                     noticed Platner had withdrawn.

  stale candidate    a configured name that has stopped appearing in recent
                     polling while another name has started.

  venue divergence   Polymarket and Kalshi disagreeing by more than a few
                     points. Caught the New Hampshire ticker that pointed at
                     the primary rather than the general.

  new races          contests with polling that the config does not track.

  dead markets       a configured market that has closed or stopped
                     returning a price.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fetch_polls_votehub import (RACES, get_json, poll_list,  # noqa: E402
                                 parse_answers)

RACES_JSON = HERE / "races.json"

# A poll older than this tells you little about who is currently running.
RECENT_DAYS = 60
# Below this share of polls matching the configured pair, assume the
# nominees are wrong rather than that pollsters stopped polling the race.
MATCH_THRESHOLD = 0.5
# Venue prices further apart than this are a data problem, not a trade.
VENUE_GAP = 5.0


class Findings:
    def __init__(self):
        self.items = []

    def add(self, severity, race, title, detail):
        self.items.append({"severity": severity, "race": race,
                           "title": title, "detail": detail})

    @property
    def urgent(self):
        return [i for i in self.items if i["severity"] == "high"]

    def __len__(self):
        return len(self.items)


def check_nominees(findings):
    """Compare configured candidates against who is actually being polled."""
    cutoff = (datetime.utcnow().date() - timedelta(days=RECENT_DAYS)).isoformat()

    for race_id, (subject, dem, rep, poll_type) in sorted(RACES.items()):
        if not dem or not rep:
            findings.add("low", race_id, "no candidates configured",
                         f"{subject} is not being collected")
            continue

        polls = poll_list(get_json("/polls", {"poll_type": poll_type,
                                              "subject": subject}))
        recent = [p for p in polls if str(p.get("end_date", "")) >= cutoff]
        if not recent:
            findings.add("low", race_id, "no recent polls",
                         f"nothing in {RECENT_DAYS} days; cannot check")
            continue

        matched = sum(
            1 for p in recent
            if all(v is not None for v in parse_answers(p.get("answers"),
                                                        dem, rep)))
        rate = matched / len(recent)

        # Who is actually appearing, most recent first.
        names = defaultdict(lambda: "")
        counts = defaultdict(int)
        for p in recent:
            for a in p.get("answers") or []:
                n = a.get("choice")
                if not n:
                    continue
                counts[n] += 1
                end = str(p.get("end_date", ""))
                if end > names[n]:
                    names[n] = end

        if rate < MATCH_THRESHOLD:
            top = sorted(counts.items(), key=lambda kv: -kv[1])[:4]
            listed = ", ".join(f"{n} ({c} polls, last {names[n]})"
                               for n, c in top)
            findings.add(
                "high", race_id, "configured nominees may be wrong",
                f"only {matched}/{len(recent)} recent polls match "
                f"{dem} vs {rep}. Appearing instead: {listed}")
            continue

        # A configured name absent from recent polling while others appear.
        for who, label in ((dem, "Democratic"), (rep, "Republican")):
            surname = who.split()[-1].lower()
            if not any(surname in n.lower() for n in counts):
                findings.add(
                    "high", race_id, f"{label} nominee not in recent polls",
                    f"{who} has not appeared in {RECENT_DAYS} days; "
                    f"seen instead: {', '.join(sorted(counts)[:5])}")


def check_markets(findings):
    """Config points at markets that still trade and still agree."""
    if not RACES_JSON.exists():
        return
    cfg = json.loads(RACES_JSON.read_text())

    for race in cfg.get("races", []):
        race_id = race["race_id"]
        prices = {}

        slug = race.get("polymarket_slug")
        if slug:
            payload = get_json_abs(
                "https://gamma-api.polymarket.com/markets", {"slug": slug})
            if not payload:
                findings.add("high", race_id, "Polymarket market missing",
                             f"no market for slug {slug}")
            else:
                m = payload[0] if isinstance(payload, list) else payload
                if m.get("closed"):
                    findings.add("medium", race_id, "Polymarket market closed",
                                 f"{slug} has settled")
                raw = m.get("outcomePrices")
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except json.JSONDecodeError:
                        raw = None
                if raw:
                    prices["polymarket"] = float(raw[0])

        ticker = race.get("kalshi_ticker")
        if ticker:
            payload = get_json_abs(
                f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}")
            m = (payload or {}).get("market")
            if not m:
                findings.add("high", race_id, "Kalshi market missing",
                             f"no market for ticker {ticker}")
            else:
                if m.get("status") in ("finalized", "determined"):
                    findings.add("medium", race_id, "Kalshi market settled",
                                 f"{ticker} status {m.get('status')}")
                last = m.get("last_price_dollars")
                if last:
                    prices["kalshi"] = float(last)

        if len(prices) == 2:
            gap = abs(prices["polymarket"] - prices["kalshi"]) * 100
            if gap > VENUE_GAP:
                findings.add(
                    "high", race_id, "venues disagree",
                    f"Polymarket {prices['polymarket']:.3f} vs Kalshi "
                    f"{prices['kalshi']:.3f} ({gap:.1f} points). Usually a "
                    f"contract mismatch: check rules_primary on the Kalshi "
                    f"market.")


def get_json_abs(url, params=None):
    """get_json in the poll module is relative to the VoteHub base."""
    import json as _json
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    params = params or {}
    query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    full = f"{url}?{query}" if query else url
    try:
        req = Request(full, headers={"User-Agent": "oddsvspolls.com drift check"})
        with urlopen(req, timeout=30) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, _json.JSONDecodeError):
        return None


def check_new_races(findings):
    """Senate contests with polling that the config does not track."""
    subjects = get_json("/subjects") or []
    # Tracked as (subject, poll_type): Georgia appears for both offices, so
    # the subject alone would wrongly mark the governor race as covered.
    tracked = {(subject, ptype) for subject, _, _, ptype in RACES.values()}

    for s in subjects:
        subject = s.get("subject")
        types = s.get("poll_types") or []
        for ptype in ("us-senator", "governor"):
            if ptype in types and (subject, ptype) not in tracked:
                break
        else:
            continue
        if not str(subject).startswith("2026"):
            continue
        # Party-specific subjects are primaries, not races.
        if any(w in str(subject) for w in ("Democratic", "Republican")):
            continue
        findings.add("medium", f"{subject} / {ptype}", "untracked race",
                     f"{subject} has {ptype} polling but is not in RACES")


def report(findings, markdown=False):
    if not len(findings):
        print("No drift detected." if not markdown
              else "No drift detected. Nothing to do.")
        return 0

    order = {"high": 0, "medium": 1, "low": 2}
    items = sorted(findings.items, key=lambda i: order[i["severity"]])

    if markdown:
        print("Automated check found configuration drift. Each item needs a "
              "human decision; nothing has been changed.\n")
        for sev in ("high", "medium", "low"):
            chunk = [i for i in items if i["severity"] == sev]
            if not chunk:
                continue
            print(f"\n### {sev.title()}\n")
            for i in chunk:
                print(f"- **{i['race']}** — {i['title']}  \n  {i['detail']}")
        print("\n---\n")
        print("Nominee changes go in `RACES` in `analysis/"
              "fetch_polls_votehub.py`. Market changes: rerun "
              "`build_races.py --write` and `fetch_markets_kalshi.py "
              "--map-races --write`, then check the diff.")
    else:
        for i in items:
            print(f"[{i['severity']:<6}] {i['race']:<24} {i['title']}")
            print(f"           {i['detail']}")

    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--skip-markets", action="store_true")
    args = ap.parse_args()

    findings = Findings()
    check_nominees(findings)
    check_new_races(findings)
    if not args.skip_markets:
        check_markets(findings)

    return report(findings, args.markdown)


if __name__ == "__main__":
    sys.exit(main())
