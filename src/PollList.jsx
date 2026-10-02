// The polls behind a race's polling average, collapsed under the race card.
//
// Reads data/poll_inputs.json, which analysis/poll_inputs.py builds with the
// same window and weights as the live average and checks against the
// published figure. Nothing here recomputes the average: if the listed
// polls ever failed to reproduce the number on the card, the race is left
// out of that file and this component renders nothing.

const PARTISAN = { DEM: 'D-sponsored', REP: 'R-sponsored' }

function shortDate(iso) {
  if (!iso) return ''
  const d = new Date(`${iso}T00:00:00Z`)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

function dates(p) {
  return p.start_date && p.start_date !== p.end_date
    ? `${shortDate(p.start_date)} to ${shortDate(p.end_date)}`
    : shortDate(p.end_date)
}

function pct(v) {
  return `${Math.round(v * 10) / 10}%`
}

function marginLabel(m) {
  return `${m >= 0 ? 'D' : 'R'}+${Math.abs(m).toFixed(1)}`
}

export default function PollList({ inputs, race, windowDays, halfLifeDays }) {
  const polls = inputs?.polls
  if (!polls || polls.length === 0) return null
  // Belt and braces: the card's poll count comes from a different file.
  if (race.n_polls !== null && race.n_polls !== undefined
      && polls.length !== race.n_polls) return null

  const dem = race.demShort || 'Dem'
  const rep = race.repShort || 'Rep'
  const n = polls.length

  return (
    <details className="poll-list">
      <summary>Show the {n} poll{n === 1 ? '' : 's'} in this average</summary>
      <div className="poll-table-wrap">
        <table className="poll-table">
          <thead>
            <tr>
              <th>Pollster</th>
              <th>Field dates</th>
              <th className="pt-n">Sample</th>
              <th className="num">{dem}</th>
              <th className="num">{rep}</th>
              <th className="num">Margin</th>
              <th className="num">Weight</th>
            </tr>
          </thead>
          <tbody>
            {polls.map((p, i) => (
              <tr key={`${p.pollster}-${p.end_date}-${i}`}>
                <td>
                  {p.url
                    ? <a href={p.url} target="_blank" rel="noopener noreferrer">{p.pollster}</a>
                    : p.pollster}
                  {p.partisan && (
                    <span className="flag pt-flag">{PARTISAN[p.partisan] || 'partisan'}</span>
                  )}
                  {p.sponsors && <span className="pt-sponsor">for {p.sponsors}</span>}
                </td>
                <td className="pt-date">{dates(p)}</td>
                <td className="pt-n">
                  {p.sample_size
                    ? `${p.sample_size.toLocaleString()}${p.population ? ` ${p.population.toUpperCase()}` : ''}`
                    : 'n/a'}
                </td>
                <td className="num">{pct(p.dem_pct)}</td>
                <td className="num">{pct(p.rep_pct)}</td>
                <td className="num">{marginLabel(p.margin)}</td>
                <td className="num">{Math.round(p.weight_share * 100)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="poll-note">
        Average {marginLabel(inputs.margin)} as of {shortDate(inputs.as_of)}.
        Polls that finished fieldwork in the previous {windowDays ?? 45} days,
        weighted by sample size and by recency, with weight halving every{' '}
        {halfLifeDays ?? 14} days. Partisan polls are included at full weight
        and flagged. Poll data from VoteHub (CC BY 4.0), with some polls from
        Wikipedia.
      </p>
    </details>
  )
}
