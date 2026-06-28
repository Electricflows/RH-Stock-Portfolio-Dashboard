"""
loaders.py — Cached data-fetching functions for the Portfolio Dashboard.
"""

import json
from pathlib import Path

import streamlit as st
import pandas as pd
from datetime import date, datetime

from Calculations import (
    get_portfolio_summary,
    get_daily_values,
    get_transactions_df,
    get_ticker_names,
    get_52week_ranges,
    get_ticker_daily_values,
)
from Prices import get_delisted_tickers, to_yf_ticker

WATCHLIST_FILE = Path("watchlist.json")

def load_watchlist() -> list:
    """Return list of {ticker, note, added} dicts."""
    if WATCHLIST_FILE.exists():
        try:
            return json.loads(WATCHLIST_FILE.read_text())
        except Exception:
            pass
    return []

def save_watchlist(items: list):
    WATCHLIST_FILE.write_text(json.dumps(items, indent=2))


@st.cache_data(ttl=300)
def load_delisted():
    return get_delisted_tickers()


@st.cache_data(ttl=300)
def load_summary(dbs_tuple, account):
    return get_portfolio_summary(list(dbs_tuple), account=account)


@st.cache_data(ttl=300)
def load_daily(dbs_tuple, account, start, end, excluded_tuple=()):
    return get_daily_values(list(dbs_tuple), start_date=start,
                            end_date=end, account=account,
                            exclude_tickers=set(excluded_tuple))


@st.cache_data(ttl=300)
def load_transactions(dbs_tuple, account, ticker_filter, type_filter):
    return get_transactions_df(list(dbs_tuple), account=account,
                               ticker=ticker_filter or None,
                               tx_type=type_filter or None)


@st.cache_data(ttl=3600)
def load_ticker_names(tickers_tuple):
    return get_ticker_names(list(tickers_tuple))


@st.cache_data(ttl=300)
def load_52week(tickers_tuple):
    return get_52week_ranges(list(tickers_tuple))


@st.cache_data(ttl=300)
def load_ticker_daily(dbs_tuple, account, ticker, start, end):
    return get_ticker_daily_values(ticker, list(dbs_tuple),
                                   start_date=start, end_date=end,
                                   account=account)


@st.cache_data(ttl=1800)
def load_benchmark_history(start: str, end: str, benchmarks_tuple: tuple = (), benchmarks_map: tuple = ()) -> dict:
    """
    Fetch daily closing prices for requested benchmarks only.
    Returns {name: {date_str: close_price}}.
    benchmarks_map: tuple of (name, symbol) pairs (passed instead of a dict for cache-friendliness).
    """
    import yfinance as _yf
    bm_dict = dict(benchmarks_map)
    names_to_fetch = benchmarks_tuple if benchmarks_tuple else tuple(bm_dict.keys())
    result = {}
    for name in names_to_fetch:
        sym = bm_dict.get(name, name)
        try:
            df = _yf.Ticker(sym).history(start=start, end=end, auto_adjust=True)
            if df is not None and not df.empty:
                df.index = df.index.tz_localize(None) if df.index.tz else df.index
                result[name] = {
                    d.strftime("%Y-%m-%d"): float(c)
                    for d, c in zip(df.index, df["Close"])
                }
        except Exception:
            pass
    return result


@st.cache_data(ttl=1800)
def load_ticker_fundamentals(tickers_tuple):
    """Fetch P/E, forward P/E, market cap, and analyst price targets from Yahoo Finance."""
    import yfinance as _yf
    from concurrent.futures import ThreadPoolExecutor, as_completed
    _dl = get_delisted_tickers()

    def _fetch_one(t):
        if t.upper() in _dl:
            return t, {"sector": "Delisted"}
        try:
            yf_sym = to_yf_ticker(t)
            ticker_obj = _yf.Ticker(yf_sym)
            info = ticker_obj.info

            # Total assets from balance sheet (not in info dict)
            total_assets = None
            try:
                bs = ticker_obj.balance_sheet
                if bs is not None and not bs.empty:
                    for _key in ("Total Assets", "TotalAssets"):
                        if _key in bs.index:
                            _val = bs.loc[_key].iloc[0]
                            if _val and not pd.isna(_val):
                                total_assets = float(_val)
                            break
            except Exception:
                pass

            return t, {
                "pe":              info.get("trailingPE"),
                "fwd_pe":          info.get("forwardPE"),
                "market_cap":      info.get("marketCap"),
                "target_mean":     info.get("targetMeanPrice"),
                "target_high":     info.get("targetHighPrice"),
                "target_low":      info.get("targetLowPrice"),
                "sector":          info.get("sector") or "Other",
                "total_cash":      info.get("totalCash"),
                "total_debt":      info.get("totalDebt"),
                "total_revenue":   info.get("totalRevenue"),
                "free_cashflow":   info.get("freeCashflow"),
                "oper_cashflow":   info.get("operatingCashflow"),
                "total_assets":    total_assets,
                "gross_margins":     info.get("grossMargins"),
                "oper_margins":      info.get("operatingMargins"),
                "revenue_growth":    info.get("revenueGrowth"),
                "earnings_growth":   info.get("earningsGrowth"),
                "debt_to_equity":    info.get("debtToEquity"),
                "shares_outstanding":info.get("sharesOutstanding"),
                # Next earnings: pick first FUTURE timestamp across all fields
                "earnings_ts":       next((ts for ts in [
                                          info.get("earningsTimestamp"),
                                          info.get("earningsTimestampStart"),
                                          info.get("earningsTimestampEnd"),
                                      ] if ts and ts > __import__("time").time()), None),
                # EQUITY | ETF | MUTUALFUND | INDEX | CRYPTOCURRENCY …
                "quote_type":        (info.get("quoteType") or "EQUITY").upper(),
            }
        except Exception:
            return t, {}

    result = {}
    with ThreadPoolExecutor(max_workers=min(len(tickers_tuple), 8)) as pool:
        for t, data in pool.map(_fetch_one, tickers_tuple):
            result[t] = data
    return result


@st.cache_data(ttl=3600)
def load_ticker_extended(ticker: str) -> dict:
    """
    Fetch annual income/cashflow history and next earnings date for one ticker.
    Heavier than fundamentals — only called in detail view and Compare tab.
    Returns {"earnings_date": date|None, "annual": {year_str: {revenue, gross_profit,
             net_income, fcf, ocf, shares, shares_diluted}}}
    """
    import yfinance as _yf
    _empty = {"earnings_date": None, "annual": {}}
    try:
        obj = _yf.Ticker(to_yf_ticker(ticker))

        # Next earnings date — calendar dict first, then info timestamp fallback
        earnings_date = None
        try:
            cal = obj.calendar
            if isinstance(cal, dict):
                ed_list = cal.get("Earnings Date", [])
                if hasattr(ed_list, "__iter__") and not isinstance(ed_list, str):
                    future = []
                    for d in ed_list:
                        try:
                            d_obj = d.date() if hasattr(d, "date") else d
                            if d_obj >= date.today():
                                future.append(d_obj)
                        except Exception:
                            pass
                    if future:
                        earnings_date = min(future)
        except Exception:
            pass

        if earnings_date is None:
            try:
                info = obj.info
                for ts_key in ("earningsTimestamp", "earningsTimestampStart"):
                    ts = info.get(ts_key)
                    if ts:
                        d_obj = datetime.fromtimestamp(ts).date()
                        if d_obj >= date.today():
                            earnings_date = d_obj
                            break
            except Exception:
                pass

        # Annual financial history
        annual: dict = {}
        try:
            income   = obj.income_stmt
            cashflow = obj.cashflow

            if income is not None and not income.empty:
                _income_map = {
                    "Total Revenue":          "revenue",
                    "Gross Profit":           "gross_profit",
                    "Net Income":             "net_income",
                    "Basic Average Shares":   "shares",
                    "Diluted Average Shares": "shares_diluted",
                }
                _cf_map = {
                    "Free Cash Flow":      "fcf",
                    "Operating Cash Flow": "ocf",
                }
                for col in sorted(income.columns, reverse=True):
                    try:
                        yr = col.year if hasattr(col, "year") else int(str(col)[:4])
                    except Exception:
                        continue
                    row: dict = {}
                    for src, dst in _income_map.items():
                        if src in income.index:
                            v = income.loc[src, col]
                            row[dst] = float(v) if pd.notna(v) else None
                    if cashflow is not None and not cashflow.empty and col in cashflow.columns:
                        for src, dst in _cf_map.items():
                            if src in cashflow.index:
                                v = cashflow.loc[src, col]
                                row[dst] = float(v) if pd.notna(v) else None
                    if row:
                        annual[str(yr)] = row
        except Exception:
            pass

        return {"earnings_date": earnings_date, "annual": annual}
    except Exception:
        return _empty


@st.cache_data(ttl=300)
def load_all_ticker_twrs(dbs_tuple, tickers_tuple, account, start=None, end=None):
    """Compute TWR + MWR for every open position for the given date range."""
    from concurrent.futures import ThreadPoolExecutor

    dbs = list(dbs_tuple)

    def _compute_one(ticker):
        tc = get_ticker_daily_values(ticker, dbs, account=account,
                                     start_date=start, end_date=end)
        return ticker, {
            "twr_total":      tc["twr_total"],
            "twr_annualized": tc["twr_annualized"],
            "mwr_annualized": tc["mwr_annualized"],
            "total_days":     tc["total_days"],
        }

    results = {}
    with ThreadPoolExecutor(max_workers=min(len(tickers_tuple), 8)) as pool:
        for ticker, data in pool.map(_compute_one, tickers_tuple):
            results[ticker] = data
    return results
