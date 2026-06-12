"""
Calculations.py
===============
Portfolio math for the dashboard.  Works with the database structure
created by Import.py (account DBs) and Prices.py (prices.db).

Public API:
    get_account_dbs()                         -> list of db paths
    get_account_names(account_dbs)            -> {db: [account, ...]}
    get_portfolio_summary(account_dbs, ...)   -> dict of top-level metrics
    get_current_positions(account_dbs, ...)   -> list of per-ticker dicts
    get_daily_values(account_dbs, ...)        -> dict for the performance chart
    get_transactions_df(account_dbs, ...)     -> pd.DataFrame for the table
"""

import bisect
import sqlite3
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional
import math
import pandas as pd


# ---------------------------------------------------------------------------
# IRR / Money-Weighted Return solver
# ---------------------------------------------------------------------------

def _compute_mwr(cash_flows: list) -> Optional[float]:
    """
    Compute annualized IRR from a list of (t_years, amount) pairs.
    Outflows (investments) are negative; inflows (returns) are positive.
    Returns the annualized rate as a decimal (e.g. 0.25 = 25%), or None.
    """
    if len(cash_flows) < 2:
        return None

    def npv(r: float) -> float:
        total = 0.0
        for t, cf in cash_flows:
            base = 1.0 + r
            if base <= 0:
                return float("inf")
            total += cf / (base ** max(t, 0.0))
        return total

    def npv_d(r: float, h: float = 1e-6) -> float:
        return (npv(r + h) - npv(r - h)) / (2 * h)

    # Newton's method with a few starting points
    for r0 in [0.1, 0.5, -0.1, 2.0, -0.5]:
        r = r0
        try:
            for _ in range(100):
                f  = npv(r)
                fp = npv_d(r)
                if abs(fp) < 1e-12:
                    break
                step = f / fp
                r_new = r - step
                # keep search in sensible range
                r_new = max(-0.9999, min(r_new, 100.0))
                if abs(r_new - r) < 1e-8:
                    if abs(npv(r_new)) < 1.0:   # residual check
                        result = r_new
                        if abs(result) < 50:     # cap absurd values
                            return result
                    break
                r = r_new
        except Exception:
            continue

    # Bisection fallback
    lo, hi = -0.9999, 20.0
    try:
        vlo = npv(lo)
        if npv(lo) * npv(hi) > 0:
            for hi_try in [5.0, 2.0, 1.0, 0.5, 0.1]:
                if vlo * npv(hi_try) <= 0:
                    hi = hi_try
                    break
            else:
                return None
        for _ in range(150):
            mid = (lo + hi) / 2.0
            if npv(lo) * npv(mid) <= 0:
                hi = mid
            else:
                lo = mid
            if hi - lo < 1e-8:
                break
        result = (lo + hi) / 2.0
        return result if abs(result) < 50 else None
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Transaction type sets
# ---------------------------------------------------------------------------

# Add shares to FIFO lots
BUY_TYPES = frozenset({"buy", "crypto_buy", "dividend_reinvestment"})

# Remove shares from FIFO lots
SELL_TYPES = frozenset({"sell", "crypto_sell", "liquidation"})

# Shares arrive with no cash cost recorded (ACATS in — use market price on date)
TRANSFER_IN_TYPES = frozenset({"transfer_in"})

# Transaction types whose amounts are already captured by FUTSWP (net sweep).
# Excluding them from cash prevents double-counting.
CASH_EXCLUDED_TYPES = frozenset({"event_contract_buy", "event_contract_payout"})

# External cash flows that count as deposits for TWR
DEPOSIT_TYPES = frozenset({
    "direct_deposit", "deposit", "asset_transfer",
    "bonus_credit", "miscellaneous_income",
})

# External cash outflows for TWR
WITHDRAWAL_TYPES = frozenset({"subscription_fee"})


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

@lru_cache(maxsize=4096)
def _parse_date(s: str) -> date:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unknown date format: {s!r}")


@lru_cache(maxsize=4096)
def _parse_date_safe(s: str) -> Optional[date]:
    try:
        return _parse_date(s)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Account DB discovery
# ---------------------------------------------------------------------------

def get_account_dbs(directory: str = ".") -> list:
    return sorted(
        str(p) for p in Path(directory).glob("*.db")
        if p.name != "prices.db"
    )


def get_account_names(account_dbs: list) -> dict:
    """Return {db_path: [account_name, ...]}."""
    result = {}
    for db_path in account_dbs:
        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT account_name FROM accounts ORDER BY account_name"
            ).fetchall()
            conn.close()
            result[db_path] = [r[0] for r in rows]
        except Exception:
            result[db_path] = []
    return result


# ---------------------------------------------------------------------------
# Price lookups (prices.db)
# ---------------------------------------------------------------------------

def get_latest_prices(tickers: list, prices_db: str = "prices.db") -> dict:
    """Return {ticker: latest adj_close}."""
    if not Path(prices_db).exists() or not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    conn = sqlite3.connect(prices_db)
    rows = conn.execute(
        f"""SELECT p.ticker, p.adj_close
            FROM prices p
            JOIN (SELECT ticker, MAX(date) AS max_date
                  FROM prices WHERE ticker IN ({placeholders})
                  GROUP BY ticker) latest
              ON p.ticker = latest.ticker AND p.date = latest.max_date""",
        tickers,
    ).fetchall()
    conn.close()
    return {r[0]: float(r[1]) for r in rows}


@lru_cache(maxsize=4096)
def get_price_on_date(ticker: str, target: date, prices_db: str = "prices.db") -> Optional[float]:
    """Closest price on or before target date."""
    if not Path(prices_db).exists():
        return None
    conn = sqlite3.connect(prices_db)
    row = conn.execute(
        """SELECT adj_close FROM prices
           WHERE ticker = ? AND date <= ?
           ORDER BY date DESC LIMIT 1""",
        (ticker, target.isoformat()),
    ).fetchone()
    conn.close()
    return float(row[0]) if row else None


def get_ticker_names(tickers: list, prices_db: str = "prices.db") -> dict:
    """Return {ticker: long_name} using prices.db cache, fetching any missing names."""
    from Prices import fetch_ticker_names
    return fetch_ticker_names(tickers, prices_db)


def get_52week_ranges(tickers: list, prices_db: str = "prices.db") -> dict:
    """Return {ticker: {low, high, current}} over the trailing 52 weeks."""
    if not Path(prices_db).exists() or not tickers:
        return {}
    cutoff       = (date.today() - timedelta(days=365)).isoformat()
    placeholders = ",".join("?" * len(tickers))
    conn = sqlite3.connect(prices_db)

    range_rows = conn.execute(
        f"""SELECT ticker, MIN(adj_close), MAX(adj_close)
            FROM prices WHERE ticker IN ({placeholders}) AND date >= ?
            GROUP BY ticker""",
        [*tickers, cutoff],
    ).fetchall()

    cur_rows = conn.execute(
        f"""SELECT p.ticker, p.adj_close
            FROM prices p
            JOIN (SELECT ticker, MAX(date) AS max_date
                  FROM prices WHERE ticker IN ({placeholders})
                  GROUP BY ticker) latest
              ON p.ticker = latest.ticker AND p.date = latest.max_date""",
        tickers,
    ).fetchall()
    conn.close()

    cur_prices = {r[0]: float(r[1]) for r in cur_rows}
    return {
        ticker: {
            "low":     float(lo),
            "high":    float(hi),
            "current": cur_prices.get(ticker),
        }
        for ticker, lo, hi in range_rows
        if lo is not None
    }


def get_price_series(tickers: list, start: str, end: str,
                     prices_db: str = "prices.db") -> dict:
    """Return {ticker: {date_str: price}} for the given range."""
    if not Path(prices_db).exists() or not tickers:
        return {}
    conn = sqlite3.connect(prices_db)

    # Load delisted tickers so we can inject their final price
    try:
        _dl_rows = conn.execute(
            "SELECT ticker, final_price FROM delisted_tickers"
        ).fetchall()
        delisted = {r[0]: float(r[1]) for r in _dl_rows}
    except Exception:
        delisted = {}

    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""SELECT ticker, date, adj_close FROM prices
            WHERE ticker IN ({placeholders}) AND date >= ? AND date <= ?
            ORDER BY ticker, date""",
        [*tickers, start, end],
    ).fetchall()
    conn.close()

    result: dict = {}
    for ticker_r, date_str, price in rows:
        result.setdefault(ticker_r, {})[date_str] = float(price)

    # Inject delisted final prices for tickers with no live data
    for ticker in tickers:
        if ticker not in result and ticker in delisted:
            result[ticker] = {start: delisted[ticker], end: delisted[ticker]}

    return result


def _build_split_schedule(ticker_txns: dict) -> dict:
    """
    Return {ticker: [(date_str, ratio), ...]} sorted chronologically.

    Yahoo Finance returns split-adjusted adj_close for ALL historical dates.
    When shares in the FIFO are pre-split but the stored price is post-split-
    adjusted, the product (shares × price) is inflated by the split factor.
    This schedule lets callers reverse-apply future splits to the stored price
    so that pre-split shares × corrected price equals the real historical value.
    """
    schedule = {}
    for ticker, txns in ticker_txns.items():
        splits = [
            ((_parse_date_safe(tx["activity_date"]) or date.min).isoformat(),
             float(tx["quantity"]))
            for tx in txns
            if tx["transaction_type"] == "stock_split"
            and tx.get("quantity") and float(tx["quantity"]) > 0
        ]
        if splits:
            schedule[ticker] = sorted(splits, key=lambda x: x[0])
    return schedule


def _split_price_factor(split_schedule: list, ds: str) -> float:
    """
    Return the cumulative factor to multiply a stored adj_close price by so it
    reflects the share units that existed on date `ds`.

    For each split that occurs STRICTLY AFTER `ds`, the stored adj_close has
    already been multiplied by 1/ratio (reverse split) or ratio (forward split)
    relative to the pre-split price.  Multiplying by ratio undoes that adjustment
    for dates before the split.

    Example: INO 1-for-12 reverse split (ratio=0.083333) on 2024-01-24.
      - adj_close on 2021-01-04 stored as ~$115 (actual price × 12).
      - For ds < 2024-01-24: factor = 0.083333, so effective price = 115 × 0.083333 ≈ $9.58.
      - For ds >= 2024-01-24: factor = 1.0 (no future splits).
    """
    factor = 1.0
    for split_date, ratio in split_schedule:
        if split_date > ds:
            factor *= ratio
    return factor


def _precompute_split_factors(schedule: list, sorted_dates: list) -> list:
    """
    Return one factor per entry in sorted_dates, computed in O(n_splits + n_dates)
    instead of O(n_splits × n_dates).

    Starts with all splits "future" (all ratios applied), then divides each ratio
    out as its split date is reached while iterating forward through sorted_dates.
    """
    if not schedule or not sorted_dates:
        return [1.0] * len(sorted_dates)

    current = 1.0
    for _, ratio in schedule:
        current *= ratio

    factors    = []
    split_idx  = 0
    n_splits   = len(schedule)
    for ds in sorted_dates:
        while split_idx < n_splits and schedule[split_idx][0] <= ds:
            current /= schedule[split_idx][1]
            split_idx += 1
        factors.append(current)
    return factors


# ---------------------------------------------------------------------------
# Transaction fetching
# ---------------------------------------------------------------------------

def _get_transactions(account_dbs: list, account: str = None,
                      ticker: str = None) -> list:
    rows = []
    for db_path in account_dbs:
        if not Path(db_path).exists():
            continue
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conditions, params = [], []
        if account:
            conditions.append("account = ?")
            params.append(account)
        if ticker:
            conditions.append("ticker = ?")
            params.append(ticker.upper())
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        result = conn.execute(
            f"SELECT * FROM transactions {where} ORDER BY activity_date",
            params,
        ).fetchall()
        rows.extend(dict(r) for r in result)
        conn.close()
    return rows


# ---------------------------------------------------------------------------
# ACATI + ITRF passthrough detection
# ---------------------------------------------------------------------------

def _acati_itrf_passthrough_hashes(transactions: list) -> set:
    """
    When an ACATI (transfer_in with qty) is followed within 3 days by an ITRF
    (internal_transfer, same ticker, no qty, no amount), the shares just passed
    through — Robinhood moved them to an IRA or sub-account immediately.

    Return the tx_hash values of both sides so FIFO can skip them entirely.
    Pre-existing lots for that ticker (bought before the ACATI) are preserved.
    """
    # Collect ITRF marker rows: {ticker: [date, ...]}
    itrf_events: dict = {}
    for tx in transactions:
        if (tx["transaction_type"] == "internal_transfer"
                and tx.get("ticker")
                and not (tx.get("quantity") or 0)
                and not (tx.get("amount") or 0)):
            d = _parse_date_safe(tx["activity_date"])
            itrf_events.setdefault(tx["ticker"], []).append((d, tx.get("tx_hash")))

    if not itrf_events:
        return set()

    skip: set = set()
    for tx in transactions:
        if (tx["transaction_type"] == "transfer_in"
                and tx.get("ticker")
                and (tx.get("quantity") or 0) > 0):
            ticker  = tx["ticker"]
            tx_date = _parse_date_safe(tx["activity_date"])
            for itrf_date, itrf_hash in itrf_events.get(ticker, []):
                if tx_date and itrf_date and 0 <= (itrf_date - tx_date).days <= 3:
                    skip.add(tx.get("tx_hash"))
                    skip.add(itrf_hash)

    return skip


# ---------------------------------------------------------------------------
# FIFO engine
# ---------------------------------------------------------------------------

def _apply_tx_to_fifo(tx: dict, fifo: list, prices_db: str = "prices.db",
                      closed_lots: list = None):
    """Apply one transaction to a mutable FIFO lot list.

    If closed_lots is provided, realized gains from sells are appended to it
    as (qty, basis, proceeds, gain) tuples.
    """
    tx_type = tx["transaction_type"]
    qty     = float(tx["quantity"] or 0)
    price   = float(tx["price"]   or 0)
    amount  = float(tx["amount"]  or 0)

    if tx_type in BUY_TYPES and qty > 0:
        cost = price if price > 0 else (abs(amount) / qty if amount != 0 else 0.0)
        fifo.append([qty, cost, tx.get("activity_date", "")])

    elif tx_type in TRANSFER_IN_TYPES and qty > 0:
        # No original cost basis available; use market price on transfer date
        tx_date = _parse_date_safe(tx.get("activity_date", ""))
        cost = 0.0
        if tx_date and tx.get("ticker"):
            fetched = get_price_on_date(tx["ticker"], tx_date, prices_db)
            if fetched:
                cost = fetched
        fifo.append([qty, cost, tx.get("activity_date", "")])

    elif tx_type in SELL_TYPES:
        if qty > 0:
            to_sell = qty
        elif price > 0 and amount != 0:
            to_sell = abs(amount) / price
        else:
            return
        proc_per  = price if price > 0 else (abs(amount) / to_sell if to_sell > 0 else 0)
        remaining = to_sell
        while remaining > 1e-9 and fifo:
            lot_qty, lot_cost = fifo[0][0], fifo[0][1]
            if lot_qty <= remaining + 1e-9:
                if closed_lots is not None:
                    proceeds = lot_qty * proc_per
                    basis    = lot_qty * lot_cost
                    closed_lots.append((lot_qty, basis, proceeds, proceeds - basis))
                remaining -= lot_qty
                fifo.pop(0)
            else:
                if closed_lots is not None:
                    proceeds = remaining * proc_per
                    basis    = remaining * lot_cost
                    closed_lots.append((remaining, basis, proceeds, proceeds - basis))
                fifo[0][0] -= remaining
                remaining = 0

    elif tx_type == "stock_split" and qty > 0:
        # qty holds the split ratio (e.g. 0.05 for a 1-for-20 reverse split,
        # 2.0 for a 2-for-1 forward split).  Adjust every open lot in place.
        for lot in fifo:
            lot[0] *= qty          # new share count
            lot[1] /= qty          # new cost per share (total basis unchanged)

    elif tx_type == "share_exchange":
        fifo.clear()


def _build_fifo(transactions: list, prices_db: str = "prices.db") -> tuple:
    """Full FIFO for one ticker. Returns (open_lots, closed_lots)."""
    sorted_txns = sorted(
        transactions,
        key=lambda r: (_parse_date_safe(r["activity_date"]) or date.min),
    )
    skip        = _acati_itrf_passthrough_hashes(sorted_txns)
    open_lots   = []
    closed_lots = []
    for tx in sorted_txns:
        if tx.get("tx_hash") in skip:
            continue
        _apply_tx_to_fifo(tx, open_lots, prices_db, closed_lots)
    return open_lots, closed_lots


# ---------------------------------------------------------------------------
# Current positions
# ---------------------------------------------------------------------------

def _compute_positions(all_txns: list, prices_db: str = "prices.db") -> list:
    """Build the positions list from a pre-fetched transaction list."""
    relevant = BUY_TYPES | SELL_TYPES | TRANSFER_IN_TYPES | {"share_exchange"}
    tickers  = sorted(set(
        tx["ticker"] for tx in all_txns
        if tx.get("ticker") and tx["transaction_type"] in relevant
    ))

    latest_prices = get_latest_prices(tickers, prices_db)
    positions = []

    for ticker in tickers:
        ticker_txns         = [tx for tx in all_txns if tx.get("ticker") == ticker]
        open_lots, closed   = _build_fifo(ticker_txns, prices_db)

        shares_held     = sum(lot[0] for lot in open_lots)
        cost_basis_held = sum(lot[0] * lot[1] for lot in open_lots)
        avg_cost        = cost_basis_held / shares_held if shares_held > 1e-9 else 0.0
        realized_gain   = sum(cl[3] for cl in closed)
        total_proceeds  = sum(cl[2] for cl in closed)
        total_cost_sold = sum(cl[1] for cl in closed)

        dividends      = sum(float(tx["amount"] or 0) for tx in ticker_txns
                             if tx["transaction_type"] in
                             {"dividend", "capital_gains_distribution"})
        lending_income = sum(float(tx["amount"] or 0) for tx in ticker_txns
                             if tx["transaction_type"] == "lending_income")

        live_price   = latest_prices.get(ticker)
        market_value = shares_held * live_price if live_price and shares_held > 1e-9 else None
        unrealized   = market_value - cost_basis_held if market_value is not None else None
        unrlzd_pct   = (unrealized / cost_basis_held * 100
                        if unrealized is not None and cost_basis_held > 0 else None)

        total_gain = realized_gain + dividends + lending_income
        if unrealized is not None:
            total_gain += unrealized

        positions.append({
            "ticker":          ticker,
            "shares_held":     round(shares_held, 6),
            "avg_cost":        round(avg_cost, 4),
            "cost_basis":      round(cost_basis_held, 2),
            "live_price":      round(live_price, 4)  if live_price   is not None else None,
            "market_value":    round(market_value, 2) if market_value is not None else None,
            "unrealized_gain": round(unrealized, 2)   if unrealized  is not None else None,
            "unrealized_pct":  round(unrlzd_pct, 2)   if unrlzd_pct  is not None else None,
            "realized_gain":   round(realized_gain, 2),
            "total_proceeds":  round(total_proceeds, 2),
            "total_cost_sold": round(total_cost_sold, 2),
            "dividends":       round(dividends, 2),
            "lending_income":  round(lending_income, 2),
            "total_gain":      round(total_gain, 2),
            "open_lots":       [(round(q, 6), round(c, 4), d) for q, c, d in open_lots],
            "closed_lots":     [(round(q, 6), round(b, 2), round(p, 2), round(g, 2))
                                for q, b, p, g in closed],
        })

    open_pos   = sorted([p for p in positions if p["shares_held"] > 1e-9],
                        key=lambda p: p["market_value"] or p["cost_basis"], reverse=True)
    closed_pos = sorted([p for p in positions if p["shares_held"] <= 1e-9],
                        key=lambda p: p["realized_gain"], reverse=True)
    return open_pos + closed_pos


def get_current_positions(account_dbs: list, prices_db: str = "prices.db",
                          account: str = None) -> list:
    """All positions (open + closed) with FIFO cost basis and current prices."""
    return _compute_positions(
        _get_transactions(account_dbs, account=account), prices_db
    )


# ---------------------------------------------------------------------------
# Cash balance
# ---------------------------------------------------------------------------

def get_cash_balance(account_dbs: list, account: str = None,
                     as_of: date = None) -> float:
    """
    Net cash = sum of all signed transaction amounts.
    Portfolio value = cash + stock market value (no double-counting because
    buys subtract from cash and add to stock holdings simultaneously).
    """
    total = 0.0
    for db_path in account_dbs:
        if not Path(db_path).exists():
            continue
        conn = sqlite3.connect(db_path)
        excluded = ",".join(f"'{t}'" for t in CASH_EXCLUDED_TYPES)
        conditions = [
            "amount IS NOT NULL",
            f"transaction_type NOT IN ({excluded})",
        ]
        params = []
        if account:
            conditions.append("account = ?")
            params.append(account)
        rows = conn.execute(
            f"SELECT activity_date, amount FROM transactions WHERE {' AND '.join(conditions)}",
            params,
        ).fetchall()
        conn.close()
        for row in rows:
            if as_of:
                tx_date = _parse_date_safe(row[0])
                if tx_date and tx_date > as_of:
                    continue
            total += float(row[1])
    return round(total, 2)


# ---------------------------------------------------------------------------
# Portfolio summary
# ---------------------------------------------------------------------------

def get_portfolio_summary(account_dbs: list, prices_db: str = "prices.db",
                          account: str = None) -> dict:
    all_txns   = _get_transactions(account_dbs, account=account)
    positions  = _compute_positions(all_txns, prices_db)
    open_pos   = [p for p in positions if p["shares_held"] > 1e-9]

    total_market_value = sum(p["market_value"]    or 0 for p in open_pos)
    total_cost_basis   = sum(p["cost_basis"]          for p in open_pos)
    total_unrealized   = sum(p["unrealized_gain"] or 0 for p in open_pos)
    total_realized     = sum(p["realized_gain"]       for p in positions)
    total_dividends    = sum(p["dividends"]            for p in positions)
    total_lending      = sum(p["lending_income"]       for p in positions)

    cash = round(sum(
        float(tx["amount"] or 0) for tx in all_txns
        if tx.get("amount") is not None
        and tx["transaction_type"] not in CASH_EXCLUDED_TYPES
    ), 2)
    account_total = total_market_value + cash
    total_gain    = total_unrealized + total_realized + total_dividends + total_lending

    total_deposited = sum(
        float(tx["amount"] or 0) for tx in all_txns
        if tx["transaction_type"] in DEPOSIT_TYPES and float(tx["amount"] or 0) > 0
    )

    unrlzd_pct  = (total_unrealized / total_cost_basis * 100
                   if total_cost_basis > 0 else None)
    return_pct  = (total_gain / total_deposited * 100
                   if total_deposited > 0 else None)

    return {
        "as_of":              date.today().isoformat(),
        "account_total":      round(account_total, 2),
        "cash_balance":       round(cash, 2),
        "total_market_value": round(total_market_value, 2),
        "total_cost_basis":   round(total_cost_basis, 2),
        "total_unrealized":   round(total_unrealized, 2),
        "unrealized_pct":     round(unrlzd_pct, 2)  if unrlzd_pct  is not None else None,
        "total_realized":     round(total_realized, 2),
        "total_dividends":    round(total_dividends, 2),
        "total_lending":      round(total_lending, 2),
        "total_gain":         round(total_gain, 2),
        "total_deposited":    round(total_deposited, 2),
        "return_pct":         round(return_pct, 2)   if return_pct  is not None else None,
        "open_positions":     open_pos,
        "closed_positions":   [p for p in positions if p["shares_held"] <= 1e-9],
        "all_positions":      positions,
    }


# ---------------------------------------------------------------------------
# Daily portfolio values (performance chart + TWR)
# ---------------------------------------------------------------------------

def get_daily_values(account_dbs: list, prices_db: str = "prices.db",
                     start_date: str = None, end_date: str = None,
                     account: str = None) -> dict:
    """
    Daily portfolio value = stock holdings + cash balance.

    TWR uses the daily chain-link method (industry standard):
        daily_return  = (V_today - V_yesterday - CF_today) / V_yesterday
        TWR           = product(1 + daily_return) - 1
    where CF is external cash flows only (deposits/withdrawals).
    Annualised TWR is only shown for periods >= 30 days.
    """
    today     = date.today()
    end       = datetime.strptime(end_date,   "%Y-%m-%d").date() if end_date   else today
    all_txns  = _get_transactions(account_dbs, account=account)

    if not all_txns:
        return _empty_chart()

    first_tx = min(
        _parse_date_safe(tx["activity_date"]) or today for tx in all_txns
    )
    start = max(
        datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else first_tx,
        first_tx,
    )
    start_str, end_str = start.isoformat(), end.isoformat()

    # Tickers that had share activity
    share_types = BUY_TYPES | SELL_TYPES | TRANSFER_IN_TYPES | {"share_exchange"}
    tickers = sorted(set(
        tx["ticker"] for tx in all_txns
        if tx.get("ticker") and tx["transaction_type"] in share_types
    ))

    price_series = get_price_series(tickers, start_str, end_str, prices_db)

    # Union of all trading dates in range
    all_dates = set()
    for ser in price_series.values():
        all_dates.update(ser.keys())
    sorted_dates = sorted(d for d in all_dates if start_str <= d <= end_str)

    if not sorted_dates:
        return _empty_chart()

    first_chart_date = _parse_date(sorted_dates[0])

    # Remove ACATI+ITRF passthroughs before building per-ticker lists
    all_skip = _acati_itrf_passthrough_hashes(all_txns)

    # Group transactions by ticker
    ticker_txns = {
        t: sorted(
            [tx for tx in all_txns
             if tx.get("ticker") == t and tx.get("tx_hash") not in all_skip],
            key=lambda r: (_parse_date_safe(r["activity_date"]) or date.min),
        )
        for t in tickers
    }

    # ── Split schedule (for price correction before split dates) ─────
    split_schedule = _build_split_schedule(ticker_txns)

    # ── Initialise FIFO state to just before chart start ──────────────
    ticker_fifo_state = {}
    ticker_idx_state  = {}
    for ticker in tickers:
        txns = ticker_txns[ticker]
        fifo, idx = [], len(txns)
        for i, tx in enumerate(txns):
            tx_date = _parse_date_safe(tx["activity_date"])
            if tx_date is None or tx_date >= first_chart_date:
                idx = i
                break
            _apply_tx_to_fifo(tx, fifo, prices_db)
        ticker_fifo_state[ticker] = fifo
        ticker_idx_state[ticker]  = idx

    # ── Initialise cash state to just before chart start ─────────────
    cash_txns = sorted(
        [tx for tx in all_txns
         if tx.get("amount") is not None
         and tx["transaction_type"] not in CASH_EXCLUDED_TYPES],
        key=lambda r: (_parse_date_safe(r["activity_date"]) or date.min),
    )
    cum_cash, cash_idx = 0.0, len(cash_txns)
    for i, tx in enumerate(cash_txns):
        tx_date = _parse_date_safe(tx["activity_date"])
        if tx_date is None or tx_date >= first_chart_date:
            cash_idx = i
            break
        cum_cash += float(tx["amount"])

    # ── External cash flows by date (for TWR) ────────────────────────
    # CF = every amount that is NOT a trading action (buy/sell).
    # This neutralises deposits, withdrawals, fees, transfers, ABIP
    # reversals, FUTSWP sweeps, etc. so the TWR only reflects price
    # changes and realised gains from actual trades.
    _trading_types = BUY_TYPES | SELL_TYPES | CASH_EXCLUDED_TYPES
    cf_by_date: dict = {}
    for tx in all_txns:
        if (tx.get("amount") is not None
                and tx["transaction_type"] not in _trading_types):
            ds = (_parse_date_safe(tx["activity_date"]) or date.min).isoformat()
            cf_by_date[ds] = cf_by_date.get(ds, 0.0) + float(tx["amount"])

    # ── Pre-compute per-ticker price date lists (for O(log n) lookup) ──
    ticker_price_dates = {
        t: sorted(price_series.get(t, {}).keys()) for t in tickers
    }

    # ── Pre-compute cumulative split factors per ticker per date ─────
    ticker_split_factors: dict = {}
    for ticker, schedule in split_schedule.items():
        ticker_split_factors[ticker] = _precompute_split_factors(schedule, sorted_dates)

    # ── Day-by-day loop ───────────────────────────────────────────────
    portfolio_values, stock_values, cash_values, cost_basis_vals = [], [], [], []

    for date_idx, ds in enumerate(sorted_dates):
        # Advance cash
        while cash_idx < len(cash_txns):
            tx = cash_txns[cash_idx]
            tx_date = _parse_date_safe(tx["activity_date"])
            if tx_date is None or tx_date.isoformat() > ds:
                break
            cum_cash += float(tx["amount"])
            cash_idx += 1

        # Advance FIFO and price each ticker
        day_stock = 0.0
        day_cost  = 0.0
        for ticker in tickers:
            fifo = ticker_fifo_state[ticker]
            idx  = ticker_idx_state[ticker]
            txns = ticker_txns[ticker]

            while idx < len(txns):
                tx = txns[idx]
                tx_date = _parse_date_safe(tx["activity_date"])
                if tx_date is None or tx_date.isoformat() > ds:
                    break
                _apply_tx_to_fifo(tx, fifo, prices_db)
                idx += 1
            ticker_idx_state[ticker] = idx

            shares    = sum(lot[0] for lot in fifo)
            cost      = sum(lot[0] * lot[1] for lot in fifo)
            day_cost += cost

            # Forward-fill price via binary search; backward-fill before history start
            pdates = ticker_price_dates[ticker]
            ser    = price_series.get(ticker, {})
            pi     = bisect.bisect_right(pdates, ds) - 1
            price  = ser[pdates[pi]] if pi >= 0 else (ser[pdates[0]] if pdates else 0.0)
            # Correct for split-adjusted prices stored by Yahoo Finance.
            if ticker in ticker_split_factors:
                price *= ticker_split_factors[ticker][date_idx]
            day_stock += shares * price

        portfolio_values.append(round(day_stock + cum_cash, 2))
        stock_values.append(round(day_stock, 2))
        cash_values.append(round(cum_cash, 2))
        cost_basis_vals.append(round(day_cost, 2))

    # ── Daily chain-link TWR ──────────────────────────────────────────
    compound   = 1.0
    twr_series = [0.0]
    for i in range(1, len(sorted_dates)):
        v_prev  = portfolio_values[i - 1]
        v_today = portfolio_values[i]
        cf      = cf_by_date.get(sorted_dates[i], 0.0)
        if v_prev > 1e-6:
            daily_r   = (v_today - v_prev - cf) / v_prev
            compound *= 1 + daily_r
        twr_series.append(round((compound - 1) * 100, 4))

    total_twr  = compound - 1
    total_days = max((end - start).days, 1)
    ann_twr    = ((compound ** (365.0 / total_days)) - 1
                  if total_days >= 30 and compound > 0 else None)

    # Net external flows after day 0 (day-0 already in start_value).
    period_net_flows = round(sum(cf_by_date.get(d, 0.0) for d in sorted_dates[1:]), 2)

    # ── Portfolio MWR (IRR on true external deposits/withdrawals) ─────────
    mwr_pct_port = None
    if sorted_dates and portfolio_values:
        origin_p = _parse_date(sorted_dates[0])
        port_mwr_cfs = []

        if portfolio_values[0] > 0:
            port_mwr_cfs.append((0.0, -portfolio_values[0]))

        # Only true external flows (deposits/withdrawals), not dividends/interest
        for tx in all_txns:
            tx_type = tx["transaction_type"]
            if tx_type not in (DEPOSIT_TYPES | WITHDRAWAL_TYPES):
                continue
            tx_date_ = _parse_date_safe(tx["activity_date"])
            if tx_date_ is None:
                continue
            ds_ = tx_date_.isoformat()
            if ds_ < sorted_dates[0] or ds_ > sorted_dates[-1]:
                continue
            t_yr = max((tx_date_ - origin_p).days / 365.0, 0.0)
            amt  = float(tx.get("amount") or 0)
            # Deposits: positive amount → investor puts money in → negative CF
            # Withdrawals: negative amount → investor takes out → positive CF
            port_mwr_cfs.append((t_yr, -amt))

        T_yr_p = max((end - origin_p).days / 365.0, 1 / 365.0)
        if portfolio_values[-1] > 0:
            port_mwr_cfs.append((T_yr_p, portfolio_values[-1]))

        raw_port_mwr = _compute_mwr(port_mwr_cfs)
        if raw_port_mwr is not None:
            mwr_pct_port = round(raw_port_mwr * 100, 2)

    return {
        "dates":             sorted_dates,
        "values":            portfolio_values,
        "stock_values":      stock_values,
        "cash_values":       cash_values,
        "cost_basis":        cost_basis_vals,
        "twr_series":        twr_series,
        "twr_total":         round(total_twr * 100, 2),
        "twr_annualized":    round(ann_twr * 100, 2) if ann_twr is not None else None,
        "mwr_annualized":    mwr_pct_port,
        "total_days":        total_days,
        "start_value":       portfolio_values[0]  if portfolio_values else 0,
        "end_value":         portfolio_values[-1] if portfolio_values else 0,
        "period_net_flows":  period_net_flows,
    }


def get_ticker_daily_values(ticker: str, account_dbs: list,
                            prices_db: str = "prices.db",
                            start_date: str = None, end_date: str = None,
                            account: str = None) -> dict:
    """
    Daily value for a single ticker position (shares * price).
    TWR uses the same chain-link method, treating buy costs as positive CF
    into the position and sell proceeds as negative CF (money leaving).
    """
    today = date.today()
    end   = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else today

    all_txns = _get_transactions(account_dbs, account=account, ticker=ticker)
    if not all_txns:
        return _empty_chart()

    share_types = BUY_TYPES | SELL_TYPES | TRANSFER_IN_TYPES | {"share_exchange", "stock_split"}
    anchor_txns = [tx for tx in all_txns
                   if tx.get("ticker") == ticker and tx["transaction_type"] in share_types]
    if not anchor_txns:
        return _empty_chart()

    first_tx = min(_parse_date_safe(tx["activity_date"]) or today for tx in anchor_txns)
    start = max(
        datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else first_tx,
        first_tx,
    )
    start_str, end_str = start.isoformat(), end.isoformat()

    price_series = get_price_series([ticker], start_str, end_str, prices_db)
    ser = price_series.get(ticker, {})
    sorted_dates = sorted(d for d in ser if start_str <= d <= end_str)
    if not sorted_dates:
        return _empty_chart()

    first_chart_date = _parse_date(sorted_dates[0])

    all_skip = _acati_itrf_passthrough_hashes(all_txns)
    ticker_txns_sorted = sorted(
        [tx for tx in all_txns
         if tx.get("ticker") == ticker and tx.get("tx_hash") not in all_skip],
        key=lambda r: (_parse_date_safe(r["activity_date"]) or date.min),
    )

    # Initialise FIFO to just before chart start
    fifo, idx = [], 0
    for i, tx in enumerate(ticker_txns_sorted):
        tx_date = _parse_date_safe(tx["activity_date"])
        if tx_date is None or tx_date >= first_chart_date:
            idx = i
            break
        _apply_tx_to_fifo(tx, fifo, prices_db)
    else:
        idx = len(ticker_txns_sorted)

    # Build split schedule and pre-compute per-date factors
    ticker_split_schedule  = _build_split_schedule({ticker: ticker_txns_sorted}).get(ticker, [])
    ticker_split_fac_list  = _precompute_split_factors(ticker_split_schedule, sorted_dates)

    # Pre-sorted price date list for O(log n) forward-fill
    ser_dates = sorted(ser.keys())

    # Pre-period value: shares held before the chart starts (needed for MWR)
    _pre_shares = sum(lot[0] for lot in fifo)
    if _pre_shares > 0:
        _pre_pi = bisect.bisect_right(ser_dates, sorted_dates[0]) - 1
        _pre_px = ser[ser_dates[_pre_pi]] if _pre_pi >= 0 else (ser[ser_dates[0]] if ser_dates else 0.0)
        if ticker_split_schedule:
            _pre_px *= ticker_split_fac_list[0]
        pre_period_value = _pre_shares * _pre_px
    else:
        pre_period_value = 0.0

    # CF by date: buys = positive (money into position), sells = negative
    cf_by_date: dict = {}
    for tx in ticker_txns_sorted:
        tx_type = tx["transaction_type"]
        amount  = float(tx.get("amount") or 0)
        qty     = float(tx.get("quantity") or 0)
        price_  = float(tx.get("price") or 0)
        ds = (_parse_date_safe(tx["activity_date"]) or date.min).isoformat()
        if tx_type in BUY_TYPES:
            cf_by_date[ds] = cf_by_date.get(ds, 0.0) + (-amount if amount != 0
                                                          else qty * price_)
        elif tx_type in TRANSFER_IN_TYPES and qty > 0:
            tx_date_ = _parse_date_safe(tx.get("activity_date", ""))
            cost_ = get_price_on_date(ticker, tx_date_, prices_db) or 0.0
            cf_by_date[ds] = cf_by_date.get(ds, 0.0) + qty * cost_
        elif tx_type in SELL_TYPES:
            cf_by_date[ds] = cf_by_date.get(ds, 0.0) + (-amount if amount != 0
                                                          else -(qty * price_))

    # Transaction-price valuation by date.
    # On days with buy/sell/transfer activity we value the portfolio at the
    # weighted-average transaction price rather than the market close.  This
    # eliminates intraday friction between your trade price and the close and
    # means TWR sub-periods start/end at the price you actually transacted at.
    tx_val_price: dict = {}   # {date_str: weighted_avg_tx_price}
    _tx_qty_acc:  dict = {}
    _tx_cost_acc: dict = {}
    for tx in ticker_txns_sorted:
        tx_type = tx["transaction_type"]
        amount  = float(tx.get("amount") or 0)
        qty     = float(tx.get("quantity") or 0)
        price_  = float(tx.get("price") or 0)
        ds = (_parse_date_safe(tx["activity_date"]) or date.min).isoformat()
        if tx_type in BUY_TYPES and qty > 0:
            cost_ = abs(amount) if amount != 0 else qty * price_
            _tx_qty_acc[ds]  = _tx_qty_acc.get(ds,  0.0) + qty
            _tx_cost_acc[ds] = _tx_cost_acc.get(ds, 0.0) + cost_
        elif tx_type in TRANSFER_IN_TYPES and qty > 0:
            tx_date_ = _parse_date_safe(tx.get("activity_date", ""))
            cost_ = (get_price_on_date(ticker, tx_date_, prices_db) or 0.0) * qty
            _tx_qty_acc[ds]  = _tx_qty_acc.get(ds,  0.0) + qty
            _tx_cost_acc[ds] = _tx_cost_acc.get(ds, 0.0) + cost_
        elif tx_type in SELL_TYPES:
            sell_qty = qty if qty > 0 else (abs(amount)/price_ if price_ > 0 else 0)
            sell_px  = price_ if price_ > 0 else (abs(amount)/sell_qty if sell_qty > 0 else 0)
            if sell_qty > 0:
                _tx_qty_acc[ds]  = _tx_qty_acc.get(ds,  0.0) + sell_qty
                _tx_cost_acc[ds] = _tx_cost_acc.get(ds, 0.0) + sell_qty * sell_px
    for ds, qty_acc in _tx_qty_acc.items():
        if qty_acc > 0:
            tx_val_price[ds] = _tx_cost_acc[ds] / qty_acc

    # Day-by-day loop
    values, cost_basis_vals, prices_list = [], [], []
    for date_idx, ds in enumerate(sorted_dates):
        while idx < len(ticker_txns_sorted):
            tx = ticker_txns_sorted[idx]
            tx_date = _parse_date_safe(tx["activity_date"])
            if tx_date is None or tx_date.isoformat() > ds:
                break
            _apply_tx_to_fifo(tx, fifo, prices_db)
            idx += 1

        shares = sum(lot[0] for lot in fifo)
        cost   = sum(lot[0] * lot[1] for lot in fifo)

        # price_display: always the raw split-adjusted series price — gives a
        # smooth continuous line on the chart regardless of whether today is a
        # trade day or not.
        pi_d          = bisect.bisect_right(ser_dates, ds) - 1
        price_display = ser[ser_dates[pi_d]] if pi_d >= 0 else (ser[ser_dates[0]] if ser_dates else 0.0)

        # price_: used for value calculation only.
        # On trade days use the actual transaction price (accurate to what was
        # paid/received). On other days use the series price × split factor so
        # that pre-split FIFO lot counts × corrected price = true market value.
        if ds in tx_val_price:
            price_ = tx_val_price[ds]
        else:
            price_ = price_display
            if ticker_split_schedule:
                price_ *= ticker_split_fac_list[date_idx]

        values.append(round(shares * price_, 2))
        cost_basis_vals.append(round(cost, 2))
        prices_list.append(round(price_display, 4))

    # Daily chain-link TWR
    compound   = 1.0
    twr_series = [0.0]
    for i in range(1, len(sorted_dates)):
        v_prev  = values[i - 1]
        v_today = values[i]
        cf      = cf_by_date.get(sorted_dates[i], 0.0)
        if v_prev > 1e-6:
            compound *= 1 + (v_today - v_prev - cf) / v_prev
        twr_series.append(round((compound - 1) * 100, 4))

    total_twr  = compound - 1
    total_days = max((end - start).days, 1)
    ann_twr    = ((compound ** (365.0 / total_days)) - 1
                  if total_days >= 30 and compound > 0 else None)

    # Net new money invested AFTER day 0 (day-0 already in start_value).
    period_net_invested = round(sum(cf_by_date.get(d, 0.0) for d in sorted_dates[1:]), 2)

    # ── MWR (Money-Weighted Return / IRR) ───────────────────────────────────
    mwr_pct = None
    if sorted_dates and values:
        origin_dt = _parse_date(sorted_dates[0])
        mwr_cfs = []

        # Shares held before chart start = initial investment for this period
        if pre_period_value > 0:
            mwr_cfs.append((0.0, -pre_period_value))

        # Transaction cash flows within the charted period
        _div_income = {"dividend", "capital_gains_distribution"}
        for tx in ticker_txns_sorted:
            tx_date_ = _parse_date_safe(tx["activity_date"])
            if tx_date_ is None:
                continue
            ds_ = tx_date_.isoformat()
            if ds_ < sorted_dates[0] or ds_ > sorted_dates[-1]:
                continue
            t_yr = max((tx_date_ - origin_dt).days / 365.0, 0.0)
            tt   = tx["transaction_type"]
            amt  = float(tx.get("amount") or 0)
            qty_ = float(tx.get("quantity") or 0)
            px_  = float(tx.get("price") or 0)
            if tt in BUY_TYPES:
                cost_ = abs(amt) if amt != 0 else qty_ * px_
                mwr_cfs.append((t_yr, -cost_))
            elif tt in SELL_TYPES:
                proc_ = abs(amt) if amt != 0 else qty_ * px_
                mwr_cfs.append((t_yr, proc_))
            elif tt in TRANSFER_IN_TYPES and qty_ > 0:
                assigned_ = get_price_on_date(ticker, tx_date_, prices_db) or 0.0
                mwr_cfs.append((t_yr, -(qty_ * assigned_)))
            elif tt in _div_income and amt > 0:
                mwr_cfs.append((t_yr, amt))

        # Terminal value (as if liquidated at end of period)
        T_yr = max((end - origin_dt).days / 365.0, 1 / 365.0)
        if values[-1] > 0:
            mwr_cfs.append((T_yr, values[-1]))

        raw_mwr = _compute_mwr(mwr_cfs)
        if raw_mwr is not None:
            mwr_pct = round(raw_mwr * 100, 2)

    return {
        "dates":               sorted_dates,
        "values":              values,
        "prices":              prices_list,
        "cost_basis":          cost_basis_vals,
        "twr_series":          twr_series,
        "twr_total":           round(total_twr * 100, 2),
        "twr_annualized":      round(ann_twr * 100, 2) if ann_twr is not None else None,
        "mwr_annualized":      mwr_pct,
        "total_days":          total_days,
        "start_value":         values[0]  if values else 0,
        "end_value":           values[-1] if values else 0,
        "period_net_invested": period_net_invested,
    }


def _empty_chart() -> dict:
    return {
        "dates": [], "values": [], "prices": [], "stock_values": [], "cash_values": [],
        "cost_basis": [], "twr_series": [], "twr_total": 0,
        "twr_annualized": None, "mwr_annualized": None, "total_days": 0,
        "start_value": 0, "end_value": 0,
        "period_net_flows": 0, "period_net_invested": 0,
    }


# ---------------------------------------------------------------------------
# Transactions table
# ---------------------------------------------------------------------------

def get_transactions_df(account_dbs: list, account: str = None,
                        ticker: str = None, tx_type: str = None,
                        include_hash: bool = False) -> pd.DataFrame:
    txns = _get_transactions(account_dbs, account=account, ticker=ticker)
    if tx_type:
        txns = [t for t in txns if t["transaction_type"] == tx_type]
    if not txns:
        return pd.DataFrame()

    df = pd.DataFrame(txns)
    df["_sort"] = df["activity_date"].apply(_parse_date_safe)
    drop_cols = ["_sort"] if include_hash else ["_sort", "tx_hash"]
    df = df.sort_values("_sort", ascending=False).drop(columns=drop_cols, errors="ignore")

    col_map = {
        "tx_hash":          "_hash",
        "activity_date":    "Date",
        "account":          "Account",
        "ticker":           "Ticker",
        "transaction_type": "Type",
        "trans_code":       "Code",
        "description":      "Description",
        "quantity":         "Qty",
        "price":            "Price",
        "amount":           "Amount",
    }
    keep = [c for c in col_map if c in df.columns]
    df = df[keep].rename(columns=col_map)
    return df
