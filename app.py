import io
import re
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="My Flogas Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
)


def init_state():
    if "bills" not in st.session_state:
        today = date.today()
        first_of_month = today.replace(day=1)
        prev_month_anchor = (first_of_month - timedelta(days=1)).replace(day=1)
        st.session_state.bills = pd.DataFrame(
            [
                {
                    "bill_date": first_of_month,
                    "due_date": today + timedelta(days=12),
                    "amount": 124.80,
                    "status": "Due",
                    "notes": "Sample current bill",
                },
                {
                    "bill_date": prev_month_anchor,
                    "due_date": prev_month_anchor + timedelta(days=14),
                    "amount": 98.40,
                    "status": "Paid",
                    "notes": "Sample previous bill",
                },
            ]
        )

    if "meter_readings" not in st.session_state:
        st.session_state.meter_readings = pd.DataFrame(
            columns=[
                "mprn",
                "meter_serial",
                "reading_at",
                "reading_value",
                "estimated_kwh",
                "meter_type",
                "source",
                "date_only",
            ]
        )

    if "reminders" not in st.session_state:
        st.session_state.reminders = pd.DataFrame(
            [
                {
                    "title": "Submit meter reading",
                    "due_date": date.today() + timedelta(days=7),
                    "status": "Open",
                    "notes": "Avoid estimated bill",
                },
                {
                    "title": "Review latest bill",
                    "due_date": date.today() + timedelta(days=3),
                    "status": "Open",
                    "notes": "Check cost and projected trend",
                },
            ]
        )


def parse_interval_csv(uploaded_file):
    raw = uploaded_file.read().decode("utf-8", errors="ignore")
    uploaded_file.seek(0)

    def finalize(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame()

        if "read_value_kw" not in df.columns and "read_value" in df.columns:
            df["read_value_kw"] = pd.to_numeric(df["read_value"], errors="coerce")

        df["reading_at"] = pd.to_datetime(df["reading_at"], errors="coerce")
        df = df.dropna(subset=["reading_at", "read_value_kw"]).copy()
        if df.empty:
            return pd.DataFrame()

        df = df.sort_values("reading_at")
        df["interval_hours"] = 0.5
        read_type_series = df.get("read_type", "").astype(str)
        is_kw = read_type_series.str.contains(r"\(kW\)|\skW$", regex=True, na=False)
        df["estimated_kwh"] = df["read_value_kw"].where(~is_kw, df["read_value_kw"] * df["interval_hours"])
        df["date_only"] = df["reading_at"].dt.date

        for col in ["mprn", "meter_serial"]:
            if col not in df.columns:
                df[col] = ""

        df = df.rename(columns={"read_value_kw": "reading_value"})
        df["meter_type"] = "interval"
        df["source"] = "upload"
        return df[
            [
                "mprn",
                "meter_serial",
                "reading_at",
                "reading_value",
                "estimated_kwh",
                "meter_type",
                "source",
                "date_only",
            ]
        ]

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    headerless = False
    if lines:
        first_parts = [part.strip() for part in lines[0].split(",")]
        headerless = len(first_parts) >= 5 and re.fullmatch(r"\d{11}", first_parts[0] or "") is not None

    try:
        if headerless:
            csv_df = pd.read_csv(
                io.StringIO(raw),
                header=None,
                names=["mprn", "meter serial number", "read value", "read type", "read date and end time"],
            )
        else:
            csv_df = pd.read_csv(io.StringIO(raw))

        normalized = {str(col).strip().lower(): col for col in csv_df.columns}
        required = ["mprn", "meter serial number", "read value", "read type", "read date and end time"]
        if all(col in normalized for col in required):
            df = pd.DataFrame(
                {
                    "mprn": csv_df[normalized["mprn"]].astype(str).str.strip(),
                    "meter_serial": csv_df[normalized["meter serial number"]].astype(str).str.strip(),
                    "read_value_kw": pd.to_numeric(csv_df[normalized["read value"]], errors="coerce"),
                    "read_type": csv_df[normalized["read type"]].astype(str).str.strip(),
                    "reading_at": pd.to_datetime(
                        csv_df[normalized["read date and end time"]],
                        format="%d-%m-%Y %H:%M",
                        errors="coerce",
                    ),
                }
            )
            df = df[
                df["read_type"].str.contains(
                    r"Active Import Interval(?:\s*\((?:kW|kWh)\)|\s+kW|\s+kWh)",
                    regex=True,
                    na=False,
                )
            ]
            parsed = finalize(df)
            if not parsed.empty:
                return parsed
    except Exception:
        pass

    pattern = re.compile(
        r"(?P<mprn>\d{11})\s*"
        r"(?P<serial>\d+)\s*"
        r"(?P<value>\d+\.\d{3,4})\s*"
        r"(?P<read_type>Active Import Interval(?:\s*\((?:kW|kWh)\)|\s+kW|\s+kWh))\s*"
        r"(?P<date>\d{2}-\d{2}-\d{4})\s+"
        r"(?P<time>\d{2}:?\d{2}|\d{4})"
    )

    rows = []
    for match in pattern.finditer(raw):
        time_text = match.group("time").replace(":", "")
        rows.append(
            {
                "mprn": match.group("mprn"),
                "meter_serial": match.group("serial"),
                "read_value_kw": float(match.group("value")),
                "read_type": match.group("read_type"),
                "reading_at": pd.to_datetime(
                    f"{match.group('date')} {time_text}",
                    format="%d-%m-%Y %H%M",
                    errors="coerce",
                ),
            }
        )

    if not rows:
        return pd.DataFrame()
    return finalize(pd.DataFrame(rows))


def add_bill(bill_date, due_date, amount, status, notes):
    new_row = pd.DataFrame(
        [{
            "bill_date": bill_date,
            "due_date": due_date,
            "amount": float(amount),
            "status": status,
            "notes": notes,
        }]
    )
    st.session_state.bills = pd.concat([st.session_state.bills, new_row], ignore_index=True)


def add_reminder(title, due_date, status, notes):
    new_row = pd.DataFrame(
        [{
            "title": title,
            "due_date": due_date,
            "status": status,
            "notes": notes,
        }]
    )
    st.session_state.reminders = pd.concat([st.session_state.reminders, new_row], ignore_index=True)


def safe_dates(df, cols):
    out = df.copy()
    for col in cols:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce")
    return out


def render_overview():
    bills = safe_dates(st.session_state.bills, ["bill_date", "due_date"])
    readings = safe_dates(st.session_state.meter_readings, ["reading_at"])
    reminders = safe_dates(st.session_state.reminders, ["due_date"])

    total_bills = float(bills["amount"].sum()) if not bills.empty else 0.0
    latest_bill = float(bills.sort_values("bill_date")["amount"].iloc[-1]) if not bills.empty else 0.0
    total_kwh = float(readings["estimated_kwh"].sum()) if not readings.empty else 0.0
    open_reminders = int((reminders["status"].astype(str).str.lower() == "open").sum()) if not reminders.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Bills total", f"€{total_bills:,.2f}")
    c2.metric("Latest bill", f"€{latest_bill:,.2f}")
    c3.metric("Imported usage", f"{total_kwh:,.1f} kWh")
    c4.metric("Open reminders", open_reminders)

    left, right = st.columns([1.2, 1])

    with left:
        st.subheader("Recent bills")
        display_bills = bills.sort_values("bill_date", ascending=False).copy()
        if not display_bills.empty:
            display_bills["bill_date"] = display_bills["bill_date"].dt.date
            display_bills["due_date"] = display_bills["due_date"].dt.date
            st.dataframe(display_bills, use_container_width=True, hide_index=True)
        else:
            st.info("No bills added yet.")

    with right:
        st.subheader("Usage trend")
        if not readings.empty:
            usage = readings.copy()
            usage["date_only"] = pd.to_datetime(usage["reading_at"]).dt.date
            daily = usage.groupby("date_only", as_index=False)["estimated_kwh"].sum()
            daily = daily.rename(columns={"date_only": "date", "estimated_kwh": "kWh"})
            st.line_chart(daily.set_index("date"))
        else:
            st.info("Upload an interval CSV to see usage.")


def render_bills():
    st.subheader("Bills")

    with st.form("bill_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        bill_date = c1.date_input("Bill date", value=date.today())
        due_date = c2.date_input("Due date", value=date.today() + timedelta(days=14))
        c3, c4 = st.columns(2)
        amount = c3.number_input("Amount (€)", min_value=0.0, step=0.01, value=0.0)
        status = c4.selectbox("Status", ["Due", "Paid", "Overdue"])
        notes = st.text_input("Notes")
        submitted = st.form_submit_button("Add bill")
        if submitted:
            add_bill(bill_date, due_date, amount, status, notes)
            st.success("Bill added.")

    bills = safe_dates(st.session_state.bills, ["bill_date", "due_date"])
    if not bills.empty:
        bills = bills.sort_values("bill_date", ascending=False).copy()
        bills["bill_date"] = bills["bill_date"].dt.date
        bills["due_date"] = bills["due_date"].dt.date
        st.dataframe(bills, use_container_width=True, hide_index=True)
    else:
        st.info("No bills available.")


def render_readings():
    st.subheader("Meter readings")

    uploaded = st.file_uploader("Upload meter CSV", type=["csv"], key="main_upload")
    if uploaded is not None:
        parsed = parse_interval_csv(uploaded)
        if parsed.empty:
            st.error("Could not parse that file.")
        else:
            existing = st.session_state.meter_readings.copy()
            merged = pd.concat([existing, parsed], ignore_index=True)
            merged = merged.drop_duplicates(subset=["reading_at", "reading_value", "meter_serial"], keep="last")
            st.session_state.meter_readings = merged.sort_values("reading_at").reset_index(drop=True)
            st.success(f"Imported {len(parsed)} rows.")

    readings = safe_dates(st.session_state.meter_readings, ["reading_at"])
    if not readings.empty:
        r1, r2 = st.columns([1.2, 1])
        with r1:
            show = readings.sort_values("reading_at", ascending=False).copy()
            show["reading_at"] = show["reading_at"].dt.strftime("%Y-%m-%d %H:%M")
            st.dataframe(show, use_container_width=True, hide_index=True)
        with r2:
            daily = readings.copy()
            daily["date_only"] = pd.to_datetime(daily["reading_at"]).dt.date
            daily = daily.groupby("date_only", as_index=False)["estimated_kwh"].sum()
            daily = daily.rename(columns={"date_only": "date", "estimated_kwh": "kWh"})
            st.bar_chart(daily.set_index("date"))
    else:
        st.info("No meter readings imported yet.")


def render_reminders():
    st.subheader("Reminders")

    with st.form("reminder_form", clear_on_submit=True):
        title = st.text_input("Reminder title")
        due_date = st.date_input("Due date", value=date.today() + timedelta(days=7), key="rem_due")
        status = st.selectbox("Status", ["Open", "Done", "Snoozed"], key="rem_status")
        notes = st.text_input("Notes", key="rem_notes")
        submitted = st.form_submit_button("Add reminder")
        if submitted and title.strip():
            add_reminder(title.strip(), due_date, status, notes)
            st.success("Reminder added.")

    reminders = safe_dates(st.session_state.reminders, ["due_date"])
    if not reminders.empty:
        reminders = reminders.sort_values("due_date", ascending=True).copy()
        reminders["due_date"] = reminders["due_date"].dt.date
        st.dataframe(reminders, use_container_width=True, hide_index=True)
    else:
        st.info("No reminders available.")


def main():
    init_state()

    st.title("My Flogas Dashboard")
    st.caption("A simple bill, usage, and reminder tracker that runs without a database.")

    st.sidebar.header("Navigation")
    page = st.sidebar.radio("Go to", ["Overview", "Bills", "Meter readings", "Reminders"])

    st.sidebar.markdown("---")
    st.sidebar.subheader("Quick upload")
    quick_upload = st.sidebar.file_uploader("Upload interval CSV", type=["csv"], key="sidebar_upload")
    if quick_upload is not None:
        parsed = parse_interval_csv(quick_upload)
        if parsed.empty:
            st.sidebar.error("Could not parse that CSV.")
        else:
            existing = st.session_state.meter_readings.copy()
            merged = pd.concat([existing, parsed], ignore_index=True)
            merged = merged.drop_duplicates(subset=["reading_at", "reading_value", "meter_serial"], keep="last")
            st.session_state.meter_readings = merged.sort_values("reading_at").reset_index(drop=True)
            st.sidebar.success(f"Imported {len(parsed)} rows")

    st.sidebar.markdown("---")
    st.sidebar.write(f"Bills: {len(st.session_state.bills)}")
    st.sidebar.write(f"Readings: {len(st.session_state.meter_readings)}")
    st.sidebar.write(f"Reminders: {len(st.session_state.reminders)}")

    if page == "Overview":
        render_overview()
    elif page == "Bills":
        render_bills()
    elif page == "Meter readings":
        render_readings()
    else:
        render_reminders()


if __name__ == "__main__":
    main()
