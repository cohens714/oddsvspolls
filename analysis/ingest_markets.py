"""
Daily market snapshot for Polymarket and Kalshi.

Appends one row per (race, venue) to data/snapshots.csv. Designed to be run
several times a day by GitHub Actions even though the site displays daily
figures, because intraday resolution cannot be backfilled later.

    python3 ingest_markets.py                 # fetch and append
    python3 ingest_markets.py --dry-run       # fetch and print, no write
    python3 ingest_markets.py --self-test     # offline check of the parsers

Design notes:

Every probability is normalized to P(yes_side wins) as declared in races.json.
Both venues are free to phrase their own question, and Kalshi frequently poses
the opposite side of what Polymarket lists. The normalization happens once,
here, so nothing downstream has to think about it.

A failure on one race or one venue never aborts the run. A partial snapshot is
far better than none, since a missed day is a permanent hole in the series.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
CONFIG = Path(__file__).resolve().parent / "races.json"
SNAPSHOT_FILE = ROOT / "data" / "snapshots.csv"

POLYMARKET_GAMMA = "https://gamma-api.polymarket.com/markets"
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2/markets"

USER_AGENT = "oddsvspolls.com data collector (+https://oddsvspolls.com)"
TIMEOUT = 20
RETRIES = 3

FIELDS = [
    "fetched_at",      # UTC timestamp of the request
    "snapshot_date",   # UTC date, the grouping key for daily display
    "race_id",
    "cycle",
    "venue",           # polymarket | kalshi
    "yes_side",        # which side prob refers to, e.g. DEM
    "prob",            # P(yes_side wins), in [0, 1]
    "days_out",        # days until election day
    "raw_price",       # venue's own number before any inversion
    "inverted",        # true if raw_price was flipped to match yes_side
    "volume",          # cumulative traded volume, venue's own units
    "liquidity",       # resting book depth where the venue reports it
    "spread",          # ask minus bid, a thinness signal price alone hides
    "note",            # empty on success, otherwise the failure reason
]


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def get_json(url: str, params: dict) -> object:
    """GET with retry and exponential backoff. Raises on final failure."""
    query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    full = f"{url}?{query}" if query else url

    last = None
    for attempt in range(RETRIES):
        try:
            req = Request(full, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"failed after {RETRIES} attempts: {full}: {last}")


# --------------------------------------------------------------------------
# Parsers, kept pure so they can be tested without network
# --------------------------------------------------------------------------

def parse_polymarket(payload: object, wanted_outcome: str) -> dict:
    """Extract price and depth for a named outcome from a Gamma response.

    Matches the outcome by label, never by index. Polymarket does not
    guarantee outcome ordering and an index-based read is a silent inversion
    waiting to happen.

    Returns price plus volume and liquidity. A price with no volume attached
    is not comparable to a poll average, and volume cannot be reconstructed
    after the fact, so it is captured at snapshot time whether or not the
    site displays it yet.
    """
    if isinstance(payload, list):
        if not payload:
            raise ValueError("no market returned for slug")
        market = payload[0]
    else:
        market = payload

    outcomes = market.get("outcomes")
    prices = market.get("outcomePrices")

    # Gamma returns these as JSON-encoded strings rather than arrays.
    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)
    if isinstance(prices, str):
        prices = json.loads(prices)

    if not outcomes or not prices or len(outcomes) != len(prices):
        raise ValueError(f"malformed outcomes/prices: {outcomes} {prices}")

    labels = [str(o).strip().lower() for o in outcomes]
    target = wanted_outcome.strip().lower()
    if target not in labels:
        raise ValueError(f"outcome {wanted_outcome!r} not in {outcomes}")

    price = float(prices[labels.index(target)])
    if not 0.0 <= price <= 1.0:
        raise ValueError(f"price out of range: {price}")

    def num(*keys):
        for k in keys:
            v = market.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return ""

    return {
        "price": price,
        "volume": num("volumeNum", "volume"),
        "liquidity": num("liquidityNum", "liquidity"),
        "spread": num("spread"),
    }


def parse_kalshi(payload: object) -> dict:
    """Mid-price of the YES contract, in [0, 1], plus depth.

    Kalshi quotes in cents. Uses the bid/ask midpoint when both sides are
    quoted, since last_price can be stale for hours in a thin market. Falls
    back to last_price only when the book is one-sided.
    """
    markets = payload.get("markets") if isinstance(payload, dict) else None
    if not markets:
        raise ValueError("no market returned for ticker")
    m = markets[0]

    bid, ask = m.get("yes_bid"), m.get("yes_ask")
    spread = ""
    if bid is not None and ask is not None and 0 < bid and ask < 100:
        cents = (float(bid) + float(ask)) / 2.0
        spread = (float(ask) - float(bid)) / 100.0
    elif m.get("last_price"):
        cents = float(m["last_price"])
    else:
        raise ValueError("no usable price: empty book and no last trade")

    price = cents / 100.0
    if not 0.0 <= price <= 1.0:
        raise ValueError(f"price out of range: {price}")

    def num(*keys):
        for k in keys:
            v = m.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return ""

    return {
        "price": price,
        "volume": num("volume", "volume_24h"),
        "liquidity": num("open_interest", "liquidity"),
        "spread": spread,
    }


def orient(price: float, venue_side: str, yes_side: str) -> tuple[float, bool]:
    """Flip the price if the venue quotes the opposite side."""
    if venue_side == yes_side:
        return price, False
    return 1.0 - price, True


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------

def fetch_race(race: dict, cycle: int, election_day: date) -> list[dict]:
    now = datetime.now(timezone.utc)
    base = {
        "fetched_at": now.isoformat(timespec="seconds"),
        "snapshot_date": now.date().isoformat(),
        "race_id": race["race_id"],
        "cycle": cycle,
        "yes_side": race["yes_side"],
        "days_out": (election_day - now.date()).days,
    }
    rows = []

    if race.get("polymarket_slug"):
        row = dict(base, venue="polymarket", prob="", raw_price="",
                   inverted="", volume="", liquidity="", spread="", note="")
        try:
            payload = get_json(POLYMARKET_GAMMA, {"slug": race["polymarket_slug"]})
            res = parse_polymarket(payload, race["polymarket_outcome"])
            # polymarket_outcome is chosen to be the yes_side, so no flip.
            row.update(prob=round(res["price"], 6),
                       raw_price=round(res["price"], 6), inverted=False,
                       volume=res["volume"], liquidity=res["liquidity"],
                       spread=res["spread"])
        except Exception as exc:
            row["note"] = f"{type(exc).__name__}: {exc}"[:200]
        rows.append(row)

    if race.get("kalshi_ticker"):
        row = dict(base, venue="kalshi", prob="", raw_price="",
                   inverted="", volume="", liquidity="", spread="", note="")
        try:
            payload = get_json(KALSHI_API, {"tickers": race["kalshi_ticker"]})
            res = parse_kalshi(payload)
            prob, flipped = orient(res["price"], race["kalshi_yes_means"],
                                   race["yes_side"])
            row.update(prob=round(prob, 6), raw_price=round(res["price"], 6),
                       inverted=flipped, volume=res["volume"],
                       liquidity=res["liquidity"], spread=res["spread"])
        except Exception as exc:
            row["note"] = f"{type(exc).__name__}: {exc}"[:200]
        rows.append(row)

    return rows


def append_rows(rows: list[dict]) -> None:
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    new_file = not SNAPSHOT_FILE.exists()
    with SNAPSHOT_FILE.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


# --------------------------------------------------------------------------
# Offline parser checks
# --------------------------------------------------------------------------

def self_test() -> int:
    poly = [{
        "outcomes": '["Republicans", "Democrats"]',
        "outcomePrices": '["0.62", "0.38"]',
        "volumeNum": 122787.0,
        "liquidityNum": 5000.0,
    }]
    res = parse_polymarket(poly, "Democrats")
    assert abs(res["price"] - 0.38) < 1e-9
    assert res["volume"] == 122787.0, "volume must be captured"
    assert abs(parse_polymarket(poly, "republicans")["price"] - 0.62) < 1e-9

    # Reversed ordering must give the same answer. This is the whole point of
    # matching on label rather than index.
    flipped = [{
        "outcomes": '["Democrats", "Republicans"]',
        "outcomePrices": '["0.38", "0.62"]',
    }]
    assert abs(parse_polymarket(flipped, "Democrats")["price"] - 0.38) < 1e-9

    # Polymarket's real 2026 markets are binary Yes/No per party.
    binary = [{"outcomes": '["Yes", "No"]', "outcomePrices": '["0.935", "0.065"]'}]
    assert abs(parse_polymarket(binary, "Yes")["price"] - 0.935) < 1e-9

    # Missing volume must not fail the parse, only leave the field empty.
    assert parse_polymarket(binary, "Yes")["volume"] == ""

    try:
        parse_polymarket(poly, "Whigs")
        raise AssertionError("should have rejected unknown outcome")
    except ValueError:
        pass

    kalshi = {"markets": [{"yes_bid": 40, "yes_ask": 44, "last_price": 99,
                           "volume": 1234, "open_interest": 500}]}
    res = parse_kalshi(kalshi)
    assert abs(res["price"] - 0.42) < 1e-9, "midpoint, not stale last"
    assert abs(res["spread"] - 0.04) < 1e-9
    assert res["volume"] == 1234.0

    thin = {"markets": [{"yes_bid": None, "yes_ask": None, "last_price": 55}]}
    assert abs(parse_kalshi(thin)["price"] - 0.55) < 1e-9

    try:
        parse_kalshi({"markets": [{"yes_bid": None, "yes_ask": None}]})
        raise AssertionError("should have rejected empty book")
    except ValueError:
        pass

    flip_val, flipped_flag = orient(0.42, "REP", "DEM")
    assert abs(flip_val - 0.58) < 1e-9 and flipped_flag is True
    same_val, same_flag = orient(0.42, "DEM", "DEM")
    assert abs(same_val - 0.42) < 1e-9 and same_flag is False

    print("parser self-test passed")
    return 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print, do not write")
    ap.add_argument("--self-test", action="store_true", help="offline parser checks")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    cfg = json.loads(CONFIG.read_text())
    election_day = date.fromisoformat(cfg["election_date"])

    rows = []
    for race in cfg["races"]:
        rows.extend(fetch_race(race, cfg["cycle"], election_day))

    ok = [r for r in rows if not r["note"]]
    bad = [r for r in rows if r["note"]]

    for r in rows:
        status = r["note"] or f"{r['prob']:.3f}"
        print(f"{r['race_id']:<24} {r['venue']:<12} {status}")

    if args.dry_run:
        print(f"\ndry run, nothing written. {len(ok)} ok, {len(bad)} failed")
        return 0

    if not ok:
        print("\nno successful fetches, refusing to write an all-error snapshot",
              file=sys.stderr)
        return 1

    append_rows(rows)
    print(f"\nappended {len(rows)} rows to {SNAPSHOT_FILE.relative_to(ROOT)} "
          f"({len(ok)} ok, {len(bad)} failed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
