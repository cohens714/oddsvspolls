#!/usr/bin/env python3
"""auto_calls.py: record race calls in data/outcomes.csv from settled markets.

Runs hourly inside the snapshot workflow after polls close. For every race
still pending, it asks both venues whether their market has settled:

  Polymarket  closed, with outcomePrices at exactly 1 and 0. Polymarket's
              race markets resolve once AP, Fox News and NBC all call the
              race for the same candidate, so this is a free stand-in for
              an AP feed that lags it by a few hours.
  Kalshi      status determined/settled/finalized with a yes/no result.
              Often waits for certification, so it mostly serves as a
              cross-check, but a Kalshi settlement alone is also accepted.

A race is called when at least one venue has settled and no venue disagrees.
If the venues disagree, or a settled market disagrees with a call already in
outcomes.csv, nothing is written for that race and it is listed as a
conflict for a person to look at (the workflow opens an issue).

Calls already in outcomes.csv are never changed. Manual calls made with
call_race.py always win.

    python3 auto_calls.py                 the real run (does nothing before polls close)
    python3 auto_calls.py --dry-run       show what each venue says now; writes nothing
    python3 auto_calls.py --self-test     offline check of the settlement parsers
    python3 auto_calls.py --probe-2024    read resolved 2024 markets to confirm the
                                          live API fields still look as expected

Standard library only, like the collector.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG = HERE / "races.json"
OUTCOMES = ROOT / "data" / "outcomes.csv"
META = ROOT / "data" / "race_meta.json"
FIELDS = ["race_id", "winner", "called_date", "status", "note"]

POLYMARKET_GAMMA = "https://gamma-api.polymarket.com/markets"
POLYMARKET_EVENTS = "https://gamma-api.polymarket.com/events"
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2/markets"
USER_AGENT = "oddsvspolls.com race-call checker (+https://oddsvspolls.com)"
TIMEOUT = 20
DELAY = 0.3
BUDGET_S = 6 * 60          # stop checking new races after this; finish what we have
DOWN_AFTER = 3             # consecutive failures before a venue is skipped this run
_failures = {"polymarket": 0, "kalshi": 0}

ET = ZoneInfo("America/New_York")
POLLS_CLOSE = datetime(2026, 11, 3, 18, 0, tzinfo=ET)
KALSHI_DONE = {"determined", "settled", "finalized"}
SIDES = {"DEM": "dem", "REP": "rep"}


# ------------------------------------------------------------------ http

def get_json(url: str, params: dict | None = None):
    query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    full = f"{url}?{query}" if query else url
    req = Request(full, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last = None
    for attempt in range(3):
        try:
            with urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                raise
            last = exc
        except (URLError, TimeoutError) as exc:
            last = exc
        time.sleep(2 * (attempt + 1))
    raise last


# ------------------------------------------------------------------ parsers

def parse_time(v):
    """ISO time from either venue, on any Python 3.9+.

    Polymarket writes "2024-11-06 16:23:45+00" and Kalshi "...Z"; before 3.11
    fromisoformat accepts neither, so normalize to "+00:00" first.
    """
    t = str(v).strip().replace(" ", "T")
    t = re.sub(r"Z$", "+00:00", t)
    t = re.sub(r"([+-]\d{2})$", r"\1:00", t)
    t = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", t)
    t = re.sub(r"(\.\d{6})\d+", r"\1", t)          # trim nanoseconds
    t = re.sub(r"\.(\d{1,5})(?=[+-]|$)", lambda m: "." + m.group(1).ljust(6, "0"), t)
    when = datetime.fromisoformat(t)
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def polymarket_settlement(payload, outcome_label: str = "Yes"):
    """(resolved, label_won: bool|None, when: datetime|None, detail: str).

    Resolved means closed with prices exactly 1 and 0. A closed market whose
    prices are still fractional is mid-resolution (UMA proposal or dispute)
    and counts as not resolved yet.
    """
    market = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(market, dict) or not market:
        raise ValueError("no market returned")
    outcomes = market.get("outcomes")
    prices = market.get("outcomePrices")
    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)
    if isinstance(prices, str):
        prices = json.loads(prices)
    if not outcomes or not prices or len(outcomes) != len(prices):
        raise ValueError(f"malformed outcomes/prices: {outcomes} {prices}")
    labels = [str(o).strip().lower() for o in outcomes]
    if outcome_label.lower() not in labels:
        raise ValueError(f"outcome {outcome_label!r} not in {outcomes}")
    p = float(prices[labels.index(outcome_label.lower())])
    uma = str(market.get("umaResolutionStatus") or "").lower()
    closed = bool(market.get("closed"))
    detail = f"closed={closed} uma={uma or '-'} {outcome_label}={p:g}"
    if not closed or p not in (0.0, 1.0) or uma in ("proposed", "disputed", "challenged"):
        return False, None, None, detail
    when = None
    for key in ("closedTime", "umaEndDate", "endDate"):
        v = market.get(key)
        if v:
            try:
                when = parse_time(v)
                break
            except ValueError:
                continue
    return True, p == 1.0, when, detail


def kalshi_settlement(payload):
    """(resolved, yes_won: bool|None, when: datetime|None, detail: str)."""
    m = payload.get("market", payload) if isinstance(payload, dict) else {}
    if not m:
        raise ValueError("no market returned")
    status = str(m.get("status") or "").lower()
    result = str(m.get("result") or "").lower()
    detail = f"status={status or '-'} result={result or '-'}"
    if status not in KALSHI_DONE or result not in ("yes", "no"):
        return False, None, None, detail
    when = None
    for key in ("settlement_ts", "expiration_time", "close_time"):
        v = m.get(key)
        if v:
            try:
                when = parse_time(v)
                break
            except ValueError:
                continue
    return True, result == "yes", when, detail


# ------------------------------------------------------------------ per race

def check_race(race: dict) -> dict:
    """What each venue says about who won, as 'dem'/'rep'/None."""
    out = {"race_id": race["race_id"], "venues": {}, "errors": [], "when": []}
    yes_side = SIDES.get(race["yes_side"])
    other = "rep" if yes_side == "dem" else "dem"

    if race.get("polymarket_slug") and _failures["polymarket"] < DOWN_AFTER:
        try:
            payload = get_json(POLYMARKET_GAMMA, {"slug": race["polymarket_slug"]})
            ok, won, when, detail = polymarket_settlement(
                payload, race.get("polymarket_outcome") or "Yes")
            # polymarket_outcome names the yes_side party ("Will the
            # Democrats win..."). A No means that party lost; in a two-party
            # race (check_two_party.py keeps it that way) the other one won.
            out["venues"]["polymarket"] = (yes_side if won else other) if ok else None
            out["polymarket_detail"] = detail
            if ok and when:
                out["when"].append(when)
            _failures["polymarket"] = 0
        except Exception as exc:
            _failures["polymarket"] += 1
            out["errors"].append(f"polymarket: {type(exc).__name__}: {exc}"[:200])
        time.sleep(DELAY)

    if race.get("kalshi_ticker") and _failures["kalshi"] < DOWN_AFTER:
        try:
            payload = get_json(f"{KALSHI_API}/{race['kalshi_ticker']}")
            ok, yes_won, when, detail = kalshi_settlement(payload)
            means = SIDES.get(race.get("kalshi_yes_means") or race["yes_side"])
            flip = "rep" if means == "dem" else "dem"
            out["venues"]["kalshi"] = (means if yes_won else flip) if ok else None
            out["kalshi_detail"] = detail
            if ok and when:
                out["when"].append(when)
            _failures["kalshi"] = 0
        except Exception as exc:
            _failures["kalshi"] += 1
            out["errors"].append(f"kalshi: {type(exc).__name__}: {exc}"[:200])
        time.sleep(DELAY)

    return out


def decide(check: dict, recorded: str | None):
    """('call', winner) | ('conflict', reason) | ('wait', reason) | ('ok', '')."""
    said = {v: w for v, w in check["venues"].items() if w}
    winners = set(said.values())
    if len(winners) > 1:
        return "conflict", "venues disagree: " + ", ".join(f"{v} says {w}" for v, w in said.items())
    if recorded:
        if winners and recorded not in winners:
            return "conflict", (f"outcomes.csv has {recorded} but "
                                + ", ".join(f"{v} settled {w}" for v, w in said.items()))
        return "ok", ""
    if not winners:
        return "wait", "not settled"
    return "call", winners.pop()


# ------------------------------------------------------------------ files

def load_outcomes():
    raw = OUTCOMES.read_bytes()
    eol = "\r\n" if b"\r\n" in raw[:200] else "\n"
    with OUTCOMES.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh)), eol


def save_outcomes(rows, eol):
    with OUTCOMES.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator=eol)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def label(rid, winner, meta):
    m = meta.get(rid, {})
    name = m.get("label", rid)
    if not rid.endswith("-control"):
        name += " governor" if "-gov-" in rid else " Senate"
    if winner:
        who = m.get(f"{winner}_short") or winner
        name += f": {who} ({'D' if winner == 'dem' else 'R'})"
    return name


def set_output(key, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as fh:
            fh.write(f"{key}={value}\n")


# ------------------------------------------------------------------ modes

def run(dry_run: bool, report_path: str | None):
    now = datetime.now(ET)
    if now < POLLS_CLOSE and not dry_run:
        print("Polls haven't closed yet (6 p.m. ET, Nov 3); nothing to do.")
        set_output("calls", 0)
        return 0

    config = json.loads(CONFIG.read_text())
    races = {r["race_id"]: r for r in config["races"]}
    meta = json.loads(META.read_text()) if META.exists() else {}
    rows, eol = load_outcomes()

    calls, conflicts, errors = [], [], []
    started = time.monotonic()
    for row in rows:
        if time.monotonic() - started > BUDGET_S:
            errors.append("time budget reached; remaining races wait for next hour")
            break
        rid = row["race_id"]
        race = races.get(rid)
        if not race:
            continue
        recorded = row["winner"] if row["status"] == "called" else None
        check = check_race(race)
        action, info = decide(check, recorded)
        venues = " | ".join(filter(None, [
            f"poly {check.get('polymarket_detail', '-')}" if race.get("polymarket_slug") else "",
            f"kalshi {check.get('kalshi_detail', '-')}" if race.get("kalshi_ticker") else ""]))
        if check["errors"]:
            errors.append(f"{label(rid, None, meta)}: " + "; ".join(check["errors"]))
        if dry_run:
            state = {"call": f"WOULD CALL {info}", "conflict": f"CONFLICT {info}",
                     "wait": "pending", "ok": f"already called {recorded}"}[action]
            print(f"{rid:<22} {state:<28} {venues}")
            continue
        if action == "call":
            when = min(check["when"]).astimezone(ET) if check["when"] else now
            settled_by = " + ".join(v for v, w in check["venues"].items() if w)
            row.update(winner=info, called_date=when.date().isoformat(),
                       status="called", note=f"auto: {settled_by} settled")
            calls.append(label(rid, info, meta))
        elif action == "conflict":
            conflicts.append(f"{label(rid, None, meta)}: {info}")

    if dry_run:
        for e in errors:
            print("error:", e)
        return 0

    if calls:
        save_outcomes(rows, eol)
        print("Recorded calls:\n  " + "\n  ".join(calls))
    else:
        print("No new calls.")
    if conflicts:
        print("Conflicts (left alone):\n  " + "\n  ".join(conflicts))
    if errors:
        print("Fetch errors (will retry next hour):\n  " + "\n  ".join(errors))

    set_output("calls", len(calls))
    set_output("conflicts", len(conflicts))
    if report_path and conflicts:
        Path(report_path).write_text(
            "The hourly race-call check found settled markets that disagree with "
            "each other or with `data/outcomes.csv`. Nothing was written for these "
            "races. Check the AP call and fix it with `call_race.py` "
            "(add `--force` to change an existing call).\n\n"
            + "\n".join(f"- {c}" for c in conflicts) + "\n")
    return 0


def self_test():
    poly = lambda closed, prices, uma="resolved": [{
        "closed": closed, "outcomes": '["Yes", "No"]',
        "outcomePrices": json.dumps([str(p) for p in prices]),
        "umaResolutionStatus": uma, "closedTime": "2026-11-04 15:20:11+00"}]
    cases = [
        (polymarket_settlement(poly(False, [0.97, 0.03]))[:2], (False, None)),
        (polymarket_settlement(poly(True, [0.999, 0.001]))[:2], (False, None)),
        (polymarket_settlement(poly(True, [1, 0], "proposed"))[:2], (False, None)),
        (polymarket_settlement(poly(True, [1, 0]))[:2], (True, True)),
        (polymarket_settlement(poly(True, ["0", "1"]))[:2], (True, False)),
        (polymarket_settlement(poly(True, [1, 0]))[2].date().isoformat(), "2026-11-04"),
        (parse_time("2026-11-04 03:10:11+00").isoformat(), "2026-11-04T03:10:11+00:00"),
        (parse_time("2026-11-04T03:10:11Z").isoformat(), "2026-11-04T03:10:11+00:00"),
        (parse_time("2026-11-04T03:10:11.123Z").isoformat(), "2026-11-04T03:10:11.123000+00:00"),
        (parse_time("2026-11-04T03:10:11.123456789+00:00").isoformat(), "2026-11-04T03:10:11.123456+00:00"),
        (parse_time("2026-11-04 03:10:11").isoformat(), "2026-11-04T03:10:11+00:00"),
        (kalshi_settlement({"market": {"status": "active", "result": ""}})[:2], (False, None)),
        (kalshi_settlement({"market": {"status": "closed", "result": "yes"}})[:2], (False, None)),
        (kalshi_settlement({"market": {"status": "determined", "result": "no"}})[:2], (True, False)),
        (kalshi_settlement({"market": {"status": "finalized", "result": "yes"}})[:2], (True, True)),
        (decide({"venues": {"polymarket": "dem", "kalshi": None}}, None), ("call", "dem")),
        (decide({"venues": {"polymarket": None, "kalshi": "rep"}}, None), ("call", "rep")),
        (decide({"venues": {"polymarket": "dem", "kalshi": "rep"}}, None)[0], "conflict"),
        (decide({"venues": {"polymarket": None, "kalshi": None}}, None)[0], "wait"),
        (decide({"venues": {"polymarket": "dem"}}, "dem"), ("ok", "")),
        (decide({"venues": {"polymarket": "rep"}}, "dem")[0], "conflict"),
        (decide({"venues": {"polymarket": None}}, "dem"), ("ok", "")),
    ]
    bad = [(i, got, want) for i, (got, want) in enumerate(cases) if got != want]
    for i, got, want in bad:
        print(f"case {i}: got {got!r}, expected {want!r}")
    print(f"self-test: {len(cases) - len(bad)}/{len(cases)} passed")
    return 1 if bad else 0


def probe_2024():
    """Read markets whose results are known, to confirm the field names."""
    ok = True
    print("Polymarket: 2024 Pennsylvania Senate (McCormick, R, won)")
    try:
        ev = get_json(POLYMARKET_EVENTS, {"slug": "pennsylvania-us-senate-election-winner"})
        ev = ev[0] if isinstance(ev, list) else ev
        found = False
        for m in ev.get("markets", []):
            q = m.get("question", "")
            res = polymarket_settlement([m])
            print(f"  {q[:60]:<60} resolved={res[0]} yes_won={res[1]}  ({res[3]})")
            found = found or res[0]
        ok = ok and found
        if not found:
            print("  !! no market read as resolved; the field names may have changed")
    except Exception as exc:
        ok = False
        print(f"  !! {type(exc).__name__}: {exc}")

    print("\nKalshi: settled markets in a 2024 Senate series")
    try:
        found = False
        for series in ("SENATEPA", "KXSENATEPA"):
            payload = get_json(KALSHI_API, {"series_ticker": series, "limit": 10})
            for m in payload.get("markets", [])[:6]:
                res = kalshi_settlement({"market": m})
                print(f"  {m.get('ticker', ''):<28} resolved={res[0]} yes_won={res[1]}  ({res[3]})")
                found = found or res[0]
            if found:
                break
        if not found:
            print("  (no settled market found in those series; not conclusive, "
                  "Kalshi is the cross-check rather than the trigger)")
    except Exception as exc:
        print(f"  !! {type(exc).__name__}: {exc}")

    print("\nprobe:", "OK, Polymarket settlement reads correctly" if ok
          else "PROBLEM, paste this output")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--probe-2024", action="store_true")
    ap.add_argument("--report", help="write a markdown conflict report here")
    a = ap.parse_args()
    if a.self_test:
        sys.exit(self_test())
    if a.probe_2024:
        sys.exit(probe_2024())
    sys.exit(run(a.dry_run, a.report))


if __name__ == "__main__":
    main()
