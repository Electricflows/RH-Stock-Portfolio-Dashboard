"""
tab_manage.py — Manage tab render function (manual transaction entry, edit/delete, delisted tickers, aliases).
"""

import streamlit as st
import pandas as pd
from datetime import datetime

from Calculations import get_transactions_df
from Prices import (
    get_delisted_tickers, mark_ticker_delisted, unmark_ticker_delisted,
    get_ticker_aliases, add_ticker_alias, remove_ticker_alias,
)
from loaders import load_delisted


# ---------------------------------------------------------------------------
# Transaction type constants
# ---------------------------------------------------------------------------

_TX_LABELS = [
    "Buy", "Sell", "Dividend", "Dividend Reinvestment",
    "Capital Gains Distribution", "Deposit", "Withdrawal",
    "Transfer In (Shares)", "Lending Income", "Interest",
    "Subscription Fee", "Stock Split",
]
_TX_TYPE_MAP = {
    "Buy":                         "buy",
    "Sell":                        "sell",
    "Dividend":                    "dividend",
    "Dividend Reinvestment":       "dividend_reinvestment",
    "Capital Gains Distribution":  "capital_gains_distribution",
    "Deposit":                     "deposit",
    "Withdrawal":                  "withdrawal",
    "Transfer In (Shares)":        "transfer_in",
    "Lending Income":              "lending_income",
    "Interest":                    "interest",
    "Subscription Fee":            "subscription_fee",
    "Stock Split":                 "stock_split",
}
_TX_CODE_MAP = {
    "buy": "Buy", "sell": "Sell",
    "dividend": "CDIV", "dividend_reinvestment": "DRIP",
    "capital_gains_distribution": "LCAP",
    "deposit": "ACH", "withdrawal": "ACH",
    "transfer_in": "ITRF", "lending_income": "SLIP",
    "interest": "INT", "subscription_fee": "GOLD",
    "stock_split": "SPLIT",
}
# Types where the amount should be negative (cash outflow)
_OUTFLOW_TYPES = {"buy", "dividend_reinvestment", "withdrawal", "subscription_fee"}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _insert_transaction(db_path: str, account: str, activity_date: str,
                        tx_type: str, ticker: str, description: str,
                        quantity, price, amount) -> tuple:
    import hashlib, sqlite3 as _sq
    trans_code = _TX_CODE_MAP.get(tx_type, "")
    key = "|".join([str(activity_date), str(ticker or ""), trans_code,
                    str(quantity or ""), str(amount or "")])
    tx_hash = hashlib.sha256(key.encode()).hexdigest()
    try:
        conn = _sq.connect(db_path)
        conn.execute("""
            INSERT INTO transactions
              (tx_hash, account, activity_date, process_date, settle_date,
               ticker, transaction_type, trans_code, description,
               quantity, price, amount, source_file, imported_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (tx_hash, account, activity_date, activity_date, activity_date,
               ticker or None, tx_type, trans_code, description or "",
               quantity or None, price or None, amount or None,
               "manual_entry", datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True, "Transaction added."
    except _sq.IntegrityError:
        return False, "Duplicate — a transaction with the same date, ticker, type, qty and amount already exists."
    except Exception as exc:
        return False, str(exc)


def _delete_transactions(db_path: str, hashes: list):
    import sqlite3 as _sq
    conn = _sq.connect(db_path)
    conn.executemany("DELETE FROM transactions WHERE tx_hash = ?", [(h,) for h in hashes])
    conn.commit()
    conn.close()


def _update_transaction(db_path: str, tx_hash: str, activity_date: str,
                        ticker: str, tx_type: str, quantity, price, amount,
                        description: str):
    import hashlib, sqlite3 as _sq
    trans_code = _TX_CODE_MAP.get(tx_type, "")
    conn = _sq.connect(db_path)
    conn.execute("""
        UPDATE transactions SET
          activity_date=?, process_date=?, settle_date=?,
          ticker=?, transaction_type=?, trans_code=?,
          description=?, quantity=?, price=?, amount=?
        WHERE tx_hash=?
    """, (activity_date, activity_date, activity_date,
          ticker or None, tx_type, trans_code, description or "",
          quantity or None, price or None, amount or None, tx_hash))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render(selected_dbs: list, selected_account, selected_label: str,
           today, db_for_account: dict):
    # Use the sidebar account selection — no separate selector needed here
    mgmt_dbs  = selected_dbs
    mgmt_acct = selected_account
    st.caption(f"Account: **{selected_label}** — change via the sidebar selector")

    sub_add, sub_edit = st.tabs(["➕  Add Transaction", "✏️  Edit / Delete"])

    # ── ADD ─────────────────────────────────────────────────────────────────
    with sub_add:
        st.subheader("Manual Transaction Entry")
        with st.form("manual_tx_form", clear_on_submit=True):
            r1c1, r1c2, r1c3 = st.columns(3)
            with r1c1:
                mt_label = st.selectbox("Transaction Type", _TX_LABELS)
                mt_type  = _TX_TYPE_MAP[mt_label]
            with r1c2:
                mt_date = st.date_input("Date", value=today)
            with r1c3:
                mt_ticker = st.text_input("Ticker (leave blank for cash txns)").upper().strip()

            r2c1, r2c2, r2c3 = st.columns(3)
            with r2c1:
                mt_qty = st.number_input("Quantity", value=0.0, min_value=0.0,
                                         step=0.0001, format="%.4f")
            with r2c2:
                mt_price = st.number_input("Price per share ($)", value=0.0,
                                           min_value=0.0, step=0.01, format="%.4f")
            with r2c3:
                outflow_hint = " (enter as negative for outflows)" if mt_type not in _OUTFLOW_TYPES else " (enter as negative)"
                mt_amount = st.number_input(f"Amount ($){outflow_hint}",
                                            value=0.0, step=0.01, format="%.2f")

            mt_desc = st.text_input("Description (optional)")
            submitted = st.form_submit_button("Add Transaction", type="primary")

        # Resolve which db to write to (use sidebar account; if All Accounts, require specific)
        if selected_label == "All Accounts":
            mt_db = mt_acct_name = None
            if submitted:
                st.warning("Please select a specific account in the sidebar before adding a transaction.")
        else:
            mt_db_path2, mt_acct_name2 = db_for_account[selected_label]
            mt_db, mt_acct_name = mt_db_path2, mt_acct_name2

        if submitted and mt_db:
            ok, msg = _insert_transaction(
                mt_db, mt_acct_name,
                mt_date.strftime("%m/%d/%Y"),
                mt_type,
                mt_ticker or None,
                mt_desc,
                mt_qty   if mt_qty   != 0 else None,
                mt_price if mt_price != 0 else None,
                mt_amount if mt_amount != 0 else None,
            )
            if ok:
                st.success(msg)
                st.cache_data.clear()
            else:
                st.error(msg)

    # ── EDIT / DELETE ────────────────────────────────────────────────────────
    with sub_edit:
        st.subheader("Edit or Delete Transactions")

        ef1, ef2, ef3 = st.columns([1, 1, 1])
        with ef1:
            edit_ticker = st.text_input("Filter Ticker", key="et").upper().strip() or None
        with ef2:
            edit_type = st.selectbox("Filter Type", [""] + _TX_LABELS, key="etype")
            edit_type_val = _TX_TYPE_MAP.get(edit_type) if edit_type else None
        with ef3:
            edit_limit = st.number_input("Max rows", value=100, min_value=10,
                                          max_value=2000, step=50, key="elimit")

        @st.cache_data(ttl=30)
        def _load_edit_txns(dbs_tuple, account, ticker, tx_type):
            return get_transactions_df(list(dbs_tuple), account=account,
                                       ticker=ticker, tx_type=tx_type,
                                       include_hash=True)

        df_edit_raw = _load_edit_txns(
            tuple(mgmt_dbs), mgmt_acct, edit_ticker, edit_type_val
        ).head(edit_limit)

        if df_edit_raw.empty:
            st.info("No transactions match the filters.")
        else:
            st.caption(f"{len(df_edit_raw):,} rows shown — check rows to delete, or edit cells then click Save.")

            # Separate hash column; hide it from display but keep for operations
            hashes = df_edit_raw["_hash"].tolist() if "_hash" in df_edit_raw.columns else []
            df_display = df_edit_raw.drop(columns=["_hash"], errors="ignore").copy()
            df_display.insert(0, "🗑 Delete", False)

            edited = st.data_editor(
                df_display,
                width='stretch',
                hide_index=True,
                num_rows="fixed",
                column_config={
                    "🗑 Delete": st.column_config.CheckboxColumn("🗑", width="small"),
                    "Date":        st.column_config.TextColumn("Date"),
                    "Ticker":      st.column_config.TextColumn("Ticker"),
                    "Type":        st.column_config.SelectboxColumn(
                                       "Type", options=list(_TX_TYPE_MAP.values())),
                    "Qty":         st.column_config.NumberColumn("Qty",   format="%.4f"),
                    "Price":       st.column_config.NumberColumn("Price", format="%.4f"),
                    "Amount":      st.column_config.NumberColumn("Amount", format="%.2f"),
                    "Description": st.column_config.TextColumn("Description"),
                },
                key="tx_editor",
            )

            col_del, col_save, _ = st.columns([1, 1, 3])
            with col_del:
                if st.button("🗑 Delete Checked", type="secondary"):
                    to_delete = [hashes[i] for i, row in edited.iterrows()
                                 if row.get("🗑 Delete") and i < len(hashes)]
                    if to_delete:
                        # Delete from all matching dbs
                        for db in mgmt_dbs:
                            _delete_transactions(db, to_delete)
                        st.success(f"Deleted {len(to_delete)} transaction(s).")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.warning("No rows checked for deletion.")

            with col_save:
                if st.button("💾 Save Edits", type="primary"):
                    saved = 0
                    orig = df_display.drop(columns=["🗑 Delete"], errors="ignore")
                    edit_data = edited.drop(columns=["🗑 Delete"], errors="ignore")
                    for i in range(min(len(orig), len(edit_data), len(hashes))):
                        if not orig.iloc[i].equals(edit_data.iloc[i]):
                            row = edit_data.iloc[i]
                            # Find which db has this hash
                            import sqlite3 as _sq2
                            for db in mgmt_dbs:
                                c = _sq2.connect(db)
                                exists = c.execute("SELECT 1 FROM transactions WHERE tx_hash=?",
                                                   (hashes[i],)).fetchone()
                                c.close()
                                if exists:
                                    _update_transaction(
                                        db, hashes[i],
                                        str(row.get("Date", "")),
                                        str(row.get("Ticker", "") or ""),
                                        str(row.get("Type", "")),
                                        row.get("Qty"),
                                        row.get("Price"),
                                        row.get("Amount"),
                                        str(row.get("Description", "") or ""),
                                    )
                                    saved += 1
                                    break
                    if saved:
                        st.success(f"Saved {saved} change(s).")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.info("No changes detected.")


    # ── DELISTED TICKERS ──────────────────────────────────────────────────
    st.divider()
    st.subheader("Delisted / Liquidated Tickers")
    st.caption(
        "Tickers marked here are excluded from price-fetch attempts and shown with a "
        "DELISTED badge on position cards. Their position value uses the final price "
        "you record (enter 0 for a full liquidation)."
    )

    _dl_all = load_delisted()
    if _dl_all:
        import pandas as _pd2
        _dl_rows = [
            {"Ticker": k, "Final Price": v["final_price"], "Notes": v["notes"]}
            for k, v in sorted(_dl_all.items())
        ]
        st.dataframe(_pd2.DataFrame(_dl_rows), width='stretch', hide_index=True,
                     column_config={
                         "Final Price": st.column_config.NumberColumn(format="$%.4f"),
                     })
        _rm_ticker = st.selectbox("Restore ticker (remove from delisted list)",
                                  options=[""] + sorted(_dl_all.keys()),
                                  key="dl_restore_sel")
        if st.button("Restore selected", key="dl_restore_btn") and _rm_ticker:
            unmark_ticker_delisted(_rm_ticker)
            st.cache_data.clear()
            st.rerun()
    else:
        st.info("No tickers currently marked as delisted.")

    st.markdown("**Add a delisted ticker manually:**")
    with st.form("add_delisted_form", clear_on_submit=True):
        _adl_c1, _adl_c2, _adl_c3 = st.columns([1, 1, 2])
        with _adl_c1:
            _adl_ticker = st.text_input("Ticker").upper().strip()
        with _adl_c2:
            _adl_price = st.number_input("Final price ($)", value=0.0,
                                         min_value=0.0, step=0.01, format="%.4f")
        with _adl_c3:
            _adl_notes = st.text_input("Notes", placeholder="e.g. Liquidated Mar 2025")
        if st.form_submit_button("Mark as Delisted", type="primary"):
            if _adl_ticker:
                mark_ticker_delisted(_adl_ticker, _adl_price, _adl_notes)
                st.cache_data.clear()
                st.success(f"{_adl_ticker} marked as delisted.")
                st.rerun()
            else:
                st.warning("Enter a ticker symbol.")

    st.divider()
    st.subheader("Ticker Renames / Aliases")
    st.caption("Map an old ticker symbol to a new one for price fetching (e.g. STRV → STXF after a rename). "
               "Prices will be fetched under the new symbol and stored under the original.")

    st.markdown("**Rename ticker in transactions (permanent):**")
    st.caption("Updates all transaction records across your account databases. Use this when a stock ticker changes and you want the position to appear under the new name.")
    with st.form("rename_ticker_form", clear_on_submit=True):
        _rn1, _rn2 = st.columns(2)
        with _rn1:
            _rn_old = st.text_input("Old ticker", placeholder="STRV").upper().strip()
        with _rn2:
            _rn_new = st.text_input("New ticker", placeholder="STXF").upper().strip()
        if st.form_submit_button("Rename in Transactions", type="primary"):
            if _rn_old and _rn_new and _rn_old != _rn_new:
                _rn_count = 0
                for _rn_db in selected_dbs:
                    try:
                        _rn_conn = __import__("sqlite3").connect(_rn_db)
                        _cur = _rn_conn.execute(
                            "UPDATE transactions SET ticker = ? WHERE UPPER(ticker) = ?",
                            (_rn_new, _rn_old)
                        )
                        _rn_count += _cur.rowcount
                        _rn_conn.commit()
                        _rn_conn.close()
                    except Exception as _e:
                        st.error(f"Error updating {_rn_db}: {_e}")
                if _rn_count:
                    st.cache_data.clear()
                    st.success(f"Renamed {_rn_count} transaction(s) from {_rn_old} → {_rn_new}. Refresh Data to update prices.")
                    st.rerun()
                else:
                    st.warning(f"No transactions found for {_rn_old}.")
            else:
                st.warning("Enter two different ticker symbols.")
    st.divider()

    _aliases = get_ticker_aliases()
    if _aliases:
        st.markdown("**Active aliases:**")
        for _old, _new in sorted(_aliases.items()):
            _ac1, _ac2 = st.columns([4, 1])
            with _ac1:
                st.markdown(f"`{_old}` → `{_new}`")
            with _ac2:
                if st.button("Remove", key=f"rm_alias_{_old}"):
                    remove_ticker_alias(_old)
                    st.cache_data.clear()
                    st.rerun()
    else:
        st.info("No ticker aliases defined.")

    st.markdown("**Add a ticker alias:**")
    with st.form("add_alias_form", clear_on_submit=True):
        _al1, _al2, _al3 = st.columns([2, 2, 3])
        with _al1:
            _al_old = st.text_input("Old ticker (in your transactions)", placeholder="STRV").upper().strip()
        with _al2:
            _al_new = st.text_input("New ticker (current Yahoo Finance symbol)", placeholder="STXF").upper().strip()
        with _al3:
            _al_notes = st.text_input("Notes", placeholder="e.g. Renamed to STXF Jun 2025")
        if st.form_submit_button("Add Alias", type="primary"):
            if _al_old and _al_new:
                add_ticker_alias(_al_old, _al_new, _al_notes)
                st.cache_data.clear()
                st.success(f"Alias added: {_al_old} → {_al_new}. Click Refresh Data to re-fetch prices.")
                st.rerun()
            else:
                st.warning("Enter both old and new ticker symbols.")
