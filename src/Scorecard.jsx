import { useEffect, useState } from 'react'

const FREEZE_URL = 'https://github.com/cohens714/oddsvspolls/commit/46fe190'
const NO_STORE = { cache: 'no-store' }

function parseCsv(text) {
  const rows = []
  let row = []
  let cell = ''
  let quoted = false
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') { cell += '"'; i++ } else quoted = false
      } else cell += c
    } else if (c === '"') quoted = true
    else if (c === ',') { row.push(cell); cell = '' }
    else if (c === '\n') { row.push(cell); rows.push(row); row = []; cell = '' }
    else if (c !== '\r') cell += c
  }
  if (cell || row.length) { row.push(cell); rows.push(row) }
  const [head, ...body] = rows
  if (!head) return []
  return body
    .filter((r) => r.length > 1)
    .map((r) => Object.fromEntries(head.map((h, j) => [h.trim(), (r[j] ?? '').trim()])))
}

function officeName(id) {
  if (id.includes('-gov-')) return 'governor'
  if (id.includes('-senate-')) return 'Senate'
  return ''
}

function raceName(id, meta) {
  return `${meta[id]?.label ?? id} ${officeName(id)}`.trim()
}

function winnerName(row, meta) {
  const m = meta[row.race_id] ?? {}
  if (row.winner === 'dem') return `${m.dem_short ?? 'Democrat'} (D)`
  if (row.winner === 'rep') return `${m.rep_short ?? 'Republican'} (R)`
  return 'winner not recorded'
}

export function ScorecardHeader() {
  return (
    <>
      <h1>Scorecard</h1>
      <p className="lede">
        Which was more accurate in 2026, the prediction markets or the polls?
        Results publish here after polls close on November 3 and update as
        races are called.
      </p>
    </>
  )
}

export default function Scorecard() {
  const [state, setState] = useState({ status: 'loading', rows: [], meta: {} })

  useEffect(() => {
    let alive = true
    Promise.all([
      fetch('/outcomes.csv', NO_STORE).then((r) => {
        if (!r.ok) throw new Error(String(r.status))
        return r.text()
      }),
      fetch('/race_meta.json', NO_STORE)
        .then((r) => (r.ok ? r.json() : {}))
        .catch(() => ({})),
    ])
      .then(([text, meta]) => {
        if (alive) setState({ status: 'ready', rows: parseCsv(text), meta })
      })
      .catch(() => {
        if (alive) setState({ status: 'error', rows: [], meta: {} })
      })
    return () => { alive = false }
  }, [])

  const { rows, meta } = state
  const called = rows
    .filter((r) => r.status === 'called')
    .sort((a, b) => (a.called_date || '').localeCompare(b.called_date || '')
      || raceName(a.race_id, meta).localeCompare(raceName(b.race_id, meta)))

  return (
    <main className="scorecard">
      <section>
        <h2>Race tracker</h2>
        {state.status === 'loading' && <p className="note">Loading…</p>}
        {state.status === 'error' && (
          <p className="note">The race tracker could not be loaded right now.</p>
        )}
        {state.status === 'ready' && (
          <>
            <div className="tracker">
              <div><span className="tracker-num">{rows.length}</span>
                <span className="tracker-label">races tracked</span></div>
              <div><span className="tracker-num">{called.length}</span>
                <span className="tracker-label">called</span></div>
              <div><span className="tracker-num">{rows.length - called.length}</span>
                <span className="tracker-label">pending</span></div>
            </div>
            {called.length > 0 && (
              <ul className="called-list">
                {called.map((r) => (
                  <li key={r.race_id}>
                    <span>{raceName(r.race_id, meta)}</span>
                    <span>{winnerName(r, meta)}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>

      <section>
        <h2>What the scorecard will show</h2>
        <p>
          How accurate each source was depending on how far out the forecast
          was made, so you can see when markets and polls are each at their
          most reliable. Whether each source was well calibrated: when it said
          70%, did those things happen about 70% of the time? A head-to-head
          comparison of Polymarket against polls and Kalshi against polls,
          with Senate and governor races reported separately. And the biggest
          misses, the races where a source was most confident and most wrong.
        </p>
      </section>

      <section>
        <h2>Rules set in advance</h2>
        <p>
          Every scoring rule was fixed before the election. The assumptions
          used to turn polls into probabilities were{' '}
          <a href={FREEZE_URL}>frozen on October 2, 2026</a>, and each
          source's final forecast is its last reading before 6 p.m. Eastern on
          election day. The full details are on the{' '}
          <a href="#methodology">How it works</a> page.
        </p>
      </section>

      <section>
        <h2>What to expect</h2>
        <p>
          One election is a small sample, and a nationwide polling miss can
          push every race the same way. The overall comparison may well be too
          close to call. The detail by days out is where real differences are
          most likely to show up, and the record grows with every cycle.
        </p>
      </section>
    </main>
  )
}
