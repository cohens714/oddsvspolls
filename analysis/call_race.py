#!/usr/bin/env python3
"""call_race.py: record AP race calls in data/outcomes.csv, then commit and push.

Pushing outcomes.csv triggers the scorecard workflow, which rescores and
redeploys the site. So on election night this is the only command you need.

    python3 analysis/call_race.py senate-GA ossoff
    python3 analysis/call_race.py gov-AZ dem senate-IA rep     several at once
    python3 analysis/call_race.py control rep                  Senate control
    python3 analysis/call_race.py --pending senate-GA          undo a call
    python3 analysis/call_race.py --list                       show every race
    python3 analysis/call_race.py --no-push senate-GA rep      edit only, no git

Races: senate-XX, gov-XX (or governor-XX, XX-senate, XX-gov), or control.
Winners: dem, rep, d, r, or the candidate's last name.

The call date defaults to today in Eastern time; use --date 2026-11-04 to
override. A race already called for the other candidate needs --force.
"""
import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
OUTCOMES = ROOT / "data" / "outcomes.csv"
META = ROOT / "data" / "race_meta.json"
FIELDS = ["race_id", "winner", "called_date", "status", "note"]
CYCLE = 2026
POLLS_CLOSE = datetime(2026, 11, 3, 18, 0, tzinfo=ZoneInfo("America/New_York"))


def git(*args, check=True):
    print("$ git " + " ".join(args))
    r = subprocess.run(["git", *args], cwd=ROOT)
    if check and r.returncode != 0:
        sys.exit(f"git {args[0]} failed; paste the output above.")
    return r.returncode


def race_id(token):
    t = token.strip().lower().replace("_", "-")
    if t in ("control", "senate-control"):
        return f"{CYCLE}-senate-control"
    m = (re.fullmatch(r"(senate|sen|gov|governor)-?([a-z]{2})", t)
         or re.fullmatch(r"([a-z]{2})-?(senate|sen|gov|governor)", t))
    if not m:
        return None
    a, b = m.groups()
    office, state = (a, b) if len(b) == 2 and len(a) > 2 else (b, a)
    office = "senate" if office.startswith("sen") else "gov"
    return f"{CYCLE}-{office}-{state.upper()}"


def winner_of(token, meta_row):
    t = token.strip().lower()
    if t in ("dem", "d", "democrat", "democrats"):
        return "dem"
    if t in ("rep", "r", "gop", "republican", "republicans"):
        return "rep"
    for side in ("dem", "rep"):
        names = {(meta_row.get(f"{side}_short") or "").lower(),
                 (meta_row.get(side) or "").lower()}
        names |= {n.split()[-1] for n in names if n}
        if t in names:
            return side
    return None


def describe(rid, winner, meta):
    m = meta.get(rid, {})
    label = m.get("label", rid)
    if not rid.endswith("-control"):
        label += " governor" if "-gov-" in rid else " Senate"
    if not winner:
        return label
    who = m.get(f"{winner}_short") or ("Democrat" if winner == "dem" else "Republican")
    return f"{label}: {who} ({'D' if winner == 'dem' else 'R'})"


def load():
    with OUTCOMES.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def save(rows):
    # Keep the file's existing line endings (scorecard.py wrote it with CRLF),
    # so a single call shows up in git as a single changed line.
    eol = "\r\n" if b"\r\n" in OUTCOMES.read_bytes()[:200] else "\n"
    with OUTCOMES.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator=eol)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def show(rows, meta):
    called = [r for r in rows if r["status"] == "called"]
    print(f"{len(called)} called, {len(rows) - len(called)} pending\n")
    for r in sorted(rows, key=lambda r: (r["status"] != "called", r["race_id"])):
        if r["status"] == "called":
            print(f"  called   {describe(r['race_id'], r['winner'], meta)}  ({r['called_date']})")
        else:
            print(f"  pending  {describe(r['race_id'], None, meta)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("calls", nargs="*", help="RACE WINNER pairs")
    ap.add_argument("--pending", nargs="+", metavar="RACE", help="set races back to pending")
    ap.add_argument("--list", action="store_true", help="show every race and stop")
    ap.add_argument("--date", help="call date, YYYY-MM-DD (default: today, Eastern)")
    ap.add_argument("--note", default="", help="note stored with these calls")
    ap.add_argument("--force", action="store_true", help="allow changing an existing call")
    ap.add_argument("--no-push", action="store_true", help="edit the file only, no git")
    args = ap.parse_args()

    meta = json.loads(META.read_text()) if META.exists() else {}

    if args.list:
        show(load(), meta)
        return

    if not args.calls and not args.pending:
        ap.print_help()
        return
    if len(args.calls) % 2:
        sys.exit("Calls come in pairs: RACE WINNER, e.g.  senate-GA ossoff")

    now_et = datetime.now(ZoneInfo("America/New_York"))
    if args.calls and now_et < POLLS_CLOSE and not args.no_push:
        sys.exit("Polls haven't closed yet (6 p.m. ET, Nov 3). Use --no-push to test.")
    called_date = args.date or now_et.date().isoformat()
    try:
        date.fromisoformat(called_date)
    except ValueError:
        sys.exit(f"--date must look like 2026-11-04, not {called_date!r}")

    if not args.no_push:
        # Bring in whatever the collectors pushed, so this commit lands cleanly.
        git("pull", "--rebase", "--autostash", "origin", "main")

    rows = load()
    by_id = {r["race_id"]: r for r in rows}
    changes, problems = [], []

    def lookup(token):
        rid = race_id(token)
        if rid is None:
            problems.append(f"{token!r} isn't a race; try senate-GA, gov-AZ or control")
        elif rid not in by_id:
            problems.append(f"{rid} isn't in outcomes.csv")
        else:
            return rid
        return None

    for token, who in zip(args.calls[::2], args.calls[1::2]):
        rid = lookup(token)
        if not rid:
            continue
        w = winner_of(who, meta.get(rid, {}))
        if not w:
            m = meta.get(rid, {})
            problems.append(f"{who!r} isn't a candidate in {describe(rid, None, meta)}; "
                            f"use dem/rep or {m.get('dem_short', '?')}/{m.get('rep_short', '?')}")
            continue
        r = by_id[rid]
        if r["status"] == "called" and r["winner"] == w:
            print(f"already recorded: {describe(rid, w, meta)}")
            continue
        if r["status"] == "called" and r["winner"] != w and not args.force:
            problems.append(f"{describe(rid, r['winner'], meta)} is already called; "
                            "add --force to change it")
            continue
        r.update(winner=w, called_date=called_date, status="called",
                 note=args.note or r.get("note", ""))
        changes.append(f"Call {describe(rid, w, meta)}")

    for token in args.pending or []:
        rid = lookup(token)
        if rid and by_id[rid]["status"] == "called":
            by_id[rid].update(winner="", called_date="", status="pending", note=args.note)
            changes.append(f"Uncall {describe(rid, None, meta)}")

    if problems:
        sys.exit("Nothing changed:\n  " + "\n  ".join(problems))
    if not changes:
        print("No changes to make.")
        return

    save(rows)
    print("\n".join(changes))

    if args.no_push:
        print("\nWrote data/outcomes.csv (not committed).")
        return

    msg = changes[0] if len(changes) == 1 else f"{len(changes)} race calls\n\n" + "\n".join(changes)
    git("add", "data/outcomes.csv")
    git("commit", "-m", msg)
    for attempt in range(3):
        if git("pull", "--rebase", "--autostash", "origin", "main", check=False) == 0 \
                and git("push", "origin", "main", check=False) == 0:
            called = sum(r["status"] == "called" for r in rows)
            print(f"\nPushed. {called} of {len(rows)} races called. The scorecard workflow "
                  "rescores and redeploys in a few minutes.")
            return
    sys.exit("Push failed three times. Your commit is saved locally; run  git push  "
             "once the network settles.")


if __name__ == "__main__":
    main()
