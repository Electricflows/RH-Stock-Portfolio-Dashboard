"""
tab_performance.py — Performance tab render function.
"""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd

from ui_helpers import (
    _fmt_dollar, _fmt_pct, _color_class,
    metric_card, _align_benchmark,
)
from loaders import load_benchmark_history


def render(chart: dict, BENCHMARKS: dict, BENCHMARK_COLORS: dict):
    c = chart

    if not c["dates"]:
        st.info("No price data available for the selected period.")
        return

    # Performance metric row
    _perf_days = c.get("total_days", 0)
    _perf_dlbl = f" · {_perf_days}d" if _perf_days else ""
    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        metric_card("Start Value", _fmt_dollar(c["start_value"]), sub="&nbsp;")
    with m2:
        metric_card("End Value", _fmt_dollar(c["end_value"]), sub="&nbsp;")
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
                        sub="&nbsp;", sub_color=_color_class(c["twr_total"]))
    with m5:
        _pmwr = c.get("mwr_annualized")
        if _pmwr is not None:
            metric_card(f"Ann. MWR{_perf_dlbl}",
                        _fmt_pct(_pmwr, signed=True),
                        sub="&nbsp;", sub_color=_color_class(_pmwr))
        else:
            metric_card(f"Ann. MWR{_perf_dlbl}", "—", sub="&nbsp;")

    st.divider()

    # ── Portfolio value chart ─────────────────────────────────────────────────
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

    # ── TWR cumulative return chart ───────────────────────────────────────────
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
        bm_data = load_benchmark_history(
            c["dates"][0], c["dates"][-1],
            tuple(selected_benchmarks),
            benchmarks_map=tuple(BENCHMARKS.items()),
        )

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
