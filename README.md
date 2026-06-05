# FloEn

Personal energy cost tracker with daily usage and projected cost dashboard.

## Features

- Track daily gas or electricity usage
- Calculate daily cost using flat-rate pricing
- Show projected monthly cost
- Import ESB smart meter usage data
- Keep credentials out of the public repo

## Pricing used

Gas:
- Unit rate: 8.09 cent per kWh
- Standing charge: €151.18 per year
- Carbon tax: €137.65 per year

Electricity:
- Unit rate: 26.41 cent per kWh
- Standing charge urban: €270.45 per year
- Standing charge rural: €329.82 per year
- PSO levy: €19.10 per year

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Create a local `.env` file from `.env.example`

3. Run the ESB fetch script:

```bash
python esb_fetch_secure.py --debug
```

4. Use the output files for the Streamlit dashboard

## Security

Do not commit your real `.env` file or any real credentials.
Only commit `.env.example`.
