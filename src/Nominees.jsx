import { useEffect, useState } from 'react'

// Same reasoning as the race data: these files are rewritten in place
// several times a day, so a cached copy is stale rather than merely old.
const FETCH_OPTS = { cache: 'no-store' }

const BOARDS = [
  { id: '2028-president', title: 'Presidency' },
  { id: '2028-dem-nominee', title: 'Democratic nominee' },
  { id: '2028-rep-nominee', title: 'Republican nominee' },
]
const VENUES = ['polymarket', 'kalshi']
const TOP_N = 10
const DISAGREE_PTS = 5
const DAY_MS = 86400000

// ---------- tab state ----------

// The tab lives in the URL hash so a link to the nominees page can be
// shared, without adding a router or any Cloudflare routing rules.
function readTab() {
  return window.location.hash === '#nominees' ? 'nominees' : 'races'
}

export function useTab() {
  const [tab, setTab] = useState(readTab)
  useEffect(() => {
    const onHash = () => { setTab(readTab()); window.scrollTo(0, 0) }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  return tab
}

export function TabNav({ tab }) {
  return (
    <nav className="tabs" aria-label="Sections">
      <a href="#races" className={tab === 'races' ? 'tab tab-on' : 'tab'}
         aria-current={tab === 'races' ? 'page' : undefined}>2026 races</a>
      <a href="#nominees" className={tab === 'nominees' ? 'tab tab-on' : 'tab'}
         aria-current={tab === 'nominees' ? 'page' : undefined}>2028 election</a>
    </nav>
  )
}

export function NomineesHeader() {
  return (
    <>
      <h1>Who the markets expect in 2028</h1>
      <p className="lede">
        Polymarket and Kalshi prices for who wins the White House in 2028,
        and for each party&rsquo;s nomination, side by side and tracked over
        time. There is no polling column here: this far out, primary polls
        measure a share of the vote and general-election polls test matchups
        that may never happen, so neither maps onto a chance of winning.
      </p>
    </>
  )
}

// ---------- data ----------

function parseCsv(text) {
  const rows = []
  let row = []
  let field = ''
  let quoted = false
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++ } else quoted = false
      } else field += c
    } else if (c === '"') quoted = true
    else if (c === ',') { row.push(field); field = '' }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++
      row.push(field); field = ''
      if (row.length > 1 || row[0] !== '') rows.push(row)
      row = []
    } else field += c
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row) }
  const [head, ...body] = rows
  if (!head) return []
  return body.map((r) => Object.fromEntries(head.map((h, j) => [h, r[j] ?? ''])))
}

async function loadCsv(path, optional = false) {
  try {
    const res = await fetch(path, FETCH_OPTS)
    if (!res.ok) throw new Error(`${path} returned ${res.status}`)
    const text = await res.text()
    // A missing file can come back as the site's index page with a 200,
    // so check it is actually the CSV before parsing.
    if (!text.startsWith('ts_utc')) throw new Error(`${path} is not the expected CSV`)
    return parseCsv(text)
  } catch (err) {
    if (optional) return []
    throw err
  }
}

function change30(series, cur) {
  const v = cur.polymarket !== null ? 'polymarket' : 'kalshi'
  const s = series[v]
  if (!s.length || cur[v] === null) return null
  const target = s[s.length - 1].t - 30 * DAY_MS
  let then = null
  for (const pt of s) { if (pt.t <= target) then = pt; else break }
  if (!then) return null
  return { venue: v, pts: (cur[v] - then.p) * 100 }
}

function buildBoards(rows) {
  const raw = {}
  const runTs = {}
  let asOf = ''
  let firstLive = ''

  for (const r of rows) {
    const p = parseFloat(r.price)
    if (!r.event_id || !r.candidate_key || !VENUES.includes(r.venue) || !Number.isFinite(p)) continue
    const live = !(r.price_source || '').startsWith('backfill')
    const board = (raw[r.event_id] ||= {})
    const c = (board[r.candidate_key] ||= {
      key: r.candidate_key, labels: {}, latest: {},
      days: { polymarket: {}, kalshi: {} },
    })
    // One point per venue per day, keeping the day's last observation.
    const day = r.ts_utc.slice(0, 10)
    const prev = c.days[r.venue][day]
    if (!prev || r.ts_utc > prev.ts) c.days[r.venue][day] = { ts: r.ts_utc, p }
    if (r.candidate_label) c.labels[r.venue] = r.candidate_label
    if (live) {
      const l = c.latest[r.venue]
      if (!l || r.ts_utc > l.ts) c.latest[r.venue] = { ts: r.ts_utc, p }
      const rk = `${r.event_id}|${r.venue}`
      if (!runTs[rk] || r.ts_utc > runTs[rk]) runTs[rk] = r.ts_utc
      if (r.ts_utc > asOf) asOf = r.ts_utc
      if (!firstLive || r.ts_utc < firstLive) firstLive = r.ts_utc
    }
  }

  const boards = BOARDS.map(({ id, title }) => {
    const cands = Object.values(raw[id] || {}).map((c) => {
      // A current price only counts if it came from the latest collection
      // run. A candidate who has since dropped under the collector's floor
      // would otherwise keep showing a stale figure.
      const cur = {}
      for (const v of VENUES) {
        const l = c.latest[v]
        cur[v] = l && l.ts === runTs[`${id}|${v}`] ? l.p : null
      }
      const vals = VENUES.map((v) => cur[v]).filter((x) => x !== null)
      if (!vals.length) return null
      const series = {}
      for (const v of VENUES) {
        series[v] = Object.keys(c.days[v]).sort()
          .map((day) => ({ t: Date.parse(day), p: c.days[v][day].p }))
      }
      return {
        key: c.key,
        label: c.labels.polymarket || c.labels.kalshi || c.key,
        cur,
        avg: vals.reduce((a, b) => a + b, 0) / vals.length,
        series,
        gap: cur.polymarket !== null && cur.kalshi !== null
          ? (cur.polymarket - cur.kalshi) * 100 : null,
        change: change30(series, cur),
      }
    }).filter(Boolean).sort((a, b) => b.avg - a.avg)

    const totals = {}
    for (const v of VENUES) totals[v] = cands.reduce((s, c) => s + (c.cur[v] ?? 0), 0)
    return { id, title, cands, totals }
  })

  return { boards, asOf, firstLive }
}

// ---------- formatting ----------

const fmtPct = (p) => (p * 100 < 1 ? '<1' : (p * 100).toFixed(0))
const fmtDay = (t) => new Date(t).toLocaleDateString('en-US',
  { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })
const fmtMonth = (t) => new Date(t).toLocaleDateString('en-US',
  { month: 'short', year: 'numeric', timeZone: 'UTC' })

// ---------- chart ----------

function nearest(s, t) {
  if (!s.length) return null
  let lo = 0
  let hi = s.length - 1
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (s[mid].t < t) lo = mid + 1; else hi = mid
  }
  const b = s[lo]
  const a = s[lo - 1]
  return a && Math.abs(a.t - t) < Math.abs(b.t - t) ? a : b
}

// Each row gets its own vertical scale, printed in the caption. On a
// shared scale everyone below the favorite would draw as a flat line on
// the floor, and the movement worth seeing is exactly theirs.
function niceMax(m) {
  return Math.ceil(Math.max(0.1, m * 1.15) * 20) / 20
}

function NomineeChart({ series, label }) {
  const [hover, setHover] = useState(null)
  const all = [...series.polymarket, ...series.kalshi]
  if (all.length < 2) return null

  let t0 = Infinity, t1 = -Infinity, pMax = 0
  for (const d of all) {
    if (d.t < t0) t0 = d.t
    if (d.t > t1) t1 = d.t
    if (d.p > pMax) pMax = d.p
  }
  const span = Math.max(t1 - t0, 1)
  const yMax = niceMax(pMax)
  const X = (t) => ((t - t0) / span) * 100
  const Y = (p) => 40 - (Math.min(p, yMax) / yMax) * 40
  const path = (s) => s.map((d, i) =>
    `${i ? 'L' : 'M'}${X(d.t).toFixed(2)},${Y(d.p).toFixed(2)}`).join('')

  const onMove = (e) => {
    const box = e.currentTarget.getBoundingClientRect()
    const f = Math.min(1, Math.max(0, (e.clientX - box.left) / box.width))
    setHover(t0 + f * span)
  }

  const pts = hover === null ? null : VENUES.map((v) => {
    const pt = nearest(series[v], hover)
    return { v, pt: pt && Math.abs(pt.t - hover) <= 3 * DAY_MS ? pt : null }
  })
  const hx = hover === null ? 0 : X(hover)

  return (
    <figure className="history">
      <div className="history-plot"
           onPointerMove={onMove} onPointerDown={onMove}
           onPointerLeave={() => setHover(null)}
           role="img" aria-label={`${label}: nomination price history`}>
        <svg viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">
          {series.kalshi.length > 1 && (
            <path className="h-line h-kalshi" d={path(series.kalshi)} />
          )}
          {series.polymarket.length > 1 && (
            <path className="h-line h-market" d={path(series.polymarket)} />
          )}
          {hover !== null && (
            <line className="h-cross" x1={hx} x2={hx} y1="0" y2="40"
                  vectorEffect="non-scaling-stroke" />
          )}
        </svg>
        {pts && pts.map(({ v, pt }) => pt && (
          <span key={v}
                className={`h-dot ${v === 'kalshi' ? 'h-dot-kalshi' : 'h-dot-market'}`}
                style={{ left: `${X(pt.t)}%`, top: `${(Y(pt.p) / 40) * 100}%` }} />
        ))}
        {pts && (
          <div className={hx > 66 ? 'h-tip h-tip-left' : 'h-tip'}
               style={{ left: `${hx}%` }}>
            <div className="h-tip-date"><span>{fmtDay(hover)}</span></div>
            {pts.map(({ v, pt }) => (
              <div key={v} className="h-tip-row">
                <i className={`swatch ${v === 'kalshi' ? 'swatch-kalshi' : 'swatch-market'}`} />
                <span className="h-tip-label">{v}</span>
                <span className="h-tip-value">
                  {pt ? `${(pt.p * 100).toFixed(1)}%` : 'n/a'}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
      <figcaption>
        <span>{fmtMonth(t0)}</span>
        <span>scale 0 to {Math.round(yMax * 100)}%</span>
      </figcaption>
    </figure>
  )
}

// ---------- rows ----------

function NomineeRow({ c, rank }) {
  const listedOn = VENUES.filter((v) => c.series[v].length)
  const disagree = c.gap !== null && Math.abs(c.gap) >= DISAGREE_PTS
  return (
    <li className="race">
      <div className="race-head">
        <div className="race-title">
          <span className="race-name">{c.label}</span>
          <span className="matchup">#{rank}</span>
        </div>
        <div className="calls">
          {VENUES.map((v) => (
            <span key={v} className="call">
              <span className="call-name">{v}</span>
              {c.cur[v] !== null ? (
                <span className={`call-pct ${v === 'kalshi' ? 'call-kalshi' : 'call-market'}`}>
                  {fmtPct(c.cur[v])}<span className="pct">%</span>
                </span>
              ) : (
                <span className="call-none">
                  {c.series[v].length ? 'under 0.5%' : 'not listed'}
                </span>
              )}
            </span>
          ))}
          {c.gap !== null && (
            <span className="gap-line">{Math.abs(c.gap).toFixed(1)} pts apart</span>
          )}
        </div>
      </div>

      <NomineeChart series={c.series} label={c.label} />

      <div className="race-meta">
        {c.change && (
          <span>
            {c.change.pts >= 0 ? '+' : ''}{c.change.pts.toFixed(1)} pts in 30 days
            {' '}({c.change.venue})
          </span>
        )}
        {listedOn.length === 1 && <span>listed on {listedOn[0]} only</span>}
        {disagree && <span className="flag">venues disagree</span>}
      </div>
    </li>
  )
}

// ---------- page ----------

export default function Nominees() {
  const [state, setState] = useState({ status: 'loading' })
  const [open, setOpen] = useState({})

  useEffect(() => {
    Promise.all([
      loadCsv('/nominee_snapshots.csv'),
      loadCsv('/nominee_backfill.csv', true),
    ])
      .then(([live, back]) => {
        const data = buildBoards([...back, ...live])
        const any = data.boards.some((b) => b.cands.length)
        setState({ status: any ? 'ready' : 'empty', ...data })
      })
      .catch((err) => setState({ status: 'error', error: err.message }))
  }, [])

  const pctTotal = (x) => (x * 100).toFixed(0)

  return (
    <>
      <main>
        {state.status === 'loading' && <p className="note">Loading…</p>}
        {state.status === 'error' && (
          <p className="note">Could not load data ({state.error}).</p>
        )}
        {state.status === 'empty' && <p className="note">No data yet.</p>}

        {state.status === 'ready' && (
          <>
            <div className="legend">
              <span className="key"><i className="swatch swatch-market" /> polymarket</span>
              <span className="key"><i className="swatch swatch-kalshi" /> kalshi</span>
              <span className="key key-muted">chance of winning</span>
            </div>

            {state.boards.filter((b) => b.cands.length).map((b) => {
              const shown = open[b.id] ? b.cands : b.cands.slice(0, TOP_N)
              return (
                <section key={b.id} className="office board">
                  <h2 className="office-title">
                    <span>{b.title}</span>
                    <span className="office-count">{b.cands.length} candidates</span>
                  </h2>
                  <ol className="races">
                    {shown.map((c, i) => <NomineeRow key={c.key} c={c} rank={i + 1} />)}
                  </ol>
                  {b.cands.length > TOP_N && (
                    <button type="button" className="show-all"
                            onClick={() => setOpen((o) => ({ ...o, [b.id]: !o[b.id] }))}>
                      {open[b.id] ? `Show top ${TOP_N}` : `Show all ${b.cands.length}`}
                    </button>
                  )}
                  <p className="board-note">
                    Candidates shown total {pctTotal(b.totals.polymarket)}% on
                    Polymarket and {pctTotal(b.totals.kalshi)}% on Kalshi. The
                    rest is spread across candidates under half a percent and
                    anyone neither venue has listed yet.
                  </p>
                </section>
              )
            })}
          </>
        )}
      </main>

      <footer>
        <p>
          {state.asOf
            ? `Market data last fetched ${state.asOf.replace('T', ' ').slice(0, 16)} UTC.`
            : ''}{' '}
          Prices from Polymarket and Kalshi, recorded every six hours. Every
          figure is committed to a{' '}
          <a href="https://github.com/cohens714/oddsvspolls">public repository</a>{' '}
          with its timestamp. Code MIT, data{' '}
          <a href="https://creativecommons.org/licenses/by-nc/4.0/">CC BY-NC 4.0</a>.
        </p>
        <p className="caveat">
          <strong>Why there are no polls here.</strong> Primary polls report
          each candidate&rsquo;s share of the vote, and a 30% share in a
          crowded field two years out says little about the chance of winning.
          General-election polls this early can only test hypothetical
          matchups. Rather than invent a conversion, this page shows the
          markets alone.
        </p>
        <p className="caveat">
          <strong>Why the prices don&rsquo;t add to 100%.</strong> Each
          candidate is a separate yes-or-no contract, so nothing forces a
          board to sum to exactly 100%. Scaling everyone up to
          fill the gap would inflate the favorites most, so the raw prices are
          shown as traded.
        </p>
        <p className="caveat">
          <strong>Reading long-dated prices.</strong> These contracts
          don&rsquo;t settle until 2028, and money tied up that long has a
          cost. That tends to make longshots look a little more likely, and
          favorites a little less, than traders really believe.
        </p>
        <p className="caveat">
          <strong>Reading the presidency board.</strong> A presidency
          contract only pays if its candidate wins the nomination and then
          the general election, so each price there should sit at or below
          the same person&rsquo;s nomination price. Dividing one by the
          other gives the market&rsquo;s view of how that candidate would do
          as the nominee.
        </p>
        <p className="caveat">
          <strong>When the venues disagree.</strong> Gaps of {DISAGREE_PTS}{' '}
          points or more are flagged. Each contract&rsquo;s resolution rules
          are checked automatically on every collection, so a flagged gap here
          is more likely a real difference between the two venues&rsquo;
          traders than a mismatched contract.
        </p>
        {state.firstLive && (
          <p className="caveat">
            <strong>Where the history comes from.</strong> Lines before{' '}
            {fmtDay(Date.parse(state.firstLive.slice(0, 10)))} are backfilled
            at daily resolution from each venue&rsquo;s own price history
            rather than recorded by us at the time.
          </p>
        )}
      </footer>
    </>
  )
}
