# Known limitations and honest release boundary

This is a working research application and a tested risk-gated execution implementation. It is not certified production trading software and the user's full V1 acceptance criteria are not yet satisfied without credentialed paper qualification and deployment verification.

- No real credentials were provided. No historical market performance, paper trades or live trades were produced during this build. Synthetic fixture returns validate code only.
- Real Alpaca streams, brackets, partial-fill protection, cancellation, liquidation, reconnect and unattended operation remain unverified. The full paper lifecycle must be exercised before treating the service as ready.
- No point-in-time constituent/corporate-action/delisting feed is integrated. Outliers are quarantined rather than silently adjusted. IEX is not consolidated coverage. Vendor historical revisions may carry information unavailable in real time.
- Minute fills are approximations: no order-book/queue reconstruction, tick sequence, dynamic impact, protective-exit volume caps, borrow/locate cost model, or full halt simulation. Live entries are limit/bracket only. Native stop replacement/trailing exits are not yet qualified.
- Explicit partial-bracket cancellation is retained for operator attention because canceling the parent may remove protection. Manual broker activity and mixed-ownership exits require review. Positions remain broker authoritative even where trade attribution is incomplete.
- Broker round-trip records initially mark fees unreconciled. Explicit statement-based fee allocation and operator-certified paper evidence are implemented, but direct automatic broker-statement ingestion is not. Readiness remains locked; an auditable, expiring operator override exists and was not used.
- Strategy drawdown controls consume persisted closed-trade drawdown; continuous per-strategy marked-equity drawdown is not yet available. Weekly equity on a partial-week start cannot reconstruct earlier activity or external cash flows.
- Session reports run same-session standard-fill backtests and compare candidate identities, fills, counts and timing against paper records. Replay uses bar-derived spread assumptions; a quote-accurate reconstruction and reliable matching through feed delays need paper qualification.
- ML is a working offline experiment pipeline, not a production probability-serving system. No automatic training/deployment or model-based discretionary execution occurs. Sector/beta-relative features and news integration are not implemented.
- Runtime market/model drift hooks exist; sufficiently populated reference/outcome records are needed. Full live calibration tracking and long-history decay qualification need paper records.
- SQLite/PostgreSQL use a compact indexed-record architecture, not separate normalized tables for every requested entity. The local hash chain is not externally anchored. A single process/local lease is required; multi-host high availability is not supported.
- Docker files and CI are provided, but Docker and PostgreSQL were unavailable for local execution. Internet-facing authentication needs deployment hardening (TLS, secure cookies, monitoring and access controls).

None of the evaluated strategies has demonstrated a robust real-market positive expectancy. No threshold or test guarantees future profitability.
