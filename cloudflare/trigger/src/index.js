// Starts the oddsvspolls GitHub workflows on a reliable clock.
//
// GitHub's own cron starts only a fraction of scheduled runs (snapshots were
// landing 3 to 7 hours apart). Cloudflare cron triggers fire on time, so this
// Worker calls GitHub's workflow_dispatch API on the schedule we actually
// want. The GitHub schedules stay in place as a backup; an extra run is
// harmless because every row records its own fetched_at.
//
// Secret: GITHUB_TOKEN, a fine-grained token limited to cohens714/oddsvspolls
// with Actions: read and write. Set with  npx wrangler secret put GITHUB_TOKEN

const REPO = 'cohens714/oddsvspolls'

// Cloudflare cron expression (UTC) -> workflow files it starts.
// Must match the crons in wrangler.toml exactly.
export const SCHEDULE = {
  '7,27,47 * * * *': ['snapshot.yml'],
  '13,43 * * * *': ['calls.yml'],
  // Election day, 8 a.m. to 6:59 p.m. Eastern: every 10 minutes so the last
  // reading before the 6 p.m. cutoff is fresh.
  '3,13,23,33,43,53 13-23 3 11 *': ['snapshot.yml'],
}

export async function dispatch(workflow, token, fetchImpl = fetch) {
  const res = await fetchImpl(
    `https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'oddsvspolls-trigger',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ref: 'main' }),
    },
  )
  if (res.status !== 204) {
    const text = await res.text()
    throw new Error(`${workflow}: GitHub answered ${res.status} ${text.slice(0, 200)}`)
  }
  return workflow
}

export default {
  async scheduled(event, env, ctx) {
    const workflows = SCHEDULE[event.cron] ?? []
    if (!workflows.length) {
      console.log(`no workflows mapped to cron "${event.cron}"`)
      return
    }
    if (!env.GITHUB_TOKEN) throw new Error('GITHUB_TOKEN secret is not set')
    const results = await Promise.allSettled(
      workflows.map((w) => dispatch(w, env.GITHUB_TOKEN)))
    for (const r of results) {
      if (r.status === 'fulfilled') console.log(`started ${r.value}`)
      else console.error(String(r.reason))
    }
    if (results.some((r) => r.status === 'rejected')) {
      throw new Error('one or more dispatches failed')   // shows as an error in the dashboard
    }
  },

  // Visiting the Worker's URL shows what it does; it never starts anything.
  async fetch() {
    return new Response(
      'oddsvspolls trigger: starts GitHub workflows on a schedule.\n'
      + Object.entries(SCHEDULE).map(([c, w]) => `  ${c}  ->  ${w.join(', ')}`).join('\n') + '\n',
      { headers: { 'content-type': 'text/plain' } })
  },
}
