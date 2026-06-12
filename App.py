"""
App.py  —  Portfolio Dashboard
================================
Run with:  streamlit run App.py
"""

import bisect
import json
import os
import sqlite3
import tempfile
from pathlib import Path

# Always resolve paths relative to this file, regardless of where the app is launched from
os.chdir(Path(__file__).parent)

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from datetime import date, datetime, timedelta

from Calculations import (
    get_account_dbs,
    get_account_names,
    get_portfolio_summary,
    get_current_positions,
    get_daily_values,
    get_ticker_daily_values,
    get_52week_ranges,
    get_ticker_names,
    get_transactions_df,
)
from Prices import (
    check_and_fill_price_gaps,
    get_delisted_tickers,
    mark_ticker_delisted,
    unmark_ticker_delisted,
    to_yf_ticker,
)
from Import import init_db, import_csv, account_to_db_name, ensure_indexes

IGNORED_TICKERS_FILE    = Path("ignored_tickers.json")
EXCLUDED_POSITIONS_FILE = Path("excluded_positions.json")

def load_ignored_tickers() -> set:
    if IGNORED_TICKERS_FILE.exists():
        try:
            return set(json.loads(IGNORED_TICKERS_FILE.read_text()))
        except Exception:
            pass
    return set()

def load_excluded_positions() -> set:
    if EXCLUDED_POSITIONS_FILE.exists():
        try:
            return set(json.loads(EXCLUDED_POSITIONS_FILE.read_text()))
        except Exception:
            pass
    return set()

def save_excluded_positions(tickers: set):
    EXCLUDED_POSITIONS_FILE.write_text(json.dumps(sorted(tickers)))
    return set()

def save_ignored_tickers(tickers: set):
    IGNORED_TICKERS_FILE.write_text(json.dumps(sorted(tickers)))

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Portfolio Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .metric-card {
        background: #1e1e2e;
        border-radius: 10px;
        padding: 16px 20px;
        margin-bottom: 8px;
    }
    .metric-label { font-size: 0.78rem; color: #888; text-transform: uppercase; letter-spacing: 0.05em; }
    .metric-value { font-size: 1.6rem; font-weight: 700; margin-top: 4px; }
    .positive { color: #4ade80; }
    .negative { color: #f87171; }
    .neutral  { color: #e2e8f0; }

    .pos-card {
        background: #1e1e2e;
        border: 1px solid #2a2a3e;
        border-radius: 12px;
        padding: 16px;
        margin-bottom: 12px;
        height: 100%;
    }
    .pos-ticker { font-size: 1.3rem; font-weight: 800; color: #e2e8f0; }
    .pos-badge-pos { background: #1a3a2a; color: #4ade80; border-radius: 6px;
                     padding: 2px 8px; font-size: 0.8rem; font-weight: 700; }
    .pos-badge-neg { background: #3a1a1a; color: #f87171; border-radius: 6px;
                     padding: 2px 8px; font-size: 0.8rem; font-weight: 700; }
    .pos-row { display: flex; justify-content: space-between; margin-top: 10px; }
    .pos-kv  { flex: 1; }
    .pos-k   { font-size: 0.68rem; color: #666; text-transform: uppercase; letter-spacing: 0.04em; }
    .pos-v   { font-size: 0.95rem; font-weight: 600; color: #e2e8f0; margin-top: 2px; }
    .pos-v-pos { font-size: 0.95rem; font-weight: 600; color: #4ade80; margin-top: 2px; }
    .pos-v-neg { font-size: 0.95rem; font-weight: 600; color: #f87171; margin-top: 2px; }
    .wk52-wrap { margin-top: 12px; }
    .wk52-labels { display: flex; justify-content: space-between;
                   font-size: 0.68rem; color: #666; margin-bottom: 4px; }
    .wk52-track { position: relative; height: 6px; background: #2a2a3e; border-radius: 3px; }
    .wk52-fill  { height: 100%; border-radius: 3px; }
    .wk52-dot   { position: absolute; top: -3px; width: 12px; height: 12px;
                  border-radius: 50%; transform: translateX(-50%); }
    .pos-price  { font-size: 1.05rem; color: #94a3b8; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

@st.cache_data(ttl=300)
def load_delisted():
    return get_delisted_tickers()

# ---------------------------------------------------------------------------
# Startup price-gap check (runs once per session)
# ---------------------------------------------------------------------------

if "price_check_done" not in st.session_state:
    account_dbs_startup = get_account_dbs(".")
    ensure_indexes(account_dbs_startup)
    with st.spinner("Checking price history…"):
        failed_tickers = check_and_fill_price_gaps(account_dbs_startup)
    st.session_state["price_check_done"] = True
    st.session_state["price_check_failed"] = failed_tickers

ignored  = load_ignored_tickers()
delisted = load_delisted()
visible_failures = [
    t for t in st.session_state.get("price_check_failed", [])
    if t not in ignored and t.upper() not in delisted
]

if visible_failures:
    missing = ", ".join(visible_failures)
    err_col, btn_col = st.columns([5, 1])
    with err_col:
        st.error(
            f"Could not retrieve full price history for: **{missing}**. "
            "Historical values for these tickers may be understated. "
            "They may be delisted or not available on Yahoo Finance."
        )
    with btn_col:
        if st.button("Ignore permanently", type="secondary"):
            ignored.update(visible_failures)
            save_ignored_tickers(ignored)
            st.rerun()

# ---------------------------------------------------------------------------
# Sidebar — account & date selection
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("📈 Portfolio")
    st.divider()

    account_dbs  = get_account_dbs(".")
    account_map  = get_account_names(account_dbs)

    # Build flat list: "All Accounts" + each individual account
    all_accounts = ["All Accounts"]
    db_for_account = {}  # account_label -> (db_path, account_name or None)
    for db_path, names in account_map.items():
        for name in names:
            label = name
            all_accounts.append(label)
            db_for_account[label] = (db_path, name)

    selected_label = st.selectbox("Account", all_accounts)

    if selected_label == "All Accounts":
        selected_dbs     = account_dbs
        selected_account = None
    else:
        db_path, acct_name = db_for_account[selected_label]
        selected_dbs       = [db_path]
        selected_account   = acct_name

    st.divider()
    st.subheader("Performance Range")
    today = date.today()
    period = st.radio(
        "Period",
        ["1M", "3M", "6M", "YTD", "1Y", "2Y", "3Y", "All", "Custom"],
        horizontal=True,
        index=7,
        key="period_radio",
    )
    if period == "1M":
        start_date = (today - timedelta(days=30)).isoformat()
    elif period == "3M":
        start_date = (today - timedelta(days=91)).isoformat()
    elif period == "6M":
        start_date = (today - timedelta(days=182)).isoformat()
    elif period == "YTD":
        start_date = date(today.year, 1, 1).isoformat()
    elif period == "1Y":
        start_date = (today - timedelta(days=365)).isoformat()
    elif period == "2Y":
        start_date = (today - timedelta(days=730)).isoformat()
    elif period == "3Y":
        start_date = (today - timedelta(days=1095)).isoformat()
    elif period == "All":
        start_date = None
    else:  # Custom
        start_date = None  # placeholder; overridden below

    if period == "Custom":
        _actual_today = date.today()
        _default_start = _actual_today - timedelta(days=365)
        _c1, _c2 = st.columns(2)
        with _c1:
            _custom_start = st.date_input("From", value=_default_start,
                                          max_value=_actual_today,
                                          key="custom_start")
        with _c2:
            _custom_end = st.date_input("To", value=_actual_today,
                                        max_value=_actual_today,
                                        key="custom_end")
        if _custom_start >= _custom_end:
            st.warning("Start must be before end.")
            _custom_start = _default_start
        start_date = _custom_start.isoformat()
        today = _custom_end

    # Show active range as a confirmation label
    _range_label = f"{start_date} → {today.isoformat()}" if start_date else f"All history → {today.isoformat()}"
    st.caption(f"📅 Active range: {_range_label}")

    st.divider()
    st.subheader("Concentration Alerts")
    _stock_thresh = st.slider(
        "Per-stock threshold (%)",
        min_value=5, max_value=50, value=20, step=5,
        help="Warn when a single position exceeds this % of total portfolio value.",
        key="conc_stock_thresh",
    )
    _sector_thresh = st.slider(
        "Per-sector threshold (%)",
        min_value=10, max_value=80, value=40, step=5,
        help="Warn when a single sector exceeds this % of total portfolio value.",
        key="conc_sector_thresh",
    )

    st.divider()
    if st.button("🔄  Refresh Data", width='stretch'):
        st.cache_data.clear()

# ---------------------------------------------------------------------------
# Cached data loading
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def load_summary(dbs_tuple, account):
    return get_portfolio_summary(list(dbs_tuple), account=account)

@st.cache_data(ttl=300)
def load_daily(dbs_tuple, account, start, end):
    return get_daily_values(list(dbs_tuple), start_date=start,
                            end_date=end, account=account)

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

BENCHMARKS = {
    "S&P 500":  "^GSPC",
    "Dow Jones": "^DJI",
    "Nasdaq":   "^IXIC",
}
BENCHMARK_COLORS = {
    "S&P 500":  "#f97316",
    "Dow Jones": "#38bdf8",
    "Nasdaq":   "#e879f9",
}

@st.cache_data(ttl=1800)
def load_benchmark_history(start: str, end: str, benchmarks_tuple: tuple = ()) -> dict:
    """
    Fetch daily closing prices for requested benchmarks only.
    Returns {name: {date_str: close_price}}.
    """
    import yfinance as _yf
    names_to_fetch = benchmarks_tuple if benchmarks_tuple else tuple(BENCHMARKS.keys())
    result = {}
    for name in names_to_fetch:
        sym = BENCHMARKS.get(name)
        if not sym:
            continue
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
            }
        except Exception:
            return t, {}

    result = {}
    with ThreadPoolExecutor(max_workers=min(len(tickers_tuple), 8)) as pool:
        for t, data in pool.map(_fetch_one, tickers_tuple):
            result[t] = data
    # If every non-delisted ticker returned an empty dict, yfinance likely failed — don't cache so next load retries
    non_dl = [t for t in tickers_tuple if result.get(t) != {"sector": "Delisted"}]
    if non_dl and all(not result.get(t) for t in non_dl):
        raise RuntimeError("Fundamentals fetch returned all empty — skipping cache")
    return result

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

summary  = load_summary(tuple(selected_dbs), selected_account)
chart    = load_daily(tuple(selected_dbs), selected_account,
                      start_date, today.isoformat())
excluded = load_excluded_positions()

# Adjust summary metrics to exclude hidden positions
def _apply_exclusions(s: dict, excl: set) -> dict:
    """Return a copy of the summary with excluded tickers stripped from totals."""
    if not excl:
        return s
    excl_pos = [p for p in s["open_positions"] if p["ticker"] in excl]
    adj = dict(s)
    adj["open_positions"]     = [p for p in s["open_positions"]  if p["ticker"] not in excl]
    adj["total_market_value"] = s["total_market_value"] - sum(p["market_value"]    or 0 for p in excl_pos)
    adj["total_cost_basis"]   = s["total_cost_basis"]   - sum(p["cost_basis"]          for p in excl_pos)
    adj["total_unrealized"]   = s["total_unrealized"]   - sum(p["unrealized_gain"] or 0 for p in excl_pos)
    adj["account_total"]      = s["account_total"]      - sum(p["market_value"]    or 0 for p in excl_pos)
    adj["total_gain"]         = s["total_gain"]         - sum(p["total_gain"]          for p in excl_pos)
    # Subtract excluded positions' cost basis from total deposited so the
    # "return on deposits" percentage ignores money tied up in excluded stocks.
    excl_cost = sum(p["cost_basis"] for p in excl_pos)
    adj["total_deposited"] = max(0, (s.get("total_deposited") or 0) - excl_cost)
    cb = adj["total_cost_basis"]
    adj["unrealized_pct"] = (adj["total_unrealized"] / cb * 100) if cb > 0 else None
    td = adj["total_deposited"]
    adj["return_pct"] = (adj["total_gain"] / td * 100) if td > 0 else None
    return adj

summary = _apply_exclusions(summary, excluded)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_dollar(v, signed=False):
    if v is None:
        return "—"
    prefix = "+" if (signed and v > 0) else ""
    return f"{prefix}${v:,.2f}"

def _fmt_pct(v, signed=False):
    if v is None:
        return "—"
    prefix = "+" if (signed and v > 0) else ""
    return f"{prefix}{v:.2f}%"

def _color_class(v):
    if v is None or v == 0:
        return "neutral"
    return "positive" if v > 0 else "negative"

def _fmt_large(v) -> str:
    if v is None: return "—"
    if v >= 1e12: return f"${v/1e12:.2f}T"
    if v >= 1e9:  return f"${v/1e9:.2f}B"
    if v >= 1e6:  return f"${v/1e6:.2f}M"
    return f"${v:,.0f}"

def metric_card(label, value, sub=None, sub_color=None):
    color = sub_color or "neutral"
    sub_html = f'<div class="metric-label" style="margin-top:4px">{sub}</div>' if sub else ""
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value {color}">{value}</div>
        {sub_html}
    </div>
    """, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Shared benchmark alignment helper
# ---------------------------------------------------------------------------

def _align_benchmark(raw: dict, port_dates: list, start_value: float):
    """
    Forward-fill raw {date: price} onto port_dates.
    Returns (norm_values, pct_series):
      norm_values — scaled so first value equals start_value
      pct_series  — cumulative % return from the first port_date
    """
    if not raw or not port_dates:
        return [], []
    sorted_bm  = sorted(raw.items())          # [(date_str, price), ...]
    bm_dates   = [d for d, _ in sorted_bm]
    bm_prices  = [p for _, p in sorted_bm]

    # Anchor: latest benchmark price on or before the first portfolio date
    ai = bisect.bisect_right(bm_dates, port_dates[0]) - 1
    anchor_price = bm_prices[ai] if ai >= 0 else bm_prices[0]

    norm, pct = [], []
    for pd_ in port_dates:
        i     = bisect.bisect_right(bm_dates, pd_) - 1
        price = bm_prices[i] if i >= 0 else anchor_price
        norm.append(round(price / anchor_price * start_value, 2))
        pct.append(round((price / anchor_price - 1) * 100, 4))
    return norm, pct

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_overview, tab_positions, tab_performance, tab_dividends, tab_transactions, tab_import, tab_manage, tab_lookup = st.tabs([
    "📊  Overview", "💼  Positions", "📈  Performance", "📅  Dividends", "🗒  Transactions", "📥  Import", "✏️  Manage", "🔍  Lookup"
])

# ═══════════════════════════════════════════════════════════════════════════
# TAB 1 — Overview
# ═══════════════════════════════════════════════════════════════════════════
with tab_overview:
    s = summary

    # Top metric row
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        metric_card("Account Total", _fmt_dollar(s["account_total"]),
                    sub=f"as of {s['as_of']}")
    with c2:
        metric_card("Invested (Market)", _fmt_dollar(s["total_market_value"]))
    with c3:
        metric_card("Cash Balance", _fmt_dollar(s["cash_balance"]))
    with c4:
        metric_card("Unrealized Gain",
                    _fmt_dollar(s["total_unrealized"], signed=True),
                    sub=_fmt_pct(s["unrealized_pct"], signed=True),
                    sub_color=_color_class(s["total_unrealized"]))
    with c5:
        metric_card("Total Gain / Loss",
                    _fmt_dollar(s["total_gain"], signed=True),
                    sub=_fmt_pct(s["return_pct"], signed=True) + " on deposits",
                    sub_color=_color_class(s["total_gain"]))

    # ── Concentration alerts ───────────────────────────────────────────────────
    _port_total        = s["account_total"]
    _stock_thresh_frac = _stock_thresh / 100.0
    _sector_thresh_frac= _sector_thresh / 100.0

    if _port_total > 0 and s["open_positions"]:
        # Per-stock alerts
        _concentrated = [
            p for p in s["open_positions"]
            if p.get("market_value") and p["market_value"] / _port_total >= _stock_thresh_frac
        ]
        if _concentrated:
            _alert_parts = ", ".join(
                f"**{p['ticker']}** {p['market_value']/_port_total*100:.1f}%"
                for p in _concentrated
            )
            st.warning(
                f"⚠️ Stock concentration — {_alert_parts} of portfolio "
                f"(threshold ≥{_stock_thresh}%)",
                icon=None,
            )

        # Per-sector alerts (uses fundamentals cache — no extra network call)
        _alert_tickers = tuple(p["ticker"] for p in s["open_positions"])
        _alert_funds   = load_ticker_fundamentals(_alert_tickers)
        _alert_sectors: dict = {}
        for _p in s["open_positions"]:
            _sec = _alert_funds.get(_p["ticker"], {}).get("sector") or "Other"
            _alert_sectors[_sec] = _alert_sectors.get(_sec, 0.0) + (_p.get("market_value") or 0.0)
        _heavy_sectors = {
            sec: val for sec, val in _alert_sectors.items()
            if val / _port_total >= _sector_thresh_frac
        }
        if _heavy_sectors:
            _sec_parts = ", ".join(
                f"**{sec}** {val/_port_total*100:.1f}%"
                for sec, val in sorted(_heavy_sectors.items(), key=lambda x: x[1], reverse=True)
            )
            st.warning(
                f"⚠️ Sector concentration — {_sec_parts} of portfolio "
                f"(threshold ≥{_sector_thresh}%)",
                icon=None,
            )

    st.divider()

    col_left, col_right = st.columns([1.4, 1])

    with col_left:
        # Gain breakdown bar
        st.subheader("Gain Breakdown")
        gain_data = {
            "Unrealized": s["total_unrealized"],
            "Realized":   s["total_realized"],
            "Dividends":  s["total_dividends"],
            "Lending":    s["total_lending"],
        }
        fig_bar = go.Figure(go.Bar(
            x=list(gain_data.keys()),
            y=list(gain_data.values()),
            marker_color=["#4ade80" if v >= 0 else "#f87171" for v in gain_data.values()],
            text=[_fmt_dollar(v, signed=True) for v in gain_data.values()],
            textposition="outside",
        ))
        fig_bar.update_layout(
            height=300, margin=dict(t=20, b=20, l=10, r=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(showgrid=True, gridcolor="#333", zeroline=True, zerolinecolor="#555"),
            xaxis=dict(showgrid=False),
            font=dict(color="#ccc"),
            showlegend=False,
        )
        st.plotly_chart(fig_bar, width='stretch')

    with col_right:
        # Holdings pie chart
        st.subheader("Holdings Mix")
        open_pos = s["open_positions"]
        if open_pos:
            labels = [p["ticker"] for p in open_pos if p["market_value"]]
            values = [p["market_value"] for p in open_pos if p["market_value"]]
            if s["cash_balance"] > 0:
                labels.append("Cash")
                values.append(s["cash_balance"])
            total = sum(values)
            palette = px.colors.qualitative.Plotly
            colors = [palette[i % len(palette)] for i in range(len(labels))]

            text_labels = [
                f"{l}<br>{v/total*100:.1f}%" if total and v/total >= 0.05 else ""
                for l, v in zip(labels, values)
            ]
            fig_pie = go.Figure(go.Pie(
                labels=labels, values=values,
                hole=0.45,
                text=text_labels,
                textinfo="text",
                textposition="inside",
                insidetextorientation="radial",
                textfont_size=11,
                marker=dict(colors=colors),
                showlegend=False,
                hovertemplate="%{label}: %{value:$,.2f} (%{percent})<extra></extra>",
            ))

            # Add a dummy scatter trace per small slice to build a legend
            small_slices = [(l, v, c) for l, v, c in zip(labels, values, colors)
                            if total and v / total < 0.05]
            for label, value, color in small_slices:
                fig_pie.add_trace(go.Scatter(
                    x=[None], y=[None],
                    mode="markers",
                    marker=dict(size=10, color=color, symbol="square"),
                    name=f"{label} ({value/total*100:.1f}%)",
                    showlegend=True,
                    hoverinfo="skip",
                ))

            has_small = bool(small_slices)
            fig_pie.update_layout(
                height=340, margin=dict(t=10, b=10, l=10, r=130 if has_small else 10),
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#ccc"),
                showlegend=has_small,
                legend=dict(
                    title=dict(text="< 5%", font=dict(size=10)),
                    orientation="v", x=1.02, y=0.5,
                    font=dict(size=10),
                ),
            )
            st.plotly_chart(fig_pie, width='stretch')
        else:
            st.info("No open positions.")

    # Income summary
    st.divider()
    st.subheader("Income Summary")
    ic1, ic2, ic3, ic4 = st.columns(4)
    with ic1:
        metric_card("Total Deposited", _fmt_dollar(s["total_deposited"]))
    with ic2:
        metric_card("Realized Gains", _fmt_dollar(s["total_realized"], signed=True),
                    sub_color=_color_class(s["total_realized"]))
    with ic3:
        metric_card("Dividends Received", _fmt_dollar(s["total_dividends"]))
    with ic4:
        metric_card("Stock Lending Income", _fmt_dollar(s["total_lending"]))

    # Sector allocation
    st.divider()
    _ov_open = s["open_positions"]
    if _ov_open:
        _ov_tickers = tuple(p["ticker"] for p in _ov_open)
        _ov_funds   = load_ticker_fundamentals(_ov_tickers)
        _sector_map: dict = {}
        for p in _ov_open:
            sec = _ov_funds.get(p["ticker"], {}).get("sector") or "Other"
            _sector_map[sec] = _sector_map.get(sec, 0.0) + (p["market_value"] or 0.0)
        if _sector_map:
            st.subheader("Sector Allocation")
            _s_labels = list(_sector_map.keys())
            _s_values = list(_sector_map.values())
            _s_total  = sum(_s_values)
            _sec_palette = px.colors.qualitative.Pastel
            _sec_colors  = [_sec_palette[i % len(_sec_palette)] for i in range(len(_s_labels))]
            _sec_col1, _sec_col2 = st.columns([1, 1])
            with _sec_col1:
                fig_sec_pie = go.Figure(go.Pie(
                    labels=_s_labels, values=_s_values,
                    hole=0.45,
                    textinfo="label+percent",
                    textposition="outside",
                    marker=dict(colors=_sec_colors),
                    showlegend=False,
                    hovertemplate="%{label}: %{value:$,.2f} (%{percent})<extra></extra>",
                ))
                fig_sec_pie.update_layout(
                    height=360, margin=dict(t=30, b=30, l=30, r=30),
                    paper_bgcolor="rgba(0,0,0,0)", font=dict(color="#ccc"),
                    autosize=True,
                )
                fig_sec_pie.update_traces(automargin=True)
                st.plotly_chart(fig_sec_pie, width='stretch')
            with _sec_col2:
                _sorted_sectors = sorted(zip(_s_labels, _s_values), key=lambda x: x[1], reverse=True)
                fig_sec_bar = go.Figure(go.Bar(
                    y=[s[0] for s in _sorted_sectors],
                    x=[s[1] for s in _sorted_sectors],
                    orientation="h",
                    marker_color=_sec_colors[:len(_sorted_sectors)],
                    text=[f"${v:,.0f}  ({v/_s_total*100:.1f}%)" if _s_total else "" for _, v in _sorted_sectors],
                    textposition="inside",
                    insidetextanchor="start",
                ))
                fig_sec_bar.update_layout(
                    height=360, margin=dict(t=10, b=10, l=10, r=10),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(showgrid=True, gridcolor="#2a2a3e", tickprefix="$"),
                    yaxis=dict(showgrid=False, automargin=True),
                    font=dict(color="#ccc"),
                    showlegend=False,
                )
                st.plotly_chart(fig_sec_bar, width='stretch')


# ═══════════════════════════════════════════════════════════════════════════
# TAB 2 — Positions
# ═══════════════════════════════════════════════════════════════════════════
with tab_positions:
    open_pos   = summary["open_positions"]
    closed_pos = summary["closed_positions"]

    if "selected_ticker" not in st.session_state:
        st.session_state["selected_ticker"] = None

    # ── helpers ────────────────────────────────────────────────────────────

    def _parse_lot_date(s: str):
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(s).strip(), fmt).date()
            except ValueError:
                continue
        return None

    def _all_lots_long_term(open_lots: list) -> bool:
        """Return True if every open lot was acquired more than 365 days ago."""
        if not open_lots:
            return False
        cutoff = today - timedelta(days=365)
        for lot in open_lots:
            d = _parse_lot_date(lot[2]) if len(lot) > 2 else None
            if d is None or d > cutoff:
                return False
        return True

    def _52bar(ticker, week52):
        r = week52.get(ticker, {})
        lo, hi, cur = r.get("low"), r.get("high"), r.get("current")
        if lo is None or hi is None or cur is None or hi == lo:
            return ""
        pct = max(0.0, min(100.0, (cur - lo) / (hi - lo) * 100))
        dot_color = "#4ade80" if pct >= 50 else "#f87171"
        fill_color = "#4ade80" if pct >= 50 else "#f87171"
        return f"""
        <div class="wk52-wrap">
          <div class="wk52-labels">
            <span>52W Low<br>${lo:,.2f}</span>
            <span style="text-align:center">Current<br>${cur:,.2f}</span>
            <span style="text-align:right">52W High<br>${hi:,.2f}</span>
          </div>
          <div class="wk52-track">
            <div class="wk52-fill" style="width:{pct}%;background:{fill_color};opacity:0.4"></div>
            <div class="wk52-dot"  style="left:{pct}%;background:{dot_color}"></div>
          </div>
        </div>"""

    def _render_open_card(p, week52, names, twrs, fundamentals=None):
        t = p["ticker"]
        full_name = names.get(t, "")
        unr  = p.get("unrealized_gain") or 0
        unrp = p.get("unrealized_pct")  or 0
        sign = "+" if unr >= 0 else ""
        badge_cls = "pos-badge-pos" if unr >= 0 else "pos-badge-neg"
        val_cls   = "pos-v-pos"     if unr >= 0 else "pos-v-neg"
        mv   = f"${p['market_value']:,.2f}"   if p.get("market_value")   else "—"
        cb   = f"${p['cost_basis']:,.2f}"
        ap   = f"${p['avg_cost']:.4f}"
        lp   = f"${p['live_price']:.4f}"      if p.get("live_price")     else "—"
        sh   = f"{p['shares_held']:.4f}"
        divs = f"${p['dividends']:,.2f}"       if p.get("dividends")      else "—"

        # Fundamentals from Yahoo Finance
        funds = (fundamentals or {}).get(t, {})
        _pe  = funds.get("pe")
        _mc  = funds.get("market_cap")
        _tgt = funds.get("target_mean")

        pe_str  = f"{_pe:.1f}x"   if _pe  else "—"
        mc_str  = _fmt_large(_mc)
        tgt_str = f"${_tgt:.2f}"          if _tgt else "—"

        # Annualized TWR + MWR (full history)
        twr_data   = twrs.get(t, {})
        ann_twr    = twr_data.get("twr_annualized")
        tot_twr    = twr_data.get("twr_total")
        ann_mwr    = twr_data.get("mwr_annualized")
        t_days     = twr_data.get("total_days", 0)
        days_label = f" · {t_days}d" if t_days else ""

        if ann_twr is not None:
            _s = "+" if ann_twr >= 0 else ""
            twr_str   = f"{_s}{ann_twr:.2f}%"
            twr_label = f"Ann. TWR{days_label}"
            twr_cls   = "pos-v-pos" if ann_twr >= 0 else "pos-v-neg"
        elif tot_twr is not None:
            _s = "+" if tot_twr >= 0 else ""
            twr_str   = f"{_s}{tot_twr:.2f}%"
            twr_label = f"Total TWR{days_label}"
            twr_cls   = "pos-v-pos" if tot_twr >= 0 else "pos-v-neg"
        else:
            twr_str, twr_label, twr_cls = "—", f"Ann. TWR{days_label}", "pos-v"

        if ann_mwr is not None:
            _s = "+" if ann_mwr >= 0 else ""
            mwr_str   = f"{_s}{ann_mwr:.2f}%"
            mwr_label = f"Ann. MWR{days_label}"
            mwr_cls   = "pos-v-pos" if ann_mwr >= 0 else "pos-v-neg"
        else:
            mwr_str, mwr_label, mwr_cls = "—", f"Ann. MWR{days_label}", "pos-v"

        bar = _52bar(t, week52)
        name_html = f'<div style="font-size:0.75rem;color:#888;margin-top:2px">{full_name}</div>' if full_name and full_name != t else ""
        ltcg = _all_lots_long_term(p.get("open_lots", []))
        ltcg_html = '<span title="All lots held &gt; 1 year — Long-Term Capital Gains" style="font-size:1.1rem;cursor:default">⭐</span>' if ltcg else ""

        _dl_info = delisted.get(t.upper())
        if _dl_info is not None:
            _dl_notes = _dl_info.get("notes", "")
            _dl_tip = f'title="{_dl_notes}"' if _dl_notes else ""
            delisted_badge = f'<span {_dl_tip} style="background:#7f1d1d;color:#fca5a5;border-radius:4px;padding:2px 7px;font-size:0.7rem;font-weight:700;cursor:default">DELISTED</span>'
        else:
            delisted_badge = ""

        st.markdown(f"""
        <div class="pos-card">
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
            <span class="pos-ticker">{t}</span>
            <span class="pos-price">{lp}</span>
            <span class="{badge_cls}">{sign}{unrp:.2f}%</span>
            {ltcg_html}
            {delisted_badge}
          </div>
          {name_html}
          <div class="pos-row">
            <div class="pos-kv"><div class="pos-k">Shares</div><div class="pos-v">{sh}</div></div>
            <div class="pos-kv"><div class="pos-k">Avg Cost</div><div class="pos-v">{ap}</div></div>
            <div class="pos-kv"><div class="pos-k">Live Price</div><div class="pos-v">{lp}</div></div>
          </div>
          <div class="pos-row">
            <div class="pos-kv"><div class="pos-k">Market Value</div><div class="pos-v">{mv}</div></div>
            <div class="pos-kv"><div class="pos-k">Cost Basis</div><div class="pos-v">{cb}</div></div>
            <div class="pos-kv"><div class="pos-k">Unrealized $</div>
              <div class="{val_cls}">{sign}${abs(unr):,.2f}</div></div>
          </div>
          <div class="pos-row">
            <div class="pos-kv"><div class="pos-k">Dividends</div><div class="pos-v">{divs}</div></div>
            <div class="pos-kv"><div class="pos-k">{twr_label}</div><div class="{twr_cls}">{twr_str}</div></div>
            <div class="pos-kv"><div class="pos-k">{mwr_label}</div><div class="{mwr_cls}">{mwr_str}</div></div>
          </div>
          <div class="pos-row">
            <div class="pos-kv"><div class="pos-k">P/E (TTM)</div><div class="pos-v">{pe_str}</div></div>
            <div class="pos-kv"><div class="pos-k">Mkt Cap</div><div class="pos-v">{mc_str}</div></div>
            <div class="pos-kv"><div class="pos-k">Avg Target</div><div class="pos-v">{tgt_str}</div></div>
          </div>
          {bar}
        </div>""", unsafe_allow_html=True)

        btn_c1, btn_c2 = st.columns(2)
        with btn_c1:
            if st.button("View Details →", key=f"det_{t}", width='stretch'):
                st.session_state["selected_ticker"] = t
                st.rerun()
        with btn_c2:
            if st.button("Exclude ✕", key=f"excl_{t}", width='stretch'):
                _excl = load_excluded_positions()
                _excl.add(t)
                save_excluded_positions(_excl)
                st.rerun()

    # ── detail view ────────────────────────────────────────────────────────

    def _show_detail(ticker, names=None):
        pos = next((p for p in open_pos + closed_pos if p["ticker"] == ticker), None)

        if st.button("← Back to Positions"):
            st.session_state["selected_ticker"] = None
            st.rerun()

        if names and ticker in names:
            det_name = names[ticker]
        else:
            det_name = load_ticker_names((ticker,)).get(ticker, "")
        det_funds = load_ticker_fundamentals((ticker,)).get(ticker, {})
        _det_lp = pos.get("live_price") if pos else None
        _det_lp_str = f"${_det_lp:,.4f}" if _det_lp else ""
        _det_lots = pos.get("open_lots", []) if pos else []
        _det_ltcg = _all_lots_long_term(_det_lots)
        _ltcg_badge = "  ⭐ Long-Term" if _det_ltcg else ""

        if det_name and det_name != ticker:
            st.subheader(f"{ticker} — {det_name}  {_det_lp_str}{_ltcg_badge}")
        else:
            st.subheader(f"{ticker}  {_det_lp_str}{_ltcg_badge}")
        if pos:
            is_open = pos["shares_held"] > 1e-9
            c1, c2, c3, c4, c5 = st.columns(5)
            with c1:
                metric_card("Shares", f"{pos['shares_held']:.4f}")
            with c2:
                metric_card("Avg Cost", f"${pos['avg_cost']:.4f}")
            with c3:
                metric_card("Market Value",
                            _fmt_dollar(pos.get("market_value")) if is_open else "—")
            with c4:
                unr = pos.get("unrealized_gain")
                metric_card("Unrealized",
                            _fmt_dollar(unr, signed=True) if is_open else "—",
                            sub=_fmt_pct(pos.get("unrealized_pct"), signed=True) if is_open else None,
                            sub_color=_color_class(unr) if is_open else "neutral")
            with c5:
                rz = pos.get("realized_gain", 0)
                metric_card("Realized Gain", _fmt_dollar(rz, signed=True),
                            sub=f"Dividends: {_fmt_dollar(pos.get('dividends', 0))}",
                            sub_color=_color_class(rz))

            _pe      = det_funds.get("pe")
            _fwd_pe  = det_funds.get("fwd_pe")
            _mc      = det_funds.get("market_cap")
            _tgt_avg = det_funds.get("target_mean")
            _tgt_hi  = det_funds.get("target_high")
            _tgt_lo  = det_funds.get("target_low")

            fa, fb, fc, fd, fe, ff = st.columns(6)
            with fa:
                metric_card("P/E (TTM)", f"{_pe:.1f}x" if _pe else "—")
            with fb:
                metric_card("Forward P/E", f"{_fwd_pe:.1f}x" if _fwd_pe else "—")
            with fc:
                metric_card("Market Cap", _fmt_large(_mc))
            with fd:
                metric_card("Avg Target", f"${_tgt_avg:.2f}" if _tgt_avg else "—")
            with fe:
                metric_card("High Target", f"${_tgt_hi:.2f}" if _tgt_hi else "—")
            with ff:
                metric_card("Low Target", f"${_tgt_lo:.2f}" if _tgt_lo else "—")

            # ── Balance sheet snapshot ────────────────────────────────────────
            _cash     = det_funds.get("total_cash")
            _debt     = det_funds.get("total_debt")
            _assets   = det_funds.get("total_assets")
            _rev      = det_funds.get("total_revenue")
            _fcf      = det_funds.get("free_cashflow")
            _ocf      = det_funds.get("oper_cashflow")
            _has_bs   = any(v is not None for v in [_cash, _debt, _assets, _rev, _fcf, _ocf])
            if _has_bs:
                _net_cash = (_cash - _debt) if (_cash is not None and _debt is not None) else None
                _net_cash_color = _color_class(_net_cash)
                ba, bb, bc, bd, be, bf = st.columns(6)
                with ba:
                    metric_card("Cash & Equiv.", _fmt_large(_cash))
                with bb:
                    metric_card("Total Debt", _fmt_large(_debt))
                with bc:
                    _nc_str = _fmt_large(abs(_net_cash)) if _net_cash is not None else "—"
                    if _net_cash is not None:
                        _nc_str = ("+" if _net_cash >= 0 else "-") + _nc_str
                    metric_card("Net Cash", _nc_str, sub_color=_net_cash_color)
                with bd:
                    metric_card("Total Assets", _fmt_large(_assets))
                with be:
                    metric_card("Revenue (TTM)", _fmt_large(_rev))
                with bf:
                    _fcf_str = _fmt_large(abs(_fcf)).replace("$", ("$+" if _fcf and _fcf >= 0 else "$")) if _fcf is not None else "—"
                    metric_card("Free Cash Flow", _fmt_large(_fcf),
                                sub=f"Operating: {_fmt_large(_ocf)}" if _ocf else None,
                                sub_color=_color_class(_fcf))

        st.divider()
        st.caption(f"Period: **{period}** — change via the sidebar selector")

        tc = load_ticker_daily(tuple(selected_dbs), selected_account,
                               ticker, start_date, today.isoformat())

        if not tc["dates"]:
            st.info("No price data available for the selected period.")
        else:
            _det_days = tc.get("total_days", 0)
            _det_dlbl = f" · {_det_days}d" if _det_days else ""
            tm1, tm2, tm3, tm4, tm5 = st.columns(5)
            with tm1:
                metric_card("Period Start Value", _fmt_dollar(tc["start_value"]))
            with tm2:
                metric_card("Period End Value", _fmt_dollar(tc["end_value"]))
            with tm3:
                true_gain = tc["end_value"] - tc["start_value"] - tc["period_net_invested"]
                ni = tc["period_net_invested"]
                sub_note = f"New invested: {_fmt_dollar(ni)}" if abs(ni) > 0.01 else None
                metric_card("Period Return $", _fmt_dollar(true_gain, signed=True),
                            sub=sub_note, sub_color=_color_class(true_gain))
            with tm4:
                if tc["twr_annualized"] is not None:
                    metric_card(f"TWR{_det_dlbl}",
                                _fmt_pct(tc["twr_total"], signed=True),
                                sub=f"Ann: {_fmt_pct(tc['twr_annualized'], signed=True)}",
                                sub_color=_color_class(tc["twr_total"]))
                else:
                    metric_card(f"TWR{_det_dlbl}",
                                _fmt_pct(tc["twr_total"], signed=True),
                                sub_color=_color_class(tc["twr_total"]))
            with tm5:
                _mwr = tc.get("mwr_annualized")
                if _mwr is not None:
                    metric_card(f"Ann. MWR{_det_dlbl}",
                                _fmt_pct(_mwr, signed=True),
                                sub_color=_color_class(_mwr))
                else:
                    metric_card(f"Ann. MWR{_det_dlbl}", "—")

            # Build a date→value lookup for placing event markers on the line
            _date_val  = dict(zip(tc["dates"], tc["values"]))
            _chart_dates_sorted = sorted(_date_val.keys())

            def _nearest_chart_date(tx_date_str):
                """Return the closest chart date on or after the transaction date."""
                i = bisect.bisect_left(_chart_dates_sorted, tx_date_str)
                if i < len(_chart_dates_sorted):
                    return _chart_dates_sorted[i]
                return _chart_dates_sorted[-1] if _chart_dates_sorted else None

            def _iso(d_str):
                """Normalise mm/dd/yyyy or yyyy-mm-dd to yyyy-mm-dd."""
                for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
                    try:
                        return datetime.strptime(str(d_str).strip(), fmt).strftime("%Y-%m-%d")
                    except ValueError:
                        continue
                return None

            # Gather buy / sell / split events from transaction history for this ticker
            _buy_types  = {"buy", "crypto_buy", "transfer_in", "dividend_reinvestment"}
            _sell_types = {"sell", "crypto_sell"}
            _tx_all = load_transactions(tuple(selected_dbs), selected_account, ticker, None)

            # price_dict: split-adjusted display prices (smooth, no plummet at split)
            _price_dict = dict(zip(tc["dates"], tc.get("prices", [])))

            bx, by, bt   = [], [], []    # position-value chart markers
            sx, sy, st_  = [], [], []
            pbx, pby, pbt = [], [], []   # price chart markers (snapped to display price)
            psx, psy, pst = [], [], []
            split_dates   = []           # dates of stock splits for annotations

            for _, row in _tx_all.iterrows():
                iso = _iso(row.get("Date", ""))
                if not iso:
                    continue
                cd = _nearest_chart_date(iso)
                if not cd:
                    continue
                qty   = row.get("Qty",    "")
                price = row.get("Price",  "")
                amt   = row.get("Amount", "")
                qty_s   = f"{float(qty):.4f}"    if qty   not in ("", None) else "—"
                price_s = f"${float(price):.4f}" if price not in ("", None) else "—"
                amt_s   = f"${float(amt):,.2f}"  if amt   not in ("", None) else "—"

                tx_type = str(row.get("Type", ""))
                if tx_type in _buy_types:
                    yv = _date_val[cd]
                    bx.append(cd); by.append(yv)
                    bt.append(f"BUY  {iso}<br>{qty_s} shares @ {price_s}<br>Total: {amt_s}")
                    # Snap marker to the split-adjusted display price so it sits on the line
                    disp_px = _price_dict.get(cd)
                    if disp_px:
                        pbx.append(cd); pby.append(disp_px)
                        pbt.append(f"BUY  {iso}<br>{qty_s} shares @ {price_s}<br>Total: {amt_s}")
                elif tx_type in _sell_types:
                    yv = _date_val[cd]
                    sx.append(cd); sy.append(yv)
                    st_.append(f"SELL  {iso}<br>{qty_s} shares @ {price_s}<br>Proceeds: {amt_s}")
                    disp_px = _price_dict.get(cd)
                    if disp_px:
                        psx.append(cd); psy.append(disp_px)
                        pst.append(f"SELL  {iso}<br>{qty_s} shares @ {price_s}<br>Proceeds: {amt_s}")
                elif tx_type == "stock_split":
                    try:
                        _ratio = float(qty) if qty not in ("", None) else 0
                        _ratio_str = str(int(_ratio)) if _ratio == int(_ratio) else f"{_ratio:g}"
                    except Exception:
                        _ratio_str = qty_s
                    split_dates.append((cd, _ratio_str))

            # ── Price chart ──────────────────────────────────────────────────
            if tc.get("prices"):
                fig_price = go.Figure()
                fig_price.add_trace(go.Scatter(
                    x=tc["dates"], y=tc["prices"],
                    name="Price (split-adjusted)",
                    line=dict(color="#a78bfa", width=2),
                    fill="tozeroy", fillcolor="rgba(167,139,250,0.06)",
                ))
                if pbx:
                    fig_price.add_trace(go.Scatter(
                        x=pbx, y=pby, mode="markers", name="Buy",
                        marker=dict(symbol="triangle-up", size=13, color="#4ade80",
                                    line=dict(color="#1a3a2a", width=1)),
                        hovertext=pbt, hoverinfo="text",
                    ))
                if psx:
                    fig_price.add_trace(go.Scatter(
                        x=psx, y=psy, mode="markers", name="Sell",
                        marker=dict(symbol="triangle-down", size=13, color="#f87171",
                                    line=dict(color="#3a1a1a", width=1)),
                        hovertext=pst, hoverinfo="text",
                    ))
                # Split event markers
                for _sd, _sq in split_dates:
                    fig_price.add_vline(
                        x=_sd, line_color="#fbbf24", line_dash="dash", line_width=1.5, opacity=0.7,
                    )
                    fig_price.add_annotation(
                        x=_sd, yref="paper", y=1.04,
                        text=f"Split {_sq}:1", showarrow=False,
                        font=dict(color="#fbbf24", size=10), xanchor="center",
                    )
                fig_price.update_layout(
                    title=f"{ticker} — Share Price (split-adjusted)",
                    height=340,
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#ccc"),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                    yaxis=dict(showgrid=True, gridcolor="#2a2a3e", tickprefix="$"),
                    xaxis=dict(showgrid=False),
                    hovermode="x unified",
                )
                st.plotly_chart(fig_price, width='stretch')

            fig_det = go.Figure()
            fig_det.add_trace(go.Scatter(
                x=tc["dates"], y=tc["values"],
                name="Market Value",
                line=dict(color="#60a5fa", width=2),
                fill="tozeroy", fillcolor="rgba(96,165,250,0.08)",
            ))
            fig_det.add_trace(go.Scatter(
                x=tc["dates"], y=tc["cost_basis"],
                name="Cost Basis",
                line=dict(color="#94a3b8", width=1.5, dash="dash"),
            ))
            if bx:
                fig_det.add_trace(go.Scatter(
                    x=bx, y=by, mode="markers", name="Buy",
                    marker=dict(symbol="triangle-up", size=13, color="#4ade80",
                                line=dict(color="#1a3a2a", width=1)),
                    hovertext=bt, hoverinfo="text",
                ))
            if sx:
                fig_det.add_trace(go.Scatter(
                    x=sx, y=sy, mode="markers", name="Sell",
                    marker=dict(symbol="triangle-down", size=13, color="#f87171",
                                line=dict(color="#3a1a1a", width=1)),
                    hovertext=st_, hoverinfo="text",
                ))
            fig_det.update_layout(
                title=f"{ticker} — Position Value",
                height=400,
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#ccc"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                yaxis=dict(showgrid=True, gridcolor="#2a2a3e", tickprefix="$"),
                xaxis=dict(showgrid=False),
                hovermode="x unified",
            )
            st.plotly_chart(fig_det, width='stretch')

            if len(tc["twr_series"]) > 1:
                # ── Benchmark toggle (above the return chart) ─────────────────
                _det_bm_col1, _det_bm_col2 = st.columns([3, 1])
                with _det_bm_col1:
                    _det_sel_bm = st.multiselect(
                        "Overlay benchmarks",
                        options=list(BENCHMARKS.keys()),
                        default=[],
                        key=f"bm_{ticker}",
                        label_visibility="collapsed",
                    )
                with _det_bm_col2:
                    st.caption("Overlay benchmarks ↑")

                _det_bm_raw = {}
                if _det_sel_bm and tc["dates"]:
                    _det_bm_raw = load_benchmark_history(tc["dates"][0], tc["dates"][-1], tuple(_det_sel_bm))

                fig_twr = go.Figure(go.Scatter(
                    x=tc["dates"], y=tc["twr_series"],
                    name=f"{ticker} TWR %",
                    line=dict(color="#4ade80", width=2),
                    fill="tozeroy", fillcolor="rgba(74,222,128,0.06)",
                ))
                _det_bm_pct = {}
                for _bm in _det_sel_bm:
                    _bm_norm, _bm_pct = _align_benchmark(
                        _det_bm_raw.get(_bm, {}), tc["dates"], 100.0)
                    if _bm_pct:
                        fig_twr.add_trace(go.Scatter(
                            x=tc["dates"], y=_bm_pct,
                            name=f"{_bm} Return %",
                            line=dict(color=BENCHMARK_COLORS[_bm], width=1.5, dash="dot"),
                        ))
                        _det_bm_pct[_bm] = _bm_pct
                fig_twr.add_hline(y=0, line_color="#555", line_dash="dash")
                fig_twr.update_layout(
                    title=f"{ticker} — Cumulative Return (%) vs Benchmarks",
                    height=280,
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#ccc"),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                    yaxis=dict(showgrid=True, gridcolor="#2a2a3e", ticksuffix="%"),
                    xaxis=dict(showgrid=False),
                    hovermode="x unified",
                )
                st.plotly_chart(fig_twr, width='stretch')

                # Alpha summary cards when benchmarks are selected
                if _det_bm_pct:
                    _stk_final = tc["twr_series"][-1] if tc["twr_series"] else 0
                    _alpha_cols = st.columns(1 + len(_det_bm_pct))
                    with _alpha_cols[0]:
                        metric_card(f"{ticker} TWR",
                                    _fmt_pct(_stk_final, signed=True),
                                    sub_color=_color_class(_stk_final))
                    for _ai, (_bm, _pct_vals) in enumerate(_det_bm_pct.items()):
                        _bm_fin = _pct_vals[-1] if _pct_vals else 0
                        _alpha  = _stk_final - _bm_fin
                        with _alpha_cols[_ai + 1]:
                            metric_card(_bm,
                                        _fmt_pct(_bm_fin, signed=True),
                                        sub=f"Alpha: {'+' if _alpha>=0 else ''}{_alpha:.2f}%",
                                        sub_color=_color_class(_alpha))

            # ── Drawdown chart ────────────────────────────────────────────────
            _det_prices = tc.get("prices", [])
            if len(_det_prices) > 1:
                _dd_peak = _det_prices[0]
                _dd_series = []
                for _pv in _det_prices:
                    _dd_peak = max(_dd_peak, _pv)
                    _dd_series.append(round((_pv - _dd_peak) / _dd_peak * 100, 4) if _dd_peak > 0 else 0.0)
                _max_dd     = min(_dd_series)
                _max_dd_idx = _dd_series.index(_max_dd)
                _max_dd_date = tc["dates"][_max_dd_idx]
                fig_dd = go.Figure(go.Scatter(
                    x=tc["dates"], y=_dd_series,
                    name="Price Drawdown %",
                    fill="tozeroy",
                    line=dict(color="#f87171", width=1.5),
                    fillcolor="rgba(248,113,113,0.12)",
                ))
                fig_dd.add_hline(y=0, line_color="#555", line_dash="dash")
                fig_dd.update_layout(
                    title=f"{ticker} — Price Drawdown  (Max: {_max_dd:.2f}% on {_max_dd_date})",
                    height=220,
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#ccc"),
                    yaxis=dict(showgrid=True, gridcolor="#2a2a3e", ticksuffix="%"),
                    xaxis=dict(showgrid=False),
                    hovermode="x unified",
                )
                st.plotly_chart(fig_dd, width='stretch')

        # Cost basis lots table
        if _det_lots:
            st.divider()
            with st.expander("Cost Basis Lots", expanded=False):
                _live_px = pos.get("live_price") if pos else None
                _lot_rows = []
                for qty, cost_per_share, acq_date_str in _det_lots:
                    acq_d = _parse_lot_date(acq_date_str)
                    days_held = (today - acq_d).days if acq_d else None
                    total_cost = qty * cost_per_share
                    cur_val    = qty * _live_px if _live_px else None
                    gain       = cur_val - total_cost if cur_val is not None else None
                    gain_pct   = gain / total_cost * 100 if gain is not None and total_cost > 0 else None
                    lt_flag    = "⭐" if (acq_d and (today - acq_d).days > 365) else ""
                    _lot_rows.append({
                        "Acquired":       acq_date_str,
                        "Days Held":      days_held,
                        "LT":             lt_flag,
                        "Shares":         round(qty, 6),
                        "Cost/Share":     round(cost_per_share, 4),
                        "Total Cost":     round(total_cost, 2),
                        "Cur. Value":     round(cur_val, 2) if cur_val is not None else None,
                        "Gain $":         round(gain, 2) if gain is not None else None,
                        "Gain %":         round(gain_pct, 2) if gain_pct is not None else None,
                    })
                df_lots = pd.DataFrame(_lot_rows)

                def _color_lot_gain(val):
                    if pd.isna(val) or val == 0: return ""
                    return "color: #4ade80" if val > 0 else "color: #f87171"

                fmt = {"Total Cost": "${:,.2f}", "Cur. Value": "${:,.2f}",
                       "Gain $": "${:,.2f}", "Gain %": "{:.2f}%"}
                styled_lots = df_lots.style.map(_color_lot_gain, subset=["Gain $", "Gain %"]).format(fmt, na_rep="—")
                st.dataframe(styled_lots, width='stretch', hide_index=True)
                if _det_ltcg:
                    st.caption("⭐ All lots qualify for long-term capital gains treatment (held > 1 year).")
                else:
                    short_count = sum(1 for r in _lot_rows if r["LT"] == "")
                    st.caption(f"{short_count} lot(s) still short-term (< 1 year).")

        st.divider()
        st.subheader("Transactions")
        df_tx_det = load_transactions(tuple(selected_dbs), selected_account, ticker, None)
        if df_tx_det.empty:
            st.info("No transactions found.")
        else:
            st.caption(f"{len(df_tx_det):,} transactions")
            st.dataframe(df_tx_det, width='stretch', hide_index=True)

    # ── routing ────────────────────────────────────────────────────────────

    if st.session_state["selected_ticker"]:
        _all_tickers = tuple(p["ticker"] for p in open_pos + closed_pos)
        _all_names   = load_ticker_names(_all_tickers) if _all_tickers else {}
        _show_detail(st.session_state["selected_ticker"], names=_all_names)
    else:
        # Open positions grid
        st.subheader(f"Open Positions  ({len(open_pos)})")
        if open_pos:
            tickers_open = [p["ticker"] for p in open_pos]
            week52 = load_52week(tuple(tickers_open))
            names  = load_ticker_names(tuple(tickers_open))
            funds  = load_ticker_fundamentals(tuple(tickers_open))
            with st.spinner("Computing annualized returns…"):
                twrs = load_all_ticker_twrs(tuple(selected_dbs), tuple(tickers_open), selected_account,
                                            start=start_date, end=today.isoformat())
            cols_per_row = 3
            for i in range(0, len(open_pos), cols_per_row):
                row_slice = open_pos[i:i + cols_per_row]
                cols = st.columns(cols_per_row)
                for col, p in zip(cols, row_slice):
                    with col:
                        _render_open_card(p, week52, names, twrs, funds)
        else:
            st.info("No open positions.")

        st.divider()
        st.subheader(f"Closed Positions  ({len(closed_pos)})")
        if closed_pos:

            def _color_gain_closed(val):
                if pd.isna(val) or val == 0:
                    return ""
                return "color: #4ade80" if val > 0 else "color: #f87171"

            rows_c = []
            for p in closed_pos:
                rows_c.append({
                    "Ticker":       p["ticker"],
                    "Total Sold":   p["total_proceeds"],
                    "Cost Basis":   p["total_cost_sold"],
                    "Realized G/L": p["realized_gain"],
                    "Dividends":    p["dividends"],
                    "Total Gain":   p["total_gain"],
                })
            df_closed = pd.DataFrame(rows_c)
            styled_c = df_closed.style.map(
                _color_gain_closed, subset=["Realized G/L", "Total Gain"]
            ).format({
                "Total Sold":   "${:,.2f}",
                "Cost Basis":   "${:,.2f}",
                "Realized G/L": "${:,.2f}",
                "Dividends":    "${:,.2f}",
                "Total Gain":   "${:,.2f}",
            })
            st.dataframe(styled_c, width='stretch', hide_index=True)

            # Detail buttons for closed positions
            st.caption("Click a ticker to view its history:")
            btn_cols = st.columns(min(len(closed_pos), 8))
            for col, p in zip(btn_cols, closed_pos):
                with col:
                    if st.button(p["ticker"], key=f"cdet_{p['ticker']}"):
                        st.session_state["selected_ticker"] = p["ticker"]
                        st.rerun()
        else:
            st.info("No closed positions.")

        # ── Export ──────────────────────────────────────────────────────────
        if open_pos or closed_pos:
            _export_rows = []
            for p in open_pos + closed_pos:
                _export_rows.append({
                    "Ticker":          p["ticker"],
                    "Status":          "Open" if p["shares_held"] > 1e-9 else "Closed",
                    "Shares":          p["shares_held"],
                    "Avg Cost":        p["avg_cost"],
                    "Cost Basis":      p["cost_basis"],
                    "Live Price":      p.get("live_price"),
                    "Market Value":    p.get("market_value"),
                    "Unrealized $":    p.get("unrealized_gain"),
                    "Unrealized %":    p.get("unrealized_pct"),
                    "Realized Gain":   p["realized_gain"],
                    "Dividends":       p["dividends"],
                    "Total Gain":      p["total_gain"],
                })
            _export_csv = pd.DataFrame(_export_rows).to_csv(index=False)
            st.download_button(
                "⬇ Export Positions CSV",
                data=_export_csv,
                file_name=f"positions_{date.today().isoformat()}.csv",
                mime="text/csv",
            )

        # ── Tax loss harvesting candidates ─────────────────────────────────
        _tlh_candidates = [
            p for p in open_pos
            if p.get("unrealized_gain") is not None and p["unrealized_gain"] < 0
        ]
        if _tlh_candidates:
            st.divider()
            st.subheader("Tax Loss Harvesting Candidates")
            st.caption(
                "Open positions with unrealized losses. "
                "Short-term losses (< 1 year) offset ordinary income; long-term losses offset long-term gains."
            )
            _tlh_rows = []
            for _p in _tlh_candidates:
                _lots = _p.get("open_lots", [])
                _st_shares = _lt_shares = 0.0
                for _lqty, _lcost, _ldate in _lots:
                    _ld = _parse_lot_date(_ldate)
                    if _ld and (today - _ld).days > 365:
                        _lt_shares += _lqty
                    else:
                        _st_shares += _lqty
                _est_st_loss = _st_shares * ((_p.get("live_price") or 0) - (_p["avg_cost"])) if _st_shares > 0 else 0.0
                _est_lt_loss = _lt_shares * ((_p.get("live_price") or 0) - (_p["avg_cost"])) if _lt_shares > 0 else 0.0
                _tlh_rows.append({
                    "Ticker":          _p["ticker"],
                    "Market Value":    _p.get("market_value"),
                    "Unrealized $":    _p["unrealized_gain"],
                    "Unrealized %":    _p.get("unrealized_pct"),
                    "ST Shares":       round(_st_shares, 4) if _st_shares else None,
                    "Est. ST Loss":    round(_est_st_loss, 2) if _est_st_loss < 0 else None,
                    "LT Shares":       round(_lt_shares, 4) if _lt_shares else None,
                    "Est. LT Loss":    round(_est_lt_loss, 2) if _est_lt_loss < 0 else None,
                })
            _df_tlh = pd.DataFrame(_tlh_rows)

            def _color_loss(val):
                if pd.isna(val) or val == 0: return ""
                return "color: #f87171" if val < 0 else "color: #4ade80"

            _styled_tlh = _df_tlh.style.map(
                _color_loss, subset=["Unrealized $", "Est. ST Loss", "Est. LT Loss"]
            ).format({
                "Market Value":  "${:,.2f}",
                "Unrealized $":  "${:,.2f}",
                "Unrealized %":  "{:.2f}%",
                "Est. ST Loss":  "${:,.2f}",
                "Est. LT Loss":  "${:,.2f}",
            }, na_rep="—")
            st.dataframe(_styled_tlh, width='stretch', hide_index=True)
            st.caption("⚠️ Consult a tax advisor before harvesting losses. Wash-sale rules apply if you repurchase the same or substantially identical security within 30 days.")

        # ── Excluded positions management ──────────────────────────────────
        all_excl = load_excluded_positions()
        if all_excl:
            st.divider()
            st.subheader("Excluded from Portfolio")
            st.caption("These positions are hidden from cards and removed from all portfolio totals. "
                       "Their transaction history is preserved in the database.")
            excl_cols = st.columns(min(len(all_excl), 6))
            for col, ticker in zip(excl_cols, sorted(all_excl)):
                with col:
                    if st.button(f"↩ {ticker}", key=f"reincl_{ticker}",
                                 help=f"Re-include {ticker} in portfolio calculations"):
                        all_excl.discard(ticker)
                        save_excluded_positions(all_excl)
                        st.rerun()


# ═══════════════════════════════════════════════════════════════════════════
# TAB 3 — Performance
# ═══════════════════════════════════════════════════════════════════════════
with tab_performance:
    c = chart

    if not c["dates"]:
        st.info("No price data available for the selected period.")
    else:
        # Performance metric row
        _perf_days = c.get("total_days", 0)
        _perf_dlbl = f" · {_perf_days}d" if _perf_days else ""
        m1, m2, m3, m4, m5 = st.columns(5)
        with m1:
            metric_card("Start Value", _fmt_dollar(c["start_value"]))
        with m2:
            metric_card("End Value", _fmt_dollar(c["end_value"]))
        with m3:
            true_gain = c["end_value"] - c["start_value"] - c["period_net_flows"]
            nf = c["period_net_flows"]
            sub_note = f"Net flows: {_fmt_dollar(nf, signed=True)}" if abs(nf) > 0.01 else None
            metric_card("Period Return $", _fmt_dollar(true_gain, signed=True),
                        sub=sub_note, sub_color=_color_class(true_gain))
        with m4:
            if c["twr_annualized"] is not None:
                metric_card(f"TWR{_perf_dlbl}",
                            _fmt_pct(c["twr_total"], signed=True),
                            sub=f'Ann: {_fmt_pct(c["twr_annualized"], signed=True)}',
                            sub_color=_color_class(c["twr_total"]))
            else:
                metric_card(f"TWR{_perf_dlbl}",
                            _fmt_pct(c["twr_total"], signed=True),
                            sub_color=_color_class(c["twr_total"]))
        with m5:
            _pmwr = c.get("mwr_annualized")
            if _pmwr is not None:
                metric_card(f"Ann. MWR{_perf_dlbl}",
                            _fmt_pct(_pmwr, signed=True),
                            sub_color=_color_class(_pmwr))
            else:
                metric_card(f"Ann. MWR{_perf_dlbl}", "—")

        st.divider()

        # ── Portfolio value chart ─────────────────────────────────────────────
        fig = go.Figure()

        fig.add_trace(go.Scatter(
            x=c["dates"], y=c["values"],
            name="Portfolio Value",
            line=dict(color="#60a5fa", width=2.5),
            fill="tozeroy",
            fillcolor="rgba(96,165,250,0.08)",
        ))
        fig.add_trace(go.Scatter(
            x=c["dates"], y=c["stock_values"],
            name="Stock Holdings",
            line=dict(color="#a78bfa", width=1.5, dash="dot"),
        ))
        fig.add_trace(go.Scatter(
            x=c["dates"], y=c["cost_basis"],
            name="Cost Basis",
            line=dict(color="#94a3b8", width=1.5, dash="dash"),
        ))
        fig.add_trace(go.Scatter(
            x=c["dates"], y=c["cash_values"],
            name="Cash",
            line=dict(color="#fbbf24", width=1.5, dash="dot"),
        ))

        fig.update_layout(
            title="Portfolio Value Over Time",
            xaxis_title="Date",
            yaxis_title="Value ($)",
            height=420,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#ccc"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
            yaxis=dict(showgrid=True, gridcolor="#2a2a3e", tickprefix="$"),
            xaxis=dict(showgrid=False),
            hovermode="x unified",
        )
        st.plotly_chart(fig, width='stretch')

        # ── TWR cumulative return chart ───────────────────────────────────────
        # Benchmark selector lives here — benchmarks only appear on the TWR chart
        bm_col1, bm_col2 = st.columns([3, 1])
        with bm_col1:
            selected_benchmarks = st.multiselect(
                "Overlay benchmarks",
                options=list(BENCHMARKS.keys()),
                default=[],
                help="Benchmarks are shown as cumulative % return on the chart below.",
                label_visibility="collapsed",
            )
        with bm_col2:
            st.caption("Overlay benchmarks ↓")

        bm_data = {}
        if selected_benchmarks and c["dates"]:
            bm_data = load_benchmark_history(c["dates"][0], c["dates"][-1], tuple(selected_benchmarks))

        bm_pct_series = {}
        for bm_name in selected_benchmarks:
            raw = bm_data.get(bm_name, {})
            _, pct_vals = _align_benchmark(raw, c["dates"], c["start_value"])
            if pct_vals:
                bm_pct_series[bm_name] = pct_vals

        if len(c["twr_series"]) > 1:
            fig_twr = go.Figure(go.Scatter(
                x=c["dates"], y=c["twr_series"],
                name="Portfolio TWR %",
                line=dict(color="#4ade80", width=2.5),
                fill="tozeroy",
                fillcolor="rgba(74,222,128,0.06)",
            ))
            for bm_name, pct_vals in bm_pct_series.items():
                color = BENCHMARK_COLORS[bm_name]
                fig_twr.add_trace(go.Scatter(
                    x=c["dates"], y=pct_vals,
                    name=f"{bm_name} Return %",
                    line=dict(color=color, width=1.5, dash="dot"),
                ))
            fig_twr.add_hline(y=0, line_color="#555", line_dash="dash")
            fig_twr.update_layout(
                title="Cumulative Return (%) — Portfolio vs Benchmarks",
                height=300,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#ccc"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                yaxis=dict(showgrid=True, gridcolor="#2a2a3e", ticksuffix="%"),
                xaxis=dict(showgrid=False),
                hovermode="x unified",
            )
            st.plotly_chart(fig_twr, width='stretch')

            # Benchmark summary metrics
            if bm_pct_series:
                port_final_pct = c["twr_series"][-1] if c["twr_series"] else 0
                bm_cols = st.columns(1 + len(bm_pct_series))
                with bm_cols[0]:
                    metric_card("Portfolio TWR",
                                _fmt_pct(port_final_pct, signed=True),
                                sub_color=_color_class(port_final_pct))
                for i, (bm_name, pct_vals) in enumerate(bm_pct_series.items()):
                    bm_final = pct_vals[-1] if pct_vals else 0
                    alpha = port_final_pct - bm_final
                    alpha_str = f"Alpha: {'+' if alpha >= 0 else ''}{alpha:.2f}%"
                    with bm_cols[i + 1]:
                        metric_card(bm_name,
                                    _fmt_pct(bm_final, signed=True),
                                    sub=alpha_str,
                                    sub_color=_color_class(alpha))

        # Drawdown chart
        if len(c["values"]) > 1:
            _dd_vals = c["values"]
            _running_max = []
            _peak = _dd_vals[0]
            _drawdown = []
            for v in _dd_vals:
                _peak = max(_peak, v)
                _running_max.append(_peak)
                _drawdown.append(round((v - _peak) / _peak * 100, 4) if _peak > 0 else 0.0)

            _max_dd = min(_drawdown)
            _max_dd_date = c["dates"][_drawdown.index(_max_dd)]
            fig_dd = go.Figure(go.Scatter(
                x=c["dates"], y=_drawdown,
                name="Drawdown %",
                fill="tozeroy",
                line=dict(color="#f87171", width=1.5),
                fillcolor="rgba(248,113,113,0.12)",
            ))
            fig_dd.add_hline(y=0, line_color="#555", line_dash="dash")
            fig_dd.update_layout(
                title=f"Drawdown — Max: {_max_dd:.2f}% on {_max_dd_date}",
                height=240,
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#ccc"),
                yaxis=dict(showgrid=True, gridcolor="#2a2a3e", ticksuffix="%"),
                xaxis=dict(showgrid=False),
                hovermode="x unified",
            )
            st.plotly_chart(fig_dd, width='stretch')

        # ── Annual returns table ──────────────────────────────────────────────
        if len(c["dates"]) > 1:
            st.divider()
            st.subheader("Annual Returns")
            _ann_rows = []
            _prev_compound = 1.0
            _dates_list = c["dates"]
            _twr_list   = c["twr_series"]  # cumulative % from day 0

            # Collect all years present in the chart data
            _years = sorted(set(d[:4] for d in _dates_list))
            for _yr in _years:
                _yr_dates = [d for d in _dates_list if d[:4] == _yr]
                if not _yr_dates:
                    continue
                _first_idx = _dates_list.index(_yr_dates[0])
                _last_idx  = _dates_list.index(_yr_dates[-1])

                # Compound factor at end of this year vs start of this year
                _compound_start = 1 + _twr_list[_first_idx] / 100
                _compound_end   = 1 + _twr_list[_last_idx]  / 100
                _yr_return_pct  = (_compound_end / _compound_start - 1) * 100 if _compound_start > 0 else 0.0

                _start_val = c["values"][_first_idx]
                _end_val   = c["values"][_last_idx]
                _ann_rows.append({
                    "Year":       int(_yr),
                    "Start Value": _start_val,
                    "End Value":   _end_val,
                    "TWR %":       round(_yr_return_pct, 2),
                })

            if _ann_rows:
                _df_ann = pd.DataFrame(_ann_rows)

                def _color_twr(val):
                    if pd.isna(val) or val == 0: return ""
                    return "color: #4ade80" if val > 0 else "color: #f87171"

                _styled_ann = _df_ann.style.map(_color_twr, subset=["TWR %"]).format({
                    "Start Value": "${:,.2f}",
                    "End Value":   "${:,.2f}",
                    "TWR %":       "{:+.2f}%",
                })
                st.dataframe(_styled_ann, width='stretch', hide_index=True)
                st.caption("TWR % is the chain-link return for each calendar year using dates available in the selected period.")

        st.caption(
            "TWR (Time-Weighted Return) uses the daily chain-link method, "
            "isolating portfolio performance from external cash flows. "
            "Benchmark lines on the value chart are scaled to your portfolio's start value. "
            "Annualised rate shown only for periods ≥ 30 days."
        )


# ═══════════════════════════════════════════════════════════════════════════
# TAB 4 — Dividends
# ═══════════════════════════════════════════════════════════════════════════
with tab_dividends:
    _div_df = load_transactions(tuple(selected_dbs), selected_account, None, "dividend")
    _cg_df  = load_transactions(tuple(selected_dbs), selected_account, None, "capital_gains_distribution")
    _all_income = pd.concat([_div_df, _cg_df], ignore_index=True) if not _cg_df.empty else _div_df.copy()

    if _all_income.empty:
        st.info("No dividend or capital gains transactions found.")
    else:
        # Parse dates and amounts robustly
        def _parse_div_date(s):
            for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
                try:
                    return datetime.strptime(str(s).strip(), fmt).date()
                except ValueError:
                    continue
            return None

        _all_income = _all_income.copy()
        _all_income["_date"] = _all_income["Date"].apply(_parse_div_date)
        _all_income["_amt"]  = pd.to_numeric(_all_income["Amount"], errors="coerce").fillna(0.0)
        _all_income = _all_income.dropna(subset=["_date"])
        _all_income["_year"]  = _all_income["_date"].apply(lambda d: d.year)
        _all_income["_month"] = _all_income["_date"].apply(lambda d: d.month)
        _all_income["_ym"]    = _all_income["_date"].apply(lambda d: d.strftime("%Y-%m"))

        # ── Projection engine ──────────────────────────────────────────────
        _STANDARD_INTERVALS = [30, 60, 91, 182, 365]  # monthly/bimonthly/quarterly/semi/annual

        def _snap_interval(days: int) -> int:
            return min(_STANDARD_INTERVALS, key=lambda s: abs(s - days))

        def _project_ticker(rows_tk):
            """
            Given historical payment rows for one ticker (sorted by date, amt > 0),
            return list of (date, amount) projected payments up to 6 months forward.
            Needs at least 2 historical payments to detect an interval.
            """
            rows_tk = rows_tk.sort_values("_date")
            dates   = list(rows_tk["_date"])
            amounts = list(rows_tk["_amt"])
            if len(dates) < 2:
                return []
            intervals = [(dates[i+1] - dates[i]).days for i in range(len(dates) - 1)]
            # Use median interval to be robust against gaps
            intervals.sort()
            med = intervals[len(intervals) // 2]
            interval = _snap_interval(med)
            # Average of last 4 actual amounts
            avg_amt = sum(amounts[-4:]) / min(4, len(amounts))
            fwd_cutoff = today + timedelta(days=183)
            result = []
            next_date = dates[-1] + timedelta(days=interval)
            while next_date <= fwd_cutoff:
                result.append((next_date, round(avg_amt, 2)))
                next_date += timedelta(days=interval)
            return result

        # Build projections for all tickers
        _hist_pos = _all_income[_all_income["_amt"] > 0]
        _proj_rows = []   # {ym, Ticker, amount, projected=True}
        for _tk in _hist_pos["Ticker"].unique():
            _tk_hist = _hist_pos[_hist_pos["Ticker"] == _tk]
            for _proj_date, _proj_amt in _project_ticker(_tk_hist):
                _proj_rows.append({
                    "_date":     _proj_date,
                    "_ym":       _proj_date.strftime("%Y-%m"),
                    "Ticker":    _tk,
                    "_amt":      _proj_amt,
                    "_projected": True,
                })
        _proj_df = pd.DataFrame(_proj_rows) if _proj_rows else pd.DataFrame(
            columns=["_date", "_ym", "Ticker", "_amt", "_projected"])

        _proj_6m  = _proj_df["_amt"].sum() if not _proj_df.empty else 0.0
        _proj_12m = _proj_6m * 2  # rough annualised projection from 6-month window

        # ── Summary metrics ────────────────────────────────────────────────
        _total_divs  = _all_income["_amt"].sum()
        _this_year   = today.year
        _ytd_divs    = _all_income[_all_income["_year"] == _this_year]["_amt"].sum()
        _last_12m    = _all_income[_all_income["_date"] >= (today - timedelta(days=365))]["_amt"].sum()
        _payers      = _hist_pos["Ticker"].nunique()

        dv1, dv2, dv3, dv4, dv5, dv6 = st.columns(6)
        with dv1:
            metric_card("Total Income", _fmt_dollar(_total_divs))
        with dv2:
            metric_card(f"{_this_year} YTD", _fmt_dollar(_ytd_divs))
        with dv3:
            metric_card("Trailing 12M", _fmt_dollar(_last_12m))
        with dv4:
            metric_card("Paying Tickers", str(_payers))
        with dv5:
            metric_card("Projected 6M", _fmt_dollar(_proj_6m),
                        sub="next 6 months")
        with dv6:
            metric_card("Projected 12M", _fmt_dollar(_proj_12m),
                        sub="annualised est.")

        st.divider()

        # ── ±6-month dividend calendar ─────────────────────────────────────
        st.subheader("Dividend Calendar  —  6 Months Back & Forward")
        _cal_start = today - timedelta(days=183)
        _cal_end   = today + timedelta(days=183)

        # Actual payments in the window
        _cal_actual = _hist_pos[
            (_hist_pos["_date"] >= _cal_start) & (_hist_pos["_date"] <= _cal_end)
        ].copy()
        _cal_actual["_projected"] = False

        # Projected payments in the window
        _cal_proj = _proj_df[
            (_proj_df["_date"] >= _cal_start) & (_proj_df["_date"] <= _cal_end)
        ].copy() if not _proj_df.empty else pd.DataFrame()

        # All months in window
        _cal_months = []
        _m = _cal_start.replace(day=1)
        while _m <= _cal_end:
            _cal_months.append(_m.strftime("%Y-%m"))
            # advance one month
            if _m.month == 12:
                _m = _m.replace(year=_m.year + 1, month=1)
            else:
                _m = _m.replace(month=_m.month + 1)

        _cal_tickers = sorted(set(
            list(_cal_actual["Ticker"].unique()) +
            (list(_cal_proj["Ticker"].unique()) if not _cal_proj.empty else [])
        ))
        _div_palette = px.colors.qualitative.Plotly

        fig_cal = go.Figure()
        _today_ym = today.strftime("%Y-%m")

        for i, tk in enumerate(_cal_tickers):
            color = _div_palette[i % len(_div_palette)]

            # Actual bars
            _act_tk = _cal_actual[_cal_actual["Ticker"] == tk]
            _act_by_ym = _act_tk.groupby("_ym")["_amt"].sum().to_dict()
            _act_y = [_act_by_ym.get(ym, 0) for ym in _cal_months]
            fig_cal.add_trace(go.Bar(
                name=tk,
                x=_cal_months,
                y=_act_y,
                marker_color=color,
                legendgroup=tk,
                showlegend=True,
                hovertemplate=f"{tk}: $%{{y:.2f}}<extra>Actual</extra>",
            ))

            # Projected bars (same color, 40% opacity, dashed outline)
            _proj_tk = _cal_proj[_cal_proj["Ticker"] == tk] if not _cal_proj.empty else pd.DataFrame()
            _proj_by_ym = _proj_tk.groupby("_ym")["_amt"].sum().to_dict() if not _proj_tk.empty else {}
            _proj_y = [_proj_by_ym.get(ym, 0) for ym in _cal_months]
            fig_cal.add_trace(go.Bar(
                name=f"{tk} (proj.)",
                x=_cal_months,
                y=_proj_y,
                marker=dict(color=color, opacity=0.4, line=dict(color=color, width=1.5)),
                legendgroup=f"{tk}_proj",
                showlegend=any(v > 0 for v in _proj_y),
                hovertemplate=f"{tk}: $%{{y:.2f}}<extra>Projected</extra>",
            ))

        # Today divider (add_vline doesn't support categorical axes — use shape + annotation)
        if _today_ym in _cal_months:
            fig_cal.add_shape(
                type="line", xref="x", yref="paper",
                x0=_today_ym, x1=_today_ym, y0=0, y1=1,
                line=dict(color="#fbbf24", dash="dash", width=1.5),
            )
            fig_cal.add_annotation(
                x=_today_ym, yref="paper", y=1.08,
                text="Today", showarrow=False,
                font=dict(color="#fbbf24", size=11),
                xanchor="center",
            )
        fig_cal.update_layout(
            barmode="stack",
            height=400,
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#ccc"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
            yaxis=dict(showgrid=True, gridcolor="#2a2a3e", tickprefix="$"),
            xaxis=dict(showgrid=False, tickangle=-30),
            hovermode="x unified",
        )
        st.plotly_chart(fig_cal, width='stretch')

        # ── Upcoming payments table ────────────────────────────────────────
        if not _proj_df.empty:
            st.subheader("Upcoming Projected Payments")
            _upcoming = _proj_df[_proj_df["_date"] > today].sort_values("_date")
            if not _upcoming.empty:
                _upcoming_disp = _upcoming[["_date", "Ticker", "_amt"]].copy()
                _upcoming_disp.columns = ["Expected Date", "Ticker", "Est. Amount"]
                _upcoming_disp["Expected Date"] = _upcoming_disp["Expected Date"].apply(
                    lambda d: d.strftime("%b %d, %Y"))
                _upcoming_disp["Est. Amount"] = _upcoming_disp["Est. Amount"].apply(
                    lambda v: f"${v:,.2f}")
                st.dataframe(_upcoming_disp, width='stretch', hide_index=True)
                st.caption("Amounts estimated from the average of recent payments. "
                           "Interval detected from historical payment frequency.")

        st.divider()

        # ── Full-history monthly bar chart ─────────────────────────────────
        st.subheader("Monthly Income  (All History)")
        _monthly = (
            _hist_pos
            .groupby(["_ym", "Ticker"])["_amt"]
            .sum()
            .reset_index()
        )
        if not _monthly.empty:
            _all_yms = sorted(_monthly["_ym"].unique())
            _tickers_div = sorted(_monthly["Ticker"].unique())

            fig_div = go.Figure()
            for i, tk in enumerate(_tickers_div):
                _tk_data = _monthly[_monthly["Ticker"] == tk]
                _ym_amt  = dict(zip(_tk_data["_ym"], _tk_data["_amt"]))
                fig_div.add_trace(go.Bar(
                    name=tk,
                    x=_all_yms,
                    y=[_ym_amt.get(ym, 0) for ym in _all_yms],
                    marker_color=_div_palette[i % len(_div_palette)],
                ))
            fig_div.update_layout(
                barmode="stack",
                height=340,
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#ccc"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                yaxis=dict(showgrid=True, gridcolor="#2a2a3e", tickprefix="$"),
                xaxis=dict(showgrid=False, tickangle=-45),
                hovermode="x unified",
            )
            st.plotly_chart(fig_div, width='stretch')

        # ── Annual summary table ───────────────────────────────────────────
        st.subheader("Annual Summary")
        _annual = (
            _all_income[_all_income["_amt"] > 0]
            .groupby(["_year", "Ticker"])["_amt"]
            .sum()
            .unstack(fill_value=0)
        )
        _annual["Total"] = _annual.sum(axis=1)
        _annual = _annual.sort_index(ascending=False).reset_index().rename(columns={"_year": "Year"})
        st.dataframe(_annual.style.format("${:,.2f}", subset=[c for c in _annual.columns if c != "Year"]),
                     width='stretch', hide_index=True)

        # ── Per-ticker breakdown ───────────────────────────────────────────
        st.divider()
        st.subheader("By Ticker")
        _by_ticker = (
            _all_income[_all_income["_amt"] > 0]
            .groupby("Ticker")["_amt"]
            .agg(["sum", "count", "mean"])
            .rename(columns={"sum": "Total", "count": "Payments", "mean": "Avg Payment"})
            .sort_values("Total", ascending=False)
            .reset_index()
        )
        st.dataframe(
            _by_ticker.style.format({"Total": "${:,.2f}", "Avg Payment": "${:,.2f}"}),
            width='stretch', hide_index=True,
        )

        # ── Full transaction list ──────────────────────────────────────────
        st.divider()
        with st.expander("All Dividend Transactions"):
            _disp = _all_income.drop(columns=["_date", "_amt", "_year", "_month", "_ym"], errors="ignore")
            st.dataframe(_disp, width='stretch', hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════
# TAB 5 — Transactions
# ═══════════════════════════════════════════════════════════════════════════
with tab_transactions:
    f1, f2, f3 = st.columns([1, 1, 2])
    with f1:
        ticker_filter = st.text_input("Filter by Ticker", placeholder="e.g. TSLA").upper()
    with f2:
        type_options = [""] + sorted({
            "buy", "sell", "crypto_buy", "crypto_sell",
            "direct_deposit", "deposit", "dividend",
            "interest", "lending_income", "subscription_fee",
            "transfer_in", "internal_transfer", "event_contract_buy",
            "event_contract_payout", "forced_buy_in", "bonus_credit",
        })
        type_filter = st.selectbox("Filter by Type", type_options)

    df_tx = load_transactions(
        tuple(selected_dbs), selected_account,
        ticker_filter or None, type_filter or None,
    )

    if df_tx.empty:
        st.info("No transactions match the current filters.")
    else:
        _tx_dl_col, _tx_cap_col = st.columns([1, 5])
        with _tx_dl_col:
            st.download_button(
                "⬇ Export CSV",
                data=df_tx.to_csv(index=False),
                file_name=f"transactions_{date.today().isoformat()}.csv",
                mime="text/csv",
            )
        with _tx_cap_col:
            st.caption(f"{len(df_tx):,} transactions")
        st.dataframe(df_tx, width='stretch', height=600)


# ═══════════════════════════════════════════════════════════════════════════
# TAB 5 — Import
# ═══════════════════════════════════════════════════════════════════════════
with tab_import:
    st.subheader("Import Robinhood CSV")
    st.caption("Upload one or more Robinhood account export files. Duplicates are automatically skipped.")

    uploaded_files = st.file_uploader(
        "Select CSV file(s)", type="csv", accept_multiple_files=True
    )

    existing_accounts = []
    for db in get_account_dbs("."):
        try:
            conn_tmp = sqlite3.connect(db)
            rows = conn_tmp.execute("SELECT account_name FROM accounts ORDER BY account_name").fetchall()
            conn_tmp.close()
            existing_accounts.extend(r[0] for r in rows)
        except Exception:
            pass

    account_options = ["-- New account --"] + existing_accounts
    selected_option = st.selectbox("Account name", account_options)

    if selected_option == "-- New account --":
        account_name = st.text_input("New account name", placeholder="e.g. Michael IRA")
    else:
        account_name = selected_option

    if st.button("Import", type="primary", disabled=not (uploaded_files and account_name.strip())):
        account_name = account_name.strip()
        db_path = account_to_db_name(account_name)
        conn_imp = init_db(db_path)

        total_stats = {"inserted": 0, "duplicates": 0, "unknown": 0, "errors": 0}

        for uf in uploaded_files:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
                tmp.write(uf.read())
                tmp_path = tmp.name
            try:
                stats = import_csv(conn_imp, tmp_path, account=account_name)
                for k in total_stats:
                    total_stats[k] += stats[k]
            except ValueError as e:
                st.error(f"{uf.name}: {e}")
            finally:
                Path(tmp_path).unlink(missing_ok=True)

        conn_imp.close()

        st.success(
            f"Import complete — "
            f"**{total_stats['inserted']}** new rows, "
            f"**{total_stats['duplicates']}** duplicates skipped, "
            f"**{total_stats['unknown']}** flagged for review, "
            f"**{total_stats['errors']}** errors."
        )

        if total_stats["inserted"] > 0:
            with st.spinner("Fetching missing price history…"):
                failed = check_and_fill_price_gaps([db_path])
            if failed:
                st.warning(f"Could not fetch prices for: {', '.join(failed)}")
            st.cache_data.clear()
            st.session_state.pop("price_check_done", None)
            st.rerun()

    st.divider()
    st.subheader("Flagged Transactions")
    st.caption("Rows with unrecognised or blank transaction codes. Add new codes to TRANS_CODE_MAP in Import.py to resolve them.")

    flagged_rows = []
    for db in get_account_dbs("."):
        try:
            conn_f = sqlite3.connect(db)
            rows = conn_f.execute(
                """SELECT account, trans_code, raw_row, source_file, imported_at
                   FROM unknown_transactions ORDER BY imported_at DESC"""
            ).fetchall()
            conn_f.close()
            for account_f, trans_code_f, raw_row_f, source_f, imported_at_f in rows:
                try:
                    raw = json.loads(raw_row_f)
                except Exception:
                    raw = {}
                flagged_rows.append({
                    "Account":       account_f,
                    "Trans Code":    trans_code_f,
                    "Date":          raw.get("Activity Date", ""),
                    "Ticker":        raw.get("Instrument", ""),
                    "Description":   raw.get("Description", ""),
                    "Amount":        raw.get("Amount", ""),
                    "Source File":   source_f,
                    "Imported At":   imported_at_f,
                })
        except Exception:
            pass

    if flagged_rows:
        st.dataframe(pd.DataFrame(flagged_rows), width='stretch')
    else:
        st.info("No flagged transactions.")


# ═══════════════════════════════════════════════════════════════════════════
# TAB 6 — Manage (manual entry + edit/delete)
# ═══════════════════════════════════════════════════════════════════════════

# Transaction type options for dropdowns
_TX_LABELS = [
    "Buy", "Sell", "Dividend", "Dividend Reinvestment",
    "Capital Gains Distribution", "Deposit", "Withdrawal",
    "Transfer In (Shares)", "Lending Income", "Interest",
    "Subscription Fee", "Stock Split",
]
_TX_TYPE_MAP = {
    "Buy":                         "buy",
    "Sell":                        "sell",
    "Dividend":                    "dividend",
    "Dividend Reinvestment":       "dividend_reinvestment",
    "Capital Gains Distribution":  "capital_gains_distribution",
    "Deposit":                     "deposit",
    "Withdrawal":                  "withdrawal",
    "Transfer In (Shares)":        "transfer_in",
    "Lending Income":              "lending_income",
    "Interest":                    "interest",
    "Subscription Fee":            "subscription_fee",
    "Stock Split":                 "stock_split",
}
_TX_CODE_MAP = {
    "buy": "Buy", "sell": "Sell",
    "dividend": "CDIV", "dividend_reinvestment": "DRIP",
    "capital_gains_distribution": "LCAP",
    "deposit": "ACH", "withdrawal": "ACH",
    "transfer_in": "ITRF", "lending_income": "SLIP",
    "interest": "INT", "subscription_fee": "GOLD",
    "stock_split": "SPLIT",
}
# Types where the amount should be negative (cash outflow)
_OUTFLOW_TYPES = {"buy", "dividend_reinvestment", "withdrawal", "subscription_fee"}


def _insert_transaction(db_path: str, account: str, activity_date: str,
                        tx_type: str, ticker: str, description: str,
                        quantity, price, amount) -> tuple:
    import hashlib, sqlite3 as _sq
    trans_code = _TX_CODE_MAP.get(tx_type, "")
    key = "|".join([str(activity_date), str(ticker or ""), trans_code,
                    str(quantity or ""), str(amount or "")])
    tx_hash = hashlib.sha256(key.encode()).hexdigest()
    try:
        conn = _sq.connect(db_path)
        conn.execute("""
            INSERT INTO transactions
              (tx_hash, account, activity_date, process_date, settle_date,
               ticker, transaction_type, trans_code, description,
               quantity, price, amount, source_file, imported_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (tx_hash, account, activity_date, activity_date, activity_date,
               ticker or None, tx_type, trans_code, description or "",
               quantity or None, price or None, amount or None,
               "manual_entry", datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True, "Transaction added."
    except _sq.IntegrityError:
        return False, "Duplicate — a transaction with the same date, ticker, type, qty and amount already exists."
    except Exception as exc:
        return False, str(exc)


def _delete_transactions(db_path: str, hashes: list):
    import sqlite3 as _sq
    conn = _sq.connect(db_path)
    conn.executemany("DELETE FROM transactions WHERE tx_hash = ?", [(h,) for h in hashes])
    conn.commit()
    conn.close()


def _update_transaction(db_path: str, tx_hash: str, activity_date: str,
                        ticker: str, tx_type: str, quantity, price, amount,
                        description: str):
    import hashlib, sqlite3 as _sq
    trans_code = _TX_CODE_MAP.get(tx_type, "")
    conn = _sq.connect(db_path)
    conn.execute("""
        UPDATE transactions SET
          activity_date=?, process_date=?, settle_date=?,
          ticker=?, transaction_type=?, trans_code=?,
          description=?, quantity=?, price=?, amount=?
        WHERE tx_hash=?
    """, (activity_date, activity_date, activity_date,
          ticker or None, tx_type, trans_code, description or "",
          quantity or None, price or None, amount or None, tx_hash))
    conn.commit()
    conn.close()


with tab_manage:
    # Use the sidebar account selection — no separate selector needed here
    mgmt_dbs  = selected_dbs
    mgmt_acct = selected_account
    st.caption(f"Account: **{selected_label}** — change via the sidebar selector")

    sub_add, sub_edit = st.tabs(["➕  Add Transaction", "✏️  Edit / Delete"])

    # ── ADD ─────────────────────────────────────────────────────────────────
    with sub_add:
        st.subheader("Manual Transaction Entry")
        with st.form("manual_tx_form", clear_on_submit=True):
            r1c1, r1c2, r1c3 = st.columns(3)
            with r1c1:
                mt_label = st.selectbox("Transaction Type", _TX_LABELS)
                mt_type  = _TX_TYPE_MAP[mt_label]
            with r1c2:
                mt_date = st.date_input("Date", value=today)
            with r1c3:
                mt_ticker = st.text_input("Ticker (leave blank for cash txns)").upper().strip()

            r2c1, r2c2, r2c3 = st.columns(3)
            with r2c1:
                mt_qty = st.number_input("Quantity", value=0.0, min_value=0.0,
                                         step=0.0001, format="%.4f")
            with r2c2:
                mt_price = st.number_input("Price per share ($)", value=0.0,
                                           min_value=0.0, step=0.01, format="%.4f")
            with r2c3:
                outflow_hint = " (enter as negative for outflows)" if mt_type not in _OUTFLOW_TYPES else " (enter as negative)"
                mt_amount = st.number_input(f"Amount ($){outflow_hint}",
                                            value=0.0, step=0.01, format="%.2f")

            mt_desc = st.text_input("Description (optional)")
            submitted = st.form_submit_button("Add Transaction", type="primary")

        # Resolve which db to write to (use sidebar account; if All Accounts, require specific)
        if selected_label == "All Accounts":
            mt_db = mt_acct_name = None
            if submitted:
                st.warning("Please select a specific account in the sidebar before adding a transaction.")
        else:
            mt_db_path2, mt_acct_name2 = db_for_account[selected_label]
            mt_db, mt_acct_name = mt_db_path2, mt_acct_name2

        if submitted and mt_db:
            ok, msg = _insert_transaction(
                mt_db, mt_acct_name,
                mt_date.strftime("%m/%d/%Y"),
                mt_type,
                mt_ticker or None,
                mt_desc,
                mt_qty   if mt_qty   != 0 else None,
                mt_price if mt_price != 0 else None,
                mt_amount if mt_amount != 0 else None,
            )
            if ok:
                st.success(msg)
                st.cache_data.clear()
            else:
                st.error(msg)

    # ── EDIT / DELETE ────────────────────────────────────────────────────────
    with sub_edit:
        st.subheader("Edit or Delete Transactions")

        ef1, ef2, ef3 = st.columns([1, 1, 1])
        with ef1:
            edit_ticker = st.text_input("Filter Ticker", key="et").upper().strip() or None
        with ef2:
            edit_type = st.selectbox("Filter Type", [""] + _TX_LABELS, key="etype")
            edit_type_val = _TX_TYPE_MAP.get(edit_type) if edit_type else None
        with ef3:
            edit_limit = st.number_input("Max rows", value=100, min_value=10,
                                          max_value=2000, step=50, key="elimit")

        @st.cache_data(ttl=30)
        def _load_edit_txns(dbs_tuple, account, ticker, tx_type):
            return get_transactions_df(list(dbs_tuple), account=account,
                                       ticker=ticker, tx_type=tx_type,
                                       include_hash=True)

        df_edit_raw = _load_edit_txns(
            tuple(mgmt_dbs), mgmt_acct, edit_ticker, edit_type_val
        ).head(edit_limit)

        if df_edit_raw.empty:
            st.info("No transactions match the filters.")
        else:
            st.caption(f"{len(df_edit_raw):,} rows shown — check rows to delete, or edit cells then click Save.")

            # Separate hash column; hide it from display but keep for operations
            hashes = df_edit_raw["_hash"].tolist() if "_hash" in df_edit_raw.columns else []
            df_display = df_edit_raw.drop(columns=["_hash"], errors="ignore").copy()
            df_display.insert(0, "🗑 Delete", False)

            edited = st.data_editor(
                df_display,
                width='stretch',
                hide_index=True,
                num_rows="fixed",
                column_config={
                    "🗑 Delete": st.column_config.CheckboxColumn("🗑", width="small"),
                    "Date":        st.column_config.TextColumn("Date"),
                    "Ticker":      st.column_config.TextColumn("Ticker"),
                    "Type":        st.column_config.SelectboxColumn(
                                       "Type", options=list(_TX_TYPE_MAP.values())),
                    "Qty":         st.column_config.NumberColumn("Qty",   format="%.4f"),
                    "Price":       st.column_config.NumberColumn("Price", format="%.4f"),
                    "Amount":      st.column_config.NumberColumn("Amount", format="%.2f"),
                    "Description": st.column_config.TextColumn("Description"),
                },
                key="tx_editor",
            )

            col_del, col_save, _ = st.columns([1, 1, 3])
            with col_del:
                if st.button("🗑 Delete Checked", type="secondary"):
                    to_delete = [hashes[i] for i, row in edited.iterrows()
                                 if row.get("🗑 Delete") and i < len(hashes)]
                    if to_delete:
                        # Delete from all matching dbs
                        for db in mgmt_dbs:
                            _delete_transactions(db, to_delete)
                        st.success(f"Deleted {len(to_delete)} transaction(s).")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.warning("No rows checked for deletion.")

            with col_save:
                if st.button("💾 Save Edits", type="primary"):
                    saved = 0
                    orig = df_display.drop(columns=["🗑 Delete"], errors="ignore")
                    edit_data = edited.drop(columns=["🗑 Delete"], errors="ignore")
                    for i in range(min(len(orig), len(edit_data), len(hashes))):
                        if not orig.iloc[i].equals(edit_data.iloc[i]):
                            row = edit_data.iloc[i]
                            # Find which db has this hash
                            import sqlite3 as _sq2
                            for db in mgmt_dbs:
                                c = _sq2.connect(db)
                                exists = c.execute("SELECT 1 FROM transactions WHERE tx_hash=?",
                                                   (hashes[i],)).fetchone()
                                c.close()
                                if exists:
                                    _update_transaction(
                                        db, hashes[i],
                                        str(row.get("Date", "")),
                                        str(row.get("Ticker", "") or ""),
                                        str(row.get("Type", "")),
                                        row.get("Qty"),
                                        row.get("Price"),
                                        row.get("Amount"),
                                        str(row.get("Description", "") or ""),
                                    )
                                    saved += 1
                                    break
                    if saved:
                        st.success(f"Saved {saved} change(s).")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.info("No changes detected.")


    # ── DELISTED TICKERS ──────────────────────────────────────────────────
    st.divider()
    st.subheader("Delisted / Liquidated Tickers")
    st.caption(
        "Tickers marked here are excluded from price-fetch attempts and shown with a "
        "DELISTED badge on position cards. Their position value uses the final price "
        "you record (enter 0 for a full liquidation)."
    )

    _dl_all = load_delisted()
    if _dl_all:
        import pandas as _pd2
        _dl_rows = [
            {"Ticker": k, "Final Price": v["final_price"], "Notes": v["notes"]}
            for k, v in sorted(_dl_all.items())
        ]
        st.dataframe(_pd2.DataFrame(_dl_rows), width='stretch', hide_index=True,
                     column_config={
                         "Final Price": st.column_config.NumberColumn(format="$%.4f"),
                     })
        _rm_ticker = st.selectbox("Restore ticker (remove from delisted list)",
                                  options=[""] + sorted(_dl_all.keys()),
                                  key="dl_restore_sel")
        if st.button("Restore selected", key="dl_restore_btn") and _rm_ticker:
            unmark_ticker_delisted(_rm_ticker)
            st.cache_data.clear()
            st.rerun()
    else:
        st.info("No tickers currently marked as delisted.")

    st.markdown("**Add a delisted ticker manually:**")
    with st.form("add_delisted_form", clear_on_submit=True):
        _adl_c1, _adl_c2, _adl_c3 = st.columns([1, 1, 2])
        with _adl_c1:
            _adl_ticker = st.text_input("Ticker").upper().strip()
        with _adl_c2:
            _adl_price = st.number_input("Final price ($)", value=0.0,
                                         min_value=0.0, step=0.01, format="%.4f")
        with _adl_c3:
            _adl_notes = st.text_input("Notes", placeholder="e.g. Liquidated Mar 2025")
        if st.form_submit_button("Mark as Delisted", type="primary"):
            if _adl_ticker:
                mark_ticker_delisted(_adl_ticker, _adl_price, _adl_notes)
                st.cache_data.clear()
                st.success(f"{_adl_ticker} marked as delisted.")
                st.rerun()
            else:
                st.warning("Enter a ticker symbol.")


# ═══════════════════════════════════════════════════════════════════════════
# TAB 7 — Stock Lookup
# ═══════════════════════════════════════════════════════════════════════════
with tab_lookup:
    import yfinance as _yf
    from plotly.subplots import make_subplots as _make_subplots

    @st.cache_data(ttl=300)
    def _fetch_stock(symbol: str):
        obj  = _yf.Ticker(symbol)
        hist = obj.history(period="1y")
        info = obj.info
        # S&P 500 benchmark for return comparison
        spy  = _yf.Ticker("SPY").history(period="1y")["Close"]
        return hist, info, spy

    def _fmt_large(v):
        if v is None: return "—"
        if v >= 1e12: return f"${v/1e12:.2f}T"
        if v >= 1e9:  return f"${v/1e9:.2f}B"
        if v >= 1e6:  return f"${v/1e6:.2f}M"
        return f"${v:,.0f}"

    def _fmt_pct_plain(v):
        if v is None: return "—"
        return f"{v*100:+.2f}%" if abs(v) < 10 else f"{v:+.2f}%"

    # ── Search bar ────────────────────────────────────────────────────────
    lk_col1, lk_col2 = st.columns([3, 1])
    with lk_col1:
        lk_input = st.text_input("Enter ticker symbol", placeholder="e.g. AAPL, TSLA, BTC-USD",
                                  label_visibility="collapsed").upper().strip()
    with lk_col2:
        lk_go = st.button("Look Up", type="primary", width='stretch')

    if "lookup_ticker" not in st.session_state:
        st.session_state["lookup_ticker"] = ""
    if lk_go and lk_input:
        st.session_state["lookup_ticker"] = lk_input
    symbol = st.session_state["lookup_ticker"]

    if symbol:
        with st.spinner(f"Fetching {symbol}…"):
            try:
                hist, info, spy = _fetch_stock(symbol)
            except Exception as e:
                st.error(f"Could not fetch data for {symbol}: {e}")
                hist = None

        if hist is None or hist.empty:
            st.warning(f"No price data found for **{symbol}**. Check the ticker and try again.")
        else:
            # ── Company header ────────────────────────────────────────────
            name    = info.get("longName") or info.get("shortName") or symbol
            sector  = info.get("sector", "")
            industry = info.get("industry", "")
            exchange = info.get("exchange", "")
            header_sub = "  ·  ".join(filter(None, [exchange, sector, industry]))

            st.subheader(f"{name}  ({symbol})")
            if header_sub:
                st.caption(header_sub)

            summary_text = info.get("longBusinessSummary", "")
            if summary_text:
                with st.expander("About"):
                    st.write(summary_text)

            st.divider()

            # ── Key stats ─────────────────────────────────────────────────
            price   = info.get("currentPrice") or info.get("regularMarketPrice") or hist["Close"].iloc[-1]
            prev    = info.get("previousClose") or hist["Close"].iloc[-2] if len(hist) > 1 else price
            chg     = price - prev
            chg_pct = chg / prev * 100 if prev else 0

            wk52_hi  = info.get("fiftyTwoWeekHigh")  or hist["High"].max()
            wk52_lo  = info.get("fiftyTwoWeekLow")   or hist["Low"].min()
            pct_from_hi = (price - wk52_hi) / wk52_hi * 100 if wk52_hi else None
            pct_from_lo = (price - wk52_lo) / wk52_lo * 100 if wk52_lo else None

            mkt_cap   = info.get("marketCap")
            pe        = info.get("trailingPE")
            fwd_pe    = info.get("forwardPE")
            div_yield = info.get("dividendYield")
            avg_vol   = info.get("averageVolume") or info.get("averageDailyVolume10Day")
            beta      = info.get("beta")
            eps       = info.get("trailingEps")

            k1, k2, k3, k4, k5, k6 = st.columns(6)
            chg_color = "#4ade80" if chg >= 0 else "#f87171"
            with k1:
                st.markdown(f"""<div class="metric-card">
                    <div class="metric-label">Price</div>
                    <div class="metric-value neutral">${price:,.4f}</div>
                    <div class="metric-label" style="color:{chg_color};margin-top:4px">
                        {'+' if chg>=0 else ''}{chg:,.2f} ({'+' if chg_pct>=0 else ''}{chg_pct:.2f}%)
                    </div></div>""", unsafe_allow_html=True)
            with k2:
                metric_card("Market Cap", _fmt_large(mkt_cap))
            with k3:
                pe_str = f"{pe:.1f}x" if pe else "—"
                fwd_str = f"Fwd: {fwd_pe:.1f}x" if fwd_pe else None
                metric_card("P/E (TTM)", pe_str, sub=fwd_str)
            with k4:
                metric_card("Div Yield", _fmt_pct_plain(div_yield) if div_yield else "—")
            with k5:
                metric_card("Beta", f"{beta:.2f}" if beta else "—")
            with k6:
                vol_str = f"{hist['Volume'].iloc[-1]/1e6:.1f}M" if not hist.empty else "—"
                avg_str = f"Avg: {avg_vol/1e6:.1f}M" if avg_vol else None
                metric_card("Volume", vol_str, sub=avg_str)

            # 52-week range row
            wk_c1, wk_c2, wk_c3 = st.columns(3)
            with wk_c1:
                lo_color = "#4ade80" if (pct_from_lo or 0) > 20 else "#fbbf24"
                metric_card("52W Low", f"${wk52_lo:,.2f}" if wk52_lo else "—",
                             sub=f"{'+' if (pct_from_lo or 0)>=0 else ''}{pct_from_lo:.1f}% above low" if pct_from_lo else None,
                             sub_color="positive" if (pct_from_lo or 0) > 0 else "negative")
            with wk_c2:
                hi_color = "#4ade80" if (pct_from_hi or 0) > -10 else "#f87171"
                metric_card("52W High", f"${wk52_hi:,.2f}" if wk52_hi else "—",
                             sub=f"{pct_from_hi:.1f}% from high" if pct_from_hi else None,
                             sub_color="negative" if (pct_from_hi or 0) < -10 else "positive")
            with wk_c3:
                ytd_start = hist[hist.index >= f"{today.year}-01-01"]["Close"].iloc[0] \
                            if not hist[hist.index >= f"{today.year}-01-01"].empty else hist["Close"].iloc[0]
                ytd_ret = (price - ytd_start) / ytd_start * 100
                metric_card("YTD Return", f"{'+' if ytd_ret>=0 else ''}{ytd_ret:.2f}%",
                             sub_color="positive" if ytd_ret >= 0 else "negative")

            st.divider()

            # ── Price chart with MAs + volume ─────────────────────────────
            hist.index = hist.index.tz_localize(None) if hist.index.tz else hist.index
            closes = hist["Close"]
            ma50  = closes.rolling(50).mean()
            ma200 = closes.rolling(200).mean()

            fig_lk = _make_subplots(
                rows=2, cols=1, shared_xaxes=True,
                vertical_spacing=0.04, row_heights=[0.75, 0.25],
            )

            # Candlestick
            fig_lk.add_trace(go.Candlestick(
                x=hist.index, open=hist["Open"], high=hist["High"],
                low=hist["Low"],  close=hist["Close"],
                name=symbol,
                increasing_line_color="#4ade80", decreasing_line_color="#f87171",
                increasing_fillcolor="#4ade80", decreasing_fillcolor="#f87171",
            ), row=1, col=1)

            # 50-day MA
            fig_lk.add_trace(go.Scatter(
                x=hist.index, y=ma50, name="50d MA",
                line=dict(color="#fbbf24", width=1.5),
            ), row=1, col=1)

            # 200-day MA (only if enough data)
            if ma200.notna().sum() >= 10:
                fig_lk.add_trace(go.Scatter(
                    x=hist.index, y=ma200, name="200d MA",
                    line=dict(color="#a78bfa", width=1.5, dash="dot"),
                ), row=1, col=1)

            # 52-week high/low reference lines
            if wk52_hi:
                fig_lk.add_hline(y=wk52_hi, line_color="#4ade80", line_dash="dash",
                                  line_width=1, opacity=0.5, row=1, col=1)
            if wk52_lo:
                fig_lk.add_hline(y=wk52_lo, line_color="#f87171", line_dash="dash",
                                  line_width=1, opacity=0.5, row=1, col=1)

            # Volume bars
            vol_colors = ["#4ade80" if c >= o else "#f87171"
                          for c, o in zip(hist["Close"], hist["Open"])]
            fig_lk.add_trace(go.Bar(
                x=hist.index, y=hist["Volume"],
                name="Volume", marker_color=vol_colors, opacity=0.7,
            ), row=2, col=1)

            fig_lk.update_layout(
                title=f"{symbol} — 1 Year",
                height=560,
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#ccc"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                xaxis_rangeslider_visible=False,
                yaxis=dict(showgrid=True, gridcolor="#2a2a3e", tickprefix="$"),
                yaxis2=dict(showgrid=False),
                xaxis2=dict(showgrid=False),
                hovermode="x unified",
            )
            st.plotly_chart(fig_lk, width='stretch')

            # ── Return comparison vs S&P 500 ──────────────────────────────
            st.subheader("Return vs S&P 500 (1 Year)")
            if not spy.empty:
                spy.index = spy.index.tz_localize(None) if spy.index.tz else spy.index
                common_start = max(closes.index[0], spy.index[0])
                stk_norm = closes[closes.index >= common_start] / closes[closes.index >= common_start].iloc[0] * 100
                spy_norm = spy[spy.index >= common_start] / spy[spy.index >= common_start].iloc[0] * 100

                fig_cmp = go.Figure()
                fig_cmp.add_trace(go.Scatter(
                    x=stk_norm.index, y=stk_norm,
                    name=symbol, line=dict(color="#60a5fa", width=2),
                ))
                fig_cmp.add_trace(go.Scatter(
                    x=spy_norm.index, y=spy_norm,
                    name="S&P 500 (SPY)", line=dict(color="#94a3b8", width=1.5, dash="dot"),
                ))
                fig_cmp.add_hline(y=100, line_color="#555", line_dash="dash", line_width=1)
                stk_final = stk_norm.iloc[-1] - 100
                spy_final = spy_norm.iloc[-1] - 100
                fig_cmp.update_layout(
                    title=f"{symbol} {'+' if stk_final>=0 else ''}{stk_final:.1f}%  vs  "
                          f"S&P 500 {'+' if spy_final>=0 else ''}{spy_final:.1f}%",
                    height=280,
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#ccc"),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                    yaxis=dict(showgrid=True, gridcolor="#2a2a3e", ticksuffix="%"),
                    xaxis=dict(showgrid=False),
                    hovermode="x unified",
                )
                st.plotly_chart(fig_cmp, width='stretch')

            # ── Fundamentals table ────────────────────────────────────────
            with st.expander("Fundamentals"):
                fund_data = {
                    "EPS (TTM)":          f"${eps:.2f}" if eps else "—",
                    "P/E (TTM)":          f"{pe:.1f}x" if pe else "—",
                    "Forward P/E":        f"{fwd_pe:.1f}x" if fwd_pe else "—",
                    "Price/Book":         f"{info.get('priceToBook'):.2f}x" if info.get('priceToBook') else "—",
                    "Revenue Growth":     _fmt_pct_plain(info.get("revenueGrowth")),
                    "Earnings Growth":    _fmt_pct_plain(info.get("earningsGrowth")),
                    "Gross Margin":       _fmt_pct_plain(info.get("grossMargins")),
                    "Operating Margin":   _fmt_pct_plain(info.get("operatingMargins")),
                    "Profit Margin":      _fmt_pct_plain(info.get("profitMargins")),
                    "Return on Equity":   _fmt_pct_plain(info.get("returnOnEquity")),
                    "Return on Assets":   _fmt_pct_plain(info.get("returnOnAssets")),
                    "Debt/Equity":        f"{info.get('debtToEquity'):.1f}" if info.get('debtToEquity') else "—",
                    "Current Ratio":      f"{info.get('currentRatio'):.2f}" if info.get('currentRatio') else "—",
                    "Shares Outstanding": _fmt_large(info.get("sharesOutstanding")).replace("$", "") if info.get("sharesOutstanding") else "—",
                    "Float":              _fmt_large(info.get("floatShares")).replace("$", "")       if info.get("floatShares") else "—",
                    "Short % of Float":   _fmt_pct_plain(info.get("shortPercentOfFloat")),
                    "Analyst Target":     f"${info.get('targetMeanPrice'):.2f}" if info.get('targetMeanPrice') else "—",
                    "Analyst Rating":     info.get("recommendationKey", "—").replace("_", " ").title(),
                }
                fc1, fc2 = st.columns(2)
                items = list(fund_data.items())
                half = len(items) // 2 + len(items) % 2
                with fc1:
                    for k, v in items[:half]:
                        st.markdown(f"**{k}:** {v}")
                with fc2:
                    for k, v in items[half:]:
                        st.markdown(f"**{k}:** {v}")
    else:
        st.info("Enter a ticker symbol above and click **Look Up** to see price history and stats.")
