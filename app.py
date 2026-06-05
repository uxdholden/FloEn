import csv
import io
import os
from datetime import datetime

import pandas as pd
import streamlit as st


def get_secret(name: str, default: str | None = None):
    if name in st.secrets:
        return st.secrets[name]
    return os.getenv(name, default)


def parse_uploaded_csv(uploaded_file) -> list[dict]:
    raw = uploaded_file.getvalue()
    text = raw.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def aggregate_daily(records):
    date_candidates = [
        "Read Date and End Time",
        "Read Date",
        "Date",
        "Interval End",
        "Meter Read Date",
        "Timestamp",
    ]
    value_candidates = [
        "Read Value",
        "Consumption",
        "kWh",
        "Active Import Interval (kW)",
        "Interval Read",
        "Usage",
    ]

    def parse_date(value: str):
        if not value:
            return None
        for fmt in (
            "%d-%m-%Y %H:%M",
            "%d/%m/%Y %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%d-%m-%Y",
            "%d/%m/%Y",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(str(value).strip(), fmt).date()
            except Exception:
                pass
        return None

    def parse_float(value: str):
        if value is None:
            return None
        try:
            return float(str(value).strip().replace(",", ""))
        except Exception:
            return None

    date_key = next((k for k in date_candidates if records and k in records[0]), None)
    value_key = next((k for k in value_candidates if records and k in records[0]), None)

    if not date_key or not value_key:
        return [], date_key, value_key

    daily = {}
    for row in records:
        day = parse_date(row.get(date_key, ""))
        val = parse_float(row.get(value_key, ""))
        if day is None or val is None:
            continue
        daily.setdefault(str(day), 0.0)
        daily[str(day)] += val

    rows = []
    for day in sorted(daily):
        kwh = round(daily[day], 4)
        rows.append(
            {
                "date": day,
                "kwh": kwh,
                "avg_kwh_per_hour": round(kwh / 24.0, 4),
            }
        )
    return rows, date_key, value_key


def add_costs(daily_rows, unit_rate, standing_charge):
    if not daily_rows:
        return []

    avg_daily = sum(r["kwh"] for r in daily_rows) / len(daily_rows)
    projected_month_cost = (avg_daily * unit_rate + standing_charge) * 30

    out = []
    for row in daily_rows:
        daily_cost = row["kwh"] * unit_rate + standing_charge
        out.append(
            {
                **row,
                "unit_rate": round(unit_rate, 6),
                "standing_charge": round(standing_charge, 6),
                "daily_cost": round(daily_cost, 4),
                "projected_month_cost": round(projected_month_cost, 2),
            }
        )
    return out


def records_to_df(rows):
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.sort_values("date")
    return df


st.set_page_config(page_title="FloEn", layout="wide")
st.title("FloEn")
st.caption("Personal energy cost tracker using manual ESB CSV upload")

required = ["APP_PASSWORD", "FLOGAS_UNIT_RATE", "FLOGAS_STANDING_CHARGE_DAILY"]
missing = [k for k in required if get_secret(k) in (None, "")]

if missing:
    st.error("Missing required secrets: " + ", ".join(missing))
    st.info("Add them in Streamlit Secrets before using the app.")
    st.stop()

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if "df" not in st.session_state:
    st.session_state.df = pd.DataFrame()

if "raw_records" not in st.session_state:
    st.session_state.raw_records = []

if "source_name" not in st.session_state:
    st.session_state.source_name = None

app_password = get_secret("APP_PASSWORD")

if not st.session_state.authenticated:
    st.subheader("App Login")
    entered_password = st.text_input("Enter app password", type="password")
    login_clicked = st.button("Unlock app", type="primary")

    if login_clicked:
        if entered_password == app_password:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password.")

    st.stop()

unit_rate = float(get_secret("FLOGAS_UNIT_RATE", "0.0809"))
standing_charge = float(get_secret("FLOGAS_STANDING_CHARGE_DAILY", "0.4142"))

with st.sidebar:
    st.header("Upload")
    uploaded_file = st.file_uploader("Upload ESB CSV file", type=["csv"])
    st.caption("Download your usage file from ESB manually, then upload it here.")
    st.divider()
    st.header("Rates")
    st.write(f"Unit rate: €{unit_rate:.4f} per kWh")
    st.write(f"Standing charge: €{standing_charge:.4f} per day")

if uploaded_file is not None:
    try:
        records = parse_uploaded_csv(uploaded_file)
        daily_rows, detected_date_key, detected_value_key = aggregate_daily(records)

        if not daily_rows:
            st.error("Could not detect usable date/value columns in the uploaded CSV.")
            if records:
                st.write("Detected columns:")
                st.write(list(records[0].keys()))
            st.stop()

        cost_rows = add_costs(daily_rows, unit_rate, standing_charge)
        st.session_state.df = records_to_df(cost_rows)
        st.session_state.raw_records = records
        st.session_state.source_name = uploaded_file.name
        st.session_state.detected_date_key = detected_date_key
        st.session_state.detected_value_key = detected_value_key
        st.success(f"Loaded file: {uploaded_file.name}")
    except Exception as e:
        st.error(f"Upload failed: {e}")
        st.stop()

if st.session_state.df.empty:
    st.info("Unlock the app, then upload your ESB CSV file to view usage and costs.")
    st.stop()

df = st.session_state.df
latest = df.iloc[-1]
avg_daily_cost = df["daily_cost"].mean()
avg_daily_kwh = df["kwh"].mean()
projected_month = latest["projected_month_cost"]
avg_hourly = avg_daily_kwh / 24 if avg_daily_kwh else 0

st.write(f"Source file: {st.session_state.source_name}")
st.write(
    f"Detected date column: `{st.session_state.detected_date_key}` | "
    f"Detected value column: `{st.session_state.detected_value_key}`"
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Latest Daily Cost", f"€{latest['daily_cost']:.2f}")
c2.metric("Projected Month", f"€{projected_month:.2f}")
c3.metric("Average Daily Usage", f"{avg_daily_kwh:.2f} kWh")
c4.metric("Average Hourly Usage", f"{avg_hourly:.2f} kWh")

tab1, tab2, tab3 = st.tabs(["Costs", "Usage", "Data"])

with tab1:
    st.subheader("Daily Cost Trend")
    st.line_chart(df.set_index("date")["daily_cost"])
    st.dataframe(
        df[["date", "kwh", "daily_cost", "projected_month_cost"]],
        use_container_width=True,
    )

with tab2:
    st.subheader("Usage Trend")
    st.line_chart(df.set_index("date")["kwh"])
    st.bar_chart(df.set_index("date")["avg_kwh_per_hour"])

with tab3:
    st.subheader("Processed Data")
    st.dataframe(df, use_container_width=True)

    if st.session_state.raw_records:
        raw_df = pd.DataFrame(st.session_state.raw_records)
        st.subheader("Raw Uploaded Rows")
        st.dataframe(raw_df, use_container_width=True)
