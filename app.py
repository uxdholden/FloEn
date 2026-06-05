import csv
import io
import os
import calendar
from datetime import date, datetime

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


def aggregate_daily_electricity(records):
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
        daily.setdefault(day, 0.0)
        daily[day] += val

    rows = []
    for day in sorted(daily):
        kwh = round(daily[day], 4)
        rows.append(
            {
                "date": day,
                "month": day.strftime("%Y-%m"),
                "kwh": kwh,
                "avg_kwh_per_hour": round(kwh / 24.0, 4),
            }
        )
    return rows, date_key, value_key


def to_df(rows):
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.sort_values("date")
    return df


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def month_label_from_key(month_key: str) -> str:
    dt = datetime.strptime(month_key, "%Y-%m")
    return dt.strftime("%B %Y")


def month_start_end(month_key: str):
    dt = datetime.strptime(month_key, "%Y-%m")
    start = date(dt.year, dt.month, 1)
    end = date(dt.year, dt.month, days_in_month(dt.year, dt.month))
    return start, end


def calc_electricity_month(month_df, annual_standing_charge, annual_pso, unit_rate_eur):
    if month_df.empty:
        return None

    month_key = month_df["month"].iloc[0]
    start, end = month_start_end(month_key)
    total_days_in_month = (end - start).days + 1
    today = date.today()
    is_current_month = today.strftime("%Y-%m") == month_key

    actual_kwh = float(month_df["kwh"].sum())
    actual_days_with_data = int(month_df["date"].dt.date.nunique())

    standing_daily = annual_standing_charge / 365.0
    pso_daily = annual_pso / 365.0

    actual_usage_cost = actual_kwh * unit_rate_eur
    actual_standing_cost = standing_daily * actual_days_with_data
    actual_pso_cost = pso_daily * actual_days_with_data
    actual_total = actual_usage_cost + actual_standing_cost + actual_pso_cost

    projected_kwh = actual_kwh
    projected_usage_cost = actual_usage_cost
    projected_standing_cost = standing_daily * total_days_in_month
    projected_pso_cost = pso_daily * total_days_in_month

    if is_current_month and actual_days_with_data > 0:
        avg_daily_kwh = actual_kwh / actual_days_with_data
        projected_kwh = avg_daily_kwh * total_days_in_month
        projected_usage_cost = projected_kwh * unit_rate_eur

    projected_total = projected_usage_cost + projected_standing_cost + projected_pso_cost

    return {
        "month": month_key,
        "is_current_month": is_current_month,
        "actual_days_with_data": actual_days_with_data,
        "days_in_month": total_days_in_month,
        "actual_kwh": round(actual_kwh, 2),
        "actual_usage_cost": round(actual_usage_cost, 2),
        "actual_standing_cost": round(actual_standing_cost, 2),
        "actual_pso_cost": round(actual_pso_cost, 2),
        "actual_total": round(actual_total, 2),
        "projected_kwh": round(projected_kwh, 2),
        "projected_usage_cost": round(projected_usage_cost, 2),
        "projected_standing_cost": round(projected_standing_cost, 2),
        "projected_pso_cost": round(projected_pso_cost, 2),
        "projected_total": round(projected_total, 2),
    }


def calc_gas_month(month_key, gas_kwh, annual_standing_charge, annual_carbon_tax, unit_rate_eur):
    dt = datetime.strptime(month_key, "%Y-%m")
    total_days = days_in_month(dt.year, dt.month)
    today = date.today()
    is_current_month = today.strftime("%Y-%m") == month_key

    standing_daily = annual_standing_charge / 365.0
    carbon_daily = annual_carbon_tax / 365.0

    usage_cost = gas_kwh * unit_rate_eur
    full_standing = standing_daily * total_days
    full_carbon = carbon_daily * total_days
    full_total = usage_cost + full_standing + full_carbon

    return {
        "month": month_key,
        "is_current_month": is_current_month,
        "kwh": round(gas_kwh, 2),
        "usage_cost": round(usage_cost, 2),
        "standing_cost": round(full_standing, 2),
        "carbon_cost": round(full_carbon, 2),
        "total": round(full_total, 2),
    }


@st.cache_data
def convert_df_to_csv(df):
    return df.to_csv(index=False).encode("utf-8")


st.set_page_config(page_title="FloEn", layout="wide")
st.title("FloEn")
st.caption("Electricity + gas dashboard with ESB upload and manual gas entry")

required = [
    "APP_PASSWORD",
    "GAS_UNIT_RATE_CENT",
    "GAS_STANDING_CHARGE_ANNUAL",
    "GAS_CARBON_TAX_ANNUAL",
    "ELEC_UNIT_RATE_CENT",
    "ELEC_STANDING_CHARGE_URBAN_ANNUAL",
    "ELEC_PSO_LEVY_ANNUAL",
]
missing = [k for k in required if get_secret(k) in (None, "")]
if missing:
    st.error("Missing required secrets: " + ", ".join(missing))
    st.info("Add them in Streamlit Secrets before using the app.")
    st.stop()

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if "electricity_df" not in st.session_state:
    st.session_state.electricity_df = pd.DataFrame()

if "raw_records" not in st.session_state:
    st.session_state.raw_records = []

if "source_name" not in st.session_state:
    st.session_state.source_name = None

if "gas_entries" not in st.session_state:
    st.session_state.gas_entries = []

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

gas_unit_rate_eur = float(get_secret("GAS_UNIT_RATE_CENT")) / 100.0
gas_standing_annual = float(get_secret("GAS_STANDING_CHARGE_ANNUAL"))
gas_carbon_annual = float(get_secret("GAS_CARBON_TAX_ANNUAL"))

elec_unit_rate_eur = float(get_secret("ELEC_UNIT_RATE_CENT")) / 100.0
elec_standing_annual = float(get_secret("ELEC_STANDING_CHARGE_URBAN_ANNUAL"))
elec_pso_annual = float(get_secret("ELEC_PSO_LEVY_ANNUAL"))

with st.sidebar:
    st.header("Electricity")
    uploaded_file = st.file_uploader("Upload ESB CSV file", type=["csv"])
    st.caption("Upload your ESB electricity usage CSV.")

    st.divider()
    st.header("Gas entries")
    gas_upload = st.file_uploader("Import gas CSV", type=["csv"], key="gas_csv_upload")
    if gas_upload is not None:
        try:
            gas_df_import = pd.read_csv(gas_upload)
            required_cols = {"month", "gas_kwh"}
            if required_cols.issubset(set(gas_df_import.columns)):
                st.session_state.gas_entries = gas_df_import[["month", "gas_kwh"]].to_dict("records")
                st.success("Gas CSV imported.")
            else:
                st.error("Gas CSV must contain: month, gas_kwh")
        except Exception as e:
            st.error(f"Could not import gas CSV: {e}")

    st.divider()
    st.header("Tariffs in use")
    st.write(f"Electricity unit rate: €{elec_unit_rate_eur:.4f}/kWh")
    st.write(f"Electricity standing charge: €{elec_standing_annual:.2f}/year")
    st.write(f"Electricity PSO levy: €{elec_pso_annual:.2f}/year")
    st.write(f"Gas unit rate: €{gas_unit_rate_eur:.4f}/kWh")
    st.write(f"Gas standing charge: €{gas_standing_annual:.2f}/year")
    st.write(f"Gas carbon tax: €{gas_carbon_annual:.2f}/year")

if uploaded_file is not None:
    try:
        records = parse_uploaded_csv(uploaded_file)
        daily_rows, detected_date_key, detected_value_key = aggregate_daily_electricity(records)

        if not daily_rows:
            st.error("Could not detect usable electricity date/value columns in the uploaded CSV.")
            if records:
                st.write("Detected columns:")
                st.write(list(records[0].keys()))
            st.stop()

        st.session_state.electricity_df = to_df(daily_rows)
        st.session_state.raw_records = records
        st.session_state.source_name = uploaded_file.name
        st.session_state.detected_date_key = detected_date_key
        st.session_state.detected_value_key = detected_value_key
        st.success(f"Loaded electricity file: {uploaded_file.name}")
    except Exception as e:
        st.error(f"Upload failed: {e}")
        st.stop()

st.subheader("Gas monthly input")
with st.form("gas_monthly_form"):
    c1, c2 = st.columns(2)
    current_month = date.today().strftime("%Y-%m")
    gas_month = c1.text_input("Month (YYYY-MM)", value=current_month)
    gas_kwh = c2.number_input("Gas usage for month (kWh)", min_value=0.0, value=0.0, step=1.0)
    add_gas = st.form_submit_button("Add / update gas month")

if add_gas:
    try:
        datetime.strptime(gas_month, "%Y-%m")
        existing = {row["month"]: row for row in st.session_state.gas_entries}
        existing[gas_month] = {"month": gas_month, "gas_kwh": float(gas_kwh)}
        st.session_state.gas_entries = sorted(existing.values(), key=lambda x: x["month"])
        st.success(f"Saved gas entry for {gas_month}.")
    except ValueError:
        st.error("Month must be in YYYY-MM format.")

gas_entries_df = pd.DataFrame(st.session_state.gas_entries)
if not gas_entries_df.empty:
    gas_csv = convert_df_to_csv(gas_entries_df)
    st.download_button(
        "Download gas entries CSV",
        gas_csv,
        file_name="gas_entries.csv",
        mime="text/csv",
    )

available_months = set()

if not st.session_state.electricity_df.empty:
    available_months.update(st.session_state.electricity_df["month"].dropna().unique().tolist())

if not gas_entries_df.empty:
    available_months.update(gas_entries_df["month"].dropna().unique().tolist())

if not available_months:
    st.info("Upload an electricity CSV and/or add a gas month to begin.")
    st.stop()

available_months = sorted(available_months, reverse=True)
selected_month = st.selectbox(
    "Select month",
    available_months,
    format_func=month_label_from_key,
)

electricity_result = None
gas_result = None

if not st.session_state.electricity_df.empty:
    elec_month_df = st.session_state.electricity_df[
        st.session_state.electricity_df["month"] == selected_month
    ].copy()
    if not elec_month_df.empty:
        electricity_result = calc_electricity_month(
            elec_month_df,
            elec_standing_annual,
            elec_pso_annual,
            elec_unit_rate_eur,
        )

if not gas_entries_df.empty:
    gas_match = gas_entries_df[gas_entries_df["month"] == selected_month]
    if not gas_match.empty:
        gas_kwh_selected = float(gas_match.iloc[0]["gas_kwh"])
        gas_result = calc_gas_month(
            selected_month,
            gas_kwh_selected,
            gas_standing_annual,
            gas_carbon_annual,
            gas_unit_rate_eur,
        )

st.subheader(f"Summary for {month_label_from_key(selected_month)}")

col1, col2, col3 = st.columns(3)

combined_actual = 0.0
combined_projected = 0.0

if electricity_result:
    if electricity_result["is_current_month"]:
        elec_total_to_show = electricity_result["projected_total"]
        elec_label = "Electricity Projected"
    else:
        elec_total_to_show = electricity_result["actual_total"]
        elec_label = "Electricity Total"

    combined_actual += electricity_result["actual_total"]
    combined_projected += electricity_result["projected_total"]

    col1.metric(elec_label, f"€{elec_total_to_show:.2f}")
else:
    col1.metric("Electricity", "—")

if gas_result:
    combined_actual += gas_result["total"]
    combined_projected += gas_result["total"]
    col2.metric("Gas Total", f"€{gas_result['total']:.2f}")
else:
    col2.metric("Gas", "—")

if selected_month == date.today().strftime("%Y-%m"):
    col3.metric("Combined Projected", f"€{combined_projected:.2f}")
else:
    col3.metric("Combined Total", f"€{combined_actual:.2f}")

tab1, tab2, tab3, tab4 = st.tabs(["Combined", "Electricity", "Gas", "Data"])

with tab1:
    rows = []
    if electricity_result:
        rows.append(
            {
                "source": "Electricity",
                "usage_cost": electricity_result["projected_usage_cost"] if electricity_result["is_current_month"] else electricity_result["actual_usage_cost"],
                "fixed_charge_1": electricity_result["projected_standing_cost"] if electricity_result["is_current_month"] else electricity_result["actual_standing_cost"],
                "fixed_charge_2": electricity_result["projected_pso_cost"] if electricity_result["is_current_month"] else electricity_result["actual_pso_cost"],
                "total": electricity_result["projected_total"] if electricity_result["is_current_month"] else electricity_result["actual_total"],
            }
        )
    if gas_result:
        rows.append(
            {
                "source": "Gas",
                "usage_cost": gas_result["usage_cost"],
                "fixed_charge_1": gas_result["standing_cost"],
                "fixed_charge_2": gas_result["carbon_cost"],
                "total": gas_result["total"],
            }
        )
    if rows:
        combined_df = pd.DataFrame(rows)
        st.dataframe(combined_df, use_container_width=True)

with tab2:
    if electricity_result:
        st.write(f"Electricity source file: {st.session_state.source_name}")
        st.write(
            f"Detected date column: `{st.session_state.detected_date_key}` | "
            f"Detected value column: `{st.session_state.detected_value_key}`"
        )

        e1, e2, e3, e4 = st.columns(4)
        e1.metric("Actual kWh", f"{electricity_result['actual_kwh']:.2f}")
        e2.metric("Usage charge", f"€{(electricity_result['projected_usage_cost'] if electricity_result['is_current_month'] else electricity_result['actual_usage_cost']):.2f}")
        e3.metric("Standing charge", f"€{(electricity_result['projected_standing_cost'] if electricity_result['is_current_month'] else electricity_result['actual_standing_cost']):.2f}")
        e4.metric("PSO levy", f"€{(electricity_result['projected_pso_cost'] if electricity_result['is_current_month'] else electricity_result['actual_pso_cost']):.2f}")

        if electricity_result["is_current_month"]:
            st.info(
                f"Current month projection based on {electricity_result['actual_days_with_data']} day(s) of data out of {electricity_result['days_in_month']} days."
            )
            st.metric("Projected electricity total", f"€{electricity_result['projected_total']:.2f}")
        else:
            st.metric("Electricity total", f"€{electricity_result['actual_total']:.2f}")

        elec_df = st.session_state.electricity_df.copy()
        elec_df["date"] = pd.to_datetime(elec_df["date"])
        st.line_chart(elec_df.set_index("date")["kwh"])
        st.dataframe(
            elec_df[elec_df["month"] == selected_month][["date", "kwh", "avg_kwh_per_hour"]],
            use_container_width=True,
        )
    else:
        st.info("No electricity data for the selected month.")

with tab3:
    if gas_result:
        g1, g2, g3, g4 = st.columns(4)
        g1.metric("Gas kWh", f"{gas_result['kwh']:.2f}")
        g2.metric("Usage charge", f"€{gas_result['usage_cost']:.2f}")
        g3.metric("Standing charge", f"€{gas_result['standing_cost']:.2f}")
        g4.metric("Carbon tax", f"€{gas_result['carbon_cost']:.2f}")
        st.metric("Gas total", f"€{gas_result['total']:.2f}")
    else:
        st.info("No gas entry for the selected month.")

    if not gas_entries_df.empty:
        st.dataframe(gas_entries_df.sort_values("month", ascending=False), use_container_width=True)

with tab4:
    st.subheader("Electricity processed data")
    if not st.session_state.electricity_df.empty:
        st.dataframe(st.session_state.electricity_df, use_container_width=True)
    else:
        st.info("No electricity data uploaded.")

    st.subheader("Gas entries")
    if not gas_entries_df.empty:
        st.dataframe(gas_entries_df, use_container_width=True)
    else:
        st.info("No gas entries yet.")
