"""
ui_helpers.py — Shared helper/utility functions for the Portfolio Dashboard.
"""

import bisect
import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from datetime import date, datetime


# ---------------------------------------------------------------------------
# Formatting helpers
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

def _fmt_pct_raw(v):
    """Format a 0–1 ratio as a percentage string, e.g. 0.153 → '15.3%'."""
    return f"{v*100:.1f}%" if v is not None else "—"

def _fmt_pct_raw_signed(v):
    return f"{v*100:+.1f}%" if v is not None else "—"

def _fmt_x(v):
    """Format a multiple, e.g. 23.4 → '23.4x'."""
    return f"{v:.1f}x" if v is not None else "—"

def _fmt_shares(v):
    if v is None: return "—"
    if v >= 1e9: return f"{v/1e9:.2f}B"
    if v >= 1e6: return f"{v/1e6:.1f}M"
    return f"{v:,.0f}"

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

def _is_equity(funds: dict) -> bool:
    """True for individual stocks; False for ETFs, mutual funds, indices, crypto."""
    return funds.get("quote_type", "EQUITY") == "EQUITY"


def _fmt_earnings_date(funds: dict) -> str:
    """Return human-readable next earnings date string, or empty string."""
    ts = funds.get("earnings_ts")
    if not ts:
        return ""
    try:
        d = datetime.fromtimestamp(ts).date()
        if d >= date.today():
            days_out = (d - date.today()).days
            suffix = f" · {days_out}d" if days_out <= 90 else ""
            return d.strftime("%b %d, %Y") + suffix
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Quality score helpers
# ---------------------------------------------------------------------------

def _calc_roic(funds: dict):
    """Return ROIC as a float (e.g. 0.15 = 15%) or None if data is missing."""
    ocf    = funds.get("oper_cashflow")
    assets = funds.get("total_assets")
    cash   = funds.get("total_cash")
    if ocf is None or assets is None or cash is None:
        return None
    invested = assets - cash
    if invested <= 0:
        return None
    return ocf / invested


_QS_CHECKS = [
    # (label, weight)
    ("Gross Margin ≥ 30%",      12.5),
    ("Oper. Margin ≥ 10%",      12.5),
    ("FCF Margin ≥ 8%",         12.5),
    ("ROIC ≥ 10%",              12.5),
    ("Net Cash Positive",       12.5),
    ("Revenue Growth ≥ 5%",     12.5),
    ("Earnings Growth ≥ 5%",    12.5),
    ("Debt / Assets < 40%",     12.5),
]

def _calc_quality_score(funds: dict):
    """
    Returns (score: int 0–100, details: list of (label, passed, actual_str),
             growth_phase: bool).
    Returns (None, [], False) when fewer than 4 checks have data.
    growth_phase = True when the company shows strong revenue growth (≥20%) but
    fails FCF / earnings checks — signals a deliberate reinvestment phase, not
    a fundamentally weak business.
    """
    ocf   = funds.get("oper_cashflow")
    fcf   = funds.get("free_cashflow")
    rev   = funds.get("total_revenue")
    cash  = funds.get("total_cash")
    debt  = funds.get("total_debt")
    assets= funds.get("total_assets")
    gm    = funds.get("gross_margins")
    om    = funds.get("oper_margins")
    rg    = funds.get("revenue_growth")
    eg    = funds.get("earnings_growth")

    roic  = _calc_roic(funds)

    def _check(val, condition):
        return condition(val) if val is not None else None

    fcf_margin = (fcf / rev) if (fcf is not None and rev and rev > 0) else None
    debt_ratio = (debt / assets) if (debt is not None and assets and assets > 0) else None

    raw = [
        _check(gm,        lambda v: v >= 0.30),
        _check(om,        lambda v: v >= 0.10),
        _check(fcf_margin,lambda v: v >= 0.08),
        _check(roic,      lambda v: v >= 0.10),
        ((cash - debt) > 0) if (cash is not None and debt is not None) else None,
        _check(rg,        lambda v: v >= 0.05),
        _check(eg,        lambda v: v >= 0.05),
        _check(debt_ratio,lambda v: v < 0.40),
    ]

    def _pf(v, mult=100, suffix="%", decimals=1):
        return f"{v * mult:.{decimals}f}{suffix}" if v is not None else None

    actuals = [
        _pf(gm),
        _pf(om),
        _pf(fcf_margin),
        _pf(roic),
        (f"${(cash-debt)/1e9:.2f}B net cash" if (cash is not None and debt is not None) else None),
        _pf(rg),
        _pf(eg),
        _pf(debt_ratio),
    ]

    available = [r for r in raw if r is not None]
    if len(available) < 4:
        return None, [], False

    score = round(sum(12.5 for r in raw if r is True))
    details = [(label, raw[i], actuals[i]) for i, (label, _) in enumerate(_QS_CHECKS)]

    # Growth-phase flag: strong revenue growth but FCF or earnings checks fail
    _high_growth  = rg is not None and rg >= 0.20
    _fcf_fail     = fcf_margin is not None and fcf_margin < 0.08
    _earn_fail    = eg is not None and eg < 0.05
    growth_phase  = _high_growth and (_fcf_fail or _earn_fail)

    return score, details, growth_phase


def _quality_color(score):
    if score is None:
        return "#888"
    if score >= 75:
        return "#4ade80"
    if score >= 50:
        return "#fbbf24"
    return "#f87171"


# ---------------------------------------------------------------------------
# DCF helpers
# ---------------------------------------------------------------------------

def _dcf_intrinsic(fcf_ps: float, growth: float, discount: float,
                   terminal_growth: float, years: int = 10) -> float | None:
    """Return intrinsic value per share via DCF. Returns None if inputs are invalid."""
    if fcf_ps <= 0 or discount <= terminal_growth:
        return None
    pv_sum = 0.0
    fcf = fcf_ps
    for y in range(1, years + 1):
        fcf *= (1 + growth)
        pv_sum += fcf / (1 + discount) ** y
    terminal_value = fcf * (1 + terminal_growth) / (discount - terminal_growth)
    pv_sum += terminal_value / (1 + discount) ** years
    return pv_sum


def _render_dcf(ticker: str, current_price: float | None, funds: dict, key_prefix: str = ""):
    """
    Render an interactive DCF widget. Sliders are per-scenario; shared inputs sit above.
    key_prefix must be unique per call site to avoid Streamlit key collisions.
    """
    fcf   = funds.get("free_cashflow")
    shares= funds.get("shares_outstanding")

    if not shares or shares == 0:
        st.info("DCF requires Shares Outstanding data — not available for this ticker.")
        return

    fcf_ps_actual = (fcf / shares) if fcf else None

    # ── FCF override ─────────────────────────────────────────────────────────
    _ov_col, _ov_tog = st.columns([3, 1])
    with _ov_tog:
        use_override = st.checkbox("Override FCF/share", key=f"{key_prefix}_fcf_ov",
                                   help="Use a custom normalized FCF/share instead of today's reported figure. "
                                        "Useful for growth companies where current FCF is temporarily depressed.")
    with _ov_col:
        if fcf_ps_actual is not None:
            _caption = f"Reported FCF/share: **${fcf_ps_actual:.2f}**  ·  Shares: **{shares/1e9:.3f}B**  ·  Total FCF: **${fcf/1e9:.2f}B**"
            if fcf_ps_actual <= 0:
                _caption += "  ·  ⚠️ Negative — override recommended"
            st.caption(_caption)
        else:
            st.caption("FCF data not available — enter an override value to run the model.")

    if use_override:
        _suggested = max(fcf_ps_actual, 0.01) if fcf_ps_actual and fcf_ps_actual > 0 else 1.0
        fcf_ps = st.number_input(
            "Normalized FCF/share ($)", min_value=0.01, max_value=9999.0,
            value=round(float(_suggested), 2), step=0.25,
            key=f"{key_prefix}_fcf_ov_val",
            help="Enter the FCF/share you expect once the business normalizes. "
                 "A starting point: (expected revenue × target FCF margin) ÷ shares outstanding.")
        st.caption(f"Using override FCF/share: **${fcf_ps:.2f}**  ·  implied total FCF: **${fcf_ps * shares / 1e9:.2f}B**")
    else:
        if fcf_ps_actual is None or fcf_ps_actual <= 0:
            st.warning("FCF/share is negative or unavailable — check **Override FCF/share** above to enter a normalized estimate.")
            return
        fcf_ps = fcf_ps_actual

    # ── Shared parameters ────────────────────────────────────────────────────
    sh1, sh2, sh3 = st.columns(3)
    with sh1:
        dcf_years = st.slider("Projection years", 5, 15, 10, 1, key=f"{key_prefix}_yrs",
                              help="How many years of cash flow to project before calculating a terminal value. 10 years is the standard.")
    with sh2:
        dcf_tg = st.slider("Terminal growth rate (%)", 1.0, 5.0, 3.0, 0.5,
                           key=f"{key_prefix}_tg",
                           help="The perpetual growth rate assumed after the projection period ends. Typically set near long-run GDP growth (~3%). Must be below the discount rate.") / 100
    with sh3:
        dcf_mos = st.slider("Margin of safety (%)", 0, 40, 25, 5,
                            key=f"{key_prefix}_mos",
                            help="A discount applied on top of the intrinsic value before buying. A 25% MOS means you only buy if the stock is at least 25% below the estimated fair value — protection against model error.")

    st.divider()

    # ── Three scenario columns ────────────────────────────────────────────────
    _scenarios = [
        ("🐻 Bear",  5.0,  12.0, "#f87171"),
        ("📊 Base",  10.0, 10.0, "#fbbf24"),
        ("🐂 Bull",  15.0,  8.0, "#4ade80"),
    ]

    sc_cols = st.columns(3)
    for col, (label, def_g, def_d, color) in zip(sc_cols, _scenarios):
        slug = label.split()[-1].lower()
        with col:
            st.markdown(f"<div style='font-size:1rem;font-weight:700;color:{color}'>{label}</div>",
                        unsafe_allow_html=True)
            growth   = st.slider("Growth rate (%)", 1.0, 100.0, def_g, 0.5,
                                 key=f"{key_prefix}_{slug}_g",
                                 help="Annual rate at which FCF is assumed to grow over the projection period. Bull = optimistic; Bear = pessimistic.") / 100
            discount = st.slider("Discount rate (%)", 5.0, 20.0, def_d, 0.5,
                                 key=f"{key_prefix}_{slug}_d",
                                 help="Your required annual rate of return — used to discount future cash flows back to today's dollars. Higher = more conservative valuation. Often set to 8–12%.") / 100

            iv = _dcf_intrinsic(fcf_ps, growth, discount, dcf_tg, dcf_years)
            if iv is None:
                st.markdown("**Intrinsic Value:** —")
                continue

            iv_mos = iv * (1 - dcf_mos / 100)   # price target after margin of safety

            if current_price:
                upside     = (iv - current_price) / current_price * 100
                upside_mos = (iv_mos - current_price) / current_price * 100
                upside_color = "#4ade80" if upside > 0 else "#f87171"
                verdict = ("✅ **BUY**" if upside_mos > 0
                           else "⚠️ **HOLD**" if upside > 0
                           else "❌ **OVERVALUED**")
                price_html = (
                    f"<div style='margin-top:10px'>"
                    f"<div style='font-size:0.72rem;color:#888'>Intrinsic Value</div>"
                    f"<div style='font-size:1.4rem;font-weight:800;color:{color}'>${iv:.2f}</div>"
                    f"<div style='font-size:0.72rem;color:#888;margin-top:6px'>After {dcf_mos}% MOS</div>"
                    f"<div style='font-size:1.1rem;font-weight:700;color:{color}'>${iv_mos:.2f}</div>"
                    f"<div style='margin-top:8px;font-size:0.85rem;color:{upside_color}'>"
                    f"{'▲' if upside>=0 else '▼'} {upside:+.1f}% vs current ${current_price:.2f}</div>"
                    f"<div style='margin-top:6px'>{verdict}</div>"
                    f"</div>"
                )
            else:
                price_html = (
                    f"<div style='margin-top:10px'>"
                    f"<div style='font-size:0.72rem;color:#888'>Intrinsic Value</div>"
                    f"<div style='font-size:1.4rem;font-weight:800;color:{color}'>${iv:.2f}</div>"
                    f"<div style='font-size:0.72rem;color:#888;margin-top:6px'>After {dcf_mos}% MOS</div>"
                    f"<div style='font-size:1.1rem;font-weight:700;color:{color}'>${iv_mos:.2f}</div>"
                    f"</div>"
                )
            st.markdown(price_html, unsafe_allow_html=True)

    st.caption(
        "**How to read:** Intrinsic Value = estimated fair price per share based on projected FCF. "
        "Margin of Safety (MOS) = the discount you demand before buying — protects against model error. "
        "Bear/Base/Bull differ by assumed growth and required return rates. "
        "⚠️ DCF is sensitive to assumptions — treat as a range, not a precise target."
    )


# ---------------------------------------------------------------------------
# Technical indicator helpers
# ---------------------------------------------------------------------------

def _ind_ma(prices: pd.Series, period: int) -> pd.Series:
    return prices.rolling(period).mean()

def _ind_bbands(prices: pd.Series, period: int = 20, n_std: float = 2.0):
    ma  = prices.rolling(period).mean()
    std = prices.rolling(period).std()
    return ma + n_std * std, ma, ma - n_std * std   # upper, mid, lower

def _ind_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    delta = prices.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))

def _ind_macd(prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast   = prices.ewm(span=fast, adjust=False).mean()
    ema_slow   = prices.ewm(span=slow, adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line= macd_line.ewm(span=signal, adjust=False).mean()
    histogram  = macd_line - signal_line
    return macd_line, signal_line, histogram


def _render_indicators(dates: list, prices: list, selected: list, key_prefix: str = ""):
    """
    Render MA / Bollinger / RSI / MACD charts.
    Period inputs are shown inline when RSI or MACD is selected.
    Returns overlay traces (MA, BB) to add to the caller's price figure.
    """
    if not prices or not dates:
        return []

    px_series = pd.Series(prices, index=pd.to_datetime(dates))
    overlay_traces = []

    # ── Period controls for RSI / MACD (shown only when selected) ────────────
    rsi_period, macd_fast, macd_slow, macd_sig = 14, 12, 26, 9
    show_rsi  = "RSI" in selected
    show_macd = "MACD" in selected

    if show_rsi or show_macd:
        _pcols = st.columns(5)
        if show_rsi:
            with _pcols[0]:
                rsi_period = st.number_input(
                    "RSI period", min_value=2, max_value=50, value=14, step=1,
                    key=f"{key_prefix}_rsi_p",
                    help="Lookback window. 14 = standard. Lower = more sensitive/noisy; higher = smoother/slower.")
        if show_macd:
            with _pcols[1]:
                macd_fast = st.number_input(
                    "MACD fast EMA", min_value=2, max_value=50, value=12, step=1,
                    key=f"{key_prefix}_macd_f",
                    help="Fast EMA period. Standard = 12.")
            with _pcols[2]:
                macd_slow = st.number_input(
                    "MACD slow EMA", min_value=5, max_value=100, value=26, step=1,
                    key=f"{key_prefix}_macd_s",
                    help="Slow EMA period. Standard = 26. Must be > fast EMA.")
            with _pcols[3]:
                macd_sig = st.number_input(
                    "Signal line", min_value=2, max_value=50, value=9, step=1,
                    key=f"{key_prefix}_macd_sig",
                    help="EMA of the MACD line used as a signal. Standard = 9.")

    # ── Moving averages & Bollinger Bands (overlays) ──────────────────────────
    ma_colors = {"MA(20)": "#fbbf24", "MA(50)": "#60a5fa", "MA(200)": "#a78bfa"}
    for label, color in ma_colors.items():
        if label in selected:
            ma = _ind_ma(px_series, int(label[3:-1]))
            overlay_traces.append(go.Scatter(
                x=dates, y=ma.values, name=label,
                line=dict(color=color, width=1.5, dash="dot"),
            ))

    if "Bollinger Bands" in selected:
        upper, mid, lower = _ind_bbands(px_series)
        overlay_traces.append(go.Scatter(
            x=dates, y=upper.values, name="BB Upper",
            line=dict(color="#94a3b8", width=1, dash="dash"), showlegend=True,
        ))
        overlay_traces.append(go.Scatter(
            x=dates, y=lower.values, name="BB Lower",
            line=dict(color="#94a3b8", width=1, dash="dash"),
            fill="tonexty", fillcolor="rgba(148,163,184,0.08)", showlegend=True,
        ))

    # ── RSI subplot ───────────────────────────────────────────────────────────
    if show_rsi:
        rsi = _ind_rsi(px_series, period=rsi_period)
        fig_rsi = go.Figure()
        fig_rsi.add_trace(go.Scatter(
            x=dates, y=rsi.values, name=f"RSI({rsi_period})",
            line=dict(color="#f97316", width=1.8),
        ))
        fig_rsi.add_hline(y=70, line_color="#f87171", line_dash="dash", line_width=1,
                          annotation_text="Overbought (70)", annotation_position="right",
                          annotation_font=dict(color="#f87171", size=10))
        fig_rsi.add_hline(y=30, line_color="#4ade80", line_dash="dash", line_width=1,
                          annotation_text="Oversold (30)", annotation_position="right",
                          annotation_font=dict(color="#4ade80", size=10))
        fig_rsi.update_layout(
            title=f"RSI ({rsi_period})", height=180,
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#ccc"), showlegend=False,
            margin=dict(t=30, b=20, l=10, r=60),
            yaxis=dict(range=[0, 100], showgrid=True, gridcolor="#2a2a3e",
                       tickvals=[30, 50, 70]),
            xaxis=dict(showgrid=False),
            hovermode="x unified",
        )
        st.plotly_chart(fig_rsi, width='stretch')

    # ── MACD subplot ──────────────────────────────────────────────────────────
    if show_macd:
        _slow = max(macd_slow, macd_fast + 1)   # ensure slow > fast
        macd_line, signal_line, histogram = _ind_macd(px_series, macd_fast, _slow, macd_sig)
        hist_colors = ["#4ade80" if v >= 0 else "#f87171" for v in histogram.fillna(0)]
        fig_macd = go.Figure()
        fig_macd.add_trace(go.Bar(
            x=dates, y=histogram.values, name="Histogram",
            marker_color=hist_colors, opacity=0.7,
        ))
        fig_macd.add_trace(go.Scatter(
            x=dates, y=macd_line.values, name=f"MACD({macd_fast},{_slow})",
            line=dict(color="#60a5fa", width=1.8),
        ))
        fig_macd.add_trace(go.Scatter(
            x=dates, y=signal_line.values, name=f"Signal({macd_sig})",
            line=dict(color="#f97316", width=1.4, dash="dot"),
        ))
        fig_macd.add_hline(y=0, line_color="#555", line_dash="dash", line_width=1)
        fig_macd.update_layout(
            title=f"MACD ({macd_fast}, {_slow}, {macd_sig})", height=200,
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#ccc"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
            margin=dict(t=30, b=20, l=10, r=10),
            yaxis=dict(showgrid=True, gridcolor="#2a2a3e"),
            xaxis=dict(showgrid=False),
            hovermode="x unified",
        )
        st.plotly_chart(fig_macd, width='stretch')

    return overlay_traces


# ---------------------------------------------------------------------------
# Metric card
# ---------------------------------------------------------------------------

_METRIC_TIPS: dict[str, str] = {
    # Portfolio summary
    "Account Total":        "Total value of the account: market value of all holdings plus cash balance.",
    "Invested (Market)":    "Current market value of all open stock positions (excludes cash).",
    "Cash Balance":         "Uninvested cash sitting in the account.",
    "Unrealized Gain":      "Paper gain or loss on open positions. Not taxed until you sell.",
    "Total Gain / Loss":    "Combined total of unrealized gains, realized gains, dividends, and lending income since inception.",
    "Total Deposited":      "Sum of all cash deposits made into the account.",
    "Realized Gains":       "Profit or loss locked in by selling positions.",
    "Dividends Received":   "Total dividend and capital gains distribution income received.",
    "Stock Lending Income": "Income earned by lending your shares to short-sellers via your broker.",
    # Position metrics
    "Avg Cost":             "Average price paid per share, weighted across all open lots.",
    "Cost Basis":           "Total amount paid for all currently held shares.",
    "Market Value":         "Current value of the position at today's live price.",
    "Unrealized":           "Paper gain or loss on this position. Not taxed until sold.",
    "Realized Gain":        "Profit or loss locked in when shares were sold.",
    "Dividends":            "Total dividends received from this ticker.",
    "Live Price":           "Most recent market price per share.",
    "Shares":               "Number of shares currently held.",
    # Returns
    "Ann. TWR":             "Annualized Time-Weighted Return. Measures performance independent of cash flows — the best apples-to-apples comparison against benchmarks.",
    "Ann. MWR":             "Annualized Money-Weighted Return (IRR). Reflects the return on YOUR actual invested dollars, weighted by timing. Higher than TWR = you timed purchases well.",
    "Period Start Value":   "Portfolio or position value at the start of the selected period.",
    "Period End Value":     "Portfolio or position value at the end of the selected period.",
    "Period Return $":      "Dollar gain or loss during the period, excluding new cash invested.",
    "Portfolio TWR":        "Time-Weighted Return for the full portfolio over the selected period.",
    "Start Value":          "Portfolio value at the start of the selected period.",
    "End Value":            "Portfolio value at the end of the selected period.",
    # Fundamentals
    "P/E (TTM)":            "Price-to-Earnings ratio using trailing 12-month earnings. Lower may mean cheaper, but varies by sector and growth rate.",
    "Forward P/E":          "P/E based on next year's estimated earnings. Lower than trailing P/E implies analysts expect earnings growth.",
    "Market Cap":           "Total market value of all outstanding shares (share price × shares outstanding).",
    "Avg Target":           "Mean analyst 12-month price target across all covering analysts.",
    "High Target":          "Most optimistic analyst price target.",
    "Low Target":           "Most pessimistic analyst price target.",
    "ROIC":                 "Return on Invested Capital = Operating Cash Flow ÷ (Total Assets − Cash). Measures how efficiently the business converts capital into cash. >10% is generally strong.",
    "Quality Score":        "Composite 0–100 score across 8 checks: gross margin, operating margin, FCF margin, ROIC, net cash position, revenue growth, earnings growth, and debt level. Each check is worth 12.5 pts.",
    # Balance sheet
    "Cash & Equiv.":        "Cash and short-term investments on the balance sheet.",
    "Total Debt":           "All short-term and long-term interest-bearing debt.",
    "Net Cash":             "Cash minus total debt. Positive = more cash than debt (fortress balance sheet).",
    "Total Assets":         "Everything the company owns: cash, inventory, property, intangibles.",
    "Revenue (TTM)":        "Total revenue over the trailing 12 months.",
    "Free Cash Flow":       "Cash generated after capital expenditures. Often considered the 'real' earnings — harder to manipulate than net income.",
    # DCF
    "Intrinsic Value (base)": "DCF fair value per share using base-case assumptions (10% growth, 10% discount rate, 3% terminal growth, 10 years).",
    "After 25% Margin of Safety": "Intrinsic value discounted by 25% — the price at which the DCF model says the stock offers a margin of safety against model error.",
    # Performance tab
    "Next Earnings Report": "The next scheduled date when the company reports quarterly financial results. Stock prices often move sharply around earnings.",
}


def metric_card(label, value, sub=None, sub_color=None, tooltip=None):
    color = sub_color or "neutral"
    sub_html = f'<div class="metric-label" style="margin-top:4px">{sub}</div>' if sub else ""
    tip = tooltip or _METRIC_TIPS.get(label)
    tip_html = (f'<span title="{tip}" style="cursor:help;color:#555;font-size:0.7rem;'
                f'margin-left:5px;vertical-align:middle">ⓘ</span>'
                if tip else "")
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">{label}{tip_html}</div>
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
