import datetime as dt

import streamlit as st

from core import auth, flex_credits, loaders, saasant

st.set_page_config(page_title="FLEX Credits", layout="wide")
auth.require_login()
auth.sidebar_identity()

st.title("FLEX Monthly Credit Memos")
st.caption("Generates the SaasAnt credit-memo import (item Flex-credits, class 03-Telemedicine).")

flex = loaders.flex_master()
clinics = flex.get("clinics", [])

c1, c2, c3 = st.columns(3)
today = dt.date.today()
year = c1.number_input("Year", value=today.year, step=1)
month = c2.selectbox("Month", list(range(1, 13)), index=today.month - 1,
                     format_func=lambda m: dt.date(2000, m, 1).strftime("%B"))
start_ref = c3.number_input("Starting Credit Memo No (from QBO max + 1)", value=50000, step=1)

st.info(
    "FLEX is closed to new entrants — this copies the active, credit-bearing clinics from the "
    "master. Confirm the active list against the latest finance remittance before uploading."
)

df, next_ref = flex_credits.build_import(clinics, int(year), int(month), int(start_ref))

st.metric("Credit memos", len(df))
st.metric("Total credits", f"${df['Product/Service Amount'].sum():,.2f}" if not df.empty else "$0.00")
st.dataframe(df, use_container_width=True, height=420)

mname = dt.date(2000, int(month), 1).strftime("%B")
st.download_button(
    "Download credit-memo import (xlsx)",
    saasant.to_xlsx_bytes(df, f"FlexCredits{mname}{year}"),
    file_name=f"FlexCredits_{mname}_{year}.xlsx",
    disabled=df.empty,
)
st.caption(f"Next available reference number after this batch: {next_ref}")
