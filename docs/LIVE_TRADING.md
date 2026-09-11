# Live mode remains locked

No live mode was enabled and no live order was submitted during this build. A clean install lacks live credentials, flags, phrase, capital, order limits and readiness evidence.

Live order submission requires all of: `TRADING_MODE=LIVE`, `LIVE_TRADING_ENABLED=true`, dedicated `ALPACA_LIVE_KEY` and `ALPACA_LIVE_SECRET`, a positive `LIVE_CAPITAL_LIMIT`, positive `LIVE_MAX_ORDER_NOTIONAL`, an order-permitting stage, and the exact phrase `I UNDERSTAND LIVE ORDERS USE REAL MONEY` in `LIVE_CONFIRMATION_PHRASE`. These are operator configuration steps, never automatic migrations or UI toggles. Secrets belong only in the private environment.

Stages: OBSERVE consumes data without submitting; SHADOW records candidates without submitting; APPROVAL requires a specific recorded candidate approval followed by fresh quotes and risk review; LIMITED_AUTO and FULL_CONFIGURED still enforce all configured risk/capital/readiness limits. Default is OBSERVE. The dashboard's mode badge changes to a prominent red LIVE TRADING indicator in any live configuration.

Readiness requires verified nonsynthetic paper history (defaults 20 days and 100 trades), positive lower-bound paper expectancy after reconciled costs, drawdown under threshold, positive OOS evidence, a LIVE_ELIGIBLE strategy, operational checks for paper authentication/data/reconciliation/kill switch/order states, and no unresolved critical error. Gates do not promise profitability. The provided deployment file forces PAPER and does not pass live keys.

Paper fee reconciliation, operator attestation and evidence derivation are explicit auditable commands; see CERTIFICATION.md. An expiring manual readiness override can waive evidence checklist items, while configuration, credentials, capital, unresolved critical errors, strategy promotion and independent risk still apply. No certification or override was granted to this build. Configuration alone does not establish verified paper history. Broker live code is tested with mocks only; automated tests never connect to a live account.
