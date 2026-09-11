# Deterministic risk

Risk configuration is version-controlled in `config/risk.json` and is independent of strategy code. Tests cover absurd quantities, no buying power, NaN account values, stale/future quote/clock, spread, missing evidence, closed market, disconnected systems, pending exposure, live caps and loss latches.

Caps: dollar position, account allocation, per-trade risk, aggregate open risk, position count, sector and correlation exposure, daily trade/loss count, daily/weekly/strategy drawdown, consecutive losses, liquidity, spread, price and positive EV with a positive lower bound. Existing same-symbol positions or pending entries reject averaging, pyramiding and duplicate exposure. Sizing methods are fixed-dollar, fixed-fractional and volatility-adjusted, always bounded. There is no martingale or loss-size escalation.

Quantity is the integer floor of the tightest independent cap. The risk-approved limit price includes current quote and a configured adverse buffer; stop/target geometry must remain valid at that price. Unknown sectors share a conservative bucket and all defaults share a U.S. equities correlation bucket. Unknown position stops reserve full notional as risk.

Live capital is the smaller of account equity and LIVE_CAPITAL_LIMIT; total positions and pending entries consume it. LIVE_MAX_ORDER_NOTIONAL adds an independent cap. No leverage-based enlargement occurs merely because broker buying power is high.

A daily loss breach persists a kill latch, prevents entries, cancels eligible unfilled entries and optionally requests flattening. The daily breach cannot be reset by the resume action while still breached. Operator stops persist across restart. Consecutive losses pause the remainder of the session. Weekly baseline is observed from the first service snapshot in that week; a partial-week start is explicitly recorded and cannot reconstruct earlier activity or cash flows. Account cash flows require operator reconciliation.
