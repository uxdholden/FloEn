import csv
import io
import json
import os
from datetime import datetime

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

BASE_URL = "https://myaccount.esbnetworks.ie"
LOGIN_URL = "https://login.esbnetworks.ie"
DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:142.0) Gecko/20100101 Firefox/142.0"


def get_secret(name: str, default: str | None = None):
    if name in st.secrets:
        return st.secrets[name]
    return os.getenv(name, default)


def get_settings_from_html(content: bytes) -> dict:
    import re
    match = re.search(rb"var SETTINGS = (\{.*?\});", content, re.DOTALL)
    if not match:
        raise RuntimeError("Could not extract SETTINGS payload from ESB landing page.")
    return json.loads(match.group(1).decode("utf-8"))


def login_and_download(
    session: requests.Session,
    username: str,
    password: str,
    mprn: str,
    search_type: str = "intervalkw",
) -> str:
    r1 = session.get(f"{BASE_URL}/", allow_redirects=True, timeout=(15, 15))
    r1.raise_for_status()
    settings = get_settings_from_html(r1.content)
    csrf = settings["csrf"]
    trans_id = settings["transId"]
    cookies1 = session.cookies.get_dict()

    r2 = session.post(
        f"{LOGIN_URL}/esbntwkscustportalprdb2c01.onmicrosoft.com/B2C_1A_signup_signin/SelfAsserted?tx={trans_id}&p=B2C_1A_signup_signin",
        data={"signInName": username, "password": password, "request_type": "RESPONSE"},
        headers={
            "x-csrf-token": csrf,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": LOGIN_URL,
        },
        cookies={
            "x-ms-cpim-csrf": cookies1.get("x-ms-cpim-csrf"),
            "x-ms-cpim-trans": cookies1.get("x-ms-cpim-trans"),
        },
        allow_redirects=False,
        timeout=(15, 15),
    )
    if r2.status_code >= 400:
        raise RuntimeError(f"Login step failed with status {r2.status_code}: {r2.text[:300]}")

    r3 = session.get(
        f"{LOGIN_URL}/esbntwkscustportalprdb2c01.onmicrosoft.com/B2C_1A_signup_signin/api/CombinedSigninAndSignup/confirmed",
        params={
            "rememberMe": False,
            "csrf_token": csrf,
            "tx": trans_id,
            "p": "B2C_1A_signup_signin",
        },
        timeout=(15, 15),
    )
    r3.raise_for_status()
    soup3 = BeautifulSoup(r3.content, "html.parser")
    form = soup3.find("form", {"id": "auto"})
    if not form:
        raise RuntimeError("Could not complete login flow. ESB may have challenged the session.")

    login_url = form.get("action")
    state = form.find("input", {"name": "state"}).get("value")
    client_info = form.find("input", {"name": "client_info"}).get("value")
    code = form.find("input", {"name": "code"}).get("value")

    r4 = session.post(
        login_url,
        data={"state": state, "client_info": client_info, "code": code},
        allow_redirects=False,
        timeout=(15, 15),
    )
    if r4.status_code not in (200, 302):
        raise RuntimeError(f"OIDC signin step returned unexpected status {r4.status_code}")

    session.get(f"{BASE_URL}", timeout=(15, 15)).raise_for_status()
    session.get(f"{BASE_URL}/Api/HistoricConsumption", timeout=(15, 15)).raise_for_status()

    r7 = session.get(
        f"{BASE_URL}/af/t",
        headers={"X-Returnurl": f"{BASE_URL}/Api/HistoricConsumption"},
        timeout=(15, 15),
    )
    r7.raise_for_status()
    file_download_token = r7.json()["token"]

    r8 = session.post(
        f"{BASE_URL}/DataHub/DownloadHdfPeriodic",
        headers={
            "Content-Type": "application/json",
            "X-Returnurl": f"{BASE_URL}/Api/HistoricConsumption",
            "X-Xsrf-Token": file_download_token,
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/Api/HistoricConsumption",
        },
        json={"mprn": mprn, "searchType": search_type},
        timeout=(30, 30),
    )
    r8.raise_for_status()
    content = r8.content.decode("utf-8") if isinstance(r8.content, bytes) else r8.text
    if not content.startswith("MPRN"):
        raise RuntimeError("Downloaded file does not look like the expected CSV export.")
    return content


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_esb_csv_cached(
    username: str,
    password: str,
    mprn: str,
    search_type: str,
    user_agent: str,
):
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    try:
        return login_and_download(session, username, password, mprn, search_type)
    finally:
        session.close()


def csv_to_records(csv_text: str):
    return list(csv.DictReader(io.StringIO(csv_text)))


def aggregate_daily(records):
    date_candidates = [
        "Read Date and End Time",
        "Read Date",
        "Date",
        "Interval End",
        "Meter Read Date",
    ]
    value_candidates = [
        "Read Value",
        "Consumption",
        "kWh",
        "Active Import Interval (kW)",
        "Interval Read",
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
                return datetime.strptime(value.strip(), fmt).date()
            except Exception:
                pass
        return None

    def parse_float(value: str):
        if value is None:
            return None
        try:
            return float(value.strip().replace(",", ""))
        except Exception:
            return None

    date_key = next((k for k in date_candidates if records and k in records[0]), None)
    value_key = next((k for k in value_candidates if records and k in records[0]), None)
    if not date_key or not value_key:
        return []

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
    return rows


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
st.caption("Personal energy cost tracker with Streamlit-hosted secrets")

required = [
    "ESB_USERNAME",
    "ESB_PASSWORD",
    "ESB_MPRN",
    "FLOGAS_UNIT_RATE",
    "FLOGAS_STANDING_CHARGE_DAILY",
]
missing = [k for k in required if get_secret(k) in (None, "")]

if missing:
    st.error("Missing required Streamlit secrets: " + ", ".join(missing))
    st.info("Add them in your deployed app settings under Secrets.")
    st.stop()

unit_rate = float(get_secret("FLOGAS_UNIT_RATE", "0.0809"))
standing_charge = float(get_secret("FLOGAS_STANDING_CHARGE_DAILY", "0.4142"))

with st.sidebar:
    st.header("Data")
    search_type = st.selectbox("ESB search type", ["intervalkw", "consumption"], index=0)
    fetch_now = st.button("Fetch latest ESB data", type="primary")
    clear_cache = st.button("Clear fetch cache")
    if clear_cache:
        fetch_esb_csv_cached.clear()
        st.success("Fetch cache cleared.")
    st.caption("Live ESB fetches are limited to once every 24 hours unless you clear the cache.")

if "df" not in st.session_state:
    st.session_state.df = pd.DataFrame()

if fetch_now:
    try:
        with st.spinner("Loading ESB data..."):
            csv_text = fetch_esb_csv_cached(
                get_secret("ESB_USERNAME"),
                get_secret("ESB_PASSWORD"),
                get_secret("ESB_MPRN"),
                search_type,
                get_secret("ESB_USER_AGENT", DEFAULT_USER_AGENT),
            )
            records = csv_to_records(csv_text)
            daily_rows = aggregate_daily(records)
            cost_rows = add_costs(daily_rows, unit_rate, standing_charge)
            st.session_state.df = records_to_df(cost_rows)
            st.session_state.raw_count = len(records)
            st.success("Data loaded. Live fetches are limited to once every 24 hours.")
    except Exception as e:
        st.error(f"Fetch failed: {e}")

if st.session_state.df.empty:
    st.info("Press 'Fetch latest ESB data' to load your usage and cost data.")
    st.stop()

df = st.session_state.df
latest = df.iloc[-1]
avg_daily_cost = df["daily_cost"].mean()
avg_daily_kwh = df["kwh"].mean()
projected_month = latest["projected_month_cost"]
avg_hourly = avg_daily_kwh / 24 if avg_daily_kwh else 0

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
