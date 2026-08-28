// CSV parsing and shaping. Kept separate from the components so it can be
// reasoned about (and eventually tested) without rendering anything.

// Volume below this is treated as thin enough to flag. Polymarket state
// races frequently sit in the low six figures, which one trader can move.
// This is a display flag, not a filter: the row still shows, it just carries
// a warning so readers weight it themselves.
export const THIN_VOLUME = 250000

export const RACE_LABELS = {
  '2026-senate-control': 'Senate control',
  '2026-house-control': 'House control',
  '2026-senate-GA': 'Georgia',
  '2026-senate-MI': 'Michigan',
  '2026-senate-NC': 'North Carolina',
  '2026-senate-ME': 'Maine',
  '2026-senate-OH': 'Ohio',
  '2026-senate-TX': 'Texas',
  '2026-senate-IA': 'Iowa',
  '2026-senate-NH': 'New Hampshire',
  '2026-senate-MN': 'Minnesota',
  '2026-senate-AK': 'Alaska',
  '2026-senate-NE': 'Nebraska',
  '2026-senate-KS': 'Kansas',
}

// Minimal CSV reader. The collector writes plain values with no embedded
// commas except in the note column, which we do not display, so a full
// quoting parser would be more machinery than this needs.
export function parseCsv(text) {
  const lines = text.trim().split('\n')
  if (lines.length < 2) return []

  const headers = lines[0].split(',')
  return lines.slice(1).map((line) => {
    const cells = splitRow(line, headers.length)
    const row = {}
    headers.forEach((h, i) => {
      row[h] = cells[i] ?? ''
    })
    return row
  })
}

// Splits into exactly n fields, letting the final column absorb any extra
// commas. That keeps a comma inside an error message from shifting every
// column to its left.
function splitRow(line, n) {
  const parts = line.split(',')
  if (parts.length <= n) return parts
  return [...parts.slice(0, n - 1), parts.slice(n - 1).join(',')]
}

export function groupByRace(rows) {
  const byRace = new Map()

  for (const row of rows) {
    if (row.note) continue // failed fetches carry no price
    const prob = Number(row.prob)
    if (!Number.isFinite(prob)) continue

    if (!byRace.has(row.race_id)) {
      byRace.set(row.race_id, { race_id: row.race_id, market: [], poll: [] })
    }
    const race = byRace.get(row.race_id)
    const bucket = row.venue === 'poll' ? race.poll : race.market
    bucket.push({
      t: row.fetched_at,
      prob,
      volume: Number(row.volume) || 0,
      days_out: Number(row.days_out),
    })
  }

  return [...byRace.values()]
    .map((race) => {
      const market = race.market.sort((a, b) => (a.t < b.t ? -1 : 1))
      const poll = race.poll.sort((a, b) => (a.t < b.t ? -1 : 1))
      const latest = market[market.length - 1]
      const latestPoll = poll[poll.length - 1]
      const volume = latest?.volume ?? 0

      return {
        race_id: race.race_id,
        market: latest?.prob ?? null,
        poll: latestPoll?.prob ?? null,
        series: market,
        points: market.length,
        volume,
        thin: volume > 0 && volume < THIN_VOLUME,
        days_out: latest?.days_out ?? null,
      }
    })
    .filter((r) => r.market !== null)
    // Most contested first. A race sitting at 0.97 tells a reader far less
    // than one at 0.52, so uncertainty earns the top of the page.
    .sort((a, b) => Math.abs(0.5 - a.market) - Math.abs(0.5 - b.market))
}
