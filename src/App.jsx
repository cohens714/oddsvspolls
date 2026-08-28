import { useEffect, useState } from 'react'
import GapBar from './GapBar.jsx'
import { parseCsv, combine, RACE_LABELS } from './data.js'

async function loadCsv(path) {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`${path} returned ${res.status}`)
  return parseCsv(await res.text())
}

export default function App() {
  const [state, setState] = useState({ status: 'loading', races: [] })

  useEffect(() => {
    Promise.all([loadCsv('/snapshots.csv'), loadCsv('/poll_probabilities.csv')])
      .then(([marketRows, pollRows]) => {
        const races = combine(marketRows, pollRows)
        const asOf = marketRows.reduce(
          (l, r) => (r.fetched_at > l ? r.fetched_at : l), '')
        setState({ status: races.length ? 'ready' : 'empty', races, asOf })
      })
      .catch((err) => setState({ status: 'error', races: [], error: err.message }))
  }, [])

  const withPolls = state.races.filter((r) => r.poll !== null)

  return (
    <>
      <header>
        <p className="eyebrow">oddsvspolls.com</p>
        <h1>Where the markets and the polls disagree</h1>
        <p className="lede">
          Prediction market prices for the 2026 Senate races, next to what the
          polling implies, updated daily. Sorted by the size of the
          disagreement.
        </p>
      </header>

      <main>
        {state.status === 'loading' && <p className="note">Loading…</p>}

        {state.status === 'error' && (
          <p className="note">Could not load data ({state.error}).</p>
        )}

        {state.status === 'empty' && (
          <p className="note">No data recorded yet.</p>
        )}

        {state.status === 'ready' && (
          <>
            <div className="legend">
              <span className="key"><i className="swatch swatch-market" /> market</span>
              <span className="key"><i className="swatch swatch-poll" /> polls</span>
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
                    <span className="race-figures">
                      <span className="figure figure-market">
                        {(race.market * 100).toFixed(0)}<span className="pct">%</span>
                      </span>
                      <span className="figure figure-poll">
                        {race.poll !== null
                          ? <>{(race.poll * 100).toFixed(0)}<span className="pct">%</span></>
                          : <span className="figure-none">—</span>}
                      </span>
                    </span>
                  </div>

                  <GapBar market={race.market} poll={race.poll} />

                  <div className="race-meta">
                    {race.margin !== null && (
                      <span>
                        polls {race.margin >= 0 ? 'D' : 'R'}+
                        {Math.abs(race.margin).toFixed(1)}
                      </span>
                    )}
                    {race.n_polls !== null && (
                      <span>
                        {race.n_polls} poll{race.n_polls === 1 ? '' : 's'}
                        {race.effective_n !== null &&
                          ` (eff ${race.effective_n.toFixed(1)})`}
                      </span>
                    )}
                    <span>
                      {race.volume
                        ? `$${Math.round(race.volume).toLocaleString()} volume`
                        : 'volume unreported'}
                    </span>
                    {race.thinPolls && <span className="flag">thin polling</span>}
                    {race.thinMarket && <span className="flag">thin market</span>}
                    {race.n_partisan > 0 && (
                      <span className="flag">
                        {race.n_partisan} partisan poll
                        {race.n_partisan === 1 ? '' : 's'}
                      </span>
                    )}
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
            ? `Market data last fetched ${state.asOf.replace('T', ' ').slice(0, 16)} UTC.`
            : ''}{' '}
          Market prices from Polymarket. Poll data from{' '}
          <a href="https://votehub.com">VoteHub</a>, used under CC BY 4.0.
          Every figure is a recorded observation, committed to a{' '}
          <a href="https://github.com/cohens714/oddsvspolls">public repository</a>{' '}
          with its timestamp.
        </p>
        <p className="caveat">
          <strong>How the poll probability is calculated, and why to doubt it.</strong>{' '}
          Polls give a margin, not a probability. Converting one to the other
          means assuming how wrong polls usually are. We assume the eventual
          error is normally distributed with a standard deviation of 6 points
          on election day, widening the further out we are, and narrowing
          slightly where more polls exist. That figure is taken from published
          estimates rather than measured from our own data, so treat these
          numbers as provisional. A larger assumed error would push every poll
          probability toward 50%, and a smaller one would push them all
          outward.
        </p>
        <p className="caveat">
          {withPolls.length} of {state.races.length} races have poll data.
          Volume and effective poll counts are shown on every race because a
          price with little money behind it, or an average resting on one
          survey, should not be read like one with more. Partisan-sponsored
          polls are included in the average and flagged.
        </p>
      </footer>
    </>
  )
}
