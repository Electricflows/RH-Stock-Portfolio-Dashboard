"""
Robinhood CSV Importer
======================
Reads Robinhood account CSV exports and loads them into a SQLite database.

Features:
  - Per-account SQLite databases (auto-named from account when --db is omitted)
  - Cleans RH-specific formatting (parenthetical negatives, dollar signs, embedded newlines)
  - Maps raw trans codes to normalized transaction_type labels
  - ACH direction (deposit vs withdrawal) resolved from amount sign
  - Blank/null trans codes flagged as "no_trans_code" rather than silently dropped
  - Flags unknown trans codes to a separate review table instead of silently dropping them
  - Deduplicates across overlapping exports using a SHA-256 content hash as the primary key
  - Records which source file each row came from

Usage:
  python Import.py <file.csv> [file2.csv ...] [--db path/to/db.sqlite] [--account "Name"]

  If --db is omitted the database is auto-named from the account:
    "Michael IRA"  →  michael_ira.db
    "Taxable"      →  taxable.db
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------------------
# Trans code → normalized transaction_type mapping
# ---------------------------------------------------------------------------
# For codes whose direction depends on amount sign (ACH), see resolve_transaction_type().
# Add new codes here as you discover them; unknown codes go to the review table.
# ---------------------------------------------------------------------------
TRANS_CODE_MAP = {
    # ── Equity trades ----------------------------------------------------------------------──
    "Buy":   "buy",
    "Sell":  "sell",

    # ── Cash / transfers ----------------------------------------------------------------------
    # ACH direction is resolved dynamically in resolve_transaction_type()
    "ACH":   None,                  # placeholder n/a handled below
    "ACATS": "account_transfer",
    "RHD":   "debit_card_transaction",
    "RHW":   "debit_card_transaction",
    "OPPF":  "deposit",
    "PFIR":  "deposit",             # Prior-year IRA contribution
    "MTCH":  "deposit",             # IRA match / interest on contribution
    "LIQ":   "liquidation",
    "SXCH":  "share_exchange",
    "SPLIT": "stock_split",

    # ── Fees & subscriptions ─────────────────────────────────────────────────
    "GOLD":  "subscription_fee",
    "FEE":   "fee",

    # ── Dividends, interest, rewards ─────────────────────────────────────────
    "LCAP":  "capital_gains_distribution",
    "DIV":   "dividend",
    "CDIV":  "dividend",
    "DRIP":  "dividend_reinvestment",
    "INT":   "interest",
    "SLIP":  "lending_income",
    "REWD":  "reward",

    # ── Options ----------------------------------------------------------------------─────────
    "OBUY":  "option_buy",
    "OSEL":  "option_sell",
    "EXER":  "option_exercise",
    "ASGN":  "option_assignment",
    "EXPR":  "option_expire",

    # ── Crypto ----------------------------------------------------------------------──────────
    "CBUY":  "crypto_buy",
    "CSEL":  "crypto_sell",
    "CREC":  "crypto_receive",
    "CSND":  "crypto_send",

    # ── Corporate actions ────────────────────────────────────────────────────
    "SPL":   "stock_split",
    "MRGR":  "merger",
    "SPIN":  "spin_off",
    "ABIP":  "forced_buy_in",
    "GMPC":  "bonus_credit",
    "MISC":  "miscellaneous_income",

    # ── Account transfers & internal adjustments ─────────────────────────────
    "NOA":   "direct_deposit",
    "ACATI": "transfer_in",
    "ITRF":  "internal_transfer",
    "T/A":   "asset_transfer",

    # ── Futures & derivatives ─────────────────────────────────────────────────
    "FUTSWP": "futures_margin_sweep",
    "SPR":    "option_spread_adjustment",

    # ── Event contracts (Robinhood prediction markets) ────────────────────────
    # Cash already captured as net via FUTSWP; these rows are informational.
    "EVTBUY": "event_contract_buy",
    "EVTPAY": "event_contract_payout",
}

# Buy rows whose description contains this substring are dividend reinvestments.
DRIP_MARKER = "Dividend Reinvestment"


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    account_name TEXT PRIMARY KEY,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    tx_hash          TEXT PRIMARY KEY,
    account          TEXT,
    activity_date    TEXT,
    process_date     TEXT,
    settle_date      TEXT,
    ticker           TEXT,
    description      TEXT,
    trans_code       TEXT,
    transaction_type TEXT,
    quantity         REAL,
    price            REAL,
    amount           REAL,
    source_file      TEXT,
    FOREIGN KEY (account) REFERENCES accounts(account_name)
);

CREATE TABLE IF NOT EXISTS unknown_transactions (
    tx_hash     TEXT,
    account     TEXT,
    trans_code  TEXT,
    raw_row     TEXT,
    source_file TEXT,
    imported_at TEXT DEFAULT (datetime('now'))
);

-- Cash inflows
CREATE VIEW IF NOT EXISTS deposits AS
    SELECT account, activity_date, transaction_type, amount, description, source_file
    FROM transactions
    WHERE transaction_type IN ('deposit', 'account_transfer')
      AND (amount IS NULL OR amount >= 0);

-- Cash outflows
CREATE VIEW IF NOT EXISTS withdrawals AS
    SELECT account, activity_date, transaction_type, amount, description, source_file
    FROM transactions
    WHERE transaction_type IN ('withdrawal', 'account_transfer')
       OR (transaction_type = 'deposit' AND amount < 0);

-- All cash flows (deposits + withdrawals) in one place
CREATE VIEW IF NOT EXISTS cash_flows AS
    SELECT account, activity_date, transaction_type, amount, description, source_file
    FROM transactions
    WHERE transaction_type IN ('deposit', 'withdrawal', 'account_transfer',
                               'subscription_fee', 'fee', 'debit_card_transaction');

-- Indexes for frequent filter columns (safe to run on existing databases)
CREATE INDEX IF NOT EXISTS idx_tx_ticker  ON transactions(ticker);
CREATE INDEX IF NOT EXISTS idx_tx_account ON transactions(account);
CREATE INDEX IF NOT EXISTS idx_tx_type    ON transactions(transaction_type);
CREATE INDEX IF NOT EXISTS idx_tx_date    ON transactions(activity_date);
"""


def account_to_db_name(account: str) -> str:
    """Convert an account name to a filesystem-safe db filename.

    'Michael IRA'  →  'michael_ira.db'
    'Taxable (Joint)'  →  'taxable_joint.db'
    """
    slug = re.sub(r"[^a-z0-9]+", "_", account.strip().lower()).strip("_")
    return f"{slug}.db"


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def ensure_indexes(account_dbs: list):
    """Apply any missing indexes to all existing account databases."""
    _index_sql = """
        CREATE INDEX IF NOT EXISTS idx_tx_ticker  ON transactions(ticker);
        CREATE INDEX IF NOT EXISTS idx_tx_account ON transactions(account);
        CREATE INDEX IF NOT EXISTS idx_tx_type    ON transactions(transaction_type);
        CREATE INDEX IF NOT EXISTS idx_tx_date    ON transactions(activity_date);
    """
    for db_path in account_dbs:
        try:
            conn = sqlite3.connect(db_path)
            conn.executescript(_index_sql)
            conn.commit()
            conn.close()
        except Exception:
            pass


def register_account(conn: sqlite3.Connection, account_name: str):
    """Add account to the accounts table if it doesn't already exist."""
    conn.execute(
        "INSERT OR IGNORE INTO accounts (account_name) VALUES (?)", (account_name,)
    )
    conn.commit()


def list_accounts(db_path: str) -> list:
    """Return all account names stored in a given database."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT account_name FROM accounts ORDER BY account_name"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_amount(value) -> float | None:
    """Convert RH amount strings like '$1,241.72' or '($619.20)' to float."""
    if pd.isna(value) or str(value).strip() == "":
        return None
    s = str(value).strip()
    negative = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[(),$]", "", s).replace(",", "")
    try:
        result = float(s)
        return -result if negative else result
    except ValueError:
        return None


def parse_price(value) -> float | None:
    """Convert RH price strings like '$248.35' to float."""
    if pd.isna(value) or str(value).strip() == "":
        return None
    s = re.sub(r"[$,]", "", str(value).strip())
    try:
        return float(s)
    except ValueError:
        return None


def parse_quantity(value) -> float | None:
    if pd.isna(value) or str(value).strip() == "":
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def clean_description(value) -> str:
    """Strip embedded newlines and extra whitespace from description field."""
    if pd.isna(value):
        return ""
    return " | ".join(part.strip() for part in str(value).split("\n") if part.strip())


def make_hash(row: dict) -> str:
    """
    Deterministic SHA-256 hash from the fields that uniquely identify a transaction.
    Deduplicates rows across overlapping CSV exports.
    """
    key = "|".join([
        str(row.get("activity_date", "")),
        str(row.get("ticker", "")),
        str(row.get("trans_code", "")),
        str(row.get("quantity", "")),
        str(row.get("amount", "")),
    ])
    return hashlib.sha256(key.encode()).hexdigest()


def resolve_transaction_type(trans_code: str, description: str, amount: float | None) -> str:
    """
    Map a trans_code to a normalized transaction_type.

    Special cases:
      - 'Buy' rows with 'Dividend Reinvestment' in description → dividend_reinvestment
      - 'ACH' direction is inferred from amount sign:
            amount >= 0  →  deposit
            amount < 0   →  withdrawal
      - Blank/null trans_code  →  'no_trans_code'  (flagged for review)
    """
    if not trans_code:
        return "no_trans_code"

    # DRIP disguised as a Buy
    if trans_code == "Buy" and DRIP_MARKER in description:
        return "dividend_reinvestment"

    # ACH direction from amount sign
    if trans_code == "ACH":
        if amount is not None and amount < 0:
            return "withdrawal"
        return "deposit"

    mapped = TRANS_CODE_MAP.get(trans_code)
    if mapped is None and trans_code in TRANS_CODE_MAP:
        # Code is in map but explicitly None (shouldn't happen after ACH check above)
        return "unknown"
    return mapped if mapped is not None else "unknown"


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

EXPECTED_COLUMNS = {
    "Activity Date", "Process Date", "Settle Date",
    "Instrument", "Description", "Trans Code",
    "Quantity", "Price", "Amount",
}


def load_csv(csv_path: str) -> pd.DataFrame:
    """
    Load a Robinhood CSV export.

    RH descriptions often contain embedded newlines inside quoted fields
    (e.g. "Amazon\\nCUSIP: 023135106\\nDividend Reinvestment"). Pandas'
    C and Python engines both choke on these. We use Python's stdlib csv
    module, which handles RFC-4180 quoted newlines correctly, then hand
    the result to pandas for convenience.
    """
    try:
        import csv as csv_mod, io
        with open(csv_path, "r", newline="", encoding="utf-8-sig") as f:
            raw = f.read()
        reader = csv_mod.reader(io.StringIO(raw))
        rows = list(reader)
    except Exception as e:
        raise ValueError(f"Could not read {csv_path}: {e}") from e

    if not rows:
        raise ValueError(f"{csv_path} appears to be empty.")

    headers = rows[0]
    # RH appends a disclaimer footer with a different column count n/a drop it
    data_rows = [r for r in rows[1:] if len(r) == len(headers)]
    df = pd.DataFrame(data_rows, columns=headers)

    missing = EXPECTED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            f"{csv_path} is missing expected columns: {missing}\n"
            f"Found: {list(df.columns)}"
        )

    # Replace empty strings with NaN, then drop completely empty rows
    df = df.replace("", pd.NA).dropna(how="all")
    return df


# ---------------------------------------------------------------------------
# Import pipeline
# ---------------------------------------------------------------------------

def import_csv(conn: sqlite3.Connection, csv_path: str, account: str = "Default") -> dict:
    source_file = Path(csv_path).name
    register_account(conn, account)
    df = load_csv(csv_path)

    stats = {"inserted": 0, "duplicates": 0, "unknown": 0, "errors": 0}
    seen_hashes: dict[str, int] = {}  # base_hash -> count seen in this file

    for _, row in df.iterrows():
        trans_code = str(row.get("Trans Code", "")).strip() if not pd.isna(row.get("Trans Code", pd.NA)) else ""
        description = clean_description(row.get("Description", ""))
        ticker = str(row.get("Instrument", "")).strip() if not pd.isna(row.get("Instrument", pd.NA)) else None
        activity_date = str(row.get("Activity Date", "")).strip()
        amount = parse_amount(row.get("Amount"))
        quantity = parse_quantity(row.get("Quantity"))
        price = parse_price(row.get("Price"))

        transaction_type = resolve_transaction_type(trans_code, description, amount)

        record = {
            "account":          account,
            "activity_date":    activity_date,
            "process_date":     str(row.get("Process Date", "")).strip(),
            "settle_date":      str(row.get("Settle Date", "")).strip(),
            "ticker":           ticker,
            "description":      description,
            "trans_code":       trans_code,
            "transaction_type": transaction_type,
            "quantity":         quantity,
            "price":            price,
            "amount":           amount,
            "source_file":      source_file,
        }

        base_hash = make_hash(record)
        seen_hashes[base_hash] = seen_hashes.get(base_hash, 0) + 1
        # Append counter for 2nd, 3rd... identical rows in the same file so
        # genuine duplicate trades (same ticker/amount/date) aren't dropped.
        tx_hash = base_hash if seen_hashes[base_hash] == 1 else f"{base_hash}_{seen_hashes[base_hash]}"
        record["tx_hash"] = tx_hash

        # Flag unknown and no_trans_code rows to the review table
        if transaction_type in ("unknown", "no_trans_code"):
            stats["unknown"] += 1
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO unknown_transactions
                       (tx_hash, account, trans_code, raw_row, source_file)
                       VALUES (?, ?, ?, ?, ?)""",
                    (tx_hash, account, trans_code or "(blank)", json.dumps(dict(row), default=str), source_file),
                )
            except Exception as e:
                print(f"  Warning: could not write to unknown_transactions: {e}")

        # Insert into main table; silently skip duplicates
        try:
            conn.execute(
                """INSERT INTO transactions
                   (tx_hash, account, activity_date, process_date, settle_date,
                    ticker, description, trans_code, transaction_type,
                    quantity, price, amount, source_file)
                   VALUES
                   (:tx_hash, :account, :activity_date, :process_date, :settle_date,
                    :ticker, :description, :trans_code, :transaction_type,
                    :quantity, :price, :amount, :source_file)""",
                record,
            )
            stats["inserted"] += 1
        except sqlite3.IntegrityError:
            stats["duplicates"] += 1
        except Exception as e:
            stats["errors"] += 1
            print(f"  Error inserting row: {e}\n  Row: {record}")

    conn.commit()
    return stats


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_summary(conn: sqlite3.Connection):
    print("\n-- Database summary --------------------------------------------------")

    rows = conn.execute(
        """SELECT transaction_type, COUNT(*) as n, SUM(amount) as total
           FROM transactions
           GROUP BY transaction_type
           ORDER BY n DESC"""
    ).fetchall()
    print(f"  {'Type':<28} {'Count':>6}  {'Total amount':>14}")
    print(f"  {'-'*28} {'-'*6}  {'-'*14}")
    for t, n, total in rows:
        total_str = f"${total:,.2f}" if total is not None else "n/a"
        print(f"  {t:<28} {n:>6}  {total_str:>14}")

    unknown_count = conn.execute("SELECT COUNT(*) FROM unknown_transactions").fetchone()[0]
    if unknown_count:
        print(f"\n  WARNING:  {unknown_count} row(s) flagged for review (unknown or blank trans code).")
        codes = conn.execute(
            "SELECT trans_code, COUNT(*) FROM unknown_transactions GROUP BY trans_code"
        ).fetchall()
        for code, n in codes:
            label = "(blank)" if code == "(blank)" else f"'{code}'"
            print(f"     {label} x {n} n/a add to TRANS_CODE_MAP in Import.py")

    total_rows = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    print(f"\n  Total rows in database: {total_rows}")
    print("----------------------------------------------------------------------\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Import Robinhood CSV exports into a SQLite database."
    )
    parser.add_argument("files", nargs="+", help="One or more Robinhood CSV files to import")
    parser.add_argument(
        "--db", default=None,
        help=(
            "Path to SQLite database file (created if it does not exist). "
            "If omitted, auto-named from the account: 'Michael IRA' → michael_ira.db"
        ),
    )
    parser.add_argument(
        "--account", default=None,
        help="Account name to assign to these transactions (e.g. 'Michael IRA')"
    )
    args = parser.parse_args()

    account = args.account
    if not account:
        account = input("Account name for these transactions (e.g. 'Michael IRA'): ").strip()
        if not account:
            account = "Default"

    db_path = args.db or account_to_db_name(account)

    print(f"Database: {db_path}")
    print(f"Account:  {account}")
    conn = init_db(db_path)

    for csv_path in args.files:
        print(f"\nImporting: {csv_path}")
        try:
            stats = import_csv(conn, str(csv_path), account=account)
            print(
                f"  Inserted: {stats['inserted']}  |  "
                f"Duplicates skipped: {stats['duplicates']}  |  "
                f"Flagged for review: {stats['unknown']}  |  "
                f"Errors: {stats['errors']}"
            )
        except ValueError as e:
            print(f"  SKIPPED n/a {e}")

    print_summary(conn)
    conn.close()


if __name__ == "__main__":
    main()
