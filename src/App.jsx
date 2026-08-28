import { useEffect, useState } from 'react'
import GapBar from './GapBar.jsx'
import { parseCsv, combine } from './data.js'

async function loadCsv(path) {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`${path} returned ${res.status}`)
  return parseCsv(await res.text())
}

async function loadJson(path) {
  const res = await fetch(path)
  if (!res.ok) return {}
  try { return await res.json() } catch { return {} }
}

// Who a source is calling, and with what confidence. Both stored
// probabilities are P(Democrat wins), so a figure below 50% is a call for
// the Republican and has to be flipped for display.
function call(prob, race) {
  if (prob === null || prob === undefined) return null
  const dem = prob >= 0.5
  const name = dem
    ? race.demShort || (race.kind === 'control' ? 'Dem' : 'Democrat')
    : race.repShort || (race.kind === 'control' ? 'Rep' : 'Republican')
  return { name, party: dem ? 'D' : 'R', pct: (dem ? prob : 1 - prob) * 100 }
}

export default function App() {
  const [state, setState] = useState({ status: 'loading', races: [] })

  useEffect(() => {
    Promise.all([
      loadCsv('/snapshots.csv'),
      loadCsv('/poll_probabilities.csv'),
      loadJson('/race_meta.json'),
    ])
      .then(([marketRows, pollRows, meta]) => {
        const races = combine(marketRows, pollRows, meta)
        const asOf = marketRows.reduce(
          (l, r) => (r.fetched_at > l ? r.fetched_at : l), '')
        setState({ status: races.length ? 'ready' : 'empty', races, asOf })
      })
      .catch((err) => setState({ status: 'error', races: [], error: err.message }))
  }, [])

  const withPolls = state.races.filter((r) => r.poll !== null)
  const splits = state.races.filter((r) => r.splitCall)

  return (
    <>
      <header>
        <p className="eyebrow">oddsvspolls.com</p>
        <h1>Where the markets and the polls disagree</h1>
        <p className="lede">
          Prediction market prices for the 2026 Senate races, next to what the
          polling implies. Updated daily, sorted by disagreement.
          {splits.length > 0 && (
            <> Right now the two sources name{' '}
              <strong>different winners in {splits.length}{' '}
              {splits.length === 1 ? 'race' : 'races'}</strong>.
            </>
          )}
        </p>
      </header>

      <main>
        {state.status === 'loading' && <p className="note">Loading…</p>}
        {state.status === 'error' && (
          <p className="note">Could not load data ({state.error}).</p>
        )}
        {state.status === 'empty' && <p className="note">No data yet.</p>}

        {state.status === 'ready' && (
          <>
            <div className="legend">
              <span className="key"><i className="swatch swatch-market" /> market</span>
              <span className="key"><i className="swatch swatch-poll" /> polls</span>
              <span className="key key-muted">bar shows chance the Democrat wins</span>
            </div>

            <ol className="races">
              {state.races.map((race) => {
                const m = call(race.market, race)
                const p = call(race.poll, race)
                return (
                  <li key={race.race_id}
                      className={race.splitCall ? 'race race-split' : 'race'}>
                    <div className="race-head">
                      <div className="race-title">
                        <span className="race-name">
                          {race.label || race.race_id}
                        </span>
                        {race.demShort && race.repShort && (
                          <span className="matchup">
                            {race.demShort} (D) vs {race.repShort} (R)
                          </span>
                        )}
                      </div>
                      <div className="calls">
                        <span className="call">
                          <span className="call-name">{m.name}</span>
                          <span className="call-pct call-market">
                            {m.pct.toFixed(0)}<span className="pct">%</span>
                          </span>
                        </span>
                        <span className="call">
                          {p ? (
                            <>
                              <span className="call-name">{p.name}</span>
                              <span className="call-pct call-poll">
                                {p.pct.toFixed(0)}<span className="pct">%</span>
                              </span>
                            </>
                          ) : (
                            <span className="call-none">no polls</span>
                          )}
                        </span>
                      </div>
                    </div>

                    {race.splitCall && (
                      <p className="split-note">
                        Market favours {m.name}; polling favours {p.name}.
                      </p>
                    )}

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
                )
              })}
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
          <strong>How the poll probability is calculated.</strong>{' '}
          Polls give a margin, not a probability. Converting one to the other
          requires an estimate of how wrong polls usually are. We treat the
          eventual error as normally distributed with a standard deviation of
          4.5 points on election day, widening at longer horizons and
          narrowing where more polls exist.
        </p>
        <p className="caveat">
          <strong>Where 4.5 comes from.</strong> We tested candidate values
          against 379 Senate races from 2000 to 2022, using{' '}
          <a href="https://github.com/fivethirtyeight/data/tree/master/pollster-ratings">
            538&rsquo;s pollster-ratings archive
          </a>, and chose the best calibrated rather than the best scoring.
          Two more obvious numbers are worse. The measured spread of polling
          error is 6.5 points, but that is inflated by a handful of large
          misses: used as the assumption it is too cautious, and its 70-80%
          forecasts won 97% of the time. A value of 3.2 scores best overall
          but is overconfident where it matters, winning only 62% of its
          70-80% forecasts. At 4.5 no confidence band is off by more than two
          points.
        </p>
        <p className="caveat">
          <strong>What this still gets wrong.</strong> Polling error is
          shared within an election year rather than independent across
          races. In 2020 the average Senate poll overstated Democrats by
          almost seven points, in every state at once. A single race
          probability cannot express that, so when these numbers are wrong
          they will tend to be wrong together and in the same direction.
          How much error grows at longer horizons is also still an
          assumption, because the archive we fitted to contains only polls
          from the final three weeks.
        </p>
        <p className="caveat">
          {withPolls.length} of {state.races.length} races have poll data.
          Volume and effective poll counts appear on every race because a
          price with little money behind it, or an average resting on a single
          survey, should not be read like one with more. Partisan-sponsored
          polls are included and flagged.
        </p>
      </footer>
    </>
  )
}
