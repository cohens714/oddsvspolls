// The signature element. A 0-100% track with a marker for each source and
// the span between them filled in. When both sources are present the filled
// span IS the story: it is the disagreement the whole site exists to show.
//
// Deliberately not a chart. At a glance a reader should see two positions
// and the distance between them, without axes, gridlines or a legend to
// decode. Colour encodes the source, never the party, because the comparison
// being made here is between methods rather than between candidates.

export default function GapBar({ market, poll, gap: gapPts, splitCall,
                                demShort }) {
  const hasPoll = poll !== null && poll !== undefined
  const marketPct = market * 100
  const pollPct = hasPoll ? poll * 100 : null

  const lo = hasPoll ? Math.min(marketPct, pollPct) : null
  const hi = hasPoll ? Math.max(marketPct, pollPct) : null

  // Comes in pre-rounded so it matches the figures printed above the bar.
  const gap = hasPoll ? Math.abs(gapPts) : null

  return (
    <div className="gap">
      <div
        className="gap-track"
        role="img"
        aria-label={
          hasPoll
            ? `Market ${marketPct.toFixed(0)} percent, polls ${pollPct.toFixed(0)} percent, a gap of ${gap.toFixed(0)} points`
            : `Market ${marketPct.toFixed(0)} percent. No poll figure yet.`
        }
      >
        <i className="gap-mid" aria-hidden="true" />

        {hasPoll && (
          <i
            className="gap-span"
            style={{ left: `${lo}%`, width: `${hi - lo}%` }}
            aria-hidden="true"
          />
        )}

        <i
          className="gap-mark gap-mark-market"
          style={{ left: `${marketPct}%` }}
          aria-hidden="true"
        />

        {hasPoll && (
          <i
            className="gap-mark gap-mark-poll"
            style={{ left: `${pollPct}%` }}
            aria-hidden="true"
          />
        )}
      </div>

      <span className={hasPoll ? 'gap-value' : 'gap-value gap-value-empty'}>
        {!hasPoll && 'awaiting polls'}
        {/* On a split call the two figures above name different
            candidates, so no arithmetic on them yields this number.
            Naming the candidate the gap is measured on makes it
            checkable instead of looking like an error. */}
        {hasPoll && (splitCall && demShort
          ? <>{gap} pts apart<br /><span className="gap-basis">on {demShort}</span></>
          : `${gap} pt gap`)}
      </span>
    </div>
  )
}
