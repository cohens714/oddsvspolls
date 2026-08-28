// Price and polling over time for one race. Hand-rolled SVG rather than a
// charting library: three lines with no axes needs no dependency, and the
// result stays legible at the width of a list row.
//
// The x axis is days until the election, running left to right toward
// election day, so "as the election gets closer" reads the way it is said.
// The y axis is fixed at 0 to 100 rather than scaled to the data, because a
// race that has sat between 88% and 94% all year should look flat, not
// dramatic. Auto-scaling would manufacture drama in exactly the races where
// nothing is happening.

const W = 560
const H = 90
const PAD = 4

function path(points, maxDays) {
  if (!points.length) return ''
  return points
    .map((p, i) => {
      const x = PAD + (1 - p.days_out / maxDays) * (W - PAD * 2)
      const y = PAD + (1 - p.prob) * (H - PAD * 2)
      return `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}

export default function History({ series, label }) {
  const all = [...(series.polymarket || []), ...(series.kalshi || []),
               ...(series.poll || [])]
  if (all.length < 3) return null

  const maxDays = Math.max(...all.map((p) => p.days_out), 30)
  const half = PAD + (1 - 0.5) * (H - PAD * 2)

  return (
    <figure className="history">
      <svg viewBox={`0 0 ${W} ${H}`} role="img"
           aria-label={`Probability over time for ${label}`}
           preserveAspectRatio="none">
        {/* 50% reference. Every reading on this site is a distance from
            a coin flip, so the line is the one gridline worth drawing. */}
        <line x1={PAD} x2={W - PAD} y1={half} y2={half} className="h-mid" />

        {series.poll?.length > 1 && (
          <path d={path(series.poll, maxDays)} className="h-line h-poll" />
        )}
        {series.kalshi?.length > 1 && (
          <path d={path(series.kalshi, maxDays)} className="h-line h-kalshi" />
        )}
        {series.polymarket?.length > 1 && (
          <path d={path(series.polymarket, maxDays)}
                className="h-line h-market" />
        )}
      </svg>
      <figcaption>
        <span>{maxDays > 400 ? 'over a year out' : `${maxDays} days out`}</span>
        <span>election day</span>
      </figcaption>
    </figure>
  )
}
