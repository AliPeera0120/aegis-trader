# Continuous paper service

Follow ALPACA_SETUP.md first. `TRADING_MODE=PAPER` is the default. The API/dashboard does not start a trading worker unless `SERVICE_ENABLED=true`; with that flag, startup launches it. `aegis paper` runs the same service directly and refuses LIVE mode. A file lock prevents two owners using the same runtime directory. Deploy one process with one persistent runtime directory.

During the 30 minutes before the exchange open, the service downloads historical context, checks assets, calculates liquidity/volatility metadata and builds the configured universe. During the session, bars/quotes/trades feed the scanner. Every candidate is recorded and independently risk reviewed. Unsupported strategies remain in IDEA and generate research hypotheses without authorized entries.

The broker clock and calendar govern entry hours. Five minutes before the session close, FLATTEN policy requests liquidation and stops new entries. HOLD_PROTECTED retains positions. A prior END_OF_DAY latch can clear on the next session only after successful broker reconciliation. Explicit operator and daily-loss latches remain distinct. After close, the service runs a same-session standard-fill replay for expected-versus-paper comparisons, generates an account/trade/error/risk-rejection report, updates rolling strategy monitoring and persists JSON under `var/reports`.

PAPER and LIVE use identical data/strategy/risk/monitoring code; only the broker adapter and additional live limits differ. The dashboard refreshes backend records every ten seconds. It has separate research, paper and live curves, a candidate table with recorded rejection reasons, versioned strategy cards, broker positions, trade journal, risk limits, live locks and audit integrity.

Real broker authentication, streams, order acknowledgments, protective legs, partial fills, cancel/replace semantics, disconnect recovery and a full unattended session have not been exercised without credentials. Mocks establish code behavior, not broker operational readiness. Do not describe PAPER as verified until the real lifecycle is recorded.
