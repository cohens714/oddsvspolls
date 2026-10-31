#!/usr/bin/env python3
"""setup_trigger_worker.py: start the GitHub workflows from Cloudflare, on time.

GitHub's scheduler is starting only a fraction of the snapshot runs (they've
been landing 3 to 7 hours apart, with no failures). This deploys a small
Cloudflare Worker, oddsvspolls-trigger, whose cron triggers call GitHub's
"run workflow" API on the schedule we want:

    snapshot.yml   at :07, :27, :47 every hour
    calls.yml      at :13, :43 every hour
    snapshot.yml   every 10 minutes on Nov 3, 8 a.m. to 6:59 p.m. Eastern

The GitHub schedules stay as a backup. It uses 3 of the 5 cron triggers a
free Cloudflare account allows.

Run from anywhere inside the repo:
    python3 analysis/setup_trigger_worker.py

It walks you through: Cloudflare login (browser), deploying the Worker,
pasting a GitHub token (checked by starting one real snapshot run), storing
it as a Worker secret, and committing the Worker's files.
"""
import getpass
import json
import os
import shutil
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO = "cohens714/oddsvspolls"
WORKER_NAME = "oddsvspolls-trigger"
DIR = os.path.join("cloudflare", "trigger")
FILES = {
    os.path.join(DIR, "wrangler.toml"): 'name = "oddsvspolls-trigger"\nmain = "src/index.js"\ncompatibility_date = "2026-10-01"\n\n# UTC. Keep in sync with SCHEDULE in src/index.js.\n[triggers]\ncrons = [\n  "7,27,47 * * * *",\n  "13,43 * * * *",\n  "3,13,23,33,43,53 13-23 3 11 *",\n]\n',
    os.path.join(DIR, "src", "index.js"): '// Starts the oddsvspolls GitHub workflows on a reliable clock.\n//\n// GitHub\'s own cron starts only a fraction of scheduled runs (snapshots were\n// landing 3 to 7 hours apart). Cloudflare cron triggers fire on time, so this\n// Worker calls GitHub\'s workflow_dispatch API on the schedule we actually\n// want. The GitHub schedules stay in place as a backup; an extra run is\n// harmless because every row records its own fetched_at.\n//\n// Secret: GITHUB_TOKEN, a fine-grained token limited to cohens714/oddsvspolls\n// with Actions: read and write. Set with  npx wrangler secret put GITHUB_TOKEN\n\nconst REPO = \'cohens714/oddsvspolls\'\n\n// Cloudflare cron expression (UTC) -> workflow files it starts.\n// Must match the crons in wrangler.toml exactly.\nexport const SCHEDULE = {\n  \'7,27,47 * * * *\': [\'snapshot.yml\'],\n  \'13,43 * * * *\': [\'calls.yml\'],\n  // Election day, 8 a.m. to 6:59 p.m. Eastern: every 10 minutes so the last\n  // reading before the 6 p.m. cutoff is fresh.\n  \'3,13,23,33,43,53 13-23 3 11 *\': [\'snapshot.yml\'],\n}\n\nexport async function dispatch(workflow, token, fetchImpl = fetch) {\n  const res = await fetchImpl(\n    `https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`,\n    {\n      method: \'POST\',\n      headers: {\n        Authorization: `Bearer ${token}`,\n        Accept: \'application/vnd.github+json\',\n        \'X-GitHub-Api-Version\': \'2022-11-28\',\n        \'User-Agent\': \'oddsvspolls-trigger\',\n        \'Content-Type\': \'application/json\',\n      },\n      body: JSON.stringify({ ref: \'main\' }),\n    },\n  )\n  if (res.status !== 204) {\n    const text = await res.text()\n    throw new Error(`${workflow}: GitHub answered ${res.status} ${text.slice(0, 200)}`)\n  }\n  return workflow\n}\n\nexport default {\n  async scheduled(event, env, ctx) {\n    const workflows = SCHEDULE[event.cron] ?? []\n    if (!workflows.length) {\n      console.log(`no workflows mapped to cron "${event.cron}"`)\n      return\n    }\n    if (!env.GITHUB_TOKEN) throw new Error(\'GITHUB_TOKEN secret is not set\')\n    const results = await Promise.allSettled(\n      workflows.map((w) => dispatch(w, env.GITHUB_TOKEN)))\n    for (const r of results) {\n      if (r.status === \'fulfilled\') console.log(`started ${r.value}`)\n      else console.error(String(r.reason))\n    }\n    if (results.some((r) => r.status === \'rejected\')) {\n      throw new Error(\'one or more dispatches failed\')   // shows as an error in the dashboard\n    }\n  },\n\n  // Visiting the Worker\'s URL shows what it does; it never starts anything.\n  async fetch() {\n    return new Response(\n      \'oddsvspolls trigger: starts GitHub workflows on a schedule.\\n\'\n      + Object.entries(SCHEDULE).map(([c, w]) => `  ${c}  ->  ${w.join(\', \')}`).join(\'\\n\') + \'\\n\',\n      { headers: { \'content-type\': \'text/plain\' } })\n  },\n}\n',
}
WRANGLER = ["npx", "--yes", "wrangler@4"]

TOKEN_HELP = f"""
Create a GitHub token for the Worker (about a minute):

  1. Open https://github.com/settings/personal-access-tokens/new
  2. Token name: oddsvspolls-trigger.  Expiration: 90 days is plenty.
  3. Repository access: "Only select repositories" -> {REPO.split('/')[1]}
  4. Permissions -> Repository permissions -> Actions: "Read and write"
     (Metadata: Read-only gets added automatically; nothing else.)
  5. Generate token and copy it.
"""


def run(cmd, **kw):
    print("$ " + " ".join(cmd))
    return subprocess.run(cmd, **kw)


def repo_root():
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("Run this from inside the oddsvspolls repo.")
    return r.stdout.strip()


def github(method, path, token, body=None):
    req = Request(f"https://api.github.com{path}", method=method,
                  data=json.dumps(body).encode() if body is not None else None,
                  headers={"Authorization": f"Bearer {token}",
                           "Accept": "application/vnd.github+json",
                           "X-GitHub-Api-Version": "2022-11-28",
                           "User-Agent": "oddsvspolls-setup",
                           "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except HTTPError as e:
        return e.code, e.read().decode()
    except URLError as e:
        return 0, str(e)


def get_token():
    print(TOKEN_HELP)
    while True:
        token = getpass.getpass("Paste the token (hidden), then Enter: ").strip()
        if not token:
            continue
        status, text = github("POST", f"/repos/{REPO}/actions/workflows/snapshot.yml/dispatches",
                              token, {"ref": "main"})
        if status == 204:
            print("Token works: it just started a real 'Snapshot markets' run.")
            return token
        hint = {401: "GitHub doesn't recognize it; check you copied the whole token.",
                403: "It's valid but can't start workflows; set Actions to 'Read and write'.",
                404: f"It can't see {REPO}; under Repository access, select oddsvspolls."
                }.get(status, text[:200])
        print(f"That didn't work ({status}). {hint}\nTry again, or Ctrl+C to stop.")


def main():
    os.chdir(repo_root())
    if not shutil.which("npx"):
        sys.exit("npx isn't available. It comes with Node.js, which the site build already uses.")

    for path, content in FILES.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write(content)
        print(f"wrote {path}")

    print("\n=== Cloudflare login ===")
    if run(WRANGLER + ["whoami"], cwd=DIR, capture_output=True, text=True).stdout.find("You are logged in") < 0:
        if run(WRANGLER + ["login"], cwd=DIR).returncode != 0:
            sys.exit("Cloudflare login didn't finish. Rerun this script to try again.")
    else:
        print("already logged in")

    print("\n=== Deploy the Worker ===")
    if run(WRANGLER + ["deploy"], cwd=DIR).returncode != 0:
        sys.exit("Deploy failed; paste the output above.")

    print("\n=== GitHub token ===")
    token = get_token()
    r = subprocess.run(WRANGLER + ["secret", "put", "GITHUB_TOKEN", "--name", WORKER_NAME],
                       cwd=DIR, input=token + "\n", text=True)
    if r.returncode != 0:
        sys.exit("Storing the secret failed; paste the output above.")
    print("stored GITHUB_TOKEN as a Worker secret")

    print("\n=== Commit the Worker's files ===")
    paths = list(FILES) + [os.path.join("analysis", "setup_trigger_worker.py")]
    for cmd in (["git", "add"] + paths,
                ["git", "commit", "-m", "Start workflows from a Cloudflare cron Worker"],
                ["git", "pull", "--rebase", "--autostash", "origin", "main"],
                ["git", "push", "origin", "main"]):
        if run(cmd).returncode != 0:
            print("git step failed; the Worker is deployed and working regardless. "
                  "Paste the output and we'll sort out the commit.")
            break

    print(f"""
Done. The Worker starts snapshots at :07, :27 and :47 and race-call checks at
:13 and :43. A snapshot commit from the run this script just started should
land within a few minutes.

To watch it fire live:   cd {DIR} && npx wrangler tail
The token expires in 90 days; after the election that's fine to let lapse.""")


if __name__ == "__main__":
    main()
