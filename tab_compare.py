"""
tab_compare.py — Compare tab render function.
"""

import streamlit as st

from ui_helpers import (
    _fmt_pct_raw, _fmt_pct_raw_signed, _fmt_x, _fmt_shares, _fmt_large,
    _calc_roic, _calc_quality_score, _fmt_earnings_date, _dcf_intrinsic,
)
from loaders import (
    load_ticker_fundamentals, load_ticker_names, load_ticker_extended,
    load_watchlist,
)


def _cmp_row(label, values, _cmp_list, fmt_fn=None, better="higher", highlight=True):
    """
    values: list aligned to _cmp_list — raw floats or strings.
    fmt_fn: callable(raw) -> display string. If None, values are already strings.
    better: "higher" | "lower" | None  — controls which value gets the green highlight.
    """
    cols = st.columns([2] + [3] * len(_cmp_list))
    with cols[0]:
        st.markdown(f"<span style='font-size:0.82rem;color:#888'>{label}</span>",
                    unsafe_allow_html=True)

    numeric = [v for v in values if isinstance(v, (int, float)) and v is not None]
    if highlight and numeric and better in ("higher", "lower"):
        best = max(numeric) if better == "higher" else min(numeric)
    else:
        best = None

    for i, raw in enumerate(values):
        disp = fmt_fn(raw) if (fmt_fn and raw is not None) else (raw if raw is not None else "—")
        is_best = (highlight and best is not None
                   and isinstance(raw, (int, float)) and raw == best
                   and len(numeric) > 1)
        color = "#4ade80" if is_best else "#e2e8f0"
        with cols[i + 1]:
            st.markdown(f"<span style='font-size:0.92rem;color:{color}'>{disp}</span>",
                        unsafe_allow_html=True)


def render(summary: dict):
    st.subheader("Stock Comparison")
    st.caption("Compare up to 3 tickers side-by-side. Pick from your positions, watchlist, or enter any ticker.")

    _pos_tickers = sorted(set(p["ticker"] for p in summary["open_positions"]))
    _wl_tickers_cmp = sorted(set(w["ticker"] for w in load_watchlist()))
    _wl_only = [t for t in _wl_tickers_cmp if t not in _pos_tickers]

    _CUSTOM = "✏️  Custom ticker…"
    _cmp_options = [_CUSTOM]
    if _pos_tickers:
        _cmp_options += ["── Positions ──"] + _pos_tickers
    if _wl_only:
        _cmp_options += ["── Watchlist ──"] + _wl_only

    _SEPARATORS = {"── Positions ──", "── Watchlist ──"}

    def _cmp_picker(label, col_key, default_ticker=None):
        """Selectbox from positions + watchlist, with custom text-input fallback."""
        _def_idx = 0
        if default_ticker and default_ticker in _cmp_options:
            _def_idx = _cmp_options.index(default_ticker)

        sel = st.selectbox(label, _cmp_options,
                           index=_def_idx,
                           key=f"cmp_sel_{col_key}")

        if sel == _CUSTOM or sel in _SEPARATORS:
            val = st.text_input("Enter ticker", key=f"cmp_txt_{col_key}",
                                placeholder="e.g. NVDA").upper().strip()
        else:
            val = sel
            # Clear the text box so it doesn't interfere
            if f"cmp_txt_{col_key}" not in st.session_state:
                st.session_state[f"cmp_txt_{col_key}"] = ""
        return val

    cmp_c1, cmp_c2, cmp_c3, cmp_c4 = st.columns([2, 2, 2, 1])
    with cmp_c1:
        _def1 = _pos_tickers[0] if _pos_tickers else None
        cmp_t1 = _cmp_picker("Ticker 1", "1", _def1)
    with cmp_c2:
        _def2 = _pos_tickers[1] if len(_pos_tickers) > 1 else None
        cmp_t2 = _cmp_picker("Ticker 2", "2", _def2)
    with cmp_c3:
        cmp_t3 = _cmp_picker("Ticker 3 (optional)", "3", None)
    with cmp_c4:
        st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
        cmp_go = st.button("Compare", type="primary", width="stretch")

    if "cmp_tickers" not in st.session_state:
        st.session_state["cmp_tickers"] = []
    if cmp_go:
        st.session_state["cmp_tickers"] = [t for t in [cmp_t1, cmp_t2, cmp_t3] if t]

    _cmp_list = st.session_state["cmp_tickers"]

    if len(_cmp_list) >= 2:
        with st.spinner("Fetching data…"):
            _cmp_funds = load_ticker_fundamentals(tuple(_cmp_list))
            _cmp_names = load_ticker_names(tuple(_cmp_list))
            _cmp_ext   = {t: load_ticker_extended(t) for t in _cmp_list}

        # ── Header row ────────────────────────────────────────────────────────
        _cmp_types = {t: _cmp_funds.get(t, {}).get("quote_type", "EQUITY") for t in _cmp_list}
        _all_equity = all(v == "EQUITY" for v in _cmp_types.values())

        _hdr_cols = st.columns([2] + [3] * len(_cmp_list))
        with _hdr_cols[0]:
            st.markdown("**Metric**")
        for i, t in enumerate(_cmp_list):
            _nm  = _cmp_names.get(t, "")
            _qt  = _cmp_types[t]
            _qt_badge = "" if _qt == "EQUITY" else f" `{_qt}`"
            with _hdr_cols[i + 1]:
                st.markdown(f"**{t}**{_qt_badge}" + (f"  \n<span style='font-size:0.75rem;color:#888'>{_nm}</span>" if _nm and _nm != t else ""),
                            unsafe_allow_html=True)

        st.divider()

        _pct        = _fmt_pct_raw
        _pct_signed = _fmt_pct_raw_signed
        _x          = _fmt_x
        _shares_fmt = _fmt_shares

        # ── Valuation ─────────────────────────────────────────────────────────
        st.markdown("#### Valuation")
        _cmp_row("Price / Earnings (TTM)",
                 [_cmp_funds.get(t, {}).get("pe") for t in _cmp_list],
                 _cmp_list, fmt_fn=_x, better="lower")
        _cmp_row("Forward P/E",
                 [_cmp_funds.get(t, {}).get("fwd_pe") for t in _cmp_list],
                 _cmp_list, fmt_fn=_x, better="lower")
        _cmp_row("Market Cap",
                 [_cmp_funds.get(t, {}).get("market_cap") for t in _cmp_list],
                 _cmp_list, fmt_fn=_fmt_large, better=None)
        if _all_equity:
            _cmp_row("Analyst Mean Target",
                     [_cmp_funds.get(t, {}).get("target_mean") for t in _cmp_list],
                     _cmp_list, fmt_fn=lambda v: f"${v:.2f}" if v else "—", better="higher")

        st.divider()

        # ── Profitability ──────────────────────────────────────────────────────
        st.markdown("#### Profitability")
        _cmp_row("Gross Margin",
                 [_cmp_funds.get(t, {}).get("gross_margins") for t in _cmp_list],
                 _cmp_list, fmt_fn=_pct, better="higher")
        _cmp_row("Operating Margin",
                 [_cmp_funds.get(t, {}).get("oper_margins") for t in _cmp_list],
                 _cmp_list, fmt_fn=_pct, better="higher")
        _fcf_margins = []
        for t in _cmp_list:
            f = _cmp_funds.get(t, {})
            fcf, rev = f.get("free_cashflow"), f.get("total_revenue")
            _fcf_margins.append(fcf / rev if (fcf and rev and rev > 0) else None)
        _cmp_row("FCF Margin",
                 _fcf_margins, _cmp_list, fmt_fn=_pct, better="higher")
        if _all_equity:
            _cmp_row("ROIC",
                     [_calc_roic(_cmp_funds.get(t, {})) for t in _cmp_list],
                     _cmp_list, fmt_fn=_pct, better="higher")

        st.divider()

        # ── Growth ────────────────────────────────────────────────────────────
        st.markdown("#### Growth (YoY)")
        _cmp_row("Revenue Growth",
                 [_cmp_funds.get(t, {}).get("revenue_growth") for t in _cmp_list],
                 _cmp_list, fmt_fn=_pct_signed, better="higher")
        _cmp_row("Earnings Growth",
                 [_cmp_funds.get(t, {}).get("earnings_growth") for t in _cmp_list],
                 _cmp_list, fmt_fn=_pct_signed, better="higher")

        st.divider()

        # ── Balance Sheet ──────────────────────────────────────────────────────
        st.markdown("#### Balance Sheet")
        _net_cash = []
        for t in _cmp_list:
            f = _cmp_funds.get(t, {})
            c, d = f.get("total_cash"), f.get("total_debt")
            _net_cash.append((c - d) if (c is not None and d is not None) else None)
        _cmp_row("Net Cash (Cash − Debt)",
                 _net_cash, _cmp_list, fmt_fn=_fmt_large, better="higher")
        _debt_assets = []
        for t in _cmp_list:
            f = _cmp_funds.get(t, {})
            d, a = f.get("total_debt"), f.get("total_assets")
            _debt_assets.append(d / a if (d is not None and a and a > 0) else None)
        _cmp_row("Debt / Assets",
                 _debt_assets, _cmp_list, fmt_fn=_pct, better="lower")
        _cmp_row("Revenue (TTM)",
                 [_cmp_funds.get(t, {}).get("total_revenue") for t in _cmp_list],
                 _cmp_list, fmt_fn=_fmt_large, better=None)
        _cmp_row("Free Cash Flow (TTM)",
                 [_cmp_funds.get(t, {}).get("free_cashflow") for t in _cmp_list],
                 _cmp_list, fmt_fn=_fmt_large, better="higher")

        if _all_equity:
            st.divider()

            # ── Shares & Dilution ─────────────────────────────────────────────
            st.markdown("#### Shares & Dilution")
            _cmp_row("Shares Outstanding",
                     [_cmp_funds.get(t, {}).get("shares_outstanding") for t in _cmp_list],
                     _cmp_list, fmt_fn=_shares_fmt, better=None)

            _dilution_yoy = []
            for t in _cmp_list:
                ann = _cmp_ext[t].get("annual", {})
                yrs = sorted(ann.keys(), reverse=True)
                if len(yrs) >= 2:
                    sh_new = ann[yrs[0]].get("shares") or ann[yrs[0]].get("shares_diluted")
                    sh_old = ann[yrs[1]].get("shares") or ann[yrs[1]].get("shares_diluted")
                    _dilution_yoy.append((sh_new - sh_old) / sh_old if (sh_new and sh_old and sh_old > 0) else None)
                else:
                    _dilution_yoy.append(None)
            _cmp_row("Share Count Change (1Y)",
                     _dilution_yoy, _cmp_list, fmt_fn=_pct_signed, better="lower")

            st.divider()

            # ── Quality ───────────────────────────────────────────────────────
            st.markdown("#### Quality Score")
            _qs_scores = []
            for t in _cmp_list:
                qs, _, _gp = _calc_quality_score(_cmp_funds.get(t, {}))
                _qs_scores.append(qs)
            _cmp_row("Quality Score (0–100)",
                     _qs_scores,
                     _cmp_list,
                     fmt_fn=lambda v: str(v) if v is not None else "—",
                     better="higher")

            _earn_dates = []
            for t in _cmp_list:
                _es = _fmt_earnings_date(_cmp_funds.get(t, {}))
                if not _es and _cmp_ext[t].get("earnings_date"):
                    _es = _cmp_ext[t]["earnings_date"].strftime("%b %d, %Y")
                _earn_dates.append(_es or "—")
            _cmp_row("Next Earnings",
                     _earn_dates, _cmp_list, fmt_fn=None, better=None, highlight=False)

        if _all_equity:
            st.divider()
            st.markdown("#### DCF Fair Value (Base Case)")
            st.caption("Growth 10% · Discount 10% · Terminal 3% · 10 years · 25% margin of safety")
            _dcf_ivs, _dcf_mos_ivs = [], []
            for t in _cmp_list:
                f = _cmp_funds.get(t, {})
                fcf_s = f.get("free_cashflow")
                shs   = f.get("shares_outstanding")
                if fcf_s and shs and shs > 0:
                    iv = _dcf_intrinsic(fcf_s / shs, 0.10, 0.10, 0.03, 10)
                    _dcf_ivs.append(iv)
                    _dcf_mos_ivs.append(iv * 0.75 if iv else None)
                else:
                    _dcf_ivs.append(None)
                    _dcf_mos_ivs.append(None)
            _cmp_row("Intrinsic Value (base)",
                     _dcf_ivs, _cmp_list, fmt_fn=lambda v: f"${v:.2f}" if v else "—", better="higher")
            _cmp_row("After 25% Margin of Safety",
                     _dcf_mos_ivs, _cmp_list, fmt_fn=lambda v: f"${v:.2f}" if v else "—", better="higher")

        st.divider()
        st.caption("Green highlight = best value in row (for directional metrics). "
                   "Equity-only metrics (ROIC, Quality Score, Earnings) hidden for ETFs/funds.")

    elif _cmp_list:
        st.info("Enter at least 2 tickers and click **Compare**.")
    else:
        st.info("Enter 2 or 3 ticker symbols above and click **Compare** to see a side-by-side breakdown.")
