#!/usr/bin/env python3
import argparse
import csv
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:142.0) Gecko/20100101 Firefox/142.0"
BASE_URL = "https://myaccount.esbnetworks.ie"
LOGIN_URL = "https://login.esbnetworks.ie"


def debug_print(enabled, *args):
    if enabled:
        print(*args, file=sys.stderr)


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def safe_sleep(min_seconds: float, max_seconds: float, enabled: bool):
    delay = random.uniform(min_seconds, max_seconds)
    debug_print(enabled, f"[debug] sleeping for {delay:.2f}s")
    time.sleep(delay)


def get_settings_from_html(content: bytes) -> dict:
    match = re.search(rb"var SETTINGS = (\{.*?\});", content, re.DOTALL)
    if not match:
        raise RuntimeError("Could not extract SETTINGS payload from ESB landing page.")
    return json.loads(match.group(1).decode("utf-8"))


def login_and_download(session: requests.Session, username: str, password: str, mprn: str, search_type: str, debug: bool) -> str:
    debug_print(debug, "Request 1: GET landing page")
    r1 = session.get(f"{BASE_URL}/", allow_redirects=True, timeout=(15, 15))
    r1.raise_for_status()
    settings = get_settings_from_html(r1.content)
    csrf = settings["csrf"]
    trans_id = settings["transId"]
    cookies1 = session.cookies.get_dict()

    safe_sleep(2, 4, debug)
    debug_print(debug, "Request 2: POST SelfAsserted")
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

    debug_print(debug, "Request 3: GET confirmed signin page")
    r3 = session.get(
        f"{LOGIN_URL}/esbntwkscustportalprdb2c01.onmicrosoft.com/B2C_1A_signup_signin/api/CombinedSigninAndSignup/confirmed",
        params={"rememberMe": False, "csrf_token": csrf, "tx": trans_id, "p": "B2C_1A_signup_signin"},
        timeout=(15, 15),
    )
    r3.raise_for_status()
    soup3 = BeautifulSoup(r3.content, "html.parser")
    form = soup3.find("form", {"id": "auto"})
    if not form:
        title = soup3.find("title")
        title_text = title.text.strip() if title else "Unknown page"
        raise RuntimeError(f"Could not complete login flow. ESB may have challenged the session. Page title: {title_text}")
    login_url = form.get("action")
    state = form.find("input", {"name": "state"}).get("value")
    client_info = form.find("input", {"name": "client_info"}).get("value")
    code = form.find("input", {"name": "code"}).get("value")

    safe_sleep(1, 2, debug)
    debug_print(debug, "Request 4: POST signin-oidc")
    r4 = session.post(
        login_url,
        data={"state": state, "client_info": client_info, "code": code},
        allow_redirects=False,
        timeout=(15, 15),
    )
    if r4.status_code not in (200, 302):
        raise RuntimeError(f"OIDC signin step returned unexpected status {r4.status_code}")

    debug_print(debug, "Request 5: GET portal home")
    r5 = session.get(f"{BASE_URL}", timeout=(15, 15))
    r5.raise_for_status()

    safe_sleep(1, 2, debug)
    debug_print(debug, "Request 6: GET HistoricConsumption page")
    r6 = session.get(f"{BASE_URL}/Api/HistoricConsumption", timeout=(15, 15))
    r6.raise_for_status()

    safe_sleep(1, 2, debug)
    debug_print(debug, "Request 7: GET anti-forgery token")
    r7 = session.get(
        f"{BASE_URL}/af/t",
        headers={"X-Returnurl": f"{BASE_URL}/Api/HistoricConsumption"},
        timeout=(15, 15),
    )
    r7.raise_for_status()
    file_download_token = r7.json()["token"]

    debug_print(debug, "Request 8: POST DownloadHdfPeriodic")
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


def csv_to_json_records(csv_text: str):
    return list(csv.DictReader(csv_text.splitlines()))


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
        for fmt in ("%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except Exception:
                pass
        return None

    def parse_float(value: str):
        if value is None:
            return None
        value = value.strip().replace(",", "")
        try:
            return float(value)
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

    out = []
    for day in sorted(daily):
        kwh = round(daily[day], 4)
        out.append({"date": day, "kwh": kwh, "avg_kwh_per_hour": round(kwh / 24.0, 4)})
    return out


def add_costs(daily_rows, unit_rate, standing_charge):
    if not daily_rows:
        return []
    enriched = []
    avg_daily = sum(r["kwh"] for r in daily_rows) / len(daily_rows)
    projected_month_cost = (avg_daily * unit_rate + standing_charge) * 30
    for row in daily_rows:
        daily_cost = row["kwh"] * unit_rate + standing_charge
        enriched.append({
            **row,
            "unit_rate": round(unit_rate, 6),
            "standing_charge": round(standing_charge, 6),
            "daily_cost": round(daily_cost, 4),
            "projected_month_cost": round(projected_month_cost, 2),
        })
    return enriched


def write_csv(path: Path, rows):
    if not rows:
        path.write_text("", 
