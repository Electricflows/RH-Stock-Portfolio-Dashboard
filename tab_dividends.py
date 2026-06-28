"""
tab_dividends.py — Dividends tab render function.
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from datetime import date, timedelta, datetime

from ui_helpers import _fmt_dollar, metric_card
from loaders import load_transactions


def render(selected_dbs: list, selected_account, today: date):
    _div_df = load_transactions(tuple(selected_dbs), selected_account, None, "dividend")
    _cg_df  = load_transactions(tuple(selected_dbs), selected_account, None, "capital_gains_distribution")
    _all_income = pd.concat([_div_df, _cg_df], ignore_index=True) if not _cg_df.empty else _div_df.copy()

    if _all_income.empty:
        st.info("No dividend or capital gains transactions found.")
        return

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
    _proj_rows = []
    _single_payment_tickers = []   # tickers skipped due to only 1 payment
    for _tk in _hist_pos["Ticker"].unique():
        _tk_hist = _hist_pos[_hist_pos["Ticker"] == _tk]
        if len(_tk_hist) == 1:
            _single_payment_tickers.append(_tk)
            continue
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
        metric_card("Total Income", _fmt_dollar(_total_divs), sub="&nbsp;")
    with dv2:
        metric_card(f"{_this_year} YTD", _fmt_dollar(_ytd_divs), sub="&nbsp;")
    with dv3:
        metric_card("Trailing 12M", _fmt_dollar(_last_12m), sub="&nbsp;")
    with dv4:
        metric_card("Paying Tickers", str(_payers), sub="&nbsp;")
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
    if _single_payment_tickers:
        st.caption(
            f"⚠️ No projection for {', '.join(sorted(_single_payment_tickers))} — "
            "only 1 payment recorded; need at least 2 to detect a payment interval."
        )

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
