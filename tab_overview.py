"""
tab_overview.py — Overview tab render function.
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px

from ui_helpers import (
    _fmt_dollar, _fmt_pct, _color_class, _fmt_large,
    metric_card,
)
from loaders import load_ticker_fundamentals


def render(summary: dict, _conc_enabled: bool, _stock_thresh: int, _sector_thresh: int):
    s = summary

    # Top metric row
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        metric_card("Account Total", _fmt_dollar(s["account_total"]),
                    sub=f"as of {s['as_of']}")
    with c2:
        metric_card("Invested (Market)", _fmt_dollar(s["total_market_value"]), sub="&nbsp;")
    with c3:
        metric_card("Cash Balance", _fmt_dollar(s["cash_balance"]), sub="&nbsp;")
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

    if _conc_enabled and _port_total > 0 and s["open_positions"]:
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

        # ── Capital composition bar ───────────────────────────────────────────
        _acct_total = s.get("account_total") or 0
        _cost       = s.get("total_cost_basis") or 0
        _profit     = _acct_total - _cost
        if _acct_total > 0:
            _cost_pct   = _cost   / _acct_total * 100
            _profit_pct = _profit / _acct_total * 100
            _p_color    = "#4ade80" if _profit >= 0 else "#f87171"
            _p_label    = "Profit" if _profit >= 0 else "Loss"
            st.markdown(
                f"<div style='margin-top:4px'>"
                f"<div style='font-size:0.72rem;color:#888;margin-bottom:4px;text-transform:uppercase;letter-spacing:.05em'>"
                f"Capital Composition &nbsp;·&nbsp; "
                f"<span style='color:#94a3b8'>{_cost_pct:.1f}% at cost</span>"
                f" &nbsp;/&nbsp; "
                f"<span style='color:{_p_color}'>{abs(_profit_pct):.1f}% {_p_label.lower()}"
                f" ({_fmt_dollar(_profit, signed=True)})</span>"
                f"</div>"
                f"<div style='display:flex;height:10px;border-radius:5px;overflow:hidden'>"
                f"<div style='width:{_cost_pct:.1f}%;background:#475569'></div>"
                f"<div style='width:{abs(_profit_pct):.1f}%;background:{_p_color};opacity:0.8'></div>"
                f"</div></div>",
                unsafe_allow_html=True,
            )

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
