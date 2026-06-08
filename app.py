import io
import re
import calendar
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta

st.set_page_config(page_title="Energy Analytics Pro", layout="wide")

# --- CSS Styling ---
st.markdown("""
<style>
    .metric-card { background: #f8f9fa; padding: 15px; border-radius: 10px; border: 1px solid #dee2e6; text-align: center; }
    .metric-title { font-size: 0.8rem; color: #666; text-transform: uppercase; }
    .metric-value { font-size: 1.5rem; font-weight: bold; }
    .delta-pos { color: #d32f2f; font-size: 0.9rem; }
    .delta-neg { color: #388e3c; font-size: 0.9rem; }
</style>
""", unsafe_allow_html=True)

# --- Logic: Data Processing ---
@st.cache_data
def process_data(df):
    df["reading_at"] = pd.to_datetime(df["reading_at"])
    df["date"] = df["reading_at"].dt.date
    df["hour"] = df["reading_at"].dt.hour
    df["is_weekend"] = df["reading_at"].dt.dayofweek >= 5
    
    # Virtual Time-of-Use Bands
    df["band"] = np.select(
        [(df["hour"] >= 23) | (df["hour"] < 8), (df["hour"] >= 17) & (df["hour"] < 19)],
        ["Night", "Peak"], default="Day"
    )
    return df

@st.cache_data
def get_projections(df, rates):
    daily = df.groupby(["date", "is_weekend"])["estimated_kwh"].sum().reset_index()
    
    # Calculate Day-Type Weighted Averages
    wk_avg = daily[~daily["is_weekend"]]["estimated_kwh"].mean()
    we_avg = daily[daily["is_weekend"]]["estimated_kwh"].mean()
    
    # Project remaining days
    last_date = daily["date"].max()
    next_month_days = pd.date_range(start=last_date + timedelta(days=1), end=last_date + timedelta(days=30))
    projections = []
    for d in next_month_days:
        val = we_avg if d.dayofweek >= 5 else wk_avg
        projections.append(val)
    
    return sum(projections) * rates.get("flat_rate", 0.28)

# --- UI Sidebar ---
with st.sidebar:
    st.header("Tariff Settings")
    flat_rate = st.number_input("Flat Raate (Cent/kWh)", value=28.0) / 100
    rates = {"flat_rate": flat_rate}

# --- Main App ---
# (Assuming df_filtered is prepared from uploaded CSV)
# For demo purposes, we proceed if data is available
if 'df' not in st.session_state:
    st.info("Please upload your CSV to see the Comparative Analysis.")
else:
    df = st.session_state.df
    
    # Comparative Analytics (WoW, DoD, MoM)
    today = df["date"].max()
    curr_day = df[df["date"] == today]["estimated_kwh"].sum()
    prev_day = df[df["date"] == (today - timedelta(days=1))]["estimated_kwh"].sum()
    
    c1, c2, c3 = st.columns(3)
    c1.markdown(f'<div class="metric-card"><div class="metric-title">Day-on-Day</div><div class="metric-value">{curr_day:.1f} kWh</div><div class="delta-pos">{(curr_day-prev_day)/prev_day*100:+.1f}%</div></div>', unsafe_allow_html=True)
    
    # Virtual Breakdown Chart
    st.subheader("Virtual Tariff Usage Breakdown")
    band_usage = df.groupby("band")["estimated_kwh"].sum().reset_index()
    fig = px.pie(band_usage, values="estimated_kwh", names="band", hole=0.5, color="band", color_discrete_map={"Night": "#1a237e", "Day": "#ffc107", "Peak": "#d32f2f"})
    st.plotly_chart(fig, use_container_width=True)
    
    # Advanced Projection
    st.subheader("Weighted Monthly Forecast")
    proj_cost = get_projections(df, rates)
    st.info(f"Based on your specific weekday/weekend consumption patterns, your projected cost for the next 30 days is **€{proj_cost:.2f}**.")
