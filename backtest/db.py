"""SQLite schema and persistence helpers for the backtest replay engine.

Design notes:
- Orderbook levels are stored NORMALIZED (one row per level), not as JSON
  blobs: `pd.merge_asof` only needs the slim `orderbook_snapshots` table,
  and the depth walker loads levels only for the few matched snapshots.
- Prices are stored as integer thousandths (`price_m`, 0-1000): Polymarket
  binary prices tick at 0.001/0.01, so this is exact (no float drift) and
  downcasts to int16 in pandas.
- WAL mode lets the recorder write while replays read concurrently.
"""

import os
import sqlite3
import time

DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "data", "backtest.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    market_slug    TEXT PRIMARY KEY,
    condition_id   TEXT,
    yes_token_id   TEXT NOT NULL,
    no_token_id    TEXT NOT NULL,
    window_start   INTEGER NOT NULL,
    window_end     INTEGER NOT NULL,
    outcome        TEXT CHECK (outcome IN ('YES','NO')),
    outcome_source TEXT,
    resolved_at    INTEGER
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id  TEXT PRIMARY KEY,
    ts         REAL NOT NULL,
    direction  TEXT NOT NULL CHECK (direction IN ('UP','DOWN')),
    source     TEXT NOT NULL DEFAULT 'tradingview',
    raw_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts);

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    snapshot_id   INTEGER PRIMARY KEY,
    ts            REAL NOT NULL,
    market_slug   TEXT NOT NULL REFERENCES markets(market_slug),
    token_id      TEXT NOT NULL,
    side_label    TEXT NOT NULL CHECK (side_label IN ('YES','NO')),
    best_bid_m    INTEGER,
    best_ask_m    INTEGER,
    bid_depth_usd REAL,
    ask_depth_usd REAL,
    n_bid_levels  INTEGER NOT NULL,
    n_ask_levels  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snap_token_ts ON orderbook_snapshots(token_id, ts);
CREATE INDEX IF NOT EXISTS idx_snap_slug ON orderbook_snapshots(market_slug);

CREATE TABLE IF NOT EXISTS orderbook_levels (
    snapshot_id INTEGER NOT NULL REFERENCES orderbook_snapshots(snapshot_id),
    side        TEXT NOT NULL CHECK (side IN ('bid','ask')),
    level       INTEGER NOT NULL,
    price_m     INTEGER NOT NULL,
    size        REAL NOT NULL,
    PRIMARY KEY (snapshot_id, side, level)
) WITHOUT ROWID;

-- Spot (Binance) L2 depth, recorded for MLOFI / order-flow features. Prices are
-- stored as REAL (not price_m thousandths): a $60k spot price is not in [0,1].
CREATE TABLE IF NOT EXISTS spot_orderbook_snapshots (
    snapshot_id    INTEGER PRIMARY KEY,
    ts             REAL NOT NULL,
    symbol         TEXT NOT NULL,
    best_bid       REAL,
    best_ask       REAL,
    bid_depth_base REAL,
    ask_depth_base REAL,
    n_bid_levels   INTEGER NOT NULL,
    n_ask_levels   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spot_snap_sym_ts
    ON spot_orderbook_snapshots(symbol, ts);

CREATE TABLE IF NOT EXISTS spot_orderbook_levels (
    snapshot_id INTEGER NOT NULL REFERENCES spot_orderbook_snapshots(snapshot_id),
    side        TEXT NOT NULL CHECK (side IN ('bid','ask')),
    level       INTEGER NOT NULL,
    price       REAL NOT NULL,
    size        REAL NOT NULL,
    PRIMARY KEY (snapshot_id, side, level)
) WITHOUT ROWID;
"""


def price_to_m(price: float) -> int:
    """Convert a 0-1 probability price to exact integer thousandths."""
    return round(price * 1000)


def m_to_price(price_m: int) -> float:
    """Convert integer thousandths back to a float price."""
    return price_m / 1000.0


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open (and initialize) the backtest database. ':memory:' is supported."""
    path = db_path or os.getenv("BACKTEST_DB_PATH", DEFAULT_DB_PATH)
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.executescript(SCHEMA)
    return con


def upsert_market(
    con: sqlite3.Connection,
    market_slug: str,
    yes_token_id: str,
    no_token_id: str,
    window_start: int,
    window_end: int,
    condition_id: str | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO markets
            (market_slug, condition_id, yes_token_id, no_token_id,
             window_start, window_end)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(market_slug) DO UPDATE SET
            condition_id = excluded.condition_id,
            yes_token_id = excluded.yes_token_id,
            no_token_id  = excluded.no_token_id
        """,
        (
            market_slug,
            condition_id,
            yes_token_id,
            no_token_id,
            window_start,
            window_end,
        ),
    )
    con.commit()


def set_market_outcome(
    con: sqlite3.Connection, market_slug: str, outcome: str, source: str
) -> None:
    con.execute(
        "UPDATE markets SET outcome = ?, outcome_source = ?, resolved_at = ? "
        "WHERE market_slug = ?",
        (outcome, source, int(time.time()), market_slug),
    )
    con.commit()


def insert_signal(
    con: sqlite3.Connection,
    signal_id: str,
    ts: float,
    direction: str,
    source: str = "tradingview",
    raw_json: str | None = None,
) -> bool:
    """Idempotent signal insert. Returns True if a new row was written."""
    cur = con.execute(
        "INSERT OR IGNORE INTO signals (signal_id, ts, direction, source, raw_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (signal_id, ts, direction, source, raw_json),
    )
    con.commit()
    return cur.rowcount > 0


def insert_snapshot(
    con: sqlite3.Connection,
    ts: float,
    market_slug: str,
    token_id: str,
    side_label: str,
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
) -> int:
    """Persist one orderbook snapshot with its levels in a single transaction.

    `bids` and `asks` must already be normalized best-first
    (bids descending by price, asks ascending) and depth-capped.
    """
    bid_depth = sum(p * s for p, s in bids)
    ask_depth = sum(p * s for p, s in asks)
    cur = con.execute(
        """
        INSERT INTO orderbook_snapshots
            (ts, market_slug, token_id, side_label, best_bid_m, best_ask_m,
             bid_depth_usd, ask_depth_usd, n_bid_levels, n_ask_levels)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts,
            market_slug,
            token_id,
            side_label,
            price_to_m(bids[0][0]) if bids else None,
            price_to_m(asks[0][0]) if asks else None,
            bid_depth,
            ask_depth,
            len(bids),
            len(asks),
        ),
    )
    snapshot_id = cur.lastrowid
    assert snapshot_id is not None
    rows = [
        (snapshot_id, "bid", i, price_to_m(p), s) for i, (p, s) in enumerate(bids)
    ] + [(snapshot_id, "ask", i, price_to_m(p), s) for i, (p, s) in enumerate(asks)]
    if rows:
        con.executemany(
            "INSERT INTO orderbook_levels (snapshot_id, side, level, price_m, size) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
    con.commit()
    return snapshot_id


def insert_spot_snapshot(
    con: sqlite3.Connection,
    ts: float,
    symbol: str,
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
) -> int:
    """Persist one Binance spot L2 snapshot with its levels (REAL prices).

    Mirrors ``insert_snapshot`` but for the underlying spot book: prices are kept
    as REAL (a $60k price isn't a [0,1] probability). ``bids``/``asks`` must be
    normalized best-first (bids descending, asks ascending) and depth-capped.
    """
    bid_depth = sum(s for _, s in bids)
    ask_depth = sum(s for _, s in asks)
    cur = con.execute(
        """
        INSERT INTO spot_orderbook_snapshots
            (ts, symbol, best_bid, best_ask, bid_depth_base, ask_depth_base,
             n_bid_levels, n_ask_levels)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts,
            symbol,
            bids[0][0] if bids else None,
            asks[0][0] if asks else None,
            bid_depth,
            ask_depth,
            len(bids),
            len(asks),
        ),
    )
    snapshot_id = cur.lastrowid
    assert snapshot_id is not None
    rows = [(snapshot_id, "bid", i, p, s) for i, (p, s) in enumerate(bids)] + [
        (snapshot_id, "ask", i, p, s) for i, (p, s) in enumerate(asks)
    ]
    if rows:
        con.executemany(
            "INSERT INTO spot_orderbook_levels (snapshot_id, side, level, price, size) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
    con.commit()
    return snapshot_id
