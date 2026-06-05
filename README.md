# FloEn

Personal energy cost tracker with daily usage and projected cost dashboard.

## Features

- Track daily usage
- Calculate daily cost using flat-rate pricing
- Show projected monthly cost
- Fetch ESB smart meter usage data
- Store secrets securely in Streamlit Community Cloud

## Pricing used

Gas:
- Unit rate: 8.09 cent per kWh
- Standing charge: €151.18 per year

## Streamlit secrets

This app is designed to use Streamlit secrets instead of storing credentials in the repository.

Add these secrets in Streamlit Community Cloud:

```toml
ESB_USERNAME = "your_esb_email"
ESB_PASSWORD = "your_esb_password"
ESB_MPRN = "your_mprn"
FLOGAS_UNIT_RATE = "0.0809"
FLOGAS_STANDING_CHARGE_DAILY = "0.4142"
```

## Local run

If you want to test locally, you can create:

`.streamlit/secrets.toml`

Example:

```toml
ESB_USERNAME = "your_esb_email"
ESB_PASSWORD = "your_esb_password"
ESB_MPRN = "your_mprn"
FLOGAS_UNIT_RATE = "0.0809"
FLOGAS_STANDING_CHARGE_DAILY = "0.4142"
```

Do not commit that file.

## Run

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the app:

```bash
streamlit run app.py
```

Then use the sidebar button to fetch your latest ESB data.

## Security

- Do not commit passwords or credentials to GitHub
- Do not commit `.streamlit/secrets.toml`
- Use Streamlit Community Cloud secrets for deployed use
