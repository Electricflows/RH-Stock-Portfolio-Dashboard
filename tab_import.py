"""
tab_import.py — Import tab render function.
"""

import json
import sqlite3
import tempfile
import streamlit as st
import pandas as pd
from pathlib import Path

from Calculations import get_account_dbs
from Prices import check_and_fill_price_gaps
from Import import init_db, import_csv, account_to_db_name


def render():
    st.subheader("Import Robinhood CSV")
    st.caption("Upload one or more Robinhood account export files. Duplicates are automatically skipped.")

    uploaded_files = st.file_uploader(
        "Select CSV file(s)", type="csv", accept_multiple_files=True
    )

    existing_accounts = []
    for db in get_account_dbs("."):
        try:
            conn_tmp = sqlite3.connect(db)
            rows = conn_tmp.execute("SELECT account_name FROM accounts ORDER BY account_name").fetchall()
            conn_tmp.close()
            existing_accounts.extend(r[0] for r in rows)
        except Exception:
            pass

    account_options = ["-- New account --"] + existing_accounts
    selected_option = st.selectbox("Account name", account_options)

    if selected_option == "-- New account --":
        account_name = st.text_input("New account name", placeholder="e.g. Michael IRA")
    else:
        account_name = selected_option

    if st.button("Import", type="primary", disabled=not (uploaded_files and account_name.strip())):
        account_name = account_name.strip()
        db_path = account_to_db_name(account_name)
        conn_imp = init_db(db_path)

        total_stats = {"inserted": 0, "duplicates": 0, "unknown": 0, "errors": 0}

        for uf in uploaded_files:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
                tmp.write(uf.read())
                tmp_path = tmp.name
            try:
                stats = import_csv(conn_imp, tmp_path, account=account_name)
                for k in total_stats:
                    total_stats[k] += stats[k]
            except ValueError as e:
                st.error(f"{uf.name}: {e}")
            finally:
                Path(tmp_path).unlink(missing_ok=True)

        conn_imp.close()

        st.success(
            f"Import complete — "
            f"**{total_stats['inserted']}** new rows, "
            f"**{total_stats['duplicates']}** duplicates skipped, "
            f"**{total_stats['unknown']}** flagged for review, "
            f"**{total_stats['errors']}** errors."
        )

        if total_stats["inserted"] > 0:
            with st.spinner("Fetching missing price history…"):
                failed = check_and_fill_price_gaps([db_path])
            if failed:
                st.warning(f"Could not fetch prices for: {', '.join(failed)}")
            st.cache_data.clear()
            st.session_state.pop("price_check_done", None)
            st.rerun()

    st.divider()
    st.subheader("Flagged Transactions")
    st.caption("Rows with unrecognised or blank transaction codes. Add new codes to TRANS_CODE_MAP in Import.py to resolve them.")

    flagged_rows = []
    for db in get_account_dbs("."):
        try:
            conn_f = sqlite3.connect(db)
            rows = conn_f.execute(
                """SELECT account, trans_code, raw_row, source_file, imported_at
                   FROM unknown_transactions ORDER BY imported_at DESC"""
            ).fetchall()
            conn_f.close()
            for account_f, trans_code_f, raw_row_f, source_f, imported_at_f in rows:
                try:
                    raw = json.loads(raw_row_f)
                except Exception:
                    raw = {}
                flagged_rows.append({
                    "Account":       account_f,
                    "Trans Code":    trans_code_f,
                    "Date":          raw.get("Activity Date", ""),
                    "Ticker":        raw.get("Instrument", ""),
                    "Description":   raw.get("Description", ""),
                    "Amount":        raw.get("Amount", ""),
                    "Source File":   source_f,
                    "Imported At":   imported_at_f,
                })
        except Exception:
            pass

    if flagged_rows:
        st.dataframe(pd.DataFrame(flagged_rows), width='stretch')
    else:
        st.info("No flagged transactions.")
