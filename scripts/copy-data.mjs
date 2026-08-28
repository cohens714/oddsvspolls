// Copies the committed snapshot CSV into public/ so Vite serves it as a
// static asset. Runs automatically before every build via the prebuild
// script in package.json.
//
// The CSV stays the single source of truth in data/. Copying rather than
// symlinking keeps the build working on Cloudflare, which checks out the
// repo fresh each time.

import { copyFileSync, existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const src = resolve(root, 'data/snapshots.csv')
const destDir = resolve(root, 'public')
const dest = resolve(destDir, 'snapshots.csv')

mkdirSync(destDir, { recursive: true })

if (existsSync(src)) {
  copyFileSync(src, dest)
  console.log('copy-data: snapshots.csv copied to public/')
} else {
  // Never fail the build over missing data. An empty file with a header
  // lets the frontend render its "no data yet" state instead of erroring.
  writeFileSync(dest, 'fetched_at,snapshot_date,race_id,cycle,venue,yes_side,prob,days_out,raw_price,inverted,volume,liquidity,spread,note\n')
  console.log('copy-data: no data/snapshots.csv, wrote header-only file')
}
