# Dealer Positioning Console

A locally generated SPX options-positioning console built from CBOE delayed chain data.

The model treats positioning as a reaction system across strike, expiry, spot, volatility, and time—not as a fixed wall or a single net-GEX headline.

![Python](https://img.shields.io/badge/Python-3.10%2B-111111)
[![License: MIT](https://img.shields.io/badge/License-MIT-b98945.svg)](LICENSE)

## Quick start

```bash
git clone https://github.com/anthonycho-ux/dealer-positioning-console.git
cd dealer-positioning-console
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
./update-dealer-dashboard.py
./dealer-positioning-html.py
cd reports && python3 -m http.server 9090
```

Open [http://127.0.0.1:9090/dealer-positioning.html](http://127.0.0.1:9090/dealer-positioning.html).

The daily wrapper runs the same update and prints a compact Korean desk summary:

```bash
./dealer-dashboard-daily-cron.sh
```

Set `DASHBOARD_BASE_URL` when the report is served somewhere other than the local default.

## Included source

- `cboe-gex-pipeline.py` — fetches the option chain and computes GEX, delta-adjusted GEX, Vega balance, Black–Scholes Vanna, strike/expiry surfaces, and the 3×3 spot/IV scenario matrix.
- `dealer-positioning-html.py` — renders the dependency-free responsive console.
- `update-dealer-dashboard.py` — regenerates JSON and the legacy brief used by the live dashboard.
- `dealer-dashboard-daily-cron.sh` — refreshes artifacts and emits a concise notification without dumping raw JSON.

Generated JSON, HTML, screenshots, and rolling history are kept out of Git.

## Model boundaries

- Open interest is generally delayed and is not intraday trade flow.
- Dealer inventory and customer trade direction are not directly observed.
- `net_vex` remains for compatibility but means Vega balance, not Vanna.
- Vanna is reported as estimated dollar notional per one volatility-point move.
- Same-day options remain in the model with a `0.5 / 365` time floor.
- Charm is not calculated and theta is not used as a substitute.
- Scenario cells are estimated hedge-demand proxies, not observed dealer transactions.

## Verification

The deployed workflow is accepted only when:

- both Python generators compile;
- the exposure surface is `5 × 13` and the scenario matrix is `3 × 3`;
- expiry and surface Vanna totals match net Vanna within floating-point tolerance;
- the console separates Vega balance from true Vanna;
- the four evidence links remain present;
- the daily notification contains summary metrics and links but no raw tensor payload.

## Disclaimer

This project is an analytical research tool, not investment advice. Public option-chain data does not reveal customer direction or actual dealer inventory. Review CBOE's data terms before redistributing fetched data or generated artifacts.
