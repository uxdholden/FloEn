import io
import re
import calendar
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta

st.set_page_config(
    page_title="Smart Meter Cost & Analytics Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

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
    .task-card {
        background: #fdfefe;
        border: 1px solid #eaf2f8;
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 8px;
    }
    .task-green { border-left: 5px solid #2ecc71; }
    .task-orange { border-left: 5px solid #e67e22; }
    .task-red { border-left: 5px solid #e74c3c; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def _kwt_to_kwh(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce") / 2.0


def parse_wide_csv(raw: str) -> pd.DataFrame:
    try:
        df = pd.read_csv(io.StringIO(raw), dtype=str)
        df.columns = [str(c).strip() for c in df.columns]
        col_lower = {c.lower(): c for c in df.columns}

        mprn_col = next((col_lower[k] for k in col_lower if "mprn" in k), None)
        serial_col = next((col_lower[k] for k in col_lower if "serial" in k), None)
        date_col = next((col_lower[k] for k in col_lower if k == "date"), None)
        time_cols = [c for c in df.columns if re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", c)]

        if not (mprn_col and date_col and len(time_cols) >= 2):
            return pd.DataFrame()

        id_vars = [c for c in [mprn_col, serial_col, date_col] if c]
        melted = df.melt(id_vars=id_vars, value_vars=time_cols, var_name="time_slot", value_name="_raw_kwt")
        melted["estimated_kwh"] = _kwt_to_kwh(melted["_raw_kwt"])
        melted = melted.dropna(subset=["estimated_kwh"])
        melted["reading_at"] = pd.to_datetime(melted[date_col].astype(str) + " " + melted["time_slot"].str[:5], errors="coerce")
        melted = melted.dropna(subset=["reading_at"]).sort_values("reading_at").reset_index(drop=True)
        melted["mprn"] = melted[mprn_col].astype(str).str.strip()
        melted["meter_serial"] = melted[serial_col].astype(str).str.strip() if serial_col else ""
        melted["read_value_kw"] = melted["estimated_kwh"] * 2.0
        melted["date_only"] = melted["reading_at"].dt.date
        return melted[["mprn", "meter_serial", "reading_at", "read_value_kw", "estimated_kwh", "date_only"]]
    except Exception as e:
        st.error(f"Error parsing wide CSV: {e}")
        return pd.DataFrame()


@st.cache_data(show_spinner=False)
def parse_interval_csv(raw_bytes: bytes) -> pd.DataFrame:
    raw = raw_bytes.decode("utf-8-sig", errors="ignore")

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
        df["reading_at"] = pd.to_datetime(df["reading_at"], errors="coerce")
        df = df.dropna(subset=["reading_at", "_raw_numeric"]).sort_values("reading_at")
        df["estimated_kwh"] = df["_raw_numeric"] / 2.0
        df["read_value_kw"] = df["estimated_kwh"] * 2.0
        df["date_only"] = df["reading_at"].dt.date
        for col in ["mprn", "meter_serial"]:
            if col not in df.columns:
                df[col] = ""
        return df[["mprn", "meter_serial", "reading_at", "read_value_kw", "estimated_kwh", "date_only"]]

    try:
        lines = raw.splitlines()
        header_idx = next((i for i, l in enumerate(lines) if "mprn" in l.lower() and any(k in l.lower() for k in ["date", "time", "value"])), -1)
        csv_source = "\n".join(lines[header_idx:]) if header_idx != -1 else raw
        csv_df = pd.read_csv(io.StringIO(csv_source))
        cols = {str(col).strip().lower(): col for col in csv_df.columns}
        mprn_col = next((cols[c] for c in cols if "mprn" in c), None)
        serial_col = next((cols[c] for c in cols if "serial" in c or "meter" in c), None)
        value_col = next((cols[c] for c in cols if "value" in c or "reading" in c), None)
        date_col = next((cols[c] for c in cols if "date" in c or "time" in c or "at" in c), None)

        if all([mprn_col, value_col, date_col]):
            df = pd.DataFrame({
                "mprn": csv_df[mprn_col].astype(str).str.strip(),
                "meter_serial": csv_df[serial_col].astype(str).str.strip() if serial_col else "",
                "read_value": pd.to_numeric(csv_df[value_col], errors="coerce"),
                "reading_at": pd.to_datetime(csv_df[date_col], errors="coerce"),
            })
            parsed = finalize(df)
            if not parsed.empty:
                return parsed
    except Exception:
        pass

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
    end_date = datetime.now().replace(minute=0, second=0, microsecond=0)
    start_date = end_date - timedelta(days=90)
    if start_date.day < 10:
        start_date = start_date.replace(day=15)
    date_range = pd.date_range(start=start_date, end=end_date, freq="30min")
    rows = []
    for dt in date_range:
        hour, weekday = dt.hour, dt.weekday()
        base = 0.12 + np.random.normal(0, 0.015)
        if 8 <= hour < 17:
            activity = 0.22 + np.random.normal(0, 0.04)
        elif 17 <= hour < 19:
            activity = 0.80 + np.random.normal(0, 0.12)
        elif 19 <= hour < 23:
            activity = 0.40 + np.random.normal(0, 0.06)
        else:
            activity = 0.04 + np.random.normal(0, 0.01)
        if weekday >= 5:
            activity *= 1.25
        kettle_spike = 0.5 if (hour in [7, 10, 13, 16, 20]) and np.random.rand() > 0.6 else 0.0
        shower_spike = 1.6 if (hour == 8 or hour == 19) and np.random.rand() > 0.8 else 0.0
        est_kwh = max(0.01, base + activity + kettle_spike + shower_spike)
        rows.append({
            "mprn": "10303339574",
            "meter_serial": "000000000024049722",
            "reading_at": dt,
            "read_value_kw": est_kwh * 2.0,
            "estimated_kwh": est_kwh,
            "date_only": dt.date(),
        })
    return pd.DataFrame(rows)


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
    df = df.copy()
    df["app_always_on"] = np.minimum(df.groupby("date_only")["estimated_kwh"].transform("min"), 0.25)
    df["active_kwh"] = np.maximum(0.0, df["estimated_kwh"] - df["app_always_on"])
    hr = df["reading_at"].dt.hour + df["reading_at"].dt.minute / 60.0
    is_weekend = df["reading_at"].dt.dayofweek >= 5

    for col in ["app_ev", "app_heating", "app_cooking", "app_laundry", "app_entertainment", "app_misc"]:
        df[col] = 0.0

    df["sig_shower"] = df["estimated_kwh"] >= 1.5
    active_prev_raw = df["estimated_kwh"].shift(1, fill_value=0.1)
    active_next_raw = df["estimated_kwh"].shift(-1, fill_value=0.1)
    df["sig_kettle"] = (df["estimated_kwh"] - active_prev_raw > 0.4) & (df["estimated_kwh"] - active_next_raw > 0.4) & (~df["sig_shower"])

    if house_profile["has_ev"]:
        ev_mask = (hr >= 0.0) & (hr < 6.0) & (df["active_kwh"] > 1.2)
        df["app_ev"] = np.where(ev_mask, np.minimum(df["active_kwh"], 3.7), 0.0)
        df["active_kwh"] -= df["app_ev"]

    active_prev = df["active_kwh"].shift(1, fill_value=0.0)
    active_next = df["active_kwh"].shift(-1, fill_value=0.0)
    is_sustained = (df["active_kwh"] > 0.45) & ((active_prev > 0.40) | (active_next > 0.40))
    laundry_heavy_mask = is_sustained & (hr >= 7.0) & (hr < 23.0) & (df["active_kwh"] > 0)
    df["app_laundry"] += np.where(laundry_heavy_mask, df["active_kwh"] * 0.85, 0.0)
    df["active_kwh"] -= df["app_laundry"]

    heat_ratio = 0.6 if house_profile["electric_heating"] else 0.15
    heat_mask = ((hr >= 5.5) & (hr < 8.5)) | (hr >= 23.0) | (hr < 2.0)
    df["app_heating"] = np.where(heat_mask & (df["active_kwh"] > 0), df["active_kwh"] * heat_ratio, 0.0)
    df["active_kwh"] -= df["app_heating"]

    cook_mask = ((hr >= 7.0) & (hr < 9.0)) | ((hr >= 12.0) & (hr < 14.0)) | ((hr >= 16.5) & (hr < 19.5))
    cook_ratio = np.where(df["active_kwh"] > 0.15, 0.55, 0.3)
    df["app_cooking"] = np.where(cook_mask & (df["active_kwh"] > 0), df["active_kwh"] * cook_ratio, 0.0)
    df["active_kwh"] -= df["app_cooking"]

    chore_mask = ((hr >= 9.0) & (hr < 12.0)) | ((hr >= 14.0) & (hr < 16.5)) | ((hr >= 19.0) & (hr < 22.0)) | (is_weekend & (hr >= 9.0) & (hr < 18.0))
    chore_ratio = np.where(df["active_kwh"] > 0.2, 0.5, 0.25)
    chore_draw = np.where(chore_mask & (df["active_kwh"] > 0), df["active_kwh"] * chore_ratio, 0.0)
    df["app_laundry"] += chore_draw
    df["active_kwh"] -= chore_draw

    ent_mask = (hr >= 18.0) & (hr < 23.5)
    df["app_entertainment"] = np.where(ent_mask & (df["active_kwh"] > 0), df["active_kwh"] * 0.7, 0.0)
    df["active_kwh"] -= df["app_entertainment"]
    df["app_misc"] = np.maximum(0.0, df["active_kwh"])
    return df


def calculate_smart_projection(df_all: pd.DataFrame, target_month: str, rates: dict):
    df_month = df_all[df_all["year_month"] == target_month].copy()
    if df_month.empty:
        return 0.0, 0.0, 0.0, 0.0, False, 1, 30

    actual_kwh = df_month["estimated_kwh"].sum()
    actual_usage_cost = df_month["cost"].sum()
    days_recorded = sorted(df_month["date_only"].unique())
    days_elapsed = len(days_recorded)
    y_val, m_val = map(int, target_month.split("-"))
    days_in_month = calendar.monthrange(y_val, m_val)[1]
    is_latest_month = target_month == df_all["year_month"].max()
    is_first_month = target_month == df_all["year_month"].min()
    is_unfinished = False
    projected_kwh = actual_kwh
    projected_usage_cost = actual_usage_cost

    if is_latest_month and (days_elapsed < days_in_month):
        is_unfinished = True
        last_recorded_date = max(days_recorded)
        remaining_dates = []
        curr = last_recorded_date + timedelta(days=1)
        end_of_month_date = datetime(y_val, m_val, days_in_month).date()
        while curr <= end_of_month_date:
            remaining_dates.append(curr)
            curr += timedelta(days=1)

        if remaining_dates:
            df_history = df_all[df_all["year_month"] != target_month].copy()
            if df_history.empty:
                df_history = df_month.copy()

            daily_history = df_history.groupby(["date_only", "is_weekend"])["estimated_kwh"].sum().reset_index()
            weekday_avg = daily_history[~daily_history["is_weekend"]]["estimated_kwh"].mean()
            weekend_avg = daily_history[daily_history["is_weekend"]]["estimated_kwh"].mean()
            overall_mean = daily_history["estimated_kwh"].mean() if not daily_history.empty else 10.0
            if pd.isna(weekday_avg):
                weekday_avg = overall_mean
            if pd.isna(weekend_avg):
                weekend_avg = overall_mean

            df_history["rate_paid"] = df_history["cost"] / df_history["estimated_kwh"].replace(0, np.nan)
            avg_rate = df_history["rate_paid"].mean()
            if pd.isna(avg_rate):
                avg_rate = rates.get("flat_rate", rates.get("day_rate", 0.28))

            proj_rem_kwh = 0.0
            proj_rem_cost = 0.0
            for r_date in remaining_dates:
                is_we = r_date.weekday() >= 5
                day_kwh = weekend_avg if is_we else weekday_avg
                proj_rem_kwh += day_kwh
                proj_rem_cost += day_kwh * avg_rate

            projected_kwh = actual_kwh + proj_rem_kwh
            projected_usage_cost = actual_usage_cost + proj_rem_cost
    elif is_first_month:
        projected_kwh = actual_kwh
        projected_usage_cost = actual_usage_cost

    actual_standing_pso = days_elapsed * rates["daily_standing_charge"]
    actual_gross_cost = actual_usage_cost + actual_standing_pso
    projected_standing_pso = days_in_month * rates["daily_standing_charge"] if is_unfinished else days_elapsed * rates["daily_standing_charge"]
    projected_gross_cost = projected_usage_cost + projected_standing_pso

    return actual_kwh, actual_gross_cost, projected_kwh, projected_gross_cost, is_unfinished, days_elapsed, days_in_month


def build_full_month_projection_timeline(df_all: pd.DataFrame, target_month: str, rates: dict):
    y_val, m_val = map(int, target_month.split("-"))
    days_in_month = calendar.monthrange(y_val, m_val)[1]
    start_date = datetime(y_val, m_val, 1).date()
    target_dates = [start_date + timedelta(days=x) for x in range(days_in_month)]
    next_m_val = m_val + 1 if m_val < 12 else 1
    next_y_val = y_val if m_val < 12 else y_val + 1
    next_days_in_month = calendar.monthrange(next_y_val, next_m_val)[1]
    next_start_date = datetime(next_y_val, next_m_val, 1).date()
    next_dates = [next_start_date + timedelta(days=x) for x in range(next_days_in_month)]
    all_dates = target_dates + next_dates

    df_month = df_all[df_all["year_month"] == target_month].copy()
    actuals = {}
    if not df_month.empty:
        actuals = df_month.groupby("date_only").agg(kwh=("estimated_kwh", "sum"), cost=("cost", "sum")).to_dict(orient="index")

    df_history = df_all[df_all["year_month"] != target_month].copy()
    if df_history.empty:
        df_history = df_all.copy()

    daily_history = df_history.groupby(["date_only", "day_name", "is_weekend"]).agg(kwh=("estimated_kwh", "sum"), cost=("cost", "sum")).reset_index()
    daily_history["weekday_num"] = pd.to_datetime(daily_history["date_only"]).dt.dayofweek
    dow_averages = daily_history.groupby("weekday_num")[["kwh", "cost"]].mean().to_dict(orient="index")
    overall_mean_kwh = daily_history["kwh"].mean() if not daily_history.empty else 10.0
    overall_mean_cost = daily_history["cost"].mean() if not daily_history.empty else 3.0

    timeline_records = []
    accumulated_kwh_actual = 0.0
    accumulated_cost_actual = 0.0
    accumulated_kwh_projected = 0.0
    accumulated_cost_projected = 0.0
    last_actual_date = max(actuals.keys()) if actuals else None
    current_iter_month = None
    days_in_current_accum_month = 0

    for d in all_dates:
        is_we = d.weekday() >= 5
        day_of_week_num = d.weekday()

        if current_iter_month is not None and d.month != current_iter_month:
            accumulated_kwh_actual = 0.0
            accumulated_cost_actual = 0.0
            accumulated_kwh_projected = 0.0
            accumulated_cost_projected = 0.0
            days_in_current_accum_month = 0

        current_iter_month = d.month
        days_in_current_accum_month += 1
        avg_dow_data = dow_averages.get(day_of_week_num, {"kwh": overall_mean_kwh, "cost": overall_mean_cost})
        proj_kwh_val = avg_dow_data["kwh"]
        proj_cost_val = avg_dow_data["cost"]
        is_first_month = target_month == df_all["year_month"].min()
        in_target_month = d.year == y_val and d.month == m_val

        if d in actuals:
            day_kwh = actuals[d]["kwh"]
            day_cost = actuals[d]["cost"]
            status = "Actual"
            accumulated_kwh_actual += day_kwh
            accumulated_cost_actual += day_cost
            accumulated_kwh_projected = accumulated_kwh_actual
            accumulated_cost_projected = accumulated_cost_actual
        else:
            if is_first_month and in_target_month:
                day_kwh = 0.0
                day_cost = 0.0
                status = "Unrecorded (Late Start)"
                accumulated_kwh_projected = accumulated_kwh_actual
                accumulated_cost_projected = accumulated_cost_actual
            else:
                day_kwh = proj_kwh_val
                day_cost = proj_cost_val
                status = "Projected (Target Month)" if in_target_month else "Projected (Next Month)"
                accumulated_kwh_projected += day_kwh
                accumulated_cost_projected += day_cost

        daily_standing_cost = rates["daily_standing_charge"]
        gross_actual_cost = day_cost + daily_standing_cost if status == "Actual" else 0.0
        gross_projected_cost = day_cost + daily_standing_cost if status in ["Actual", "Projected (Target Month)", "Projected (Next Month)"] else 0.0
        gross_accumulated_actual_cost = accumulated_cost_actual + (days_in_current_accum_month * daily_standing_cost) if status == "Actual" else np.nan
        gross_accumulated_projected_cost = accumulated_cost_projected + (days_in_current_accum_month * daily_standing_cost)

        timeline_records.append({
            "Date": d,
            "Day of Month": d.day,
            "Month Label": d.strftime("%B %Y"),
            "Day Name": d.strftime("%A"),
            "Is Weekend": is_we,
            "Status": status,
            "Daily Consumption (kWh)": day_kwh if status == "Actual" else np.nan,
            "Daily Projected Trend (kWh)": day_kwh if status in ["Actual", "Projected (Target Month)", "Projected (Next Month)"] else np.nan,
            "Daily Cost (€)": gross_actual_cost if status == "Actual" else np.nan,
            "Daily Projected Cost Trend (€)": gross_projected_cost if status in ["Actual", "Projected (Target Month)", "Projected (Next Month)"] else np.nan,
            "Accumulated Consumption Actual (kWh)": accumulated_kwh_actual if status == "Actual" else np.nan,
            "Accumulated Consumption Projected (kWh)": accumulated_kwh_projected if status in ["Actual", "Projected (Target Month)", "Projected (Next Month)"] else np.nan,
            "Accumulated Cost Actual (€)": gross_accumulated_actual_cost if status == "Actual" else np.nan,
            "Accumulated Cost Projected (€)": gross_accumulated_projected_cost if status in ["Actual", "Projected (Target Month)", "Projected (Next Month)"] else np.nan,
        })

    timeline_df = pd.DataFrame(timeline_records)
    if last_actual_date:
        timeline_df.loc[timeline_df["Status"] == "Actual", "Daily Projected Trend (kWh)"] = timeline_df["Daily Consumption (kWh)"]
        timeline_df.loc[timeline_df["Status"] == "Actual", "Daily Projected Cost Trend (€)"] = timeline_df["Daily Cost (€)"]
    return timeline_df


st.sidebar.markdown("---")
with st.sidebar:
    st.header("📁 Data Source")
    data_option = st.radio("Choose Data Input:", ["Upload My Own File", "Use Sample Demo Data"])
    uploaded_file = None
    if data_option == "Upload My Own File":
        uploaded_file = st.file_uploader("Upload CSV/TXT File", type=["csv", "txt"])

    st.header("💰 Electricity Pricing")
    tariff_style = st.selectbox("Select Tariff Type:", ["24-Hour Flat Tariff", "Smart (Day/Night/Peak) Tariff"])

    rates = {}
    if tariff_style == "24-Hour Flat Tariff":
        rates["type"] = "flat"
        rates["flat_rate"] = st.number_input("24HR Unit Rate (Cent per kWh)", value=29.31) / 100.0
    else:
        rates["type"] = "smart"
        rates["day_rate"] = st.number_input("Day Rate (Cent/kWh)", value=29.31) / 100.0
        rates["night_rate"] = st.number_input("Night Rate (Cent/kWh)", value=15.10) / 100.0
        rates["peak_rate"] = st.number_input("Peak Rate (Cent/kWh)", value=35.40) / 100.0

    with st.expander("Fixed Charges", expanded=False):
        annual_standing = st.number_input("Standing Charge Annual - Urban (€)", value=300.20)
        annual_pso = st.number_input("PSO Levy (€)", value=19.10)
        rates["daily_standing_charge"] = (annual_standing + annual_pso) / 365.25

    st.header("🔥 Gas Pricing")
    gas_rates = {}
    gas_rates["unit_rate"] = st.number_input("Gas Unit Rate (Cent per kWh)", value=9.14) / 100.0
    gas_rates["annual_standing_charge"] = st.number_input("Gas Standing Charge Annual (€)", value=170.84)
    gas_rates["annual_carbon_tax"] = st.number_input("Gas Carbon Tax Annual (€)", value=137.65)
    gas_rates["daily_fixed_charge"] = (gas_rates["annual_standing_charge"] + gas_rates["annual_carbon_tax"]) / 365.25

    st.header("🔌 Household Profile")
    house_profile = {
        "has_ev": st.checkbox("Own an Electric Vehicle (EV)?", value=True),
        "electric_heating": st.checkbox("Use electric space/water heating?", value=True),
    }


tab_list = [
    "📈 Historical Trends",
    "📊 Monthly Forecasts",
    "⚡ Tariff Autopilot & Arbitrage",
    "🌡️ Heating & Thermostat ROI",
    "🔋 Solar & Battery Dispatch",
    "🧛 Phantom Hunter",
    "🩺 Appliance Diagnostics",
    "📅 Chore Task Master",
    "🔍 Heat Density Grid",
    "🔥 Gas Overview",
]
tabs = st.tabs(tab_list)

with tabs[9]:
    st.subheader("🔥 Gas Pricing Overview")
    st.write("This tab tracks your gas pricing defaults as VAT-inclusive values and gives quick annualised cost references.")

    gas_col1, gas_col2, gas_col3 = st.columns(3)
    with gas_col1:
        st.metric("Gas Unit Rate", f"{gas_rates['unit_rate'] * 100:.2f} c/kWh")
    with gas_col2:
        st.metric("Standing Charge", f"€{gas_rates['annual_standing_charge']:.2f}/year")
    with gas_col3:
        st.metric("Carbon Tax", f"€{gas_rates['annual_carbon_tax']:.2f}/year")

    st.markdown("---")
    st.markdown("### Annual Gas Bill Estimator")
    annual_gas_kwh = st.slider("Estimated Annual Gas Usage (kWh)", min_value=0, max_value=50000, value=12000, step=500)
    annual_gas_usage_cost = annual_gas_kwh * gas_rates["unit_rate"]
    annual_gas_fixed_cost = gas_rates["annual_standing_charge"] + gas_rates["annual_carbon_tax"]
    annual_gas_total = annual_gas_usage_cost + annual_gas_fixed_cost

    g1, g2, g3 = st.columns(3)
    with g1:
        st.metric("Estimated Usage Cost", f"€{annual_gas_usage_cost:,.2f}")
    with g2:
        st.metric("Fixed Charges", f"€{annual_gas_fixed_cost:,.2f}")
    with g3:
        st.metric("Estimated Annual Gas Bill", f"€{annual_gas_total:,.2f}")

    gas_df = pd.DataFrame({
        "Component": ["Usage", "Standing Charge", "Carbon Tax"],
        "Amount": [annual_gas_usage_cost, gas_rates["annual_standing_charge"], gas_rates["annual_carbon_tax"]],
    })
    fig_gas = px.bar(gas_df, x="Component", y="Amount", text="Amount", title="Annual Gas Cost Breakdown", color="Component")
    fig_gas.update_traces(texttemplate="€%{y:,.0f}", textposition="outside")
    st.plotly_chart(fig_gas, use_container_width=True)


df_raw = None
if data_option == "Upload My Own File" and uploaded_file is not None:
    with st.spinner("Processing smart meter file..."):
        file_bytes = uploaded_file.read()
        df_raw = parse_interval_csv(file_bytes)
        if df_raw.empty:
            st.error("Failed to parse file. Verify it's a standard HDF or ESB export.")
elif data_option == "Use Sample Demo Data":
    df_raw = generate_demo_data()

if df_raw is None or df_raw.empty:
    st.info("👈 Upload your Smart Meter Data or select the Demo Data option to get started.")
    st.stop()


df = apply_tariffs(df_raw, rates)
df["reading_at"] = pd.to_datetime(df["reading_at"])
df["year_month"] = df["reading_at"].dt.strftime("%Y-%m")
df["day_name"] = df["reading_at"].dt.day_name()
df["hour_of_day"] = df["reading_at"].dt.hour

df["hour_float"] = df["reading_at"].dt.hour + df["reading_at"].dt.minute / 60.0
df["is_weekend"] = df["reading_at"].dt.dayofweek >= 5
df = disaggregate_appliances(df, house_profile)

st.sidebar.markdown("---")
st.sidebar.header("📅 View Filter")
months = sorted(df["year_month"].unique().tolist())
selected_month = st.sidebar.selectbox("Select Period:", ["All Months"] + months)
first_dataset_month = df["year_month"].min()
latest_dataset_month = df["year_month"].max()

if selected_month == "All Months":
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

daily_stats = df_filtered.groupby("date_only").agg(kwh=("estimated_kwh", "sum"), cost=("cost", "sum")).reset_index()
daily_stats["cost_inc_fixed"] = daily_stats["cost"] + rates["daily_standing_charge"]
if not daily_stats.empty:
    lowest_daily_cost = daily_stats["cost_inc_fixed"].quantile(0.10)
    peak_daily_cost = daily_stats["cost_inc_fixed"].quantile(0.90)
else:
    lowest_daily_cost = 0.0
    peak_daily_cost = 0.0

max_kw = df_filtered["read_value_kw"].max()
avg_import_rate = df_filtered["cost"].sum() / actual_kwh if actual_kwh > 0 else 0.30

st.markdown("<p style='font-size: 1.2rem; color: #555;'>Analyze your electricity usage, simulate hyper-actionable savings plans, and model battery/appliance payback periods.</p>", unsafe_allow_html=True)
if is_unfinished:
    st.warning(f"⚠️ **Target Period is Incomplete.** Only **{days_elapsed} of {days_in_month} days** are recorded. Projections for the active month are highlighted in brown.")
if selected_month == first_dataset_month and first_dataset_month != latest_dataset_month:
    first_recorded_day = df[df["year_month"] == first_dataset_month]["reading_at"].min().day
    if first_recorded_day > 1:
        st.info(f"ℹ️ **First recorded month starts late on Day {first_recorded_day}.** Projections are kept equivalent to actuals based on the remaining active days, preventing retrospective inflation.")

col1, col2, col3, col4 = st.columns(4)
with col1:
    color, val, label, sub = ("#4e342e", proj_kwh, "Estimated Period-End", f"Actual to-date: {actual_kwh:,.1f} kWh") if is_unfinished else ("#0d47a1", actual_kwh, "Total Consumption", "Completed Period")
    st.markdown(f'<div class="metric-container"><div class="metric-value" style="color:{color};">{val:,.1f} kWh</div><div class="metric-label">{label}</div><div class="metric-badge badge-actual">{sub}</div></div>', unsafe_allow_html=True)
with col2:
    color, val, label, sub = ("#4e342e", proj_cost, "Projected Period Bill", f"Actual to-date: €{actual_gross_cost:,.2f}") if is_unfinished else ("#1b5e20", actual_gross_cost, "Total Cost", "Completed Period")
    st.markdown(f'<div class="metric-container"><div class="metric-value" style="color:{color};">€{val:,.2f}</div><div class="metric-label">{label}</div><div class="metric-badge badge-success">{sub}</div></div>', unsafe_allow_html=True)
with col3:
    st.markdown(f'<div class="metric-container"><div class="metric-value" style="color:#e65100;">€{(actual_gross_cost/max(days_elapsed,1)):.2f}/day</div><div class="metric-label">Avg Daily Cost</div><div class="metric-badge badge-warning">Target: < €{(actual_gross_cost*0.9/max(days_elapsed,1)):.2f}</div></div>', unsafe_allow_html=True)
with col4:
    st.markdown(f'<div class="metric-container"><div class="metric-value" style="color:#37474f;">{max_kw:.2f} kW</div><div class="metric-label">Peak Demand Spike</div><div class="metric-badge badge-actual" style="background-color:#eceff1;color:#37474f;">Limit simultaneous loads</div></div>', unsafe_allow_html=True)

with tabs[0]:
    st.subheader("Monthly Historical Trends")
    monthly = df.groupby("year_month").agg(total_kwh=("estimated_kwh", "sum"), cost=("cost", "sum"), days=("date_only", "nunique")).reset_index()
    monthly["total_cost"] = monthly["cost"] + (monthly["days"] * rates["daily_standing_charge"])
    c1, c2 = st.columns(2)
    with c1:
        fig1 = px.line(monthly, x="year_month", y="total_kwh", text=monthly["total_kwh"].round(0), markers=True, title="Consumption Trend (kWh)")
        fig1.update_traces(hovertemplate="<b>Billing Month:</b> %{x}<br><b>Energy Consumed:</b> %{y:,.1f} kWh<extra></extra>", line=dict(width=3, color="#1e88e5"))
        st.plotly_chart(fig1, use_container_width=True)
    with c2:
        fig2 = px.bar(monthly, x="year_month", y="total_cost", text=monthly["total_cost"].round(0), title="Total Financial Bill (€)", color_discrete_sequence=["#2e7d32"])
        fig2.update_traces(hovertemplate="<b>Billing Month:</b> %{x}<br><b>Calculated Bill:</b> €%{y:,.2f}<extra></extra>")
        st.plotly_chart(fig2, use_container_width=True)

with tabs[1]:
    st.subheader("Accumulated & Proportional Daily Costs with Month-Ahead Forecasting")
    eval_month = selected_month if selected_month != "All Months" else latest_dataset_month
    timeline_df = build_full_month_projection_timeline(df, eval_month, rates)
    next_month_df = timeline_df[timeline_df["Status"] == "Projected (Next Month)"]
    next_month_name = next_month_df["Month Label"].iloc[0] if not next_month_df.empty else "Next Month"
    total_next_month_kwh = next_month_df["Daily Projected Trend (kWh)"].sum() if not next_month_df.empty else 0.0
    if not next_month_df.empty:
        next_month_days_count = len(next_month_df)
        raw_usage_cost_next = next_month_df["Daily Projected Cost Trend (€)"].sum() - (next_month_days_count * rates["daily_standing_charge"])
        total_next_month_cost = raw_usage_cost_next + (next_month_days_count * rates["daily_standing_charge"])
    else:
        total_next_month_cost = 0.0

    remaining_target_df = timeline_df[timeline_df["Status"] == "Projected (Target Month)"].copy()
    remaining_days = len(remaining_target_df)
    total_remaining_kwh = remaining_target_df["Daily Projected Trend (kWh)"].sum()
    total_remaining_cost = remaining_target_df["Daily Projected Cost Trend (€)"].sum()
    remaining_target_df["Cumulative Remaining kWh"] = remaining_target_df["Daily Projected Trend (kWh)"].cumsum()
    remaining_target_df["Cumulative Remaining Cost (€)"] = remaining_target_df["Daily Projected Cost Trend (€)"].cumsum()

    col_side, col_graph = st.columns([1, 3])
    with col_side:
        st.markdown(f"#### Spend Profile: **{eval_month}**")
        st.write("We model future spending boundaries by analyzing your historical weekday vs weekend averages:")
        st.metric("Worst-Case Average (90th Pct)", f"€{peak_daily_cost:.2f} / day")
        st.metric("Best-Case Average (10th Pct)", f"€{lowest_daily_cost:.2f} / day")
        st.markdown("---")
        st.markdown(f"#### 📅 Forecast: **{next_month_name}**")
        st.write("Day-type weighted projection for the next calendar month (resets to zero at Day 1):")
        st.metric(f"Projected {next_month_name} Usage", f"{total_next_month_kwh:,.1f} kWh")
        st.metric(f"Forecasted {next_month_name} Bill", f"€{total_next_month_cost:,.2f}")
        st.markdown("---")
        sim_days = st.slider("Forecast Cumulative Costs over Days:", min_value=1, max_value=60, value=30, key="forecast_days")
        best_sim_total = lowest_daily_cost * sim_days
        worst_sim_total = peak_daily_cost * sim_days
        st.write(f"In **{sim_days} days**, you are projected to spend between **€{best_sim_total:.2f}** and **€{worst_sim_total:.2f}**.")

    with col_graph:
        fig_daily_trend = go.Figure()
        fig_daily_trend.add_trace(go.Scatter(x=timeline_df["Date"], y=timeline_df["Daily Consumption (kWh)"], mode="lines+markers", name="Actual Daily Consumption (kWh)", line=dict(color="#0d47a1", width=3.5), hovertemplate="<b>Date:</b> %{x}<br><b>Actual Draw:</b> %{y:.2f} kWh<extra></extra>"))
        fig_daily_trend.add_trace(go.Scatter(x=timeline_df["Date"], y=timeline_df["Daily Projected Trend (kWh)"], mode="lines", name="Projected Daily Trend (Weighted)", line=dict(color="#1565c0", width=2.5, dash="dot"), hovertemplate="<b>Date:</b> %{x}<br><b>Status:</b> %{customdata}<br><b>Draw:</b> %{y:.2f} kWh<extra></extra>", customdata=timeline_df["Status"]))
        if not next_month_df.empty:
            fig_daily_trend.add_vline(x=next_month_df["Date"].iloc[0], line_width=1.5, line_dash="dash", line_color="#b0bec5")
        fig_daily_trend.update_layout(title=f"Daily Consumption & Forecast Trend (kWh) - Extending into {next_month_name}", xaxis_title="Date", yaxis_title="Energy Draw (kWh)", legend_orientation="h")
        st.plotly_chart(fig_daily_trend, use_container_width=True)

        st.markdown("---")
        fig_cum = go.Figure()
        fig_cum.add_trace(go.Scatter(x=timeline_df["Date"], y=timeline_df["Accumulated Cost Actual (€)"], mode="lines+markers", name="Actual Cumulative Spend (€)", line=dict(color="#1b5e20", width=4), hovertemplate="<b>Date:</b> %{x}<br><b>Actual Cumulative spend:</b> €%{y:,.2f}<extra></extra>"))
        fig_cum.add_trace(go.Scatter(x=timeline_df["Date"], y=timeline_df["Accumulated Cost Projected (€)"], mode="lines", name="Projected Spend Climb (€)", line=dict(color="#43a047", width=3, dash="dot"), hovertemplate="<b>Date:</b> %{x}<br><b>Status:</b> %{customdata}<br><b>Cumulative Spend:</b> €%{y:,.2f}<extra></extra>", customdata=timeline_df["Status"]))
        if not next_month_df.empty:
            fig_cum.add_vline(x=next_month_df["Date"].iloc[0], line_width=1.5, line_dash="dash", line_color="#b0bec5")
        fig_cum.update_layout(title=f"Projective Cumulative Spend Climb (Standing Charges Included) - Resets at 1st of {next_month_name}", xaxis_title="Date", yaxis_title="Total Bill Accumulation (€)", legend_orientation="h")
        st.plotly_chart(fig_cum, use_container_width=True)

with tabs[2]:
    st.subheader("Tariff Autopilot & Load Shifting Simulator")
    st.write("Simulate shifting appliance loads to lower-cost periods based on your historical data.")
    colctrl1, colctrl2 = st.columns(2)
    with colctrl1:
        laundry_shift_pct = st.slider("Percentage of Laundry to Shift to Night (23:00 - 08:00)", 0, 100, 40)
    with colctrl2:
        ev_shift_pct = st.slider("Percentage of EV Charging to Shift to Night (23:00 - 08:00)", 0, 100, 75)

    night_rate = rates.get("night_rate", rates.get("flat_rate", 0.15))
    day_rate = rates.get("day_rate", rates.get("flat_rate", 0.2931))
    total_laundry_kwh = df_filtered["app_laundry"].sum()
    total_ev_kwh = df_filtered["app_ev"].sum()
    original_laundry_cost = total_laundry_kwh * avg_import_rate
    original_ev_cost = total_ev_kwh * avg_import_rate
    shifted_laundry_cost = total_laundry_kwh * (1 - laundry_shift_pct / 100) * day_rate + total_laundry_kwh * (laundry_shift_pct / 100) * night_rate
    shifted_ev_cost = total_ev_kwh * (1 - ev_shift_pct / 100) * day_rate + total_ev_kwh * (ev_shift_pct / 100) * night_rate
    savings_laundry = max(0.0, original_laundry_cost - shifted_laundry_cost)
    savings_ev = max(0.0, original_ev_cost - shifted_ev_cost)
    total_simulated_savings = savings_laundry + savings_ev
    m1, m2, m3 = st.columns(3)
    m1.metric("Laundry Shifting Savings", f"€{savings_laundry:.2f}")
    m2.metric("EV Charger Shifting Savings", f"€{savings_ev:.2f}")
    m3.metric("Total Period Arbitrage Saved", f"€{total_simulated_savings:.2f}")

with tabs[3]:
    st.subheader("Thermostat Auditing & Heat Pump ROI Engine")
    thermostat_diff = st.slider("Lower Thermostat Temperature Setting by (°C)", 0.0, 4.0, 1.5, step=0.5)
    heating_savings_pct = thermostat_diff * 0.10
    total_heating_kwh = df_filtered["app_heating"].sum()
    original_heating_cost = total_heating_kwh * avg_import_rate
    new_heating_cost = original_heating_cost * (1 - heating_savings_pct)
    heating_savings_val = original_heating_cost - new_heating_cost
    c1, c2 = st.columns(2)
    c1.metric("Estimated Original Heating Bill", f"€{original_heating_cost:.2f}")
    c2.metric("Adjusted Heating Bill", f"€{new_heating_cost:.2f}", delta=f"-€{heating_savings_val:.2f}")

    hp_install_cost = st.number_input("Est. Net Installation Cost After Subsidies/Grants (€)", value=6500.0)
    hp_annual_kwh_saved = total_heating_kwh * (1 if selected_month == "All Months" else 12) * (1 - 1.0 / 3.5)
    hp_annual_financial_saved = hp_annual_kwh_saved * avg_import_rate
    hp_payback_years = hp_install_cost / hp_annual_financial_saved if hp_annual_financial_saved > 0 else 99
    h1, h2, h3 = st.columns(3)
    h1.metric("Annual Space/Water Heating Saved", f"{hp_annual_kwh_saved:,.0f} kWh")
    h2.metric("Est. Annual Cash Savings", f"€{hp_annual_financial_saved:,.2f}")
    h3.metric("Investment Payback Period", f"{hp_payback_years:.1f} Years")

with tabs[4]:
    st.subheader("Solar & Smart Battery Dispatch Simulator")
    battery_capacity = st.slider("Select Battery Capacity (kWh)", 0.0, 15.0, 5.0, step=1.0)
    roundtrip_efficiency = st.slider("Est. Roundtrip Battery Efficiency (%)", 70, 95, 85) / 100.0
    daily_cycles = st.selectbox("Dispatch Cycles per Day", [1, 2], index=0)
    night_rate = rates.get("night_rate", rates.get("flat_rate", 0.15))
    peak_rate = rates.get("peak_rate", rates.get("flat_rate", 0.2931))
    daily_arbitrage_profit = battery_capacity * (peak_rate - night_rate) * roundtrip_efficiency * daily_cycles
    monthly_profit = max(0.0, daily_arbitrage_profit * 30.4)
    annual_profit = monthly_profit * 12
    b1, b2 = st.columns(2)
    b1.metric("Estimated Monthly Bill Offset", f"€{monthly_profit:.2f}")
    b1.metric("Estimated Annual Yield", f"€{annual_profit:.2f}")

with tabs[5]:
    st.subheader("Phantom Load Hunter / Standby Audit")
    avg_vampire_kwh = df_filtered["app_always_on"].mean()
    avg_vampire_watts = avg_vampire_kwh * 2.0 * 1000.0
    annual_vampire_cost = df_filtered["app_always_on"].sum() * avg_import_rate * (1 if selected_month == "All Months" else 12)
    st.markdown(f"Your audited continuous baseload is **{avg_vampire_watts:.1f} W**, costing about **€{annual_vampire_cost:.2f}/year**.")

with tabs[6]:
    st.subheader("Appliance Diagnostics")
    diag = pd.DataFrame({
        "Appliance": ["Always On", "EV", "Heating", "Cooking", "Laundry", "Entertainment", "Misc"],
        "kWh": [
            df_filtered["app_always_on"].sum(),
            df_filtered["app_ev"].sum(),
            df_filtered["app_heating"].sum(),
            df_filtered["app_cooking"].sum(),
            df_filtered["app_laundry"].sum(),
            df_filtered["app_entertainment"].sum(),
            df_filtered["app_misc"].sum(),
        ]
    })
    diag["Estimated Cost (€)"] = diag["kWh"] * avg_import_rate
    st.dataframe(diag.style.format({"kWh": "{:.2f}", "Estimated Cost (€)": "€{:.2f}"}), use_container_width=True, hide_index=True)

with tabs[7]:
    st.subheader("Chore Task Master")
    st.write("Use this tab as a simple view of laundry-heavy energy patterns that could be shifted or combined.")
    chore_daily = df_filtered.groupby("date_only")["app_laundry"].sum().reset_index()
    fig_chore = px.bar(chore_daily, x="date_only", y="app_laundry", title="Daily Laundry / Chore Load (kWh)")
    st.plotly_chart(fig_chore, use_container_width=True)

with tabs[8]:
    st.subheader("Heat Density Grid")
    heat_df = df_filtered.copy()
    heat_df["Hour"] = heat_df["reading_at"].dt.hour
    heat_df["Date"] = pd.to_datetime(heat_df["date_only"])
    pivot = heat_df.pivot_table(index="Hour", columns="Date", values="estimated_kwh", aggfunc="sum")
    fig_heat = px.imshow(pivot, aspect="auto", color_continuous_scale="YlOrRd", title="Usage Heat Density Grid")
    st.plotly_chart(fig_heat, use_container_width=True)
