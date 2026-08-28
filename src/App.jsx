import { useEffect, useState } from 'react'
import GapBar from './GapBar.jsx'
import { parseCsv, groupByRace, RACE_LABELS } from './data.js'

export default function App() {
  const [state, setState] = useState({ status: 'loading', races: [], asOf: null })

  useEffect(() => {
    fetch('/snapshots.csv')
      .then((r) => {
        if (!r.ok) throw new Error(`snapshots.csv returned ${r.status}`)
        return r.text()
      })
      .then((text) => {
        const rows = parseCsv(text)
        const races = groupByRace(rows)
        const asOf = rows.reduce(
          (latest, r) => (r.fetched_at > latest ? r.fetched_at : latest),
          '',
        )
        setState({ status: races.length ? 'ready' : 'empty', races, asOf })
      })
      .catch((err) => setState({ status: 'error', races: [], error: err.message }))
  }, [])

  return (
    <>
      <header>
        <p className="eyebrow">oddsvspolls.com</p>
        <h1>Where the markets and the polls disagree</h1>
        <p className="lede">
          Prediction market prices for the 2026 midterms, captured four times a
          day. Poll-derived probabilities are not wired up yet, so the gap
          column is empty yet by design rather than by omission.
        </p>
      </header>

      <main>
        {state.status === 'loading' && <p className="note">Loading snapshots…</p>}

        {state.status === 'error' && (
          <p className="note">
            Could not load snapshots ({state.error}). The collector may not have
            run yet.
          </p>
        )}

        {state.status === 'empty' && (
          <p className="note">
            No snapshots recorded yet. The collector runs at 02:00, 08:00, 14:00
            and 20:00 UTC.
          </p>
        )}

        {state.status === 'ready' && (
          <>
            <div className="legend">
              <span className="key">
                <i className="swatch swatch-market" /> market
              </span>
              <span className="key">
                <i className="swatch swatch-poll" /> polls
              </span>
              <span className="key key-muted">
                probability the Democratic candidate wins
              </span>
            </div>

            <ol className="races">
              {state.races.map((race) => (
                <li key={race.race_id} className="race">
                  <div className="race-head">
                    <span className="race-name">
                      {RACE_LABELS[race.race_id] || race.race_id}
                    </span>
                    <span className="race-figure">
                      {(race.market * 100).toFixed(0)}
                      <span className="pct">%</span>
                    </span>
                  </div>

                  <GapBar market={race.market} poll={race.poll} />

                  <div className="race-meta">
                    <span>{race.days_out} days out</span>
                    <span>{race.points} snapshots</span>
                    <span>
                      {race.volume
                        ? `$${Math.round(race.volume).toLocaleString()} volume`
                        : 'volume unreported'}
                    </span>
                    {race.thin && <span className="flag">thin market</span>}
                  </div>
                </li>
              ))}
            </ol>
          </>
        )}
      </main>

      <footer>
        <p>
          {state.asOf
            ? `Last snapshot ${state.asOf.replace('T', ' ').slice(0, 16)} UTC.`
            : 'No snapshots yet.'}{' '}
          Market data from Polymarket. Every figure is a recorded observation,
          committed to a public repository with its timestamp.
        </p>
        <p className="caveat">
          Volume is shown on every race because a price with little money
          behind it should not be read like one with a lot. Nothing here is a
          forecast of our own.
        </p>
      </footer>
    </>
  )
}
