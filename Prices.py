"""
Prices.py
=========
Downloads and maintains daily OHLCV price history for every ticker that
appears across all portfolio databases, using Yahoo Finance (yfinance).

Behaviour:
  - First run  : downloads from the ticker's earliest transaction date.
  - Later runs : only fetches data newer than the last stored date.
  - Tickers not found on Yahoo Finance are skipped with a warning.
  - Crypto tickers are automatically mapped to the YF symbol (e.g. BTC -> BTC-USD).

Usage:
  python Prices.py                          # auto-discovers *.db account files
  python Prices.py --accounts my.db        # specific account db(s)
  python Prices.py --prices prices.db      # custom prices db path
"""

import argparse
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yfinance as yf
import pandas as pd


# ---------------------------------------------------------------------------
# Crypto ticker -> Yahoo Finance suffix mapping
# ---------------------------------------------------------------------------

CRYPTO_TICKERS = {
    "BTC", "ETH", "SOL", "XRP", "DOGE", "SHIB", "ADA",
    "AVAX", "XLM", "DOT", "MATIC", "LTC", "BCH", "LINK", "UNI",
    "ATOM", "ETC", "TRX", "APT", "OP", "ARB",
}

# Crypto tickers whose Yahoo Finance symbol differs from {TICKER}-USD
CRYPTO_SYMBOL_OVERRIDES = {
    "PEPE": "PEPE24478-USD",
}

def get_ticker_aliases(prices_db: str = "prices.db") -> dict:
    """Return {old_ticker: new_ticker} from the ticker_aliases table."""
    if not Path(prices_db).exists():
        return {}
    try:
        conn = sqlite3.connect(prices_db)
        rows = conn.execute("SELECT old_ticker, new_ticker FROM ticker_aliases").fetchall()
        conn.close()
        return {r[0].upper(): r[1].upper() for r in rows}
    except Exception:
        return {}


def to_yf_ticker(ticker: str, aliases: dict = None) -> str:
    """Return the Yahoo Finance symbol for a ticker, applying aliases and crypto mapping."""
    t = ticker.upper().lstrip("$")
    # Strip existing -USD suffix to normalise the base symbol
    base = t[:-4] if t.endswith("-USD") else t
    # User-defined alias takes priority
    if aliases and base in aliases:
        return aliases[base]
    if aliases and t in aliases:
        return aliases[t]
    # Crypto override map (non-standard Yahoo symbols)
    if base in CRYPTO_SYMBOL_OVERRIDES:
        return CRYPTO_SYMBOL_OVERRIDES[base]
    # Standard crypto mapping
    if base in CRYPTO_TICKERS:
        return f"{base}-USD"
    return t


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

PRICES_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    ticker    TEXT NOT NULL,
    date      TEXT NOT NULL,
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    adj_close REAL,
    volume    INTEGER,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS ticker_info (
    ticker       TEXT PRIMARY KEY,
    yf_ticker    TEXT,
    first_date   TEXT,
    last_fetched TEXT,
    updated_at   TEXT,
    long_name    TEXT
);

CREATE TABLE IF NOT EXISTS ticker_aliases (
    old_ticker  TEXT PRIMARY KEY,
    new_ticker  TEXT NOT NULL,
    notes       TEXT,
    added_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS delisted_tickers (
    ticker      TEXT PRIMARY KEY,
    final_price REAL NOT NULL DEFAULT 0,
    notes       TEXT,
    added_at    TEXT DEFAULT (datetime('now'))
);
"""


def init_prices_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(PRICES_SCHEMA)
    # Add long_name column to existing databases that predate the schema change
    try:
        conn.execute("ALTER TABLE ticker_info ADD COLUMN long_name TEXT")
        conn.commit()
    except Exception:
        pass  # Column already exists
    return conn


# ---------------------------------------------------------------------------
# Delisted ticker helpers
# ---------------------------------------------------------------------------

def get_delisted_tickers(prices_db: str = "prices.db") -> dict:
    """Return {ticker: {"final_price": float, "notes": str}} for all delisted tickers."""
    if not Path(prices_db).exists():
        return {}
    conn = sqlite3.connect(prices_db)
    try:
        rows = conn.execute(
            "SELECT ticker, final_price, notes FROM delisted_tickers ORDER BY ticker"
        ).fetchall()
        return {r[0]: {"final_price": float(r[1]), "notes": r[2] or ""} for r in rows}
    except Exception:
        return {}
    finally:
        conn.close()


def mark_ticker_delisted(ticker: str, final_price: float = 0.0,
                         notes: str = "", prices_db: str = "prices.db"):
    """Mark a ticker as delisted with a final/settlement price."""
    conn = init_prices_db(prices_db)
    conn.execute(
        """INSERT INTO delisted_tickers (ticker, final_price, notes)
           VALUES (?, ?, ?)
           ON CONFLICT(ticker) DO UPDATE SET
             final_price = excluded.final_price,
             notes       = excluded.notes""",
        (ticker.upper(), float(final_price), notes),
    )
    conn.commit()
    conn.close()


def unmark_ticker_delisted(ticker: str, prices_db: str = "prices.db"):
    """Remove a ticker from the delisted list."""
    if not Path(prices_db).exists():
        return
    conn = sqlite3.connect(prices_db)
    conn.execute("DELETE FROM delisted_tickers WHERE ticker = ?", (ticker.upper(),))
    conn.commit()
    conn.close()


def add_ticker_alias(old_ticker: str, new_ticker: str,
                     notes: str = "", prices_db: str = "prices.db"):
    """Map old_ticker → new_ticker for price fetching (e.g. ticker renames)."""
    conn = init_prices_db(prices_db)
    conn.execute(
        """INSERT INTO ticker_aliases (old_ticker, new_ticker, notes)
           VALUES (?, ?, ?)
           ON CONFLICT(old_ticker) DO UPDATE SET
             new_ticker = excluded.new_ticker,
             notes      = excluded.notes""",
        (old_ticker.upper(), new_ticker.upper(), notes),
    )
    conn.commit()
    conn.close()


def remove_ticker_alias(old_ticker: str, prices_db: str = "prices.db"):
    """Remove a ticker alias."""
    if not Path(prices_db).exists():
        return
    conn = sqlite3.connect(prices_db)
    conn.execute("DELETE FROM ticker_aliases WHERE old_ticker = ?", (old_ticker.upper(),))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Account DB helpers
# ---------------------------------------------------------------------------

def parse_rh_date(date_str: str) -> date | None:
    """Parse Robinhood date strings: M/D/YYYY or YYYY-MM-DD."""
    if not date_str:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def collect_tickers(account_dbs: list[str]) -> dict[str, date]:
    """
    Scan all account databases and return {ticker: earliest_date}.
    Only considers rows where the ticker was actually traded/transferred.
    """
    relevant_types = (
        "buy", "sell", "transfer_in", "internal_transfer",
        "crypto_buy", "crypto_sell", "dividend_reinvestment",
        "option_buy", "option_sell", "stock_split", "merger",
        "spin_off", "forced_buy_in", "asset_transfer",
    )
    placeholders = ",".join("?" * len(relevant_types))

    tickers: dict[str, date] = {}

    for db_path in account_dbs:
        if not Path(db_path).exists():
            print(f"  Warning: {db_path} not found, skipping.")
            continue
        conn = sqlite3.connect(db_path)
        # Fetch all rows — do NOT use SQLite MIN() on activity_date because
        # dates are stored as M/D/YYYY text and sort lexicographically wrong.
        rows = conn.execute(
            f"""SELECT ticker, activity_date
                FROM transactions
                WHERE ticker IS NOT NULL AND ticker != ''
                  AND transaction_type IN ({placeholders})""",
            relevant_types,
        ).fetchall()
        conn.close()

        for ticker, date_str in rows:
            parsed = parse_rh_date(date_str)
            if parsed is None:
                continue
            if ticker not in tickers or parsed < tickers[ticker]:
                tickers[ticker] = parsed

    return tickers


# ---------------------------------------------------------------------------
# Price download
# ---------------------------------------------------------------------------

def fetch_prices(yf_ticker: str, start: date, end: date) -> pd.DataFrame | None:
    """
    Download daily OHLCV data from Yahoo Finance.
    Returns a DataFrame with columns [Open, High, Low, Close, Adj Close, Volume]
    indexed by date, or None if the ticker is not found / no data returned.
    """
    try:
        ticker_obj = yf.Ticker(yf_ticker)
        df = ticker_obj.history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(), auto_adjust=False)
        if df is None or df.empty:
            return None
        df.index = pd.to_datetime(df.index).normalize()
        return df
    except Exception as e:
        print(f"    Error fetching {yf_ticker}: {e}")
        return None


def upsert_prices(conn: sqlite3.Connection, ticker: str, df: pd.DataFrame):
    """Insert price rows, silently skipping duplicates."""
    rows = []
    for ts, row in df.iterrows():
        rows.append((
            ticker,
            ts.strftime("%Y-%m-%d"),
            float(row.get("Open",  0) or 0),
            float(row.get("High",  0) or 0),
            float(row.get("Low",   0) or 0),
            float(row.get("Close", 0) or 0),
            float(row.get("Adj Close", row.get("Close", 0)) or 0),
            int(row.get("Volume", 0) or 0),
        ))
    conn.executemany(
        """INSERT OR IGNORE INTO prices
           (ticker, date, open, high, low, close, adj_close, volume)
           VALUES (?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Main update loop
# ---------------------------------------------------------------------------

def update_prices(prices_conn: sqlite3.Connection, tickers: dict[str, date]):
    today = date.today()
    aliases = get_ticker_aliases()

    for ticker, earliest_tx_date in sorted(tickers.items()):
        yf_ticker = to_yf_ticker(ticker, aliases)

        # Determine fetch window(s)
        row = prices_conn.execute(
            "SELECT first_date, last_fetched FROM ticker_info WHERE ticker = ?", (ticker,)
        ).fetchone()

        segments = []  # list of (start, end) date pairs to fetch

        if row and row[1]:
            stored_first = datetime.strptime(row[0], "%Y-%m-%d").date()
            last_fetched = datetime.strptime(row[1], "%Y-%m-%d").date()

            # Backfill: account import revealed an earlier start date
            if earliest_tx_date < stored_first:
                segments.append((earliest_tx_date, stored_first - timedelta(days=1)))

            # Forward fill: fetch new trading days since last run
            if last_fetched < today:
                segments.append((last_fetched + timedelta(days=1), today))

            if not segments:
                print(f"  {ticker:<8} already up to date ({last_fetched})")
                continue
        else:
            segments = [(earliest_tx_date, today)]

        all_frames = []
        for fetch_from, fetch_to in segments:
            print(f"  {ticker:<8} ({yf_ticker}) fetching {fetch_from} -> {fetch_to} ...", end=" ", flush=True)
            df = fetch_prices(yf_ticker, fetch_from, fetch_to)
            if df is not None and not df.empty:
                print(f"{len(df)} rows")
                all_frames.append(df)
            else:
                print("no data")

        if not all_frames:
            df = None
        else:
            df = pd.concat(all_frames).sort_index()

        if df is None or df.empty:
            print("no data (IPO / delisted / crypto not mapped?)")
            # Still record the ticker so we don't keep retrying endlessly today
            prices_conn.execute(
                """INSERT INTO ticker_info (ticker, yf_ticker, first_date, last_fetched, updated_at)
                   VALUES (?,?,?,?,datetime('now'))
                   ON CONFLICT(ticker) DO UPDATE SET
                     yf_ticker   = excluded.yf_ticker,
                     updated_at  = excluded.updated_at""",
                (ticker, yf_ticker, earliest_tx_date.isoformat(), None),
            )
            prices_conn.commit()
            continue

        upsert_prices(prices_conn, ticker, df)

        last_date = df.index.max().strftime("%Y-%m-%d")
        prices_conn.execute(
            """INSERT INTO ticker_info (ticker, yf_ticker, first_date, last_fetched, updated_at)
               VALUES (?,?,?,?,datetime('now'))
               ON CONFLICT(ticker) DO UPDATE SET
                 yf_ticker    = excluded.yf_ticker,
                 first_date   = MIN(first_date, excluded.first_date),
                 last_fetched = excluded.last_fetched,
                 updated_at   = excluded.updated_at""",
            (ticker, yf_ticker, earliest_tx_date.isoformat(), last_date),
        )
        prices_conn.commit()
        print(f"{len(df)} rows, last date {last_date}")


# ---------------------------------------------------------------------------
# Ticker name cache (called from App.py / Calculations.py)
# ---------------------------------------------------------------------------

def fetch_ticker_names(tickers: list, prices_db: str = "prices.db") -> dict:
    """
    Return {ticker: long_name} for all tickers, using prices.db as a cache.
    Any tickers without a cached name are fetched from Yahoo Finance and stored.
    """
    if not tickers:
        return {}

    conn = init_prices_db(prices_db)
    names = {}

    # Read what we already have cached
    rows = conn.execute("SELECT ticker, long_name FROM ticker_info").fetchall()
    cached = {r[0]: r[1] for r in rows if r[1]}

    missing = [t for t in tickers if t not in cached]
    _aliases = get_ticker_aliases()

    for ticker in missing:
        yf_sym = to_yf_ticker(ticker, _aliases)
        try:
            info = yf.Ticker(yf_sym).info
            name = info.get("longName") or info.get("shortName") or ticker
        except Exception:
            name = ticker
        conn.execute(
            """INSERT INTO ticker_info (ticker, yf_ticker, long_name, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(ticker) DO UPDATE SET
                 long_name  = excluded.long_name,
                 updated_at = excluded.updated_at""",
            (ticker, yf_sym, name),
        )
        cached[ticker] = name

    conn.commit()
    conn.close()

    return {t: cached.get(t, t) for t in tickers}


# ---------------------------------------------------------------------------
# Startup gap check (called from App.py)
# ---------------------------------------------------------------------------

def check_and_fill_price_gaps(
    account_dbs: list[str],
    prices_db: str = "prices.db",
) -> list[str]:
    """
    For every ticker in account_dbs:
      - Back-fill any missing history before the earliest transaction date.
      - Forward-fill any missing trading days from the last stored date to today.

    Returns a list of tickers that could not be fetched at all.
    """
    tickers = collect_tickers(account_dbs)
    if not tickers:
        return []

    prices_conn = init_prices_db(prices_db)
    failed: list[str] = []
    today = date.today()
    aliases = get_ticker_aliases(prices_db)

    # Skip tickers the user has explicitly marked as delisted
    try:
        _delisted_rows = prices_conn.execute(
            "SELECT ticker FROM delisted_tickers"
        ).fetchall()
        _delisted_set = {r[0] for r in _delisted_rows}
    except Exception:
        _delisted_set = set()

    for ticker, earliest_tx_date in sorted(tickers.items()):
        _ticker_clean = ticker.upper().lstrip("$")
        if ticker.upper() in _delisted_set or _ticker_clean in _delisted_set:
            continue
        yf_ticker = to_yf_ticker(ticker, aliases)

        row = prices_conn.execute(
            "SELECT MIN(date), MAX(date) FROM prices WHERE ticker = ?", (ticker,)
        ).fetchone()
        earliest_price = (
            datetime.strptime(row[0], "%Y-%m-%d").date() if row and row[0] else None
        )
        latest_price = (
            datetime.strptime(row[1], "%Y-%m-%d").date() if row and row[1] else None
        )

        segments = []

        # Back-fill: history missing before earliest transaction
        if earliest_price is None:
            segments.append((earliest_tx_date, today))
        else:
            if earliest_price > earliest_tx_date:
                segments.append((earliest_tx_date, earliest_price - timedelta(days=1)))
            # Forward-fill: new trading days since last stored date
            if latest_price is not None and latest_price < today:
                segments.append((latest_price + timedelta(days=1), today))

        if not segments:
            continue

        any_success = False
        for fetch_start, fetch_end in segments:
            df = fetch_prices(yf_ticker, fetch_start, fetch_end)
            if df is not None and not df.empty:
                upsert_prices(prices_conn, ticker, df)
                any_success = True

        if any_success:
            # Refresh the latest date after upserts
            new_max = prices_conn.execute(
                "SELECT MAX(date) FROM prices WHERE ticker = ?", (ticker,)
            ).fetchone()
            last_date = new_max[0] if new_max and new_max[0] else today.isoformat()
            prices_conn.execute(
                """INSERT INTO ticker_info (ticker, yf_ticker, first_date, last_fetched, updated_at)
                   VALUES (?,?,?,?,datetime('now'))
                   ON CONFLICT(ticker) DO UPDATE SET
                     yf_ticker    = excluded.yf_ticker,
                     first_date   = MIN(COALESCE(first_date, excluded.first_date), excluded.first_date),
                     last_fetched = excluded.last_fetched,
                     updated_at   = excluded.updated_at""",
                (ticker, yf_ticker, earliest_tx_date.isoformat(), last_date),
            )
            prices_conn.commit()
        elif earliest_price is None:
            # Never had data at all
            failed.append(ticker)

    prices_conn.close()
    return failed


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(prices_conn: sqlite3.Connection):
    rows = prices_conn.execute(
        """SELECT ti.ticker, ti.yf_ticker, ti.first_date, ti.last_fetched,
                  COUNT(p.date) as row_count
           FROM ticker_info ti
           LEFT JOIN prices p ON p.ticker = ti.ticker
           GROUP BY ti.ticker
           ORDER BY ti.ticker"""
    ).fetchall()

    print(f"\n  {'Ticker':<8} {'YF Symbol':<12} {'From':<12} {'Through':<12} {'Rows':>6}")
    print(f"  {'-'*8} {'-'*12} {'-'*12} {'-'*12} {'-'*6}")
    for ticker, yf_t, first, last, count in rows:
        last = last or "no data"
        print(f"  {ticker:<8} {(yf_t or ''):<12} {(first or ''):<12} {last:<12} {count:>6}")

    total = prices_conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    print(f"\n  Total price rows: {total:,}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def find_account_dbs(directory: str) -> list[str]:
    """Find all *.db files in directory, excluding the prices database."""
    return [
        str(p) for p in Path(directory).glob("*.db")
        if p.name != "prices.db"
    ]


def main():
    parser = argparse.ArgumentParser(
        description="Download and update daily price history for all portfolio tickers."
    )
    parser.add_argument(
        "--accounts", nargs="*", default=None,
        help="Account database file(s). If omitted, all *.db files in the current directory are used.",
    )
    parser.add_argument(
        "--prices", default="prices.db",
        help="Path to the prices database (default: prices.db).",
    )
    args = parser.parse_args()

    account_dbs = args.accounts or find_account_dbs(".")
    if not account_dbs:
        print("No account databases found. Run Import.py first.")
        sys.exit(1)

    print(f"Prices database : {args.prices}")
    print(f"Account files   : {', '.join(account_dbs)}")

    print("\nCollecting tickers from account databases...")
    tickers = collect_tickers(account_dbs)
    print(f"  Found {len(tickers)} tickers across all accounts.\n")

    prices_conn = init_prices_db(args.prices)

    print("Fetching price data...")
    update_prices(prices_conn, tickers)

    print("\n-- Prices database summary ------------------------------------------")
    print_summary(prices_conn)
    print("---------------------------------------------------------------------\n")

    prices_conn.close()


if __name__ == "__main__":
    main()
