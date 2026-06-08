import io
import os
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

st.markdown("""
<style>
    .metric-container {
        background-color: #f8f9fa;
        border-radius: 12px;
        padding: 20px;
        border: 1px solid #e9ecef;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
        text-align: center;
    }
    .metric-value {
        font-size: 2rem;
        font-weight: 700;
        margin-bottom: 5px;
    }
    .metric-label {
        font-size: 0.9rem;
        color: #6c757d;
        font-weight: 500;
    }
    .metric-badge {
        display: inline-block;
        padding: 4px 8px;
        font-size: 0.75rem;
        font-weight: 700;
        border-radius: 20px;
        margin-top: 8px;
    }
    .badge-actual    { background-color: #e3f2fd; color: #0d47a1; }
    .badge-projected { background-color: #efebe9; color: #4e342e; }
    .badge-warning   { background-color: #fff3e0; color: #e65100; }
    .badge-success   { background-color: #e8f5e9; color: #1b5e20; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# PARSING HELPERS
# ---------------------------------------------------------------------------

def _kwt_to_kwh(series: pd.Series) -> pd.Series:
    """
    ESB HDF files store half-hour energy in kWt (kilo-watt-thirties).
    1 kWt = energy consumed in one 30-minute slot measured at the meter.
    Billing kWh = kWt / 2  (because 30 min = 0.5 h, and kWh = kW * h).
    This is the SINGLE canonical conversion point used everywhere in the app.
    """
    return pd.to_numeric(series, errors="coerce") / 2.0


def parse_wide_csv(raw: str) -> pd.DataFrame:
    """
    Parses the wide daily-pivot format exported by some ESB tools:
      MPRN, Meter Serial Number, Date, 00:00:00, 00:30:00, ..., 23:30:00
    Each row = one day; 48 time columns hold kWt values for each half-hour slot.
    Returns a long-format DataFrame with estimated_kwh = kWt / 2.
    """
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

        # ── KEY FIX: raw values are kWt; divide by 2 to get billing kWh ──
        melted["estimated_kwh"] = _kwt_to_kwh(melted["_raw_kwt"])
        melted = melted.dropna(subset=["estimated_kwh"])

        melted["reading_at"] = pd.to_datetime(
            melted[date_col].astype(str) + " " + melted["time_slot"].str[:5],
            errors="coerce"
        )
        melted = melted.dropna(subset=["reading_at"]).sort_values("reading_at").reset_index(drop=True)

        melted["mprn"]         = melted[mprn_col].astype(str).str.strip()
        melted["meter_serial"] = melted[serial_col].astype(str).str.strip() if serial_col else ""
        # Instantaneous power (kW) = kWh / 0.5h  =  estimated_kwh * 2
        melted["read_value_kw"] = melted["estimated_kwh"] * 2.0
        melted["date_only"]     = melted["reading_at"].dt.date

        return melted[["mprn", "meter_serial", "reading_at", "read_value_kw", "estimated_kwh", "date_only"]]
    except Exception:
        return pd.DataFrame()


def parse_interval_csv(uploaded_file) -> pd.DataFrame:
    """
    Parses electricity smart meter interval data files (ESB HDF files).
    All raw kWt values are converted to kWh (÷2) at ingest.
    The returned DataFrame's `estimated_kwh` column always contains billing kWh.
    """
    if hasattr(uploaded_file, "read"):
        raw_bytes = uploaded_file.read()
    else:
        raw_bytes = uploaded_file

    if isinstance(raw_bytes, bytes):
        raw = raw_bytes.decode("utf-8-sig", errors="ignore")
    else:
        raw = str(raw_bytes)

    # ── Wide daily-pivot format ──
    first_line = raw.splitlines()[0] if raw.splitlines() else ""
    if re.search(r"(?i)\bdate\b", first_line) and re.search(r"\d{2}:\d{2}:\d{2}", raw):
        wide_result = parse_wide_csv(raw)
        if not wide_result.empty:
            return wide_result

    def finalize(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame()

        value_col = "read_value" if "read_value" in df.columns else "read_value_kw"
        df = df.dropna(subset=["reading_at", value_col]).copy()

        df["_raw_numeric"] = pd.to_numeric(df[value_col], errors="coerce")
        df["reading_at"]   = pd.to_datetime(df["reading_at"], errors="coerce")
        df = df.dropna(subset=["reading_at", "_raw_numeric"]).sort_values("reading_at")

        if df.empty:
            return pd.DataFrame()

        # Detect whether values are already in kWh or still in kWt.
        # ESB narrow-format files label the column "Active Import Interval (kWh)"
        # even though the values are kWt.  We divide by 2 unconditionally because
        # ALL ESB 30-min interval values are kWt regardless of the column label.
        df["estimated_kwh"] = df["_raw_numeric"] / 2.0

        # Instantaneous power = kWh / 0.5 h
        df["read_value_kw"] = df["estimated_kwh"] * 2.0
        df["date_only"]     = df["reading_at"].dt.date

        for col in ["mprn", "meter_serial"]:
            if col not in df.columns:
                df[col] = ""

        return df[["mprn", "meter_serial", "reading_at", "read_value_kw", "estimated_kwh", "date_only"]]

    # ── CSV mode ──
    try:
        lines      = raw.splitlines()
        header_idx = -1
        for idx, line in enumerate(lines):
            line_lower = line.lower()
            if "mprn" in line_lower and any(k in line_lower for k in ["date", "time", "value"]):
                header_idx = idx
                break

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
                "read_type":   csv_df[type_col].astype(str).str.strip() if type_col else "Active Import Interval (kWh)",
                "reading_at":  pd.to_datetime(csv_df[date_col], errors="coerce"),
            })

            if type_col:
                df = df[
                    df["read_type"].str.contains(
                        r"Active Import Interval\s*(?:\((?:kW|kWh)\))?",
                        regex=True, na=False, case=False
                    )
                ]

            parsed = finalize(df)
            if not parsed.empty:
                return parsed
    except Exception:
        pass

    # ── Regex fallback ──
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
        date_str  = m.group("date").replace("/", "-")
        time_text = m.group("time").replace(":", "")[:4]
        reading_at = pd.to_datetime(f"{date_str} {time_text}", format="%d-%m-%Y %H%M", errors="coerce")
        rows.append({
            "mprn":          m.group("mprn"),
            "meter_serial":  m.group("serial"),
            "read_value":    float(m.group("value")),
            "read_type":     m.group("read_type"),
            "reading_at":    reading_at,
        })

    if not rows:
        fallback_lines = []
        for line in raw.splitlines():
            if "Active Import" not in line:
                continue
            m = re.search(pattern, line)
            if m:
                date_str  = m.group("date").replace("/", "-")
                time_text = m.group("time").replace(":", "")[:4]
                fallback_lines.append({
                    "mprn":         m.group("mprn"),
                    "meter_serial": m.group("serial"),
                    "read_value":   float(m.group("value")),
                    "read_type":    m.group("read_type"),
                    "reading_at":   pd.to_datetime(
                        f"{date_str} {time_text}", format="%d-%m-%Y %H%M", errors="coerce"
                    ),
                })
        rows = fallback_lines

    if not rows:
        return pd.DataFrame()

    return finalize(pd.DataFrame(rows))


# ---------------------------------------------------------------------------
# DEMO DATA GENERATOR
# ---------------------------------------------------------------------------

def generate_demo_data() -> pd.DataFrame:
    """
    Generates synthetic half-hourly smart meter interval data.
    Values are stored as billing kWh (already ÷2 applied at generation time).
    """
    np.random.seed(42)
    end_date   = datetime.now()
    start_date = end_date - timedelta(days=90)
    date_range = pd.date_range(start=start_date, end=end_date, freq="30min")

    rows = []
    for dt in date_range:
        hour    = dt.hour
        weekday = dt.weekday()

        base     = 0.15 + np.random.normal(0, 0.02)
        if 8 <= hour < 17:
            activity = 0.25 + np.random.normal(0, 0.05)
        elif 17 <= hour < 19:
            activity = 0.85 + np.random.normal(0, 0.15)
        elif 19 <= hour < 23:
            activity = 0.45 + np.random.normal(0, 0.08)
        else:
            activity = 0.05 + np.random.normal(0, 0.01)

        if weekday >= 5:
            activity *= 1.25
        spike = 1.8 if (hour == 8 or hour == 18) and np.random.rand() > 0.7 else 0.0

        # estimated_kwh is already in billing kWh (÷2 already baked in here)
        est_kwh = max(0.01, base + activity + spike)

        rows.append({
            "mprn":          "10303339574",
            "meter_serial":  "000000000024049722",
            "reading_at":    dt,
            "read_value_kw": est_kwh * 2.0,   # instantaneous kW
            "estimated_kwh": est_kwh,           # billing kWh
            "date_only":     dt.date()
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# TARIFF ENGINE
# ---------------------------------------------------------------------------

def apply_tariffs(df: pd.DataFrame, rates: dict) -> pd.DataFrame:
    """
    Applies tariff bands to each reading.
    `df['estimated_kwh']` must already be in billing kWh (kWt ÷ 2).
    Cost = estimated_kwh × tariff_rate  (€/kWh).
    """
    df        = df.copy()
    df["hour"] = df["reading_at"].dt.hour

    if rates["type"] == "flat":
        df["tariff_band"] = "24hr Flat"
        df["tariff_rate"] = rates["flat_rate"]
    else:
        conditions = [
            (df["hour"] >= 23) | (df["hour"] < 8),
            (df["hour"] >= 17) & (df["hour"] < 19),
        ]
        df["tariff_band"] = np.select(conditions, ["Night", "Peak"], default="Day")
        rate_map = {"Day": rates["day_rate"], "Night": rates["night_rate"], "Peak": rates["peak_rate"]}
        df["tariff_rate"] = df["tariff_band"].map(rate_map)

    df["cost"] = df["estimated_kwh"] * df["tariff_rate"]
    return df


# ---------------------------------------------------------------------------
# APPLIANCE DISAGGREGATION
# ---------------------------------------------------------------------------

def disaggregate_appliances(df: pd.DataFrame, house_profile: dict) -> pd.DataFrame:
    df = df.copy()

    daily_min          = df.groupby("date_only")["estimated_kwh"].transform("min")
    df["app_always_on"] = np.minimum(daily_min, 0.25)
    df["active_kwh"]   = np.maximum(0.0, df["estimated_kwh"] - df["app_always_on"])

    for col in ["app_ev", "app_heating", "app_cooking", "app_laundry", "app_entertainment", "app_misc"]:
        df[col] = 0.0

    df["hour_float"]  = df["reading_at"].dt.hour + df["reading_at"].dt.minute / 60.0
    df["is_weekend"]  = df["reading_at"].dt.dayofweek >= 5
    df["active_prev"] = df["active_kwh"].shift(1).fillna(0.0)
    df["active_next"] = df["active_kwh"].shift(-1).fillna(0.0)

    for idx, row in df.iterrows():
        active = row["active_kwh"]
        if active <= 0:
            continue
        hr = row["hour_float"]

        if house_profile["has_ev"] and (0.0 <= hr < 6.0) and active > 1.5:
            ev_draw = min(active, 3.7)
            df.at[idx, "app_ev"] = ev_draw
            active -= ev_draw

        if active > 0:
            is_sustained_heavy = (active > 0.45) and ((row["active_prev"] > 0.40) or (row["active_next"] > 0.40))
            if is_sustained_heavy and (7.0 <= hr < 23.0):
                dryer_draw = active * 0.85
                df.at[idx, "app_laundry"] = dryer_draw
                active -= dryer_draw

        if active > 0:
            is_heating_window = (5.5 <= hr < 8.5) or (23.0 <= hr) or (0.0 <= hr < 2.0)
            if is_heating_window:
                heating_ratio = 0.6 if house_profile["electric_heating"] else 0.15
                heat_draw = active * heating_ratio
                df.at[idx, "app_heating"] = heat_draw
                active -= heat_draw

        if active > 0:
            is_cooking_window = (7.0 <= hr < 9.0) or (12.0 <= hr < 14.0) or (16.5 <= hr < 19.5)
            if is_cooking_window:
                cooking_ratio = 0.55 if active > 0.15 else 0.3
                cooking_draw  = active * cooking_ratio
                df.at[idx, "app_cooking"] = cooking_draw
                active -= cooking_draw

        if active > 0:
            is_chore_window = (
                (9.0 <= hr < 12.0) or (14.0 <= hr < 16.5) or
                (19.0 <= hr < 22.0) or (row["is_weekend"] and 9.0 <= hr < 18.0)
            )
            if is_chore_window:
                laundry_ratio = 0.5 if active > 0.2 else 0.25
                laundry_draw  = active * laundry_ratio
                df.at[idx, "app_laundry"] += laundry_draw
                active -= laundry_draw

        if active > 0 and 18.0 <= hr < 23.5:
            ent_draw = active * 0.7
            df.at[idx, "app_entertainment"] = ent_draw
            active -= ent_draw

        if active > 0:
            df.at[idx, "app_misc"] = active

    return df


# ---------------------------------------------------------------------------
# STREAMLIT UI
# ---------------------------------------------------------------------------

st.title("⚡ Smart Meter Analytics & Cost Dashboard")
st.markdown("Electricity usage analytics with correct ESB kWt → kWh conversion, cost forecasting and appliance profiling.")

# ── Sidebar: Data Source ──
st.sidebar.header("📁 Data Source")
data_option  = st.sidebar.radio("Choose Data Input:", ["Upload My Own File", "Use Sample Demo Data"])
uploaded_file = None
if data_option == "Upload My Own File":
    uploaded_file = st.sidebar.file_uploader(
        "Upload Smart Meter File (CSV or TXT)",
        type=["csv", "txt"],
        help="Upload standard ESB Smart Meter HDF files (kWt values are automatically converted to kWh)"
    )

# ── Sidebar: Tariff Settings ──
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
        "Flat Rate (Cent / kWh)", min_value=0.0, max_value=200.0,
        value=26.41, step=0.01, format="%.2f",
        help="Your flat electricity rate in cent/kWh."
    )
    rates["flat_rate"] = flat_rate_cent / 100.0
else:
    rates["type"]       = "smart"
    day_rate_cent   = st.sidebar.number_input("Day Rate (Cent/kWh)",   min_value=0.0, max_value=200.0, value=28.20, step=0.01, format="%.2f")
    night_rate_cent = st.sidebar.number_input("Night Rate (Cent/kWh)", min_value=0.0, max_value=200.0, value=15.10, step=0.01, format="%.2f")
    peak_rate_cent  = st.sidebar.number_input("Peak Rate (Cent/kWh)",  min_value=0.0, max_value=200.0, value=35.40, step=0.01, format="%.2f")
    rates["day_rate"]   = day_rate_cent   / 100.0
    rates["night_rate"] = night_rate_cent / 100.0
    rates["peak_rate"]  = peak_rate_cent  / 100.0

st.sidebar.markdown("**Fixed & Standing Charges (€)**")
annual_standing = st.sidebar.number_input("Annual Standing Charge (€)", min_value=0.0, max_value=1000.0, value=270.45, step=0.01)
annual_pso      = st.sidebar.number_input("Annual PSO Levy (€)",         min_value=0.0, max_value=200.0,  value=19.10,  step=0.01)
vat_rate        = st.sidebar.number_input("VAT Rate (%)",                min_value=0.0, max_value=100.0,  value=9.0,    step=0.5) / 100.0

daily_standing_rate           = (annual_standing + annual_pso) / 365.25
rates["daily_standing_charge"] = daily_standing_rate
rates["vat_rate"]              = vat_rate

# ── Sidebar: Household Profile ──
st.sidebar.markdown("---")
st.sidebar.header("🔌 Household Profile Survey")
house_profile = {
    "has_ev":           st.sidebar.checkbox("Do you own an Electric Vehicle (EV)?",          value=False),
    "electric_heating": st.sidebar.checkbox("Do you use electric space/water heating?",      value=True)
}

# ── Load Data ──
df_raw = None
if data_option == "Upload My Own File" and uploaded_file is not None:
    with st.spinner("Processing uploaded smart meter file…"):
        df_raw = parse_interval_csv(uploaded_file)
        if df_raw.empty:
            st.error("Failed to parse file. Please verify the CSV structure.")
elif data_option == "Use Sample Demo Data":
    df_raw = generate_demo_data()
    st.sidebar.info("💡 Using synthetic demo data.")

# ---------------------------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------------------------

if df_raw is not None and not df_raw.empty:
    # All downstream work uses estimated_kwh which is already in billing kWh.
    df = apply_tariffs(df_raw, rates)
    df["reading_at"]  = pd.to_datetime(df["reading_at"])
    df["year_month"]  = df["reading_at"].dt.strftime("%Y-%m")
    df["day_name"]    = df["reading_at"].dt.day_name()
    df["hour_of_day"] = df["reading_at"].dt.hour

    # ── Month Filter ──
    st.sidebar.markdown("---")
    st.sidebar.header("📅 Filter Analysis View")
    available_months = sorted(df["year_month"].unique().tolist())
    selected_month   = st.sidebar.selectbox(
        "Select Target Period:", ["All Months"] + available_months, index=0
    )

    df_filtered  = df[df["year_month"] == selected_month].copy() if selected_month != "All Months" else df.copy()

    # ── Projection logic ──
    is_unfinished        = False
    proj_factor          = 1.0
    days_in_month        = 30
    days_elapsed         = df_filtered["date_only"].nunique()
    min_projection_days  = 7

    if selected_month != "All Months":
        try:
            y_val, m_val  = map(int, selected_month.split("-"))
            days_in_month = calendar.monthrange(y_val, m_val)[1]
            if days_elapsed < days_in_month:
                is_unfinished = True
                proj_factor   = (days_in_month / days_elapsed) if days_elapsed >= min_projection_days else 1.0
        except Exception:
            pass

    actual_kwh           = df_filtered["estimated_kwh"].sum()
    actual_usage_cost    = df_filtered["cost"].sum()
    actual_standing_pso  = days_elapsed * rates["daily_standing_charge"]
    actual_gross_cost    = (actual_usage_cost + actual_standing_pso) * (1 + rates["vat_rate"])

    projected_kwh         = actual_kwh        * proj_factor
    projected_usage_cost  = actual_usage_cost * proj_factor
    projected_standing_pso= days_in_month     * rates["daily_standing_charge"]
    projected_gross_cost  = (projected_usage_cost + projected_standing_pso) * (1 + rates["vat_rate"])

    max_demand_row  = df_filtered.loc[df_filtered["read_value_kw"].idxmax()]
    max_demand_kw   = max_demand_row["read_value_kw"]
    max_demand_time = max_demand_row["reading_at"].strftime("%d %b %H:%M")

    # ── Partial-month warning ──
    if is_unfinished:
        st.warning(
            f"⚠️ **{selected_month} is a Partial/Unfinished Month.** "
            f"Only **{days_elapsed} of {days_in_month} days** are recorded. "
            "Projections for the full month are highlighted in brown below."
        )

    # ── Top KPI Cards ──
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if is_unfinished and proj_factor > 1.0:
            st.markdown(f"""
            <div class="metric-container">
                <div class="metric-value" style="color:#4e342e;">{projected_kwh:,.1f} kWh</div>
                <div class="metric-label">Estimated Month-End Usage</div>
                <div class="metric-badge badge-projected">Actual to-date: {actual_kwh:,.1f} kWh</div>
            </div>""", unsafe_allow_html=True)
        elif is_unfinished:
            st.markdown(f"""
            <div class="metric-container">
                <div class="metric-value" style="color:#0d47a1;">{actual_kwh:,.1f} kWh</div>
                <div class="metric-label">Actual Usage To Date</div>
                <div class="metric-badge badge-warning">Projection needs ≥{min_projection_days} days</div>
            </div>""", unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div class="metric-container">
                <div class="metric-value" style="color:#0d47a1;">{actual_kwh:,.1f} kWh</div>
                <div class="metric-label">Total Consumption (kWh)</div>
                <div class="metric-badge badge-actual">Completed Period</div>
            </div>""", unsafe_allow_html=True)

    with col2:
        if is_unfinished and proj_factor > 1.0:
            st.markdown(f"""
            <div class="metric-container">
                <div class="metric-value" style="color:#4e342e;">€{projected_gross_cost:,.2f}</div>
                <div class="metric-label">Projected Month-End Bill</div>
                <div class="metric-badge badge-projected">Actual to-date: €{actual_gross_cost:,.2f}</div>
            </div>""", unsafe_allow_html=True)
        elif is_unfinished:
            st.markdown(f"""
            <div class="metric-container">
                <div class="metric-value" style="color:#1b5e20;">€{actual_gross_cost:,.2f}</div>
                <div class="metric-label">Actual Bill To Date</div>
                <div class="metric-badge badge-warning">Projection needs ≥{min_projection_days} days</div>
            </div>""", unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div class="metric-container">
                <div class="metric-value" style="color:#1b5e20;">€{actual_gross_cost:,.2f}</div>
                <div class="metric-label">Total Cost (Inc. VAT)</div>
                <div class="metric-badge badge-success">Completed Period</div>
            </div>""", unsafe_allow_html=True)

    with col3:
        avg_cost_day = actual_gross_cost / max(days_elapsed, 1)
        avg_kwh_day  = actual_kwh        / max(days_elapsed, 1)
        st.markdown(f"""
        <div class="metric-container">
            <div class="metric-value" style="color:#e65100;">€{avg_cost_day:.2f}/day</div>
            <div class="metric-label">Avg Daily Cost</div>
            <div class="metric-badge badge-warning">Avg: {avg_kwh_day:.2f} kWh/day</div>
        </div>""", unsafe_allow_html=True)

    with col4:
        st.markdown(f"""
        <div class="metric-container">
            <div class="metric-value" style="color:#37474f;">{max_demand_kw:.2f} kW</div>
            <div class="metric-label">Peak Power Demand</div>
            <div class="metric-badge badge-actual" style="background-color:#eceff1;color:#37474f;">
                Spike on {max_demand_time}
            </div>
        </div>""", unsafe_allow_html=True)

    st.markdown("---")

    # ==========================================
    # TABS
    # ==========================================
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "📈 Month-to-Month Overview",
        "📊 Usage & Cost Projections",
        "⏰ Hourly & Peak Analysis",
        "🔌 Appliance Breakdown",
        "🎯 Bill Saving Simulator",
        "🔍 Appliance Detective"
    ])

    # ── TAB 1: Month-to-Month Overview ──
    with tab1:
        st.subheader("🗓️ Month-to-Month Usage & Trend Overview")

        monthly_summary = df.groupby("year_month").agg(
            total_kwh    =("estimated_kwh", "sum"),
            usage_cost   =("cost",          "sum"),
            days_measured=("date_only",     "nunique")
        ).reset_index()

        monthly_summary["standing_charges"]    = monthly_summary["days_measured"] * rates["daily_standing_charge"]
        monthly_summary["total_cost_inc_vat"]  = (monthly_summary["usage_cost"] + monthly_summary["standing_charges"]) * (1 + rates["vat_rate"])
        monthly_summary["avg_daily_kwh"]       = monthly_summary["total_kwh"] / monthly_summary["days_measured"]
        monthly_summary["kwh_pct_change"]      = monthly_summary["total_kwh"].pct_change() * 100
        monthly_summary["cost_pct_change"]     = monthly_summary["total_cost_inc_vat"].pct_change() * 100
        monthly_summary["Status"]              = "Completed"

        for i, r in monthly_summary.iterrows():
            y_p, m_p = map(int, r["year_month"].split("-"))
            if r["days_measured"] < calendar.monthrange(y_p, m_p)[1]:
                monthly_summary.at[i, "Status"] = "Incomplete (Partial)"

        col_m1, col_m2 = st.columns(2)
        with col_m1:
            st.markdown("#### Monthly Consumption Trends")
            fig = px.line(monthly_summary, x="year_month", y="total_kwh",
                          text=monthly_summary["total_kwh"].round(1), markers=True,
                          color_discrete_sequence=["#1e88e5"])
            fig.update_traces(
                hovertemplate="<b>Month:</b> %{x}<br><b>Total:</b> %{y:,.1f} kWh<extra></extra>",
                hoverlabel=dict(bgcolor="#e3f2fd", font_size=16)
            )
            fig.update_layout(xaxis_title="Month", yaxis_title="Total kWh")
            st.plotly_chart(fig, use_container_width=True)

        with col_m2:
            st.markdown("#### Monthly Total Bills (Inc. Standing & VAT)")
            fig = px.bar(monthly_summary, x="year_month", y="total_cost_inc_vat",
                         color="Status",
                         color_discrete_map={"Completed": "#2e7d32", "Incomplete (Partial)": "#ef6c00"},
                         text=monthly_summary["total_cost_inc_vat"].map(lambda x: f"€{x:.2f}"))
            fig.update_traces(
                hovertemplate="<b>Month:</b> %{x}<br><b>Bill:</b> €%{y:.2f}<extra></extra>",
                hoverlabel=dict(bgcolor="#e8f5e9", font_size=16)
            )
            fig.update_layout(xaxis_title="Month", yaxis_title="Total Bill (€)")
            st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### Month-on-Month Financial & Usage Summary")
        grid_df = monthly_summary.copy()
        grid_df["total_kwh"]           = grid_df["total_kwh"].map(lambda x: f"{x:,.1f} kWh")
        grid_df["total_cost_inc_vat"]  = grid_df["total_cost_inc_vat"].map(lambda x: f"€{x:,.2f}")
        grid_df["avg_daily_kwh"]       = grid_df["avg_daily_kwh"].map(lambda x: f"{x:.2f} kWh/day")
        grid_df["kwh_pct_change"]      = grid_df["kwh_pct_change"].map(lambda x: f"{x:+.1f}%" if pd.notnull(x) else "-")
        grid_df["cost_pct_change"]     = grid_df["cost_pct_change"].map(lambda x: f"{x:+.1f}%" if pd.notnull(x) else "-")

        st.table(grid_df[[
            "year_month", "days_measured", "total_kwh", "total_cost_inc_vat",
            "avg_daily_kwh", "kwh_pct_change", "cost_pct_change", "Status"
        ]].rename(columns={
            "year_month": "Month", "days_measured": "Days Tracked",
            "total_kwh": "Usage (kWh)", "total_cost_inc_vat": "Total Cost",
            "avg_daily_kwh": "Avg Daily", "kwh_pct_change": "MoM Usage Δ",
            "cost_pct_change": "MoM Cost Δ"
        }))

    # ── TAB 2: Projections ──
    with tab2:
        if selected_month == "All Months":
            st.subheader("Monthly Usage & Projected Cost Outlook")

            projections_list = []
            for _, row in monthly_summary.iterrows():
                y_p, m_p = map(int, row["year_month"].split("-"))
                tot_days = calendar.monthrange(y_p, m_p)[1]
                if row["days_measured"] < tot_days:
                    f = tot_days / row["days_measured"]
                    projections_list += [
                        {"Month": row["year_month"], "Status": "Projected (Full Month)",
                         "Consumption (kWh)": row["total_kwh"] * f,
                         "Total Cost (€)":    row["total_cost_inc_vat"] * f},
                        {"Month": row["year_month"], "Status": "Actual (To-Date)",
                         "Consumption (kWh)": row["total_kwh"],
                         "Total Cost (€)":    row["total_cost_inc_vat"]},
                    ]
                else:
                    projections_list.append({
                        "Month": row["year_month"], "Status": "Actual (Complete)",
                        "Consumption (kWh)": row["total_kwh"],
                        "Total Cost (€)":    row["total_cost_inc_vat"]
                    })

            proj_df = pd.DataFrame(projections_list)
            col_p1, col_p2 = st.columns(2)
            cmap = {"Actual (Complete)": "#1e88e5", "Actual (To-Date)": "#1565c0", "Projected (Full Month)": "#a1887f"}

            with col_p1:
                fig = px.bar(proj_df, x="Month", y="Consumption (kWh)", color="Status",
                             barmode="group", color_discrete_map=cmap)
                fig.update_traces(hovertemplate="<b>Month:</b> %{x}<br><b>Usage:</b> %{y:,.1f} kWh<extra></extra>")
                st.plotly_chart(fig, use_container_width=True)

            with col_p2:
                cmap2 = {"Actual (Complete)": "#2e7d32", "Actual (To-Date)": "#1b5e20", "Projected (Full Month)": "#8d6e63"}
                fig = px.bar(proj_df, x="Month", y="Total Cost (€)", color="Status",
                             barmode="group", color_discrete_map=cmap2)
                fig.update_traces(hovertemplate="<b>Month:</b> %{x}<br><b>Cost:</b> €%{y:.2f}<extra></extra>")
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.subheader(f"📅 Granular Daily Breakdowns for {selected_month}")
            daily_summary = df_filtered.groupby("date_only").agg(
                total_kwh  =("estimated_kwh", "sum"),
                usage_cost =("cost",          "sum")
            ).reset_index()
            daily_summary["total_cost"] = (daily_summary["usage_cost"] + rates["daily_standing_charge"]) * (1 + rates["vat_rate"])

            col_d1, col_d2 = st.columns(2)
            with col_d1:
                st.markdown("#### Daily Energy Consumed (kWh)")
                fig = px.bar(daily_summary, x="date_only", y="total_kwh", color_discrete_sequence=["#1e88e5"])
                fig.update_traces(hovertemplate="<b>Date:</b> %{x}<br><b>Usage:</b> %{y:.2f} kWh<extra></extra>",
                                  hoverlabel=dict(bgcolor="#bbdefb", font_size=15))
                st.plotly_chart(fig, use_container_width=True)
            with col_d2:
                st.markdown("#### Daily Total Cost (Inc. Standing & VAT)")
                fig = px.bar(daily_summary, x="date_only", y="total_cost", color_discrete_sequence=["#2e7d32"])
                fig.update_traces(hovertemplate="<b>Date:</b> %{x}<br><b>Cost:</b> €%{y:.2f}<extra></extra>",
                                  hoverlabel=dict(bgcolor="#c8e6c9", font_size=15))
                st.plotly_chart(fig, use_container_width=True)

    # ── TAB 3: Hourly & Peak Analysis ──
    with tab3:
        st.subheader("⏰ Usage Profiling by Hour of Day & Spikes")

        df_filtered = df_filtered.copy()
        df_filtered["day_type"] = np.where(df_filtered["reading_at"].dt.dayofweek < 5, "Weekday", "Weekend")

        hourly_daytype = df_filtered.groupby(["hour_of_day", "day_type"]).agg(
            avg_kwh=("estimated_kwh", "mean")
        ).reset_index()

        col_h1, col_h2 = st.columns(2)
        with col_h1:
            st.markdown("#### Average Usage Profile per Hour")
            fig = px.line(hourly_daytype, x="hour_of_day", y="avg_kwh", color="day_type",
                          markers=True, color_discrete_sequence=["#FF7043", "#26A69A"])
            fig.update_traces(hovertemplate="<b>Hour:</b> %{x}:00<br><b>Avg:</b> %{y:.3f} kWh<extra></extra>",
                              hoverlabel=dict(font_size=15))
            fig.update_layout(xaxis=dict(tickmode="linear", tick0=0, dtick=2), xaxis_title="Hour of Day (24h)")
            st.plotly_chart(fig, use_container_width=True)

        with col_h2:
            st.markdown("#### Share of Consumption by Time Window")
            conditions = [
                (df_filtered["hour_of_day"] >= 23) | (df_filtered["hour_of_day"] < 8),
                (df_filtered["hour_of_day"] >= 17) & (df_filtered["hour_of_day"] < 19)
            ]
            df_filtered["time_window"] = np.select(
                conditions,
                ["Night (23:00–08:00)", "Peak (17:00–19:00)"],
                default="Day (08:00–17:00 / 19:00–23:00)"
            )
            window_summary = df_filtered.groupby("time_window")["estimated_kwh"].sum().reset_index()
            fig = px.pie(window_summary, values="estimated_kwh", names="time_window",
                         color="time_window",
                         color_discrete_map={
                             "Day (08:00–17:00 / 19:00–23:00)": "#FFCA28",
                             "Night (23:00–08:00)": "#5C6BC0",
                             "Peak (17:00–19:00)": "#EF5350"
                         }, hole=0.4)
            fig.update_traces(textinfo="percent+label",
                              hovertemplate="<b>%{label}</b><br>%{value:,.1f} kWh<extra></extra>")
            st.plotly_chart(fig, use_container_width=True)

    # ── TAB 4: Appliance Breakdown ──
    with tab4:
        st.subheader("🔌 Appliance Disaggregation")
        st.markdown("Energy mapped to appliance groups using behavioral signatures on billing kWh values.")

        dis_df = disaggregate_appliances(df_filtered, house_profile)

        app_categories = {
            "app_always_on":     "Always On (Baseload Standby)",
            "app_heating":       "Space & Water Heating",
            "app_cooking":       "Cooking & Kitchen",
            "app_laundry":       "Laundry & Dishwasher",
            "app_entertainment": "Entertainment & Lighting",
            "app_ev":            "Electric Vehicle (EV)",
            "app_misc":          "Other / Unclassified"
        }

        app_costs_data = []
        for col_name, label in app_categories.items():
            if col_name == "app_ev" and not house_profile["has_ev"]:
                continue
            tot_kwh      = dis_df[col_name].sum()
            tot_cost     = (dis_df[col_name] * dis_df["tariff_rate"]).sum()
            proportion   = tot_kwh / actual_kwh if actual_kwh > 0 else 0
            standing_app = proportion * actual_standing_pso
            tot_gross    = (tot_cost + standing_app) * (1 + rates["vat_rate"])
            app_costs_data.append({
                "Appliance Category": label,
                "Consumption (kWh)":  tot_kwh,
                "Estimated Cost (€)": tot_gross
            })

        app_summary = pd.DataFrame(app_costs_data)

        col_a1, col_a2 = st.columns(2)
        with col_a1:
            st.markdown("#### Appliance Energy Share")
            fig = px.pie(app_summary, values="Consumption (kWh)", names="Appliance Category",
                         color_discrete_sequence=px.colors.qualitative.Safe, hole=0.4)
            fig.update_traces(textinfo="percent+label",
                              hovertemplate="<b>%{label}</b><br>%{value:,.1f} kWh<extra></extra>")
            st.plotly_chart(fig, use_container_width=True)

        with col_a2:
            st.markdown("#### Estimated Bill Contribution")
            sorted_app = app_summary.sort_values("Estimated Cost (€)", ascending=False)
            fig = px.bar(sorted_app, x="Appliance Category", y="Estimated Cost (€)",
                         color="Appliance Category",
                         color_discrete_sequence=px.colors.qualitative.Safe,
                         text=sorted_app["Estimated Cost (€)"].map(lambda x: f"€{x:.2f}"))
            fig.update_traces(hovertemplate="<b>%{x}</b><br>€%{y:.2f}<extra></extra>",
                              hoverlabel=dict(font_size=15))
            fig.update_layout(showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

    # ── TAB 5: Bill Saving Simulator ──
    with tab5:
        st.subheader("🎯 Interactive Smart Saving Simulator")

        target_savings = st.slider("Select Your Target Monthly Savings:", min_value=5, max_value=100, value=20, step=5, format="€%d")

        avg_tariff_rate    = (rates["flat_rate"] if rates["type"] == "flat"
                              else (actual_usage_cost / actual_kwh if actual_kwh > 0 else 0.28))
        needed_kwh_reduction = (target_savings / (1 + rates["vat_rate"])) / avg_tariff_rate

        st.markdown(f"To save **€{target_savings:.2f}** this month, you need to reduce consumption by **{needed_kwh_reduction:.1f} kWh**.")
        st.markdown("#### 🛠️ Customise Your Saving Actions:")

        savings_actions = [
            {"id": "always_on",      "label": "🔌 Unplug TVs, routers & chargers when sleeping/away",
             "kwh_save": 0.04 * 24 * 30, "desc": "Saves ~28.8 kWh/month (40 W standby eliminated)."},
            {"id": "wash_30",        "label": "🧼 Wash laundry at 30°C instead of 40°C+",
             "kwh_save": 0.8 * 12,       "desc": "Saves ~9.6 kWh/month (12 loads)."},
            {"id": "dryer_line",     "label": "☀️ Hang-dry instead of tumble dryer",
             "kwh_save": 2.5 * 8,        "desc": "Saves ~20.0 kWh/month (8 loads skipped)."},
            {"id": "heating_one_deg","label": "🌡️ Lower water heating thermostat by 1°C",
             "kwh_save": 1.2 * 30,       "desc": "Saves ~36.0 kWh/month."},
            {"id": "led_swap",       "label": "💡 Swap 5 halogen bulbs for LED",
             "kwh_save": 0.18 * 5 * 30,  "desc": "Saves ~27.0 kWh/month."},
        ]

        selected_savings_kwh = 0.0
        for action in savings_actions:
            if st.checkbox(f"{action['label']} — *{action['desc']}*", value=False):
                selected_savings_kwh += action["kwh_save"]

        pct_achieved = min(100.0, (selected_savings_kwh / needed_kwh_reduction) * 100.0) if needed_kwh_reduction > 0 else 0.0
        saved_euros  = (selected_savings_kwh * avg_tariff_rate) * (1 + rates["vat_rate"])

        st.markdown("---")
        st.markdown("### 🏆 Savings Progress:")
        if pct_achieved >= 100.0:
            st.success(f"🎉 **Goal Achieved!** {selected_savings_kwh:.1f} kWh saved = **€{saved_euros:.2f}** this month!")
        else:
            st.info(f"⚡ **Keep going!** {selected_savings_kwh:.1f} kWh of {needed_kwh_reduction:.1f} kWh needed (saving **€{saved_euros:.2f}** so far).")
        st.progress(pct_achieved / 100.0)

        fig = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=saved_euros,
            title={"text": "Target vs Simulated Monthly Savings (€)", "font": {"size": 20}},
            delta={"reference": target_savings, "increasing": {"color": "green"}},
            gauge={
                "axis": {"range": [None, max(target_savings * 1.3, 40)]},
                "bar":  {"color": "#1b5e20"},
                "steps": [
                    {"range": [0, target_savings],             "color": "#ffe0b2"},
                    {"range": [target_savings, max(target_savings * 1.3, 40)], "color": "#c8e6c9"}
                ],
                "threshold": {"line": {"color": "red", "width": 4}, "thickness": 0.75, "value": target_savings}
            }
        ))
        st.plotly_chart(fig, use_container_width=True)

    # ── TAB 6: Appliance Detective ──
    with tab6:
        st.subheader("🔍 Smart Signature Detective")
        st.markdown("Scans raw interval logs for specific appliance spike signatures based on physical power profiles.")

        det_col1, det_col2 = st.columns(2)
        with det_col1:
            st.info(
                "🛁 **Shower Run Profile:**\n"
                "An Irish instantaneous electric shower draws 8.5–10.5 kW. "
                "A 10-minute shower consumes ~1.2–1.8 kWh within a single 30-min slot. "
                "Any isolated reading ≥ 0.8 kWh is flagged."
            )
        with det_col2:
            st.success(
                "🍵 **Kettle Boil Profile:**\n"
                "A 3 kW kettle boiled for 3 min uses ~0.15 kWh. "
                "Rapid daytime surges between 0.15–0.4 kWh above local baseline are evaluated."
            )

        st.markdown("---")
        df_detect              = df_filtered.copy()
        df_detect["prev_kwh"]  = df_detect["estimated_kwh"].shift(1)
        df_detect["next_kwh"]  = df_detect["estimated_kwh"].shift(-1)

        shower_cond  = (
            (df_detect["estimated_kwh"] >= 0.80) &
            (df_detect["prev_kwh"] < 0.45) &
            (df_detect["next_kwh"] < 0.45)
        )
        shower_runs = df_detect[shower_cond].copy()
        shower_runs["run_cost"] = (
            shower_runs["estimated_kwh"] * rates["flat_rate"] if rates["type"] == "flat"
            else shower_runs["cost"]
        ) * (1 + rates["vat_rate"])

        df_detect["local_ambient_avg"]        = (df_detect["prev_kwh"] + df_detect["next_kwh"]) / 2
        df_detect["net_spike_above_baseline"] = df_detect["estimated_kwh"] - df_detect["local_ambient_avg"]

        kettle_cond = (
            (df_detect["net_spike_above_baseline"] >= 0.12) &
            (df_detect["net_spike_above_baseline"] <= 0.35) &
            (df_detect["hour_of_day"] >= 7) &
            (df_detect["hour_of_day"] <= 22) &
            (df_detect["estimated_kwh"] < 0.80)
        )
        kettle_runs = df_detect[kettle_cond].copy()
        kettle_runs["run_cost"] = (
            kettle_runs["net_spike_above_baseline"] * rates["flat_rate"] if rates["type"] == "flat"
            else kettle_runs["cost"]
        ) * (1 + rates["vat_rate"])

        res_col1, res_col2 = st.columns(2)

        with res_col1:
            st.markdown(f"#### 🛁 Detected Electric Shower Runs ({len(shower_runs)})")
            if not shower_runs.empty:
                show_df = shower_runs[["reading_at", "day_name", "estimated_kwh", "run_cost"]].copy()
                show_df["Time of Run"]   = show_df["reading_at"].dt.strftime("%d %b %Y (%H:%M)")
                show_df["Consumption"]   = show_df["estimated_kwh"].map(lambda x: f"{x:.2f} kWh")
                show_df["Cost (Inc. VAT)"] = show_df["run_cost"].map(lambda x: f"€{x:.2f}")
                fig = px.scatter(show_df, x="reading_at", y="estimated_kwh",
                                 size="estimated_kwh", color="run_cost",
                                 color_continuous_scale="Reds",
                                 labels={"reading_at": "Date & Time", "estimated_kwh": "Spike (kWh)"})
                fig.update_traces(hovertemplate="<b>Shower</b><br>%{x}<br>%{y:.2f} kWh<extra></extra>",
                                  hoverlabel=dict(bgcolor="#ffebee", font_size=16))
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(show_df[["Time of Run", "day_name", "Consumption", "Cost (Inc. VAT)"]].rename(
                    columns={"day_name": "Day"}), use_container_width=True)
            else:
                st.success("No heavy isolated shower spikes detected in this period.")

        with res_col2:
            st.markdown(f"#### 🍵 Detected Kettle Boil Surges ({len(kettle_runs)})")
            if not kettle_runs.empty:
                kettle_df = kettle_runs[["reading_at", "day_name", "net_spike_above_baseline", "run_cost"]].copy()
                kettle_df["Time of Run"]   = kettle_df["reading_at"].dt.strftime("%d %b %Y (%H:%M)")
                kettle_df["Boil Surge"]    = kettle_df["net_spike_above_baseline"].map(lambda x: f"+{x:.2f} kWh")
                kettle_df["Cost (Inc. VAT)"] = kettle_df["run_cost"].map(lambda x: f"€{x:.2f}")
                fig = px.scatter(kettle_df, x="reading_at", y="net_spike_above_baseline",
                                 size="net_spike_above_baseline", color="run_cost",
                                 color_continuous_scale="Viridis",
                                 labels={"reading_at": "Date & Time", "net_spike_above_baseline": "Surge (kWh)"})
                fig.update_traces(hovertemplate="<b>Kettle</b><br>%{x}<br>+%{y:.2f} kWh<extra></extra>",
                                  hoverlabel=dict(bgcolor="#e8f5e9", font_size=16))
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(kettle_df[["Time of Run", "day_name", "Boil Surge", "Cost (Inc. VAT)"]].rename(
                    columns={"day_name": "Day"}), use_container_width=True)
            else:
                st.info("No distinct isolated kettle runs identified in this period.")

else:
    st.warning("Upload a valid smart meter file or select 'Use Sample Demo Data' to populate the dashboard.")
