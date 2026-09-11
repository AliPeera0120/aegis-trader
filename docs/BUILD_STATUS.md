# Build status — 2026-09-11

## Release boundary

Aegis Trader 0.1.0 is implemented and running locally as a research/dashboard application with a mock-tested execution path. The complete production V1 acceptance criteria are **not yet certified**: real Alpaca paper credentials, a full paper lifecycle/session, and Docker/PostgreSQL runtime verification remain necessary. Live trading is locked. No live configuration, operator certification or readiness override was enabled.

## COMPLETED

- Python package, pinned dependency lock, environment template, SQLAlchemy storage, Alembic migration, Git repository, structured audit and CI/deployment files.
- Official Alpaca paper/live adapters; dedicated credentials; limit/bracket entry construction; every live configuration gate; safe exceptions.
- Historical/streaming data adapters, raw/processed separation, exchange calendar handling, timestamp/OHLC validation, quality reports and point-in-time metadata interfaces.
- Timestamp-safe features, seven versioned baseline strategies, ranking, deterministic regimes, independent risk sizing/caps and duplicate-exposure rejection.
- Event-driven backtesting, three fill scenarios, partial entries, costs, protective/time/trailing/EOD simulation, metrics, attribution, Monte Carlo, gap studies, replay and walk-forward selection.
- Calibrated classification, regression/ranking diagnostics, purged labels, train-only transforms, reliability metrics, importance/ablation and drift controls.
- Durable state machine, incremental fills, duplicate updates, uncertain submission reconciliation, reservations, kill/resume latches, liquidation attribution and single-owner runtime service.
- Six-view responsive dashboard, asynchronous research jobs, separate equity modes, account/P&L records, candidate explanations, evidence comparison, trade journal with auditable manual review notes, risk gates and audit inspection.
- Session reporting with expected-fill replay/comparison, rolling decay, explicit fee reconciliation, operator paper-check certification, derived evidence and sequential promotion.
- Auditable expiring readiness override that cannot waive configuration, capital, critical errors, strategy eligibility or deterministic risk.

## CURRENT

Local dashboard: `http://127.0.0.1:8000/`. Trading worker is disabled; broker is disconnected. Synthetic fixture experiments are visible. Seven current baseline versions are in IDEA and disabled. Setup instructions are in ALPACA_SETUP.md.

## TESTS PASSING

- **114 passed, 2 skipped** in the full automated suite. The skips are the two opt-in, credentialed Alpaca PAPER tests.
- Approximately **78% line coverage overall**, **95% risk engine**, **99% domain validation**, **93% ML**, **86% backtest**. The live service/network paths have lower coverage and remain subject to real-paper qualification.
- Ruff lint and JavaScript syntax checks pass.
- Secret-pattern scan passes.
- Fresh SQLite Alembic migration applies successfully; schema check reports no new upgrade operations.
- PostgreSQL migration SQL compiles offline. PostgreSQL server behavior has not been exercised.
- Browser checks verified dashboard rendering at the provided desktop/mobile widths, real Research-lab form submission, all three completed fixture runs, stored result display, strategy/evidence table, and no browser console warnings/errors in the checked flows.
- The local audit chain verifies successfully. Validation did not include an external WORM anchor.
- Targeted checks after final database-resource cleanup also pass. Upstream HTTPX/Starlette/WebSocket deprecation warnings remain.

## KNOWN BUGS / QUALIFICATION LIMITS

No known failing offline test remains. Important operational limitations are explicit in LIMITATIONS.md: real-paper bracket/cancel/reconnect semantics; manual/mixed-owner trade attribution; no point-in-time corporate-action/constituent feed; bar-level fill approximations; closed-trade rather than continuously marked strategy drawdown; no distributed execution lease; and deployment hardening for an internet-facing server. None should be represented as already qualified.

## BACKTEST RESULTS

Only synthetic infrastructure fixtures were run during this build. The three-session SPY/AAPL/MSFT fixture with seed 7 produced these recorded outcomes under the tested ORB baseline:

| Fill assumptions | Completed trades | Net simulated P&L | Expectancy per trade |
|---|---:|---:|---:|
| Optimistic | 5 | $2.298836 | $0.459767 |
| Standard | 5 | -$1.348389 | -$0.269678 |
| Conservative | 2 | -$9.793336 | -$4.896668 |

These values come from stored experiment results, not fabricated market performance. They are **not evidence of an edge**. The different fill models can produce different trade counts because latency, executable prices and risk controls change which entries fill. Exact dataset hash, recorded source version, assumptions, metrics and provenance are in SYNTHETIC_VALIDATION.json. Strategy source formatting/version changes are recorded separately; the numbers above identify the actual stored runs.

## PAPER RESULTS

None. No real Alpaca authentication, paper orders, fills, protective exits, or completed unattended sessions were performed. PAPER is not declared operationally verified.

## LIVE READINESS

LOCKED. No dedicated live credentials/enablement/confirmation/capital/order limits or qualifying paper/OOS evidence were configured. No real live order was submitted. Passing readiness checks in the future would not guarantee profitability.

## NEXT TASKS

1. Configure paper/data credentials privately according to ALPACA_SETUP.md and run the opt-in connection/data checks.
2. Acquire real historical data, inspect quality/corporate events and run sufficiently long conservative/walk-forward research. Reject strategies without robust positive OOS evidence.
3. Record and qualify a complete real paper lifecycle, partial fills, protective exits, disconnect/restart, emergency stop and end-of-day service behavior.
4. Reconcile broker fees, verify operational evidence and accrue the configured paper history before any live eligibility review.
5. Build/run the Docker image and validate PostgreSQL, backups, process supervision and secure deployment on the intended host.
