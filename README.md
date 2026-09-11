# Aegis Trader

An intraday U.S. equities research application with reproducible backtests, chronological ML experiments, a live scanner, deterministic risk controls, and official Alpaca paper/live adapters.

**Default: PAPER. Live execution is locked. No strategy is assumed profitable.**

The project is implemented and runs locally. Broker integration and Docker/PostgreSQL deployment still need qualification in the target environment. No real brokerage credentials were provided, no paper or live order was submitted, and no real-market return was fabricated. See [build status](docs/BUILD_STATUS.md) and [limitations](docs/LIMITATIONS.md) for the precise release boundary.

## Run locally

Python 3.13 is the tested, pinned runtime.

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
alembic upgrade head
aegis init
aegis serve
```

Open [the dashboard](http://127.0.0.1:8000). It starts with empty broker balances and locked controls. Use the Research lab to run a synthetic fixture through optimistic, standard and conservative execution assumptions. Fixtures are prominently labeled and cannot qualify as trading evidence.

```sh
# Deterministic infrastructure validation, not market performance
aegis demo --days 3 --strategy orb --fill all

# Offline verification; no broker credentials required
pytest -q
ruff check .
python scripts/secret_scan.py
```

## Connect Alpaca paper

Follow [ALPACA_SETUP.md](docs/ALPACA_SETUP.md). Configure dedicated paper/data credentials privately through environment variables or a local ignored `.env`; never put them in source code or chat. Set a private operator token before using broker controls. `SERVICE_ENABLED` defaults to false. All strategy versions begin in IDEA and remain disabled until evidence-driven promotion.

The dashboard and service share a backend. With `SERVICE_ENABLED=true`, starting `aegis serve` starts the scanner/worker as well. `aegis paper` is a standalone alternative; run only one continuous execution owner. The provided Docker Compose file forces PAPER and omits live credentials.

## Implemented

- Historical data ingestion, raw/processed separation, UTC timestamps, exchange holidays/early closes/DST, streaming bars/quotes/trades and data-quality flags.
- Timestamp-safe features, seven baseline strategies, cross-sectional ranking, deterministic regimes and a versioned strategy registry.
- Event-driven backtesting with latency, limit/market/stop research orders, partial entry fills, spread/slippage/fees, brackets, stop-first ambiguity, scheduled/time/trailing exits and marked equity.
- Grid/seeded-random search, embargoed walk-forward testing, parameter sensitivity, Monte Carlo, attribution and gap studies.
- Train-only transforms, purged labels, logistic/random-forest/gradient-boosting experiments, Platt/isotonic calibration, reliability metrics, permutation importance, ablation, regression and drift monitoring.
- Independent bounded risk sizing, pending-order reservations, loss limits, duplicate/averaging rejection, stop latches, native broker brackets and live capital caps.
- Durable order state, idempotent cumulative fills, uncertain-submission recovery, broker reconciliation, liquidation tracking, audit hash chains and session reports.
- A responsive six-view dashboard: command center, research lab, strategies, trade journal, risk/readiness and audit.
- SQLite/PostgreSQL storage support, Alembic migrations, container/deployment files, CI and secret scanning.

## Documentation

[Architecture](docs/ARCHITECTURE.md) · [Broker](docs/BROKER.md) · [Market data](docs/MARKET_DATA.md) · [Features](docs/FEATURES.md) · [Leakage controls](docs/LOOKAHEAD_AND_LEAKAGE.md) · [Strategies](docs/STRATEGIES.md) · [Backtesting](docs/BACKTESTING.md) · [Research methods](docs/RESEARCH_METHODS.md) · [ML](docs/ML.md) · [Risk](docs/RISK_ENGINE.md) · [Execution](docs/EXECUTION.md) · [Paper service](docs/PAPER_TRADING.md) · [Live gates](docs/LIVE_TRADING.md) · [Certification](docs/CERTIFICATION.md) · [Security](docs/SECURITY.md) · [Deployment](docs/DEPLOYMENT.md) · [Testing](docs/TESTING.md) · [Limitations](docs/LIMITATIONS.md).

Every reported result must be traceable to a stored dataset, experiment or broker record. Passing tests or readiness thresholds does not guarantee profitability.
