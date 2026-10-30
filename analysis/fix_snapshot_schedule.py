#!/usr/bin/env python3
"""fix_snapshot_schedule.py: make market snapshots actually land through election day.

The snapshot workflow is scheduled at minute 0 every hour, the busiest minute
on GitHub's scheduler, and most of those runs are being dropped: the repo has
been getting 3 to 5 snapshots a day instead of 24. On election day that would
leave the last pre-cutoff reading hours stale.

This changes snapshot.yml to:
  * three off-peak minutes an hour (07, 27, 47), so a dropped run is covered
    by the next one twenty minutes later
  * every 10 minutes on Nov 3 from 8 a.m. to 6:59 p.m. Eastern (13:00 to 23:59
    UTC), so the final readings before the 6 p.m. cutoff are fresh

It also moves calls.yml (if installed) off :30 to 13 and 43 past the hour.

Every row records its own fetched_at, so extra runs need no change downstream:
the scorecard already takes each venue's last reading per day.

Run from anywhere inside the repo:
    python3 analysis/fix_snapshot_schedule.py            patch and show the diff
    python3 analysis/fix_snapshot_schedule.py --ship     commit and push
    python3 analysis/fix_snapshot_schedule.py --revert   undo, if you haven't shipped
"""
import os
import subprocess
import sys

SNAP = os.path.join(".github", "workflows", "snapshot.yml")
CALLS = os.path.join(".github", "workflows", "calls.yml")
SELF = os.path.join("analysis", "fix_snapshot_schedule.py")

SNAP_OLD = '    - cron: "0 * * * *"\n'
SNAP_NEW = (
    '    # Off the top of the hour, where GitHub drops most scheduled runs.\n'
    '    # Three a hour so one dropped run is covered by the next.\n'
    '    - cron: "7,27,47 * * * *"\n'
    '    # Election day, 8 a.m. to 6:59 p.m. Eastern (UTC times): every 10\n'
    '    # minutes so the last reading before the 6 p.m. cutoff is fresh.\n'
    '    # Harmless to leave in afterwards; it only matches Nov 3.\n'
    '    - cron: "3,13,23,33,43,53 13-23 3 11 *"\n'
)
CALLS_OLD = '    - cron: "30 * * * *"\n'
CALLS_NEW = '    - cron: "13,43 * * * *"\n'


def run(cmd, **kw):
    print("$ " + " ".join(cmd))
    return subprocess.run(cmd, **kw)


def repo_root():
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("Run this from inside the oddsvspolls repo.")
    return r.stdout.strip()


def tracked(p):
    return subprocess.run(["git", "ls-files", "--error-unmatch", p],
                          capture_output=True).returncode == 0


def patch(path, old, new, label):
    if not os.path.exists(path):
        print(f"{label}: not installed, skipped")
        return False
    s = open(path).read()
    if new in s:
        print(f"{label}: already patched")
        return True
    if s.count(old) != 1:
        sys.exit(f"{label}: couldn't find its schedule line ({old.strip()}). "
                 "Nothing changed; paste this message.")
    open(path, "w").write(s.replace(old, new, 1))
    print(f"{label}: patched")
    return True


def install():
    run(["git", "pull", "--rebase", "--autostash", "origin", "main"])
    patch(SNAP, SNAP_OLD, SNAP_NEW, "snapshot.yml")
    calls = patch(CALLS, CALLS_OLD, CALLS_NEW, "calls.yml")
    print()
    run(["git", "--no-pager", "diff", "--", SNAP] + ([CALLS] if calls and tracked(CALLS) else []))
    print("\nIf that looks right:\n"
          "  python3 analysis/fix_snapshot_schedule.py --ship\n"
          "Otherwise:\n"
          "  python3 analysis/fix_snapshot_schedule.py --revert")


def revert():
    for p, old, new in ((SNAP, SNAP_OLD, SNAP_NEW), (CALLS, CALLS_OLD, CALLS_NEW)):
        if os.path.exists(p):
            s = open(p).read()
            if new in s:
                open(p, "w").write(s.replace(new, old, 1))
    print("Schedules put back.")


def ship():
    if SNAP_NEW not in open(SNAP).read():
        sys.exit("Not patched yet; run without --ship first.")
    paths = [SNAP, SELF] + ([CALLS] if os.path.exists(CALLS) else [])
    for cmd in (["git", "add"] + paths,
                ["git", "commit", "-m", "Move snapshot schedule off the top of the hour; dense on election day"],
                ["git", "pull", "--rebase", "--autostash", "origin", "main"],
                ["git", "push", "origin", "main"]):
        if run(cmd).returncode != 0:
            sys.exit("git step failed; paste the output.")
    print("\nShipped. Snapshot commits should start showing up two to three times\n"
          "an hour. Check the commit list in an hour or two.")


def main():
    os.chdir(repo_root())
    if "--revert" in sys.argv:
        revert()
    elif "--ship" in sys.argv:
        ship()
    else:
        install()


if __name__ == "__main__":
    main()
