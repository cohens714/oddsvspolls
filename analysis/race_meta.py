"""
Write data/race_meta.json so the frontend can name candidates.

    python3 race_meta.py            # write it
    python3 race_meta.py --show     # print, write nothing

Standard library only. Rerun after changing nominees in
fetch_polls_votehub.py or adding races to races.json.

Candidate names live in fetch_polls_votehub.py because the poll parser
needs them to match answer labels. Rather than duplicating them into a
second place that can drift, this derives the frontend's copy from that
one, and any race the poll collector does not know about still gets a
label from races.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_polls_votehub import RACES  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
RACES_JSON = HERE / "races.json"
OUT = DATA / "race_meta.json"

STATE_NAMES = {
    "AK": "Alaska", "AZ": "Arizona", "CA": "California", "FL": "Florida",
    "GA": "Georgia", "IA": "Iowa", "IL": "Illinois", "KS": "Kansas",
    "ME": "Maine", "MI": "Michigan", "MN": "Minnesota", "NC": "North Carolina",
    "NE": "Nebraska", "NH": "New Hampshire", "NM": "New Mexico",
    "NV": "Nevada", "NY": "New York", "OH": "Ohio", "PA": "Pennsylvania",
    "TX": "Texas", "WI": "Wisconsin",
}

# Races that are not a two-candidate contest, so they carry a label but no
# matchup. Their probability is party control, not a person winning.
CONTROL = {
    "2026-senate-control": "Senate control",
    "2026-house-control": "House control",
}


def surname(full_name):
    """Last word of a name. Enough to identify a candidate in a tight
    column, and what a reader recognises anyway."""
    if not full_name:
        return None
    return full_name.strip().split()[-1]


def build():
    meta = {}

    for race_id, label in CONTROL.items():
        meta[race_id] = {
            "label": label,
            "kind": "control",
            "dem": "Democrats",
            "rep": "Republicans",
            "dem_short": "Dem",
            "rep_short": "Rep",
        }

    for race_id, (subject, dem, rep, poll_type) in RACES.items():
        code = race_id.split("-")[-1]
        state = STATE_NAMES.get(code, code)
        kind = "governor" if poll_type == "governor" else "senate"
        # The page groups by office and heads each group, so repeating the
        # office in every row would be noise. `kind` travels with the race
        # for anything that needs to disambiguate out of context.
        label = state
        meta[race_id] = {
            "label": label,
            "kind": kind,
            "dem": dem,
            "rep": rep,
            "dem_short": surname(dem),
            "rep_short": surname(rep),
        }

    # Any race the market collector tracks but the poll collector does not
    # still needs a label, or the site falls back to a raw id.
    if RACES_JSON.exists():
        cfg = json.loads(RACES_JSON.read_text())
        for race in cfg.get("races", []):
            rid = race["race_id"]
            if rid in meta:
                continue
            code = rid.split("-")[-1]
            meta[rid] = {
                "label": STATE_NAMES.get(code, code),
                "kind": race.get("office", "senate"),
                "dem": None, "rep": None,
                "dem_short": None, "rep_short": None,
            }

    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    meta = build()

    for rid, m in sorted(meta.items()):
        matchup = (f"{m['dem_short']} (D) vs {m['rep_short']} (R)"
                   if m["dem_short"] and m["rep_short"] else "no matchup")
        print(f"  {rid:<24} {m['label']:<16} {matchup}")

    if args.show:
        return 0

    DATA.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {OUT.name} ({len(meta)} races)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
