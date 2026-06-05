import pandas as pd
import streamlit as st
from pathlib import Path

from config import TariffConfig

st.set_page_config(page_title="FloEn", layout="wide")
st.title("FloEn")
st.caption("Personal energy cost tracker")

cost_file = Path("output/daily_costs.csv")
usage_file = Path("output/daily_usage.csv")

if not cost_file.exists() and not usage_file.exists():
    st.warning("No usage data found yet. Run esb_fetch_secure.py first to generate CSV files.")
    st.stop()

if cost_file.exists():
    df = pd.read_csv(cost_file)
else:
    df = pd.read_csv(usage_file)
    df["unit_rate"] = TariffConfig.get_unit_rate()
    df["standing_charge"] = TariffConfig.get_standing_charge_daily()
    df["daily_cost"] = df["kwh"] * df["unit_rate"] + df["standing_charge"]
    avg_daily_cost = df["daily_cost"].mean() if not df.empty else 0
    df["projected_month_cost"] = avg_daily_cost * 30

if "date" in df.columns:
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values("date")

latest = df.iloc[-1] if not df.empty else None
avg_daily_cost = df["daily_cost"].mean() if "daily_cost" in df.columns and not df.empty else 0
avg_daily_kwh = df["kwh"].mean() if "kwh" in df.columns and not df.empty else 0
projected_month = latest["projected_month_cost"] if latest is not None and "projected_month_cost" in df.columns else avg_daily_cost * 30
avg_hourly = avg_daily_kwh / 24 if avg_daily_kwh else 0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Latest Daily Cost", f"€{latest['daily_cost']:.2f}" if latest is not None else "€0.00")
c2.metric("Projected Month", f"€{projected_month:.2f}")
c3.metric("Average Daily Usage", f"{avg_daily_kwh:.2f} kWh")
c4.metric("Average Hourly Usage", f"{avg_hourly:.2f} kWh")

tab1, tab2, tab3 = st.tabs(["Costs", "Usage", "Data"])

with tab1:
    st.subheader("Daily Cost Trend")
    if "date" in df.columns and "daily_cost" in df.columns:
        st.line_chart(df.set_index("date")["daily_cost"])
    st.dataframe(df[[c for c in ["date", "kwh", "daily_cost", "projected_month_cost"] if c in df.columns]], use_container_width=True)

with tab2:
    st.subheader("Usage Trend")
    if "date" in df.columns and "kwh" in df.columns:
        st.line_chart(df.set_index("date")["kwh"])
    if "avg_kwh_per_hour" in df.columns:
        st.bar_chart(df.set_index("date")["avg_kwh_per_hour"])

with tab3:
    st.subheader("Raw Data")
    st.dataframe(df, use_container_width=True)
