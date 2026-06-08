import io
import re
import calendar
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta

# Set page configuration
st.set_page_config(
    page_title="Smart Meter Cost & Analytics Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Move CSS to a constant to keep code clean
CUSTOM_CSS = """
<style>
    .metric-container {
        background-color: #f8f9fa;
        border-radius: 12px;
        padding: 20px;
        border: 1px solid #e9ecef;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
        text-align: center;
    }
    .metric-value { font-size: 2rem; font-weight: 700; margin-bottom: 5px; }
    .metric-label { font-size: 0.9rem; color: #6c757d; font-weight: 500; }
    .metric-badge {
        display: inline-block; padding: 4px 8px; font-size: 0.75rem;
        font-weight: 700; border-radius: 20px; margin-top: 8px;
    }
    .badge-actual    { background-color: #e3f2fd; color: #0d47a1; }
    .badge-projected { background-color: #efebe9; color: #4e342e; }
    .badge-warning   { background-color: #fff3e0; color: #e65100; }
    .badge-success   { background-color: #e8f5e9; color: #1b5e20; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# PARSING HELPERS (Cached for performance)
# ---------------------------------------------------------------------------

def _kwt_to_kwh(series: pd.Series) -> pd.Series:
    """Converts ESB kWt (kilo-watt-thirties) to billing kWh."""
    return pd.to_numeric(series, errors="coerce") / 2.0

def parse_wide_csv(raw: str) -> pd.DataFrame:
    try:
        df = pd.read_csv(io.StringIO(raw), dtype=str)
        df.columns = [str(c).strip() for c in df.columns]
        col_lower  = {c.lower(): c for c in df.columns}

        mprn_col   = next((col_lower[k] for k in col_lower if "mprn"   in k), None)
        serial_col = next((col_lower[k] for k in col_lower if "serial" in k), None)
        date_col   = next((col_lower[k] for k in col_lower if k == "date"), None)
        time_cols  = [c for c in df.columns if re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", c)]

        if not (mprn_col and date_col and len(time_cols) >= 2):
            return pd.DataFrame()

        id_vars = [c for c in [mprn_col, serial_col, date_col] if c]
        melted  = df.melt(id_vars=id_vars, value_vars=time_cols,
                          var_name="time_slot", value_name="_raw_kwt")

        melted["estimated_kwh"] = _kwt_to_kwh(melted["_raw_kwt"])
        melted = melted.dropna(subset=["estimated_kwh"])

        melted["reading_at"] = pd.to_datetime(
            melted[date_col].astype(str) + " " + melted["time_slot"].str[:5],
            errors="coerce"
        )
        melted = melted.dropna(subset=["reading_at"]).sort_values("reading_at").reset_index(drop=True)

        melted["mprn"]         = melted[mprn_col].astype(str).str.strip()
        melted["meter_serial"] = melted[serial_col].astype(str).str.strip() if serial_col else ""
        melted["read_value_kw"] = melted["estimated_kwh"] * 2.0
        melted["date_only"]     = melted["reading_at"].dt.date

        return melted[["mprn", "meter_serial", "reading_at", "read_value_kw", "estimated_kwh", "date_only"]]
    except Exception as e:
        st.error(f"Error parsing wide CSV: {e}")
        return pd.DataFrame()

@st.cache_data(show_spinner=False)
def parse_interval_csv(raw_bytes: bytes) -> pd.DataFrame:
    raw = raw_bytes.decode("utf-8-sig", errors="ignore")

    # Wide format check
    first_line = raw.splitlines()[0] if raw.splitlines() else ""
    if re.search(r"(?i)\bdate\b", first_line) and re.search(r"\d{2}:\d{2}:\d{2}", raw):
        wide_result = parse_wide_csv(raw)
        if not wide_result.empty: return wide_result

    def finalize(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty: return pd.DataFrame()
        value_col = "read_value" if "read_value" in df.columns else "read_value_kw"
        df = df.dropna(subset=["reading_at", value_col]).copy()
        df["_raw_numeric"] = pd.to_numeric(df[value_col], errors="coerce")
        df["reading_at"]   = pd.to_datetime(df["reading_at"], errors="coerce")
        df = df.dropna(subset=["reading_at", "_raw_numeric"]).sort_values("reading_at")
        
        df["estimated_kwh"] = df["_raw_numeric"] / 2.0
        df["read_value_kw"] = df["estimated_kwh"] * 2.0
        df["date_only"]     = df["reading_at"].dt.date

        for col in ["mprn", "meter_serial"]:
            if col not in df.columns: df[col] = ""
        return df[["mprn", "meter_serial", "reading_at", "read_value_kw", "estimated_kwh", "date_only"]]

    # CSV narrow mode
    try:
        lines = raw.splitlines()
        header_idx = next((i for i, l in enumerate(lines) if "mprn" in l.lower() and any(k in l.lower() for k in ["date", "time", "value"])), -1)

        csv_source = "\n".join(lines[header_idx:]) if header_idx != -1 else raw
        csv_df     = pd.read_csv(io.StringIO(csv_source))
        cols       = {str(col).strip().lower(): col for col in csv_df.columns}

        mprn_col   = next((cols[c] for c in cols if "mprn"   in c), None)
        serial_col = next((cols[c] for c in cols if "serial" in c or "meter" in c), None)
        value_col  = next((cols[c] for c in cols if "value"  in c or "reading" in c), None)
        type_col   = next((cols[c] for c in cols if "type"   in c), None)
        date_col   = next((cols[c] for c in cols if "date"   in c or "time" in c or "at" in c), None)

        if all([mprn_col, value_col, date_col]):
            df = pd.DataFrame({
                "mprn":        csv_df[mprn_col].astype(str).str.strip(),
                "meter_serial": csv_df[serial_col].astype(str).str.strip() if serial_col else "",
                "read_value":  pd.to_numeric(csv_df[value_col], errors="coerce"),
                "reading_at":  pd.to_datetime(csv_df[date_col], errors="coerce"),
            })
            parsed = finalize(df)
            if not parsed.empty: return parsed
    except Exception:
        pass # Fallback to regex

    # Regex fallback
    pattern = re.compile(
        r"(?P<mprn>\d{11})[,\s\t;]+(?P<serial>[A-Za-z0-9_-]+)[,\s\t;]+(?P<value>\d+(?:\.\d+)?)[,\s\t;]+"
        r"(?P<read_type>Active Import Interval.*?)[,\s\t;]+(?P<date>\d{2}[-/]\d{2}[-/]\d{4})[,\s\t ]+(?P<time>\d{2}:?\d{2})"
    )
    
    rows = []
    for m in pattern.finditer(raw):
        date_str, time_text = m.group("date").replace("/", "-"), m.group("time").replace(":", "")[:4]
        reading_at = pd.to_datetime(f"{date_str} {time_text}", format="%d-%m-%Y %H%M", errors="coerce")
        rows.append({"mprn": m.group("mprn"), "meter_serial": m.group("serial"), "read_value": float(m.group("value")), "reading_at": reading_at})

    return finalize(pd.DataFrame(rows)) if rows else pd.DataFrame()


@st.cache_data
def generate_demo_data() -> pd.DataFrame:
    np.random.seed(42)
    end_date   = datetime.now().replace(minute=0, second=0, microsecond=0)
    start_date = end_date - timedelta(days=90)
    date_range = pd.date_range(start=start_date, end=end_date, freq="30min")

    rows = []
    for dt in date_range:
        hour, weekday = dt.hour, dt.weekday()
        base = 0.15 + np.random.normal(0, 0.02)
        
        if 8 <= hour < 17: activity = 0.25 + np.random.normal(0, 0.05)
        elif 17 <= hour < 19: activity = 0.85 + np.random.normal(0, 0.15)
        elif 19 <= hour < 23: activity = 0.45 + np.random.normal(0, 0.08)
        else: activity = 0.05 + np.random.normal(0, 0.01)

        if weekday >= 5: activity *= 1.25
        spike = 1.8 if (hour == 8 or hour == 18) and np.random.rand() > 0.7 else 0.0
        est_kwh = max(0.01, base + activity + spike)

        rows.append({
            "mprn": "10303339574", "meter_serial": "000000000024049722",
            "reading_at": dt, "read_value_kw": est_kwh * 2.0, "estimated_kwh": est_kwh, "date_only": dt.date()
        })
    return pd.DataFrame(rows)

# ---------------------------------------------------------------------------
# VECTORIZED PROCESSORS (Cached & Fast)
# ---------------------------------------------------------------------------

def apply_tariffs(df: pd.DataFrame, rates: dict) -> pd.DataFrame:
    df = df.copy()
    df["hour"] = df["reading_at"].dt.hour
    if rates["type"] == "flat":
        df["tariff_band"] = "24hr Flat"
        df["tariff_rate"] = rates["flat_rate"]
    else:
        conditions = [(df["hour"] >= 23) | (df["hour"] < 8), (df["hour"] >= 17) & (df["hour"] < 19)]
        df["tariff_band"] = np.select(conditions, ["Night", "Peak"], default="Day")
        df["tariff_rate"] = df["tariff_band"].map({"Day": rates["day_rate"], "Night": rates["night_rate"], "Peak": rates["peak_rate"]})
    
    df["cost"] = df["estimated_kwh"] * df["tariff_rate"]
    return df

@st.cache_data
def disaggregate_appliances(df: pd.DataFrame, house_profile: dict) -> pd.DataFrame:
    """Highly optimized vectorized appliance breakdown."""
    df = df.copy()
    
    # Base load
    df["app_always_on"] = np.minimum(df.groupby("date_only")["estimated_kwh"].transform("min"), 0.25)
    df["active_kwh"]    = np.maximum(0.0, df["estimated_kwh"] - df["app_always_on"])
    
    # Create helpers
    hr = df["reading_at"].dt.hour + df["reading_at"].dt.minute / 60.0
    is_weekend = df["reading_at"].dt.dayofweek >= 5
    
    for col in ["app_ev", "app_heating", "app_cooking", "app_laundry", "app_entertainment", "app_misc"]:
        df[col] = 0.0

    # EV (Vectorized)
    if house_profile["has_ev"]:
        ev_mask = (hr >= 0.0) & (hr < 6.0) & (df["active_kwh"] > 1.5)
        df["app_ev"] = np.where(ev_mask, np.minimum(df["active_kwh"], 3.7), 0.0)
        df["active_kwh"] -= df["app_ev"]

    # Laundry / Sustained Heavy (Vectorized Shift)
    active_prev = df["active_kwh"].shift(1, fill_value=0.0)
    active_next = df["active_kwh"].shift(-1, fill_value=0.0)
    is_sustained = (df["active_kwh"] > 0.45) & ((active_prev > 0.40) | (active_next > 0.40))
    laundry_heavy_mask = is_sustained & (hr >= 7.0) & (hr < 23.0) & (df["active_kwh"] > 0)
    df["app_laundry"] += np.where(laundry_heavy_mask, df["active_kwh"] * 0.85, 0.0)
    df["active_kwh"] -= df["app_laundry"]

    # Heating
    heat_ratio = 0.6 if house_profile["electric_heating"] else 0.15
    heat_mask = ((hr >= 5.5) & (hr < 8.5)) | (hr >= 23.0) | (hr < 2.0)
    df["app_heating"] = np.where(heat_mask & (df["active_kwh"] > 0), df["active_kwh"] * heat_ratio, 0.0)
    df["active_kwh"] -= df["app_heating"]

    # Cooking
    cook_mask = ((hr >= 7.0) & (hr < 9.0)) | ((hr >= 12.0) & (hr < 14.0)) | ((hr >= 16.5) & (hr < 19.5))
    cook_ratio = np.where(df["active_kwh"] > 0.15, 0.55, 0.3)
    df["app_cooking"] = np.where(cook_mask & (df["active_kwh"] > 0), df["active_kwh"] * cook_ratio, 0.0)
    df["active_kwh"] -= df["app_cooking"]

    # Chores/Extra Laundry
    chore_mask = ((hr >= 9.0) & (hr < 12.0)) | ((hr >= 14.0) & (hr < 16.5)) | ((hr >= 19.0) & (hr < 22.0)) | (is_weekend & (hr >= 9.0) & (hr < 18.0))
    chore_ratio = np.where(df["active_kwh"] > 0.2, 0.5, 0.25)
    chore_draw = np.where(chore_mask & (df["active_kwh"] > 0), df["active_kwh"] * chore_ratio, 0.0)
    df["app_laundry"] += chore_draw
    df["active_kwh"] -= chore_draw

    # Entertainment
    ent_mask = (hr >= 18.0) & (hr < 23.5)
    df["app_entertainment"] = np.where(ent_mask & (df["active_kwh"] > 0), df["active_kwh"] * 0.7, 0.0)
    df["active_kwh"] -= df["app_entertainment"]

    # Misc
    df["app_misc"] = np.maximum(0.0, df["active_kwh"])
    
    return df

# ---------------------------------------------------------------------------
# STREAMLIT UI
# ---------------------------------------------------------------------------

st.title("⚡ Smart Meter Analytics & Cost Dashboard")
st.markdown("Analyze your electricity usage, forecast bills, and breakdown appliance consumption patterns.")

# ── Sidebar ──
with st.sidebar:
    st.header("📁 Data Source")
    data_option  = st.radio("Choose Data Input:", ["Upload My Own File", "Use Sample Demo Data"])
    uploaded_file = None
    if data_option == "Upload My Own File":
        uploaded_file = st.file_uploader("Upload CSV/TXT File", type=["csv", "txt"])

    st.header("💰 Tariff Settings")
    tariff_style = st.selectbox("Select Tariff Type:", ["24-Hour Flat Tariff", "Smart (Day/Night/Peak) Tariff"])
    
    rates = {}
    if tariff_style == "24-Hour Flat Tariff":
        rates["type"] = "flat"
        rates["flat_rate"] = st.number_input("Flat Rate (Cent/kWh)", value=26.41) / 100.0
    else:
        rates["type"] = "smart"
        rates["day_rate"] = st.number_input("Day Rate (Cent/kWh)", value=28.20) / 100.0
        rates["night_rate"] = st.number_input("Night Rate (Cent/kWh)", value=15.10) / 100.0
        rates["peak_rate"] = st.number_input("Peak Rate (Cent/kWh)", value=35.40) / 100.0

    with st.expander("Fixed Charges & Taxes", expanded=False):
        annual_standing = st.number_input("Annual Standing Charge (€)", value=270.45)
        annual_pso      = st.number_input("Annual PSO Levy (€)", value=19.10)
        rates["vat_rate"] = st.number_input("VAT Rate (%)", value=9.0) / 100.0
        rates["daily_standing_charge"] = (annual_standing + annual_pso) / 365.25

    st.header("🔌 Household Profile")
    house_profile = {
        "has_ev": st.checkbox("Own an Electric Vehicle (EV)?", value=False),
        "electric_heating": st.checkbox("Use electric space/water heating?", value=True)
    }

# ── Load Data ──
df_raw = None
if data_option == "Upload My Own File" and uploaded_file is not None:
    with st.spinner("Processing smart meter file..."):
        file_bytes = uploaded_file.read()
        df_raw = parse_interval_csv(file_bytes)
        if df_raw.empty: st.error("Failed to parse file. Verify it's a standard HDF or ESB export.")
elif data_option == "Use Sample Demo Data":
    df_raw = generate_demo_data()

# ---------------------------------------------------------------------------
# DASHBOARD RENDERING
# ---------------------------------------------------------------------------

if df_raw is not None and not df_raw.empty:
    # 1. Apply Tariffs (Dynamic, not cached because rates change)
    df = apply_tariffs(df_raw, rates)
    
    # 2. Enrich Time
    df["reading_at"]  = pd.to_datetime(df["reading_at"])
    df["year_month"]  = df["reading_at"].dt.strftime("%Y-%m")
    df["day_name"]    = df["reading_at"].dt.day_name()
    df["hour_of_day"] = df["reading_at"].dt.hour

    # 3. Disaggregate Appliances (Cached execution)
    df = disaggregate_appliances(df, house_profile)

    # Filter Setup
    st.sidebar.markdown("---")
    st.sidebar.header("📅 View Filter")
    months = sorted(df["year_month"].unique().tolist())
    selected_month = st.sidebar.selectbox("Select Period:", ["All Months"] + months)
    
    df_filtered = df[df["year_month"] == selected_month].copy() if selected_month != "All Months" else df.copy()

    # Metrics Calculations
    days_elapsed = df_filtered["date_only"].nunique()
    days_in_month = 30
    is_unfinished, proj_factor = False, 1.0

    if selected_month != "All Months":
        y_val, m_val = map(int, selected_month.split("-"))
        days_in_month = calendar.monthrange(y_val, m_val)[1]
        if days_elapsed < days_in_month:
            is_unfinished = True
            proj_factor = (days_in_month / days_elapsed) if days_elapsed >= 7 else 1.0

    actual_kwh = df_filtered["estimated_kwh"].sum()
    actual_gross_cost = (df_filtered["cost"].sum() + (days_elapsed * rates["daily_standing_charge"])) * (1 + rates["vat_rate"])
    proj_kwh = actual_kwh * proj_factor
    proj_cost = (df_filtered["cost"].sum() * proj_factor + (days_in_month * rates["daily_standing_charge"])) * (1 + rates["vat_rate"])
    max_kw = df_filtered["read_value_kw"].max()

    if is_unfinished:
        st.warning(f"⚠️ **{selected_month} is a Partial Month** ({days_elapsed}/{days_in_month} days). Showing projected end-of-month data in brown.")

    # Top KPI Cards
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        color, val, label, sub = ("#4e342e", proj_kwh, "Estimated Month-End", f"Actual: {actual_kwh:,.1f}") if (is_unfinished and proj_factor > 1) else ("#0d47a1", actual_kwh, "Total Consumption", "Completed Period")
        st.markdown(f'<div class="metric-container"><div class="metric-value" style="color:{color};">{val:,.1f} kWh</div><div class="metric-label">{label}</div><div class="metric-badge badge-actual">{sub}</div></div>', unsafe_allow_html=True)
    with col2:
        color, val, label, sub = ("#4e342e", proj_cost, "Projected Bill", f"Actual: €{actual_gross_cost:,.2f}") if (is_unfinished and proj_factor > 1) else ("#1b5e20", actual_gross_cost, "Total Cost (Inc VAT)", "Completed Period")
        st.markdown(f'<div class="metric-container"><div class="metric-value" style="color:{color};">€{val:,.2f}</div><div class="metric-label">{label}</div><div class="metric-badge badge-actual">{sub}</div></div>', unsafe_allow_html=True)
    with col3:
        st.markdown(f'<div class="metric-container"><div class="metric-value" style="color:#e65100;">€{(actual_gross_cost/max(days_elapsed,1)):.2f}/day</div><div class="metric-label">Avg Daily Cost</div><div class="metric-badge badge-warning">{actual_kwh/max(days_elapsed,1):.1f} kWh/day</div></div>', unsafe_allow_html=True)
    with col4:
        st.markdown(f'<div class="metric-container"><div class="metric-value" style="color:#37474f;">{max_kw:.1f} kW</div><div class="metric-label">Peak Power Demand</div><div class="metric-badge badge-actual" style="background-color:#eceff1;color:#37474f;">Max draw recorded</div></div>', unsafe_allow_html=True)

    st.markdown("---")

    # Tabs
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "📈 Month-to-Month", "📊 Projections", "⏰ Hourly Peaks", 
        "🔌 Appliance Breakdown", "🎯 Simulator", "🔍 Heatmap"
    ])

    with tab1:
        st.subheader("Monthly Trends")
        monthly = df.groupby("year_month").agg(total_kwh=("estimated_kwh", "sum"), cost=("cost", "sum"), days=("date_only", "nunique")).reset_index()
        monthly["total_cost"] = (monthly["cost"] + (monthly["days"] * rates["daily_standing_charge"])) * (1 + rates["vat_rate"])
        
        c1, c2 = st.columns(2)
        with c1:
            fig1 = px.line(monthly, x="year_month", y="total_kwh", text=monthly["total_kwh"].round(0), markers=True, title="Consumption (kWh)")
            st.plotly_chart(fig1, use_container_width=True)
        with c2:
            fig2 = px.bar(monthly, x="year_month", y="total_cost", text=monthly["total_cost"].round(0), title="Cost (€)", color_discrete_sequence=["#2e7d32"])
            st.plotly_chart(fig2, use_container_width=True)

    with tab2:
        st.subheader("Time Series Breakdown")
        daily = df_filtered.groupby("date_only")["estimated_kwh"].sum().reset_index()
        fig = px.bar(daily, x="date_only", y="estimated_kwh", title="Daily Consumption (kWh)", color_discrete_sequence=["#1e88e5"])
        st.plotly_chart(fig, use_container_width=True)

    with tab3:
        st.subheader("Average Hourly Profile")
        hourly = df_filtered.groupby("hour_of_day")["estimated_kwh"].mean().reset_index()
        fig = px.area(hourly, x="hour_of_day", y="estimated_kwh", title="Average Usage by Hour (kWh)", color_discrete_sequence=["#ff9800"])
        fig.update_layout(xaxis=dict(tickmode='linear', tick0=0, dtick=1))
        st.plotly_chart(fig, use_container_width=True)

    with tab4:
        st.subheader("Estimated Appliance Usage")
        app_cols = ["app_always_on", "app_ev", "app_heating", "app_cooking", "app_laundry", "app_entertainment", "app_misc"]
        totals = df_filtered[app_cols].sum().reset_index()
        totals.columns = ["Appliance", "kWh"]
        totals["Appliance"] = totals["Appliance"].str.replace("app_", "").str.replace("_", " ").str.title()
        
        c1, c2 = st.columns([1, 2])
        with c1:
            fig = px.pie(totals, values="kWh", names="Appliance", hole=0.4, title="Total Breakdown")
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            app_daily = df_filtered.groupby("date_only")[app_cols].sum().reset_index()
            app_daily.columns = ["Date"] + [c.replace("app_", "").title() for c in app_cols]
            fig2 = px.bar(app_daily, x="Date", y=app_daily.columns[1:], title="Daily Breakdown", barmode="stack")
            st.plotly_chart(fig2, use_container_width=True)

    with tab5:
        st.subheader("Tariff Comparison Simulator")
        st.info("Compare your current selected tariff against a standard Flat 28c rate.")
        flat_sim_cost = (df_filtered["estimated_kwh"].sum() * 0.28 + (days_elapsed * rates["daily_standing_charge"])) * 1.09
        diff = actual_gross_cost - flat_sim_cost
        
        c1, c2 = st.columns(2)
        c1.metric("Your Selected Tariff Cost", f"€{actual_gross_cost:.2f}")
        c2.metric("Standard 28c Flat Rate", f"€{flat_sim_cost:.2f}", delta=f"€{diff:.2f} difference", delta_color="inverse")

    with tab6:
        st.subheader("Usage Heatmap")
        pivot = df_filtered.pivot_table(index="day_name", columns="hour_of_day", values="estimated_kwh", aggfunc="mean").reindex(
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        )
        fig = px.imshow(pivot, labels=dict(x="Hour of Day", y="Day of Week", color="Avg kWh"), title="Average kWh by Hour and Day", color_continuous_scale="Viridis")
        st.plotly_chart(fig, use_container_width=True)

else:
    st.info("👈 Upload your Smart Meter Data or select the Demo Data option to get started.")
