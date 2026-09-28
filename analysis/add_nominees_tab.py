#!/usr/bin/env python3
"""
Adds the 2028 Nominees tab to the site. Run from anywhere in the repo.

    python3 add_nominees_tab.py            install, then build to check it compiles
    python3 add_nominees_tab.py --ship     commit and push (deploys the site)
    python3 add_nominees_tab.py --revert   undo, if you haven't shipped yet

Nothing is committed until you run --ship, so you can preview locally first.
"""
import os
import subprocess
import sys

NOMINEES_JSX = 'import { useEffect, useState } from \'react\'\n\n// Same reasoning as the race data: these files are rewritten in place\n// several times a day, so a cached copy is stale rather than merely old.\nconst FETCH_OPTS = { cache: \'no-store\' }\n\nconst BOARDS = [\n  { id: \'2028-dem-nominee\', title: \'Democratic nominee\' },\n  { id: \'2028-rep-nominee\', title: \'Republican nominee\' },\n]\nconst VENUES = [\'polymarket\', \'kalshi\']\nconst TOP_N = 10\nconst DISAGREE_PTS = 5\nconst DAY_MS = 86400000\n\n// ---------- tab state ----------\n\n// The tab lives in the URL hash so a link to the nominees page can be\n// shared, without adding a router or any Cloudflare routing rules.\nfunction readTab() {\n  return window.location.hash === \'#nominees\' ? \'nominees\' : \'races\'\n}\n\nexport function useTab() {\n  const [tab, setTab] = useState(readTab)\n  useEffect(() => {\n    const onHash = () => { setTab(readTab()); window.scrollTo(0, 0) }\n    window.addEventListener(\'hashchange\', onHash)\n    return () => window.removeEventListener(\'hashchange\', onHash)\n  }, [])\n  return tab\n}\n\nexport function TabNav({ tab }) {\n  return (\n    <nav className="tabs" aria-label="Sections">\n      <a href="#races" className={tab === \'races\' ? \'tab tab-on\' : \'tab\'}\n         aria-current={tab === \'races\' ? \'page\' : undefined}>2026 races</a>\n      <a href="#nominees" className={tab === \'nominees\' ? \'tab tab-on\' : \'tab\'}\n         aria-current={tab === \'nominees\' ? \'page\' : undefined}>2028 nominees</a>\n    </nav>\n  )\n}\n\nexport function NomineesHeader() {\n  return (\n    <>\n      <h1>Who the markets expect in 2028</h1>\n      <p className="lede">\n        Polymarket and Kalshi prices for each party&rsquo;s 2028 presidential\n        nomination, side by side and tracked over time. There is no polling\n        column here: primary polls measure a share of the vote, not a chance\n        of winning, so the two can&rsquo;t be compared directly.\n      </p>\n    </>\n  )\n}\n\n// ---------- data ----------\n\nfunction parseCsv(text) {\n  const rows = []\n  let row = []\n  let field = \'\'\n  let quoted = false\n  for (let i = 0; i < text.length; i++) {\n    const c = text[i]\n    if (quoted) {\n      if (c === \'"\') {\n        if (text[i + 1] === \'"\') { field += \'"\'; i++ } else quoted = false\n      } else field += c\n    } else if (c === \'"\') quoted = true\n    else if (c === \',\') { row.push(field); field = \'\' }\n    else if (c === \'\\n\' || c === \'\\r\') {\n      if (c === \'\\r\' && text[i + 1] === \'\\n\') i++\n      row.push(field); field = \'\'\n      if (row.length > 1 || row[0] !== \'\') rows.push(row)\n      row = []\n    } else field += c\n  }\n  if (field !== \'\' || row.length) { row.push(field); rows.push(row) }\n  const [head, ...body] = rows\n  if (!head) return []\n  return body.map((r) => Object.fromEntries(head.map((h, j) => [h, r[j] ?? \'\'])))\n}\n\nasync function loadCsv(path, optional = false) {\n  try {\n    const res = await fetch(path, FETCH_OPTS)\n    if (!res.ok) throw new Error(`${path} returned ${res.status}`)\n    const text = await res.text()\n    // A missing file can come back as the site\'s index page with a 200,\n    // so check it is actually the CSV before parsing.\n    if (!text.startsWith(\'ts_utc\')) throw new Error(`${path} is not the expected CSV`)\n    return parseCsv(text)\n  } catch (err) {\n    if (optional) return []\n    throw err\n  }\n}\n\nfunction change30(series, cur) {\n  const v = cur.polymarket !== null ? \'polymarket\' : \'kalshi\'\n  const s = series[v]\n  if (!s.length || cur[v] === null) return null\n  const target = s[s.length - 1].t - 30 * DAY_MS\n  let then = null\n  for (const pt of s) { if (pt.t <= target) then = pt; else break }\n  if (!then) return null\n  return { venue: v, pts: (cur[v] - then.p) * 100 }\n}\n\nfunction buildBoards(rows) {\n  const raw = {}\n  const runTs = {}\n  let asOf = \'\'\n  let firstLive = \'\'\n\n  for (const r of rows) {\n    const p = parseFloat(r.price)\n    if (!r.event_id || !r.candidate_key || !VENUES.includes(r.venue) || !Number.isFinite(p)) continue\n    const live = !(r.price_source || \'\').startsWith(\'backfill\')\n    const board = (raw[r.event_id] ||= {})\n    const c = (board[r.candidate_key] ||= {\n      key: r.candidate_key, labels: {}, latest: {},\n      days: { polymarket: {}, kalshi: {} },\n    })\n    // One point per venue per day, keeping the day\'s last observation.\n    const day = r.ts_utc.slice(0, 10)\n    const prev = c.days[r.venue][day]\n    if (!prev || r.ts_utc > prev.ts) c.days[r.venue][day] = { ts: r.ts_utc, p }\n    if (r.candidate_label) c.labels[r.venue] = r.candidate_label\n    if (live) {\n      const l = c.latest[r.venue]\n      if (!l || r.ts_utc > l.ts) c.latest[r.venue] = { ts: r.ts_utc, p }\n      const rk = `${r.event_id}|${r.venue}`\n      if (!runTs[rk] || r.ts_utc > runTs[rk]) runTs[rk] = r.ts_utc\n      if (r.ts_utc > asOf) asOf = r.ts_utc\n      if (!firstLive || r.ts_utc < firstLive) firstLive = r.ts_utc\n    }\n  }\n\n  const boards = BOARDS.map(({ id, title }) => {\n    const cands = Object.values(raw[id] || {}).map((c) => {\n      // A current price only counts if it came from the latest collection\n      // run. A candidate who has since dropped under the collector\'s floor\n      // would otherwise keep showing a stale figure.\n      const cur = {}\n      for (const v of VENUES) {\n        const l = c.latest[v]\n        cur[v] = l && l.ts === runTs[`${id}|${v}`] ? l.p : null\n      }\n      const vals = VENUES.map((v) => cur[v]).filter((x) => x !== null)\n      if (!vals.length) return null\n      const series = {}\n      for (const v of VENUES) {\n        series[v] = Object.keys(c.days[v]).sort()\n          .map((day) => ({ t: Date.parse(day), p: c.days[v][day].p }))\n      }\n      return {\n        key: c.key,\n        label: c.labels.polymarket || c.labels.kalshi || c.key,\n        cur,\n        avg: vals.reduce((a, b) => a + b, 0) / vals.length,\n        series,\n        gap: cur.polymarket !== null && cur.kalshi !== null\n          ? (cur.polymarket - cur.kalshi) * 100 : null,\n        change: change30(series, cur),\n      }\n    }).filter(Boolean).sort((a, b) => b.avg - a.avg)\n\n    const totals = {}\n    for (const v of VENUES) totals[v] = cands.reduce((s, c) => s + (c.cur[v] ?? 0), 0)\n    return { id, title, cands, totals }\n  })\n\n  return { boards, asOf, firstLive }\n}\n\n// ---------- formatting ----------\n\nconst fmtPct = (p) => (p * 100 < 1 ? \'<1\' : (p * 100).toFixed(0))\nconst fmtDay = (t) => new Date(t).toLocaleDateString(\'en-US\',\n  { month: \'short\', day: \'numeric\', year: \'numeric\', timeZone: \'UTC\' })\nconst fmtMonth = (t) => new Date(t).toLocaleDateString(\'en-US\',\n  { month: \'short\', year: \'numeric\', timeZone: \'UTC\' })\n\n// ---------- chart ----------\n\nfunction nearest(s, t) {\n  if (!s.length) return null\n  let lo = 0\n  let hi = s.length - 1\n  while (lo < hi) {\n    const mid = (lo + hi) >> 1\n    if (s[mid].t < t) lo = mid + 1; else hi = mid\n  }\n  const b = s[lo]\n  const a = s[lo - 1]\n  return a && Math.abs(a.t - t) < Math.abs(b.t - t) ? a : b\n}\n\n// Each row gets its own vertical scale, printed in the caption. On a\n// shared scale everyone below the favorite would draw as a flat line on\n// the floor, and the movement worth seeing is exactly theirs.\nfunction niceMax(m) {\n  return Math.ceil(Math.max(0.1, m * 1.15) * 20) / 20\n}\n\nfunction NomineeChart({ series, label }) {\n  const [hover, setHover] = useState(null)\n  const all = [...series.polymarket, ...series.kalshi]\n  if (all.length < 2) return null\n\n  let t0 = Infinity, t1 = -Infinity, pMax = 0\n  for (const d of all) {\n    if (d.t < t0) t0 = d.t\n    if (d.t > t1) t1 = d.t\n    if (d.p > pMax) pMax = d.p\n  }\n  const span = Math.max(t1 - t0, 1)\n  const yMax = niceMax(pMax)\n  const X = (t) => ((t - t0) / span) * 100\n  const Y = (p) => 40 - (Math.min(p, yMax) / yMax) * 40\n  const path = (s) => s.map((d, i) =>\n    `${i ? \'L\' : \'M\'}${X(d.t).toFixed(2)},${Y(d.p).toFixed(2)}`).join(\'\')\n\n  const onMove = (e) => {\n    const box = e.currentTarget.getBoundingClientRect()\n    const f = Math.min(1, Math.max(0, (e.clientX - box.left) / box.width))\n    setHover(t0 + f * span)\n  }\n\n  const pts = hover === null ? null : VENUES.map((v) => {\n    const pt = nearest(series[v], hover)\n    return { v, pt: pt && Math.abs(pt.t - hover) <= 3 * DAY_MS ? pt : null }\n  })\n  const hx = hover === null ? 0 : X(hover)\n\n  return (\n    <figure className="history">\n      <div className="history-plot"\n           onPointerMove={onMove} onPointerDown={onMove}\n           onPointerLeave={() => setHover(null)}\n           role="img" aria-label={`${label}: nomination price history`}>\n        <svg viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">\n          {series.kalshi.length > 1 && (\n            <path className="h-line h-kalshi" d={path(series.kalshi)} />\n          )}\n          {series.polymarket.length > 1 && (\n            <path className="h-line h-market" d={path(series.polymarket)} />\n          )}\n          {hover !== null && (\n            <line className="h-cross" x1={hx} x2={hx} y1="0" y2="40"\n                  vectorEffect="non-scaling-stroke" />\n          )}\n        </svg>\n        {pts && pts.map(({ v, pt }) => pt && (\n          <span key={v}\n                className={`h-dot ${v === \'kalshi\' ? \'h-dot-kalshi\' : \'h-dot-market\'}`}\n                style={{ left: `${X(pt.t)}%`, top: `${(Y(pt.p) / 40) * 100}%` }} />\n        ))}\n        {pts && (\n          <div className={hx > 66 ? \'h-tip h-tip-left\' : \'h-tip\'}\n               style={{ left: `${hx}%` }}>\n            <div className="h-tip-date"><span>{fmtDay(hover)}</span></div>\n            {pts.map(({ v, pt }) => (\n              <div key={v} className="h-tip-row">\n                <i className={`swatch ${v === \'kalshi\' ? \'swatch-kalshi\' : \'swatch-market\'}`} />\n                <span className="h-tip-label">{v}</span>\n                <span className="h-tip-value">\n                  {pt ? `${(pt.p * 100).toFixed(1)}%` : \'n/a\'}\n                </span>\n              </div>\n            ))}\n          </div>\n        )}\n      </div>\n      <figcaption>\n        <span>{fmtMonth(t0)}</span>\n        <span>scale 0 to {Math.round(yMax * 100)}%</span>\n      </figcaption>\n    </figure>\n  )\n}\n\n// ---------- rows ----------\n\nfunction NomineeRow({ c, rank }) {\n  const listedOn = VENUES.filter((v) => c.series[v].length)\n  const disagree = c.gap !== null && Math.abs(c.gap) >= DISAGREE_PTS\n  return (\n    <li className="race">\n      <div className="race-head">\n        <div className="race-title">\n          <span className="race-name">{c.label}</span>\n          <span className="matchup">#{rank}</span>\n        </div>\n        <div className="calls">\n          {VENUES.map((v) => (\n            <span key={v} className="call">\n              <span className="call-name">{v}</span>\n              {c.cur[v] !== null ? (\n                <span className={`call-pct ${v === \'kalshi\' ? \'call-kalshi\' : \'call-market\'}`}>\n                  {fmtPct(c.cur[v])}<span className="pct">%</span>\n                </span>\n              ) : (\n                <span className="call-none">\n                  {c.series[v].length ? \'under 0.5%\' : \'not listed\'}\n                </span>\n              )}\n            </span>\n          ))}\n          {c.gap !== null && (\n            <span className="gap-line">{Math.abs(c.gap).toFixed(1)} pts apart</span>\n          )}\n        </div>\n      </div>\n\n      <NomineeChart series={c.series} label={c.label} />\n\n      <div className="race-meta">\n        {c.change && (\n          <span>\n            {c.change.pts >= 0 ? \'+\' : \'\'}{c.change.pts.toFixed(1)} pts in 30 days\n            {\' \'}({c.change.venue})\n          </span>\n        )}\n        {listedOn.length === 1 && <span>listed on {listedOn[0]} only</span>}\n        {disagree && <span className="flag">venues disagree</span>}\n      </div>\n    </li>\n  )\n}\n\n// ---------- page ----------\n\nexport default function Nominees() {\n  const [state, setState] = useState({ status: \'loading\' })\n  const [open, setOpen] = useState({})\n\n  useEffect(() => {\n    Promise.all([\n      loadCsv(\'/nominee_snapshots.csv\'),\n      loadCsv(\'/nominee_backfill.csv\', true),\n    ])\n      .then(([live, back]) => {\n        const data = buildBoards([...back, ...live])\n        const any = data.boards.some((b) => b.cands.length)\n        setState({ status: any ? \'ready\' : \'empty\', ...data })\n      })\n      .catch((err) => setState({ status: \'error\', error: err.message }))\n  }, [])\n\n  const pctTotal = (x) => (x * 100).toFixed(0)\n\n  return (\n    <>\n      <main>\n        {state.status === \'loading\' && <p className="note">Loading…</p>}\n        {state.status === \'error\' && (\n          <p className="note">Could not load data ({state.error}).</p>\n        )}\n        {state.status === \'empty\' && <p className="note">No data yet.</p>}\n\n        {state.status === \'ready\' && (\n          <>\n            <div className="legend">\n              <span className="key"><i className="swatch swatch-market" /> polymarket</span>\n              <span className="key"><i className="swatch swatch-kalshi" /> kalshi</span>\n              <span className="key key-muted">chance of winning the nomination</span>\n            </div>\n\n            {state.boards.filter((b) => b.cands.length).map((b) => {\n              const shown = open[b.id] ? b.cands : b.cands.slice(0, TOP_N)\n              return (\n                <section key={b.id} className="office board">\n                  <h2 className="office-title">\n                    <span>{b.title}</span>\n                    <span className="office-count">{b.cands.length} candidates</span>\n                  </h2>\n                  <ol className="races">\n                    {shown.map((c, i) => <NomineeRow key={c.key} c={c} rank={i + 1} />)}\n                  </ol>\n                  {b.cands.length > TOP_N && (\n                    <button type="button" className="show-all"\n                            onClick={() => setOpen((o) => ({ ...o, [b.id]: !o[b.id] }))}>\n                      {open[b.id] ? `Show top ${TOP_N}` : `Show all ${b.cands.length}`}\n                    </button>\n                  )}\n                  <p className="board-note">\n                    Candidates shown total {pctTotal(b.totals.polymarket)}% on\n                    Polymarket and {pctTotal(b.totals.kalshi)}% on Kalshi. The\n                    rest is spread across candidates under half a percent and\n                    anyone neither venue has listed yet.\n                  </p>\n                </section>\n              )\n            })}\n          </>\n        )}\n      </main>\n\n      <footer>\n        <p>\n          {state.asOf\n            ? `Market data last fetched ${state.asOf.replace(\'T\', \' \').slice(0, 16)} UTC.`\n            : \'\'}{\' \'}\n          Prices from Polymarket and Kalshi, recorded every six hours. Every\n          figure is committed to a{\' \'}\n          <a href="https://github.com/cohens714/oddsvspolls">public repository</a>{\' \'}\n          with its timestamp. Code MIT, data{\' \'}\n          <a href="https://creativecommons.org/licenses/by-nc/4.0/">CC BY-NC 4.0</a>.\n        </p>\n        <p className="caveat">\n          <strong>Why there are no polls here.</strong> Primary polls report\n          each candidate&rsquo;s share of the vote, and a 30% share in a\n          crowded field two years out says little about the chance of winning.\n          Rather than invent a conversion, this page shows the markets alone.\n        </p>\n        <p className="caveat">\n          <strong>Why the prices don&rsquo;t add to 100%.</strong> Each\n          candidate is a separate yes-or-no contract, so nothing forces a\n          party&rsquo;s board to sum to exactly 100%. Scaling everyone up to\n          fill the gap would inflate the favorites most, so the raw prices are\n          shown as traded.\n        </p>\n        <p className="caveat">\n          <strong>Reading long-dated prices.</strong> These contracts\n          don&rsquo;t settle until 2028, and money tied up that long has a\n          cost. That tends to make longshots look a little more likely, and\n          favorites a little less, than traders really believe.\n        </p>\n        <p className="caveat">\n          <strong>When the venues disagree.</strong> Gaps of {DISAGREE_PTS}{\' \'}\n          points or more are flagged. Each contract&rsquo;s resolution rules\n          are checked automatically on every collection, so a flagged gap here\n          is more likely a real difference between the two venues&rsquo;\n          traders than a mismatched contract.\n        </p>\n        {state.firstLive && (\n          <p className="caveat">\n            <strong>Where the history comes from.</strong> Lines before{\' \'}\n            {fmtDay(Date.parse(state.firstLive.slice(0, 10)))} are backfilled\n            at daily resolution from each venue&rsquo;s own price history\n            rather than recorded by us at the time.\n          </p>\n        )}\n      </footer>\n    </>\n  )\n}\n'

CSS_BLOCK = '\n/* ---------- nominees tab ---------- */\n\n/* The section switch sits on the eyebrow line rather than as a nav bar,\n   so the page opens on the headline the way it always has. */\n.masthead {\n  display: flex;\n  align-items: baseline;\n  justify-content: space-between;\n  flex-wrap: wrap;\n  gap: 0.75rem 1.5rem;\n  margin: 0 0 2rem;\n}\n\n.masthead .eyebrow { margin: 0; }\n\n.tabs {\n  display: flex;\n  gap: 1.1rem;\n  font-family: var(--mono);\n  font-size: 0.7rem;\n  letter-spacing: 0.1em;\n  text-transform: uppercase;\n}\n\n.tab {\n  color: var(--ink-faint);\n  text-decoration: none;\n  padding-bottom: 0.2rem;\n  border-bottom: 1.5px solid transparent;\n}\n\n.tab:hover { color: var(--ink-soft); }\n.tab-on { color: var(--ink); border-bottom-color: var(--ink); }\n\n.call-kalshi { color: var(--kalshi); }\n\n.board-note {\n  font-family: var(--mono);\n  font-size: 0.7rem;\n  line-height: 1.55;\n  color: var(--ink-faint);\n  margin: 1rem 0 0;\n  max-width: 62ch;\n}\n\n.show-all {\n  margin-top: 1.1rem;\n  font-family: var(--mono);\n  font-size: 0.7rem;\n  letter-spacing: 0.05em;\n  text-transform: uppercase;\n  color: var(--ink-soft);\n  background: none;\n  border: 1px solid var(--rule-strong);\n  border-radius: 3px;\n  padding: 0.45rem 0.8rem;\n  cursor: pointer;\n}\n\n.show-all:hover { color: var(--ink); border-color: var(--ink-soft); }\n'
CSS_MARKER = "/* ---------- nominees tab ---------- */"

# (description, exact text to find, replacement). Each must match once.
APP_PATCHES = [
    ("import",
     "import History from './History.jsx'\n",
     "import History from './History.jsx'\n"
     "import Nominees, { useTab, TabNav, NomineesHeader } from './Nominees.jsx'\n"),
    ("tab hook",
     "export default function App() {\n",
     "export default function App() {\n"
     "  const tab = useTab()\n"),
    ("masthead",
     '        <p className="eyebrow">oddsvspolls.com</p>\n'
     "        <h1>Where the markets and the polls disagree</h1>\n",
     '        <div className="masthead">\n'
     '          <p className="eyebrow">oddsvspolls.com</p>\n'
     "          <TabNav tab={tab} />\n"
     "        </div>\n"
     "        {tab === 'nominees' ? <NomineesHeader /> : (<>\n"
     "        <h1>Where the markets and the polls disagree</h1>\n"),
    ("header close",
     "          )}\n        </p>\n      </header>\n",
     "          )}\n        </p>\n        </>)}\n      </header>\n\n"
     "      {tab === 'nominees' ? <Nominees /> : (<>\n"),
    ("page close",
     "      </footer>\n    </>\n  )\n}",
     "      </footer>\n      </>)}\n    </>\n  )\n}"),
]

APP = os.path.join("src", "App.jsx")
CSS = os.path.join("src", "styles.css")
NOM = os.path.join("src", "Nominees.jsx")
TOUCHED = [APP, CSS, NOM]


def run(cmd, **kw):
    print("$ " + " ".join(cmd))
    return subprocess.run(cmd, **kw)


def repo_root():
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("Run this from inside the oddsvspolls repo.")
    return r.stdout.strip()


def revert(quiet=False):
    subprocess.run(["git", "checkout", "--", APP, CSS])
    if os.path.exists(NOM):
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", NOM],
                                 capture_output=True).returncode == 0
        if tracked:
            subprocess.run(["git", "checkout", "--", NOM])
        else:
            os.remove(NOM)
    if not quiet:
        print("Reverted App.jsx and styles.css, removed Nominees.jsx.")


def install():
    for p in (APP, CSS):
        if not os.path.exists(p):
            sys.exit(f"Can't find {p}; nothing changed.")

    installed = "from './Nominees.jsx'" in open(APP).read()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", APP, CSS],
                           capture_output=True, text=True).stdout.strip()
    if dirty and not installed:
        sys.exit(f"You have uncommitted edits in App.jsx or styles.css:\n{dirty}\n"
                 "Commit or stash them first so --revert can't lose your work.")

    # Pull first so the nominee CSVs the collector has committed are here
    # for the local preview.
    run(["git", "pull", "--rebase", "--autostash", "origin", "main"])

    app = open(APP).read()
    if "from './Nominees.jsx'" in app:
        print("App.jsx already has the tab; leaving it alone")
    else:
        for name, old, new in APP_PATCHES:
            n = app.count(old)
            if n != 1:
                sys.exit(f"App.jsx: expected the '{name}' spot once, found {n}. "
                         "Nothing changed. Upload App.jsx again and I'll adjust.")
        for name, old, new in APP_PATCHES:
            app = app.replace(old, new, 1)
        open(APP, "w").write(app)
        print("patched App.jsx")

    open(NOM, "w").write(NOMINEES_JSX)
    print("wrote src/Nominees.jsx")

    css = open(CSS).read()
    if CSS_MARKER in css:
        print("styles.css already has the tab styles")
    else:
        with open(CSS, "a") as f:
            f.write(CSS_BLOCK)
        print("added tab styles to styles.css")

    print("\nbuilding to check it compiles...\n")
    b = subprocess.run(["npm", "run", "build"], capture_output=True, text=True)
    if b.returncode != 0:
        print(b.stdout[-3000:], b.stderr[-3000:], sep="\n")
        revert(quiet=True)
        sys.exit("\nBuild failed, so everything was put back. Paste the output above.")
    print("build ok\n")

    for f in ("nominee_snapshots.csv", "nominee_backfill.csv"):
        p = os.path.join("public", f)
        status = f"{sum(1 for _ in open(p)) - 1} rows" if os.path.exists(p) else "missing"
        print(f"  public/{f}: {status}")

    print("\nPreview it locally:\n"
          "  npm run dev\n"
          "then open the address it prints and add #nominees to the end.\n"
          "Press Ctrl+C to stop the preview. When it looks right:\n"
          "  python3 analysis/add_nominees_tab.py --ship\n"
          "If it doesn't:\n"
          "  python3 analysis/add_nominees_tab.py --revert")


def ship():
    app = open(APP).read() if os.path.exists(APP) else ""
    if "from './Nominees.jsx'" not in app:
        sys.exit("The tab isn't installed yet; run without --ship first.")
    for cmd in (["git", "add"] + TOUCHED,
                ["git", "commit", "-m", "Add 2028 nominees tab"],
                ["git", "pull", "--rebase", "--autostash", "origin", "main"],
                ["git", "push", "origin", "main"]):
        if run(cmd).returncode != 0:
            sys.exit("git step failed; paste the output.")
    print("\nShipped. Cloudflare usually deploys within a couple of minutes:\n"
          "  https://oddsvspolls.com/#nominees")


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
