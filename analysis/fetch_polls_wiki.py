"""
Scrape poll tables from Wikipedia race articles.

    python3 fetch_polls_wiki.py --inspect GA      # what tables are on the page?
    python3 fetch_polls_wiki.py --dump GA 3       # show table 3 raw
    python3 fetch_polls_wiki.py GA MI NC          # parse and print rows
    python3 fetch_polls_wiki.py --all --write     # all races -> raw_polls.csv

Standard library only.

Wikipedia content is CC BY-SA, so reuse requires attribution and share-alike
on any derived database. Credit it visibly on the site.

Uses the MediaWiki parse API rather than scraping the rendered page: the API
is a documented interface, returns stable HTML, and is far friendlier to
Wikipedia's servers than hammering article URLs.

Poll tables are hand-maintained by editors, so column layouts vary between
articles and change over time. This makes no assumption about column
position; it reads the header row and locates columns by name. When a table
does not look like a poll table it is skipped and reported rather than
guessed at, because a silently mis-parsed poll is worse than a missing one.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = ("oddsvspolls.com/0.1 (https://oddsvspolls.com; "
              "research project) python-urllib")
TIMEOUT = 30
OUT = Path(__file__).resolve().parent.parent / "data" / "raw_polls.csv"

# Wikipedia article titles per race. Statewide race articles follow a
# consistent naming scheme, but verify each one: redirects and disambiguation
# suffixes change, and a wrong title yields an empty parse rather than an error.
ARTICLES = {
    "GA": "2026 United States Senate election in Georgia",
    "MI": "2026 United States Senate election in Michigan",
    "NC": "2026 United States Senate election in North Carolina",
    "ME": "2026 United States Senate election in Maine",
    "OH": "2026 United States Senate special election in Ohio",
    "TX": "2026 United States Senate election in Texas",
    "IA": "2026 United States Senate election in Iowa",
    "NH": "2026 United States Senate election in New Hampshire",
    "MN": "2026 United States Senate election in Minnesota",
    "AK": "2026 United States Senate election in Alaska",
    "NE": "2026 United States Senate election in Nebraska",
    "KS": "2026 United States Senate election in Kansas",
}

FIELDS = [
    "race_id", "state", "pollster", "start_date", "end_date",
    "sample_size", "population", "dem_pct", "rep_pct", "other_pct",
    "margin", "source_article", "scraped_at",
]

# Header cells that identify each column. Matched case-insensitively as
# substrings, first match wins, so order matters within each list.
HEADER_HINTS = {
    "pollster": ["poll source", "pollster", "poll sponsor", "source"],
    "dates": ["date", "dates administered", "field date"],
    "sample": ["sample size", "sample"],
    "margin_err": ["margin of error", "moe"],
    "other": ["other", "undecided", "someone else"],
}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def get_json(params: dict):
    query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    url = f"{API}?{query}"
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"  request failed: {exc}", file=sys.stderr)
        return None


def fetch_html(article: str):
    payload = get_json({
        "action": "parse", "page": article, "prop": "text",
        "format": "json", "formatversion": 2, "redirects": 1,
    })
    if not payload:
        return None
    if "error" in payload:
        print(f"  API error: {payload['error'].get('info')}", file=sys.stderr)
        return None
    return payload.get("parse", {}).get("text")


# --------------------------------------------------------------------------
# Table extraction
# --------------------------------------------------------------------------

class TableParser(HTMLParser):
    """Collect every <table class="wikitable"> as a grid of cell strings.

    Written against html.parser rather than a dependency so the collector
    stays install-free, the same property that makes ingest_markets.py
    unlikely to rot.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._depth = 0
        self._table = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            self._depth += 1
            if self._depth == 1 and "wikitable" in (attrs.get("class") or ""):
                self._table = []
        elif self._table is not None:
            if tag == "tr":
                self._row = []
            elif tag in ("td", "th"):
                self._cell = []

    def handle_endtag(self, tag):
        if tag == "table":
            if self._depth == 1 and self._table is not None:
                self.tables.append(self._table)
                self._table = None
            self._depth = max(0, self._depth - 1)
        elif self._table is not None:
            if tag == "tr" and self._row is not None:
                if any(c.strip() for c in self._row):
                    self._table.append(self._row)
                self._row = None
            elif tag in ("td", "th") and self._cell is not None:
                text = " ".join("".join(self._cell).split())
                if self._row is not None:
                    self._row.append(text)
                self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def extract_tables(html: str):
    p = TableParser()
    p.feed(html)
    return p.tables


# --------------------------------------------------------------------------
# Field parsing
# --------------------------------------------------------------------------

def find_column(headers, hints):
    """Index of the first header matching any hint, or None."""
    lowered = [h.lower() for h in headers]
    for hint in hints:
        for i, h in enumerate(lowered):
            if hint in h:
                return i
    return None


def find_party_columns(headers):
    """Locate the Democratic and Republican candidate columns.

    Wikipedia writes these as the candidate's name followed by a party
    marker, e.g. 'Jon Ossoff (D)'. Falls back to the words themselves for
    tables that use party names instead of candidates.
    """
    dem = rep = None
    for i, h in enumerate(headers):
        low = h.lower()
        if dem is None and (re.search(r"\(d\)|\bdem", low) or "democrat" in low):
            dem = i
        elif rep is None and (re.search(r"\(r\)|\brep", low) or "republican" in low):
            rep = i
    return dem, rep


PCT = re.compile(r"(\d{1,3}(?:\.\d)?)\s*%")


def parse_pct(cell):
    """First percentage in a cell, or None. Handles '47%' and '47.5%'."""
    if not cell:
        return None
    m = PCT.search(cell)
    if not m:
        # Some tables omit the sign entirely.
        m2 = re.fullmatch(r"\s*(\d{1,3}(?:\.\d)?)\s*", cell)
        if not m2:
            return None
        val = float(m2.group(1))
        return val if 0 <= val <= 100 else None
    return float(m.group(1))


def parse_sample(cell):
    if not cell:
        return None
    m = re.search(r"(\d[\d,]{1,6})", cell)
    return int(m.group(1).replace(",", "")) if m else None


def parse_population(cell):
    """LV / RV / A, the likely-voter screen. Materially affects the margin,
    so it is captured rather than averaged over blindly."""
    if not cell:
        return None
    low = cell.lower()
    for token, label in (("lv", "LV"), ("rv", "RV"), ("a)", "A")):
        if token in low:
            return label
    return None


MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}


def parse_dates(cell, default_year=2026):
    """Parse 'August 1-3, 2026' or 'July 28 - August 2, 2026' into a
    (start, end) pair of ISO dates. Returns (None, None) when unrecognised
    rather than guessing, since a wrong field date silently corrupts the
    days-to-election axis the whole analysis turns on."""
    if not cell:
        return None, None

    text = cell.replace("\u2013", "-").replace("\u2014", "-")
    year_m = re.search(r"(20\d{2})", text)
    year = int(year_m.group(1)) if year_m else default_year

    # month day - month day
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2})\s*-\s*([A-Za-z]+)\s+(\d{1,2})", text)
    if m and m.group(1).lower() in MONTHS and m.group(3).lower() in MONTHS:
        try:
            start = date(year, MONTHS[m.group(1).lower()], int(m.group(2)))
            end = date(year, MONTHS[m.group(3).lower()], int(m.group(4)))
            if end < start:  # range crossed a year boundary
                start = date(year - 1, start.month, start.day)
            return start.isoformat(), end.isoformat()
        except ValueError:
            return None, None

    # month day - day
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2})\s*-\s*(\d{1,2})", text)
    if m and m.group(1).lower() in MONTHS:
        try:
            mo = MONTHS[m.group(1).lower()]
            return (date(year, mo, int(m.group(2))).isoformat(),
                    date(year, mo, int(m.group(3))).isoformat())
        except ValueError:
            return None, None

    # single date
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2})", text)
    if m and m.group(1).lower() in MONTHS:
        try:
            d = date(year, MONTHS[m.group(1).lower()], int(m.group(2))).isoformat()
            return d, d
        except ValueError:
            return None, None

    return None, None


# --------------------------------------------------------------------------
# Table interpretation
# --------------------------------------------------------------------------

def looks_like_polls(headers):
    """A poll table needs a pollster column, a date column, and at least one
    party column. Anything else on these pages (results, endorsements,
    fundraising) fails at least one of those."""
    has_pollster = find_column(headers, HEADER_HINTS["pollster"]) is not None
    has_dates = find_column(headers, HEADER_HINTS["dates"]) is not None
    dem, rep = find_party_columns(headers)
    return has_pollster and has_dates and (dem is not None or rep is not None)


def parse_table(table, state, article):
    """Turn one wikitable into poll rows. Returns (rows, skipped_count)."""
    if len(table) < 2:
        return [], 0

    headers = table[0]
    if not looks_like_polls(headers):
        return [], 0

    i_pollster = find_column(headers, HEADER_HINTS["pollster"])
    i_dates = find_column(headers, HEADER_HINTS["dates"])
    i_sample = find_column(headers, HEADER_HINTS["sample"])
    i_other = find_column(headers, HEADER_HINTS["other"])
    i_dem, i_rep = find_party_columns(headers)

    scraped = datetime.utcnow().isoformat(timespec="seconds")
    rows, skipped = [], 0

    for raw in table[1:]:
        def cell(idx):
            return raw[idx] if idx is not None and idx < len(raw) else ""

        dem = parse_pct(cell(i_dem))
        rep = parse_pct(cell(i_rep))
        start, end = parse_dates(cell(i_dates))
        pollster = cell(i_pollster).strip()

        # Require both party figures and a usable end date. A poll missing
        # either cannot be scored or placed on the horizon axis, and keeping
        # a half-parsed row invites it to be silently averaged in later.
        if dem is None or rep is None or not end or not pollster:
            skipped += 1
            continue

        rows.append({
            "race_id": f"2026-senate-{state}",
            "state": state,
            "pollster": pollster,
            "start_date": start or end,
            "end_date": end,
            "sample_size": parse_sample(cell(i_sample)) or "",
            "population": parse_population(cell(i_sample)) or "",
            "dem_pct": dem,
            "rep_pct": rep,
            "other_pct": parse_pct(cell(i_other)) or "",
            "margin": round(dem - rep, 1),
            "source_article": article,
            "scraped_at": scraped,
        })

    return rows, skipped


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------

def inspect(state):
    article = ARTICLES.get(state.upper())
    if not article:
        print(f"no article configured for {state}", file=sys.stderr)
        return 1

    print(f"=== {article} ===")
    html = fetch_html(article)
    if not html:
        print("  could not fetch\n")
        return 1

    tables = extract_tables(html)
    print(f"  {len(tables)} wikitable(s) found\n")
    for i, t in enumerate(tables):
        headers = t[0] if t else []
        verdict = "POLLS" if looks_like_polls(headers) else "skip"
        print(f"  [{i}] {len(t) - 1} rows  {verdict}")
        print(f"      headers: {headers[:8]}")
    print()
    return 0


def dump(state, index):
    article = ARTICLES.get(state.upper())
    html = fetch_html(article) if article else None
    if not html:
        return 1
    tables = extract_tables(html)
    if index >= len(tables):
        print(f"only {len(tables)} tables", file=sys.stderr)
        return 1
    for row in tables[index][:12]:
        print(row)
    return 0


def scrape(states, write):
    all_rows = []
    for state in states:
        article = ARTICLES.get(state.upper())
        if not article:
            print(f"  {state}: no article configured")
            continue
        html = fetch_html(article)
        if not html:
            print(f"  {state}: fetch failed")
            continue

        rows, skipped = [], 0
        for table in extract_tables(html):
            r, s = parse_table(table, state.upper(), article)
            rows.extend(r)
            skipped += s

        print(f"  {state}: {len(rows)} polls parsed, {skipped} rows skipped")
        all_rows.extend(rows)

    if not all_rows:
        print("\nno polls parsed. Run --inspect on one state to see the "
              "table structure.")
        return 1

    print(f"\n{len(all_rows)} polls total")

    if not write:
        print("\n--- sample (not written, pass --write) ---")
        for r in all_rows[:5]:
            print(f"  {r['end_date']}  {r['pollster'][:26]:<28} "
                  f"D {r['dem_pct']:>5}  R {r['rep_pct']:>5}  "
                  f"margin {r['margin']:+.1f}")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)
    print(f"wrote {OUT}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("states", nargs="*", help="state codes, e.g. GA MI")
    ap.add_argument("--inspect", metavar="STATE", help="list tables on a page")
    ap.add_argument("--dump", nargs=2, metavar=("STATE", "N"),
                    help="print table N raw")
    ap.add_argument("--all", action="store_true", help="every configured state")
    ap.add_argument("--write", action="store_true", help="write raw_polls.csv")
    args = ap.parse_args()

    if args.inspect:
        return inspect(args.inspect)
    if args.dump:
        return dump(args.dump[0], int(args.dump[1]))

    states = list(ARTICLES) if args.all else [s.upper() for s in args.states]
    if not states:
        ap.error("give state codes, --all, --inspect STATE or --dump STATE N")
    return scrape(states, args.write)


if __name__ == "__main__":
    sys.exit(main())
