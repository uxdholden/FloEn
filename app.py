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

# Move CSS to a constant to keep code clean and beautifully styled
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
    .metric-value { font-size: 1.8rem; font-weight: 700; margin-bottom: 5px; }
    .metric-label { font-size: 0.85rem; color: #6c757d; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; }
    .metric-badge {
        display: inline-block; padding: 4px 10px; font-size: 0.75rem;
        font-weight: 700; border-radius: 20px; margin-top: 8px;
    }
    .badge-actual    { background-color: #e3f2fd; color: #0d47a1; }
    .badge-projected { background-color: #efebe9; color: #4e342e; }
    .badge-warning   { background-color: #fff3e0; color: #e65100; }
    .badge-success   { background-color: #e8f5e9; color: #1b5e20; }
    
    /* Custom Styling for Appliance Detective cards */
    .appliance-card {
        background: #ffffff;
        border-left: 5px solid #1e88e5;
        padding: 15px;
        border-radius: 8px;
        margin-bottom: 15px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.02);
    }
    .appliance-title { font-weight: 700; font-size: 1.1rem; color: #2c3e50; }
    .appliance-stats { font-size: 0.9rem; color: #7f8c8d; margin-top: 5px; }
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
    # Generate exactly 90 days ending today, starting on day 12 of the first month to simulate a "late start" month
    end_date   = datetime.now().replace(minute=0, second=0, microsecond=0)
    start_date = end_date - timedelta(days=90)
    
    # Adjust start date to middle of month to guarantee a realistic late-start test case
    if start_date.day < 10:
        start_date = start_date.replace(day=15)
        
    date_range = pd.date_range(start=start_date, end=end_date, freq="30min")

    rows = []
    for dt in date_range:
        hour, weekday = dt.hour, dt.weekday()
        base = 0.12 + np.random.normal(0, 0.015)
        
        # High wattage signatures added directly to base patterns
        if 8 <= hour < 17: activity = 0.22 + np.random.normal(0, 0.04)
        elif 17 <= hour < 19: activity = 0.80 + np.random.normal(0, 0.12)
        elif 19 <= hour < 23: activity = 0.40 + np.random.normal(0, 0.06)
        else: activity = 0.04 + np.random.normal(0, 0.01)

        if weekday >= 5: activity *= 1.25
        
        # Micro kettle peaks (2.5 kW for 5 mins -> fits into a single 30min interval as ~0.4 kWh spike)
        kettle_spike = 0.5 if (hour in [7, 10, 13, 16, 20]) and np.random.rand() > 0.6 else 0.0
        
        # Shower events (9 kW for 10 mins -> fits into a 30min interval as ~1.5 kWh spike)
        shower_spike = 1.6 if (hour == 8 or hour == 19) and np.random.rand() > 0.8 else 0.0

        est_kwh = max(0.01, base + activity + kettle_spike + shower_spike)

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
    """Highly optimized vectorized appliance breakdown & special signature tagging."""
    df = df.copy()
    
    # Base load (Always on)
    df["app_always_on"] = np.minimum(df.groupby("date_only")["estimated_kwh"].transform("min"), 0.25)
    df["active_kwh"]    = np.maximum(0.0, df["estimated_kwh"] - df["app_always_on"])
    
    # Create helpers
    hr = df["reading_at"].dt.hour + df["reading_at"].dt.minute / 60.0
    is_weekend = df["reading_at"].dt.dayofweek >= 5
    
    for col in ["app_ev", "app_heating", "app_cooking", "app_laundry", "app_entertainment", "app_misc"]:
        df[col] = 0.0

    # 1. SPECIAL DETECTION: Instant High-Power events (Power Showers or Electric Hobs / Kettles)
    # Shower Signature Heuristic: Instantaneous rate > 6.5 kW (estimated_kwh > 1.625 kWh in a single 30-min window)
    df["sig_shower"] = df["estimated_kwh"] >= 1.5
    
    # Kettle Signature Heuristic: Sudden isolated narrow spike (+0.45 kWh relative to both immediate neighbors)
    active_prev_raw = df["estimated_kwh"].shift(1, fill_value=0.1)
    active_next_raw = df["estimated_kwh"].shift(-1, fill_value=0.1)
    df["sig_kettle"] = (df["estimated_kwh"] - active_prev_raw > 0.4) & (df["estimated_kwh"] - active_next_raw > 0.4) & (~df["sig_shower"])

    # 2. Standard Continuous Profile Disaggregation
    # EV (Vectorized)
    if house_profile["has_ev"]:
        ev_mask = (hr >= 0.0) & (hr < 6.0) & (df["active_kwh"] > 1.2)
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
# DYNAMIC BOUNDARY PROJECTION ENGINE
# ---------------------------------------------------------------------------

def calculate_smart_projection(df_all: pd.DataFrame, target_month: str, rates: dict):
    """
    Computes weighted projections for a specific target month.
    - If target_month is the latest in progress, it scales up to the full month using historical day-type averages.
    - If target_month is the first month and starts late, it keeps the projection as actuals (no retrospective scaling).
    """
    df_month = df_all[df_all["year_month"] == target_month].copy()
    if df_month.empty:
        return 0.0, 0.0, 0.0, 0.0, False, 1, 30
    
    actual_kwh = df_month["estimated_kwh"].sum()
    actual_usage_cost = df_month["cost"].sum()
    
    # Calculate days recorded in this month
    days_recorded = sorted(df_month["date_only"].unique())
    days_elapsed = len(days_recorded)
    
    # Get total days in this calendar month
    y_val, m_val = map(int, target_month.split("-"))
    days_in_month = calendar.monthrange(y_val, m_val)[1]
    
    is_latest_month = (target_month == df_all["year_month"].max())
    is_first_month = (target_month == df_all["year_month"].min())
    
    is_unfinished = False
    projected_kwh = actual_kwh
    projected_usage_cost = actual_usage_cost
    
    # Scenario A: The latest month is currently in progress (unfinished)
    if is_latest_month and (days_elapsed < days_in_month):
        is_unfinished = True
        
        last_recorded_date = max(days_recorded)
        
        # Map remaining calendar dates
        remaining_dates = []
        curr = last_recorded_date + timedelta(days=1)
        end_of_month_date = datetime(y_val, m_val, days_in_month).date()
        while curr <= end_of_month_date:
            remaining_dates.append(curr)
            curr += timedelta(days=1)
            
        if remaining_dates:
            # Gather clean daily profiles from historical data (excluding target month) if available
            df_history = df_all[df_all["year_month"] != target_month].copy()
            if df_history.empty:
                df_history = df_month.copy()
            
            daily_history = df_history.groupby(["date_only", "is_weekend"])["estimated_kwh"].sum().reset_index()
            
            # Weekend/Weekday consumption split
            weekday_avg = daily_history[~daily_history["is_weekend"]]["estimated_kwh"].mean()
            weekend_avg = daily_history[daily_history["is_weekend"]]["estimated_kwh"].mean()
            
            overall_mean = daily_history["estimated_kwh"].mean() if not daily_history.empty else 10.0
            if pd.isna(weekday_avg): weekday_avg = overall_mean
            if pd.isna(weekend_avg): weekend_avg = overall_mean
            
            # Track average tariff rate paid
            df_history["rate_paid"] = df_history["cost"] / df_history["estimated_kwh"].replace(0, np.nan)
            avg_rate = df_history["rate_paid"].mean()
            if pd.isna(avg_rate):
                avg_rate = rates.get("flat_rate", rates.get("day_rate", 0.28))
                
            proj_rem_kwh = 0.0
            proj_rem_cost = 0.0
            for r_date in remaining_dates:
                is_we = (r_date.weekday() >= 5)
                day_kwh = weekend_avg if is_we else weekday_avg
                proj_rem_kwh += day_kwh
                proj_rem_cost += day_kwh * avg_rate
                
            projected_kwh = actual_kwh + proj_rem_kwh
            projected_usage_cost = actual_usage_cost + proj_rem_cost
            
    # Scenario B: The first month starts late
    # Keep projection equal to actuals for the active part of that historical month
    elif is_first_month:
        projected_kwh = actual_kwh
        projected_usage_cost = actual_usage_cost
        
    # Standardize standing charges & VAT
    actual_standing_pso = days_elapsed * rates["daily_standing_charge"]
    actual_gross_cost = (actual_usage_cost + actual_standing_pso) * (1 + rates["vat_rate"])
    
    if is_unfinished:
        projected_standing_pso = days_in_month * rates["daily_standing_charge"]
    else:
        projected_standing_pso = days_elapsed * rates["daily_standing_charge"]
        
    projected_gross_cost = (projected_usage_cost + projected_standing_pso) * (1 + rates["vat_rate"])
    
    return actual_kwh, actual_gross_cost, projected_kwh, projected_gross_cost, is_unfinished, days_elapsed, days_in_month


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
    df["hour_float"]  = df["reading_at"].dt.hour + df["reading_at"].dt.minute / 60.0
    df["is_weekend"]  = df["reading_at"].dt.dayofweek >= 5

    # 3. Disaggregate Appliances (Cached execution)
    df = disaggregate_appliances(df, house_profile)

    # Filter Setup
    st.sidebar.markdown("---")
    st.sidebar.header("📅 View Filter")
    months = sorted(df["year_month"].unique().tolist())
    selected_month = st.sidebar.selectbox("Select Period:", ["All Months"] + months)
    
    # Get overall active list of months to analyze bounds
    first_dataset_month = df["year_month"].min()
    latest_dataset_month = df["year_month"].max()

    # 4. RUN COMPREHENSIVE PROJECTION METRICS
    if selected_month == "All Months":
        # Sum up individual smart projections for each month to respect boundaries
        actual_kwh = 0.0
        actual_gross_cost = 0.0
        proj_kwh = 0.0
        proj_cost = 0.0
        is_unfinished = False
        days_elapsed = 0
        days_in_month = 0
        
        for m in months:
            m_act_kwh, m_act_cost, m_proj_kwh, m_proj_cost, m_unf, m_el, m_tot = calculate_smart_projection(df, m, rates)
            actual_kwh += m_act_kwh
            actual_gross_cost += m_act_cost
            proj_kwh += m_proj_kwh
            proj_cost += m_proj_cost
            days_elapsed += m_el
            days_in_month += m_tot
            if m_unf:
                is_unfinished = True
    else:
        actual_kwh, actual_gross_cost, proj_kwh, proj_cost, is_unfinished, days_elapsed, days_in_month = calculate_smart_projection(df, selected_month, rates)

    df_filtered = df[df["year_month"] == selected_month].copy() if selected_month != "All Months" else df.copy()

    # Calculate metrics & daily patterns
    daily_stats = df_filtered.groupby("date_only").agg(
        kwh=("estimated_kwh", "sum"),
        cost=("cost", "sum")
    ).reset_index()
    daily_stats["cost_inc_fixed_vat"] = (daily_stats["cost"] + rates["daily_standing_charge"]) * (1 + rates["vat_rate"])

    if not daily_stats.empty:
        lowest_daily_cost = daily_stats["cost_inc_fixed_vat"].quantile(0.10)
        peak_daily_cost   = daily_stats["cost_inc_fixed_vat"].quantile(0.90)
    else:
        lowest_daily_cost = 0.0
        peak_daily_cost   = 0.0

    max_kw = df_filtered["read_value_kw"].max()
    avg_import_rate = df_filtered["cost"].sum() / actual_kwh if actual_kwh > 0 else 0.30

    # Display Warning Cards
    if is_unfinished:
        st.warning(f"⚠️ **Target Period is Incomplete.** Only **{days_elapsed} of {days_in_month} days** are recorded. Projections for the active month are highlighted in brown.")
    
    if selected_month == first_dataset_month and first_dataset_month != latest_dataset_month:
        # Check if first day starts late in the month (e.g. > 1st)
        first_recorded_day = df[df["year_month"] == first_dataset_month]["reading_at"].min().day
        if first_recorded_day > 1:
            st.info(f"ℹ️ **First recorded month starts late on Day {first_recorded_day}.** Projections are kept equivalent to actuals based on the remaining active days, preventing retrospective inflation.")

    # Top KPI Cards
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        color, val, label, sub = ("#4e342e", proj_kwh, "Estimated Period-End", f"Actual to-date: {actual_kwh:,.1f} kWh") if is_unfinished else ("#0d47a1", actual_kwh, "Total Consumption", "Completed Period")
        st.markdown(f'<div class="metric-container"><div class="metric-value" style="color:{color};">{val:,.1f} kWh</div><div class="metric-label">{label}</div><div class="metric-badge badge-actual">{sub}</div></div>', unsafe_allow_html=True)
    with col2:
        color, val, label, sub = ("#4e342e", proj_cost, "Projected Period Bill", f"Actual to-date: €{actual_gross_cost:,.2f}") if is_unfinished else ("#1b5e20", actual_gross_cost, "Total Cost (Inc VAT)", "Completed Period")
        st.markdown(f'<div class="metric-container"><div class="metric-value" style="color:{color};">€{val:,.2f}</div><div class="metric-label">{label}</div><div class="metric-badge badge-success">{sub}</div></div>', unsafe_allow_html=True)
    with col3:
        st.markdown(f'<div class="metric-container"><div class="metric-value" style="color:#e65100;">€{(actual_gross_cost/max(days_elapsed,1)):.2f}/day</div><div class="metric-label">Avg Daily Cost</div><div class="metric-badge badge-warning">Target: < €{(actual_gross_cost*0.9/max(days_elapsed,1)):.2f}</div></div>', unsafe_allow_html=True)
    with col4:
        st.markdown(f'<div class="metric-container"><div class="metric-value" style="color:#37474f;">{max_kw:.2f} kW</div><div class="metric-label">Peak Demand Spike</div><div class="metric-badge badge-actual" style="background-color:#eceff1;color:#37474f;">Limit simultaneous loads</div></div>', unsafe_allow_html=True)

    st.markdown("---")

    # Tabs
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "📈 Month-to-Month", "📊 Cumulative Projections", "⏰ Hourly Peaks", 
        "🔌 Appliance Breakdown", "🧠 Advanced Insights", "🔍 Interactive Heatmap"
    ])

    with tab1:
        st.subheader("Monthly Historical Trends")
        monthly = df.groupby("year_month").agg(total_kwh=("estimated_kwh", "sum"), cost=("cost", "sum"), days=("date_only", "nunique")).reset_index()
        monthly["total_cost"] = (monthly["cost"] + (monthly["days"] * rates["daily_standing_charge"])) * (1 + rates["vat_rate"])
        
        c1, c2 = st.columns(2)
        with c1:
            fig1 = px.line(monthly, x="year_month", y="total_kwh", text=monthly["total_kwh"].round(0), markers=True, title="Consumption Trend (kWh)")
            fig1.update_traces(
                hovertemplate="<b>Billing Month:</b> %{x}<br><b>Energy Consumed:</b> %{y:,.1f} kWh<extra></extra>",
                line=dict(width=3, color="#1e88e5")
            )
            st.plotly_chart(fig1, use_container_width=True)
        with c2:
            fig2 = px.bar(monthly, x="year_month", y="total_cost", text=monthly["total_cost"].round(0), title="Total Financial Bill (€)", color_discrete_sequence=["#2e7d32"])
            fig2.update_traces(
                hovertemplate="<b>Billing Month:</b> %{x}<br><b>Calculated Bill:</b> €%{y:,.2f}<extra></extra>"
            )
            st.plotly_chart(fig2, use_container_width=True)

    with tab2:
        st.subheader("Accumulated & Proportional Daily Costs")
        
        # Cumulative Cost Tracking
        daily_stats["Cumulative Cost"] = daily_stats["cost_inc_fixed_vat"].cumsum()
        daily_stats["Day Number"] = np.arange(len(daily_stats)) + 1
        
        # Add linear dynamic baseline projections
        avg_daily = actual_gross_cost / max(days_elapsed, 1)
        daily_stats["Average Path"] = daily_stats["Day Number"] * avg_daily
        daily_stats["90th Pct Path"] = daily_stats["Day Number"] * peak_daily_cost
        daily_stats["10th Pct Path"] = daily_stats["Day Number"] * lowest_daily_cost
        
        col_side, col_graph = st.columns([1, 3])
        with col_side:
            st.markdown("#### Cost Range Models")
            st.write("We modeled your future spend boundaries by evaluating the $90^{\\text{th}}$ and $10^{\\text{th}}$ percentiles of your daily spending history:")
            st.metric("Worst-Case Projection (90th Pct)", f"€{peak_daily_cost:.2f} / day")
            st.metric("Best-Case Projection (10th Pct)", f"€{lowest_daily_cost:.2f} / day")
            
            # Interactive Slider to simulate future days
            sim_days = st.slider("Project Cumulative Costs over Days:", min_value=1, max_value=60, value=30)
            best_sim_total = lowest_daily_cost * sim_days
            worst_sim_total = peak_daily_cost * sim_days
            st.write(f"In **{sim_days} days**, you are projected to spend between **€{best_sim_total:.2f}** and **€{worst_sim_total:.2f}** based on these percentile rates.")

        with col_graph:
            fig_cum = go.Figure()
            # Plot actual cumulative spend
            fig_cum.add_trace(go.Scatter(
                x=daily_stats["date_only"], y=daily_stats["Cumulative Cost"],
                mode='lines+markers', name='Actual Accumulated Spend',
                line=dict(color='#1b5e20', width=4),
                hovertemplate="<b>Date:</b> %{x}<br><b>Accumulated Cost:</b> €%{y:,.2f}<extra></extra>"
            ))
            # Plot typical average path
            fig_cum.add_trace(go.Scatter(
                x=daily_stats["date_only"], y=daily_stats["Average Path"],
                mode='lines', name='Linear Average Base Path',
                line=dict(color='#1e88e5', dash='dash'),
                hovertemplate="<b>Date:</b> %{x}<br><b>Linear Average Base:</b> €%{y:,.2f}<extra></extra>"
            ))
            # Bound shading for confidence interval (90th vs 10th percentile daily bounds)
            fig_cum.add_trace(go.Scatter(
                x=daily_stats["date_only"], y=daily_stats["90th Pct Path"],
                mode='lines', name='Upper Peak Projection (90th %)',
                line=dict(color='#ef5350', width=1, dash='dot')
            ))
            fig_cum.add_trace(go.Scatter(
                x=daily_stats["date_only"], y=daily_stats["10th Pct Path"],
                mode='lines', name='Lower Savings Projection (10th %)',
                line=dict(color='#66bb6a', width=1, dash='dot'),
                fill='tonexty', fillcolor='rgba(139, 195, 74, 0.1)'
            ))
            
            fig_cum.update_layout(
                title="Accumulated Billing Growth vs Dynamic Percentile Boundaries",
                xaxis_title="Date",
                yaxis_title="Total Cost Accumulation (Inc. Standing Charges & VAT)",
                legend_orientation="h"
            )
            st.plotly_chart(fig_cum, use_container_width=True)

    with tab3:
        st.subheader("Diurnal Load Profiling")
        hourly = df_filtered.groupby("hour_of_day")["estimated_kwh"].agg(["mean", "max", "min"]).reset_index()
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=hourly["hour_of_day"], y=hourly["mean"],
            mode='lines', fill='tozeroy', name='Avg Demand',
            line=dict(color='#ff9800', width=3),
            hovertemplate="<b>Hour Slot:</b> %{x}:00<br><b>Average Draw:</b> %{y:.3f} kWh<extra></extra>"
        ))
        fig.add_trace(go.Scatter(
            x=hourly["hour_of_day"], y=hourly["max"],
            mode='lines', name='Max Recorded Spike',
            line=dict(color='#d32f2f', width=2, dash='dash'),
            hovertemplate="<b>Hour Slot:</b> %{x}:00<br><b>Max Recorded Peak:</b> %{y:.2f} kWh<extra></extra>"
        ))
        fig.update_layout(
            title="Hourly Aggregated Performance Curves",
            xaxis=dict(title="Hour of Day", tickmode='linear', tick0=0, dtick=1),
            yaxis_title="Draw Rate (kWh per 30 mins)"
        )
        st.plotly_chart(fig, use_container_width=True)

    with tab4:
        st.subheader("Statistical Appliance Profiling")
        app_cols = ["app_always_on", "app_ev", "app_heating", "app_cooking", "app_laundry", "app_entertainment", "app_misc"]
        totals = df_filtered[app_cols].sum().reset_index()
        totals.columns = ["Appliance", "kWh"]
        totals["Appliance"] = totals["Appliance"].str.replace("app_", "").str.replace("_", " ").str.title()
        
        # Map dynamic color palette
        color_map_apps = {
            "Always On": "#90a4ae", "Ev": "#4caf50", "Heating": "#f44336",
            "Cooking": "#ff9800", "Laundry": "#2196f3", "Entertainment": "#9c27b0", "Misc": "#757575"
        }
        
        c1, c2 = st.columns([2, 3])
        with c1:
            fig = px.pie(
                totals, values="kWh", names="Appliance", hole=0.45,
                title="Continuous Load Contribution",
                color="Appliance", color_discrete_map=color_map_apps
            )
            fig.update_traces(
                textposition='inside', textinfo='percent+label',
                hovertemplate="<b>%{label}</b><br>Consumption Volume: <b>%{value:,.1f} kWh</b><br>Share: <b>%{percent}</b><extra></extra>"
            )
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            app_daily = df_filtered.groupby("date_only")[app_cols].sum().reset_index()
            app_daily.columns = ["Date"] + [c.replace("app_", "").title() for c in app_cols]
            fig2 = px.bar(
                app_daily, x="Date", y=app_daily.columns[1:],
                title="Stacked Daily Multi-Load Patterns", barmode="stack",
                color_discrete_map=color_map_apps
            )
            fig2.update_traces(
                hovertemplate="<b>%{x}</b><br>%{series.name} share: <b>%{y:,.2f} kWh</b><extra></extra>"
            )
            st.plotly_chart(fig2, use_container_width=True)

    with tab5:
        st.subheader("🧠 Advanced Smart Insights & Predictive Models")
        st.write("Going beyond basic tariffs: AI-driven models mapping your historical behavior against potential physical and financial upgrades.")

        # 1. Solar Viability Model
        st.markdown("#### ☀️ Solar PV Self-Consumption Model (Simulated 3kWp System)")
        st.write("Calculates real self-consumption capability by mapping a synthetic solar generation curve against your exact minute-by-minute daytime load history.")
        
        # Create a synthetic solar generation bell curve (peak ~1.0 kWh per 30 mins around 13:00)
        df_filtered["solar_gen_kwh"] = np.where(
            (df_filtered["hour_float"] >= 6) & (df_filtered["hour_float"] <= 20),
            1.0 * np.exp(-0.10 * (df_filtered["hour_float"] - 13.0)**2),
            0.0
        )
        
        # Calculate matching self-consumption
        df_filtered["solar_self_consumed"] = np.minimum(df_filtered["estimated_kwh"], df_filtered["solar_gen_kwh"])
        df_filtered["solar_exported"] = df_filtered["solar_gen_kwh"] - df_filtered["solar_self_consumed"]

        total_solar_gen = df_filtered["solar_gen_kwh"].sum()
        total_self_consumed = df_filtered["solar_self_consumed"].sum()
        total_exported = df_filtered["solar_exported"].sum()

        # Financials: Saved import cost + Generated export profit (assuming standard 20c CEG rate)
        solar_savings = (total_self_consumed * avg_import_rate) + (total_exported * 0.20)

        col_s1, col_s2, col_s3 = st.columns(3)
        col_s1.metric("Estimated Solar Generation", f"{total_solar_gen:,.1f} kWh")
        col_s2.metric("Self-Consumption Rate", f"{(total_self_consumed/total_solar_gen*100):.1f}%" if total_solar_gen > 0 else "0%")
        col_s3.metric("Projected Financial Yield", f"€{solar_savings:,.2f}", delta=f"Offsets {total_self_consumed/actual_kwh*100:.1f}% of demand")

        st.markdown("---")

        # 2. Peak Shifting Arbitrage
        st.markdown("#### ⏰ Behavioral Arbitrage (Peak Load Shifting)")
        peak_mask = (df_filtered["hour_of_day"] >= 17) & (df_filtered["hour_of_day"] < 19)
        night_rate = rates.get("night_rate", rates.get("flat_rate", 0.15))
        peak_rate = rates.get("peak_rate", rates.get("flat_rate", 0.35))
        
        peak_kwh_total = df_filtered.loc[peak_mask, "estimated_kwh"].sum()
        
        if peak_rate > night_rate:
            # Assume a 30% shift is achievable by moving dishwashers, washing machines, and delayed EV charging
            shift_savings = peak_kwh_total * 0.30 * (peak_rate - night_rate)
            st.success(f"💡 **Actionable Arbitrage:** By utilizing appliance timers to shift just **30%** of your peak-time usage (17:00-19:00) to cheap night hours, you would save an estimated **€{shift_savings:.2f}** this period alone without reducing total consumption.")
        else:
            st.info("💡 **Actionable Arbitrage:** You are currently utilizing a Flat rate. If you switched to a Smart Tariff and shifted 30% of your evening peak usage to night hours, you could generate significant financial arbitrage. Adjust your sidebar settings to simulate this.")

        st.markdown("---")

        # 3. Vampire Draw Benchmark
        st.markdown("#### 🧛 Vampire Draw (Always-On Waste Benchmarking)")
        baseload_kwh_total = df_filtered["app_always_on"].sum()
        baseload_pct = (baseload_kwh_total / actual_kwh) * 100 if actual_kwh > 0 else 0
        benchmark_pct = 12.0 # Modern highly efficient home benchmark
        baseload_cost = baseload_kwh_total * avg_import_rate

        col_v1, col_v2 = st.columns(2)
        with col_v1:
            if baseload_pct > 20:
                st.error(f"⚠️ Your idle 'always-on' background usage accounts for **{baseload_pct:.1f}%** of your total consumption. This indicates high vampire draw. (Efficient Benchmark: ~{benchmark_pct}%)")
            else:
                st.success(f"✅ Your idle 'always-on' background usage accounts for **{baseload_pct:.1f}%** of your total consumption. This is excellent! (Efficient Benchmark: ~{benchmark_pct}%)")
        with col_v2:
             st.metric("Total Cost of Standby & Idle Devices", f"€{baseload_cost:,.2f} / period")

    with tab6:
        st.subheader("🔍 Appliance Detective & Signature Event Miner")
        st.markdown(
            "This module scans high-frequency interval files to identify distinct and brief electrical event signatures. "
            "Unlike continuous models, this captures instantaneous user-initiated behaviors."
        )

        # Event Counts Heuristics
        total_showers = int(df_filtered["sig_shower"].sum())
        total_kettles = int(df_filtered["sig_kettle"].sum())
        
        # Standing baseload estimate
        avg_baseload_kw = df_filtered["app_always_on"].mean() * 2.0  # kwh to kW
        
        col_det1, col_det2 = st.columns(2)
        with col_det1:
            st.markdown("### Detected High-Wattage Signature Summaries")
            
            # Kettle Card
            st.markdown(
                f"""
                <div class="appliance-card" style="border-left-color: #ff9800;">
                    <div class="appliance-title">☕ Kettle Boiling Events</div>
                    <div class="appliance-stats">
                        Detected <b>{total_kettles} isolated boiling events</b> this period.<br>
                        Estimated Consumption Rate: <b>~2.5 kW to 3.0 kW</b> sustained for 3 to 5 minutes.<br>
                        Approximate cost per boil: <b>€{(0.20 * avg_import_rate):.3f}</b>
                    </div>
                </div>
                """, unsafe_allow_html=True
            )
            
            # Shower Card
            st.markdown(
                f"""
                <div class="appliance-card" style="border-left-color: #f44336;">
                    <div class="appliance-title">🚿 Power Shower / High-Wattage Events</div>
                    <div class="appliance-stats">
                        Detected <b>{total_showers} heavy-draw power shower cycles</b> this period.<br>
                        Estimated Consumption Rate: <b>~7.5 kW to 9.5 kW</b> sustained for 10 to 15 minutes.<br>
                        Approximate cost per shower cycle: <b>€{(1.60 * avg_import_rate):.2f}</b>
                    </div>
                </div>
                """, unsafe_allow_html=True
            )
            
            # Baseload Card
            st.markdown(
                f"""
                <div class="appliance-card" style="border-left-color: #90a4ae;">
                    <div class="appliance-title">🔌 Baseline Idle Load (Always-On)</div>
                    <div class="appliance-stats">
                        Calculated average always-on idle draw: <b>{avg_baseload_kw:.3f} kW</b>.<br>
                        This constitutes standby devices, routers, clocks, and recurring refrigeration periods.<br>
                        Extrapolated constant idle cost: <b>€{(avg_baseload_kw * 24 * 30 * avg_import_rate):.2f} / month</b>.
                    </div>
                </div>
                """, unsafe_allow_html=True
            )

        with col_det2:
            st.markdown("### Energy Intensity Grid Heatmap")
            pivot = df_filtered.pivot_table(index="day_name", columns="hour_of_day", values="estimated_kwh", aggfunc="mean").reindex(
                ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            )
            
            # Custom styled premium heat array color representation
            fig_heat = px.imshow(
                pivot,
                labels=dict(x="Hour of Day", y="Day of Week", color="Average Consumption (kWh)"),
                x=pivot.columns, y=pivot.index,
                color_continuous_scale="Thermal" # Heat map optimal visual contrast coloring
            )
            
            # Custom rich tooltip formatting for the pivot coordinates
            fig_heat.update_traces(
                hovertemplate="<b>Day:</b> %{y}<br><b>Time:</b> %{x}:00 to %{x}:30<br><b>Average Draw:</b> %{z:.3f} kWh<br><b>Equivalent Load:</b> %{customdata:.2f} kW<extra></extra>",
                customdata=pivot.values * 2.0  # instantaneous kW mapping
            )
            
            fig_heat.update_layout(
                title="Weekly Operational Density Heat Grid",
                xaxis=dict(title="Hour of Day (30-min Intervals)", tickmode='linear', tick0=0, dtick=2),
                yaxis_title="Day of Week"
            )
            st.plotly_chart(fig_heat, use_container_width=True)

else:
    st.info("👈 Upload your Smart Meter Data or select the Demo Data option to get started.")
