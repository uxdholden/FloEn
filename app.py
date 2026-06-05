import streamlit as st
import pandas as pd
import sqlite3
from pathlib import Path
from datetime import datetime, date, timedelta
import io
import re

DB_PATH = Path("flogas_dashboard.db")

st.set_page_config(page_title="My Flogas Dashboard", layout="wide")


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS bills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bill_date TEXT,
        due_date TEXT,
        amount REAL,
        status TEXT,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS meter_readings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reading_at TEXT,
        meter_type TEXT,
        reading_value REAL,
        source TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        due_date TEXT,
        status TEXT,
        notes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    return conn


def seed_defaults(conn):
    bills_count = pd.read_sql_query("select count(*) as c from bills", conn).iloc[0]["c"]
    rem_count = pd.read_sql_query("select count(*) as c from reminders", conn).iloc[0]["c"]
    if bills_count == 0:
        conn.executemany(
            "insert into bills (bill_date, due_date, amount, status, notes) values (?, ?, ?, ?, ?)",
            [
                (str(date.today().replace(day=1)), str(date.today() + timedelta(days=12)), 124.80, "Due", "Sample bill"),
                (str((date.today().replace(day=1) - timedelta(days=25))), str((date.today().replace(day=1) - timedelta(days=12))), 98.40, "Paid", "Imported manually"),
            ],
        )
    if rem_count == 0:
        conn.executemany(
            "insert into reminders (title, due_date, status, notes) values (?, ?, ?, ?)",
            [
                ("Submit meter reading", str(date.today() + timedelta(days=7)), "Open", "Avoid estimated bill"),
                ("Check latest bill", str(date.today() + timedelta(days=3)), "Open", "Review charges and dates"),
            ],
        )
    conn.commit()


def parse_interval_csv(uploaded_file):
    raw = uploaded_file.read().decode("utf-8", errors="ignore")
    pattern = re.compile(r"(\d{11})(\d+\.\d{3})Active Import Interval kW(\d{2}-\d{2}-\d{4})\s+(\d{4})")
    rows = []
    for m in pattern.finditer(raw):
        rows.append({
            "mprn": m.group(1),
            "read_value_kw": float(m.group(2)),
            "date": m.group(3),
            "time": m.group(4),
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["reading_at"] = pd.to_datetime(df["date"] + " " + df["time"], format="%d-%m-%Y %H%M", errors="coerce")
    df = df.dropna(subset=["reading_at"]).sort_values("reading_at")
    df["interval_hours"] = 0.5
    df["estimated_kwh"] = df["read_value_kw"] * df["interval_hours"]
    return df[["mprn", "reading_at", "read_value_kw", "estimated_kwh"]]


def kpi_cards(conn):
    bills = pd.read_sql_query("select * from bills order by bill_date desc", conn)
    readings = pd.read_sql_query("select * from meter_readings order by reading_at desc", conn)
    reminders = pd.read_sql_query("select * from reminders", conn)
    balance_due = bills.loc[bills["status"].str.lower().eq("due"), "amount"].sum() if not bills.empty else 0
    latest_bill = bills.iloc[0]["bill_date"] if not bills.empty else "No data"
    latest_read = readings.iloc[0]["reading_at"] if not readings.empty else "No data"
    open_rem = int((reminders["status"].fillna("").str.lower() != "done").sum()) if not reminders.empty else 0
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Balance due", f"€{balance_due:,.2f}")
    c2.metric("Latest bill", str(latest_bill))
    c3.metric("Latest reading", str(latest_read)[:16])
    c4.metric("Open reminders", open_rem)


def overview_tab(conn):
    st.subheader("Overview")
    kpi_cards(conn)
    bills = pd.read_sql_query("select * from bills order by bill_date desc", conn)
    readings = pd.read_sql_query("select * from meter_readings order by reading_at desc limit 30", conn)

    col1, col2 = st.columns([1.1, 1])
    with col1:
        st.markdown("### Bills")
        st.dataframe(bills[["bill_date", "due_date", "amount", "status", "notes"]] if not bills.empty else pd.DataFrame(), use_container_width=True)
    with col2:
        st.markdown("### Recent readings")
        st.dataframe(readings[["reading_at", "meter_type", "reading_value", "source"]] if not readings.empty else pd.DataFrame(), use_container_width=True)

    if not readings.empty:
        chart_df = readings.copy()
        chart_df["reading_at"] = pd.to_datetime(chart_df["reading_at"], errors="coerce")
        chart_df = chart_df.sort_values("reading_at")
        st.markdown("### Reading trend")
        st.line_chart(chart_df.set_index("reading_at")["reading_value"])


def bills_tab(conn):
    st.subheader("Bills")
    with st.form("add_bill"):
        c1, c2, c3, c4 = st.columns(4)
        bill_date = c1.date_input("Bill date", value=date.today())
        due_date = c2.date_input("Due date", value=date.today() + timedelta(days=14))
        amount = c3.number_input("Amount (€)", min_value=0.0, step=0.01)
        status = c4.selectbox("Status", ["Due", "Paid", "Planned"])
        notes = st.text_input("Notes")
        submitted = st.form_submit_button("Save bill")
        if submitted:
            conn.execute(
                "insert into bills (bill_date, due_date, amount, status, notes) values (?, ?, ?, ?, ?)",
                (str(bill_date), str(due_date), float(amount), status, notes),
            )
            conn.commit()
            st.success("Bill saved")

    bills = pd.read_sql_query("select * from bills order by bill_date desc", conn)
    st.dataframe(bills, use_container_width=True)
    if not bills.empty:
        bills_chart = bills.copy()
        bills_chart["bill_date"] = pd.to_datetime(bills_chart["bill_date"], errors="coerce")
        bills_chart = bills_chart.sort_values("bill_date")
        st.bar_chart(bills_chart.set_index("bill_date")["amount"])


def readings_tab(conn):
    st.subheader("Meter readings")

    with st.expander("Add manual reading", expanded=False):
        with st.form("add_reading"):
            c1, c2, c3 = st.columns(3)
            reading_at = c1.datetime_input("Reading date and time", value=datetime.now()) if hasattr(c1, 'datetime_input') else None
            meter_type = c2.selectbox("Meter type", ["Electricity", "Gas", "LPG", "Other"])
            reading_value = c3.number_input("Reading value", min_value=0.0, step=0.001, format="%.3f")
            source = st.text_input("Source", value="Manual")
            submitted = st.form_submit_button("Save reading")
            if submitted:
                if reading_at is None:
                    reading_at = datetime.now()
                conn.execute(
                    "insert into meter_readings (reading_at, meter_type, reading_value, source) values (?, ?, ?, ?)",
                    (reading_at.isoformat(sep=' ', timespec='minutes'), meter_type, float(reading_value), source),
                )
                conn.commit()
                st.success("Reading saved")

    upload = st.file_uploader("Upload Flogas / smart meter interval CSV", type=["csv", "txt"])
    if upload is not None:
        parsed = parse_interval_csv(upload)
        if parsed.empty:
            st.warning("Could not parse interval readings from that file.")
        else:
            st.success(f"Parsed {len(parsed):,} interval rows")
            st.dataframe(parsed.tail(100), use_container_width=True)
            preview = parsed.copy()
            preview["date"] = preview["reading_at"].dt.date
            daily = preview.groupby("date", as_index=False)["estimated_kwh"].sum()
            st.markdown("### Estimated daily usage from interval kW")
            st.line_chart(daily.set_index("date")["estimated_kwh"])

            if st.button("Import parsed readings to database"):
                rows = [
                    (r.reading_at.isoformat(sep=' ', timespec='minutes'), "Electricity", float(r.read_value_kw), "CSV import")
                    for r in parsed.itertuples(index=False)
                ]
                conn.executemany(
                    "insert into meter_readings (reading_at, meter_type, reading_value, source) values (?, ?, ?, ?)",
                    rows,
                )
                conn.commit()
                st.success(f"Imported {len(rows):,} readings")

    readings = pd.read_sql_query("select * from meter_readings order by reading_at desc", conn)
    st.dataframe(readings, use_container_width=True)


def reminders_tab(conn):
    st.subheader("Reminders")
    with st.form("add_reminder"):
        c1, c2, c3 = st.columns(3)
        title = c1.text_input("Task")
        due_date = c2.date_input("Due date", value=date.today() + timedelta(days=7))
        status = c3.selectbox("Status", ["Open", "Done"])
        notes = st.text_input("Notes")
        submitted = st.form_submit_button("Save reminder")
        if submitted and title.strip():
            conn.execute(
                "insert into reminders (title, due_date, status, notes) values (?, ?, ?, ?)",
                (title.strip(), str(due_date), status, notes),
            )
            conn.commit()
            st.success("Reminder saved")

    reminders = pd.read_sql_query("select * from reminders order by due_date asc", conn)
    st.dataframe(reminders, use_container_width=True)


def contacts_tab():
    st.subheader("Contacts and notes")
    st.markdown("""
- Natural Gas & Electricity: 041 214 9500
- LPG Support: 041 214 9600
- Customer support email: customersupport@flogas.ie
- LPG support email: lpgsupport@flogas.ie
    """)
    st.info("This app is a personal companion dashboard. It does not log into the official Flogas portal.")


def main():
    conn = get_conn()
    seed_defaults(conn)

    st.title("My Flogas Dashboard")
    st.caption("Personal Streamlit companion app for bills, readings, and reminders")

    tabs = st.tabs(["Overview", "Bills", "Readings", "Reminders", "Contacts"])
    with tabs[0]:
        overview_tab(conn)
    with tabs[1]:
        bills_tab(conn)
    with tabs[2]:
        readings_tab(conn)
    with tabs[3]:
        reminders_tab(conn)
    with tabs[4]:
        contacts_tab()


if __name__ == "__main__":
    main()
