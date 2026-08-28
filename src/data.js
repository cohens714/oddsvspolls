// CSV parsing and shaping. Separate from the components so it can be
// reasoned about, and tested, without rendering anything.

// Volume below this is flagged as thin. Polymarket state races often sit in
// the low six figures, which one trader can move. A display flag, not a
// filter: the row still shows, it just carries a warning.
export const THIN_VOLUME = 250000

// A poll average worth less than this many equally-weighted polls is flagged.
// Several races currently sit at 1.0, meaning a single survey.
export const THIN_EFFECTIVE_N = 3

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

export function parseCsv(text) {
  // Split on any line ending. Python's csv module writes \r\n by default,
  // while files edited through a web UI use \n, so a single file can hold
  // both. Splitting on \n alone leaves a trailing \r on the last field of
  // every Python-written row, which lands in `note` and makes the row look
  // like a failed fetch.
  const lines = text.trim().split(/\r\n|\n|\r/).filter((l) => l.length)
  if (lines.length < 2) return []
  const headers = lines[0].split(',').map((h) => h.trim())
  return lines.slice(1).map((line) => {
    const cells = splitRow(line, headers.length)
    const row = {}
    headers.forEach((h, i) => { row[h] = (cells[i] ?? '').trim() })
    return row
  })
}

// Split into exactly n fields, letting the last column absorb extra commas.
// Keeps a comma inside an error message from shifting every column left.
function splitRow(line, n) {
  const parts = line.split(',')
  if (parts.length <= n) return parts
  return [...parts.slice(0, n - 1), parts.slice(n - 1).join(',')]
}

function num(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

// Latest row per race from the market snapshots.
function latestMarkets(rows) {
  const byRace = new Map()
  for (const row of rows) {
    if (row.note) continue        // failed fetches carry no price
    const prob = num(row.prob)
    if (prob === null) continue
    const prev = byRace.get(row.race_id)
    if (!prev || row.fetched_at > prev.fetched_at) {
      byRace.set(row.race_id, {
        fetched_at: row.fetched_at,
        prob,
        volume: num(row.volume) ?? 0,
        days_out: num(row.days_out),
      })
    }
  }
  return byRace
}

// Latest row per race from the converted poll probabilities.
function latestPolls(rows) {
  const byRace = new Map()
  for (const row of rows) {
    const prob = num(row.prob)
    if (prob === null) continue
    const prev = byRace.get(row.race_id)
    if (!prev || row.computed_at > prev.computed_at) {
      byRace.set(row.race_id, {
        computed_at: row.computed_at,
        prob,
        margin: num(row.margin),
        sigma: num(row.sigma),
        n_polls: num(row.n_polls),
        effective_n: num(row.effective_n),
        n_partisan: num(row.n_partisan) ?? 0,
      })
    }
  }
  return byRace
}

export function combine(marketRows, pollRows) {
  const markets = latestMarkets(marketRows)
  const polls = latestPolls(pollRows)

  const ids = new Set([...markets.keys(), ...polls.keys()])
  const out = []

  for (const race_id of ids) {
    const m = markets.get(race_id)
    const p = polls.get(race_id)
    if (!m) continue              // no market price means nothing to compare

    const volume = m.volume ?? 0
    out.push({
      race_id,
      market: m.prob,
      poll: p ? p.prob : null,
      gap: p ? (m.prob - p.prob) * 100 : null,
      margin: p ? p.margin : null,
      sigma: p ? p.sigma : null,
      n_polls: p ? p.n_polls : null,
      effective_n: p ? p.effective_n : null,
      n_partisan: p ? p.n_partisan : 0,
      days_out: m.days_out,
      volume,
      thinMarket: volume > 0 && volume < THIN_VOLUME,
      thinPolls: p ? p.effective_n < THIN_EFFECTIVE_N : false,
    })
  }

  // Biggest disagreement first. The gap is the story; a race where both
  // sources agree is the least interesting row on the page. Races with no
  // poll data sort last rather than being dropped.
  return out.sort((a, b) => {
    if (a.gap === null && b.gap === null) {
      return Math.abs(0.5 - a.market) - Math.abs(0.5 - b.market)
    }
    if (a.gap === null) return 1
    if (b.gap === null) return -1
    return Math.abs(b.gap) - Math.abs(a.gap)
  })
}
