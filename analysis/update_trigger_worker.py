#!/usr/bin/env python3
"""update_trigger_worker.py: have the Cloudflare Worker start the daily poll update too.

GitHub's scheduler has been starting "Update polls" 5 to 6 hours late every
day, and on Nov 2 not at all. This redeploys the oddsvspolls-trigger Worker so
its existing :13 race-call trigger also starts polls.yml at 12:13 and 18:13 UTC
(6:13 a.m. and 12:13 p.m. Central). No new cron slot is used, and the GitHub
token already stored in the Worker is reused.

Run from anywhere inside the repo:
    python3 analysis/update_trigger_worker.py
"""
import os
import subprocess
import sys

DIR = os.path.join("cloudflare", "trigger")
INDEX = os.path.join(DIR, "src", "index.js")
NEW_INDEX = '// Starts the oddsvspolls GitHub workflows on a reliable clock.\n//\n// GitHub\'s own cron starts only a fraction of scheduled runs (snapshots were\n// landing 3 to 7 hours apart). Cloudflare cron triggers fire on time, so this\n// Worker calls GitHub\'s workflow_dispatch API on the schedule we actually\n// want. The GitHub schedules stay in place as a backup; an extra run is\n// harmless because every row records its own fetched_at.\n//\n// Secret: GITHUB_TOKEN, a fine-grained token limited to cohens714/oddsvspolls\n// with Actions: read and write. Set with  npx wrangler secret put GITHUB_TOKEN\n\nconst REPO = \'cohens714/oddsvspolls\'\n\n// Cloudflare cron expression (UTC) -> workflow files it starts.\n// Must match the crons in wrangler.toml exactly.\nexport const SCHEDULE = {\n  \'7,27,47 * * * *\': [\'snapshot.yml\'],\n  \'13,43 * * * *\': [\'calls.yml\'],\n  // Election day, 8 a.m. to 6:59 p.m. Eastern: every 10 minutes so the last\n  // reading before the 6 p.m. cutoff is fresh.\n  \'3,13,23,33,43,53 13-23 3 11 *\': [\'snapshot.yml\'],\n}\n\n// Workflows that only need to run a few times a day ride on an existing\n// cron instead of using another of the free plan\'s 5 trigger slots.\n// [cron, UTC hours, minute] -> extra workflows started at those times.\nexport const PIGGYBACK = [\n  // Polls: GitHub was starting the daily run 5 to 6 hours late and on\n  // Nov 2 not at all. 12:13 and 18:13 UTC; rerunning is idempotent.\n  [\'13,43 * * * *\', [12, 18], 13, [\'polls.yml\']],\n]\n\nexport function workflowsFor(cron, scheduledTime) {\n  const t = new Date(scheduledTime ?? Date.now())\n  const extra = PIGGYBACK\n    .filter(([c, hours, minute]) => c === cron\n      && hours.includes(t.getUTCHours()) && t.getUTCMinutes() === minute)\n    .flatMap(([, , , w]) => w)\n  return [...(SCHEDULE[cron] ?? []), ...extra]\n}\n\nexport async function dispatch(workflow, token, fetchImpl = fetch) {\n  const res = await fetchImpl(\n    `https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`,\n    {\n      method: \'POST\',\n      headers: {\n        Authorization: `Bearer ${token}`,\n        Accept: \'application/vnd.github+json\',\n        \'X-GitHub-Api-Version\': \'2022-11-28\',\n        \'User-Agent\': \'oddsvspolls-trigger\',\n        \'Content-Type\': \'application/json\',\n      },\n      body: JSON.stringify({ ref: \'main\' }),\n    },\n  )\n  if (res.status !== 204) {\n    const text = await res.text()\n    throw new Error(`${workflow}: GitHub answered ${res.status} ${text.slice(0, 200)}`)\n  }\n  return workflow\n}\n\nexport default {\n  async scheduled(event, env, ctx) {\n    const workflows = workflowsFor(event.cron, event.scheduledTime)\n    if (!workflows.length) {\n      console.log(`no workflows mapped to cron "${event.cron}"`)\n      return\n    }\n    if (!env.GITHUB_TOKEN) throw new Error(\'GITHUB_TOKEN secret is not set\')\n    const results = await Promise.allSettled(\n      workflows.map((w) => dispatch(w, env.GITHUB_TOKEN)))\n    for (const r of results) {\n      if (r.status === \'fulfilled\') console.log(`started ${r.value}`)\n      else console.error(String(r.reason))\n    }\n    if (results.some((r) => r.status === \'rejected\')) {\n      throw new Error(\'one or more dispatches failed\')   // shows as an error in the dashboard\n    }\n  },\n\n  // Visiting the Worker\'s URL shows what it does; it never starts anything.\n  async fetch() {\n    return new Response(\n      \'oddsvspolls trigger: starts GitHub workflows on a schedule.\\n\'\n      + Object.entries(SCHEDULE).map(([c, w]) => `  ${c}  ->  ${w.join(\', \')}`).join(\'\\n\') + \'\\n\'\n      + PIGGYBACK.map(([c, h, m, w]) => `  ${c} at ${h.map((x) => `${x}:${String(m).padStart(2, \'0\')}`).join(\', \')} UTC  ->  also ${w.join(\', \')}`).join(\'\\n\') + \'\\n\',\n      { headers: { \'content-type\': \'text/plain\' } })\n  },\n}\n'
WRANGLER = ["npx", "--yes", "wrangler@4"]


def run(cmd, **kw):
    print("$ " + " ".join(cmd))
    return subprocess.run(cmd, **kw)


def main():
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("Run this from inside the oddsvspolls repo.")
    os.chdir(r.stdout.strip())
    if not os.path.exists(INDEX):
        sys.exit(f"Can't find {INDEX}; was setup_trigger_worker.py run in this repo?")

    run(["git", "pull", "--rebase", "--autostash", "origin", "main"])
    open(INDEX, "w").write(NEW_INDEX)
    print(f"updated {INDEX}")

    if run(WRANGLER + ["deploy"], cwd=DIR).returncode != 0:
        subprocess.run(["git", "checkout", "--", INDEX])
        sys.exit("Deploy failed, so the file was put back. Paste the output above.")

    for cmd in (["git", "add", INDEX, os.path.join("analysis", "update_trigger_worker.py")],
                ["git", "commit", "-m", "Trigger worker: start the poll update twice a day"],
                ["git", "pull", "--rebase", "--autostash", "origin", "main"],
                ["git", "push", "origin", "main"]):
        if run(cmd).returncode != 0:
            print("git step failed; the Worker is deployed regardless. Paste the output.")
            break

    print("\nDone. Polls now start at 6:13 a.m. and 12:13 p.m. Central.\n"
          "Today's run was missed, so start one now: Actions tab -> Update polls -> Run workflow.")


if __name__ == "__main__":
    main()
