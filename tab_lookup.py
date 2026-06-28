"""
tab_lookup.py — Stock Lookup tab render function.
"""

import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots as _make_subplots
from datetime import date

from ui_helpers import (
    _fmt_large, _render_dcf, _render_indicators, _ind_bbands,
    metric_card,
)


def render(today: date):
    import yfinance as _yf

    @st.cache_data(ttl=300)
    def _fetch_stock(symbol: str):
        obj  = _yf.Ticker(symbol)
        hist = obj.history(period="1y")
        info = obj.info
        # S&P 500 benchmark for return comparison
        spy  = _yf.Ticker("SPY").history(period="1y")["Close"]
        return hist, info, spy

    def _fmt_large_lk(v):
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
                metric_card("Market Cap", _fmt_large_lk(mkt_cap))
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

            # Indicator selector
            _lk_ind_sel = st.multiselect(
                "Technical indicators",
                ["MA(20)", "MA(50)", "MA(200)", "Bollinger Bands", "RSI", "MACD"],
                default=["MA(50)", "MA(200)", "RSI"],
                key=f"lk_ind_{symbol}",
                label_visibility="collapsed",
                placeholder="Add technical indicators…",
                help="MA(N): Moving average over N days — smooths price noise. Price above MA = uptrend. | Bollinger Bands: ±2 standard deviations from 20-day MA — near upper band = overbought, near lower = oversold. | RSI(14): Momentum oscillator 0–100. >70 overbought, <30 oversold. | MACD(12,26,9): Trend-following momentum. Signal line crossovers indicate potential reversals.",
            )

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

            # MA overlays driven by selector
            _lk_ma_colors = {"MA(20)": "#60a5fa", "MA(50)": "#fbbf24", "MA(200)": "#a78bfa"}
            for _lk_ma_lbl, _lk_ma_color in _lk_ma_colors.items():
                if _lk_ma_lbl in _lk_ind_sel:
                    _lk_period = int(_lk_ma_lbl[3:-1])
                    _lk_ma_vals = closes.rolling(_lk_period).mean()
                    if _lk_ma_vals.notna().sum() >= 5:
                        fig_lk.add_trace(go.Scatter(
                            x=hist.index, y=_lk_ma_vals, name=_lk_ma_lbl,
                            line=dict(color=_lk_ma_color, width=1.5),
                        ), row=1, col=1)

            # Bollinger Bands overlay
            if "Bollinger Bands" in _lk_ind_sel:
                _lk_bb_u, _lk_bb_m, _lk_bb_l = _ind_bbands(closes)
                fig_lk.add_trace(go.Scatter(
                    x=hist.index, y=_lk_bb_u, name="BB Upper",
                    line=dict(color="#94a3b8", width=1, dash="dash"),
                ), row=1, col=1)
                fig_lk.add_trace(go.Scatter(
                    x=hist.index, y=_lk_bb_l, name="BB Lower",
                    line=dict(color="#94a3b8", width=1, dash="dash"),
                    fill="tonexty", fillcolor="rgba(148,163,184,0.08)",
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

            # RSI / MACD subplots (rendered as separate charts below)
            _lk_dates_str = [str(d.date()) for d in hist.index]
            _render_indicators(_lk_dates_str, closes.tolist(), _lk_ind_sel,
                               key_prefix=f"lk_ind2_{symbol}")

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

            # ── DCF Fair Value ────────────────────────────────────────────
            _lk_qt = (info.get("quoteType") or "EQUITY").upper()
            if _lk_qt == "EQUITY":
                st.divider()
                _lk_funds = {
                    "free_cashflow":    info.get("freeCashflow"),
                    "shares_outstanding": info.get("sharesOutstanding"),
                }
                with st.expander("DCF Fair Value Model", expanded=False):
                    _render_dcf(symbol, price, _lk_funds, key_prefix=f"lk_{symbol}")

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
                    "Shares Outstanding": _fmt_large_lk(info.get("sharesOutstanding")).replace("$", "") if info.get("sharesOutstanding") else "—",
                    "Float":              _fmt_large_lk(info.get("floatShares")).replace("$", "")       if info.get("floatShares") else "—",
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
