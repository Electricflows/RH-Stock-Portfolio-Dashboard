"""
tab_watchlist.py — Watchlist tab render function.
"""

import streamlit as st
from datetime import date

from ui_helpers import (
    _fmt_large, _is_equity, _calc_quality_score, _calc_roic, _quality_color,
    _fmt_earnings_date,
)
from loaders import load_ticker_fundamentals, load_ticker_names, load_watchlist, save_watchlist


def render(summary: dict):
    st.subheader("Watchlist")
    st.caption("Track stocks you're researching. Add notes, see live fundamentals, open DCF or Compare from here.")

    _wl = load_watchlist()
    _owned = set(p["ticker"] for p in summary["open_positions"])

    # ── Add ticker form ───────────────────────────────────────────────────────
    with st.expander("➕  Add to Watchlist", expanded=len(_wl) == 0):
        _wa, _wb, _wc = st.columns([2, 4, 1])
        with _wa:
            _wl_new_t = st.text_input("Ticker", key="wl_add_t", placeholder="e.g. NVDA").upper().strip()
        with _wb:
            _wl_new_note = st.text_input("Note (optional)", key="wl_add_note",
                                         placeholder="e.g. AI play, watching for pullback to $90")
        with _wc:
            st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
            _wl_add_btn = st.button("Add", type="primary", width="stretch", key="wl_add_btn")

        if _wl_add_btn and _wl_new_t:
            if any(w["ticker"] == _wl_new_t for w in _wl):
                st.warning(f"{_wl_new_t} is already on your watchlist.")
            else:
                _wl.append({"ticker": _wl_new_t, "note": _wl_new_note,
                             "added": date.today().isoformat()})
                save_watchlist(_wl)
                st.rerun()

    if not _wl:
        st.info("Your watchlist is empty. Add tickers above to start tracking candidates.")
        return

    # Fetch fundamentals for all watchlist tickers
    _wl_tickers = tuple(w["ticker"] for w in _wl)
    with st.spinner("Loading fundamentals…"):
        _wl_funds = load_ticker_fundamentals(_wl_tickers)
        _wl_names = load_ticker_names(_wl_tickers)

    for _wi, _wentry in enumerate(_wl):
        _wt   = _wentry["ticker"]
        _wnote= _wentry.get("note", "")
        _wadd = _wentry.get("added", "")
        _wf   = _wl_funds.get(_wt, {})
        _wname= _wl_names.get(_wt, "")
        _weq  = _is_equity(_wf)

        _wpe     = _wf.get("pe")
        _wfpe    = _wf.get("fwd_pe")
        _wmc     = _wf.get("market_cap")
        _wtgt    = _wf.get("target_mean")
        _wrg     = _wf.get("revenue_growth")
        _wfcf    = _wf.get("free_cashflow")
        _wshs    = _wf.get("shares_outstanding")
        _wqs, _, _wgp = _calc_quality_score(_wf) if _weq else (None, [], False)
        _wroic   = _calc_roic(_wf) if _weq else None
        _wearing = _fmt_earnings_date(_wf)

        # Live price from yfinance (already in fundamentals cache via info dict)
        _wcur_price = _wf.get("target_mean")   # fallback only
        try:
            import yfinance as _yf_wl
            _wp_info = _yf_wl.Ticker(_wt).info
            _wcur_price = _wp_info.get("currentPrice") or _wp_info.get("regularMarketPrice")
        except Exception:
            _wcur_price = None

        _wqs_color = _quality_color(_wqs)
        _owned_badge = (
            " <span style='background:#1a3a2a;color:#4ade80;border-radius:4px;"
            "padding:1px 6px;font-size:0.7rem;font-weight:700'>OWNED</span>"
            if _wt in _owned else ""
        )

        with st.container():
            st.markdown(
                f"<div style='background:#1e1e2e;border:1px solid #2a2a3e;border-radius:10px;"
                f"padding:14px 18px;margin-bottom:10px'>",
                unsafe_allow_html=True
            )

            # Header row
            _hc1, _hc2 = st.columns([6, 1])
            with _hc1:
                st.markdown(
                    f"<span style='font-size:1.2rem;font-weight:800;color:#e2e8f0'>{_wt}</span>"
                    f"{_owned_badge}"
                    + (f"  <span style='color:#888;font-size:0.85rem'>{_wname}</span>" if _wname and _wname != _wt else "")
                    + (f"<br><span style='font-size:0.78rem;color:#60a5fa;font-style:italic'>{_wnote}</span>" if _wnote else "")
                    + (f"<span style='font-size:0.68rem;color:#555;margin-left:8px'>Added {_wadd}</span>" if _wadd else ""),
                    unsafe_allow_html=True
                )
            with _hc2:
                if st.button("Remove ✕", key=f"wl_rm_{_wi}", use_container_width=True):
                    _wl = [w for w in _wl if w["ticker"] != _wt]
                    save_watchlist(_wl)
                    st.rerun()

            # Metrics row
            _wm_cols = st.columns(6)
            with _wm_cols[0]:
                _wp_str = f"${_wcur_price:,.2f}" if _wcur_price else "—"
                st.metric("Price", _wp_str)
            with _wm_cols[1]:
                st.metric("P/E (TTM)", f"{_wpe:.1f}x" if _wpe else "—")
            with _wm_cols[2]:
                st.metric("Fwd P/E", f"{_wfpe:.1f}x" if _wfpe else "—")
            with _wm_cols[3]:
                st.metric("Market Cap", _fmt_large(_wmc))
            with _wm_cols[4]:
                if _weq:
                    st.metric("Quality", f"{_wqs}/100" if _wqs is not None else "—")
                else:
                    st.metric("Type", _wf.get("quote_type", "—"))
            with _wm_cols[5]:
                if _weq and _wroic is not None:
                    st.metric("ROIC", f"{_wroic*100:.1f}%")
                elif _weq:
                    _rev_g = f"{_wrg*100:.0f}% rev growth" if _wrg else "—"
                    st.metric("Rev Growth", _rev_g)
                else:
                    st.metric("Analyst Target", f"${_wtgt:.2f}" if _wtgt else "—")

            # Growth phase note
            if _wgp:
                st.caption("⚡ Growth Phase — revenue growing fast; low FCF score may reflect reinvestment, not weakness.")

            # Earnings + target row
            _wa2, _wb2, _wc2, _wd2 = st.columns([2, 2, 2, 2])
            if _weq:
                with _wa2:
                    if _wearing:
                        st.caption(f"Next earnings: **{_wearing}**")
                with _wb2:
                    if _wtgt and _wcur_price:
                        _wupside = (_wtgt - _wcur_price) / _wcur_price * 100
                        _wu_col = "#4ade80" if _wupside > 0 else "#f87171"
                        st.markdown(
                            f"<span style='font-size:0.8rem;color:#888'>Analyst target: </span>"
                            f"<span style='color:{_wu_col};font-weight:700'>${_wtgt:.2f} "
                            f"({_wupside:+.1f}%)</span>",
                            unsafe_allow_html=True
                        )

            # Action buttons
            _btn1, _btn2, _btn3, _btn_spacer = st.columns([2, 2, 2, 6])
            with _btn1:
                if st.button("🔍 Look Up", key=f"wl_lu_{_wi}", use_container_width=True):
                    st.session_state["lookup_symbol"] = _wt
                    st.rerun()
            with _btn2:
                if st.button("⚖️ Compare", key=f"wl_cmp_{_wi}", use_container_width=True):
                    # Pre-load compare tab with this ticker vs top 2 positions
                    _owned_list = [p["ticker"] for p in summary["open_positions"]]
                    _cmp_pre = [_wt] + [t for t in _owned_list if t != _wt][:2]
                    st.session_state["cmp_tickers"] = _cmp_pre
                    st.rerun()

            st.markdown("</div>", unsafe_allow_html=True)
