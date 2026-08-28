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
# Single-market endpoint: /markets/{ticker}. The list endpoint's `tickers`
# filter is ignored, and an ignored filter returns an arbitrary market
# rather than an error, which is worse than failing.
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


def parse_kalshi(payload: object, expect_ticker: str = None) -> dict:
    """Mid-price of the YES contract, in [0, 1], plus depth.

    Kalshi quotes in cents. Uses the bid/ask midpoint when both sides are
    quoted, since last_price can be stale for hours in a thin market. Falls
    back to last_price only when the book is one-sided.

    Verifies the returned ticker matches the one requested. This is not
    paranoia: a filter parameter that the API silently ignores returns
    somebody else's market, and every downstream check would pass. The
    price would be real, the row would look fine, and it would belong to
    the wrong race.
    """
    if isinstance(payload, dict) and payload.get("market"):
        m = payload["market"]                      # /markets/{ticker}
    elif isinstance(payload, dict) and payload.get("markets"):
        m = payload["markets"][0]                  # /markets?tickers=
    else:
        raise ValueError("no market returned for ticker")

    if expect_ticker and m.get("ticker") != expect_ticker:
        raise ValueError(
            f"got {m.get('ticker')!r}, asked for {expect_ticker!r}; "
            f"the ticker filter was ignored")

    def dollars(*keys):
        """Read a *_dollars field. Kalshi returns these as decimal strings
        already in [0, 1], not the integer cents the older docs describe."""
        for k in keys:
            v = m.get(k)
            if v in (None, ""):
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return None

    def cents(*keys):
        """Legacy integer-cent fields, kept so a rollback on their side
        does not break collection."""
        for k in keys:
            v = m.get(k)
            if v in (None, ""):
                continue
            try:
                return float(v) / 100.0
            except (TypeError, ValueError):
                continue
        return None

    bid = dollars("yes_bid_dollars")
    ask = dollars("yes_ask_dollars")

    # Only one side of the book is always published. A NO quote is a YES
    # quote reflected: the best price to buy NO at X implies YES is
    # available at 1-X, and the sides swap because a bid on one is an ask
    # on the other.
    if bid is None:
        no_ask = dollars("no_ask_dollars")
        bid = (1.0 - no_ask) if no_ask is not None else cents("yes_bid")
    if ask is None:
        no_bid = dollars("no_bid_dollars")
        ask = (1.0 - no_bid) if no_bid is not None else cents("yes_ask")

    spread = ""
    if bid is not None and ask is not None and 0.0 < bid <= ask < 1.0:
        price = (bid + ask) / 2.0
        spread = round(ask - bid, 4)
    else:
        # last_price can be hours stale in a thin market, so it is the
        # fallback rather than the default.
        price = dollars("last_price_dollars", "previous_price_dollars")
        if price is None:
            price = cents("last_price")
        if price is None:
            raise ValueError("no usable price: empty book and no last trade")

    if not 0.0 <= price <= 1.0:
        raise ValueError(f"price out of range: {price}")

    return {
        "price": price,
        "volume": dollars("volume_dollars") or m.get("volume") or "",
        "liquidity": dollars("liquidity_dollars")
                     or (float(m["open_interest_fp"])
                         if m.get("open_interest_fp") else ""),
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
            ticker = race["kalshi_ticker"]
            payload = get_json(f"{KALSHI_API}/{ticker}", {})
            res = parse_kalshi(payload, ticker)
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

    # Real shape: decimal dollar strings, and only the NO side quoted.
    kalshi = {"market": {"ticker": "SENATEGA-26-D",
                         "no_bid_dollars": "0.0730",
                         "no_ask_dollars": "0.0790",
                         "last_price_dollars": "0.9270",
                         "liquidity_dollars": "0.0000",
                         "open_interest_fp": "202351.97"}}
    res = parse_kalshi(kalshi, "SENATEGA-26-D")
    # no_ask 0.079 -> yes_bid 0.921; no_bid 0.073 -> yes_ask 0.927
    assert abs(res["price"] - 0.924) < 1e-9, res["price"]
    assert abs(res["spread"] - 0.006) < 1e-9, res["spread"]

    # Explicit YES quotes win over derived ones.
    both = {"market": {"ticker": "T", "yes_bid_dollars": "0.4000",
                       "yes_ask_dollars": "0.4400",
                       "no_bid_dollars": "0.9000", "no_ask_dollars": "0.9500"}}
    assert abs(parse_kalshi(both)["price"] - 0.42) < 1e-9

    # A mismatched ticker must fail loudly rather than return a real price
    # belonging to a different race.
    try:
        parse_kalshi(kalshi, "SENATETX-26-D")
        raise AssertionError("should have rejected a mismatched ticker")
    except ValueError as exc:
        assert "ignored" in str(exc)

    # Both response shapes must work.
    listed = {"markets": [{"ticker": "X", "yes_bid_dollars": "0.4000",
                           "yes_ask_dollars": "0.4400"}]}
    assert abs(parse_kalshi(listed)["price"] - 0.42) < 1e-9

    # Legacy integer cents must still parse, in case they revert.
    legacy = {"market": {"ticker": "T", "yes_bid": 40, "yes_ask": 44}}
    assert abs(parse_kalshi(legacy)["price"] - 0.42) < 1e-9

    # The book must beat last_price: on the Georgia fixture the last trade
    # was 0.9270 while the book implies 0.924, and a thin market's last
    # trade can be hours old.
    assert abs(res["price"] - 0.924) < 1e-9, "book midpoint, not last trade"
    assert res["liquidity"] == 202351.97

    thin = {"market": {"ticker": "T", "last_price_dollars": "0.5500"}}
    assert abs(parse_kalshi(thin)["price"] - 0.55) < 1e-9

    try:
        parse_kalshi({"market": {"ticker": "T"}})
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
