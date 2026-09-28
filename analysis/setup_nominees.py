#!/usr/bin/env python3
"""
One-shot setup for nominee collection. Run from anywhere inside the repo:

    python3 setup_nominees.py

Safe to run more than once; each step skips itself if already done.
"""
import os
import shutil
import subprocess
import sys

WORKFLOW = """name: Collect nominee markets

on:
  schedule:
    - cron: "17 */6 * * *"
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: data-commits
  cancel-in-progress: false

jobs:
  collect:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Ingest nominee prices
        working-directory: analysis
        run: python3 ingest_nominees.py

      - name: Commit and push
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add public/nominee_snapshots.csv
          if git diff --cached --quiet; then
            echo "no changes"; exit 0
          fi
          git commit -m "nominee snapshot $(date -u +%Y-%m-%dT%H:%MZ)"
          for i in 1 2 3; do
            git pull --rebase origin main && git push origin main && exit 0
            sleep 10
          done
          exit 1
"""

NEW_ALIASES = [
    ('"donald j trump"', '"donald trump"'),
    ('"donald j trump jr"', '"donald trump jr"'),
    ('"dwayne the rock johnson"', '"dwayne johnson"'),
]

ATTR_LINE = "public/nominee_snapshots.csv merge=union"


def run(*cmd, check=True):
    print("$ " + " ".join(cmd))
    return subprocess.run(cmd, check=check)


def main():
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True)
    if root.returncode != 0:
        sys.exit("Run this from inside the oddsvspolls repo.")
    root = root.stdout.strip()
    os.chdir(root)
    print(f"repo: {root}\n")

    # 1. Make sure ingest_nominees.py is in analysis/
    ingest = os.path.join("analysis", "ingest_nominees.py")
    if not os.path.exists(ingest):
        dl = os.path.expanduser("~/Downloads/ingest_nominees.py")
        if os.path.exists(dl):
            shutil.copy(dl, ingest)
            print(f"copied {dl} -> {ingest}")
        else:
            sys.exit("Can't find ingest_nominees.py in analysis/ or ~/Downloads.")

    # 2. Add the new aliases
    src = open(ingest).read()
    marker = '    "aoc": "alexandria ocasio cortez",\n'
    if marker not in src:
        sys.exit("ALIASES block isn't where expected; stopping so nothing breaks.")
    added = []
    for k, v in NEW_ALIASES:
        if k not in src:
            line = f"    {k}: {v},\n"
            src = src.replace(marker, marker + line, 1)
            marker = marker + line
            added.append(k)
    if added:
        open(ingest, "w").write(src)
        print(f"aliases added: {', '.join(added)}")
    else:
        print("aliases already present")

    # 3. Install the workflow
    wf = os.path.join(".github", "workflows", "nominees.yml")
    os.makedirs(os.path.dirname(wf), exist_ok=True)
    if not os.path.exists(wf) or open(wf).read() != WORKFLOW:
        open(wf, "w").write(WORKFLOW)
        print(f"wrote {wf}")
    else:
        print("workflow already installed")

    # 4. Union-merge rule
    attrs = open(".gitattributes").read() if os.path.exists(".gitattributes") else ""
    if ATTR_LINE not in attrs:
        with open(".gitattributes", "a") as f:
            if attrs and not attrs.endswith("\n"):
                f.write("\n")
            f.write(ATTR_LINE + "\n")
        print("added union-merge rule to .gitattributes")
    else:
        print(".gitattributes already has the rule")

    # 5. Sanity check before committing
    print("\nrunning a dry run to confirm the script still works...\n")
    r = subprocess.run([sys.executable, "ingest_nominees.py", "--dry-run"],
                       cwd="analysis", capture_output=True, text=True)
    tail = [l for l in r.stdout.splitlines() + r.stderr.splitlines()
            if "one venue only" in l or "dry run" in l or "FAILED" in l]
    print("\n".join(tail))
    if r.returncode != 0:
        sys.exit("\nDry run failed; nothing committed. Paste the output above.")

    # 6. Commit and push
    print()
    run("git", "add", ingest, wf, ".gitattributes")
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"])
    if staged.returncode == 0:
        print("nothing new to commit")
    else:
        run("git", "commit", "-m", "Add 2028 nominee market collection")
    run("git", "pull", "--rebase", "--autostash", "origin", "main")
    run("git", "push", "origin", "main")

    # 7. Kick off the first collection if the GitHub CLI is available
    if shutil.which("gh"):
        run("gh", "workflow", "run", "nominees.yml", check=False)
        print("\nFirst collection started. Check the Actions tab in a minute.")
    else:
        print("\nDone. Last step: on GitHub, go to Actions > Collect nominee "
              "markets > Run workflow.")


if __name__ == "__main__":
    main()
