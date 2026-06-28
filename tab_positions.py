"""
tab_positions.py — Positions tab render function.
"""

import bisect
import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from datetime import date, timedelta, datetime

from ui_helpers import (
    _fmt_dollar, _fmt_pct, _color_class, _fmt_large,
    _calc_roic, _calc_quality_score, _quality_color,
    _is_equity, _fmt_earnings_date,
    _render_dcf, _render_indicators, _align_benchmark,
    metric_card,
)
from loaders import (
    load_ticker_names, load_52week, load_ticker_fundamentals,
    load_ticker_extended, load_all_ticker_twrs,
    load_ticker_daily, load_benchmark_history, load_transactions,
)
from Calculations import BUY_TYPES, SELL_TYPES, TRANSFER_IN_TYPES


# ---------------------------------------------------------------------------
# Module-level helpers (only used in this tab)
# ---------------------------------------------------------------------------

def _parse_lot_date(s: str):
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _all_lots_long_term(open_lots: list, today: date) -> bool:
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


def _render_open_card(p, week52, names, twrs, delisted, excluded, today, fundamentals=None):
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
    _pe   = funds.get("pe")
    _mc   = funds.get("market_cap")
    _tgt  = funds.get("target_mean")
    _roic = _calc_roic(funds)
    _qs, _qs_details, _qs_growth = _calc_quality_score(funds)

    pe_str   = f"{_pe:.1f}x"  if _pe   else "—"
    mc_str   = _fmt_large(_mc)
    tgt_str  = f"${_tgt:.2f}" if _tgt  else "—"
    roic_str    = f"{_roic*100:.1f}%" if _roic is not None else "—"
    qs_color    = _quality_color(_qs)
    qs_str      = str(_qs) if _qs is not None else "—"
    _earn_str   = _fmt_earnings_date(funds)
    _equity     = _is_equity(funds)
    _quote_type = funds.get("quote_type", "EQUITY").upper()
    _is_etf     = _quote_type in ("ETF", "MUTUALFUND")
    _sector     = funds.get("sector", "") or ""
    _type_badge = ('<span style="background:#1e3a5f;color:#60a5fa;border-radius:4px;'
                   'padding:2px 7px;font-size:0.7rem;font-weight:700">ETF</span>'
                   if _is_etf else '<span></span>')
    _sector_badge = ""
    # Compact date format for card: "7/28/26"
    _earn_short = ""
    if _earn_str and _equity:
        try:
            _ed = datetime.strptime(_earn_str.split(" · ")[0].strip(), "%b %d, %Y")
            _earn_short = f"{_ed.month}/{_ed.day}/{str(_ed.year)[2:]}"
        except Exception:
            _earn_short = ""
    earn_html   = ""

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
    ltcg = _all_lots_long_term(p.get("open_lots", []), today)
    ltcg_html = '<span title="All lots held over 1 year - Long-Term Capital Gains" style="font-size:1.1rem;cursor:default">⭐</span>' if ltcg else ""

    _dl_info = delisted.get(t.upper())
    if _dl_info is not None:
        _dl_notes = _dl_info.get("notes", "")
        _dl_tip = f'title="{_dl_notes}"' if _dl_notes else ""
        delisted_badge = f'<span {_dl_tip} style="background:#7f1d1d;color:#fca5a5;border-radius:4px;padding:2px 7px;font-size:0.7rem;font-weight:700;cursor:default">DELISTED</span>'
    else:
        delisted_badge = ""

    _is_excl = t in excluded
    excl_badge = ""
    card_opacity = "opacity:0.6;" if _is_excl else ""

    st.markdown(f"""
    <div class="pos-card" style="{card_opacity}">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <span class="pos-ticker">{t}</span>
        <span class="pos-price">{lp}</span>
        <span class="{badge_cls}">{sign}{unrp:.2f}%</span>
        {_type_badge}
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
      {f'''
      <div class="pos-row">
        <div class="pos-kv"><div class="pos-k">P/E (TTM)</div><div class="pos-v">{pe_str}</div></div>
        <div class="pos-kv"><div class="pos-k">Mkt Cap</div><div class="pos-v">{mc_str}</div></div>
        <div class="pos-kv"><div class="pos-k">Avg Target</div><div class="pos-v">{tgt_str}</div></div>
      </div>
      <div class="pos-row">
        <div class="pos-kv"><div class="pos-k">ROIC</div><div class="pos-v">{roic_str}</div></div>
        <div class="pos-kv">
          <div class="pos-k">Quality Score</div>
          <div style="margin-top:4px;display:flex;align-items:center;gap:8px">
            <span style="font-size:1.1rem;font-weight:800;color:{qs_color}">{qs_str}</span>
            <span style="font-size:0.72rem;color:#666">/ 100</span>
          </div>
        </div>
        <div class="pos-kv">
          <div class="pos-k">Earnings Date</div>
          <div class="pos-v">{_earn_short if _earn_short else "—"}</div>
        </div>
      </div>''' if _equity else '''
      <div style="min-height:56px"></div>
      <div style="min-height:56px"></div>'''}
      {bar}
    </div>""", unsafe_allow_html=True)

    if st.button("View Details →", key=f"det_{t}", width='stretch'):
        st.session_state["selected_ticker"] = t
        st.rerun()


def _show_detail(ticker, open_pos, closed_pos, selected_dbs, selected_account,
                 start_date, today, period, BENCHMARKS, BENCHMARK_COLORS,
                 delisted, excluded, names=None):
    pos = next((p for p in open_pos + closed_pos if p["ticker"] == ticker), None)

    import json
    from pathlib import Path
    EXCLUDED_POSITIONS_FILE = Path("excluded_positions.json")

    def _save_excluded_positions(tickers: set):
        EXCLUDED_POSITIONS_FILE.write_text(json.dumps(sorted(tickers)))

    _back_col, _excl_col = st.columns([3, 1])
    with _back_col:
        if st.button("← Back to Positions"):
            st.session_state["selected_ticker"] = None
            st.rerun()
    with _excl_col:
        if st.button("Exclude from Portfolio ✕", key=f"excl_det_{ticker}", width='stretch'):
            _excl = set(excluded)
            _excl.add(ticker)
            _save_excluded_positions(_excl)
            st.session_state["selected_ticker"] = None
            st.rerun()

    if names and ticker in names:
        det_name = names[ticker]
    else:
        det_name = load_ticker_names((ticker,)).get(ticker, "")
    det_funds    = load_ticker_fundamentals((ticker,)).get(ticker, {})
    det_extended = load_ticker_extended(ticker)
    _det_lp = pos.get("live_price") if pos else None
    _det_lp_str = f"${_det_lp:,.4f}" if _det_lp else ""
    _det_lots = pos.get("open_lots", []) if pos else []
    _det_ltcg = _all_lots_long_term(_det_lots, today)
    _ltcg_badge = "  ⭐ Long-Term" if _det_ltcg else ""

    if det_name and det_name != ticker:
        st.subheader(f"{ticker} — {det_name}  {_det_lp_str}{_ltcg_badge}")
    else:
        st.subheader(f"{ticker}  {_det_lp_str}{_ltcg_badge}")
    if pos:
        is_open = pos["shares_held"] > 1e-9
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            metric_card("Shares", f"{pos['shares_held']:.4f}", sub="&nbsp;")
        with c2:
            metric_card("Avg Cost", f"${pos['avg_cost']:.4f}", sub="&nbsp;")
        with c3:
            metric_card("Market Value",
                        _fmt_dollar(pos.get("market_value")) if is_open else "—", sub="&nbsp;")
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

        _det_equity = _is_equity(det_funds)
        _pe      = det_funds.get("pe")
        _fwd_pe  = det_funds.get("fwd_pe")
        _mc      = det_funds.get("market_cap")
        _tgt_avg = det_funds.get("target_mean")
        _tgt_hi  = det_funds.get("target_high")
        _tgt_lo  = det_funds.get("target_low")
        _det_roic = _calc_roic(det_funds)
        _det_qs, _det_qs_details, _det_qs_growth = _calc_quality_score(det_funds)

        if _det_equity:
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

        if _det_equity:
            # ROIC + Quality Score row
            _roic_str = f"{_det_roic*100:.1f}%" if _det_roic is not None else "—"
            _qs_color = _quality_color(_det_qs)
            ga, gb = st.columns([1, 5])
            with ga:
                _roic_color = ("neutral" if _det_roic is None else
                               ("positive" if _det_roic >= 0.10 else "negative"))
                st.markdown(f"""
                <div class="metric-card" style="height:100%;box-sizing:border-box">
                    <div class="metric-label">ROIC</div>
                    <div class="metric-value {_roic_color}">{_roic_str}</div>
                    <div class="metric-label" style="margin-top:4px">OCF / (Assets − Cash)</div>
                </div>""", unsafe_allow_html=True)
            with gb:
                if _det_qs is not None:
                    _qs_bar_html = "".join(
                        f'<span title="{lbl}" style="display:inline-block;width:10px;height:10px;'
                        f'border-radius:2px;margin:1px;background:'
                        f'{"#4ade80" if passed is True else "#f87171" if passed is False else "#444"}'
                        f'"></span>'
                        for lbl, passed, _act in _det_qs_details
                    )
                    _gp_banner = (
                        "<div style='margin-top:8px;padding:5px 8px;border-radius:5px;"
                        "background:#1a2a1a;border:1px solid #2d4a2d;font-size:0.72rem;color:#86efac'>"
                        "⚡ Growth Phase — strong revenue growth detected. Low FCF/earnings checks "
                        "may reflect deliberate reinvestment, not a weak business.</div>"
                    ) if _det_qs_growth else ""
                    st.markdown(f"""
                    <div class="metric-card">
                      <div class="metric-label">Quality Score</div>
                      <div style="display:flex;align-items:baseline;gap:8px;margin-top:4px">
                        <span style="font-size:1.6rem;font-weight:700;color:{_qs_color}">{_det_qs}</span>
                        <span style="color:#666;font-size:0.85rem">/ 100</span>
                        <span style="margin-left:8px">{_qs_bar_html}</span>
                      </div>
                      <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:4px 16px;margin-top:6px">
                        {"".join(
                            f'<div style="font-size:0.72rem;color:{"#4ade80" if p is True else "#f87171" if p is False else "#555"}">'
                            f'{"✓" if p is True else "✗" if p is False else "·"} {l}'
                            f'{"<br><span style=\'color:#aaa;font-size:0.68rem\'>" + a + "</span>" if a else ""}'
                            f'</div>'
                            for l, p, a in _det_qs_details
                        )}
                      </div>
                      {_gp_banner}
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    metric_card("Quality Score", "—", sub="Insufficient data")

        # ── Balance sheet snapshot (equity only) ──────────────────────────
        _cash     = det_funds.get("total_cash")
        _debt     = det_funds.get("total_debt")
        _assets   = det_funds.get("total_assets")
        _rev      = det_funds.get("total_revenue")
        _fcf      = det_funds.get("free_cashflow")
        _ocf      = det_funds.get("oper_cashflow")
        _has_bs   = _det_equity and any(v is not None for v in [_cash, _debt, _assets, _rev, _fcf, _ocf])
        if _has_bs:
            _net_cash = (_cash - _debt) if (_cash is not None and _debt is not None) else None
            _net_cash_color = _color_class(_net_cash)
            ba, bb, bc, bd, be, bf = st.columns(6)
            with ba:
                metric_card("Cash & Equiv.", _fmt_large(_cash), sub="&nbsp;")
            with bb:
                metric_card("Total Debt", _fmt_large(_debt), sub="&nbsp;")
            with bc:
                _nc_str = _fmt_large(abs(_net_cash)) if _net_cash is not None else "—"
                if _net_cash is not None:
                    _nc_str = ("+" if _net_cash >= 0 else "-") + _nc_str
                metric_card("Net Cash", _nc_str, sub="&nbsp;", sub_color=_net_cash_color)
            with bd:
                metric_card("Total Assets", _fmt_large(_assets), sub="&nbsp;")
            with be:
                metric_card("Revenue (TTM)", _fmt_large(_rev), sub="&nbsp;")
            with bf:
                _fcf_str = _fmt_large(abs(_fcf)).replace("$", ("$+" if _fcf and _fcf >= 0 else "$")) if _fcf is not None else "—"
                metric_card("Free Cash Flow", _fmt_large(_fcf),
                            sub=f"Operating: {_fmt_large(_ocf)}" if _ocf else None,
                            sub_color=_color_class(_fcf))

        # ── Earnings date (equity only) ───────────────────────────────────
        _earn_str_det = _fmt_earnings_date(det_funds) if _det_equity else ""
        if not _earn_str_det and _det_equity and det_extended.get("earnings_date"):
            _earn_str_det = det_extended["earnings_date"].strftime("%b %d, %Y")
        if _earn_str_det:
            st.markdown(f"""
            <div class="metric-card" style="display:inline-block;padding:10px 20px;margin-top:8px">
              <div class="metric-label">Next Earnings Report</div>
              <div style="font-size:1.2rem;font-weight:700;color:#fbbf24;margin-top:4px">
                📅 {_earn_str_det}
              </div>
            </div>""", unsafe_allow_html=True)

    # ── Annual financial history (equity only) ────────────────────────────
    _annual = det_extended.get("annual", {})
    _det_equity = _is_equity(det_funds)
    if _annual and _det_equity:
        st.divider()
        with st.expander("Annual Financial History", expanded=False):
            st.caption("Source: yfinance annual income statement & cash flow. Typically 4–5 years available.")
            _hist_rows = []
            for _yr in sorted(_annual.keys(), reverse=True):
                _r = _annual[_yr]
                _rev_v  = _r.get("revenue")
                _gp_v   = _r.get("gross_profit")
                _ni_v   = _r.get("net_income")
                _fcf_v  = _r.get("fcf")
                _ocf_v  = _r.get("ocf")
                _gm_v   = (_gp_v / _rev_v * 100) if (_gp_v and _rev_v and _rev_v > 0) else None
                _fcfm_v = (_fcf_v / _rev_v * 100) if (_fcf_v and _rev_v and _rev_v > 0) else None
                _hist_rows.append({
                    "Year":         int(_yr),
                    "Revenue":      _rev_v,
                    "Gross Profit": _gp_v,
                    "Net Income":   _ni_v,
                    "FCF":          _fcf_v,
                    "OCF":          _ocf_v,
                    "Gross Margin": _gm_v,
                    "FCF Margin":   _fcfm_v,
                })
            if _hist_rows:
                _df_hist = pd.DataFrame(_hist_rows)

                def _color_hist(val):
                    if pd.isna(val) or val == 0: return ""
                    return "color: #4ade80" if val > 0 else "color: #f87171"

                _fmt_hist = {
                    "Revenue":      lambda v: _fmt_large(v) if pd.notna(v) else "—",
                    "Gross Profit": lambda v: _fmt_large(v) if pd.notna(v) else "—",
                    "Net Income":   lambda v: _fmt_large(v) if pd.notna(v) else "—",
                    "FCF":          lambda v: _fmt_large(v) if pd.notna(v) else "—",
                    "OCF":          lambda v: _fmt_large(v) if pd.notna(v) else "—",
                    "Gross Margin": lambda v: f"{v:.1f}%" if pd.notna(v) else "—",
                    "FCF Margin":   lambda v: f"{v:.1f}%" if pd.notna(v) else "—",
                }
                _styled_hist = (
                    _df_hist.style
                    .map(_color_hist, subset=["Net Income", "FCF"])
                    .format(_fmt_hist, na_rep="—")
                )
                st.dataframe(_styled_hist, width='stretch', hide_index=True)

    # ── Share dilution (equity only) ─────────────────────────────────────
    _share_rows = [
        (yr, _annual[yr].get("shares") or _annual[yr].get("shares_diluted"))
        for yr in sorted(_annual.keys(), reverse=True)
        if _annual[yr].get("shares") or _annual[yr].get("shares_diluted")
    ]
    if len(_share_rows) >= 2 and _det_equity:
        st.divider()
        with st.expander("Share Count Trend  (Dilution Check)", expanded=False):
            st.caption("Rising share count = dilution (bad). Falling = buybacks (good).")
            _dil_rows = []
            for i, (yr, sh) in enumerate(_share_rows):
                prev_sh = _share_rows[i + 1][1] if i + 1 < len(_share_rows) else None
                yoy_pct = ((sh - prev_sh) / prev_sh * 100) if (prev_sh and prev_sh > 0) else None
                _dil_rows.append({
                    "Year":              int(yr),
                    "Shares Outstanding": sh,
                    "YoY Change":        yoy_pct,
                })
            _df_dil = pd.DataFrame(_dil_rows)

            def _color_dilution(val):
                if pd.isna(val) or val == 0: return ""
                # Dilution is bad (red), buybacks are good (green)
                return "color: #f87171" if val > 0 else "color: #4ade80"

            def _fmt_shares_dil(v):
                if pd.isna(v): return "—"
                if v >= 1e9:  return f"{v/1e9:.3f}B"
                if v >= 1e6:  return f"{v/1e6:.2f}M"
                return f"{v:,.0f}"

            _styled_dil = (
                _df_dil.style
                .map(_color_dilution, subset=["YoY Change"])
                .format({
                    "Shares Outstanding": _fmt_shares_dil,
                    "YoY Change":         lambda v: f"{v:+.2f}%" if pd.notna(v) else "—",
                }, na_rep="—")
            )
            st.dataframe(_styled_dil, width='stretch', hide_index=True)

    # ── DCF Fair Value (equity only) ──────────────────────────────────────
    if _det_equity:
        st.divider()
        _det_live_px = pos.get("live_price") if pos else None
        with st.expander("DCF Fair Value Model", expanded=False):
            _render_dcf(ticker, _det_live_px, det_funds, key_prefix=f"det_{ticker}")

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
        _buy_types  = BUY_TYPES | TRANSFER_IN_TYPES
        _sell_types = SELL_TYPES
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
            # Indicator selector
            _ind_sel = st.multiselect(
                "Technical indicators",
                ["MA(20)", "MA(50)", "MA(200)", "Bollinger Bands", "RSI", "MACD"],
                default=["RSI"],
                key=f"ind_{ticker}",
                label_visibility="collapsed",
                placeholder="Add technical indicators…",
                help="MA(N): Moving average over N days — smooths price noise. Price above MA = uptrend. | Bollinger Bands: ±2 standard deviations from 20-day MA — near upper band = overbought, near lower = oversold. | RSI: Momentum oscillator 0–100. >70 overbought, <30 oversold. Period is adjustable. | MACD: Trend-following momentum indicator. Fast/slow/signal periods are adjustable.",
            )

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
            # Add overlay traces (MA, BB)
            _overlay = _render_indicators(tc["dates"], tc["prices"], _ind_sel,
                                          key_prefix=f"ind_{ticker}")
            for _tr in _overlay:
                fig_price.add_trace(_tr)

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
                _det_bm_raw = load_benchmark_history(
                    tc["dates"][0], tc["dates"][-1],
                    tuple(_det_sel_bm),
                    benchmarks_map=tuple(BENCHMARKS.items()),
                )

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


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render(summary: dict, all_open_positions: list, excluded: set,
           selected_dbs: list, selected_account,
           start_date, today: date, period: str,
           BENCHMARKS: dict, BENCHMARK_COLORS: dict,
           delisted: dict,
           load_excluded_positions_fn, save_excluded_positions_fn):
    open_pos   = all_open_positions   # includes excluded — cards show badge, detail view still works
    closed_pos = summary["closed_positions"]

    if "selected_ticker" not in st.session_state:
        st.session_state["selected_ticker"] = None

    # ── routing ────────────────────────────────────────────────────────────

    if st.session_state["selected_ticker"]:
        _all_tickers = tuple(p["ticker"] for p in open_pos + closed_pos)
        _all_names   = load_ticker_names(_all_tickers) if _all_tickers else {}
        _show_detail(
            st.session_state["selected_ticker"],
            open_pos, closed_pos,
            selected_dbs, selected_account,
            start_date, today, period,
            BENCHMARKS, BENCHMARK_COLORS,
            delisted, excluded,
            names=_all_names,
        )
    else:
        # Open positions grid
        _n_excl = sum(1 for p in open_pos if p["ticker"] in excluded)
        _excl_suffix = f"  ·  {_n_excl} excluded" if _n_excl else ""
        st.subheader(f"Open Positions  ({len(open_pos)}){_excl_suffix}")
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
                        _render_open_card(p, week52, names, twrs, delisted, excluded, today, funds)
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
        all_excl = load_excluded_positions_fn()
        if all_excl:
            st.divider()
            st.subheader("Re-include Excluded Positions")
            st.caption("Excluded positions still appear as cards above (marked EXCLUDED) and removed from all portfolio totals. "
                       "Click to re-include.")
            excl_cols = st.columns(min(len(all_excl), 6))
            for col, ticker in zip(excl_cols, sorted(all_excl)):
                with col:
                    if st.button(f"↩ {ticker}", key=f"reincl_{ticker}",
                                 help=f"Re-include {ticker} in portfolio calculations"):
                        all_excl.discard(ticker)
                        save_excluded_positions_fn(all_excl)
                        st.rerun()
