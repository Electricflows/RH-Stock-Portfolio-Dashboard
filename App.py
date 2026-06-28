"""
App.py  —  Portfolio Dashboard
================================
Run with:  streamlit run App.py
"""

import json
import os
from pathlib import Path

# Always resolve paths relative to this file, regardless of where the app is launched from
os.chdir(Path(__file__).parent)

import streamlit as st
import pandas as pd
from datetime import date, timedelta

from Calculations import (
    get_account_dbs,
    get_account_names,
)
from Prices import (
    check_and_fill_price_gaps,
)
from Import import ensure_indexes

from loaders import (
    load_delisted, load_summary, load_daily,
)
import tab_overview
import tab_performance
import tab_positions
import tab_dividends
import tab_compare
import tab_watchlist
import tab_lookup
import tab_transactions
import tab_import
import tab_manage

# ---------------------------------------------------------------------------
# File I/O helpers (stay in App.py per project rules)
# ---------------------------------------------------------------------------

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

def save_ignored_tickers(tickers: set):
    IGNORED_TICKERS_FILE.write_text(json.dumps(sorted(tickers)))

SETTINGS_FILE = Path("settings.json")

def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except Exception:
            pass
    return {}

def save_settings(d: dict):
    existing = load_settings()
    existing.update(d)
    SETTINGS_FILE.write_text(json.dumps(existing, indent=2))

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
        height: 100%;
        box-sizing: border-box;
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

    /* Equal-height cards per row */
    div[data-testid="stHorizontalBlock"] { align-items: stretch; }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]
        > div > div > div[data-testid="stMarkdownContainer"] { height: 100%; }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]
        > div > div > div[data-testid="stMarkdownContainer"] > div.metric-card { height: 100%; }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
        display: flex; flex-direction: column;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]
        > div[data-testid="stVerticalBlockBorderWrapper"],
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]
        > div[data-testid="stVerticalBlockBorderWrapper"] > div[data-testid="stVerticalBlock"] {
        flex: 1; display: flex; flex-direction: column;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] .pos-card {
        flex: 1;
    }
</style>
""", unsafe_allow_html=True)

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

IS_CLOUD = os.path.exists("/mount/src")
_settings = load_settings()

with st.sidebar:
    st.title("📈 Portfolio")
    if IS_CLOUD:
        st.info(
            "**Demo mode** — running on Streamlit Cloud. "
            "Some live data (sector info, fundamentals) may be unavailable due to API rate limits. "
            "Run locally for full functionality.",
            icon="☁️",
        )
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
    _conc_enabled = st.checkbox(
        "Concentration Alerts",
        value=_settings.get("conc_enabled", True),
        key="conc_enabled_chk",
        help="Show warnings when a single stock or sector exceeds the thresholds below.",
    )
    if _conc_enabled != _settings.get("conc_enabled", True):
        save_settings({"conc_enabled": _conc_enabled})
    if _conc_enabled:
        _stock_thresh = st.slider(
            "Per-stock threshold (%)",
            min_value=5, max_value=50,
            value=_settings.get("conc_stock_thresh", 20), step=5,
            help="Warn when a single position exceeds this % of total portfolio value.",
            key="conc_stock_thresh",
        )
        _sector_thresh = st.slider(
            "Per-sector threshold (%)",
            min_value=10, max_value=80,
            value=_settings.get("conc_sector_thresh", 40), step=5,
            help="Warn when a single sector exceeds this % of total portfolio value.",
            key="conc_sector_thresh",
        )
        if (_stock_thresh != _settings.get("conc_stock_thresh", 20) or
                _sector_thresh != _settings.get("conc_sector_thresh", 40)):
            save_settings({"conc_stock_thresh": _stock_thresh, "conc_sector_thresh": _sector_thresh})
    else:
        _stock_thresh  = _settings.get("conc_stock_thresh", 20)
        _sector_thresh = _settings.get("conc_sector_thresh", 40)

    st.divider()
    st.subheader("Custom Benchmarks")
    _custom_bm_raw = st.text_input(
        "Extra tickers (comma-separated)",
        key="custom_benchmarks",
        placeholder="e.g. QQQ, ARKK, BRK-B",
        help="Any Yahoo Finance ticker. Will appear in the benchmark overlay selector on Performance and detail charts.",
    )
    _custom_bm_tickers = [t.strip().upper() for t in _custom_bm_raw.split(",") if t.strip()]

    st.divider()
    if st.button("🔄  Refresh Data", width='stretch'):
        st.cache_data.clear()

# ---------------------------------------------------------------------------
# Benchmark dicts (depend on sidebar _custom_bm_tickers at runtime)
# ---------------------------------------------------------------------------

_BUILTIN_BENCHMARKS = {
    "S&P 500":   "^GSPC",
    "Dow Jones": "^DJI",
    "Nasdaq":    "^IXIC",
    "Russell 2000": "^RUT",
    "Total Market (VTI)": "VTI",
    "60/40 (AOR)": "AOR",
}
_BUILTIN_BENCHMARK_COLORS = {
    "S&P 500":            "#f97316",
    "Dow Jones":          "#38bdf8",
    "Nasdaq":             "#e879f9",
    "Russell 2000":       "#34d399",
    "Total Market (VTI)": "#a78bfa",
    "60/40 (AOR)":        "#fb923c",
}
_CUSTOM_BM_COLORS = ["#f43f5e", "#22d3ee", "#84cc16", "#eab308", "#c084fc"]

# Will be extended at runtime with any custom tickers the user adds
BENCHMARKS: dict        = dict(_BUILTIN_BENCHMARKS)
BENCHMARK_COLORS: dict  = dict(_BUILTIN_BENCHMARK_COLORS)

for _i, _ct in enumerate(_custom_bm_tickers):
    if _ct not in BENCHMARKS:
        BENCHMARKS[_ct] = _ct
        BENCHMARK_COLORS[_ct] = _CUSTOM_BM_COLORS[_i % len(_CUSTOM_BM_COLORS)]

# ---------------------------------------------------------------------------
# Main data load
# ---------------------------------------------------------------------------

summary  = load_summary(tuple(selected_dbs), selected_account)
excluded = load_excluded_positions()
chart    = load_daily(tuple(selected_dbs), selected_account,
                      start_date, today.isoformat(),
                      excluded_tuple=tuple(sorted(excluded)))

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

# Keep full positions list (including excluded) for the Positions tab display
all_open_positions = summary["open_positions"]
summary = _apply_exclusions(summary, excluded)

# ---------------------------------------------------------------------------
# Tabs — orchestration
# ---------------------------------------------------------------------------

tab_overview_ui, tab_positions_ui, tab_performance_ui, tab_dividends_ui, tab_transactions_ui, tab_import_ui, tab_manage_ui, tab_lookup_ui, tab_compare_ui, tab_watchlist_ui = st.tabs([
    "📊  Overview", "💼  Positions", "📈  Performance", "📅  Dividends", "🗒  Transactions", "📥  Import", "✏️  Manage", "🔍  Lookup", "⚖️  Compare", "👁  Watchlist",
])

with tab_overview_ui:
    tab_overview.render(
        summary=summary,
        _conc_enabled=_conc_enabled,
        _stock_thresh=_stock_thresh,
        _sector_thresh=_sector_thresh,
    )

with tab_positions_ui:
    tab_positions.render(
        summary=summary,
        all_open_positions=all_open_positions,
        excluded=excluded,
        selected_dbs=selected_dbs,
        selected_account=selected_account,
        start_date=start_date,
        today=today,
        period=period,
        BENCHMARKS=BENCHMARKS,
        BENCHMARK_COLORS=BENCHMARK_COLORS,
        delisted=delisted,
        load_excluded_positions_fn=load_excluded_positions,
        save_excluded_positions_fn=save_excluded_positions,
    )

with tab_performance_ui:
    tab_performance.render(
        chart=chart,
        BENCHMARKS=BENCHMARKS,
        BENCHMARK_COLORS=BENCHMARK_COLORS,
    )

with tab_dividends_ui:
    tab_dividends.render(
        selected_dbs=selected_dbs,
        selected_account=selected_account,
        today=today,
    )

with tab_transactions_ui:
    tab_transactions.render(
        selected_dbs=selected_dbs,
        selected_account=selected_account,
    )

with tab_import_ui:
    tab_import.render()

with tab_manage_ui:
    tab_manage.render(
        selected_dbs=selected_dbs,
        selected_account=selected_account,
        selected_label=selected_label,
        today=today,
        db_for_account=db_for_account,
    )

with tab_lookup_ui:
    tab_lookup.render(today=today)

with tab_compare_ui:
    tab_compare.render(summary=summary)

with tab_watchlist_ui:
    tab_watchlist.render(summary=summary)
