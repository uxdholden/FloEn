def parse_interval_csv(uploaded_file):
    raw = uploaded_file.read().decode("utf-8", errors="ignore")

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
        df["interval_hours"] = 0.5
        df["estimated_kwh"] = df["read_value_kw"] * df["interval_hours"]
        df["date_only"] = df["reading_at"].dt.date
        for col in ["mprn", "meter_serial"]:
            if col not in df.columns:
                df[col] = ""
        return df[["mprn", "meter_serial", "reading_at", "read_value_kw", "estimated_kwh", "date_only"]]

    try:
        csv_df = pd.read_csv(io.StringIO(raw))
        normalized = {str(col).strip().lower(): col for col in csv_df.columns}
        required = ["mprn", "meter serial number", "read value", "read type", "read date and end time"]

        if all(col in normalized for col in required):
            df = pd.DataFrame({
                "mprn": csv_df[normalized["mprn"]].astype(str).str.strip(),
                "meter_serial": csv_df[normalized["meter serial number"]].astype(str).str.strip(),
                "read_value": pd.to_numeric(csv_df[normalized["read value"]], errors="coerce"),
                "read_type": csv_df[normalized["read type"]].astype(str).str.strip(),
                "reading_at": pd.to_datetime(
                    csv_df[normalized["read date and end time"]],
                    format="%d-%m-%Y %H:%M",
                    errors="coerce",
                ),
            })

            df = df[
                df["read_type"].str.contains(
                    r"Active Import Interval\s*\((kW|kWh)\)",
                    regex=True,
                    na=False,
                )
            ]

            parsed = finalize(df)
            if not parsed.empty:
                return parsed
    except Exception:
        pass

    pattern = re.compile(
        r"(?P<mprn>\d{11})\s*"
        r"(?P<serial>\d+)\s*"
        r"(?P<value>\d+\.\d{3,4})\s*"
        r"(?P<read_type>Active Import Interval(?:\s*\((?:kW|kWh)\)|\s+kW|\s+kWh))\s*"
        r"(?P<date>\d{2}-\d{2}-\d{4})\s+"
        r"(?P<time>\d{2}:?\d{2}|\d{4})"
    )

    rows = []
    for m in pattern.finditer(raw):
        time_text = m.group("time").replace(":", "")
        reading_at = pd.to_datetime(
            f"{m.group('date')} {time_text}",
            format="%d-%m-%Y %H%M",
            errors="coerce",
        )
        rows.append({
            "mprn": m.group("mprn"),
            "meter_serial": m.group("serial"),
            "read_value_kw": float(m.group("value")),
            "reading_at": reading_at,
        })

    if not rows:
        fallback_lines = []
        for line in raw.splitlines():
            line = line.strip()
            if "Active Import Interval" not in line:
                continue
            m = re.search(
                r"(?P<mprn>\d{11})\s*(?P<serial>\d+)\s*(?P<value>\d+\.\d{3,4})\s*Active Import Interval(?:\s*\((?:kW|kWh)\)|\s+kW|\s+kWh)\s*(?P<date>\d{2}-\d{2}-\d{4})\s+(?P<time>\d{2}:?\d{2}|\d{4})",
                line,
            )
            if m:
                time_text = m.group("time").replace(":", "")
                fallback_lines.append({
                    "mprn": m.group("mprn"),
                    "meter_serial": m.group("serial"),
                    "read_value_kw": float(m.group("value")),
                    "reading_at": pd.to_datetime(
                        f"{m.group('date')} {time_text}",
                        format="%d-%m-%Y %H%M",
                        errors="coerce",
                    ),
                })
        rows = fallback_lines

    if not rows:
        return pd.DataFrame()

    return finalize(pd.DataFrame(rows))
