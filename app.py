import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import re

st.set_page_config(page_title="My Flogas Dashboard", layout="wide", initial_sidebar_state="expanded")

if "bills" not in st.session_state:
    today = date.today()
    first_of_month = today.replace(day=1)
    st.session_state.bills = pd.DataFrame([
        {"bill_date": str(first_of_month), "due_date": str(today + timedelta(days=12)), "amount": 124.80, "status": "Due", "notes": "Sample current bill"},
        {"bill_date": str(first_of_month - timedelta(days=31)), "due_date": str(first_of_month - timedelta(days=18)), "amount": 98.40, "status": "Paid", "notes": "Sample previous bill"},
    ])

if "meter_readings" not in st.session_state:
    st.session_state.meter_readings = pd.DataFrame(columns=["reading_at", "meter_type", "reading_value", "source"])

if "reminders" not in st.session_state:
    st.session_state.reminders = pd.DataFrame([
        {"title": "Submit meter reading", "due_date": str(date.today() + timedelta(days=7)), "status": "Open", "notes": "Avoid estimated bill"},
        {"title": "Review latest bill", "due_date": str(date.today() + timedelta(days=3)), "status": "Open", "notes": "Check cost and projected trend"},
    ])


def parse_interval_csv(uploaded_file):
    raw = uploaded_file.read().decode("utf-8", errors="ignore")
    pattern = re.compile(
        r"(?P<mprn>\d{11})\s*"
        r"(?P<serial>\d+)\s*"
        r"(?P<value>\d+\.\d{3})\s*"
        r"(?P<read_type>Active Import Interval kW)\s*"
        r"(?P<date>\d{2}-\d{2}-\d{4})\s+"
        r"(?P<time>\d{4})"
    )

    rows = []
    for m in pattern.finditer(raw):
        rows.append({
            "mprn": m.group("mprn"),
            "meter_serial": m.group("serial"),
            "read_value_kw": float(m.group("value")),
            "date": m.group("date"),
            "time": m.group("time"),
        })

    if not rows:
        fallback_lines = []
        for line in raw.splitlines():
            line = line.strip()
            if "Active Import Interval kW" not in line:
                continue
            m = re.search(
                r"(?P<mprn>\d{11})\s*(?P<serial>\d+)\s*(?P<value>\d+\.\d{3})\s*Active Import Interval kW\s*(?P<date>\d{2}-\d{2}-\d{4})\s+(?P<time>\d{4})",
                line,
            )
            if m:
                fallback_lines.append({
                    "mprn": m.group("mprn"),
                    "meter_serial": m.group("serial"),
                    "read_value_kw": float(m.group("value")),
                    "date": m.group("date"),
                    "time": m.group("time"),
                })
        rows = fallback_lines

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["reading_at"] = pd.to_datetime(df["date"] + " " + df["time"], format="%d-%m-%Y %H%M", errors="coerce")
    df = df.dropna(subset=["reading_at"]).sort_values("reading_at")
    df["interval_hours"] = 0.5
    df["estimated_kwh"] = df["read_value_kw"] * df["interval_hours"]
    df["date_only"] = df["reading_at"].dt.date
    return df[["mprn", "meter_serial", "reading_at", "read_value_kw", "estimated_kwh", "date_only"]]


def add_bill(bill_date, due_date, amount, status, notes):
    new_row = pd.DataFrame([{
        "bill_date": str(bill_date),
        "due_date": str(due_date),
        "amount": float(amount),
        "status": status,
        "notes": notes,
    }])
    st.session_state.bills = pd.concat([new_row, st.session_state.bills], ignore_index=True)


def add_reading(reading_at, meter_type, reading_value, source):
    new_row = pd.DataFrame([{
        "reading_at": reading_at,
        "meter_type": meter_type,
        "reading_value": float(reading_value),
        "source": source,
    }])
    st.session_state.meter_readings = pd.concat([new_row, st.session_state.meter_readings], ignore_index=True)


def add_reminder(title, due_date, status, notes):
    new_row = pd.DataFrame([{
        "title": title.strip(),
        "due_date": str(due_date),
        "status": status,
        "notes": notes,
    }])
    st.session_state.reminders = pd.concat([st.session_state.reminders, new_row], ignore_index=True)


def overview_tab():
    st.subheader("Overview")

    bills = st.session_state.bills.copy()
    readings = st.session_state.meter_readings.copy()
    reminders = st.session_state.reminders.copy()

    balance_due = bills.loc[bills["status"].fillna("").str.lower().eq("due"), "amount"].sum() if not bills.empty else 0
    latest_bill = bills.iloc[0]["bill_date"] if not bills.empty else "No data"
    latest_read = readings.iloc[0]["reading_at"] if not readings.empty else "No data"
    open_reminders = int((reminders["status"].fillna("").str.lower() != "done").sum()) if not reminders.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Balance due", f"€{balance_due:,.2f}")
    c2.metric("Latest bill", str(latest_bill))
    c3.metric("Latest reading", str(latest_read)[:16])
    c4.metric("Open reminders", open_reminders)

    left, right = st.columns([1.1, 1])
    with left:
        st.markdown("### Latest bills")
        bill_view = bills[["bill_date", "due_date", "amount", "status", "notes"]] if not bills.empty else pd.DataFrame()
        st.dataframe(bill_view, use_container_width=True, hide_index=True)

    with right:
        st.markdown("### Recent readings")
        read_view = readings[["reading_at", "meter_type", "reading_value", "source"]].head(25) if not readings.empty else pd.DataFrame()
        st.dataframe(read_view, use_container_width=True, hide_index=True)

    if not readings.empty:
        chart_df = readings.copy()
        chart_df["reading_at"] = pd.to_datetime(chart_df["reading_at"], errors="coerce")
        chart_df = chart_df.dropna(subset=["reading_at"]).sort_values("reading_at")
        st.markdown("### Reading trend")
        st.line_chart(chart_df.set_index("reading_at")["reading_value"])


def bills_tab():
    st.subheader("Bills")

    with st.form("add_bill"):
        c1, c2, c3, c4 = st.columns(4)
        bill_date = c1.date_input("Bill date", value=date.today())
        due_date = c2.date_input("Due date", value=date.today() + timedelta(days=14))
        amount = c3.number_input("Amount (€)", min_value=0.0, step=0.01, format="%.2f")
        status = c4.selectbox("Status", ["Due", "Paid", "Planned"])
        notes = st.text_input("Notes")
        submitted = st.form_submit_button("Save bill")
        if submitted:
            add_bill(bill_date, due_date, amount, status, notes)
            st.success("Bill saved")

    bills = st.session_state.bills.copy()
    st.dataframe(bills, use_container_width=True, hide_index=True)

    if not bills.empty:
        chart_df = bills.copy()
        chart_df["bill_date"] = pd.to_datetime(chart_df["bill_date"], errors="coerce")
        chart_df = chart_df.dropna(subset=["bill_date"]).sort_values("bill_date")
        st.markdown("### Bill amounts")
        st.bar_chart(chart_df.set_index("bill_date")["amount"])


def readings_tab():
    st.subheader("Meter readings")

    with st.expander("Add manual reading"):
        with st.form("add_manual_reading"):
            c1, c2 = st.columns(2)
            reading_date = c1.date_input("Reading date", value=date.today(), key="manual_reading_date")
            reading_time = c2.time_input("Reading time", value=datetime.now().time().replace(second=0, microsecond=0), key="manual_reading_time")
            c3, c4, c5 = st.columns([1, 1, 1.2])
            meter_type = c3.selectbox("Meter type", ["Electricity", "Gas", "LPG", "Other"])
            reading_value = c4.number_input("Reading value", min_value=0.0, step=0.001, format="%.3f")
            source = c5.text_input("Source", value="Manual")
            submitted = st.form_submit_button("Save reading")
            if submitted:
                reading_at = datetime.combine(reading_date, reading_time).strftime("%Y-%m-%d %H:%M")
                add_reading(reading_at, meter_type, reading_value, source)
                st.success("Reading saved")

    readings = st.session_state.meter_readings.copy()
    st.dataframe(readings, use_container_width=True, hide_index=True)

    if not readings.empty:
        chart_df = readings.copy()
        chart_df["reading_at"] = pd.to_datetime(chart_df["reading_at"], errors="coerce")
        chart_df = chart_df.dropna(subset=["reading_at"]).sort_values("reading_at")
        st.markdown("### Imported and manual reading trend")
        st.line_chart(chart_df.set_index("reading_at")["reading_value"])


def reminders_tab():
    st.subheader("Reminders")

    with st.form("add_reminder"):
        c1, c2, c3 = st.columns(3)
        title = c1.text_input("Task")
        due_date = c2.date_input("Due date", value=date.today() + timedelta(days=7))
        status = c3.selectbox("Status", ["Open", "Done"])
        notes = st.text_input("Notes")
        submitted = st.form_submit_button("Save reminder")
        if submitted and title.strip():
            add_reminder(title, due_date, status, notes)
            st.success("Reminder saved")

    reminders = st.session_state.reminders.copy()
    st.dataframe(reminders, use_container_width=True, hide_index=True)


def contacts_tab():
    st.subheader("Contacts")
    st.markdown(
        """
- Natural Gas & Electricity: 041 214 9500
- LPG Support: 041 214 9600
- Customer support email: customersupport@flogas.ie
- LPG support email: lpgsupport@flogas.ie
        """
    )
    st.info("This is a personal companion dashboard and does not log into the official Flogas portal.")


def sidebar_imports():
    st.sidebar.markdown("### Import interval CSV")
    upload = st.sidebar.file_uploader("Upload Flogas / smart meter CSV", type=["csv", "txt"])
    if upload is None:
        return

    parsed = parse_interval_csv(upload)
    if parsed.empty:
        st.sidebar.warning("Could not parse interval readings from that file.")
        st.sidebar.caption("Expected format includes MPRN, meter serial, kW value, and date/time.")
        return

    st.sidebar.success(f"Parsed {len(parsed):,} rows")
    st.sidebar.caption("Estimated kWh uses 30-minute intervals: kW × 0.5")

    with st.expander("Imported preview", expanded=False):
        st.dataframe(parsed.tail(100), use_container_width=True, hide_index=True)
        daily = parsed.groupby("date_only", as_index=False)["estimated_kwh"].sum()
        st.markdown("### Estimated daily usage")
        st.line_chart(daily.set_index("date_only")["estimated_kwh"])

    if st.sidebar.button("Import parsed readings", use_container_width=True):
        rows = pd.DataFrame([
            {
                "reading_at": r.reading_at.strftime("%Y-%m-%d %H:%M"),
                "meter_type": "Electricity",
                "reading_value": float(r.read_value_kw),
                "source": "CSV import",
            }
            for r in parsed.itertuples(index=False)
        ])
        st.session_state.meter_readings = pd.concat([rows, st.session_state.meter_readings], ignore_index=True)
        st.sidebar.success(f"Imported {len(rows):,} readings")


def main():
    st.title("My Flogas Dashboard")
    st.caption("Bills, readings, reminders, and interval usage imports")

    with st.sidebar:
        st.header("Navigation")
        page = st.radio(
            "Go to",
            ["Overview", "Readings", "Bills", "Reminders", "Contacts"],
            index=0,
        )
        st.markdown("---")
        st.caption("Personal dashboard")
        st.caption("Session-based storage")

    sidebar_imports()

    if page == "Overview":
        overview_tab()
    elif page == "Readings":
        readings_tab()
    elif page == "Bills":
        bills_tab()
    elif page == "Reminders":
        reminders_tab()
    elif page == "Contacts":
        contacts_tab()


if __name__ == "__main__":
    main()
