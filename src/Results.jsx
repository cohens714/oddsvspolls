import { useEffect, useState } from 'react'

// Renders data/scorecard.json, which the scorecard workflow writes after
// polls close. Before then the file is "{}" and this renders nothing.
//
// A dry-run scorecard (simulated outcomes) only ever shows on localhost, so
// a preview file that slips into a commit can't publish invented results.

const NO_STORE = { cache: 'no-store' }
const VENUES = [
  ['polymarket', 'Polymarket'],
  ['kalshi', 'Kalshi'],
]

const isLocal = () =>
  ['localhost', '127.0.0.1'].includes(window.location.hostname)

const fmt = (x) => (x == null ? '–' : x.toFixed(3))
const pct = (x) => (x == null ? '–' : `${Math.round(x * 100)}%`)

function horizonLabel(h) {
  let m = /^(\d+)-(\d+)d$/.exec(h)
  if (m) {
    const [a, b] = [Number(m[1]), Number(m[2])]
    if (a === 0 && b === 0) return 'Election day'
    if (a === b) return `${a} day${a === 1 ? '' : 's'} out`
    return `${a} to ${b} days out`
  }
  m = /^(\d+)\+d$/.exec(h)
  if (m) return `${m[1]}+ days out`
  return h
}

function horizonTable(report) {
  // { horizon: { rank, polls, venue } } from one venue's report
  const out = {}
  for (const r of report?.horizon_scores ?? []) {
    const row = (out[r.horizon] ??= { rank: r.horizon_rank })
    row[r.source === 'polls' ? 'polls' : 'venue'] = r.brier
  }
  return out
}

function Pair({ polls, venue }) {
  if (polls == null || venue == null) return <td className="num">–</td>
  const pollsBetter = polls < venue
  return (
    <td className="num">
      <span className={pollsBetter ? 'better' : ''}>{fmt(polls)}</span>
      {' / '}
      <span className={!pollsBetter ? 'better' : ''}>{fmt(venue)}</span>
    </td>
  )
}

function verdict(h2h, venueName) {
  const b = h2h?.brier
  if (!b) return null
  const leader = b.diff < 0 ? 'Polls' : venueName
  const gap = Math.abs(b.diff)
  return (
    <p>
      <strong>Polls {fmt(b.brier_a)}</strong> vs{' '}
      <strong>{venueName} {fmt(b.brier_b)}</strong> across {b.n_races} races.{' '}
      {gap < 0.0005
        ? 'Effectively a tie.'
        : `${leader} ${leader === 'Polls' ? 'were' : 'was'} more accurate by ${fmt(gap)}`}
      {gap < 0.0005 ? '' : b.significant
        ? ', a difference that holds up statistically.'
        : `, but with ${b.n_races} races in ${b.n_cycles === 1 ? 'one election' : `${b.n_cycles} elections`} that gap is within the noise.`}
    </p>
  )
}

export default function Results() {
  const [data, setData] = useState(null)
  const [meta, setMeta] = useState({})

  useEffect(() => {
    let alive = true
    fetch('/scorecard.json', NO_STORE)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
      .then((d) => { if (alive) setData(d) })
    fetch('/race_meta.json', NO_STORE)
      .then((r) => (r.ok ? r.json() : {}))
      .catch(() => ({}))
      .then((m) => { if (alive) setMeta(m) })
    return () => { alive = false }
  }, [])

  if (!data?.reports || !(data.final_forecasts?.length)) return null
  const dry = data.mode === 'dry-run'
  if (dry && !isLocal()) return null

  const reports = data.reports
  const tables = Object.fromEntries(
    VENUES.map(([v]) => [v, horizonTable(reports[`${v}_vs_polls`])]))
  const horizons = [...new Set(VENUES.flatMap(([v]) => Object.keys(tables[v])))]
    .sort((a, b) => (tables.polymarket[a]?.rank ?? tables.kalshi[a]?.rank ?? 0)
      - (tables.polymarket[b]?.rank ?? tables.kalshi[b]?.rank ?? 0))

  const offices = ['senate', 'governor'].filter((o) =>
    VENUES.some(([v]) => reports[`${v}_vs_polls`]?.by_office?.[o]))

  // Chance each source gave the eventual winner. prob is the Democrat's.
  const forWinner = (p, outcome) => (p == null ? null : outcome === 1 ? p : 1 - p)
  const races = data.final_forecasts.map((f) => {
    const m = meta[f.race_id] ?? {}
    const winner = f.race_id.endsWith('-control')
      ? (f.outcome === 1 ? 'Democrats' : 'Republicans')
      : f.outcome === 1
      ? `${m.dem_short ?? 'Democrat'} (D)`
      : `${m.rep_short ?? 'Republican'} (R)`
    const chances = {
      polls: forWinner(f.polls, f.outcome),
      polymarket: forWinner(f.polymarket, f.outcome),
      kalshi: forWinner(f.kalshi, f.outcome),
    }
    const vals = Object.values(chances).filter((x) => x != null)
    return { ...f, winner, chances, worst: vals.length ? Math.min(...vals) : 1 }
  }).sort((a, b) => a.worst - b.worst)

  // Calibration: polls from the Polymarket report, which covers the most races.
  const calSources = [
    ['polls', 'Polls', reports.polymarket_vs_polls?.calibration?.polls],
    ...VENUES.map(([v, name]) => [v, name, reports[`${v}_vs_polls`]?.calibration?.[v]]),
  ].filter(([, , bins]) => bins?.length)
  const binKeys = [...new Set(calSources.flatMap(([, , bins]) =>
    bins.filter((b) => b.n > 0).map((b) => b.bin_low.toFixed(1))))].sort()

  const pending = data.coverage?.pending ?? []

  return (
    <section className="results">
      {dry && (
        <p className="dry-banner">
          Local preview with simulated outcomes. This never shows on the live site.
        </p>
      )}

      <h2>Results</h2>
      <p className="note-inline">
        Scored with the Brier score: the average squared gap between the
        forecast and what happened. Zero is perfect, 0.25 is a coin flip, and
        lower is better. Each market venue is compared with polls only on days
        when both had a reading, so the poll figure differs slightly between
        the two comparisons.
        {pending.length > 0 && ` ${pending.length} race${pending.length === 1 ? ' is' : 's are'} still pending and not yet scored.`}
      </p>

      {VENUES.map(([v, name]) => {
        const r = reports[`${v}_vs_polls`]
        return r ? (
          <div key={v} className="verdict">
            <h3>{name} vs polls</h3>
            {verdict(r.head_to_head, name)}
          </div>
        ) : null
      })}

      <h3>By how far out the forecast was made</h3>
      <div className="table-wrap">
        <table className="results-table">
          <thead>
            <tr>
              <th>Days out</th>
              {VENUES.map(([v, name]) => <th key={v} className="num">Polls / {name}</th>)}
            </tr>
          </thead>
          <tbody>
            {horizons.map((h) => (
              <tr key={h}>
                <td>{horizonLabel(h)}</td>
                {VENUES.map(([v]) => (
                  <Pair key={v} polls={tables[v][h]?.polls} venue={tables[v][h]?.venue} />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="note-inline">The more accurate figure in each pair is in bold.</p>

      {offices.length > 0 && (
        <>
          <h3>Senate and governor</h3>
          <div className="table-wrap">
            <table className="results-table">
              <thead>
                <tr>
                  <th>Office</th>
                  {VENUES.map(([v, name]) => <th key={v} className="num">Polls / {name}</th>)}
                </tr>
              </thead>
              <tbody>
                {offices.map((o) => (
                  <tr key={o}>
                    <td>{o === 'senate' ? 'Senate' : 'Governor'}</td>
                    {VENUES.map(([v]) => {
                      const b = reports[`${v}_vs_polls`]?.by_office?.[o]?.head_to_head?.brier
                      return <Pair key={v} polls={b?.brier_a} venue={b?.brier_b} />
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {calSources.length > 0 && binKeys.length > 0 && (
        <>
          <h3>Calibration</h3>
          <p className="note-inline">
            When a source gave a candidate a chance in each range, how often
            that candidate actually won, with the number of race-days behind
            each figure. A well calibrated source lands inside its own range.
          </p>
          <div className="table-wrap">
            <table className="results-table">
              <thead>
                <tr>
                  <th>Forecast</th>
                  {calSources.map(([k, name]) => <th key={k} className="num">{name}</th>)}
                </tr>
              </thead>
              <tbody>
                {binKeys.map((key) => {
                  const lo = Number(key)
                  return (
                    <tr key={key}>
                      <td>{Math.round(lo * 100)} to {Math.round(lo * 100) + 10}%</td>
                      {calSources.map(([k, , bins]) => {
                        const b = bins.find((x) => x.bin_low.toFixed(1) === key)
                        return (
                          <td key={k} className="num">
                            {b && b.n > 0 ? <>{pct(b.observed_rate)} <span className="n">({b.n})</span></> : '–'}
                          </td>
                        )
                      })}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      <h3>Every race, biggest misses first</h3>
      <p className="note-inline">
        The chance each source gave the eventual winner in its final forecast
        before polls closed. Figures under 50% mean that source picked the
        loser.
      </p>
      <div className="table-wrap">
        <table className="results-table">
          <thead>
            <tr>
              <th>Race</th>
              <th>Winner</th>
              <th className="num">Polls</th>
              {VENUES.map(([v, name]) => <th key={v} className="num">{name}</th>)}
            </tr>
          </thead>
          <tbody>
            {races.map((r) => (
              <tr key={r.race_id}>
                <td>{r.race_id.endsWith('-control') ? r.label : `${r.label} ${r.office === 'governor' ? 'governor' : 'Senate'}`}</td>
                <td>{r.winner}</td>
                {['polls', 'polymarket', 'kalshi'].map((s) => (
                  <td key={s} className={`num${r.chances[s] != null && r.chances[s] < 0.5 ? ' miss' : ''}`}>
                    {pct(r.chances[s])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.generated_at && !dry && (
        <p className="note-inline">
          Updated {new Date(data.generated_at).toLocaleString('en-US', {
            timeZone: 'America/New_York', month: 'short', day: 'numeric',
            hour: 'numeric', minute: '2-digit',
          })} ET.
        </p>
      )}
    </section>
  )
}
