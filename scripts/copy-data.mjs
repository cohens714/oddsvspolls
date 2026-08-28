// Copies committed CSVs into public/ so Vite serves them as static assets.
// Runs before every build via the prebuild script in package.json.
//
// data/ stays the single source of truth. Copying rather than symlinking
// keeps the build working on Cloudflare, which checks the repo out fresh.

import { copyFileSync, existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const destDir = resolve(root, 'public')
mkdirSync(destDir, { recursive: true })

// [source file, header to write if it is missing]
const FILES = [
  ['data/snapshots.csv',
   'fetched_at,snapshot_date,race_id,cycle,venue,yes_side,prob,days_out,raw_price,inverted,volume,liquidity,spread,note'],
  ['data/poll_probabilities.csv',
   'computed_at,race_id,as_of_date,days_out,margin,prob,sigma,sigma_systematic,sigma_sampling,n_polls,effective_n,n_partisan,partisan_lean,excluded_partisan,sigma_final_assumed'],
]

for (const [rel, header] of FILES) {
  const src = resolve(root, rel)
  const dest = resolve(destDir, rel.split('/').pop())
  if (existsSync(src)) {
    copyFileSync(src, dest)
    console.log(`copy-data: ${rel} copied`)
  } else {
    // Never fail the build over missing data. A header-only file lets the
    // frontend render its empty state instead of erroring.
    writeFileSync(dest, header + '\n')
    console.log(`copy-data: ${rel} missing, wrote header only`)
  }
}
