#!/usr/bin/env python3
"""
Adds a 2028 presidential winner board to the nominees tab.

    python3 add_president_board.py           install, dry-run the collector, build
    python3 add_president_board.py --ship    commit, collect, backfill, push
    python3 add_president_board.py --revert  undo, if you haven't shipped yet
"""
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

UA = "oddsvspolls/1.0 (+https://oddsvspolls.com)"
KALSHI_EVENT = "KXPRESPERSON-28"
SLUG_GUESSES = ["presidential-election-winner-2028", "2028-presidential-election-winner"]
EVENT_ID = "2028-president"

INGEST = os.path.join("analysis", "ingest_nominees.py")
NOM = os.path.join("src", "Nominees.jsx")
SNAP = os.path.join("public", "nominee_snapshots.csv")


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def run(cmd, **kw):
    print("$ " + " ".join(cmd))
    return subprocess.run(cmd, **kw)


def repo_root():
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("Run this from inside the oddsvspolls repo.")
    return r.stdout.strip()


def revert(quiet=False):
    subprocess.run(["git", "checkout", "--", INGEST, NOM])
    if not quiet:
        print("Reverted ingest_nominees.py and Nominees.jsx.")


# ---------------- lookups ----------------

def find_polymarket_slug():
    for slug in SLUG_GUESSES:
        try:
            data = fetch_json(f"https://gamma-api.polymarket.com/events?slug={slug}")
        except Exception:
            continue
        if isinstance(data, list) and data and data[0].get("markets"):
            return slug, data[0].get("title", "")
    q = urllib.parse.quote("presidential election winner 2028")
    data = fetch_json(f"https://gamma-api.polymarket.com/public-search?q={q}")
    for ev in data.get("events") or []:
        t = (ev.get("title") or "").lower()
        if "2028" in t and "winner" in t and "presiden" in t and "nomin" not in t:
            return ev.get("slug"), ev.get("title", "")
    return None, None


def check_kalshi():
    data = fetch_json("https://api.elections.kalshi.com/trade-api/v2/events/"
                      f"{KALSHI_EVENT}?with_nested_markets=true")
    markets = (data.get("event") or {}).get("markets") or []
    if not markets:
        return 0, ""
    return len(markets), markets[0].get("rules_primary", "")


# ---------------- patches ----------------

RULES_HELPER = '''def rules_ok(rules, ev, venue):
    """Every must_contain phrase has to appear, and no must_not_contain phrase."""
    need = ev[f"{venue}_rules_must_contain"]
    need = [need] if isinstance(need, str) else need
    avoid = ev.get(f"{venue}_rules_must_not_contain", [])
    return all(n in rules for n in need) and not any(a in rules for a in avoid)


def to_float(v):'''


def ingest_patches(slug):
    entry = (
        "EVENTS = [\n"
        "    # Winner board. The guard is inverted from the nominee boards: the\n"
        "    # rules must mention the presidency and must NOT mention a\n"
        "    # nomination, so a nominee contract can never land here.\n"
        "    {\n"
        f'        "event_id": "{EVENT_ID}",\n'
        '        "party": "ANY",\n'
        f'        "kalshi_event": "{KALSHI_EVENT}",\n'
        f'        "polymarket_slug": "{slug}",\n'
        '        "kalshi_rules_must_contain": ["presiden"],\n'
        '        "kalshi_rules_must_not_contain": ["nominat"],\n'
        '        "polymarket_rules_must_contain": ["presiden"],\n'
        '        "polymarket_rules_must_not_contain": ["nominat"],\n'
        "    },\n"
    )
    return [
        ("EVENTS", "EVENTS = [\n", entry),
        ("rules helper", "def to_float(v):", RULES_HELPER),
        ("kalshi check", 'if ev["kalshi_rules_must_contain"] not in rules:',
         'if not rules_ok(rules, ev, "kalshi"):'),
        ("polymarket check", 'if ev["polymarket_rules_must_contain"] not in rules:',
         'if not rules_ok(rules, ev, "polymarket"):'),
    ]


NOM_PATCHES = [
    ("boards", "const BOARDS = [\n",
     "const BOARDS = [\n  { id: '2028-president', title: 'Presidency' },\n"),
    ("tab label", ">2028 nominees</a>", ">2028 election</a>"),
    ("lede",
     "        Polymarket and Kalshi prices for each party&rsquo;s 2028 presidential\n"
     "        nomination, side by side and tracked over time. There is no polling\n"
     "        column here: primary polls measure a share of the vote, not a chance\n"
     "        of winning, so the two can&rsquo;t be compared directly.\n",
     "        Polymarket and Kalshi prices for who wins the White House in 2028,\n"
     "        and for each party&rsquo;s nomination, side by side and tracked over\n"
     "        time. There is no polling column here: this far out, primary polls\n"
     "        measure a share of the vote and general-election polls test matchups\n"
     "        that may never happen, so neither maps onto a chance of winning.\n"),
    ("legend", "chance of winning the nomination</span>", "chance of winning</span>"),
    ("footer polls",
     "          Rather than invent a conversion, this page shows the markets alone.\n",
     "          General-election polls this early can only test hypothetical\n"
     "          matchups. Rather than invent a conversion, this page shows the\n"
     "          markets alone.\n"),
    ("footer sum", "forces a\n          party&rsquo;s board to sum", "forces a\n          board to sum"),
    ("footer presidency",
     "        <p className=\"caveat\">\n          <strong>When the venues disagree.</strong>",
     "        <p className=\"caveat\">\n"
     "          <strong>Reading the presidency board.</strong> A presidency\n"
     "          contract only pays if its candidate wins the nomination and then\n"
     "          the general election, so each price there should sit at or below\n"
     "          the same person&rsquo;s nomination price. Dividing one by the\n"
     "          other gives the market&rsquo;s view of how that candidate would do\n"
     "          as the nominee.\n"
     "        </p>\n"
     "        <p className=\"caveat\">\n          <strong>When the venues disagree.</strong>"),
]


def apply(path, patches, marker):
    text = open(path).read()
    if marker in text:
        print(f"{path} already patched")
        return False
    for name, old, new in patches:
        n = text.count(old)
        if n != 1:
            sys.exit(f"{path}: expected the '{name}' spot once, found {n}. Nothing "
                     "changed there. Upload the file and I'll adjust.")
    for name, old, new in patches:
        text = text.replace(old, new, 1)
    open(path, "w").write(text)
    print(f"patched {path}")
    return True


# ---------------- steps ----------------

def install():
    for p in (INGEST, NOM):
        if not os.path.exists(p):
            sys.exit(f"Can't find {p}; nothing changed.")
    installed = EVENT_ID in open(INGEST).read()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", INGEST, NOM],
                           capture_output=True, text=True).stdout.strip()
    if dirty and not installed:
        sys.exit(f"Uncommitted edits in these files:\n{dirty}\nCommit or stash them first.")

    run(["git", "pull", "--rebase", "--autostash", "origin", "main"])

    if not installed:
        print("\nlooking up the markets...")
        n, rules = check_kalshi()
        if not n:
            sys.exit(f"Kalshi {KALSHI_EVENT} returned no markets; nothing changed.")
        print(f"  kalshi {KALSHI_EVENT}: {n} markets")
        print(f"  sample rules: {rules[:140]}")
        slug, title = find_polymarket_slug()
        if not slug:
            sys.exit("Couldn't find the Polymarket winner event; nothing changed.")
        print(f"  polymarket: {slug}  ({title})\n")
        apply(INGEST, ingest_patches(slug), f'"{EVENT_ID}"')

    print("\ndry-running the collector...\n")
    r = subprocess.run([sys.executable, "ingest_nominees.py", "--dry-run"],
                       cwd="analysis", capture_output=True, text=True)
    lines = (r.stdout + r.stderr).splitlines()
    print("\n".join(l for l in lines if l.startswith(EVENT_ID) or "FAILED" in l
                    or "dry run" in l))
    if r.returncode != 0:
        revert(quiet=True)
        sys.exit("\nCollector dry run failed, so everything was put back. "
                 "Paste the output above.")

    apply(NOM, NOM_PATCHES, "'2028-president'")

    print("\nbuilding to check it compiles...")
    b = subprocess.run(["npm", "run", "build"], capture_output=True, text=True)
    if b.returncode != 0:
        print(b.stdout[-3000:], b.stderr[-3000:], sep="\n")
        revert(quiet=True)
        sys.exit("\nBuild failed, so everything was put back. Paste the output above.")
    print("build ok\n")
    print("The board appears once there's a live snapshot for it, which --ship\n"
          "collects. To ship:\n"
          "  python3 analysis/add_president_board.py --ship\n"
          "To undo instead:\n"
          "  python3 analysis/add_president_board.py --revert")


def ship():
    if EVENT_ID not in open(INGEST).read():
        sys.exit("Not installed yet; run without --ship first.")
    steps = [
        ["git", "add", INGEST, NOM],
        ["git", "commit", "-m", "Add 2028 presidential winner board"],
    ]
    for cmd in steps:
        if run(cmd).returncode != 0:
            sys.exit("git step failed; paste the output.")

    print("\ncollecting a live snapshot so the board shows up right away...")
    if run([sys.executable, "ingest_nominees.py"], cwd="analysis").returncode != 0:
        sys.exit("Collection failed; code is committed locally but not pushed. "
                 "Paste the output.")
    for cmd in (["git", "add", SNAP],
                ["git", "commit", "-m", "Nominee snapshot with presidency board"],
                ["git", "pull", "--rebase", "--autostash", "origin", "main"],
                ["git", "push", "origin", "main"]):
        if run(cmd).returncode != 0:
            sys.exit("git step failed; paste the output.")

    print("\nrebuilding the backfill to include the presidency board "
          "(a few minutes)...\n")
    if run([sys.executable, "backfill_nominees.py"], cwd="analysis").returncode != 0:
        sys.exit("\nThe board is live, but the backfill didn't finish. Paste the output.")
    print("\nAll shipped. Check https://oddsvspolls.com/#nominees in a couple of minutes.")


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
