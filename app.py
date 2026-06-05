import csv
import io
import os
import calendar
from datetime import date, datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
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

    result = {
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
        "standing_daily": round(standing_daily, 4),
        "pso_daily": round(pso_daily, 4),
    }
    return result


def add_daily_cost_columns(df, annual_standing_charge, annual_pso, unit_rate_eur):
    if df.empty:
        return df.copy()

    out = df.copy()
    standing_daily = annual_standing_charge / 365.0
    pso_daily = annual_pso / 365.0

    out["usage_cost"] = out["kwh"] * unit_rate_eur
    out["standing_cost"] = standing_daily
    out["pso_cost"] = pso_daily
    out["daily_total_cost"] = out["usage_cost"] + out["standing_cost"] + out["pso_cost"]
    out["cumulative_cost"] = out.groupby("month")["daily_total_cost"].cumsum()
    return out


def theme_layout(fig, title):
    fig.update_layout(
        title=title,
        template="plotly_white",
        margin=dict(l=20, r=20, t=60, b=20),
        height=420,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


st.set_page_config(page_title="FloEn", layout="wide")
st.title("FloEn")
st.caption("Electricity dashboard with ESB upload, month selector, and projected current-month bill")

required = [
    "APP_PASSWORD",
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

elec_unit_rate_eur = float(get_secret("ELEC_UNIT_RATE_CENT")) / 100.0
elec_standing_annual = float(get_secret("ELEC_STANDING_CHARGE_URBAN_ANNUAL"))
elec_pso_annual = float(get_secret("ELEC_PSO_LEVY_ANNUAL"))

with st.sidebar:
    st.header("Upload")
    uploaded_file = st.file_uploader("Upload ESB CSV file", type=["csv"])
    st.caption("Upload your ESB electricity usage CSV.")

    st.divider()
    st.header("Tariffs in use")
    st.write(f"Unit rate: €{elec_unit_rate_eur:.4f}/kWh")
    st.write(f"Standing charge: €{elec_standing_annual:.2f}/year")
    st.write(f"PSO levy: €{elec_pso_annual:.2f}/year")

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

        df = to_df(daily_rows)
        df = add_daily_cost_columns(df, elec_standing_annual, elec_pso_annual, elec_unit_rate_eur)

        st.session_state.electricity_df = df
        st.session_state.raw_records = records
        st.session_state.source_name = uploaded_file.name
        st.session_state.detected_date_key = detected_date_key
        st.session_state.detected_value_key = detected_value_key
        st.success(f"Loaded electricity file: {uploaded_file.name}")
    except Exception as e:
        st.error(f"Upload failed: {e}")
        st.stop()

if st.session_state.electricity_df.empty:
    st.info("Unlock the app and upload your ESB CSV file to begin.")
    st.stop()

df = st.session_state.electricity_df.copy()
available_months = sorted(df["month"].dropna().unique().tolist(), reverse=True)

selected_month = st.selectbox(
    "Select month",
    available_months,
    format_func=month_label_from_key,
)

month_df = df[df["month"] == selected_month].copy()
month_result = calc_electricity_month(
    month_df,
    elec_standing_annual,
    elec_pso_annual,
    elec_unit_rate_eur,
)

st.write(f"Source file: {st.session_state.source_name}")
st.write(
    f"Detected date column: `{st.session_state.detected_date_key}` | "
    f"Detected value column: `{st.session_state.detected_value_key}`"
)

top1, top2, top3, top4 = st.columns(4)
top1.metric("Actual kWh", f"{month_result['actual_kwh']:.2f}")
top2.metric("Usage charge", f"€{month_result['actual_usage_cost']:.2f}")

if month_result["is_current_month"]:
    top3.metric("Projected kWh", f"{month_result['projected_kwh']:.2f}")
    top4.metric("Projected bill", f"€{month_result['projected_total']:.2f}")
    st.info(
        f"Projection for {month_label_from_key(selected_month)} is based on "
        f"{month_result['actual_days_with_data']} day(s) of data out of "
        f"{month_result['days_in_month']} days."
    )
else:
    top3.metric("Standing + PSO", f"€{month_result['actual_standing_cost'] + month_result['actual_pso_cost']:.2f}")
    top4.metric("Month total", f"€{month_result['actual_total']:.2f}")

chart_tab, breakdown_tab, data_tab = st.tabs(["Charts", "Breakdown", "Data"])

with chart_tab:
    c1, c2 = st.columns(2)

    line_fig = px.line(
        month_df,
        x="date",
        y="kwh",
        markers=True,
        labels={"date": "Date", "kwh": "kWh"},
    )
    line_fig.update_traces(line_color="#0f766e", marker_color="#0f766e")
    theme_layout(line_fig, f"Daily Usage Trend — {month_label_from_key(selected_month)}")
    c1.plotly_chart(line_fig, use_container_width=True)

    bar_fig = px.bar(
        month_df,
        x="date",
        y="daily_total_cost",
        labels={"date": "Date", "daily_total_cost": "€"},
    )
    bar_fig.update_traces(marker_color="#2563eb")
    theme_layout(bar_fig, f"Daily Cost — {month_label_from_key(selected_month)}")
    c2.plotly_chart(bar_fig, use_container_width=True)

    c3, c4 = st.columns(2)

    area_fig = go.Figure()
    area_fig.add_trace(
        go.Scatter(
            x=month_df["date"],
            y=month_df["cumulative_cost"],
            mode="lines",
            fill="tozeroy",
            line=dict(color="#7c3aed", width=3),
            name="Cumulative cost",
        )
    )
    theme_layout(area_fig, f"Cumulative Cost — {month_label_from_key(selected_month)}")
    c3.plotly_chart(area_fig, use_container_width=True)

    pie_values = (
        [month_result["projected_usage_cost"], month_result["projected_standing_cost"], month_result["projected_pso_cost"]]
        if month_result["is_current_month"]
        else [month_result["actual_usage_cost"], month_result["actual_standing_cost"], month_result["actual_pso_cost"]]
    )
    donut_fig = go.Figure(
        data=[
            go.Pie(
                labels=["Usage", "Standing charge", "PSO levy"],
                values=pie_values,
                hole=0.55,
                marker=dict(colors=["#0f766e", "#2563eb", "#f59e0b"]),
            )
        ]
    )
    theme_layout(donut_fig, f"Bill Breakdown — {month_label_from_key(selected_month)}")
    c4.plotly_chart(donut_fig, use_container_width=True)

    c5, c6 = st.columns(2)

    scatter_fig = px.scatter(
        month_df,
        x="kwh",
        y="daily_total_cost",
        size="kwh",
        color="avg_kwh_per_hour",
        hover_data=["date"],
        labels={"kwh": "Daily kWh", "daily_total_cost": "Daily cost (€)", "avg_kwh_per_hour": "Avg kWh/hour"},
        color_continuous_scale="Tealgrn",
    )
    theme_layout(scatter_fig, f"Usage vs Cost — {month_label_from_key(selected_month)}")
    c5.plotly_chart(scatter_fig, use_container_width=True)

    hist_fig = px.histogram(
        month_df,
        x="kwh",
        nbins=min(12, max(5, len(month_df))),
        labels={"kwh": "Daily kWh"},
    )
    hist_fig.update_traces(marker_color="#dc2626")
    theme_layout(hist_fig, f"Usage Distribution — {month_label_from_key(selected_month)}")
    c6.plotly_chart(hist_fig, use_container_width=True)

    st.subheader("Monthly comparison")
    monthly_summary = (
        df.groupby("month", as_index=False)
        .agg(
            kwh=("kwh", "sum"),
            usage_cost=("usage_cost", "sum"),
            standing_cost=("standing_cost", "sum"),
            pso_cost=("pso_cost", "sum"),
            total_cost=("daily_total_cost", "sum"),
        )
        .sort_values("month")
    )
    monthly_summary["month_label"] = monthly_summary["month"].apply(month_label_from_key)

    monthly_fig = px.bar(
        monthly_summary,
        x="month_label",
        y=["usage_cost", "standing_cost", "pso_cost"],
        labels={"value": "€", "month_label": "Month", "variable": "Component"},
        barmode="stack",
    )
    theme_layout(monthly_fig, "Monthly Cost Components")
    st.plotly_chart(monthly_fig, use_container_width=True)

with breakdown_tab:
    left, right = st.columns(2)

    if month_result["is_current_month"]:
        breakdown_df = pd.DataFrame(
            [
                {"component": "Usage", "amount": month_result["projected_usage_cost"]},
                {"component": "Standing charge", "amount": month_result["projected_standing_cost"]},
                {"component": "PSO levy", "amount": month_result["projected_pso_cost"]},
                {"component": "Projected total", "amount": month_result["projected_total"]},
            ]
        )
    else:
        breakdown_df = pd.DataFrame(
            [
                {"component": "Usage", "amount": month_result["actual_usage_cost"]},
                {"component": "Standing charge", "amount": month_result["actual_standing_cost"]},
                {"component": "PSO levy", "amount": month_result["actual_pso_cost"]},
                {"component": "Month total", "amount": month_result["actual_total"]},
            ]
        )

    left.subheader("Cost breakdown")
    left.dataframe(breakdown_df, use_container_width=True)

    summary_rows = [
        {"item": "Unit rate", "value": f"€{elec_unit_rate_eur:.4f}/kWh"},
        {"item": "Standing charge daily", "value": f"€{month_result['standing_daily']:.4f}"},
        {"item": "PSO levy daily", "value": f"€{month_result['pso_daily']:.4f}"},
        {"item": "Days with data", "value": str(month_result["actual_days_with_data"])},
        {"item": "Days in month", "value": str(month_result["days_in_month"])},
    ]
    right.subheader("Month details")
    right.dataframe(pd.DataFrame(summary_rows), use_container_width=True)

with data_tab:
    st.subheader("Selected month data")
    st.dataframe(
        month_df[["date", "kwh", "avg_kwh_per_hour", "usage_cost", "standing_cost", "pso_cost", "daily_total_cost", "cumulative_cost"]],
        use_container_width=True,
    )

    st.subheader("All processed data")
    st.dataframe(df, use_container_width=True)
