"""
tab_transactions.py — Transactions tab render function.
"""

import streamlit as st
from datetime import date

from loaders import load_transactions


def render(selected_dbs: list, selected_account):
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
