import { useCallback, useMemo, useRef, useState } from 'react'

// Probability over time for one race. Hand-rolled SVG rather than a charting
// library: three lines and a crosshair needs no dependency, and the result
// stays legible at the width of a list row.
//
// x is days until the election, running left to right toward election day,
// so "as the election gets closer" reads the way it is said.
//
// y is fixed at 0 to 100 rather than scaled to the data. A race that has sat
// between 88% and 94% all year should look flat. Auto-scaling would
// manufacture drama in exactly the races where nothing is happening, which
// is how a chart lies without containing a single wrong number.

const W = 560
const H = 90
const PAD = 4
const ELECTION = new Date('2026-11-03T00:00:00Z')

const SERIES = [
  { key: 'polymarket', label: 'Polymarket', cls: 'market' },
  { key: 'kalshi', label: 'Kalshi', cls: 'kalshi' },
  { key: 'poll', label: 'Polls', cls: 'poll' },
]

function toX(days, maxDays) {
  return PAD + (1 - days / maxDays) * (W - PAD * 2)
}

function path(points, maxDays) {
  if (!points?.length) return ''
  return points
    .map((p, i) => {
      const y = PAD + (1 - p.prob) * (H - PAD * 2)
      return `${i ? 'L' : 'M'}${toX(p.days_out, maxDays).toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}

// Nearest point by horizontal distance. Never interpolates: an interpolated
// reading would show a price that was never quoted, which on a chart people
// may screenshot is worse than showing a slightly older real one.
function nearest(points, days) {
  if (!points?.length) return null
  let best = points[0]
  let bestGap = Math.abs(points[0].days_out - days)
  for (const p of points) {
    const gap = Math.abs(p.days_out - days)
    if (gap < bestGap) {
      best = p
      bestGap = gap
    }
  }
  return { ...best, gap: bestGap }
}

function dateFor(days) {
  const d = new Date(ELECTION.getTime() - days * 86400000)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric',
                                           year: 'numeric', timeZone: 'UTC' })
}

export default function History({ series, label, demShort, repShort }) {
  const wrap = useRef(null)
  const [hover, setHover] = useState(null)

  const all = useMemo(
    () => SERIES.flatMap((s) => series[s.key] || []),
    [series],
  )
  const maxDays = useMemo(
    () => Math.max(...all.map((p) => p.days_out), 30),
    [all],
  )

  const onMove = useCallback((event) => {
    const box = wrap.current?.getBoundingClientRect()
    if (!box || !box.width) return
    const point = event.touches?.[0] || event
    const frac = Math.min(Math.max((point.clientX - box.left) / box.width, 0), 1)

    // The svg uses preserveAspectRatio="none", so the viewBox maps linearly
    // onto the element box and a simple proportion is exact.
    const span = (W - PAD * 2) / W
    const adjusted = Math.min(Math.max((frac - PAD / W) / span, 0), 1)
    setHover(maxDays * (1 - adjusted))
  }, [maxDays])

  const clear = useCallback(() => setHover(null), [])

  if (all.length < 3) return null

  const readings = hover === null ? [] : SERIES
    .map((s) => ({ ...s, point: nearest(series[s.key], hover) }))
    .filter((s) => s.point)

  // Anchor the crosshair to the closest actual observation rather than the
  // raw cursor position, so the line sits on data instead of between it.
  const anchor = readings.length
    ? readings.reduce((a, b) => (a.point.gap <= b.point.gap ? a : b)).point
    : null
  const anchorX = anchor ? toX(anchor.days_out, maxDays) : 0
  const flip = anchorX > W * 0.62

  return (
    <figure className="history">
      <div
        className="history-plot"
        ref={wrap}
        onMouseMove={onMove}
        onMouseLeave={clear}
        onTouchStart={onMove}
        onTouchMove={onMove}
        onTouchEnd={clear}
      >
        <svg viewBox={`0 0 ${W} ${H}`} role="img"
             aria-label={`Probability over time for ${label}`}
             preserveAspectRatio="none">
          {/* 50% reference. Every reading here is a distance from a coin
              flip, so it is the one gridline worth drawing. */}
          <line x1={PAD} x2={W - PAD}
                y1={PAD + 0.5 * (H - PAD * 2)} y2={PAD + 0.5 * (H - PAD * 2)}
                className="h-mid" />

          {anchor && (
            <line x1={anchorX} x2={anchorX} y1={PAD} y2={H - PAD}
                  className="h-cross" />
          )}

          {SERIES.map((s) =>
            (series[s.key]?.length > 1) && (
              <path key={s.key} d={path(series[s.key], maxDays)}
                    className={`h-line h-${s.cls}`} />
            ),
          )}
        </svg>

        {/* Markers are HTML rather than SVG: the non-uniform aspect ratio
            would squash an SVG circle into an ellipse. */}
        {readings.map((s) => (
          <i key={s.key}
             className={`h-dot h-dot-${s.cls}`}
             style={{
               left: `${(toX(s.point.days_out, maxDays) / W) * 100}%`,
               top: `${((PAD + (1 - s.point.prob) * (H - PAD * 2)) / H) * 100}%`,
             }} />
        ))}

        {anchor && (
          <div className={flip ? 'h-tip h-tip-left' : 'h-tip'}
               style={{ left: `${(anchorX / W) * 100}%` }}>
            <div className="h-tip-date">
              {dateFor(anchor.days_out)}
              <span className="h-tip-days">{anchor.days_out}d out</span>
            </div>
            {readings.map((s) => (
              <div key={s.key} className="h-tip-row">
                <i className={`swatch swatch-${s.cls}`} />
                <span className="h-tip-label">{s.label}</span>
                <span className="h-tip-value">
                  {(s.point.prob * 100).toFixed(0)}%
                </span>
              </div>
            ))}
            {demShort && (
              <div className="h-tip-foot">chance {demShort} wins</div>
            )}
          </div>
        )}
      </div>

      <figcaption>
        <span>{maxDays > 400 ? 'over a year out' : `${maxDays} days out`}</span>
        <span>election day</span>
      </figcaption>
    </figure>
  )
}
