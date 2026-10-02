const REPO_URL = 'https://github.com/cohens714/oddsvspolls'
const FREEZE_URL = 'https://github.com/cohens714/oddsvspolls/commit/46fe190'
const ARCHIVE_URL = 'https://github.com/fivethirtyeight/data'

export function MethodologyHeader() {
  return (
    <>
      <h1>How this works</h1>
      <p className="lede">
        Every step behind the numbers on this site, including the parts that
        are assumptions.
      </p>
    </>
  )
}

export default function Methodology() {
  return (
    <main className="methodology">
      <section>
        <h2>The idea</h2>
        <p>
          oddsvspolls compares two ways of forecasting elections: prediction
          markets, where people trade contracts that pay out based on who
          wins, and polls. Markets produce a probability directly. Polls
          produce a margin, so we convert that margin into a probability using
          a fixed, published method. Then we score both against what actually
          happens.
        </p>
      </section>

      <section>
        <h2>What we track</h2>
        <p>
          We cover the 2026 US Senate and governor races where both markets
          and polls exist. Market prices come from Polymarket and Kalshi and
          are collected hourly. Poll data comes from VoteHub (CC BY 4.0) and
          is collected daily. We record the date we first saw each poll, so a
          poll is never credited with information that wasn't public yet.
        </p>
        <p>
          Races that aren't two-candidate contests are excluded, because a
          two-party probability means nothing there. Nebraska's Senate race,
          where an independent holds a large share of the market, is the
          current example.
        </p>
      </section>

      <section>
        <h2>Turning polls into probabilities</h2>
        <p>
          For each race we build a polling average, then ask how likely the
          leading candidate is to win given how wrong polls typically are. The
          probability is &Phi;(margin &divide; &sigma;), where &Phi; is the
          standard normal distribution and &sigma; is the expected size of the
          polling error. &sigma; has two parts.
        </p>
        <p>
          The first is systematic error, the kind that more polling doesn't
          fix. On election day we set it at 4.5 points, fitted against 379
          Senate races from 2000 to 2022 in{' '}
          <a href={ARCHIVE_URL}>FiveThirtyEight's pollster-ratings archive</a>.
          We tested alternatives. A value of 3.2 points scored best on one
          metric but was overconfident: its 70 to 80% forecasts won only 62%
          of the time. A value of 6.5 points, the raw measured spread, was
          underconfident: those same forecasts won 97% of the time. 4.5 was
          the best calibrated.
        </p>
        <p>
          Systematic error also grows the further out you are, so we assume
          its variance doubles every 120 days. That number is an assumption,
          not a measurement, because the historical archive only covers polls
          from the final three weeks of each race. We show how the results
          change under different values.
        </p>
        <p>
          The second part is sampling error, which shrinks as more polls come
          in. A race with one poll gets more uncertainty than a race with
          nine.
        </p>
        <p>
          Both values were <a href={FREEZE_URL}>frozen on October 2, 2026</a>,
          a month before the election, and will not change until every 2026
          race has resolved. Governor races use the same values as Senate
          races, and we report results for each separately.
        </p>
      </section>

      <section>
        <h2>How we score</h2>
        <p>
          Each source's final forecast is its last reading before 6 p.m.
          Eastern on November 3, when the first polls close, so election-night
          returns never leak into the comparison. A race resolves on the
          eventual winner of the seat as called by the Associated Press,
          including any runoff. Races not yet called are marked pending and
          left out of scores until they are.
        </p>
        <p>
          Our main measure is the Brier score, the average squared gap between
          the forecast and the outcome, where lower is better. We also report
          log score, which punishes confident misses more heavily, and AUC,
          which only asks whether a source ranked races in the right order.
          AUC matters because it barely depends on our polling assumptions. If
          polls win on AUC but lose on Brier, the polling information was good
          and our conversion was the weak link.
        </p>
        <p>
          We compare sources only on the same race on the same day, and we
          track accuracy by how far out each forecast was made, so you can see
          when each source is most reliable. Polymarket and Kalshi are scored
          separately against polls. Because races move together when there's
          a national polling miss, our uncertainty estimates treat each
          election year as a cluster rather than pretending every race is
          independent.
        </p>
      </section>

      <section>
        <h2>What one election can't tell you</h2>
        <p>
          A single cycle is a small sample, and a nationwide polling miss can
          swing the result in one direction. We treat 2026 as the first entry
          in a record that grows each cycle rather than a final verdict.
        </p>
        <p>
          All of the code and data behind this site is public on{' '}
          <a href={REPO_URL}>GitHub</a>.
        </p>
      </section>
    </main>
  )
}
