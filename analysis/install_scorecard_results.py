#!/usr/bin/env python3
"""install_scorecard_results.py: show the election-night scores on the Scorecard tab.

Until now the Scorecard tab only read outcomes.csv (the race tracker). The
scorecard workflow writes data/scorecard.json after polls close, but nothing
displayed it. This adds src/Results.jsx, which renders that file: the
head-to-head verdicts, accuracy by days out, Senate vs governor, calibration,
and every race sorted by biggest miss.

It also installs analysis/call_race.py, for entering AP calls on election
night:  python3 analysis/call_race.py senate-GA ossoff

Run from anywhere inside the repo:
    python3 analysis/install_scorecard_results.py            install, build, preview data
    python3 analysis/install_scorecard_results.py --ship     commit and push (deploys)
    python3 analysis/install_scorecard_results.py --revert   undo, if you haven't shipped

The install step also writes a dry-run scorecard (simulated outcomes) to
data/scorecard.json so `npm run dev` has something to show. Results.jsx only
ever displays a dry run on localhost, and --ship and --revert both delete
that preview file, so it can't reach the live site.
"""
import json
import os
import subprocess
import sys

RESULTS_JSX = 'import { useEffect, useState } from \'react\'\n\n// Renders data/scorecard.json, which the scorecard workflow writes after\n// polls close. Before then the file is "{}" and this renders nothing.\n//\n// A dry-run scorecard (simulated outcomes) only ever shows on localhost, so\n// a preview file that slips into a commit can\'t publish invented results.\n\nconst NO_STORE = { cache: \'no-store\' }\nconst VENUES = [\n  [\'polymarket\', \'Polymarket\'],\n  [\'kalshi\', \'Kalshi\'],\n]\n\nconst isLocal = () =>\n  [\'localhost\', \'127.0.0.1\'].includes(window.location.hostname)\n\nconst fmt = (x) => (x == null ? \'–\' : x.toFixed(3))\nconst pct = (x) => (x == null ? \'–\' : `${Math.round(x * 100)}%`)\n\nfunction horizonLabel(h) {\n  let m = /^(\\d+)-(\\d+)d$/.exec(h)\n  if (m) {\n    const [a, b] = [Number(m[1]), Number(m[2])]\n    if (a === 0 && b === 0) return \'Election day\'\n    if (a === b) return `${a} day${a === 1 ? \'\' : \'s\'} out`\n    return `${a} to ${b} days out`\n  }\n  m = /^(\\d+)\\+d$/.exec(h)\n  if (m) return `${m[1]}+ days out`\n  return h\n}\n\nfunction horizonTable(report) {\n  // { horizon: { rank, polls, venue } } from one venue\'s report\n  const out = {}\n  for (const r of report?.horizon_scores ?? []) {\n    const row = (out[r.horizon] ??= { rank: r.horizon_rank })\n    row[r.source === \'polls\' ? \'polls\' : \'venue\'] = r.brier\n  }\n  return out\n}\n\nfunction Pair({ polls, venue }) {\n  if (polls == null || venue == null) return <td className="num">–</td>\n  const pollsBetter = polls < venue\n  return (\n    <td className="num">\n      <span className={pollsBetter ? \'better\' : \'\'}>{fmt(polls)}</span>\n      {\' / \'}\n      <span className={!pollsBetter ? \'better\' : \'\'}>{fmt(venue)}</span>\n    </td>\n  )\n}\n\nfunction verdict(h2h, venueName) {\n  const b = h2h?.brier\n  if (!b) return null\n  const leader = b.diff < 0 ? \'Polls\' : venueName\n  const gap = Math.abs(b.diff)\n  return (\n    <p>\n      <strong>Polls {fmt(b.brier_a)}</strong> vs{\' \'}\n      <strong>{venueName} {fmt(b.brier_b)}</strong> across {b.n_races} races.{\' \'}\n      {gap < 0.0005\n        ? \'Effectively a tie.\'\n        : `${leader} ${leader === \'Polls\' ? \'were\' : \'was\'} more accurate by ${fmt(gap)}`}\n      {gap < 0.0005 ? \'\' : b.significant\n        ? \', a difference that holds up statistically.\'\n        : `, but with ${b.n_races} races in ${b.n_cycles === 1 ? \'one election\' : `${b.n_cycles} elections`} that gap is within the noise.`}\n    </p>\n  )\n}\n\nexport default function Results() {\n  const [data, setData] = useState(null)\n  const [meta, setMeta] = useState({})\n\n  useEffect(() => {\n    let alive = true\n    fetch(\'/scorecard.json\', NO_STORE)\n      .then((r) => (r.ok ? r.json() : null))\n      .catch(() => null)\n      .then((d) => { if (alive) setData(d) })\n    fetch(\'/race_meta.json\', NO_STORE)\n      .then((r) => (r.ok ? r.json() : {}))\n      .catch(() => ({}))\n      .then((m) => { if (alive) setMeta(m) })\n    return () => { alive = false }\n  }, [])\n\n  if (!data?.reports || !(data.final_forecasts?.length)) return null\n  const dry = data.mode === \'dry-run\'\n  if (dry && !isLocal()) return null\n\n  const reports = data.reports\n  const tables = Object.fromEntries(\n    VENUES.map(([v]) => [v, horizonTable(reports[`${v}_vs_polls`])]))\n  const horizons = [...new Set(VENUES.flatMap(([v]) => Object.keys(tables[v])))]\n    .sort((a, b) => (tables.polymarket[a]?.rank ?? tables.kalshi[a]?.rank ?? 0)\n      - (tables.polymarket[b]?.rank ?? tables.kalshi[b]?.rank ?? 0))\n\n  const offices = [\'senate\', \'governor\'].filter((o) =>\n    VENUES.some(([v]) => reports[`${v}_vs_polls`]?.by_office?.[o]))\n\n  // Chance each source gave the eventual winner. prob is the Democrat\'s.\n  const forWinner = (p, outcome) => (p == null ? null : outcome === 1 ? p : 1 - p)\n  const races = data.final_forecasts.map((f) => {\n    const m = meta[f.race_id] ?? {}\n    const winner = f.race_id.endsWith(\'-control\')\n      ? (f.outcome === 1 ? \'Democrats\' : \'Republicans\')\n      : f.outcome === 1\n      ? `${m.dem_short ?? \'Democrat\'} (D)`\n      : `${m.rep_short ?? \'Republican\'} (R)`\n    const chances = {\n      polls: forWinner(f.polls, f.outcome),\n      polymarket: forWinner(f.polymarket, f.outcome),\n      kalshi: forWinner(f.kalshi, f.outcome),\n    }\n    const vals = Object.values(chances).filter((x) => x != null)\n    return { ...f, winner, chances, worst: vals.length ? Math.min(...vals) : 1 }\n  }).sort((a, b) => a.worst - b.worst)\n\n  // Calibration: polls from the Polymarket report, which covers the most races.\n  const calSources = [\n    [\'polls\', \'Polls\', reports.polymarket_vs_polls?.calibration?.polls],\n    ...VENUES.map(([v, name]) => [v, name, reports[`${v}_vs_polls`]?.calibration?.[v]]),\n  ].filter(([, , bins]) => bins?.length)\n  const binKeys = [...new Set(calSources.flatMap(([, , bins]) =>\n    bins.filter((b) => b.n > 0).map((b) => b.bin_low.toFixed(1))))].sort()\n\n  const pending = data.coverage?.pending ?? []\n\n  return (\n    <section className="results">\n      {dry && (\n        <p className="dry-banner">\n          Local preview with simulated outcomes. This never shows on the live site.\n        </p>\n      )}\n\n      <h2>Results</h2>\n      <p className="note-inline">\n        Scored with the Brier score: the average squared gap between the\n        forecast and what happened. Zero is perfect, 0.25 is a coin flip, and\n        lower is better. Each market venue is compared with polls only on days\n        when both had a reading, so the poll figure differs slightly between\n        the two comparisons.\n        {pending.length > 0 && ` ${pending.length} race${pending.length === 1 ? \' is\' : \'s are\'} still pending and not yet scored.`}\n      </p>\n\n      {VENUES.map(([v, name]) => {\n        const r = reports[`${v}_vs_polls`]\n        return r ? (\n          <div key={v} className="verdict">\n            <h3>{name} vs polls</h3>\n            {verdict(r.head_to_head, name)}\n          </div>\n        ) : null\n      })}\n\n      <h3>By how far out the forecast was made</h3>\n      <div className="table-wrap">\n        <table className="results-table">\n          <thead>\n            <tr>\n              <th>Days out</th>\n              {VENUES.map(([v, name]) => <th key={v} className="num">Polls / {name}</th>)}\n            </tr>\n          </thead>\n          <tbody>\n            {horizons.map((h) => (\n              <tr key={h}>\n                <td>{horizonLabel(h)}</td>\n                {VENUES.map(([v]) => (\n                  <Pair key={v} polls={tables[v][h]?.polls} venue={tables[v][h]?.venue} />\n                ))}\n              </tr>\n            ))}\n          </tbody>\n        </table>\n      </div>\n      <p className="note-inline">The more accurate figure in each pair is in bold.</p>\n\n      {offices.length > 0 && (\n        <>\n          <h3>Senate and governor</h3>\n          <div className="table-wrap">\n            <table className="results-table">\n              <thead>\n                <tr>\n                  <th>Office</th>\n                  {VENUES.map(([v, name]) => <th key={v} className="num">Polls / {name}</th>)}\n                </tr>\n              </thead>\n              <tbody>\n                {offices.map((o) => (\n                  <tr key={o}>\n                    <td>{o === \'senate\' ? \'Senate\' : \'Governor\'}</td>\n                    {VENUES.map(([v]) => {\n                      const b = reports[`${v}_vs_polls`]?.by_office?.[o]?.head_to_head?.brier\n                      return <Pair key={v} polls={b?.brier_a} venue={b?.brier_b} />\n                    })}\n                  </tr>\n                ))}\n              </tbody>\n            </table>\n          </div>\n        </>\n      )}\n\n      {calSources.length > 0 && binKeys.length > 0 && (\n        <>\n          <h3>Calibration</h3>\n          <p className="note-inline">\n            When a source gave a candidate a chance in each range, how often\n            that candidate actually won, with the number of race-days behind\n            each figure. A well calibrated source lands inside its own range.\n          </p>\n          <div className="table-wrap">\n            <table className="results-table">\n              <thead>\n                <tr>\n                  <th>Forecast</th>\n                  {calSources.map(([k, name]) => <th key={k} className="num">{name}</th>)}\n                </tr>\n              </thead>\n              <tbody>\n                {binKeys.map((key) => {\n                  const lo = Number(key)\n                  return (\n                    <tr key={key}>\n                      <td>{Math.round(lo * 100)} to {Math.round(lo * 100) + 10}%</td>\n                      {calSources.map(([k, , bins]) => {\n                        const b = bins.find((x) => x.bin_low.toFixed(1) === key)\n                        return (\n                          <td key={k} className="num">\n                            {b && b.n > 0 ? <>{pct(b.observed_rate)} <span className="n">({b.n})</span></> : \'–\'}\n                          </td>\n                        )\n                      })}\n                    </tr>\n                  )\n                })}\n              </tbody>\n            </table>\n          </div>\n        </>\n      )}\n\n      <h3>Every race, biggest misses first</h3>\n      <p className="note-inline">\n        The chance each source gave the eventual winner in its final forecast\n        before polls closed. Figures under 50% mean that source picked the\n        loser.\n      </p>\n      <div className="table-wrap">\n        <table className="results-table">\n          <thead>\n            <tr>\n              <th>Race</th>\n              <th>Winner</th>\n              <th className="num">Polls</th>\n              {VENUES.map(([v, name]) => <th key={v} className="num">{name}</th>)}\n            </tr>\n          </thead>\n          <tbody>\n            {races.map((r) => (\n              <tr key={r.race_id}>\n                <td>{r.race_id.endsWith(\'-control\') ? r.label : `${r.label} ${r.office === \'governor\' ? \'governor\' : \'Senate\'}`}</td>\n                <td>{r.winner}</td>\n                {[\'polls\', \'polymarket\', \'kalshi\'].map((s) => (\n                  <td key={s} className={`num${r.chances[s] != null && r.chances[s] < 0.5 ? \' miss\' : \'\'}`}>\n                    {pct(r.chances[s])}\n                  </td>\n                ))}\n              </tr>\n            ))}\n          </tbody>\n        </table>\n      </div>\n      {data.generated_at && !dry && (\n        <p className="note-inline">\n          Updated {new Date(data.generated_at).toLocaleString(\'en-US\', {\n            timeZone: \'America/New_York\', month: \'short\', day: \'numeric\',\n            hour: \'numeric\', minute: \'2-digit\',\n          })} ET.\n        </p>\n      )}\n    </section>\n  )\n}\n'

CALL_RACE_PY = '#!/usr/bin/env python3\n"""call_race.py: record AP race calls in data/outcomes.csv, then commit and push.\n\nPushing outcomes.csv triggers the scorecard workflow, which rescores and\nredeploys the site. So on election night this is the only command you need.\n\n    python3 analysis/call_race.py senate-GA ossoff\n    python3 analysis/call_race.py gov-AZ dem senate-IA rep     several at once\n    python3 analysis/call_race.py control rep                  Senate control\n    python3 analysis/call_race.py --pending senate-GA          undo a call\n    python3 analysis/call_race.py --list                       show every race\n    python3 analysis/call_race.py --no-push senate-GA rep      edit only, no git\n\nRaces: senate-XX, gov-XX (or governor-XX, XX-senate, XX-gov), or control.\nWinners: dem, rep, d, r, or the candidate\'s last name.\n\nThe call date defaults to today in Eastern time; use --date 2026-11-04 to\noverride. A race already called for the other candidate needs --force.\n"""\nimport argparse\nimport csv\nimport json\nimport re\nimport subprocess\nimport sys\nfrom datetime import date, datetime\nfrom pathlib import Path\nfrom zoneinfo import ZoneInfo\n\nROOT = Path(__file__).resolve().parent.parent\nOUTCOMES = ROOT / "data" / "outcomes.csv"\nMETA = ROOT / "data" / "race_meta.json"\nFIELDS = ["race_id", "winner", "called_date", "status", "note"]\nCYCLE = 2026\nPOLLS_CLOSE = datetime(2026, 11, 3, 18, 0, tzinfo=ZoneInfo("America/New_York"))\n\n\ndef git(*args, check=True):\n    print("$ git " + " ".join(args))\n    r = subprocess.run(["git", *args], cwd=ROOT)\n    if check and r.returncode != 0:\n        sys.exit(f"git {args[0]} failed; paste the output above.")\n    return r.returncode\n\n\ndef race_id(token):\n    t = token.strip().lower().replace("_", "-")\n    if t in ("control", "senate-control"):\n        return f"{CYCLE}-senate-control"\n    m = (re.fullmatch(r"(senate|sen|gov|governor)-?([a-z]{2})", t)\n         or re.fullmatch(r"([a-z]{2})-?(senate|sen|gov|governor)", t))\n    if not m:\n        return None\n    a, b = m.groups()\n    office, state = (a, b) if len(b) == 2 and len(a) > 2 else (b, a)\n    office = "senate" if office.startswith("sen") else "gov"\n    return f"{CYCLE}-{office}-{state.upper()}"\n\n\ndef winner_of(token, meta_row):\n    t = token.strip().lower()\n    if t in ("dem", "d", "democrat", "democrats"):\n        return "dem"\n    if t in ("rep", "r", "gop", "republican", "republicans"):\n        return "rep"\n    for side in ("dem", "rep"):\n        names = {(meta_row.get(f"{side}_short") or "").lower(),\n                 (meta_row.get(side) or "").lower()}\n        names |= {n.split()[-1] for n in names if n}\n        if t in names:\n            return side\n    return None\n\n\ndef describe(rid, winner, meta):\n    m = meta.get(rid, {})\n    label = m.get("label", rid)\n    if not rid.endswith("-control"):\n        label += " governor" if "-gov-" in rid else " Senate"\n    if not winner:\n        return label\n    who = m.get(f"{winner}_short") or ("Democrat" if winner == "dem" else "Republican")\n    return f"{label}: {who} ({\'D\' if winner == \'dem\' else \'R\'})"\n\n\ndef load():\n    with OUTCOMES.open(newline="", encoding="utf-8") as fh:\n        return list(csv.DictReader(fh))\n\n\ndef save(rows):\n    # Keep the file\'s existing line endings (scorecard.py wrote it with CRLF),\n    # so a single call shows up in git as a single changed line.\n    eol = "\\r\\n" if b"\\r\\n" in OUTCOMES.read_bytes()[:200] else "\\n"\n    with OUTCOMES.open("w", newline="", encoding="utf-8") as fh:\n        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator=eol)\n        w.writeheader()\n        for r in rows:\n            w.writerow({k: r.get(k, "") for k in FIELDS})\n\n\ndef show(rows, meta):\n    called = [r for r in rows if r["status"] == "called"]\n    print(f"{len(called)} called, {len(rows) - len(called)} pending\\n")\n    for r in sorted(rows, key=lambda r: (r["status"] != "called", r["race_id"])):\n        if r["status"] == "called":\n            print(f"  called   {describe(r[\'race_id\'], r[\'winner\'], meta)}  ({r[\'called_date\']})")\n        else:\n            print(f"  pending  {describe(r[\'race_id\'], None, meta)}")\n\n\ndef main():\n    ap = argparse.ArgumentParser(description=__doc__,\n                                 formatter_class=argparse.RawDescriptionHelpFormatter)\n    ap.add_argument("calls", nargs="*", help="RACE WINNER pairs")\n    ap.add_argument("--pending", nargs="+", metavar="RACE", help="set races back to pending")\n    ap.add_argument("--list", action="store_true", help="show every race and stop")\n    ap.add_argument("--date", help="call date, YYYY-MM-DD (default: today, Eastern)")\n    ap.add_argument("--note", default="", help="note stored with these calls")\n    ap.add_argument("--force", action="store_true", help="allow changing an existing call")\n    ap.add_argument("--no-push", action="store_true", help="edit the file only, no git")\n    args = ap.parse_args()\n\n    meta = json.loads(META.read_text()) if META.exists() else {}\n\n    if args.list:\n        show(load(), meta)\n        return\n\n    if not args.calls and not args.pending:\n        ap.print_help()\n        return\n    if len(args.calls) % 2:\n        sys.exit("Calls come in pairs: RACE WINNER, e.g.  senate-GA ossoff")\n\n    now_et = datetime.now(ZoneInfo("America/New_York"))\n    if args.calls and now_et < POLLS_CLOSE and not args.no_push:\n        sys.exit("Polls haven\'t closed yet (6 p.m. ET, Nov 3). Use --no-push to test.")\n    called_date = args.date or now_et.date().isoformat()\n    try:\n        date.fromisoformat(called_date)\n    except ValueError:\n        sys.exit(f"--date must look like 2026-11-04, not {called_date!r}")\n\n    if not args.no_push:\n        # Bring in whatever the collectors pushed, so this commit lands cleanly.\n        git("pull", "--rebase", "--autostash", "origin", "main")\n\n    rows = load()\n    by_id = {r["race_id"]: r for r in rows}\n    changes, problems = [], []\n\n    def lookup(token):\n        rid = race_id(token)\n        if rid is None:\n            problems.append(f"{token!r} isn\'t a race; try senate-GA, gov-AZ or control")\n        elif rid not in by_id:\n            problems.append(f"{rid} isn\'t in outcomes.csv")\n        else:\n            return rid\n        return None\n\n    for token, who in zip(args.calls[::2], args.calls[1::2]):\n        rid = lookup(token)\n        if not rid:\n            continue\n        w = winner_of(who, meta.get(rid, {}))\n        if not w:\n            m = meta.get(rid, {})\n            problems.append(f"{who!r} isn\'t a candidate in {describe(rid, None, meta)}; "\n                            f"use dem/rep or {m.get(\'dem_short\', \'?\')}/{m.get(\'rep_short\', \'?\')}")\n            continue\n        r = by_id[rid]\n        if r["status"] == "called" and r["winner"] == w:\n            print(f"already recorded: {describe(rid, w, meta)}")\n            continue\n        if r["status"] == "called" and r["winner"] != w and not args.force:\n            problems.append(f"{describe(rid, r[\'winner\'], meta)} is already called; "\n                            "add --force to change it")\n            continue\n        r.update(winner=w, called_date=called_date, status="called",\n                 note=args.note or r.get("note", ""))\n        changes.append(f"Call {describe(rid, w, meta)}")\n\n    for token in args.pending or []:\n        rid = lookup(token)\n        if rid and by_id[rid]["status"] == "called":\n            by_id[rid].update(winner="", called_date="", status="pending", note=args.note)\n            changes.append(f"Uncall {describe(rid, None, meta)}")\n\n    if problems:\n        sys.exit("Nothing changed:\\n  " + "\\n  ".join(problems))\n    if not changes:\n        print("No changes to make.")\n        return\n\n    save(rows)\n    print("\\n".join(changes))\n\n    if args.no_push:\n        print("\\nWrote data/outcomes.csv (not committed).")\n        return\n\n    msg = changes[0] if len(changes) == 1 else f"{len(changes)} race calls\\n\\n" + "\\n".join(changes)\n    git("add", "data/outcomes.csv")\n    git("commit", "-m", msg)\n    for attempt in range(3):\n        if git("pull", "--rebase", "--autostash", "origin", "main", check=False) == 0 \\\n                and git("push", "origin", "main", check=False) == 0:\n            called = sum(r["status"] == "called" for r in rows)\n            print(f"\\nPushed. {called} of {len(rows)} races called. The scorecard workflow "\n                  "rescores and redeploys in a few minutes.")\n            return\n    sys.exit("Push failed three times. Your commit is saved locally; run  git push  "\n             "once the network settles.")\n\n\nif __name__ == "__main__":\n    main()\n'

CSS_MARKER = "/* scorecard results (install_scorecard_results.py) */"
CSS_BLOCK = '\n/* scorecard results (install_scorecard_results.py) */\n.results h3 { margin: 1.5rem 0 0.4rem; font-size: 1.05rem; }\n.results .verdict p { margin: 0.2rem 0 0.6rem; }\n.note-inline { font-size: 0.85rem; color: var(--ink-soft); }\n.dry-banner {\n  font-family: var(--mono); font-size: 0.75rem; color: var(--flag);\n  border: 1px dashed var(--flag); padding: 0.5rem 0.75rem; border-radius: 4px;\n}\n.table-wrap { overflow-x: auto; }\n.results-table {\n  width: 100%; border-collapse: collapse; font-size: 0.85rem; margin: 0.25rem 0;\n}\n.results-table th {\n  font-family: var(--mono); font-size: 0.68rem; letter-spacing: 0.06em;\n  text-transform: uppercase; color: var(--ink-faint); font-weight: normal;\n  text-align: left; padding: 0.35rem 0.5rem; border-bottom: 1px solid var(--rule-strong);\n}\n.results-table td {\n  padding: 0.35rem 0.5rem; border-bottom: 1px solid var(--rule); white-space: nowrap;\n}\n.results-table .num { text-align: right; font-family: var(--mono); }\n.results-table .better { font-weight: 700; color: var(--ink); }\n.results-table .miss { color: var(--flag); font-weight: 700; }\n.results-table .n { color: var(--ink-faint); font-size: 0.75rem; }\n/* Once results exist, the "what the scorecard will show" promise is redundant. */\n.scorecard:has(.results) .scorecard-preview { display: none; }\n@media (max-width: 560px) {\n  .results-table { font-size: 0.74rem; }\n  .results-table th { font-size: 0.6rem; letter-spacing: 0.02em; white-space: normal; }\n  .results-table th, .results-table td { padding: 0.3rem 0.25rem; }\n  .results-table td:first-child, .results-table td:nth-child(2) { white-space: normal; }\n  .results-table .n { display: none; }\n}\n'

SC_PATCHES = [
    ("import",
     "import { useEffect, useState } from 'react'\n",
     "import { useEffect, useState } from 'react'\n"
     "import Results from './Results.jsx'\n"),
    ("results slot",
     "      <section>\n        <h2>What the scorecard will show</h2>\n",
     "      <Results />\n\n"
     "      <section className=\"scorecard-preview\">\n"
     "        <h2>What the scorecard will show</h2>\n"),
]

# Generated by copy-data.mjs on every dev run or build. Untracked, but a
# habitual `git add .` would commit them, so ignore them like the others.
IGNORE_LINES = ["public/outcomes.csv", "public/scorecard.json", "public/poll_inputs.json"]

SC = os.path.join("src", "Scorecard.jsx")
CSS = os.path.join("src", "styles.css")
RES = os.path.join("src", "Results.jsx")
CALL = os.path.join("analysis", "call_race.py")
GI = ".gitignore"
PREVIEW = os.path.join("data", "scorecard.json")
TOUCHED = [SC, CSS, RES, GI, CALL, os.path.join("analysis", "install_scorecard_results.py")]


def run(cmd, **kw):
    print("$ " + " ".join(cmd))
    return subprocess.run(cmd, **kw)


def repo_root():
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("Run this from inside the oddsvspolls repo.")
    return r.stdout.strip()


def drop_preview():
    """Delete data/scorecard.json only if it's our dry-run preview."""
    if not os.path.exists(PREVIEW):
        return
    try:
        mode = json.load(open(PREVIEW)).get("mode")
    except Exception:
        return
    if mode == "dry-run":
        os.remove(PREVIEW)
        for p in (os.path.join("public", "scorecard.json"),):
            if os.path.exists(p):
                os.remove(p)
        print("removed the dry-run preview scorecard")


def revert(quiet=False):
    subprocess.run(["git", "checkout", "--", SC, CSS, GI])
    if os.path.exists(RES):
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", RES],
                                 capture_output=True).returncode == 0
        if tracked:
            subprocess.run(["git", "checkout", "--", RES])
        else:
            os.remove(RES)
    if os.path.exists(CALL) and subprocess.run(
            ["git", "ls-files", "--error-unmatch", CALL], capture_output=True).returncode:
        os.remove(CALL)
    drop_preview()
    if not quiet:
        print("Reverted Scorecard.jsx, styles.css and .gitignore, removed Results.jsx.")


def make_preview():
    if os.path.exists(PREVIEW):
        try:
            if json.load(open(PREVIEW)).get("mode") != "dry-run":
                print("data/scorecard.json holds real results; not replacing it")
                return
        except Exception:
            pass
    r = subprocess.run([sys.executable, "scorecard.py", "--dry-run"],
                       cwd="analysis", capture_output=True, text=True)
    src = "/tmp/oddsvspolls_scorecard_dryrun.json"
    if r.returncode != 0 or not os.path.exists(src):
        print("Couldn't build preview data (scorecard.py needs pandas and numpy):")
        print((r.stderr or r.stdout)[-800:])
        print("To preview locally:  pip3 install pandas numpy  then rerun this.\n"
              "Or skip the preview; the page was tested before you got it.")
        return
    with open(src) as f, open(PREVIEW, "w") as g:
        g.write(f.read())
    print("wrote dry-run preview to data/scorecard.json (local only, never shipped)")


def install():
    for p in (SC, CSS):
        if not os.path.exists(p):
            sys.exit(f"Can't find {p}; nothing changed.")

    installed = "from './Results.jsx'" in open(SC).read()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", SC, CSS],
                           capture_output=True, text=True).stdout.strip()
    if dirty and not installed:
        sys.exit(f"You have uncommitted edits in Scorecard.jsx or styles.css:\n{dirty}\n"
                 "Commit or stash them first so --revert can't lose your work.")

    run(["git", "pull", "--rebase", "--autostash", "origin", "main"])

    sc = open(SC).read()
    if installed:
        print("Scorecard.jsx already wired up; leaving it alone")
    else:
        for name, old, new in SC_PATCHES:
            n = sc.count(old)
            if n != 1:
                sys.exit(f"Scorecard.jsx: expected the '{name}' spot once, found {n}. "
                         "Nothing changed. Paste this message and I'll adjust.")
        for name, old, new in SC_PATCHES:
            sc = sc.replace(old, new, 1)
        open(SC, "w").write(sc)
        print("patched src/Scorecard.jsx")

    open(RES, "w").write(RESULTS_JSX)
    print("wrote src/Results.jsx")
    open(CALL, "w").write(CALL_RACE_PY)
    os.chmod(CALL, 0o755)
    print("wrote analysis/call_race.py")

    css = open(CSS).read()
    if CSS_MARKER in css:
        print("styles.css already has the results styles")
    else:
        with open(CSS, "a") as f:
            f.write(CSS_BLOCK)
        print("added results styles to styles.css")

    gi = open(GI).read() if os.path.exists(GI) else ""
    missing = [l for l in IGNORE_LINES if l not in gi.splitlines()]
    if missing:
        with open(GI, "a") as f:
            f.write(("" if gi.endswith("\n") or not gi else "\n") + "\n".join(missing) + "\n")
        print("added generated public/ files to .gitignore")

    make_preview()

    print("\nbuilding to check it compiles...\n")
    b = subprocess.run(["npm", "run", "build"], capture_output=True, text=True)
    if b.returncode != 0:
        print(b.stdout[-3000:], b.stderr[-3000:], sep="\n")
        revert(quiet=True)
        sys.exit("\nBuild failed, so everything was put back. Paste the output above.")
    print("build ok\n")

    print("Preview it locally:\n"
          "  npm run dev\n"
          "then open the address it prints and add #scorecard to the end.\n"
          "You'll see a red 'local preview' banner over simulated results.\n"
          "Press Ctrl+C to stop. When it looks right:\n"
          "  python3 analysis/install_scorecard_results.py --ship\n"
          "If it doesn't:\n"
          "  python3 analysis/install_scorecard_results.py --revert")


def ship():
    sc = open(SC).read() if os.path.exists(SC) else ""
    if "from './Results.jsx'" not in sc:
        sys.exit("Not installed yet; run without --ship first.")
    drop_preview()
    if os.path.exists(PREVIEW) and subprocess.run(
            ["git", "ls-files", "--error-unmatch", PREVIEW], capture_output=True).returncode:
        print("note: leaving untracked data/scorecard.json alone (not a preview)")
    paths = [p for p in TOUCHED if os.path.exists(p)]
    for cmd in (["git", "add"] + paths,
                ["git", "commit", "-m", "Show scorecard results on the Scorecard tab"],
                ["git", "pull", "--rebase", "--autostash", "origin", "main"],
                ["git", "push", "origin", "main"]):
        if run(cmd).returncode != 0:
            sys.exit("git step failed; paste the output.")
    print("\nShipped. Until polls close the live tab looks the same as today;\n"
          "results appear there once the first race is called after 6 p.m. ET Nov 3.\n"
          "  https://oddsvspolls.com/#scorecard\n\n"
          "On election night, enter calls with:\n"
          "  python3 analysis/call_race.py senate-GA ossoff gov-AZ dem\n"
          "  python3 analysis/call_race.py --list")


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
