const BUILT = new Date().toISOString().slice(0, 10)

export default function App() {
  return (
    <main>
      <p className="eyebrow">oddsvspolls.com</p>
      <h1>
        Prediction markets and polling averages
        disagree. This site tracks by how much.
      </h1>
      <p className="lede">
        Daily snapshots of market-implied probabilities alongside
        poll-derived ones for the 2026 midterms, plus scoring on
        past races to show which source has actually been more
        accurate, and how far out.
      </p>
      <p className="status">Under construction. Build {BUILT}.</p>
    </main>
  )
}
