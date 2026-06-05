import io
import re
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta

# Set page configuration
st.set_page_config(
    page_title="Smart Meter Analytics Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- UTILITY PARSER FUNCTION (Optimized) ---
def parse_interval_csv(uploaded_file) -> pd.DataFrame:
    """
    Parses electricity smart meter interval data files (such as ESB HDF files).
    Handles files with leading metadata headers, flexible column names, and 
    both CSV and raw text-copy formats.
    """
    if hasattr(uploaded_file, "read"):
        raw_bytes = uploaded_file.read()
    else:
        raw_bytes = uploaded_file

    if isinstance(raw_bytes, bytes):
        raw = raw_bytes.decode("utf-8-sig", errors="ignore")
    else:
        raw = str(raw_bytes)

    def finalize(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame()
        
        value_col = "read_value" if "read_value" in df.columns else "read_value_kw"
        df = df.dropna(subset=["reading_at", value_col]).copy()
        
        if "read_value_kw" not in df.columns:
            df["read_value_kw"] = pd.to_numeric(df[value_col], errors="coerce")
            
        df["reading_at"] = pd.to_datetime(df["reading_at"], errors="coerce")
        df = df.dropna(subset=["reading_at", "read_value_kw"]).sort_values("reading_at")
        
        if df.empty:
            return pd.DataFrame()
            
        is_kwh = df["read_type"].str.contains("kWh", case=False, na=False) if "read_type" in df.columns else True
        df["interval_hours"] = 0.5
        df["estimated_kwh"] = np.where(is_kwh, df["read_value_kw"], df["read_value_kw"] * df["interval_hours"])
        
        df["date_only"] = df["reading_at"].dt.date
        
        for col in ["mprn", "meter_serial"]:
            if col not in df.columns:
                df[col] = ""
                
        return df[["mprn", "meter_serial", "reading_at", "read_value_kw", "estimated_kwh", "date_only"]]

    # Extract Data using Pandas (CSV Mode)
    try:
        lines = raw.splitlines()
        header_idx = -1
        required_keywords = ["mprn", "value", "date"]
        
        for idx, line in enumerate(lines):
            line_lower = line.lower()
            if "mprn" in line_lower and any(k in line_lower for k in ["date", "time", "value"]):
                header_idx = idx
                break
        
        csv_source = "\n".join(lines[header_idx:]) if header_idx != -1 else raw
        csv_df = pd.read_csv(io.StringIO(csv_source))
        
        cols = {str(col).strip().lower(): col for col in csv_df.columns}
        
        mprn_col = next((cols[c] for c in cols if "mprn" in c), None)
        serial_col = next((cols[c] for c in cols if "serial" in c or "meter" in c), None)
        value_col = next((cols[c] for c in cols if "value" in c or "reading" in c), None)
        type_col = next((cols[c] for c in cols if "type" in c), None)
        date_col = next((cols[c] for c in cols if "date" in c or "time" in c or "at" in c), None)

        if all([mprn_col, value_col, date_col]):
            df = pd.DataFrame({
                "mprn": csv_df[mprn_col].astype(str).str.strip(),
                "meter_serial": csv_df[serial_col].astype(str).str.strip() if serial_col else "",
                "read_value": pd.to_numeric(csv_df[value_col], errors="coerce"),
                "read_type": csv_df[type_col].astype(str).str.strip() if type_col else "Active Import Interval (kWh)",
                "reading_at": pd.to_datetime(
                    csv_df[date_col],
                    errors="coerce",
                ),
            })

            if type_col:
                df = df[
                    df["read_type"].str.contains(
                        r"Active Import Interval\s*(?:\((?:kW|kWh)\))?",
                        regex=True,
                        na=False,
                        case=False
                    )
                ]

            parsed = finalize(df)
            if not parsed.empty:
                return parsed
    except Exception:
        pass

    # Fallback Regex Parser
    pattern = re.compile(
        r"(?P<mprn>\d{11})[,\s\t;]+"
        r"(?P<serial>[A-Za-z0-9_-]+)[,\s\t;]+"
        r"(?P<value>\d+(?:\.\d+)?)[,\s\t;]+"
        r"(?P<read_type>Active Import Interval(?:\s*\((?:kW|kWh)\)|\s+kW|\s+kWh))[,\s\t;]+"
        r"(?P<date>\d{2}[-/]\d{2}[-/]\d{4})[,\s\t ]+"
        r"(?P<time>\d{2}:?\d{2}(?::?\d{2})?)"
    )

    rows = []
    for m in pattern.finditer(raw):
        date_str = m.group("date").replace("/", "-")
        time_text = m.group("time").replace(":", "")
        if len(time_text) > 4:
            time_text = time_text[:4]
            
        reading_at = pd.to_datetime(
            f"{date_str} {time_text}",
            format="%d-%m-%Y %H%M",
            errors="coerce",
        )
        rows.append({
            "mprn": m.group("mprn"),
            "meter_serial": m.group("serial"),
            "read_value_kw": float(m.group("value")),
            "read_type": m.group("read_type"),
            "reading_at": reading_at,
        })

    if not rows:
        fallback_lines = []
        for line in raw.splitlines():
            line = line.strip()
            if "Active Import" not in line:
                continue
            m = re.search(pattern, line)
            if m:
                date_str = m.group("date").replace("/", "-")
                time_text = m.group("time").replace(":", "")
                if len(time_text) > 4:
                    time_text = time_text[:4]
                fallback_lines.append({
                    "mprn": m.group("mprn"),
                    "meter_serial": m.group("serial"),
                    "read_value_kw": float(m.group("value")),
                    "read_type": m.group("read_type"),
                    "reading_at": pd.to_datetime(
                        f"{date_str} {time_text}",
                        format="%d-%m-%Y %H%M",
                        errors="coerce",
                    ),
                })
        rows = fallback_lines

    if not rows:
        return pd.DataFrame()

    return finalize(pd.DataFrame(rows))


# --- DEMO DATA GENERATOR ---
def generate_demo_data() -> pd.DataFrame:
    """Generates synthetic half-hourly smart meter interval data for demo purposes."""
    np.random.seed(42)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=90)  # ~3 months of data
    
    date_range = pd.date_range(start=start_date, end=end_date, freq="30min")
    
    rows = []
    for dt in date_range:
        hour = dt.hour
        weekday = dt.weekday()
        
        # Base load behavior (refrigerator, standby appliances)
        base = 0.15 + np.random.normal(0, 0.02)
        
        # Activity multipliers
        if 8 <= hour < 17:  # Daytime work hours
            activity = 0.25 + np.random.normal(0, 0.05)
        elif 17 <= hour < 19:  # Peak dinner time
            activity = 0.85 + np.random.normal(0, 0.15)
        elif 19 <= hour < 23:  # Evening wind-down
            activity = 0.45 + np.random.normal(0, 0.08)
        else:  # Night time sleep
            activity = 0.05 + np.random.normal(0, 0.01)
            
        # Add weekend variation
        if weekday >= 5:
            activity *= 1.25
            
        # Add kitchen/heating spikes occasionally
        spike = 1.8 if (hour == 8 or hour == 18) and np.random.rand() > 0.7 else 0.0
            
        read_val = max(0.01, base + activity + spike)
        
        rows.append({
            "mprn": "10303339574",
            "meter_serial": "000000000024049722",
            "reading_at": dt,
            "read_value_kw": read_val * 2, # Convert kWh consumption back to kW demand
            "estimated_kwh": read_val,
            "date_only": dt.date()
        })
        
    return pd.DataFrame(rows)


# --- DYNAMIC COST CALCULATION ---
def apply_tariffs(df: pd.DataFrame, rates: dict) -> pd.DataFrame:
    """
    Applies custom tariff bands to each reading based on the configured rates.
    Supports either a flat 24-hour rate or dynamic Smart Day/Night/Peak bands.
    """
    df = df.copy()
    df["hour"] = df["reading_at"].dt.hour
    
    if rates["type"] == "flat":
        df["tariff_band"] = "24hr Flat"
        df["tariff_rate"] = rates["flat_rate"]
    else:
        # Assign Smart Tariff Categories
        conditions = [
            (df["hour"] >= 23) | (df["hour"] < 8),                     # Night (11 PM - 8 AM)
            (df["hour"] >= 17) & (df["hour"] < 19),                     # Peak (5 PM - 7 PM)
        ]
        choices = ["Night", "Peak"]
        df["tariff_band"] = np.select(conditions, choices, default="Day")
        
        # Map rates to bands
        rate_map = {
            "Day": rates["day_rate"],
            "Night": rates["night_rate"],
            "Peak": rates["peak_rate"]
        }
        df["tariff_rate"] = df["tariff_band"].map(rate_map)
        
    df["cost"] = df["estimated_kwh"] * df["tariff_rate"]
    return df


# --- HEURISTIC APPLIANCE DISAGGREGATION ENGINE ---
def disaggregate_appliances(df: pd.DataFrame, house_profile: dict) -> pd.DataFrame:
    """
    Uses NILM-style baseline subtraction and rules-based heuristic models
    to disaggregate total half-hourly kWh usage into common household appliance groups.
    """
    df = df.copy()
    
    # 1. Identify daily baseline (Always On/Standby load)
    # Group by date and find the minimum 30-min window consumption as the baseload power signature
    daily_min = df.groupby("date_only")["estimated_kwh"].transform("min")
    
    # Always On: Runs continuously. We cap always-on at a reasonable 0.25 kWh per 30 mins (500W continuous)
    df["app_always_on"] = np.minimum(daily_min, 0.25)
    
    # Remaining active consumption to disaggregate
    df["active_kwh"] = np.maximum(0.0, df["estimated_kwh"] - df["app_always_on"])
    
    # Initialize disaggregated categories
    df["app_ev"] = 0.0
    df["app_heating"] = 0.0
    df["app_cooking"] = 0.0
    df["app_laundry"] = 0.0
    df["app_entertainment"] = 0.0
    df["app_misc"] = 0.0
    
    # Helper time flags
    df["hour_float"] = df["reading_at"].dt.hour + df["reading_at"].dt.minute / 60.0
    df["is_weekend"] = df["reading_at"].dt.dayofweek >= 5
    
    for idx, row in df.iterrows():
        active = row["active_kwh"]
        if active <= 0:
            continue
            
        hr = row["hour_float"]
        
        # A. ELECTRIC VEHICLE (EV) CHARGING
        # Standard home chargers pull 7.4 kW (which is ~3.7 kWh per 30 mins)
        # Usually runs overnight (e.g., midnight to 6 AM)
        if house_profile["has_ev"] and (0.0 <= hr < 6.0) and active > 1.5:
            ev_draw = min(active, 3.7) # Cap at typical EV charger max output per half hour
            df.at[idx, "app_ev"] = ev_draw
            active -= ev_draw
            
        # B. SPACE & WATER HEATING
        # High-power thermal draws in the morning (5:30 - 8:30) or night-boost (23:00 - 2:00)
        if active > 0:
            is_heating_window = (5.5 <= hr < 8.5) or (23.0 <= hr) or (0.0 <= hr < 2.0)
            if is_heating_window:
                heating_ratio = 0.6 if house_profile["electric_heating"] else 0.15
                heat_draw = active * heating_ratio
                df.at[idx, "app_heating"] = heat_draw
                active -= heat_draw

        # C. COOKING & KITCHEN APPLIANCES
        # Confined to traditional meal prep windows: Breakfast (7:00-9:00), Lunch (12:00-14:00), Dinner (16:30-19:30)
        if active > 0:
            is_cooking_window = (7.0 <= hr < 9.0) or (12.0 <= hr < 14.0) or (16.5 <= hr < 19.5)
            if is_cooking_window:
                # Cooking involves short, sharp surges (kettles, hob, oven)
                cooking_ratio = 0.55 if active > 0.15 else 0.3
                cooking_draw = active * cooking_ratio
                df.at[idx, "app_cooking"] = cooking_draw
                active -= cooking_draw

        # D. WET APPLIANCES (Laundry / Dishwasher)
        # Typically run during morning chores (9:00-12:00), afternoon slots (14:00-16:30), or weekends.
        # Marked by moderate continuous draws.
        if active > 0:
            is_chore_window = (9.0 <= hr < 12.0) or (14.0 <= hr < 16.5) or (row["is_weekend"] and 10.0 <= hr < 17.0)
            if is_chore_window:
                laundry_ratio = 0.5 if active > 0.2 else 0.2
                laundry_draw = active * laundry_ratio
                df.at[idx, "app_laundry"] = laundry_draw
                active -= laundry_draw

        # E. ENTERTAINMENT & LIGHTING
        # Active evenings awake times (18:00 - 23:30)
        if active > 0:
            if 18.0 <= hr < 23.5:
                ent_draw = active * 0.7
                df.at[idx, "app_entertainment"] = ent_draw
                active -= ent_draw
                
        # F. MISCELLANEOUS (unallocated active loads)
        if active > 0:
            df.at[idx, "app_misc"] = active
            
    return df


# --- STREAMLIT UI LAYOUT ---

st.title("⚡ Smart Meter Analytics & Cost Dashboard")
st.markdown("Upload your utility smart meter interval export (HDF files, CSVs, or text captures) to analyze usage and model costs.")

# --- SIDEBAR CONTROLS ---
st.sidebar.header("📁 Data Source")
data_option = st.sidebar.radio("Choose Data Input:", ["Use Sample Demo Data", "Upload My Own File"])

uploaded_file = None
if data_option == "Upload My Own File":
    uploaded_file = st.sidebar.file_uploader(
        "Upload Smart Meter File (CSV or TXT)", 
        type=["csv", "txt"],
        help="Supports standard Smart Meter HDF CSVs (with leading metadata or plain table structures)"
    )

# --- TARIFF / COST SIDEBAR ---
st.sidebar.header("💰 Tariff Settings")

tariff_style = st.sidebar.selectbox(
    "Select Tariff Type:",
    ["24-Hour Flat Tariff", "Smart (Day/Night/Peak) Tariff"],
    index=0
)

rates = {}
if tariff_style == "24-Hour Flat Tariff":
    rates["type"] = "flat"
    flat_rate_cent = st.sidebar.number_input(
        "Flat Rate (Cent / kWh)", 
        min_value=0.0, 
        max_value=200.0, 
        value=26.41, 
        step=0.1, 
        format="%.2f",
        help="Your flat electricity usage unit fee. Converts automatically to Euros."
    )
    rates["flat_rate"] = flat_rate_cent / 100.0  # Convert Cent to Euro
else:
    rates["type"] = "smart"
    day_rate_cent = st.sidebar.number_input("Day Rate (Cent/kWh)", min_value=0.0, max_value=200.0, value=38.00, step=0.1, format="%.2f")
    night_rate_cent = st.sidebar.number_input("Night Rate (Cent/kWh)", min_value=0.0, max_value=200.0, value=20.00, step=0.1, format="%.2f")
    peak_rate_cent = st.sidebar.number_input("Peak Rate (Cent/kWh)", min_value=0.0, max_value=200.0, value=46.00, step=0.1, format="%.2f")
    
    rates["day_rate"] = day_rate_cent / 100.0
    rates["night_rate"] = night_rate_cent / 100.0
    rates["peak_rate"] = peak_rate_cent / 100.0

# Fixed Standing and PSO Charges
st.sidebar.markdown("**Fixed & Standing Charges (€)**")
annual_standing = st.sidebar.number_input(
    "Annual Standing Charge (€)", 
    min_value=0.0, 
    max_value=1000.0, 
    value=270.45, 
    step=1.0,
    help="Your annual fixed provider fee. E.g., €270.45"
)
annual_pso = st.sidebar.number_input(
    "Annual PSO Levy (€)", 
    min_value=0.0, 
    max_value=200.0, 
    value=19.10, 
    step=0.5,
    help="Your annual Public Service Obligation levy fee. E.g., €19.10"
)
vat_rate = st.sidebar.number_input(
    "VAT Rate (%)", 
    min_value=0.0, 
    max_value=100.0, 
    value=9.0, 
    step=0.5
) / 100.0

# Combined daily standing charge calculations
daily_standing_rate = (annual_standing + annual_pso) / 365.25
rates["daily_standing_charge"] = daily_standing_rate
rates["vat_rate"] = vat_rate


# --- APPLIANCE DISAGGREGATION PROFILE SURVEY ---
st.sidebar.markdown("---")
st.sidebar.header("🔌 Household Profile Survey")
st.sidebar.info("Fill out your appliance survey below to map statistical patterns to specific category groups, similar to Electric Ireland's portal.")

house_profile = {
    "has_ev": st.sidebar.checkbox("Do you own an Electric Vehicle (EV)?", value=False, help="Checks for heavy nightly charge loads (~3-7kW continuous draw overnight)"),
    "electric_heating": st.sidebar.checkbox("Do you use electric space/water heating?", value=True, help="Allocates a larger signature weight to morning and winter spikes")
}


# --- LOAD DATA ---
df_raw = None
if data_option == "Upload My Own File" and uploaded_file is not None:
    with st.spinner("Processing your smart meter file..."):
        df_raw = parse_interval_csv(uploaded_file)
        if df_raw.empty:
            st.error("Failed to parse the file. Please check if the file format matches an interval-based CSV/TXT, or use our Sample Demo Data.")
        else:
            st.success("Successfully parsed uploaded file!")
elif data_option == "Use Sample Demo Data":
    df_raw = generate_demo_data()
    st.info("💡 Displaying mock analytical data. Switch the data source in the sidebar to visualize your own smart meter profile!")

# --- DISPLAY DASHBOARD ---
if df_raw is not None and not df_raw.empty:
    # 1. Apply costs & extract datetime characteristics
    df = apply_tariffs(df_raw, rates)
    df["reading_at"] = pd.to_datetime(df["reading_at"])
    df["year_month"] = df["reading_at"].dt.strftime("%Y-%m")
    df["day_name"] = df["reading_at"].dt.day_name()
    df["hour_of_day"] = df["reading_at"].dt.hour
    
    # --- MONTH FILTER SELECTOR ---
    st.sidebar.markdown("---")
    st.sidebar.header("📅 Filter Analysis View")
    available_months = sorted(list(df["year_month"].unique()))
    selected_month = st.sidebar.selectbox(
        "Select Target Period:",
        ["All Months"] + available_months,
        index=0,
        help="Filters the KPIs, projections, and heatmaps below to a single billing month."
    )
    
    # Filter dataset accordingly
    if selected_month != "All Months":
        df_filtered = df[df["year_month"] == selected_month].copy()
    else:
        df_filtered = df.copy()

    # Aggregate Standing Charge Costs (based on filtered days)
    unique_days = df_filtered["date_only"].nunique()
    total_standing_charge = unique_days * rates["daily_standing_charge"]
    
    # 2. Key Metrics Calculations (Filtered)
    total_kwh = df_filtered["estimated_kwh"].sum()
    usage_cost = df_filtered["cost"].sum()
    gross_cost = (usage_cost + total_standing_charge) * (1 + rates["vat_rate"])
    
    max_demand_row = df_filtered.loc[df_filtered["read_value_kw"].idxmax()]
    max_demand_kw = max_demand_row["read_value_kw"]
    max_demand_time = max_demand_row["reading_at"].strftime("%d %b %H:%M")
    
    avg_daily_kwh = total_kwh / unique_days
    avg_daily_cost = gross_cost / unique_days

    # Layout: Top Key KPI Cards
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(
            label="Total Consumption", 
            value=f"{total_kwh:,.1f} kWh", 
            help="Sum of all interval energy usage during the selected period."
        )
    with col2:
        st.metric(
            label="Total Cost (Inc. VAT & Standing)", 
            value=f"€{gross_cost:,.2f}", 
            help=f"Includes usage charges, daily standing charges + PSO Levy (totaling €{total_standing_charge:.2f}), and VAT ({rates['vat_rate']*100:.1f}%)."
        )
    with col3:
        st.metric(
            label="Average Daily Cost", 
            value=f"€{avg_daily_cost:.2f}/day",
            help="Total calculated cost divided by number of unique days in the selected period."
        )
    with col4:
        st.metric(
            label="Peak Power Demand", 
            value=f"{max_demand_kw:.2f} kW", 
            delta=f"at {max_demand_time}",
            delta_color="off"
        )
    
    st.markdown("---")
    
    # TAB VIEW FOR SECTIONS
    tab1, tab2, tab3, tab4 = st.tabs([
        "📊 Usage & Cost Projections", 
        "⏰ Hourly & Peak Analysis", 
        "🔌 Appliance Breakdown", 
        "📅 Daily Patterns & Heatmaps"
    ])
    
    # --- TAB 1: MONTHLY & PROJECTIONS ---
    with tab1:
        current_year_month = datetime.now().strftime("%Y-%m")
        
        # Scenario A: ALL MONTHS Selected -> Show monthly comparison bars
        if selected_month == "All Months":
            st.subheader("Monthly Usage, Actual Costs & Projections")
            
            # Monthly grouping
            monthly_summary = df.groupby("year_month").agg(
                total_kwh=("estimated_kwh", "sum"),
                usage_cost=("cost", "sum"),
                days_in_dataset=("date_only", "nunique")
            ).reset_index()
            
            # Calculate standing charges and VAT per month
            monthly_summary["standing_charges"] = monthly_summary["days_in_dataset"] * rates["daily_standing_charge"]
            monthly_summary["total_cost_inc_vat"] = (monthly_summary["usage_cost"] + monthly_summary["standing_charges"]) * (1 + rates["vat_rate"])
            
            has_current_month = current_year_month in monthly_summary["year_month"].values
            
            projections_list = []
            for index, row in monthly_summary.iterrows():
                is_current = row["year_month"] == current_year_month
                days_tracked = row["days_in_dataset"]
                
                # Estimate remaining days in month
                year, month = map(int, row["year_month"].split("-"))
                next_month = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
                total_days_in_month = (next_month - datetime(year, month, 1)).days
                
                if is_current and days_tracked < total_days_in_month:
                    avg_kwh_per_day = row["total_kwh"] / days_tracked
                    avg_cost_per_day = row["total_cost_inc_vat"] / days_tracked
                    
                    projected_kwh = avg_kwh_per_day * total_days_in_month
                    projected_cost = avg_cost_per_day * total_days_in_month
                    
                    projections_list.append({
                        "Month": row["year_month"],
                        "Status": "Projected (Full Month)",
                        "Consumption (kWh)": projected_kwh,
                        "Total Cost (€)": projected_cost
                    })
                    projections_list.append({
                        "Month": row["year_month"],
                        "Status": "Actual (To-Date)",
                        "Consumption (kWh)": row["total_kwh"],
                        "Total Cost (€)": row["total_cost_inc_vat"]
                    })
                else:
                    projections_list.append({
                        "Month": row["year_month"],
                        "Status": "Actual (Complete)",
                        "Consumption (kWh)": row["total_kwh"],
                        "Total Cost (€)": row["total_cost_inc_vat"]
                    })
                    
            proj_df = pd.DataFrame(projections_list)
            
            # Render charts for Monthly
            m_col1, m_col2 = st.columns(2)
            
            with m_col1:
                st.markdown("#### Monthly Consumption (kWh)")
                fig_month_kwh = px.bar(
                    proj_df, 
                    x="Month", 
                    y="Consumption (kWh)", 
                    color="Status",
                    barmode="group",
                    color_discrete_map={
                        "Actual (Complete)": "#1E88E5",
                        "Actual (To-Date)": "#1565C0",
                        "Projected (Full Month)": "#90CAF9"
                    },
                    text_auto=".0f"
                )
                fig_month_kwh.update_layout(xaxis_title="Month", yaxis_title="kWh Used", legend_title="Usage Category")
                st.plotly_chart(fig_month_kwh, use_container_width=True)
                
            with m_col2:
                st.markdown("#### Monthly Cost (€)")
                fig_month_cost = px.bar(
                    proj_df, 
                    x="Month", 
                    y="Total Cost (€)", 
                    color="Status",
                    barmode="group",
                    color_discrete_map={
                        "Actual (Complete)": "#2E7D32",
                        "Actual (To-Date)": "#1B5E20",
                        "Projected (Full Month)": "#A5D6A7"
                    },
                    text_auto=".2f"
                )
                fig_month_cost.update_layout(xaxis_title="Month", yaxis_title="Total Bill (€)", legend_title="Cost Status")
                st.plotly_chart(fig_month_cost, use_container_width=True)
                
            # Explanatory projection text card
            if has_current_month:
                this_month_proj = proj_df[(proj_df["Month"] == current_year_month) & (proj_df["Status"] == "Projected (Full Month)")]
                this_month_act = proj_df[(proj_df["Month"] == current_year_month) & (proj_df["Status"] == "Actual (To-Date)")]
                
                if not this_month_proj.empty and not this_month_act.empty:
                    proj_val = this_month_proj["Total Cost (€)"].values[0]
                    act_val = this_month_act["Total Cost (€)"].values[0]
                    days_elapsed = monthly_summary[monthly_summary["year_month"] == current_year_month]["days_in_dataset"].values[0]
                    
                    st.info(
                        f"🔮 **Current Month Projection ({current_year_month}):** Based on the first **{days_elapsed} days** of this month, "
                        f"your actual usage cost so far is **€{act_val:.2f}**. "
                        f"At your current rate of consumption, we project your final bill for this month to reach **€{proj_val:.2f}**."
                    )
        
        # Scenario B: SPECIFIC MONTH SELECTED -> Zoom into Daily Granular View for that Month
        else:
            st.subheader(f"Daily Details for Selected Month: {selected_month}")
            
            daily_summary = df_filtered.groupby("date_only").agg(
                total_kwh=("estimated_kwh", "sum"),
                usage_cost=("cost", "sum")
            ).reset_index()
            
            # Daily standing charge + VAT application
            daily_summary["total_cost_inc_vat"] = (daily_summary["usage_cost"] + rates["daily_standing_charge"]) * (1 + rates["vat_rate"])
            
            m_col1, m_col2 = st.columns(2)
            
            with m_col1:
                st.markdown(f"#### Daily Consumption in {selected_month} (kWh)")
                fig_daily_kwh = px.bar(
                    daily_summary,
                    x="date_only",
                    y="total_kwh",
                    color_discrete_sequence=["#1E88E5"]
                )
                fig_daily_kwh.update_layout(xaxis_title="Date", yaxis_title="Daily kWh")
                st.plotly_chart(fig_daily_kwh, use_container_width=True)
                
            with m_col2:
                st.markdown(f"#### Daily Cost in {selected_month} (€)")
                fig_daily_cost = px.bar(
                    daily_summary,
                    x="date_only",
                    y="total_cost_inc_vat",
                    color_discrete_sequence=["#2E7D32"]
                )
                fig_daily_cost.update_layout(xaxis_title="Date", yaxis_title="Daily Cost Inc. VAT & Standing (€)")
                st.plotly_chart(fig_daily_cost, use_container_width=True)
            
            # If the selected month is the active current month, offer daily extrapolation card
            if selected_month == current_year_month:
                days_elapsed = daily_summary["date_only"].nunique()
                year, month = map(int, selected_month.split("-"))
                next_month = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
                total_days_in_month = (next_month - datetime(year, month, 1)).days
                
                if days_elapsed < total_days_in_month:
                    avg_daily_act_kwh = total_kwh / days_elapsed
                    avg_daily_act_cost = gross_cost / days_elapsed
                    
                    proj_full_month_kwh = avg_daily_act_kwh * total_days_in_month
                    proj_full_month_cost = avg_daily_act_cost * total_days_in_month
                    
                    st.info(
                        f"🔮 **Projection for Current Month ({selected_month}):** Based on **{days_elapsed} elapsed days** of this month:\n"
                        f"- **Current Accumulation:** {total_kwh:,.1f} kWh consumed with a total cost of **€{gross_cost:.2f}**.\n"
                        f"- **Forecasted Month-End Usage:** **{proj_full_month_kwh:,.1f} kWh**.\n"
                        f"- **Forecasted Month-End Cost:** **€{proj_full_month_cost:.2f}**."
                    )

    # --- TAB 2: HOURLY & PEAK ANALYSIS ---
    with tab2:
        st.subheader("Usage Profile by Hour of Day & Weekday")
        
        # Diurnal (hourly) usage profile (Filtered)
        hourly_summary = df_filtered.groupby(["hour_of_day"]).agg(
            avg_kwh=("estimated_kwh", "mean"),
            total_kwh=("estimated_kwh", "sum")
        ).reset_index()
        
        # Weekday vs Weekend Average profile (Filtered)
        df_filtered["day_type"] = np.where(df_filtered["reading_at"].dt.dayofweek < 5, "Weekday", "Weekend")
        hourly_daytype_summary = df_filtered.groupby(["hour_of_day", "day_type"]).agg(
            avg_kwh=("estimated_kwh", "mean")
        ).reset_index()
        
        h_col1, h_col2 = st.columns(2)
        
        with h_col1:
            st.markdown("#### Average Energy Profile by Hour of Day")
            fig_hourly = px.line(
                hourly_daytype_summary, 
                x="hour_of_day", 
                y="avg_kwh", 
                color="day_type",
                markers=True,
                color_discrete_sequence=["#FF7043", "#26A69A"]
            )
            fig_hourly.update_layout(
                xaxis=dict(tickmode="linear", tick0=0, dtick=2),
                xaxis_title="Hour of Day (24h)",
                yaxis_title="Average Usage (kWh)",
                legend_title="Day Type"
            )
            st.plotly_chart(fig_hourly, use_container_width=True)
            
        with h_col2:
            st.markdown("#### Consumption Share by Time Window")
            df_temp = df_filtered.copy()
            conditions_temp = [
                (df_temp["hour_of_day"] >= 23) | (df_temp["hour_of_day"] < 8),
                (df_temp["hour_of_day"] >= 17) & (df_temp["hour_of_day"] < 19)
            ]
            df_temp["virtual_band"] = np.select(conditions_temp, ["Night Time", "Peak Time (17-19)"], default="Standard Day")
            
            temp_breakdown = df_temp.groupby("virtual_band").agg(
                total_kwh=("estimated_kwh", "sum")
            ).reset_index()
            
            fig_pie = px.pie(
                temp_breakdown, 
                values="total_kwh", 
                names="virtual_band",
                color="virtual_band",
                color_discrete_map={"Standard Day": "#FFCA28", "Night Time": "#5C6BC0", "Peak Time (17-19)": "#EF5350"},
                hole=0.4
            )
            fig_pie.update_traces(textinfo="percent+label")
            fig_pie.update_layout(legend_title="Time Window")
            st.plotly_chart(fig_pie, use_container_width=True)
            
        st.markdown("---")
        st.markdown("#### ⚡ Demand Shift & Load Insights")
        p_col1, p_col2 = st.columns(2)
        
        with p_col1:
            peak_only = df_filtered[(df_filtered["hour_of_day"] >= 17) & (df_filtered["hour_of_day"] < 19)]
            avg_peak_load = peak_only["read_value_kw"].mean() if not peak_only.empty else 0.0
            st.markdown(
                f"""
                * **Peak Period (17:00 - 19:00):** Your average demand during evening peak window is **{avg_peak_load:.2f} kW**.
                * **Bill-Saving Insights:** Even though you are on a **24-Hour Flat Tariff ({rates.get('flat_rate', 0)*100:.2f} c/kWh)**, you can use these charts to simulate potential savings if you were to shift major appliances to night hours and switch to a Time-of-Use smart tariff.
                """
            )
            
        with p_col2:
            top_peaks = df_filtered.sort_values(by="read_value_kw", ascending=False).head(5)
            st.markdown("**Top 5 Single Highest Appliance Spikes Recorded:**")
            for _, r in top_peaks.iterrows():
                st.write(f"- 🔴 **{r['read_value_kw']:.2f} kW** on {r['reading_at'].strftime('%A, %d %b %Y at %H:%M')}")


    # --- TAB 3: APPLIANCE BREAKDOWN ---
    with tab4:
        st.write("") # Kept intact as Tab 4 for formatting, but actual view will render under Appliance Breakdown below.

    with tab3:
        st.subheader("🔌 Replicated Appliance Disaggregation")
        st.markdown(
            "Below is the estimated category breakdown of your smart meter data using a statistical baseline and time-of-day heuristic engine. "
            "Update your household profile questions in the sidebar to refine this output."
        )
        
        # Run Heuristics Engine
        dis_df = disaggregate_appliances(df_filtered, house_profile)
        
        # Sum up disaggregated columns
        app_cols = {
            "app_always_on": "Always On (Baseload)",
            "app_heating": "Space & Water Heating",
            "app_cooking": "Cooking & Kitchen",
            "app_laundry": "Laundry & Dishwasher",
            "app_entertainment": "Entertainment & Lighting",
            "app_ev": "Electric Vehicle (EV)",
            "app_misc": "Other / Unclassified"
        }
        
        # Calculate sums and costs per appliance
        app_data = []
        for col_name, label in app_cols.items():
            if col_name == "app_ev" and not house_profile["has_ev"]:
                continue
                
            total_app_kwh = dis_df[col_name].sum()
            
            # Map cost proportionally based on interval-specific tariff rates
            # This handles cases where certain appliances run heavily on flat vs. time-of-use night rates
            if "cost" in dis_df.columns:
                total_app_cost = (dis_df[col_name] * dis_df["tariff_rate"]).sum()
            else:
                total_app_cost = total_app_kwh * (rates.get("flat_rate") if rates["type"] == "flat" else rates.get("day_rate", 0.30))
                
            # Apply proportional standing charge and VAT
            app_share = total_app_kwh / total_kwh if total_kwh > 0 else 0
            allocated_standing_vat = (total_standing_charge * app_share) * (1 + rates["vat_rate"])
            total_app_cost_inc_vat = (total_app_cost * (1 + rates["vat_rate"])) + allocated_standing_vat
            
            app_data.append({
                "Appliance Category": label,
                "Consumption (kWh)": total_app_kwh,
                "Estimated Cost (€)": total_app_cost_inc_vat
            })
            
        app_summary = pd.DataFrame(app_data)
        
        # Render disaggregation visualizations
        app_col1, app_col2 = st.columns(2)
        
        with app_col1:
            st.markdown("#### Appliance Energy Share (%)")
            fig_app_pie = px.pie(
                app_summary,
                values="Consumption (kWh)",
                names="Appliance Category",
                color="Appliance Category",
                color_discrete_sequence=px.colors.qualitative.Pastel,
                hole=0.4
            )
            fig_app_pie.update_traces(textinfo="percent+label")
            st.plotly_chart(fig_app_pie, use_container_width=True)
            
        with app_col2:
            st.markdown("#### Estimated Cost Breakdown (€)")
            fig_app_cost = px.bar(
                app_summary.sort_values(by="Estimated_Cost", s=False) if "Estimated_Cost" in app_summary else app_summary,
                x="Appliance Category",
                y="Estimated Cost (€)",
                text_auto=".2f",
                color="Appliance Category",
                color_discrete_sequence=px.colors.qualitative.Pastel
            )
            fig_app_cost.update_layout(xaxis_title="Category", yaxis_title="Total Bill Portion (€)", showlegend=False)
            st.plotly_chart(fig_app_cost, use_container_width=True)
            
        st.markdown("---")
        st.markdown("#### 🕒 Average Diurnal Appliance Timeline")
        st.markdown("This chart displays how your dynamic appliance loads shift and overlap throughout an average 24-hour day in your selected dataset.")
        
        # Group by hour of day for stacked timeline
        timeline_cols = list(app_cols.keys())
        if not house_profile["has_ev"]:
            timeline_cols.remove("app_ev")
            
        hourly_timeline = dis_df.groupby("hour_of_day")[timeline_cols].mean().reset_index()
        hourly_timeline = hourly_timeline.rename(columns=app_cols)
        
        # Melt dataframe for easy stacked charting
        melted_timeline = hourly_timeline.melt(
            id_vars=["hour_of_day"],
            value_vars=list(hourly_timeline.columns[1:]),
            var_name="Appliance Category",
            value_name="Average Hourly Consumption (kWh)"
        )
        
        fig_timeline = px.bar(
            melted_timeline,
            x="hour_of_day",
            y="Average Hourly Consumption (kWh)",
            color="Appliance Category",
            color_discrete_sequence=px.colors.qualitative.Pastel
        )
        fig_timeline.update_layout(
            xaxis=dict(tickmode="linear", tick0=0, dtick=1),
            xaxis_title="Hour of Day (24h Stacked View)",
            yaxis_title="Avg Consumption per Hour (kWh)"
        )
        st.plotly_chart(fig_timeline, use_container_width=True)


    # --- TAB 4: DAILY PATTERNS & HEATMAPS ---
    with tab4:
        st.subheader("Daily Trends and Heatmap Distribution")
        
        # Line plot of daily usage (Filtered)
        daily_kwh = df_filtered.groupby("date_only").agg(
            total_kwh=("estimated_kwh", "sum"),
            cost_inc_standing=("cost", lambda x: (x.sum() + rates["daily_standing_charge"]) * (1 + rates["vat_rate"]))
        ).reset_index()
        
        fig_daily = px.area(
            daily_kwh, 
            x="date_only", 
            y="total_kwh", 
            line_shape="spline",
            color_discrete_sequence=["#26A69A"]
        )
        fig_daily.update_layout(
            xaxis_title="Date",
            yaxis_title="Daily Consumption (kWh)",
            hovermode="x unified"
        )
        st.plotly_chart(fig_daily, use_container_width=True)
        
        # 2D Heatmap of hour vs day of week (Filtered)
        st.markdown("#### Hourly vs Day-of-Week Intensity Heatmap")
        heatmap_data = df_filtered.groupby(["day_name", "hour_of_day"])["estimated_kwh"].mean().reset_index()
        
        # Sort days correctly
        day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        heatmap_pivot = heatmap_data.pivot(index="day_name", columns="hour_of_day", values="estimated_kwh").reindex(day_order)
        
        fig_heatmap = px.imshow(
            heatmap_pivot,
            labels=dict(x="Hour of Day", y="Day of Week", color="Avg kWh"),
            x=list(range(24)),
            y=day_order,
            color_continuous_scale="Viridis"
        )
        fig_heatmap.update_layout(xaxis=dict(tickmode="linear", tick0=0, dtick=1))
        st.plotly_chart(fig_heatmap, use_container_width=True)

else:
    st.warning("Please upload a valid smart meter interval data file or select 'Use Sample Demo Data' in the sidebar to populate the dashboard!")
